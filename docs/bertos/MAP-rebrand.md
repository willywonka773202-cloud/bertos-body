I now have a complete, verified picture. No `og:`/`twitter:` meta tags exist. The remaining `odysseus` references in JS are all localStorage keys / code identifiers (NON-user-facing). I have everything needed for the deliverable.

---

# Odysseus → BertOS Rebrand: Phase-1 Edit List (READ-ONLY recon)

Repo: `/Users/willlambert/Documents/odysseus`, branch `bertos` (forked from `dev` @ `62ffcb6`). All claimed files exist. Verified against actual code — several claimed line anchors were stale; corrected below, plus **8 user-facing occurrences the brief missed**.

## A. Verification of claimed anchors (corrections in bold)

| Claimed | Actual | Status |
|---|---|---|
| manifest.json name/short_name/description | `:2 / :3 / :4` | OK (exact lines) |
| index.html `<title> ~:5` | `:5` | OK — value is `Odysseus Chat` (not just "Odysseus") |
| index.html per-route title map `~:153-171` | **titles map `:153-160`; default-fallback literals `:170-171`** | Two distinct blocks, not one range |
| index.html welcome wordmark `~:943` | `:943` | OK |
| index.html theme-color meta `~:8` | `:8` (`#282c34`) | OK |
| index.html accent fallback `#e06c75 ~:104` | `:104` — **plus :6, :41, :58, :538, :539** | One of 6 sites; see §D |
| agent_loop.py `~:140` | `:140` — **plus :190, :360** | 3 model-visible sites, not 1 |
| tool_index.py `~:156` | `:156` | OK (also non-user-facing `:87`) |
| tool_schemas.py `~:532` | `:532` — **plus :552, :953** | 3 model-visible sites |
| README.md:1 | `:1` (`# Odysseus`) + `:3` repo URL | OK |
| odysseus-ui.service Description | `:4` | OK (filename + other lines are NON-user-facing service id) |

---

## B. ORDERED USER-FACING EDIT LIST (top-to-bottom safe)

Ordered file-by-file; within a file, top→bottom so line numbers don't shift under you. New brand text = **BertOS**. Welcome sub-headline / og can stay generic.

### B1. `static/manifest.json` (PWA identity — phone home-screen)
1. `:2` `"name": "Odysseus"` → `"name": "BertOS"`
2. `:3` `"short_name": "Odysseus"` → `"short_name": "BertOS"`
3. `:4` `"description": "Self-hosted AI chat with memory, documents, and tools"` → keep or rebrand, e.g. `"BertOS — self-hosted AI workspace with memory, documents, and tools"` (description is generic; renaming optional)
   - NOTE `:9-10` `background_color`/`theme_color` `#282c34` — these are brand color, covered in §E, not here.

### B2. `static/index.html` (the big one — rendered UI + tab titles)
4. `:5` `<title>Odysseus Chat</title>` → `<title>BertOS Chat</title>`
5. `:153` `'/calendar': 'Calendar — Odysseus'` → `'… — BertOS'`
6. `:154` `'/notes': 'Notes — Odysseus'` → `'… — BertOS'`
7. `:155` `'/cookbook': 'Cookbook — Odysseus'` → `'… — BertOS'`
8. `:156` `'/email': 'Email — Odysseus'` → `'… — BertOS'`
9. `:157` `'/memory': 'Memory — Odysseus'` → `'… — BertOS'`
10. `:158` `'/gallery': 'Gallery — Odysseus'` → `'… — BertOS'`
11. `:159` `'/tasks': 'Tasks — Odysseus'` → `'… — BertOS'`
12. `:160` `'/library': 'Library — Odysseus'` → `'… — BertOS'`
13. `:170` `name: (titles[path] || 'Odysseus')` → `… || 'BertOS')` (per-route PWA fallback name)
14. `:171` `short_name: (titles[path] || 'Odysseus')...` → `… || 'BertOS')...`
15. `:499` `<label>Odysseus Logo</label>` (theme editor "brand color" picker label) → `<label>BertOS Logo</label>`
16. `:686` `<span class="sidebar-brand-title">Odysseus</span>` → `BertOS` (**sidebar wordmark — high visibility**)
17. `:938` `<h1 class="a11y-visually-hidden">Odysseus</h1>` → `BertOS` (screen-reader page heading)
18. `:941` `<span id="current-meta">Odysseus Chat</span>` → `BertOS Chat` (top-bar default chat title)
19. `:943` `…</svg>Odysseus</div>` (welcome-screen wordmark, inside `.welcome-name`) → `…</svg>BertOS`
20. `:999` `placeholder="Message Odysseus..."` → `placeholder="Message BertOS..."`
21. `:1682` `<span class="vis-label">Odysseus <span class="vis-hint">Brand name</span></span>` → `BertOS <span…>Brand name</span>` (settings → UI-visibility "Brand name" row label)
22. `:1993` admin-toggle help text `…clickable links back to Odysseus inside outgoing reminder…` → `…back to BertOS…` (admin settings copy)

   - `:13` (comment "Odysseus theme") and `:17` `window._odysseusLoadTime`, `:103/:212` JS identifiers — **NON-user-facing, leave.**
   - `:6, :41, :58, :104, :538, :539` `#e06c75` and `:8` `#282c34` — brand **color**, see §E.

