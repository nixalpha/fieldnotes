import asyncio
import io

import pytest
from PIL import Image
from pydantic import ValidationError

from fieldnotes.agent import Agent, WindowClock
from fieldnotes.core import Frame, FrameBuffer, Runtime, VisualSummary
from fieldnotes.ingest import read_ppm
from fieldnotes.model import VisionModel


def jpeg():
    buf = io.BytesIO()
    Image.new('RGB', (16, 9), 'green').save(buf, 'JPEG')
    return buf.getvalue()


def frame(n, ms, epoch=1, size=4):
    return Frame('a' * 32, epoch, n, '2026-01-01T00:00:00+00:00', ms, 16, 9, 'replay', b'x' * size)


def test_buffer_limits_and_latest_subscription():
    buffer = FrameBuffer(max_age_ms=100, max_bytes=8)
    queue = buffer.subscribe()
    for i, ms in enumerate([0, 50, 100]):
        buffer.append(frame(i, ms))
    assert [f.frame_id for f in buffer.frames] == [1, 2]
    assert buffer.byte_size == 8
    assert queue.qsize() == 1 and queue.get_nowait().frame_id == 2
    buffer.evict(201)
    assert not buffer.frames and buffer.byte_size == 0
    buffer.unsubscribe(queue)


def test_selection_gaps_distinct_half_open():
    buffer = FrameBuffer()
    for i, ms in enumerate([0, 200, 400, 2000, 5000]):
        buffer.append(frame(i, ms, epoch=2 if ms >= 2000 else 1))
    selected, gaps = buffer.select(0, 5000)
    assert len({f.frame_id for f in selected}) == len(selected)
    assert all(0 <= f.elapsed_ms < 5000 for f in selected)
    assert any(g['reason'] == 'reconnect' for g in gaps)
    assert buffer.select(6000, 7000)[0] == []


async def test_ppm_partial_reads_and_truncation():
    reader = asyncio.StreamReader()
    task = asyncio.create_task(read_ppm(reader))
    for chunk in [b'P6\n', b'2 1\n255\n', b'abc', b'def']:
        reader.feed_data(chunk)
        await asyncio.sleep(0)
    assert await task == (b'abcdef', 2, 1)
    reader = asyncio.StreamReader()
    reader.feed_data(b'P6\n2 1\n255\nabc')
    reader.feed_eof()
    with pytest.raises(asyncio.IncompleteReadError):
        await read_ppm(reader)


def test_window_clock_no_drift():
    clock = WindowClock(123, 5000, 123)
    assert clock.due(5122) == []
    assert clock.due(5123) == [(123, 5123)]
    assert clock.due(20124) == [(5123, 10123), (10123, 15123), (15123, 20123)]
    assert clock.end == 25123
    assert WindowClock(123, 5000, 18000).end == 20123


def test_evidence_persistence_validation_and_dedup(tmp_path):
    now = [0.0]
    rt = Runtime(tmp_path, clock=lambda: now[0])
    rt.accept(jpeg(), 16, 9)
    now[0] = 5
    observation = rt.observe(0, 5000)
    rt.buffer.evict(70_000)
    assert rt.journal.images(observation)[0] == jpeg()
    valid = VisualSummary(summary='Cone visible.', observed_actions=[{'description': 'Cone visible', 'frame_ids': [0]}],
                          uncertainties=[], change_state='uncertain')
    entry = rt.journal.record(observation['observation_id'], valid)
    assert rt.journal.record(observation['observation_id'], valid)['seq'] == entry['seq']
    invalid = valid.model_copy(update={'observed_actions': [valid.observed_actions[0].model_copy(update={'frame_ids': [99]})]})
    with pytest.raises(ValueError, match='outside'):
        rt.journal.record(observation['observation_id'], invalid)
    with pytest.raises(ValidationError):
        VisualSummary(summary='invalid')
    rt.journal.close()
    next_rt = Runtime(tmp_path)
    assert next_rt.journal.list()[0]['visual']['summary'] == 'Cone visible.'
    next_rt.journal.close()


def test_pending_queue_replacement_and_pause(tmp_path):
    now = [0.0]
    rt = Runtime(tmp_path, clock=lambda: now[0])
    rt.accept(jpeg(), 16, 9)
    now[0] = 5
    a = rt.observe(0, 5000)
    now[0] = 10
    b = rt.observe(5000, 10000)
    agent = Agent(rt, 'http://127.0.0.1:8000/mcp', VisionModel(stub=True))
    agent.enqueue(a)
    agent.enqueue(b)
    assert agent.pending == b
    assert rt.journal.get_summary(a['observation_id'])['status'] == 'skipped'
    agent.pause()
    assert agent.pending is None
    assert rt.journal.get_summary(b['observation_id'])['status'] == 'skipped'
    rt.journal.close()


def test_staleness_and_empty_windows(tmp_path):
    now = [0.0]
    rt = Runtime(tmp_path, clock=lambda: now[0])
    assert rt.status()['state'] == 'waiting'
    rt.accept(jpeg(), 16, 9)
    now[0] = 3
    assert rt.status()['state'] == 'stale'
    now[0] = 10
    obs = rt.observe(5000, 10000)
    assert obs['coverage'] == 'empty'
    rt.record_system(obs, 'no_video', 'No frames')
    assert rt.journal.list()[0]['visual'] is None
    rt.journal.close()
