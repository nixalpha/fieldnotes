import asyncio
import base64

import httpx
from conftest import running_app
from test_core import jpeg

from fieldnotes.agent import call, connect_mcp


async def test_real_mcp_images_and_journal(tmp_path):
    async with running_app(tmp_path, ingest=False, stub=True) as (app, base):
        rt = app.state.runtime
        frame = rt.accept(jpeg(), 16, 9)
        await asyncio.sleep(.02)
        async with connect_mcp(base + '/mcp') as session:
            tools = await session.list_tools()
            assert {
                'get_stream_status', 'get_observation_window', 'get_recent_summaries',
                'get_observation_details', 'record_summary'} <= {t.name for t in tools.tools}
            observation, images = await call(session, 'get_observation_window', {
                'start_elapsed_ms': frame.elapsed_ms, 'end_elapsed_ms': rt.elapsed_ms()})
            assert base64.b64decode(images[0]) == jpeg()
            entry, _ = await call(session, 'record_summary', {
                'observation_id': observation['observation_id'],
                'summary': {'summary': 'A visible test frame.', 'observed_actions': [
                    {'description': 'Visible test frame', 'frame_ids': [frame.frame_id]}],
                    'uncertainties': [], 'change_state': 'uncertain'}})
            assert entry['status'] == 'completed'
            invalid = await session.call_tool('record_summary', {
                'observation_id': observation['observation_id'],
                'summary': {'summary': 'Invalid', 'observed_actions': [
                    {'description': 'Invented', 'frame_ids': [999]}],
                    'uncertainties': [], 'change_state': 'uncertain'}})
            assert invalid.isError
        async with httpx.AsyncClient(base_url=base) as client:
            assert (await client.get('/')).status_code == 200
            entries = (await client.get('/api/journal')).json()['entries']
            assert len(entries) == 1
            assert (await client.get(f'/api/evidence/{rt.session_id}/{frame.frame_id}.jpg')).content == jpeg()
            assert len((await client.get('/api/export')).text.splitlines()) == 1
            assert (await client.post('/api/summaries/pause', headers={'Origin': 'https://example.com'})).status_code == 403


async def test_scheduler_through_mcp_with_gaps(tmp_path):
    async with running_app(tmp_path, ingest=False, stub=True) as (app, base):
        rt = app.state.runtime
        agent = app.state.agent
        agent.start('Test visible work.', 1)
        # Actual network MCP calls and wall-clock scheduling; short intervals keep this fast.
        for _ in range(14):
            rt.accept(jpeg(), 16, 9)
            await asyncio.sleep(.1)
        for _ in range(30):
            if any(e['status'] == 'completed' for e in rt.journal.list()):
                break
            await asyncio.sleep(.1)
        assert any(e['model'] == 'stub-no-vision' for e in rt.journal.list())
        await asyncio.sleep(2)
        assert any(e['status'] == 'no_video' for e in rt.journal.list())
        agent.pause()
        assert agent.pending is None


async def test_missing_key_keeps_preview_service_available(tmp_path, monkeypatch):
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    async with running_app(tmp_path, ingest=False) as (app, base):
        async with httpx.AsyncClient(base_url=base) as client:
            status = (await client.get('/api/status')).json()
            assert not status['agent']['available']
            assert 'missing' in status['agent']['model_error']
            assert (await client.post('/api/summaries/start', json={})).status_code == 409


async def test_slow_model_skips_pending_and_timeout_records_error(tmp_path):
    async with running_app(tmp_path, ingest=False, stub=True) as (app, base):
        rt, agent = app.state.runtime, app.state.agent
        original = agent.model.summarize
        active, maximum = 0, 0

        async def slow(*args):
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            try:
                await asyncio.sleep(2.6)
                return await original(*args)
            finally:
                active -= 1

        agent.model.summarize = slow
        agent.start('test', 1)
        for _ in range(44):
            rt.accept(jpeg(), 16, 9)
            await asyncio.sleep(.1)
        assert maximum == 1
        assert any(e['status'] == 'skipped' for e in rt.journal.list())
        # An active slow call finishes; subsequent calls use a shortened test deadline.
        agent.model_timeout = .05
        for _ in range(35):
            rt.accept(jpeg(), 16, 9)
            await asyncio.sleep(.1)
        assert any(e['status'] == 'model_error' and 'TimeoutError' in e['message'] for e in rt.journal.list())
        assert maximum == 1
