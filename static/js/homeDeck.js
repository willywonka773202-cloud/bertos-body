// homeDeck.js — the ambient "command center" that greets you on the BertOS
// welcome screen. It turns the empty new-chat state into a glanceable Jarvis
// deck: a time-aware greeting, the brain's live pulse (engines online, builds
// shipped today, new memories today), a scrolling activity ticker, quick-launch
// tiles into every panel, and an on-demand "what should I work on?" pass.
//
// Everything binds to the body's read-only /api/brain proxy (already verified)
// and degrades gracefully: brain offline → the deck still shows the greeting +
// launch tiles, with a soft "wake your brain" note instead of live stats.
// Self-contained — injects its own DOM into #welcome-screen and wires its own
// refresh, so memory.js / app.js stay untouched.

import bertOrb from './bertOrb.js';

const API = window.location.origin;
const USER_NAME = 'Will'; // BertOS is Will's personal OS
let injected = false;
let lastLoad = 0;
let loadingPulse = false;

function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"]/g, (m) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[m]));
}
async function jget(path, timeoutMs) {
  const ctl = new AbortController();
  const t = setTimeout(() => ctl.abort(), timeoutMs || 12000);
  try {
    const r = await fetch(API + path, { cache: 'no-store', signal: ctl.signal });
    return await r.json();
  } catch (e) {
    return { ok: false, code: 'NET', error: String(e) };
  } finally { clearTimeout(t); }
}

function greeting() {
  const h = new Date().getHours();
  if (h < 5) return 'Still up';
  if (h < 12) return 'Good morning';
  if (h < 17) return 'Good afternoon';
  if (h < 22) return 'Good evening';
  return 'Working late';
}
// Bert's opening line — personal, time-aware, a touch of character.
function bertLine() {
  const h = new Date().getHours();
  if (h < 5) return `Late one, ${USER_NAME}? I never sleep — what are we building?`;
  if (h < 12) return `Bert here — tap the orb and tell me what you need.`;
  if (h < 17) return `Bert here — what are we working on?`;
  if (h < 22) return `Bert here — I've been keeping things warm.`;
  return `Bert here — winding down, or one more thing?`;
}

// Open the Brain modal and switch to a given tab (tree | cockpit | …).
function openBrainTab(tab) {
  document.getElementById('tool-memory-btn')?.click();
  setTimeout(() => {
    const t = document.querySelector(`.memory-tab[data-memory-tab="${tab}"]`);
    if (t) t.click();
  }, 60);
}
function clickIf(id) { const e = document.getElementById(id); if (e) e.click(); }

function isToday(ts) {
  if (!ts) return false;
  const d = new Date(ts);
  if (isNaN(d)) return false;
  const n = new Date();
  return d.getFullYear() === n.getFullYear() && d.getMonth() === n.getMonth() && d.getDate() === n.getDate();
}

const KIND_ICON = { decision: '◆', fact: '•', artifact: '▣', plan: '◇', note: '·' };

