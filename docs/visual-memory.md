# Visual memory

FieldNotes retains the original RTMP observer and adds temporal interpretations, MobileCLIP2-S0 image retrieval, and point-prompted EdgeTAM tracking. The source baseline is commit `01de0febb54a35bd5a8c18bb320858cf2b000a7d`. No reconstruction or drone actuation is involved.

## Setup

From the FieldNotes directory:

```bash
uv sync --extra perception
uv run --extra perception fieldnotes setup-memory-models
```

Setup explicitly downloads public weights and the pinned official EdgeTAM source into ignored `data/models/`. It preserves upstream licenses and writes revisions and SHA-256 hashes into `models.json`. Model execution uses local assets; it does not upload images. The exact dependency resolution is in `uv.lock`.

Ordinary preview and summaries can still run with `uv sync` without the optional perception dependencies. Missing weights are visible in memory status. When using the optional models, keep `--extra perception` on `uv run` so uv does not remove the extra dependencies.

Configuration:

```dotenv
OPENAI_MODEL=gpt-6-luna
FIELDNOTES_MEMORY_MODELS=data/models
FIELDNOTES_MODEL_DEVICE=auto
FIELDNOTES_TRACK_DEVICE=cpu
```

`FIELDNOTES_MODEL_DEVICE=auto` prefers MPS for MobileCLIP; `cpu` forces CPU search. EdgeTAM has a separate `FIELDNOTES_TRACK_DEVICE` setting: `cpu` (default) and `auto` use CPU because this setup produced invalid MPS outputs. Explicit `mps` is experimental. EdgeTAM non-finite outputs are rejected and trigger a fresh CPU predictor; simply moving the old model is insufficient because upstream positional encodings cache device-specific tensors. No throughput or memory claims have been established. The live API model remains configurable through `.env`.

## Live operation

```bash
uv run --extra perception fieldnotes dev
```

Retained summary evidence is indexed in the background. While a visual track is active, decoded frames are additionally retained for that track. Indexing and model inference do not run on the video/API event loop. A bounded frame subscription may skip inputs under load; tracking continuity remains tentative. Epoch changes or more than three seconds between retained observations stop the track. This threshold describes observation continuity, not proof of a network outage.

The built-in agent proposes evidence-cited memory records alongside summaries. Summary persistence is independent from memory persistence. Failed memory writes appear in agent status and rejected proposals are retained for correction/retry through `record_memory`. Reusing an idempotency key with different content is refused. A corrected proposal that was never accepted may reuse its key; revising an accepted interpretation requires a new key and optional `revises` reference.

## Isolated saved-image replay

```bash
uv run --extra perception fieldnotes replay-memory \
  data/evidence/41d59093746a404e8ff0adee79493ec0 \
  --source-journal data/journal.sqlite3
```

This prints a new path under `data/memory-runs/`. It copies and hashes originals, preserves source timestamps and metadata, and indexes every image. The source journal is opened read-only. Source frame IDs are scoped by the new replay session; original session provenance is retained.

The bundled fixture authoring logic is specifically for the provided session: frames 177, 222, and 451 have image-reviewed descriptions; other windows explicitly make no additional semantic claim. Use `--fixture-file` for a different explicitly authored fixture set. Fixtures must match the generated observation IDs and selected frame IDs. Missing fixtures are errors.

Start the resulting archive (replace `RUN_DIRECTORY`):

```bash
uv run --extra perception fieldnotes view-memory RUN_DIRECTORY --port 8002
uv run --extra perception fieldnotes exercise-memory-mcp RUN_DIRECTORY --port 8002
```

The viewer uses port 8001 by default and selects 8002 if 8001 is occupied. An explicitly occupied other port produces an error. No existing process is stopped. The viewer prints the actual URLs; use that port for the MCP exercise.

Archive mode never launches RTMP, FFmpeg, a live LLM capability check, or paid model requests, even when `.env` contains credentials. It persists authored mock results only in the isolated run. The 5-second windows use original receipt times and a logical clock; irregular sampling is not labeled network loss. All input images are retained, even when a summary window selects only five.

The exercise calls the actual HTTP MCP server with the official SDK. It records image hashes, tool metadata, errors, mock outputs, real search results, and real tracking results. It does not benchmark. A PASS for a lost track means the functional loss path executed; it does not mean tracking was accurate. Explicit tracking model errors remain failures.

To inspect persistence after an orderly server restart:

```bash
uv run --extra perception fieldnotes exercise-memory-mcp RUN_DIRECTORY \
  --port 8002 --no-start-tracks
```

