from __future__ import annotations

import asyncio
import base64
import json

from mcp.server.fastmcp import FastMCP
from mcp.types import ImageContent, TextContent
from pydantic import BaseModel, Field

from .core import Runtime, VisualSummary
from .memory_store import MemoryProposal


class GenerationMetadata(BaseModel):
    model: str = Field(default="external", max_length=100)
    duration_ms: int = Field(default=0, ge=0)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)


def make_mcp(runtime: Runtime) -> FastMCP:
    server = FastMCP("FieldNotes", stateless_http=True, json_response=True, log_level="WARNING")

    memory = getattr(runtime, "memory", None)

    @server.tool()
    async def list_sessions(limit: int = 50, cursor: str | None = None) -> dict:
        """List recording sessions, including historical and waiting-for-video sessions."""
        return runtime.sessions.list(limit, cursor)

    @server.tool()
    async def start_session(request_id: str, name: str | None = None,
                            expected_active_session_id: str | None = None) -> dict:
        """Create a recording session. Supply the current active ID to rotate it.

        request_id is an idempotency key. Without video the session waits for its
        first frame. Archive servers reject lifecycle mutations.
        """
        return runtime.sessions.start(request_id, name, expected_active_session_id)

    @server.tool()
    async def end_session(session_id: str, request_id: str, reason: str | None = None) -> dict:
        """End exactly this recording session. The next decoded frame starts another.

        Retrying request_id is idempotent. An already-ended ID never ends a replacement.
        """
        return runtime.sessions.end(session_id, request_id, reason)

    @server.tool()
    def get_stream_status() -> dict:
        """Read stream freshness and buffer coverage. Timestamps describe local receipt."""
        return runtime.status()

    @server.tool(structured_output=False)
    async def get_observation_window(start_elapsed_ms: int, end_elapsed_ms: int, session_id: str | None = None) -> list:
        """Get up to five JPEG images from a completed half-open window (at most 60s).

        The first content block is JSON metadata. Following image blocks match frames in order.
        Evidence is persisted. Empty/partial coverage is explicit; no unseen action is implied.
        """
        observation = runtime.observe(start_elapsed_ms, end_elapsed_ms, session_id)
        if memory and observation["frames"]:
            frames = memory.ingest_observation(observation)
            bundle = memory.store.bundle([f["id"] for f in frames])
            observation = {**observation, "evidence_bundle_id": bundle["evidence_bundle_id"],
                           "evidence_ids": [f["id"] for f in frames]}
        return [TextContent(type="text", text=json.dumps(observation)), *[
            ImageContent(type="image", mimeType="image/jpeg", data=base64.b64encode(jpeg).decode())
            for jpeg in runtime.journal.images(observation)
        ]]

    @server.tool()
    async def get_recent_summaries(limit: int = 3, session_id: str | None = None) -> dict:
        """Read recent completed summaries in this session as prior interpretations, not ground truth."""
        if not 1 <= limit <= 20:
            raise ValueError("limit must be between 1 and 20")
        sid = session_id or runtime.session_id
        if sid is None:
            return {"summaries": []}
        entries = runtime.journal.list(1000, session_id=sid)
        return {"summaries": [e for e in entries if e["status"] == "completed"][-limit:]}

    @server.tool()
    def record_summary(observation_id: str, summary: VisualSummary,
                       generation: GenerationMetadata | None = None) -> dict:
        """Persist a summary once. Action evidence must cite frame IDs in this observation."""
        generation = generation or GenerationMetadata()
        if memory and memory.store.provenance == "mock":
            generation.model = "fixture-model" if memory.archive else "stub-no-vision"
            generation.input_tokens = generation.output_tokens = 0
        entry = runtime.journal.record(
            observation_id, summary, model=generation.model, duration_ms=generation.duration_ms,
            usage={"input_tokens": generation.input_tokens, "output_tokens": generation.output_tokens})
        runtime.revision += 1
        return entry

    if memory:
        register_memory_tools(server, memory)
    return server


def image_blocks(metadata, images):
    return [TextContent(type="text", text=json.dumps(metadata)), *[
        ImageContent(type="image", mimeType=mime, data=base64.b64encode(data).decode()) for mime, data in images]]


