# Jobs

Jobs group whole observation sessions around a named, configurable theme. A session belongs to at most one job; sessions without membership are **Untracked**. Existing recordings remain Untracked until explicitly assigned. Job operations never invoke a model.

## Selection and capture

The selection is persisted across application restarts and applies to **new sessions only**. Selecting a different job, editing its theme, or selecting Untracked does not end, rotate, or change the current session. An explicitly created session captures context when it is created, even if it is still waiting for its first frame. A stream interruption that ends a session allows the next session to use the latest selection.

Each new session snapshots `job_context`: `job_id`, `name`, `theme`, and `revision`, or null. Each retained observation window copies this snapshot. The observer receives separate `job_context` and `task_brief` fields. Theme is attention guidance, never evidence that work happened or a goal was completed. Queued inference and overview generation retain the original context even after job configuration changes.

Membership is stored separately from captured context. Assigning a historical or actively recording Untracked session changes its organization only. It does not rewrite saved windows, summaries, names, or evidence; it does not change the theme used by later windows in that same session. The portal labels retrospective assignment explicitly.

## MCP tools

All mutations require a caller-generated `request_id` (1–200 characters). Repeat the same ID and arguments after a lost response; a duplicate returns the original snapshot with `duplicate: true`. Reusing an ID for different arguments fails. Read current state after a retried mutation rather than treating its saved response as current state.

| Tool | Arguments | Result |
| --- | --- | --- |
| `create_job` | `request_id`, `name`, `theme` | Creates a job without selecting it; returns `job`. |
| `list_jobs` | `limit=50`, `cursor=null` | Jobs, counts, next cursor, selected job ID and selection revision. |
| `get_job` | `job_id` | Current name, theme, revision, timestamps, count, and selected flag. |
| `update_job` | `request_id`, `job_id`, `expected_revision`, optional `name`, optional `theme` | Updated job; stale revision is rejected. |
| `get_job_context` | none | Selected job for new sessions, selection revision, current session membership and captured context, selection availability. |
| `switch_job` | `request_id`, `job_id` or null, `expected_selection_revision` | Selection effective for the next session; current session unchanged. |
| `assign_observations_to_job` | `request_id`, `job_id`, `session_ids` | Assigned/unchanged IDs and updated job count. |

`list_sessions` additionally accepts `job_id` or `untracked_only=true`, but not both. Session pages retain their existing cursors and include `job_id`, `job`, `job_context`, and `job_assignment`. `get_stream_status` includes the context under `jobs`. `get_observation_window` includes its captured `job_context` alongside the saved brief.

Names must contain 1–120 characters and themes 1–4,000 characters after trimming. Updates require at least one field. Configuration changes increment the job revision; changing the selection increments a separate selection revision, preventing an A→B→A change from passing an old revision check.

Assignment accepts 1–100 session IDs. Validation, membership writes and the request result are transactional. Already belonging to the requested job is a no-op. Any missing session or membership in another job rejects the entire batch. V1 has no move, unlink, delete, or job-level AI synthesis.

### Example workflow

1. `create_job(request_id="create-workshop-1", name="Workshop setup", theme="Observe equipment placement, preparation steps, and clear access routes. Report only visible changes and uncertainty.")`
2. Read `get_job_context()`.
3. `switch_job(request_id="select-workshop-1", job_id="<returned ID>", expected_selection_revision=<returned revision>)`.
4. Let the next recording session inherit this job; an ongoing session remains unchanged.
5. Read `list_sessions(untracked_only=true)`.
6. `assign_observations_to_job(request_id="attach-workshop-1", job_id="<ID>", session_ids=["<older session ID>"])`.

## Portal

- **Jobs**: job cards, themes, observation counts, and New job.
- **Job details**: edit configuration, select for next observation, browse member observations, and add Untracked sessions in batches.
- **Sessions**: All jobs, a particular job, or Untracked filtering; membership labels on cards.
- **Review**: membership, captured theme/revision, and Add to job for Untracked sessions.
- **Live**: next-session picker, current session's membership/captured theme, and an explicit next-observation notice when the contexts differ.

Demo mode has independent in-memory jobs, selection and assignments. It never reads or writes the real Jobs service and resets when the page reloads. Saved archives allow job creation, editing and assignment but reject capture-selection changes.

## HTTP and persistence

The REST and MCP adapters call the same `Jobs` service:

- `GET /api/jobs?limit=50&cursor=...`
- `POST /api/jobs` with `request_id`, `name`, `theme`
- `GET /api/jobs/context`
- `POST /api/jobs/selection` with `request_id`, `job_id`, `expected_selection_revision`
- `GET /api/jobs/{job_id}`
- `POST /api/jobs/{job_id}` with `request_id`, `expected_revision`, and name/theme changes
- `POST /api/jobs/{job_id}/observations` with `request_id`, `session_ids`
- Existing `/api/sessions` and `/api/library` accept the membership filters.

Job errors expose a code in the error detail: `unknown_job`/`unknown_session` (404), `invalid_arguments` (422), and `stale_revision`, `stale_selection`, `membership_conflict`, `request_conflict`, or `archive_read_only` (409).

Additive tables in `memory.sqlite3`: `jobs`, `job_revisions`, `job_memberships`, `job_selection`, `job_requests`. No historical evidence migration or summary rewrite is performed. Session creation and initial membership are committed together. Original job revisions are retained for provenance.

Restart FieldNotes to load the new Python service and MCP/HTTP routes, then refresh the portal. No restart was performed during implementation.

## Verification status

Tests were added for service validation, persistence, filtered pagination, deduplication, revision conflicts, atomic assignment, waiting sessions, stream reconnection, immutable observation/LLM context, custom names, archive restrictions, and HTTP behavior. They were **not run**, per request. No lint, builds, browser checks, model exercises, or application restarts were performed. Portal interaction and visual behavior are also unverified.
