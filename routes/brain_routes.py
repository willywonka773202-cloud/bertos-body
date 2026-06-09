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
            except Exception as e:  # background task — log, never raise
                logger.warning(f"brain build background run failed: {e}")

        asyncio.create_task(_run())
        return {"ok": True, "started": True, "objective": objective[:120]}

    return router
