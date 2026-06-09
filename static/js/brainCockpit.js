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
      ${buildable ? `<button class="cockpit-build-btn" data-proj="${esc(pr.name)}" title="Ask the chat to build in this project">⚒ Build…</button>` : ''}
    </div>`;
  }).join('');
  // "Build…" → prefill the chat with a build request for that project (the chat's
  // brain_build/brain_deep_build tools do the work). No direct spend from here.
  box.querySelectorAll('.cockpit-build-btn').forEach((b) => {
    b.onclick = () => prefillChat(`Use the brain to build in the "${b.dataset.proj}" project: `);
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
  el('cockpit-status') && (el('cockpit-status').innerHTML = '<div class="cockpit-loading">Connecting to your brain…</div>');
  const [status, fleet, projects, auto, recent, jobs] = await Promise.all([
    jget('/api/brain/status'), jget('/api/brain/usage'), jget('/api/brain/projects'),
    jget('/api/brain/auto-jobs?limit=8'),
    jget('/api/brain/memory/recent?limit=12'), jget('/api/brain/deep-jobs?limit=8'),
  ]);
  loaded = true; loading = false;
  renderStatus(status); renderFleet(fleet); renderProjects(projects); renderAuto(auto); renderRecent(recent); renderBuilds(jobs);
}

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

document.addEventListener('DOMContentLoaded', () => {
  const tab = document.querySelector('.memory-tab[data-memory-tab="cockpit"]');
  if (tab) tab.addEventListener('click', () => setTimeout(() => render(false), 30));
  const rf = el('cockpit-refresh');
  if (rf) rf.onclick = () => render(true);
  const sg = el('cockpit-suggest-btn');
  if (sg) sg.onclick = suggest;
});

const brainCockpit = { render };
export default brainCockpit;
window.brainCockpit = brainCockpit;
