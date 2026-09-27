# FieldNotes: temporal visual memory implementation handoff

## Instructions to the implementing agent

Implement this plan in the supplied FieldNotes repository. This document is self-contained; no previous conversation is required. Inspect the repository and applicable `AGENTS.md` instructions before changing files.

The requested work is to restore the application source to commit `01de0febb54a35bd5a8c18bb320858cf2b000a7d`, then implement three features:

1. Evidence-backed temporal memory, using Graphiti-inspired semantics in SQLite.
2. Visual history retrieval using MobileCLIP2-S0.
3. Selective region tracking using EdgeTAM.

Exercise the implementation with the existing 55 saved images, real embedding and tracking inference, and explicitly mocked LLM outputs through the actual MCP server. Complete the implementation and the scoped offline MCP exercise; do not stop after proposing another plan.

Do not add DynaMem, Gaussian splatting, depth reconstruction, SLAM, a local generative model, autonomous flight commands, or a general object-identity registry.

This document describes intended work, not completed implementation or passed tests.

## 1. Repository and known starting state

Expected repository location on the original machine:

`/Users/macos/coding/FieldNotes`

Treat paths below as relative to the supplied repository unless absolute. Do not modify the former WorldGit repository.

At handoff preparation, `HEAD` was already the target commit. Reconstruction features existed as uncommitted tracked changes and untracked additions. Recheck the actual state before acting; do not assume it remains identical.

The target commit contains:

- Python 3.12 application managed with `uv`.
- FastAPI dashboard, MJPEG preview, REST endpoints, and SSE.
- MediaMTX RTMP receiver and FFmpeg ingestion.
- Timestamped JPEG frames at 5 FPS, with a bounded in-memory buffer.
- SQLite summary journal and retained JPEG evidence.
- Official Python MCP SDK using Streamable HTTP on localhost.
- A built-in agent that actually connects through MCP.
- Five-second observation windows, up to five images per summary, one model request in flight and one latest pending window.
- A basic stub model that produces placeholder summaries, not useful mocked interpretations.
- Plain HTML/CSS/JavaScript frontend.

Useful baseline source files:

- `src/fieldnotes/core.py`: frames, buffer, journal, runtime.
- `src/fieldnotes/ingest.py`: FFmpeg decoder.
- `src/fieldnotes/agent.py`: scheduler and MCP client.
- `src/fieldnotes/model.py`: model adapter and structured summaries.
- `src/fieldnotes/mcp_server.py`: original MCP tools.
- `src/fieldnotes/app.py`: dashboard/API and application lifecycle.
- `src/fieldnotes/cli.py`: operational commands.
- `src/fieldnotes/static/`: dashboard assets.

The original MCP tools are `get_stream_status`, `get_observation_window`, `get_recent_summaries`, and `record_summary`. Preserve compatibility with these tools.

The target commit defaults to `gpt-4o-mini`. Later working-tree changes used `gpt-6-luna` with model-specific request settings. Preserve the actual current configured model and relevant invocation settings after the source restoration. Inspect configuration without printing credentials. This task does not request a model migration.

## 2. Outcome and scope

The agent should be able to:

- Recover relevant earlier images rather than relying only on recent summaries.
- Query what was observed at a particular time.
- Inspect evidence-backed changes, contradictions, and uncertainty.
- Follow a user-selected region through nearby frames when tracking is usable.
- Distinguish last observed state from currently known state.
- Cite original source evidence for interpretations.

Physical agency in this milestone means better observation, memory, and contextual reasoning. There is no physical actuator or drone-control integration.

## 3. Restore the baseline without losing work or data

Before restoration:

1. Inspect Git status, commit history, tracked changes, and untracked files.
2. Save staged and unstaged changes in recoverable form, including binary changes.
3. Archive explicitly inventoried untracked source, documentation, and frontend files separately. Preserve this handoff document too.
4. Record the commit, file inventory, and backup locations.
5. Coordinate graceful shutdown of FieldNotes processes using the checkout before replacing their source. Identify processes by ownership and command; do not kill unrelated processes by name or port.

