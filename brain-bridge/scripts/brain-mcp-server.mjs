#!/usr/bin/env node
/**
 * BertOS Brain — MCP bridge (Phase 2, slice A).
 *
 * A stdio MCP server that exposes bertosV2's coding/orchestration "brain" to the
 * BertOS body (the Odysseus fork). It is a THIN HTTP CLIENT to the running
 * bertosV2 Next server — it does NOT import the brain libs (they are
 * `import "server-only"` and only run inside Next) and does NOT drive the daemon
 * directly (that would bypass orchestration + the Lane-A/B cost guards). All
 * guardrails live behind the Next route boundary, so the bridge inherits them.
 *
 * The bridge never auto-spawns Next/daemon/Ollama; it fails closed with a clear
 * message when they are down. Start the brain host with `npm run bertos:host`
 * (Next :3000 + daemon :4319) in ~/Documents/bertosV2.
 *
 * Env (injected by BertOS's builtin_mcp.py):
 *   BERTOS_BASE_URL    bertosV2 Next base    (default http://127.0.0.1:3000)
 *   BERTOS_DAEMON_URL  bertosV2 daemon base  (default http://127.0.0.1:4319)
 *   BRAIN_ALLOW_PAID   "1" to permit paid engines from the bridge (default "0")
 *
 * Slice A exposes one tool, `brain_health`, to prove the pipe end-to-end.
 * Slices B/C/D add brain_council, brain_deep_build (job-id + poll), brain_auto_cycle.
 */
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { z } from "zod";

const BASE_URL = (process.env.BERTOS_BASE_URL || "http://127.0.0.1:3000").replace(/\/$/, "");
const DAEMON_URL = (process.env.BERTOS_DAEMON_URL || "http://127.0.0.1:4319").replace(/\/$/, "");
const ALLOW_PAID = process.env.BRAIN_ALLOW_PAID === "1";
// Optional bearer token for the daemon (or a fronting proxy) when it runs
// token-gated. This is only used on the daemon /health probe — callBrain hits the
// Next route boundary, which is NOT daemon-token-gated, so the token is not threaded
// onto brain API requests.
const DAEMON_TOKEN = process.env.BERTOS_DAEMON_TOKEN || "";

/** Best-effort HTTP probe. "reachable" = any HTTP response; "ok" = 2xx. */
async function probe(base, path, timeoutMs = 2500, headers = undefined) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const res = await fetch(base + path, { signal: ctrl.signal, headers });
    return { reachable: true, ok: res.ok, status: res.status };
  } catch (err) {
    return { reachable: false, ok: false, error: String((err && err.name) || err) };
  } finally {
    clearTimeout(timer);
  }
}

/** The fail-closed message every tool returns when the Next brain host is unreachable. */
const BRAIN_DOWN_HINT =
  "Brain host is DOWN (Next server unreachable at " +
  BASE_URL +
  "). Start it with `npm run bertos:host` in ~/Documents/bertosV2, then retry.";

/** A clean text-content tool result carrying a JSON payload. */
function jsonResult(payload, isError = false) {
  return {
    isError,
    content: [{ type: "text", text: JSON.stringify(payload, null, 2) }],
  };
}

/** Standard fail-closed result when the brain Next host can't be reached. */
function brainDownResult(detail) {
  return jsonResult(
    {
      ok: false,
      code: "BRAIN_DOWN",
      error: BRAIN_DOWN_HINT,
      ...(detail ? { detail } : {}),
    },
    true,
  );
}

/**
 * Call a brain Next API route. Every route returns the BertOS envelope
 * `{ ok:true, data }` | `{ ok:false, error }`. This:
 *  - fails CLOSED (BRAIN_DOWN) when the host is unreachable (fetch throws / aborts),
 *  - threads the optional bearer token,
 *  - unwraps the envelope, surfacing the route's own error code/message on `ok:false`,
 *  - maps the brain's 409 single-writer lock to a clean PROJECT_BUSY tool error.
 * Returns `{ down }` on transport failure, or `{ data }` / `{ error }` on a response.
 */
