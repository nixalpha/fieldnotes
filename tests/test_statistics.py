"""Job-scoped statistics: deterministic metrics, ChartSpec validation, idempotent persistence, model round trip."""
import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from conftest import running_app

from fieldnotes.core import Runtime
from fieldnotes.jobs import JobError, Jobs
from fieldnotes.memory_store import MemoryStore
from fieldnotes.sessions import Sessions
from fieldnotes.statistics import ChartDraft, ChartSpec, Statistics

A, B = 'a' * 32, 'b' * 32


def seed_session(runtime, sid, started='2026-09-27T10:00:00+00:00'):
    runtime.sessions._save({'session_id': sid, 'name': f'Session {sid[0]}', 'state': 'ended', 'source': 'drone',
                            'started_at': started, 'created_at': started, 'last_received_at': started,
                            'start_elapsed_ms': 0, 'end_elapsed_ms': 60000})


def seed_observation(runtime, store, sid, index, *, minute=0, state='change_observed', actions=1, gaps=None,
                     duration=1200, tokens=(100, 20), summarized=True):
    start = index * 5000
    frame = {'frame_id': index, 'session_id': sid, 'id': f'{sid}:{index}', 'elapsed_ms': start + 100,
             'received_at': f'2026-09-27T10:{minute:02d}:01+00:00', 'asset': f'evidence/{sid}/{index}.jpg', 'source': 'drone'}
    body = {'observation_id': f'{sid}-{index}', 'session_id': sid, 'start_elapsed_ms': start, 'end_elapsed_ms': start + 5000,
            'frames': [frame], 'gaps': gaps or [], 'brief': None, 'job_context': None,
            'start_at': f'2026-09-27T10:{minute:02d}:00+00:00', 'end_at': f'2026-09-27T10:{minute:02d}:05+00:00'}
    summary = {**body, 'status': 'completed', 'model': 'real-model', 'duration_ms': duration,
               'usage': {'input_tokens': tokens[0], 'output_tokens': tokens[1]},
               'visual': {'summary': 'A person picks up a phone.', 'change_state': state, 'uncertainties': ['lighting'],
                          'observed_actions': [{'description': 'Picks up phone', 'frame_ids': [index]}] * actions}}
    with runtime.journal.db:
        runtime.journal.db.execute('INSERT INTO observations VALUES (?,?,?)', (body['observation_id'], sid, json.dumps(body)))
        if summarized:
            runtime.journal.db.execute('INSERT INTO summaries(observation_id,body) VALUES (?,?)',
                                       (body['observation_id'], json.dumps(summary)))
    store.execute('INSERT INTO evidence VALUES (?,?,?,?)', (frame['id'], sid, frame['elapsed_ms'], json.dumps(frame)))
    return body['observation_id']


@pytest.fixture
def setup(tmp_path):
    runtime = Runtime(tmp_path)
    store = MemoryStore(tmp_path, 'model_interpretation')
    runtime.sessions = Sessions(runtime, store)
    runtime.jobs = Jobs(runtime, store)
    stats = Statistics(runtime, store, runtime.jobs, model_name='configured-model', mcp_url='http://127.0.0.1:1/mcp')
    seed_session(runtime, A)
    seed_session(runtime, B)
    job = runtime.jobs.create('create', 'Phone use', 'Watch for phone pickups.')['job']
    runtime.jobs.assign('assign', job['job_id'], [A])
    yield runtime, store, stats, job
    store.close()
    runtime.journal.close()


def spec(observation_ids, kind='line', x_kind='time', x='2026-09-27T10:00:00+00:00', y=2, used=1, available=1):
    return ChartSpec.model_validate({
        'kind': kind, 'title': 'Phone pickups', 'subtitle': None,
        'x': {'label': 'Time', 'kind': x_kind}, 'y': {'label': 'Pickups', 'kind': 'number'},
        'series': [{'label': 'Pickups', 'points': [{'x': x, 'y': y, 'evidence': observation_ids}]}],
        'definition': 'Counted observations whose summary mentions picking up a phone.',
        'uncertainties': [], 'coverage': {'observations_used': used, 'observations_available': available}})