Preserve in place:

- `.env` and credentials.
- Original `data/journal.sqlite3`, including any SQLite sidecar files.
- All original `data/evidence/` images.
- Calibration profiles and cached model weights.
- Spatial databases and existing reconstruction-run archives.
- Any unrelated user changes discovered during inspection.

Create a branch using the `codex/` prefix, preferably `codex/temporal-visual-memory`, from the target commit. If that branch already exists, inspect it rather than overwriting it.

Restore the reviewed application source to the target commit. Since the known changes are uncommitted, this is a working-tree restoration, not `git revert` of the initial commit. If new commits or unrelated work are present, preserve them before adapting the restoration procedure.

Remove only inventoried reconstruction additions from the active application, including depth/spatial services, spatial MCP tools, Three.js viewer, and reconstruction-only frontend tooling. Do not use blanket `git clean -fdx` or delete ignored data.

Keep historical artifacts on disk even though the new application will not serve the old reconstruction interface. Deliberately reapply the current live-summary model configuration after the baseline restoration.

## 4. Temporal memory

Use a new `memory.sqlite3`; leave the original summary-journal schema intact.

Reference: [Graphiti](https://github.com/getzep/graphiti).

Adopt temporal assertions, provenance, and preserved corrections. Do not install Graphiti or a separate graph database in this MVP.

### Records

| Record | Required information |
| --- | --- |
| Evidence | Run/session, source session, frame ID, UTC receipt time, elapsed time, stream epoch, hash, dimensions, asset reference |
| Assertion | Local subject reference, predicate, value, evidence references, uncertainty, interpretation source, creation time |
| Relationship | Observation-scoped links between selected regions or described scene elements |
| Change candidate | Before/after assertions, supporting frames, bounded possible change interval, unresolved explanations |
| Correction | Prior interpretation, replacement or contradiction, reason, supporting evidence |
| Track reference | Seed frame, local track ID, selected region, associated observations |

Keep these concepts separate:

- Evidence capture/receipt time.
- Interpretation creation time.
- Last supporting observation.
- Possible interval in which a change occurred.
- Interpretation provenance: real model, mock fixture, or human annotation.

Assertions are fallible interpretations, not ground truth. Structural validation of citations does not validate semantic correctness.

### Update rules

- Historical visibility does not establish current visibility.
- Occlusion, cropped views, and tracking loss do not prove removal.
- Retain contradictory interpretations and their evidence.
- Preserve corrections instead of silently overwriting history.
- Do not infer exact change timing between sampled observations.
- Do not infer identity across unrelated sessions or disconnected tracks.
- Use idempotency keys and transactions to prevent duplicate writes.
- Track references are local and tentative, not persistent identities.

Extend the existing agent to propose structured memory records alongside summaries. Keep the existing summary schema compatible. A memory failure must not interrupt ingestion or erase a valid summary. Independent summary and memory writes need explicit status and idempotent recovery.

## 5. MobileCLIP2 visual retrieval

Use `MobileCLIP2-S0` from the official implementation/checkpoint family:

[Apple MobileCLIP repository](https://github.com/apple-aiml-research/ml-mobileclip).

Implement:

- An image embedding for each retained image.
- Text-query embeddings in the same model space.
- Recorded model revision, preprocessing version, and source-image hash.
- Time/session filters, visual similarity search, and SQLite text search over interpretations.
- Optional neighboring frames for temporal context.
- Native MCP image results with original timestamps and evidence references.

For the initial archive, use an exact similarity scan over a versioned local embedding index associated with SQLite. Do not introduce a separate vector database service. Keep indexes rebuildable from source assets; do not mix incompatible embedding versions.

Similarity ranks retrieval candidates. It is not a calibrated probability or proof of an object, state, or action. The agent must inspect retrieved images before using them as support.

Use native PyTorch inference, prefer MPS where supported, and retain explicit CPU fallback. Pin dependency versions and model revisions. Missing weights leave existing preview and summaries operational and expose an unavailable search state.

For live use, index retained evidence independently of ingestion. Keep work bounded, expose indexing backlog/coverage, and recover unindexed retained assets from disk rather than silently claiming they are searchable.

## 6. EdgeTAM selective tracking

Use the actual EdgeTAM temporal video predictor:

- [Official EdgeTAM repository](https://github.com/facebookresearch/EdgeTAM)
- [Core ML exporter](https://github.com/facebookresearch/EdgeTAM/blob/main/coreml/export_to_coreml.py)

User flow:

1. Choose a source frame.
2. Click a region or item.
3. Inspect and optionally refine the initial segmentation mask.
4. Assign a local descriptive label.
5. Start or stop tracking.

Limit the MVP to three active selections. Store seed prompts, frame references, masks, tracking state, quality signals, and reinitialization/discontinuity events. Use PNG or compressed mask arrays rather than storing large mask arrays in JSON.

Do not treat independent segmentation of each image as temporal tracking. Do not interpret model quality scores as calibrated certainty. Camera-relative image displacement is not physical world displacement.

### Bounded execution

- Use short input windows with bounded predictor state.
- Carry a usable boundary mask forward as a prompt for the next window.
- Record that reinitialization and its provenance.
- If no usable boundary mask exists, mark the track lost; require a new prompt when necessary.
- Preserve original timestamps and record skipped live inputs.
- Offline processing must consume every retained frame sequentially without latest-frame replacement.
- Treat epoch changes and substantial observation gaps explicitly; do not silently join identities across them.

Start with native PyTorch, MPS when supported, and CPU fallback. Do not make full Core ML conversion a prerequisite. The published component exporter is not a complete ready-made streaming tracker for this app.

Retain licenses and pin any imported/vendored upstream code. If inference fails, show the actual failure; never substitute fake masks and report successful model integration.

## 7. MCP interface

Keep the original four tools compatible. Add these capabilities with validated schemas:

| Tool | Contract |
| --- | --- |
| `get_memory_status` | Model availability, indexing coverage, active tracks, run/source labels |
| `search_visual_history` | Query, session/time filters, bounded result count; returns evidence metadata and original images |
| `get_memory_state` | Interpretations supported at a requested time, last observation, uncertainty, contradictions |
| `get_change_history` | Evidence-backed changes and unresolved candidates in a time range |
| `get_evidence` | Original images and provenance for validated evidence references |
| `record_memory` | Validate and persist structured proposals idempotently |
| `start_visual_track` | Explicit frame reference, point prompt, local label; create tracking job |
| `get_visual_track` | Track state, source references, bounded mask/original-image results |
| `stop_visual_track` | Stop processing while preserving history |

All images must be native MCP image content. Put ordered JSON metadata before image blocks. Clearly distinguish originals, masks, and overlays.

Evidence references must be scoped by run/session, not frame ID alone. Memory writes may cite the current observation and historical evidence retrieved through MCP. Use server-issued evidence bundles or an equivalent auditable mechanism to validate citation eligibility. Reject invented, cross-run, or unknown references.

The server assigns interpretation provenance from the execution mode. A caller cannot mark mock output as real-model or human-verified output.

The built-in agent uses an actual MCP client connection for reads and writes. Its image text remains untrusted scene content, never instructions.

## 8. Portal

Preserve live video, summary controls, journal, evidence, and export. Replace the spatial panel with:

- Natural-language visual search.
- Historical observation timeline.
- Time-specific memory state with freshness labels.
- Change candidates and contradictions.
- Side-by-side original evidence.
- Point selection, mask preview/correction, and tracking controls.
- Visible indexing/tracking availability and errors.
- Prominent mock interpretation labels in replay mode.

An assertion opens supporting originals. A change opens both sides and explains gaps. Distinguish “last observed” from “currently visible.”

Keep frontend implementation simple; no 3D frontend tooling is needed.

## 9. Saved evidence and isolated replay

Source images:

`data/evidence/41d59093746a404e8ff0adee79493ec0/`

Source journal:

`data/journal.sqlite3`

Known dataset characteristics, to confirm during import:

- 55 JPEGs, each 720 × 540.
- Approximately 55 seconds with irregular sampling.
- Original frame timestamps and metadata in journal observations.
- Original stream epoch 48.
- Indoor seating area, a seated person, laptop, handheld device, furniture, and substantial camera movement.
- Visible compression corruption in frame 222.
- No ground-truth tracks, scripted removal event, or measured calibration.

At the target commit, journal observations have `id`, `session_id`, and JSON `body`. Frame metadata is under `body["frames"]`. Inspect actual schema before reading; do not migrate the source database.

Open the source journal read-only. Match numeric JPEG filenames to frame IDs, detect conflicting/missing metadata, preserve original timestamps, and sort by `elapsed_ms` with frame ID as a deterministic tie-breaker.

The previous untracked `src/fieldnotes/offline.py` contains reusable metadata-import logic. Preserve it before restoration and extract only generic import logic; do not retain spatial/depth dependencies.

Copy the small input image set to a fresh run. Record hashes, original source metadata, and a new replay identity. Set `source: replay` and `input_kind: sampled_evidence`.

Missing frame IDs are unsaved samples, not proof of RTMP/network failure. Do not feed these intervals into the live buffer's continuous-5-FPS gap detector.

### Proposed commands

Implement:

- `fieldnotes replay-memory`: import/process an evidence directory into an isolated archive.
- `fieldnotes view-memory`: serve the archive, portal, and MCP without live ingestion.
- `fieldnotes exercise-memory-mcp`: execute the scoped scripted MCP scenarios.

Provide explicit arguments for source evidence, source journal, output run, fixture file, model paths, and server port. Document final command syntax in the README.

Use a logical replay clock anchored to the first source frame. Process five-second windows sequentially; include a labeled partial final window. Do not wait for wall-clock playback or drop frames to match inference speed. All 55 images must be retained, embedded, and made available to tracking even though summary windows select at most five images each.

Example run layout, under ignored `data/memory-runs/<run-id>/`:

```text
manifest.json
journal.sqlite3
memory.sqlite3
evidence/
embeddings/
tracks/
fixtures/
mcp-transcript.jsonl
review.md
```

Bind only to localhost. Prefer port 8001 if free; otherwise use 8002 or an explicitly selected free port and print the actual dashboard/MCP URLs. Do not kill a process to obtain a port. Configure the client with the selected port rather than a hardcoded default.

Replay/archive mode must not start MediaMTX, FFmpeg, paid model calls, or a live-model capability check. Mock mode must remain effective even if API credentials exist in `.env`.

## 10. Mock LLM adapter

Use real MobileCLIP2 and EdgeTAM inference. Mock only generative interpretations and scripted agent decisions.

Create deterministic, versioned fixtures selected by observation ID or explicit frame sets. Do not select them solely by mutable call order. Missing fixtures produce an explicit error. Record fixture ID, mock model identity, and zero paid-model usage.

Example authored fixtures, to review against the originals:

| Frame | Mock interpretation |
| --- | --- |
| 177 | A seated person has an open laptop on their lap and holds a handheld device. |
| 222 | Image corruption reduces confidence in fine details and visual continuity. |
| 451 | The view is low and dominated by the floor and chair. The laptop's current state cannot be established from this image. |

These are fixture statements, not evidence of successful LLM recognition. Populate the remaining windows with conservative, image-reviewed fixtures or explicit uncertainty. Do not fabricate an event simply to make a demonstration more interesting.

Manually select seed points for the chair and laptop on frame 177 and save the actual coordinates. Do not invent coordinates in advance. Retain frame 222 in the baseline.

Add separate negative fixtures for unknown frame IDs, malformed schema, duplicate writes, conflicting interpretations, and attempts to override mock provenance. Semantic errors with structurally valid citations should remain visible as fallible interpretations; do not claim a schema validator can detect all false physical claims.

## 11. Scoped MCP exercise and manual review

Execute a scripted client using the official MCP SDK against the real Streamable HTTP endpoint. Do not replace this with direct function calls or a fake MCP implementation.

Record requests, metadata, expected errors, and image hashes in a transcript. Store large images as assets, not repeated base64 blobs in the transcript.

| Scenario | Expected behavior |
| --- | --- |
| Initialize and discover tools | Original tools plus memory/search/tracking tools appear. |
| Search for earlier laptop views | Original timestamped image candidates are returned; inspect ranking manually. |
| Fetch an observation and write its mock interpretation | Valid references persist with mock provenance. |
| Query laptop state after the low-angle view | Earlier visibility is preserved; latest state remains uncertain. |
| Start chair/laptop tracks and retrieve results | Real masks and tracking state are returned. |
| Inspect frame 222 | Corruption is retained; actual tracking failure or misleading continuity is documented. |
| Submit nonexistent frame reference | Tool error, no invalid assertion persisted. |
| Repeat an identical write | No duplicate assertion. |
| Submit conflicting fixtures | Contradiction is inspectable, not silently erased. |
| Attempt to relabel mock output as real | Server retains enforced mock provenance or rejects the request. |
| Restart the archive server | Evidence, memory, search index, and track history remain accessible. |

Exercise this mock agent question through MCP:

> Was the laptop removed?

The scripted agent must search earlier views, retrieve originals, and query later observations before producing the fixture answer:

> The laptop was visible earlier. The later low-angle image does not show enough of that area to establish whether it was removed.

Label the answer as mocked. This demonstrates the evidence workflow, not independent reasoning accuracy.

Use source frames 177, 222, 303, 378, and 451 as manual review anchors. Inspect original images, search results, masks, continuity, and changes in visibility. Retrieval and tracking may fail; report their actual outcomes without selecting only attractive outputs.

Do not run benchmarks or collect throughput, latency, memory, or reconstruction-accuracy measurements. Do not run unrelated test suites. The authorized validation is this offline MCP exercise and manual review; do not claim it establishes real-time drone performance. Report functional failures explicitly.

If a model/checkpoint cannot run, finish the independent memory/MCP work and report that feature as incomplete rather than fabricating successful output.

## 12. Implementation order

1. Preserve working changes/data and restore target application source.
2. Restore current configured live-model behavior deliberately.
3. Implement memory schemas, provenance, validation, and persistence.
4. Implement isolated evidence import and fixture model adapter.
5. Add MCP memory tools and the scripted client transcript runner.
6. Add MobileCLIP2 indexing and search.
7. Add EdgeTAM selection, bounded tracking, masks, and MCP tools.
8. Add the portal memory/search/tracking interface.
9. Process the 55-image archive and execute MCP scenarios.
10. Review outputs manually and document setup, operation, and limitations.

Resolve ordinary implementation details autonomously. Keep dependencies optional where possible so missing perception models do not disable preview, summaries, or basic history access. Pin resolved dependencies and model revisions; download required public weights through explicit setup without sending images to paid APIs.

## 13. Delivery requirements

Provide a final report with:

- Baseline commit and branch used.
- Backup locations and preserved artifacts.
- Implemented features and files.
- Exact setup, replay, viewer, and MCP exercise commands.
- Run directory and dashboard/MCP URLs.
- Scenarios actually executed, expected/actual outcomes, and failures.
- Clear separation of real embedding/tracking outputs and mock interpretations.
- Search mistakes, tracking loss, and misleading accepted masks found during review.
- Remaining manual checks and unverified live behavior.

Success means the system preserves evidence, retrieves earlier observations, records temporal interpretations with provenance, supports selective tracking when possible, and exposes those capabilities through MCP. Mock responses and clean-looking overlays alone are not proof of perception accuracy.