async function callBrain(method, path, body, timeoutMs = 600_000) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  const headers = { "content-type": "application/json" };
  if (DAEMON_TOKEN) headers["authorization"] = `Bearer ${DAEMON_TOKEN}`;
  let res;
  try {
    res = await fetch(BASE_URL + path, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: ctrl.signal,
    });
  } catch (err) {
    return { down: true, detail: String((err && err.name) || err) };
  } finally {
    clearTimeout(timer);
  }
  let json;
  try {
    json = await res.json();
  } catch {
    // A non-JSON body from the brain host means it's up but not the expected app
    // (e.g. an HTML error page) — treat as down so the caller gets the start hint.
    return { down: true, detail: `non-JSON response (HTTP ${res.status})` };
  }
  if (json && json.ok === true) return { data: json.data, status: res.status };
  const error = (json && json.error) || { code: "UNKNOWN", message: `HTTP ${res.status}` };
  if (res.status === 409 || error.code === "PROJECT_BUSY") {
    return { error: { code: "PROJECT_BUSY", message: error.message || "Another run is working on this project." } };
  }
  return { error: { code: error.code || "BRAIN_ERROR", message: error.message || `HTTP ${res.status}`, details: error.details } };
}

const server = new McpServer({ name: "bertos-brain", version: "0.2.0" });

/** Clean refusal when a paid-subscription tool is invoked while paid is disabled. */
function paidBlockedResult(tool) {
  return jsonResult(
    {
      ok: false,
      code: "PAID_DISABLED",
      error:
        `${tool} drives a paid AI subscription (Claude Code / Codex / Gemini CLI) and is currently disabled. ` +
        "Set BERTOS_BRAIN_ALLOW_PAID=1 (the desktop deploy turns this on) to use subscription power. " +
        "Free tools (plan, council, projects, providers, usage, recommend) work regardless.",
      allowPaid: false,
    },
    true,
  );
}

server.registerTool(
  "brain_health",
  {
    title: "BertOS brain — health",
    description:
      "Check that the BertOS coding brain (bertosV2) is reachable so brain tools " +
      "can run: probes the Next server (orchestration routes) and the local daemon. " +
      "Returns { ok, next, daemon, baseUrl }. If next.ok is false, start the brain " +
      "host with `npm run bertos:host` in ~/Documents/bertosV2 before using brain tools.",
    inputSchema: {},
  },
  async () => {
    // The daemon serves /health (and 404s everything else); probe that, not "/".
    // Carry the bearer token when set so a token-gated daemon doesn't 401 the probe.
    const daemonHeaders = DAEMON_TOKEN ? { authorization: `Bearer ${DAEMON_TOKEN}` } : undefined;
    const [next, daemon] = await Promise.all([
      probe(BASE_URL, "/api/deep/jobs?limit=1", 6000),
      probe(DAEMON_URL, "/health", 2500, daemonHeaders),
    ]);
    // Build-readiness (Deep Build/Auto/build) needs the daemon. The DIRECT probe
    // works on a native run (loopback reachable). But when the body runs in a
    // container, the daemon is loopback-only on the host and NOT reachable at
    // host.docker.internal — yet the brain's Next server reaches it locally, so
    // builds DO work. Fall back to the Next-side doctor check ("Local daemon") so
    // canBuild is HONEST in the container case instead of falsely false.
    let daemonOk = daemon.ok;
    let daemonVia = daemon.ok ? "direct" : null;
    if (!daemon.ok && next.ok) {
      try {
        const res = await fetch(BASE_URL + "/api/doctor", { signal: AbortSignal.timeout(8000) });
        const j = await res.json();
        const checks = (j && j.data && j.data.checks) || (j && j.checks) || [];
        const dmn = checks.find((c) => /daemon/i.test(String(c && (c.name || c.id || ""))));
        if (dmn && dmn.status === "ok") { daemonOk = true; daemonVia = "next-doctor"; }
      } catch {
        /* /api/doctor missing (older brain) — leave daemonOk false, builds may still work */
      }
    }
    const canBuild = next.ok && daemonOk;
    const health = {
      ok: next.ok,
      canBuild,
      next: { url: BASE_URL, ...next },
      daemon: { url: DAEMON_URL, ...daemon, effectiveOk: daemonOk, reachedVia: daemonVia },
      allowPaid: ALLOW_PAID,
      hint: next.ok
        ? `Brain Next reachable — Council / plan / chat work. Build-readiness (Deep Build/Auto/build): ${canBuild ? "READY" : "daemon not confirmed"}` +
          (daemonVia === "next-doctor" ? " (daemon confirmed via the brain server; direct probe N/A inside a container)." : ".")
        : "Brain Next server is DOWN. Run `npm run bertos:host` in your bertosV2.",
    };
    return { content: [{ type: "text", text: JSON.stringify(health, null, 2) }] };
  }
);

