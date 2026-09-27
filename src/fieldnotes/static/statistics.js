// Job statistics: deterministic metrics and model-generated charts rendered from validated ChartSpecs.
// Chart.js only ever receives data the server has already checked; no model output reaches this file as code.
const PALETTE = ['#c0e66e', '#f2b849', '#7cc4ff', '#f46d63', '#d3a4ff', '#8fd9c8'];
const METRIC_ORDER = ['observations', 'actions', 'uncertainties', 'latency', 'tokens', 'gaps', 'assertions', 'sessions'];
const EXAMPLES = [
  'How many times I picked up my phone and doomscrolled over the past 10 minutes, per minute, as a line graph.',
  'Minutes per hour where someone was at the desk versus away, as a stacked bar chart.',
  'Count of observations mentioning a laptop screen, per 5 minutes.',
];
const charts = new Map();
let env = null, requestPoll = null;

export function configureStatistics(helpers) { env = helpers; }
export function statisticsHash(id) { return '#/jobs/' + encodeURIComponent(id) + '/statistics'; }
export function stopStatistics() { clearInterval(requestPoll); requestPoll = null; for (const chart of charts.values()) chart.destroy(); charts.clear(); }

const spanDays = spec => { const times = spec.series.flatMap(s => s.points.map(p => Date.parse(p.x))).filter(Number.isFinite); return times.length ? (Math.max(...times) - Math.min(...times)) / 864e5 : 0; };
function xLabel(spec, x, days) {
  if (spec.x.kind !== 'time') return x;
  const d = new Date(x); if (Number.isNaN(+d)) return x;
  const t = d.toLocaleTimeString([], {hour: '2-digit', minute: '2-digit', second: days < 0.02 ? '2-digit' : undefined, hour12: false});
  return days >= 1 ? `${d.toLocaleDateString([], {month: 'short', day: 'numeric'})} ${t}` : t;
}
function observationLink(observationId) {
  const parts = observationId.split('-'); const sid = parts.slice(0, -2).join('-') || observationId;
  const a = env.link(observationId.length > 28 ? `${sid.slice(0, 8)}… ${parts.at(-2)}–${parts.at(-1)} ms` : observationId, `#/sessions/${encodeURIComponent(sid)}`, 'tiny');
  return a;
}
const fmt = value => value == null ? '—' : typeof value === 'number' ? (Number.isInteger(value) ? value.toLocaleString() : value.toLocaleString([], {maximumFractionDigits: 2})) : String(value);