### B3. `static/login.html` (login page — user-facing before auth)
23. `:6` `<title>Odysseus — Login</title>` → `<title>BertOS — Login</title>`
24. `:253` `…</svg><span>Odysseus</span>` (login wordmark) → `…<span>BertOS</span>`
   - `:23, :88, :137, :311, :458, :459` are localStorage keys / comments — **NON-user-facing, leave** (see §D alias list re `odysseus-last-user`).

### B4. `static/app.js` (runtime JS-rendered strings)
25. `:354` `const sessionName = meta ? meta.name : 'Odysseus Chat';` → `'BertOS Chat'` (fallback session title shown in tab/UI)
26. `:2214` `… : 'Message Odysseus...';` (responsive placeholder twin of index.html:999) → `'Message BertOS...'`
   - `:2` comment; `:80, :1055, :1318, :1651, :2475, :2754, :3433-3435, :3484, :3647, :4010-4012, :4171, :4173` — `window.__odysseus*` / `startOdysseusApp` / localStorage keys — **NON-user-facing, leave.**

### B5. `src/agent_loop.py` (agent self-identity — MODEL-VISIBLE system prompt)
27. `:140` `- You are running INSIDE Odysseus — there is no OpenWebUI…` → `…INSIDE BertOS —…` (**the core self-identity line**)
28. `:190` `…Works from any machine that can reach the Odysseus UI…` → `…the BertOS UI…`
29. `:360` `GENERIC LOOPBACK to allowed Odysseus internal endpoints.` → `…allowed BertOS internal endpoints.`
   - `:4` module docstring "Streaming agent loop for odysseus-ui." — comment/identifier, **NON-user-facing, leave** (also ties to service id).

### B6. `src/tool_index.py` (MODEL-VISIBLE tool description)
30. `:156` `app_api`: `"Generic loopback to allowed Odysseus internal endpoints…"` → `"…allowed BertOS internal endpoints…"`
   - `:87` `COLLECTION_NAME = "odysseus_tool_index"` — **vector-DB collection name, NON-user-facing & a data-migration hazard — DO NOT rename** (renaming orphans the existing embedding collection).

### B7. `src/tool_schemas.py` (MODEL-VISIBLE tool descriptions)
31. `:532` `manage_calendar` desc: `"…the tool creates the Odysseus note reminder…"` → `"…the BertOS note reminder…"`
32. `:552` `reminder_minutes` desc: `"…create an Odysseus reminder this many minutes before…"` → `"…a BertOS reminder…"`
33. `:953` `app_api` desc: `"Generic loopback to allowed internal Odysseus endpoints…"` → `"…internal BertOS endpoints…"`

### B8. `README.md` (repo front page)
34. `:1` `# Odysseus` → `# BertOS`
   - `:3` repo URL `pewdiepie-archdaemon/odysseus` — **alias-breaker, only change when re-homed** (see §D).

### B9. `odysseus-ui.service` (systemd unit — Description is user-facing in `systemctl status`)
35. `:4` `Description=Odysseus UI` → `Description=BertOS UI`
   - **DO NOT rename the file** `odysseus-ui.service` nor the `odysseus-ui` service id in `:1, :11, :12, :15` install-path comments — that id is referenced by `scripts/claim_ownerless.py:96` and is the systemd unit name. See §D.

---

