"""BertOS brain proxy routes.

Thin, read-only HTTP proxy from the body to the running bertosV2 "brain" Next
server, so the browser SPA can render the Memory Tree + a Brain panel without the
browser needing to reach the brain host directly (it only ever talks to the body).

All endpoints are GET, read-only, and fail soft: when the brain is down they
return `{ok: false, code: "BRAIN_DOWN", ...}` with HTTP 200 so the UI can show a
friendly "start your brain" state instead of erroring. The brain base URL is the
same one the MCP bridge uses (BERTOS_BRAIN_BASE_URL).
"""
import asyncio
import logging
import os

import httpx
from fastapi import APIRouter, Body, Depends

try:
    from routes.email_helpers import require_user
except Exception:  # pragma: no cover - fallback if auth module shifts
    def require_user(*_a, **_k):  # type: ignore
        return ""

logger = logging.getLogger(__name__)

_BRAIN_BASE = os.environ.get("BERTOS_BRAIN_BASE_URL", "http://127.0.0.1:3000").rstrip("/")


async def _brain_get(path: str, timeout: float = 30.0) -> dict:
    """GET a brain route and unwrap the {ok,data} envelope. Fails soft."""
    url = _BRAIN_BASE + path
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            res = await client.get(url)
    except Exception as e:  # transport error → brain down
        return {
            "ok": False,
            "code": "BRAIN_DOWN",
            "error": (
                f"Brain host unreachable at {_BRAIN_BASE}. Start it with "
                "`npm run bertos:host` in your bertosV2."
            ),
            "detail": str(e)[:200],
        }
    try:
        body = res.json()
    except Exception:
        return {"ok": False, "code": "BRAIN_BAD_RESPONSE", "error": f"Non-JSON from brain (HTTP {res.status_code})."}
    if isinstance(body, dict) and body.get("ok") is True:
        return {"ok": True, "data": body.get("data")}
    err = (body or {}).get("error") if isinstance(body, dict) else None
    return {
        "ok": False,
        "code": (err or {}).get("code", "BRAIN_ERROR") if isinstance(err, dict) else "BRAIN_ERROR",
        "error": (err or {}).get("message", f"HTTP {res.status_code}") if isinstance(err, dict) else f"HTTP {res.status_code}",
        "status": res.status_code,
    }