function shell() {
  // The deck markup. Injected once, right after the welcome sub-line.
  return `
  <div class="deck-greeting">
    <div class="deck-hello-line"><span id="deck-hello">${esc(greeting())}, ${esc(USER_NAME)}.</span></div>
    <div class="deck-bert-line" id="deck-bert-line">${esc(bertLine())}</div>
    <div class="deck-context" id="deck-context"></div>
  </div>

  <div class="deck-pulse" id="deck-pulse">
    <div class="deck-card deck-card-brain" data-go="cockpit" title="Open the Cockpit">
      <div class="deck-card-icon">🧠</div>
      <div class="deck-card-val" id="deck-brain-val">…</div>
      <div class="deck-card-label">Brain</div>
    </div>
    <div class="deck-card" data-go="builds" title="See what your AI built">
      <div class="deck-card-icon">⚒</div>
      <div class="deck-card-val" id="deck-builds-val">·</div>
      <div class="deck-card-label">Built today</div>
    </div>
    <div class="deck-card" data-go="tree" title="Open the Memory Tree">
      <div class="deck-card-icon">✦</div>
      <div class="deck-card-val" id="deck-mem-val">·</div>
      <div class="deck-card-label">Memories</div>
    </div>
    <div class="deck-card deck-card-map" data-go="tree" title="Open the Memory Tree">
      <div class="deck-card-icon">🕸</div>
      <div class="deck-card-val deck-card-val-sm">Memory&nbsp;Tree</div>
      <div class="deck-card-label">explore the graph</div>
    </div>
  </div>

  <div class="deck-day" id="deck-day" style="display:none">
    <button class="deck-day-seg" data-act="calendar" title="Open your calendar">
      <span class="deck-day-i">📅</span><span id="deck-day-cal">…</span>
    </button>
    <button class="deck-day-seg" data-act="email" title="Open your inbox">
      <span class="deck-day-i">✉</span><span id="deck-day-mail">…</span>
    </button>
  </div>

  <div class="deck-digest" id="deck-digest">
    <button class="deck-digest-btn" id="deck-digest-btn">📥 Summarize my inbox</button>
    <div class="deck-digest-out" id="deck-digest-out"></div>
  </div>

  <div class="deck-ticker-wrap" id="deck-ticker-wrap" style="display:none">
    <span class="deck-ticker-tag">live</span>
    <div class="deck-ticker" id="deck-ticker"></div>
  </div>

  <div class="deck-launch">
    <button class="deck-tile" data-act="newchat"><span class="deck-tile-i">＋</span>New chat</button>
    <button class="deck-tile" data-act="tree"><span class="deck-tile-i">🕸</span>Memory Tree</button>
    <button class="deck-tile" data-act="cockpit"><span class="deck-tile-i">🧠</span>Cockpit</button>
    <button class="deck-tile" data-act="calendar"><span class="deck-tile-i">📅</span>Calendar</button>
    <button class="deck-tile" data-act="email"><span class="deck-tile-i">✉</span>Email</button>
    <button class="deck-tile" data-act="tasks"><span class="deck-tile-i">◷</span>Tasks</button>
  </div>

  <div class="deck-next">
    <button class="deck-next-btn" id="deck-next-btn">✨ Bert, what should I work on?</button>
    <div class="deck-next-out" id="deck-next-out"></div>
  </div>`;
}

function wire(root) {
  // Pulse cards.
  root.querySelectorAll('.deck-card[data-go]').forEach((c) => {
    c.onclick = () => {
      const go = c.dataset.go;
      if (go === 'tree' || go === 'cockpit') openBrainTab(go);
      else if (go === 'builds') openBrainTab('cockpit');
    };
  });
  // Launch tiles + the Today strip segments (same act vocabulary).
  root.querySelectorAll('.deck-tile[data-act], .deck-day-seg[data-act]').forEach((b) => {
    b.onclick = () => {
      const a = b.dataset.act;
      if (a === 'newchat') clickIf('sidebar-new-chat-btn');
      else if (a === 'tree') openBrainTab('tree');
      else if (a === 'cockpit') openBrainTab('cockpit');
      else if (a === 'calendar') clickIf('tool-calendar-btn');
      else if (a === 'email') clickIf('email-section-title');
      else if (a === 'tasks') clickIf('tool-tasks-btn');
    };
  });
  // On-demand recommendation.
  const nb = root.querySelector('#deck-next-btn');
  if (nb) nb.onclick = suggestNext;
  // On-demand inbox digest.
  const db = root.querySelector('#deck-digest-btn');
  if (db) db.onclick = loadDigest;
}

