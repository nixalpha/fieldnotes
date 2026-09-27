from __future__ import annotations

import asyncio
import json
import os
import socket
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .agent import Agent
from .core import Runtime
from .ingest import Decoder
from .jobs import Jobs
from .jobs_api import router as jobs_router
from .mcp_server import make_mcp
from .memory import Memory
from .memory_api import router as memory_router
from .model import VisionModel
from .portal import Portal
from .portal import router as portal_router
from .sessions import Sessions
from .statistics import Statistics
from .statistics_api import router as statistics_router

STATIC = Path(__file__).parent / "static"


def lan_ip() -> str:
    if value := os.getenv("FIELDNOTES_LAN_IP"):
        return value
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))  # Route lookup only; no packet is sent.
            return sock.getsockname()[0]
    except OSError:
        return "<laptop-LAN-IP>"


class SummarySettings(BaseModel):
    brief: str = Field(default="Describe the visible physical work, including placing and moving cones.",
                       min_length=1, max_length=4000)
    interval_seconds: float = Field(default=5, ge=1, le=60)


def create_app(data_dir: Path | None = None, *, port: int = 8000, source: str = "drone",
               stub: bool = False, autostart: bool = False, ingest: bool = True,
               rtmp_url: str = "rtmp://127.0.0.1:1935/live/drone", archive: bool = False, model_root: Path | None = None) -> FastAPI:
    if stub and not archive:
        raise ValueError("Live mode requires real vision. Stub interpretations are restricted to saved mock archives.")
    runtime = Runtime(data_dir or Path(os.getenv("FIELDNOTES_DATA_DIR", "data")), source)
    if archive:
        from .memory_replay import ArchiveRuntime
        runtime.journal.close()
        runtime = ArchiveRuntime(data_dir)
    memory = Memory(runtime, model_root=model_root, archive=archive, mock=stub or archive)
    runtime.memory = memory
    runtime.sessions = Sessions(runtime, memory.store, archive=archive)
    runtime.jobs = Jobs(runtime, memory.store, archive=archive)
    model = VisionModel(stub or archive)
    mcp_url = f"http://127.0.0.1:{port}/mcp"
    runtime.statistics = Statistics(runtime, memory.store, runtime.jobs, archive=archive, model_name=model.name, mcp_url=mcp_url)
    mcp = make_mcp(runtime)
    mcp_app = mcp.streamable_http_app()
    agent = Agent(runtime, mcp_url, model)
    agent.autostart = autostart
    portal = Portal(runtime, agent, memory, archive)
    def session_ended(session, reason):
        try:
            agent.session_ended(session, reason)
        finally:
            memory.end_session(session['session_id'], reason)
            portal.ended(session, reason)
    runtime.sessions.on_end = session_ended
    decoder = Decoder(runtime, rtmp_url)
    publish_url = f"rtmp://{lan_ip()}:1935/live/drone"

    @asynccontextmanager
    async def lifespan(app):
        async with mcp.session_manager.run():
            decoder_task = asyncio.create_task(decoder.run()) if ingest and not archive else None
            watchdog = asyncio.create_task(runtime.sessions.watch()) if not archive else None
            if not archive: agent.launch()
            memory.launch()
            try:
                yield
            finally:
                if watchdog:
                    watchdog.cancel()
                    await asyncio.gather(watchdog, return_exceptions=True)
                if decoder_task:
                    decoder_task.cancel()
                    await asyncio.gather(decoder_task, return_exceptions=True)
                runtime.sessions.shutdown()
                await agent.close()
                await runtime.statistics.close()
                await portal.close()
                await memory.close()
                runtime.journal.close()

    app = FastAPI(title="FieldNotes", lifespan=lifespan)
    app.state.runtime, app.state.agent = runtime, agent
    app.state.memory = memory
    app.include_router(memory_router(memory))
    app.include_router(portal_router(portal))
    app.include_router(jobs_router(runtime.jobs))
    app.include_router(statistics_router(runtime.statistics))

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        # Reject cross-origin browser mutations and DNS rebinding on the local control surface.
        host = request.url.hostname
        origin = request.headers.get("origin")
        if host not in {"127.0.0.1", "localhost", "testserver"}:
            return JSONResponse({"detail": "Localhost only"}, status_code=403)
        if origin and urlparse(origin).netloc != request.headers.get("host"):
            return JSONResponse({"detail": "Cross-origin requests are not allowed"}, status_code=403)
        return await call_next(request)

    @app.get("/")
    async def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/api/status")
    async def status():
        return {"stream": runtime.status(), "agent": agent.status(), "publish_url": publish_url}

    @app.get("/api/sessions")
    async def sessions(limit: int = 50, cursor: str | None = None, job_id: str | None = None, untracked_only: bool = False):
        try:
            return runtime.sessions.list(limit, cursor, job_id, untracked_only)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/api/config")
    async def config():
        return {"brief": agent.brief, "interval_seconds": agent.interval_seconds, "publish_url": publish_url}

    @app.post("/api/summaries/start")
    async def start(settings: SummarySettings):
        if archive: raise HTTPException(409, "Archive mode: use the explicit mock MCP exercise; no paid model calls")
        try:
            agent.start(settings.brief, settings.interval_seconds)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        return agent.status()

    @app.post("/api/summaries/pause")
    async def pause():
        agent.pause()
        return agent.status()

    @app.get("/api/journal")
    async def journal(after: int = 0, limit: int = 200, session_id: str | None = None):
        if after < 0 or not 1 <= limit <= 1000:
            raise HTTPException(422, "Invalid journal cursor or limit")
        return {"entries": runtime.journal.list(limit, after, session_id)}

    @app.get("/api/export")
    async def export():
        async def chunks():
            for line in runtime.journal.export():
                yield line
        return StreamingResponse(chunks(), media_type="application/x-ndjson",
                                 headers={"Content-Disposition": 'attachment; filename="fieldnotes.jsonl"'})

    @app.get("/api/evidence/{session_id}/{frame_id}.jpg")
    async def evidence(session_id: str, frame_id: int):
        if len(session_id) != 32 or any(c not in "0123456789abcdef" for c in session_id) or frame_id < 0:
            raise HTTPException(404)
        path = runtime.journal.directory / "evidence" / session_id / f"{frame_id}.jpg"
        if not path.is_file():
            raise HTTPException(404)
        return FileResponse(path, media_type="image/jpeg")

    @app.get("/api/preview")
    async def preview(request: Request):
        if archive:
            frames = memory.store.frames(runtime.session_id, limit=1)
            return FileResponse(memory.store.asset(frames[0]["asset"])) if frames else JSONResponse({"detail":"Empty archive"}, status_code=404)
        async def frames():
            queue = runtime.buffer.subscribe()
            try:
                if runtime.buffer.frames:
                    queue.put_nowait(runtime.buffer.frames[-1])
                while not await request.is_disconnected():
                    try:
                        frame = await asyncio.wait_for(queue.get(), 1)
                    except TimeoutError:
                        continue
                    if frame.session_id != runtime.session_id:
                        continue
                    yield (b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                           + str(len(frame.jpeg)).encode() + b"\r\n\r\n" + frame.jpeg + b"\r\n")
            finally:
                runtime.buffer.unsubscribe(queue)
        return StreamingResponse(frames(), media_type="multipart/x-mixed-replace; boundary=frame",
                                 headers={"Cache-Control": "no-store"})

    @app.get("/api/events")
    async def events(request: Request):
        async def updates():
            revision = -1
            while not await request.is_disconnected():
                payload = {"stream": runtime.status(), "agent": agent.status(), "publish_url": publish_url}
                yield f"event: status\ndata: {json.dumps(payload)}\n\n"
                if revision != runtime.revision:
                    revision = runtime.revision
                    yield f"event: journal\ndata: {json.dumps({'revision': revision})}\n\n"
                await asyncio.sleep(1)
        return StreamingResponse(updates(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    app.mount("/", mcp_app)
    return app
