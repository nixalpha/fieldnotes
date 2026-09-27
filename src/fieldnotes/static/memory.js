(() => {
  const el=id=>document.getElementById(id);
  const node=(tag,value,cls)=>{const n=document.createElement(tag);n.textContent=value;if(cls)n.className=cls;return n;};
  const imageURL=asset=>'/api/memory/asset/'+asset.split('/').map(encodeURIComponent).join('/');
  let frames=[], selected=null, points=[], labels=[], currentSession='', lastStatus=null, selectedTrack=null;
  let sequence=0;
  async function request(path,body){const r=await fetch('/api/memory'+path,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});const j=await r.json();if(!r.ok)throw Error(typeof j.detail==='string'?j.detail:JSON.stringify(j.detail));return j;}
  function error(e){el('memory-error').textContent=e?.message||'';el('memory-error').hidden=!e;}
  function sourceLink(id){const [session,frame]=id.split(':');const a=node('a','Frame '+frame);a.href='/api/evidence/'+encodeURIComponent(session)+'/'+Number(frame)+'.jpg';a.target='_blank';a.rel='noopener';return a;}
  function originals(ids){const row=node('div','','memory-thumbs');for(const id of ids){const a=sourceLink(id);const img=new Image();img.src=a.href;img.alt='Original '+id;img.loading='lazy';a.prepend(img);row.append(a);}return row;}
  async function refresh(){
    const s=await request('/status');lastStatus=s;
    el('memory-mode').textContent=s.interpretation_source==='mock'?'MOCK INTERPRETATIONS':'FALLIBLE INTERPRETATIONS';
    el('memory-status').textContent=`${s.evidence_count} retained images · ${s.indexed_count} indexed · ${s.index_backlog} pending · search ${s.models.clip_device||'not loaded'} · tracking ${s.models.edge_device||s.models.edgetam.configured_device+' (on demand)'}`;
    if(s.worker_error)error(Error(s.worker_error));
    const select=el('memory-session');const old=select.value;
    select.replaceChildren(...s.sessions.map(x=>{const o=node('option',`${x.session_id.slice(0,8)} · ${x.frame_count} images`);o.value=x.session_id;return o;}));
    if([...select.options].some(o=>o.value===old))select.value=old;
    if(select.value && currentSession!==select.value)await loadSession();
    drawTracks(s.tracks);
  }
  async function loadSession(){
    currentSession=el('memory-session').value;
    if(!currentSession)return;
    frames=(await request('/frames?session_id='+encodeURIComponent(currentSession)+'&limit=1000')).frames;
    el('memory-time').max=Math.max(0,frames.length-1);el('memory-time').value=frames.length-1;
    el('memory-results').replaceChildren();
    if(frames.length)await choose(frames[frames.length-1]);
  }
  function drawPoints(){const c=el('memory-points'),im=el('memory-original');c.width=im.clientWidth;c.height=im.clientHeight;const ctx=c.getContext('2d');if(!selected)return;points.forEach((p,i)=>{ctx.beginPath();ctx.arc(p[0]/selected.width*c.width,p[1]/selected.height*c.height,6,0,Math.PI*2);ctx.fillStyle=labels[i]?'#37e5b3':'#ff806e';ctx.fill();ctx.strokeStyle='#111';ctx.stroke();});}
  async function choose(frame){
    selected=frame;points=[];labels=[];selectedTrack=null;
    el('memory-original').src=imageURL(frame.asset);
    el('memory-source').textContent=`Original frame ${frame.frame_id} · ${frame.received_at} · ${frame.source} · receipt time`;
    el('memory-time-label').textContent=`Frame ${frame.frame_id} · ${frame.received_at}`;
    const idx=frames.findIndex(f=>f.id===frame.id);if(idx>=0)el('memory-time').value=idx;
    drawPoints();const generation=++sequence;
    const [state,changes]=await Promise.all([request('/state?session_id='+encodeURIComponent(currentSession)+'&at='+frame.elapsed_ms),request('/changes?session_id='+encodeURIComponent(currentSession)+'&end='+frame.elapsed_ms)]);
    if(generation!==sequence)return;
    el('memory-state').replaceChildren(...state.states.map(s=>{const a=s.latest_interpretation;const card=node('article','','memory-card');card.append(node('strong',`${a.subject} · ${a.predicate}`),node('p',a.value),node('small',`${a.epistemic} · ${a.interpretation_source} · last observed ${a.last_observed_at}`),node('p',a.uncertainty,'uncertainty'),originals(a.evidence_ids));if(s.support_conflict)card.append(node('p','Conflicting interpretations of the same observation remain unresolved.','uncertainty'));if(s.recent_history.length>1){const details=node('details','');details.append(node('summary',`${s.recent_history.length} recent interpretations`));s.recent_history.forEach(h=>details.append(node('p',`${h.value} · ${h.last_observed_at} · ${h.uncertainty}`)));card.append(details);}return card;}));
    if(!state.states.length)el('memory-state').append(node('p','No interpretations recorded for this time.'));
    el('memory-changes').replaceChildren(...changes.changes.map(c=>{const card=node('article','','memory-card');card.append(node('strong',c.kind.replaceAll('_',' ')),node('p',`${c.before.value} → ${c.after.value}`),node('p',c.explanation),node('small',`Evidence interval ${c.earliest_ms}–${c.latest_ms} ms · exact change time unknown`),originals([...new Set([...c.before.evidence_ids,...c.after.evidence_ids])]));return card;}));
  }
  function drawTracks(tracks){
    const own=tracks.filter(t=>t.session_id===currentSession);
    const container=el('memory-tracks');
    const expanded=el('recovered-tracks')?.open||false;
    container.replaceChildren(node('h3','Selections'));
    const recovered=node('details','');recovered.id='recovered-tracks';recovered.open=expanded;
    const history=own.filter(t=>t.state==='error'&&t.recovery);
    recovered.append(node('summary',`Recovered attempts (${history.length})`),node('p','Earlier failures are retained for diagnosis. Their successful replacements are shown above.'));
    for(const t of own){
      const resolved=t.state==='error'&&t.recovery;
      const card=node('div','','memory-card');
      card.append(node('strong',`${t.label} · ${resolved?'previous failed attempt':t.state}`),node('p',`${t.processed_frames} processed frames${t.model_device?' · '+t.model_device.toUpperCase():''} · tentative continuity`));
      if(t.reason)card.append(node('p',t.reason,'uncertainty'));
      if(resolved){
        card.append(node('p',`Recovered by ${t.recovery.label}: ${t.recovery.processed_frames} frames${t.recovery.model_device?' on '+t.recovery.model_device.toUpperCase():''}.`));
        const replacement=node('button','Inspect successful run');replacement.type='button';replacement.onclick=()=>inspectTrack(t.recovery.track_id).catch(error);card.append(replacement);
      }
      if(t.processed_frames>0){const show=node('button',t.state==='error'?'Inspect diagnostic artifacts':'Inspect masks');show.type='button';show.onclick=()=>inspectTrack(t.id).catch(error);card.append(show);}
      if(['running','queued','waiting'].includes(t.state)){const stop=node('button','Stop');stop.type='button';stop.onclick=async()=>{try{await request('/tracks/'+t.id+'/stop',{});await refresh();}catch(e){error(e);}};card.append(stop);}
      (resolved?recovered:container).append(card);
    }
    if(history.length)container.append(recovered);
    if(selectedTrack){const panel=node('div','','track-inspection');panel.id='track-inspection';container.append(panel);inspectTrack(selectedTrack).catch(error);}
  }
  async function inspectTrack(id){selectedTrack=id;const result=await request('/tracks/'+id+'?limit=100');let panel=el('track-inspection');if(!panel){panel=node('div','','track-inspection');panel.id='track-inspection';el('memory-tracks').append(panel);}panel.replaceChildren(node('p',result.track.state==='error'?'Failed attempt · diagnostic artifacts only. These masks must not be used as valid tracking evidence.':'Derived overlays · inspect against originals; masks can drift.'));
    for(const f of result.frames){const card=node('div','','memory-card');card.append(node('small',`Frame ${f.frame.frame_id} · ${f.state}`));const row=node('div','','memory-thumbs');for(const [asset,label] of [[f.frame.asset,'Original'],[f.overlay_asset,'Overlay'],[f.mask_asset,'Mask']]){const a=node('a',label);a.href=imageURL(asset);a.target='_blank';a.rel='noopener';const im=new Image();im.src=a.href;im.alt=label+' '+f.evidence_id;im.loading='lazy';a.prepend(im);row.append(a);}card.append(row);panel.append(card);}
  }
  el('memory-original').onload=drawPoints;window.addEventListener('resize',drawPoints);
  el('memory-points').onclick=e=>{if(!selected||points.length>=8)return;const r=e.currentTarget.getBoundingClientRect();points.push([(e.clientX-r.left)/r.width*selected.width,(e.clientY-r.top)/r.height*selected.height]);labels.push(Number(el('track-point-kind').value));drawPoints();};
  el('track-clear').onclick=()=>{points=[];labels=[];drawPoints();};
  async function start(preview){try{error(null);if(!selected||!points.length)throw Error('Select a source image and click a region first.');const t=await request('/tracks',{evidence_id:selected.id,points,labels,label:el('track-label').value.trim(),preview_only:preview});selectedTrack=t.id;await refresh();}catch(e){error(e);}}
  el('track-preview').onclick=()=>start(true);el('track-start').onclick=()=>start(false);
  el('memory-session').onchange=()=>loadSession().catch(error);
  el('memory-time').oninput=()=>choose(frames[Number(el('memory-time').value)]).catch(error);
  el('memory-refresh').onclick=async()=>{try{await refresh();await loadSession();}catch(e){error(e);}};
  el('memory-search').onsubmit=async e=>{e.preventDefault();const button=e.currentTarget.querySelector('button');button.disabled=true;button.textContent='Searching…';try{error(null);const result=await request('/search',{query:el('memory-query').value,session_id:currentSession});el('memory-results').replaceChildren(...result.results.map(r=>{const b=node('button',`Frame ${r.evidence.frame_id} · similarity ${r.similarity.toFixed(3)}`);b.type='button';const im=new Image();im.src=imageURL(r.evidence.asset);im.alt='Search candidate '+r.evidence.frame_id;b.prepend(im);b.onclick=()=>choose(r.evidence).catch(error);return b;}));if(!result.results.length)el('memory-results').append(node('p','No indexed candidates in this session.'));}catch(e){error(e);}finally{button.disabled=false;button.textContent='Search images';}};
  refresh().catch(error);setInterval(()=>refresh().catch(error),5000);
})();
