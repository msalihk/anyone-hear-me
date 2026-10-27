'use strict';

// ---------- helpers ----------
const $ = (s, el = document) => el.querySelector(s);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const words = (s) => String(s ?? '').replace(/_/g, ' ');
const hhmm = (m) => `${Math.floor(m / 60)}:${String(m % 60).padStart(2, '0')}`;
const CHANNELS = { x: 'X', sms: 'SMS', whatsapp: 'WhatsApp', instagram: 'Instagram', facebook: 'Facebook', telegram: 'Telegram',
  form: 'Web form', 'voice note': 'Voice note', call: 'Phone call', email: 'Email' };
const channel = (c) => CHANNELS[c] || (c ? c[0].toUpperCase() + c.slice(1) : 'Report');
const URG = { critical: 0, high: 1, medium: 2, low: 3, closed: 4 };

const ADDR_STATUS = {
  exact: 'matches the official list',
  corrected: 'spelling corrected against the official list',
  inferred: 'filled in from the neighbourhood name',
  ambiguous: 'exists in more than one place',
  not_found: 'not in the official list',
  missing: 'not given',
};
const PLACE_STATUS = {
  confirmed: 'Address checked',
  conflicting: 'Reports disagree on the place',
  settled_by_cross_reports: 'Place settled by other reports',
  ambiguous: 'Place unclear',
  unknown: 'No place yet',
};
const STATUS = { open: 'Open', partly_rescued: 'Some people rescued', resolved: 'Everyone reported rescued' };

// mark evidence quotes inside the original text, without trusting either string as HTML
function highlight(text, quotes) {
  const spans = [];
  for (const q of quotes || []) {
    if (!q || q.length < 3) continue;
    const i = text.indexOf(q);
    if (i >= 0) spans.push([i, i + q.length]);
  }
  spans.sort((a, b) => a[0] - b[0]);
  const merged = [];
  for (const s of spans) {
    const last = merged[merged.length - 1];
    if (last && s[0] <= last[1]) last[1] = Math.max(last[1], s[1]);
    else merged.push([...s]);
  }
  let out = '', at = 0;
  for (const [a, b] of merged) {
    out += esc(text.slice(at, a)) + '<mark>' + esc(text.slice(a, b)) + '</mark>';
    at = b;
  }
  return out + esc(text.slice(at));
}

// ---------- state ----------
const state = {
  steps: [],          // recorded scenario
  coords: {},
  shown: 0,           // how many recorded steps are on the desk
  live: [],           // steps added by this visitor
  tasks: new Map(),   // tid -> latest snapshot
  owner: new Map(),   // message id -> tid
  byId: new Map(),    // message id -> step
  timer: null,
  selected: null,     // {kind: 'step'|'task', id}
  questions: new Map(), // tid -> drafted follow-up questions (live only)
};

// ---------- map ----------
let map, layer;
function initMap() {
  map = L.map('map', { scrollWheelZoom: false, attributionControl: true }).setView([37.3, 37.2], 7);
  L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 17, attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
  }).addTo(map);
  layer = L.layerGroup().addTo(map);
}

function where(task) {
  const p = task.place;
  if (!p || !p.province) return null;
  const k3 = [p.province, p.district, p.neighbourhood].filter(Boolean).join('|');
  const k2 = [p.province, p.district].filter(Boolean).join('|');
  if (p.neighbourhood && state.coords[k3]) return { ...state.coords[k3], approx: false };
  if (state.coords[k2]) return { ...state.coords[k2], approx: true };
  return null;
}

function drawMap(fit) {
  layer.clearLayers();
  const seen = new Map();
  const pts = [];
  for (const t of state.tasks.values()) {
    const w = where(t);
    if (!w) continue;
    const key = `${w.lat.toFixed(4)},${w.lon.toFixed(4)}`;
    const n = seen.get(key) || 0;
    seen.set(key, n + 1);
    // spread tasks that share a neighbourhood centre so each one can be selected
    const ang = n * 2.4, r = n ? 0.0016 * Math.sqrt(n) : 0;
    const lat = w.lat + r * Math.cos(ang), lon = w.lon + r * Math.sin(ang);
    const closed = t.status === 'resolved';
    const icon = L.divIcon({ className: '', iconSize: [22, 22],
      html: `<div class="pin${closed ? ' closed' : ''}${w.approx ? ' approx' : ''}"></div>` });
    const m = L.marker([lat, lon], { icon, keyboard: true, title: `${t.id}: ${taskTitle(t)}` });
    m.on('click', () => select('task', t.id));
    m.addTo(layer);
    pts.push([lat, lon]);
  }
  if (fit && pts.length) map.fitBounds(pts, { padding: [24, 24], maxZoom: 12 });
}

