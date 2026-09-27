# FieldNotes — Physical Agency: sources and claims

Repository: `nixalpha/fieldnotes`, reviewed at commit `a07cbd8`.
Every factual claim on the deck is listed with its status: **IMPLEMENTED** (present in the codebase), **OBSERVED** (seen in the recorded session), **RESEARCH** (published external work), **ILLUSTRATIVE** (conceptual or proposed), or **PLANNED** (roadmap). Numeric claims also carry VERIFIED / RESEARCH / CALCULATED / ILLUSTRATIVE labels on the slide.

## 1. Numeric claims

| Slide | Claim | Label | Source | Notes |
|---|---|---|---|---|
| 05 | 148 retained evidence images | VERIFIED | `data/memory.sqlite3`, session `80777bc1e2ac4fa8b2578015d93a332d`, read-only | Snapshot recorded on the recording Mac; see caveat below |
| 05 | 712 received frames | VERIFIED | same | same |
| 05 | 140.7 s of observed footage | CALCULATED | last receipt `2026-09-27T09:52:08.075604+00:00` − first receipt `2026-09-27T09:49:47.368214+00:00` = 140.707 s | Receipt time at decode, not exposure time |
| 04 | frame 300 receipt 02:50:45.991 PDT, 27 Sep 2026 | VERIFIED | session evidence record | Local receipt time |
| 04 | frame 370 receipt 02:50:59.918 PDT, 27 Sep 2026 | VERIFIED | session evidence record | Local receipt time |
| 08 | 20 annotated sessions | ILLUSTRATIVE | proposed evaluation target | Not existing evidence |
| 07 | 5-second default observation interval (notes only) | IMPLEMENTED | README, `src/fieldnotes/core.py` config | Configurable |

**Caveat.** The verification checkout used to build this deck does not contain `data/`. The 712 / 148 / 140.707 figures are a previously recorded snapshot taken on the recording Mac from the frozen session database and were **not independently re-verified** during the build. Re-run the read-only counts before presenting if the database is available.

## 2. Evidence provenance

| Deck reference | Original | Status in this package |
|---|---|---|
| Slide 04, left frame | `data/evidence/80777bc1e2ac4fa8b2578015d93a332d/300.jpg` — person seated beside an open laptop | **PLACEHOLDER** — file not present in checkout; copy from recording Mac |
| Slide 04, right frame | `data/evidence/80777bc1e2ac4fa8b2578015d93a332d/370.jpg` — person standing | **PLACEHOLDER** — file not present in checkout; copy from recording Mac |
| Notes only | frame 358 — person still seated, turned toward camera | reference only |
| Notes only | frame 376 — person farther right as camera reframes | reference only; supports "camera reframed" caveat |
| Notes only | frame 442 — compression corruption | limitation example, not shown |
| Notes only | frame 454 — person facing away | reference only |

See `assets/evidence/MANIFEST.md` for the copy manifest. No databases, credentials, full session directories, or model caches are packaged.

## 3. Capability claims

### IMPLEMENTED (in repository)
- DJI + phone RTMP publish → MediaMTX → FFmpeg ingest with browser preview (`src/fieldnotes/ingest.py`, README).
- Timestamped sessions with retained original JPEG evidence (`docs/sessions.md`, `src/fieldnotes/sessions.py`).
- Configurable observation interval, default five seconds.
- OpenAI vision summaries via the Responses API (`src/fieldnotes/model.py`); selected sampled images are sent to the API.
- Temporal interpretation records in SQLite (`src/fieldnotes/memory.py`, `docs/visual-memory.md`).
- MobileCLIP2-S0 local visual retrieval (`src/fieldnotes/perception.py`).
- Optional EdgeTAM selected-region tracking (`start_visual_track`, `get_visual_track`, `stop_visual_track`).
- FieldNotes MCP (FastMCP, Streamable HTTP) exposing status, session, observation, evidence, search, memory, and tracking tools (`src/fieldnotes/mcp_server.py`).
- Portal served from the same backend (`src/fieldnotes/app.py`, `static/`).

### OBSERVED (recorded session `80777bc1…`)
- Posture change from seated (frame 300) to standing (frame 370).
- Camera reframing between frames (frame 376).
- Compression corruption on at least one frame (frame 442).
- MCP tools exercised: `get_memory_status`, `list_sessions`, `get_observation_window`, `search_visual_history`, `get_evidence`.
- EdgeTAM tracking was **not** exercised in this session.

### RESEARCH (external; grounding, not dependencies)
- SayCan — separating high-level reasoning from executable skills: <https://arxiv.org/abs/2204.01691>
- DynaMem — dynamic spatio-semantic memory for changing environments: <https://arxiv.org/abs/2411.04999>
- Gemini Robotics — AI into the physical world: <https://deepmind.google/blog/gemini-robotics-brings-ai-into-the-physical-world/>
- MCP server tools specification (2025-06-18): <https://modelcontextprotocol.io/specification/2025-06-18/server/tools>
- Apple MobileCLIP2: <https://machinelearning.apple.com/research/mobileclip2>
- EdgeTAM (official repository): <https://github.com/facebookresearch/EdgeTAM>

### ILLUSTRATIVE
- "Is the workstation ready for the next job?" — intended workflow, not a completed demonstration.
- Slide 04 agent exchange — evidence-based example, not a verbatim model transcript.
- Observed / Last seen / Changed / Unknown — information categories, not automatically resolved states.
- Cover, problem, and roadmap imagery — generated concept art (see `image-prompts.md`), labeled on-slide as not recorded evidence.
- Frame-sampling motif on slide 05 — not to scale.

### PLANNED (roadmap; not achieved)
- Session review and cited process timelines.
- Change-interpretation evaluation across 20 annotated sessions.
- One bounded robot observation tool connected through a separate robot MCP, with returned evidence verified.

## 4. Explicitly NOT claimed
- Autonomous drone navigation or robot manipulation (human controls flight).
- Calibrated world coordinates, exact distances, or persistent physical identities.
- Gaussian splatting or any current 3D world model.
- Core ML or fully local inference (vision summaries use the OpenAI API).
- Automatic session topic organization or a redesigned portal.
- Quantified productivity improvement, labor savings, or broadly validated semantic accuracy.
- Completed workstation inspection or completed work assignment.
- A defensible moat.

## 5. Limitations to state when asked
- Evidence is sampled at the observation interval; events between samples are missed.
- Timestamps are local receipt at decode, not exposure time.
- Vision summaries are interpretations and can be wrong.
- Compression artifacts occur on the RTMP path (frame 442).
- Reliability across varied physical jobs has not been demonstrated.

## 6. Constraint verification (build output)

| Constraint | Result |
|---|---|
| Exactly 8 slides, instruction slide removed | pass |
| 16:9, 10 × 5.625 in | pass |
| Problem + Inspiration ≤ 200 words | 138 |
| Differentiation ≤ 100 words | 54 |
| Technical design ≤ 150 words, editable diagram | 66, native shapes/connectors |
| Roadmap ≤ 75 words | 44 |
| Solution overview has a visual | evidence workflow panel + frame placeholders |
| ≥ 1 supported impact number | 148 / 712 (VERIFIED), 140.7 s (CALCULATED) |
| Deck usable offline | no live server or network references; fonts fall back if Aptos absent |
