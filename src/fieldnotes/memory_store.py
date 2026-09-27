"""Evidence-backed interpretations. Citation validity is not semantic truth."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import uuid
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from .core import utc_now


class Assertion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subject: str = Field(min_length=1, max_length=120)
    predicate: str = Field(min_length=1, max_length=120)
    value: str = Field(min_length=1, max_length=1500)
    evidence_ids: list[str] = Field(min_length=1, max_length=20)
    epistemic: Literal["visually_supported", "inferred", "unknown"]
    uncertainty: str = Field(default="", max_length=2000)
    revises: str | None = None
    track_id: str | None = None


class MemoryProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str
    idempotency_key: str = Field(min_length=1, max_length=200)
    evidence_bundle_ids: list[str] = Field(min_length=1, max_length=20)
    assertions: list[Assertion] = Field(min_length=1, max_length=20)
    fixture_id: str | None = Field(default=None, max_length=200)


class MemoryStore:
    def __init__(self, root: Path, provenance: str):
        self.root, self.provenance = root.resolve(), provenance
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.root / "memory.sqlite3", check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS evidence(id TEXT PRIMARY KEY, session TEXT NOT NULL, elapsed INTEGER NOT NULL, body TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS evidence_time ON evidence(session,elapsed);
        CREATE TABLE IF NOT EXISTS bundles(id TEXT PRIMARY KEY, session TEXT NOT NULL, body TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS writes(id TEXT PRIMARY KEY, digest TEXT NOT NULL, body TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS assertions(seq INTEGER PRIMARY KEY, id TEXT UNIQUE, session TEXT, subject TEXT, predicate TEXT, elapsed INTEGER, body TEXT);
        CREATE INDEX IF NOT EXISTS assertion_time ON assertions(session,subject,predicate,elapsed);
        CREATE VIRTUAL TABLE IF NOT EXISTS assertion_text USING fts5(id UNINDEXED, session UNINDEXED, text);
        CREATE TABLE IF NOT EXISTS changes(id TEXT PRIMARY KEY, session TEXT, elapsed INTEGER, body TEXT);
        CREATE TABLE IF NOT EXISTS tracks(id TEXT PRIMARY KEY, session TEXT, body TEXT);
        CREATE TABLE IF NOT EXISTS track_events(seq INTEGER PRIMARY KEY, track TEXT, body TEXT);
        CREATE TABLE IF NOT EXISTS track_frames(track TEXT, evidence TEXT, body TEXT, PRIMARY KEY(track,evidence));
        CREATE TABLE IF NOT EXISTS embeddings(evidence TEXT, version TEXT, body TEXT, PRIMARY KEY(evidence,version));
        CREATE TABLE IF NOT EXISTS processing(id TEXT PRIMARY KEY, body TEXT);
        CREATE TABLE IF NOT EXISTS recording_sessions(seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE, state TEXT NOT NULL, body TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS session_requests(id TEXT PRIMARY KEY, signature TEXT NOT NULL, body TEXT NOT NULL);
        """)

    def rows(self, query, args=()):
        with self.lock:
            return self.db.execute(query, args).fetchall()

    def iter_rows(self, query, args=()):
        # A read snapshot with bounded batches avoids loading the entire visual archive.
        db = sqlite3.connect((self.root / "memory.sqlite3").as_uri() + "?mode=ro", uri=True)
        try:
            cursor = db.execute(query, args)
            while batch := cursor.fetchmany(128):
                yield from batch
        finally:
            db.close()

    def execute(self, query, args=()):
        with self.lock, self.db:
            self.db.execute(query, args)

    def asset(self, relative: str) -> Path:
        path = (self.root / relative).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError("Asset escapes archive")
        return path

    def add_evidence(self, frame: dict, *, source_session: str | None = None) -> dict:
        frame = dict(frame)
        eid = f"{frame['session_id']}:{frame['frame_id']}"
        asset = f"evidence/{frame['session_id']}/{frame['frame_id']}.jpg"
        digest = hashlib.sha256(self.asset(asset).read_bytes()).hexdigest()
        body = {**frame, "id": eid, "source_session_id": source_session or frame['session_id'],
                "asset": asset, "sha256": digest, "time_basis": "original local receipt, not exposure"}
        existing = self.rows("SELECT body FROM evidence WHERE id=?", (eid,))
        if existing:
            old = json.loads(existing[0][0])
            if any(old[k] != body[k] for k in ("sha256", "elapsed_ms", "received_at", "stream_epoch")):
                raise ValueError("Conflicting immutable evidence metadata")
            return old
        self.execute("INSERT INTO evidence VALUES(?,?,?,?)", (eid, frame['session_id'], frame['elapsed_ms'], json.dumps(body)))
        return body

    def evidence(self, eid: str) -> dict:
        rows = self.rows("SELECT body FROM evidence WHERE id=?", (eid,))
        if not rows:
            raise ValueError(f"Unknown evidence: {eid}")
        return json.loads(rows[0][0])

    def frames(self, session: str, start: int = 0, end: int | None = None, limit: int = 1000):
        if not 1 <= limit <= 1000:
            raise ValueError("limit must be 1..1000")
        return [json.loads(r[0]) for r in self.rows(
            "SELECT body FROM evidence WHERE session=? AND elapsed>=? AND elapsed<=? ORDER BY elapsed,id LIMIT ?",
            (session, start, end if end is not None else 2**62, limit))]

    def sessions(self):
        return [dict(session_id=s, frame_count=n, start_elapsed_ms=a, end_elapsed_ms=b)
                for s,n,a,b in self.rows("SELECT session,count(*),min(elapsed),max(elapsed) FROM evidence GROUP BY session")]

    def bundle(self, ids: list[str]):
        if not 1 <= len(ids) <= 20 or len(set(ids)) != len(ids):
            raise ValueError("Request 1..20 distinct evidence IDs")
        frames = [self.evidence(i) for i in ids]
        sessions = {f['session_id'] for f in frames}
        if len(sessions) != 1:
            raise ValueError("An evidence bundle cannot cross sessions")
        bid = uuid.uuid4().hex
        body = {"evidence_bundle_id": bid, "session_id": frames[0]['session_id'], "frames": frames,
                "image_order": [{"kind":"original", "evidence_id":f['id']} for f in frames],
                "created_at": utc_now()}
        self.execute("INSERT INTO bundles VALUES(?,?,?)", (bid, frames[0]['session_id'], json.dumps(body)))
        return body

    def record(self, proposal: MemoryProposal):
        if self.provenance != "mock" and proposal.fixture_id:
            raise ValueError("Fixtures are allowed only in explicit mock mode")
        write_id = proposal.session_id + ":" + proposal.idempotency_key
        # Retrieval bundle IDs change on retry; semantic content is the idempotency boundary.
        payload = proposal.model_dump(exclude={'evidence_bundle_ids'})
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        with self.lock, self.db:
            permitted = set()
            for bid in proposal.evidence_bundle_ids:
                row = self.db.execute("SELECT session,body FROM bundles WHERE id=?", (bid,)).fetchone()
                if not row or row[0] != proposal.session_id:
                    raise ValueError("Unknown or cross-session evidence bundle")
                permitted.update(f['id'] for f in json.loads(row[1])['frames'])
            old = self.db.execute("SELECT digest,body FROM writes WHERE id=?", (write_id,)).fetchone()
            if old:
                if old[0] != digest:
                    raise ValueError("Idempotency key already used for different content")
                return {**json.loads(old[1]), "duplicate": True}
            prepared = []
            for draft in proposal.assertions:
                if set(draft.evidence_ids) - permitted:
                    raise ValueError("Assertion cites evidence not retrieved in its bundles")
                evidence = [self.evidence(i) for i in draft.evidence_ids]
                if any(e['session_id'] != proposal.session_id for e in evidence):
                    raise ValueError("Cross-session evidence refused")
                if draft.revises:
                    prior = self.db.execute("SELECT session,subject,predicate FROM assertions WHERE id=?", (draft.revises,)).fetchone()
                    if not prior or prior != (proposal.session_id, draft.subject, draft.predicate):
                        raise ValueError("Correction must reference the same session, subject and predicate")
                if draft.track_id:
                    track = self.track(draft.track_id)
                    if track['session_id'] != proposal.session_id:
                        raise ValueError("Cross-session track")
                prepared.append((draft, evidence))
            saved, changes = [], []
            for draft, evidence in prepared:
                last = max(evidence, key=lambda e:e['elapsed_ms'])
                first = min(evidence, key=lambda e:e['elapsed_ms'])
                prior = self.db.execute("SELECT body FROM assertions WHERE session=? AND subject=? AND predicate=? AND elapsed<=? ORDER BY elapsed DESC,seq DESC LIMIT 1",
                    (proposal.session_id,draft.subject,draft.predicate,last['elapsed_ms'])).fetchone()
                body = {**draft.model_dump(), "id": uuid.uuid4().hex, "session_id": proposal.session_id,
                    "observed_start_ms": first['elapsed_ms'], "last_observed_ms": last['elapsed_ms'],
                    "last_observed_at": last['received_at'], "created_at": utc_now(),
                    "interpretation_source": self.provenance, "fixture_id": proposal.fixture_id,
                    "warning": "Fallible interpretation; citations do not establish semantic correctness"}
                self.db.execute("INSERT INTO assertions(id,session,subject,predicate,elapsed,body) VALUES(?,?,?,?,?,?)",
                    (body['id'],proposal.session_id,draft.subject,draft.predicate,last['elapsed_ms'],json.dumps(body)))
                self.db.execute("INSERT INTO assertion_text VALUES(?,?,?)", (body['id'],proposal.session_id,
                    f"{draft.subject} {draft.predicate} {draft.value} {draft.uncertainty}"))
                if prior:
                    before = json.loads(prior[0])
                    if before['value'] != draft.value or draft.revises:
                        kind = ('correction' if draft.revises else 'coverage_uncertainty' if draft.epistemic == 'unknown'
                                or before['epistemic'] == 'unknown' else 'contradiction' if before['last_observed_ms'] >= first['elapsed_ms'] else 'change_candidate')
                        change = {"id":uuid.uuid4().hex, "kind":kind, "before":before, "after":body,
                            "earliest_ms":before['last_observed_ms'], "latest_ms":last['elapsed_ms'],
                            "exact_change_time_known":False, "interpretation_source":self.provenance,
                            "explanation":"Compare originals; camera motion and visibility changes can explain differences."}
                        self.db.execute("INSERT INTO changes VALUES(?,?,?,?)", (change['id'],proposal.session_id,last['elapsed_ms'],json.dumps(change)))
                        changes.append(change)
                saved.append(body)
            result = {"assertions":saved,"changes":changes,"interpretation_source":self.provenance,"duplicate":False}
            self.db.execute("INSERT INTO writes VALUES(?,?,?)", (write_id,digest,json.dumps(result)))
            return result

    def state(self, session: str, at: int | None = None):
        at = at if at is not None else 2**62
        # Latest interpretation per predicate; keep history separately rather than imply continuous truth.
        rows = self.rows("""SELECT body FROM (SELECT body, ROW_NUMBER() OVER
            (PARTITION BY subject,predicate ORDER BY elapsed DESC,seq DESC) AS n
            FROM assertions WHERE session=? AND elapsed<=?) WHERE n=1 LIMIT 201""", (session,at))
        states = []
        for (raw,) in rows[:200]:
            body=json.loads(raw)
            prior=self.rows("SELECT body FROM assertions WHERE session=? AND subject=? AND predicate=? AND elapsed<=? ORDER BY elapsed DESC,seq DESC LIMIT 20",
                           (session,body['subject'],body['predicate'],body['last_observed_ms']))
            history=[json.loads(r[0]) for r in prior]
            support=next((r for r in history if r['epistemic']=='visually_supported'),None)
            candidates=[r for r in history if support and r['epistemic']=='visually_supported' and r['last_observed_ms']==support['last_observed_ms']]
            conflict=len({r['value'] for r in candidates})>1
            states.append({"latest_interpretation":body,"last_visual_support":None if conflict else support,
                           "support_conflict":conflict,"last_visual_support_candidates":candidates,"recent_history":history,
                           "current_state_verified":False})
        return {"session_id":session,"at_elapsed_ms":None if at==2**62 else at,"states":states,
                "truncated":len(rows)>200,"notice":"Last observed is not currently known. History includes fallible interpretations."}

    def changes(self, session, start=0, end=None, limit=100):
        return {"changes":[json.loads(r[0]) for r in self.rows("SELECT body FROM changes WHERE session=? AND elapsed>=? AND elapsed<=? ORDER BY elapsed LIMIT ?",
            (session,start,end if end is not None else 2**62,min(max(limit,1),200)))]}

    def text_search(self, query, session, limit=8):
        terms = [p for p in query.split() if p.strip()][:12]
        if not terms: return []
        expression = ' OR '.join('"'+t.replace('"','""')+'"' for t in terms)
        return [json.loads(r[0]) for r in self.rows("SELECT a.body FROM assertion_text f JOIN assertions a ON a.id=f.id WHERE assertion_text MATCH ? AND f.session=? ORDER BY rank LIMIT ?", (expression,session,limit))]

    def track(self, tid):
        rows=self.rows("SELECT body FROM tracks WHERE id=?", (tid,))
        if not rows: raise ValueError("Unknown track")
        return self._with_recovery([json.loads(rows[0][0])])[0]

    def save_track(self, body):
        self.execute("INSERT INTO tracks VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body",(body['id'],body['session_id'],json.dumps(body)))

    def tracks(self):
        return self._with_recovery([json.loads(r[0]) for r in self.rows("SELECT body FROM tracks ORDER BY rowid DESC LIMIT 100")])

    def _with_recovery(self, tracks):
        # Recovery is a link to a later complete attempt, never a rewrite of an
        # error or a claim that its old masks became valid.
        if not any(t['state']=='error' for t in tracks): return tracks
        completed=[json.loads(r[0]) for r in self.rows(
            "SELECT body FROM tracks WHERE json_extract(body,'$.state')='completed' ORDER BY rowid DESC LIMIT 100")]
        for track in tracks:
            if track['state']!='error': continue
            for candidate in completed:
                if candidate.get('created_at','') <= track.get('created_at',''): continue
                if not all(candidate.get(k)==track.get(k) for k in
                           ('session_id','seed_evidence_id','points','labels','preview_only')): continue
                if candidate.get('processed_frames',0) <= 0: continue
                # Every retained frame of the failed attempt must be covered.
                missing=self.rows('''SELECT 1 FROM track_frames f WHERE f.track=? AND NOT EXISTS
                    (SELECT 1 FROM track_frames c WHERE c.track=? AND c.evidence=f.evidence) LIMIT 1''',
                    (track['id'],candidate['id']))
                if missing: continue
                track['recovery']={'track_id':candidate['id'],'label':candidate['label'],
                    'processed_frames':candidate['processed_frames'],'model_device':candidate.get('model_device'),
                    'notice':'A later attempt with the same seed completed. Original failed outputs remain diagnostic only.'}
                break
        return tracks

    def track_frames(self, tid, limit=8, after=0):
        rows=self.rows("SELECT body FROM track_frames WHERE track=? ORDER BY rowid LIMIT ? OFFSET ?",(tid,limit,after))
        return [json.loads(r[0],parse_constant=lambda _:None) for r in rows]

    def close(self):
        with self.lock: self.db.close()
