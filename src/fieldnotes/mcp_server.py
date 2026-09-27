from __future__ import annotations

import base64
import json

from mcp.server.fastmcp import FastMCP
from mcp.types import ImageContent, TextContent
from pydantic import BaseModel, Field

from .core import Runtime, VisualSummary


class GenerationMetadata(BaseModel):
    model: str = Field(default="external", max_length=100)
    duration_ms: int = Field(default=0, ge=0)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)


def make_mcp(runtime: Runtime) -> FastMCP:
    server = FastMCP("FieldNotes", stateless_http=True, json_response=True, log_level="WARNING")

    @server.tool()
    def get_stream_status() -> dict:
        """Read stream freshness and buffer coverage. Timestamps describe local receipt."""
        return runtime.status()

    @server.tool(structured_output=False)
    def get_observation_window(start_elapsed_ms: int, end_elapsed_ms: int) -> list:
        """Get up to five JPEG images from a completed half-open window (at most 60s).

        The first content block is JSON metadata. Following image blocks match frames in order.
        Evidence is persisted. Empty/partial coverage is explicit; no unseen action is implied.
        """
        observation = runtime.observe(start_elapsed_ms, end_elapsed_ms)
        return [TextContent(type="text", text=json.dumps(observation)), *[
            ImageContent(type="image", mimeType="image/jpeg", data=base64.b64encode(jpeg).decode())
            for jpeg in runtime.journal.images(observation)
        ]]

    @server.tool()
    def get_recent_summaries(limit: int = 3) -> dict:
        """Read recent completed summaries in this session as prior interpretations, not ground truth."""
        if not 1 <= limit <= 20:
            raise ValueError("limit must be between 1 and 20")
        entries = runtime.journal.list(1000, session_id=runtime.session_id)
        return {"summaries": [e for e in entries if e["status"] == "completed"][-limit:]}

    @server.tool()
    def record_summary(observation_id: str, summary: VisualSummary,
                       generation: GenerationMetadata | None = None) -> dict:
        """Persist a summary once. Action evidence must cite frame IDs in this observation."""
        generation = generation or GenerationMetadata()
        entry = runtime.journal.record(
            observation_id, summary, model=generation.model, duration_ms=generation.duration_ms,
            usage={"input_tokens": generation.input_tokens, "output_tokens": generation.output_tokens})
        runtime.revision += 1
        return entry

    return server
