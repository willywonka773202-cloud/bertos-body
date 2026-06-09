I'll synthesize the maps and adversarial verdicts into the Phase-1 plan. The verdicts contain critical corrections — especially the guardrail one, which proves the "gate resolve_endpoint" strategy alone leaks. Let me fold those in.

---

# BertOS Phase-1 Execution Plan
**Repo:** `/Users/willlambert/Documents/odysseus` · branch `bertos`

## 1. RECOMMENDED BASE

**Base on `dev` (the current `bertos` checkout, HEAD `62ffcb6`)** — every master-prompt anchor matches it line-for-line, it carries +184 commits of Phase-1 prerequisites (the `CHROMA_DIR` constant and `agent_loop` rewrite that `main` lacks entirely), and `main`'s only exclusive value is 4 isolated security hotfixes that touch zero Phase-1 files. **First action in Slice 0:** cherry-pick `d4ff7fc f5c9095 3738df3 7367325` onto `bertos` to close the 4-commit security gap (clean, no Phase-1 conflict).

---

## 2. ORDERED SLICES

Dependency order: **0 boot harness → 1 rebrand → 2 memory→vault → 3 guardrail → 4 daily-brief.** Rationale for this order: rebrand is isolated and de-risks the harness (proves edit→cache-bust→observe loop). Memory must precede daily-brief (brief reads memory via the vault). Guardrail must precede daily-brief because the brief's "free-only unattended" promise depends on it — BUT the recommended daily-brief path (deterministic action, no LLM) makes the guardrail *non-blocking* for the brief itself; guardrail still ships first because it's the larger surface and the brief's verification asserts no paid leak.

---

### SLICE 0 — Boot locally as a verification harness
**This is the gate for every later slice. Nothing is "done" until it boots and you can curl it.**

**(a) Changes (file:line checklist):**
- `git cherry-pick d4ff7fc f5c9095 3738df3 7367325` onto `bertos` (the 4 security hotfixes; clean).
- No source edits. Build the venv with **arm64 Homebrew Python 3.11** — NOT system 3.9.6. The 3.9.6 blocker is hard-confirmed: `src/secret_storage.py:34` `_fernet: Fernet | None = None` is a module-scope PEP 604 annotation with no `from __future__ import annotations` → `TypeError` at import time on 3.9.
- Add to `.env` for unattended verification: `LOCALHOST_BYPASS=true` (`app.py:157-159`, loopback-only auth skip).

**(b) Verification — how we RUN it, what REAL output proves it:**
```bash
cd /Users/willlambert/Documents/odysseus
./start-macos.sh          # creates arm64 Py3.11 venv, installs, auto-fixes chromadb-client→chromadb, launches uvicorn:7860
```
- Success line in logs: **`Application startup complete`** (warnings `MCP startup failed (non-critical)`, `Tool index warmup failed`, `Warmup ping failed` are EXPECTED with no Ollama/Chroma).
- From a second shell, the proof is **HTTP, not logs**:
  - `curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:7860/api/health` → **200** (`{"status":"healthy"}`)
  - `curl -s http://127.0.0.1:7860/api/ready` → **200** `{"ready":true,...}` (proves SQLite reachable + `data/` writable)
  - `curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:7860/login` → **200**
- URL is **7860**, not 7000 (macOS AirPlay holds 7000). Fallback if `start-macos.sh` misbehaves: manual venv with `/opt/homebrew/bin/python3.11`, then `pip uninstall chromadb-client -y && pip install --force-reinstall chromadb`, then `python -m uvicorn app:app --host 127.0.0.1 --port 7860`.

**(c) Definition of Done:** `./start-macos.sh` reaches `Application startup complete` and all three curls return 200 from a clean checkout on Python 3.11.

---

### SLICE 1 — Rebrand Odysseus → BertOS (user-facing only)
**Isolated, zero env/service/plumbing changes. The env-var rename (BERTOS_* fallback) and service-id rename are explicitly DEFERRED out of Phase 1.**

