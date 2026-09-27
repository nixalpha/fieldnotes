import asyncio
import shutil
import socket

import pytest
from conftest import running_app

from fieldnotes.ingest import stop_process


@pytest.mark.integration
async def test_rtmp_replay_reconnect_and_persistence(tmp_path):
    if not shutil.which('mediamtx') or not shutil.which('ffmpeg'):
        pytest.skip('MediaMTX and FFmpeg required')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        rtmp_port = sock.getsockname()[1]
    config = tmp_path / 'mediamtx.yml'
    config.write_text(f'logLevel: error\nrtmp: yes\nrtmpAddress: 127.0.0.1:{rtmp_port}\nrtsp: no\nhls: no\nwebrtc: no\nsrt: no\nmoq: no\npaths:\n  live/drone:\n    source: publisher\n')
    clip = tmp_path / 'test.mp4'
    make_clip = await asyncio.create_subprocess_exec('ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
        'testsrc2=size=320x180:rate=15', '-t', '3', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(clip))
    assert await make_clip.wait() == 0
    media = await asyncio.create_subprocess_exec('mediamtx', str(config), stdout=asyncio.subprocess.DEVNULL,
                                                 stderr=asyncio.subprocess.DEVNULL)
    publisher = None
    url = f'rtmp://127.0.0.1:{rtmp_port}/live/drone'

    async def publish():
        return await asyncio.create_subprocess_exec('ffmpeg', '-v', 'error', '-nostdin', '-re',
            '-stream_loop', '-1', '-i', str(clip), '-an', '-c:v', 'libx264', '-preset', 'ultrafast',
            '-tune', 'zerolatency', '-g', '15', '-f', 'flv', url,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)

    async def until(predicate, timeout=30):
        async with asyncio.timeout(timeout):
            while not predicate():
                await asyncio.sleep(.2)

    try:
        await asyncio.sleep(.5)
        assert media.returncode is None, "MediaMTX failed to start"
        publisher = await publish()
        async with running_app(tmp_path / 'data', source='replay', stub=True, autostart=True,
                               rtmp_url=url) as (app, base):
            rt = app.state.runtime
            await until(lambda: any(e['status'] == 'completed' for e in rt.journal.list()))
            first_epoch = rt.epoch
            assert rt.status()['state'] == 'live'
            entry = next(e for e in rt.journal.list() if e['status'] == 'completed')
            assert entry['source'] == 'replay'
            assert entry['frames'] and entry['model'] == 'stub-no-vision'
            assert entry['end_elapsed_ms'] - entry['start_elapsed_ms'] == 5000
            await stop_process(publisher)
            await until(lambda: rt.status()['state'] in {'stale', 'reconnecting'}, 15)
            publisher = await publish()
            await until(lambda: rt.epoch > first_epoch and rt.status()['state'] == 'live')
        from fieldnotes.core import Journal
        journal = Journal(tmp_path / 'data')
        assert journal.list() and journal.images(entry)
        journal.close()
    finally:
        if publisher:
            await stop_process(publisher)
        await stop_process(media)
