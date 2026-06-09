I now have the complete picture, including the critical bypass: `webhook_routes.sync_chat` and `research_routes` select endpoints via direct DB queries (`_select_api_chat_fallback_endpoint`, `_owned_enabled_endpoint`) that **bypass `resolve_endpoint` entirely**. These are guardrail holes that must be addressed. I have everything needed to write the deliverable.

---

# Free-First Cost Guardrail — Phase-1 Integration Surface (Odysseus `bertos` branch)

Read-only recon complete. All anchors verified against actual code at `/Users/willlambert/Documents/odysseus`. Odysseus has **no cost concept today** — confirmed (no `is_paid`, no `BERTOS_ALLOW_PAID`, no cost grep hits anywhere).

## 1. `resolve_endpoint()` anatomy (`src/endpoint_resolver.py`)

**Signature** (`:221-227`):
```python
def resolve_endpoint(setting_prefix, fallback_url=None, fallback_model=None,
                     fallback_headers=None, owner=None) -> Tuple[Optional[str], Optional[str], Optional[Dict]]
```
Returns `(chat_url, model, headers)` — note the URL is the **built chat URL** (`build_chat_url(base)`, `:296`), not the raw base.

**Flow:**
- `:240-244` loads settings (returns fallback tuple on any exception).
- `:250-272` resolves `ep_id`/`model` through the role-cascade: `{prefix}_endpoint_id` → (for non-utility) `utility_*` → `default_*`. Early-returns the caller fallback at `:257-258` when no `ep_id` but a fallback url+model exist.
- `:274-275` returns fallback if still no `ep_id`.
- **DB-row selection block `:277-297`** (the heart): `SessionLocal()` → `db.query(ModelEndpoint).filter(id == ep_id, is_enabled == True)` (`:279-282`), owner-scoped via `owner_filter` (`:283-285`). `if not ep: return fallback` (`:288-289`). Then `resolve_endpoint_runtime(ep, owner)` (`:292`) → `build_chat_url` (`:296`) + `build_headers` (`:297`).
- `:299-311` model-pick (hidden-model discard + `_first_chat_model`).

**`resolve_endpoint_by_id()` (`:319-362`)** — same selection pattern (`:331-338`), used only by the fallback-chain builder.

**Fallback machinery:**
- `_resolve_fallback_candidates(setting_key, owner)` **def `:393-407`** — reads a settings list (`default_model_fallbacks` / `utility_model_fallbacks` / `vision_model_fallbacks`), and for each entry calls `resolve_endpoint_by_id(...)` (`:404`), appending resolvable ones.
- Public wrappers: `resolve_chat_fallback_candidates` (`:365-372`), `resolve_utility_fallback_candidates` (`:375-385`), `resolve_vision_fallback_candidates` (`:388-390`).
- There is **no function literally named `_resolve_fallback`** — the singular is `_resolve_fallback_candidates`. The actual paid→free *cross-tier* fallback the master prompt envisions does not exist yet; today's "fallback" is the role-cascade in `:257-272` plus the candidate list.

**Where `free_only: bool=False` gates the paid→free fallback — TWO seams:**

1. **Primary gate — row-selection block, `:288` (after `ep = ...first()`):** insert a paid-check. If `free_only and _endpoint_is_paid(ep)`: treat as a miss → `return fallback_url, fallback_model, fallback_headers` (so the role-cascade / caller fallback applies). Mirror identically in `resolve_endpoint_by_id` at `:339`.
2. **Fallback-chain gate — `_resolve_fallback_candidates` `:404-405`:** thread `free_only` into `resolve_endpoint_by_id(..., free_only=free_only)` so paid entries in `*_model_fallbacks` are dropped from the candidate list. Propagate `free_only` through the three public wrappers (`:365/375/388`) too.

`free_only` must be added to the signatures of `resolve_endpoint` (`:227`), `resolve_endpoint_by_id` (`:321`), `_resolve_fallback_candidates` (`:393`), and the three wrappers. Default `False` keeps all attended callers unchanged.

## 2. COMPLETE classified call-site table

Convention: **`resolve_endpoint_runtime` sites are NOT selection sites** — they resolve credentials for an *already-chosen* `ep` row, so the guardrail does not belong there (it belongs where the row is *selected*). They are listed but marked N/A for `free_only`.

