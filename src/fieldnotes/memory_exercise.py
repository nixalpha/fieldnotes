"""Scoped, explicit MCP exercise. Does not call a generative model or benchmark."""
from __future__ import annotations
import asyncio
import base64
import hashlib
import json
from pathlib import Path

from .agent import connect_mcp
from .memory_replay import FixtureModel
from .core import utc_now


async def exercise(directory:Path,url:str,start_tracks:bool=True):
    manifest=json.loads((directory/'manifest.json').read_text())
    sid=manifest['session_id'];model=FixtureModel(directory/'fixtures/interpretations.json')
    results=[]; transcripts=directory/'mcp-transcript.jsonl'
    frames={str(e['frame']['frame_id']):e for e in manifest['frames']}
    def record(row):
        with transcripts.open('a') as file: file.write(json.dumps(row)+'\n')
    async with connect_mcp(url) as client:
        discovery=await client.list_tools()
        names={t.name for t in discovery.tools}
        required={'get_stream_status','get_observation_window','get_recent_summaries','record_summary',
                  'get_memory_status','search_visual_history','get_memory_state','get_change_history',
                  'get_evidence','record_memory','start_visual_track','get_visual_track','stop_visual_track'}
        results.append({'scenario':'discover_tools','passed':required<=names,'tools':sorted(names)})
        record({'event':'list_tools','names':sorted(names),'at':utc_now()})
        async def call(name,args=None,expected_error=False):
            result=await client.call_tool(name,args or {})
            texts=[c.text for c in result.content if c.type=='text']
            try: meta=json.loads(texts[0]) if texts else {}
            except json.JSONDecodeError: meta={'message':' '.join(texts)}
            images=[c for c in result.content if c.type=='image']
            hashes=[hashlib.sha256(base64.b64decode(c.data)).hexdigest() for c in images]
            record({'tool':name,'arguments':args or {},'is_error':bool(result.isError),'metadata':meta,
                    'images':[{'sha256':h,'mime_type':i.mimeType} for h,i in zip(hashes,images)],'at':utc_now()})
            if expected_error:
                results.append({'scenario':name+':reject_invalid_request','passed':bool(result.isError),'response':meta})
            elif result.isError:
                raise RuntimeError(f'{name}: {texts}')
            return meta,hashes
        status,_=await call('get_memory_status')
        if status['interpretation_source']!='mock' or not status['archive'] or not any(s['session_id']==sid for s in status['sessions']):
            raise ValueError('Exercise requires the matching isolated mock archive; refusing to mutate another server')
        search_results=[]
        for query in ['An open laptop.','A person holding a handheld device.','A close view of the floor and chair.','An overview of the seating area.']:
            try:
                found,hashes=await call('search_visual_history',{'query':query,'session_id':sid,'limit':4})
                matched=all(f['sha256']==h for f,h in zip(found.get('frames',[]),hashes)) and len(hashes)==len(found.get('frames',[]))
                results.append({'scenario':'search:'+query,'passed':bool(hashes) and matched,
                    'top_frames':[r['evidence']['frame_id'] for r in found['results']],
                    'notice':'Image delivery/hash check; ranking relevance requires manual review.'})
                search_results.append(found)
            except Exception as exc:
                results.append({'scenario':'search:'+query,'passed':False,'error':str(exc)})
        first_proposal=None
        for window in manifest['windows']:
            observation,_=await call('get_observation_window',{'start_elapsed_ms':window['start'],'end_elapsed_ms':window['end']})
            visual,fixture=model.summarize(observation)
            assertions=[]
            for draft in fixture['assertions']:
                value=dict(draft);ids=value.pop('frame_ids');value['evidence_ids']=[f'{sid}:{i}' for i in ids];assertions.append(value)
            proposal={'session_id':sid,'idempotency_key':fixture['fixture_id'],
                      'fixture_id':fixture['fixture_id'],'evidence_bundle_ids':[observation['evidence_bundle_id']],
                      'assertions':assertions}
            entry,_=await call('record_summary',{'observation_id':observation['observation_id'],
                'summary':visual.model_dump(),'generation':{'model':'fixture-model','duration_ms':0,'input_tokens':0,'output_tokens':0}})
            saved,_=await call('record_memory',{'proposal':proposal})
            results.append({'scenario':'mock_window:'+str(window['start']),'passed':saved['interpretation_source']=='mock',
                            'observation_id':entry['observation_id'],'duplicate':saved['duplicate']})
            if first_proposal is None: first_proposal=proposal
        duplicate,_=await call('record_memory',{'proposal':first_proposal})
        results.append({'scenario':'idempotent_write','passed':duplicate['duplicate']})
        bad=json.loads(json.dumps(first_proposal));bad['idempotency_key']='invalid-reference';bad['assertions'][0]['evidence_ids']=[f'{sid}:999999']
        await call('record_memory',{'proposal':bad},True)
        bad=json.loads(json.dumps(first_proposal));bad['idempotency_key']='invalid-schema';bad['assertions'][0]['certainty']='absolute'
        await call('record_memory',{'proposal':bad},True)
        bad=json.loads(json.dumps(first_proposal));bad['idempotency_key']='invalid-provenance';bad['interpretation_source']='human_verified'
        await call('record_memory',{'proposal':bad},True)
        bad=json.loads(json.dumps(first_proposal));bad['idempotency_key']='invalid-session';bad['session_id']='f'*32
        await call('record_memory',{'proposal':bad},True)
        # Deliberately conflicting authored interpretation; demonstrates preservation, not semantic truth.
        conflict=json.loads(json.dumps(first_proposal));conflict['idempotency_key']='deliberate-conflict-v1'
        conflict['fixture_id']='negative-fixture:deliberate-conflict'
        conflict['assertions'][0].update(value='not_visible',uncertainty='Deliberately false mock fixture to demonstrate contradiction retention.')
        await call('record_memory',{'proposal':conflict})
        history,_=await call('get_change_history',{'session_id':sid})
        results.append({'scenario':'preserve_contradiction','passed':any(c['kind']=='contradiction' for c in history['changes'])})
        state,_=await call('get_memory_state',{'session_id':sid,'at_elapsed_ms':manifest['frames'][-1]['frame']['elapsed_ms']})
        laptop=next((s for s in state['states'] if s['latest_interpretation']['subject']=='selected_laptop'),None)
        results.append({'scenario':'later_state_unknown','passed':bool(laptop and laptop['latest_interpretation']['epistemic']=='unknown')})
        evidence,hashes=await call('get_evidence',{'evidence_ids':[f'{sid}:{i}' for i in [177,222,451]]})
        results.append({'scenario':'original_evidence_hashes','passed':hashes==[frames[str(i)]['sha256'] for i in [177,222,451]]})
        record({'event':'mock_agent_answer','question':'Was the laptop removed?', 'interpretation_source':'mock',
            'answer':'The laptop was visible earlier. The later low-angle image does not show enough of that area to establish whether it was removed.',
            'evidence_ids':[f'{sid}:177',f'{sid}:451'],'notice':'Scripted fixture, not independent LLM reasoning.'})
        status,_=await call('get_memory_status')
        track_ids=[]
        # Points chosen by inspecting the 720x540 original frame 177; no geometric calibration implied.
        seeds=[{'label':'chair','points':[[291,363]],'labels':[1]},
               {'label':'laptop','points':[[356,317]],'labels':[1]}]
        (directory/'fixtures/track-seeds.json').write_text(json.dumps({'frame_id':177,'image_size':[720,540],
            'method':'Point selection after visual inspection of original frame 177','selections':seeds},indent=2))
        for seed in seeds:
            old=next((t for t in status['tracks'] if t['label']==seed['label'] and t['session_id']==sid and not t['preview_only']),None)
            if old and old['state'] != 'error': track_ids.append(old['id'])
            elif start_tracks:
                try:
                    track,_=await call('start_visual_track',{'evidence_id':f'{sid}:177',**seed})
                    track_ids.append(track['id'])
                except Exception as exc: results.append({'scenario':'start_track:'+seed['label'],'passed':False,'error':str(exc)})
        # Bounded functional wait, not a timing measurement. Outputs contain no throughput metrics.
        active=set(track_ids)
        for _ in range(180):
            if not active: break
            for tid in list(active):
                output,_=await call('get_visual_track',{'track_id':tid,'limit':1})
                track=output['track']
                if track['state'] not in ('queued','running','waiting','stopping'):
                    active.remove(tid)
                    results.append({'scenario':'track:'+track['label'],'passed':track['state'] in ('completed','lost'),
                        'state':track['state'],'processed_frames':track['processed_frames'],'reason':track.get('reason'),
                        'notice':'Model executed; mask correctness requires manual review.'})
            if active: await asyncio.sleep(5)
        for tid in active: results.append({'scenario':'track_pending','passed':False,'track_id':tid,'reason':'Still processing; inspect later.'})
        for tid in track_ids:
            for after in [0,8,24,48]:
                await call('get_visual_track',{'track_id':tid,'limit':2,'after':after})
        status,_=await call('get_memory_status')
    report={'run_id':manifest['run_id'],'session_id':sid,'source':'sampled_evidence','interpretation_source':'mock',
            'model_calls_paid':0,'results':results,'final_status':status,'search_results':search_results,
            'notice':'Scoped MCP functional exercise. No performance, real-time, tracking accuracy or real-LLM accuracy claim.'}
    (directory/'exercise-report.json').write_text(json.dumps(report,indent=2))
    lines=['# Offline memory MCP exercise','',report['notice'],'','## Scenarios','',
           '| Scenario | Outcome |','| --- | --- |']
    for result in results: lines.append('| '+result['scenario'].replace('|','/')+' | '+('PASS' if result['passed'] else 'FAIL / INCOMPLETE')+' |')
    lines += ['','## Manual review pending','','Inspect search relevance and mask overlays for frames 177, 222, 303, 378, and 451.',
              'Mock interpretations are authored. A deliberately false conflicting fixture is retained to exercise contradiction handling.',
              'Run the exercise again with --no-start-tracks after restarting the archive server to inspect persistence.',
              'Original image hashes were compared to the imported source manifest. No new frames were fabricated.']
    (directory/'review.md').write_text('\n'.join(lines)+'\n')
    print(json.dumps({'report':str(directory/'exercise-report.json'),'scenarios':len(results),
                      'failed_or_incomplete':[r['scenario'] for r in results if not r['passed']]}))