This repeats idempotent memory writes and reads existing track assets. It does not create new tracks. Reports are under `exercise-report.json`, `mcp-transcript.jsonl`, and `review.md`. The transcript is append-only. The exercise includes an intentionally false, clearly labeled conflict fixture; it must not be interpreted as an observation of an actual removal.

## Portal

1. Choose a session under Visual memory.
2. Search with a natural-language description. Similarity scores are retrieval ranks, not probabilities.
3. Select a result or move the saved-observation slider.
4. Inspect the original, last-observed interpretations, and before/after evidence.
5. Click an item on the original image; give the selection a label.
6. Preview the seed mask. Add foreground/background points if needed, then start tracking.
7. Inspect mask/original/overlay triplets. Stop preserves history. Failed attempts with a later completed run using the same seed and covering their retained frames appear under Recovered attempts; unresolved failures stay visible. Recovery links never turn old invalid masks into valid evidence. Refinement by starting again creates a separate selection; it does not rewrite prior tracks.

The first point can select only part of an item. In the sample run the chair point selects its seat rather than the entire chair. A successful inference call does not validate a mask. Moving cameras, small items, image corruption and sparse observations can cause drift or loss.

## Storage and temporal semantics

- `journal.sqlite3`: original observation/summary schema.
- `memory.sqlite3`: evidence references, assertions, relationships expressed as triples, corrections, change candidates, citation bundles, idempotent writes, rejected proposals, tracks and frame metadata.
- `evidence/<session>/<frame>.jpg`: originals.
- `embeddings/`: versioned numeric vectors with source-image hashes.
- `tracks/<track>/`: masks and derived overlays; originals are referenced rather than overwritten.
- `fixtures/`: authored replay interpretations and point selections.

Unknown visibility is not removal. Last observed is not currently known. Corrections preserve earlier interpretations. The portal can show conflicting fixtures because validating a citation proves reference integrity, not semantic truth. Source timestamps are local video receipt times, not exact exposure times. Model quality signals are not calibrated certainty.

The working set uses one indexing job, at most one search request, three selected tracks and eight frames per tracking window. Search scans vectors in bounded batches. At window boundaries, a previous mask is explicitly reused as a new prompt. The latest 20 track events are included in status; the full event history remains in SQLite. History is not automatically deleted; the operator remains responsible for available disk space.

## MCP

Connect to `http://127.0.0.1:8000/mcp` for live mode or the archive's printed port. Original tools remain compatible.

| Tool | Purpose |
| --- | --- |
| `get_memory_status` | Sessions, indexing coverage, model errors/devices, tracks, rejected proposals |
| `search_visual_history` | Native original images with scores, timestamps, text matches and citation bundle |
| `get_memory_state` | Latest interpretations and recent supporting history at a requested elapsed time |
| `get_change_history` | Contradictions, corrections, coverage uncertainty and change candidates |
| `get_evidence` | 1–20 originals and server-issued evidence bundle |
| `record_memory` | Idempotent proposal write; same-session bundle references required |
| `start_visual_track` | Original pixel points with foreground/background labels; optional seed-only preview |
| `get_visual_track` | Paginated original/mask/overlay image blocks with explicit order |
| `stop_visual_track` | Stop without deleting history |

An evidence ID is `<session_id>:<frame_id>`. A caller supplies a `MemoryProposal` containing `session_id`, `idempotency_key`, `evidence_bundle_ids`, and assertions. Each assertion has `subject`, `predicate`, `value`, `evidence_ids`, `epistemic` (`visually_supported`, `inferred`, `unknown`), and optional uncertainty/correction/track references. Mock provenance is assigned by the server and cannot be promoted by a caller.

## Manual checks

- Inspect the 55-file manifest and source hashes.
- Search laptop, handheld device, floor/chair, and room overview; judge relevance visually.
- Review temporal state before and after the low-angle view. It must not assert laptop removal.
- Inspect frame 222 and masks at 177, 222, 303, 378, and 451 where tracking reached them.
- Inspect explicit errors and tracking loss; do not treat missing masks as missing physical objects.
- Preview and refine a seed; stop a track and confirm assets remain.
- Restart the archive viewer and retrieve originals/history through MCP.
- In a separate live session, manually check preview/summaries without optional weights, stream reconnect, and tracking under load.

The saved-image exercise does not establish live ingestion behavior, physical identity, metric geometry, real LLM accuracy, or real-time performance. No unrelated test suites or benchmarks are part of this implementation exercise.
