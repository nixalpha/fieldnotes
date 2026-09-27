from __future__ import annotations

import asyncio
import json
import time
from contextlib import asynccontextmanager

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from .core import Runtime
from .model import VisionModel


@asynccontextmanager
async def connect_mcp(url: str):
    async with httpx.AsyncClient(timeout=httpx.Timeout(20, read=300)) as client:
        async with streamable_http_client(url, http_client=client) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session


async def call(session: ClientSession, name: str, arguments: dict | None = None):
    result = await session.call_tool(name, arguments or {})
    if result.isError:
        raise RuntimeError("; ".join(c.text for c in result.content if c.type == "text"))
    metadata = next((json.loads(c.text) for c in result.content if c.type == "text"), {})
    images = [c.data for c in result.content if c.type == "image"]
    return metadata, images


class WindowClock:
    """Integer boundaries stay anchored to the first frame, not model completion time."""

    def __init__(self, anchor: int, interval_ms: int, now: int):
        self.interval = interval_ms
        self.end = anchor + ((max(0, now - anchor) // interval_ms) + 1) * interval_ms

    def due(self, now: int) -> list[tuple[int, int]]:
        windows = []
        while self.end <= now:
            windows.append((self.end - self.interval, self.end))
            self.end += self.interval
        return windows


class Agent:
    def __init__(self, runtime: Runtime, url: str, model: VisionModel):
        self.runtime, self.url, self.model = runtime, url, model
        self.interval_seconds = 5.0
        self.brief = "Describe the visible physical work, including placing and moving cones."
        self.enabled = False
        self.pending: dict | None = None
        self.wakeup = asyncio.Event()
        self.clock: WindowClock | None = None
        self.clock_session: str | None = None
        self.last_window_end: int | None = None
        self.busy = False
        self.error: str | None = None
        self.memory_error: str | None = None
        self.last_duration_ms = 0
        self.tasks: list[asyncio.Task] = []
        self.autostart = False
        self.model_timeout = 15
        self.generation = 0

    def status(self):
        return {"enabled": self.enabled, "available": self.model.available,
                "memory_error": self.memory_error, "model": self.model.name, "model_error": self.model.error, "error": self.error,
                "busy": self.busy, "pending": self.pending is not None,
                "interval_seconds": self.interval_seconds, "brief": self.brief,
                "last_duration_ms": self.last_duration_ms,
                "degraded_cadence": self.last_duration_ms > self.interval_seconds * 1000}

    def start(self, brief: str, interval: float):
        if not self.model.available:
            raise ValueError(self.model.error or "Model capability check is still running")
        self.pause()
        self.brief, self.interval_seconds = brief, interval
        self.enabled = True
        self.error = None

    def pause(self):
        self.enabled = False
        self.generation += 1
        self.clock = None
        self.clock_session = None
        self.last_window_end = None
        if self.pending:
            self.runtime.record_system(self.pending, "skipped", "Summary paused before analysis.")
            self.pending = None

    def enqueue(self, observation: dict):
        if self.pending:
            self.runtime.record_system(self.pending, "skipped", "Model was busy; a newer interval replaced this one.")
        self.pending = observation
        self.wakeup.set()

    def session_ended(self, session: dict, reason: str):
        """Pin the remaining evidence before the session buffer is cleared."""
        first, last = session.get('first_frame'), session.get('last_frame')
        if first and last:
            end = last['elapsed_ms'] + 1
            interval = round(self.interval_seconds * 1000)
            start = self.last_window_end if self.clock_session == session['session_id'] else None
            if start is None:
                start = max(first['elapsed_ms'], end - interval)
            while start < end:
                stop = min(start + interval, end)
                observation = self.runtime.observe(start, stop, session['session_id'])
                observation = {**observation, 'final_session_window': stop == end,
                               'partial_final_window': stop - start < interval,
                               'session_end_reason': reason}
                with self.runtime.journal.db:
                    self.runtime.journal.db.execute('UPDATE observations SET body=? WHERE id=?',
                        (json.dumps(observation), observation['observation_id']))
                self.runtime.memory.ingest_observation(observation)
                if self.enabled and observation['frames']:
                    self.enqueue(observation)
                else:
                    self.runtime.record_system(observation, 'skipped' if observation['frames'] else 'no_video',
                        'Session ended; observer was paused.' if observation['frames'] else 'No retained video for the final interval.')
                start = stop
        self.clock = None
        self.clock_session = None
        self.last_window_end = None
        self.generation += 1

    async def initialize(self):
        await self.model.check()
        if self.autostart and self.model.available:
            self.start(self.brief, self.interval_seconds)

    async def produce(self):
        while True:
            try:
                async with connect_mcp(self.url) as session:
                    while True:
                        if self.enabled and self.runtime.session_id and self.runtime.first_frame_ms is not None:
                            sid = self.runtime.session_id
                            now = self.runtime.elapsed_ms()
                            if self.clock is None or self.clock_session != sid:
                                self.clock = WindowClock(self.runtime.first_frame_ms,
                                                         round(self.interval_seconds * 1000),
                                                         min(now, self.runtime.last_frame_ms))
                                self.clock_session = sid
                                self.last_window_end = self.clock.end - self.clock.interval
                            generation = self.generation
                            # Don't invent ongoing empty windows after video has stopped.
                            for start, end in self.clock.due(min(now, self.runtime.last_frame_ms + 1)):
                                if sid != self.runtime.session_id:
                                    break
                                # Pin synchronously before the MCP await can cross a boundary.
                                self.runtime.observe(start, end, sid)
                                self.last_window_end = end
                                observation, _ = await call(session, "get_observation_window", {
                                    "start_elapsed_ms": start, "end_elapsed_ms": end, "session_id": sid})
                                if not self.enabled or generation != self.generation:
                                    self.runtime.record_system(observation, "skipped", "Observation settings or session changed.")
                                    continue
                                if observation["coverage"] == "empty":
                                    self.runtime.record_system(observation, "no_video", "No video frames received in this interval.")
                                else:
                                    self.enqueue(observation)
                        await asyncio.sleep(0.1)
            except Exception as exc:
                self.error = f"MCP scheduler connection: {str(exc)[:300]}"
                await asyncio.sleep(1)

    async def consume(self):
        while True:
            try:
                async with connect_mcp(self.url) as session:
                    while True:
                        await self.wakeup.wait()
                        self.wakeup.clear()
                        observation, self.pending = self.pending, None
                        if observation is None:
                            continue
                        self.busy = True
                        started = time.monotonic()
                        try:
                            # Reading the pinned observation again avoids losing evidence to buffer eviction.
                            metadata, images = await call(session, "get_observation_window", {
                                "start_elapsed_ms": observation["start_elapsed_ms"],
                                "end_elapsed_ms": observation["end_elapsed_ms"],
                                "session_id": observation["session_id"]})
                            recent, _ = await call(session, "get_recent_summaries", {"limit": 3, "session_id": observation["session_id"]})
                            priors = list(recent["summaries"])
                            try:
                                memory, _ = await call(session, "get_memory_state", {
                                    "session_id": metadata["session_id"],
                                    "at_elapsed_ms": metadata["start_elapsed_ms"]})
                                priors.append({"kind": "fallible_historical_memory",
                                    "notice": "Historical labels and interpretations only; cite current images for current claims.",
                                    "states": [{"latest_interpretation": s["latest_interpretation"],
                                                "support_conflict": s.get("support_conflict", False)}
                                               for s in memory.get("states", [])[:10]]})
                            except Exception:
                                pass  # Optional memory context must not stop ordinary summaries.
                            async with asyncio.timeout(self.model_timeout):
                                visual, usage = await self.model.summarize(metadata, images,
                                                                           priors, self.brief)
                            self.last_duration_ms = round((time.monotonic() - started) * 1000)
                            await call(session, "record_summary", {
                                "observation_id": observation["observation_id"],
                                "summary": visual.model_dump(), "generation": {
                                    "model": self.model.name, "duration_ms": self.last_duration_ms, **usage}})
                            self.error = None
                            # Summary persistence and memory persistence have independent outcomes.
                            if self.model.last_memory:
                                try:
                                    assertions=[]
                                    for draft in self.model.last_memory:
                                        draft=dict(draft)
                                        ids=draft.pop("frame_ids")
                                        draft["evidence_ids"]=[f"{metadata['session_id']}:{fid}" for fid in ids]
                                        assertions.append(draft)
                                    await call(session, "record_memory", {"proposal": {
                                        "session_id": metadata["session_id"],
                                        "idempotency_key": "summary-memory:" + metadata["observation_id"],
                                        "evidence_bundle_ids": [metadata["evidence_bundle_id"]],
                                        "assertions": assertions}})
                                    self.memory_error=None
                                except Exception as exc:
                                    self.memory_error=f"Memory was not saved: {exc}"
                        except asyncio.CancelledError:
                            self.runtime.record_system(observation, "skipped", "Application stopped during analysis.")
                            raise
                        except Exception as exc:
                            self.last_duration_ms = round((time.monotonic() - started) * 1000)
                            self.error = f"{type(exc).__name__}: {str(exc)[:500]}"
                            self.runtime.record_system(observation, "model_error", self.error)
                        finally:
                            self.busy = False
            except Exception as exc:
                self.error = f"MCP worker connection: {str(exc)[:300]}"
                await asyncio.sleep(1)

    def launch(self):
        self.tasks = [asyncio.create_task(self.initialize()), asyncio.create_task(self.produce()),
                      asyncio.create_task(self.consume())]

    async def close(self):
        self.pause()
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        await self.model.close()