// ---------- tasks ----------
function taskTitle(t) {
  const b = t.building || {};
  const name = b.building_name || [b.street, b.building_no && `No ${b.building_no}`].filter(Boolean).join(' ') || b.landmark;
  return name || 'Building not named yet';
}
function placeLine(t) {
  const p = t.place;
  if (!p || !p.province) return PLACE_STATUS[t.place_status] || 'No place yet';
  return [p.neighbourhood, p.district, p.province].filter(Boolean).join(', ');
}
function peopleText(t) {
  const r = (t.people_reported || []).slice().sort((a, b) => a - b);
  if (!r.length) return '?';
  return r.length === 1 ? String(r[0]) : `${r[0]}–${r[r.length - 1]}`;
}

function putTask(t) {
  for (const mid of t.message_ids) {
    const prev = state.owner.get(mid);
    if (prev && prev !== t.id) state.tasks.delete(prev);   // two tasks joined into one
    state.owner.set(mid, t.id);
  }
  state.tasks.set(t.id, t);
}

function drawTasks(fresh) {
  const list = [...state.tasks.values()].sort((a, b) =>
    (URG[a.urgency] ?? 3) - (URG[b.urgency] ?? 3) || b.last_minute - a.last_minute);
  const resolved = list.filter((t) => t.status === 'resolved').length;
  $('#taskcount').textContent = list.length
    ? `${list.length} buildings from ${state.shown + state.live.length} reports, ${resolved} cleared` : '';
  const ol = $('#tasks');
  ol.innerHTML = list.map((t) => {
    const closed = t.status === 'resolved';
    const conf = (t.conflicts || []).some((c) => c.field === 'people_trapped');
    const hours = Math.max(0, Math.round((t.last_minute - t.first_minute) / 60));
    const cur = state.selected?.kind === 'task' && state.selected.id === t.id;
    return `<li>
      <button class="mark${closed ? ' closed' : ''}${t.urgency === 'critical' ? ' critical' : ''}${fresh?.has(t.id) ? ' new' : ''}"
        data-task="${esc(t.id)}" aria-current="${cur}" aria-label="${esc(`${t.id}, ${taskTitle(t)}, ${placeLine(t)}, ${STATUS[t.status] || t.status}, ${peopleText(t)} people reported`)}">
        <span class="box" aria-hidden="true"></span>
        <span class="q top" aria-hidden="true">${esc(t.id)}</span>
        <span class="q left" aria-hidden="true">${t.reports}</span>
        <span class="q right" aria-hidden="true">${hours}h</span>
        <span class="q bottom${conf ? ' conf' : ''}" aria-hidden="true">${esc(peopleText(t))}${t.people_rescued ? `<small> ${t.people_rescued} out</small>` : ''}</span>
      </button>
      <div class="task-caption"><b>${esc(taskTitle(t))}</b><br>${esc(placeLine(t))}</div>
    </li>`;
  }).join('');
}

// ---------- incoming feed ----------
function feedItem(s, isNew) {
  const r = s.record || {};
  const noise = !r.is_help_request;
  const u = r.status === 'resolved' ? 'closed' : r.urgency;
  const li = document.createElement('li');
  li.className = `report u-${u}${noise ? ' noise' : ''}${isNew ? ' arrive' : ''}`;
  li.dataset.step = s.id;
  li.tabIndex = 0;
  li.setAttribute('aria-current', String(state.selected?.kind === 'step' && state.selected.id === s.id));
  li.innerHTML = `<div class="meta"><span class="ch">${esc(channel(s.channel))}</span><span>hour ${hhmm(s.minute)}</span>
      ${noise ? '<span>not a help request</span>' : ''}${s.id.startsWith('L') ? '<span>yours</span>' : ''}</div>
    <p class="tr" lang="tr">${esc(s.text)}</p>
    ${r.summary_en ? `<p class="en">${esc(r.summary_en)}</p>` : ''}`;
  return li;
}

