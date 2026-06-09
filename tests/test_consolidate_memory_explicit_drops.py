"""Memory consolidation must delete only memories the model explicitly drops.

The AI tidy path computed deletions as the complement of the model's `keep`
list, so any memory the model simply omitted (a common LLM lapse) was silently
deleted. The fix honors the explicit `drop` set, so an omitted memory survives.
"""
import asyncio
import json

import src.builtin_actions as ba


class _FakeMM:
    saved = None
    deleted = None

    def __init__(self, *args, **kwargs):
        pass

    def load_all(self):
        return [
            {"id": "a", "owner": "alice", "text": "Likes dark roast coffee", "category": "preference"},
            {"id": "b", "owner": "alice", "text": "Likes dark roast coffee too", "category": "preference"},
            {"id": "c", "owner": "alice", "text": "Lives in Cairo", "category": "fact"},
        ]

    def save(self, entries, **kwargs):
        # save() gained an optional delete_orphans kwarg in the vault-backed
        # rewrite; accept and ignore it here.
        _FakeMM.saved = list(entries)

    def delete(self, ids):
        # consolidate now removes explicit drops via the locked delete-by-id
        # path (save upserts the full set; delete removes only the dropped ids).
        _FakeMM.deleted = list(ids)
        return len(_FakeMM.deleted)


def test_omitted_memory_survives_only_explicit_drop(monkeypatch):
    import src.memory
    import src.endpoint_resolver
    import src.llm_core

    _FakeMM.saved = None
    monkeypatch.setattr(src.memory, "MemoryManager", _FakeMM)
    monkeypatch.setattr(
        src.endpoint_resolver, "resolve_endpoint",
        lambda kind, owner=None, free_only=False: ("http://x/v1", "model", {}),
    )

    async def fake_llm(**kwargs):
        # Model keeps 'a', drops 'b', and OMITS 'c' entirely.
        return json.dumps({
            "keep": [{"id": "a", "text": "Likes dark roast coffee", "category": "preference"}],
            "drop": [{"id": "b", "reason": "duplicate of a"}],
        })

    monkeypatch.setattr(src.llm_core, "llm_call_async", fake_llm)

    msg, ok = asyncio.run(ba.action_consolidate_memory("alice"))

    assert ok, msg
    # Final state = everything upserted by save(), minus the ids removed by the
    # locked delete-by-id call. Omitted 'c' survives; explicitly-dropped 'b' is gone.
    saved_ids = {m["id"] for m in (_FakeMM.saved or [])}
    deleted_ids = set(_FakeMM.deleted or [])
    final = saved_ids - deleted_ids
    assert "c" in final, "omitted memory must NOT be deleted"
    assert "a" in final
    assert "b" not in final, "explicitly dropped memory should be removed"
