# FieldNotes

A standalone DJI video observer: RTMP video → timestamped frames → MCP → five-second visual summaries.

Flight and FocusTrack stay with DJI and the operator. This app only observes video. No flight control,
object tracking, bounding boxes, Git-like scene history, or Gaussian reconstruction is included.

## Quick start (macOS)

Requirements: Python 3.12, [uv](https://docs.astral.sh/uv/), FFmpeg, MediaMTX 1.21+, and an OpenAI API key for
real visual summaries. There is no Node/frontend build step.

```bash
cd /Users/macos/coding/FieldNotes
brew install ffmpeg mediamtx
uv sync --locked --extra perception
[ -f .env ] || cp .env.example .env
# Edit .env and set OPENAI_API_KEY. Do not commit credentials.
uv run fieldnotes doctor
uv run --extra perception fieldnotes dev
```

Open **http://127.0.0.1:8000**. Copy the RTMP publish address shown there. The app starts its own
MediaMTX process; do not also start a Homebrew MediaMTX service. If a service already owns port 1935,
stop it yourself or use `--external-mediamtx` with an appropriately configured receiver.

The application works without an API key for video preview and MCP frame retrieval. With a key,
startup makes one small paid capability-check request using a synthetic gray image. Summaries begin
only after **Start observing**, or automatically with `--autostart`. Sampled images are sent to OpenAI
while observation is enabled. The default model is `gpt-6-luna`; override `OPENAI_MODEL` in `.env`.

### DJI Mini 5 Pro + RC-N3

1. Connect the RC-N3 to the phone running DJI Fly and connect the aircraft normally.
2. Connect the phone and laptop to the same reachable Wi-Fi network. Guest/client isolation can block
   the connection. Allow incoming MediaMTX connections if macOS asks.
3. In DJI Fly, open **GO FLY → Transmission → Live Streaming Platforms → RTMP**. Menu labels can vary
   by app version.
4. Enter `rtmp://<laptop-LAN-IP>:1935/live/drone` using the address displayed by FieldNotes. Never use
   `127.0.0.1` on the phone: that refers to the phone, not the laptop. If separate server/key fields
   appear, use server `rtmp://<laptop-LAN-IP>:1935/live` and key `drone`.
5. Select 720p for the first test, start publishing, and confirm video appears in FieldNotes.
6. Operate FocusTrack from DJI Fly. The application neither configures nor controls tracking.
7. Enter the work brief, keep the interval at **5 seconds**, and click **Start observing**.

Official setup reference: [DJI livestream guide](https://repair.dji.com/help/content?customId=01700006727&lang=en&paperDocType=ARTICLE&re=US&spaceId=17).
Verify simultaneous livestreaming and FocusTrack on the actual phone, app, and aircraft before the demo.

If the displayed LAN address is wrong, set `FIELDNOTES_LAN_IP` in `.env` and restart. Only RTMP listens
on the LAN. The dashboard and MCP are local to the laptop. This prototype receiver is intended for a
trusted local demo network, not public internet exposure.

### Demo script

Brief: “Describe the worker placing two rows of cones. Report visible movements and uncertainty.”
Follow the worker with DJI FocusTrack while they place large cones, move one, then step away.
Check that journal entries cite the corresponding five-second intervals and show evidence images.
Occlusion and camera movement should be described as uncertainty rather than invented work.

### Replay without a drone, using real vision

In terminal one:

```bash
uv run --extra perception fieldnotes dev --source replay --autostart
```

In terminal two:

```bash
uv run fieldnotes replay /absolute/path/to/video.mp4 --loop
```

The replay uses the **same RTMP receiver and decoder**. The app visibly labels all frames and entries
as replay. Summaries use the real configured vision model and incur API usage. Live `dev` mode no longer accepts `--stub`; authored mock interpretations are confined to the isolated saved-archive workflow. The replay command refuses to publish into a
session labeled `drone`. Only one publisher can use `live/drone` at a time.

Use Ctrl+C to stop the app and its owned MediaMTX/FFmpeg processes. The replay publisher is separately
owned by its terminal; stop that command with Ctrl+C too. Existing unrelated processes are never killed.

## Timing and evidence

- FFmpeg decodes 5 RGB frames/second within 960 × 540 while preserving aspect ratio; JPEG quality is 80.
- `received_at` is UTC at local decode completion, **not sensor exposure time**. `elapsed_ms` is monotonic.
- Half-open windows are anchored to the first frame: `[t0,t0+5s)`, `[t0+5s,t0+10s)`, etc.
- Starting after a pause resumes at the next boundary; time spent paused is not backfilled.
- Each window supplies up to five distinct, evenly sampled available images. The maximum request
  window is 60 seconds. Missing frames and decoder reconnections are included in coverage metadata.
- The buffer is capped at 60 seconds or 64 MiB. Selected evidence is saved before buffer eviction.
- One model request runs at a time. There is one pending window; a newer window replaces it when
  inference falls behind. Replaced windows receive explicit `skipped` entries.
- Model calls time out after 15 seconds and are not automatically retried. Empty windows use `no_video`
  entries without a model call. Errors remain visible in the journal.
- Five seconds is the **observation cadence**, not a promise of one completed model response every five
  seconds. The UI shows generation delay and degraded cadence.
- Pausing stops new windows, marks the pending window skipped, and lets the current analysis finish.
- Summaries, system entries, and selected JPEGs persist across restarts; each run has a new session ID.
  The dashboard shows the latest 200 entries; JSONL export includes the full journal. Evidence is under
  `data/evidence`; no full video is recorded. Delete the data directory manually to clear saved sessions.

## MCP interface

Endpoint: **http://127.0.0.1:8000/mcp**, Streamable HTTP. External clients run locally; there is no public
tunnel. A client supporting HTTP MCP can use a server entry such as:

```json
{"mcpServers":{"fieldnotes":{"url":"http://127.0.0.1:8000/mcp"}}}
```

Exact client configuration syntax depends on the client. Tools:

| Tool | Arguments | Behavior |
| --- | --- | --- |
| `get_stream_status` | none | Freshness, session, epoch, dimensions, buffered time range |
| `get_observation_window` | `start_elapsed_ms`, `end_elapsed_ms` | First block: JSON metadata; remaining blocks: native JPEG image content in matching frame order |
| `get_recent_summaries` | `limit` (1–20, default 3) | Completed summaries in the active session |
| `record_summary` | `observation_id`, `summary`, optional `generation` | Validate citations and persist once per observation |

`summary` has `summary` (text), `observed_actions` (description plus nonempty `frame_ids`), `uncertainties`
(string list), and `change_state` (`change_observed`, `no_clear_change`, or `uncertain`). Optional generation
metadata contains `model`, `duration_ms`, `input_tokens`, and `output_tokens`.

The built-in agent genuinely calls these tools over HTTP MCP to fetch evidence and persist model output.
It bridges MCP image blocks into Responses API image inputs locally; the cloud model does not need
network access to localhost. Scheduling and missing-video entries are deterministic application logic.
There is no open-ended autonomous flight/tool loop.

## HTTP endpoints

- `GET /api/status`, `GET /api/config`
- `POST /api/summaries/start` with `{"brief":"...","interval_seconds":5}`
- `POST /api/summaries/pause`
- `GET /api/preview` — MJPEG; slow viewers get latest-only frames
- `GET /api/events` — SSE status and journal invalidation; clients reload journal on reconnect
- `GET /api/journal?after=0&limit=200`, `GET /api/export` — JSONL
- `GET /api/evidence/{session_id}/{frame_id}.jpg`

## Development and verification

```bash
uv run ruff check src tests
uv run pytest -q
```

The integration test generates a short video locally, publishes it through MediaMTX, exercises the actual
MCP agent using a stub model, disconnects/reconnects RTMP, and verifies persisted evidence. It requires
FFmpeg, MediaMTX, and permission to bind localhost ports. Tests do not call a paid model.

Module boundaries:

- `core`: frame contract, bounded buffer/subscriptions, observation windows, SQLite journal
- `ingest`: supervised FFmpeg decoder and exact-size PPM/RGB parsing
- `mcp_server`, `agent`, `model`: native MCP tools, fixed-cadence scheduler, vision adapter
- `app`, `cli`, `static`: localhost API/UI and owned-process supervision

Future Gaussian mapping can subscribe to `Runtime.buffer.subscribe()` (latest-only queue; always
unsubscribe when done). No pose, depth, calibration, or reconstruction is fabricated. This repository
has no dependency on WorldGit.

### Hardware acceptance checklist

- [ ] Real DJI stream visible while FocusTrack follows the person.
- [ ] Six consecutive five-second observation windows with real-model summaries.
- [ ] Visible work descriptions and appropriate uncertainty checked against evidence.
- [ ] Stream interruption changes the UI to stale/reconnecting within three seconds of last frame.
- [ ] Reconnection resumes automatically with a new epoch and explicit gaps.
- [ ] Journal and evidence survive application restart.

Real-flight acceptance and real-model semantic quality must be tested with your hardware and API key;
the automated replay test cannot establish either.


## Temporal visual memory

SQLite temporal interpretations, MobileCLIP2-S0 visual retrieval, and selective EdgeTAM tracking are available through the portal and MCP. See [setup, replay, MCP contracts, storage and manual checks](docs/visual-memory.md).

Quick setup: `uv sync --extra perception`, then `uv run --extra perception fieldnotes setup-memory-models`.

Run the isolated 55-image exercise using `replay-memory`, `view-memory`, and `exercise-memory-mcp` as documented. Generative interpretations are clearly labeled mocks; embeddings and masks use the actual local models.

### Recording sessions

Live sessions now follow decoded video: the first frame starts a session and 10 seconds without frames ends it. MCP provides `list_sessions`, `start_session`, and `end_session`; explicitly ending during a continuous stream causes the next frame to start another session. See [session operation and manual checks](docs/sessions.md). These changes require an application restart and have not been verified.

### Real live summaries

Start live observation with `uv run --extra perception fieldnotes dev --autostart`. This uses `OPENAI_MODEL` (currently configured as `gpt-6-luna`) and `OPENAI_API_KEY` from `.env`. Missing credentials or model errors are reported; live mode never substitutes placeholder summaries. Existing historical stub entries remain labeled as such. Local search uses MobileCLIP2-S0, and selected-region tracking uses EdgeTAM.