def evidence_blocks(memory, ids, extra=None):
    bundle = memory.store.bundle(ids)
    return image_blocks({**bundle, **(extra or {})}, [
        ("image/jpeg", memory.store.asset(f["asset"]).read_bytes()) for f in bundle["frames"]])


def register_memory_tools(server, memory):
    @server.tool()
    def get_memory_status() -> dict:
        """Discover archive sessions, indexing coverage, model availability and tentative tracks."""
        return memory.status()

    @server.tool(structured_output=False)
    async def search_visual_history(query: str, session_id: str, start_elapsed_ms: int = 0,
                                    end_elapsed_ms: int | None = None, limit: int = 6) -> list:
        """Search original images. Similarity is not probability or proof. Inspect returned originals."""
        result = await memory.search(query, session_id, start_elapsed_ms, end_elapsed_ms, limit)
        if not result["results"]:
            return image_blocks({**result, "frames": [], "status": "no_indexed_matches"}, [])
        return evidence_blocks(memory, [r["evidence"]["id"] for r in result["results"]], result)

    @server.tool(structured_output=False)
    def get_evidence(evidence_ids: list[str]) -> list:
        """Return 1..20 original JPEGs and a server-issued citation bundle scoped to one session."""
        return evidence_blocks(memory, evidence_ids)

    @server.tool()
    def get_memory_state(session_id: str, at_elapsed_ms: int | None = None) -> dict:
        """Read last-observed interpretations, supporting history and uncertainty, not present truth."""
        return memory.store.state(session_id, at_elapsed_ms)

    @server.tool()
    def get_change_history(session_id: str, start_elapsed_ms: int = 0,
                           end_elapsed_ms: int | None = None, limit: int = 100) -> dict:
        """Changes are candidates, coverage uncertainty or contradictions; compare supporting originals."""
        return memory.store.changes(session_id, start_elapsed_ms, end_elapsed_ms, limit)

    @server.tool()
    def record_memory(proposal: MemoryProposal) -> dict:
        """Persist fallible interpretations citing retrieved bundles. Provenance is server-assigned."""
        key = proposal.session_id + ":" + proposal.idempotency_key
        try:
            result = memory.store.record(proposal)
        except Exception as exc:
            memory.store.execute("INSERT OR REPLACE INTO processing VALUES(?,?)", (key,json.dumps({
                "status":"rejected", "proposal":proposal.model_dump(), "error":str(exc),
                "recovery":"Correct the proposal or reissue record_memory with the same idempotency key after a transient failure."})))
            raise
        memory.store.execute("DELETE FROM processing WHERE id=?", (key,))
        return result

    @server.tool()
    async def start_visual_track(evidence_id: str, points: list[list[float]], labels: list[int],
                                 label: str, preview_only: bool = False) -> dict:
        """Start real EdgeTAM tracking from original-image pixel points (1 foreground, 0 background).
        Use preview_only to inspect a seed mask before propagation. At most three active selections.
        """
        return memory.start_track(evidence_id, points, labels, label, preview_only)

    @server.tool(structured_output=False)
    def get_visual_track(track_id: str, limit: int = 4, after: int = 0) -> list:
        """Read tentative tracking with originals, masks and overlays in explicit order; page using after."""
        if not 1 <= limit <= 8 or after < 0: raise ValueError("limit 1..8, after >= 0")
        track = memory.store.track(track_id)
        rows = memory.store.track_frames(track_id, limit, after)
        metadata = {"track": track, "track_frames": rows, "next_after": after + len(rows), "image_order": []}
        images = []
        if rows:
            bundle = memory.store.bundle([r["evidence_id"] for r in rows])
            metadata["evidence_bundle_id"] = bundle["evidence_bundle_id"]
        for row in rows:
            for kind, asset, mime in [("original", row["frame"]["asset"], "image/jpeg"),
                                      ("mask", row["mask_asset"], "image/png"),
                                      ("overlay", row["overlay_asset"], "image/jpeg")]:
                metadata["image_order"].append({"kind": kind, "evidence_id": row["evidence_id"]})
                images.append((mime, memory.store.asset(asset).read_bytes()))
        return image_blocks(metadata, images)

    @server.tool()
    async def stop_visual_track(track_id: str) -> dict:
        """Stop a selection while retaining masks and source evidence."""
        return memory.stop_track(track_id)