export function chartCard(spec, meta = {}) {
  const {node, button, icon} = env;
  const card = node('article', '', 'chart-card' + (spec.kind === 'kpi' ? ' kpi' : ''));
  const head = node('div', '', 'chart-head');
  head.append(node('h3', spec.title), node('p', spec.subtitle || '', 'muted tiny'));
  if (meta.onDelete) { const del = button('', meta.onDelete, 'quiet icon-button', 'trash-2'); del.setAttribute('aria-label', 'Delete chart'); head.append(del); }
  card.append(head);
  if (meta.prompt) card.append(node('p', `“${meta.prompt}”`, 'chart-prompt'));
  const evidence = node('div', '', 'chart-evidence'); evidence.hidden = true;
  if (spec.kind === 'kpi') {
    const point = spec.series[0].points[0];
    card.append(node('p', fmt(point.y) + (spec.y.unit ? ' ' + spec.y.unit : ''), 'kpi-value'), node('p', spec.y.label, 'muted tiny'));
    if (point.evidence.length) card.append(button(`${point.evidence.length} observation${point.evidence.length === 1 ? '' : 's'}`, () => showEvidence(evidence, point.evidence), 'quiet tiny'));
  } else {
    const wrap = node('div', '', 'chart-canvas'); const canvas = node('canvas'); canvas.setAttribute('role', 'img'); canvas.setAttribute('aria-label', `${spec.title}. ${spec.definition}`); wrap.append(canvas); card.append(wrap);
    queueMicrotask(() => mount(canvas, spec, evidence));
  }
  const foot = node('div', '', 'chart-foot');
  foot.append(node('p', spec.definition, 'chart-definition'));
  spec.uncertainties.forEach(u => { const p = node('p', '', 'chart-uncertainty'); p.append(icon('alert-circle'), node('span', u)); foot.append(p); });
  const cov = spec.coverage; const bits = [`${cov.observations_used} of ${cov.observations_available} observations`];
  if (cov.start_at) bits.push(`${new Date(cov.start_at).toLocaleString()} – ${cov.end_at ? new Date(cov.end_at).toLocaleTimeString() : '…'}`);
  if (meta.model) bits.push(meta.model); if (meta.created_at) bits.push('saved ' + new Date(meta.created_at).toLocaleString());
  foot.append(node('p', bits.join(' · '), 'muted tiny'), evidence);
  card.append(foot);
  return card;
}
function showEvidence(box, ids) {
  const {node} = env; box.replaceChildren(node('span', `Evidence · ${ids.length} observation${ids.length === 1 ? '' : 's'}`, 'eyebrow'));
  const list = node('div', '', 'evidence-links'); ids.forEach(id => list.append(observationLink(id))); box.append(list); box.hidden = false;
}
function mount(canvas, spec, evidenceBox) {
  if (!window.Chart || !canvas.isConnected) return;
  const days = spanDays(spec);
  const labels = [...new Set(spec.series.flatMap(s => s.points.map(p => p.x)))];
  if (spec.x.kind === 'time') labels.sort((a, b) => Date.parse(a) - Date.parse(b));
  const type = spec.kind === 'scatter' ? 'scatter' : ['bar', 'stacked_bar'].includes(spec.kind) ? 'bar' : 'line';
  const datasets = spec.series.map((s, i) => {
    const color = PALETTE[i % PALETTE.length]; const byX = new Map(s.points.map(p => [p.x, p]));
    const data = spec.kind === 'scatter' ? s.points.map(p => ({x: Number(p.x), y: p.y})) : labels.map(x => byX.get(x)?.y ?? 0);
    return {label: s.label, data, borderColor: color, backgroundColor: type === 'bar' ? color + 'cc' : color + (spec.kind === 'area' ? '33' : 'ff'),
      fill: spec.kind === 'area', tension: 0.25, pointRadius: labels.length > 80 ? 0 : 3, pointHoverRadius: 5, borderWidth: type === 'bar' ? 0 : 2, points: labels.map(x => byX.get(x))};
  });
  const stacked = spec.kind === 'stacked_bar';
  const grid = {color: '#ffffff12'}, ticks = {color: '#aaa9a3', maxRotation: 0, autoSkip: true, maxTicksLimit: 12};
  const chart = new window.Chart(canvas, {
    type, data: {labels: spec.kind === 'scatter' ? undefined : labels.map(x => xLabel(spec, x, days)), datasets},
    options: {
      responsive: true, maintainAspectRatio: false, animation: false, interaction: {mode: 'index', intersect: false},
      plugins: {
        legend: {display: spec.series.length > 1, labels: {color: '#d3d5ca', boxWidth: 10}},
        tooltip: {callbacks: {footer: items => { const n = items.reduce((sum, item) => sum + (item.dataset.points?.[item.dataIndex]?.evidence.length || 0), 0); return n ? `${n} cited observation${n === 1 ? '' : 's'} · click to list` : 'No cited observations'; }}},
      },
      scales: {
        x: {stacked, grid, ticks, title: {display: true, text: spec.x.label + (spec.x.unit ? ` (${spec.x.unit})` : ''), color: '#777b72'}, type: spec.kind === 'scatter' ? 'linear' : 'category'},
        y: {stacked, grid, ticks, beginAtZero: true, title: {display: true, text: spec.y.label + (spec.y.unit ? ` (${spec.y.unit})` : ''), color: '#777b72'}},
      },
      onClick: (_, elements) => { const ids = [...new Set(elements.flatMap(el => datasets[el.datasetIndex].points?.[el.index]?.evidence || []))]; if (ids.length) showEvidence(evidenceBox, ids); },
    },
  });
  if (spec.baseline != null) {
    chart.data.datasets.push({label: 'Baseline', data: labels.map(() => spec.baseline), borderColor: '#777b72', borderDash: [4, 4], pointRadius: 0, borderWidth: 1, type: 'line', fill: false});
    chart.update();
  }
  charts.set(canvas, chart);
}

function kpiTile(label, value, note) { const {node} = env; const tile = node('div', '', 'kpi-tile'); tile.append(node('p', label, 'eyebrow'), node('p', value, 'kpi-value')); if (note) tile.append(node('p', note, 'muted tiny')); return tile; }
const ms = value => value == null ? '—' : value < 1000 ? `${Math.round(value)} ms` : `${(value / 1000).toFixed(1)} s`;
const minutes = value => `${(value / 60000).toFixed(1)} min`;

