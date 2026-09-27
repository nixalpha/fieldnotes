"""Job-scoped statistics: deterministic metrics, validated chart specs and chart persistence.

Every chart is a ChartSpec that the application owns and validates. Models never emit
rendering code; they emit data points that cite observation IDs inside the job.
"""
from __future__ import annotations

import asyncio
import json
import os
import uuid
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from typing import Literal

from openai import AsyncOpenAI
from pydantic import BaseModel, ConfigDict, Field

from .agent import call, connect_mcp
from .core import utc_now
from .jobs import JobError

ChartKind = Literal['line', 'bar', 'stacked_bar', 'area', 'scatter', 'kpi']
AxisKind = Literal['time', 'category', 'number']
BUCKET_CHOICES = (10, 30, 60, 300, 600, 1800, 3600, 21600, 86400)
MAX_MODEL_OBSERVATIONS = 200

INSTRUCTIONS = '''You build a single chart for a FieldNotes job from saved visual observations.
Input: the job (name, theme), the user's chart request, deterministic statistics for the job, and a list
of observations. Each observation has an observation_id, wall-clock start_at/end_at, a visual summary,
observed actions and uncertainties. The job theme describes what the observer was asked to watch; it is
NOT evidence of anything happening. Only observation summaries and actions are evidence.
Rules:
- Count or measure only what the observation text supports. Never invent values.
- Every data point must cite the observation_ids it was derived from (evidence). Points with no supporting
  observation get y=0 and no evidence.
- Use the requested time span and bucket size when stated. For time axes, x is the ISO 8601 bucket start.
  For category axes, x is the label.
- State the exact definition used to count (what counted as a match) in `definition`.
- Record ambiguity in `uncertainties`; if nothing in the observations can answer the request, set
  `cannot_answer` and leave `chart` null.'''


class Point(BaseModel):
    model_config = ConfigDict(extra='forbid')
    x: str = Field(min_length=1, max_length=120)
    y: float
    evidence: list[str] = Field(default_factory=list, max_length=12)


class Series(BaseModel):
    model_config = ConfigDict(extra='forbid')
    label: str = Field(min_length=1, max_length=80)
    points: list[Point] = Field(max_length=500)


class Axis(BaseModel):
    model_config = ConfigDict(extra='forbid')
    label: str = Field(min_length=1, max_length=80)
    kind: AxisKind
    unit: str | None = Field(default=None, max_length=40)


class Coverage(BaseModel):
    model_config = ConfigDict(extra='forbid')
    observations_used: int = Field(ge=0)
    observations_available: int = Field(ge=0)
    start_at: str | None = None
    end_at: str | None = None


class ChartSpec(BaseModel):
    model_config = ConfigDict(extra='forbid')
    kind: ChartKind
    title: str = Field(min_length=1, max_length=120)
    subtitle: str | None = Field(default=None, max_length=200)
    x: Axis
    y: Axis
    series: list[Series] = Field(min_length=1, max_length=6)
    baseline: float | None = None
    definition: str = Field(min_length=1, max_length=1000)
    uncertainties: list[str] = Field(default_factory=list, max_length=10)
    coverage: Coverage


class ChartDraft(BaseModel):
    model_config = ConfigDict(extra='forbid')
    chart: ChartSpec | None
    cannot_answer: str | None = Field(default=None, max_length=500)


def parse_time(value):
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def bucket_start(moment: datetime, seconds: int) -> datetime:
    epoch = int(moment.timestamp())
    return datetime.fromtimestamp(epoch - epoch % seconds, UTC)


METRICS = {
    'observations': 'Observation windows per bucket, split by reported change state.',
    'actions': 'Observed actions (each cites frames) per bucket.',
    'uncertainties': 'Uncertainties the observer reported per bucket.',
    'latency': 'Mean model interpretation latency per bucket.',
    'tokens': 'Model tokens consumed per bucket.',
    'gaps': 'Video gap time inside observation windows per bucket.',
    'assertions': 'Memory assertions grouped by predicate.',
    'sessions': 'Observed span per session.',
}


