# SLICE 2 — Memory → Vault: Canonical-Store Design

**Status:** design (lead-approved decision record)
**Date:** 2026-06-08
**Scope:** Make the Obsidian vault (`~/Documents/BertOS-Vault/Memory/`) the source of truth for app memory **without losing any in-memory entry state**. Phase 1 = safe, lossless, fully round-trippable. No Chroma/recall rewrite here.

This document is the binding spec. Where it conflicts with a vague phrase in `PHASE1-PLAN.md`, this document wins for Slice 2.

---

## 0. Ground truth (verified, not assumed)

- `data/memory.json` **exists and is empty** — `[]`, 2 bytes, **0 entries**. Migration is therefore a **no-op** in practice (see §A.4). The design must still handle a non-empty legacy file for safety, but on this machine there is nothing to migrate.
- The real vault has **721 notes** across 34 partition folders. Frontmatter is a stable 6-key shape: `id, ts, projectId, kind, role, tags(opt), source(opt)`. No `owner/uses/pinned/session_id/metadata/category/timestamp` keys exist in the vault today.
- `projectId` in frontmatter **always equals the parent folder name** (0/721 mismatches) — folder name is trustable as projectId.
- App-created memories under `LOCALHOST_BYPASS` are **ownerless** (`owner` key absent, value conceptually `None`, never `''`).
- The in-memory entry dict is the real contract. `_CORE_FIELDS = {id, text, timestamp, source, category, uses, owner, session_id, metadata}`; anything else is opaque and **must round-trip**.

---

## A. CANONICAL-STORE STRATEGY — DECISION

### Chosen: **(iii) Vault-canonical `.md` + per-note JSON sidecar for non-frontmatter keys**

The vault `.md` is the **single source of truth** for human-readable memory. Every note maps the six native vault frontmatter keys (`id/ts/projectId/kind/role/source` + `tags`) to in-memory fields so app notes look native beside the 721 human/agent notes. The in-memory-only keys that have **no native vault home** and **active live consumers** — `uses`, `pinned`, `session_id`, `owner`, `metadata`, and any unknown/arbitrary key — are persisted in an **`extra:` frontmatter submap inside the same `.md`** (primary), with a **`.json` sidecar** (`<notename>.bertos.json`) written alongside as a **machine-authoritative mirror** of those same extra keys.

> Naming: "JSON sidecar" = a small `<note>.bertos.json` file next to each app-written `.md`. It is NOT `data/memory.json` (that file is retired as canonical — see §A.4). The sidecar carries only the extra/state keys; the `.md` body + native frontmatter carry the human-meaningful content.

### Why (iii) and not (i) or (ii)

- **Not (i) vault-canonical with everything in frontmatter.** This is *almost* right and is the visible behavior — but "everything in frontmatter" alone is risky for two live keys: `uses` (mutated by `increment_uses`, read for analytics) and `pinned` (gates `chat_processor.py:208` injection). A human editing a 700-note vault in Obsidian can trivially reflow/delete a frontmatter line they don't recognize (`x-uses: 3`), silently regressing usage tracking or pinned injection. Frontmatter is the human's editing surface; burying machine-state there invites human corruption. We DO still write these to frontmatter under `extra:` for native visibility and single-file portability — but we do **not** trust frontmatter alone for state.
- **Not (ii) dual-write with `memory.json` canonical.** This keeps `data/memory.json` as truth and mirrors `.md` to the vault. It **violates the core requirement** — "human-editable markdown as source of truth." Under (ii), a human editing a note in Obsidian would have their edit silently overwritten on the next app save from JSON. That is the exact failure mode Slice 2 exists to eliminate. Rejected.
- **Why (iii) is the safe superset.** The `.md` is canonical and human-editable (requirement met). The `extra:` frontmatter block keeps state visible and portable in the single file. The `.bertos.json` sidecar is the **machine read-priority** copy: if a human mangles `extra:` in the `.md`, the loader falls back to the sidecar and the entry survives losslessly. On read we **union** native frontmatter ← `extra:` ← sidecar (sidecar wins on conflict for machine-state keys; `.md` body always wins for `text`). This makes the store **fully round-trippable and no-data-loss** even under hostile human editing — the Phase-1 non-negotiable.

### A.4 Migration of existing `data/memory.json`

1. On `MemoryManager.__init__`, if `data/memory.json` is non-empty and is a JSON list, iterate entries and `save()` each into the vault via the normal write path (§B/§C), then rename the old file to `data/memory.json.migrated-<ISO>` (do not delete — keep the safety copy).
2. On this machine the file is `[]` → migration loop runs 0 times → instant no-op. Verified.
3. `self.memory_file` **attribute is preserved** and continues to point at `data/memory.json` (kept as an empty stub) — `memory_extractor.py:29` derives `os.path.dirname(memory_manager.memory_file)` for its tidy-state sidecar; removing the attribute throws `AttributeError`. The file need not be the store of truth; the **path** must exist.
4. `_migrate_from_legacy` (`memory.txt` → entries) is retained and, on hit, feeds the same vault-write path.

