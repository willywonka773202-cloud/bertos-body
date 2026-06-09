"""Guardrail tests for the free-first cost gate in resolve_endpoint().

Modeled on tests/test_resolve_endpoint_fallbacks.py — reuses the same
monkeypatched fake-resolver fixtures (no real DB, no provider call).

Proves:
  (a) free_only=True + paid endpoint  -> resolves the FREE default (no paid host)
  (b) kill-switch OFF + attended       -> also blocked (global circuit-breaker)
  (c) kill-switch ON  + attended       -> paid resolves (NO regression)
  (d) is_paid=None derivation          -> endpoint_kind 'api'+anthropic -> paid;
                                          'local' -> free
  (e) a paid entry is dropped from _resolve_fallback_candidates under free_only
"""

import json
from types import SimpleNamespace

import src.endpoint_resolver as endpoint_resolver
from src.endpoint_resolver import (
    resolve_endpoint,
    _endpoint_is_paid,
    _host_is_paid,
    _resolve_fallback_candidates,
)


class _FakeColumn:
    def __init__(self, name):
        self.name = name

    def __eq__(self, value):
        return ("eq", self.name, value)


class _FakeModelEndpoint:
    id = _FakeColumn("id")
    is_enabled = _FakeColumn("is_enabled")


class _FakeQuery:
    def __init__(self, rows):
        self.rows = list(rows)

    def filter(self, *conditions):
        for condition in conditions:
            if isinstance(condition, tuple) and condition[0] == "eq":
                _, field, value = condition
                self.rows = [row for row in self.rows if getattr(row, field) == value]
        return self

    def first(self):
        return self.rows[0] if self.rows else None


class _FakeDb:
    def __init__(self, rows):
        self.rows = rows

    def query(self, model):
        return _FakeQuery(self.rows)

    def close(self):
        pass


def _endpoint(ep_id, model, *, base_url=None, endpoint_kind="auto",
              is_paid=None, hidden=None):
    """A ModelEndpoint-shaped row. base_url defaults to a free localhost URL so
    a row is free unless explicitly given a paid host / is_paid=True."""
    return SimpleNamespace(
        id=ep_id,
        base_url=base_url or "http://localhost:11434",
        api_key=f"key-{ep_id}",
        cached_models=json.dumps([model]),
        hidden_models=json.dumps(hidden or []),
        is_enabled=True,
        endpoint_kind=endpoint_kind,
        is_paid=is_paid,
    )


def _install_resolver_fakes(monkeypatch, settings, endpoints):
    import src.settings as settings_mod

    monkeypatch.setattr(settings_mod, "load_settings", lambda: settings)
    monkeypatch.setattr(
        settings_mod,
        "get_user_setting",
        lambda key, owner="", default=None: settings.get(key, default),
    )
    monkeypatch.setattr(endpoint_resolver, "ModelEndpoint", _FakeModelEndpoint)
    monkeypatch.setattr(endpoint_resolver, "SessionLocal", lambda: _FakeDb(endpoints))
    monkeypatch.setattr(endpoint_resolver, "resolve_url", lambda url: url)


# --- (a) free_only=True + paid endpoint -> resolves the FREE default ---------

def test_free_only_blocks_paid_and_cascades_to_free_default(monkeypatch):
    monkeypatch.setenv("BERTOS_ALLOW_PAID", "0")

    paid = _endpoint(
        "openrouter", "gpt-4",
        base_url="https://openrouter.ai/api/v1",
        endpoint_kind="api", is_paid=True,
    )
    free = _endpoint(
        "local", "qwen",
        base_url="http://localhost:11434",
        endpoint_kind="local", is_paid=False,
    )
    settings = {
        "utility_endpoint_id": "openrouter", "utility_model": "gpt-4",
        "default_endpoint_id": "local", "default_model": "qwen",
    }
    _install_resolver_fakes(monkeypatch, settings, [paid, free])

    # Background caller: free_only=True. The paid utility tier is refused and the
    # resolver descends to the free default tier. localhost:11434 is detected as
    # native Ollama, so build_chat_url yields the /api/chat path.
    url, model, headers = resolve_endpoint("utility", free_only=True)

    assert "openrouter.ai" not in (url or "")
    assert url == "http://localhost:11434/api/chat"
    assert model == "qwen"


