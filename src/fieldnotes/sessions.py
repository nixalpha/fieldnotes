"""Recording-session lifecycle. Mutations run synchronously on the app event loop."""
from __future__ import annotations

import asyncio
import json
import uuid

from .core import utc_now


class Sessions:
    grace_ms = 10_000

    def __init__(self, runtime, store, *, archive=False):
        self.runtime, self.store, self.archive = runtime, store, archive
        self.current = None
        self.last_ended = None
        self.on_end = None
        self._backfill()
        if not archive:
            for (raw,) in store.rows("SELECT body FROM recording_sessions WHERE state IN ('active','waiting_for_video')"):
                old = json.loads(raw)
                old.update(state='interrupted', end_reason='application_interrupted',
                           ended_at=None, end_detected_at=utc_now(), exact_end_known=False)
                self._save(old)
            runtime.session_id = None

    def _save(self, body):
        self.store.execute('''INSERT INTO recording_sessions(id,state,body) VALUES(?,?,?)
            ON CONFLICT(id) DO UPDATE SET state=excluded.state,body=excluded.body''',
            (body['session_id'], body['state'], json.dumps(body)))

    def _backfill(self):
        known = {r[0] for r in self.store.rows('SELECT id FROM recording_sessions')}
        records = {}

        def add(sid, source, first, last, start_at=None, end_at=None):
            if sid in known:
                return
            body = records.setdefault(sid, {'session_id': sid, 'name': None, 'source': source,
                'state': 'ended', 'created_at': None, 'started_at': None, 'ended_at': None,
                'end_detected_at': None, 'start_reason': 'historical_import',
                'end_reason': 'historical_boundary_inferred', 'boundaries_inferred': True,
                'exact_end_known': False, 'first_frame': None, 'last_frame': None,
                'start_elapsed_ms': None, 'end_elapsed_ms': None, 'frame_count_received': None})
            if first and (body['first_frame'] is None or first['elapsed_ms'] < body['first_frame']['elapsed_ms']):
                body['first_frame'] = first
                body['started_at'] = first['received_at']
                body['start_elapsed_ms'] = first['elapsed_ms']
            if last and (body['last_frame'] is None or last['elapsed_ms'] > body['last_frame']['elapsed_ms']):
                body['last_frame'] = last
                body['last_received_at'] = last['received_at']
                body['end_elapsed_ms'] = last['elapsed_ms'] + 1
            body['created_at'] = body['created_at'] or start_at or (first or {}).get('received_at')
            body['observed_until_at'] = body.get('last_received_at') or end_at

        for sid, raw in self.runtime.journal.db.execute('SELECT session_id,body FROM observations'):
            observation = json.loads(raw)
            frames = sorted(observation['frames'], key=lambda f: f['elapsed_ms'])
            add(sid, observation.get('source', 'drone'), frames[0] if frames else None,
                frames[-1] if frames else None, observation.get('start_at'), observation.get('end_at'))
        for (raw,) in self.store.iter_rows('SELECT body FROM evidence ORDER BY session,elapsed'):
            frame = json.loads(raw)
            add(frame['session_id'], frame.get('source', 'drone'), frame, frame)
        for body in records.values():
            self._save(body)

    def get(self, session_id):
        rows = self.store.rows('SELECT body FROM recording_sessions WHERE id=?', (session_id,))
        if not rows:
            raise ValueError('Unknown session_id')
        return json.loads(rows[0][0])

    def rename(self, session_id, name):
        name = name.strip()
        if not 1 <= len(name) <= 100:
            raise ValueError('Name must contain 1–100 characters.')
        body = self.get(session_id)
        body.update(name=name, display_name=name)
        self._save(body)
        # Keep lifecycle snapshots in sync so later frame/end writes retain the name.
        if self.current and self.current['session_id'] == session_id:
            self.current.update(name=name, display_name=name)
        if self.last_ended and self.last_ended['session_id'] == session_id:
            self.last_ended.update(name=name, display_name=name)
        self.runtime.revision += 1
        return body

    def describe(self, session):
        jobs = getattr(self.runtime, 'jobs', None)
        return jobs.describe(session) if jobs else {**session, 'job_id': None, 'job': None,
                                                    'job_context': session.get('job_context'), 'job_assignment': None}

    def list(self, limit=50, cursor=None, job_id=None, untracked_only=False):
        if not 1 <= limit <= 200:
            raise ValueError('limit must be 1..200')
        if job_id is not None and untracked_only:
            raise ValueError('job_id and untracked_only cannot be combined')
        try:
            before = int(cursor) if cursor is not None else 2**63-1
            if before < 0:
                raise ValueError()
        except (ValueError, TypeError):
            raise ValueError('Invalid session cursor') from None
        jobs = getattr(self.runtime, 'jobs', None)
        predicate, args = '', [before]
        if job_id is not None:
            if not jobs:
                raise ValueError('Jobs service unavailable')
            jobs.get(job_id)
            predicate = ' AND EXISTS (SELECT 1 FROM job_memberships m WHERE m.session_id=s.id AND m.job_id=?)'
            args.append(job_id)
        elif untracked_only and jobs:
            predicate = ' AND NOT EXISTS (SELECT 1 FROM job_memberships m WHERE m.session_id=s.id)'
        args.append(limit+1)
        rows = self.store.rows(f'''SELECT s.seq,s.body,count(e.id) FROM recording_sessions s
            LEFT JOIN evidence e ON e.session=s.id WHERE s.seq<?{predicate}
            GROUP BY s.seq ORDER BY s.seq DESC LIMIT ?''', tuple(args))
        return {'sessions': [self.describe({**json.loads(raw), 'frame_count': count}) for _, raw, count in rows[:limit]],
                'next_cursor': str(rows[limit-1][0]) if len(rows)>limit else None,
                'active_session_id': self.current['session_id'] if self.current else None,
                'archive': self.archive}

    def status(self):
        current = self.current
        age = None
        if current and current.get('last_frame'):
            age = self.runtime.elapsed_ms() - current['last_frame']['elapsed_ms']
        return {'jobs': self.runtime.jobs.context() if getattr(self.runtime, 'jobs', None) else None,
                'active_session_id': current['session_id'] if current else None,
                'session_state': current['state'] if current else 'ended' if self.last_ended or self.archive else 'waiting_for_video',
                'session_name': current.get('name') if current else None,
                'last_ended_session': self.last_ended,
                'automatic_start': not self.archive,
                'stream_end_grace_ms': self.grace_ms,
                'interruption_grace_remaining_ms': max(0, self.grace_ms-age) if age is not None and age>=3000 else None}

    def _new(self, reason, name=None):
        now = self.runtime.elapsed_ms()
        body = {'session_id': uuid.uuid4().hex, 'name': name, 'source': self.runtime.source,
                'state': 'waiting_for_video', 'created_at': utc_now(), 'created_elapsed_ms': now,
                'started_at': None, 'ended_at': None, 'end_detected_at': None,
                'start_reason': reason, 'end_reason': None, 'boundaries_inferred': False,
                'exact_end_known': True, 'first_frame': None, 'last_frame': None,
                'start_elapsed_ms': None, 'end_elapsed_ms': None, 'frame_count_received': 0}
        if getattr(self.runtime, 'jobs', None):
            self.runtime.jobs.capture_session(body)
        else:
            body['job_context'] = None
            self._save(body)
        self.current = body
        self.runtime.session_id = body['session_id']
        self.runtime.first_frame_ms = None
        self.runtime.buffer.clear()
        self.runtime.revision += 1
        return body

    def before_frame(self):
        self.expire()
        if self.current is None:
            self._new('stream_started')

    def received(self, frame):
        body = self.current
        meta = frame.metadata()
        if body['first_frame'] is None:
            body.update(first_frame=meta, started_at=frame.received_at,
                        start_elapsed_ms=frame.elapsed_ms, state='active')
        body.update(last_frame=meta, last_received_at=frame.received_at)
        body['frame_count_received'] += 1
        self._save(body)

    def _end(self, reason):
        body = self.current
        if body is None:
            return None
        # Freeze evidence while this session's buffer is still available.
        if self.on_end:
            self.on_end(body, reason)
        last = body['last_frame']
        body.update(state='ended', ended_at=utc_now(), end_detected_at=utc_now(),
                    end_reason=reason, end_elapsed_ms=last['elapsed_ms']+1 if last else None,
                    observed_until_at=last['received_at'] if last else None)
        self._save(body)
        self.last_ended = {k: body.get(k) for k in ('session_id','state','ended_at','end_reason','observed_until_at')}
        self.current = None
        self.runtime.session_id = None
        self.runtime.first_frame_ms = None
        self.runtime.buffer.clear()
        self.runtime.revision += 1
        return body

    def expire(self):
        if self.current and self.current['last_frame']:
            if self.runtime.elapsed_ms() - self.current['last_frame']['elapsed_ms'] >= self.grace_ms:
                self._end('stream_timeout')

    async def watch(self):
        while True:
            self.expire()
            await asyncio.sleep(.25)

    def _request(self, request_id, payload, operation):
        if self.archive:
            raise ValueError('Archive mode: session lifecycle is read-only')
        if not isinstance(request_id, str) or not request_id.strip() or len(request_id)>200:
            raise ValueError('request_id must be 1..200 characters')
        signature = json.dumps(payload, sort_keys=True)
        rows = self.store.rows('SELECT signature,body FROM session_requests WHERE id=?', (request_id,))
        if rows:
            if rows[0][0] != signature:
                raise ValueError('request_id was already used with different arguments')
            return {**json.loads(rows[0][1]), 'duplicate': True}
        result = {'session': self.describe(operation()), 'duplicate': False,
                  'automatic_start': True, 'notice': 'The next decoded frame starts a session if none is active.'}
        encoded = json.dumps(result)
        self.store.execute('INSERT INTO session_requests VALUES(?,?,?)', (request_id, signature, encoded))
        return json.loads(encoded)

    def start(self, request_id, name=None, expected_active_session_id=None):
        if name is not None and (not name.strip() or len(name)>120):
            raise ValueError('name must be 1..120 characters')
        def operation():
            active = self.current['session_id'] if self.current else None
            if active != expected_active_session_id:
                raise ValueError('Active session changed; supply its current ID before rotating')
            if active:
                self._end('agent_rotated')
            return self._new('agent_started', name)
        return self._request(request_id, {'action':'start', 'name':name,
                             'expected_active_session_id':expected_active_session_id}, operation)

    def end(self, session_id, request_id, reason=None):
        if reason is not None and len(reason)>500:
            raise ValueError('reason must be at most 500 characters')
        def operation():
            body = self.get(session_id)
            if body['state'] in ('ended','interrupted'):
                return body
            if not self.current or self.current['session_id'] != session_id:
                raise ValueError('The requested session is not active')
            body = self._end('agent_ended')
            body['agent_end_reason'] = reason
            self._save(body)
            return body
        return self._request(request_id, {'action':'end', 'session_id':session_id, 'reason':reason}, operation)

    def shutdown(self):
        if not self.archive:
            self._end('application_shutdown')