## C. NON-user-facing "Odysseus" hits — explicitly DO NOT touch in Phase 1

These rendered in NO UI and are either identifiers, keys, log/comment strings, or test fixtures. Renaming them is pure risk (breaks persisted state, vector collections, service wiring) with zero user-visible benefit:

- **localStorage keys** (`static/js/theme.js:35-36`, `app.js` ×7, `login.html`, `init.js`, `tourHints.js`, `tourAutoplay.js`): `odysseus-theme`, `odysseus-custom-themes`, `odysseus-model-sort`, `odysseus-tool-splash-counts`, `odysseus-ui-visibility`, `odysseus-toolbar-visibility`, `odysseus-doc-open-*`, `odysseus-last-user`, `odysseus-auth-user`, `odysseus-hint-*`, `odysseus-tour-autoplay-seen-*`. → renaming wipes user state (see §D).
- **Code identifiers / globals**: `window.__odysseus*`, `startOdysseusApp`, `window._odysseusLoadTime`, `window.odysseusInitMermaid`.
- **CSS class names**: `.odysseus-highlight`, `.odysseus-hl-label` (`style.css:5642, 5770`) — internal selectors, not text.
- **Vector collection**: `tool_index.py:87` `odysseus_tool_index`.
- **47 `ODYSSEUS_*` env var NAMES** across 29 .py files (see §D).
- **Comments / docstrings / log strings**: `index.html:13`, `app.js:2`, `agent_loop.py:4`, `sw.js:1`, `style.css:2`, README body prose.
- **Test fixtures**: `tests/test_setup_admin_user.py` etc. set `ODYSSEUS_*` envs.

---

## D. ALIAS-BREAKERS — do NOT blindly rename; safe handling per item

### D1. `ODYSSEUS_*` env vars — **47 distinct names**, read in 22 distinct `os.getenv(...)` call-sites across 29 `.py` files
Full set: `ODYSSEUS_ADMIN_PASSWORD, ODYSSEUS_ADMIN_USER, ODYSSEUS_ALLOW_PRIVATE_CALDAV, ODYSSEUS_AMD_TEST_IMAGE, ODYSSEUS_API_TOKEN, ODYSSEUS_CHAT_UPLOAD_MAX_BYTES, ODYSSEUS_CMD_EXIT, ODYSSEUS_COPILOT_API_VERSION, ODYSSEUS_COPILOT_CLIENT_ID, ODYSSEUS_COPILOT_EDITOR_VERSION, ODYSSEUS_COPILOT_INTEGRATION_ID, ODYSSEUS_COPILOT_USER_AGENT, ODYSSEUS_DATA_DIR, ODYSSEUS_DEMO_MAIL_DIR, ODYSSEUS_DIFFUSION_IMPORT_ERROR, ODYSSEUS_DISABLE_MCP, ODYSSEUS_FALLBACK_OWNER, ODYSSEUS_GALLERY_TRANSFORM_UPLOAD_MAX_BYTES, ODYSSEUS_GALLERY_UPLOAD_MAX_BYTES, ODYSSEUS_HOST, ODYSSEUS_IMAP_TIMEOUT_SECONDS, ODYSSEUS_INPROCESS_POLLERS, ODYSSEUS_INPROCESS_TASKS, ODYSSEUS_INTERNAL_BASE, ODYSSEUS_INTERNAL_TOKEN, ODYSSEUS_MAIL_ATTACHMENTS_DIR, ODYSSEUS_MAIL_ORIGIN, ODYSSEUS_MEMORY_IMPORT_MAX_BYTES, ODYSSEUS_NO_OPEN, ODYSSEUS_OLLAMA_HOST, ODYSSEUS_OLLAMA_PORT, ODYSSEUS_OLLAMA_URL, ODYSSEUS_PERSONAL_UPLOAD_MAX_BYTES, ODYSSEUS_PORT, ODYSSEUS_PREFLIGHT_EXIT, ODYSSEUS_SCRIPT_HOST, ODYSSEUS_SGLANG_IMPORT_ERROR, ODYSSEUS_SINGLE_USER, ODYSSEUS_SKIP_ADMIN_PROMPT, ODYSSEUS_SKIP_RUN_HINT, ODYSSEUS_URL, ODYSSEUS_USER_PATH, ODYSSEUS_USER_SHELL, ODYSSEUS_VLLM_BIN, ODYSSEUS_VLLM_VERSION` (+ the dynamic `ODYSSEUS_PATH__*` / `ODYSSEUS_USER_PATH` prefix family).