// ── brain_council ───────────────────────────────────────────────────────────────
// POST /api/chat/council — multi-model fan-out → one synthesized answer.
// FREE-FIRST: when paid is not allowed, the bridge NEVER passes a paid member or a
// paid synthesizer; it omits memberIds/synthesizerId entirely so the route fans out
// across the ONLINE FREE council voices only (its default free path). The brain's own
// single-paid-adjudication escalation gate still only fires when a paid CLI is online
// AND there is genuine disagreement; the bridge surfaces `escalated`/`escalatedBy` so
// any such spend is always visible to the caller. With BRAIN_ALLOW_PAID=1, an explicit
// `models` list is passed through as memberIds.
server.registerTool(
  "brain_council",
  {
    title: "BertOS brain — Council (multi-model answer)",
    description:
      "Ask the BertOS Council: several distinct-lineage models answer in parallel, " +
      "critique each other, and a synthesizer merges ONE best answer. Free-first — by " +
      "default only free models are used (no paid spend). Returns the synthesized answer " +
      "plus the members used and whether a paid adjudication escalation fired. Requires the " +
      "brain host (`npm run bertos:host`).",
    inputSchema: {
      prompt: z.string().min(1).describe("The question / task to put to the council."),
      models: z
        .array(z.string())
        .optional()
        .describe(
          "Optional explicit member provider ids. IGNORED unless BRAIN_ALLOW_PAID=1 — " +
            "free-first mode lets the brain pick the online free voices itself.",
        ),
      projectId: z.string().optional().describe("Optional project id for memory grounding."),
    },
  },
  async ({ prompt, models, projectId }) => {
    const body = {
      messages: [{ role: "user", content: prompt }],
      useMemory: true,
    };
    if (projectId) body.projectId = projectId;
    // FREE-FIRST belt: only honor an explicit member list when paid is allowed.
    const forced = [];
    if (ALLOW_PAID && Array.isArray(models) && models.length > 0) {
      body.memberIds = models;
    } else {
      forced.push(
        "free-only: omitted memberIds/synthesizerId so the route uses the online FREE council voices",
      );
    }
    const r = await callBrain("POST", "/api/chat/council", body);
    if (r.down) return brainDownResult(r.detail);
    if (r.error) return jsonResult({ ok: false, ...r.error }, true);
    const c = r.data || {};
    return jsonResult({
      ok: true,
      answer: c.answer,
      synthesizerId: c.synthesizerId,
      synthesizerLabel: c.synthesizerLabel,
      usedSynthesis: c.usedSynthesis,
      members: Array.isArray(c.members)
        ? c.members.map((m) => ({ providerId: m.providerId, label: m.label, ok: m.ok }))
        : [],
      ranking: c.ranking,
      escalated: c.escalated === true,
      escalatedBy: c.escalatedBy,
      freeFirst: forced.length ? forced : undefined,
      allowPaid: ALLOW_PAID,
    });
  }
);

// ── brain_deep_build (START) ─────────────────────────────────────────────────────
// POST /api/deep/jobs — start a background Deep Build (Forge). Returns IMMEDIATELY
// with { jobId, status } (201 DeepBuildRun); never blocks. The job edits the repo +
// git-commits per vetted round.
// FREE-FIRST: when paid is not allowed, the bridge HARD-FORCES engine="strongest"
// (strongest ONLINE-free combo + lean 24/7 params; reasoningTier resolves to "free")
// and NEVER sends engine "max"/"pro"/paid. A caller `engine:"max"|"pro"` is honored
// only when BRAIN_ALLOW_PAID=1.
server.registerTool(
  "brain_deep_build",
  {
    title: "BertOS brain — Deep Build (start, background)",
    description:
      "Start a background Deep Build (Forge): the brain plans, codes, verifies and " +
      "git-commits a real repo over multiple vetted rounds. Returns a jobId immediately " +
      "(never blocks) — poll with brain_deep_build_status, stop with brain_deep_build_stop. " +
      "Free-first: defaults to the strongest FREE engine; paid engines are blocked unless " +
      "explicitly allowed. Requires the brain host AND the daemon (`npm run bertos:host`).",
    inputSchema: {
      objective: z.string().min(1).describe("What to build / change in the repo."),
      projectId: z
        .string()
        .optional()
        .describe("Target project id (a project with a local working tree). Defaults to the active project."),
      verifyCommand: z
        .string()
        .optional()
        .describe("Optional command the brain runs to verify each round (e.g. 'npm run build')."),
      engine: z
        .enum(["strongest", "max", "pro"])
        .optional()
        .describe(
          "Engine profile. IGNORED (forced to 'strongest', the free combo) unless BRAIN_ALLOW_PAID=1.",
        ),
    },
  },
  async ({ objective, projectId, verifyCommand, engine }) => {
    const body = { objective };
    if (projectId) body.projectId = projectId;
    if (verifyCommand) body.verifyCommand = verifyCommand;
    // FREE-FIRST belt: only the free "strongest" engine unless paid is explicitly allowed.
    let forced;
    if (ALLOW_PAID && (engine === "max" || engine === "pro")) {
      body.engine = engine;
      forced = `paid engine '${engine}' permitted (BRAIN_ALLOW_PAID=1)`;
    } else {
      body.engine = "strongest"; // strongest ONLINE-free combo; route resolves reasoningTier="free"
      forced =
        engine && engine !== "strongest"
          ? `engine '${engine}' DOWNGRADED to free 'strongest' (paid not allowed)`
          : "engine forced to free 'strongest' (strongest online-free combo, no paid spend)";
    }
    const r = await callBrain("POST", "/api/deep/jobs", body, 30_000);
    if (r.down) return brainDownResult(r.detail);
    if (r.error) return jsonResult({ ok: false, ...r.error }, true);
    const run = r.data || {};
    return jsonResult({
      ok: true,
      jobId: run.id,
      status: run.status,
      projectId: run.projectId,
      objective: run.objective,
      freeFirst: forced,
      allowPaid: ALLOW_PAID,
    });
  }
);