| file:line | role | enclosing fn | A/U | `free_only=True`? |
|---|---|---|---|---|
| **`src/task_scheduler.py:1688`** | research | `_execute_research_task` | **UNATTENDED** (scheduler) | **YES** |
| **`routes/email_pollers.py:288`** | utility | poller scan loop | **UNATTENDED** (email poller) | **YES** |
| **`routes/email_pollers.py:290`** | default | poller scan loop (fallback) | **UNATTENDED** | **YES** |
| **`src/builtin_actions.py:119`,`121`** | utility/default | `_try_ai_tidy_group` (in `action_tidy_*`) | **UNATTENDED** (BUILTIN_ACTIONS) | **YES** |
| **`src/builtin_actions.py:605`,`607`** | utility/default | `action_classify_events` | **UNATTENDED** | **YES** |
| **`src/builtin_actions.py:879`,`881`** | utility/default | `action_learn_sender_signatures` | **UNATTENDED** | **YES** |
| **`src/builtin_actions.py:1139`** | default | `action_test_skills` | **UNATTENDED** | **YES** |
| **`src/builtin_actions.py:1492`,`1494`** | utility/default | `action_check_email_urgency` | **UNATTENDED** | **YES** |
| **`routes/skills_routes.py:1003`** | utility | `_resolve_audit_models` | **MIXED** → called by `run_scheduled_skill_audit:1047` (U) AND `/audit-all:1478` (A) | **YES via threaded param** (see §6) |
| **`routes/skills_routes.py:1398`** | default | `/skills/{id}/test` route | ATTENDED | no |
| **`routes/session_routes.py:188`** | utility | `_pick_endpoint_for_sort` | **UNATTENDED** (auto session-sort, called `:1140`) | **YES** |
| **`src/task_endpoint.py:13`** | task | `resolve_task_endpoint` | **MIXED** → `session_routes:194` (background sort, U) + `document_routes:889` (ai-tidy route, A) | **YES via threaded param** |
| **`src/context_compactor.py:357`** | utility | `maybe_compact` | borderline — auto-compaction; treat as **UNATTENDED** | **YES** (recommended) |
| **`routes/note_routes.py:211`,`213`** | utility/default | `dispatch_reminder` | **MIXED** → scheduler fire (U) + route `:841` (A) | **YES via threaded param** |
| `routes/email_routes.py:2413/2415` | utility/default | `/extract-style` route | ATTENDED | no |
| `routes/email_routes.py:2497/2499` | utility/default | `/summarize` route | ATTENDED | no |
| `routes/email_routes.py:2662/2664` | utility/default | `/ai-reply` route | ATTENDED | no |
| `routes/email_routes.py:2759/2765` | utility/default | `/ai-reply` route | ATTENDED | no |
| `routes/calendar_routes.py:1408/1410` | utility/default | `quick_parse` route | ATTENDED | no |
| `routes/document_routes.py:889`(task)/`892`(default) | task/default | `ai_tidy_documents` route | ATTENDED | no |
| `routes/memory_routes.py:354` | utility | `import_memories_from_file` route | ATTENDED | no |
| `routes/history_routes.py:566` | utility | `compact_session` route | ATTENDED | no |
| `routes/session_routes.py:938` | utility | `compact_session` route | ATTENDED | no |
| `routes/task_routes.py:1099/1101` | utility/default | `parse_task` route | ATTENDED | no |
| `routes/research_routes.py:44` | research | `_resolve_research_endpoint` | called from chat_routes `:384/790` (A) | no (attended deep-research) |
| `routes/research_routes.py:421/423/429/431,591/593/595` | research/utility/default/chat | research route | ATTENDED | no |
| `routes/chat_helpers.py:236` (runtime) | — | `try_fallback_endpoint` | ATTENDED | N/A (runtime) |
| `routes/chat_helpers.py:402` (runtime) | — | `resolve_session_auth` | ATTENDED | N/A (runtime) |
| `routes/chat_routes.py:229` (runtime) | — | `_recover_empty_session_model` | ATTENDED | N/A (runtime) |
| `routes/model_routes.py:558` (runtime) | — | `_resolve_probe_key` | ATTENDED (admin probe) | N/A (runtime) |
| `routes/model_routes.py:1074` (runtime) | — | model-list `_do()` | ATTENDED (admin) | N/A (runtime) |
| `routes/webhook_routes.py:331` (runtime) | — | `sync_chat` | machine (API token) | N/A (runtime) — but see §3 bypass |
| `src/ai_interaction.py:102` (runtime) | — | `_resolve_model` | ATTENDED | N/A (runtime) |
| `src/ai_interaction.py:1132` (runtime) | — | `do_list_models` | ATTENDED | N/A (runtime) |

