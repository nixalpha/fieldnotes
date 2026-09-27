"""Portal coverage; deliberately not executed as part of the UI overhaul."""
import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from fieldnotes.core import Runtime
from fieldnotes.memory_store import MemoryStore
from fieldnotes.portal import Account, Portal, router
from fieldnotes.sessions import Sessions

SID = 'a' * 32


@pytest.fixture
def portal(tmp_path):
    runtime = Runtime(tmp_path)
    store = MemoryStore(tmp_path, 'model_interpretation')
    runtime.sessions = Sessions(runtime, store)
    runtime.sessions._save({
        'session_id': SID, 'name': 'Lounge observation', 'state': 'ended', 'source': 'drone',
        'started_at': '2026-09-27T10:00:00+00:00', 'created_at': '2026-09-27T10:00:00+00:00',
        'last_received_at': '2026-09-27T10:00:10+00:00',
        'start_elapsed_ms': 1000, 'end_elapsed_ms': 11000,
    })
    agent = SimpleNamespace(model=SimpleNamespace(name='configured-model'), pending=None, busy_session=None)
    instance = Portal(runtime, agent, SimpleNamespace(store=store))
    yield instance
    store.close()
    runtime.journal.close()


def observation(portal, frame_id=1, start=1000, end=6000, brief=None, gaps=None):
    frame = {'frame_id': frame_id, 'session_id': SID, 'id': f'{SID}:{frame_id}',
             'elapsed_ms': start + 100, 'received_at': '2026-09-27T10:00:01+00:00',
             'asset': f'evidence/{SID}/{frame_id}.jpg', 'source': 'drone'}
    body = {'observation_id': f'{SID}-{start}-{end}', 'session_id': SID,
            'start_elapsed_ms': start, 'end_elapsed_ms': end, 'frames': [frame],
            'brief': brief, 'gaps': gaps or [], 'status': 'completed', 'model': 'real-model',
            'visual': {'summary': 'A person is seated.', 'observed_actions': [], 'uncertainties': []}}
    with portal.runtime.journal.db:
        portal.runtime.journal.db.execute('INSERT INTO observations VALUES (?,?,?)',
                                         (body['observation_id'], SID, json.dumps(body)))
        portal.runtime.journal.db.execute('INSERT INTO summaries(observation_id,body) VALUES (?,?)',
                                         (body['observation_id'], json.dumps(body)))
    portal.store.execute('INSERT INTO evidence VALUES (?,?,?,?)',
                         (frame['id'], SID, frame['elapsed_ms'], json.dumps(frame)))
    return body


def account(frame_id=1):
    return Account.model_validate({
        'title': 'Lounge observation', 'topics': ['Workspace'],
        'objective': {'text': 'Observe use of the lounge.', 'frame_ids': []},
        'process': {'text': 'A person was seated.', 'frame_ids': [frame_id]},
        'outcome': {'text': 'Work was finished.', 'frame_ids': []},
        'unknowns': {'text': 'Completion was not observed.', 'frame_ids': [frame_id]},
        'segments': [{'label': 'Person seated', 'start_ms': 1000, 'end_ms': 6000,
                      'frame_ids': [frame_id], 'uncertain': False}],
    })


def model(monkeypatch, result):
    calls = []

    class Client:
        def __init__(self, **kwargs):
            self.responses = self

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def parse(self, **kwargs):
            calls.append(kwargs)
            await asyncio.sleep(0)
            if isinstance(result, Exception):
                raise result
            return SimpleNamespace(output_parsed=result, usage=None)

    monkeypatch.setenv('OPENAI_API_KEY', 'test-not-a-real-key')
    monkeypatch.setattr('fieldnotes.portal.AsyncOpenAI', Client)
    return calls


