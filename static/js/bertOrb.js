// bertOrb.js — Bert's voice orb: the Jarvis-style HUD on the BertOS home. A
// spinning cobalt orb you tap to talk to; it listens (browser-native
// SpeechRecognition — free, no setup), drops your words into the chat, and
// reads Bert's reply back aloud (SpeechSynthesis), animating through
// idle → listening → thinking → speaking the whole way.
//
// Self-contained: builds its own DOM, uses browser speech APIs directly so it
// works without any STT/TTS provider configured. Degrades gracefully — if the
// browser can't do speech recognition (e.g. Firefox), tapping the orb just
// focuses the chat box so you can type to Bert instead.

const SR = window.SpeechRecognition || window.webkitSpeechRecognition || null;

const orb = {
  el: null, core: null, caption: null, mic: null,
  recog: null,
  listening: false,
  state: 'idle',
  lastTranscript: '',
  replyObserver: null,
  replyTimer: null,
};

function esc(s) { return String(s == null ? '' : s).replace(/[&<>"]/g, (m) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[m])); }

// ── DOM ───────────────────────────────────────────────────────────────────
function build() {
  if (orb.el) return orb.el;
  const wrap = document.createElement('div');
  wrap.className = 'bert-orb-wrap';
  wrap.innerHTML = `
    <button class="bert-orb" id="bert-orb" type="button" aria-label="Talk to Bert" title="Tap to talk to Bert">
      <span class="bert-orb-ring r1"></span>
      <span class="bert-orb-ring r2"></span>
      <span class="bert-orb-ring r3"></span>
      <span class="bert-orb-ripple"></span>
      <span class="bert-orb-core"></span>
      <span class="bert-orb-glyph">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <path d="M12 1.5a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0v-7a3 3 0 0 0-3-3z"/>
          <path d="M18.5 11v.5a6.5 6.5 0 0 1-13 0V11"/><line x1="12" y1="18" x2="12" y2="22"/>
        </svg>
      </span>
    </button>
    <div class="bert-orb-caption" id="bert-orb-caption"></div>`;
  orb.el = wrap.querySelector('.bert-orb');
  orb.core = wrap.querySelector('.bert-orb-core');
  orb.caption = wrap.querySelector('.bert-orb-caption');
  orb.glyph = wrap.querySelector('.bert-orb-glyph');
  orb.el.addEventListener('click', toggle);
  return wrap;
}

function setState(s) {
  orb.state = s;
  if (!orb.el) return;
  orb.el.classList.remove('listening', 'thinking', 'speaking');
  if (s !== 'idle') orb.el.classList.add(s);
}
function caption(text, kind) {
  if (!orb.caption) return;
  orb.caption.textContent = text || '';
  orb.caption.className = 'bert-orb-caption' + (kind ? ' ' + kind : '') + (text ? ' show' : '');
}

// ── voice in ────────────────────────────────────────────────────────────────
function toggle() {
  if (orb.listening) { stopListening(); return; }
  if (orb.state === 'speaking') { stopSpeaking(); setState('idle'); caption(''); return; }
  startListening();
}

function startListening() {
  if (!SR) {
    // No browser speech recognition (e.g. Firefox/Safari without flag) — let
    // the user type to Bert instead.
    caption('Type to Bert below ↓', 'hint');
    const input = document.getElementById('message');
    if (input) { input.focus(); }
    setTimeout(() => { if (orb.state === 'idle') caption(''); }, 2600);
    return;
  }
  try {
    const r = new SR();
    r.lang = 'en-US';
    r.interimResults = true;
    r.continuous = false;
    r.maxAlternatives = 1;
    orb.recog = r;
    orb.lastTranscript = '';
    orb.listening = true;
    setState('listening');
    caption('Listening…', 'status');
    r.onresult = (e) => {
      let txt = '';
      for (let i = 0; i < e.results.length; i++) txt += e.results[i][0].transcript;
      orb.lastTranscript = txt;
      caption(txt || 'Listening…', txt ? '' : 'status');
    };
    r.onerror = (e) => {
      orb.listening = false;
      if (e && e.error === 'not-allowed') caption('Mic blocked — allow it in your browser, or type below.', 'hint');
      else if (e && e.error === 'no-speech') caption('Didn’t catch that — tap and try again.', 'hint');
      else caption('', '');
      setState('idle');
      setTimeout(() => { if (orb.state === 'idle') caption(''); }, 3000);
    };
    r.onend = () => {
      orb.listening = false;
      const txt = (orb.lastTranscript || '').trim();
      if (txt) submitToBert(txt);
      else { setState('idle'); caption(''); }
    };
    r.start();
  } catch (err) {
    orb.listening = false;
    setState('idle');
    caption('Couldn’t start the mic — type to Bert below.', 'hint');
  }
}

function stopListening() {
  try { if (orb.recog) orb.recog.stop(); } catch {}
  orb.listening = false;
}

// ── hand off to the chat, then read the reply ────────────────────────────────
function lastAiText() {
  const nodes = document.querySelectorAll('#chat-history .msg-ai');
  const last = nodes[nodes.length - 1];
  if (!last) return '';
  return aiNodeText(last);
}
function aiNodeText(node) {
  // Clone and strip the chrome (role label, footer, action buttons, thinking
  // panels) so we read just Bert's prose.
  const c = node.cloneNode(true);
  c.querySelectorAll('.role, .msg-footer, .msg-actions, .role-timestamp, .msg-thinking, .tool-events, .msg-overflow-menu, button, .role-provider-logo').forEach((x) => x.remove());
  return (c.textContent || '').replace(/\s+/g, ' ').trim();
}

function submitToBert(text) {
  setState('thinking');
  caption(text, 'you');
  // Make sure we're on the chat surface (close the Brain modal if it's open).
  document.getElementById('close-memory-modal')?.click();
  const input = document.getElementById('message');
  if (!input) { setState('idle'); return; }
  const beforeCount = document.querySelectorAll('#chat-history .msg-ai').length;
  input.value = text;
  input.dispatchEvent(new Event('input', { bubbles: true }));
  // Submit the chat form the same way the send button does.
  const form = document.getElementById('chat-form');
  let sent = false;
  try { if (form && form.requestSubmit) { form.requestSubmit(); sent = true; } } catch {}
  if (!sent) {
    const btn = document.querySelector('.send-btn');
    if (btn) { btn.click(); sent = true; }
  }
  if (!sent) { setState('idle'); return; }
  watchForReply(beforeCount);
}

function watchForReply(beforeCount) {
  // Wait for a NEW assistant message, then speak it once its text stops
  // changing (streaming settled). Give up after 90s.
  clearReplyWatch();
  const hist = document.getElementById('chat-history');
  if (!hist) { setState('idle'); return; }
  const deadline = performance.now() + 90000;
  let lastText = '';
  const settle = () => {
    const nodes = hist.querySelectorAll('.msg-ai');
    if (nodes.length > beforeCount) {
      const txt = aiNodeText(nodes[nodes.length - 1]);
      if (txt && txt !== lastText) {
        lastText = txt;
        caption(txt.slice(0, 180) + (txt.length > 180 ? '…' : ''), '');
        // debounce: speak ~1s after the text last changed
        clearTimeout(orb.replyTimer);
        orb.replyTimer = setTimeout(() => { clearReplyWatch(); speak(lastText); }, 1100);
      }
    }
    if (performance.now() > deadline) { clearReplyWatch(); if (orb.state === 'thinking') { setState('idle'); caption(''); } }
  };
  orb.replyObserver = new MutationObserver(settle);
  orb.replyObserver.observe(hist, { childList: true, subtree: true, characterData: true });
  settle();
}
function clearReplyWatch() {
  if (orb.replyObserver) { orb.replyObserver.disconnect(); orb.replyObserver = null; }
  clearTimeout(orb.replyTimer);
}

// ── voice out ─────────────────────────────────────────────────────────────
function stopSpeaking() {
  try { if ('speechSynthesis' in window) window.speechSynthesis.cancel(); } catch {}
  try { if (window.aiTTSManager && window.aiTTSManager.stop) window.aiTTSManager.stop(); } catch {}
}
function speak(text) {
  if (!text) { setState('idle'); caption(''); return; }
  setState('speaking');
  const done = () => { setState('idle'); setTimeout(() => { if (orb.state === 'idle') caption(''); }, 2500); };
  // Prefer the browser voice directly — always available in Chrome/Edge, no
  // backend. Keep it to a sane length so Bert doesn't monologue.
  const say = text.length > 600 ? text.slice(0, 600) + '…' : text;
  try {
    if ('speechSynthesis' in window) {
      window.speechSynthesis.cancel();
      const u = new SpeechSynthesisUtterance(say);
      u.rate = 1.02; u.pitch = 1.0;
      // Prefer a crisp en voice if present.
      const voices = window.speechSynthesis.getVoices() || [];
      const pick = voices.find((v) => /Google US English|Samantha|Daniel|Microsoft (Aria|Guy)/i.test(v.name)) || voices.find((v) => /^en[-_]/i.test(v.lang));
      if (pick) u.voice = pick;
      u.onend = done; u.onerror = done;
      window.speechSynthesis.speak(u);
    } else if (window.aiTTSManager && window.aiTTSManager.available) {
      window.aiTTSManager.speak(say);
      setTimeout(done, Math.min(12000, 1500 + say.length * 55));
    } else { done(); }
  } catch { done(); }
}

// ── public ──────────────────────────────────────────────────────────────────
const bertOrb = {
  build,
  // Programmatic entry so other surfaces could ask Bert something out loud.
  ask(text) { if (text) submitToBert(String(text)); },
  speak,
  supported() { return !!SR; },
  _state() { return orb.state; },
};
export default bertOrb;
window.bertOrb = bertOrb;
