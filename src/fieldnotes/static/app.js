import {demoSessions, demoReview, demoState, demoActivity} from './demo.js';

const $ = id => document.getElementById(id);
const node = (tag, value = '', cls = '') => { const n = document.createElement(tag); if (value) n.textContent = value; if (cls) n.className = cls; return n; };
const icon = name => { const box = document.createElement('span'); box.innerHTML = window.feather?.icons[name]?.toSvg({'aria-hidden':'true'}) || ''; return box.firstElementChild || box; };
const append = (parent, ...children) => { parent.append(...children.filter(c => c != null)); return parent; };
const button = (label, action, cls = '', name = '') => { const b = node('button', '', cls); b.type = 'button'; if (name) b.append(icon(name)); if (label) b.append(node('span',label)); b.onclick = action; return b; };
const link = (label, href, cls = '', name = '') => { const a = node('a','',cls); a.href = href; if (name) a.append(icon(name)); if (label) a.append(node('span',label)); return a; };
const img = (url, alt, lazy = true) => { const i = new Image(); i.src = url; i.alt = alt; if (lazy) i.loading = 'lazy'; i.onerror = () => { const fallback = node('span','Image unavailable','muted tiny'); i.replaceWith(fallback); }; return i; };
const time = value => value ? new Date(value).toLocaleTimeString([], {hour:'numeric',minute:'2-digit'}) : 'Time unknown';
const precise = value => value ? new Date(value).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false}) : '—';
const date = value => value ? new Date(value).toLocaleDateString([], {month:'short',day:'numeric',year:'numeric'}) : 'Date unknown';
const imageURL = frame => frame?.url || (frame?.asset ? `/api/memory/asset/${frame.asset.split('/').map(encodeURIComponent).join('/')}` : frame ? `/api/evidence/${encodeURIComponent(frame.session_id)}/${frame.frame_id}.jpg` : '');
const topics = ['Workspace','Equipment inspection','Setup','Safety','Inventory','Access'];
let demo = new URLSearchParams(location.search).get('demo') === '1';
let route = '', routeVersion = 0, stream = null, observer = null, publish = '', events = null, poll = null;
let review = null, frames = [], selected = null, frameCursor = null, frameLoading = false, frameTask = null, reviewTab = 'overview';
let playing = null, selectionVersion = 0, libraryVersion = 0, accountBusy = false, liveBusy = false, liveSession = null;
let libraryCursor = null, libraryCards = [], filterTimer, toastTimer, liveRevision = '';
const filters = {q:'',range:'all',topic:'',from:'',to:''};