def test_metrics_are_scoped_to_job_memberships(setup):
    rt, store, stats, job = setup
    jid = job['job_id']
    seed_observation(rt, store, A, 1, minute=0)
    seed_observation(rt, store, A, 2, minute=0, state='no_clear_change', actions=0, gaps=[{'start_elapsed_ms': 5000, 'end_elapsed_ms': 6000, 'reason': 'missing_frames'}])
    seed_observation(rt, store, A, 3, minute=3, summarized=False)
    seed_observation(rt, store, B, 1, minute=0)  # untracked: must not leak into the job
    store.execute('INSERT INTO assertions(id,session,subject,predicate,elapsed,body) VALUES (?,?,?,?,?,?)',
                  ('as1', A, 'person', 'holding', 100, '{}'))
    store.execute('INSERT INTO assertions(id,session,subject,predicate,elapsed,body) VALUES (?,?,?,?,?,?)',
                  ('as2', B, 'person', 'holding', 100, '{}'))
    summary = stats.summary(jid)
    assert summary['session_count'] == 1 and summary['observation_count'] == 3 and summary['summarized_count'] == 2
    assert summary['change_observed_rate'] == 0.5 and summary['action_count'] == 1
    assert summary['tokens'] == {'input_tokens': 200, 'output_tokens': 40} and summary['gap_ms'] == 1000
    assert summary['assertion_count'] == 1 and summary['not_completed_count'] == 0
    assert summary['start_at'] == '2026-09-27T10:00:00+00:00' and summary['end_at'] == '2026-09-27T10:03:05+00:00'

    chart = stats.metric(jid, 'observations')
    assert chart.kind == 'stacked_bar' and chart.subtitle.endswith('10s buckets')
    labels = {s.label: s for s in chart.series}
    assert set(labels) == {'Change observed', 'No clear change', 'Not summarized'}
    assert labels['Change observed'].points[0].evidence == [f'{A}-1']
    assert len(labels['Change observed'].points) == 19  # 10:00:00 .. 10:03:00 inclusive at 10s

    actions = stats.metric(jid, 'actions', 60)
    assert [p.y for p in actions.series[0].points] == [1, 0, 0, 0]
    latency = stats.metric(jid, 'latency', 60)
    assert latency.series[0].points[0].y == 1200
    assert stats.metric(jid, 'assertions').series[0].points[0].model_dump() == {'x': 'holding', 'y': 1, 'evidence': []}
    sessions = stats.metric(jid, 'sessions')
    assert sessions.x.kind == 'category' and sessions.series[0].points[0].y == 0.25
    with pytest.raises(JobError, match='invalid_arguments'):
        stats.metric(jid, 'nope')
    with pytest.raises(JobError, match='invalid_arguments'):
        stats.metric(jid, 'actions', 7)
    with pytest.raises(JobError, match='unknown_job'):
        stats.summary('missing')

    page = stats.observations(jid, limit=2)
    assert page['total'] == 3 and page['next_cursor'] == '2' and 'frames' not in page['observations'][0]
    assert stats.observations(jid, page['next_cursor'], 2)['observations'][0]['observation_id'] == f'{A}-3'
    assert stats.observations(jid, start_at='2026-09-27T10:01:00+00:00')['total'] == 1


def test_chart_validation_and_idempotent_persistence(setup):
    rt, store, stats, job = setup
    jid = job['job_id']
    own = seed_observation(rt, store, A, 1)
    foreign = seed_observation(rt, store, B, 1)
    with pytest.raises(JobError, match='outside job'):
        stats.record('r1', jid, spec([foreign]))
    with pytest.raises(JobError, match='cites no observations'):
        stats.record('r1', jid, spec([]))
    with pytest.raises(JobError, match='ISO 8601'):
        stats.record('r1', jid, spec([own], x='yesterday'))
    with pytest.raises(JobError, match='more observations'):
        stats.record('r1', jid, spec([own], available=5))
    with pytest.raises(JobError, match='invalid_chart'):
        stats.record('r1', jid, {'kind': 'pie'})
    assert stats.charts(jid)['charts'] == []

    first = stats.record('r1', jid, spec([own]), prompt='phone pickups per minute')
    assert first['duplicate'] is False and first['chart']['job_revision'] == 1
    again = stats.record('r1', jid, spec([own]), prompt='phone pickups per minute')
    assert again['duplicate'] is True and again['chart']['chart_id'] == first['chart']['chart_id']
    with pytest.raises(JobError, match='request_conflict'):
        stats.record('r1', jid, spec([own], y=3), prompt='phone pickups per minute')
    assert [c['chart_id'] for c in stats.charts(jid)['charts']] == [first['chart']['chart_id']]
    assert stats.delete('d1', jid, first['chart']['chart_id'])['deleted'] is True
    assert stats.delete('d1', jid, first['chart']['chart_id'])['duplicate'] is True
    assert stats.charts(jid)['charts'] == []


def model(monkeypatch, result):
    calls = []

    class Client:
        def __init__(self, **kwargs):
            self.responses = self

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def parse(self, **kwargs):
            calls.append(kwargs)
            await asyncio.sleep(0)
            if isinstance(result, Exception):
                raise result
            return SimpleNamespace(output_parsed=result, usage=None)

    monkeypatch.setenv('OPENAI_API_KEY', 'test-not-a-real-key')
    monkeypatch.setattr('fieldnotes.statistics.AsyncOpenAI', Client)
    return calls


async def wait_ready(client, url):
    for _ in range(100):
        body = (await client.get(url)).json()['requests'][0]
        if body['state'] != 'pending':
            return body
        await asyncio.sleep(.05)
    raise AssertionError('chart request never settled')


