# FieldNotes portal

The portal has three routes, served by the existing FastAPI app without a frontend build:

- `/#/sessions`: searchable session library with date and topic filters.
- `/#/sessions/<session_id>`: original retained images, sampled playback, timeline, Overview and At this time.
- `/#/live`: current preview, recent observations, observation settings and connection details.

The header's **Explore demo** opens `/?demo=1#/sessions`. This mode uses isolated browser fixtures and generated images in `static/demo`; it does not connect to status/SSE, start observation, invoke a model, or write recordings. The persistent banner identifies the imagery and observations as demonstrations. Exit demo to return to real data. Demo settings only change the visible simulation.

Image search and region tracking are no longer exposed by the UI. Their REST and MCP interfaces remain available. Export journal is available from the page footer.

## Session accounts

Accounts are AI interpretations of saved observation summaries, not new visual analysis or independently verified facts. They include an editorial title, one or more of the six supported topics, objective, process, observed outcome, unknowns, and cited timeline segments. A missing supported outcome reads **Outcome not established.**

The observer snapshots its brief into newly retained observation records. Old observations without a brief explicitly lack a recorded objective. Account generation reads snapshots rather than the currently configured brief.

A session ending schedules account generation after that session's queued/running built-in observations finish. Shutdown does not start additional generation. Opening an old session does not generate an account; use **Generate overview**. An active or stale session can be refreshed explicitly. Generation uses `OPENAI_MODEL` and `OPENAI_API_KEY`, with additional API usage. Calls are serialized, use a 60-second timeout, and have no automatic retries. Identical concurrent requests share one job; a ready account at the same source revision is reused.

No generation occurs in saved archive mode. Stub and authored mock summaries are excluded from real account synthesis. Missing credentials, missing real observations, invalid output, and interrupted jobs have explicit error states. The last successful account survives a failed refresh and shows a stale notice when evidence has changed.

The additive `session_accounts` and `session_account_versions` tables live in `memory.sqlite3`. Each successful version records its source revision, configured model, generation time, token usage and sampling notice. Context is bounded to 160 evenly distributed real observations; the displayed notice states how many observations were included. This is not a completeness guarantee.

Every citation must refer to a frame in the supplied observations. Segment citations must fall within the segment; segment bounds must be covered by supplied observation windows without crossing recorded video gaps. These checks validate references and coverage, not the model's semantic accuracy.

## HTTP interfaces

Existing endpoints and MCP tools remain compatible. New portal endpoints:

- `GET /api/library?q=&topic=&since=&until=&cursor=0&limit=30`: card-ready filtered sessions. Dates are ISO timestamps; `until` is exclusive. Cursor is a filtered-list offset.
- `GET /api/session-review/{session_id}`: session card, account, retained observation entries, and coverage intervals.
- `GET /api/session-accounts/{session_id}`: account, generation state, stale flag, and generation availability.
- `POST /api/session-accounts/{session_id}/generate` with `{}`: queue or reuse an account generation job.
- `GET /api/session-frames/{session_id}?cursor=&limit=200`: stable evidence pages ordered by elapsed time and ID. Use the returned opaque `next_cursor`.

The review player loads additional evidence pages for next/latest and citations. It plays discrete saved images, not video. Temporal placement uses each session's actual elapsed bounds; session time is not assumed to begin at zero. Gaps between ordinary sampled images are not interpreted as outages. Unsaved intervals reflect absence of retained observation-window coverage; recorded video gaps are labeled separately.

## Assets and attribution

Local fonts: Inter and Instrument Serif (SIL Open Font License). Local icons: Feather 4.29.2 (MIT). License files are bundled in `static/vendor`.

The nine demo photographs were generated with the built-in ImageGen tool. Prompt briefs and provenance are recorded in `static/demo/README.md`. They are not camera evidence and never enter the real evidence database.

## Verification status

This overhaul was implemented without running verification, at the user's request. No tests, lint, builds, browser inspection, screenshot comparison, model calls, or app restarts were run for this change. The generated images were not visually inspected. Added automated coverage is unexecuted; runtime behavior and visual fidelity remain unverified.

## Renaming observations

Open a session and choose **Rename** beside its title. Save a name of 1–100 characters; surrounding whitespace is removed. Cancel or Escape leaves the name unchanged. Custom names are persisted in the session registry and take precedence over generated titles, including after overview regeneration. They appear in the library and search, review, and the active session's Live heading. Existing archives can be renamed without invoking a model. Demo renames affect only in-memory fixtures until the page is reloaded.

`POST /api/sessions/{session_id}/rename` accepts `{"name":"Workspace observation"}` and returns the updated session record with its display title. Invalid names return 422; an unknown session returns 404. Original observations and evidence remain unchanged. No verification was run for this addition.

Rename requests preserve the entered name on failure and handle non-JSON error responses. If the server predates the rename endpoint, the UI asks for a FieldNotes server restart; refreshing the browser alone does not load new Python routes.