// ── brain_deep_build_status (POLL) ───────────────────────────────────────────────
// GET /api/deep/jobs?limit= — list recent runs; filter to the requested id if given.
server.registerTool(
  "brain_deep_build_status",
  {
    title: "BertOS brain — Deep Build status",
    description:
      "Poll Deep Build runs. With `id`, returns that run's status (running|done|error|" +
      "interrupted), committed count, rounds and any error. Without `id`, lists recent runs. " +
      "Read-only. Requires the brain host.",
    inputSchema: {
      id: z.string().optional().describe("A specific Deep Build jobId. Omit to list recent runs."),
    },
  },
  async ({ id }) => {
    const r = await callBrain("GET", "/api/deep/jobs?limit=20", undefined, 10_000);
    if (r.down) return brainDownResult(r.detail);
    if (r.error) return jsonResult({ ok: false, ...r.error }, true);
    const runs = Array.isArray(r.data) ? r.data : [];
    const slim = (run) => ({
      jobId: run.id,
      projectId: run.projectId,
      objective: run.objective,
      status: run.status,
      committed: run.committed,
      rounds: Array.isArray(run.rounds) ? run.rounds.length : 0,
      startedAt: run.startedAt,
      finishedAt: run.finishedAt,
      error: run.error,
    });
    if (id) {
      const run = runs.find((x) => x.id === id);
      if (!run) return jsonResult({ ok: false, code: "NOT_FOUND", error: `No Deep Build run with id '${id}'.` }, true);
      return jsonResult({ ok: true, run: slim(run) });
    }
    return jsonResult({ ok: true, count: runs.length, runs: runs.map(slim) });
  }
);

// ── brain_deep_build_stop ────────────────────────────────────────────────────────
// DELETE /api/deep/jobs?id= — request a running Deep Build to stop.
server.registerTool(
  "brain_deep_build_stop",
  {
    title: "BertOS brain — Deep Build stop",
    description:
      "Request a running Deep Build to stop after its current phase. Requires the brain host.",
    inputSchema: {
      id: z.string().min(1).describe("The Deep Build jobId to stop."),
    },
  },
  async ({ id }) => {
    const r = await callBrain("DELETE", `/api/deep/jobs?id=${encodeURIComponent(id)}`, undefined, 10_000);
    if (r.down) return brainDownResult(r.detail);
    if (r.error) return jsonResult({ ok: false, ...r.error }, true);
    return jsonResult({ ok: true, stopping: (r.data && r.data.stopping) || id });
  }
);

