"""Tests for the vault-backed MemoryManager (Slice 2).

CRITICAL SAFETY: every test points the MemoryManager at a TEMP partition under
``tmp_path`` (via the explicit ``app_dir`` kwarg). No test ever reads, writes,
or unlinks the real /Users/willlambert/Documents/BertOS-Vault.

Cross-partition test (e) additionally pre-creates sibling notes under
``<tmp_vault>/Memory/global/`` and asserts they are byte-for-byte and
mtime-for-mtime UNCHANGED after a full save() — the structural NO-TOUCH
guarantee, exercised against a temp stand-in for the real vault.
"""

import os
import time

from src.memory import MemoryManager


def _mgr(tmp_path):
    """A MemoryManager whose partition is an isolated temp dir.

    We build a fake vault tree: <tmp>/vault/Memory/bertos is the app partition,
    <tmp>/vault/Memory/global holds 'other partition' sibling notes.
    """
    vault = tmp_path / "vault"
    app_dir = vault / "Memory" / "bertos"
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    return MemoryManager(str(data_dir), app_dir=str(app_dir)), app_dir, vault


# ---------------------------------------------------------------------------
# (a) round-trip: custom key + pinned + session_id + uses + owner all survive
# ---------------------------------------------------------------------------
def test_roundtrip_preserves_all_keys_and_stable_filename(tmp_path):
    mgr, app_dir, _ = _mgr(tmp_path)

    entry = mgr.add_entry("phase1 vault probe", source="bertos", category="fact", owner="alice")
    mid = entry["id"]
    entry["pinned"] = True
    entry["session_id"] = "sess-7f2a"
    entry["uses"] = 3
    entry["metadata"] = {"confidence": 0.9}
    entry["customKey"] = "survives-roundtrip"

    mgr.save([entry])

    # Stable, id-derived filename exists.
    note_path = os.path.join(str(app_dir), f"{mid}.md")
    assert os.path.exists(note_path), "note must live at <id>.md"

    loaded = mgr.load_all()
    assert len(loaded) == 1
    got = loaded[0]

    assert got["id"] == mid
    assert got["text"] == "phase1 vault probe"
    assert got["category"] == "fact"
    assert got["source"] == "bertos"
    assert got["owner"] == "alice"
    assert got["pinned"] is True
    assert got["session_id"] == "sess-7f2a"
    assert got["uses"] == 3
    assert got["metadata"] == {"confidence": 0.9}
    assert got["customKey"] == "survives-roundtrip"
    # timestamp survives the int<->ISO round-trip (within 1s granularity).
    assert abs(int(got["timestamp"]) - int(entry["timestamp"])) <= 1


# ---------------------------------------------------------------------------
# (b) update-in-place: load, mutate (pin), save -> still ONE file
# ---------------------------------------------------------------------------
def test_update_in_place_no_duplicate_file(tmp_path):
    mgr, app_dir, _ = _mgr(tmp_path)

    entry = mgr.add_entry("pin me", owner="bob")
    mid = entry["id"]
    mgr.save([entry])

    # Reload (so _vault_path is stashed), mutate, save again.
    loaded = mgr.load_all()
    assert len(loaded) == 1
    loaded[0]["pinned"] = True
    mgr.save(loaded)

    md_files = [f for f in os.listdir(str(app_dir)) if f.endswith(".md")]
    assert md_files == [f"{mid}.md"], f"expected exactly one note, got {md_files}"

    again = mgr.load_all()
    assert len(again) == 1
    assert again[0]["pinned"] is True
    assert again[0]["id"] == mid


# ---------------------------------------------------------------------------
# (c) delete semantics: delete_orphans=True removes orphan; default keeps it
# ---------------------------------------------------------------------------
def test_delete_orphans_true_removes_orphan(tmp_path):
    mgr, app_dir, _ = _mgr(tmp_path)

    a = mgr.add_entry("keep me", owner="alice")
    b = mgr.add_entry("delete me", owner="alice")
    mgr.save([a, b])
    assert len({f for f in os.listdir(str(app_dir)) if f.endswith(".md")}) == 2

    # Genuine delete: save the remaining list WITH the flag.
    mgr.save([a], delete_orphans=True)

    remaining_files = {f for f in os.listdir(str(app_dir)) if f.endswith(".md")}
    assert remaining_files == {f"{a['id']}.md"}
    loaded = mgr.load_all()
    assert [e["id"] for e in loaded] == [a["id"]]


