// memoryTree.js — the Memory Tree: an interactive force-directed view of BertOS's
// shared knowledge graph (memories + projects + tags, ~3k links), rendered in
// vanilla canvas (no external deps). Data comes from the body's brain proxy
// (/api/brain/memory/graph → the bertosV2 brain). Self-contained: it wires its
// own tab trigger so memory.js stays untouched.
//
// Design: settle a force layout on open (top-N most-connected nodes for clarity
// + speed), then render statically with pan / zoom / drag / hover / click. The
// rAF loop only runs while the layout is "alive" (settling or being dragged),
// so it idles at 0% CPU once at rest.

const API = window.location.origin;

const TYPE_STYLE = {
  memory: { fill: '#5b8cff', glow: 'rgba(91,140,255,0.55)', label: 'memories' },
  project: { fill: '#f0b429', glow: 'rgba(240,180,41,0.55)', label: 'projects' },
  tag: { fill: '#3fb1a6', glow: 'rgba(63,177,166,0.5)', label: 'tags' },
  default: { fill: '#9aa4b2', glow: 'rgba(154,164,178,0.4)', label: 'other' },
};
const KIND_HINT = { decision: '◆', fact: '•', artifact: '▣', plan: '◇', note: '·' };
const MAX_NODES = 240; // top-by-degree cap for a clean, fast graph

const state = {
  loaded: false,
  loading: false,
  raw: null, // { nodes, links, counts }
  nodes: [],
  links: [],
  byId: new Map(),
  filters: { memory: true, project: true, tag: true },
  query: '',
  view: { x: 0, y: 0, scale: 1 },
  alive: 0, // frames of remaining "heat"
  scope: null, // null = all memories; else { projectId, name } → graph scoped to one project
  hover: null,
  pinned: null,
  drag: null, // { node } | { pan:true, sx, sy, ox, oy }
  raf: 0,
  canvas: null,
  ctx: null,
  dpr: 1,
};

function themed(varName, fallback) {
  try {
    const v = getComputedStyle(document.documentElement).getPropertyValue(varName).trim();
    return v || fallback;
  } catch { return fallback; }
}

function el(id) { return document.getElementById(id); }

async function fetchGraph() {
  let url = `${API}/api/brain/memory/graph`;
  if (state.scope && state.scope.projectId) {
    url += `?scope=project&projectId=${encodeURIComponent(state.scope.projectId)}`;
  }
  const res = await fetch(url, { cache: 'no-store' });
  const j = await res.json();
  return j;
}
// Reload the graph scoped to one project (or back to all when projectId is null).
function scopeTo(projectId, name) {
  state.scope = projectId ? { projectId, name: name || projectId } : null;
  state.pinned = null;
  state.loaded = false;
  renderScopePill();
  render(true);
}
function renderScopePill() {
  const pill = el('mtree-scope-clear');
  const nm = el('mtree-scope-name');
  if (!pill) return;
  if (state.scope) {
    if (nm) nm.textContent = '⊙ ' + (state.scope.name || 'project');
    pill.classList.remove('hidden');
  } else pill.classList.add('hidden');
}

function buildModel(graph) {
  const counts = graph.counts || {};
  let nodes = Array.isArray(graph.nodes) ? graph.nodes.slice() : [];
  // Keep the most-connected nodes for clarity + performance.
  nodes.sort((a, b) => (b.degree || 0) - (a.degree || 0));
  const kept = nodes.slice(0, MAX_NODES);
  const keptIds = new Set(kept.map((n) => n.id));
  const byId = new Map();
  const W = state.canvas ? state.canvas.clientWidth : 800;
  const H = state.canvas ? state.canvas.clientHeight : 600;
  kept.forEach((n, i) => {
    const a = (i / kept.length) * Math.PI * 2;
    const r = 30 + Math.random() * Math.min(W, H) * 0.35;
    const node = {
      id: n.id, type: n.type || 'memory', kind: n.kind, label: n.label || n.id,
      preview: n.preview, tags: n.tags, projectId: n.projectId, source: n.source, ts: n.ts,
      degree: n.degree || 1,
      x: W / 2 + Math.cos(a) * r, y: H / 2 + Math.sin(a) * r, vx: 0, vy: 0,
    };
    byId.set(n.id, node);
  });
  const links = (graph.links || [])
    .filter((l) => keptIds.has(l.source) && keptIds.has(l.target))
    .map((l) => ({ s: byId.get(l.source), t: byId.get(l.target), kind: l.kind }));
  // Prune isolated memory nodes (their links fell outside the kept set) — with no
  // springs they just orbit the repulsion/gravity shell as noise. Keep all hubs.
  const linked = new Set();
  for (const l of links) { linked.add(l.s.id); linked.add(l.t.id); }
  state.raw = { counts };
  state.nodes = [...byId.values()].filter((n) => linked.has(n.id) || n.type !== 'memory');
  state.links = links;
  state.byId = byId;
}

