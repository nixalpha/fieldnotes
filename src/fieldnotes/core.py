from __future__ import annotations

import asyncio
import json
import sqlite3
import time
import uuid
from collections import deque
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class Action(BaseModel):
    model_config = ConfigDict(extra="forbid")
    description: str = Field(min_length=1, max_length=1000)
    frame_ids: list[int] = Field(min_length=1, max_length=5)


class VisualSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")
    summary: str = Field(min_length=1, max_length=3000)
    observed_actions: list[Action] = Field(max_length=20)
    uncertainties: list[str] = Field(max_length=20)
    change_state: Literal["change_observed", "no_clear_change", "uncertain"]


@dataclass(frozen=True)
class Frame:
    session_id: str
    stream_epoch: int
    frame_id: int
    received_at: str
    elapsed_ms: int
    width: int
    height: int
    source: str
    jpeg: bytes

    def metadata(self) -> dict:
        return {k: v for k, v in asdict(self).items() if k != "jpeg"}


class FrameBuffer:
    """Latest-only subscriptions are also the future mapper's input boundary."""

    def __init__(self, max_age_ms: int = 60_000, max_bytes: int = 64 * 1024 * 1024):
        self.frames: deque[Frame] = deque()
        self.byte_size = 0
        self.max_age_ms, self.max_bytes = max_age_ms, max_bytes
        self.subscribers: set[asyncio.Queue] = set()

    def evict(self, now_ms: int) -> None:
        while self.frames and (
            now_ms - self.frames[0].elapsed_ms > self.max_age_ms or self.byte_size > self.max_bytes
        ):
            self.byte_size -= len(self.frames.popleft().jpeg)

    def append(self, frame: Frame) -> None:
        self.frames.append(frame)
        self.byte_size += len(frame.jpeg)
        self.evict(frame.elapsed_ms)
        for queue in self.subscribers:
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(frame)

    def clear(self) -> None:
        self.frames.clear()
        self.byte_size = 0
        for queue in self.subscribers:
            while not queue.empty():
                queue.get_nowait()

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue[Frame] = asyncio.Queue(maxsize=1)
        self.subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self.subscribers.discard(queue)

    def select(self, start: int, end: int) -> tuple[list[Frame], list[dict]]:
        # Half-open intervals ensure boundary frames are not used twice.
        available = [f for f in self.frames if start <= f.elapsed_ms < end]
        if not available:
            return [], [{"start_elapsed_ms": start, "end_elapsed_ms": end, "reason": "no_video"}]
        selected: dict[int, Frame] = {}
        for i in range(5):
            target = start + (end - start - 1) * i / 4
            frame = min(available, key=lambda f: abs(f.elapsed_ms - target))
            selected[frame.frame_id] = frame
        gaps = []
        previous_ms, previous_epoch = start, available[0].stream_epoch
        for frame in available:
            if frame.elapsed_ms - previous_ms > 600 or frame.stream_epoch != previous_epoch:
                gaps.append({"start_elapsed_ms": previous_ms, "end_elapsed_ms": frame.elapsed_ms,
                             "reason": "reconnect" if frame.stream_epoch != previous_epoch else "missing_frames"})
            previous_ms, previous_epoch = frame.elapsed_ms, frame.stream_epoch
        if end - previous_ms > 600:
            gaps.append({"start_elapsed_ms": previous_ms, "end_elapsed_ms": end, "reason": "missing_frames"})
        return sorted(selected.values(), key=lambda f: f.elapsed_ms), gaps


