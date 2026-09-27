from __future__ import annotations

import asyncio
import json
import os
import threading
import uuid
from pathlib import Path

from PIL import Image
from .core import utc_now
from .memory_store import MemoryStore
from .perception import Models, EMBED_VERSION


class Memory:
    def __init__(self, runtime, *, model_root=None, archive=False, mock=False):
        self.runtime=runtime; self.archive=archive
        self.store=MemoryStore(runtime.journal.directory,'mock' if mock else 'model_interpretation')
        self.models=Models(Path(model_root or os.getenv('FIELDNOTES_MEMORY_MODELS','data/models')))
        self.search_gate=asyncio.Semaphore(1)
        self.tasks=[]; self.track_tasks={};self.stops={};self.stop_reasons={};self.worker_error=None
        self.closing=False
        self.cursor=0;self.index_enabled=(self.models.root/'mobileclip2_s0.pt').exists()
        for track in self.store.tracks():
            if track['state'] in ('queued','running','waiting'):
                track.update(state='interrupted',reason='Application restarted; explicitly start a new track.')
                self.store.save_track(track)

    def ingest_observation(self, observation):
        refs=[]
        for frame in observation['frames']:
            existing=self.store.rows('SELECT body FROM evidence WHERE id=?',(f"{frame['session_id']}:{frame['frame_id']}",))
            refs.append(json.loads(existing[0][0]) if existing else self.store.add_evidence(frame))
        return refs

    def status(self):
        n=self.store.rows('SELECT count(*) FROM evidence')[0][0]
        indexed=self.store.rows('SELECT count(*) FROM embeddings WHERE version=?',(EMBED_VERSION,))[0][0]
        registry = self.runtime.sessions
        page = registry.list(200)
        return {**registry.status(), 'archive':self.archive,'interpretation_source':self.store.provenance,
                'sessions':page['sessions'], 'sessions_next_cursor':page['next_cursor'],
                'models':self.models.status(),'evidence_count':n,'indexed_count':indexed,'index_backlog':n-indexed,
                'rejected_memory_writes':[json.loads(r[0]) for r in self.store.rows('SELECT body FROM processing ORDER BY rowid DESC LIMIT 20')],
                'indexing_enabled':self.index_enabled,'worker_error':self.worker_error,'tracks':self.store.tracks(),
                'notice':'Interpretations are fallible; last observed does not establish current state.'}

    async def run(self):
        while not self.closing:
            try:
                # Main-thread journal access; the model runs off the API event loop.
                rows=self.runtime.journal.db.execute('SELECT rowid,body FROM observations WHERE rowid>? ORDER BY rowid LIMIT 20',(self.cursor,)).fetchall()
                for rowid,body in rows:
                    self.ingest_observation(json.loads(body));self.cursor=rowid
                if self.index_enabled:
                    pending=self.store.rows('SELECT e.body FROM evidence e LEFT JOIN embeddings v ON e.id=v.evidence AND v.version=? WHERE v.evidence IS NULL ORDER BY e.elapsed LIMIT 1',(EMBED_VERSION,))
                    if pending:
                        await asyncio.to_thread(self.models.index,self.store,json.loads(pending[0][0]))
                        self.worker_error=None
                        await asyncio.sleep(0)
                        continue
                await asyncio.sleep(1)
            except asyncio.CancelledError: raise
            except Exception as exc:
                self.worker_error=f'{type(exc).__name__}: {exc}'
                await asyncio.sleep(5)

    async def capture(self):
        queue=self.runtime.buffer.subscribe()
        try:
            while True:
                frame=await queue.get()
                if frame.session_id != self.runtime.session_id: continue
                if not any(t['session_id']==frame.session_id and t['state'] in ('queued','running','waiting') for t in self.store.tracks()): continue
                path=self.store.root/'evidence'/frame.session_id/f'{frame.frame_id}.jpg'
                path.parent.mkdir(parents=True,exist_ok=True)
                if not path.exists(): path.write_bytes(frame.jpeg)
                self.store.add_evidence(frame.metadata())
        finally: self.runtime.buffer.unsubscribe(queue)

    def end_session(self, session_id, reason):
        for tid, event in list(self.stops.items()):
            track = self.store.track(tid)
            if track['session_id'] == session_id:
                self.stop_reasons[tid] = 'Session ended: ' + reason
                event.set()

    async def search(self, query, session, start=0, end=None, limit=6):
        if self.search_gate.locked():
            raise ValueError("A visual search is already running; retry when it completes")
        async with self.search_gate:
            return await asyncio.to_thread(self.models.search,self.store,query,session,start,end,limit)

    def track_event(self, track, event):
        self.store.execute('INSERT INTO track_events(track,body) VALUES(?,?)',(track['id'],json.dumps(event)))
        track['events']=(track['events']+[event])[-20:]
        track['events_notice']='Latest 20 events; complete events persist in track_events.'

    def launch(self):
        self.tasks=[asyncio.create_task(self.run())]
        if not self.archive: self.tasks.append(asyncio.create_task(self.capture()))

    async def close(self):
        for event in self.stops.values(): event.set()
        # Allow in-flight model calls to finish before closing their SQLite connection.
        self.closing=True
        for task in self.tasks[1:]: task.cancel()
        await asyncio.gather(*self.tasks,return_exceptions=True)
        await asyncio.gather(*self.track_tasks.values(),return_exceptions=True)
        self.store.close()

    def start_track(self, evidence_id, points, labels, label, preview_only=False):
        frame=self.store.evidence(evidence_id)
        if not label.strip() or len(label)>120: raise ValueError('Label must be 1..120 characters')
        if not 1<=len(points)<=8 or len(points)!=len(labels) or 1 not in labels:
            raise ValueError('Provide 1..8 points with matching 0/1 labels and a positive point')
        for point,kind in zip(points,labels):
            if len(point)!=2 or not 0<=point[0]<frame['width'] or not 0<=point[1]<frame['height'] or kind not in (0,1):
                raise ValueError('Point outside original image or invalid label')
        if sum(t['state'] in ('queued','running','waiting') for t in self.store.tracks())>=3:
            raise ValueError('At most three active selections')
        if not (self.models.root/'edgetam.pt').exists():
            raise ValueError('EdgeTAM weights missing. Run fieldnotes setup-memory-models.')
        tid=uuid.uuid4().hex
        track={'id':tid,'session_id':frame['session_id'],'seed_evidence_id':evidence_id,'points':points,'labels':labels,
               'label':label,'state':'queued','created_at':utc_now(),'preview_only':preview_only,'processed_frames':0,
               'tracking_kind':'EdgeTAM temporal video predictor','continuity':'tentative, local to this selection',
               'events':[]}
        self.store.save_track(track)
        stop=threading.Event();self.stops[tid]=stop
        task=asyncio.create_task(asyncio.to_thread(self.track_worker,track,stop))
        self.track_tasks[tid]=task
        task.add_done_callback(lambda _: (self.track_tasks.pop(tid,None),self.stops.pop(tid,None),self.stop_reasons.pop(tid,None)))
        return track

    def stop_track(self, tid):
        track=self.store.track(tid)
        if tid in self.stops:
            self.stops[tid].set(); track['state']='stopping'
        else: track['state']='stopped'
        self.store.save_track(track)
        return track

    def track_worker(self, track, stop):
        import numpy as np
        tid=track['id']; last=self.store.evidence(track['seed_evidence_id']); mask=None;previous_area=None
        folder=self.store.root/'tracks'/tid;folder.mkdir(parents=True,exist_ok=True)
        track['state']='running';self.store.save_track(track)
        try:
            while not stop.is_set():
                frames=self.store.frames(track['session_id'],start=last['elapsed_ms'],limit=8)
                if mask is not None and len(frames)<=1:
                    recorded = self.runtime.sessions.get(track['session_id'])
                    if self.archive or recorded['state'] in ('ended','interrupted'):
                        track['state']='completed';break
                    track['state']='waiting';self.store.save_track(track);stop.wait(0.5);continue
                if track['preview_only']: frames=frames[:1]
                discontinuity=None
                for i in range(1,len(frames)):
                    if frames[i]['stream_epoch']!=frames[i-1]['stream_epoch'] or frames[i]['elapsed_ms']-frames[i-1]['elapsed_ms']>3000:
                        discontinuity={'kind':'observation_discontinuity','before':frames[i-1]['id'],'after':frames[i]['id'],
                                       'reason':'epoch change or >3s between retained observations; not proof of network outage'}
                        frames=frames[:i];break
                if mask is not None:
                    self.track_event(track,{'kind':'window_reinitialization','evidence_id':frames[0]['id'],'prompt':'previous inferred mask'})
                outputs=self.models.track_chunk(frames,self.store,track['points'],track['labels'],mask)
                track['model_device']=self.models.edge_device
                for idx,current,quality in outputs:
                    frame=frames[idx]
                    if mask is not None and idx==0: continue
                    area=int(current.sum());fraction=float(current.mean())
                    uncertain=area==0 or fraction>0.85 or (previous_area is not None and (area>previous_area*4 or area<previous_area/4))
                    state='lost' if area==0 else 'uncertain' if uncertain else 'mask_candidate'
                    stem=str(frame['frame_id'])
                    Image.fromarray(current.astype(np.uint8)*255).save(folder/f'{stem}-mask.png')
                    original=Image.open(self.store.asset(frame['asset'])).convert('RGB')
                    tint=Image.new('RGB',original.size,(20,220,170))
                    alpha=Image.fromarray(current.astype(np.uint8)*90)
                    Image.composite(tint,original,alpha).save(folder/f'{stem}-overlay.jpg',quality=85)
                    row={'evidence_id':frame['id'],'frame':frame,'mask_asset':f'tracks/{tid}/{stem}-mask.png',
                         'overlay_asset':f'tracks/{tid}/{stem}-overlay.jpg','state':state,'mask_area_pixels':area,
                         'mean_absolute_logit':quality,'quality_notice':'Uncalibrated model signal; not an identity or correctness probability',
                         'reinitialized':mask is not None and idx==1}
                    self.store.execute('INSERT OR REPLACE INTO track_frames VALUES(?,?,?)',(tid,frame['id'],json.dumps(row)))
                    track['processed_frames']=self.store.rows('SELECT count(*) FROM track_frames WHERE track=?',(tid,))[0][0]
                    track['latest_evidence_id']=frame['id'];self.store.save_track(track)
                    previous_area=area
                    if state=='lost':
                        track.update(state='lost',reason='Empty predicted mask; new prompt required')
                        return
                last=frames[-1];mask=outputs[-1][1]
                if discontinuity:
                    self.track_event(track,discontinuity);track.update(state='lost',reason='Observation discontinuity; new prompt required');break
                if track['preview_only']: track['state']='preview';break
                track['state']='running'
            if stop.is_set(): track['state']='stopped'
        except Exception as exc:
            track.update(state='error',reason=f'{type(exc).__name__}: {exc}')
        finally:
            if tid in self.stop_reasons:
                track.update(state='stopped' if track['state']!='error' else 'error', session_end_reason=self.stop_reasons[tid])
                if track['state']=='stopped': track['reason']=self.stop_reasons[tid]
                self.track_event(track, {'kind':'session_ended','reason':self.stop_reasons[tid]})
            track['updated_at']=utc_now();self.store.save_track(track)
