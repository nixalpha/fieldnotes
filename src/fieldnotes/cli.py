from __future__ import annotations

import importlib.metadata
import json
import os
import shutil
import signal
import socket
import subprocess
import time
from pathlib import Path

import typer
import uvicorn
from dotenv import load_dotenv

from .app import create_app, lan_ip

app = typer.Typer(no_args_is_help=True, help="DJI RTMP observation journal and MCP agent")


def load_env():
    load_dotenv(Path.cwd() / ".env")


def port_free(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket() as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
            return True
        except OSError:
            return False


@app.command()
def doctor():
    """Check local prerequisites without sending images or making paid API requests."""
    load_env()
    checks = {
        "ffmpeg": shutil.which("ffmpeg"), "mediamtx": shutil.which("mediamtx"),
        "port_1935_free": port_free(1935, "0.0.0.0"), "port_8000_free": port_free(8000),
        "api_key_configured": bool(os.getenv("OPENAI_API_KEY")),
        "model": os.getenv("OPENAI_MODEL", "gpt-6-luna"),
        "dependencies": {p: importlib.metadata.version(p) for p in ("mcp", "fastapi", "openai", "pillow")},
        "publish_url": f"rtmp://{lan_ip()}:1935/live/drone",
    }
    typer.echo(json.dumps(checks, indent=2))
    if not checks["mediamtx"]:
        typer.echo("Install MediaMTX on macOS: brew install mediamtx")
    if not checks["ffmpeg"]:
        typer.echo("Install FFmpeg on macOS: brew install ffmpeg")
    if not checks["api_key_configured"]:
        typer.echo("Preview works without a key. Set OPENAI_API_KEY in .env for visual summaries.")


def terminate(proc):
    if proc and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


@app.command()
def dev(
    port: int = typer.Option(8000, min=1024, max=65535),
    source: str = typer.Option("drone", help="drone or replay; applies to the entire session"),
    stub: bool = typer.Option(False, help="Use a clearly labeled test model; no API calls"),
    autostart: bool = typer.Option(False, help="Start summaries after the model capability check"),
    external_mediamtx: bool = typer.Option(False, help="Use an already running local MediaMTX"),
):
    """Start supervised MediaMTX, video ingest, localhost dashboard, and MCP."""
    load_env()
    if source not in {"drone", "replay"}:
        raise typer.BadParameter("source must be drone or replay")
    for executable in (["ffmpeg"] if external_mediamtx else ["ffmpeg", "mediamtx"]):
        if not shutil.which(executable):
            raise typer.BadParameter(f"{executable} missing; run fieldnotes doctor")
    if not port_free(port):
        raise typer.BadParameter(f"Port {port} is already occupied; no existing process will be stopped")
    if not external_mediamtx and not port_free(1935, "0.0.0.0"):
        raise typer.BadParameter("Port 1935 occupied. Stop its owner or use --external-mediamtx")
    data = Path(os.getenv("FIELDNOTES_DATA_DIR", "data"))
    data.mkdir(parents=True, exist_ok=True)
    media = None
    log_file = None
    try:
        if not external_mediamtx:
            log_file = (data / "mediamtx.log").open("ab")
            media = subprocess.Popen(["mediamtx", str(Path(__file__).with_name("mediamtx.yml"))],
                                     stdout=log_file, stderr=subprocess.STDOUT, start_new_session=True)
            deadline = time.monotonic() + 5
            while port_free(1935) and time.monotonic() < deadline and media.poll() is None:
                time.sleep(0.1)
            if media.poll() is not None or port_free(1935):
                raise RuntimeError(f"MediaMTX failed to start; see {data / 'mediamtx.log'}")
        typer.echo(f"Dashboard: http://127.0.0.1:{port}\nMCP: http://127.0.0.1:{port}/mcp")
        typer.echo(f"DJI publish URL: rtmp://{lan_ip()}:1935/live/drone\nSource: {source}")
        application = create_app(data, port=port, source=source, stub=stub, autostart=autostart)
        server = uvicorn.Server(uvicorn.Config(application, host="127.0.0.1", port=port,
                                              log_level="warning", timeout_graceful_shutdown=3))
        # Supervise MediaMTX without terminating processes owned by other applications.
        import asyncio

        async def run():
            async def monitor():
                while not server.should_exit:
                    if media and media.poll() is not None:
                        typer.echo("MediaMTX exited; shutting down. Check data/mediamtx.log", err=True)
                        server.should_exit = True
                        return
                    await asyncio.sleep(0.5)
            task = asyncio.create_task(monitor())
            try:
                await server.serve()
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        previous_term = signal.getsignal(signal.SIGTERM)
        def terminate_signal(signum, frame):
            raise KeyboardInterrupt
        signal.signal(signal.SIGTERM, terminate_signal)
        try:
            asyncio.run(run())
        finally:
            signal.signal(signal.SIGTERM, previous_term)
    finally:
        terminate(media)
        if log_file:
            log_file.close()


@app.command()
def replay(video: Path = typer.Argument(..., exists=True, dir_okay=False),
           port: int = 8000, loop: bool = False):
    """Publish a local video to RTMP; requires dev --source replay to prevent mislabeling."""
    import httpx
    try:
        status = httpx.get(f"http://127.0.0.1:{port}/api/status", timeout=3).raise_for_status().json()
    except httpx.HTTPError as exc:
        raise typer.BadParameter("Start fieldnotes dev --source replay first") from exc
    if status["stream"]["source"] != "replay":
        raise typer.BadParameter("Replay refused: restart dev with --source replay")
    if not shutil.which("ffmpeg"):
        raise typer.BadParameter("ffmpeg is missing")
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "warning", "-nostdin", "-re"]
    if loop:
        cmd += ["-stream_loop", "-1"]
    cmd += ["-i", str(video.resolve()), "-an", "-c:v", "libx264", "-preset", "ultrafast",
            "-tune", "zerolatency", "-pix_fmt", "yuv420p", "-g", "15", "-f", "flv",
            "rtmp://127.0.0.1:1935/live/drone"]
    typer.echo("REPLAY source: publishing prerecorded video (not a live drone).")
    proc = subprocess.Popen(cmd, start_new_session=True)
    try:
        code = proc.wait()
        if code:
            raise typer.Exit(code)
    finally:
        terminate(proc)