class Statistics:
    def __init__(self, runtime, store, jobs, *, archive=False, model_name='stub-no-vision', mcp_url=None):
        self.runtime, self.store, self.jobs, self.archive = runtime, store, jobs, archive
        self.model_name, self.mcp_url = model_name, mcp_url
        self.tasks: dict[str, asyncio.Task] = {}
        self.lock = asyncio.Semaphore(1)
        with store.lock, store.db:
            store.db.executescript('''
                CREATE TABLE IF NOT EXISTS job_charts (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL,
                    job_id TEXT NOT NULL, body TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS job_charts_job ON job_charts(job_id,seq);
                CREATE TABLE IF NOT EXISTS job_chart_requests (
                    request_id TEXT PRIMARY KEY, job_id TEXT NOT NULL, created_at TEXT NOT NULL, body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS job_chart_writes (
                    request_id TEXT PRIMARY KEY, signature TEXT NOT NULL, body TEXT NOT NULL);
            ''')

    # ------------------------------------------------------------------ sources
    def session_ids(self, job_id):
        self.jobs.get(job_id)
        return [r[0] for r in self.store.rows('SELECT session_id FROM job_memberships WHERE job_id=? ORDER BY assigned_at', (job_id,))]

    def sources(self, job_id):
        sids = self.session_ids(job_id)
        if not sids:
            return sids, []
        marks = ','.join('?' * len(sids))
        rows = self.runtime.journal.db.execute(f'''SELECT o.body,s.body FROM observations o
            LEFT JOIN summaries s ON s.observation_id=o.id WHERE o.session_id IN ({marks})''', sids).fetchall()
        sources = [json.loads(summary or observation) for observation, summary in rows]
        return sids, sorted(sources, key=lambda s: (s.get('start_at') or '', s['start_elapsed_ms']))

    @staticmethod
    def span(sources):
        starts = [t for t in (parse_time(s.get('start_at')) for s in sources) if t]
        ends = [t for t in (parse_time(s.get('end_at')) for s in sources) if t]
        return (min(starts) if starts else None, max(ends) if ends else None)

    @staticmethod
    def choose_bucket(start, end):
        if not start or not end:
            return 60
        total = max((end - start).total_seconds(), 1)
        return next((b for b in BUCKET_CHOICES if total / b <= 60), BUCKET_CHOICES[-1])

    def summary(self, job_id):
        job = self.jobs.get(job_id)
        sids, sources = self.sources(job_id)
        summarized = [s for s in sources if s.get('visual')]
        states = Counter(s['visual']['change_state'] for s in summarized)
        durations = [s['duration_ms'] for s in summarized if s.get('duration_ms')]
        usage = Counter()
        for s in summarized:
            for key in ('input_tokens', 'output_tokens'):
                usage[key] += int((s.get('usage') or {}).get(key) or 0)
        start, end = self.span(sources)
        gap_ms = sum(g['end_elapsed_ms'] - g['start_elapsed_ms'] for s in sources for g in s.get('gaps', []))
        marks = ','.join('?' * len(sids))
        assertions = self.store.rows(f'SELECT count(*) FROM assertions WHERE session IN ({marks})', sids)[0][0] if sids else 0
        return {
            'job': job, 'session_count': len(sids), 'observation_count': len(sources),
            'summarized_count': len(summarized),
            'not_completed_count': sum(1 for s in sources if s.get('status') not in (None, 'completed')),
            'change_observed_rate': round(states['change_observed'] / len(summarized), 3) if summarized else None,
            'change_states': dict(states),
            'action_count': sum(len(s['visual']['observed_actions']) for s in summarized),
            'uncertainty_count': sum(len(s['visual']['uncertainties']) for s in summarized),
            'mean_latency_ms': round(sum(durations) / len(durations)) if durations else None,
            'tokens': dict(usage), 'gap_ms': gap_ms, 'assertion_count': assertions,
            'observed_ms': sum(s['end_elapsed_ms'] - s['start_elapsed_ms'] for s in sources),
            'start_at': start.isoformat() if start else None, 'end_at': end.isoformat() if end else None,
            'freshness_at': end.isoformat() if end else None, 'archive': self.archive,
            'generation_allowed': not self.archive, 'model': self.model_name, 'metrics': METRICS,
        }

    # ------------------------------------------------------------------ deterministic metrics
    def metric(self, job_id, name, bucket_seconds=None):
        if name not in METRICS:
            raise JobError('invalid_arguments', f'Unknown metric. Choose one of {", ".join(METRICS)}.', 422)
        if bucket_seconds is not None and bucket_seconds not in BUCKET_CHOICES:
            raise JobError('invalid_arguments', f'bucket_seconds must be one of {BUCKET_CHOICES}.', 422)
        job = self.jobs.get(job_id)
        sids, sources = self.sources(job_id)
        start, end = self.span(sources)
        bucket = bucket_seconds or self.choose_bucket(start, end)
        coverage = Coverage(observations_used=len(sources), observations_available=len(sources),
                            start_at=start.isoformat() if start else None, end_at=end.isoformat() if end else None)
        subtitle = f'{job["name"]} · {len(sids)} session(s) · {bucket}s buckets'
        timed = [(bucket_start(t, bucket), s) for s in sources if (t := parse_time(s.get('start_at')))]
        keys = sorted({k for k, _ in timed})
        if start and end and keys:
            cursor, keys = keys[0], []
            while cursor <= end:
                keys.append(cursor)
                cursor += timedelta(seconds=bucket)
        time_axis = Axis(label='Time', kind='time')

        def series(label, value, group=None):
            values, evidence = defaultdict(float), defaultdict(list)
            for key, source in timed:
                if group is not None and not group(source):
                    continue
                amount = value(source)
                if amount:
                    values[key] += amount
                    evidence[key].append(source['observation_id'])
            return Series(label=label, points=[Point(x=k.isoformat(), y=round(values[k], 3), evidence=evidence[k][:12]) for k in keys])

        def build(kind, title, y, data, definition, uncertainties=(), x=time_axis, baseline=None):
            return ChartSpec(kind=kind, title=title, subtitle=subtitle, x=x, y=y, series=data, baseline=baseline,
                             definition=definition, uncertainties=list(uncertainties), coverage=coverage)

        untimed = len(sources) - len(timed)
        notes = [f'{untimed} observation(s) lack wall-clock timestamps and are excluded from time buckets.'] if untimed else []
        if name == 'observations':
            labels = {'change_observed': 'Change observed', 'no_clear_change': 'No clear change',
                      'uncertain': 'Uncertain', None: 'Not summarized'}
            data = [series(label, lambda s: 1, lambda s, st=state: (s.get('visual') or {}).get('change_state') == st)
                    for state, label in labels.items()]
            return build('stacked_bar', 'Observation windows', Axis(label='Windows', kind='number'),
                         [d for d in data if any(p.y for p in d.points)] or data[:1], METRICS[name], notes)
        if name == 'actions':
            return build('line', 'Observed actions', Axis(label='Actions', kind='number'),
                         [series('Observed actions', lambda s: len((s.get('visual') or {}).get('observed_actions', [])))],
                         METRICS[name] + ' Counts actions the observer explicitly reported with frame citations.', notes)
        if name == 'uncertainties':
            return build('line', 'Reported uncertainties', Axis(label='Uncertainties', kind='number'),
                         [series('Uncertainties', lambda s: len((s.get('visual') or {}).get('uncertainties', [])))],
                         METRICS[name], notes)
        if name == 'latency':
            totals = series('Total latency', lambda s: s.get('duration_ms') or 0, lambda s: s.get('visual'))
            counts = series('Count', lambda s: 1, lambda s: s.get('visual') and s.get('duration_ms'))
            points = [Point(x=t.x, y=round(t.y / c.y) if c.y else 0, evidence=t.evidence) for t, c in zip(totals.points, counts.points)]
            return build('line', 'Model latency', Axis(label='Milliseconds', kind='number', unit='ms'),
                         [Series(label='Mean latency', points=points)], METRICS[name], notes)
        if name == 'tokens':
            return build('stacked_bar', 'Model tokens', Axis(label='Tokens', kind='number'),
                         [series('Input tokens', lambda s: int((s.get('usage') or {}).get('input_tokens') or 0)),
                          series('Output tokens', lambda s: int((s.get('usage') or {}).get('output_tokens') or 0))],
                         METRICS[name], notes)
        if name == 'gaps':
            return build('bar', 'Video gaps', Axis(label='Seconds', kind='number', unit='s'),
                         [series('Gap time', lambda s: sum(g['end_elapsed_ms'] - g['start_elapsed_ms'] for g in s.get('gaps', [])) / 1000)],
                         METRICS[name] + ' Gaps are recorded when frames are more than 600 ms apart or the stream reconnected.', notes)
        if name == 'assertions':
            marks = ','.join('?' * len(sids))
            rows = self.store.rows(f'SELECT predicate,count(*) FROM assertions WHERE session IN ({marks}) GROUP BY predicate ORDER BY count(*) DESC', sids) if sids else []
            points = [Point(x=p or 'unknown', y=c) for p, c in rows[:60]] or [Point(x='none', y=0)]
            return build('bar', 'Memory assertions by predicate', Axis(label='Assertions', kind='number'),
                         [Series(label='Assertions', points=points)],
                         METRICS[name] + ' Assertions are model interpretations of evidence, not ground truth.',
                         x=Axis(label='Predicate', kind='category'))
        per_session = defaultdict(float)
        evidence = defaultdict(list)
        for s in sources:
            per_session[s['session_id']] += (s['end_elapsed_ms'] - s['start_elapsed_ms']) / 60000
            evidence[s['session_id']].append(s['observation_id'])
        points = [Point(x=f'{sid[:8]}…', y=round(per_session[sid], 2), evidence=evidence[sid][:12]) for sid in sids] or [Point(x='none', y=0)]
        return build('bar', 'Observed minutes per session', Axis(label='Minutes', kind='number', unit='min'),
                     [Series(label='Observed minutes', points=points)], METRICS[name] + ' Sums observation windows; unsaved intervals are not counted.',
                     x=Axis(label='Session', kind='category'))

    def observations(self, job_id, cursor=None, limit=50, start_at=None, end_at=None):
        """Text-only observation listing for models. No images: evidence stays behind get_evidence."""
        if not 1 <= limit <= MAX_MODEL_OBSERVATIONS:
            raise JobError('invalid_arguments', f'limit must be 1–{MAX_MODEL_OBSERVATIONS}.', 422)
        try:
            offset = int(cursor) if cursor else 0
            if offset < 0:
                raise ValueError()
        except (ValueError, TypeError) as exc:
            raise JobError('invalid_arguments', 'Invalid observation cursor.', 422) from exc
        _, sources = self.sources(job_id)
        lo, hi = parse_time(start_at) if start_at else None, parse_time(end_at) if end_at else None
        if (start_at and not lo) or (end_at and not hi):
            raise JobError('invalid_arguments', 'start_at/end_at must be ISO 8601 timestamps.', 422)
        if lo or hi:
            sources = [s for s in sources if (t := parse_time(s.get('start_at'))) and (not lo or t >= lo) and (not hi or t < hi)]
        page = sources[offset:offset + limit]
        return {'job_id': job_id, 'total': len(sources),
                'observations': [{k: s.get(k) for k in ('observation_id', 'session_id', 'start_at', 'end_at', 'status', 'model')}
                                 | {'frame_ids': [f['frame_id'] for f in s.get('frames', [])], 'job_context': s.get('job_context'),
                                    'visual': s.get('visual'), 'gaps': s.get('gaps', [])} for s in page],
                'next_cursor': str(offset + limit) if offset + limit < len(sources) else None}

    # ------------------------------------------------------------------ charts
    def validate(self, job_id, spec: ChartSpec):
        sids, sources = self.sources(job_id)
        known = {s['observation_id'] for s in sources}
        total = 0
        for series in spec.series:
            for point in series.points:
                total += 1
                if spec.x.kind == 'time' and not parse_time(point.x):
                    raise JobError('invalid_chart', f'Time axis point {point.x!r} is not an ISO 8601 timestamp.', 422)
                if spec.x.kind == 'number':
                    try:
                        float(point.x)
                    except ValueError as exc:
                        raise JobError('invalid_chart', f'Number axis point {point.x!r} is not numeric.', 422) from exc
                foreign = [e for e in point.evidence if e not in known]
                if foreign:
                    raise JobError('invalid_chart', f'Chart cited observations outside job {job_id}: {foreign[:3]}.', 422)
                if point.y and spec.kind != 'kpi' and not point.evidence and spec.coverage.observations_used:
                    raise JobError('invalid_chart', f'Non-zero point {point.x!r} cites no observations.', 422)
        if not total:
            raise JobError('invalid_chart', 'Chart has no data points.', 422)
        if spec.kind == 'kpi' and (len(spec.series) != 1 or len(spec.series[0].points) != 1):
            raise JobError('invalid_chart', 'KPI charts carry exactly one point.', 422)
        if spec.coverage.observations_available > len(sources) or spec.coverage.observations_used > spec.coverage.observations_available:
            raise JobError('invalid_chart', 'Coverage claims more observations than the job holds.', 422)
        return len(sources)

    def record(self, request_id, job_id, spec, *, prompt=None, source='model', model=None, usage=None):
        request_id = self.jobs.text(request_id, 'request_id', 200)
        if isinstance(spec, dict):
            try:
                spec = ChartSpec.model_validate(spec)
            except ValueError as exc:
                raise JobError('invalid_chart', str(exc)[:800], 422) from exc
        signature = json.dumps({'job_id': job_id, 'spec': spec.model_dump(), 'prompt': prompt}, sort_keys=True)
        with self.store.lock, self.store.db:
            db = self.store.db
            previous = db.execute('SELECT signature,body FROM job_chart_writes WHERE request_id=?', (request_id,)).fetchone()
            if previous:
                if previous[0] != signature:
                    raise JobError('request_conflict', 'request_id was already used with different arguments.')
                return {**json.loads(previous[1]), 'duplicate': True}
            job = self.jobs._job(db, job_id)
            self.validate(job_id, spec)
            chart = {'chart_id': uuid.uuid4().hex, 'job_id': job_id, 'job_revision': job['revision'], 'prompt': prompt,
                     'source': source, 'model': model, 'usage': usage or {}, 'created_at': utc_now(), 'spec': spec.model_dump()}
            db.execute('INSERT INTO job_charts(id,job_id,body) VALUES(?,?,?)', (chart['chart_id'], job_id, json.dumps(chart)))
            result = {'chart': chart, 'duplicate': False}
            db.execute('INSERT INTO job_chart_writes VALUES(?,?,?)', (request_id, signature, json.dumps(result)))
        self.runtime.revision += 1
        return result

    def charts(self, job_id):
        self.jobs.get(job_id)
        rows = self.store.rows('SELECT body FROM job_charts WHERE job_id=? ORDER BY seq DESC', (job_id,))
        return {'job_id': job_id, 'charts': [json.loads(r[0]) for r in rows]}

    def delete(self, request_id, job_id, chart_id):
        request_id = self.jobs.text(request_id, 'request_id', 200)
        signature = json.dumps({'delete': chart_id, 'job_id': job_id})
        with self.store.lock, self.store.db:
            db = self.store.db
            previous = db.execute('SELECT signature,body FROM job_chart_writes WHERE request_id=?', (request_id,)).fetchone()
            if previous:
                if previous[0] != signature:
                    raise JobError('request_conflict', 'request_id was already used with different arguments.')
                return {**json.loads(previous[1]), 'duplicate': True}
            self.jobs._job(db, job_id)
            deleted = db.execute('DELETE FROM job_charts WHERE id=? AND job_id=?', (chart_id, job_id)).rowcount
            result = {'chart_id': chart_id, 'deleted': bool(deleted), 'duplicate': False}
            db.execute('INSERT INTO job_chart_writes VALUES(?,?,?)', (request_id, signature, json.dumps(result)))
        self.runtime.revision += 1
        return result

    # ------------------------------------------------------------------ model-generated charts
    def requests(self, job_id):
        self.jobs.get(job_id)
        rows = self.store.rows('SELECT body FROM job_chart_requests WHERE job_id=? ORDER BY created_at DESC LIMIT 20', (job_id,))
        return {'job_id': job_id, 'requests': [json.loads(r[0]) for r in rows]}

    def _save_request(self, body):
        self.store.execute('INSERT OR REPLACE INTO job_chart_requests VALUES(?,?,?,?)',
                           (body['request_id'], body['job_id'], body['created_at'], json.dumps(body)))
        self.runtime.revision += 1

    def request(self, request_id, job_id, prompt):
        request_id = self.jobs.text(request_id, 'request_id', 200)
        prompt = self.jobs.text(prompt, 'prompt', 2000)
        self.jobs.get(job_id)
        if self.archive:
            raise JobError('archive_mode', 'Saved archive mode does not make paid model calls.')
        rows = self.store.rows('SELECT body FROM job_chart_requests WHERE request_id=?', (request_id,))
        if rows:
            existing = json.loads(rows[0][0])
            if existing['job_id'] != job_id or existing['prompt'] != prompt:
                raise JobError('request_conflict', 'request_id was already used with different arguments.')
            return {**existing, 'duplicate': True}
        body = {'request_id': request_id, 'job_id': job_id, 'prompt': prompt, 'state': 'pending', 'error': None,
                'chart_id': None, 'created_at': utc_now(), 'model': self.model_name}
        self._save_request(body)
        self.tasks[request_id] = asyncio.create_task(self.generate(body))
        return {**body, 'duplicate': False}

    async def generate(self, body):
        request_id, job_id = body['request_id'], body['job_id']
        try:
            async with self.lock:
                if not os.getenv('OPENAI_API_KEY'):
                    raise ValueError('OPENAI_API_KEY is missing. Deterministic metrics remain available.')
                async with connect_mcp(self.mcp_url) as session:
                    # Reads go through the same MCP tools any external agent would use.
                    job, _ = await call(session, 'get_job', {'job_id': job_id})
                    stats, _ = await call(session, 'get_job_statistics', {'job_id': job_id})
                    listing, _ = await call(session, 'list_job_observations', {'job_id': job_id, 'limit': MAX_MODEL_OBSERVATIONS})
                    observations = listing['observations']
                    if not observations:
                        raise ValueError('The job has no saved observations to chart yet.')
                    payload = {'job': {k: job[k] for k in ('name', 'theme')}, 'request': body['prompt'], 'statistics': {k: stats[k] for k in stats if k not in ('job', 'metrics')},
                               'observations_total': listing['total'], 'observations_included': len(observations),
                               'observations': observations}
                    async with AsyncOpenAI(timeout=90, max_retries=0) as client:
                        response = await client.responses.parse(model=self.model_name, instructions=INSTRUCTIONS,
                            input=[{'role': 'user', 'content': json.dumps(payload)}], text_format=ChartDraft,
                            max_output_tokens=8000, store=False)
                    if response.output_parsed is None:
                        raise ValueError('No complete chart was returned.')
                    draft = response.output_parsed
                    if draft.chart is None:
                        raise ValueError(draft.cannot_answer or 'The model could not answer this from saved observations.')
                    spec = draft.chart
                    spec.coverage = Coverage(observations_used=len(observations), observations_available=listing['total'],
                                             start_at=stats.get('start_at'), end_at=stats.get('end_at'))
                    if len(observations) < listing['total']:
                        spec.uncertainties = (spec.uncertainties + [f'Chart based on the first {len(observations)} of {listing["total"]} observations.'])[:10]
                    usage = response.usage.model_dump() if response.usage else {}
                    result, _ = await call(session, 'record_job_chart', {
                        'request_id': f'chart-request:{request_id}', 'job_id': job_id, 'spec': spec.model_dump(),
                        'prompt': body['prompt'], 'model': self.model_name, 'usage': usage})
                self._save_request({**body, 'state': 'ready', 'chart_id': result['chart']['chart_id'], 'error': None,
                                    'usage': usage, 'completed_at': utc_now()})
        except asyncio.CancelledError:
            self._save_request({**body, 'state': 'failed', 'error': 'Generation interrupted.'})
            raise
        except Exception as exc:
            while isinstance(exc, BaseExceptionGroup) and len(exc.exceptions) == 1:
                exc = exc.exceptions[0]  # MCP transport wraps tool errors in a TaskGroup.
            self._save_request({**body, 'state': 'failed', 'error': str(exc)[:500]})
        finally:
            self.tasks.pop(request_id, None)

    async def close(self):
        for task in list(self.tasks.values()):
            task.cancel()
        await asyncio.gather(*self.tasks.values(), return_exceptions=True)


