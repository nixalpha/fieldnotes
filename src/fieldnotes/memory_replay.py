"""Isolated sampled-evidence replay. No RTMP, paid calls or fabricated frames."""
from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .core import Frame, Runtime, VisualSummary, utc_now
from .memory_store import MemoryStore
from .perception import Models


class ArchiveRuntime(Runtime):
    def __init__(self, directory: Path):
        self.manifest=json.loads((directory/'manifest.json').read_text())
        super().__init__(directory,'replay')
        self.session_id=self.manifest['session_id']
        self.saved=[entry['frame'] for entry in self.manifest['frames']]
        self.first_frame_ms=self.saved[0]['elapsed_ms'];self.last_frame_ms=self.saved[-1]['elapsed_ms']
        self.started_utc=datetime.fromisoformat(self.saved[0]['received_at'])-timedelta(milliseconds=self.first_frame_ms)
        self.epoch=self.saved[-1]['stream_epoch']
        self.last_dimensions=(self.saved[-1]['width'],self.saved[-1]['height'])

    def elapsed_ms(self): return self.last_frame_ms+1

    def status(self):
        return {'jobs':self.jobs.context() if self.jobs else None,'active_session_id':None,'session_state':'ended','automatic_start':False,
                'session_id':self.session_id,'source':'replay','state':'archive','elapsed_ms':self.elapsed_ms(),
                'first_frame_ms':self.first_frame_ms,'stream_epoch':self.epoch,'latest_frame_age_ms':None,
                'dimensions':self.last_dimensions,'input_kind':'sampled_evidence','decoder_error':None}

    def observe(self,start,end,session_id=None):
        if session_id is not None and session_id != self.session_id:
            raise ValueError('Session is not in this archive')
        if start<0 or end<=start or end-start>60000 or end>self.elapsed_ms():
            raise ValueError('Window must be positive, at most 60 seconds and within saved history')
        oid=f'{self.session_id}-{start}-{end}'
        old=self.journal.observation(oid)
        if old: return {**old,'job_context':old.get('job_context')}
        available=[f for f in self.saved if start<=f['elapsed_ms']<end]
        selected={}
        if available:
            for i in range(5):
                target=start+(end-start-1)*i/4
                frame=min(available,key=lambda f:abs(f['elapsed_ms']-target))
                selected[frame['frame_id']]=frame
        metas=sorted(selected.values(),key=lambda f:f['elapsed_ms'])
        frames=[Frame(**meta,jpeg=(self.journal.directory/'evidence'/self.session_id/f"{meta['frame_id']}.jpg").read_bytes()) for meta in metas]
        observation={'job_context':None,'observation_id':oid,'session_id':self.session_id,'source':'replay','input_kind':'sampled_evidence',
            'source_session_id':self.manifest['source_session_id'],'start_elapsed_ms':start,'end_elapsed_ms':end,
            'start_at':(self.started_utc+timedelta(milliseconds=start)).isoformat(),
            'end_at':(self.started_utc+timedelta(milliseconds=end)).isoformat(),
            'coverage':'sampled' if frames else 'empty','gaps':[],
            'sampling_notice':'Irregular retained samples; missing IDs are not proof of a network outage.',
            'partial_final_window':end==self.elapsed_ms() and end-start<5000,'frames':metas}
        self.journal.save_observation(observation,frames)
        return observation


