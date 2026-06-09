// brainCockpit.js — a Brain "Cockpit" dashboard tab: at-a-glance view of the
// powerhouse — which AI subscriptions/engines are online, the build/automate
// targets (projects), and the brain's recent memory activity. Read-only via the
// body's /api/brain proxy; degrades gracefully when the brain is offline.
// Self-contained: wires its own tab trigger so memory.js stays untouched.

const API = window.location.origin;
let loaded = false, loading = false;

async function jget(path) {
  try { const r = await fetch(API + path, { cache: 'no-store' }); return await r.json(); }
  catch (e) { return { ok: false, code: 'NET', error: String(e) }; }
}
async function jpost(path, body) {
  try { const r = await fetch(API + path, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body || {}) }); return await r.json(); }
  catch (e) { return { ok: false, error: String(e) }; }
}
function esc(s) { return String(s == null ? '' : s).replace(/[&<>"]/g, (m) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[m])); }
function el(id) { return document.getElementById(id); }

const ENGINE_LABEL = {
  'claude-code': 'Claude', 'codex-cli': 'Codex', 'gemini-cli': 'Gemini', 'ollama': 'Ollama',
};
const SUBS = ['claude-code', 'codex-cli', 'gemini-cli'];

function renderStatus(s) {
  const box = el('cockpit-status'); if (!box) return;
  if (!s || s.ok === false || s.up === false) {
    box.innerHTML = `<div class="cockpit-offline">🧠 Brain offline — start it on your host (<code>npm run bertos:host</code>) to power Council / Deep Build / build.</div>`;
    return;
  }
  const engines = s.engines || [];
  const find = (id) => engines.find((e) => e.id === id);
  const subChips = SUBS.map((id) => {
    const e = find(id); const on = e && e.online;
    return `<span class="cockpit-chip ${on ? 'on' : 'off'}"><span class="cockpit-dot"></span>${esc(ENGINE_LABEL[id] || id)}</span>`;
  }).join('');
  const ollamaOn = (find('ollama') || {}).online;
  const freeCount = engines.filter((e) => e.online && !e.paid && !e.disabled).length;
  box.innerHTML = `
    <div class="cockpit-status-head">
      <span class="cockpit-pulse"></span>
      <strong>Brain online</strong>
      <span class="cockpit-sub">${engines.filter((e) => e.online).length} engines live</span>
    </div>
    <div class="cockpit-chips">
      <span class="cockpit-grouplabel">Subscriptions</span>${subChips}
      <span class="cockpit-chip ${ollamaOn ? 'on' : 'off'} local"><span class="cockpit-dot"></span>Ollama</span>
      <span class="cockpit-chip free">+${freeCount} free</span>
    </div>`;
}

const STATUS_COLOR = { active: '#3fb1a6', paused: '#f0b429', idea: '#5b8cff', archived: '#9aa4b2' };
function renderProjects(p) {
  const box = el('cockpit-projects'); if (!box) return;
  if (!p || p.ok === false) { box.innerHTML = `<div class="cockpit-empty">Projects unavailable.</div>`; return; }
  const projects = (p.data || []).slice();
  if (!projects.length) { box.innerHTML = `<div class="cockpit-empty">No projects yet. Ask the chat to register one (brain_project_create).</div>`; return; }
  projects.sort((a, b) => (a.status === 'active' ? -1 : 0) - (b.status === 'active' ? -1 : 0));
  box.innerHTML = projects.slice(0, 24).map((pr) => {
    const c = STATUS_COLOR[pr.status] || '#9aa4b2';
    const buildable = !!pr.localPath;
    return `<div class="cockpit-proj">
      <div class="cockpit-proj-top"><span class="cockpit-proj-dot" style="background:${c}"></span>
        <span class="cockpit-proj-name">${esc(pr.name)}</span>
        <span class="cockpit-proj-status" style="color:${c}">${esc(pr.status || '')}</span></div>
      ${pr.localPath ? `<div class="cockpit-proj-path" title="${esc(pr.localPath)}">${esc(pr.localPath)}</div>` : `<div class="cockpit-proj-path dim">no local path</div>`}
      <div class="cockpit-proj-actions">
        ${buildable ? `<button class="cockpit-build-btn" data-proj="${esc(pr.name)}" title="Ask the chat to build in this project">⚒ Build…</button>` : ''}
        ${pr.id ? `<button class="cockpit-map-btn" data-pid="${esc(pr.id)}" data-pname="${esc(pr.name)}" title="See this project's memory subgraph">🕸 Map</button>` : ''}
      </div>
    </div>`;
  }).join('');
  // "Build…" → prefill the chat with a build request for that project (the chat's
  // brain_build/brain_deep_build tools do the work). No direct spend from here.
  box.querySelectorAll('.cockpit-build-btn').forEach((b) => {
    b.onclick = () => prefillChat(`Use the brain to build in the "${b.dataset.proj}" project: `);
  });
  // "Map" → jump to the Memory Tree tab, scoped to this project's subgraph.
  box.querySelectorAll('.cockpit-map-btn').forEach((b) => {
    b.onclick = () => {
      document.querySelector('.memory-tab[data-memory-tab="tree"]')?.click();
      setTimeout(() => { window.memoryTree?.scopeToProject?.(b.dataset.pid, b.dataset.pname); }, 120);
    };
  });
}

const KIND_ICON = { decision: '◆', fact: '•', artifact: '▣', plan: '◇', note: '·' };
function renderRecent(r) {
  const box = el('cockpit-recent'); if (!box) return;
  if (!r || r.ok === false) { box.innerHTML = `<div class="cockpit-empty">Recent activity unavailable${r && r.code === 'BRAIN_ERROR' ? ' (update the brain to enable)' : ''}.</div>`; return; }
  const notes = (r.data || {}).notes || [];
  if (!notes.length) { box.innerHTML = `<div class="cockpit-empty">No recent notes.</div>`; return; }
  box.innerHTML = notes.map((n) => {
    const when = n.ts ? timeAgo(new Date(n.ts)) : '';
    return `<div class="cockpit-note">
      <span class="cockpit-note-kind" title="${esc(n.kind || '')}">${KIND_ICON[n.kind] || '·'}</span>
      <span class="cockpit-note-text">${esc((n.preview || '').slice(0, 130))}</span>
      <span class="cockpit-note-meta">${esc(n.source || '')}${when ? ' · ' + when : ''}</span>
    </div>`;
  }).join('');
}
function timeAgo(d) {
  const s = (Date.now() - d.getTime()) / 1000;
  if (s < 60) return 'just now'; if (s < 3600) return Math.floor(s / 60) + 'm ago';
  if (s < 86400) return Math.floor(s / 3600) + 'h ago'; return Math.floor(s / 86400) + 'd ago';
}

function prefillChat(text) {
  // Close the Brain modal and drop the text into the chat input.
  document.getElementById('close-memory-modal')?.click();
  const input = document.getElementById('message');
  if (input) { input.value = text; input.focus(); input.dispatchEvent(new Event('input', { bubbles: true })); }
}

async function render(force) {
  const panel = document.querySelector('.memory-tab-panel[data-memory-panel="cockpit"]');
  if (!panel) return;
  if (loaded && !force) return;
  if (loading) return;
  loading = true;
  const body = el('cockpit-body');
  if (body && !loaded) body.classList.remove('cockpit-hidden');
  // Only show the "Connecting…" flicker on the very first load — not on the
  // 5s live auto-refresh.
  if (!loaded) el('cockpit-status') && (el('cockpit-status').innerHTML = '<div class="cockpit-loading">Connecting to your brain…</div>');
  const [status, fleet, projects, auto, recent, jobs, limits] = await Promise.all([
    jget('/api/brain/status'), jget('/api/brain/usage'), jget('/api/brain/projects'),
    jget('/api/brain/auto-jobs?limit=8'),
    jget('/api/brain/memory/recent?limit=12'), jget('/api/brain/deep-jobs?limit=12'),
    jget('/api/brain/limits'),
  ]);
  loaded = true; loading = false;
  renderNow(jobs, auto); renderLauncher(projects); renderLimits(limits);
  renderStatus(status); renderFleet(fleet); renderProjects(projects); renderAuto(auto); renderRecent(recent); renderBuilds(jobs);
}

// ── Build launcher — fire a Deep Build on a project right from Mission Control.
// The click + confirm IS the approval gate (Deep Build patches + commits + may
// spend a subscription), so it never fires unattended. Shows live in Now Running.
let _launchProj = null; // remember the chosen project across refreshes
function renderLauncher(projectsRes) {
  const box = el('cockpit-launcher'); if (!box) return;
  const projects = (projectsRes && projectsRes.ok !== false) ? (projectsRes.data || []) : [];
  const buildable = projects.filter((p) => p.localPath && p.id);
  if (!buildable.length) { box.innerHTML = ''; return; }
  // Don't blow away an in-progress typed objective on a 5s auto-refresh.
  const existingObj = el('cockpit-launch-obj');
  if (existingObj && document.activeElement === existingObj) return;
  const typed = existingObj ? existingObj.value : '';
  const sel = el('cockpit-launch-proj');
  if (sel) _launchProj = sel.value;
  const opts = buildable.map((p) => `<option value="${esc(p.id)}"${_launchProj === p.id ? ' selected' : ''}>${esc(p.name)}</option>`).join('');
  box.innerHTML = `
    <div class="cockpit-launch-head">⚒ Launch a build</div>
    <select id="cockpit-launch-proj" class="cockpit-launch-select">${opts}</select>
    <textarea id="cockpit-launch-obj" class="cockpit-launch-obj" rows="2" placeholder="What should Bert build or fix? e.g. “add a dark-mode toggle, then commit”">${esc(typed)}</textarea>
    <div class="cockpit-launch-actions">
      <button id="cockpit-launch-fire" class="cockpit-launch-fire">🚀 Build it</button>
      <button id="cockpit-notify-test" class="cockpit-notify-test" title="Send a test push to your phone">🔔 Test ping</button>
      <span class="cockpit-launch-status" id="cockpit-launch-status"></span>
    </div>
    <div class="cockpit-launch-hint">Fire it and walk away — Bert texts your phone when the build is done.</div>`;
  const fire = el('cockpit-launch-fire');
  if (fire) fire.onclick = fireBuild;
  const nt = el('cockpit-notify-test');
  if (nt) nt.onclick = async () => {
    const status = el('cockpit-launch-status');
    nt.disabled = true; nt.textContent = '🔔 Sending…';
    const r = await jpost('/api/brain/notify-test', {});
    nt.disabled = false; nt.textContent = '🔔 Test ping';
    if (status) status.textContent = (r && r.ok) ? '✓ Sent — check your phone.' : 'Push channel not set up yet.';
  };
}
async function fireBuild() {
  const proj = el('cockpit-launch-proj'); const obj = el('cockpit-launch-obj');
  const status = el('cockpit-launch-status'); const fire = el('cockpit-launch-fire');
  const objective = (obj && obj.value || '').trim();
  const projectId = proj && proj.value;
  const projName = (proj && proj.options[proj.selectedIndex] && proj.options[proj.selectedIndex].text) || 'this project';
  if (!objective) { if (status) status.textContent = 'Type what to build first.'; obj && obj.focus(); return; }
  if (!window.confirm(`Build in “${projName}”:\n\n“${objective}”\n\nBert will edit + commit code using your subscriptions. Start it?`)) return;
  if (fire) { fire.disabled = true; fire.textContent = '🚀 Starting…'; }
  if (status) status.textContent = '';
  const r = await jpost('/api/brain/build', { objective, projectId });
  if (fire) { fire.disabled = false; fire.textContent = '🚀 Build it'; }
  if (r && r.ok && r.started) {
    if (status) status.textContent = '✓ Started — watch it in “Now Running” above.';
    if (obj) obj.value = '';
    setTimeout(() => render(true), 800);
  } else {
    if (status) status.textContent = (r && r.error) || 'Could not start the build.';
  }
}

// ── "Now Running" hero — everything happening across the brain this second:
// active Deep Builds + Auto loops, with live elapsed time. The heartbeat of
// Mission Control. ──
function elapsedSince(ts) {
  const d = new Date(ts); if (isNaN(d)) return '';
  let s = Math.max(0, Math.floor((Date.now() - d.getTime()) / 1000));
  if (s < 60) return s + 's';
  const m = Math.floor(s / 60); const r = s % 60;
  if (m < 60) return m + 'm ' + r + 's';
  const h = Math.floor(m / 60); return h + 'h ' + (m % 60) + 'm';
}
function renderNow(jobsRes, autoRes) {
  const box = el('cockpit-now'); if (!box) return;
  const deep = (jobsRes && jobsRes.ok !== false) ? (jobsRes.data || []) : [];
  const auto = (autoRes && autoRes.ok !== false) ? (autoRes.data || []) : [];
  const items = [];
  deep.filter((j) => j.status === 'running').forEach((j) => items.push({
    kind: 'Deep Build', obj: j.objective, since: j.startedAt || j.updatedAt,
    meta: `round ${j.rounds || 1}${typeof j.committed === 'number' ? ' · ' + j.committed + ' committed' : ''}`,
  }));
  auto.filter((j) => j.status === 'running' || j.status === 'queued').forEach((j) => items.push({
    kind: 'Auto loop', obj: j.objective, since: j.startedAt || j.createdAt, meta: j.status,
  }));
  if (!items.length) {
    const last = deep.find((j) => j.status === 'done') || deep[0];
    box.classList.remove('active');
    box.innerHTML = `<div class="cockpit-now-idle"><span class="cockpit-now-idle-dot"></span>All quiet — nothing building right now.${last ? `<span class="cockpit-now-last">last: ${esc((last.objective || '').slice(0, 56))}</span>` : ''}</div>`;
    return;
  }
  box.classList.add('active');
  box.innerHTML = `<div class="cockpit-now-head"><span class="cockpit-now-pulse"></span>${items.length} running now</div>` +
    items.map((it) => `<div class="cockpit-now-row">
      <span class="cockpit-now-kind">${esc(it.kind)}</span>
      <span class="cockpit-now-obj">${esc((it.obj || '').slice(0, 88))}</span>
      <span class="cockpit-now-meta">${esc(it.meta || '')} · ⏱ ${elapsedSince(it.since)}</span>
    </div>`).join('');
}

// ── Limits — daily soft-limit usage, active model, and the auto role chain.
// "seeing my limits" at a glance. ──
function renderLimits(r) {
  const box = el('cockpit-limits'); if (!box) return;
  if (!r || r.ok === false) { box.innerHTML = ''; return; }
  const s = (r.data || {}).summary || {};
  const pct = Math.max(0, Math.min(100, s.globalPercentUsed || 0));
  const state = s.globalState || 'ok';
  const col = state === 'ok' ? '#3fd17a' : (state === 'warn' ? '#f0b429' : '#ff7a6b');
  const chain = [s.autoPlannerId, s.autoCoderId, s.autoCheckerId].filter(Boolean).map((id) => ENGINE_LABEL[id] || id);
  const warn = (s.warnings || [])[0];
  box.innerHTML = `
    <div class="cockpit-limits-head">
      <span><strong>${s.totalCalls24h ?? 0}</strong> / ${s.globalDailySoftLimit ?? '∞'} calls today</span>
      <span class="cockpit-limits-state" style="color:${col}">● ${esc(state)}</span>
    </div>
    <div class="cockpit-limits-bar"><span style="width:${pct}%;background:${col}"></span></div>
    <div class="cockpit-limits-meta">${s.activeModel ? `active: <b>${esc(ENGINE_LABEL[s.activeProviderId] || s.activeProviderId || '')}</b> · ${esc(String(s.activeModel).slice(0, 26))}` : ''}${chain.length ? ` &nbsp;·&nbsp; auto: ${chain.map(esc).join(' → ')}` : ''}</div>
    ${warn ? `<div class="cockpit-limits-warn">⚠ ${esc(String(warn).slice(0, 110))}</div>` : ''}`;
}

// ── Live auto-refresh: poll every 5s while the Cockpit is actually on-screen. ──
let _refreshTimer = null;
function cockpitVisible() {
  const panel = document.querySelector('.memory-tab-panel[data-memory-panel="cockpit"]');
  const modal = document.getElementById('memory-modal');
  return !!(panel && modal && !modal.classList.contains('hidden') && panel.offsetParent !== null);
}
function startLive() {
  if (_refreshTimer) return;
  _refreshTimer = setInterval(() => {
    if (document.hidden) return;
    if (!cockpitVisible()) { stopLive(); return; }
    render(true);
  }, 5000);
}
function stopLive() { if (_refreshTimer) { clearInterval(_refreshTimer); _refreshTimer = null; } }

// ── Auto Mode: the self-scheduling night loop. Each row is a per-project
// build loop with its planner/coder/checker engines + live status. ──
const AUTO_STATUS = { running: '#3fd17a', queued: '#5b8cff', stopped: '#9aa4b2', paused: '#f0b429', error: '#ff7a6b', done: '#3fd17a' };
function renderAuto(a) {
  const box = el('cockpit-auto'); if (!box) return;
  if (!a || a.ok === false) {
    box.innerHTML = `<div class="cockpit-empty">Auto Mode unavailable${a && a.code === 'BRAIN_ERROR' ? ' (update the brain to enable)' : ''}.</div>`;
    return;
  }
  const jobs = (a.data || []).slice(0, 8);
  if (!jobs.length) { box.innerHTML = `<div class="cockpit-empty">No Auto Mode loops yet — the night loop hasn't been armed.</div>`; return; }
  box.innerHTML = jobs.map((j) => {
    const c = AUTO_STATUS[j.status] || '#9aa4b2';
    const live = j.status === 'running' || j.status === 'queued';
    const roles = j.roles || {};
    const chain = ['plannerId', 'coderId', 'checkerId'].map((k) => roles[k]).filter(Boolean)
      .map((id) => ENGINE_LABEL[id] || id);
    const tasks = Array.isArray(j.taskQueue) ? j.taskQueue.length : 0;
    return `<div class="cockpit-auto-row">
      <span class="cockpit-auto-dot${live ? ' live' : ''}" style="background:${c}"></span>
      <span class="cockpit-auto-main">
        <span class="cockpit-auto-obj">${esc((j.objective || '').slice(0, 96))}</span>
        ${chain.length ? `<span class="cockpit-auto-chain">${chain.map(esc).join('<span class="cockpit-auto-arr">→</span>')}</span>` : ''}
      </span>
      <span class="cockpit-auto-meta">${esc(j.status || '')}${tasks ? ' · ' + tasks + ' task' + (tasks !== 1 ? 's' : '') : ''}</span>
    </div>`;
  }).join('');
}

// ── Fleet: how the orchestrator routes work across engines, and the
// free-vs-paid token split (the proof it keeps spend cheap). ──
function fmtTokens(t) {
  t = Number(t) || 0;
  if (t >= 1e6) return (t / 1e6).toFixed(t >= 1e7 ? 0 : 1) + 'M';
  if (t >= 1e3) return Math.round(t / 1e3) + 'k';
  return String(t);
}
function renderFleet(f) {
  const box = el('cockpit-fleet'); if (!box) return;
  if (!f || f.ok === false) {
    box.innerHTML = `<div class="cockpit-empty">Usage unavailable${f && f.code === 'BRAIN_ERROR' ? ' (update the brain to enable)' : ''}.</div>`;
    return;
  }
  const d = f.data || {};
  const models = (d.models || []).slice().sort((a, b) => (b.tokens || 0) - (a.tokens || 0));
  const total = d.totalTokens || models.reduce((s, m) => s + (m.tokens || 0), 0) || 1;
  const freePct = typeof d.freeSharePct === 'number' ? d.freeSharePct
    : typeof d.freeShare === 'number' ? d.freeShare
    : Math.round(100 * (1 - (d.paidTokens || 0) / total));
  const paidPct = Math.max(0, 100 - freePct);
  const top = models.slice(0, 6);
  const maxTok = top.length ? (top[0].tokens || 1) : 1;
  const bars = top.map((m) => {
    const w = Math.max(2, Math.round(100 * (m.tokens || 0) / maxTok));
    const cls = m.paid ? 'paid' : 'free';
    return `<div class="cockpit-fleet-row">
      <span class="cockpit-fleet-name">${esc(ENGINE_LABEL[m.id] || m.id)}${m.paid ? '<span class="cockpit-fleet-tag">paid</span>' : ''}</span>
      <span class="cockpit-fleet-track"><span class="cockpit-fleet-fill ${cls}" style="width:${w}%"></span></span>
      <span class="cockpit-fleet-num">${fmtTokens(m.tokens)}<span class="cockpit-fleet-calls">${m.calls || 0}×</span></span>
    </div>`;
  }).join('');
  box.innerHTML = `
    <div class="cockpit-fleet-head">
      <div class="cockpit-fleet-hero"><span class="cockpit-fleet-hero-num">${freePct}%</span> of work runs <strong>free</strong></div>
      <div class="cockpit-fleet-tot">${fmtTokens(total)} tokens · ${(d.models || []).length} engines</div>
    </div>
    <div class="cockpit-split" title="${freePct}% free · ${paidPct}% paid">
      <span class="cockpit-split-free" style="width:${freePct}%"></span>
      <span class="cockpit-split-paid" style="width:${paidPct}%"></span>
    </div>
    <div class="cockpit-split-legend"><span><span class="cockpit-split-key free"></span>free ${freePct}%</span><span><span class="cockpit-split-key paid"></span>paid ${paidPct}%${d.claudeTokens ? ' · Claude ' + fmtTokens(d.claudeTokens) : ''}</span></div>
    <div class="cockpit-fleet-bars">${bars}</div>`;
}

const JOB_STATUS = { running: '#f0b429', done: '#3fd17a', error: '#ff7a6b', interrupted: '#9aa4b2' };
function renderBuilds(j) {
  const box = el('cockpit-builds'); if (!box) return;
  if (!j || j.ok === false) { box.innerHTML = `<div class="cockpit-empty">No build history${j && j.code === 'BRAIN_ERROR' ? ' (update the brain to enable Deep Build)' : ''}.</div>`; return; }
  const runs = j.data || [];
  if (!runs.length) { box.innerHTML = `<div class="cockpit-empty">No Deep Build runs yet — ask the chat: “deep build …”.</div>`; return; }
  box.innerHTML = runs.slice(0, 8).map((r) => {
    const c = JOB_STATUS[r.status] || '#9aa4b2';
    return `<div class="cockpit-note"><span class="cockpit-note-kind" style="color:${c}">●</span>
      <span class="cockpit-note-text">${esc((r.objective || '').slice(0, 120))}</span>
      <span class="cockpit-note-meta">${esc(r.status || '')}${typeof r.committed === 'number' ? ' · ' + r.committed + ' committed' : ''}</span></div>`;
  }).join('');
}

async function suggest() {
  const box = el('cockpit-recommend'), btn = el('cockpit-suggest-btn');
  if (!box) return;
  box.innerHTML = `<div class="cockpit-loading">Thinking about what to automate… (runs a free model, ~30s)</div>`;
  if (btn) { btn.disabled = true; btn.textContent = '✨ Thinking…'; }
  const r = await jget('/api/brain/recommend');
  if (btn) { btn.disabled = false; btn.textContent = '✨ Suggest automations'; }
  if (!r || r.ok === false) { box.innerHTML = `<div class="cockpit-empty">Couldn’t get recommendations${r && r.code === 'BRAIN_ERROR' ? ' (update the brain to enable)' : ''}.</div>`; return; }
  const recs = (r.data || {}).recommendations || [];
  if (!recs.length) { box.innerHTML = `<div class="cockpit-empty">No recommendations right now.</div>`; return; }
  box.innerHTML = recs.map((x) => `<div class="cockpit-rec">
    <div class="cockpit-rec-top"><span class="cockpit-rec-type">${esc(x.type || '')}</span>${x.projectName ? `<span class="cockpit-rec-proj">${esc(x.projectName)}</span>` : ''}<span class="cockpit-rec-impact i-${esc(x.impact || 'medium')}">${esc(x.impact || '')}</span></div>
    <div class="cockpit-rec-title">${esc(x.title || '')}</div>
    <div class="cockpit-rec-why">${esc((x.why || '').slice(0, 170))}</div>
    <button class="cockpit-build-btn" data-obj="${esc(x.objective || '')}">▶ Set up</button></div>`).join('');
  box.querySelectorAll('.cockpit-build-btn[data-obj]').forEach((b) => { b.onclick = () => prefillChat(`Automate this with the brain: ${b.dataset.obj}`); });
}

// ── Level Up — score the OS across the Four Cs + rank what to build next.
// The research's compounding self-improvement loop, on demand. ──
async function runAudit() {
  const box = el('cockpit-audit'), btn = el('cockpit-audit-btn');
  if (!box) return;
  box.innerHTML = `<div class="cockpit-loading">Auditing your OS across the Four Cs… (free model, ~30s)</div>`;
  if (btn) { btn.disabled = true; btn.textContent = '📊 Auditing…'; }
  const r = await jget('/api/brain/audit');
  if (btn) { btn.disabled = false; btn.textContent = '📊 Re-run self-audit'; }
  if (!r || r.ok === false) { box.innerHTML = `<div class="cockpit-empty">Couldn't run the audit${r && r.code === 'BRAIN_ERROR' ? ' (update the brain)' : ''}.</div>`; return; }
  const sc = r.scores;
  const cs = [['Context', 'context'], ['Connections', 'connections'], ['Capabilities', 'capabilities'], ['Cadence', 'cadence']];
  const bar = (label, key) => {
    const v = sc && typeof sc[key] === 'number' ? Math.max(0, Math.min(100, sc[key])) : 0;
    const col = v >= 75 ? '#3fd17a' : v >= 50 ? '#5884FF' : v >= 30 ? '#f0b429' : '#ff7a6b';
    return `<div class="cockpit-audit-c"><span class="cockpit-audit-clabel">${label}</span><span class="cockpit-audit-ctrack"><span class="cockpit-audit-cfill" style="width:${v}%;background:${col}"></span></span><span class="cockpit-audit-cval">${v}</span></div>`;
  };
  box.innerHTML = `
    ${sc ? `<div class="cockpit-audit-overall"><span class="cockpit-audit-score">${sc.overall ?? '—'}</span><span class="cockpit-audit-headline">${esc(r.headline || '')}</span></div>
    <div class="cockpit-audit-bars">${cs.map(([l, k]) => bar(l, k)).join('')}</div>` : `<div class="cockpit-empty">${esc(r.headline || r.note || 'No scorecard available.')}</div>`}
    ${(r.gaps || []).length ? `<div class="cockpit-audit-gaps-label">What to build next</div>` + r.gaps.map((g, i) => `
      <div class="cockpit-audit-gap">
        <div class="cockpit-audit-gap-top"><span class="cockpit-audit-gap-rank">${i + 1}</span><span class="cockpit-audit-gap-title">${esc(g.title || '')}</span><span class="cockpit-rec-impact i-${esc(g.impact || 'medium')}">${esc(g.impact || '')}</span></div>
        ${g.why ? `<div class="cockpit-audit-gap-why">${esc(String(g.why).slice(0, 150))}</div>` : ''}
        ${g.action ? `<button class="cockpit-build-btn" data-act="${esc(g.action)}">▶ ${esc(String(g.action).slice(0, 62))}</button>` : ''}
      </div>`).join('') : ''}
    ${r.model ? `<div class="cockpit-audit-foot">scored by ${esc(String(r.model).slice(0, 22))}</div>` : ''}`;
  box.querySelectorAll('.cockpit-build-btn[data-act]').forEach((b) => { b.onclick = () => prefillChat(`Let's level up Bert's AI — help me with: ${b.dataset.act}`); });
}

document.addEventListener('DOMContentLoaded', () => {
  const tab = document.querySelector('.memory-tab[data-memory-tab="cockpit"]');
  if (tab) tab.addEventListener('click', () => setTimeout(() => { render(false); startLive(); }, 30));
  const rf = el('cockpit-refresh');
  if (rf) rf.onclick = () => render(true);
  const sg = el('cockpit-suggest-btn');
  if (sg) sg.onclick = suggest;
  const ab = el('cockpit-audit-btn');
  if (ab) ab.onclick = runAudit;
  // Stop the live poll when the Brain modal closes.
  document.getElementById('close-memory-modal')?.addEventListener('click', stopLive);
});

const brainCockpit = { render };
export default brainCockpit;
window.brainCockpit = brainCockpit;