def register_statistics_tools(server, statistics: Statistics):
    @server.tool()
    def get_job_statistics(job_id: str) -> dict:
        """Deterministic KPI summary for one job: sessions, observation windows, change rate, actions, latency, tokens, gaps."""
        return statistics.summary(job_id)

    @server.tool()
    def get_job_metric(job_id: str, metric: str, bucket_seconds: int | None = None) -> dict:
        """Deterministic metric chart for a job as a ChartSpec. metric: observations|actions|uncertainties|latency|tokens|gaps|assertions|sessions."""
        return statistics.metric(job_id, metric, bucket_seconds).model_dump()

    @server.tool()
    def list_job_observations(job_id: str, cursor: str | None = None, limit: int = 50,
                              start_at: str | None = None, end_at: str | None = None) -> dict:
        """Text-only observation summaries for a job, paged and optionally bounded by wall-clock time. Use get_evidence for images."""
        return statistics.observations(job_id, cursor, limit, start_at, end_at)

    @server.tool()
    def record_job_chart(request_id: str, job_id: str, spec: ChartSpec, prompt: str | None = None,
                         model: str | None = None, usage: dict | None = None) -> dict:
        """Save a validated chart for a job. Every non-zero point must cite observation_ids that belong to the job. Idempotent by request_id."""
        return statistics.record(request_id, job_id, spec, prompt=prompt, source='model', model=model, usage=usage)

    @server.tool()
    def list_job_charts(job_id: str) -> dict:
        """Saved charts for a job, newest first."""
        return statistics.charts(job_id)

    @server.tool()
    def delete_job_chart(request_id: str, job_id: str, chart_id: str) -> dict:
        """Delete a saved chart. Idempotent by request_id."""
        return statistics.delete(request_id, job_id, chart_id)