---

## B. EXACT vault `.md` schema (every in-memory key mapped)

App-written notes use **double-quoted scalars** and **block-sequence `tags`** to match the existing 721 notes byte-for-byte in style.

### B.1 Field mapping table

| in-memory key | vault home | type on disk | notes |
|---|---|---|---|
| `id` | `id:` (frontmatter, native) | quoted string | App writes a **uuid4** (NOT a slug) so the Chroma `search→by_id` join (`memory_provider.py:177`) stays stable. Slug-ids are tolerated on read but never minted by the app. Also encoded in filename hash. |
| `text` | **note body** (everything after the closing `---`) | markdown | The human-meaningful content. Body is canonical for `text`. See §D for the empty-body guard. |
| `timestamp` (int epoch) | `ts:` (frontmatter, native) | quoted ISO-8601 `...Z` | Write: `datetime.utcfromtimestamp(ts).isoformat()+"Z"`. Read: parse ISO → `int(epoch)`; tolerate the one `+00:00` offset spelling. |
| `source` | `source:` (frontmatter, native) | quoted string | Default app `source: "odysseus"`. Optional on read (absent ⇔ `role: user` in human notes) → default `""`/`"user"` per §D. |
| `category` | `kind:` (frontmatter, native) | quoted string | **Field-name divergence is intentional**: app `category` ↔ vault `kind`. Round-trip is exact (`category`→`kind` on write, `kind`→`category` on read). Default `"fact"`. |
| `owner` | `extra.owner` (+ sidecar) | quoted string | Always **absent/None** on this single-user box; preserved for multi-user safety. Never written to a native key. `None`/absent ⇒ ownerless. |
| `uses` | `extra.uses` (+ sidecar) | integer | Live: `increment_uses`. Must round-trip or analytics die. |
| `session_id` | `extra.session_id` (+ sidecar) | quoted string / null | **Query key** (session-scoped listing). Must round-trip. |
| `pinned` | `extra.pinned` (+ sidecar) | bool | Gates `chat_processor.py:208` injection. Must round-trip. |
| `metadata` | `extra.metadata` (+ sidecar) | nested map | Opaque blob, core field. |
| **unknown / arbitrary keys** | `extra.<key>` (+ sidecar) | any JSON | Backup-import dicts (`backup_routes.py:79-98`) append arbitrary keys. **Every** non-`_CORE_FIELDS` key is dropped into `extra:` and the sidecar verbatim. |
| `role` | `role:` (frontmatter, native) | quoted string | Vault-native, no in-memory equivalent. Write `role: "system"` for app memories (matches existing `local/` notes); preserved on read into `extra.role` so an edit→save round-trip doesn't lose a human-set role. |
| `tags` | `tags:` (frontmatter, native) | block sequence | No in-memory equivalent. Write `["bertos","memory"]` default; preserve any existing on read into `extra.tags`. Optional (1/721 notes omit it) → default `[]`. |

### B.2 Lossless preservation of unknown/arbitrary keys

- On **write**, the writer computes `extra = {k: v for k, v in entry.items() if k not in NATIVE_MAPPED}` where `NATIVE_MAPPED = {"id","text","timestamp","source","category"}` (the five with native homes; `text`→body, the other four→native frontmatter). **Everything else** — `owner, uses, session_id, pinned, metadata, role, tags`, and any unrecognized key — goes into the `extra:` frontmatter submap **and** into the `.bertos.json` sidecar.
- On **read**, the loader reconstructs the entry as: native-mapped keys from frontmatter/body, then `entry.update(extra_from_md)`, then `entry.update(sidecar_json)` — **sidecar last so machine-state wins** if a human corrupted `extra:` in the `.md`. The `text` body always overrides any stray `text` key.
- Result: a key the app has never heard of survives `save → load_all` unchanged. This is asserted by the round-trip test (§E).

### B.3 Example app-written note

`Memory/local/fact--phase1-vault-probe--a1b2c3d4.md`
```yaml
---
id: "a1b2c3d4-1111-2222-3333-444455556666"
ts: "2026-06-08T18:30:00.000Z"
projectId: "local"
kind: "fact"
role: "system"
source: "odysseus"
tags:
  - "bertos"
  - "memory"
extra:
  uses: 3
  pinned: true
  session_id: "sess-7f2a"
  owner: null
  metadata: {}
  customKey: "survives-roundtrip"
---
phase1 vault probe
```
Sidecar `Memory/local/fact--phase1-vault-probe--a1b2c3d4.bertos.json`:
```json
{"uses":3,"pinned":true,"session_id":"sess-7f2a","owner":null,"metadata":{},"customKey":"survives-roundtrip"}
```

