from __future__ import annotations

import asyncio
import io
import logging
from contextlib import suppress

from PIL import Image

from .core import Runtime

log = logging.getLogger(__name__)


async def read_ppm(reader: asyncio.StreamReader) -> tuple[bytes, int, int]:
    """PPM preserves each frame's actual dimensions; RGB payload uses exact-size reads."""
    if await reader.readline() != b"P6\n":
        raise ValueError("Invalid PPM frame header")
    line = await reader.readline()
    while line.startswith(b"#"):
        line = await reader.readline()
    width, height = map(int, line.split())
    if not (0 < width <= 960 and 0 < height <= 540):
        raise ValueError("Decoded frame dimensions exceed configured bounds")
    if (await reader.readline()).strip() != b"255":
        raise ValueError("Unsupported PPM pixel format")
    return await reader.readexactly(width * height * 3), width, height


def encode_jpeg(rgb: bytes, width: int, height: int) -> bytes:
    output = io.BytesIO()
    Image.frombytes("RGB", (width, height), rgb).save(output, format="JPEG", quality=80)
    return output.getvalue()


async def stop_process(proc: asyncio.subprocess.Process) -> None:
    # A stopped reader can leave a full stdout pipe, preventing asyncio's wait()
    # from completing even after the child exits. Drain it during termination.
    async def drain_stdout():
        if proc.stdout:
            while await proc.stdout.read(65536):
                pass

    drain = asyncio.create_task(drain_stdout())
    try:
        if proc.returncode is None:
            with suppress(ProcessLookupError):
                proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), 3)
        except TimeoutError:
            with suppress(ProcessLookupError):
                proc.kill()
            await asyncio.wait_for(proc.wait(), 3)
    finally:
        drain.cancel()
        await asyncio.gather(drain, return_exceptions=True)


class Decoder:
    def __init__(self, runtime: Runtime, url: str):
        self.runtime, self.url = runtime, url
        self.proc: asyncio.subprocess.Process | None = None

    async def stderr(self, reader: asyncio.StreamReader) -> None:
        while line := await reader.readline():
            log.debug("ffmpeg: %s", line.decode(errors="replace").strip())

    async def run(self):
        retry = 1
        while True:
            received = False
            err_task = None
            self.runtime.epoch += 1
            try:
                self.proc = await asyncio.create_subprocess_exec(
                    "ffmpeg", "-hide_banner", "-loglevel", "warning", "-nostdin",
                    "-fflags", "nobuffer", "-flags", "low_delay", "-rw_timeout", "10000000",
                    "-analyzeduration", "1000000", "-probesize", "1000000", "-rtmp_buffer", "0",
                    "-i", self.url, "-an", "-vf",
                    "fps=5,scale=960:540:force_original_aspect_ratio=decrease",
                    "-pix_fmt", "rgb24", "-c:v", "ppm", "-flush_packets", "1", "-f", "image2pipe", "pipe:1",
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                )
                err_task = asyncio.create_task(self.stderr(self.proc.stderr))
                while True:
                    rgb, width, height = await asyncio.wait_for(read_ppm(self.proc.stdout), 10)
                    jpeg = await asyncio.to_thread(encode_jpeg, rgb, width, height)
                    self.runtime.accept(jpeg, width, height)
                    received = True
            except (OSError, ValueError, asyncio.IncompleteReadError, TimeoutError) as exc:
                self.runtime.decoder_state = "reconnecting"
                self.runtime.decoder_error = f"{type(exc).__name__}: {str(exc)[:200]}"
                log.info("Video unavailable; retrying in %ss", retry)
            finally:
                if self.proc:
                    await stop_process(self.proc)
                    self.proc = None
                if err_task:
                    err_task.cancel()
                    await asyncio.gather(err_task, return_exceptions=True)
            if received:
                retry = 1
            await asyncio.sleep(retry)
            retry = min(retry * 2, 10)