@pytest.mark.asyncio
async def test_model_chart_round_trip_through_rest_and_mcp(tmp_path, monkeypatch):
    drafts = {}
    calls = model(monkeypatch, None)

    class Client:
        def __init__(self, **kwargs):
            self.responses = self

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def parse(self, **kwargs):
            calls.append(kwargs)
            payload = json.loads(kwargs['input'][0]['content'])
            return SimpleNamespace(output_parsed=drafts['next'](payload), usage=None)

    monkeypatch.setattr('fieldnotes.statistics.AsyncOpenAI', Client)
    async with running_app(tmp_path, ingest=False) as (app, base):
        rt, store = app.state.runtime, app.state.memory.store
        seed_session(rt, A)
        seed_observation(rt, store, A, 1, minute=0)
        seed_observation(rt, store, A, 2, minute=1)
        async with httpx.AsyncClient(base_url=base) as client:
            job = (await client.post('/api/jobs', json={'request_id': 'c', 'name': 'Phone use', 'theme': 'Watch phone pickups.'})).json()['job']
            jid = job['job_id']
            await client.post(f'/api/jobs/{jid}/observations', json={'request_id': 'a', 'session_ids': [A]})
            summary = (await client.get(f'/api/jobs/{jid}/statistics')).json()
            assert summary['observation_count'] == 2 and summary['generation_allowed'] is True
            metric = (await client.get(f'/api/jobs/{jid}/statistics/metrics/actions?bucket_seconds=60')).json()
            assert [p['y'] for p in metric['series'][0]['points']] == [1, 1]
            assert (await client.get(f'/api/jobs/{jid}/statistics/metrics/bogus')).status_code == 422

            # Model cites only observations it was shown; coverage is stamped by the server.
            def good(payload):
                assert payload['job']['theme'] == 'Watch phone pickups.' and payload['observations_total'] == 2
                assert 'frames' not in payload['observations'][0]
                ids = [o['observation_id'] for o in payload['observations']]
                return ChartDraft(chart=spec(ids[:1], used=0, available=0), cannot_answer=None)
            drafts['next'] = good
            body = {'request_id': 'q1', 'prompt': 'Phone pickups per minute over the first ten minutes as a line graph.'}
            created = (await client.post(f'/api/jobs/{jid}/charts/requests', json=body)).json()
            assert created['state'] == 'pending' and created['duplicate'] is False
            assert (await client.post(f'/api/jobs/{jid}/charts/requests', json=body)).json()['duplicate'] is True
            assert (await client.post(f'/api/jobs/{jid}/charts/requests', json={**body, 'prompt': 'other'})).status_code == 409
            done = await wait_ready(client, f'/api/jobs/{jid}/charts/requests')
            assert done['state'] == 'ready', done
            charts = (await client.get(f'/api/jobs/{jid}/charts')).json()['charts']
            assert charts[0]['chart_id'] == done['chart_id'] and charts[0]['source'] == 'model'
            assert charts[0]['spec']['coverage'] == {'observations_used': 2, 'observations_available': 2,
                                                    'start_at': '2026-09-27T10:00:00+00:00', 'end_at': '2026-09-27T10:01:05+00:00'}
            assert charts[0]['prompt'] == body['prompt'] and charts[0]['model'] == 'gpt-6-luna'

            # Foreign citations are rejected server-side and leave the request failed, never a chart.
            drafts['next'] = lambda payload: ChartDraft(chart=spec([f'{B}-9'], used=0, available=0), cannot_answer=None)
            await client.post(f'/api/jobs/{jid}/charts/requests', json={'request_id': 'q2', 'prompt': 'Something else entirely.'})
            failed = await wait_ready(client, f'/api/jobs/{jid}/charts/requests')
            assert failed['state'] == 'failed' and 'outside job' in failed['error']
            assert len((await client.get(f'/api/jobs/{jid}/charts')).json()['charts']) == 1

            drafts['next'] = lambda payload: ChartDraft(chart=None, cannot_answer='No observation mentions cooking.')
            await client.post(f'/api/jobs/{jid}/charts/requests', json={'request_id': 'q3', 'prompt': 'Cooking events.'})
            declined = await wait_ready(client, f'/api/jobs/{jid}/charts/requests')
            assert declined['state'] == 'failed' and declined['error'] == 'No observation mentions cooking.'

            manual = (await client.post(f'/api/jobs/{jid}/charts', json={'request_id': 'm1', 'spec': spec([f'{A}-2']).model_dump()})).json()
            assert manual['chart']['source'] == 'manual'
            deleted = await client.post(f'/api/jobs/{jid}/charts/{manual["chart"]["chart_id"]}/delete', json={'request_id': 'del'})
            assert deleted.json()['deleted'] is True
            assert (await client.get('/api/jobs/missing/statistics')).status_code == 404