def test_save_without_flag_keeps_orphans_audit_safety(tmp_path):
    mgr, app_dir, _ = _mgr(tmp_path)

    a = mgr.add_entry("entry a", owner="alice")
    b = mgr.add_entry("entry b", owner="alice")
    mgr.save([a, b])

    # Audit/extract style: save only a SUBSET, default (no flag). The omitted
    # entry must NOT be unlinked — this is the MUST-FIX audit-safety guard.
    mgr.save([a])

    files = {f for f in os.listdir(str(app_dir)) if f.endswith(".md")}
    assert files == {f"{a['id']}.md", f"{b['id']}.md"}, "subset save must NOT delete"
    loaded_ids = {e["id"] for e in mgr.load_all()}
    assert loaded_ids == {a["id"], b["id"]}


# ---------------------------------------------------------------------------
# (d) text guarantee: body-only / frontmatter-only / malformed -> string text
# ---------------------------------------------------------------------------
def test_text_guarantee_body_only(tmp_path):
    mgr, app_dir, _ = _mgr(tmp_path)
    os.makedirs(str(app_dir), exist_ok=True)
    # A note with no frontmatter at all — the whole file is the body.
    with open(os.path.join(str(app_dir), "body-only.md"), "w", encoding="utf-8") as f:
        f.write("just a plain body, no frontmatter\n")

    loaded = mgr.load_all()
    assert len(loaded) == 1
    assert isinstance(loaded[0]["text"], str)
    assert "plain body" in loaded[0]["text"]


def test_text_guarantee_frontmatter_only(tmp_path):
    mgr, app_dir, _ = _mgr(tmp_path)
    os.makedirs(str(app_dir), exist_ok=True)
    # Valid frontmatter, EMPTY body -> fallback to a non-empty string text.
    note = (
        "---\n"
        'id: "fm-only-id"\n'
        'ts: "2026-06-08T18:30:00.000Z"\n'
        'kind: "fact"\n'
        'source: "bertos"\n'
        "tags:\n"
        '  - "bertos"\n'
        '  - "memory"\n'
        "extra: {}\n"
        "---\n"
    )
    with open(os.path.join(str(app_dir), "fm-only-id.md"), "w", encoding="utf-8") as f:
        f.write(note)

    loaded = mgr.load_all()
    assert len(loaded) == 1
    e = loaded[0]
    assert isinstance(e["text"], str) and e["text"].strip() != ""
    assert e["id"] == "fm-only-id"


def test_text_guarantee_malformed_yaml_salvaged(tmp_path):
    mgr, app_dir, _ = _mgr(tmp_path)
    os.makedirs(str(app_dir), exist_ok=True)
    # Deliberately broken frontmatter (unterminated structure / bad indent).
    note = (
        "---\n"
        "id: \"broken\n"          # unterminated quote
        "  : : : not valid yaml :\n"
        "kind fact\n"             # missing colon
        "---\n"
        "the salvageable body text\n"
    )
    with open(os.path.join(str(app_dir), "broken-note.md"), "w", encoding="utf-8") as f:
        f.write(note)

    # Must not raise; must produce a string text.
    loaded = mgr.load_all()
    assert len(loaded) == 1
    e = loaded[0]
    assert isinstance(e["text"], str) and e["text"].strip() != ""
    # Salvage must NOT invent an owner.
    assert "owner" not in e or e.get("owner") in (None, "")
    # find_duplicates / get_relevant_memories index text with brackets — safe.
    assert mgr.find_duplicates("nope", loaded) == []
    assert mgr.get_relevant_memories("salvageable", loaded) is not None