# --- (b) kill-switch OFF + attended -> also blocked -------------------------

def test_kill_switch_off_blocks_attended_paid(monkeypatch):
    monkeypatch.setenv("BERTOS_ALLOW_PAID", "0")

    paid = _endpoint(
        "openrouter", "gpt-4",
        base_url="https://openrouter.ai/api/v1",
        endpoint_kind="api", is_paid=True,
    )
    free = _endpoint(
        "local", "qwen",
        base_url="http://localhost:11434",
        endpoint_kind="local", is_paid=False,
    )
    settings = {
        "utility_endpoint_id": "openrouter", "utility_model": "gpt-4",
        "default_endpoint_id": "local", "default_model": "qwen",
    }
    _install_resolver_fakes(monkeypatch, settings, [paid, free])

    # Attended caller (free_only=False) — but the kill-switch is OFF, so paid is
    # still globally refused and we cascade to the free default.
    url, model, headers = resolve_endpoint("utility", free_only=False)

    assert "openrouter.ai" not in (url or "")
    assert model == "qwen"


# --- (c) kill-switch ON + attended -> paid resolves (NO regression) ----------

def test_kill_switch_on_allows_attended_paid(monkeypatch):
    monkeypatch.setenv("BERTOS_ALLOW_PAID", "1")

    paid = _endpoint(
        "openrouter", "gpt-4",
        base_url="https://openrouter.ai/api/v1",
        endpoint_kind="api", is_paid=True,
    )
    settings = {
        "default_endpoint_id": "openrouter", "default_model": "gpt-4",
    }
    _install_resolver_fakes(monkeypatch, settings, [paid])

    # Attended caller, switch ON -> the paid endpoint resolves normally.
    url, model, headers = resolve_endpoint("default", free_only=False)

    assert url == "https://openrouter.ai/api/v1/chat/completions"
    assert model == "gpt-4"
    assert headers.get("Authorization") == "Bearer key-openrouter"


def test_kill_switch_on_still_blocks_background_free_only(monkeypatch):
    """free_only=True must block paid even when the kill-switch is ON."""
    monkeypatch.setenv("BERTOS_ALLOW_PAID", "1")

    paid = _endpoint(
        "openrouter", "gpt-4",
        base_url="https://openrouter.ai/api/v1",
        endpoint_kind="api", is_paid=True,
    )
    free = _endpoint(
        "local", "qwen",
        base_url="http://localhost:11434",
        endpoint_kind="local", is_paid=False,
    )
    settings = {
        "utility_endpoint_id": "openrouter", "utility_model": "gpt-4",
        "default_endpoint_id": "local", "default_model": "qwen",
    }
    _install_resolver_fakes(monkeypatch, settings, [paid, free])

    url, model, headers = resolve_endpoint("utility", free_only=True)

    assert "openrouter.ai" not in (url or "")
    assert model == "qwen"


# --- (d) is_paid=None derivation --------------------------------------------

def test_is_paid_none_derives_paid_from_api_kind_and_host(monkeypatch):
    monkeypatch.setenv("BERTOS_ALLOW_PAID", "0")

    ep = _endpoint(
        "anthropic", "claude-3",
        base_url="https://api.anthropic.com",
        endpoint_kind="api", is_paid=None,
    )
    assert _endpoint_is_paid(ep) is True


def test_is_paid_none_derives_free_from_local_kind(monkeypatch):
    ep = _endpoint(
        "tailnet", "qwen",
        base_url="http://100.64.0.1:11434",   # private host, but kind=local
        endpoint_kind="local", is_paid=None,
    )
    assert _endpoint_is_paid(ep) is False