async function api(path, body) {
  const response = await fetch(path, body === undefined ? {} : {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const raw = await response.text();
  let payload = null;
  try { payload = raw ? JSON.parse(raw) : null; } catch { /* Some server errors are plain text or HTML. */ }
  const rename = /\/sessions\/[^/]+\/rename$/.test(path);
  if (!response.ok) {
    if (rename && [404,405].includes(response.status) && payload?.detail !== 'Unknown session_id') {
      throw Error('This server has not loaded observation renaming yet. Restart the FieldNotes server, then save again. Your entered name is still here.');
    }
    if (typeof payload?.detail === 'string') throw Error(payload.detail);
    if (Array.isArray(payload?.detail)) throw Error(payload.detail.map(item => item.msg).filter(Boolean).join(' · ') || 'Please check the entered values.');
    throw Error(rename
      ? `The server could not save the name (HTTP ${response.status}). Your entered name is still here. Please retry; if it continues, restart FieldNotes.`
      : `The server could not complete the request (HTTP ${response.status}). Please retry.`);
  }
  if (!payload || typeof payload !== 'object') {
    throw Error(rename
      ? 'The server returned an unexpected save response. Your entered name is still here. Refresh the session to see whether it was saved before trying again.'
      : 'The server returned an unexpected response. Restart FieldNotes and refresh the page.');
  }
  return payload;
}

function toast(message) { $('toast').textContent = message; $('toast').hidden = false; clearTimeout(toastTimer); toastTimer = setTimeout(() => { $('toast').hidden = true; }, 5000); }
function errorBox(message) { return node('div',message,'error-box'); }
function empty(title, description, compact = false) { return append(node('div','','empty'+(compact?' compact':'')), node('h2',title), node('p',description)); }
function footer() { const f = node('footer','','page-foot'); f.append(node('span',demo ? 'FieldNotes / Demonstration' : 'FieldNotes / Camera observations, not inferred intent.')); f.append(demo ? node('span','Generated images · authored observations') : link('Export journal','/api/export')); return f; }
function refHash(id, frame) { return `#/sessions/${encodeURIComponent(id)}${frame != null ? '?frame='+frame : ''}`; }
function setDemo(value) {
  demo = value; const url = new URL(location.href); if (value) url.searchParams.set('demo','1'); else url.searchParams.delete('demo');
  url.hash = '#/sessions'; history.replaceState(null,'',url); filters.q='';filters.topic='';filters.range=value?'2':'all';
  setupConnection(); renderRoute();
}
$('demo-switch').onclick = () => setDemo(!demo); $('exit-demo').onclick = () => setDemo(false);
if (demo) filters.range = '2';
function shell() {
  $('demo-banner').hidden = !demo; $('demo-switch').textContent = demo ? 'Exit demo' : 'Explore demo';
  document.querySelectorAll('[data-nav]').forEach(a => { if ((route === 'live' ? 'live' : 'sessions') === a.dataset.nav) a.setAttribute('aria-current','page'); else a.removeAttribute('aria-current'); });
  connection();
}
function connection(disconnected = false) {
  const status = $('connection'), s = stream?.state;
  status.className = 'connection '+(demo ? 'live' : disconnected ? 'stale' : s === 'live' ? 'live' : ['stale','reconnecting'].includes(s) ? 'stale' : '');
  status.lastElementChild.textContent = demo ? 'Demo' : disconnected ? 'App disconnected' : s === 'live' ? 'Connected' : s === 'archive' ? 'Archive' : s === 'stale' || s === 'reconnecting' ? 'Reconnecting' : s ? 'Waiting for video' : 'Connecting';
}
function setupConnection() {
  events?.close(); events = null; clearInterval(poll); stream = null; observer = null; liveRevision = ''; liveSession = null;
  if (demo) { connection(); return; }
  const mode = demo;
  api('/api/status').then(s => { if (demo === mode) updateStatus(s); }).catch(e => { if (!demo) {connection(true);toast(e.message);} });
  events = new EventSource('/api/events');
  events.addEventListener('status', e => { if (!demo) updateStatus(JSON.parse(e.data)); });
  events.addEventListener('journal', () => { if (demo) return; if (route === 'live') loadActivity(); if (route === 'review') refreshReview(); if (route === 'sessions') loadLibrary(false,true); });
  events.onerror = () => { if (!demo) {connection(true); if ($('live-freshness')) $('live-freshness').textContent='App disconnected'; if ($('live-stage')) $('live-stage').classList.add('stale');} };
  events.onopen = () => { if (!demo && route === 'live') loadActivity(); };
  poll = setInterval(() => { if (demo) return; if (route === 'review') refreshReview(); if (route === 'sessions') loadLibrary(false,true); }, 5000);
}
function updateStatus(payload) {
  stream = payload.stream; observer = payload.agent; publish = payload.publish_url;
  connection();
  if (route === 'live') {
    updateLiveStatus(); const sid = stream.active_session_id;
    if (sid !== liveSession) { liveSession = sid; loadActivity(); }
  }
}
async function renderRoute() {
  routeVersion++; const version = routeVersion; stopPlayback(); selectionVersion++;
  review = null; frames = []; selected = null; frameCursor = null; frameTask=null; frameLoading=false;
  const hash = location.hash.slice(1) || '/sessions';
  const [path, query] = hash.split('?'); const parts = path.split('/').filter(Boolean);
  route = parts[0] === 'live' ? 'live' : parts[0] === 'sessions' && parts[1] ? 'review' : 'sessions';
  shell(); $('content').replaceChildren();
  if (route === 'sessions') renderLibrary();
  else if (route === 'live') renderLive();
  else {
    $('content').append(node('div','Loading saved evidence…','loading'));
    try {
      const id = decodeURIComponent(parts[1]);
      const result = demo ? demoReview(id) : await api(`/api/session-review/${encodeURIComponent(id)}`);
      if (version !== routeVersion) return;
      review = result; reviewTab = 'overview';
      if (demo) frames = result.frames;
      else { const page = await api(`/api/session-frames/${encodeURIComponent(id)}?limit=200`); if (version !== routeVersion) return; frames=page.frames;frameCursor=page.next_cursor; }
      renderReview();
      const target = Number(new URLSearchParams(query || '').get('frame')) || (demo ? 118 : null);
      if (frames.length) chooseFrame(frames[0]);
      if (target) await chooseCitation(target);
    } catch (e) { if (version === routeVersion) $('content').replaceChildren(link('All sessions','#/sessions','back-link','arrow-left'),empty('Session unavailable',e.message)); }
  }
}
window.addEventListener('hashchange',renderRoute);

function rangeBounds() {
  if (filters.range === 'all') return {};
  let from, to;
  if (filters.range === 'custom') { from = filters.from ? new Date(filters.from+'T00:00:00') : null; to = filters.to ? new Date(filters.to+'T00:00:00') : null; if (to) to.setDate(to.getDate()+1); }
  else { from = new Date(); from.setHours(0,0,0,0); from.setDate(from.getDate()-(Number(filters.range)-1)); }
  return {since:from?.toISOString(),until:to?.toISOString()};
}
function renderLibrary() {
  document.title='Sessions · FieldNotes';
  const head = node('section','','library-head'); head.append(node('h1','Work, remembered.'));
  const row = node('div','','filters'), search = node('label','','searchbox'); search.append(icon('search'));
  const input=node('input'); input.type='search';input.placeholder='Search sessions, spaces, outcomes';input.value=filters.q; input.setAttribute('aria-label','Search sessions, spaces, outcomes');
  input.oninput=()=>{filters.q=input.value;clearTimeout(filterTimer);filterTimer=setTimeout(()=>loadLibrary(),180);};search.append(input);row.append(search);
  const dateLabel=node('label','','date-filter');dateLabel.append(icon('calendar'));const select=node('select');select.setAttribute('aria-label','Date range');
  [['all','All time'],['1','Today'],['2','Last 2 days'],['7','Last 7 days'],['30','Last 30 days'],['custom','Custom range']].forEach(([value,label])=>{const option=node('option',label);option.value=value;select.append(option);});select.value=filters.range;
  select.onchange=()=>{filters.range=select.value;$('custom-dates').hidden=select.value!=='custom';loadLibrary();};dateLabel.append(select);row.append(dateLabel);
  const chips=node('div','','topic-filters');chips.setAttribute('aria-label','Filter by topic');
  topics.forEach(topic=>{const b=button(topic,()=>{filters.topic=filters.topic===topic?'':topic;chips.querySelectorAll('button').forEach(c=>c.setAttribute('aria-pressed',String(c.textContent===filters.topic)));loadLibrary();},'chip');b.setAttribute('aria-pressed',String(topic===filters.topic));chips.append(b);});row.append(chips);head.append(row);
  const dates=node('div','','custom-dates');dates.id='custom-dates';dates.hidden=filters.range!=='custom';
  for(const [key,label] of [['from','From'],['to','Through']]){const l=node('label',label+' '),field=node('input');field.type='date';field.value=filters[key];field.onchange=()=>{filters[key]=field.value;loadLibrary();};l.append(field);dates.append(l);}head.append(dates);
  const results=node('div');results.id='library-results';results.setAttribute('aria-live','polite');
  $('content').append(head,results,footer());loadLibrary();
}
async function loadLibrary(more=false,quiet=false) {
  if(route!=='sessions')return; const generation=++libraryVersion, version=routeVersion;const root=$('library-results');
  if(!quiet&&!more)root.replaceChildren(node('div','Loading sessions…','loading'));
  try{
    const bounds=rangeBounds(); let result;
    if(demo){const sessions=demoSessions.filter(s=>(!filters.topic||s.topics.includes(filters.topic))&&[s.title,s.outcome,...s.topics].join(' ').toLowerCase().includes(filters.q.toLowerCase())&&(!bounds.since||Date.parse(s.started_at)>=Date.parse(bounds.since))&&(!bounds.until||Date.parse(s.started_at)<Date.parse(bounds.until)));result={sessions,next_cursor:null};}
    else {const params=new URLSearchParams({q:filters.q,topic:filters.topic,limit:String(quiet?Math.max(30,Math.min(100,libraryCards.length)):30)});if(more&&libraryCursor!=null)params.set('cursor',libraryCursor);for(const[k,v]of Object.entries(bounds))if(v)params.set(k,v);result=await api('/api/library?'+params);}
    if(version!==routeVersion||generation!==libraryVersion)return;
    libraryCards=more?[...libraryCards,...result.sessions]:result.sessions;libraryCursor=result.next_cursor;
    if(quiet&&root.dataset.signature===JSON.stringify(libraryCards))return;
    root.dataset.signature=JSON.stringify(libraryCards);root.replaceChildren();
    if(!libraryCards.length){const box=empty(filters.q||filters.topic||filters.range!=='all'?'No matching sessions':'Your observations start here.',filters.q||filters.topic?'Try another search or topic.':'Connect a camera from Live, or explore the demonstration archive.');if(filters.q||filters.topic||filters.range!=='all')box.append(button('Clear filters',()=>{filters.q='';filters.topic='';filters.range='all';renderLibraryReset();}));else box.append(link('Open Live','#/live','primary'));root.append(box);return;}
    const groups=new Map();const today=new Date();today.setHours(0,0,0,0);const yesterday=new Date(today);yesterday.setDate(yesterday.getDate()-1);
    for(const session of libraryCards){const d=new Date(session.started_at||session.created_at);d.setHours(0,0,0,0);const label=+d===+today?'Today':+d===+yesterday?'Yesterday':date(session.started_at||session.created_at);if(!groups.has(label))groups.set(label,[]);groups.get(label).push(session);}
    for(const[label,sessions]of groups){const group=node('section','','session-group');group.append(node('h2',label,'group-title'));const grid=node('div','','session-grid');sessions.forEach(s=>grid.append(sessionCard(s)));group.append(grid);root.append(group);}
    if(libraryCursor!=null)root.append(button('More sessions',()=>loadLibrary(true),'load-more'));
  }catch(e){if(version===routeVersion&&generation===libraryVersion){if(!quiet)root.replaceChildren(empty('Could not load sessions',e.message),button('Try again',()=>loadLibrary(),'load-more'));else toast(e.message);}}
}
function renderLibraryReset(){ $('content').replaceChildren();renderLibrary(); }
function sessionCard(session){
  const card=link('',refHash(session.session_id),'session-card'),picture=node('div','','card-picture');
  if(session.thumbnail)picture.append(img(imageURL(session.thumbnail),`Retained view from ${session.title}`));else picture.append(node('span','No retained image','muted tiny'));
  if(session.state==='active'){const badge=node('span','','recording');badge.append(node('i','','status-dot'),node('span',demo?'Demo recording':'Recording'));picture.append(badge);}
  const body=node('div','','card-body'),heading=node('div','','card-title');heading.append(node('h3',session.title),icon('arrow-up-right'));body.append(heading);
  const meta=node('div','','card-meta');meta.append(node('span',`${time(session.started_at||session.created_at)} – ${session.state==='active'?'present':time(session.last_received_at||session.observed_until_at)}`));const tags=node('span','','tags');session.topics.forEach(t=>tags.append(node('span',t,'tag')));if(session.mock)tags.append(node('span','Mock interpretations','tag amber'));if(session.source==='replay')tags.append(node('span','Replay','tag')); meta.append(tags);body.append(meta,node('p',session.outcome,'card-outcome'));card.append(picture,body);return card;
}

function observationTitle(session){
  const row=node('div','','observation-title');row.id='observation-title';
  const title=node('h1',session.title),rename=button('Rename',()=>editObservationName(session.session_id),'quiet rename-button','edit-2');
  rename.setAttribute('aria-label','Rename observation');row.append(title,rename);return row;
}
function editObservationName(id){
  const row=$('observation-title');if(!row||!review||row.querySelector('form'))return;
  const version=routeVersion,form=node('form','','rename-form');
  const label=node('label','Observation name');label.htmlFor='observation-name';
  const input=node('input');input.id='observation-name';input.type='text';input.required=true;input.maxLength=100;input.value=review.session.title;input.autocomplete='off';
  const actions=node('div','','rename-actions'),save=button('Save',null,'primary'),cancel=button('Cancel',restore,'quiet');save.type='submit';actions.append(save,cancel);
  const error=node('p','','rename-error');error.setAttribute('role','alert');error.hidden=true;
  let saving=false;
  function restore(){if(version!==routeVersion)return;row.replaceWith(observationTitle(review.session));$('observation-title').querySelector('button').focus();}
  form.append(label,input,actions,error);row.replaceChildren(form);input.focus();input.select();
  form.onkeydown=e=>{if(e.key==='Escape'&&!saving){e.preventDefault();restore();}};
  form.onsubmit=async e=>{
    e.preventDefault();if(saving)return;const name=input.value.trim();
    if(!name){error.textContent='Enter an observation name.';error.hidden=false;input.focus();return;}
    saving=true;save.disabled=true;cancel.disabled=true;input.disabled=true;save.textContent='Saving…';error.hidden=true;
    try{
      let updated;
      if(demo){const fixture=demoSessions.find(s=>s.session_id===id);Object.assign(fixture,{name,title:name,display_name:name});updated={...review.session,name,title:name,display_name:name};}
      else updated=await api(`/api/sessions/${encodeURIComponent(id)}/rename`,{name});
      if(version!==routeVersion)return;
      review.session={...review.session,...updated};document.title=`${updated.title} · FieldNotes`;restore();toast('Observation renamed.');
    }catch(e){if(version===routeVersion){error.textContent=e.message;error.hidden=false;saving=false;save.disabled=false;cancel.disabled=false;input.disabled=false;save.textContent='Save';input.focus();}}
  };
}

function renderReview(){
  const s=review.session;document.title=`${s.title} · FieldNotes`;
  const layout=node('div','','review-layout'),main=node('section','','review-main'),heading=node('div','','review-heading');
  heading.append(link('All sessions','#/sessions','back-link','arrow-left'),observationTitle(s),node('p',`${date(s.started_at||s.created_at)} · ${time(s.started_at)} – ${s.state==='active'?'present':time(s.last_received_at||s.observed_until_at)}${s.location?' · '+s.location:s.mock?' · Mock interpretations':s.source==='replay'?' · Replay':''}`,'session-subtitle'));
  const stage=node('div','','image-stage');stage.id='image-stage';if(!frames.length)stage.append(empty('No retained images','This session has no saved camera evidence.',true));else{const original=img(imageURL(frames[0]),'Original retained camera frame',false);original.id='original-frame';stage.append(original);const provenance=node('span','','frame-provenance');provenance.id='frame-provenance';stage.append(provenance);}
  main.append(heading,stage,buildTimeline(),node('p',demo?'Generated demonstration imagery · playback advances through authored samples.':'Sampled frames · local receipt timestamps · unsaved intervals cannot be replayed.','sampled-note'));
  const aside=node('aside','','overview');aside.setAttribute('aria-label','Session account');const tabs=node('div','','tabs');tabs.setAttribute('role','tablist');tabs.setAttribute('aria-label','Session account view');
  for(const[key,label]of[['overview','Overview'],['time','At this time']]){const b=button(label,()=>switchTab(key));b.id=`tab-${key}`;b.setAttribute('role','tab');b.setAttribute('aria-controls','account-panel');b.setAttribute('aria-selected',String(key===reviewTab));b.tabIndex=key===reviewTab?0:-1;b.onkeydown=e=>{if(['ArrowLeft','ArrowRight','Home','End'].includes(e.key)){e.preventDefault();switchTab(key==='overview'?'time':'overview');document.querySelector('.tabs [aria-selected=true]')?.focus();}};tabs.append(b);}
  const panel=node('div');panel.id='account-panel';panel.setAttribute('role','tabpanel');panel.setAttribute('aria-labelledby','tab-overview');aside.append(tabs,panel);layout.append(main,aside);$('content').replaceChildren(layout,footer());renderAccount();
}
function bounds(){const s=review.session;const a=s.start_elapsed_ms??frames[0]?.elapsed_ms??0;const b=Math.max(a+1,s.end_elapsed_ms??frames.at(-1)?.elapsed_ms??a+1);return[a,b];}
function percent(ms){const[a,b]=bounds();return Math.max(0,Math.min(100,(ms-a)/(b-a)*100));}
function buildTimeline(){
  const timeline=node('div','','timeline');timeline.id='timeline';const track=node('div','','timeline-track');
  const film=node('div','','filmstrip');film.id='filmstrip';track.append(film);const segments=node('div','','segment-track');segments.id='segments';track.append(segments);
  const labels=node('div','','timeline-labels');labels.id='timeline-labels';track.append(labels);
  const ticks=node('div','','time-ticks');ticks.id='time-ticks';track.append(ticks);
  track.onclick=e=>{if(e.target===segments||e.target===ticks){const rect=segments.getBoundingClientRect();const[a,b]=bounds();const position=a+Math.max(0,Math.min(1,(e.clientX-rect.left)/rect.width))*(b-a);const nearest=frames.reduce((old,f)=>!old||Math.abs(f.elapsed_ms-position)<Math.abs(old.elapsed_ms-position)?f:old,null);stopPlayback();if(nearest)chooseFrame(nearest);}};
  const seek=node('input','','seek');seek.id='seek';seek.type='range';const[a,b]=bounds();seek.min=a;seek.max=b;seek.step=1;seek.value=a;seek.disabled=!frames.length;seek.setAttribute('aria-label','Selected frame time');seek.oninput=()=>{stopPlayback();const nearest=frames.reduce((old,f)=>Math.abs(f.elapsed_ms-Number(seek.value))<Math.abs(old.elapsed_ms-Number(seek.value))?f:old,frames[0]);if(nearest)chooseFrame(nearest);};seek.onkeydown=e=>{if(e.key==='ArrowLeft'||e.key==='ArrowRight'){e.preventDefault();stepFrame(e.key==='ArrowLeft'?-1:1);}};track.append(seek);
  const controls=node('div','','timeline-controls'),clock=node('div','','frame-time');clock.id='frame-time';const count=node('span','','frame-count');count.id='frame-count';controls.append(clock,count);
  const transport=node('div','','transport');const prev=button('',()=>stepFrame(-1),'','skip-back');prev.id='previous-frame';prev.setAttribute('aria-label','Previous sampled frame');prev.title='Previous sampled frame';const play=button('',togglePlayback,'','play');play.id='play-frames';play.setAttribute('aria-label','Play sampled frames');play.setAttribute('aria-pressed','false');play.title='Play sampled frames';const next=button('',()=>stepFrame(1),'','skip-forward');next.id='next-frame';next.setAttribute('aria-label','Next sampled frame');next.title='Next sampled frame';const latest=button('Latest',chooseLatest,'','fast-forward');latest.id='latest-frame';transport.append(prev,play,next,latest);controls.append(transport);timeline.append(track,controls);
  queueMicrotask(renderTimeline);return timeline;
}
function renderTimeline(){
  if(!review||!$('filmstrip'))return;
  const film=$('filmstrip');film.replaceChildren();const sample=frames.length<=9?frames:Array.from({length:9},(_,i)=>frames[Math.round(i*(frames.length-1)/8)]);
  sample.forEach(f=>{const b=button('',()=>{stopPlayback();chooseFrame(f);});b.style.left=`${Math.min(91,percent(f.elapsed_ms))}%`;b.title=`Frame ${f.frame_id} · ${precise(f.received_at)}`;b.setAttribute('aria-label',b.title);b.dataset.frame=f.frame_id;b.classList.toggle('selected',selected?.id===f.id);b.append(img(imageURL(f),`Frame ${f.frame_id}`));film.append(b);});
  const segments=$('segments'),labels=$('timeline-labels');segments.replaceChildren();labels.replaceChildren();
  const all=[...(review.account.account?.segments||[]),...(review.coverage||[]).map(g=>({...g,gap:true,uncertain:true}))];
  const palette=['#c0e66e','#ad91ed','#79b5ef','#ed9780','#70d0ba','#e99ac7','#c5b48a','#8fd0e3','#a9b5ed','#d0d788','#dca9e5','#89c6a0','#edb990','#a6c5c9','#c3a7a0','#b8c58c'];
  all.forEach((s,i)=>{
    const color=s.gap?'#f2b849':palette[i%palette.length];
    const description=s.uncertain&&!s.gap?`${s.label} (uncertain)`:s.label;
    const b=button('',()=>{if(s.frame_ids?.length)chooseCitation(s.frame_ids[0]);else toast(`${s.label}: no continuous footage is retained here.`);},`segment${s.uncertain?' uncertain':''}${s.gap?' gap':''}`);
    b.style.setProperty('--segment-color',color);
    b.style.left=`${percent(s.start_ms)}%`;b.style.width=`${Math.max(.25,percent(s.end_ms)-percent(s.start_ms))}%`;
    b.title=description;b.setAttribute('aria-label',description);segments.append(b);
    if(i<5){const label=node('span',s.label,s.uncertain?'uncertain':'');label.style.setProperty('--segment-color',color);label.title=description;label.prepend(node('i'));labels.append(label);}
  });
  if(!all.length)labels.append(node('span','Retained samples · no established segments'));
  const[a,b]=bounds(),ticks=$('time-ticks');ticks.replaceChildren();const origin=Date.parse(review.session.started_at||frames[0]?.received_at||new Date().toISOString())-a;
  for(let i=0;i<6;i++)ticks.append(node('span',new Date(origin+a+(b-a)*i/5).toLocaleTimeString([],{hour:'2-digit',minute:'2-digit',hour12:false})));
  $('seek').min=a;$('seek').max=b;$('seek').disabled=!frames.length;updateTransport();
}
function chooseFrame(frame){
  if(!frame||!review)return;selected=frame;const stage=$('image-stage');let original=$('original-frame');
  if(!original){original=img(imageURL(frame),'Original retained camera frame',false);original.id='original-frame';const label=node('span','','frame-provenance');label.id='frame-provenance';stage.replaceChildren(original,label);}else original.src=imageURL(frame);
  original.alt=`Original frame ${frame.frame_id}, received ${precise(frame.received_at)}`;
  $('frame-provenance').textContent=`${demo?'DEMO · GENERATED IMAGE':frame.source==='replay'?'REPLAY · SAVED ORIGINAL':'SAVED ORIGINAL'} · ${precise(frame.received_at)}`;
  $('seek').value=frame.elapsed_ms;$('seek').setAttribute('aria-valuetext',`Frame ${frame.frame_id}, ${precise(frame.received_at)}`);
  $('frame-time').replaceChildren(document.createTextNode(precise(frame.received_at)+' '),node('span','/ '+precise(review.session.last_received_at||frames.at(-1)?.received_at)));
  $('frame-count').textContent=`Frame ${frame.frame_id} · ${frames.findIndex(f=>f.id===frame.id)+1} of ${frames.length}${frameCursor?' loaded':''}`;
  document.querySelectorAll('.filmstrip button').forEach(b=>b.classList.toggle('selected',Number(b.dataset.frame)===frame.frame_id));updateTransport();
  if(reviewTab==='time')renderAtTime();
}
function updateTransport(){if(!$('previous-frame'))return;const index=frames.findIndex(f=>f.id===selected?.id);$('previous-frame').disabled=index<=0;$('next-frame').disabled=!frames.length||(index===frames.length-1&&!frameCursor);$('play-frames').disabled=!frames.length;$('latest-frame').disabled=!frames.length;}
async function loadFrames(){
  if(!frameCursor||demo)return;if(frameTask)return frameTask;
  const version=routeVersion,id=review.session.session_id,cursor=frameCursor;frameLoading=true;
  const task=(async()=>{const page=await api(`/api/session-frames/${encodeURIComponent(id)}?limit=200&cursor=${encodeURIComponent(cursor)}`);if(version!==routeVersion)return;frames.push(...page.frames.filter(f=>!frames.some(old=>old.id===f.id)));frameCursor=page.next_cursor;renderTimeline();})();
  frameTask=task;try{await task;}finally{if(version===routeVersion){frameLoading=false;frameTask=null;}}
}
async function chooseCitation(id){
  stopPlayback();const version=routeVersion;
  try{while(!frames.some(f=>f.frame_id===Number(id))&&frameCursor&&version===routeVersion)await loadFrames();if(version!==routeVersion)return;const frame=frames.find(f=>f.frame_id===Number(id));if(frame)chooseFrame(frame);else toast('This cited frame is not available in the retained archive.');}catch(e){toast(e.message);}
}
async function chooseLatest(){stopPlayback();const version=routeVersion;try{while(frameCursor&&version===routeVersion)await loadFrames();if(version===routeVersion)chooseFrame(frames.at(-1));}catch(e){toast(e.message);}}
async function stepFrame(direction,keepPlaying=false){if(!keepPlaying)stopPlayback();const version=routeVersion;try{let index=frames.findIndex(f=>f.id===selected?.id);if(direction>0&&index===frames.length-1&&frameCursor)await loadFrames();if(version!==routeVersion)return;index=frames.findIndex(f=>f.id===selected?.id);const next=frames[index+direction];if(next)chooseFrame(next);else stopPlayback();}catch(e){stopPlayback();toast(e.message);}}
function togglePlayback(){if(playing){stopPlayback();return;}if(!frames.length)return;if(selected?.id===frames.at(-1)?.id&&!frameCursor)chooseFrame(frames[0]);const token={};playing=token;const play=$('play-frames');play.replaceChildren(icon('pause'));play.setAttribute('aria-pressed','true');play.setAttribute('aria-label','Pause sampled playback');const tick=async()=>{if(playing!==token)return;await stepFrame(1,true);if(playing===token)token.timer=setTimeout(tick,950);};token.timer=setTimeout(tick,950);}
function stopPlayback(){if(playing)clearTimeout(playing.timer);playing=null;const b=$('play-frames');if(b){b.replaceChildren(icon('play'));b.setAttribute('aria-pressed','false');b.setAttribute('aria-label','Play sampled frames');}}
function switchTab(key){reviewTab=key;document.querySelectorAll('.tabs button').forEach(b=>{const active=b.id===`tab-${key}`;b.setAttribute('aria-selected',String(active));b.tabIndex=active?0:-1;});$('account-panel').setAttribute('aria-labelledby',`tab-${key}`);if(key==='time')renderAtTime();else{selectionVersion++;renderAccount();}}
function citation(frameID){const frame=frames.find(f=>f.frame_id===Number(frameID));const b=button('',()=>chooseCitation(frameID),'citation');if(frame)b.append(img(imageURL(frame),`Evidence frame ${frameID}`));b.append(node('span',`Frame ${frameID}${frame?' · '+precise(frame.received_at):''}`),icon('chevron-right'));b.title=`View original frame ${frameID}`;return b;}
function citations(ids){const row=node('div','','citations');[...new Set(ids||[])].slice(0,4).forEach(id=>row.append(citation(Number(String(id).split(':').at(-1)))));return row;}
function renderAccount(){
  if(!review||reviewTab!=='overview'||!$('account-panel'))return;const root=$('account-panel'),meta=review.account,a=meta.account;root.replaceChildren();
  const sections=node('div','','account-sections');
  for(const[key,title,name]of[['objective','Objective','target'],['process','Process','file-text'],['outcome','Observed outcome','check-circle'],['unknowns','Still unknown','help-circle']]){
    const value=a?.[key]||{text:key==='objective'?'Objective was not recorded.':key==='process'?'Generate an overview from this session’s saved observations.':key==='outcome'?'Outcome not established.':'No account has been generated. Unobserved intervals remain unknown.',frame_ids:[]};
    const card=node('details','',`account-section${key==='unknowns'?' unknown':''}`);card.open=true;const summary=node('summary');summary.append(icon(name),node('h2',title),icon('chevron-down'));card.append(summary,node('p',value.text,'account-copy'));if(value.frame_ids?.length)card.append(citations(value.frame_ids));sections.append(card);
  }
  root.append(sections);const actions=node('div','','account-actions');
  if(meta.stale)actions.append(node('p','This account predates the latest evidence.','amber'));
  if(meta.error)actions.append(errorBox(meta.error));
  if(meta.sampling_notice)actions.append(node('p',meta.sampling_notice));
  if(meta.generated_at)actions.append(node('p',`Version ${meta.version} · ${date(meta.generated_at)} · ${meta.model} · AI interpretation`));
  if(!demo&&meta.generation_allowed){const b=button(meta.state==='pending'?'Generating overview…':a?'Refresh overview':'Generate overview',generateAccount);b.disabled=meta.state==='pending';actions.append(b,node('p','Uses the configured model and may incur API usage.'));
  }else if(!demo)actions.append(node('p','Saved archive · generation is disabled.'));
  root.append(actions);
}
async function generateAccount(){const version=routeVersion;try{review.account=await api(`/api/session-accounts/${encodeURIComponent(review.session.session_id)}/generate`,{});if(version===routeVersion)renderAccount();}catch(e){toast(e.message);}}
async function refreshReview(){
  if(route!=='review'||!review||demo||accountBusy)return;accountBusy=true;const version=routeVersion,id=review.session.session_id;
  try{const next=await api(`/api/session-review/${encodeURIComponent(id)}`);if(version!==routeVersion)return;const accountChanged=JSON.stringify(next.account)!==JSON.stringify(review.account);const wasLatest=selected?.id===frames.at(-1)?.id&&!frameCursor;const newest=frames.at(-1);
    review={...review,...next};if(!$('observation-title')?.querySelector('form')){$('observation-title')?.replaceWith(observationTitle(review.session));document.title=`${review.session.title} · FieldNotes`;}if(accountChanged)renderAccount();
    if(newest&&!frameCursor){const cursor=btoa(JSON.stringify([newest.elapsed_ms,newest.id]));const page=await api(`/api/session-frames/${encodeURIComponent(id)}?limit=200&cursor=${encodeURIComponent(cursor)}`);if(version!==routeVersion)return;if(page.frames.length){frames.push(...page.frames);frameCursor=page.next_cursor;if(wasLatest&&!playing)chooseFrame(frames.at(-1));}}
    else if(!newest){const page=await api(`/api/session-frames/${encodeURIComponent(id)}?limit=200`);if(version!==routeVersion)return;frames=page.frames;frameCursor=page.next_cursor;if(frames.length)chooseFrame(frames[0]);}
    renderTimeline();
  }catch(e){if(version===routeVersion)toast(e.message);}finally{accountBusy=false;}
}
async function renderAtTime(){
  if(!review||!$('account-panel'))return;const root=$('account-panel');if(!selected){root.replaceChildren(empty('No frame selected','Select retained evidence to inspect observations at that time.',true));return;}
  const version=++selectionVersion,session=review.session.session_id,frame=selected;root.replaceChildren(node('div','Loading observations…','loading'));
  try{const result=demo?demoState(frame):await api(`/api/memory/state?session_id=${encodeURIComponent(session)}&at=${frame.elapsed_ms}`);
    const changes=demo?{changes:[]}:await api(`/api/memory/changes?session_id=${encodeURIComponent(session)}&end=${frame.elapsed_ms}`);
    if(version!==selectionVersion||reviewTab!=='time'||route!=='review')return;root.replaceChildren();
    if(!result.states.length)root.append(empty('Not established at this time','No saved interpretations support this selected time.',true));
    for(const item of result.states){const a=item.latest_interpretation,card=node('article','','state-card');card.append(node('h3',a.subject),node('p',a.value),node('small',`${a.epistemic.replaceAll('_',' ')} · last observed ${precise(a.last_observed_at)}`));if(a.uncertainty)card.append(node('p',a.uncertainty,'uncertainty'));if(item.support_conflict)card.append(node('p','Conflicting interpretations remain unresolved.','uncertainty'));card.append(citations(a.evidence_ids));root.append(card);}
    for(const change of changes.changes.slice(-3)){const card=node('article','','state-card');card.append(node('h3',change.kind.replaceAll('_',' ')),node('p',change.explanation),citations([...(change.before?.evidence_ids||[]),...(change.after?.evidence_ids||[])]));root.append(card);}
    root.append(node('p','Last observed does not mean currently known.','account-actions'));
  }catch(e){if(version===selectionVersion)root.replaceChildren(errorBox(e.message));}
}

function renderLive(){
  document.title='Live · FieldNotes';liveRevision='';const layout=node('div','','live-layout'),main=node('section'),viewer=node('div','','live-viewer');
  const heading=node('div','','live-heading'),identity=node('div'),title=node('h1',demo?demoSessions.find(s=>s.session_id==='demo-lounge').title:'Live observation');title.id='live-title';const subtitle=node('p',demo?'Started 10:12 AM · Lounge (North)':'Waiting for a recording session');subtitle.id='live-subtitle';identity.append(title,subtitle);heading.append(identity);
  const history=link('Open session history',demo?refHash('demo-lounge'):'#/sessions','history-link','book-open');history.id='history-link';history.append(icon('arrow-right'));heading.append(history);viewer.append(heading);
  const stage=node('div','','live-stage');stage.id='live-stage';const preview=img(demo?'/static/demo/lounge-seated.png':'/api/preview','Current camera view',false);preview.id='preview';preview.onerror=()=>{preview.hidden=true;if($('video-placeholder')){$('video-placeholder').hidden=false;$('video-placeholder').firstElementChild.textContent='Camera image unavailable.';}};preview.onload=()=>{preview.hidden=false;};stage.append(preview);
  const overlay=node('div','','live-overlay'),badge=node('span','','live-badge');badge.id='live-badge';badge.append(node('i','','status-dot'),node('span',demo?'DEMO LIVE':'WAITING'));const freshness=node('span',demo?'Simulated feed':'Waiting for video');freshness.id='live-freshness';overlay.append(badge,freshness);stage.append(overlay);
  const placeholder=node('div','','video-placeholder');placeholder.id='video-placeholder';placeholder.hidden=demo;placeholder.append(node('h2','Awaiting your perspective.'),node('p','Publish a camera stream using the address in Connection details below.'));stage.append(placeholder);viewer.append(stage);main.append(viewer,observationSettings(),connectionSettings());
  const panel=node('aside','','activity-panel');panel.setAttribute('aria-label','Recent live observations');const ah=node('div','','activity-heading');ah.append(node('h2','Live observations'));const count=node('span','0');count.id='activity-count';ah.append(count);panel.append(ah);const entries=node('div');entries.id='activity-entries';panel.append(entries);layout.append(main,panel);$('content').append(layout,footer());
  if(demo)renderActivity(demoActivity());else{updateLiveStatus();loadActivity();const version=routeVersion;api('/api/config').then(c=>{if(version!==routeVersion)return;$('brief').value=c.brief;$('interval').value=c.interval_seconds;publish=c.publish_url;updateLiveStatus();}).catch(e=>toast(e.message));}
}
function disclosure(title,description,name,id){const d=node('details','','settings-panel');d.id=id;const s=node('summary'),labels=node('div');labels.append(node('h2',title),node('p',description));s.append(icon(name),labels,icon('chevron-down'));d.append(s);return d;}
function observationSettings(){
  const d=disclosure('Observation brief and cadence',demo?'Objective: monitor lounge usage · cadence: every 20s':'Configure what to observe and how often','file-text','observation-settings'),body=node('div','','settings-content'),form=node('form');form.id='settings';
  const label=node('label','What should the observer describe?');label.htmlFor='brief';const brief=node('textarea');brief.id='brief';brief.rows=3;brief.maxLength=4000;brief.required=true;brief.value=demo?'Monitor lounge usage. Describe visible activities and preserve uncertainty.':observer?.brief||'';
  const row=node('div','','controls-row'),cadence=node('label','Observe every ');cadence.htmlFor='interval';const input=node('input');input.id='interval';input.type='number';input.min=1;input.max=60;input.step='any';input.value=demo?20:observer?.interval_seconds||5;input.required=true;cadence.append(input,document.createTextNode(' seconds'));const pause=button('Pause',async()=>{if(demo){$('observer-status').textContent='Demo observation paused.';return;}try{await api('/api/summaries/pause',{});updateStatus(await api('/api/status'));}catch(e){toast(e.message);}});pause.id='pause-observing';const start=button('Start observing',null,'primary');start.id='start-observing';start.type='submit';row.append(cadence,pause,start);form.append(label,brief,row);form.onsubmit=async e=>{e.preventDefault();if(demo){$('observer-status').textContent=`Demo settings applied · every ${input.value}s. No model calls.`;return;}start.disabled=true;try{await api('/api/summaries/start',{brief:brief.value.trim(),interval_seconds:Number(input.value)});updateStatus(await api('/api/status'));toast('Observation settings applied.');}catch(e){toast(e.message);start.disabled=false;}};
  const status=node('p',demo?'Demonstration only. Settings do not affect a camera.':'','footnote');status.id='observer-status';status.setAttribute('role','status');body.append(form,status,node('p','Only sampled images are retained. Observation sends images to the configured model; analysis may lag the feed. Flight remains with the operator.','footnote'));d.append(body);return d;
}
function connectionSettings(){
  const d=disclosure('Connection details','RTMP addresses and diagnostic information hidden','wifi','connection-settings'),body=node('div','','settings-content'),row=node('div','','address-row');const address=node('code',demo?'Demonstration · no RTMP connection':publish||'Loading connection details…');address.id='publish-address';const copy=button('Copy',async()=>{if(demo){toast('The demo has no stream address.');return;}try{await navigator.clipboard.writeText(publish);toast('RTMP address copied.');}catch{toast('Copy unavailable. Select the address and copy it manually.');}});copy.id='copy-address';row.append(address,copy);body.append(node('p','RTMP publish address','muted tiny'),row);const diagnostics=node('div','','diagnostics');diagnostics.id='diagnostics';body.append(diagnostics);d.append(body);return d;
}
function updateLiveStatus(){
  if(route!=='live'||demo||!$('live-stage')||!stream)return;
  const live=stream.state==='live',archive=stream.state==='archive',stale=!live&&stream.latest_frame_age_ms!=null;
  $('live-stage').classList.toggle('stale',stale);$('live-badge').lastElementChild.textContent=archive?'ARCHIVE':stream.source==='replay'?'REPLAY':live?'LIVE':stale?'STALE':'WAITING';
  $('live-freshness').textContent=archive?'Saved original · not live':stream.latest_frame_age_ms==null?'Waiting for video':`Last frame ${(stream.latest_frame_age_ms/1000).toFixed(1)}s ago`;
  const placeholder=$('video-placeholder');placeholder.hidden=(live||archive)&&!$('preview').hidden;placeholder.firstElementChild.textContent=stale?'Video interrupted.':'Awaiting your perspective.';placeholder.lastElementChild.textContent=stale?'The last frame is stale. Waiting for the stream to return.':'Publish a camera stream using the address in Connection details below.';
  $('live-title').textContent=stream.session_name|| (archive?'Saved archive':'Live observation');$('live-subtitle').textContent=archive?'Historical evidence · not a live stream':stream.active_session_id?`${stream.source==='replay'?'Prerecorded replay':'Camera connected'} · ${stream.session_state==='active'?'Recording':'Waiting for video'}`:'No active recording session';
  $('history-link').href=stream.active_session_id?refHash(stream.active_session_id):'#/sessions';$('history-link').hidden=!stream.active_session_id&&!archive;
  $('publish-address').textContent=archive?'Archive mode · RTMP disabled':publish;$('copy-address').disabled=archive||!publish;
  if(observer){$('start-observing').disabled=archive||!observer.available;$('start-observing').textContent=observer.enabled?'Apply settings':'Start observing';$('pause-observing').disabled=archive||!observer.enabled;
    $('observer-status').textContent=observer.model_error||observer.error||observer.memory_error||(archive?'Archive mode · no paid calls':observer.enabled?`${observer.busy?'Analyzing':'Observing'} · every ${observer.interval_seconds}s${observer.degraded_cadence?' · analysis is behind live video':''}`:'Observation paused');
    $('observation-settings').querySelector('summary p').textContent=`${observer.enabled?'Observing':'Paused'} · cadence: every ${observer.interval_seconds}s`;
  }
  $('diagnostics').textContent=`Source: ${stream.source}\nStream: ${stream.state}\nSession: ${stream.active_session_id||'none'}\nModel: ${observer?.model||'unavailable'}${stream.decoder_error?'\nDecoder: '+stream.decoder_error:''}${stream.interruption_grace_remaining_ms!=null?'\nSession closes after '+Math.ceil(stream.interruption_grace_remaining_ms/1000)+'s without video':''}`;
}
async function loadActivity(){
  if(route!=='live'||demo||liveBusy)return;liveBusy=true;const version=routeVersion,id=stream?.active_session_id;
  try{if(!id){if($('activity-entries'))renderActivity([]);return;}const result=await api(`/api/journal?session_id=${encodeURIComponent(id)}&limit=30`);if(version!==routeVersion||id!==stream?.active_session_id)return;
    const entries=result.entries,signature=JSON.stringify(entries);if(signature===liveRevision)return;liveRevision=signature;
    const completed=entries.filter(e=>e.visual).slice(-3).reverse();const items=completed.map(e=>({title:e.visual.observed_actions?.[0]?.description||'Observation recorded',text:e.visual.summary,time:e.end_at,image:e.frames[0]?imageURL(e.frames[0]):'',frame:e.frames[0]?.frame_id,session:id,mock:['fixture-model','stub-no-vision'].includes(e.model)}));
    const uncertain=[...entries].reverse().find(e=>e.visual?.uncertainties?.length||e.status!=='completed'||e.gaps?.length);
    if(uncertain)items.push({title:uncertain.status==='completed'?'Could not confirm':'Observation interrupted',text:uncertain.visual?.uncertainties?.join(' ')||uncertain.message||'Video coverage is incomplete.',time:uncertain.end_at,image:uncertain.frames?.[0]?imageURL(uncertain.frames[0]):'',frame:uncertain.frames?.[0]?.frame_id,session:id,uncertain:true});renderActivity(items);
  }catch(e){if(version===routeVersion&&$('activity-entries'))$('activity-entries').replaceChildren(errorBox(e.message));}finally{liveBusy=false;}
}
function renderActivity(items){
  const root=$('activity-entries');if(!root)return;root.replaceChildren();$('activity-count').textContent=items.filter(i=>!i.uncertain).length;
  if(!items.length){root.append(empty('Ready to observe','Saved observations will appear here once video is connected and observation is running.',true));return;}
  for(const item of items){const article=node('article','',`activity-item${item.uncertain?' uncertain':''}`),stamp=node('div','','activity-time');stamp.append(item.uncertain?icon('alert-circle'):node('i','','status-dot active'),node('span',precise(item.time)));if(item.uncertain)stamp.append(node('span','Uncertain','amber'));if(item.mock)stamp.append(node('span','MOCK','amber'));article.append(stamp);const body=node('div','','activity-body');if(item.image){const evidence=link('',refHash(item.session,item.frame),'activity-evidence');evidence.title='Open cited frame';evidence.append(img(item.image,`Evidence at ${precise(item.time)}`));body.append(evidence);}const copy=node('div','','activity-text');copy.append(node('h3',item.title),node('p',item.text));body.append(copy);article.append(body);root.append(article);}
}
setupConnection();renderRoute();