**`free_only=True` subset (the unattended set that MUST be gated):** `task_scheduler.py:1688`; `email_pollers.py:288,290`; `builtin_actions.py:119,121,605,607,879,881,1139,1492,1494`; `session_routes.py:188`; `context_compactor.py:357`; plus the three MIXED helpers (`skills_routes._resolve_audit_models`, `task_endpoint.resolve_task_endpoint`, `note_routes.dispatch_reminder`) which need a threaded `free_only` param set True only on their scheduler-fire path.

**Master-prompt list verification:**
- ✅ `task_scheduler.py:1688`, `email_pollers.py:288`, `skills_routes.py:1003`, `email_routes.py:2413/2497/2662/2759` — all confirmed.
- ✅ Also confirmed the ones the prompt hinted at: `skills_routes.py:1398` (ATTENDED — test route, NOT a gate target), `email_pollers.py:290` (UNATTENDED default-fallback — gate it).
- ⚠️ **MISSED by the master prompt (newly found UNATTENDED / mixed gates):** all of `src/builtin_actions.py` (8 sites — the entire scheduled-action surface, dispatched via `task_scheduler.py:1012 BUILTIN_ACTIONS`), `session_routes.py:188` (`_pick_endpoint_for_sort`), `context_compactor.py:357`, `task_endpoint.py:13`, `note_routes.py:211/213`. These are the largest part of the unattended surface and are easy to miss because they live in helper functions, not obviously-named pollers.
- Note: the prompt's "default fallbacks" intuition is right — every unattended site has a `if not url: resolve_endpoint("default")` second line; **both** lines need `free_only=True` (a paid `default` would otherwise leak through the fallback).

## 3. Kill-switch (`BERTOS_ALLOW_PAID`) + bypass audit

**Read it in `src/constants.py`** (after `:79`, alongside `CLEANUP_ENABLED`, same idiom):
```python
ALLOW_PAID = os.getenv("BERTOS_ALLOW_PAID", "0").strip().lower() in ("1", "true", "yes", "on")
```
Default `'0'` ⇒ `ALLOW_PAID = False` ⇒ paid endpoints globally refused.

