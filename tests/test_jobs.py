"""Jobs service and context coverage. Added without executing verification."""
import base64
import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from fieldnotes.core import Runtime
from fieldnotes.jobs import JobError, Jobs
from fieldnotes.jobs_api import router
from fieldnotes.memory_store import MemoryStore
from fieldnotes.model import VisionModel
from fieldnotes.sessions import Sessions


@pytest.fixture
def setup(tmp_path):
    clock = [0.0]
    runtime = Runtime(tmp_path, clock=lambda: clock[0])
    store = MemoryStore(tmp_path, 'model_interpretation')
    runtime.sessions = Sessions(runtime, store)
    runtime.jobs = Jobs(runtime, store)
    yield runtime, store, clock
    store.close()
    runtime.journal.close()


def create(runtime, key='create', name='Workshop', theme='Observe workspace setup.'):
    return runtime.jobs.create(key, name, theme)['job']


def start(runtime, key='start'):
    return runtime.sessions.start(key)['session']


def end(runtime, session, key='end'):
    return runtime.sessions.end(session['session_id'], key)


def test_create_validation_pagination_and_request_dedup(setup):
    rt, _, _ = setup
    job = create(rt)
    assert rt.jobs.context()['selected_job_id'] is None
    assert create(rt)['job_id'] == job['job_id']
    with pytest.raises(JobError, match='request_conflict'):
        create(rt, name='Another job')
    with pytest.raises(JobError, match='invalid_arguments'):
        create(rt, 'blank', theme='  ')
    with pytest.raises(JobError, match='invalid_arguments'):
        create(rt, 'long', name='x' * 121)
    other = create(rt, 'create-2', name='Inventory')
    first = rt.jobs.list(1)
    second = rt.jobs.list(1, first['next_cursor'])
    assert first['jobs'][0]['job_id'] == other['job_id']
    assert second['jobs'][0]['job_id'] == job['job_id']
    assert second['next_cursor'] is None


def test_switch_and_edit_only_affect_new_sessions(setup):
    rt, _, _ = setup
    a, b = create(rt), create(rt, 'create-2', name='Second job')
    rt.jobs.switch('select-a', a['job_id'], 0)
    session = start(rt)
    captured = session['job_context']
    assert session['state'] == 'waiting_for_video'
    assert captured['job_id'] == a['job_id']
    rt.jobs.update('edit-a', a['job_id'], 1, theme='A new theme.')
    rt.jobs.switch('select-b', b['job_id'], 1)
    assert rt.sessions.current['job_context'] == captured
    assert rt.jobs.context()['current_session']['job_id'] == a['job_id']
    assert rt.jobs.context()['selected_job_id'] == b['job_id']
    end(rt, session)
    new = start(rt, 'start-2')
    assert new['job_context']['job_id'] == b['job_id']
    end(rt, new, 'end-2')
    rt.jobs.switch('select-a-again', a['job_id'], 2)
    newest = start(rt, 'start-3')
    assert newest['job_context']['revision'] == 2
    assert newest['job_context']['theme'] == 'A new theme.'


def test_revision_conflicts_and_idempotent_switch(setup):
    rt, _, _ = setup
    job = create(rt)
    rt.jobs.update('edit', job['job_id'], 1, name='Renamed')
    with pytest.raises(JobError, match='stale_revision'):
        rt.jobs.update('stale-edit', job['job_id'], 1, theme='Different')
    rt.jobs.switch('select', job['job_id'], 0)
    rt.jobs.switch('clear', None, 1)
    replay = rt.jobs.switch('select', job['job_id'], 0)
    assert replay['duplicate']
    assert rt.jobs.context()['selected_job_id'] is None
    with pytest.raises(JobError, match='stale_selection'):
        rt.jobs.switch('stale-select', job['job_id'], 0)


def test_assign_batch_is_atomic_and_does_not_change_capture_or_name(setup):
    rt, store, _ = setup
    a, b = create(rt), create(rt, 'create-b', name='Other job')
    first = start(rt)
    rt.sessions.rename(first['session_id'], 'My observation')
    end(rt, first)
    second = start(rt, 'start-2')
    before = rt.sessions.get(second['session_id'])
    rt.jobs.assign('attach-first', a['job_id'], [first['session_id']])
    with pytest.raises(JobError, match='membership_conflict'):
        rt.jobs.assign('bad-batch', b['job_id'], [second['session_id'], first['session_id']])
    assert rt.jobs.describe(before)['job_id'] is None
    assert not store.rows('SELECT 1 FROM job_requests WHERE request_id=?', ('bad-batch',))
    result = rt.jobs.assign('attach-second', a['job_id'], [second['session_id']])
    assert result['capture_context_unchanged']
    assert rt.sessions.current['job_context'] is None
    assert rt.sessions.get(second['session_id']) == before
    assert rt.sessions.get(first['session_id'])['display_name'] == 'My observation'
    assert rt.jobs.get(a['job_id'])['observation_count'] == 2
    repeated = rt.jobs.assign('attach-same', a['job_id'], [second['session_id']])
    assert repeated['assigned_session_ids'] == []
    assert repeated['unchanged_session_ids'] == [second['session_id']]
    with pytest.raises(JobError, match='unknown_session'):
        rt.jobs.assign('unknown', a['job_id'], ['missing'])


