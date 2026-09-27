# FieldNotes — Physical Agency: speaker notes

Three-minute script (target 180 s; allocated 180 s). Times are per slide. The same text is embedded in the PPTX notes pane; this file adds delivery guidance and provenance.

Ground rules for the speaker:
- FieldNotes is the **temporal evidence and memory layer** of a physical-agency system. It is not a surveillance dashboard, a video summarizer, or a 3D reconstruction tool.
- A **human flies the drone** (DJI Fly / FocusTrack stay with the operator). FieldNotes observes video; it never controls flight.
- **MCP is an interface**, not a model. The agent's reasoning happens in whatever LLM sits behind the MCP client.
- The recorded proof is a **seated → standing posture change**. Never say a workstation inspection or a work assignment was completed.
- Timestamps are **local receipt time at decode**, not exposure time.

---

## 01 · Title — 12 s (0:00–0:12)

> Physical agency means an AI can help pursue a goal in the real world. FieldNotes builds one necessary part: a memory of physical observations that the agent can revisit.

Delivery: cover only, no stack. If asked, the drone is human-operated.

## 02 · Problem — 22 s (0:12–0:34)

> Consider a worker asking whether a station is ready for the next job. A useful agent needs more than a plausible answer. It needs recent evidence. Earlier images can be stale, and an object outside the camera view may still be present. Someone usually has to reconstruct that context.

Delivery: the job question is the *intended* workflow, not something FieldNotes has demonstrated end-to-end. Urgency is a design need for capable agents, not a market-trend claim. Don't say "existing video products can't search".

## 03 · Inspiration — 20 s (0:34–0:54)

> Research in robot planning separates high-level reasoning from executable skills. Dynamic-memory research shows why changing environments also matter. Our approach uses three responsibilities: the LLM reasons about the job, robot tools execute bounded capabilities, and Space Info carries observations through time. FieldNotes focuses on that third part.

Delivery: Observed / Last seen / Changed / Unknown are *information categories*, not a claim that all four are automatically resolved. "Robot tools" is a future integration (dotted box). FieldNotes implements no robot control, 3D localization, or DynaMem.

Sources (related research, not dependencies): SayCan <https://arxiv.org/abs/2204.01691>; DynaMem <https://arxiv.org/abs/2411.04999>; Gemini Robotics <https://deepmind.google/blog/gemini-robotics-brings-ai-into-the-physical-world/>; MCP tools spec <https://modelcontextprotocol.io/specification/2025-06-18/server/tools>.

## 04 · Solution Overview — 32 s (0:54–1:26)

> Here is evidence from our recorded session. An agent can search earlier images and inspect the originals. In the first frame, the person is seated beside a laptop. In the later frame, they are standing. That supports a posture change. It does not establish that a job finished, and the camera also reframed.

Provenance: session `80777bc1e2ac4fa8b2578015d93a332d`; `300.jpg` receipt 02:50:45.991 PDT and `370.jpg` receipt 02:50:59.918 PDT, 27 Sep 2026. Receipt time ≠ exposure time. The on-slide exchange is an evidence-based example, not a verbatim model transcript. The redesigned portal is not shown because it does not exist yet.

MCP tools exercised in the inspected session: `get_memory_status`, `list_sessions`, `get_observation_window`, `search_visual_history`, `get_evidence` (`src/fieldnotes/mcp_server.py`).

If frames 300/370 are still placeholders on the slide: say "the original frames live on the recording Mac" rather than describing an image that isn't there.

## 05 · Impact — 20 s (1:26–1:46)

> One recorded session received 712 frames over about 141 seconds and retained 148 evidence images. Those are verified prototype counts, not a productivity claim. They demonstrate that observations remain available for later inspection. Whether this reduces review time is the next evaluation question.

Numbers: 712 received frames (VERIFIED), 148 retained evidence images (VERIFIED), 140.707 s = last receipt − first receipt (CALCULATED). 148 is evidence *coverage*, not accuracy, compression efficiency, or labor savings. See `sources-and-claims.md` for the snapshot caveat.

## 06 · What Makes It Different — 22 s (1:46–2:08)

> The distinction is that evidence remains available to the agent when it needs context. A summary is useful, but it is still an interpretation. FieldNotes exposes earlier originals and temporal records through MCP. Our contribution is the integrated workflow. Reliability across different jobs remains to be demonstrated.

Delivery: compare workflows, not vendors. No moat is claimed; the potential advantage is accumulated workflow evaluation. Requesting a physical re-observation through a robot tool is future work.

## 07 · Technical Design — 32 s (2:08–2:40)

> The drone publishes video through DJI Fly. MediaMTX and FFmpeg decode timestamped frames. FieldNotes retains selected originals, generates vision summaries, and indexes evidence for local visual search. MCP lets an agent retrieve that history. The portal reads the same backend. Flight remains with the operator. A separate robot-control interface belongs to the future architecture.

Diagram legend: **solid** = implemented data flow; **dotted** = future boundary (Future robot MCP, NOT IMPLEMENTED). Selected sampled images leave the local Mac for the OpenAI API while observation is enabled — not all processing is local. Optional EdgeTAM selected-region tracking (`start_visual_track` / `get_visual_track` / `stop_visual_track`) is implemented but was not exercised in the inspected session. Excluded by design: Gaussian splatting, calibrated 3D maps, Core ML, automatic flight control.

Sources: `src/fieldnotes/{ingest,core,mcp_server,memory,perception}.py`, `docs/visual-memory.md`, `docs/sessions.md`; MobileCLIP2 <https://machinelearning.apple.com/research/mobileclip2>; EdgeTAM <https://github.com/facebookresearch/EdgeTAM>.

## 08 · Future Roadmap — 20 s (2:40–3:00)

> Next we will improve session review and evaluate interpretations across twenty annotated sessions. Then we will connect a bounded robot observation tool and check its returned evidence. The broader goal is physical agency where an agent can recognize that its knowledge is incomplete and obtain the next observation it needs.

Delivery: all three horizons are planned milestones. "20 sessions" is a proposed evaluation target (ILLUSTRATIVE). The long-term milestone is a *constrained observation* capability, not manipulation. There is no thank-you slide; stop on the closing line.

---

## Likely questions

- **Does it fly the drone?** No. The operator flies; FieldNotes ingests the RTMP stream.
- **Is the analysis local?** Ingest, evidence retention, SQLite records, and MobileCLIP2 retrieval are local. Vision summaries call the OpenAI API with selected sampled frames.
- **Do you know where objects are in 3D?** No calibrated world coordinates, distances, or persistent physical identities.
- **How accurate are the summaries?** Not yet measured across varied jobs — that is the medium-term milestone.
- **What is the 5-second interval?** Default configurable observation interval; frames are sampled, so events between samples can be missed.