**Enforce it in `src/endpoint_resolver.py`** at the same two selection points as `free_only` (`:288` and `:339`). Add a module helper:
```python
def _paid_blocked(ep, free_only: bool) -> bool:
    from src.constants import ALLOW_PAID
    return _endpoint_is_paid(ep) and (free_only or not ALLOW_PAID)
```
So: kill-switch OFF (`ALLOW_PAID=False`) ⇒ **all** callers (attended too) are denied paid endpoints — a true global circuit-breaker; `free_only=True` ⇒ this background caller is denied paid even when the switch is ON. When blocked, return the fallback tuple (don't raise) so the role-cascade can find a free `utility`/`default`.

**⚠️ BYPASS PATHS that do NOT route through `resolve_endpoint` (guardrail holes — flag as RISK):**
1. **`routes/webhook_routes.py` `sync_chat` (`:235`)** — selects via `_select_api_chat_fallback_endpoint(db, token_owner)` (`:312`) and direct `ModelEndpoint` queries, building the URL with `build_chat_url`/`build_headers` directly (`:324-325`). API-token machine path → unattended-like → **can dispatch to a paid endpoint with the guardrail completely bypassed.** Needs an explicit `_paid_blocked` check at its selection point.
2. **`routes/research_routes.py` `_owned_enabled_endpoint` (`:54`)** — direct first-enabled-endpoint fallback (`:439`), bypasses resolve_endpoint's gate. Attended, but with switch OFF it should still be blocked.
3. **`llm_core` dispatch entrypoints** (`llm_call_async` `:1239`, `stream_llm` `:1399`, `llm_call_with_fallback` `:1199`, `stream_llm_with_fallback` `:1992`) take a raw `(url, model, headers)` — they trust the caller. They are the right **defense-in-depth choke point** for a belt-and-suspenders check (`_detect_provider(url)` is paid + switch off → refuse), but the *authoritative* gate is at row-selection. Recommend the primary gate at selection + an assert-style guard in `llm_call_async`/`stream_llm` for the direct-build bypass callers.

## 4. `is_paid` column + migration

**Schema** — add to `ModelEndpoint` in `core/database.py` after `supports_tools` (`:359`), before `owner` (`:360`):
```python
# Cost tier. NULL = unknown (derive from endpoint_kind + host). True = metered
# external API; False = local/self-hosted/subscription (free at call time).
is_paid = Column(Boolean, nullable=True, default=None)
```

**Migration** — new `_migrate_add_is_paid_column()` mirroring `_migrate_add_supports_tools_column` (`:901-917`, the canonical Boolean pattern):
```python
def _migrate_add_is_paid_column():
    """Add is_paid column to model_endpoints if it doesn't exist."""
    import sqlite3
    db_path = DATABASE_URL.replace("sqlite:///", "")
    if not os.path.exists(db_path):
        return
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.execute("PRAGMA table_info(model_endpoints)")
        columns = [row[1] for row in cursor.fetchall()]
        if columns and "is_paid" not in columns:
            conn.execute("ALTER TABLE model_endpoints ADD COLUMN is_paid BOOLEAN")
            conn.commit()
            logging.getLogger(__name__).info("Migrated: added 'is_paid' column to model_endpoints")
        conn.close()
    except Exception as e:
        logging.getLogger(__name__).warning(f"is_paid migration failed: {e}")
```
Register it in `init_db()` right after `_migrate_add_supports_tools_column()` (`core/database.py:1641`).

**Default-derive `is_paid` before any UI** — `_endpoint_is_paid(ep)` in `endpoint_resolver.py`. When the column is NULL, derive at resolve-time:
```python
def _endpoint_is_paid(ep) -> bool:
    explicit = getattr(ep, "is_paid", None)
    if explicit is not None:
        return bool(explicit)
    kind = (getattr(ep, "endpoint_kind", "") or "auto").lower()
    if kind == "local":
        return False              # self-hosted → free
    base = normalize_base(getattr(ep, "base_url", "") or "")
    return _host_is_paid(base)    # api/proxy/auto → host detection
```

**Paid hosts** (from `src/llm_core._detect_provider` `:408-435` + `_provider_label` `:456-477`). Maintain an explicit host allow/deny list keyed on `_host_match`:

- **PAID (metered external API):** `openai.com`, `anthropic.com`, `openrouter.ai`, `googleapis.com` (Google/Gemini), `x.ai` (xAI), `mistral.ai`, `deepseek.com`, `together.xyz`/`together.ai`, `fireworks.ai`, `groq.com`, `opencode.ai/zen` + `opencode.ai/zen/go`.
- **FREE (subscription-backed / local):** `chatgpt-subscription` (`is_chatgpt_subscription_base`, `src/chatgpt_subscription.py:61` — flat-rate sub, not metered), `copilot` (`is_copilot_base`, `src/copilot.py:89` — sub), native Ollama (`_is_ollama_native_url`), and `localhost`/`127.0.0.1`/`::1`/`0.0.0.0`.
- **`ollama.com`** (Ollama Cloud) — paid/metered cloud; classify PAID (distinct from native local Ollama).
- **Unknown host** (the `_detect_provider` "openai" catch-all at `:435`): since `endpoint_kind=api/proxy` defaults to paid, derive **paid** for unknown external hosts when kind≠local, **free** when kind=local or host is loopback. ⚠️ Flag: the safest free-first default for a truly-unknown host is **paid** (fail-closed on cost).

## 5. Test design (lane-B style)

The existing harness `tests/test_resolve_endpoint_fallbacks.py` is the exact template: monkeypatched `_FakeDb`/`_FakeQuery`/`_FakeModelEndpoint` + `SimpleNamespace` rows via `_install_resolver_fakes` (`:62`). No real DB, fully unit-testable. Add `is_paid`/`endpoint_kind`/`base_url` to `_endpoint()` SimpleNamespace.

New file `tests/test_free_only_guardrail.py`:

```python
def test_background_task_cannot_resolve_paid_endpoint_with_switch_off(monkeypatch):
    monkeypatch.setenv("BERTOS_ALLOW_PAID", "0")
    # force constants to re-read (it's import-time): patch the resolved flag
    import src.constants as c
    monkeypatch.setattr(c, "ALLOW_PAID", False, raising=False)

    paid = _endpoint("openrouter", "gpt-4", base_url="https://openrouter.ai/api/v1",
                     endpoint_kind="api", is_paid=True)
    free = _endpoint("local", "qwen", base_url="http://localhost:11434",
                     endpoint_kind="local", is_paid=False)
    settings = {"utility_endpoint_id": "openrouter", "utility_model": "gpt-4",
                "default_endpoint_id": "local", "default_model": "qwen"}
    _install_resolver_fakes(monkeypatch, settings, [paid, free])

    # background caller passes free_only=True (and switch is off anyway)
    url, model, headers = resolve_endpoint("utility", free_only=True)
    # MUST NOT be the paid endpoint
    assert "openrouter.ai" not in (url or "")
    # cascades to the free default
    assert url == "http://localhost:11434/v1/chat/completions"
    assert model == "qwen"
```

Add companion cases: (a) **switch ON + free_only=True** still blocks paid for background; (b) **switch OFF + attended caller** (`free_only=False`) also blocked (global kill-switch); (c) **switch ON + attended** resolves paid normally (no regression); (d) **`is_paid` NULL derive** — a row with `endpoint_kind="api"`, `base_url="https://api.anthropic.com"`, `is_paid=None` is treated paid; `endpoint_kind="local"` treated free; (e) fallback-chain: a paid entry in `default_model_fallbacks` is dropped from `_resolve_fallback_candidates` when `free_only`. Assert at the resolver boundary (no provider call needed — `_FakeDb` never dispatches).

## 6. The three MIXED helpers (must thread `free_only`, not hardcode)

These resolve once but serve both attended and unattended callers — hardcoding `free_only=True` would break the attended UI path:
- `skills_routes._resolve_audit_models(owner=None)` (`:994`) → add `free_only=False` param, pass to `resolve_endpoint` (`:1003`); `run_scheduled_skill_audit` (`:1047`) passes `True`, `/audit-all` route (`:1478`) passes `False`.
- `task_endpoint.resolve_task_endpoint(...)` (`:6`) → add `free_only=False`, pass to `resolve_endpoint("task", ..., free_only=free_only)` (`:13`); `session_routes._pick_endpoint_for_sort` background caller (`:194`) passes `True`, `document_routes.ai_tidy_documents` route (`:889`) passes `False`.
- `note_routes.dispatch_reminder(...)` (`:138`) → thread `free_only` from the scheduler-fire path (True) vs the HTTP route at `:841` (False).

## Change-plan summary (file:line)
1. `src/constants.py` after `:79` — `ALLOW_PAID = os.getenv("BERTOS_ALLOW_PAID","0")...`.
2. `core/database.py` `:359` — add `is_paid = Column(Boolean, nullable=True, default=None)`; new `_migrate_add_is_paid_column()` after `:917`; register in `init_db()` at `:1641`.
3. `src/endpoint_resolver.py` — add `_endpoint_is_paid`, `_host_is_paid`, `_paid_blocked` helpers; add `free_only=False` to `resolve_endpoint` (`:227`), `resolve_endpoint_by_id` (`:321`), `_resolve_fallback_candidates` (`:393`) + 3 wrappers (`:365/375/388`); enforce gate at `:288` and `:339`; thread `free_only` at `:404`.
4. Unattended call sites — add `free_only=True`: `task_scheduler.py:1688`, `email_pollers.py:288/290`, `builtin_actions.py:119/121/605/607/879/881/1139/1492/1494`, `session_routes.py:188`, `context_compactor.py:357`; thread param through the 3 mixed helpers (`skills_routes.py:994/1003`, `task_endpoint.py:6/13`, `note_routes.py:138/211/213`).
5. Bypass closures — `webhook_routes.py` `sync_chat` selection (`:312`) and `research_routes.py:439` direct-select: add `_paid_blocked` check; optional defense-in-depth guard in `llm_core.llm_call_async` (`:1239`) / `stream_llm` (`:1399`).
6. `tests/test_free_only_guardrail.py` — new, modeled on `tests/test_resolve_endpoint_fallbacks.py`.

**RISK flags:** (a) `sync_chat` API-token path is the highest-risk bypass — machine-driven, fully outside `resolve_endpoint`. (b) `constants.ALLOW_PAID` is import-time; tests must monkeypatch the resolved attribute, and any runtime toggle requires re-import or reading `os.getenv` live inside `_paid_blocked` (recommend reading live to allow hot-flip). (c) Unknown-host derivation — fail-closed to paid is the free-first-safe choice but may surprise users with private OpenAI-compatible servers reachable only by IP and `endpoint_kind=auto`; document that such endpoints should be set `endpoint_kind=local` or `is_paid=False` explicitly.