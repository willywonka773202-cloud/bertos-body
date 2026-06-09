# Phase 2 — Transplant the bertosV2 Coding Brain into the BertOS Body via MCP

**Status:** design (architect sign-off)
**Author:** Lead architect, Phase 2
**Date:** 2026-06-08
**Master prompt §5:** transplant the TypeScript coding brain into the Odysseus-fork body **via MCP — WITHOUT re-coding it in Python.**

> Two repos, one brain:
> - **Brain (bertosV2, TypeScript/Next):** `/Users/willlambert/Documents/bertosV2` — Forge (Deep Build), Council, Auto Mode, the daemon, all guardrails.
> - **Body (Odysseus fork, Python, branch `bertos`):** `/Users/willlambert/Documents/odysseus` — the agent loop + MCP host that will *call* the brain as tools.
> - **The bridge:** a standalone Node stdio MCP server in the brain repo, spawned by the body. It re-uses the brain wholesale; zero Python re-implementation.

---

## 1. MCP-Bridge Architecture

### The decision: HTTP-call the running Next server, NOT lib imports

The bridge is a single standalone Node ESM file:

```
/Users/willlambert/Documents/bertosV2/scripts/brain-mcp-server.mjs
```

It uses `@modelcontextprotocol/sdk` (`McpServer` + `StdioServerTransport`) and is **a thin HTTP client.** Every tool POSTs/GETs the brain's existing Next API routes; it imports **no brain libs**.

**Why this seam (per the runtime map):**

- **Lib import is the worst seam.** Every orchestrator module (`lib/auto/pipeline.ts`, `lib/deepbuild/orchestrate.ts`, `lib/providers/*`, `lib/daemon.ts`, `lib/core/env.ts`) opens with `import "server-only"`, which **throws on a plain `node` import** ("This module cannot be imported from a Client Component"). It only no-ops under Next's `react-server` export condition. A standalone runner would also have to register the `@/*` TS path alias and strip TS. Replicating Next's module conditions to re-host the orchestrators is fragile and exactly the kind of re-coding §5 forbids.
- **Daemon-only (port 4319) is too low.** Talking straight to `scripts/bertos-daemon.mjs` (`/ask`, `/run`, `/git`) drives a single CLI and loses **everything that makes BertOS beat raw Claude**: orchestration, Borda judge, refute/repair, budget governor, memory grounding, the single-writer project lock, and Lane-A/B enforcement. The daemon is the brain's *hands*, not its *mind*.
- **HTTP to Next reuses every guardrail for free.** The routes are `runtime="nodejs"; dynamic="force-dynamic"` and call `runDeepBuild` / `startDeepBuildJob` / `startAutoJob` / `runCouncil` in-process. The bridge needs only one config value: the app base URL. All safety lives behind the route boundary and cannot be bypassed by the bridge.

```
┌─────────────────────────┐         ┌──────────────────────────────────────────┐
│  Odysseus body (Python) │         │  bertosV2 brain (TypeScript)               │
│  agent loop + McpManager│ stdio   │                                            │
│                         │ spawn   │  brain-mcp-server.mjs  (Node, MCP SDK)     │
│  node brain-mcp-server  ├────────►│        │ HTTP (fetch) to BERTOS_BASE_URL    │
│   → tools mcp__brain__* │  JSON-  │        ▼                                   │
│                         │  RPC    │  Next server :3000  ── /api/chat/council   │
│                         │         │   (server-only libs) ─ /api/deep/jobs      │
│                         │◄────────┤                      ─ /api/auto/jobs      │
└─────────────────────────┘ tool    │        │                                  │
                            results  │        ▼ daemonAsk/Run/Git                │
                                     │  bertos-daemon.mjs :4319 (CLIs/git/files) │
                                     │        + Ollama :11434 (free backbone)    │
                                     └──────────────────────────────────────────┘
```

### What must be running for the bridge to do real work