async function loadDigest() {
  const out = document.getElementById('deck-digest-out');
  const btn = document.getElementById('deck-digest-btn');
  if (!out) return;
  out.innerHTML = `<div class="deck-digest-loading">Bert's reading your inbox… (free model, ~10s)</div>`;
  if (btn) { btn.disabled = true; btn.textContent = '📥 Reading…'; }
  const r = await jget('/api/email/digest', 60000);
  if (btn) { btn.disabled = false; btn.textContent = '📥 Re-summarize inbox'; }
  if (!r || r.ok === false) { out.innerHTML = `<div class="deck-digest-empty">Couldn't read the inbox right now.</div>`; return; }
  if (!r.count) { out.innerHTML = `<div class="deck-digest-empty">📭 Inbox zero — nothing unread.</div>`; return; }
  const needs = (r.needs_reply || []).slice(0, 6);
  const fyi = (r.fyi || []).slice(0, 5);
  out.innerHTML = `
    <div class="deck-digest-summary">${esc(r.summary || '')}</div>
    ${needs.length ? `<div class="deck-digest-label">Needs a reply</div>` + needs.map((n) => `
      <div class="deck-digest-item needs">
        <div class="deck-digest-from">${esc((n.from || '').slice(0, 38))}</div>
        <div class="deck-digest-subj">${esc((n.subject || '').slice(0, 72))}</div>
        ${n.why ? `<div class="deck-digest-why">${esc(String(n.why).slice(0, 80))}</div>` : ''}
      </div>`).join('') : ''}
    ${fyi.length ? `<div class="deck-digest-label">FYI</div><ul class="deck-digest-fyi">` + fyi.map((f) => `<li>${esc(String(f).slice(0, 90))}</li>`).join('') + `</ul>` : ''}
    <div class="deck-digest-foot">${r.count} unread${r.skip ? ` · ${r.skip} skippable` : ''}${r.model ? ` · ${esc(String(r.model).slice(0, 22))}` : ''}</div>`;
}

function inject() {
  if (injected) return true;
  const ws = document.getElementById('welcome-screen');
  if (!ws) return false;
  const host = document.createElement('div');
  host.id = 'home-deck';
  host.className = 'home-deck';
  host.innerHTML = shell();
  // The Bert orb is the hero — prepend it above the greeting.
  try {
    const orbEl = bertOrb.build();
    if (orbEl) host.insertBefore(orbEl, host.firstChild);
  } catch (e) { /* orb is enhancement-only; never block the deck */ }
  // Place it after the tip line but before the incognito button so the deck is
  // the visual centerpiece and the Nobody toggle stays at the bottom.
  const anchor = document.getElementById('welcome-tip') || document.getElementById('welcome-sub');
  if (anchor && anchor.parentNode === ws) anchor.insertAdjacentElement('afterend', host);
  else ws.appendChild(host);
  // The deck makes the welcome content tall — switch the welcome screen to a
  // top-anchored, self-scrolling region that sits entirely above the chat input
  // bar (CSS .has-deck), and drop the composer to the bottom (deck-mode) instead
  // of its centered welcome position, so nothing overlaps on shorter windows.
  ws.classList.add('has-deck');
  document.getElementById('chat-container')?.classList.add('deck-mode');
  wire(host);
  injected = true;
  return true;
}