def import_inputs(evidence:Path,journal:Path):
    original={}
    with sqlite3.connect(journal.resolve().as_uri()+'?mode=ro',uri=True) as db:
        for (body,) in db.execute('SELECT body FROM observations WHERE session_id=?',(evidence.name,)):
            for frame in json.loads(body)['frames']:
                old=original.get(frame['frame_id'])
                if old is not None and old!=frame: raise ValueError('Conflicting original frame metadata')
                original[frame['frame_id']]=frame
    from PIL import Image
    entries=[]
    for path in evidence.glob('*.jpg'):
        if not path.stem.isdigit() or int(path.stem) not in original:
            raise ValueError(f'Image lacks recorded metadata: {path.name}')
        frame=original[int(path.stem)]
        with Image.open(path) as im:
            if im.size!=(frame['width'],frame['height']): raise ValueError('Image dimensions differ from metadata')
        entries.append({'filename':path.name,'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'original_frame':frame})
    if not entries: raise ValueError('No source JPEGs')
    return sorted(entries,key=lambda e:(e['original_frame']['elapsed_ms'],e['original_frame']['frame_id']))


def fixture_for(observation):
    ids=[f['frame_id'] for f in observation['frames']]
    assertions=[]
    text='MOCK: These retained samples are available for inspection; no additional action is asserted.'
    uncertainty='Authored fixture, not generated visual understanding. Unsaved intervals are unknown.'
    if 177 in ids:
        text='MOCK: A seated person has an open laptop on their lap and holds a handheld device.'
        assertions=[{'subject':'selected_laptop','predicate':'visibility','value':'visible',
                     'frame_ids':[177],'epistemic':'visually_supported','uncertainty':'Authored interpretation; no identity across sessions.'}]
    elif 451 in ids:
        text='MOCK: The low view is dominated by the floor and chair; the laptop state cannot be established.'
        assertions=[{'subject':'selected_laptop','predicate':'visibility','value':'unknown_from_this_view',
                     'frame_ids':[451],'epistemic':'unknown','uncertainty':'Outside useful coverage; not proof of removal.'}]
    elif 222 in ids:
        text='MOCK: Frame 222 has visible corruption; fine details and continuity are uncertain.'
        assertions=[{'subject':'scene','predicate':'image_quality','value':'compression_corruption',
                     'frame_ids':[222],'epistemic':'visually_supported','uncertainty':'No claim about tracking correctness.'}]
    else:
        assertions=[{'subject':'scene','predicate':'unreviewed_interval','value':'no_additional_claim',
                     'frame_ids':[ids[0]],'epistemic':'unknown','uncertainty':uncertainty}]
    return {'fixture_id':'saved-evidence-v1:'+observation['observation_id'],
            'expected_frame_ids':ids,'visual':{'summary':text,'observed_actions':[],
                'uncertainties':[uncertainty],'change_state':'uncertain'},'assertions':assertions}


class FixtureModel:
    name='fixture-model'
    def __init__(self,path:Path): self.fixtures=json.loads(path.read_text())['observations']
    def summarize(self,observation):
        fixture=self.fixtures.get(observation['observation_id'])
        if not fixture or fixture['expected_frame_ids']!=[f['frame_id'] for f in observation['frames']]:
            raise ValueError('Missing fixture or evidence selection differs from fixture')
        return VisualSummary.model_validate(fixture['visual']),fixture


def replay(evidence:Path,journal:Path,output_root:Path,model_root:Path,fixture_file:Path|None=None):
    entries=import_inputs(evidence,journal)
    run_id=datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:8]
    directory=output_root.resolve()/run_id;directory.mkdir(parents=True,exist_ok=False)
    sid=uuid.uuid4().hex
    dest=directory/'evidence'/sid;dest.mkdir(parents=True)
    for entry in entries:
        shutil.copyfile(evidence/entry['filename'],dest/entry['filename'])
        entry['frame']={**entry['original_frame'],'session_id':sid,'source':'replay'}
    manifest={'schema_version':1,'session_id':sid,'source_session_id':evidence.name,'run_id':run_id,
              'source':'replay','input_kind':'sampled_evidence','created_at':utc_now(),
              'source_directory':str(evidence.resolve()),'source_journal':str(journal.resolve()),
              'model_root':str(model_root.resolve()),'frames':entries,'windows':[],
              'interpretation_source':'mock','status':'imported','component_errors':{}}
    (directory/'manifest.json').write_text(json.dumps(manifest,indent=2))
    runtime=ArchiveRuntime(directory);store=MemoryStore(directory,'mock')
    try:
        for entry in entries: store.add_evidence(entry['frame'],source_session=evidence.name)
        fixtures={'schema_version':1,'interpretation_source':'mock','observations':{}}
        for start in range(runtime.first_frame_ms,runtime.elapsed_ms(),5000):
            observation=runtime.observe(start,min(start+5000,runtime.elapsed_ms()))
            manifest['windows'].append({'start':observation['start_elapsed_ms'],'end':observation['end_elapsed_ms'],'observation_id':observation['observation_id']})
            fixtures['observations'][observation['observation_id']]=fixture_for(observation)
        (directory/'fixtures').mkdir()
        if fixture_file:
            fixtures=json.loads(fixture_file.read_text())
        (directory/'fixtures/interpretations.json').write_text(json.dumps(fixtures,indent=2))
        models=Models(model_root)
        for entry in entries:
            try: models.index(store,store.evidence(f"{sid}:{entry['frame']['frame_id']}"))
            except Exception as exc:
                manifest['component_errors']['visual_index']=f'{type(exc).__name__}: {exc}'
                break
        manifest['indexed_count']=store.rows('SELECT count(*) FROM embeddings')[0][0]
        manifest['status']='ready' if not manifest['component_errors'] else 'partial'
    finally:
        runtime.journal.close();store.close()
        (directory/'manifest.json').write_text(json.dumps(manifest,indent=2))
    (directory/'review.md').write_text('# Memory replay review\n\nImported sampled evidence; interpretation fixtures are authored mocks.\n\n'
        'Run exercise-memory-mcp against view-memory to populate the journal and memory through MCP.\n\n'
        'No performance or accuracy measurements have been collected.\n\n'
        'Manual review anchors: 177, 222 (corruption), 303, 378, 451.\n')
    return directory