// ── brain_auto_cycle ─────────────────────────────────────────────────────────────
// POST /api/auto/jobs — start a Lane-B Auto Mode job. Returns IMMEDIATELY with
// { jobId, status } (201 AutoJob); never blocks.
// FREE-FIRST: the bridge HARD-FORCES `unattended:true` regardless of input, which trips
// the route's coerceFreeRoles choke (route.ts:182) — any paid role → Ollama, escalation
// CLI stripped. No paid-loop knob is ever exposed; this can never spend a subscription.
server.registerTool(
  "brain_auto_cycle",
  {
    title: "BertOS brain — Auto cycle (Lane-B, free)",
    description:
      "Run one BertOS Auto Mode cycle on a repo (the free Lane-B Night Loop): plan → code → " +
      "verify, free Hermes+Ollama only. ALWAYS unattended (hard-forced) so it can NEVER spend a " +
      "paid subscription. Returns a jobId immediately (never blocks). Requires the brain host AND " +
      "the daemon (`npm run bertos:host`).",
    inputSchema: {
      task: z.string().min(1).describe("The objective/task for this cycle."),
      projectId: z.string().min(1).describe("Target project id (with a local working tree)."),
    },
  },
  async ({ task, projectId }) => {
    // HARD-FORCE Lane B: unattended:true (trips coerceFreeRoles), one pass.
    const body = {
      objective: task,
      tasks: [task],
      projectId,
      unattended: true,
      maxRuns: 1,
      commitOnPass: true,
    };
    const r = await callBrain("POST", "/api/auto/jobs", body, 30_000);
    if (r.down) return brainDownResult(r.detail);
    if (r.error) return jsonResult({ ok: false, ...r.error }, true);
    const job = r.data || {};
    return jsonResult({
      ok: true,
      jobId: job.id,
      status: job.status,
      projectId: job.projectId,
      roles: job.roles,
      freeFirst: "unattended:true hard-forced → Lane-B coerceFreeRoles (free Hermes+Ollama only, no paid spend)",
      allowPaid: ALLOW_PAID,
    });
  }
);

// ════════════════════════════════════════════════════════════════════════════
// Expanded brain surface (v0.2) — make BertOS a real build+automate powerhouse.
// Free / read-only tools are ALWAYS available. Tools that spend a paid
// subscription (the CLI agents) are gated behind BRAIN_ALLOW_PAID and return a
// clear PAID_DISABLED message when off. Auto Mode stays free elsewhere (above).
// Contracts mapped from the live bertosV2 routes; envelopes are {ok,data}.
// ════════════════════════════════════════════════════════════════════════════

const PAID_CLIS = new Set(["claude-code", "codex-cli", "gemini-cli"]);

// ── brain_chat — ask ONE model (free Ollama or a subscription CLI) ───────────────
server.registerTool(
  "brain_chat",
  {
    title: "BertOS brain — ask one model",
    description:
      "Ask a SINGLE model one question (lighter/faster than the full Council), grounded in shared BertOS memory. " +
      "Choose the engine with providerId: 'ollama' (free local, default) or a subscription CLI " +
      "('claude-code' | 'codex-cli' | 'gemini-cli'). Subscription engines need BRAIN_ALLOW_PAID=1. Requires the brain host.",
    inputSchema: {
      prompt: z.string().min(1).describe("The question / instruction."),
      providerId: z
        .string()
        .optional()
        .describe("Engine id: 'ollama' (free, default) or a subscription CLI (needs BRAIN_ALLOW_PAID=1)."),
      projectId: z.string().optional().describe("Optional project id for memory + cwd grounding."),
    },
  },
  async ({ prompt, providerId, projectId }) => {
    const pid = providerId || "ollama";
    if (PAID_CLIS.has(pid) && !ALLOW_PAID) return paidBlockedResult(`brain_chat (providerId='${pid}')`);
    const body = { providerId: pid, messages: [{ role: "user", content: prompt }], useMemory: true };
    if (projectId) body.projectId = projectId;
    const r = await callBrain("POST", "/api/chat", body);
    if (r.down) return brainDownResult(r.detail);
    if (r.error) return jsonResult({ ok: false, ...r.error }, true);
    const d = r.data || {};
    return jsonResult({ ok: true, providerId: d.providerId, model: d.model, text: d.text, latencyMs: d.latencyMs, allowPaid: ALLOW_PAID });
  }
);

// ── brain_projects — list build/automate targets ─────────────────────────────────
server.registerTool(
  "brain_projects",
  {
    title: "BertOS brain — list projects",
    description:
      "List the projects/repos the brain can build or automate (id, name, status, on-disk localPath). " +
      "Use this to find a projectId/target for brain_build, brain_deep_build, brain_deep_plan, brain_grounded_objective, etc. " +
      "Read-only. Requires the brain host.",
    inputSchema: {},
  },
  async () => {
    const r = await callBrain("GET", "/api/projects", undefined, 10_000);
    if (r.down) return brainDownResult(r.detail);
    if (r.error) return jsonResult({ ok: false, ...r.error }, true);
    const projects = Array.isArray(r.data) ? r.data : [];
    return jsonResult({
      ok: true,
      count: projects.length,
      projects: projects.map((p) => ({ id: p.id, name: p.name, status: p.status, localPath: p.localPath, repoUrl: p.repoUrl, tags: p.tags })),
    });
  }
);