// ---------- agent trace ----------
function stepTrace(s) {
  const r = s.record || {};
  const parts = [];
  const n = () => parts.length + 1;
  const quotes = (r.evidence || []).map((e) => e.quote);

  // 1. heard or read
  parts.push(`<div class="step"><span class="n">${n()}</span><div>
    <h3>${s.transcript ? 'Transcribed the voice note' : `Read a report sent on ${esc(channel(s.channel))}`} at hour ${hhmm(s.minute)}</h3>
    <p class="quote" lang="tr">${highlight(s.text, quotes)}</p>
    <p class="muted">${s.transcript ? 'Muse Voice Transcribe, biased toward local place names. ' : ''}Highlighted words are the
    quotes the agent used as evidence${s.seconds ? `; ${s.seconds} seconds end to end` : ''}.</p></div></div>`);

  // 2. understood
  if (!r.is_help_request) {
    const why = (s.agent || []).find((a) => a.step === 'not_request')?.reason || r.summary_en;
    parts.push(`<div class="step"><span class="n">${n()}</span><div><h3>Set it aside: not a help request</h3>
      <p>${esc(why)}</p></div></div>`);
    return parts.join('');
  }
  const people = r.people_trapped != null ? `${r.people_trapped} trapped` : 'number not given';
  parts.push(`<div class="step"><span class="n">${n()}</span><div><h3>Understood it with Muse Spark</h3>
    <p>${esc(r.summary_en)}</p>
    <dl class="fields">
      <dt>People</dt><dd>${esc(people)}${r.people_rescued ? `, ${r.people_rescued} rescued` : ''}</dd>
      <dt>Signs of life</dt><dd>${esc(words(r.signs_of_life))}</dd>
      ${r.needs?.length ? `<dt>Needs</dt><dd>${esc(r.needs.map(words).join(', '))}</dd>` : ''}
      <dt>Urgency</dt><dd>${esc(r.status === 'resolved' ? 'closed, everyone reported out' : r.urgency)}${r.urgency_reason ? `: ${esc(r.urgency_reason)}` : ''}</dd>
      ${r.missing_info?.length ? `<dt>Missing</dt><dd>${esc(r.missing_info.join('; '))}</dd>` : ''}
    </dl></div></div>`);

  // 3. address
  const ac = r.address_check || {};
  const rows = ['province', 'district', 'neighbourhood'].map((lvl) => {
    const a = ac[lvl] || {};
    let v;
    if (a.status === 'corrected') v = `<span class="fix"><del>${esc(a.given)}</del> <ins>${esc(a.value)}</ins></span>`;
    else if (a.status === 'ambiguous') v = `${esc(a.given)}, ${ADDR_STATUS.ambiguous}:<ul class="options">${(a.alternatives || []).slice(0, 5).map((o) => `<li>${esc(o)}</li>`).join('')}</ul>`;
    else if (a.status === 'missing') v = `<span class="muted">${ADDR_STATUS.missing}</span>`;
    else v = `${esc(a.value || a.given)} <span class="muted">${esc(ADDR_STATUS[a.status] || '')}</span>`;
    return `<dt>${lvl[0].toUpperCase() + lvl.slice(1)}</dt><dd>${v}</dd>`;
  }).join('');
  const loc = r.location || {};
  const bld = [loc.street, loc.building_no && `No ${loc.building_no}`, loc.building_name, loc.landmark].filter(Boolean).join(', ');
  parts.push(`<div class="step"><span class="n">${n()}</span><div><h3>Checked the address against the official neighbourhood list</h3>
    <dl class="fields">${rows}<dt>Building</dt><dd>${bld ? esc(bld) : '<span class="muted">not given</span>'}</dd></dl></div></div>`);

  // 4. merge decision
  const lines = (s.agent || []).map((a) => {
    if (a.step === 'rule') return `<p>Same place and same building as an open task: joined <b>${esc(a.task)}</b>. <span class="muted">${esc(a.reason)}</span></p>`;
    if (a.step === 'agent') return `<p>Asked Muse Spark whether this is the same building as <b>${esc(a.task)}</b>: <span class="verdict ${esc(a.verdict)}">${esc(a.verdict)}</span></p><p class="muted">${esc(a.reason)}</p>`;
    if (a.step === 'new') return `<p>No open task matches this building, so it opened <b>${esc(a.task)}</b>.</p>`;
    if (a.step === 'possible') return `<p>Possible match, kept apart until confirmed: <span class="muted">${esc(a.reason)}</span></p>`;
    if (a.step === 'linked') return `<p>Mentions more than one building: ${(a.tasks || []).map((t) => `<b>${esc(t)}</b>`).join(', ')}.</p>`;
    return '';
  }).join('');
  parts.push(`<div class="step"><span class="n">${n()}</span><div><h3>Matched it to a building</h3>${lines}</div></div>`);

  // 5. task now, and follow-up questions
  if (s.task) {
    const t = s.task;
    const qs = state.questions.get(t.id) || s.questions || [];
    parts.push(`<div class="step"><span class="n">${n()}</span><div><h3>Updated ${esc(t.id)}: ${esc(taskTitle(t))}</h3>
      <p>${esc(placeLine(t))}. ${esc(STATUS[t.status] || t.status)}; ${t.people_reported?.length ? `${esc(peopleText(t))} people reported trapped` : 'number of people not known yet'}; ${t.reports} report${t.reports > 1 ? 's' : ''} so far.</p>
      ${conflictsHtml(t)}
      ${qs.length ? questionsHtml(t.id, qs) : ''}
      <p><button class="btn" data-open-task="${esc(t.id)}">Open ${esc(t.id)}</button></p></div></div>`);
  }
  return parts.join('');
}