function radius(n) { return Math.max(3, Math.min(22, 3 + Math.sqrt(n.degree) * 2.4)); }
function visible(n) { return state.filters[n.type] !== false; }
function nodeMatches(n, q) {
  return (n.label && n.label.toLowerCase().includes(q)) ||
    (n.preview && n.preview.toLowerCase().includes(q)) ||
    (Array.isArray(n.tags) && n.tags.some((t) => String(t).toLowerCase().includes(q)));
}

// ── force simulation (one tick) ──────────────────────────────────────────────
function tick() {
  const nodes = state.nodes, links = state.links;
  const W = state.canvas.clientWidth, H = state.canvas.clientHeight;
  const cx = W / 2, cy = H / 2;
  // repulsion (O(n^2) but n<=320 → ~100k ops/tick, fine)
  for (let i = 0; i < nodes.length; i++) {
    const a = nodes[i];
    if (!visible(a)) continue;
    for (let j = i + 1; j < nodes.length; j++) {
      const b = nodes[j];
      if (!visible(b)) continue;
      let dx = a.x - b.x, dy = a.y - b.y;
      let d2 = dx * dx + dy * dy || 0.01;
      if (d2 > 250000) continue; // ignore far pairs (within ~500px)
      const f = 2600 / d2;
      const d = Math.sqrt(d2);
      const fx = (dx / d) * f, fy = (dy / d) * f;
      a.vx += fx; a.vy += fy; b.vx -= fx; b.vy -= fy;
    }
    // gentle gravity to center (keeps the graph contained; fit-to-view frames it)
    a.vx += (cx - a.x) * 0.001;
    a.vy += (cy - a.y) * 0.001;
  }
  // springs
  for (const l of links) {
    if (!l.s || !l.t || !visible(l.s) || !visible(l.t)) continue;
    let dx = l.t.x - l.s.x, dy = l.t.y - l.s.y;
    const d = Math.sqrt(dx * dx + dy * dy) || 0.01;
    const rest = 95;
    const f = (d - rest) * 0.0032;
    const fx = (dx / d) * f, fy = (dy / d) * f;
    l.s.vx += fx; l.s.vy += fy; l.t.vx -= fx; l.t.vy -= fy;
  }
  // integrate
  for (const n of nodes) {
    if (state.drag && state.drag.node === n) { n.vx = 0; n.vy = 0; continue; }
    n.vx = Math.max(-26, Math.min(26, n.vx * 0.85));
    n.vy = Math.max(-26, Math.min(26, n.vy * 0.85));
    n.x += n.vx; n.y += n.vy;
  }
}