// ── brain_project_create — register a repo as a target ───────────────────────────
server.registerTool(
  "brain_project_create",
  {
    title: "BertOS brain — register a project",
    description:
      "Register a repo/workstream as a brain project so it can be a build/automate target. Only 'name' is required; " +
      "set 'localPath' to an absolute on-disk repo path to make it buildable. Creates a TRACKING RECORD only — it never " +
      "clones or scaffolds. Requires the brain host.",
    inputSchema: {
      name: z.string().min(1).describe("Project name."),
      localPath: z.string().optional().describe("Absolute path to the local working tree (required for builds)."),
      repoUrl: z.string().optional().describe("Source repo URL."),
      description: z.string().optional(),
      status: z.enum(["active", "paused", "idea", "archived"]).optional(),
    },
  },
  async ({ name, localPath, repoUrl, description, status }) => {
    const body = { name };
    if (localPath) body.localPath = localPath;
    if (repoUrl) body.repoUrl = repoUrl;
    if (description) body.description = description;
    if (status) body.status = status;
    const r = await callBrain("POST", "/api/projects", body, 10_000);
    if (r.down) return brainDownResult(r.detail);
    if (r.error) return jsonResult({ ok: false, ...r.error }, true);
    const p = r.data || {};
    return jsonResult({ ok: true, id: p.id, name: p.name, localPath: p.localPath, status: p.status });
  }
);

// ── brain_build — BUILD IT NOW with a subscription CLI (one-shot, edits files) ────
// PAID: drives Claude Code / Codex / Gemini CLI inside the repo with editing on.
server.registerTool(
  "brain_build",
  {
    title: "BertOS brain — build it now (subscription)",
    description:
      "Drive a coding-agent SUBSCRIPTION (Claude Code / Codex CLI / Gemini CLI) to edit real files in a project's repo " +
      "and accomplish a task, in ONE shot (edits the working tree; does not auto-commit). This is the direct 'build it' " +
      "action. For larger multi-round autonomous builds with per-round verify+commit, use brain_deep_build instead. " +
      "Needs BRAIN_ALLOW_PAID=1, the brain host + daemon, and the chosen CLI installed + logged in.",
    inputSchema: {
      projectId: z.string().min(1).describe("Target project id (must have a localPath). Get one from brain_projects."),
      task: z.string().min(1).describe("What to build / change in the repo."),
      tool: z
        .enum(["claude-code", "codex-cli", "gemini-cli"])
        .optional()
        .describe("Which subscription CLI to drive (default claude-code)."),
    },
  },
  async ({ projectId, task, tool }) => {
    if (!ALLOW_PAID) return paidBlockedResult("brain_build");
    const body = { projectId, task };
    if (tool) body.tool = tool;
    const r = await callBrain("POST", "/api/build", body, 600_000);
    if (r.down) return brainDownResult(r.detail);
    if (r.error) return jsonResult({ ok: false, ...r.error }, true);
    const d = r.data || {};
    return jsonResult({ ok: true, tool: d.tool, projectId: d.projectId, cwd: d.cwd, durationMs: d.durationMs, text: d.text, allowPaid: ALLOW_PAID });
  }
);

// ── brain_deep_plan — FREE plan preview (verified live) ──────────────────────────
server.registerTool(
  "brain_deep_plan",
  {
    title: "BertOS brain — plan a build (free preview)",
    description:
      "Preview HOW the brain would build something: it fans out several approaches from different angles, scores them " +
      "with a reviewer panel, and synthesizes the winning plan. FREE (local Ollama) and READ-ONLY — touches no files. " +
      "Great to run before brain_deep_build / brain_build. Requires the brain host.",
    inputSchema: {
      objective: z.string().min(1).describe("What to build / change."),
      projectId: z.string().optional().describe("Optional project id for memory grounding."),
      approaches: z.number().int().min(1).max(6).optional().describe("How many approaches to fan out."),
    },
  },
  async ({ objective, projectId, approaches }) => {
    const body = { objective };
    if (projectId) body.projectId = projectId;
    if (approaches) body.approaches = approaches;
    const r = await callBrain("POST", "/api/deep/plan", body, 300_000);
    if (r.down) return brainDownResult(r.detail);
    if (r.error) return jsonResult({ ok: false, ...r.error }, true);
    const d = r.data || {};
    const win = typeof d.winner === "number" && Array.isArray(d.approaches) ? d.approaches[d.winner] : undefined;
    return jsonResult({
      ok: true,
      plan: d.plan,
      rationale: d.rationale,
      winningApproach: win ? { angle: win.angle, summary: win.summary, steps: win.steps } : undefined,
      approaches: Array.isArray(d.approaches) ? d.approaches.map((a) => ({ angle: a.angle, summary: a.summary })) : [],
      totals: d.totals,
    });
  }
);