function conflictsHtml(t) {
  const c = (t.conflicts || []).filter((x) => x.values);
  if (!c.length) return '';
  return c.map((x) => `<p class="conflict">Reports disagree on ${esc(words(x.field).replace('people trapped', 'how many people are trapped'))}:
    ${Object.entries(x.values).map(([v, ids]) => `${esc(v)} (${ids.map(esc).join(', ')})`).join(' vs ')}</p>`).join('');
}

function questionsHtml(tid, qs) {
  return `<div><p><b>Questions drafted for the person who reported it</b></p>
    ${qs.map((q) => `<div class="ask" lang="tr">${esc(q.question || q)}${q.why ? `<div class="why" lang="en">${esc(q.why)}</div>` : ''}</div>`).join('')}
    <div class="approve-row"><button class="btn" data-approve="${esc(tid)}">Approve questions</button>
    <span class="muted" id="approved-${esc(tid)}">A coordinator approves before anything is sent. This demo sends nothing.</span></div></div>`;
}

function taskTrace(t) {
  const reports = t.message_ids.map((id) => state.byId.get(id)).filter(Boolean);
  const ev = new Map();
  for (const e of t.evidence || []) {
    if (!ev.has(e.message)) ev.set(e.message, []);
    ev.get(e.message).push(e.quote);
  }
  const qs = state.questions.get(t.id) || [];
  return `<div class="taskview">
    <h3 class="big">${esc(t.id)}: ${esc(taskTitle(t))}</h3>
    <p>${esc(placeLine(t))}. <span class="muted">${esc(PLACE_STATUS[t.place_status] || '')}</span></p>
    <dl class="fields">
      <dt>Status</dt><dd>${esc(STATUS[t.status] || t.status)}</dd>
      <dt>People reported</dt><dd>${esc(peopleText(t))}${t.people_rescued ? `, ${t.people_rescued} rescued` : ''}</dd>
      <dt>Signs of life</dt><dd>${esc(words(t.signs_of_life))}</dd>
      ${t.needs?.length ? `<dt>Needs</dt><dd>${esc(t.needs.map(words).join(', '))}</dd>` : ''}
      <dt>Reports</dt><dd>${t.reports} on ${esc((t.channels || []).map(channel).join(', '))}, hour ${hhmm(t.first_minute)} to ${hhmm(t.last_minute)}</dd>
      ${(t.building || {}).landmark ? `<dt>Landmark</dt><dd lang="tr">${esc(t.building.landmark)}</dd>` : ''}
    </dl>
    ${conflictsHtml(t)}
    ${(t.possible_duplicates || []).length ? `<p>May be the same building as ${t.possible_duplicates.map((d) => `<b>${esc(d.task || d)}</b>`).join(', ')}; not merged until someone confirms.</p>` : ''}
    ${qs.length ? questionsHtml(t.id, qs) : ''}
    ${(t.open_questions || []).length ? `<p><b>Still unknown</b></p><ul class="options">${t.open_questions.slice(0, 5).map((q) => `<li>${esc(q)}</li>`).join('')}</ul>` : ''}
    <p><b>Every report behind this task</b></p>
    ${reports.map((s) => `<div class="step plain"><div>
      <p class="muted">${esc(channel(s.channel))}, hour ${hhmm(s.minute)}</p>
      <p class="quote" lang="tr">${highlight(s.text, ev.get(s.id) || (s.record?.evidence || []).map((e) => e.quote))}</p>
      <p><button class="btn" data-open-step="${esc(s.id)}">Why it was joined</button></p></div></div>`).join('')}
  </div>`;
}

