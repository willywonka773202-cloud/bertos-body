All key claims verified against the actual code. The findings are sound. Now synthesizing into the de-duplicated, prioritized list.

---

# BertOS Hardening — Consolidated Fix List

## HIGH

**H1 — Deep Build rescue spends paid `codex-cli` despite `BRAIN_ALLOW_PAID=0`**
`app/api/deep/jobs/route.ts:89` → `strongFreeRoleConfig(online, "codex-cli")` (`lib/providers/strategy.ts:453-462`) sets `escalationCoderId="codex-cli"` whenever it's online; Deep Build never runs `coerceFreeRoles`, so the paid rescue survives into `orchestrate.ts:524` (`runAuto` with `coderId==="codex-cli"`) on a skeptic-triggered repair. Real subscription spend on an unattended path. "Strongest free" is not free.
*Fix:* in `deep/jobs/route.ts`, after building `roles`, gate on a route-read env (`process.env.BERTOS_ALLOW_PAID==="1"`) and `roles = coerceFreeRoles(roles)` otherwise (strips `escalationCoderId`). Use a route-visible var — the bridge's `BRAIN_ALLOW_PAID` is not in scope here.

**H2 — Dispatch guardrail fails OPEN on a scheme-less base URL**
`src/endpoint_resolver.py:104-115` / `_host_match` (`src/llm_core.py:387`) uses `urlparse(url).hostname`, which is `None` without a scheme → host classified NOT paid. Verified: `api.openai.com/v1` and `openrouter.ai` both return `host_is_paid=False`. `normalize_base`/`resolve_url` never prepend a scheme, so a scheme-less row reaches the fail-closed choke point and leaks. Guard is `except: pass` (fail-open), so a miss = real spend.
*Fix:* prepend a scheme when absent before matching — `b if "://" in b else "//"+b` (`urlparse("//host/x").hostname=="host"`) — and feed that into `_host_match` and the `urlparse(...).hostname` in `_host_is_known_free`.

**H3 — Daily brief swallows push-delivery failures, always reports success**
`src/builtin_actions.py:1130-1142`: `action_daily_brief` calls `dispatch_reminder(...)`, discards the return dict, and unconditionally `return plain_body, True`. A failed ntfy/email/webhook send (e.g. `ntfy_error`, "No enabled ntfy integration") still shows a green TaskRun; the 7am push silently never went out. Violates the never-fake-delivered mandate.
*Fix:* capture `res = await dispatch_reminder(...)`, read the per-channel flag for the active `reminder_channel`, and return `(…, False)` with the error string when not delivered — mirror the existing pattern at `builtin_actions.py:1940-1944`.

## MEDIUM

**M1 — `brain_council` (free mode) can pull one paid adjudication despite `BRAIN_ALLOW_PAID=0`**
Bridge omits `memberIds`/escalation flag → `app/api/chat/council/route.ts:75-82` calls `runCouncil` without `escalateOnDisagreement`. Gate at `lib/chat/council.ts:1224` is `!== false`, so `undefined` ENABLES escalation; on genuine disagreement with a paid CLI online, `runDepthEscalation` (`council.ts:790`) makes one real paid `agent()` call. Capped at one call and disclosed → MED.
*Fix:* council route passes `escalateOnDisagreement: allowPaid ? undefined : false` keyed on the same route-read paid switch (shared with H1).

