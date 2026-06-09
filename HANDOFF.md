# HANDOFF — BertOS (Odysseus fork)

<!-- State header: keep these 5 lines accurate. Claude reads this first each session and picks up from Next. -->
- **Version:** 0.1.0
- **Status:** building
- **Updated:** 2026-06-08
- **Next:** Slice 2 — memory→Obsidian-vault (the LAST Phase-1 slice; SAFE app-owned partition per `docs/bertos/SLICE2-MEMORY-DESIGN-v2.md` + its re-critique must-fixes — incl. the `delete_orphans` gate; backup the vault before first write; round-trip + restart-persistence tests are non-negotiable). Daily-brief delivery channel (self-hosted ntfy on the always-on host / ntfy.sh / in-app browser) pending the owner's choice; interim `reminder_channel=browser`. Live server: `uvicorn app:app` on http://127.0.0.1:7777.

---

## What this is

This repo is a fork of **Odysseus** (PewDiePie's MIT self-hosted AI workspace, Python/FastAPI) being rebuilt into **BertOS** — the "body" for BertOS's existing "brain" (`~/Documents/bertosV2`, TypeScript). Plan lives in `~/Documents/BertOS-REBUILD-MASTER-PROMPT.md`. Execute **Phase 1 first**: rebrand + free-first cost guardrail + Obsidian-vault memory + a daily brief. Keep the workspace surface (email/cal/notes/files) as-is for now. Brain comes in Phase 2 via MCP.

- **Base branch:** `bertos`, branched from `dev` (the master-prompt anchors match `dev`, not `main`). Upstream pristine = `origin`/`upstream` → pewdiepie-archdaemon/odysseus. Open question: confirm `dev` vs `main` as the base (being resolved by the surface-map pass).
- **Non-negotiables:** free-by-default (no surprise billing, ever); unattended work forced to free local models; honest UI (no fake "connected"); **verify by running + reading real output before claiming done** (v2's fatal failure was trusting memory over the working tree).

---

## Log (newest first)

### 2026-06-08 — Claude Code — build+check
- **Slice 4 DONE & verified — daily brief → push.** `action_daily_brief` now pushes its digest via in-process `dispatch_reminder` (free_only=True); seeded a paused "Daily Brief" cron (07:00) in `HOUSEKEEPING_DEFAULTS`. Verification caught + fixed a REAL bug: the ntfy `Title` header carried a raw em-dash → `'ascii' codec` encode error that silently dropped EVERY push; now ASCII-sanitized (body stays full UTF-8).
- Proven END-TO-END through the real scheduler: `ensure_defaults` seeds `daily_brief` (cron `0 7 * * *`) → `run_task_now` → **TaskRun=success** → **observed ntfy POST** captured on a local listener (path `/bertos-brief`, ASCII Title, UTF-8 body). The brief is honestly sparse now (no email/calendar configured) and fills in as the owner connects accounts.
- ntfy from brew is client-only (no `serve`); the real phone-delivery channel (self-hosted ntfy on the always-on host / ntfy.sh / in-app browser) is the owner's call — flagged, not blocked. Interim `reminder_channel=browser`.
- next: slice 2 — memory→vault (last Phase-1 slice).

### 2026-06-08 — Claude Code — build+check
- **Slice 3 DONE & verified — free-first cost guardrail.** `is_paid` column + migration; `free_only` param + `BERTOS_ALLOW_PAID` kill-switch (default OFF, read LIVE); **fail-CLOSED guard at the `llm_core` dispatch choke point** (`llm_call` + `llm_call_async` + `stream_llm`) so no direct-build/bypass caller can spend; `free_only=True` threaded through all unattended sites; the 8 bypass leaks gated.
- **Fixed 3 things verification caught (verify-before-claim earned its keep):** (1) REGRESSION — the builder's fail-closed "unknown host = paid" broke 4 pre-existing fallback tests AND would block legit self-hosted endpoints → replaced with a KIND-AWARE policy: known-paid host=paid; unknown host paid ONLY if `endpoint_kind='api'`; `auto`/`local`/`proxy`/unset = free (self-hosted LAN/Tailscale just works); `is_paid=True` forces paid. Expanded the known-paid host list. (2) LEAK `embeddings.py` — added a known-paid guard (no paid embeddings on autopilot; falls back to local FastEmbed). (3) LEAK sync `llm_call` — added the dispatch guard (mirror of async).
- Verified: **pytest 20/20** (4 fallback + 12 free-only + 4 dispatch — incl. a no-network proof that a paid host raises 402 with `httpx.post` NEVER called); app boots clean on :7777 with the guardrail loaded, `/api/health` 200, kill-switch default OFF, rebrand intact. To use paid models interactively: set `BERTOS_ALLOW_PAID=1` (unattended work stays free regardless).
- next: slice 4 daily-brief → ntfy.

### 2026-06-08 — Claude Code — check (design) + reorder
- **Slice-2 memory design — adversarial pass caught CRITICAL data-loss bugs → reordered + rescoped.** 5-agent design+critique workflow (`docs/bertos/SLICE2-MEMORY-DESIGN.md`) chose vault-canonical .md, but the critic proved the naive design would, on the first audit/`save()` (full-store-replace, absence=delete): (HIGH) RELOCATE all ~720 existing human/agent notes into one folder + rewrite their projectId/role/source/tags; (HIGH) make delete a no-op (orphans never unlinked → resurrect); (HIGH) duplicate/orphan notes on every pin/increment_uses (slug mismatch). The user's real 721-note brain vault would be corrupted.
- **DECISION:** (1) memory→vault moved LAST + rescoped SAFE-by-construction — app owns a DEDICATED vault partition (writes .md there only; reconciles save() within that folder only; NEVER reads/writes the 720 existing notes); broader read-only retrieval deferred; backup vault before first write. (2) Reordered: guardrail next (money-safety, code-only, zero vault risk); daily-brief doesn't depend on memory. New order: 3 → 4 → 2.
- next: implement slice 3 guardrail (build+verify workflow) + integration-test the no-paid-on-autopilot guarantee.

### 2026-06-08 — Claude Code — build+check
- **Slice 1 DONE & verified — committed `bb2b714` (scaffolding) + `346811f` (rebrand):** user-facing Odysseus→BertOS across 11 files (manifest/index/login/app.js display strings; model-visible agent_loop / tool_index:156 / tool_schemas; README H1; service Description) + gold-on-charcoal default theme (#d4af37 on #0f1420, incl. inline boot-CSS fallbacks → no white flash) + sw.js cache v327→v328. Aliases/internals kept INTACT: ODYSSEUS_* env, odysseus-ui service id, odysseus-theme LS key, odysseus_tool_index COLLECTION_NAME, upstream repo URL.
- Verification = 4-agent workflow (1 build + 3 adversarial audits: NO USER-FACING LEFTOVERS / all 6 invariants INTACT / theme OK) AND live curl on :7777 rendered DOM (title/wordmark/placeholder/login all "BertOS"; manifest BertOS; served boot-CSS gold+charcoal, zero old hexes; /api/health 200). Screenshot skipped (2 browsers connected → picker friction; verified via curl instead). App is live at http://127.0.0.1:7777.
- next: slice 2 memory→vault.

### 2026-06-08 — Claude Code — build+check
- **Slice 0 DONE & verified:** BertOS boots via py3.11 `.venv` + `uvicorn app:app` on http://127.0.0.1:7777. Boot blocker found+fixed by running (not guessing): `init_db()` ran at import before any dir was created → `sqlite3.OperationalError: unable to open database file`; fixed via `setup.create_dirs()` (11 data dirs). Proof = HTTP: `/`,`/login`,`/api/health` → 200; built-in MCP (RAG/Memory/Image/Email-11-tools) connected; Chroma absent → graceful degrade; log reaches "Application startup complete".
- **9-agent surface map complete** → `docs/bertos/PHASE1-PLAN.md` (+ `MAP-*.md`, `VERDICTS.json`). Base = `dev` confirmed (anchors match; cherry-pick 4 security hotfixes from `main` later — they touch zero Phase-1 files). Order: 1 rebrand → 2 memory→vault → 3 guardrail → 4 daily-brief.
- **Adversarial pass found real guardrail leaks:** scheduler `_resolve_defaults`, `ai_interaction._resolve_model`, `try_fallback_endpoint`, research/webhook paths pick endpoints WITHOUT `resolve_endpoint` → the kill-switch must also gate the `llm_core` dispatch choke point (fail-closed). And `action_daily_brief` already exists + gathers cal/email/todos with no LLM → slice 4 is mostly cron+ntfy wiring.
- next: execute slice 1 (rebrand).

### 2026-06-08 — Claude Code — build
- Reviewed the master rebuild prompt against the real `~/Documents/odysseus` repo: core anchors (memory.py:35, constants.py:17/44, endpoint_resolver.py:221, database.py:333/348, email_routes guardrail sites) verified accurate; flagged 5 fixes (memory-surface scope trap, stale `1142×/217-files` count → actually ~406/131 case-sensitive, "4 call sites" undercount, app.py off-by-ones, branch-from-dev note).
- Set up the `bertos` working branch off `dev`; added `upstream` remote; initialized this work-log.
- Launched a multi-agent ground-truth map of the Phase-1 surface (run-recipe, rebrand, guardrail, memory, daily-brief, base-decision) with adversarial verification of the two scope traps.
- next: synthesize the map into an ordered slice plan, then execute slice 0 (boot locally).