def setup_brain_routes() -> APIRouter:
    router = APIRouter(prefix="/api/brain", tags=["brain"])

    @router.get("/status")
    async def brain_status():
        """Lightweight: is the brain up, and which engines/subscriptions are online."""
        health = await _brain_get("/api/health", timeout=6.0)
        if not health.get("ok"):
            return {"ok": False, "code": health.get("code", "BRAIN_DOWN"), "error": health.get("error"), "up": False}
        provs = await _brain_get("/api/providers", timeout=10.0)
        engines = []
        if provs.get("ok"):
            for p in ((provs.get("data") or {}).get("providers") or []):
                engines.append({
                    "id": p.get("id"), "label": p.get("label"), "group": p.get("group"),
                    "online": p.get("online"), "paid": p.get("paid"), "disabled": p.get("disabled"),
                })
        return {"ok": True, "up": True, "baseUrl": _BRAIN_BASE, "engines": engines}

    @router.get("/memory/graph")
    async def brain_memory_graph(scope: str = "all", projectId: str | None = None):
        """The knowledge-graph projection of the shared memory: {nodes, links, counts}."""
        q = f"?scope={'project' if scope == 'project' else 'all'}"
        if projectId:
            q += f"&projectId={projectId}"
        r = await _brain_get("/api/memory/graph" + q, timeout=45.0)
        return r

    @router.get("/memory/recent")
    async def brain_memory_recent(limit: int = 40, kind: str | None = None, projectId: str | None = None):
        """Most recent durable memory notes."""
        q = f"?limit={max(1, min(200, limit))}"
        if kind:
            q += f"&kind={kind}"
        if projectId:
            q += f"&projectId={projectId}"
        return await _brain_get("/api/memory/recent" + q, timeout=15.0)

    @router.get("/projects")
    async def brain_projects():
        """The brain's projects (build/automate targets)."""
        return await _brain_get("/api/projects", timeout=10.0)

    @router.get("/deep-jobs")
    async def brain_deep_jobs(limit: int = 10):
        """Recent Deep Build runs (what the AI built), newest first."""
        return await _brain_get(f"/api/deep/jobs?limit={max(1, min(50, limit))}", timeout=12.0)

    @router.get("/recommend")
    async def brain_recommend():
        """Ranked automation recommendations grounded in the real projects.
        Slow (runs a free model) — call on demand, not on dashboard open."""
        return await _brain_get("/api/automations/recommend", timeout=130.0)

    @router.get("/usage")
    async def brain_usage():
        """Per-engine call/token usage + the free-vs-paid routing split — the
        proof the orchestrator keeps work cheap (freeShare%, paidTokens, etc.)."""
        return await _brain_get("/api/usage", timeout=10.0)

    @router.get("/auto-jobs")
    async def brain_auto_jobs(limit: int = 8):
        """Auto Mode jobs — the self-scheduling night loop's per-project build
        loops (objective + planner/coder/checker role engines + status)."""
        return await _brain_get(f"/api/auto/jobs?limit={max(1, min(40, limit))}", timeout=12.0)

    @router.get("/limits")
    async def brain_limits():
        """Usage limits + health summary (daily soft-limit, % used, active model,
        auto role engines, warnings) — powers the 'seeing my limits' view."""
        return await _brain_get("/api/usage/limits", timeout=10.0)

    @router.post("/build")
    async def brain_build(body: dict = Body(default_factory=dict), owner: str = Depends(require_user)):
        """Fire a Deep Build in the BACKGROUND and return immediately.

        This is the Mission Control 'Build launcher'. It's a deliberate,
        user-initiated action (the click + confirm IS the approval gate for the
        patches/git/paid-calls Deep Build performs) — so it's allowed to spend
        the brain's subscriptions, unlike unattended automations. The brain's
        Deep Build registers a job that the live 'Now Running' panel surfaces, so
        the user watches it there; we only need to keep the upstream stream alive
        so the build runs to completion.
        """
        objective = str((body or {}).get("objective") or "").strip()
        project_id = str((body or {}).get("projectId") or "").strip()
        if not objective:
            return {"ok": False, "error": "An objective is required."}
        if not project_id:
            return {"ok": False, "error": "A project is required."}

        async def _run():
            ok_done = False
            try:
                timeout = httpx.Timeout(1800.0, connect=10.0)
                async with httpx.AsyncClient(timeout=timeout) as client:
                    async with client.stream(
                        "POST", _BRAIN_BASE + "/api/deep/run",
                        json={"objective": objective, "projectId": project_id},
                    ) as resp:
                        # Drain the SSE stream so the upstream build isn't cancelled
                        # by an early client disconnect; the live job state is read
                        # from /api/brain/deep-jobs by Mission Control.
                        async for _ in resp.aiter_lines():
                            pass
                ok_done = True
            except Exception as e:  # background task — log, never raise
                logger.warning(f"brain build background run failed: {e}")
            # Proactive text: ping the phone (via the configured ntfy channel)
            # the moment the build is done, so you can fire-and-walk-away.
            try:
                from routes.note_routes import dispatch_reminder
                await dispatch_reminder(
                    title="Bert's AI",
                    note_body=f"{'✅' if ok_done else '⚠️'} Build {'finished' if ok_done else 'ended'}: {objective[:90]}",
                    note_id=f"build-{abs(hash(objective)) % 1000000}",
                    owner=owner or "",
                )
            except Exception as e:
                logger.debug(f"build-done notify skipped: {e}")

        asyncio.create_task(_run())
        return {"ok": True, "started": True, "objective": objective[:120]}

    @router.get("/audit")
    async def brain_audit():
        """Self-audit: score Bert's AI across the Four Cs (Context / Connections /
        Capabilities / Cadence) and rank the highest-leverage gaps to build next.
        The research's compounding self-improvement loop. Free model; fails soft."""
        providers, projects, limits, graph, autojobs, deepjobs = await asyncio.gather(
            _brain_get("/api/providers", 10.0),
            _brain_get("/api/projects", 10.0),
            _brain_get("/api/usage/limits", 10.0),
            _brain_get("/api/memory/graph", 45.0),
            _brain_get("/api/auto/jobs?limit=20", 10.0),
            _brain_get("/api/deep/jobs?limit=20", 10.0),
        )
        engines = ((providers.get("data") or {}).get("providers") or []) if providers.get("ok") else []
        online = [e for e in engines if e.get("online")]
        subs = [e.get("label") or e.get("id") for e in online if e.get("paid") and not e.get("disabled")]
        proj = projects.get("data") or [] if projects.get("ok") else []
        buildable = [p for p in proj if p.get("localPath")]
        lim = ((limits.get("data") or {}).get("summary") or {}) if limits.get("ok") else {}
        counts = (((graph.get("data") or {}).get("graph") or {}).get("counts") or {}) if graph.get("ok") else {}
        autos = autojobs.get("data") or [] if autojobs.get("ok") else []
        builds = deepjobs.get("data") or [] if deepjobs.get("ok") else []
        snapshot = (
            f"ENGINES: {len(online)} online ({len(subs)} subscriptions: {', '.join(subs[:6]) or 'none'}); "
            f"{lim.get('localOrFreeCalls24h', 0)} free + {lim.get('subscriptionCalls24h', 0)} sub calls in 24h.\n"
            f"PROJECTS: {len(proj)} registered, {len(buildable)} buildable (with local paths).\n"
            f"MEMORY: {counts.get('memories', 0)} memories, {counts.get('projects', 0)} projects, "
            f"{counts.get('tags', 0)} tags, {counts.get('links', 0)} links in the knowledge graph.\n"
            f"CADENCE: {len(autos)} Auto Mode loops, {len([b for b in builds if b.get('status') == 'done'])} completed Deep Builds.\n"
            f"LIMITS: {lim.get('globalPercentUsed', 0)}% of daily soft-limit used, state={lim.get('globalState', '?')}. "
            f"Warnings: {'; '.join((lim.get('warnings') or [])[:3]) or 'none'}."
        )
        from src.endpoint_resolver import resolve_endpoint
        from src.llm_core import llm_call_async
        url = model = None
        try:
            url, model, headers = resolve_endpoint("utility", free_only=True)
            if not url:
                url, model, headers = resolve_endpoint("default", free_only=True)
        except Exception:
            headers = {}
        if not url or not model:
            return {"ok": True, "snapshot": snapshot, "scores": None, "gaps": [], "note": "Configure a free model for the AI scorecard."}
        prompt = (
            "You audit a PERSONAL AI operating system ('Bert's AI'). Score it across the Four Cs, each 0-100, "
            "based on the snapshot, then rank the TOP 5 highest-leverage gaps to build next (each with a concrete next action). "
            "Four Cs: Context (what it knows about the user), Connections (data/APIs it reaches), Capabilities (skills/things it can produce), Cadence (autonomous scheduled work). "
            "This OS is already strong (multi-engine orchestration, Deep Build, memory graph, automations). Be honest but calibrated. "
            "Return ONLY JSON: {\"scores\":{\"context\":n,\"connections\":n,\"capabilities\":n,\"cadence\":n,\"overall\":n}, "
            "\"headline\":\"one-line state of the OS\", "
            "\"gaps\":[{\"title\":\"...\",\"pillar\":\"Context|Connections|Capabilities|Cadence\",\"why\":\"short\",\"action\":\"concrete next step\",\"impact\":\"high|medium|low\"}]}\n\n"
            f"SNAPSHOT:\n{snapshot}"
        )
        try:
            raw = await llm_call_async(url, model, [{"role": "user", "content": prompt}], headers=headers, max_tokens=1400)
        except Exception as e:
            logger.debug(f"audit llm failed: {e}")
            return {"ok": True, "snapshot": snapshot, "scores": None, "gaps": [], "error": "ai_unavailable"}
        import json as _json
        import re as _re
        parsed = None
        try:
            mch = _re.search(r"\{.*\}", (raw or "").strip(), _re.DOTALL)
            if mch:
                parsed = _json.loads(mch.group(0))
        except Exception:
            parsed = None
        if not isinstance(parsed, dict):
            return {"ok": True, "snapshot": snapshot, "scores": None, "gaps": [], "headline": (raw or "").strip()[:200], "model": model}
        gaps = parsed.get("gaps") if isinstance(parsed.get("gaps"), list) else []
        return {"ok": True, "snapshot": snapshot, "scores": parsed.get("scores"),
                "headline": str(parsed.get("headline") or "")[:200],
                "gaps": gaps[:6], "model": model}

    @router.get("/memory-lint")
    async def brain_memory_lint():
        """Memory health check — structural (dead links, isolated nodes, orphan
        tags) computed deterministically + a free-model pass to flag stale /
        redundant / conflicting facts. The research's /memory-lint."""
        graph, recent = await asyncio.gather(
            _brain_get("/api/memory/graph", 45.0),
            _brain_get("/api/memory/recent?limit=60", 15.0),
        )
        g = ((graph.get("data") or {}).get("graph") or {}) if graph.get("ok") else {}
        nodes = g.get("nodes") or []
        links = g.get("links") or []
        counts = g.get("counts") or {}
        node_ids = {n.get("id") for n in nodes}
        linked = set()
        dead = 0
        for l in links:
            s, t = l.get("source"), l.get("target")
            if s not in node_ids or t not in node_ids:
                dead += 1
            else:
                linked.add(s); linked.add(t)
        isolated = [n for n in nodes if n.get("type") == "memory" and n.get("id") not in linked]
        orphan_tags = [n for n in nodes if n.get("type") == "tag" and (n.get("degree") or 0) <= 1]
        structural = {
            "memories": counts.get("memories", 0), "links": counts.get("links", len(links)),
            "deadLinks": dead, "isolatedMemories": len(isolated), "orphanTags": len(orphan_tags),
        }
        # LLM pass over recent memory previews for stale / redundant / conflicting facts
        notes = ((recent.get("data") or {}).get("notes") or []) if recent.get("ok") else []
        issues = []
        model = None
        if notes:
            previews = "\n".join(f"- {(n.get('preview') or '')[:120]}" for n in notes[:50] if n.get("preview"))
            from src.endpoint_resolver import resolve_endpoint
            from src.llm_core import llm_call_async
            url = None
            try:
                url, model, headers = resolve_endpoint("utility", free_only=True)
                if not url:
                    url, model, headers = resolve_endpoint("default", free_only=True)
            except Exception:
                headers = {}
            if url and model:
                prompt = (
                    "You lint a personal AI's memory (durable fact notes). From these recent notes, flag at most 6 "
                    "issues: STALE (mentions a version/date/file that's likely outdated), REDUNDANT (near-duplicate of "
                    "another), or CONFLICT (contradicts another). Return ONLY JSON: "
                    '{"issues":[{"type":"stale|redundant|conflict","note":"the problem in <12 words","fix":"short suggestion"}]}. '
                    "If the memory looks healthy, return an empty issues array.\n\nNOTES:\n" + previews
                )
                try:
                    raw = await llm_call_async(url, model, [{"role": "user", "content": prompt}], headers=headers, max_tokens=900)
                    import json as _json
                    import re as _re
                    mch = _re.search(r"\{.*\}", (raw or "").strip(), _re.DOTALL)
                    if mch:
                        p = _json.loads(mch.group(0))
                        if isinstance(p, dict) and isinstance(p.get("issues"), list):
                            issues = p["issues"][:6]
                except Exception as e:
                    logger.debug(f"memory-lint llm failed: {e}")
        return {"ok": True, "structural": structural, "issues": issues, "model": model}

    @router.get("/level-up")
    async def brain_level_up():
        """Level-up: the partner to /audit. Reasons through the five level-up
        lenses (what repeats 3+ times? what felt manual? would a smart intern
        nail it? what breaks at 10x? biggest growth lever?) against real recent
        activity and emits a RANKED backlog of the next things to build. The
        research's compounding self-improvement loop — /audit scores, /level-up
        decides what to do about it. Free model; fails soft."""
        projects, deepjobs, autojobs, recent, limits = await asyncio.gather(
            _brain_get("/api/projects", 10.0),
            _brain_get("/api/deep/jobs?limit=20", 12.0),
            _brain_get("/api/auto/jobs?limit=20", 12.0),
            _brain_get("/api/memory/recent?limit=40", 15.0),
            _brain_get("/api/usage/limits", 10.0),
        )
        proj = (projects.get("data") or []) if projects.get("ok") else []
        builds = (deepjobs.get("data") or []) if deepjobs.get("ok") else []
        autos = (autojobs.get("data") or []) if autojobs.get("ok") else []
        notes = ((recent.get("data") or {}).get("notes") or []) if recent.get("ok") else []
        lim = ((limits.get("data") or {}).get("summary") or {}) if limits.get("ok") else {}
        done_builds = [b for b in builds if b.get("status") == "done"]
        build_objectives = [str(b.get("objective") or b.get("title") or "")[:80] for b in builds[:8]]
        auto_status = {}
        for a in autos:
            k = a.get("status") or "?"
            auto_status[k] = auto_status.get(k, 0) + 1
        auto_objectives = [str(a.get("objective") or "")[:70] for a in autos[:5]]
        recent_facts = [(n.get("preview") or "")[:100] for n in notes[:14] if n.get("preview")]
        snapshot = (
            f"PROJECTS ({len(proj)}): {', '.join(str(p.get('name') or p.get('id') or '?') for p in proj[:12]) or 'none'}.\n"
            f"RECENT BUILDS ({len(done_builds)} done / {len(builds)} total): "
            f"{'; '.join(o for o in build_objectives if o) or 'none yet'}.\n"
            f"AUTOMATION LOOPS ({len(autos)}; by status: "
            f"{', '.join(f'{k} {v}' for k, v in sorted(auto_status.items(), key=lambda kv: -kv[1])) or 'none'}): "
            f"{'; '.join(o for o in auto_objectives if o) or 'no objectives'}.\n"
            f"CAPACITY: {lim.get('globalPercentUsed', 0)}% of daily budget used, "
            f"{lim.get('localOrFreeCalls24h', 0)} free + {lim.get('subscriptionCalls24h', 0)} paid calls/24h.\n"
            f"RECENT MEMORY (what Will's been working on):\n" + "\n".join(f"  - {f}" for f in recent_facts)
        )
        from src.endpoint_resolver import resolve_endpoint
        from src.llm_core import llm_call_async
        url = model = None
        try:
            url, model, headers = resolve_endpoint("utility", free_only=True)
            if not url:
                url, model, headers = resolve_endpoint("default", free_only=True)
        except Exception:
            headers = {}
        if not url or not model:
            return {"ok": True, "snapshot": snapshot, "backlog": [], "note": "Configure a free model for the level-up backlog."}
        prompt = (
            "You are the level-up coach for a PERSONAL AI operating system ('Bert's AI'), owned by Will. "
            "Using the activity snapshot, reason through these five lenses and produce a RANKED backlog of the "
            "next 5 highest-leverage things to BUILD (a new skill, automation, connection, or capability):\n"
            "1. REPEATS: what has Will (or the OS) done 3+ times that should be a reusable skill/automation?\n"
            "2. MANUAL: what still feels manual or token-heavy that the OS could own end-to-end?\n"
            "3. INTERN-TEST: what could a smart intern reliably do that the OS isn't doing yet?\n"
            "4. 10X: what breaks or bottlenecks if Will's usage grew 10x?\n"
            "5. LEVER: the single biggest growth lever for Will's actual goals (coding velocity, automation, life-admin).\n"
            "Be concrete and grounded in the snapshot — name real projects/loops where you can. Rank by leverage (highest first). "
            "Return ONLY JSON: {\"headline\":\"one line: the single most important next move\", "
            "\"backlog\":[{\"title\":\"build X\",\"lens\":\"repeats|manual|intern|10x|lever\",\"why\":\"grounded reason <16 words\","
            "\"build\":\"the concrete first step\",\"effort\":\"S|M|L\",\"leverage\":\"high|medium|low\"}]}\n\n"
            f"SNAPSHOT:\n{snapshot}"
        )
        try:
            raw = await llm_call_async(url, model, [{"role": "user", "content": prompt}], headers=headers, max_tokens=1400)
        except Exception as e:
            logger.debug(f"level-up llm failed: {e}")
            return {"ok": True, "snapshot": snapshot, "backlog": [], "error": "ai_unavailable"}
        import json as _json
        import re as _re
        parsed = None
        try:
            mch = _re.search(r"\{.*\}", (raw or "").strip(), _re.DOTALL)
            if mch:
                parsed = _json.loads(mch.group(0))
        except Exception:
            parsed = None
        if not isinstance(parsed, dict):
            return {"ok": True, "snapshot": snapshot, "backlog": [], "headline": (raw or "").strip()[:200], "model": model}
        backlog = parsed.get("backlog") if isinstance(parsed.get("backlog"), list) else []
        return {"ok": True, "snapshot": snapshot,
                "headline": str(parsed.get("headline") or "")[:200],
                "backlog": backlog[:6], "model": model}

    @router.get("/memory-hot")
    async def brain_memory_hot():
        """Hot-cache: a compact, always-fresh digest of the most-recent decisions
        and context, served live from the memory vault. The research's hot.md —
        but as a live API read (never stale) instead of a static file, so any
        query can grab 'what's hot right now' cheaply without crawling the graph.
        Free model distils; deterministic fallback if no model. Fails soft."""
        recent = await _brain_get("/api/memory/recent?limit=30", 15.0)
        notes = ((recent.get("data") or {}).get("notes") or []) if recent.get("ok") else []
        previews = [(n.get("preview") or "").strip() for n in notes if n.get("preview")]
        previews = [p for p in previews if p][:24]
        # Deterministic fallback hot-cache: the most recent previews, char-capped.
        det_lines, total = [], 0
        for p in previews:
            line = "- " + p[:110]
            if total + len(line) > 650:
                break
            det_lines.append(line)
            total += len(line) + 1
        deterministic = "\n".join(det_lines)
        if not previews:
            return {"ok": True, "hot": "", "items": [], "note": "No recent memory to distil.", "model": None}
        from src.endpoint_resolver import resolve_endpoint
        from src.llm_core import llm_call_async
        url = model = None
        try:
            url, model, headers = resolve_endpoint("utility", free_only=True)
            if not url:
                url, model, headers = resolve_endpoint("default", free_only=True)
        except Exception:
            headers = {}
        if not url or not model:
            return {"ok": True, "hot": deterministic, "items": det_lines, "model": None, "mode": "deterministic"}
        prompt = (
            "You maintain the HOT-CACHE for a personal AI ('Bert's AI'): the ~5-7 most important things to know "
            "RIGHT NOW about what Will is working on and the latest decisions, so a query can skip crawling the full "
            "memory graph. From these recent memory notes, write a tight hot-cache: at most 7 bullets, each <14 words, "
            "newest/most-active first, no fluff, no preamble. Return ONLY JSON: "
            '{"hot":["bullet","bullet",...]}.\n\nRECENT NOTES:\n' + "\n".join(f"- {p[:120]}" for p in previews)
        )
        try:
            raw = await llm_call_async(url, model, [{"role": "user", "content": prompt}], headers=headers, max_tokens=600)
            import json as _json
            import re as _re
            mch = _re.search(r"\{.*\}", (raw or "").strip(), _re.DOTALL)
            if mch:
                p = _json.loads(mch.group(0))
                if isinstance(p, dict) and isinstance(p.get("hot"), list):
                    bullets = [str(b).strip() for b in p["hot"] if str(b).strip()][:7]
                    if bullets:
                        return {"ok": True, "hot": "\n".join("- " + b for b in bullets),
                                "items": bullets, "model": model, "mode": "ai"}
        except Exception as e:
            logger.debug(f"memory-hot llm failed: {e}")
        return {"ok": True, "hot": deterministic, "items": det_lines, "model": model, "mode": "deterministic"}

    @router.get("/daily-plan")
    async def brain_daily_plan(owner: str = Depends(require_user)):
        """Daily Plan — the Jarvis morning briefing. Weaves today's calendar,
        inbox, active todos, and what the brain shipped overnight into a short
        plan + 3 concrete focus actions. Read-only; free model; deterministic
        fallback so it always returns a usable plan even with no chat model."""
        from src.builtin_actions import gather_day_context
        ctx = await gather_day_context(owner or "")
        events = ctx.get("events") or []
        subjects = ctx.get("subjects") or []
        todos = ctx.get("todos") or []
        brain = ctx.get("brain") or {}
        unread = ctx.get("unread_count") or 0
        ev_str = "; ".join(
            f"{e['time']} {e['summary']}" + (f" @ {e['location']}" if e.get("location") else "")
            for e in events
        ) or "nothing scheduled"
        subj_str = "; ".join(f"{s['from']}: {s['subject']}" for s in subjects) or "none"
        todo_str = "; ".join(todos) or "none"
        brain_str = (
            f"{brain.get('builds', 0)} build(s) / {brain.get('commits', 0)} commit(s), "
            f"{brain.get('new_mems', 0)} new memories in 24h"
        )
        context = (
            f"DATE: {ctx.get('date_label', '')}\n"
            f"CALENDAR ({len(events)}): {ev_str}\n"
            f"INBOX: {unread} unread. Most recent: {subj_str}\n"
            f"ACTIVE TODOS: {todo_str}\n"
            f"OVERNIGHT (Will's AI worked while he slept): {brain_str}"
        )
        # Deterministic focus list (used as the fallback, and as a floor).
        det_focus = []
        if subjects:
            det_focus.append({"action": f"Triage your inbox — {unread:,} unread, newest from {subjects[0]['from']}", "kind": "reply"})
        elif unread:
            det_focus.append({"action": f"Clear {unread:,} unread emails", "kind": "reply"})
        if events:
            det_focus.append({"action": f"Prep for \"{events[0]['summary']}\" at {events[0]['time']}", "kind": "calendar"})
        if todos:
            det_focus.append({"action": todos[0], "kind": "todo"})
        det_focus = det_focus[:3]
        base = {
            "ok": True, "date": ctx.get("date_label", ""),
            "counts": {"events": len(events), "unread": unread, "todos": len(todos),
                       "builds": brain.get("builds", 0), "newMemories": brain.get("new_mems", 0)},
            "events": events[:6], "subjects": subjects[:5],
        }
        from src.endpoint_resolver import resolve_endpoint
        from src.llm_core import llm_call_async
        url = model = None
        try:
            url, model, headers = resolve_endpoint("utility", free_only=True)
            if not url:
                url, model, headers = resolve_endpoint("default", free_only=True)
        except Exception:
            headers = {}
        if not url or not model:
            return {**base, "greeting": "Here's your day, Will.", "focus": det_focus, "model": None, "mode": "deterministic"}
        prompt = (
            "You are Bert, Will's personal AI. Write his MORNING PLAN from the context below. "
            "Be warm but tight — Will is busy. Pick the 3 highest-leverage focus actions for TODAY, grounded in "
            "the real calendar/inbox/todos (don't invent). Each focus action gets a kind: "
            "reply (email), calendar (an event), build (ship code via his AI), todo, or personal. "
            "Return ONLY JSON: {\"greeting\":\"one warm line naming what matters most today\", "
            "\"focus\":[{\"action\":\"concrete thing to do <14 words\",\"kind\":\"reply|calendar|build|todo|personal\","
            "\"why\":\"short\"}], \"note\":\"optional one-line nudge or 'looks like a calm day'\"}\n\n"
            f"CONTEXT:\n{context}"
        )
        try:
            raw = await llm_call_async(url, model, [{"role": "user", "content": prompt}], headers=headers, max_tokens=700)
            import json as _json
            import re as _re
            mch = _re.search(r"\{.*\}", (raw or "").strip(), _re.DOTALL)
            if mch:
                p = _json.loads(mch.group(0))
                if isinstance(p, dict):
                    focus = p.get("focus") if isinstance(p.get("focus"), list) else []
                    focus = [f for f in focus if isinstance(f, dict) and f.get("action")][:3]
                    return {**base, "greeting": str(p.get("greeting") or "Here's your day, Will.")[:160],
                            "focus": focus or det_focus, "note": str(p.get("note") or "")[:140],
                            "model": model, "mode": "ai"}
        except Exception as e:
            logger.debug(f"daily-plan llm failed: {e}")
        return {**base, "greeting": "Here's your day, Will.", "focus": det_focus, "model": model, "mode": "deterministic"}

    @router.post("/daily-plan/push")
    async def brain_daily_plan_push(owner: str = Depends(require_user)):
        """Send today's plan to the user's phone (ntfy / configured channel).
        User-initiated self-notification — the click is the approval, same
        channel as the 7am brief. Composes a concise text deterministically so
        it's fast and works without a chat model. Never messages anyone else."""
        from src.builtin_actions import gather_day_context
        ctx = await gather_day_context(owner or "")
        events = ctx.get("events") or []
        subjects = ctx.get("subjects") or []
        todos = ctx.get("todos") or []
        brain = ctx.get("brain") or {}
        unread = ctx.get("unread_count") or 0
        lines = [f"☀️ Your day — {ctx.get('date_label', 'today')}", ""]
        if events:
            lines.append("📅 Calendar:")
            for e in events[:6]:
                loc = f" @ {e['location']}" if e.get("location") else ""
                lines.append(f"  {e['time']}  {e['summary']}{loc}")
        else:
            lines.append("📅 No events today.")
        lines.append("")
        lines.append(f"✉ {unread:,} unread"
                     + (f" — newest from {subjects[0]['from']}" if subjects else ""))
        if brain.get("builds") or brain.get("new_mems"):
            lines.append(f"🔨 {brain.get('builds', 0)} built overnight · 🧠 {brain.get('new_mems', 0)} new memories")
        if todos:
            lines.append("")
            lines.append("✓ Todos: " + "; ".join(todos[:4]))
        body = "\n".join(lines)
        try:
            from routes.note_routes import dispatch_reminder
            res = await dispatch_reminder(
                title="Bert · Your day",
                note_body=body,
                note_id="daily-plan-manual",
                owner=owner or "",
                free_only=True,
            )
            return {"ok": True, "dispatched": res, "preview": body[:280]}
        except Exception as e:
            return {"ok": False, "error": str(e)[:200]}

    async def _weekly_rollup():
        """Deterministic 7-day rollup of the OS's week (no LLM). Shared by
        GET /weekly-review and POST /weekly-review/push so both report the
        exact same numbers. Fails soft to zeros when the brain is down."""
        from datetime import datetime, timedelta
        providers, limits, deepjobs, autojobs, recent, projects = await asyncio.gather(
            _brain_get("/api/providers", 10.0),
            _brain_get("/api/usage/limits", 10.0),
            _brain_get("/api/deep/jobs?limit=50", 15.0),
            _brain_get("/api/auto/jobs?limit=40", 15.0),
            _brain_get("/api/memory/recent?limit=100", 15.0),
            _brain_get("/api/projects", 10.0),
        )
        since = datetime.now() - timedelta(days=7)

        def _in_window(ts):
            # Brain timestamps are UTC ISO with Z — convert to local naive,
            # exactly like _recent_ts in gather_day_context.
            try:
                return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).astimezone().replace(tzinfo=None) >= since
            except Exception:
                return False

        engines = ((providers.get("data") or {}).get("providers") or []) if providers.get("ok") else []
        engines_online = sum(1 for e in engines if e.get("online"))
        lim = ((limits.get("data") or {}).get("summary") or {}) if limits.get("ok") else {}
        try:
            budget_used = round(float(lim.get("globalPercentUsed") or 0), 1)
        except Exception:
            budget_used = 0
        runs = (deepjobs.get("data") or []) if deepjobs.get("ok") else []
        builds_done = builds_failed = commits = 0
        per_project: dict = {}
        for r in runs:
            if not isinstance(r, dict) or not _in_window(r.get("finishedAt") or r.get("startedAt")):
                continue
            status = r.get("status")
            if status == "done":
                builds_done += 1
                try:
                    commits += int(r.get("committed") or 0)
                except Exception:
                    pass
            elif status in ("failed", "error"):
                builds_failed += 1
            pid = r.get("projectId")
            if pid:
                per_project[pid] = per_project.get(pid, 0) + 1
        notes = (((recent.get("data") or {}).get("notes")) or []) if recent.get("ok") else []
        new_mems = sum(1 for n in notes if isinstance(n, dict) and _in_window(n.get("ts")))
        autos = (autojobs.get("data") or []) if autojobs.get("ok") else []
        auto_ok = auto_failed = 0
        for j in autos:
            if not isinstance(j, dict) or not _in_window(j.get("updatedAt")):
                continue
            try:
                auto_ok += int(j.get("completedRuns") or 0)
                auto_failed += int(j.get("failedRuns") or 0)
            except Exception:
                continue
        proj = (projects.get("data") or []) if projects.get("ok") else []
        names = {p.get("id"): (p.get("name") or p.get("id")) for p in proj if isinstance(p, dict)}
        top_projects = [
            {"name": str(names.get(pid, pid))[:60], "builds": n}
            for pid, n in sorted(per_project.items(), key=lambda kv: -kv[1])[:3]
        ]
        rollup = {
            "buildsDone": builds_done, "buildsFailed": builds_failed, "commits": commits,
            "newMemories": new_mems, "autoRunsOk": auto_ok, "autoRunsFailed": auto_failed,
            "enginesOnline": engines_online, "budgetUsedPct": budget_used,
        }
        det_headline = (
            f"This week: {builds_done} build{'s' if builds_done != 1 else ''} done"
            f" ({builds_failed} failed), {commits} commit{'s' if commits != 1 else ''}, "
            f"{new_mems} new memories, {auto_ok} auto runs ok ({auto_failed} failed), "
            f"{engines_online} engines online."
        )
        return rollup, top_projects, det_headline

    @router.get("/weekly-review")
    async def brain_weekly_review():
        """Weekly Review — the state-of-the-OS week (research P1.4). A
        deterministic 7-day rollup (builds, commits, memories, automation runs,
        engines, budget) plus a free-model pass for the headline / wins /
        next-week priorities. Read-only; fails soft to the deterministic
        rollup so it always returns a usable review."""
        rollup, top_projects, det_headline = await _weekly_rollup()
        base = {"ok": True, "window": "7d", "rollup": rollup, "topProjects": top_projects}
        from src.endpoint_resolver import resolve_endpoint
        from src.llm_core import llm_call_async
        url = model = None
        try:
            url, model, headers = resolve_endpoint("utility", free_only=True)
            if not url:
                url, model, headers = resolve_endpoint("default", free_only=True)
        except Exception:
            headers = {}
        if not url or not model:
            return {**base, "headline": det_headline, "wins": [], "priorities": [], "model": None, "mode": "deterministic"}
        top_str = ", ".join(f"{t['name']} ({t['builds']} builds)" for t in top_projects) or "none"
        prompt = (
            "You write the WEEKLY REVIEW for a personal AI operating system ('Bert's AI') owned by Will. "
            "From the 7-day rollup below, write a one-line state of the week, up to 3 wins, and up to 3 "
            "priorities for next week. Ground everything in the numbers — don't invent specifics. "
            "Return ONLY JSON: {\"headline\":\"one line state of the week\",\"wins\":[\"...\"],\"priorities\":[\"...\"]}\n\n"
            f"ROLLUP (last 7 days): builds done={rollup['buildsDone']}, builds failed={rollup['buildsFailed']}, "
            f"commits={rollup['commits']}, new memories={rollup['newMemories']}, "
            f"automation runs ok={rollup['autoRunsOk']}, automation runs failed={rollup['autoRunsFailed']}, "
            f"engines online={rollup['enginesOnline']}, daily budget used={rollup['budgetUsedPct']}%.\n"
            f"TOP PROJECTS BY BUILDS: {top_str}"
        )
        try:
            raw = await llm_call_async(url, model, [{"role": "user", "content": prompt}], headers=headers, max_tokens=900)
            import json as _json
            import re as _re
            mch = _re.search(r"\{.*\}", (raw or "").strip(), _re.DOTALL)
            if mch:
                p = _json.loads(mch.group(0))
                if isinstance(p, dict):
                    wins = p.get("wins") if isinstance(p.get("wins"), list) else []
                    wins = [str(w)[:160] for w in wins if w][:3]
                    prios = p.get("priorities") if isinstance(p.get("priorities"), list) else []
                    prios = [str(w)[:160] for w in prios if w][:3]
                    return {**base, "headline": str(p.get("headline") or det_headline)[:200],
                            "wins": wins, "priorities": prios, "model": model, "mode": "ai"}
        except Exception as e:
            logger.debug(f"weekly-review llm failed: {e}")
        return {**base, "headline": det_headline, "wins": [], "priorities": [], "model": model, "mode": "deterministic"}

    @router.post("/weekly-review/push")
    async def brain_weekly_review_push(owner: str = Depends(require_user)):
        """Send the weekly review to the user's phone (ntfy / configured
        channel). User-initiated self-notification — the click is the approval,
        same channel as the daily plan. Composes the text deterministically
        from the SAME rollup as GET /weekly-review (no LLM) so it's fast and
        works without a chat model. Never messages anyone else."""
        rollup, top_projects, _det = await _weekly_rollup()
        lines = ["📈 Your week — Bert's AI", ""]
        lines.append(f"🔨 Builds: {rollup['buildsDone']} done · {rollup['buildsFailed']} failed · {rollup['commits']} commits")
        lines.append(f"🧠 New memories: {rollup['newMemories']}")
        lines.append(f"🤖 Auto runs: {rollup['autoRunsOk']} ok · {rollup['autoRunsFailed']} failed")
        lines.append(f"⚙ Engines online: {rollup['enginesOnline']} · {rollup['budgetUsedPct']}% daily budget used")
        if top_projects:
            lines.append("")
            lines.append("Top projects: " + "; ".join(f"{t['name']} ({t['builds']})" for t in top_projects))
        body = "\n".join(lines)
        try:
            from routes.note_routes import dispatch_reminder
            res = await dispatch_reminder(
                title="Bert · Weekly review",
                note_body=body,
                note_id="weekly-review-manual",
                owner=owner or "",
                free_only=True,
            )
            return {"ok": True, "dispatched": res, "preview": body[:280]}
        except Exception as e:
            return {"ok": False, "error": str(e)[:200]}

    @router.get("/checkup")
    async def brain_checkup(owner: str = Depends(require_user)):
        """Connector Check-in — every connector's health at a glance (research
        P1.4). Fully deterministic and fast (NO LLM): brain, engines, limits,
        memory, email (IMAP), calendar (CalDAV), push channel. Owner-scoped
        because it probes the caller's IMAP/CalDAV/push config. Presence-only
        checks — reports configured true/false, never secret values. Every
        check is individually try/excepted so one dead connector never hides
        the rest; always returns ok:True."""
        checks: list = []

        def _add(name: str, status: str, detail: str):
            checks.append({"name": name, "status": status, "detail": str(detail)[:140]})

        # Brain-side reads in parallel (each _brain_get already fails soft).
        try:
            health, provs, limits, recent = await asyncio.gather(
                _brain_get("/api/health", 6.0),
                _brain_get("/api/providers", 10.0),
                _brain_get("/api/usage/limits", 10.0),
                _brain_get("/api/memory/recent?limit=1", 10.0),
            )
        except Exception as e:  # belt-and-braces: gather itself should never raise
            health = provs = limits = recent = {"ok": False, "error": str(e)[:140]}

        # 1. brain — is the brain host reachable at all?
        try:
            if health.get("ok"):
                _add("brain", "ok", "brain host reachable")
            else:
                _add("brain", "down", health.get("error") or "brain unreachable")
        except Exception as e:
            _add("brain", "down", str(e) or e.__class__.__name__)

        # 2. engines — how many providers are online?
        try:
            if provs.get("ok"):
                engines = ((provs.get("data") or {}).get("providers") or [])
                online = sum(1 for p in engines if isinstance(p, dict) and p.get("online"))
                if online > 0:
                    _add("engines", "ok", f"{online}/{len(engines)} engines online")
                else:
                    _add("engines", "warn", f"0/{len(engines)} engines online")
            else:
                _add("engines", "down", provs.get("error") or "providers unavailable")
        except Exception as e:
            _add("engines", "down", str(e) or e.__class__.__name__)

        # 3. limits — daily budget state + percent used.
        try:
            if limits.get("ok"):
                lim = ((limits.get("data") or {}).get("summary") or {})
                state = str(lim.get("globalState") or "?").lower()
                try:
                    pct = round(float(lim.get("globalPercentUsed") or 0), 1)
                except Exception:
                    pct = 0
                if state in ("ok", "normal"):
                    _add("limits", "ok", f"{pct}% of daily budget used")
                else:
                    _add("limits", "warn", f"state={state} · {pct}% of daily budget used")
            else:
                _add("limits", "down", limits.get("error") or "limits unavailable")
        except Exception as e:
            _add("limits", "down", str(e) or e.__class__.__name__)

        # 4. memory — can the vault be read?
        try:
            if recent.get("ok"):
                notes = ((recent.get("data") or {}).get("notes") or [])
                if notes:
                    _add("memory", "ok", "vault readable")
                else:
                    _add("memory", "warn", "vault reachable but empty")
            else:
                _add("memory", "down", recent.get("error") or "memory unavailable")
        except Exception as e:
            _add("memory", "down", str(e) or e.__class__.__name__)

        # 5. email — live IMAP probe (read-only SELECT) for the caller's account.
        try:
            def _probe_imap():
                from routes.email_helpers import _imap_connect
                conn = _imap_connect(None, owner=owner)
                try:
                    conn.select("INBOX", readonly=True)
                finally:
                    try:
                        conn.logout()
                    except Exception:
                        pass
            await asyncio.wait_for(asyncio.to_thread(_probe_imap), timeout=10.0)
            _add("email", "ok", "IMAP inbox reachable")
        except asyncio.TimeoutError:
            _add("email", "down", "IMAP timed out (10s)")
        except Exception as e:
            _add("email", "down", (str(e) or e.__class__.__name__)[:120])

        # 6. calendar — are any CalDAV accounts configured for this owner?
        try:
            from src.caldav_sync import _load_caldav_accounts
            accounts = _load_caldav_accounts(owner or "")
            n = len(accounts or [])
            if n:
                _add("calendar", "ok", f"{n} CalDAV account{'s' if n != 1 else ''} configured")
            else:
                _add("calendar", "warn", "no accounts configured")
        except Exception as e:
            _add("calendar", "down", (str(e) or e.__class__.__name__)[:120])

        # 7. push — PRESENCE-ONLY check of the configured reminder channel.
        # Mirrors note_routes.dispatch_reminder's per-channel config keys;
        # never reads or reports secret values, only configured true/false.
        try:
            from src.settings import load_settings
            settings = load_settings()
            channel = (settings.get("reminder_channel") or "browser").strip() or "browser"
            configured = False
            if channel == "browser":
                configured = True  # browser pushes are wired client-side
            elif channel == "ntfy":
                from src.integrations import load_integrations
                configured = any(
                    i.get("preset") == "ntfy" and i.get("enabled", True) and i.get("base_url")
                    for i in load_integrations()
                )
            elif channel == "webhook":
                from src.integrations import load_integrations
                intg_id = (settings.get("reminder_webhook_integration_id") or "").strip()
                configured = bool(intg_id) and any(
                    i.get("id") == intg_id and i.get("base_url")
                    for i in load_integrations()
                )
            elif channel == "email":
                from routes.email_helpers import _get_email_config
                acc_id = (settings.get("reminder_email_account_id") or "").strip() or None
                cfg = _get_email_config(account_id=acc_id, owner=owner or "")
                configured = bool(cfg.get("smtp_host") and cfg.get("smtp_user") and cfg.get("smtp_password"))
            if configured:
                _add("push", "ok", f"channel '{channel}' configured")
            else:
                _add("push", "warn", f"channel '{channel}' not configured")
        except Exception as e:
            _add("push", "down", (str(e) or e.__class__.__name__)[:120])

        summary = {"ok": 0, "warn": 0, "down": 0}
        for c in checks:
            summary[c["status"]] = summary.get(c["status"], 0) + 1
        return {"ok": True, "checks": checks, "summary": summary}

    @router.get("/deploy-prep")
    async def brain_deploy_prep():
        """Deploy pre-flight — pre-ship checklist (research P1.4). Fully
        deterministic local checks (NO LLM): git working tree, unpushed
        commits, python/js syntax gates, brain reachability, env sanity.
        Read-only with zero side-effects, so no auth dep needed. Fails soft
        everywhere: the production container has no .git directory (and may
        lack node), so those checks degrade to status "skip" instead of
        raising — this endpoint must never 500. Presence-only env check:
        never reads or prints secret values."""
        import ast as _ast
        import shutil
        import subprocess
        from pathlib import Path

        checks: list = []

        def _add(name: str, status: str, detail: str):
            checks.append({"name": name, "status": status, "detail": str(detail)[:140]})

        root = Path(__file__).resolve().parents[1]
        no_git = "not a git checkout (deployed container)"

        def _git(*args):
            """Run git at the repo root; None on any failure (fail soft)."""
            try:
                return subprocess.run(
                    ["git", *args],
                    cwd=str(root), capture_output=True, text=True, timeout=10,
                )
            except Exception:
                return None

        def _local_checks():
            try:
                git_repo = (root / ".git").exists()
            except Exception:
                git_repo = False

            # 1. working-tree — any uncommitted changes?
            try:
                r = _git("status", "--porcelain") if git_repo else None
                if r is None or r.returncode != 0:
                    _add("working-tree", "skip", no_git)
                else:
                    dirty = [ln for ln in r.stdout.splitlines() if ln.strip()]
                    if dirty:
                        _add("working-tree", "warn", f"{len(dirty)} uncommitted file{'s' if len(dirty) != 1 else ''}")
                    else:
                        _add("working-tree", "ok", "clean")
            except Exception as e:
                _add("working-tree", "skip", (str(e) or e.__class__.__name__)[:120])

            # 2. unpushed — commits not on the deploy remote yet?
            try:
                r = _git("rev-list", "--count", "mine/bertos..HEAD") if git_repo else None
                if r is None:
                    _add("unpushed", "skip", no_git)
                elif r.returncode != 0:
                    _add("unpushed", "skip", "remote ref mine/bertos unknown")
                else:
                    try:
                        n = int((r.stdout or "").strip() or "0")
                    except Exception:
                        n = 0
                    if n:
                        _add("unpushed", "warn", f"{n} unpushed commit{'s' if n != 1 else ''}")
                    else:
                        _add("unpushed", "ok", "in sync")
            except Exception as e:
                _add("unpushed", "skip", (str(e) or e.__class__.__name__)[:120])

            # 3. python-syntax — compile the load-bearing modules.
            try:
                bad = []
                for rel in ("routes/brain_routes.py", "src/builtin_actions.py", "src/endpoint_resolver.py"):
                    try:
                        _ast.parse((root / rel).read_text(encoding="utf-8", errors="replace"))
                    except SyntaxError as se:
                        bad.append(f"{rel}:{se.lineno or '?'}")
                    except Exception:
                        bad.append(f"{rel} (unreadable)")
                if bad:
                    _add("python-syntax", "warn", "; ".join(bad))
                else:
                    _add("python-syntax", "ok", "3 modules compile")
            except Exception as e:
                _add("python-syntax", "warn", (str(e) or e.__class__.__name__)[:120])

            # 4. js-syntax — node --check the front-end entry files.
            try:
                if not shutil.which("node"):
                    _add("js-syntax", "skip", "node not available (container)")
                else:
                    bad = []
                    for rel in ("static/js/homeDeck.js", "static/js/brainCockpit.js", "static/js/bertOrb.js"):
                        try:
                            r = subprocess.run(
                                ["node", "--check", str(root / rel)],
                                cwd=str(root), capture_output=True, text=True, timeout=10,
                            )
                            if r.returncode != 0:
                                bad.append(rel)
                        except Exception:
                            bad.append(f"{rel} (check failed)")
                    if bad:
                        _add("js-syntax", "warn", "; ".join(bad))
                    else:
                        _add("js-syntax", "ok", "3 files pass node --check")
            except Exception as e:
                _add("js-syntax", "skip", (str(e) or e.__class__.__name__)[:120])

        try:
            await asyncio.to_thread(_local_checks)
        except Exception as e:  # belt-and-braces: local checks must never 500
            _add("local-checks", "skip", (str(e) or e.__class__.__name__)[:120])

        # 5. brain-reachable — a deploy with the brain down still works (the
        # body degrades gracefully), so an unreachable brain is warn, not fatal.
        try:
            health = await _brain_get("/api/health", 6.0)
            if health.get("ok"):
                _add("brain-reachable", "ok", "brain host reachable")
            else:
                _add("brain-reachable", "warn", health.get("error") or "brain unreachable — body degrades gracefully")
        except Exception as e:
            _add("brain-reachable", "warn", (str(e) or e.__class__.__name__)[:120])

        # 6. env-sanity — PRESENCE-only: is the brain base URL pinned? That var
        # holds only a host:port (no credentials), so echoing the host is safe.
        try:
            raw = (os.environ.get("BERTOS_BRAIN_BASE_URL") or "").strip()
            if raw:
                try:
                    from urllib.parse import urlsplit
                    host = urlsplit(raw).netloc or raw
                except Exception:
                    host = raw
                _add("env-sanity", "ok", f"BERTOS_BRAIN_BASE_URL set ({host[:60]})")
            else:
                _add("env-sanity", "warn", "BERTOS_BRAIN_BASE_URL unset — using default")
        except Exception as e:
            _add("env-sanity", "warn", (str(e) or e.__class__.__name__)[:120])

        summary = {"ok": 0, "warn": 0, "skip": 0}
        for c in checks:
            summary[c["status"]] = summary.get(c["status"], 0) + 1
        return {"ok": True, "checks": checks, "ready": summary.get("warn", 0) == 0, "summary": summary}

    @router.get("/memory-search")
    async def brain_memory_search(q: str = "", owner: str = Depends(require_user)):
        """Search Bert's durable memory from the HomeDeck. The brain exposes no
        server-side text search (the graph route's ?q= is ignored — verified
        live), so we pull the most recent notes and filter their previews here:
        case-insensitive, every word of the query must appear. Deterministic,
        read-only, and fails soft to zero hits when the brain is down."""
        q = (q or "").strip()
        if not q:
            return {"ok": True, "q": "", "hits": [], "total": 0}
        r = await _brain_get("/api/memory/recent?limit=200", 15.0)
        if not r.get("ok"):
            return {"ok": True, "q": q, "hits": [], "total": 0,
                    "note": r.get("error") or "brain offline"}
        notes = (r.get("data") or {}).get("notes") or []
        words = [w for w in q.lower().split() if w]
        hits = []
        for n in notes:
            if not isinstance(n, dict):
                continue
            preview = str(n.get("preview") or "")
            hay = preview.lower()
            if not words or not all(w in hay for w in words):
                continue
            hits.append({
                "preview": preview[:220],
                "ts": n.get("ts"),
                "kind": n.get("kind"),
                "source": n.get("source"),
            })
        return {"ok": True, "q": q, "hits": hits[:12], "total": len(hits)}

    @router.get("/day-wrap")
    async def brain_day_wrap(owner: str = Depends(require_user)):
        """Evening Wrap — the bookend to the morning Daily Plan. What shipped
        today (builds / commits / new memories), where the inbox stands, and
        the top todos waiting for tomorrow. Deterministic (no LLM), read-only,
        and fails soft to zeros so the deck always gets a usable wrap."""
        from src.builtin_actions import gather_day_context
        try:
            ctx = await gather_day_context(owner or "")
        except Exception:  # belt-and-braces: the wrap must never 500
            ctx = {}
        brain = ctx.get("brain") or {}
        todos = ctx.get("todos") or []
        unread = ctx.get("unread_count") or 0
        builds = brain.get("builds", 0)
        greeting = (
            f"Day's done, Will — {builds} build{'s' if builds != 1 else ''} shipped. Here's the wrap."
            if builds else "Day's done, Will — here's the wrap."
        )
        return {
            "ok": True,
            "date": ctx.get("date_label", ""),
            "shipped": {"builds": builds, "commits": brain.get("commits", 0),
                        "newMemories": brain.get("new_mems", 0)},
            "unread": unread,
            "tomorrowTodos": todos[:3],
            "greeting": greeting,
        }

    @router.post("/day-wrap/push")
    async def brain_day_wrap_push(owner: str = Depends(require_user)):
        """Send the evening wrap to the user's phone (ntfy / configured channel).
        User-initiated self-notification — the click is the approval, same
        channel as the 7am brief. Composed deterministically (no LLM) so it's
        fast and always works. Never messages anyone else."""
        from src.builtin_actions import gather_day_context
        ctx = await gather_day_context(owner or "")
        brain = ctx.get("brain") or {}
        todos = ctx.get("todos") or []
        unread = ctx.get("unread_count") or 0
        lines = [f"🌙 Day wrap — {ctx.get('date_label', 'today')}", ""]
        if brain.get("builds") or brain.get("commits") or brain.get("new_mems"):
            lines.append(
                f"🔨 Shipped: {brain.get('builds', 0)} build(s), {brain.get('commits', 0)} commit(s)"
                f" · 🧠 {brain.get('new_mems', 0)} new memories"
            )
        else:
            lines.append("🔨 Nothing shipped today — fresh start tomorrow.")
        lines.append(f"✉ {unread:,} unread")
        if todos:
            lines.append("")
            lines.append("→ Tomorrow: " + "; ".join(todos[:3]))
        body = "\n".join(lines)
        try:
            from routes.note_routes import dispatch_reminder
            res = await dispatch_reminder(
                title="Bert · Day wrap",
                note_body=body,
                note_id="day-wrap-manual",
                owner=owner or "",
                free_only=True,
            )
            return {"ok": True, "dispatched": res, "preview": body[:280]}
        except Exception as e:
            return {"ok": False, "error": str(e)[:200]}

    @router.post("/notify-test")
    async def brain_notify_test(owner: str = Depends(require_user)):
        """Send a test push to the configured channel (ntfy → phone) so the user
        can confirm proactive texts reach them. Auth-gated + owner-scoped so it
        routes via the caller's configured channel (not a shared/default one)."""
        try:
            from routes.note_routes import dispatch_reminder
            res = await dispatch_reminder(
                title="Bert's AI",
                note_body="🔔 Test ping — proactive texts are working.",
                note_id="notify-test",
                owner=owner or "",
            )
            return {"ok": True, "dispatched": res}
        except Exception as e:
            return {"ok": False, "error": str(e)[:200]}

    return router