@app.command()
def setup_memory_models(model_dir: Path = Path("data/models")):
    """Explicitly download pinned public weights and EdgeTAM source; no image upload."""
    from .perception import setup
    typer.echo(json.dumps(setup(model_dir), indent=2))


@app.command()
def replay_memory(evidence: Path = typer.Argument(..., exists=True, file_okay=False),
                  source_journal: Path = typer.Option(Path("data/journal.sqlite3"), exists=True),
                  output_root: Path = Path("data/memory-runs"), model_dir: Path = Path("data/models"),
                  fixture_file: Path | None = None):
    """Copy sampled evidence and index every retained image in an isolated mock run."""
    from .memory_replay import replay
    typer.echo(replay(evidence, source_journal, output_root, model_dir, fixture_file))


@app.command()
def view_memory(archive: Path = typer.Argument(..., exists=True, file_okay=False),
                port: int = typer.Option(8001, min=1024, max=65535), model_dir: Path | None = None):
    """Serve an isolated mock archive and actual MCP; never starts ingestion or paid calls."""
    manifest=json.loads((archive / "manifest.json").read_text())
    if manifest.get("input_kind") != "sampled_evidence":
        raise typer.BadParameter("Not a sampled-evidence memory archive")
    if not port_free(port):
        if port == 8001 and port_free(8002): port=8002
        else: raise typer.BadParameter("Port occupied; choose --port. No process was stopped.")
    root=model_dir or Path(manifest["model_root"])
    application=create_app(archive.resolve(), port=port, source="replay", stub=True, ingest=False,
                           archive=True, model_root=root)
    typer.echo(f"MOCK ARCHIVE dashboard: http://127.0.0.1:{port}/\nMCP: http://127.0.0.1:{port}/mcp")
    uvicorn.run(application, host="127.0.0.1", port=port, log_level="warning", timeout_graceful_shutdown=3)


@app.command()
def exercise_memory_mcp(archive: Path = typer.Argument(..., exists=True, file_okay=False),
                        port: int = typer.Option(8002, min=1024, max=65535),
                        start_tracks: bool = True):
    """Run only the scoped offline MCP scenarios, with authored mock LLM responses."""
    import asyncio
    from .memory_exercise import exercise
    asyncio.run(exercise(archive, f"http://127.0.0.1:{port}/mcp", start_tracks))
