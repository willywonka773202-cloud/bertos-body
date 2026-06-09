All evidence gathered. Here is the deliverable.

---

# Phase-1 Branch Decision: `dev` vs `main` — Recon Findings

**Repo:** `/Users/willlambert/Documents/odysseus` · **Branch checked out:** `bertos` (== `origin/dev` tip) · **HEAD:** `62ffcb6` (2026-06-09) · **origin/main:** `7367325` (2026-06-05, 4 days older) · **merge-base:** `e0e250d`
**Remote note:** both `origin` and `upstream` point at the same URL (`pewdiepie-archdaemon/odysseus.git`) — no separate BertOS fork remote is configured here; you are diffing against upstream's own main/dev.

## 1. Divergence (both directions)
- **Ahead — `git log --oneline origin/main..HEAD | wc -l` → 184** commits on dev that main lacks.
- **Behind — `git log --oneline HEAD..origin/main | wc -l` → 4** commits on main that dev lacks.
- The 4 "behind" commits are confirmed genuinely absent from dev (verified via `git log --cherry --right-only` → all 4 print as `+`, no patch-equivalent on dev). They are all security/correctness hotfixes:
  - `7367325` fix(caldav): include owner in calendar ID hash to prevent PK collision (#2765)
  - `3738df3` fix(tasks): validate then_task_id belongs to same owner (#2764)
  - `f5c9095` fix(document): add 404 guard to version list/get endpoints (#2762)
  - `d4ff7fc` fix(gallery): add auth check to /api/image/sharpen endpoint (#2761)
- **These 4 hotfixes touch ZERO Phase-1 files.** They only touch `routes/document_routes.py`, `routes/gallery_routes.py`, `routes/task_routes.py`, `src/caldav_sync.py` (intersection with the Phase-1 set is empty). So whichever base you pick, picking up these 4 fixes later is a clean, low-conflict cherry-pick.

## 2. Phase-1 touch-file diff (`git diff --stat origin/main..HEAD`)
11 of 15 Phase-1 files differ; **953 insertions / 253 deletions**. Heaviest: `src/agent_loop.py` (+600 region, the big rewrite), `core/database.py` (+175), `routes/skills_routes.py` (+108), `src/constants.py` (+67), `src/mcp_manager.py` (+66), `src/endpoint_resolver.py` (+61), `src/task_scheduler.py` (+45), `static/index.html` (+38), `app.py` (+34), `routes/email_routes.py` (+8), `routes/email_pollers.py` (+4).
**4 Phase-1 files are byte-identical on main and dev** (no diff): `src/memory.py`, `src/memory_provider.py`, `src/builtin_mcp.py`, `static/manifest.json`. All 15 files exist on both branches (no file is dev-only).

## 3. Anchor-match finding — do the master prompt's anchors match main or only dev?
The master prompt's line anchors were written against **dev**. Result is **mixed — 1 of 4 matches main, none drift on dev**:

| Anchor | dev (HEAD) | origin/main | Match? |
|---|---|---|---|
| `memory.py: class MemoryManager` | **35** | **35** | ✅ matches BOTH (file identical) |
| `constants.py: MEMORY_FILE` | 17 | 14 | ❌ main drifts −3 |
| `constants.py: CHROMA_DIR` | 44 | **absent** | ❌ **does not exist on main at all** |
| `endpoint_resolver.py: def resolve_endpoint(` | 221 | 208 | ❌ main drifts −13 |
| `core/database.py: class ModelEndpoint` | 333 | 332 | ❌ main drifts −1 |

**Verdict: the anchors match `dev` exactly; on `main` they DRIFT (and one is missing entirely).** The standout: `CHROMA_DIR` (Chroma vector-store dir for the memory provider) is **not present in main's `constants.py` at all** — main's file is 40 lines vs dev's 103 (+63 lines of config dev added). Any Phase-1 work that resolves the vector-memory path via `constants.CHROMA_DIR` would break on a main base until that line is back-ported.

## 4. Recommendation (one line)
**Base BertOS on `dev` (current `bertos` checkout) — every master-prompt anchor matches it line-for-line, it carries +184 commits of Phase-1 prerequisites (incl. the entire `CHROMA_DIR`/agent_loop work that main lacks), and the only thing main has that dev doesn't is 4 isolated security hotfixes that touch no Phase-1 file and cherry-pick in cleanly.**

Rationale / tradeoff: choosing main would buy "curated/stable" at the cost of re-porting 953 lines across 11 files and a missing `CHROMA_DIR` constant — high friction with no Phase-1 upside, since main's only exclusive value (4 hotfixes) is trivially portable onto dev. The "dev may be unstable" risk is real but bounded; mitigate by cherry-picking `d4ff7fc f5c9095 3738df3 7367325` onto `bertos` to close the 4-commit security gap.

**Flagged uncertainties:** (1) I did not assess dev's *runtime* stability — "may be unstable" is taken from your framing, not verified by running anything (READ-ONLY recon). (2) `origin`==`upstream` here, so there is no evidence in-repo of a separate BertOS fork remote; confirm where `bertos` is meant to push. (3) Anchor matching was checked at the 4 named symbols only — other anchors in the master prompt were not line-verified against main.