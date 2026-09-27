// Isolated authored fixtures. Never written to the recording database.
const asset = name => `/static/demo/${name}.png`;
const at = (hour, minute, yesterday = false) => { const d = new Date(); d.setDate(d.getDate() - Number(yesterday)); d.setHours(hour, minute, 0, 0); return d.toISOString(); };
export const demoSessions = [
  ['workspace', 'Workspace sweep', 9, 12, 9, 24, false, 'workspace', ['Workspace', 'Setup'], 'North bench cleared for calibration; chair obstruction removed.'],
  ['receiving', 'Receiving corner check', 10, 3, 10, 15, false, 'receiving', ['Equipment inspection', 'Inventory'], 'Two unpacked cases remain beside the charging cart.'],
  ['lounge', 'Lounge live pass', 11, 41, 11, 54, false, 'lounge-seated', ['Workspace', 'Safety'], 'One person is seated at a laptop; the main passage remains clear.'],
  ['toolwall', 'Tool wall review', 16, 8, 16, 19, true, 'toolwall', ['Equipment inspection', 'Setup'], 'Missing torque driver noted on lower peg row.'],
  ['entrance', 'Lab entrance audit', 15, 21, 15, 29, true, 'entrance', ['Access', 'Safety'], 'Floor mat slightly displaced beside the entrance.'],
  ['storage', 'Storage mezzanine pass', 13, 54, 14, 6, true, 'storage', ['Inventory', 'Equipment inspection'], 'Outcome not established.'],
].map(([id, title, h, m, eh, em, yesterday, image, topics, outcome]) => ({
  session_id: `demo-${id}`, title, name: title, state: id === 'lounge' ? 'active' : 'ended',
  source: 'demo', started_at: at(h, m, yesterday), created_at: at(h, m, yesterday),
  last_received_at: at(eh, em, yesterday), start_elapsed_ms: 0, end_elapsed_ms: 790001,
  topics, outcome, thumbnail: {url: asset(image)}, account_state: 'ready', image,
}));
const samples = [[21,12000],[40,90000],[60,180000],[80,270000],[100,400000],[118,562000],[130,600000],[150,640000],[167,663000],[171,672000],[183,708000],[190,730000],[210,790000]];
const section = (text, frame_ids = []) => ({text, frame_ids});
export function demoReview(id) {
  const session = demoSessions.find(s => s.session_id === id);
  if (!session) throw Error('This demonstration session does not exist.');
  const lounge = ['demo-workspace', 'demo-lounge'].includes(id);
  const start = lounge ? at(10, 5) : session.started_at;
  const frames = samples.map(([frame_id, elapsed_ms]) => ({
    id: `${id}:${frame_id}`, frame_id, session_id:id, elapsed_ms,
    received_at: new Date(Date.parse(start) + elapsed_ms).toISOString(), source:'demo', width: 1200, height:900,
    url: asset(lounge ? elapsed_ms < 660000 ? 'lounge-seated' : elapsed_ms < 700000 ? 'lounge-standing' : 'lounge-empty' : session.image),
  }));
  return {session: {...demoDecorate(session), title: session.display_name || (lounge ? 'Workspace observation.' : session.title), started_at:start,
    last_received_at: new Date(Date.parse(start)+790000).toISOString(), location: lounge ? 'Lounge' : ''}, frames,
    coverage:[{start_ms:735000, end_ms:785000, label:'Unsaved interval'}],
    account:{state:'ready', version:1, generation_allowed:false, model:'Authored demonstration',
      sampling_notice:'Demonstration account · generated imagery, authored observations.', account:{
      title:session.title, topics:session.topics,
      objective:section(lounge ? 'Understand how the lounge space is used during the morning work period.' : `Observe the ${session.title.toLowerCase()} and record visible changes.`, [21,118]),
      process:section(lounge ? 'Review the session to identify key activities, transitions, and notable events in the lounge area.' : 'Review sampled views and compare visible equipment and access conditions.', [167,183]),
      outcome:section(lounge ? 'Person stood and moved out of the seating view.' : session.outcome, session.image === 'storage' ? [] : [171]),
      unknowns:section(lounge ? 'Task completion was not observed.' : 'Conditions outside the retained camera views could not be established.', [210]),
      segments: lounge ? [
        {label:'Seated at laptop',start_ms:12000,end_ms:660000,frame_ids:[21,118],uncertain:false},
        {label:'Stands up',start_ms:660000,end_ms:700000,frame_ids:[167,171],uncertain:false},
        {label:'Camera reframes',start_ms:700000,end_ms:735000,frame_ids:[183],uncertain:true},
      ] : [{label:'Workspace observed',start_ms:12000,end_ms:735000,frame_ids:[21,118,183],uncertain:false}],
    }}, entries:[]};
}
export function demoState(frame) {
  const moving = frame.elapsed_ms >= 660000, empty = frame.elapsed_ms >= 700000;
  return {states:[{latest_interpretation:{subject:'Lounge seating area',predicate:'visible activity',
    value:empty ? 'The seating area is empty in this view.' : moving ? 'Person is standing beside the seating area.' : 'One person is seated using an open laptop.',
    epistemic:'authored demonstration',interpretation_source:'demo',last_observed_at:frame.received_at,
    uncertainty:'Task completion was not observed.',evidence_ids:[frame.id]}}], changes:[]};
}
export function demoActivity() {
  const id = 'demo-lounge';
  return [
    {title:'Person seated at laptop',text:'One person is seated in the lounge using a laptop.',time:at(10,14),image:asset('lounge-seated'),frame:118,session:id},
    {title:'Person stands up',text:'The person has stood beside the sofa. Their next activity is not established.',time:at(10,13),image:asset('lounge-standing'),frame:171,session:id},
    {title:'Main passage clear',text:'The pathway between the lounge and entry remains clear.',time:at(10,12),image:asset('lounge-empty'),frame:183,session:id},
    {title:'Could not confirm',text:'Unable to confirm whether the task was completed outside the seating view.',time:at(10,11),image:asset('lounge-empty'),frame:210,session:id,uncertain:true},
  ];
}

