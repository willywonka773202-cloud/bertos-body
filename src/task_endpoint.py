"""Shared resolver for background-task AI endpoint (auto-naming, memory, sorting)."""

from src.endpoint_resolver import resolve_endpoint


def resolve_task_endpoint(fallback_url=None, fallback_model=None, fallback_headers=None,
                          owner=None, free_only=False):
    """Return (endpoint_url, model, headers) for background tasks.

    Reads task_endpoint_id / task_model from admin settings.
    Falls back to the provided values when the setting is empty or the
    endpoint cannot be resolved.

    free_only is threaded through to resolve_endpoint: True on the unattended
    scheduler/auto-sort path (forces free/local), False on interactive HTTP
    routes.
    """
    return resolve_endpoint("task", fallback_url, fallback_model, fallback_headers,
                            owner=owner, free_only=free_only)
