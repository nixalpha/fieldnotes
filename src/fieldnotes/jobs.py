"""Persistent job organization and immutable capture context, shared by HTTP and MCP."""
from __future__ import annotations

import json
import uuid

from .core import utc_now


class JobError(ValueError):
    def __init__(self, code, message, status=409):
        self.code, self.status = code, status
        super().__init__(f'{code}: {message}')


class Jobs:
    def __init__(self, runtime, store, *, archive=False):
        self.runtime, self.store, self.archive = runtime, store, archive
        with store.lock, store.db:
            store.db.executescript('''
                CREATE TABLE IF NOT EXISTS jobs (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL,
                    name TEXT NOT NULL, theme TEXT NOT NULL, revision INTEGER NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS job_revisions (
                    job_id TEXT NOT NULL, revision INTEGER NOT NULL, body TEXT NOT NULL,
                    PRIMARY KEY(job_id,revision));
                CREATE TABLE IF NOT EXISTS job_memberships (
                    session_id TEXT PRIMARY KEY, job_id TEXT NOT NULL,
                    assigned_at TEXT NOT NULL, assignment_kind TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS job_memberships_job ON job_memberships(job_id,session_id);
                CREATE TABLE IF NOT EXISTS job_selection (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1), job_id TEXT,
                    revision INTEGER NOT NULL);
                INSERT OR IGNORE INTO job_selection VALUES(1,NULL,0);
                CREATE TABLE IF NOT EXISTS job_requests (
                    request_id TEXT PRIMARY KEY, signature TEXT NOT NULL, body TEXT NOT NULL);
            ''')

    @staticmethod
    def text(value, name, maximum):
        if not isinstance(value, str) or not 1 <= len(value.strip()) <= maximum:
            raise JobError('invalid_arguments', f'{name} must contain 1–{maximum} characters.', 422)
        return value.strip()

    def _selection(self, db):
        job_id, revision = db.execute('SELECT job_id,revision FROM job_selection WHERE singleton=1').fetchone()
        return {'selected_job_id': job_id, 'selection_revision': revision}

    def _job(self, db, job_id):
        row = db.execute('SELECT id,name,theme,revision,created_at,updated_at FROM jobs WHERE id=?', (job_id,)).fetchone()
        if not row:
            raise JobError('unknown_job', 'The requested job does not exist.', 404)
        result = dict(zip(('job_id', 'name', 'theme', 'revision', 'created_at', 'updated_at'), row))
        result['observation_count'] = db.execute('SELECT count(*) FROM job_memberships WHERE job_id=?', (job_id,)).fetchone()[0]
        result['selected'] = self._selection(db)['selected_job_id'] == job_id
        return result

    def get(self, job_id):
        with self.store.lock:
            return self._job(self.store.db, job_id)

    def list(self, limit=50, cursor=None):
        if not 1 <= limit <= 200:
            raise JobError('invalid_arguments', 'limit must be 1–200.', 422)
        try:
            before = int(cursor) if cursor is not None else 2**63-1
            if before < 0:
                raise ValueError()
        except (ValueError, TypeError) as exc:
            raise JobError('invalid_arguments', 'Invalid jobs cursor.', 422) from exc
        with self.store.lock:
            db = self.store.db
            rows = db.execute('SELECT seq,id FROM jobs WHERE seq<? ORDER BY seq DESC LIMIT ?', (before, limit+1)).fetchall()
            return {'jobs': [self._job(db, row[1]) for row in rows[:limit]],
                    'next_cursor': str(rows[limit-1][0]) if len(rows) > limit else None,
                    **self._selection(db), 'selection_allowed': not self.archive}

    def _request(self, request_id, arguments, operation):
        request_id = self.text(request_id, 'request_id', 200)
        signature = json.dumps(arguments, sort_keys=True)
        # No store.execute() inside this transaction: it commits independently.
        with self.store.lock, self.store.db:
            db = self.store.db
            previous = db.execute('SELECT signature,body FROM job_requests WHERE request_id=?', (request_id,)).fetchone()
            if previous:
                if previous[0] != signature:
                    raise JobError('request_conflict', 'request_id was already used with different arguments.')
                return {**json.loads(previous[1]), 'duplicate': True}
            result = {**operation(db), 'duplicate': False}
            db.execute('INSERT INTO job_requests VALUES(?,?,?)', (request_id, signature, json.dumps(result)))
        self.runtime.revision += 1
        return result

    def _save_revision(self, db, job_id):
        job = self._job(db, job_id)
        db.execute('INSERT INTO job_revisions VALUES(?,?,?)', (job_id, job['revision'], json.dumps(job)))
        return job

    def create(self, request_id, name, theme):
        name, theme = self.text(name, 'name', 120), self.text(theme, 'theme', 4000)
        def operation(db):
            job_id, now = uuid.uuid4().hex, utc_now()
            db.execute('INSERT INTO jobs(id,name,theme,revision,created_at,updated_at) VALUES(?,?,?,1,?,?)',
                       (job_id, name, theme, now, now))
            return {'job': self._save_revision(db, job_id)}
        return self._request(request_id, {'action': 'create', 'name': name, 'theme': theme}, operation)

    def update(self, request_id, job_id, expected_revision, name=None, theme=None):
        if name is None and theme is None:
            raise JobError('invalid_arguments', 'Provide a name or theme to update.', 422)
        if name is not None:
            name = self.text(name, 'name', 120)
        if theme is not None:
            theme = self.text(theme, 'theme', 4000)
        def operation(db):
            job = self._job(db, job_id)
            if job['revision'] != expected_revision:
                raise JobError('stale_revision', 'Job changed. Read the latest job and retry with its revision.')
            next_name, next_theme = name or job['name'], theme or job['theme']
            if (next_name, next_theme) == (job['name'], job['theme']):
                return {'job': job}
            db.execute('UPDATE jobs SET name=?,theme=?,revision=revision+1,updated_at=? WHERE id=?',
                       (next_name, next_theme, utc_now(), job_id))
            return {'job': self._save_revision(db, job_id)}
        return self._request(request_id, {'action': 'update', 'job_id': job_id, 'expected_revision': expected_revision,
                                         'name': name, 'theme': theme}, operation)

    def switch(self, request_id, job_id, expected_selection_revision):
        if self.archive:
            raise JobError('archive_read_only', 'Capture selection cannot change in a saved archive.')
        def operation(db):
            selected = self._selection(db)
            if selected['selection_revision'] != expected_selection_revision:
                raise JobError('stale_selection', 'Selection changed. Read get_job_context and retry with its selection revision.')
            if job_id is not None:
                self._job(db, job_id)
            if selected['selected_job_id'] != job_id:
                db.execute('UPDATE job_selection SET job_id=?,revision=revision+1 WHERE singleton=1', (job_id,))
            return {**self._selection(db), 'selected_job': self._job(db, job_id) if job_id else None,
                    'effective': 'next_session', 'current_session_unchanged': True}
        return self._request(request_id, {'action': 'switch', 'job_id': job_id,
                                         'expected_selection_revision': expected_selection_revision}, operation)

    def assign(self, request_id, job_id, session_ids):
        if not isinstance(session_ids, list) or not 1 <= len(session_ids) <= 100 or any(not isinstance(s, str) or not s for s in session_ids):
            raise JobError('invalid_arguments', 'Provide 1–100 session IDs.', 422)
        ids = sorted(set(session_ids))
        def operation(db):
            self._job(db, job_id)
            assigned, existing = [], []
            # Validate the complete batch before changing any membership.
            for sid in ids:
                if not db.execute('SELECT 1 FROM recording_sessions WHERE id=?', (sid,)).fetchone():
                    raise JobError('unknown_session', f'Observation {sid} does not exist.', 404)
                membership = db.execute('SELECT job_id FROM job_memberships WHERE session_id=?', (sid,)).fetchone()
                if membership and membership[0] != job_id:
                    raise JobError('membership_conflict', f'Observation {sid} already belongs to another job.')
                (existing if membership else assigned).append(sid)
            for sid in assigned:
                db.execute('INSERT INTO job_memberships VALUES(?,?,?,?)', (sid, job_id, utc_now(), 'assigned'))
            return {'job': self._job(db, job_id), 'assigned_session_ids': assigned, 'unchanged_session_ids': existing,
                    'capture_context_unchanged': True}
        return self._request(request_id, {'action': 'assign', 'job_id': job_id, 'session_ids': ids}, operation)

    def capture_session(self, body):
        """Persist session and initial membership together before any frame arrives."""
        with self.store.lock, self.store.db:
            db = self.store.db
            sid = body['session_id']
            selected = self._selection(db)['selected_job_id'] if not self.archive else None
            job = self._job(db, selected) if selected else None
            body['job_context'] = {key: job[key] for key in ('job_id', 'name', 'theme', 'revision')} if job else None
            db.execute('INSERT INTO recording_sessions(id,state,body) VALUES(?,?,?)', (sid, body['state'], json.dumps(body)))
            if job:
                db.execute('INSERT INTO job_memberships VALUES(?,?,?,?)', (sid, selected, utc_now(), 'captured'))
        return body

    def describe(self, session):
        with self.store.lock:
            db = self.store.db
            row = db.execute('SELECT job_id,assigned_at,assignment_kind FROM job_memberships WHERE session_id=?',
                             (session['session_id'],)).fetchone()
            job = self._job(db, row[0]) if row else None
            return {**session, 'job_id': row[0] if row else None, 'job': job,
                    'job_context': session.get('job_context'),
                    'job_assignment': {'assigned_at': row[1], 'kind': row[2]} if row else None}

    def context(self):
        with self.store.lock:
            selection = self._selection(self.store.db)
            job_id = selection['selected_job_id']
            current = self.runtime.sessions.current if self.runtime.sessions else None
            return {**selection, 'selected_job': self._job(self.store.db, job_id) if job_id else None,
                    'current_session': self.describe(current) if current else None,
                    'selection_allowed': not self.archive}
