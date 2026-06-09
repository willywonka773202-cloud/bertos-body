# src/endpoint_resolver.py
"""Unified endpoint resolution for all backend services.

Consolidates the 4+ copies of normalize_base / resolve_endpoint logic into one place.
"""

import json
import logging
import socket
import subprocess
from typing import Optional, Tuple, Dict
from urllib.parse import urlparse, urlunparse

from core.database import SessionLocal, ModelEndpoint
from src.llm_core import _detect_provider, _host_match, _ollama_api_root

logger = logging.getLogger(__name__)

# Model-name substrings that are NOT chat/generation models. When an endpoint
# has no explicit model configured we pick the first CHAT model from its list —
# never an embedding/tts/etc. (an OpenAI-style endpoint often lists
# `text-embedding-ada-002` first, which silently broke email-summarize and
# other resolve_endpoint callers with "Cannot reach model").
_NON_CHAT_MODEL = (
    "text-embedding", "embedding", "tts-", "whisper", "dall-e",
    "moderation", "rerank", "reranker", "clip", "stable-diffusion",
)


def _first_chat_model(models) -> Optional[str]:
    """First model that isn't an embedding/tts/etc.; falls back to models[0]."""
    for m in (models or []):
        if not any(p in str(m).lower() for p in _NON_CHAT_MODEL):
            return m
    return (models[0] if models else None)


def _endpoint_cached_models(ep) -> list:
    """Return cached model ids from the current or legacy endpoint field."""
    raw = getattr(ep, "cached_models", None) or getattr(ep, "models", None)
    if not raw:
        return []
    try:
        models = json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        return []
    return models if isinstance(models, list) else []


def _endpoint_hidden_models(ep) -> set:
    """Model ids the admin disabled on this endpoint (the UI's hidden list)."""
    raw = getattr(ep, "hidden_models", None)
    if not raw:
        return set()
    try:
        hidden = json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        return set()
    return set(hidden) if isinstance(hidden, list) else set()


def _endpoint_enabled_models(ep) -> list:
    """Cached models minus the ones disabled on the endpoint, order preserved.

    The auto-pick fallback must never select a model the user disabled — a
    Groq endpoint can list 16 models with only 1 enabled, and picking the
    raw first one resolves to a model that 400s ("requires terms acceptance").
    """
    hidden = _endpoint_hidden_models(ep)
    return [m for m in _endpoint_cached_models(ep) if m not in hidden]


# ---------------------------------------------------------------------------
# Free-first cost guardrail (BertOS)
# ---------------------------------------------------------------------------
# Hosts that bill per-token (metered external APIs). Membership is decided by
# hostname (exact or subdomain) via _host_match — NOT substring — so a path or
# query that merely contains the domain text is never misclassified. The list
# mirrors src/llm_core._provider_label.
_PAID_HOSTS = (
    "openai.com",
    "anthropic.com",
    "openrouter.ai",
    "googleapis.com",      # Google / Gemini
    "google.com",
    "x.ai",                # xAI
    "mistral.ai",
    "deepseek.com",
    "together.xyz", "together.ai",
    "fireworks.ai",
    "groq.com",
    "opencode.ai",         # opencode-zen / opencode-go
    "ollama.com",          # Ollama Cloud (metered) — distinct from native local Ollama
    "perplexity.ai",
    "cohere.com", "cohere.ai",
    "ai21.com",
    "voyageai.com",        # paid embeddings
    "anyscale.com",
    "replicate.com",
    "openai.azure.com",    # Azure OpenAI (*.openai.azure.com)
    "amazonaws.com",       # AWS Bedrock (bedrock-runtime.<region>.amazonaws.com) — metered.
                           # Errs toward blocking on the free-first contract: AWS
                           # has no bedrock-only registrable domain, so the whole
                           # amazonaws.com family is treated as paid.
    "cognitiveservices.azure.com",  # Azure AI Services (*.cognitiveservices.azure.com)
)