// ── brain_deep_refute — FREE adversarial verify of a diff ────────────────────────
server.registerTool(
  "brain_deep_refute",
  {
    title: "BertOS brain — adversarially verify a diff (free)",
    description:
      "Run a panel of independent skeptics that each try to REFUTE a code diff against an objective; the change 'survives' " +
      "unless a majority find a blocking issue. FREE (Ollama), read-only. You supply the diff. Requires the brain host.",
    inputSchema: {
      objective: z.string().min(1).describe("What the diff is supposed to accomplish."),
      diff: z.string().min(1).describe("The unified diff / patch to scrutinize."),
      skeptics: z.number().int().min(1).max(7).optional().describe("How many skeptics to spawn."),
    },
  },
  async ({ objective, diff, skeptics }) => {
    const body = { objective, diff };
    if (skeptics) body.skeptics = skeptics;
    const r = await callBrain("POST", "/api/deep/refute", body, 200_000);
    if (r.down) return brainDownResult(r.detail);
    if (r.error) return jsonResult({ ok: false, ...r.error }, true);
    const d = r.data || {};
    return jsonResult({ ok: true, survived: d.survived, skeptics: d.skeptics, rejections: d.rejections, issues: d.issues });
  }
);

// ── brain_recommend_automations — FREE "what should I automate?" ─────────────────
server.registerTool(
  "brain_recommend_automations",
  {
    title: "BertOS brain — what should I automate?",
    description:
      "Get 4–6 ranked, concrete automation recommendations (auto-loop | audit | report | maintenance | fix-config) " +
      "grounded in your REAL projects and their build health. Each comes with a ready-to-run objective. FREE. Requires the brain host.",
    inputSchema: {},
  },
  async () => {
    const r = await callBrain("GET", "/api/automations/recommend", undefined, 120_000);
    if (r.down) return brainDownResult(r.detail);
    if (r.error) return jsonResult({ ok: false, ...r.error }, true);
    const d = r.data || {};
    return jsonResult({
      ok: true,
      recommendations: (d.recommendations || []).map((x) => ({
        title: x.title, type: x.type, projectName: x.projectName, projectId: x.projectId, objective: x.objective, why: x.why, impact: x.impact, effort: x.effort,
      })),
    });
  }
);

// ── brain_grounded_objective — FREE: best next objective for a project ───────────
server.registerTool(
  "brain_grounded_objective",
  {
    title: "BertOS brain — best next objective for a project",
    description:
      "Synthesize the single highest-value next objective for a project from its memory (decisions, recurring review " +
      "complaints), recent commits and build pass-rate. FREE. Feed the result into brain_deep_build / brain_build. Requires the brain host.",
    inputSchema: { projectId: z.string().min(1).describe("Target project id (from brain_projects).") },
  },
  async ({ projectId }) => {
    const r = await callBrain("POST", "/api/auto/objective", { projectId }, 120_000);
    if (r.down) return brainDownResult(r.detail);
    if (r.error) return jsonResult({ ok: false, ...r.error }, true);
    const d = r.data || {};
    return jsonResult({ ok: true, objective: d.objective, title: d.title, rationale: d.rationale, grounded: d.grounded, signals: d.signals });
  }
);

// ── brain_suggest_improvements — improvement ideas for a project (free-degrading) ─
server.registerTool(
  "brain_suggest_improvements",
  {
    title: "BertOS brain — improvement ideas for a project",
    description:
      "Get a batch of detailed, codebase-grounded improvement suggestions for a project; each includes a ready-to-run " +
      "objective. Reads the repo with a CLI agent when BRAIN_ALLOW_PAID=1, else a lighter free pass. Requires the brain host.",
    inputSchema: {
      projectId: z.string().min(1).describe("Target project id (from brain_projects)."),
      count: z.number().int().min(1).max(10).optional().describe("How many suggestions."),
    },
  },
  async ({ projectId, count }) => {
    const body = { projectId };
    if (count) body.count = count;
    if (!ALLOW_PAID) body.providerId = "ollama"; // free pass when paid is off
    const r = await callBrain("POST", "/api/auto/suggest", body, 300_000);
    if (r.down) return brainDownResult(r.detail);
    if (r.error) return jsonResult({ ok: false, ...r.error }, true);
    const d = r.data || {};
    return jsonResult({
      ok: true,
      suggestions: (d.suggestions || []).map((s) => ({ title: s.title, objective: s.objective, category: s.category, rationale: s.rationale })),
      allowPaid: ALLOW_PAID,
    });
  }
);