def test_explicit_is_paid_false_overrides_paid_host(monkeypatch):
    """An admin override (is_paid=False) wins over host derivation."""
    ep = _endpoint(
        "proxy", "gpt-4",
        base_url="https://openrouter.ai/api/v1",
        endpoint_kind="api", is_paid=False,
    )
    assert _endpoint_is_paid(ep) is False


def test_unknown_host_api_kind_fails_closed_to_paid(monkeypatch):
    """Fail-CLOSED for a DECLARED cloud API: an unknown host with
    endpoint_kind='api' (the owner called it an API) is treated as paid."""
    ep = _endpoint(
        "mystery", "model-x",
        base_url="https://some-unknown-llm.example/v1",
        endpoint_kind="api", is_paid=None,
    )
    assert _endpoint_is_paid(ep) is True


def test_unknown_host_self_hosted_kind_is_free(monkeypatch):
    """Self-hosted-friendly: an unknown host with kind 'auto'/unset (a private
    OpenAI-compatible server on a LAN/Tailscale/custom host) is NOT auto-paid,
    so it stays usable. Mark is_paid=True to force blocking."""
    ep = _endpoint(
        "selfhosted", "model-x",
        base_url="https://vllm.my-tailnet.ts.net/v1",
        endpoint_kind="auto", is_paid=None,
    )
    assert _endpoint_is_paid(ep) is False


def test_host_is_paid_classification():
    assert _host_is_paid("https://api.anthropic.com") is True
    assert _host_is_paid("https://openrouter.ai/api/v1") is True
    assert _host_is_paid("https://ollama.com/api") is True       # cloud, paid
    assert _host_is_paid("http://localhost:11434") is False
    assert _host_is_paid("http://127.0.0.1:11434") is False
    assert _host_is_paid("https://vllm.my-tailnet.ts.net/v1") is False  # unknown/self-hosted -> not a KNOWN paid host
    assert _host_is_paid("") is False                            # no url -> not a known paid host


def test_host_is_paid_scheme_less_url_classified_paid():
    """H2 regression: a scheme-less base URL must NOT fail open.

    urlparse('api.openai.com/v1').hostname is None (it's parsed as a path), so
    without prepending a scheme the host looks unknown and a paid provider is
    mis-classified as free — a real-money leak at the dispatch choke point."""
    assert _host_is_paid("api.openai.com/v1") is True
    assert _host_is_paid("openrouter.ai") is True
    # And the free side stays free without a scheme.
    assert _host_is_paid("localhost:11434") is False


# --- (e) paid entry dropped from _resolve_fallback_candidates ---------------

def test_paid_fallback_candidate_dropped_under_free_only(monkeypatch):
    monkeypatch.setenv("BERTOS_ALLOW_PAID", "1")  # switch ON: only free_only drops it

    paid = _endpoint(
        "openrouter", "gpt-4",
        base_url="https://openrouter.ai/api/v1",
        endpoint_kind="api", is_paid=True,
    )
    free = _endpoint(
        "local", "qwen",
        base_url="http://localhost:11434",
        endpoint_kind="local", is_paid=False,
    )
    settings = {
        "default_model_fallbacks": [
            {"endpoint_id": "openrouter", "model": "gpt-4"},
            {"endpoint_id": "local", "model": "qwen"},
        ],
    }
    _install_resolver_fakes(monkeypatch, settings, [paid, free])

    # Without free_only the paid entry resolves (switch is ON).
    all_cands = _resolve_fallback_candidates("default_model_fallbacks", free_only=False)
    assert any("openrouter.ai" in c[0] for c in all_cands)
    assert any(c[1] == "qwen" for c in all_cands)

    # With free_only the paid entry is dropped; only the free one survives.
    free_cands = _resolve_fallback_candidates("default_model_fallbacks", free_only=True)
    assert all("openrouter.ai" not in c[0] for c in free_cands)
    assert any(c[1] == "qwen" for c in free_cands)
    assert len(free_cands) == 1