class Journal:
    def __init__(self, directory: Path):
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(directory / "journal.sqlite3")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS observations (id TEXT PRIMARY KEY, session_id TEXT, body TEXT);
            CREATE TABLE IF NOT EXISTS summaries (
                seq INTEGER PRIMARY KEY AUTOINCREMENT, observation_id TEXT UNIQUE, body TEXT);
        """)

    def observation(self, observation_id: str) -> dict | None:
        row = self.db.execute("SELECT body FROM observations WHERE id=?", (observation_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def save_observation(self, observation: dict, frames: list[Frame]) -> None:
        for frame in frames:
            path = self.directory / "evidence" / frame.session_id / f"{frame.frame_id}.jpg"
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                path.write_bytes(frame.jpeg)
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO observations VALUES (?, ?, ?)",
                            (observation["observation_id"], observation["session_id"], json.dumps(observation)))

    def images(self, observation: dict) -> list[bytes]:
        return [(self.directory / "evidence" / f["session_id"] / f'{f["frame_id"]}.jpg').read_bytes()
                for f in observation["frames"]]

    def get_summary(self, observation_id: str) -> dict | None:
        row = self.db.execute("SELECT seq, body FROM summaries WHERE observation_id=?", (observation_id,)).fetchone()
        return {**json.loads(row[1]), "seq": row[0]} if row else None

    def record(self, observation_id: str, visual: VisualSummary | None = None, *,
               status: str = "completed", message: str = "", model: str | None = None,
               duration_ms: int = 0, usage: dict | None = None) -> dict:
        observation = self.observation(observation_id)
        if not observation:
            raise ValueError("Unknown observation_id")
        if visual:
            allowed = {f["frame_id"] for f in observation["frames"]}
            if not allowed:
                raise ValueError("Cannot summarize an observation without frames")
            if any(set(action.frame_ids) - allowed for action in visual.observed_actions):
                raise ValueError("Summary cites frames outside its observation")
        existing = self.get_summary(observation_id)
        if existing:
            return existing
        entry = {**observation, "status": status, "generated_at": utc_now(), "model": model,
                 "duration_ms": duration_ms, "usage": usage or {},
                 "visual": visual.model_dump() if visual else None, "message": message}
        with self.db:
            self.db.execute("INSERT INTO summaries (observation_id, body) VALUES (?, ?)",
                            (observation_id, json.dumps(entry)))
        return self.get_summary(observation_id)

    def list(self, limit: int = 100, after: int = 0, session_id: str | None = None) -> list[dict]:
        # Persisted observations keep evidence URLs and timestamps valid across restarts.
        rows = self.db.execute("SELECT seq, body FROM summaries WHERE seq>? ORDER BY seq", (after,))
        results = [{**json.loads(body), "seq": seq} for seq, body in rows]
        if session_id:
            results = [r for r in results if r["session_id"] == session_id]
        return results[-limit:]

    def export(self):
        for seq, body in self.db.execute("SELECT seq, body FROM summaries ORDER BY seq"):
            yield json.dumps({**json.loads(body), "seq": seq}) + "\n"

    def close(self):
        self.db.close()


class Runtime:
    def __init__(self, directory: Path, source: str = "drone", clock=time.monotonic):
        self.clock = clock
        self.started = clock()
        self.started_utc = datetime.now(UTC)
        self.session_id = None
        self.sessions = None
        self.jobs = None
        self.source = source
        self.buffer = FrameBuffer()
        self.journal = Journal(directory)
        self.first_frame_ms: int | None = None
        self.last_frame_ms: int | None = None
        self.last_dimensions: tuple[int, int] | None = None
        self.epoch = 0
        self.next_frame = 0
        self.decoder_state = "waiting"
        self.decoder_error: str | None = None
        self.revision = 0

    def elapsed_ms(self) -> int:
        return int((self.clock() - self.started) * 1000)

    def accept(self, jpeg: bytes, width: int, height: int) -> Frame:
        if self.sessions:
            self.sessions.before_frame()
        elif self.session_id is None:
            self.session_id = uuid.uuid4().hex
        elapsed = self.elapsed_ms()
        frame = Frame(self.session_id, self.epoch, self.next_frame, utc_now(), elapsed,
                      width, height, self.source, jpeg)
        self.next_frame += 1
        self.last_frame_ms = elapsed
        self.last_dimensions = (width, height)
        if self.first_frame_ms is None:
            self.first_frame_ms = elapsed
        self.decoder_state, self.decoder_error = "live", None
        if self.sessions:
            self.sessions.received(frame)
        self.buffer.append(frame)
        return frame

    def status(self) -> dict:
        now = self.elapsed_ms()
        self.buffer.evict(now)
        age = None if self.last_frame_ms is None else now - self.last_frame_ms
        state = self.decoder_state
        if age is not None and age >= 3000:
            state = "reconnecting" if state == "reconnecting" else "stale"
        elif age is not None:
            state = "live"
        return {**(self.sessions.status() if self.sessions else {}),
                "session_id": self.session_id, "source": self.source, "state": state,
                "elapsed_ms": now, "first_frame_ms": self.first_frame_ms, "stream_epoch": self.epoch,
                "latest_frame_age_ms": age, "dimensions": self.last_dimensions,
                "buffer_frames": len(self.buffer.frames), "buffer_bytes": self.buffer.byte_size,
                "buffer_start_ms": self.buffer.frames[0].elapsed_ms if self.buffer.frames else None,
                "buffer_end_ms": self.buffer.frames[-1].elapsed_ms if self.buffer.frames else None,
                "decoder_error": self.decoder_error}

    def observe(self, start: int, end: int, session_id: str | None = None) -> dict:
        if start < 0 or end <= start or end - start > 60_000:
            raise ValueError("Window must be positive and at most 60 seconds")
        sid = session_id or self.session_id
        if not sid:
            raise ValueError("No active session; wait for video or call start_session")
        oid = f"{sid}-{start}-{end}"
        existing = self.journal.observation(oid)
        if existing:
            return {**existing, "job_context": existing.get("job_context")}
        if sid != self.session_id:
            raise ValueError("Historical window is not retained; retrieve saved evidence instead")
        if end > self.elapsed_ms() + 10:
            raise ValueError("Observation window has not finished")
        if self.sessions:
            current = self.sessions.current
            if current is None or start < current['created_elapsed_ms']:
                raise ValueError("Observation window crosses the session boundary")
        self.buffer.evict(self.elapsed_ms())
        frames, gaps = self.buffer.select(start, end)
        observation = {"observation_id": oid, "session_id": self.session_id, "source": self.source,
                       "start_elapsed_ms": start, "end_elapsed_ms": end,
                       "brief": getattr(self, "observation_brief", None),
                       "job_context": self.sessions.current.get("job_context") if self.sessions and self.sessions.current else None,
                       "start_at": (self.started_utc + timedelta(milliseconds=start)).isoformat(),
                       "end_at": (self.started_utc + timedelta(milliseconds=end)).isoformat(),
                       "coverage": "empty" if not frames else "partial" if gaps else "complete",
                       "gaps": gaps, "frames": [f.metadata() for f in frames]}
        self.journal.save_observation(observation, frames)
        return observation

    def record_system(self, observation: dict, status: str, message: str) -> dict:
        entry = self.journal.record(observation["observation_id"], status=status, message=message)
        self.revision += 1
        return entry