**(a) Changes (file:line checklist — top-to-bottom per file so line numbers don't shift):**
- `static/manifest.json`: `:2` name, `:3` short_name → `BertOS`; `:9-10` `background_color`/`theme_color` → `#0f1420` (theme, below).
- `static/index.html`: `:5` title→`BertOS Chat`; route-title map `:153-160` (8 lines `— Odysseus`→`— BertOS`); `:170-171` PWA fallback `|| 'BertOS'`; `:499` logo label; `:686` sidebar wordmark; `:938` a11y heading; `:941` `BertOS Chat`; `:943` welcome wordmark; `:999` placeholder; `:1682` brand-name row; `:1993` admin help text.
- `static/login.html`: `:6` title; `:253` wordmark. (Leave localStorage keys at `:23,:88,:137,:311,:458-459`.)
- `static/app.js`: `:354` `'BertOS Chat'`; `:2214` `'Message BertOS...'`. (Leave all `window.__odysseus*`/`startOdysseusApp`/LS keys.)
- `src/agent_loop.py` (MODEL-VISIBLE): `:140`, `:190`, `:360`.
- `src/tool_index.py`: `:156` only. **DO NOT touch `:87` `COLLECTION_NAME = "odysseus_tool_index"`** (renaming orphans the vector collection).
- `src/tool_schemas.py`: `:532`, `:552`, `:953`.
- `README.md:1` `# BertOS`. **Leave `:3` repo URL** until re-homed.
- `odysseus-ui.service:4` `Description=BertOS UI` only. **DO NOT rename the file or the `odysseus-ui` service id** (`scripts/claim_ownerless.py:96` hard-references it).
- **Theme** (gold-on-charcoal default, no LS-key change): `theme.js:12` redefine `dark` preset `{bg:'#0f1420',fg:'#e8e3d6',panel:'#161c2b',border:'#2a3344',red:'#d4af37'}`; `index.html` hardcoded fallbacks `:8,:41,:58,:104,:6,:226-227,:538-539` (`#282c34`→`#0f1420`, `#e06c75`→`#d4af37`); `manifest.json:9-10`. **Keep `theme.js:35 LS_KEY='odysseus-theme'`** unchanged.
- **MANDATORY cache-bust:** `static/sw.js:10` bump `CACHE_NAME` `odysseus-v327`→`v328` or the service worker serves stale `index.html`/`manifest.json` and the rebrand never appears.

**(b) Verification — how we RUN it, what REAL output proves it:**
- Restart uvicorn, hard-reload (the sw bump forces it). Then **observe rendered DOM, not source**:
  - `curl -s http://127.0.0.1:7860/ | grep -c Odysseus` on user-facing strings → should drop to 0 in rendered HTML (`<title>`, sidebar wordmark, placeholder).
  - `curl -s http://127.0.0.1:7860/login | grep -i '<title>'` → contains `BertOS`.
  - Browser: tab title = `BertOS Chat`, sidebar wordmark = `BertOS`, input placeholder = `Message BertOS...`, **wordmark renders gold (`#d4af37`) on charcoal (`#0f1420`)** with no white-flash on first paint (confirms hardcoded fallbacks took).
  - Screenshot the welcome screen + sidebar as the real artifact.
- Verify the derived syntax-highlight palette (HSL math at `index.html:58-69` keys off `--red`; gold hue ~46° shifts code colors warm) is acceptable — eyeball a code block.

**(c) Definition of Done:** A hard-reloaded browser shows zero user-facing "Odysseus" strings, gold-on-charcoal default theme with no first-paint flash, and `/api/health` still 200.

---

### SLICE 2 — Memory → Obsidian-vault backend
**SCOPE-TRAP CORRECTION (folded in): "reimplement only 5 methods" is a TRAP. The full `MemoryManager` surface plus a non-method attribute and out-of-band consumers must be preserved.**

**FULL MemoryManager surface to reimplement (all of these, not 5):** `load_all`, `load(owner=)`, `save`, `add_entry`, `get_relevant_memories` **PLUS** `find_duplicates`, `increment_uses`, `claim_ownerless`, `ensure_file_exists`, `extract_memory_from_chat`, `process_inline_memory_command`, `_validate_entries`, `_migrate_from_legacy` — and **keep the `self.memory_file` ATTRIBUTE** (`memory_extractor.py:29` does `os.path.dirname(memory_manager.memory_file)` → method-only reimpl throws `AttributeError`). `categorize_memory_by_relevance` has no live caller but `claim_ownerless` IS exercised by `tests/test_memory_imports.py:53` — do NOT stub it to raise.

**The real contract is the entry dict, and it is STRICTER than "named fields":** `memory_provider.py:118-122` `_to_record` preserves **EVERY** key not in `_CORE_FIELDS` as metadata, and `backup_routes.py:87-98` appends arbitrary external dicts. So `save()` must **preserve ALL keys, known or unknown** — round-tripping `id/text/timestamp/source/category/uses/owner/session_id/pinned/metadata` AND any custom key. Callers mutate fields in-place then `save()` (`memory_routes.py:490` `pinned`, `:513-516` text/category/timestamp; `ai_interaction.py:1024`; `memory_provider.py:160`). `session_id` is a **query key**, not just blob (`memory_routes.py:138,186`; `service.py:58`) — losing it breaks session-scoped listing endpoints.

**KeyError coupling:** `find_duplicates:253` AND `get_relevant_memories:267/286/324/...` index `entry['text']` with brackets (not `.get`). `load()/load_all()` MUST guarantee every returned entry has a string `text` (an empty/frontmatter-only `.md` in the 721-file human-edited vault would crash dedup AND retrieval).

**(a) Changes (file:line checklist):**
- `src/constants.py` near `:17`/`:44`: add `MEMORY_VAULT_DIR = os.getenv("BERTOS_OBSIDIAN_VAULT", ~/Documents/BertOS-Vault)`. **DO NOT mirror into `core/constants.py`** — it is a `from src.constants import *` shim; mirroring creates the exact drift it exists to kill (verdict correction).
- `src/memory.py`: reimplement `MemoryManager` against the vault while keeping the in-memory entry-dict shape and `self.memory_file` attribute. Map on read: vault `kind`→`category`, `ts`(ISO)→`timestamp`(int), `projectId`→owner partition, body→`text`. On write: synthesize frontmatter; **invent frontmatter homes for `uses`/`pinned`/`session_id`/`owner`/unknown keys** or those features regress (`increment_uses` no-ops, `chat_processor.py:208` pinned injection dies, session listing breaks).
- Reconcile the 4 out-of-band consumers (dual-write `memory.json` OR route through MemoryManager): `scripts/claim_ownerless.py:40`, `core/database.py:1160-1175`, `routes/admin_wipe_routes.py:42-47` (must wipe vault too), `scripts/migrate_faiss_to_chroma.py:69`.
- Untouched-but-dependent: `app_initializer.py:65` Chroma rebuild needs `load()` to keep yielding `{id,text}`; vault ids are slugs not uuids → **id-stability risk** for the `search→by_id` join (`memory_provider.py:177-185`).

**(b) Verification — how we RUN it, what REAL output proves it:**
- **Unit (no app):** `./venv/bin/pytest tests/test_memory_imports.py tests/test_claim_ownerless_json.py -v` → green proves the import/attribute/`claim_ownerless` contracts hold. Add a round-trip test: construct an entry with a custom key + `pinned`+`session_id`+`uses`, `save()`, `load_all()`, assert ALL keys survive.
- **Live round-trip via running app (Slice 0 harness):**
  - `curl -s -X POST http://127.0.0.1:7860/api/memory -H 'Content-Type: application/json' -d '{"text":"phase1 vault probe","category":"fact"}'` → 200.
  - Confirm a NEW `.md` file appears under `~/Documents/BertOS-Vault/Memory/<projectId>/` with correct frontmatter (`ls -t` the dir; read the file).
  - `curl -s 'http://127.0.0.1:7860/api/memory'` → the probe entry returns with `id` and `text` intact.
  - Pin it (`POST /api/memory/{id}/pin`), re-fetch, assert `pinned:true` survived (proves the round-trip contract). Then restart uvicorn and re-fetch — proves vault persistence, not in-memory.
- Boot must still reach `Application startup complete` (Chroma rebuild consumes `load()`).

**(c) Definition of Done:** Creating a memory writes a real `.md` to the vault, all custom/`pinned`/`session_id`/`uses` keys survive a save→load→restart→load cycle, and `pytest tests/test_memory_imports.py` is green.

---

### SLICE 3 — Free-first cost guardrail
**SCOPE-TRAP CORRECTION (folded in): the adversarial verdict proves "gate `resolve_endpoint` and you've covered the unattended surface" is FALSE. The biggest money-spending background path — the scheduled-task executor — selects endpoints WITHOUT ever calling `resolve_endpoint`. The authoritative gate must ALSO sit at the dispatch choke point. Below is the COMPLETE call-site + leak list.**

**(a) Changes (file:line checklist):**

**Core mechanism:**
- `src/constants.py` after `:79`: `ALLOW_PAID` — but read `os.getenv("BERTOS_ALLOW_PAID")` **live inside `_paid_blocked`** (not import-time), so the kill-switch can hot-flip and tests can monkeypatch.
- `core/database.py`: add `is_paid = Column(Boolean, nullable=True, default=None)` after `:359`; new `_migrate_add_is_paid_column()` after `:917` (mirror `_migrate_add_supports_tools_column`); register in `init_db()` at `:1641`.
- `src/endpoint_resolver.py`: add helpers `_endpoint_is_paid(ep)` (NULL→derive from `endpoint_kind`+host), `_host_is_paid(base)`, `_paid_blocked(ep, free_only)`. Add `free_only=False` to `resolve_endpoint` (`:227`), `resolve_endpoint_by_id` (`:321`), `_resolve_fallback_candidates` (`:393`) + 3 wrappers (`:365/375/388`). **Enforce gate at BOTH `:288` and `:339`** (return fallback tuple, don't raise). Thread `free_only` at `:404`.
- Paid hosts (fail-CLOSED on unknown): openai/anthropic/openrouter/googleapis/x.ai/mistral/deepseek/together/fireworks/groq/opencode-zen/**ollama.com (cloud, paid)**. Free: chatgpt-subscription, copilot, native Ollama, localhost/127/::1/0.0.0.0.

**`free_only=True` at every UNATTENDED `resolve_endpoint` site:** `task_scheduler.py:1688`; `email_pollers.py:288,290`; `builtin_actions.py:119,121,605,607,879,881,1139,1492,1494` (the entire scheduled-action surface — the master prompt's biggest miss); `session_routes.py:188`; `context_compactor.py:357`.

**Thread `free_only` through the 3 MIXED helpers (True on scheduler path, False on HTTP route):** `skills_routes._resolve_audit_models:994/1003`; `task_endpoint.resolve_task_endpoint:6/13`; `note_routes.dispatch_reminder:138/211/213`.

**LEAKS THE VERIFIER FOUND that bypass `resolve_endpoint` entirely (these are the load-bearing additions — `resolve_endpoint` gating does NOT cover them):**
1. **HIGH — `src/task_scheduler.py:1294-1300/1832-1846/1439-1453` (`_execute_llm_task` + `_resolve_defaults` + `_execute_checkin`).** The primary scheduled-task executor picks endpoint via raw `task.endpoint_url` → `crew.endpoint_url` → `_resolve_defaults()` (raw `db.query(DbSession).filter(endpoint_url.isnot(None)).order_by(created_at.desc()).first()`). None call `resolve_endpoint`. If the user's last chat used a paid endpoint, every scheduled task silently uses it. **Gate where `endpoint_url` is consumed** — add `_paid_blocked` at the `llm_call_async`/`stream_llm` dispatch (`:1398/:1655`) or route all three sources through a paid-check.
2. **HIGH — `src/ai_interaction.py:88-148 `_resolve_model`.** A full selection function (queries `is_enabled`, probes `/models`, returns built URL) — mislabeled "runtime/N/A" in the map. Reached unattended via `teacher_escalation._call_teacher` inside the agent loop (`agent_loop.py:2670`) which runs scheduled tasks. Add `_paid_blocked`.
3. **HIGH — `routes/skills_routes.py:1023` teacher resolution.** `_resolve_audit_models` ALSO resolves a teacher via `_resolve_model` (the ungated selector) — gating only `:1003` leaves the most expensive half of the nightly audit open. Gate the teacher too.
4. **MED — `routes/chat_helpers.py:201-260 `try_fallback_endpoint`.** Own `db.query(ModelEndpoint).filter(is_enabled==True).all()` for mid-stream failover — bypasses everything. With kill-switch OFF a failure could fail over to paid. Add `_paid_blocked` at its selection point.
5. **MED — `src/tool_execution.py:1357-1362` + `tool_index.py:403` (`chat_with_model`/`ask_teacher` agent tools).** RAG-selectable into an unattended scheduled agent's toolset → paid model outside any gate. Gate via `_resolve_model`.
6. **MED — `routes/research_routes.py:411,440,602 `_owned_enabled_endpoint` (THREE sites, not one).** Each does direct `is_enabled` select + build. All three need `_paid_blocked`.
7. **MED — `routes/webhook_routes.py:312 `sync_chat`.** API-token machine path selects via `_select_api_chat_fallback_endpoint` + direct queries. Highest-risk machine bypass. Add `_paid_blocked`.
8. **LOW — `src/ai_interaction.py:1617-1647` image-model fallback** (direct `model_type=='image'` select); **`src/session_actions.py:155`** SECOND unattended `resolve_task_endpoint` caller the map omitted; **`session_routes.py:200`** second `resolve_endpoint('default')` in the same fn.

**Defense-in-depth:** assert-style `_paid_blocked` guard in `llm_core.llm_call_async:1239`/`stream_llm:1399` — the single choke point that catches every direct-build bypass caller. With this, even an un-enumerated leak fails closed when `ALLOW_PAID=0`.

**(b) Verification — how we RUN it, what REAL output proves it:**
- **Unit (authoritative, no provider call):** new `tests/test_free_only_guardrail.py` modeled on `tests/test_resolve_endpoint_fallbacks.py` (`_install_resolver_fakes`). `./venv/bin/pytest tests/test_free_only_guardrail.py -v` must prove: (a) `free_only=True` + paid → resolves the FREE default, `assert "openrouter.ai" not in url`; (b) switch OFF + attended → also blocked (global kill-switch); (c) switch ON + attended → paid resolves (no regression); (d) `is_paid=None` derive: `endpoint_kind="api"`+anthropic host → paid, `="local"` → free; (e) paid entry dropped from `_resolve_fallback_candidates` under `free_only`.
- **The bypass leaks need integration proof, not just the unit test** (that's the whole point of the verdict): with `BERTOS_ALLOW_PAID=0`, seed ONE paid endpoint + one local, force-run a scheduled LLM task (`POST /api/tasks/{id}/run`), and assert the `TaskRun.result`/logs show the **local** endpoint was dispatched, never the paid host. Repeat for `try_fallback_endpoint` (kill the primary mid-stream) and `webhook sync_chat` (API-token call). **Trust the dispatched URL in the run record, not a "guardrail active" log line.**

**(c) Definition of Done:** With `BERTOS_ALLOW_PAID=0`, no path — `resolve_endpoint`, the scheduler `_resolve_defaults`, `_resolve_model`, `try_fallback_endpoint`, the agent tools, research, or webhook — can dispatch to a paid host; proven by green `test_free_only_guardrail.py` AND an integration run showing a forced scheduled task hit the local endpoint.

---

### SLICE 4 — Daily brief → ntfy
**KEY FINDING: the gather logic already exists.** `action_daily_brief` (`src/builtin_actions.py:986-1112`), registered `"daily_brief"` at `:2217`/`:2239`, already collects today's calendar + unread email + todos and returns formatted text. It calls **no LLM** (not in `_MODEL_BACKED_ACTIONS`), so the Slice-3 guardrail is **moot for the recommended path** — there's no model to constrain. Phase-1 work = register a cron task + route the action's output to ntfy.

**(a) Changes (file:line checklist):**
- **Register the task** — add a `HOUSEKEEPING_DEFAULTS` entry `src/task_scheduler.py:205` (seeded, idempotent per owner), mirroring `summarize_emails:210`, likely `ship_paused: True`: `task_type="action"`, `action="daily_brief"`, `schedule="cron"`, `cron_expression="0 7 * * *"`, `owner=<user>`. (Or `POST /api/tasks`.)
- **Gap A (the ONE real wiring change) — action output → ntfy.** Today nothing reaches ntfy from an action: `_deliver_task_result:1412-1437` has no ntfy/notification branch, and the browser-notify gate `should_notify:824-827` requires `task_type in {"llm","research"}` (excludes actions). **Fix A1 (recommended):** at the end of `action_daily_brief` (`builtin_actions.py:~1112`, before return) add `await dispatch_reminder(title="Daily brief — <date>", note_body=plain_body, note_id="daily-brief", owner=owner)` — exactly the pattern `action_check_email_urgency:1903-1925` uses. Direct in-process call is MANDATORY: the HTTP route 401s the background scheduler (no session cookie) — confirmed by the inline comment at `:1900-1901`.
- **Dependency:** ntfy must be an enabled Integration (preset `"ntfy"`, `base_url` set, default `http://localhost:8091`); `reminder_ntfy_topic` set; `reminder_channel="ntfy"` (or force via `dispatch_reminder(settings_override={"reminder_channel":"ntfy"})`, supported `note_routes.py:144,160`).
- **Timezone caution:** cron `next_run` is UTC unless linked to a `CrewMember` with a `timezone` (`compute_next_run`/`_resolve_task_timezone:67-198`). "07:00 local" needs a crew-linked tz or a UTC-offset cron.

**(b) Verification — how we RUN it, what REAL output proves it (an OBSERVED push, not an exit code):**
1. Configure ntfy Integration (reachable `base_url`), set topic + `reminder_channel="ntfy"`.
2. Force-run: `curl -s -X POST http://127.0.0.1:7860/api/tasks/{id}/run` (`run_task_now:1920`), or set cron to next minute.
3. **Prove the push landed:** `curl -s "$NTFY_BASE_URL/<topic>/json?poll=1"` (documented poll endpoint, `integrations.py:101`) → a message appears with the brief body + `Title` header. Success ⇒ `dispatch_reminder` returned `ntfy_sent=True` (`note_routes.py:483,547`).
4. **Run record:** the `TaskRun` row is `status="success"`, `result`=brief text (`task_scheduler.py:715-717`).
5. **Content correctness:** brief contains today's REAL calendar events + real unread count/senders + real todos — cross-check against the calendar UI / IMAP. Note `_imap_connect(None)` uses the **default account only** (multi-account users undercount — flag, not a blocker).

**(c) Definition of Done:** Force-running the `daily_brief` task produces an observed ntfy message (confirmed via the poll endpoint) whose body matches today's real calendar/email/todos, and the `TaskRun` row is `success`.

---

## 3. TOP 3 RISKS / UNKNOWNS

1. **Guardrail bypass leaks are the real risk surface, not `resolve_endpoint`.** The adversarial verdict proved the scheduler's `_resolve_defaults` (raw "most recent session's endpoint") and `ai_interaction._resolve_model` dispatch to paid endpoints with zero gating. If we ship the map's `resolve_endpoint`-only plan, the single largest background money path stays wide open. **Mitigation:** the `llm_core` dispatch-level `_paid_blocked` guard (Slice 3 defense-in-depth) is non-optional — it's the only thing that fails-closed against an un-enumerated leak. Verify by integration run, never by log line.

2. **Memory→vault schema mismatch + owner↔projectId mapping is an undefined design gap.** The vault frontmatter has no `owner`/`uses`/`pinned`/`session_id` and uses `kind`/`ts`/`projectId` instead of `category`/`timestamp`/`owner`. The map's "preserve ALL keys including unknown" contract (`memory_provider.py:118`) means a naive `.md`-per-memory writer silently drops state, breaking pinned-injection, usage tracking, AND session-scoped listing endpoints. Vault ids are slugs, not uuids → Chroma id-join drift. **This needs a lead decision on owner↔projectId + extra frontmatter keys before coding, and a non-negotiable round-trip test.**

3. **Python/arch boot fragility + unpinned deps.** The 3.9.6 import-time crash is the hard floor (`secret_storage.py:34`), and an Intel/Rosetta Python silently builds wrong-arch wheels (`bcrypt`/`onnxruntime`/`cryptography`). Unpinned `numpy`/`fastembed`/`onnxruntime` resolve at install time — if `start-macos.sh` picks Python 3.13, the onnxruntime wheel may be unavailable (prefer 3.12). The whole plan rests on Slice 0 booting; a wrong-arch venv produces a deep, confusing import error, not a clean message. **Mitigation:** always `/opt/homebrew/bin/python3.11`; if onnxruntime fails, fall back to python@3.12.