function showTrace() {
  const el = $('#trace');
  const sel = state.selected;
  if (!sel) return;
  if (sel.kind === 'step') {
    const s = state.byId.get(sel.id);
    if (s) el.innerHTML = stepTrace(s);
  } else {
    const t = state.tasks.get(sel.id);
    if (t) el.innerHTML = taskTrace(t);
  }
  el.parentElement.scrollTop = 0;
}

function select(kind, id, { pause = true } = {}) {
  if (pause) stop();
  state.selected = { kind, id };
  showTrace();
  for (const li of document.querySelectorAll('.report')) li.setAttribute('aria-current', String(kind === 'step' && li.dataset.step === id));
  for (const b of document.querySelectorAll('.mark')) b.setAttribute('aria-current', String(kind === 'task' && b.dataset.task === id));
  if (kind === 'task') {
    const t = state.tasks.get(id), w = t && where(t);
    if (w) map.setView([w.lat, w.lon], Math.max(map.getZoom(), 12));
  }
}

// ---------- playback ----------
function apply(s, isNew) {
  state.byId.set(s.id, s);
  $('#feed').append(feedItem(s, isNew));
  if (s.task) putTask(s.task);
  if (s.questions?.length && s.task) state.questions.set(s.task.id, s.questions);
}

function updateClock() {
  const last = state.live.length ? state.live[state.live.length - 1] : state.steps[state.shown - 1];
  $('#clock').textContent = `Hour ${hhmm(last ? last.minute : 0)}, ${state.shown} of ${state.steps.length} reports`;
  const done = state.shown >= state.steps.length;
  const play = $('#play');
  play.textContent = state.timer ? 'Pause' : done ? 'Play again' : state.shown ? 'Resume' : 'Play the first six hours';
  play.setAttribute('aria-pressed', String(!!state.timer));
  $('#jump').hidden = done;
}

function tick() {
  if (state.shown >= state.steps.length) { stop(); return; }
  const s = state.steps[state.shown++];
  apply(s, true);
  const fresh = new Set(s.agent.filter((a) => a.step === 'new').map((a) => a.task));
  drawTasks(fresh);
  drawMap(fresh.size > 0 && state.tasks.size <= 12);
  state.selected = { kind: 'step', id: s.id };
  showTrace();
  updateClock();
}

function play() {
  if (state.shown >= state.steps.length) reset();
  stop();
  state.timer = setInterval(tick, Number($('#speed').value));
  tick();
}
function stop() {
  if (state.timer) clearInterval(state.timer);
  state.timer = null;
  updateClock();
}
function reset() {
  state.shown = 0; state.live = []; state.tasks.clear(); state.owner.clear(); state.byId.clear(); state.questions.clear();
  $('#feed').innerHTML = '';
  drawTasks(); drawMap(false);
}
function jumpToEnd() {
  stop();
  while (state.shown < state.steps.length) apply(state.steps[state.shown++], false);
  drawTasks(); drawMap(true); updateClock();
  const last = state.steps[state.steps.length - 1];
  if (!state.selected) select('step', last.id, { pause: false });
}

// ---------- live reports ----------
const composer = $('#composer');
let tab = 'text', recorder = null, chunks = [], audioBlob = null, recTimer = null;

function setTab(name) {
  tab = name;
  for (const b of composer.querySelectorAll('[role="tab"]')) b.setAttribute('aria-selected', String(b.dataset.tab === name));
  for (const p of composer.querySelectorAll('[data-panel]')) p.hidden = p.dataset.panel !== name;
}

function showError(msg) {
  const e = $('#live-error');
  e.textContent = msg || '';
  e.hidden = !msg;
}

async function startRecording() {
  if (recorder) { recorder.stop(); return; }
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    recorder = new MediaRecorder(stream);
    chunks = [];
    recorder.ondataavailable = (e) => chunks.push(e.data);
    recorder.onstop = () => {
      stream.getTracks().forEach((t) => t.stop());
      audioBlob = new Blob(chunks, { type: recorder.mimeType || 'audio/webm' });
      audioBlob.name = 'note.' + ((recorder.mimeType || '').includes('mp4') ? 'm4a' : 'webm');
      recorder = null; clearTimeout(recTimer);
      $('#rec').textContent = 'Record again'; $('#rec').classList.remove('rec');
      $('#rec-status').textContent = 'Recorded. Send it to the agent.';
    };
    recorder.start();
    $('#rec').textContent = 'Stop recording'; $('#rec').classList.add('rec');
    $('#rec-status').textContent = 'Recording…';
    recTimer = setTimeout(() => recorder && recorder.stop(), 45000);
  } catch {
    showError('The microphone is not available here. Choose an audio file instead.');
  }
}