async def test_account_is_persisted_deduplicated_and_conservative(portal, monkeypatch):
    observation(portal)
    calls = model(monkeypatch, account())
    assert portal.request(SID)['state'] == 'pending'
    job = portal.jobs[SID]
    portal.request(SID)
    assert portal.jobs[SID] is job
    await job
    result = portal.account(SID)
    assert result['state'] == 'ready'
    assert result['account']['outcome']['text'] == 'Outcome not established.'
    assert result['account']['objective']['text'] == 'Objective was not recorded.'
    assert result['version'] == 1
    assert portal.request(SID)['state'] == 'ready'
    assert len(calls) == 1
    assert portal.store.rows('SELECT version FROM session_account_versions') == [(1,)]


async def test_rejects_foreign_citations_and_retains_previous_account(portal, monkeypatch):
    observation(portal)
    model(monkeypatch, account())
    portal.request(SID)
    await portal.jobs[SID]
    observation(portal, frame_id=2, start=6000, end=11000)
    assert portal.account(SID)['state'] == 'stale'
    model(monkeypatch, account(999))
    portal.request(SID)
    await portal.jobs[SID]
    result = portal.account(SID)
    assert result['state'] == 'failed'
    assert result['stale']
    assert result['version'] == 1
    assert result['account']['process']['frame_ids'] == [1]
    assert 'outside' in result['error']


async def test_segment_cannot_bridge_recorded_gap(portal, monkeypatch):
    observation(portal, gaps=[{'start_elapsed_ms': 3000, 'end_elapsed_ms': 4000}])
    model(monkeypatch, account())
    portal.request(SID)
    await portal.jobs[SID]
    assert portal.account(SID)['state'] == 'failed'
    assert 'unsupported coverage' in portal.account(SID)['error']


async def test_model_failure_and_archive_do_not_fabricate_accounts(portal, monkeypatch):
    observation(portal)
    model(monkeypatch, RuntimeError('Model unavailable'))
    portal.request(SID)
    await portal.jobs[SID]
    assert portal.account(SID)['account'] is None
    assert portal.account(SID)['error'] == 'Model unavailable'
    portal.archive = True
    with pytest.raises(ValueError, match='archive'):
        portal.request(SID)


def test_coverage_does_not_treat_sampling_spacing_as_an_outage(portal):
    observation(portal)
    assert portal.coverage(SID) == [{'start_ms': 6000, 'end_ms': 11000, 'label': 'Unsaved interval'}]
    observation(portal, frame_id=2, start=6000, end=11000)
    assert portal.coverage(SID) == []


async def test_frame_pagination_handles_tied_times_and_more_than_1000_frames(portal):
    for i in range(1005):
        frame = {'id': f'{SID}:{i}', 'frame_id': i, 'session_id': SID, 'elapsed_ms': 1500}
        portal.store.execute('INSERT INTO evidence VALUES (?,?,?,?)',
                             (frame['id'], SID, 1500, json.dumps(frame)))
    app = FastAPI()
    app.include_router(router(portal))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://testserver') as client:
        first = (await client.get(f'/api/session-frames/{SID}?limit=1000')).json()
        second = (await client.get(f'/api/session-frames/{SID}', params={'limit': 1000, 'cursor': first['next_cursor']})).json()
        assert len(first['frames']) == 1000
        assert len(second['frames']) == 5
        assert len({f['id'] for f in first['frames'] + second['frames']}) == 1005
        assert second['next_cursor'] is None


async def test_library_filters_before_pagination(portal):
    observation(portal)
    portal.save(SID, {'state': 'ready', 'account': account().model_dump(), 'version': 1,
                      'source_revision': portal.revision(SID)})
    app = FastAPI()
    app.include_router(router(portal))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://testserver') as client:
        result = (await client.get('/api/library', params={'q': 'lounge', 'topic': 'Workspace'})).json()
        assert [s['session_id'] for s in result['sessions']] == [SID]
        assert (await client.get('/api/library?topic=Inventory')).json()['sessions'] == []
        assert (await client.get('/api/library?since=2026-09-28T00:00:00Z')).json()['sessions'] == []
        assert (await client.get('/api/library?since=invalid')).status_code == 422
