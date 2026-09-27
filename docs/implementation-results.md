# Temporal visual memory implementation results

## Source and preservation

Implemented on `codex/temporal-visual-memory` from commit `01de0febb54a35bd5a8c18bb320858cf2b000a7d`. Changes are uncommitted. At implementation start, `main` had already restored the baseline and added the handoff document; no spatial source remained to remove. The prior branch/history and handoff were preserved in `data/implementation-backups/20260927T085019Z/`, including a Git bundle and inventories. Credentials, original journal/evidence, calibration, depth caches and reconstruction archives remain in place. WorldGit was not modified.

## Delivered

1. A separate SQLite temporal memory with scoped evidence, server-issued citation bundles, idempotent writes, fallible assertions, corrections, conflicts, changes and explicit unknown states.
2. MobileCLIP2-S0 visual retrieval and interpretation text search, with local versioned embeddings, bounded exact scanning and native MCP images.
3. Selective EdgeTAM temporal tracking with point prompts, short windows, boundary-mask reinitialization, saved masks/overlays, discontinuity handling and a maximum of three active selections.

The portal includes search, a retained-image timeline, historical interpretations and before/after evidence, seed preview/refinement, tracking controls and explicit model errors. The original four MCP tools remain; nine memory/tracking tools were added. The built-in live agent reads bounded historical memory and proposes separately persisted memory alongside its summaries. The configured `gpt-6-luna` invocation is retained; no model migration was performed.

Optional perception dependencies are pinned in `pyproject.toml` and fully resolved in `uv.lock`. Official model revisions/checkpoint hashes and the pinned EdgeTAM source revision are recorded under `data/models/models.json`; upstream licenses are retained. The implementation uses native PyTorch, not Core ML.

## Reproduce the completed run

Run from `/Users/macos/coding/FieldNotes`:

```bash
uv sync --extra perception
uv run --extra perception fieldnotes setup-memory-models
uv run --extra perception fieldnotes view-memory \
  data/memory-runs/20260927T085657Z-7b67ac56 --port 8002
```

In another terminal:

```bash
uv run --extra perception fieldnotes exercise-memory-mcp \
  data/memory-runs/20260927T085657Z-7b67ac56 \
  --port 8002 --no-start-tracks
```

Dashboard: <http://127.0.0.1:8002/>. MCP: <http://127.0.0.1:8002/mcp>.
The viewer is running at delivery. The existing service on port 8001 was left untouched. Archive mode disables RTMP, FFmpeg, live-model initialization and paid model requests.

To make another isolated run:

```bash
uv run --extra perception fieldnotes replay-memory \
  data/evidence/41d59093746a404e8ff0adee79493ec0 \
  --source-journal data/journal.sqlite3
# Use the printed directory with view-memory and exercise-memory-mcp.
```

## Saved outputs

Run directory: `data/memory-runs/20260927T085657Z-7b67ac56/`.
Replay session: `70d740e0bb2d4aa0925666c80d321e6c`.
Source session: `41d59093746a404e8ff0adee79493ec0`.

- `manifest.json`: 55 inputs with original metadata and hashes.
- `exercise-report.json`: final 26-scenario result after restart, all passing.
- `mcp-transcript.jsonl`: append-only tool calls, metadata, image hashes and mocked answer.
- `journal.sqlite3`: 11 explicitly authored mock summary windows.
- `memory.sqlite3`, `embeddings/`, `tracks/`: retained memory, real embeddings and real tracking outputs.
- `manual-review.md`: qualitative review and limitations, retained separately from repeatable exercise reports.
- `exercise-report-initial-mps.json`: initial failed tracking diagnostic report.

Completed CPU track IDs:

- Chair seat: `7a2b534f07cd4f4f8564618dda0763de`, 55 frames.
- Laptop screen: `249d7308d90d4900b6bf69f73f56f497`, 55 frames.


## Completed manual review and restart

- Restarted the archive server, reran the actual MCP exercise with `--no-start-tracks`, and obtained 26 passing functional scenarios with no failures. Persisted search, mock interpretations, and original/mask/overlay retrieval remained available. This is a scoped functional exercise, not a tracking-accuracy result.
- Reviewed the portal after restart: 55 retained images, 55 indexed, two completed 55-frame tracks, historical uncertainty and explicit contradictory interpretations. Historical diagnostic errors remain visible.
- Reviewed chair and laptop overlays at frames 177, 222, 303, 378, and 451. The chair selection follows the seat/underside, not the whole chair; boundaries can include adjacent parts. The laptop selection follows the visible screen surface and becomes a partial upper-image region in the low view. These are tentative masks, not validated persistent identities.
- Frame 222 remains visibly corrupted. The CPU tracker still emitted masks at this frame; accepted propagation does not establish that corruption was harmless.
- Reviewed top search examples: laptop frame 309, handheld-device frame 360, floor/chair frame 422, and room-overview frame 252. The visible content is relevant to those queries. No retrieval metric was measured.
- All 55 original source JPEG hashes still match the import manifest. Original evidence was not edited.
- Initial MPS tracking produced non-finite values. A first CPU fallback attempt exposed upstream device-specific positional-encoding caches. The final implementation rejects non-finite values and creates a fresh CPU predictor. Two subsequent CPU tracks completed all 55 retained images. Earlier invalid/error runs are explicitly marked and preserved.

## Mock agent example through MCP

Question: **Was the laptop removed?**

Authored mock answer: **The laptop was visible earlier. The later low-angle image does not show enough of that area to establish whether it was removed.**

The exercise retrieved search candidates, queried historical memory, and returned original frames 177 and 451 through native MCP image blocks. This demonstrates the evidence workflow; it is not independent LLM reasoning. A deliberately false conflict fixture remains marked as such and must not be treated as a real removal event.

## Still requires manual live checks

Preview/summaries with missing optional dependencies, live RTMP reconnect, sustained live indexing/tracking, seed refinement and stop controls, and the paid live model's expanded structured output were not exercised here. No broad test suite, performance measurements, physical accuracy claims, or paid API calls were used.

## Setup, operation and manual procedures

See [visual-memory.md](visual-memory.md) for model setup, CLI options, storage semantics, MCP tools, portal operation and remaining manual checks. The scoped MCP exercise is the only scripted functional validation added/run for this task. No performance benchmark or unrelated test suite was run.

## Follow-up: tracking device errors and recovered attempts

EdgeTAM now defaults directly to CPU through `FIELDNOTES_TRACK_DEVICE=cpu`, independently of MobileCLIP's MPS search setting. Predictor state stays on the model device. Explicit MPS tracking remains experimental, with invalid numerical outputs rejected and the existing fresh-CPU-predictor fallback retained.

The four historical failures are linked to later completed runs only when seed/session/prompts match and the completed run covers every retained failed-run frame. They appear in a collapsed Recovered attempts section, with links to successful runs. Failure records and diagnostic artifacts are preserved; unresolved errors remain visible. Zero-frame failures no longer offer an empty mask inspection.

Focused verification: real CPU inference produced nonempty masks for an initial two-frame window and a subsequent two-frame window carrying its boundary mask across three saved images, with no model error or fallback. Actual MCP reads reported four recovered attempts and no unresolved track errors. The refreshed portal shows the two completed 55-frame CPU runs and the collapsed recovery history. This is a functional check, not a mask accuracy or performance result.
