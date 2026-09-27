# Recording sessions

## Lifecycle

A live FieldNotes server starts a recording session on its first decoded video frame. Starting the application without video creates no automatic empty session. A monotonic watchdog ends the active session after 10 seconds without decoded frames. Returning video starts another session. Decoder retries and short interruptions do not create sessions; stream epochs and observation gaps still record interruptions within a session.

Session boundaries describe local receipt, not exact camera exposure or the RTMP publisher's connection lifetime. Session records distinguish last observed frame time from the later time at which an end was detected. Frame IDs and elapsed milliseconds continue across sessions within one application process; do not treat elapsed zero as the start of each session.

An explicit end while streaming does not pause the drone or preview. It closes the current session, and the next decoded frame starts a new automatic session. An explicit start can rotate a known active session; without video it creates a waiting-for-video session whose first arriving frame activates it. Empty manually created sessions remain inspectable.

## MCP tools

- `list_sessions(limit=50, cursor=null)`: list sessions, their lifecycle state, names, observation times, end reasons and retained-image counts. Use `next_cursor` to request another page. `active_session_id` identifies the current active or waiting session.
- `start_session(request_id, name=null, expected_active_session_id=null)`: use a unique request ID for each intended operation. If a session is active, supply its exact ID to rotate it. A stale or omitted expected ID cannot replace a current session accidentally.
- `end_session(session_id, request_id, reason=null)`: close exactly this session. Ending an already-ended session returns its record and cannot close a newer one.

Repeat identical requests with the same request ID to recover the original response. Reusing an ID with different arguments is rejected. Retried responses are snapshots of the original operation, not claims that the returned session remains active; query status for current state.

Example arguments for a deliberately named session when no session exists:

```json
{"request_id":"begin-cone-inspection-001","name":"Cone inspection"}
```

For an active session, discover its ID first and add `expected_active_session_id` to that start request. To end it:

```json
{"session_id":"<exact-session-id>","request_id":"end-cone-inspection-001","reason":"Inspection finished"}
```

`get_stream_status` and `get_memory_status` include lifecycle state, active session ID and the 10-second grace policy. `get_observation_window` and `get_recent_summaries` accept an optional `session_id`; omitting it uses the current session. Historical windows can be retrieved only when that exact window was already retained. Use evidence/search tools for other saved history; expired unsaved video cannot be recreated.

## Summaries, tracking and viewer

Summary jobs carry explicit session IDs. Existing work may finish after a boundary and is saved to its original session. Before clearing a session buffer, the backend persists the final remaining observation through the last received frame. Enabled summaries use the existing one-running/one-pending scheduler; replaced or paused work receives an explicit skipped record. Closing the application may skip a final queued summary during shutdown. User brief, cadence and enabled/paused preference remain unchanged across ordinary boundaries.

Tracking jobs stop at their session's end and retain an explicit end reason. A chunk already running can finish on its previously selected frames. New sessions never receive old tracking masks. Preview subscriptions survive buffer clearing and continue on subsequent frames.

The session selector filters both the journal and visual-memory panel. Selecting a historical session turns off following the active session; choose **Follow live session** to resume. The main preview continues showing the current stream independently. **More sessions** paginates the registry. The existing retained-image slider is limited to 1,000 images per loaded session; arbitrary long-history image paging is not introduced by this change.

## Persistence and archive compatibility

The additional `recording_sessions` and `session_requests` tables live in `memory.sqlite3`. The summary schema remains intact. Existing IDs, evidence and mock provenance are preserved. Historical registry entries are inferred from journal observations and retained evidence, and labeled accordingly; they are not exact flight boundaries.

On clean shutdown the active session ends with `application_shutdown`. On the next startup, an unfinished persisted session is marked `interrupted` without inventing the crash time. A subsequent stream starts a new ID.

Saved archives expose their historical session registry but reject `start_session` and `end_session`. They have no automatic lifecycle watchdog and no live ingestion. REST `GET /api/sessions` provides the same paginated registry, and `GET /api/journal?session_id=...` filters the journal.

## Deployment and manual checks

Restart the relevant FieldNotes process to load the backend changes, then refresh its browser tab. This implementation has not been executed or verified, at the user's request. No tests, model requests, benchmarks, stream exercises or application restarts were performed for this change.

Manual checks for a later verification pass:

1. Start live mode without video; confirm there is no automatic empty session.
2. Begin video, briefly interrupt it, then interrupt it for more than 10 seconds; inspect session IDs and end reasons.
3. End via MCP while video continues; confirm the next frame belongs to a new session.
4. Rotate with an expected active ID and repeat the same request ID; confirm only one rotation. Try a stale active ID.
5. Let summary/tracking work cross a boundary; confirm every output still cites its original session.
6. Select historical history, inspect the filtered journal, and return to following live sessions.
7. Restart the application and inspect the saved session registry.
8. Open the existing saved archive; confirm evidence remains available and lifecycle mutations are refused.
