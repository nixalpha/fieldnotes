"""Saved observation retrieval coverage; not executed during implementation."""
import base64
import json
from types import SimpleNamespace

import pytest

from conftest import running_app
from fieldnotes.agent import call, connect_mcp
from fieldnotes.core import Frame, Journal
from fieldnotes.memory_store import MemoryStore
from fieldnotes.observation_details import observation_details
from fieldnotes.sessions import Sessions
from test_core import jpeg


def seed(journal, sid='saved'):
    for i in range(5):
        frame = Frame(sid, 1, i, '2026-09-27T14:52:34+00:00', i * 5000,
                      16, 9, 'drone', jpeg())
        journal.save_observation({'observation_id': f'{sid}-{i}', 'session_id': sid,
            'start_elapsed_ms': i * 5000, 'end_elapsed_ms': (i + 1) * 5000,
            'coverage': 'complete', 'gaps': [], 'brief': 'Original brief',
            'job_context': {'name': 'Captured job'}, 'frames': [frame.metadata()]}, [frame])
    journal.record(f'{sid}-0', status='model_error', message='Saved failure')


@pytest.fixture
def saved(tmp_path):
    journal = Journal(tmp_path)
    store = MemoryStore(tmp_path, 'real')
    seed(journal)
    runtime = SimpleNamespace(journal=journal, memory=SimpleNamespace(store=store), jobs=None)
    runtime.sessions = Sessions(runtime, store, archive=True)
    try:
        yield runtime
    finally:
        journal.close()
        store.close()


def test_pages_and_original_metadata_are_read_only(saved):
    before = (saved.journal.db.total_changes, saved.memory.store.db.total_changes)
    page, images = observation_details(saved, session_id='saved', limit=2)
    assert page['total_windows'] == 5 and images == []
    assert page['windows'][0]['summary']['status'] == 'model_error'
    assert page['windows'][1]['summary_status'] == 'missing'
    assert page['windows'][0]['job_context']['name'] == 'Captured job'
    assert page['session']['job'] is None
    seen = list(page['windows'])
    while page['next_cursor']:
        page, _ = observation_details(saved, session_id='saved', limit=2, cursor=page['next_cursor'])
        seen.extend(page['windows'])
    assert [w['observation_id'] for w in seen] == [f'saved-{i}' for i in range(5)]
    assert before == (saved.journal.db.total_changes, saved.memory.store.db.total_changes)


def test_images_indexing_and_missing_files(saved):
    original = saved.journal.observation('saved-0')['frames'][0]
    saved.memory.store.add_evidence(original)
    page, images = observation_details(saved, observation_id='saved-0', include_images=True)
    assert images == [('image/jpeg', jpeg())]
    assert page['image_mapping'][0]['content_block_index'] == 1
    assert page['image_mapping'][0]['evidence_id'] == 'saved:0'
    assert page['windows'][0]['frames'][0]['sha256']
    (saved.journal.directory / 'evidence/saved/1.jpg').unlink()
    page, images = observation_details(saved, observation_id='saved-1', include_images=True)
    assert images == []
    assert page['windows'][0]['frames'][0]['image_status'] == 'missing'
    assert page['windows'][0]['frames'][0]['evidence_status'] == 'unindexed'


@pytest.mark.parametrize('args', [{}, {'session_id': 'saved', 'observation_id': 'saved-0'},
    {'session_id': 'missing'}, {'observation_id': 'missing'},
    {'session_id': 'saved', 'cursor': 'bad'}, {'session_id': 'saved', 'limit': 0},
    {'session_id': 'saved', 'include_images': True, 'limit': 5}])
def test_invalid_requests(saved, args):
    with pytest.raises(ValueError):
        observation_details(saved, **args)


def test_cursor_cannot_cross_scope(saved):
    page, _ = observation_details(saved, session_id='saved', limit=1)
    with pytest.raises(ValueError, match='scope mismatch'):
        observation_details(saved, observation_id='saved-0', cursor=page['next_cursor'])


async def test_http_mcp_retrieval(tmp_path):
    async with running_app(tmp_path, ingest=False) as (app, base):
        rt = app.state.runtime
        sid = rt.sessions.start('details-test')['session']['session_id']
        seed(rt.journal, sid)
        before = (rt.journal.db.total_changes, rt.memory.store.db.total_changes)
        async with connect_mcp(base + '/mcp') as client:
            assert 'get_observation_details' in {t.name for t in (await client.list_tools()).tools}
            page, images = await call(client, 'get_observation_details', {
                'observation_id': f'{sid}-0', 'include_images': True})
            assert page['total_windows'] == 1
            assert base64.b64decode(images[0]) == jpeg()
            assert page['image_mapping'][0]['frame_id'] == 0
        assert before == (rt.journal.db.total_changes, rt.memory.store.db.total_changes)


def test_reopened_archive_records(tmp_path):
    journal = Journal(tmp_path)
    seed(journal)
    journal.close()
    journal = Journal(tmp_path)
    store = MemoryStore(tmp_path, 'real')
    runtime = SimpleNamespace(journal=journal, memory=SimpleNamespace(store=store), jobs=None)
    runtime.sessions = Sessions(runtime, store, archive=True)
    try:
        page, images = observation_details(runtime, observation_id='saved-0', include_images=True)
        assert page['windows'][0]['summary']['message'] == 'Saved failure'
        assert images[0][1] == jpeg()
    finally:
        journal.close()
        store.close()


async def test_waiting_session_has_empty_page(tmp_path):
    async with running_app(tmp_path, ingest=False) as (app, base):
        sid = app.state.runtime.sessions.start('empty-details')['session']['session_id']
        async with connect_mcp(base + '/mcp') as client:
            page, images = await call(client, 'get_observation_details', {'session_id': sid})
            assert page['session']['state'] == 'waiting_for_video'
            assert page['total_windows'] == 0
            assert page['windows'] == [] and page['next_cursor'] is None and images == []