function renderPulse(status, recent, jobs) {
  const brainVal = document.getElementById('deck-brain-val');
  const buildsVal = document.getElementById('deck-builds-val');
  const memVal = document.getElementById('deck-mem-val');
  const up = status && status.ok !== false && status.up !== false;

  // Bert's context line under the greeting — what he's been up to.
  const ctx = document.getElementById('deck-context');
  if (ctx) {
    if (up) {
      const live = (status.engines || []).filter((e) => e.online).length;
      const runs = (jobs && jobs.ok !== false) ? (jobs.data || []) : [];
      const builtToday = runs.filter((r) => r.status === 'done' && isToday(r.finishedAt || r.startedAt)).length;
      const notes = (recent && recent.ok !== false) ? ((recent.data || {}).notes || []) : [];
      const memToday = notes.filter((n) => isToday(n.ts)).length;
      const parts = [`${live} engine${live !== 1 ? 's' : ''} warm`];
      if (builtToday) { const h = new Date().getHours(); parts.push(`${builtToday} built ${h < 11 ? 'while you slept' : 'today'}`); }
      if (memToday) parts.push(`${memToday} new memor${memToday !== 1 ? 'ies' : 'y'} logged`);
      ctx.textContent = parts.join(' · ');
    } else {
      ctx.textContent = "my brain's asleep — wake it to come fully online";
    }
  }

  if (brainVal) {
    if (up) {
      const live = (status.engines || []).filter((e) => e.online).length;
      brainVal.innerHTML = `<span class="deck-on">●</span> ${live}`;
      brainVal.parentElement?.classList.add('is-on');
      brainVal.parentElement?.classList.remove('is-off');
    } else {
      brainVal.innerHTML = `<span class="deck-off">●</span> off`;
      brainVal.parentElement?.classList.add('is-off');
      brainVal.parentElement?.classList.remove('is-on');
    }
  }

  // Builds shipped today (Deep Build runs done, finished today).
  if (buildsVal) {
    if (jobs && jobs.ok !== false) {
      const runs = jobs.data || [];
      const n = runs.filter((r) => r.status === 'done' && isToday(r.finishedAt || r.startedAt)).length;
      buildsVal.textContent = String(n);
    } else buildsVal.textContent = up ? '0' : '·';
  }

  // New memories today / total.
  if (memVal) {
    if (recent && recent.ok !== false) {
      const notes = (recent.data || {}).notes || [];
      const today = notes.filter((n) => isToday(n.ts)).length;
      memVal.innerHTML = `${today}<span class="deck-card-val-sub">new</span>`;
    } else memVal.textContent = up ? '0' : '·';
  }

  // Ticker — most recent durable memories, scrolling.
  const tickerWrap = document.getElementById('deck-ticker-wrap');
  const ticker = document.getElementById('deck-ticker');
  if (ticker && recent && recent.ok !== false) {
    const notes = ((recent.data || {}).notes || []).slice(0, 14);
    if (notes.length) {
      const items = notes.map((n) => {
        const ic = KIND_ICON[n.kind] || '·';
        return `<span class="deck-tick-item"><span class="deck-tick-ic">${ic}</span>${esc((n.preview || '').slice(0, 90))}</span>`;
      });
      // Duplicate once for a seamless marquee loop.
      ticker.innerHTML = `<div class="deck-tick-run">${items.join('<span class="deck-tick-sep">•</span>')}${items.length > 3 ? '<span class="deck-tick-sep">•</span>' + items.join('<span class="deck-tick-sep">•</span>') : ''}</div>`;
      if (tickerWrap) tickerWrap.style.display = '';
    } else if (tickerWrap) tickerWrap.style.display = 'none';
  } else if (tickerWrap) tickerWrap.style.display = 'none';
}

async function loadPulse(force) {
  if (!inject()) return;
  if (loadingPulse) return;
  // Throttle: don't refetch more than every 20s unless forced.
  if (!force && Date.now() - lastLoad < 20000) return;
  loadingPulse = true;
  const [status, recent, jobs] = await Promise.all([
    jget('/api/brain/status', 7000),
    jget('/api/brain/memory/recent?limit=14', 9000),
    jget('/api/brain/deep-jobs?limit=20', 9000),
  ]);
  lastLoad = Date.now();
  loadingPulse = false;
  renderPulse(status, recent, jobs);
  loadDay(force);
}

// ── Today strip: real calendar + inbox, straight from the body (works even
// when the brain is off — these are native body endpoints). ──
let lastDay = 0;
function pad2(n) { return String(n).padStart(2, '0'); }
function fmtTime(d) {
  let h = d.getHours(); const m = d.getMinutes(); const ap = h < 12 ? 'am' : 'pm';
  h = h % 12; if (h === 0) h = 12;
  return m ? `${h}:${pad2(m)}${ap}` : `${h}${ap}`;
}
async function loadDay(force) {
  const strip = document.getElementById('deck-day');
  if (!strip) return;
  if (!force && Date.now() - lastDay < 20000) return;
  lastDay = Date.now();
  const now = new Date();
  const start = `${now.getFullYear()}-${pad2(now.getMonth() + 1)}-${pad2(now.getDate())}T00:00:00`;
  const end = `${now.getFullYear()}-${pad2(now.getMonth() + 1)}-${pad2(now.getDate())}T23:59:59`;
  const [cal, mail] = await Promise.all([
    jget(`/api/calendar/events?start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}`, 9000),
    jget('/api/email/unread-count', 9000),
  ]);
  const calTxt = document.getElementById('deck-day-cal');
  const mailTxt = document.getElementById('deck-day-mail');
  let shown = false;

  if (calTxt) {
    const evs = (cal && (cal.events || cal.data)) || [];
    if (Array.isArray(evs) && evs.length) {
      // Find the next event still ahead today, else the first.
      const parsed = evs.map((e) => ({ e, t: new Date(e.start || e.dtstart || e.when || 0) }))
        .filter((o) => !isNaN(o.t)).sort((a, b) => a.t - b.t);
      const next = parsed.length ? (parsed.find((o) => o.t >= now) || parsed[0]) : null;
      const title = (next && (next.e.summary || next.e.title || next.e.subject)) || 'event';
      const tm = next && !isNaN(next.t) && next.t > 0 ? fmtTime(next.t) + ' ' : '';
      calTxt.innerHTML = `<strong>${tm}${esc(String(title).slice(0, 26))}</strong>${evs.length > 1 ? ` <span class="deck-day-more">+${evs.length - 1}</span>` : ''}`;
      shown = true;
    } else {
      calTxt.textContent = 'No events today';
      shown = true;
    }
  }

  if (mailTxt) {
    if (mail && mail.ok && typeof mail.count === 'number') {
      mailTxt.innerHTML = `<strong>${mail.count.toLocaleString()}</strong> unread`;
      shown = true;
    } else {
      mailTxt.textContent = 'Inbox';
    }
  }

  strip.style.display = shown ? '' : 'none';
}