// ── brain_providers — which engines/subscriptions are live ───────────────────────
server.registerTool(
  "brain_providers",
  {
    title: "BertOS brain — provider/subscription status",
    description:
      "Show which AI engines are live: your subscription CLIs (claude-code / codex-cli / gemini-cli), local Ollama models, " +
      "and free cloud providers — configured/online per engine. Read-only. Use to confirm your subscriptions are connected " +
      "before a big build. Requires the brain host.",
    inputSchema: {},
  },
  async () => {
    const r = await callBrain("GET", "/api/providers", undefined, 15_000);
    if (r.down) return brainDownResult(r.detail);
    if (r.error) return jsonResult({ ok: false, ...r.error }, true);
    const d = r.data || {};
    const provs = Array.isArray(d.providers) ? d.providers : [];
    return jsonResult({
      ok: true,
      allowPaid: ALLOW_PAID,
      providers: provs.map((p) => ({ id: p.id, label: p.label, group: p.group, configured: p.configured, online: p.online, disabled: p.disabled, paid: p.paid })),
    });
  }
);

// ── brain_provider_test — liveness-check one engine ──────────────────────────────
server.registerTool(
  "brain_provider_test",
  {
    title: "BertOS brain — test a provider",
    description:
      "Liveness-check ONE engine end-to-end with a tiny real prompt (returns pass/fail + latency). Refuses disabled paid " +
      "API providers without calling. Good for 'is my subscription actually working?'. Requires the brain host.",
    inputSchema: { id: z.string().min(1).describe("Provider id, e.g. 'ollama' or a subscription CLI id.") },
  },
  async ({ id }) => {
    const r = await callBrain("POST", "/api/providers/test", { id }, 60_000);
    if (r.down) return brainDownResult(r.detail);
    if (r.error) return jsonResult({ ok: false, ...r.error }, true);
    const d = r.data || {};
    return jsonResult({ ok: true, id: d.id, passed: d.passed, model: d.model, latencyMs: d.latencyMs, text: d.text });
  }
);

// ── brain_usage — token usage, paid-vs-free split (reassurance, not billing) ──────
server.registerTool(
  "brain_usage",
  {
    title: "BertOS brain — usage (paid vs free)",
    description:
      "Show approximate token usage by model since the brain started, with the paid-vs-free split and free-share % " +
      "(reassurance, NOT billing). Read-only. Requires the brain host.",
    inputSchema: {},
  },
  async () => {
    const r = await callBrain("GET", "/api/usage", undefined, 10_000);
    if (r.down) return brainDownResult(r.detail);
    if (r.error) return jsonResult({ ok: false, ...r.error }, true);
    const d = r.data || {};
    return jsonResult({
      ok: true,
      totalTokens: d.totalTokens,
      paidTokens: d.paidTokens,
      freeShare: d.freeShare,
      models: (d.models || []).map((m) => ({ id: m.id, paid: m.paid, calls: m.calls, tokens: m.tokens })),
    });
  }
);

// ── brain_foreman_runs — what the home brain recently did (read-only) ────────────
server.registerTool(
  "brain_foreman_runs",
  {
    title: "BertOS brain — recent foreman activity",
    description:
      "Read-only history of what the home brain (foreman) recently did: its delegation decisions, the free workers it " +
      "dispatched, and how it finalized. Requires the brain host.",
    inputSchema: { limit: z.number().int().min(1).max(60).optional().describe("How many recent runs (default 20).") },
  },
  async ({ limit }) => {
    const q = limit ? `?limit=${limit}` : "";
    const r = await callBrain("GET", `/api/foreman/runs${q}`, undefined, 10_000);
    if (r.down) return brainDownResult(r.detail);
    if (r.error) return jsonResult({ ok: false, ...r.error }, true);
    const d = r.data || {};
    return jsonResult({
      ok: true,
      summary: d.summary,
      runs: (d.runs || []).slice(0, limit || 20).map((x) => ({ ts: x.ts, question: x.question, needsDelegation: x.needsDelegation, delegations: (x.delegations || []).length })),
    });
  }
);

const transport = new StdioServerTransport();
await server.connect(transport);
// Keep stderr clean of stdout pollution; MCP uses stdout for the JSON-RPC stream.
process.stderr.write(`[brain-mcp] bertos-brain MCP server up (base=${BASE_URL}, daemon=${DAEMON_URL}, allowPaid=${ALLOW_PAID})\n`);