# Subscription-backed or local hosts that are FREE at call time. Loopback hosts
# are matched separately (exact hostname) since they have no registrable domain.
_FREE_HOSTS = (
    "localhost",
    "127.0.0.1",
    "::1",
    "0.0.0.0",
)


def _with_scheme(base: str) -> str:
    """Ensure a base URL carries a scheme so urlparse(...).hostname is populated.

    A scheme-less authority like ``api.openai.com/v1`` parses with
    ``hostname is None`` (urlparse treats it as a path), which makes every
    hostname-based classifier FAIL OPEN — the host looks unknown, so a paid
    provider is mis-classified as free. Prepending ``//`` makes urlparse read
    the leading token as the authority (``urlparse('//host/x').hostname=='host'``)
    without committing to a protocol.
    """
    base = (base or "").strip()
    if not base:
        return base
    return base if "://" in base else "//" + base


def _host_is_known_paid(base: str) -> bool:
    """True iff the host is a KNOWN metered provider — the owner's real money
    risk (their cloud API keys). Hostname match (exact/subdomain), not substring."""
    base = (base or "").strip()
    if not base:
        return False
    return _host_match(_with_scheme(base), *_PAID_HOSTS)


def _host_is_known_free(base: str) -> bool:
    """True iff the host is free at call time: subscription-backed (ChatGPT/
    Copilot), native (local) Ollama, or loopback / explicitly-local."""
    base = (base or "").strip()
    if not base:
        return False
    # Subscription-backed providers are flat-rate (free at call time).
    try:
        from src.chatgpt_subscription import is_chatgpt_subscription_base
        if is_chatgpt_subscription_base(base):
            return True
    except Exception:
        pass
    try:
        from src.copilot import is_copilot_base
        if is_copilot_base(base):
            return True
    except Exception:
        pass
    # Native (local) Ollama is free; Ollama Cloud (ollama.com) is paid (below).
    try:
        from src.llm_core import _is_ollama_native_url
        if _is_ollama_native_url(base) and not _host_match(_with_scheme(base), "ollama.com"):
            return True
    except Exception:
        pass
    # Loopback / explicitly-local hosts are free.
    try:
        host = (urlparse(_with_scheme(base)).hostname or "").lower().rstrip(".")
    except Exception:
        host = ""
    return host in _FREE_HOSTS


def _host_is_paid(base: str) -> bool:
    """Dispatch-level host classification (no endpoint row available).

    Returns True ONLY for KNOWN metered hosts (the owner's cloud API keys).
    Unknown / self-hosted / loopback hosts are NOT blocked at dispatch, so a
    private OpenAI-compatible server (LAN / Tailscale / custom domain) stays
    usable. The stricter kind-aware policy lives in _endpoint_is_paid, where the
    endpoint row (endpoint_kind / is_paid column) is available.
    """
    if _host_is_known_free(base):
        return False
    return _host_is_known_paid(base)


def _endpoint_is_paid(ep) -> bool:
    """Whether a ModelEndpoint row should be treated as paid/metered.

    Free-first + self-hosted-friendly policy:
      1. an explicit is_paid column wins;
      2. a KNOWN paid host -> paid; a KNOWN free host (loopback / native Ollama /
         subscription-backed) -> free;
      3. unknown host -> only an explicitly endpoint_kind='api' row fails CLOSED
         to paid (the owner declared it a cloud API). 'local'/'proxy'/'auto'/
         unset default to FREE so self-hosted servers on custom hosts/IPs work.
    Mark is_paid=True on any endpoint to force it paid regardless of host.
    """
    explicit = getattr(ep, "is_paid", None)
    if explicit is not None:
        return bool(explicit)
    base = normalize_base(getattr(ep, "base_url", "") or "")
    if _host_is_known_paid(base):
        return True
    if _host_is_known_free(base):
        return False
    kind = (getattr(ep, "endpoint_kind", "") or "auto").lower()
    return kind == "api"