async function suggestNext() {
  const out = document.getElementById('deck-next-out');
  const btn = document.getElementById('deck-next-btn');
  if (!out) return;
  out.innerHTML = `<div class="deck-next-loading">Bert's thinking it over… (free model, ~30s)</div>`;
  if (btn) { btn.disabled = true; btn.textContent = '✨ Bert is thinking…'; }
  const r = await jget('/api/brain/recommend', 130000);
  if (btn) { btn.disabled = false; btn.textContent = '✨ Bert, what should I work on?'; }
  if (!r || r.ok === false) {
    out.innerHTML = `<div class="deck-next-empty">${r && r.code === 'BRAIN_DOWN' ? "Wake my brain and I'll have ideas for you." : "I've got nothing pressing right now" + (r && r.code === 'BRAIN_ERROR' ? ' (update the brain to enable this).' : '.')}</div>`;
    return;
  }
  const recs = ((r.data || {}).recommendations || []).slice(0, 3);
  if (!recs.length) { out.innerHTML = `<div class="deck-next-empty">You're all caught up, ${esc(USER_NAME)}. Nothing pressing.</div>`; return; }
  out.innerHTML = `<div class="deck-rec-intro">Here's what I'd tackle, ${esc(USER_NAME)}:</div>` + recs.map((x) => `
    <div class="deck-rec">
      <div class="deck-rec-head"><span class="deck-rec-type">${esc(x.type || 'idea')}</span>${x.projectName ? `<span class="deck-rec-proj">${esc(x.projectName)}</span>` : ''}<span class="deck-rec-impact i-${esc(x.impact || 'medium')}">${esc(x.impact || '')}</span></div>
      <div class="deck-rec-title">${esc(x.title || '')}</div>
      <button class="deck-rec-go" data-obj="${esc(x.objective || x.title || '')}">▶ Start this</button>
    </div>`).join('');
  out.querySelectorAll('.deck-rec-go[data-obj]').forEach((b) => {
    b.onclick = () => {
      const input = document.getElementById('message');
      if (input) {
        input.value = `Use the brain to work on this: ${b.dataset.obj}`;
        input.focus();
        input.dispatchEvent(new Event('input', { bubbles: true }));
      }
    };
  });
}

// Show/hide with the welcome screen. The welcome screen is visible when the
// chat container carries .welcome-active; we observe that class so the deck
// refreshes whenever the user returns to the empty state.
function watchWelcome() {
  const cc = document.getElementById('chat-container');
  if (!cc) return;
  const apply = () => {
    if (cc.classList.contains('welcome-active')) {
      if (inject()) loadPulse(false);
    }
  };
  apply();
  const mo = new MutationObserver(apply);
  mo.observe(cc, { attributes: true, attributeFilter: ['class'] });
}

document.addEventListener('DOMContentLoaded', () => {
  // Defer slightly so app.js has set the initial welcome-active state.
  setTimeout(() => { watchWelcome(); }, 120);
});

const homeDeck = { loadPulse, inject };
export default homeDeck;
window.homeDeck = homeDeck;