# ---------------------------------------------------------------------------
# (e) cross-partition: sibling notes under Memory/global/ are NEVER touched
# ---------------------------------------------------------------------------
def test_cross_partition_siblings_unchanged(tmp_path):
    mgr, app_dir, vault = _mgr(tmp_path)

    # Pre-create 'other partition' notes (stand-in for the 721 real notes).
    global_dir = vault / "Memory" / "global"
    global_dir.mkdir(parents=True, exist_ok=True)
    sibling_a = global_dir / "human-note-a.md"
    sibling_b = global_dir / "human-note-b.md"
    sibling_a.write_text("---\nid: \"human-a\"\nrole: \"assistant\"\n---\nhuman content A\n", encoding="utf-8")
    sibling_b.write_text("---\nid: \"human-b\"\nprojectId: \"global\"\n---\nhuman content B\n", encoding="utf-8")

    # Snapshot content + mtime, then age them so any rewrite changes mtime.
    before = {}
    for p in (sibling_a, sibling_b):
        before[str(p)] = (p.read_bytes(), p.stat().st_mtime)
    old = time.time() - 10_000
    os.utime(str(sibling_a), (old, old))
    os.utime(str(sibling_b), (old, old))
    snap_a = (sibling_a.read_bytes(), sibling_a.stat().st_mtime)
    snap_b = (sibling_b.read_bytes(), sibling_b.stat().st_mtime)

    # Do a full lifecycle in the app partition, incl. an orphan-unlink delete.
    e1 = mgr.add_entry("app entry 1", owner="alice")
    e2 = mgr.add_entry("app entry 2", owner="alice")
    mgr.save([e1, e2])
    mgr.load_all()
    mgr.save([e1], delete_orphans=True)  # the most destructive path

    # load_all reads ONLY the bertos partition — it must not surface siblings.
    loaded = mgr.load_all()
    assert {e["id"] for e in loaded} == {e1["id"]}
    assert "human-a" not in {e["id"] for e in loaded}
    assert "human-b" not in {e["id"] for e in loaded}

    # Siblings: count, content, and mtime all unchanged.
    assert sibling_a.exists() and sibling_b.exists()
    assert (sibling_a.read_bytes(), sibling_a.stat().st_mtime) == snap_a
    assert (sibling_b.read_bytes(), sibling_b.stat().st_mtime) == snap_b
    # And the global dir still has exactly the two siblings.
    assert sorted(os.listdir(str(global_dir))) == ["human-note-a.md", "human-note-b.md"]


# ---------------------------------------------------------------------------
# extra: the legacy self.memory_file attribute survives (memory_extractor dep)
# ---------------------------------------------------------------------------
def test_memory_file_attribute_preserved(tmp_path):
    mgr, _, _ = _mgr(tmp_path)
    assert mgr.memory_file.endswith("memory.json")
    # memory_extractor does os.path.dirname(memory_manager.memory_file).
    assert os.path.isdir(os.path.dirname(mgr.memory_file))


def test_env_override_app_dir(tmp_path, monkeypatch):
    """The BERTOS_MEMORY_APP_DIR env hook isolates the partition too."""
    target = tmp_path / "envvault" / "Memory" / "bertos"
    monkeypatch.setenv("BERTOS_MEMORY_APP_DIR", str(target))
    data_dir = tmp_path / "d2"
    data_dir.mkdir()
    mgr = MemoryManager(str(data_dir))
    assert os.path.abspath(mgr.app_dir) == os.path.abspath(str(target))
    e = mgr.add_entry("env routed", owner="x")
    mgr.save([e])
    assert os.path.exists(os.path.join(str(target), f"{e['id']}.md"))


# ---------------------------------------------------------------------------
# (FIX 1) path-traversal: a crafted id can NEVER escape the partition
# ---------------------------------------------------------------------------
def test_traversal_id_cannot_escape_partition(tmp_path):
    mgr, app_dir, vault = _mgr(tmp_path)

    # Pre-create a 'precious' sibling note OUTSIDE the partition that a
    # "../../evil"-style id would overwrite if traversal worked.
    human_dir = vault / "Memory" / "human"
    human_dir.mkdir(parents=True, exist_ok=True)
    precious = human_dir / "precious.md"
    precious.write_text("DO NOT TOUCH\n", encoding="utf-8")

    # Two hostile ids: a parent-traversal and a sub-path separator.
    e1 = {"id": "../../evil", "text": "traversal one", "source": "bertos", "category": "fact"}
    e2 = {"id": "a/b", "text": "traversal two", "source": "bertos", "category": "fact"}
    mgr.save([e1, e2])

    # The in-memory ids were stamped with the sanitized (uuid) values, and the
    # raw hostile strings no longer appear as ids.
    assert e1["id"] != "../../evil"
    assert e2["id"] != "a/b"
    assert "/" not in e1["id"] and ".." not in e1["id"]
    assert "/" not in e2["id"]

    # NOTHING was written outside the partition: the precious sibling is byte
    # identical and no stray file appeared anywhere under the fake vault except
    # inside Memory/bertos.
    assert precious.read_text(encoding="utf-8") == "DO NOT TOUCH\n"
    for root, _dirs, files in os.walk(str(vault)):
        for fname in files:
            full = os.path.abspath(os.path.join(root, fname))
            in_partition = full.startswith(os.path.abspath(str(app_dir)) + os.sep)
            is_precious = full == os.path.abspath(str(precious))
            assert in_partition or is_precious, f"stray write escaped partition: {full}"

    # The entries DID persist INSIDE the partition under their sanitized ids,
    # and the on-disk filename matches the in-memory id (they agree).
    loaded = mgr.load_all()
    texts = {e["text"] for e in loaded}
    assert {"traversal one", "traversal two"} <= texts
    for e in loaded:
        if e["text"] in ("traversal one", "traversal two"):
            note_path = os.path.join(str(app_dir), f"{e['id']}.md")
            assert os.path.exists(note_path), f"sanitized note missing: {note_path}"
            # filename stem == in-memory id
            assert os.path.splitext(os.path.basename(note_path))[0] == e["id"]