// ── render ───────────────────────────────────────────────────────────────────
function draw() {
  const ctx = state.ctx, c = state.canvas;
  const W = c.clientWidth, H = c.clientHeight;
  // Keep the backing buffer synced with the (flex-settled) display size, and
  // clear the FULL buffer — otherwise a size mismatch leaves an uncleared strip
  // where stale frames pile up (the "bottom band").
  if (c.width !== Math.round(W * state.dpr) || c.height !== Math.round(H * state.dpr)) resize();
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.clearRect(0, 0, c.width, c.height);
  ctx.setTransform(state.dpr, 0, 0, state.dpr, 0, 0);
  ctx.save();
  ctx.translate(state.view.x, state.view.y);
  ctx.scale(state.view.scale, state.view.scale);

  const hl = state.hover || state.pinned;
  const q = state.query;
  const neigh = new Set();
  if (hl) { neigh.add(hl.id); for (const l of state.links) { if (l.s === hl) neigh.add(l.t.id); if (l.t === hl) neigh.add(l.s.id); } }

  // links
  ctx.lineWidth = 1 / state.view.scale;
  for (const l of state.links) {
    if (!l.s || !l.t || !visible(l.s) || !visible(l.t)) continue;
    const on = hl && (l.s === hl || l.t === hl);
    ctx.strokeStyle = on ? 'rgba(120,160,255,0.55)' : (hl ? 'rgba(120,140,170,0.06)' : 'rgba(120,140,170,0.14)');
    ctx.beginPath(); ctx.moveTo(l.s.x, l.s.y); ctx.lineTo(l.t.x, l.t.y); ctx.stroke();
  }
  // nodes
  for (const n of state.nodes) {
    if (!visible(n)) continue;
    const st = TYPE_STYLE[n.type] || TYPE_STYLE.default;
    const r = radius(n);
    const hit = q && nodeMatches(n, q);
    const dim = (hl && !neigh.has(n.id)) || (q && !hit);
    ctx.globalAlpha = dim ? 0.1 : 1;
    if (!dim && (n === hl || hit || r > 9)) {
      ctx.shadowColor = hit ? '#ffffff' : st.glow; ctx.shadowBlur = (n === hl || hit) ? 20 : 10;
    } else { ctx.shadowBlur = 0; }
    ctx.beginPath(); ctx.arc(n.x, n.y, r, 0, Math.PI * 2);
    ctx.fillStyle = st.fill; ctx.fill();
    ctx.shadowBlur = 0;
    if (n === hl || hit) { ctx.lineWidth = 2 / state.view.scale; ctx.strokeStyle = '#fff'; ctx.stroke(); }
    ctx.globalAlpha = 1;
  }
  // labels for hubs / hovered
  ctx.font = `${12 / state.view.scale}px ui-sans-serif, system-ui, sans-serif`;
  ctx.textAlign = 'left'; ctx.textBaseline = 'middle';
  for (const n of state.nodes) {
    if (!visible(n)) continue;
    const big = radius(n) >= 11;
    if (!big && n !== hl) continue;
    if (hl && !neigh.has(n.id)) continue;
    const r = radius(n);
    const txt = (n.label || '').slice(0, n === hl ? 60 : 22);
    ctx.fillStyle = n === hl ? '#fff' : 'rgba(220,228,240,0.75)';
    ctx.fillText(txt, n.x + r + 4, n.y);
  }
  ctx.restore();
}

function fitView() {
  const all = state.nodes.filter(visible);
  if (all.length < 3 || !state.canvas) return;
  // Frame the central mass (ignore the furthest ~6% so a few stragglers don't
  // shrink the whole graph) — outliers stay pannable.
  let mx = 0, my = 0; for (const n of all) { mx += n.x; my += n.y; } mx /= all.length; my /= all.length;
  const ns = all.map((n) => ({ n, d: (n.x - mx) ** 2 + (n.y - my) ** 2 }))
    .sort((a, b) => a.d - b.d).slice(0, Math.max(3, Math.floor(all.length * 0.94))).map((o) => o.n);
  let minx = 1e9, miny = 1e9, maxx = -1e9, maxy = -1e9;
  for (const n of ns) { if (n.x < minx) minx = n.x; if (n.y < miny) miny = n.y; if (n.x > maxx) maxx = n.x; if (n.y > maxy) maxy = n.y; }
  const W = state.canvas.clientWidth, H = state.canvas.clientHeight, pad = 56;
  const sx = (W - pad * 2) / Math.max(1, maxx - minx);
  const sy = (H - pad * 2) / Math.max(1, maxy - miny);
  state.view.scale = Math.max(0.2, Math.min(sx, sy, 1.6));
  state.view.x = W / 2 - ((minx + maxx) / 2) * state.view.scale;
  state.view.y = H / 2 - ((miny + maxy) / 2) * state.view.scale;
}
function loop() {
  if (state.alive > 0 || state.drag) {
    for (let i = 0; i < 2; i++) tick();
    if (!state.drag) {
      state.alive--;
      if (state.alive <= 0 && state.needsFit) { state.needsFit = false; fitView(); }
    }
  }
  draw();
  // Animate only while settling/dragging or when a one-shot redraw was requested
  // (hover/search/zoom). Otherwise idle at 0% CPU — important on phones.
  if (state.alive > 0 || state.drag || state._redraw) { state._redraw = false; state.raf = requestAnimationFrame(loop); }
  else { state.raf = 0; }
}
function kick(frames = 220) { state.alive = Math.max(state.alive, frames); if (!state.raf) state.raf = requestAnimationFrame(loop); }
function requestDraw() { state._redraw = true; if (!state.raf) state.raf = requestAnimationFrame(loop); }