The bridge is just a transport — the muscle behind it is the same as any real brain run. Per the runtime map, a **Deep Build / Auto run that edits + verifies + commits a repo requires:**

1. **The Next server** — `next start` (prod) or `next dev`. Provides `/api/deep/jobs`, `/api/auto/jobs`, `/api/chat/council`. **Hard requirement** for every tool except the bridge's own `brain_health` probe. The orchestrators are `server-only`; there is no other supported entry.
2. **The daemon** — `node scripts/bertos-daemon.mjs` (loopback `127.0.0.1:4319`). The **only** path that spawns CLIs, runs the verify command, does git, writes files. Required for any coding tool (`brain_deep_build`, `brain_auto_cycle`). **Council does not need it** (no file/git writes).
3. **Model muscle** — a logged-in `claude`/`codex`/`gemini` CLI on PATH (paid lanes) and/or **Ollama** at `OLLAMA_BASE_URL` (free lane). Council and Lane-B both run on the free backbone.

The one-command form that brings up daemon + Next together: `npm run bertos:host` (prod, builds then `next start` on :3000) or `npm run bertos:dev` (daemon + `next dev`). **The bridge should NOT try to boot these** — it assumes they are up and fails each tool gracefully with a clear "brain offline: start `npm run bertos:host`" message if `BERTOS_BASE_URL` is unreachable. (Auto-spawning the host from inside an MCP child process invites orphaned servers and the `.next` cache corruption gotcha #4.)

### Per-tool seam summary

| Tool | Seam | Needs Next | Needs daemon | Needs Ollama/CLI |
|---|---|---|---|---|
| `brain_health` | HTTP `GET /api/.../health` probe (or daemon `/health`) | no | helpful | no |
| `brain_council` | HTTP `POST /api/chat/council` | **yes** | no | yes (free fan-out) |
| `brain_deep_build` (start) | HTTP `POST /api/deep/jobs` | **yes** | **yes** | yes |
| `brain_deep_status` (poll) | HTTP `GET /api/deep/jobs` | **yes** | no | no |
| `brain_deep_stop` | HTTP `DELETE /api/deep/jobs?id=` | **yes** | no | no |
| `brain_auto_cycle` | HTTP `POST /api/auto/jobs` (`unattended:true`) | **yes** | **yes** | yes (Ollama) |

> **Job seam over streaming seam.** The brain exposes both SSE (`/api/deep/run`, `/api/chat/council/stream`) and durable-job (`/api/deep/jobs`) variants. An MCP stdio tool call is a request→response RPC; it cannot hand a live SSE stream back to the body's agent loop. So the bridge uses the **durable-job routes** for long-running work (start → poll → stop) and the **single-response routes** for Council. This is the shape the runtime map recommends.

---

## 2. Tool Surface (start small + verifiable)

First tool set — minimal, each independently testable. Long-running work uses **job-id + poll**, never a blocking call over stdio.

### `brain_health` (Slice A proof-of-pipe)
- **In:** `{}` (none).
- **Seam:** `GET {BERTOS_BASE_URL}/api/deep/jobs?limit=1` (cheap, proves Next is up) and optionally daemon `/health`.
- **Out:** `{ ok, next: "up"|"down", daemon: "up"|"down", baseUrl }`.
- **Purpose:** the "hello brain" tool. Proves the body→bridge→brain pipe end to end with zero side effects.

### `brain_council(prompt)` (request/response — easiest real payoff)
- **In:** `{ prompt: string (req), projectId?: string, useMemory?: boolean }`.
- **Seam:** `POST /api/chat/council` with `{ messages: [{ role:"user", content: prompt }], projectId, useMemory }`.
- **Out:** `{ answer, synthesizerId, members: [...], escalated, why? }` (mapped from `CouncilResult`).
- **Latency:** slow-ish multi-model fan-out, but **one response** (no maxDuration). Safe to `await` inside a single tool call.
- **Cost:** free-only by default; escalates to **exactly one** paid adjudication only on genuine disagreement with a paid CLI online. This is the brain's own guardrail — the bridge does not need to add anything for Council, but see §2 enforcement note.

### `brain_deep_build(repo, instruction)` (long-running — the flagship Forge pillar)
Returned as **job-id + poll**, not a blocking call.
- **`brain_deep_build`** — `{ repo: string (projectId or path), instruction: string (req), engine?: "free"|"max"|"claude-base", verifyCommand?: string }`
  - **Seam:** `POST /api/deep/jobs` → returns immediately with `DeepBuildRun` (201). The bridge returns `{ jobId, status:"running" }`.
  - **Default engine is the FREE config** (see enforcement note). `engine:"max"`/`"claude-base"` (paid subscriptions, Lane A) is gated behind explicit opt-in.
- **`brain_deep_status`** — `{ jobId: string (req) }` → `GET /api/deep/jobs`, find the run → `{ status: "running"|"done"|"error"|"interrupted", committed, rounds, ok, error? }`.
- **`brain_deep_stop`** — `{ jobId }` → `DELETE /api/deep/jobs?id=`.
- **Side effects (surface to the agent):** edits real files in the repo, `git commit` per vetted round; the **job path does not push** (unlike `/api/deep/run`, which pushes). Per-project single-writer lock → a second run on the same repo returns **409 PROJECT_BUSY**; the bridge maps this to a clean tool error `{ ok:false, code:"PROJECT_BUSY" }` so the agent can wait/retry, not crash.

### `brain_auto_cycle(repo)` (Lane-B free background loop)
- **In:** `{ repo: string, objective?: string, tasks?: string[], maxRuns?: number, intervalSeconds?: number }`.
- **Seam:** `POST /api/auto/jobs` with **`unattended:true` hard-coded by the bridge** → returns `AutoJob` (201). Poll via a `brain_auto_status(jobId)` → `GET /api/auto/jobs` / `/api/auto/jobs/[id]`; stop via `brain_auto_stop(jobId)` → `POST /api/auto/jobs/[id] {action:"stop"}`.
- This is the Night Loop entry. Confirmed in code: the route forces free roles at `app/api/auto/jobs/route.ts:182` via `coerceFreeRoles` whenever `unattended === true`.

### Free-first / Lane-B enforcement (so brain calls never surprise-spend)

Two layers, defense in depth:

1. **The brain already enforces it server-side.** Confirmed: `app/api/auto/jobs/route.ts:148` reads `unattended`, and at `:182` (when `unattended === true`) calls `coerceFreeRoles(roles)` — rewriting any paid role to Ollama and **stripping `escalationCoderId` entirely** (`lib/providers/registry.ts:599`). Council is free-by-default with single-escalation. Deep Build only spends when `engine:"max"`/`"claude-base"`.
2. **The bridge adds a belt over the suspenders.** Because the body's agent could be invoked unattended:
   - `brain_auto_cycle` **always sends `unattended:true`** — never exposes a paid-loop knob.
   - `brain_deep_build` **defaults `engine` to the free config** and requires an explicit `engine:"max"|"claude-base"` (i.e., a human/opt-in choice) to spend a subscription. The bridge can additionally honor a `BRAIN_ALLOW_PAID=0` env flag that hard-rejects any paid engine, so a fully-unattended body deployment is structurally free-only.
   - `brain_council` passes through the brain's own free-by-default behavior; the bridge surfaces `escalated`/`escalatedBy` in the result so spend is always visible to the agent.

---

## 3. BertOS Registration

**Recommendation: BUILT-IN entry with `server_id = "brain"`.** This is a permanent, code-owned bridge that should ship with BertOS, not a user toy. (A DB row is the alternative if Will wants a UI toggle — see below.)

### Why `server_id="brain"` (not `builtin_brain`)
Critical namespacing rule confirmed at `src/mcp_manager.py:543` and `:642`: `get_all_openai_schemas` and the prompt builder **skip any server where `is_builtin(server_id)` is true, except `builtin_browser`.** `is_builtin` is true when the id starts with `builtin_` or is in `{image_gen, memory, rag, email}` (`:604`). So `builtin_brain` would be **silently dropped from function-calling.** Using `brain` keeps it visible; tools surface to the agent as `mcp__brain__*`.

### The exact built-in entry (`src/builtin_mcp.py`)

A Node stdio server does not fit `_BUILTIN_SERVERS` (Python-script shape, launched with `sys.executable`) or `_BUILTIN_NPX_SERVERS` (npx-package shape). Add a dedicated node registry mirroring the Python connect template (`_connect_python_server`, `builtin_mcp.py:98–123`).

```python
# src/builtin_mcp.py — near the other registries (~:69)
_BUILTIN_NODE_SERVERS = {
    "brain": {
        "name": "Built-in: Brain Bridge",
        "script": "/Users/willlambert/Documents/bertosV2/scripts/brain-mcp-server.mjs",
        "env": {
            "BERTOS_BASE_URL": "http://127.0.0.1:3000",
            "BERTOS_DAEMON_URL": "http://127.0.0.1:4319",
            # "BERTOS_DAEMON_TOKEN": "...",   # only if the daemon runs token-gated
            "BRAIN_ALLOW_PAID": "0",           # structurally free-only by default
        },
    },
}

# in register_builtin_servers (~:89), AFTER the Python loop:
node_path = shutil.which("node") or "node"
for server_id, cfg in _BUILTIN_NODE_SERVERS.items():
    if not os.path.exists(cfg["script"]):
        logger.warning(f"Brain MCP script not found: {cfg['script']}")
        continue
    asyncio.create_task(mcp_manager.connect_server(
        server_id=server_id,          # "brain" → tools become mcp__brain__*
        name=cfg["name"],
        transport="stdio",
        command=node_path,            # absolute node path; PATH may be minimal under a service manager
        args=[cfg["script"]],
        env=cfg["env"],               # overlaid on os.environ at _connect_stdio (mcp_manager.py:186)
    ))
```

`connect_server` (`mcp_manager.py:148`) → `_connect_stdio` builds `StdioServerParameters(command, args, env={**os.environ, **env})`, runs `session.initialize()` then `session.list_tools()`, storing schemas. Runs at startup via `app.py:897` (`register_builtin_servers`), **with no 20s timeout** on the built-in path.

> **Use an absolute node path.** The maps note PATH may be minimal under a service manager; `shutil.which("node")` resolves `/Users/willlambert/.local/bin/node` (verified present, v22.22.3).

### DB-row alternative (if Will wants a UI toggle)
Admin-gated `POST /mcp/servers` (`routes/mcp_routes.py:152`) with form fields:
```
name=Brain Bridge
transport=stdio
command=node
args=["/Users/willlambert/Documents/bertosV2/scripts/brain-mcp-server.mjs"]
env={"BERTOS_BASE_URL":"http://127.0.0.1:3000","BRAIN_ALLOW_PAID":"0"}
```
Gets a uuid8 id (tools become `mcp__<uuid>__*`), picked up on restart by `connect_all_enabled` (`mcp_manager.py:410`) — **subject to the 20s startup timeout (`app.py:901`)**, so the bridge must connect fast (it does: no heavy imports, just MCP SDK + fetch). Toggle via `PATCH /mcp/servers/{id}`.

| | Built-in (`brain`) — **recommended** | DB row |
|---|---|---|
| Defined in | code (`builtin_mcp.py`) | DB, admin route |
| UI toggle | no | yes |
| Startup timeout | none | 20s |
| Auto-reconnect on crash | only if added to `_reconnect_builtin` (acceptable to skip first cut) | reconnect route exists |
| Surfaces as | `mcp__brain__*` | `mcp__<uuid>__*` |

---

## 4. Slice Plan (ordered, each independently verifiable by running)

Each slice ends with a concrete "the BertOS agent invokes the tool → real brain output" check.

### Slice A — "hello brain": 1-tool MCP server, body connects + lists it
**Change:**
- `cd /Users/willlambert/Documents/bertosV2 && npm i @modelcontextprotocol/sdk` (currently **not installed** — confirmed).
- Write `scripts/brain-mcp-server.mjs`: `McpServer` over `StdioServerTransport`, registering exactly **one** tool `brain_health` (the §2 spec). No HTTP yet required to prove the pipe — `brain_health` can return `{ ok:true, baseUrl }` plus a best-effort `GET /api/deep/jobs?limit=1` probe.
- Add the `_BUILTIN_NODE_SERVERS` registry + node loop to `src/builtin_mcp.py` (§3).

**Verify (run it):**
1. `node /Users/willlambert/Documents/bertosV2/scripts/brain-mcp-server.mjs` should start and sit on stdio without crashing (Ctrl-C to exit).
2. Start the body; in logs confirm `connect_server("brain", ...)` succeeded and `list_tools` returned `brain_health`.
3. In a BertOS agent turn, confirm the tool appears as `mcp__brain__brain_health` and **invoke it** → returns the health JSON. **Pipe proven.** (No Next/daemon dependency for the bridge to load; the probe just reports `next: down` if the app isn't up.)

### Slice B — wire `brain_council` (request/response, easiest real brain output)
**Change:** add `brain_council` to the bridge → `POST {BERTOS_BASE_URL}/api/chat/council`, map `CouncilResult` → `{ answer, synthesizerId, members, escalated }`.

**Verify (run it):**
1. Bring the brain up: `npm run bertos:host` (or `bertos:dev`); confirm `/api/chat/council` answers a raw curl.
2. From a BertOS agent turn, invoke `mcp__brain__brain_council` with a real prompt (e.g. "Explain the tradeoff between X and Y").
3. Assert the response is a real multi-model Council answer (members listed, a synthesizer id) — **not** a single-model reply. Confirm `escalated:false` for an easy prompt (free path, no spend).

### Slice C — `brain_deep_build` (long-running, the real payoff)
**Change:** add `brain_deep_build` (`POST /api/deep/jobs`, returns `{ jobId }`), `brain_deep_status` (`GET /api/deep/jobs`), `brain_deep_stop` (`DELETE`). Default `engine` to free; map 409 → `PROJECT_BUSY`. Surface `committed`/`rounds`/`ok` in status.

**Verify (run it):**
1. Ensure daemon + Next + Ollama (or a CLI login) are up.
2. From a BertOS agent turn, invoke `mcp__brain__brain_deep_build` on a scratch repo with a tiny instruction ("add a CHANGELOG.md line").
3. Get a `jobId`; poll `brain_deep_status` until `status:"done"`; then **inspect the real repo**: `git log` shows the brain's commit(s), the file changed. The body drove a real Forge run end to end.
4. Negative check: start a second build on the same repo → `PROJECT_BUSY` returned cleanly (agent not crashed).

### Slice D — `brain_auto_cycle` (Lane-B Night Loop)
**Change:** add `brain_auto_cycle` (`POST /api/auto/jobs` with `unattended:true` forced) + `brain_auto_status` + `brain_auto_stop`.

**Verify (run it):**
1. Invoke `mcp__brain__brain_auto_cycle` on a scratch repo with `maxRuns:1`.
2. In the brain's logs, confirm the **Lane-B safety line** fires (`app/api/auto/jobs/route.ts:182` `coerceFreeRoles`) — roles forced to Hermes+Ollama, **no paid CLI selected, escalation stripped**.
3. Poll to completion; confirm a passing pass committed (if `commitOnPass`) and that **no subscription was spent** (no `claude`/`codex`/`gemini` CLI invocation in daemon logs).

---

## 5. Top Risks

1. **Daemon + Next dependency (the brain isn't a library — it's two running servers).** Every real coding tool needs **both** the daemon (`:4319`, files/git/CLIs) **and** the Next server (`:3000`, the `server-only` orchestrators), plus Ollama or a CLI login. If either is down, tools fail. *Mitigation:* `brain_health` reports each leg; tools fail closed with a precise "start `npm run bertos:host`" message; the bridge never auto-spawns the host (avoids orphan servers + the `.next` cache-corruption gotcha #4).
2. **Long-running work over a request/response stdio transport.** Deep Build runs minutes; an MCP tool call cannot hold an SSE stream open to the body's agent. *Mitigation:* job-id + poll via the durable `/api/deep/jobs` + `/api/auto/jobs` routes (start → `*_status` → `*_stop`), never a blocking call. The agent polls; the brain's heartbeat reconciles a dead run to `interrupted`.
3. **Provider / env + free-first enforcement (surprise-spend).** A wrong base URL, a token-gated daemon the bridge doesn't know about, or a missing Lane-B guard could either break runs or silently spend a paid subscription. *Mitigation:* bridge defaults `brain_deep_build` to free and hard-forces `unattended:true` on `brain_auto_cycle` (over the brain's own `coerceFreeRoles` choke at `route.ts:182`); `BRAIN_ALLOW_PAID=0` env flag structurally blocks paid engines for unattended body deployments. Pass `BERTOS_BASE_URL`, `BERTOS_DAEMON_URL`, and (if set) `BERTOS_DAEMON_TOKEN` via the built-in `env`.

**Secondary brain gotchas to honor (from the runtime map):** (a) **single-writer project lock** → 409 PROJECT_BUSY must be a clean tool error, not a crash; (b) **structured calls off prose CLIs** — the bridge does no structured sub-calls itself, so it inherits the brain's `strategy.ts` routing for free (don't add CLI-forcing); (c) **git args as discrete argv arrays** — entirely inside the daemon, the bridge never shells out, so space-in-path corruption can't originate here; (d) **never prod-build against a live dev cache** — operational, applies to whoever starts the host, not the bridge.

---

## Appendix — confirmed code anchors

- Brain seam routes: `app/api/chat/council/route.ts` (`runtime nodejs`), `app/api/deep/jobs/route.ts:58` (POST start), `:44` (GET list), `:50` (DELETE stop), `app/api/auto/jobs/route.ts:182` (`coerceFreeRoles` under `unattended`), `:148` (`unattended` read).
- Lane-B choke: `lib/providers/registry.ts:599` `coerceFreeRoles` (rewrites paid roles → Ollama, strips `escalationCoderId`).
- Deep job shape (for poll mapping): `lib/deepbuild/jobs.ts:42` `status: "running"|"done"|"error"|"interrupted"`, `:46` `committed`, `:47` `rounds`.
- server-only barrier (why not lib-import): orchestrators begin `import "server-only"`; pure exceptions are `lib/deepbuild/core.ts`, `lib/providers/strategy.ts`, `lib/auto/verdict.ts` (not the orchestrators).
- Daemon: `scripts/bertos-daemon.mjs` — `127.0.0.1:4319` (`BERTOS_DAEMON_PORT` :50), `/health` (:535), optional `BERTOS_DAEMON_TOKEN` (:54).
- Body ingestion: `src/mcp_manager.py:148` `connect_server`, `:179` `_connect_stdio`, `:543`/`:604` built-in skip rule, `:555` `mcp__{server_id}__{tool}` namespacing; `src/builtin_mcp.py:69`/`:89`/`:98–123` registries + connect template; `app.py:897` startup; `routes/mcp_routes.py:152` `POST /mcp/servers`; `core/database.py:388` `McpServer`.
- Env: MCP SDK **not yet installed** in bertosV2 (`npm i @modelcontextprotocol/sdk` in Slice A); node v22.22.3 at `/Users/willlambert/.local/bin/node`.