def _paid_blocked(ep, free_only: bool = False) -> bool:
    """True when this endpoint must be refused on cost grounds.

    An endpoint is blocked when it is PAID and either this caller is
    free-only (background/unattended work) OR the global BERTOS_ALLOW_PAID
    kill-switch is OFF. Read the switch LIVE so it hot-flips and tests can
    monkeypatch.
    """
    if not _endpoint_is_paid(ep):
        return False
    try:
        from src.constants import allow_paid
        switch_on = allow_paid()
    except Exception:
        switch_on = False
    return free_only or not switch_on


def _host_paid_blocked(url: str, free_only: bool = False) -> bool:
    """Dispatch-level variant of _paid_blocked keyed on a raw target URL.

    Used by the fail-closed guard in src/llm_core so direct-build callers that
    bypass resolve_endpoint still cannot spend by default. The URL here is the
    built chat URL; _host_is_paid normalizes/classifies by hostname.
    """
    if not _host_is_paid(url):
        return False
    try:
        from src.constants import allow_paid
        switch_on = allow_paid()
    except Exception:
        switch_on = False
    return free_only or not switch_on


def resolve_endpoint_runtime(ep, owner: Optional[str] = None) -> Tuple[str, Optional[str]]:
    """Resolve a ModelEndpoint row to its runtime base URL and bearer/API key.

    Static-key providers use ``ModelEndpoint.api_key``. Session-backed providers
    store refreshable credentials in ProviderAuthSession and must resolve a
    current access token at call time.
    """
    base = normalize_base(getattr(ep, "base_url", "") or "")
    api_key = getattr(ep, "api_key", None)
    auth_id = getattr(ep, "provider_auth_id", None)
    if auth_id:
        from src.chatgpt_subscription import resolve_runtime_credentials

        creds = resolve_runtime_credentials(auth_id, owner=owner)
        base = normalize_base(creds.get("base_url") or base)
        api_key = creds.get("api_key")
    return base, api_key


# Cache for Tailscale hostname → IP resolution
_tailscale_cache: Dict[str, Optional[str]] = {}