// ── interaction ──────────────────────────────────────────────────────────────
function toWorld(px, py) {
  return { x: (px - state.view.x) / state.view.scale, y: (py - state.view.y) / state.view.scale };
}
function nodeAt(px, py) {
  const w = toWorld(px, py);
  let best = null, bd = 1e9;
  for (const n of state.nodes) {
    if (!visible(n)) continue;
    const dx = n.x - w.x, dy = n.y - w.y, d = dx * dx + dy * dy;
    const rr = (radius(n) + 6) ** 2;
    if (d < rr && d < bd) { bd = d; best = n; }
  }
  return best;
}
function wireCanvas() {
  const c = state.canvas;
  c.onmousedown = (e) => {
    const r = c.getBoundingClientRect();
    const n = nodeAt(e.clientX - r.left, e.clientY - r.top);
    if (n) state.drag = { node: n };
    else state.drag = { pan: true, sx: e.clientX, sy: e.clientY, ox: state.view.x, oy: state.view.y };
    kick(40);
  };
  window.addEventListener('mousemove', (e) => {
    const r = c.getBoundingClientRect();
    const mx = e.clientX - r.left, my = e.clientY - r.top;
    if (state.drag?.node) {
      const w = toWorld(mx, my); state.drag.node.x = w.x; state.drag.node.y = w.y; state.drag.node.vx = 0; state.drag.node.vy = 0; kick(30);
    } else if (state.drag?.pan) {
      state.view.x = state.drag.ox + (e.clientX - state.drag.sx);
      state.view.y = state.drag.oy + (e.clientY - state.drag.sy);
    } else if (mx >= 0 && my >= 0 && mx <= c.clientWidth && my <= c.clientHeight) {
      const n = nodeAt(mx, my);
      if (n !== state.hover) { state.hover = n; c.style.cursor = n ? 'pointer' : 'grab'; requestDraw(); }
    }
  });
  window.addEventListener('mouseup', () => { if (state.drag?.node) kick(60); state.drag = null; requestDraw(); });
  c.onclick = (e) => {
    const r = c.getBoundingClientRect();
    const n = nodeAt(e.clientX - r.left, e.clientY - r.top);
    state.pinned = n && n === state.pinned ? null : n;
    renderDetail(); requestDraw();
  };
  c.ondblclick = () => { fitView(); requestDraw(); };
  c.onwheel = (e) => {
    e.preventDefault();
    const r = c.getBoundingClientRect();
    const mx = e.clientX - r.left, my = e.clientY - r.top;
    const before = toWorld(mx, my);
    const f = e.deltaY < 0 ? 1.12 : 0.89;
    state.view.scale = Math.max(0.2, Math.min(4, state.view.scale * f));
    const after = toWorld(mx, my);
    state.view.x += (after.x - before.x) * state.view.scale;
    state.view.y += (after.y - before.y) * state.view.scale;
    requestDraw();
  };
}