def test_filter_and_unknown_job(setup):
    rt, _, _ = setup
    job = create(rt)
    first = start(rt)
    end(rt, first)
    second = start(rt, 'start-2')
    rt.jobs.assign('attach', job['job_id'], [first['session_id']])
    assert [s['session_id'] for s in rt.sessions.list(job_id=job['job_id'])['sessions']] == [first['session_id']]
    assert [s['session_id'] for s in rt.sessions.list(untracked_only=True)['sessions']] == [second['session_id']]
    with pytest.raises(ValueError, match='cannot be combined'):
        rt.sessions.list(job_id=job['job_id'], untracked_only=True)
    with pytest.raises(JobError, match='unknown_job'):
        rt.jobs.get('missing')


def test_selection_persists_and_sessions_snapshot_after_reconnection(setup):
    rt, store, clock = setup
    a, b = create(rt), create(rt, 'other', name='Other')
    rt.jobs.switch('select-a', a['job_id'], 0)
    first = rt.accept(b'jpeg', 10, 10)
    rt.jobs.switch('select-b', b['job_id'], 1)
    clock[0] = 11
    rt.sessions.expire()
    next_frame = rt.accept(b'jpeg', 10, 10)
    assert first.session_id != next_frame.session_id
    assert rt.sessions.get(first.session_id)['job_context']['job_id'] == a['job_id']
    assert rt.sessions.current['job_context']['job_id'] == b['job_id']
    rt.sessions.shutdown()
    rt.jobs = Jobs(rt, store)
    assert rt.jobs.context()['selected_job_id'] == b['job_id']
    assert rt.jobs.context()['selection_revision'] == 2


async def test_saved_window_and_llm_keep_captured_context_after_switch(setup):
    rt, _, clock = setup
    a, b = create(rt), create(rt, 'other', name='Other')
    rt.jobs.switch('select-a', a['job_id'], 0)
    rt.observation_brief = 'Describe visible movements.'
    rt.accept(b'jpeg', 10, 10)
    clock[0] = 5
    window = rt.observe(0, 5000)
    rt.jobs.switch('select-b', b['job_id'], 1)
    rt.jobs.update('edit-a', a['job_id'], 1, theme='A later theme.')
    assert rt.observe(0, 5000)['job_context'] == window['job_context']
    calls = []

    async def parse(**kwargs):
        calls.append(kwargs)
        output = SimpleNamespace(memory=[], model_dump=lambda **kw: {
            'summary': 'A person is visible.', 'observed_actions': [],
            'uncertainties': [], 'change_state': 'uncertain'})
        return SimpleNamespace(output_parsed=output, usage=None)

    model = VisionModel()
    model.client = SimpleNamespace(responses=SimpleNamespace(parse=parse))
    await model.summarize(window, [base64.b64encode(b'jpeg').decode()], [], window['brief'])
    payload = json.loads(calls[0]['input'][0]['content'][0]['text'])
    assert payload['job_context']['job_id'] == a['job_id']
    assert payload['job_context']['revision'] == 1
    assert payload['task_brief'] == 'Describe visible movements.'


def test_archive_allows_organization_but_not_selection(setup):
    rt, _, _ = setup
    session = start(rt)
    end(rt, session)
    rt.jobs.archive = True
    job = create(rt)
    rt.jobs.update('edit', job['job_id'], 1, name='Archived work')
    rt.jobs.assign('attach', job['job_id'], [session['session_id']])
    with pytest.raises(JobError, match='archive_read_only'):
        rt.jobs.switch('select', job['job_id'], 0)
    assert rt.jobs.context()['selection_allowed'] is False


async def test_http_uses_shared_service_with_explicit_errors(setup):
    rt, _, _ = setup
    app = FastAPI()
    app.include_router(router(rt.jobs))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://testserver') as client:
        created = await client.post('/api/jobs', json={'request_id': 'new', 'name': 'HTTP job', 'theme': 'Observe setup.'})
        jid = created.json()['job']['job_id']
        assert rt.jobs.get(jid)['name'] == 'HTTP job'
        assert (await client.get('/api/jobs/context')).json()['selected_job_id'] is None
        assert (await client.get('/api/jobs/missing')).status_code == 404
        assert (await client.post('/api/jobs/selection', json={'request_id': 'stale', 'job_id': jid, 'expected_selection_revision': 99})).status_code == 409
        assert (await client.post('/api/jobs', json={'request_id': 'empty', 'name': ' ', 'theme': 'x'})).status_code == 422
