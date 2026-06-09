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
from fastapi import APIRouter, Body

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
    async def brain_build(body: dict = Body(default_factory=dict)):
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
                    owner="",
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

    @router.post("/notify-test")
    async def brain_notify_test():
        """Send a test push to the configured channel (ntfy → phone) so the user
        can confirm proactive texts reach them."""
        try:
            from routes.note_routes import dispatch_reminder
            res = await dispatch_reminder(
                title="Bert's AI",
                note_body="🔔 Test ping — proactive texts are working.",
                note_id="notify-test",
                owner="",
            )
            return {"ok": True, "dispatched": res}
        except Exception as e:
            return {"ok": False, "error": str(e)[:200]}

    return router
