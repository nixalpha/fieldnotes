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
        self.busy = False
        self.error: str | None = None
        self.last_duration_ms = 0
        self.tasks: list[asyncio.Task] = []
        self.autostart = False
        self.model_timeout = 15
        self.generation = 0

    def status(self):
        return {"enabled": self.enabled, "available": self.model.available,
                "model": self.model.name, "model_error": self.model.error, "error": self.error,
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
        if self.pending:
            self.runtime.record_system(self.pending, "skipped", "Summary paused before analysis.")
            self.pending = None

    def enqueue(self, observation: dict):
        if self.pending:
            self.runtime.record_system(self.pending, "skipped", "Model was busy; a newer interval replaced this one.")
        self.pending = observation
        self.wakeup.set()

    async def initialize(self):
        await self.model.check()
        if self.autostart and self.model.available:
            self.start(self.brief, self.interval_seconds)

    async def produce(self):
        while True:
            try:
                async with connect_mcp(self.url) as session:
                    while True:
                        if self.enabled and self.runtime.first_frame_ms is not None:
                            now = self.runtime.elapsed_ms()
                            if self.clock is None:
                                self.clock = WindowClock(self.runtime.first_frame_ms,
                                                         round(self.interval_seconds * 1000), now)
                            generation = self.generation
                            for start, end in self.clock.due(now):
                                observation, _ = await call(session, "get_observation_window", {
                                    "start_elapsed_ms": start, "end_elapsed_ms": end})
                                if not self.enabled or generation != self.generation:
                                    self.runtime.record_system(observation, "skipped", "Observation settings changed.")
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
                                "end_elapsed_ms": observation["end_elapsed_ms"]})
                            recent, _ = await call(session, "get_recent_summaries", {"limit": 3})
                            async with asyncio.timeout(self.model_timeout):
                                visual, usage = await self.model.summarize(metadata, images,
                                                                           recent["summaries"], self.brief)
                            self.last_duration_ms = round((time.monotonic() - started) * 1000)
                            await call(session, "record_summary", {
                                "observation_id": observation["observation_id"],
                                "summary": visual.model_dump(), "generation": {
                                    "model": self.model.name, "duration_ms": self.last_duration_ms, **usage}})
                            self.error = None
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
