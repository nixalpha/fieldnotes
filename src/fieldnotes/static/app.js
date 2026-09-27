const $ = (id) => document.getElementById(id);
const text = (tag, value, cls) => { const el = document.createElement(tag); el.textContent = value; if (cls) el.className = cls; return el; };
let publish = '', loading = false;
const fmt = (iso) => new Date(iso).toLocaleTimeString([], {hour12: false});
async function api(path, body) {
  const response = await fetch(path, body === undefined ? {} : {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
  const result = await response.json();
  if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : JSON.stringify(result.detail));
  return result;
}
function showError(message) { $('error').textContent = message || ''; $('error').hidden = !message; }
function status({stream, agent, publish_url}) {
  publish = publish_url; $('publish').textContent = publish;
  $('source').textContent = `${stream.source.toUpperCase()} / ${agent.model === 'stub-no-vision' ? 'TEST STUB' : 'OBSERVATION'}`;
  $('video-label').textContent = stream.source === 'replay' ? 'PRERECORDED REPLAY' : 'DRONE FEED';
  $('connection').textContent = stream.state.toUpperCase(); $('connection').className = stream.state;
  $('session-id').textContent = `SESSION ${stream.session_id.slice(0,8)}`;
  $('freshness').textContent = stream.latest_frame_age_ms === null ? 'Waiting for video' : `Last frame ${(stream.latest_frame_age_ms/1000).toFixed(1)}s ago`;
  if (stream.state === 'archive') {
    $('source').textContent='SAMPLED REPLAY / MOCK INTERPRETATIONS';
    $('video-label').textContent='SAVED ORIGINAL · NOT LIVE';
    $('freshness').textContent='Historical evidence';
    $('video-placeholder').hidden=true;
    $('preview').parentElement.classList.remove('stale');
    $('start').disabled=true; $('pause').disabled=true;
    $('publish').textContent='Archive mode · RTMP disabled';
    $('agent-status').textContent='Authored mock interpretations · no paid model calls';
    $('model').textContent='fixture-model';
    return;
  }
  const fresh = stream.state === 'live';
  $('preview').parentElement.classList.toggle('stale', !fresh);
  $('video-placeholder').hidden = fresh;
  if (!fresh) $('video-placeholder').replaceChildren(text('div', stream.latest_frame_age_ms === null ? 'Awaiting your perspective' : 'Video interrupted'), text('span', stream.latest_frame_age_ms === null ? 'Publish the DJI stream to the address below.' : 'The last frame is stale. Waiting for the stream to return.'));
  $('model').textContent = agent.model;
  $('start').disabled = !agent.available;
  $('start').textContent = agent.enabled ? 'Restart observing' : 'Start observing';
  $('pause').disabled = !agent.enabled;
  $('agent-status').textContent = !agent.available ? 'Observer unavailable · preview remains active' : agent.enabled ? `${agent.busy ? 'Analyzing' : 'Observing'} · ${agent.interval_seconds}s windows${agent.pending ? ' · next window queued' : ''}${agent.degraded_cadence ? ' · analysis is behind live video' : ''}` : `Paused${agent.busy ? ' · finishing current analysis' : ''}`;
  showError(agent.model_error || agent.error || agent.memory_error);
}
async function journal() {
  if (loading) return; loading = true;
  try {
    const {entries} = await api('/api/journal');
    $('count').textContent = `${entries.length} recent entries`;
    if (!entries.length) return;
    const container = $('entries'), nearBottom = container.scrollHeight - container.scrollTop - container.clientHeight < 60;
    const nodes = entries.map(entry => {
      const article = text('article', '', `entry${entry.status === 'completed' ? '' : ' system'}`);
      const head = text('div', '', 'entry-head');
      head.append(text('span', `${fmt(entry.start_at)} — ${fmt(entry.end_at)}`), text('span', `${entry.source.toUpperCase()} · ${entry.status.replaceAll('_',' ')}`));
      article.append(head, text('p', entry.visual?.summary || entry.message));
      if (entry.visual?.uncertainties.length) article.append(text('p', entry.visual.uncertainties.join(' '), 'uncertainty'));
      if (entry.gaps.length) article.append(text('p', `Incomplete coverage: ${entry.gaps.length} video gap(s).`, 'uncertainty'));
      if (entry.frames.length) {
        const thumbs = text('div', '', 'thumbs');
        entry.frames.forEach(frame => { const url = `/api/evidence/${frame.session_id}/${frame.frame_id}.jpg`;
          const a = document.createElement('a'); a.href=url; a.target='_blank'; a.rel='noopener';
          const img = document.createElement('img'); img.src=url; img.loading='lazy'; img.alt=`Evidence frame ${frame.frame_id} at ${fmt(frame.received_at)}`;
          a.append(img, text('span', fmt(frame.received_at))); thumbs.append(a); });
        article.append(thumbs);
      }
      const delay = Math.max(0, (Date.parse(entry.generated_at)-Date.parse(entry.end_at))/1000).toFixed(1);
      article.append(text('div', `Written ${fmt(entry.generated_at)} · ${entry.model === "fixture-model" ? "MOCK REPLAY · authored interpretation" : delay + "s after interval"}${entry.model ? ` · ${entry.model}` : ''} · session ${entry.session_id.slice(0,8)}`, 'meta'));
      return article;
    });
    container.replaceChildren(...nodes); if (nearBottom) container.scrollTop = container.scrollHeight;
  } catch(e) { showError(e.message); } finally { loading=false; }
}
$('settings').addEventListener('submit', async e => { e.preventDefault(); try { await api('/api/summaries/start', {brief:$('brief').value, interval_seconds:Number($('interval').value)}); status(await api('/api/status')); } catch(e) { showError(e.message); } });
$('pause').onclick = async () => { try { await api('/api/summaries/pause', {}); status(await api('/api/status')); } catch(e) { showError(e.message); } };
$('copy').onclick = async () => { try { await navigator.clipboard.writeText(publish); $('copy').textContent='Copied'; setTimeout(() => $('copy').textContent='Copy',1500); } catch { showError('Copy unavailable. Select the RTMP address and copy it manually.'); } };
api('/api/config').then(c => { $('brief').value=c.brief; $('interval').value=c.interval_seconds; }).catch(e=>showError(e.message));
api('/api/status').then(status).catch(e=>showError(e.message));
const events = new EventSource('/api/events');
events.addEventListener('status', e => status(JSON.parse(e.data)));
events.addEventListener('journal', journal);
events.onopen = journal;
events.onerror = () => { $('connection').textContent='APP DISCONNECTED'; $('preview').parentElement.classList.add('stale'); };