def test_atomic_write_rejects_outside_path(tmp_path):
    """The central _atomic_write fence raises on any path outside the partition."""
    import pytest

    mgr, app_dir, vault = _mgr(tmp_path)
    outside = os.path.join(str(vault), "Memory", "human", "escape.md")
    with pytest.raises(ValueError):
        mgr._atomic_write(outside, "---\nid: x\n---\nbody\n")
    assert not os.path.exists(outside)
    # A traversal path that climbs out via .. is also rejected.
    sneaky = os.path.join(str(app_dir), "..", "..", "human", "escape2.md")
    with pytest.raises(ValueError):
        mgr._atomic_write(sneaky, "x")
    assert not os.path.exists(os.path.abspath(sneaky))


# ---------------------------------------------------------------------------
# (FIX 2) delete TOCTOU: a concurrently-added note survives a delete-by-id
# ---------------------------------------------------------------------------
def test_concurrent_add_survives_delete(tmp_path):
    mgr, app_dir, vault = _mgr(tmp_path)

    # Snapshot A: two notes exist; load them (simulating a caller that read the
    # world before deciding to delete one).
    old1 = mgr.add_entry("old note 1", owner="alice")
    old2 = mgr.add_entry("old note 2", owner="alice")
    mgr.save([old1, old2])
    snapshot_a = mgr.load_all()  # caller's stale view
    assert {e["id"] for e in snapshot_a} == {old1["id"], old2["id"]}

    # A CONCURRENT writer (fresh manager, same partition) commits a brand-new
    # note N2 to disk AFTER the caller's snapshot was taken.
    mgr2 = MemoryManager(str(tmp_path / "data"), app_dir=str(app_dir))
    n2 = mgr2.add_entry("concurrently added N2", owner="bob")
    mgr2.save([n2])
    assert os.path.exists(os.path.join(str(app_dir), f"{n2['id']}.md"))

    # The caller now deletes an OLD id via the race-safe delete-by-id. Under the
    # old delete-by-absence path, N2 (absent from snapshot_a) would be orphaned.
    removed = mgr.delete([old1["id"]])
    assert removed == 1

    # N2 still exists on disk; only old1 was removed; old2 untouched.
    assert os.path.exists(os.path.join(str(app_dir), f"{n2['id']}.md")), "concurrent add was orphaned!"
    assert not os.path.exists(os.path.join(str(app_dir), f"{old1['id']}.md"))
    assert os.path.exists(os.path.join(str(app_dir), f"{old2['id']}.md"))

    remaining_ids = {e["id"] for e in mgr.load_all()}
    assert remaining_ids == {old2["id"], n2["id"]}


# ---------------------------------------------------------------------------
# (FIX 3) fallback frontmatter: a key containing ':' round-trips
# ---------------------------------------------------------------------------
def test_colon_in_extra_key_roundtrips_fallback(monkeypatch):
    """Force the PyYAML-absent fallback emitter+parser and confirm an extra
    key like 'ns:key' (colon in the key) survives a round-trip intact."""
    from src import memory as memmod

    # Force the fallback path regardless of whether PyYAML is installed.
    monkeypatch.setattr(memmod, "_yaml", None)

    fm = {
        "id": "colon-test",
        "ts": "2026-06-08T18:30:00.000Z",
        "kind": "fact",
        "source": "bertos",
        "tags": ["bertos", "memory"],
        "extra": {
            "ns:key": "scoped-value",
            "weird#key": "hashy",
            "plain": "ok",
            "url": "http://example.com:8080/path",  # colon in VALUE too
        },
    }
    dumped = memmod._dump_frontmatter(fm)
    parsed = memmod._parse_frontmatter(dumped)

    assert parsed["id"] == "colon-test"
    assert isinstance(parsed.get("extra"), dict)
    extra = parsed["extra"]
    assert extra.get("ns:key") == "scoped-value", f"colon key lost: {extra!r}"
    assert extra.get("weird#key") == "hashy"
    assert extra.get("plain") == "ok"
    assert extra.get("url") == "http://example.com:8080/path"