def _resolve_tailscale_host(hostname: str) -> Optional[str]:
    """Try to resolve a hostname via 'tailscale status' if DNS fails."""
    if hostname in _tailscale_cache:
        return _tailscale_cache[hostname]

    # First check if normal DNS works
    try:
        socket.getaddrinfo(hostname, None, socket.AF_INET)
        _tailscale_cache[hostname] = None  # DNS works, no override needed
        return None
    except socket.gaierror:
        pass

    # DNS failed — try tailscale
    try:
        result = subprocess.run(
            ["tailscale", "status", "--json"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            import json as _json
            data = _json.loads(result.stdout)
            peers = data.get("Peer", {})
            for _id, peer in peers.items():
                peer_name = (peer.get("HostName") or "").lower()
                dns_name = (peer.get("DNSName") or "").split(".")[0].lower()
                if peer_name == hostname.lower() or dns_name == hostname.lower():
                    addrs = peer.get("TailscaleIPs", [])
                    if addrs:
                        ip = addrs[0]
                        logger.info(f"Resolved '{hostname}' via Tailscale → {ip}")
                        _tailscale_cache[hostname] = ip
                        return ip
    except Exception as e:
        logger.debug(f"Tailscale resolution failed for '{hostname}': {e}")

    _tailscale_cache[hostname] = None
    return None


def resolve_url(url: str) -> str:
    """If a URL's hostname can't be resolved via DNS, try Tailscale."""
    parsed = urlparse(url)
    hostname = parsed.hostname
    if not hostname:
        return url
    ip = _resolve_tailscale_host(hostname)
    if ip:
        # Replace hostname with IP in the URL
        netloc = ip
        if parsed.port:
            netloc = f"{ip}:{parsed.port}"
        return urlunparse(parsed._replace(netloc=netloc))
    return url


def normalize_base(url: str) -> str:
    """Strip known API path suffixes from a base URL."""
    url = (url or "").strip().rstrip("/")
    for suffix in ["/models", "/chat/completions", "/completions", "/v1/messages", "/responses"]:
        if url.endswith(suffix):
            url = url[: -len(suffix)].rstrip("/")
    for suffix in ["/chat", "/tags", "/generate"]:
        if url.endswith("/api" + suffix):
            url = url[: -len(suffix)].rstrip("/")
    return url


def _anthropic_api_root(base: str) -> str:
    """Return Anthropic's API root, preserving /v1 for OpenAI-compatible APIs elsewhere."""
    base = (base or "").strip().rstrip("/")
    if _host_match(base, "anthropic.com") and base.endswith("/v1"):
        return base[:-3].rstrip("/")
    return base


def build_chat_url(base: str) -> str:
    """Return the correct chat endpoint URL for a given base."""
    base = resolve_url(base)
    provider = _detect_provider(base)
    if provider == "anthropic":
        return _anthropic_api_root(base) + "/v1/messages"
    if provider == "ollama":
        return _ollama_api_root(base) + "/chat"
    if provider == "chatgpt-subscription":
        return base.rstrip("/") + "/responses"
    return base + "/chat/completions"


def build_models_url(base: str) -> Optional[str]:
    """Return the provider-specific model-list endpoint URL for a base."""
    base = resolve_url(base)
    provider = _detect_provider(base)
    if provider == "anthropic":
        return _anthropic_api_root(base) + "/v1/models"
    if provider == "ollama":
        return _ollama_api_root(base) + "/tags"
    if provider == "chatgpt-subscription":
        return None
    return base + "/models"


def build_headers(api_key: Optional[str], base: str) -> Dict[str, str]:
    """Build auth headers for an endpoint."""
    provider = _detect_provider(base)
    headers: Dict[str, str] = {}
    if provider == "anthropic":
        if api_key:
            headers["x-api-key"] = api_key
        headers["anthropic-version"] = "2023-06-01"
        return headers
    if provider == "copilot":
        from src.copilot import copilot_headers
        return copilot_headers(api_key)
    if provider == "chatgpt-subscription":
        from src.chatgpt_subscription import chatgpt_headers
        return chatgpt_headers(api_key)
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    if provider == "openrouter":
        headers.setdefault("HTTP-Referer", "https://github.com/pewdiepie-archdaemon/odysseus")
        headers.setdefault("X-OpenRouter-Title", "Odysseus")
    return headers


def resolve_endpoint(
    setting_prefix: str,
    fallback_url: Optional[str] = None,
    fallback_model: Optional[str] = None,
    fallback_headers: Optional[Dict] = None,
    owner: Optional[str] = None,
    free_only: bool = False,
) -> Tuple[Optional[str], Optional[str], Optional[Dict]]:
    """Resolve an endpoint/model from settings, with fallback.

    Args:
        setting_prefix: Settings key prefix, e.g. "research", "task", "utility", "default".
                       Reads ``{prefix}_endpoint_id`` and ``{prefix}_model`` from settings.
        fallback_url:    URL to use if settings are empty or endpoint missing.
        fallback_model:  Model to use if settings are empty.
        fallback_headers: Headers to use if using fallback.
        free_only:       When True (background/unattended callers), a paid
                         endpoint is refused and the resolver falls through to
                         the free/local fallback instead. The global
                         BERTOS_ALLOW_PAID kill-switch (read live) blocks paid
                         endpoints for ALL callers when off.

    Returns:
        (endpoint_url, model, headers) — resolved or fallback values.
    """
    try:
        from src.settings import get_user_setting, load_settings
        settings = load_settings()
    except Exception:
        return fallback_url, fallback_model, fallback_headers

    owner_str = owner or ""
    def _stg(key: str) -> str:
        return (get_user_setting(key, owner_str, settings.get(key, "")) or "").strip()

    ep_id = _stg(f"{setting_prefix}_endpoint_id")
    model = _stg(f"{setting_prefix}_model")

    # If the specific endpoint is not configured, but the caller provided a
    # valid fallback (e.g. the active session model), use that immediately.
    # This prevents background tasks from jumping to the global default_model
    # when the user is mid-conversation with a different model.
    if not ep_id and fallback_url and fallback_model:
        return fallback_url, fallback_model, fallback_headers

    # Build the ordered (ep_id, model) cascade. Under the free-first guardrail
    # a paid/blocked tier is skipped so we keep descending toward the free
    # default rather than failing. Without the guardrail only the first tier is
    # consulted (legacy behaviour: ep_id resolved by the cascade below).
    tiers: list = []
    seen_ids: set = set()

    def _add_tier(eid: str, mdl: str):
        eid = (eid or "").strip()
        if eid and eid not in seen_ids:
            seen_ids.add(eid)
            tiers.append((eid, (mdl or "").strip()))

    _add_tier(ep_id, model)
    # Unset Utility means "same as Default Chat Model".
    if setting_prefix == "utility":
        _add_tier(_stg("default_endpoint_id"), _stg("default_model"))
    else:
        # task/research/auto-naming descend through utility then default.
        _add_tier(_stg("utility_endpoint_id"), _stg("utility_model"))
        _add_tier(_stg("default_endpoint_id"), _stg("default_model"))

    if not tiers:
        return fallback_url, fallback_model, fallback_headers

    db = SessionLocal()
    try:
        ep = None
        ep_id, model = tiers[0]
        # `skipped_paid` records that an earlier tier resolved to a real
        # endpoint we refused on cost grounds. Only then do we keep descending
        # past a missing/disabled tier — this preserves legacy behaviour
        # (return fallback when the FIRST configured tier's row is absent)
        # while letting the guardrail land on a lower free tier.
        skipped_paid = False
        for cand_id, cand_model in tiers:
            q = db.query(ModelEndpoint).filter(
                ModelEndpoint.id == cand_id,
                ModelEndpoint.is_enabled == True,
            )
            if owner:
                from src.auth_helpers import owner_filter
                cand_ep = owner_filter(q, ModelEndpoint, owner).first()
            else:
                cand_ep = q.first()
            if not cand_ep:
                if skipped_paid:
                    continue
                break
            # Free-first cost guardrail: refuse a paid endpoint when this caller
            # is free-only or when the global kill-switch is off. Skip this tier
            # and keep descending toward the free/local default rather than
            # raising.
            if _paid_blocked(cand_ep, free_only):
                logger.info(
                    "[guardrail] refusing paid endpoint %r for prefix %r "
                    "(free_only=%s) — descending cascade toward free/local",
                    cand_id, setting_prefix, free_only,
                )
                skipped_paid = True
                continue
            ep = cand_ep
            ep_id, model = cand_id, cand_model
            break

        if not ep:
            return fallback_url, fallback_model, fallback_headers

        try:
            base, api_key = resolve_endpoint_runtime(ep, owner=owner)
        except Exception as e:
            logger.warning("Could not resolve endpoint runtime credentials: %s", e)
            return fallback_url, fallback_model, fallback_headers
        chat_url = build_chat_url(base)
        headers = build_headers(api_key, base)

        # Discard a configured model the user has since disabled on the
        # endpoint (e.g. a stale `default_model` left pointing at a now-hidden
        # model). Treat it as unset so the picker below selects a live one
        # instead of dispatching to a disabled model that 400s.
        if model and model in _endpoint_hidden_models(ep):
            model = ""
        # If no (usable) model specified, pick the first enabled chat model.
        if not model:
            model = _first_chat_model(_endpoint_enabled_models(ep)) or ""
        if not model and not fallback_model:
            logger.warning('[resolve_endpoint] no usable model (all models hidden or list empty)')

        return chat_url, model or fallback_model, headers
    except Exception as e:
        logger.debug(f"Could not resolve {setting_prefix} endpoint: {e}")
        return fallback_url, fallback_model, fallback_headers
    finally:
        db.close()


def resolve_endpoint_by_id(
    ep_id: str, model: Optional[str] = None, owner: Optional[str] = None,
    free_only: bool = False,
) -> Optional[Tuple[str, str, Dict]]:
    """Resolve a specific endpoint id (+ optional model) to (chat_url, model, headers).

    Returns None if the endpoint doesn't exist or is disabled. Used to turn
    a configured fallback entry ({endpoint_id, model}) into a dispatch target.

    When free_only is True (or the global BERTOS_ALLOW_PAID kill-switch is off)
    a paid endpoint resolves to None so it is dropped from any fallback chain.
    """
    if not ep_id:
        return None
    db = SessionLocal()
    try:
        q = db.query(ModelEndpoint).filter(
            ModelEndpoint.id == ep_id,
            ModelEndpoint.is_enabled == True,
        )
        if owner:
            from src.auth_helpers import owner_filter
            q = owner_filter(q, ModelEndpoint, owner)
        ep = q.first()
        if not ep:
            return None
        # Free-first cost guardrail: drop a paid endpoint from fallback chains.
        if _paid_blocked(ep, free_only):
            logger.info(
                "[guardrail] dropping paid endpoint %r from fallback "
                "(free_only=%s)", ep_id, free_only,
            )
            return None
        try:
            base, api_key = resolve_endpoint_runtime(ep, owner=owner)
        except Exception as e:
            logger.warning("Could not resolve endpoint runtime credentials: %s", e)
            return None
        chat_url = build_chat_url(base)
        headers = build_headers(api_key, base)
        m = (model or "").strip()
        # Drop a model the user disabled on the endpoint, then pick the first
        # enabled chat model rather than a hidden one.
        if m and m in _endpoint_hidden_models(ep):
            m = ""
        if not m:
            m = _first_chat_model(_endpoint_enabled_models(ep)) or ""
        if not m:
            return None
        return chat_url, m, headers
    except Exception as e:
        logger.debug(f"Could not resolve endpoint {ep_id}: {e}")
        return None
    finally:
        db.close()


def resolve_chat_fallback_candidates(owner: Optional[str] = None, free_only: bool = False) -> list:
    """Build the configured default-chat fallback chain as a list of
    (chat_url, model, headers) tuples, skipping any that can't resolve.

    The primary model is NOT included — callers prepend their session's
    current (url, model, headers) so per-session model overrides are honored.

    With free_only=True (or the kill-switch off) paid entries are dropped.
    """
    return _resolve_fallback_candidates("default_model_fallbacks", owner=owner, free_only=free_only)


def resolve_utility_fallback_candidates(owner: Optional[str] = None, free_only: bool = False) -> list:
    """Configured fallback chain for the Utility model (`utility_model_fallbacks`)."""
    try:
        from src.settings import get_user_setting, load_settings
        settings = load_settings()
        utility_ep = (get_user_setting("utility_endpoint_id", owner or "", settings.get("utility_endpoint_id", "")) or "").strip()
        if not utility_ep:
            return _resolve_fallback_candidates("default_model_fallbacks", owner=owner, free_only=free_only)
    except Exception:
        pass
    return _resolve_fallback_candidates("utility_model_fallbacks", owner=owner, free_only=free_only)


def resolve_vision_fallback_candidates(owner: Optional[str] = None, free_only: bool = False) -> list:
    """Configured fallback chain for the Vision model (`vision_model_fallbacks`)."""
    return _resolve_fallback_candidates("vision_model_fallbacks", owner=owner, free_only=free_only)


def _resolve_fallback_candidates(setting_key: str, owner: Optional[str] = None, free_only: bool = False) -> list:
    out = []
    try:
        from src.settings import get_user_setting, load_settings
        settings = load_settings()
        chain = get_user_setting(setting_key, owner or "", settings.get(setting_key) or []) or []
    except Exception:
        return out
    for entry in chain:
        if not isinstance(entry, dict):
            continue
        resolved = resolve_endpoint_by_id(
            entry.get("endpoint_id", ""), entry.get("model", ""),
            owner=owner, free_only=free_only,
        )
        if resolved:
            out.append(resolved)
    return out
