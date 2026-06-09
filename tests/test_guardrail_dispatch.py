"""Integration proof for the free-first cost guardrail's fail-CLOSED dispatch guard.

Every LLM dispatch in the app funnels through `llm_call` / `llm_call_async`
(the leak-hunt confirmed all background/scheduled paths terminate there), so
proving the guard blocks at this choke point proves no path can spend on a
KNOWN paid host on autopilot. We assert the network is NEVER touched for a
blocked call — trusting the dispatched call, not a log line.
"""

import httpx
import pytest
from fastapi import HTTPException

import src.llm_core as llm_core
from src.endpoint_resolver import _host_paid_blocked

PAID_URL = "https://api.openai.com/v1/chat/completions"
LOCAL_URL = "http://localhost:8000/v1/chat/completions"   # vLLM-style, known-free
MSGS = [{"role": "user", "content": "hi"}]


class _FakeResp:
    is_success = True
    status_code = 200
    text = ""

    def __init__(self, content):
        self._content = content

    def json(self):
        return {"choices": [{"message": {"content": self._content}}]}


# --- the shared guard logic both sync+async guards call --------------------

def test_host_paid_blocked_logic(monkeypatch):
    monkeypatch.delenv("BERTOS_ALLOW_PAID", raising=False)   # default OFF
    assert _host_paid_blocked(PAID_URL) is True               # known paid + switch off
    assert _host_paid_blocked(LOCAL_URL) is False             # local never blocked
    assert _host_paid_blocked(PAID_URL, free_only=True) is True  # unattended: always
    monkeypatch.setenv("BERTOS_ALLOW_PAID", "1")
    assert _host_paid_blocked(PAID_URL) is False              # opted in (attended)
    assert _host_paid_blocked(PAID_URL, free_only=True) is True  # unattended still blocked


# --- the real dispatch path: blocked paid never touches the network --------

def test_sync_dispatch_blocks_paid_without_network(monkeypatch):
    monkeypatch.delenv("BERTOS_ALLOW_PAID", raising=False)

    def _boom(*a, **k):
        raise AssertionError("httpx.post MUST NOT be called for a blocked paid host")

    monkeypatch.setattr(httpx, "post", _boom)

    with pytest.raises(HTTPException) as ei:
        llm_core.llm_call(PAID_URL, "gpt-4", MSGS)
    assert ei.value.status_code == 402          # blocked by the guardrail


def test_sync_dispatch_allows_paid_when_opted_in(monkeypatch):
    monkeypatch.setenv("BERTOS_ALLOW_PAID", "1")
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _FakeResp("opted-in-ok"))

    out = llm_core.llm_call(PAID_URL, "gpt-4", MSGS)
    assert out == "opted-in-ok"                 # reached the network → guard allowed it


def test_sync_dispatch_allows_local_even_when_switch_off(monkeypatch):
    monkeypatch.delenv("BERTOS_ALLOW_PAID", raising=False)
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _FakeResp("local-ok"))

    out = llm_core.llm_call(LOCAL_URL, "qwen", MSGS)
    assert out == "local-ok"                    # local host is never blocked