// ── DOM: detail card + legend ────────────────────────────────────────────────
// Unique neighbors of a node (the other endpoint of every incident link),
// sorted by degree so the most-connected relatives surface first.
function neighborsOf(n) {
  const seen = new Map();
  for (const l of state.links) {
    let o = null;
    if (l.s === n) o = l.t; else if (l.t === n) o = l.s;
    if (o && !seen.has(o.id)) seen.set(o.id, o);
  }
  return [...seen.values()].sort((a, b) => (b.degree || 0) - (a.degree || 0));
}
// Pin a node and glide the viewport so it sits centered — lets you walk the
// graph by clicking through the "Connected" list without losing your place.
function focusNode(n) {
  if (!n) return;
  state.pinned = n;
  if (!visible(n)) { state.filters[n.type] = true; const b = document.querySelector(`.mtree-legend-item[data-mtype="${n.type}"]`); if (b) b.classList.remove('off'); }
  const W = state.canvas.clientWidth, H = state.canvas.clientHeight;
  const scale = Math.max(state.view.scale, 0.8);
  state.view.scale = scale;
  state.view.x = W / 2 - n.x * scale;
  state.view.y = H / 2 - n.y * scale;
  renderDetail(); requestDraw();
}
function renderDetail() {
  const box = el('mtree-detail');
  if (!box) return;
  const n = state.pinned;
  if (!n) { box.classList.add('hidden'); box.innerHTML = ''; return; }
  const st = TYPE_STYLE[n.type] || TYPE_STYLE.default;
  const when = n.ts ? new Date(n.ts).toLocaleString() : '';
  const tags = (n.tags || []).map((t) => `<span class="mtree-chip">${esc(t)}</span>`).join('');
  const nbrs = neighborsOf(n);
  const nbrChips = nbrs.slice(0, 16).map((o) => {
    const os = TYPE_STYLE[o.type] || TYPE_STYLE.default;
    return `<button class="mtree-nbr" data-nid="${esc(o.id)}" title="${esc(o.label || '')}"><span class="mtree-dot" style="background:${os.fill}"></span>${esc((o.label || '').slice(0, 30))}</button>`;
  }).join('');
  // A project node can scope the whole graph to just its subgraph.
  const canScope = n.type === 'project' && n.projectId && (!state.scope || state.scope.projectId !== n.projectId);
  box.classList.remove('hidden');
  box.innerHTML = `
    <div class="mtree-detail-head"><span class="mtree-dot" style="background:${st.fill}"></span>
      <strong>${esc((n.kind ? KIND_HINT[n.kind] || '' : '') + ' ' + (n.label || ''))}</strong>
      <button id="mtree-detail-x" title="Close">✕</button></div>
    <div class="mtree-detail-meta">${esc(n.type)}${n.kind ? ' · ' + esc(n.kind) : ''}${n.source ? ' · ' + esc(n.source) : ''}${when ? ' · ' + esc(when) : ''} · ${n.degree} links</div>
    ${n.preview ? `<div class="mtree-detail-body">${esc(n.preview).slice(0, 600)}</div>` : ''}
    ${canScope ? `<button class="mtree-focus-btn" id="mtree-focus-proj">⊙ Focus this project</button>` : ''}
    ${tags ? `<div class="mtree-chips">${tags}</div>` : ''}
    ${nbrChips ? `<div class="mtree-nbr-label">Connected${nbrs.length > 16 ? ' · ' + nbrs.length : ''}</div><div class="mtree-nbrs">${nbrChips}</div>` : ''}`;
  const x = el('mtree-detail-x'); if (x) x.onclick = () => { state.pinned = null; renderDetail(); requestDraw(); };
  const fp = el('mtree-focus-proj'); if (fp) fp.onclick = () => scopeTo(n.projectId, n.label);
  box.querySelectorAll('.mtree-nbr[data-nid]').forEach((b) => {
    b.onclick = () => { const o = state.byId.get(b.dataset.nid); if (o) focusNode(o); };
  });
}
function esc(s) { return String(s == null ? '' : s).replace(/[&<>"]/g, (m) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[m])); }

function renderHeader() {
  const c = state.raw?.counts || {};
  const h = el('mtree-counts');
  if (h) h.innerHTML = `<b>${c.memories ?? 0}</b> memories · <b>${c.projects ?? 0}</b> projects · <b>${c.tags ?? 0}</b> tags · <b>${c.links ?? 0}</b> links`;
}
function wireLegend() {
  document.querySelectorAll('.mtree-legend-item[data-mtype]').forEach((b) => {
    b.onclick = () => {
      const t = b.dataset.mtype;
      state.filters[t] = !state.filters[t];
      b.classList.toggle('off', !state.filters[t]);
      kick(120); draw();
    };
  });
  const rf = el('mtree-refresh'); if (rf) rf.onclick = () => { state.loaded = false; render(true); };
}

// ── public: render (called when the Tree tab opens) ──────────────────────────
async function render(force) {
  const panel = document.querySelector('.memory-tab-panel[data-memory-panel="tree"]');
  if (!panel) return;
  state.canvas = el('mtree-canvas');
  state.ctx = state.canvas?.getContext('2d');
  if (!state.canvas || !state.ctx) return;
  resize();
  if (state.loaded && !force) { kick(60); return; }
  if (state.loading) return;
  state.loading = true;
  setStatus('Loading the memory graph…');
  try {
    const j = await fetchGraph();
    if (!j || j.ok === false) {
      const down = j && (j.code === 'BRAIN_DOWN');
      setStatus(down
        ? '🧠 Brain is offline. Start it on your host (<code>npm run bertos:host</code>) to see the Memory Tree.'
        : `Couldn't load the graph${j && j.error ? ': ' + esc(j.error) : ''}.`, true);
      state.loading = false; return;
    }
    const graph = (j.data && j.data.graph) || j.graph || j.data || j;
    buildModel(graph);
    state.needsFit = true;
    state.view = { x: 0, y: 0, scale: 1 };
    renderHeader();
    setStatus('');
    state.loaded = true;
    state.loading = false;
    if (!state._wired) { wireCanvas(); state._wired = true; }
    kick(320);
    if (!state.raf) loop();
  } catch (e) {
    setStatus('Couldn’t reach the memory graph: ' + esc(String(e)), true);
    state.loading = false;
  }
}
function setStatus(msg, isErr) {
  const s = el('mtree-status');
  if (!s) return;
  s.innerHTML = msg || '';
  s.style.display = msg ? 'flex' : 'none';
  s.classList.toggle('mtree-err', !!isErr);
}
function resize() {
  const c = state.canvas; if (!c) return;
  const dpr = window.devicePixelRatio || 1;
  state.dpr = dpr;
  c.width = c.clientWidth * dpr;
  c.height = c.clientHeight * dpr;
}
window.addEventListener('resize', () => { if (state.canvas) { resize(); draw(); } });

// Self-wire: render when the Tree tab is clicked (memory.js handles panel toggle).
document.addEventListener('DOMContentLoaded', () => {
  const tab = document.querySelector('.memory-tab[data-memory-tab="tree"]');
  if (tab) tab.addEventListener('click', () => setTimeout(() => render(false), 30));
  const scopeClear = document.getElementById('mtree-scope-clear');
  if (scopeClear) scopeClear.onclick = () => scopeTo(null);
  wireLegend();
  const search = document.getElementById('mtree-search');
  if (search) {
    search.addEventListener('input', () => { state.query = search.value.trim().toLowerCase(); requestDraw(); });
    search.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && state.query && state.canvas) {
        const m = state.nodes.find((n) => visible(n) && nodeMatches(n, state.query));
        if (m) {
          const W = state.canvas.clientWidth, H = state.canvas.clientHeight;
          state.view.scale = Math.max(1.1, state.view.scale);
          state.view.x = W / 2 - m.x * state.view.scale;
          state.view.y = H / 2 - m.y * state.view.scale;
          state.pinned = m; renderDetail(); requestDraw();
        }
      }
    });
  }
});

const memoryTree = { render };
export default memoryTree;
window.memoryTree = memoryTree;