---

## C. owner ↔ projectId mapping + exact write folder

- **owner and projectId are orthogonal.** `owner` = auth tenant (always `None` here); `projectId` = vault topic partition. There is **no `owner→projectId` derivation** — owner is `None`, which would yield a literal `None`/empty folder. Rejected.
- **App memories write to a fixed partition: `Memory/local/`, stamped `projectId: "local"`.**
  - Matches the established convention: `local/` already holds locally-originated, single-tenant, non-GitHub facts (`projectId: "local"`, `role: "system"`, `source: "local-repo"`). App memories are the same shape.
  - `global/` is the cross-project broadcast tier (`source: "hermes"`) — wrong home. UUID folders are real remote projectIds — none apply to a single-user local write.
- **Mapping rule (forward):** `owner → projectId` = constant `"local"` (ignore owner value). If a future multi-user mode lands, switch to `projectId = owner or "local"` behind a flag — out of scope for Slice 2.
- **Mapping rule (reverse, on read):** partition folder name = `projectId`; it is **not** mapped back to `owner` (owner stays in `extra`/sidecar). `load(owner=None)` returns all partitions unfiltered (the bypass fast path); `load(owner=X)` filters on the reconstructed `extra.owner`, preserving `memory.py:134` semantics exactly.
- **Read scope:** `load_all()` scans **all 34 partitions** (the operator sees everything, matching today's unfiltered `memory.json`). Writes only ever land in `local/`. The 721 human/agent notes become readable app memories automatically.
- **Env knob:** `MEMORY_VAULT_DIR = os.getenv("BERTOS_OBSIDIAN_VAULT", "~/Documents/BertOS-Vault")` added in `src/constants.py` near line 17/44. **Do NOT mirror into `core/constants.py`** (it is a `from src.constants import *` shim — mirroring re-creates the drift it exists to kill). App memory root = `<MEMORY_VAULT_DIR>/Memory`; write folder = `<root>/local`.

---

## D. load()/load_all() guarantee: every returned entry has a string `text`

The fragility: `find_duplicates` (`memory.py:253`) and `get_relevant_memories` (`memory.py:267+`) and many callers use **bracket `entry["text"]`**, not `.get`. `_validate_entries` does **not** backfill `text`. So the vault loader must guarantee a bracketable string `text` on **every** returned entry, across a 700+ note human-edited vault.

### D.1 Body extraction → `text` (the guarantee)

For each `.md`:
1. Split frontmatter (between the first `---` and its closing `---`) from body. The research confirms **all 721 notes start with `---`** and **all have a non-empty body line** — but the loader must not assume this for *future* human edits.
2. `text = body.strip()`.
3. **Empty-body guard (body-only / frontmatter-only / malformed):**
   - If body is empty/whitespace-only → `text = <a non-empty fallback>` derived in priority order: (a) frontmatter `title:` if present (none today, but tolerated), else (b) the filename slug humanized, else (c) the literal string `"(empty memory)"`. **Never `None`, never missing.**
   - If the file has **no frontmatter block** (no leading `---`) → treat the **entire file content** as body → `text`; synthesize a minimal entry (`id` from filename hash or fresh uuid4, `timestamp` from file mtime, `category="fact"`, `source=""`).
   - If the file is **frontmatter-only** (closing `---` then nothing) → empty-body guard above fires.
   - If YAML fails to parse (malformed human edit) → **do not crash the whole load**. Log-and-skip is unacceptable (data loss); instead salvage: `id`=filename-hash, `text`=raw file minus the `---` fences, all other keys defaulted. The entry is still returned with a bracketable `text`.
4. Final assertion in the loader: `assert isinstance(entry["text"], str)` before the entry is appended. This is the invariant `find_duplicates`/`get_relevant_memories` rely on.

### D.2 Defaults for optional/absent native keys (per research)

- `source` absent (109 human `role: user` notes) → `source = ""` (or `"user"`); never KeyError.
- `tags` absent (1 note) → `[]`.
- `kind`/`ts`/`projectId`/`id` are 721/721 present, but defaulted anyway (`kind→category` default `"fact"`; `ts` parse failure → file mtime; `id` absent → filename-hash uuid) for forward-safety.
- `ts` two offset spellings (`Z` and the single `+00:00`) both parse to `int` epoch.

### D.3 Performance / robustness on 700+ notes

- Loader is a single directory walk; YAML parse per file in a `try/except` that salvages rather than aborts (§D.1.4). One malformed human note must never blank out the other 720.
- Reads are tolerant; writes are strict (always emit the canonical shape in §B). This keeps app-written notes pristine while accepting the messy long tail of human notes.

---

## E. NON-NEGOTIABLE ROUND-TRIP TEST design

Three layers. All must pass before Slice 2 is "done."

### E.1 Unit round-trip (no app) — the lossless-state proof

Add to `tests/test_memory_roundtrip.py` (new), run via `./venv/bin/pytest tests/test_memory_imports.py tests/test_claim_ownerless_json.py tests/test_memory_roundtrip.py -v`:

```python
def test_full_key_roundtrip(tmp_vault):
    mm = MemoryManager(data_dir=tmp_data)          # MEMORY_VAULT_DIR -> tmp_vault
    e = mm.add_entry("roundtrip probe", source="odysseus", category="fact")
    e["pinned"] = True
    e["session_id"] = "sess-xyz"
    e["uses"] = 7
    e["customKey"] = {"nested": [1, 2, 3]}          # arbitrary/unknown key
    e["metadata"] = {"k": "v"}
    mm.save([e])

    out = mm.load_all()
    assert len(out) == 1
    r = out[0]
    assert r["text"] == "roundtrip probe"            # body canonical
    assert isinstance(r["text"], str)                # bracket-access invariant (§D)
    assert r["pinned"] is True                       # frontmatter+sidecar survived
    assert r["session_id"] == "sess-xyz"             # query key survived
    assert r["uses"] == 7                            # analytics key survived
    assert r["customKey"] == {"nested": [1, 2, 3]}   # UNKNOWN key survived losslessly
    assert r["metadata"] == {"k": "v"}
    assert r["id"] == e["id"]                         # id stable (Chroma join)
```

Plus a **hostile-edit** sub-test: after `save`, corrupt the `extra:` block in the `.md` on disk (delete the `pinned` line), then `load_all()` → assert `pinned is True` still (proves the sidecar fallback in §A/§B.2). And a **bracket-access** sub-test: write a body-only `.md` with no `text` frontmatter and call `find_duplicates("x")` → no KeyError (proves §D guarantee).

`claim_ownerless` and the import contracts (`tests/test_memory_imports.py`, `tests/test_claim_ownerless_json.py`) must stay green — they exercise the preserved method surface.

### E.2 Live curl round-trip (app running, real vault)

App on the Phase-1 port (`PHASE1-PLAN.md` uses `127.0.0.1:7860`):
```bash
# write
curl -s -X POST http://127.0.0.1:7860/api/memory \
  -H 'Content-Type: application/json' \
  -d '{"text":"phase1 vault probe","category":"fact"}'                 # expect 200
# read back
curl -s 'http://127.0.0.1:7860/api/memory' | python3 -c \
  'import sys,json;d=json.load(sys.stdin);print([m for m in d if m.get("text")=="phase1 vault probe"][0])'
# assert: id present, text intact, AND a real .md now exists in Memory/local/
ls ~/Documents/BertOS-Vault/Memory/local/*phase1-vault-probe*.md
# pin it, re-fetch, assert pinned survived the round-trip
ID=...; curl -s -X POST http://127.0.0.1:7860/api/memory/$ID/pin
curl -s "http://127.0.0.1:7860/api/memory" | grep -q '"pinned": *true'   # or assert in py
```

### E.3 Restart-persistence check (proves vault, not in-memory)

After E.2: **kill and restart uvicorn**, then re-fetch `GET /api/memory` and assert the probe entry **and its `pinned:true`** are still present. Because nothing is held in process memory and `data/memory.json` is empty, a surviving `pinned` probe proves it was reconstructed from the vault `.md` + sidecar — the actual definition of "vault is the source of truth."

**Definition of Done (Slice 2):** creating a memory writes a real `.md` to `Memory/local/`; every custom/`pinned`/`session_id`/`uses`/`metadata` key survives `save → load → restart → load`; the hostile-edit and body-only guards hold; `pytest tests/test_memory_imports.py tests/test_claim_ownerless_json.py tests/test_memory_roundtrip.py` is green.

---

## F. Out-of-band consumers to reconcile (carry-over, not blocking the design)

`scripts/claim_ownerless.py:40`, `core/database.py:1160-1175`, `routes/admin_wipe_routes.py:42-47` (must wipe the vault, not just JSON), `scripts/migrate_faiss_to_chroma.py:69`. Route all through `MemoryManager` or have them read the vault. `app_initializer.py:65` Chroma rebuild relies on `load()` yielding `{id,text}` — satisfied because app ids are uuid4 (§B.1) keeping the `search→by_id` join stable.
