# HANDOFF — BertOS (Odysseus fork)

<!-- State header: keep these 5 lines accurate. Claude reads this first each session and picks up from Next. -->
- **Version:** 0.1.0
- **Status:** building
- **Updated:** 2026-06-08
- **Next:** Slice 1 (rebrand, user-facing only + gold-on-charcoal theme + sw.js cache-bust) executing. Then slice 2 memory→vault (needs owner↔projectId design decision + round-trip test), slice 3 guardrail (llm_core dispatch-level kill-switch — resolve_endpoint gating alone leaks), slice 4 daily-brief→ntfy. Live server: `uvicorn app:app` on http://127.0.0.1:7777 (NOT 7860).

---

## What this is

This repo is a fork of **Odysseus** (PewDiePie's MIT self-hosted AI workspace, Python/FastAPI) being rebuilt into **BertOS** — the "body" for BertOS's existing "brain" (`~/Documents/bertosV2`, TypeScript). Plan lives in `~/Documents/BertOS-REBUILD-MASTER-PROMPT.md`. Execute **Phase 1 first**: rebrand + free-first cost guardrail + Obsidian-vault memory + a daily brief. Keep the workspace surface (email/cal/notes/files) as-is for now. Brain comes in Phase 2 via MCP.

- **Base branch:** `bertos`, branched from `dev` (the master-prompt anchors match `dev`, not `main`). Upstream pristine = `origin`/`upstream` → pewdiepie-archdaemon/odysseus. Open question: confirm `dev` vs `main` as the base (being resolved by the surface-map pass).
- **Non-negotiables:** free-by-default (no surprise billing, ever); unattended work forced to free local models; honest UI (no fake "connected"); **verify by running + reading real output before claiming done** (v2's fatal failure was trusting memory over the working tree).

---

## Log (newest first)

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