**M2 — Centralize the paid kill-switch at the route boundary (no bertosV2 route reads it)**
`grep ALLOW_PAID app/ lib/` returns nothing — the entire paid/free decision on the brain side lives only in the bridge's input-shaping. Any caller hitting `/api/deep/jobs` or `/api/chat/council` directly (the bertosV2 UI, another MCP client) gets none of the bridge's protection; H1's rescue and M1's adjudication both fire by default. Guardrail is in the wrong layer.
*Fix:* add a shared `serverAllowsPaid()` helper read live (mirroring Python's `allow_paid()`) and have every paid-capable route (`deep/jobs`, `chat/council`, `auto/*`, `deep/run`, `deep/plan`) consult it. This is the structural fix that subsumes H1/M1's per-route patches.

**M3 — Guardrail errors fail OPEN everywhere (inverts the stated fail-CLOSED contract)**
`src/llm_core.py:1108-1110, 1289-1291, 1462-1467`, `src/task_scheduler.py:1311-1312`, `endpoint_resolver.py:157-158, 172-173`: every `except` around the classifier treats the call as allowed. A classifier bug/import failure → paid call proceeds. For a *cost* guard the safe default is the opposite.
*Fix:* in the spend-deciding `except` branches of `llm_call`/`llm_call_async`/`stream_llm`, default to blocked (treat as paid) when `allow_paid()` is False.

**M4 — `_PAID_HOSTS` is the sole cloud-spend defense and omits metered host families**
`src/endpoint_resolver.py:78-99`: unknown host + unset `endpoint_kind` resolves FREE by design, and `is_paid` defaults NULL on migrated rows (`core/database.py:360`), so nothing is paid-by-default until a host matches this hardcoded tuple. Missing: `bedrock*.amazonaws.com` (AWS Bedrock), `*.cognitiveservices.azure.com`. A paid provider on a custom domain is never blocked.
*Fix:* add the missing metered host families to `_PAID_HOSTS`; document that adding a paid provider requires editing this list. (Optionally flip unknown public non-loopback `endpoint_kind=='api'`-unset rows to paid.)

**M5 — `brain_health` reports the daemon DOWN though it's up (probes `/`)**
`scripts/brain-mcp-server.mjs:135` does `probe(DAEMON_URL, "/")`; the daemon only serves `/health` (`bertos-daemon.mjs:535`) and 404s everything else (`:1112`) → `daemon.ok:false` on a fully-running daemon. Fake "down".
*Fix:* `probe(DAEMON_URL, "/health")`. Default no-token loopback returns 200 → maps to `ok:true` correctly. Coordinate with M6 for token-gated deployments.

**M6 — Daemon bearer token is half-wired (dead in practice; would 401 the probe)**
`brain-mcp-server.mjs:33` reads `BERTOS_DAEMON_TOKEN` and claims it threads onto every request, but `src/builtin_mcp.py:99-103` injects only `BERTOS_BASE_URL`/`BERTOS_DAEMON_URL`/`BRAIN_ALLOW_PAID` — never the token, so it's always empty on the only launch path. Even if set, `probe()` (`:39-50`) sends no headers, so the M5-fixed `/health` probe 401s on a token-gated daemon.
*Fix:* add `"BERTOS_DAEMON_TOKEN": os.environ.get("BERTOS_BRAIN_DAEMON_TOKEN","")` to the `brain` env block in `builtin_mcp.py`; give `probe()` an optional headers arg and pass `Authorization: Bearer ${DAEMON_TOKEN}` on the daemon probe. Fix the misleading "every brain request" comment (`callBrain` only hits Next, which isn't daemon-token-gated).

**M7 — `brain_health.ok` claims Deep Build/Auto can run when only Next is up**
`brain-mcp-server.mjs:138` sets `ok: next.ok` and the hint says all coding tools "can run", but `brain_deep_build`/`brain_auto_cycle` need the daemon. `POST /api/deep/jobs` returns a jobId even with the daemon down (`deep/jobs/route.ts:133`) → user starts a build that later dies. Honest-status gap.
*Fix:* once M5/M6 land, add `canBuild: next.ok && daemon.ok` and change the affirmative hint to "Council can run; Deep Build/Auto also need the daemon (daemon.ok=…)".

**M8 — Fallback YAML emitter corrupts list-of-dicts (the live serialization path here)**
`src/memory.py:166-170, 121-132`: with PyYAML absent (the live path on this machine), `_fallback_dump_value` stringifies non-scalar list items via `str(value)`, so `[{"a":1}]` round-trips to `["{'a': 1}"]` — dicts become unrecoverable `repr()` strings. Reachable: `MemoryProvider._to_record` (`memory_provider.py:119-123`) folds arbitrary agent-supplied top-level keys into `metadata`.
*Fix:* simplest safe option — detect a non-scalar list/dict in the emitter and JSON-encode the whole `extra` submap as one quoted scalar on the fallback path. Best: make PyYAML a hard dependency (add to requirements, assert at `MemoryManager.__init__`) so the lossy path never runs in production.

## LOW

**L1 — `_within_partition` is lexical (`abspath`), not `realpath`**
`src/memory.py:898-900`: a directory symlink inside the partition lets a write escape (verified empirically). NOT reachable via current flow (all targets are flat `_safe_id`-sanitized filenames; `_vault_path` is internal-only) — defense-in-depth, not a live vuln.
*Fix:* `os.path.realpath(path).startswith(os.path.realpath(self.app_dir)+os.sep)`; resolve `app_dir` to realpath once in `__init__`.

**L2 — Reminder/daily-brief email subject still ships "Reminder (Odysseus):"**
`routes/note_routes.py:365`: user-facing email subject — predates the rebuild but slice 4 routes the daily brief through this exact path, so the Odysseus→BertOS rebrand leaks in a shipped feature.
*Fix:* `msg["Subject"] = f"Reminder (BertOS): {_t}"`. Leave `X-Odysseus-*` headers alone — internal dedupe keys consumed at `builtin_actions.py:1619-1620`.

**L3 — `consolidate_memory` derives `removed_ids` from pre-`save()` ids**
`src/builtin_actions.py:284-288`: `surviving_ids` is snapshotted before `save()` re-mints unsafe ids (`memory.py:859-861`); a survivor's stale id could land in `removed_ids`. Benign today (file stems already safe; deleting a non-existent `<id>.md` is a no-op).
*Fix:* compute `surviving_ids`/`removed_ids` from the dicts AFTER `save()`.

**L4 — Concurrent same-note edits can lose an update (read-modify-write outside the lock)**
`src/memory.py:981-994` (`increment_uses`), `722-735` (`claim_ownerless`), `ai_interaction.py:1027-1041`, `builtin_actions.py:211-226`: flock is held only inside `save()`/`delete()`, not across load→mutate→save. NOT partition-blanking (all use `delete_orphans=False`) — worst case is one field edit clobbered by a staler rewrite of the same note.
*Fix:* make `increment_uses` an in-lock per-file read-modify-write, or add an mtime compare-and-swap in `_atomic_write`. Acceptable to document-and-defer for single-user.

**L5 — File lock silently no-ops without `fcntl`; daily-brief default channel is invisible**
(a) `src/memory.py:454-490`: if `fcntl`/`flock` fails, `_FileLock` is a no-op and the consolidate save→delete sequence loses cross-op serialization — fine on macOS/Linux, worth a one-line warning log on first degraded lock. (b) `src/settings.py:144` defaults `reminder_channel="browser"`; `dispatch_reminder` sets `browser_sent=True` merely by queuing an in-memory notification that expires unseen for an unattended 7am brief — largely resolved by H3 reporting real per-channel delivery; separately, don't set `browser_sent=True` for unattended queued notifications.

---

## Dropped (non-issues / re-litigation, no new evidence)
Auto path `coerceFreeRoles` (verified airtight); Odysseus dispatch guards `free_only=True` on all background callers; embeddings localhost default + `_host_is_known_paid` voyageai block; `_endpoint_is_paid` unknown-host FREE default (deliberate, guarded by `free_only`); secrets/SSRF (Fernet-encrypted, token never logged); job-id non-blocking pattern (`startDeepBuildJob` detached, `callBrain` AbortController fail-closed); `_migrate_add_is_paid_column` idempotency; rebrand of rendered surfaces; the `.{id}.md.tmp` orphan-glob analysis; comment drift on `route.ts:182` cite.

---

## Verdict
NOT production-solid as-is: three must-fix HIGHs — **H1** (Deep Build paid `codex-cli` rescue fires with the kill-switch off), **H2** (guardrail fails open on scheme-less URLs), and **H3** (daily brief reports fake delivery success) — all directly violate BertOS's free-first and never-fake-delivered mandates. Ship the three HIGHs (M2 is the clean structural fix that also closes H1/M1) before calling the rebuild done.