async function send() {
  showError('');
  const btn = $('#send');
  let req;
  if (tab === 'text') {
    const text = $('#live-text').value.trim();
    if (text.length < 3) { showError('Write the help request first.'); return; }
    req = fetch('/api/live/message', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text, channel: 'form' }) });
  } else {
    if (!audioBlob) { showError('Record a voice note or choose an audio file first.'); return; }
    const fd = new FormData();
    fd.append('audio', audioBlob, audioBlob.name || 'note.webm');
    req = fetch('/api/live/voice', { method: 'POST', body: fd });
  }
  btn.disabled = true; btn.textContent = 'The agent is working…';
  jumpToEnd();
  composer.close();
  $('#trace').innerHTML = `<div class="empty"><p class="lede">Reading your report, checking the address and comparing it
    with every open building. This takes about 10 to 20 seconds on Meta's models.</p></div>`;
  try {
    const r = await req;
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.detail || `The agent could not process this report (HTTP ${r.status}).`);
    state.live.push(data);
    apply(data, true);
    const fresh = new Set((data.agent || []).filter((a) => a.step === 'new').map((a) => a.task));
    drawTasks(fresh); drawMap(false);
    select('step', data.id, { pause: false });
    $('#live-text').value = ''; audioBlob = null; $('#rec-status').textContent = '';
    updateClock();
  } catch (e) {
    $('#trace').innerHTML = `<div class="empty"><p class="error">${esc(e.message)}</p>
      <p><button class="btn" id="retry">Edit and try again</button></p></div>`;
    $('#retry').onclick = () => composer.showModal();
  } finally {
    btn.disabled = false; btn.textContent = 'Send to the agent';
  }
}

// ---------- wiring ----------
function wire() {
  $('#play').onclick = () => (state.timer ? stop() : play());
  $('#jump').onclick = jumpToEnd;
  $('#speed').onchange = () => { if (state.timer) play(); };
  $('#add').onclick = () => { showError(''); composer.showModal(); };
  $('#send').onclick = send;
  $('#rec').onclick = startRecording;
  $('#audio-file').onchange = (e) => {
    const f = e.target.files[0];
    if (f) { audioBlob = f; $('#rec-status').textContent = `${f.name} chosen.`; }
  };
  for (const b of composer.querySelectorAll('[role="tab"]')) b.onclick = () => setTab(b.dataset.tab);
  for (const c of composer.querySelectorAll('[data-example]')) c.onclick = () => { $('#live-text').value = c.dataset.example; $('#live-text').focus(); };

  $('#feed').addEventListener('click', (e) => { const li = e.target.closest('.report'); if (li) select('step', li.dataset.step); });
  $('#feed').addEventListener('keydown', (e) => { const li = e.target.closest('.report'); if (li && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); select('step', li.dataset.step); } });
  $('#tasks').addEventListener('click', (e) => { const b = e.target.closest('.mark'); if (b) select('task', b.dataset.task); });
  $('#trace').addEventListener('click', (e) => {
    const t = e.target.closest('[data-open-task]'); if (t) return select('task', t.dataset.openTask);
    const s = e.target.closest('[data-open-step]'); if (s) return select('step', s.dataset.openStep);
    const a = e.target.closest('[data-approve]');
    if (a) {
      a.disabled = true; a.textContent = 'Approved';
      const note = document.getElementById(`approved-${a.dataset.approve}`);
      if (note) note.textContent = 'In a real deployment these would now go back on the channel the report came from. This demo sends nothing.';
    }
  });
}

async function main() {
  initMap();
  wire();
  try {
    const r = await fetch('/api/replay');
    const d = await r.json();
    state.steps = d.steps;
    state.coords = Object.fromEntries(Object.entries(d.coords).filter(([, v]) => v));
  } catch {
    $('#trace').innerHTML = '<div class="empty"><p class="error">The recorded scenario did not load. Reload the page to try again.</p></div>';
    return;
  }
  updateClock();
  drawTasks();
}

main();
