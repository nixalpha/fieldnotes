# Job statistics

Statistics correspond to a single job: every figure and chart is computed from the sessions listed in `job_memberships` for that job. Untracked sessions never enter a job's metrics, and assigning a session later does not rewrite any captured context, summary, or evidence. The job theme is displayed for orientation but is never counted as evidence.

## Page

`#/jobs/<job_id>/statistics`, reachable from a job card or the job page.

1. Key figures — sessions, observation windows (interpreted vs. skipped/failed), change-observed rate, observed actions, uncertainties, model latency and tokens, video gaps, memory assertions, freshness.
2. Built-in metrics — deterministic charts, no model calls. Choose metrics and a bucket size (auto picks 10 s … 1 day from the observed span).
3. Ask for a chart — describe a chart in plain language. The request runs in the background; pending, failed (with the model's reason) and saved charts are shown. Clicking a point lists the cited observations, linked to their sessions.

The demo (`?demo=1`) shows the page shell only; metrics need a running server.

## ChartSpec

The model never emits JavaScript or raw Chart.js configuration. It returns a `ChartSpec` (`src/fieldnotes/statistics.py`) which the server validates before saving and the browser translates to Chart.js:

```
kind: line | bar | stacked_bar | area | scatter | kpi
title, subtitle, definition, uncertainties[]
x, y: {label, kind: time | category | number, unit}
series[]: {label, points[]: {x, y, evidence: [observation_id, …]}}
baseline, coverage: {observations_used, observations_available, start_at, end_at}
```

Validation (`Statistics.validate`) rejects: evidence IDs outside the job, nonzero points without evidence, non-ISO timestamps on time axes, KPI charts with more than one point, and coverage claiming more observations than the job has. Coverage counts are stamped by the server, not trusted from the model.

## Model generation

`POST /api/jobs/{job_id}/charts/requests` stores a request and launches `Statistics.generate`, which connects to the server's own MCP endpoint, calls `get_job`, `get_job_statistics`, and `list_job_observations` (text-only summaries, at most 200), and asks the model for a structured `ChartDraft` (`chart | cannot_answer`). Valid charts are saved through the same idempotent path as `record_job_chart`; failures and `cannot_answer` reasons are kept on the request. Archives never make model calls (`generation_allowed: false`).

## MCP tools

| Tool | Purpose |
| --- | --- |
| `get_job_statistics(job_id)` | Deterministic summary and available metric names |
| `get_job_metric(job_id, metric, bucket_seconds?)` | A built-in metric as a `ChartSpec` |
| `list_job_observations(job_id, cursor?, limit?, start_at?, end_at?)` | Text summaries of the job's observations for charting |
| `record_job_chart(request_id, job_id, spec, prompt?, source?)` | Validate and save a chart (idempotent by `request_id`) |
| `list_job_charts(job_id)` | Saved charts |
| `delete_job_chart(request_id, job_id, chart_id)` | Remove a saved chart |

External MCP clients can build charts the same way the built-in generator does: read observations, produce a `ChartSpec`, call `record_job_chart`.

## HTTP routes

```
GET  /api/jobs/{job_id}/statistics
GET  /api/jobs/{job_id}/statistics/metrics/{metric}?bucket_seconds=
GET  /api/jobs/{job_id}/statistics/observations
GET  /api/jobs/{job_id}/charts
POST /api/jobs/{job_id}/charts                     {request_id, spec, prompt?}
GET  /api/jobs/{job_id}/charts/requests
POST /api/jobs/{job_id}/charts/requests            {request_id, prompt}
POST /api/jobs/{job_id}/charts/{chart_id}/delete   {request_id}
```

## Storage

`memory.sqlite3` gains `job_charts`, `job_chart_requests`, and `job_chart_writes` (idempotency). Chart.js 4.4.9 is vendored at `src/fieldnotes/static/vendor/chartjs/` (MIT).

## Verification

`uv run pytest -q tests/test_statistics.py` covers job scoping, metric bucketing, citation validation, idempotent save/delete, and REST + MCP generation with a fake model client.