export async function renderStatistics(id) {
  const {node, link, api, jobsRequest, empty, errorBox, footer, toast, button, demo, version, $} = env;
  const start = version(); stopStatistics();
  $('content').replaceChildren(node('div', 'Loading statistics…', 'loading'));
  try {
    const job = await jobsRequest('/api/jobs/' + encodeURIComponent(id)); if (start !== version()) return;
    document.title = `${job.name} statistics · FieldNotes`;
    const head = node('div', '', 'jobs-heading'), copy = node('div');
    copy.append(link(job.name, '#/jobs/' + encodeURIComponent(id), 'back-link', 'arrow-left'), node('h1', 'Statistics'), node('p', `${job.name} · ${job.observation_count} observation${job.observation_count === 1 ? '' : 's'} · revision ${job.revision}`, 'muted'));
    head.append(copy);
    const theme = node('section', '', 'job-brief'); theme.append(node('p', 'JOB THEME · GUIDES OBSERVATION, NOT EVIDENCE', 'eyebrow'), node('p', job.theme, 'job-theme-full'));
    const kpis = node('section', '', 'kpi-grid'); kpis.setAttribute('aria-label', 'Key figures');
    const metrics = node('section', '', 'session-group'); metrics.append(node('h2', 'Built-in metrics', 'group-title'), node('p', 'Computed directly from saved observations for this job. No model calls.', 'muted tiny'));
    const metricGrid = node('div', '', 'chart-grid'); metrics.append(metricGrid);
    const ask = node('section', '', 'session-group'); ask.append(node('h2', 'Ask for a chart', 'group-title'));
    const generated = node('div', '', 'chart-grid'); generated.id = 'generated-charts';
    const requests = node('div', '', 'chart-requests'); requests.id = 'chart-requests';
    $('content').replaceChildren(head, theme, kpis, metrics, ask, footer());
    if (demo) {
      kpis.append(kpiTile('Sessions', String(job.observation_count), 'Demonstration archive'));
      metricGrid.append(empty('Statistics need a running server.', 'The demonstration keeps everything in the browser; metrics and generated charts are computed from saved observations on a real FieldNotes server.', true));
      ask.append(node('p', 'Chart requests are disabled in the demonstration.', 'muted tiny'));
      return;
    }
    const summary = await api(`/api/jobs/${encodeURIComponent(id)}/statistics`); if (start !== version()) return;
    const rate = summary.change_observed_rate;
    kpis.append(
      kpiTile('Sessions', fmt(summary.session_count), summary.session_count ? `${minutes(summary.observed_ms)} observed` : 'Assign sessions to this job'),
      kpiTile('Observation windows', fmt(summary.observation_count), `${summary.summarized_count} interpreted · ${summary.not_completed_count} skipped or failed`),
      kpiTile('Change observed', rate == null ? '—' : `${Math.round(rate * 100)}%`, rate == null ? 'No interpreted windows yet' : 'Share of interpreted windows'),
      kpiTile('Observed actions', fmt(summary.action_count), `${summary.uncertainty_count} uncertainties reported`),
      kpiTile('Model latency', ms(summary.mean_latency_ms), `${fmt((summary.tokens.input_tokens || 0) + (summary.tokens.output_tokens || 0))} tokens`),
      kpiTile('Video gaps', ms(summary.gap_ms), `${fmt(summary.assertion_count)} memory assertions`),
      kpiTile('Freshness', summary.freshness_at ? new Date(summary.freshness_at).toLocaleString() : '—', summary.start_at ? 'from ' + new Date(summary.start_at).toLocaleString() : 'No timestamps yet'),
    );
    if (!summary.observation_count) {
      metricGrid.append(empty('No observations in this job yet.', 'Select this job for future sessions or add untracked observations, then return here.', true));
    } else {
      const picker = node('div', '', 'metric-picker'); picker.setAttribute('aria-label', 'Choose a metric');
      const bucketLabel = node('label', 'Bucket '), bucket = node('select'); [['', 'Auto'], ['10', '10 s'], ['30', '30 s'], ['60', '1 min'], ['300', '5 min'], ['600', '10 min'], ['1800', '30 min'], ['3600', '1 h'], ['21600', '6 h'], ['86400', '1 day']].forEach(([v, l]) => bucket.append(new Option(l, v))); bucketLabel.append(bucket);
      const active = new Set(['observations', 'actions', 'latency']);
      const draw = async () => {
        for (const canvas of metricGrid.querySelectorAll('canvas')) { charts.get(canvas)?.destroy(); charts.delete(canvas); }
        metricGrid.replaceChildren(node('div', 'Computing…', 'loading'));
        try {
          const specs = await Promise.all(METRIC_ORDER.filter(m => active.has(m)).map(m => api(`/api/jobs/${encodeURIComponent(id)}/statistics/metrics/${m}${bucket.value ? '?bucket_seconds=' + bucket.value : ''}`)));
          if (start !== version() || !metricGrid.isConnected) return;
          metricGrid.replaceChildren(...specs.map(spec => chartCard(spec)));
        } catch (e) { if (start === version()) metricGrid.replaceChildren(errorBox(e.message)); }
      };
      METRIC_ORDER.forEach(m => { const chip = button(m, () => { if (active.has(m)) active.delete(m); else active.add(m); chip.setAttribute('aria-pressed', String(active.has(m))); draw(); }, 'chip'); chip.setAttribute('aria-pressed', String(active.has(m))); chip.title = summary.metrics[m]; picker.append(chip); });
      bucket.onchange = draw; picker.append(bucketLabel); metrics.insertBefore(picker, metricGrid); draw();
    }
    // Model-generated charts: request → bounded MCP agent → validated ChartSpec → saved chart.
    const form = node('form', '', 'chart-form'); const prompt = node('textarea'); prompt.rows = 3; prompt.maxLength = 2000; prompt.required = true; prompt.placeholder = EXAMPLES[0]; prompt.setAttribute('aria-label', 'Describe the chart you want');
    const submit = button('Generate chart', null, 'primary', 'bar-chart-2'); submit.type = 'submit'; submit.disabled = !summary.generation_allowed || !summary.observation_count;
    const examples = node('div', '', 'chart-examples'); EXAMPLES.forEach(text => examples.append(button(text, () => { prompt.value = text; prompt.focus(); }, 'quiet tiny')));
    form.append(prompt, node('div', '', 'job-actions'), examples); form.querySelector('.job-actions').append(submit);
    const note = summary.generation_allowed ? `Charts are built by ${summary.model} from this job’s saved observation summaries through the FieldNotes MCP tools. Every point must cite observations inside this job; the theme itself is never counted as evidence.` : 'Saved archive · generated charts are disabled because the archive makes no model calls.';
    ask.append(form, node('p', note, 'capture-note'), requests, generated);
    const refresh = async (quiet = true) => {
      if (start !== version()) { stopStatistics(); return; }
      try {
        const [list, pending] = await Promise.all([api(`/api/jobs/${encodeURIComponent(id)}/charts`), api(`/api/jobs/${encodeURIComponent(id)}/charts/requests`)]);
        if (start !== version()) return;
        const signature = JSON.stringify([list, pending]); if (quiet && generated.dataset.signature === signature) return; generated.dataset.signature = signature;
        requests.replaceChildren();
        pending.requests.filter(r => r.state !== 'ready').slice(0, 5).forEach(r => { const row = node('div', '', 'chart-request ' + r.state); row.append(node('span', r.state === 'pending' ? 'Generating…' : 'Could not chart', 'eyebrow'), node('span', `“${r.prompt}”`)); if (r.error) row.append(node('span', r.error, 'muted tiny'), button('Retry', () => request(r.prompt), 'quiet tiny')); requests.append(row); });
        for (const canvas of generated.querySelectorAll('canvas')) { charts.get(canvas)?.destroy(); charts.delete(canvas); }
        generated.replaceChildren(...list.charts.map(c => chartCard(c.spec, {prompt: c.prompt, model: c.model, created_at: c.created_at, onDelete: async () => { try { await api(`/api/jobs/${encodeURIComponent(id)}/charts/${encodeURIComponent(c.chart_id)}/delete`, {request_id: crypto.randomUUID()}); refresh(false); } catch (e) { toast(e.message); } }})));
        if (!list.charts.length && !pending.requests.length) generated.append(node('p', 'No generated charts yet. Describe one above.', 'muted tiny'));
        clearInterval(requestPoll); requestPoll = pending.requests.some(r => r.state === 'pending') ? setInterval(refresh, 1500) : null;
      } catch (e) { if (start === version() && !quiet) toast(e.message); }
    };
    const request = async text => {
      submit.disabled = true;
      try { await api(`/api/jobs/${encodeURIComponent(id)}/charts/requests`, {request_id: crypto.randomUUID(), prompt: text}); prompt.value = ''; await refresh(false); }
      catch (e) { toast(e.message); } finally { submit.disabled = !summary.generation_allowed; }
    };
    form.onsubmit = e => { e.preventDefault(); const text = prompt.value.trim(); if (text) request(text); };
    await refresh(false);
  } catch (e) { if (start === version()) $('content').replaceChildren(link('All jobs', '#/jobs', 'back-link', 'arrow-left'), empty('Could not load statistics', e.message)); }
}