// Job organization is deliberately memory-only and never calls the real service.
const demoJobs = [
  {job_id:'demo-job-workspace',name:'Workspace readiness',theme:'Observe how work areas are prepared and used. Note equipment placement, visible activity, and access conditions without inferring task completion.',revision:1,created_at:at(8,0),updated_at:at(8,0)},
  {job_id:'demo-job-equipment',name:'Equipment inspection',theme:'Observe the location and visible condition of tools and equipment, including changes and areas that cannot be inspected from the camera view.',revision:1,created_at:at(8,10),updated_at:at(8,10)},
];
const demoMemberships = new Map([['demo-workspace','demo-job-workspace'],['demo-toolwall','demo-job-workspace'],['demo-receiving','demo-job-equipment'],['demo-storage','demo-job-equipment']].map(([sid,jid])=>[sid,{job_id:jid,kind:'captured',assigned_at:at(8,0)}]));
const demoCaptured = new Map([...demoMemberships].map(([sid,m])=>{const j=demoJobs.find(j=>j.job_id===m.job_id);return[sid,{job_id:j.job_id,name:j.name,theme:j.theme,revision:j.revision}];}));
let demoSelectedJob = 'demo-job-workspace', demoSelectionRevision = 1;
const demoJobRequests = new Map();
function getDemoJob(id){const j=demoJobs.find(j=>j.job_id===id);if(!j)throw Error('unknown_job: This demo job does not exist.');return{...j,observation_count:[...demoMemberships.values()].filter(m=>m.job_id===id).length,selected:demoSelectedJob===id};}
export function demoDecorate(session){const m=demoMemberships.get(session.session_id);return{...session,job_id:m?.job_id||null,job:m?getDemoJob(m.job_id):null,job_context:demoCaptured.get(session.session_id)||null,job_assignment:m?{kind:m.kind,assigned_at:m.assigned_at}:null};}
export function demoJobContext(){return{selected_job_id:demoSelectedJob,selected_job:demoSelectedJob?getDemoJob(demoSelectedJob):null,selection_revision:demoSelectionRevision,selection_allowed:true,current_session:demoDecorate(demoSessions.find(s=>s.session_id==='demo-lounge'))};}
export async function demoJobsRequest(path,body){
  const url=new URL(path,location.origin),parts=url.pathname.split('/').filter(Boolean).slice(2),id=parts[0];
  if(body===undefined){
    if(id==='context')return demoJobContext();
    if(id)return getDemoJob(id);
    return{jobs:demoJobs.map(j=>getDemoJob(j.job_id)),next_cursor:null,...demoJobContext()};
  }
  const signature=JSON.stringify({path,body}),old=demoJobRequests.get(body.request_id);if(old){if(old.signature!==signature)throw Error('request_conflict: Request already used.');return structuredClone(old.result);}
  let result;
  if(!id){const name=body.name.trim(),theme=body.theme.trim();if(!name||name.length>120||!theme||theme.length>4000)throw Error('Enter a name and theme within the allowed limits.');const j={job_id:'demo-job-'+crypto.randomUUID(),name,theme,revision:1,created_at:new Date().toISOString(),updated_at:new Date().toISOString()};demoJobs.unshift(j);result={job:getDemoJob(j.job_id)};}
  else if(id==='selection'){
    if(body.expected_selection_revision!==demoSelectionRevision)throw Error('stale_selection: Selection changed. Reload and try again.');
    if(body.job_id)getDemoJob(body.job_id);if(demoSelectedJob!==body.job_id){demoSelectedJob=body.job_id;demoSelectionRevision++;}result={...demoJobContext(),effective:'next_session'};
  }else if(parts[1]==='observations'){
    getDemoJob(id);const ids=[...new Set(body.session_ids)];if(!ids.length||ids.length>100)throw Error('Choose 1–100 observations.');
    for(const sid of ids){if(!demoSessions.some(s=>s.session_id===sid))throw Error('Unknown observation.');if(demoMemberships.has(sid)&&demoMemberships.get(sid).job_id!==id)throw Error('Observation already belongs to another job.');}
    ids.forEach(sid=>{if(!demoMemberships.has(sid))demoMemberships.set(sid,{job_id:id,kind:'assigned',assigned_at:new Date().toISOString()});});result={job:getDemoJob(id),capture_context_unchanged:true};
  }else{
    const job=demoJobs.find(j=>j.job_id===id);getDemoJob(id);if(job.revision!==body.expected_revision)throw Error('stale_revision: Job changed. Reload and try again.');
    const name=(body.name??job.name).trim(),theme=(body.theme??job.theme).trim();if(!name||name.length>120||!theme||theme.length>4000)throw Error('Enter a name and theme within the allowed limits.');
    if(name!==job.name||theme!==job.theme)Object.assign(job,{name,theme,revision:job.revision+1,updated_at:new Date().toISOString()});result={job:getDemoJob(id)};
  }
  demoJobRequests.set(body.request_id,{signature,result:structuredClone(result)});return result;
}