- **Definition pattern**: NO central config module. Reads are scattered `os.getenv("ODYSSEUS_X", default)` (e.g. `src/constants.py:10` `ODYSSEUS_DATA_DIR`, `setup.py:92` `ODYSSEUS_ADMIN_USER`). Only `DATA_DIR` is centralized (`src/constants.py` is "the ONLY place ODYSSEUS_DATA_DIR is read").
- **Risk**: blind rename breaks every existing `.env`, `docker-compose*.yml`, `odysseus-ui.service` EnvironmentFile, and the 22 read-sites simultaneously.
- **SAFE PLAN (BERTOS_* with ODYSSEUS_* fallback)**: introduce a tiny helper, e.g. `def env(name, default=None): return os.getenv("BERTOS_"+name) or os.getenv("ODYSSEUS_"+name, default)`, then migrate read-sites to `env("DATA_DIR", …)`. Because there's no single getter, this is a **multi-file mechanical change, not a Phase-1 rebrand item** — defer or do as a separate typed pass. Phase 1 (user-facing) does NOT require touching any env var. `ODYSSEUS_PORT`/`ODYSSEUS_HOST` are actually only consumed in shell/service/compose (the uvicorn `--port`), never in `.py`.

### D2. `odysseus-ui` service id
- Lives in: `odysseus-ui.service` (filename + `:11, :12, :15` paths) and is hard-referenced by **`scripts/claim_ownerless.py:96`** `print("Restart the server: sudo systemctl restart odysseus-ui")`.
- **SAFE**: rename ONLY the Description (`:4`, edit #35). Keep the unit id `odysseus-ui` everywhere unless you also rename the actual systemd unit on the host AND update `claim_ownerless.py:96` in lockstep. Mismatched id = broken restart instructions. Defer.

### D3. `localStorage['odysseus-theme']` (+ sibling keys)
- Defined `static/js/theme.js:35` `const LS_KEY = 'odysseus-theme'`; read in `index.html:20, :103`, `login.html:23`. Sibling keys in §C.
- **Risk**: renaming the key silently wipes every saved theme / last-user / tour-seen flag for existing installs.
- **SAFE**: **keep the key string `odysseus-theme` unchanged.** (If a clean key is ever wanted, add one-time migration: on boot, if `bertos-theme` absent and `odysseus-theme` present, copy then keep both.) For Phase 1: do nothing to these keys.

### D4. OpenRouter attribution header — `src/endpoint_resolver.py:216-217`
```
headers.setdefault("HTTP-Referer", "https://github.com/pewdiepie-archdaemon/odysseus")
headers.setdefault("X-OpenRouter-Title", "Odysseus")
```
- This is provider-facing attribution (shows in OpenRouter's dashboard), not end-user UI.
- **SAFE**: update the URL (`:216`) and title (`:217`) **only when the repo is re-homed** to BertOS's GitHub. Same for `README.md:3` repo URL. Until re-home, leave both pointing at upstream (changing the URL to a non-existent repo is worse than leaving it).

---

## E. THEME — how it works + exact default-brand change (gold "Praetorium/Jarvis")

### How theming works
There is **no `:root` CSS block**. Defaults come from two places:
1. **JS default theme object** — `static/js/theme.js:11-32` `THEMES`, with `DEFAULT_THEME = 'dark'` (`:34`). The `dark` preset (`:12`) is: `bg:#282c34, fg:#9cdef2, panel:#111111, border:#355a66, red:#e06c75`. When localStorage is empty, `theme.js` applies `THEMES['dark']` (`:921, :740`).
2. **Inline pre-paint script** — `index.html:17-95`: reads `localStorage['odysseus-theme']`, sets CSS vars (`--bg, --fg, --panel, --border, --red`) and derives `--brand-color` (`:33-34`: `c.advanced.brandColor || c.red`). Hardcoded fallbacks where no theme exists: `#282c34` (bg, in `var(--bg,#282c34)` and `meta theme-color :8`) and `#e06c75` (accent/red, at `:6, :41, :58, :104`).

The **welcome wordmark and sidebar wordmark color** come from `--brand-color` → falls back to `--red` (`style.css:1858` welcome-name gradient, `:1870` welcome-boat, loader `index.html:227`). So setting the default brand = setting `red` (and optionally `advanced.brandColor`) in the `dark` preset + the hardcoded fallbacks.

### EXACT default-brand change (dark charcoal/navy bg + gold accent), WITHOUT touching the localStorage key

Concrete hex values — **gold accent `#d4af37` (classic gold) on charcoal-navy `#0f1420`**, fg warm off-white `#e8e3d6`:

**E1. `static/js/theme.js:12`** — redefine the `dark` preset (this is the no-saved-theme default):
```
dark:  { bg:'#0f1420', fg:'#e8e3d6', panel:'#161c2b', border:'#2a3344', red:'#d4af37' },
```
(`red` is the accent channel the whole UI keys off; `#d4af37` becomes the gold wordmark/accent. Optionally add `advanced:{ brandColor:'#d4af37' }` to pin the wordmark explicitly.)

**E2. `static/index.html` hardcoded fallbacks** (so first paint before any theme is gold-on-charcoal, no flash):
- `:8` `<meta name="theme-color" content="#282c34">` → `content="#0f1420"`
- `:41` `var ac = c.red || '#e06c75';` → `|| '#d4af37';`
- `:58` `rH=h2hsl(c.red||'#e06c75')` → `||'#d4af37')`
- `:104` `… || '#e06c75';` → `|| '#d4af37';`
- `:6` favicon SVG `fill='%23e06c75'` ×2 + `stroke='%23e06c75'` → `%23d4af37` (default tab icon color)
- `:226-227` loader `var(--bg,#282c34)` → `#0f1420`; loader-wave `var(--brand-color,var(--red,#e06c75))` → `…#d4af37)`
- `:538-539` theme-editor default picker value `#e06c75` → `#d4af37` (cosmetic; the harmony picker's initial swatch)

**E3. `static/manifest.json:9-10`** — PWA splash/toolbar color:
```
"background_color": "#0f1420",
"theme_color": "#0f1420",
```

**E4. (optional, for parity) `static/login.html`** uses `#282c34`/gradient too — set its fallback bg to `#0f1420` if you want the login page to match before theme load (grep `282c34`/`e06c75` in login.html first; not enumerated here since login wasn't in the brief's theme scope).

**Do NOT** change `theme.js:35 LS_KEY = 'odysseus-theme'`. Existing users with a saved theme keep it; only *fresh* browsers (empty localStorage) get the new BertOS gold/charcoal default. If you want existing users force-migrated to the new default, that's a separate decision (would need a version bump + reset, out of Phase-1 rebrand scope).

---

## F. Risks / uncertainties flagged

1. **`sw.js` CACHE_NAME `odysseus-v327`** (`static/sw.js:10`): NON-user-facing, but after ANY of these edits you MUST bump it (e.g. `-v328`) or the service worker serves the old cached `index.html`/`manifest.json` and users won't see the rebrand. Not a rename — a **required cache-bust**. (Comment `:1` "Odysseus PWA Service Worker" can stay.)
2. **`tool_index.py:87` `odysseus_tool_index`** vector collection — flagged DO-NOT-TOUCH; renaming orphans embeddings. Confirm with lead before any later cleanup.
3. **Brand color choice**: I picked `#d4af37` gold / `#0f1420` charcoal-navy / `#e8e3d6` off-white as concrete defaults matching the "Praetorium/Jarvis gold-on-dark" brief. The derived syntax-highlight HSL math (`index.html:58-69`) keys off `--red`; gold (`#d4af37`, hue ~46°) will shift the auto-derived keyword/string/function colors warm. **Verify the derived code-block palette looks acceptable** after the change — it's algorithmic, not hand-tuned for gold.
4. **og:/twitter: meta**: none exist in index.html — nothing to rebrand for social cards. If link-preview branding is wanted, that's a new addition, not an edit.
5. **`welcome-sub` (`index.html:944`)** says "Welcome, type /setup to get started" — no "Odysseus" text, no change needed; left as-is.
6. Env-var BERTOS_*-fallback (§D1) and service-id rename (§D2) are **explicitly deferred out of Phase 1** — they're plumbing, invisible to users, and high-blast-radius. Phase 1 rebrand (edits #1–#35 + theme §E + sw.js bump) is self-contained and touches zero env/service wiring.