# SLICE 2 — Memory → Vault: APP-OWNED PARTITION Design (v2)

**Status:** design (lead-approved decision record) — **SUPERSEDES `SLICE2-MEMORY-DESIGN.md` (v1) in full.**
**Date:** 2026-06-08
**Scope:** Make a single, app-owned subfolder of the Obsidian vault the durable store for app memory, **without ever reading, moving, rewriting, or deleting any of the ~721 existing human/agent notes** in the other partitions. Lossless round-trip of every in-memory key. No Chroma/recall rewrite here.

> **Why v2 replaces v1.** v1 chose a *vault-canonical, all-partitions* model: `load_all()` scanned all 34 partitions and `save()` wrote the full reconstructed set back. An adversarial review found that this is structurally unsafe — on the first full-set `save()` (which `memory_extractor` performs), the writer would **relocate all 721 human/agent notes into `Memory/local/` and rewrite their `projectId`/`role`/`source`/`tags`** (critic finding **H2**, the worst), and the missing orphan-unlink (**H1**) made deletes silently resurrect. v2 makes those failures *impossible by construction* by giving the app its own partition and never globbing outside it. The JSON-sidecar complexity (v1 §A) is also dropped — see §4.

---

## 0. Ground truth (verified on this machine, not assumed)

- **`bertos` is a free partition name.** `ls Memory/` shows 34 folders (31 are project UUIDs, plus `github`, `global`, `local`); `test -e Memory/bertos` → **does not exist**. We claim it. (Verified 2026-06-08.)
- The vault holds **721 `.md` notes** across those folders — `find Memory -name '*.md' | wc -l` = 721. These are the notes the app must never touch. The partition model means the app's glob radius is **exactly one folder it created**, so the 721 are unreachable.
- `data/memory.json` **is `[]` (2 bytes, 0 entries)** — `wc -c` = 2. Migration is a **verified no-op** in practice; the importer still safely handles a non-empty legacy list (§6).
- **`self.memory_file` is set at `src/memory.py:37`** (`os.path.join(data_dir, "memory.json")`). `services/memory/memory_extractor.py:33` derives `os.path.dirname(memory_manager.memory_file)` for its `memory_tidy_state.json` sidecar. The **attribute must survive** (§6).
- Method surface to preserve (`src/memory.py`): `extract_memory_from_chat`, `process_inline_memory_command`, `ensure_file_exists`, `load_all`, `load(owner)`, `claim_ownerless`, `_validate_entries`, `_migrate_from_legacy`, `save`, `add_entry`, `increment_uses`, `find_duplicates`, `categorize_memory_by_relevance`, `get_relevant_memories`.
- In-memory entry contract (the real shape, from `add_entry`/`_validate_entries`/routes): `_CORE_FIELDS = {id, text, timestamp, source, category, uses, owner, session_id, metadata, pinned}` + any arbitrary key from backup-import. `session_id` is a **live query key** (`routes/memory_routes.py:138` filters `m.get("session_id") == session_id`).

---

## 1. APP-OWNED PARTITION (the structural guarantee)

- `src/constants.py` gains (near line 17, beside `MEMORY_FILE`):
  ```python
  MEMORY_VAULT_DIR = os.getenv("BERTOS_OBSIDIAN_VAULT", os.path.expanduser("~/Documents/BertOS-Vault"))
  MEMORY_APP_PARTITION = "bertos"                       # app-owned, NEVER collides with the 721 notes
  MEMORY_APP_DIR = os.path.join(MEMORY_VAULT_DIR, "Memory", MEMORY_APP_PARTITION)
  ```
  Do **NOT** mirror these into `core/constants.py` — it is a `from src.constants import *` shim; mirroring re-creates drift.
- **`MEMORY_APP_DIR = <vault>/Memory/bertos/` is the ONE and ONLY folder the app reads or writes.** It is `mkdir -p`'d on `MemoryManager.__init__`. Every glob, read, write, and unlink in this design is rooted at `MEMORY_APP_DIR` and never escapes it. `local/`, `global/`, `github/`, and the 31 UUID partitions are out of the code's reach because no code path ever constructs a path outside `MEMORY_APP_DIR`.

---

## 2. SCOPED I/O — `save()` reconciliation algorithm (eliminates H1 + H2)

`load_all()` / `load()` read **only** `MEMORY_APP_DIR/*.md`. `save(entries)` reconciles **only within `MEMORY_APP_DIR`**. The reconciliation is the heart of the design:

**`save(entries)` — 4 steps, under one flock lock (§5):**
1. **Validate** each entry in `entries` (preserve `src/memory.py:196` semantics: backfill `id`/`timestamp`/`source`/`category`). Compute the **desired id set** `S = {e["id"] for e in entries}`.
2. **Upsert** every entry: for each `e`, serialize to the §4 schema and write it atomically (tmp-file + `os.replace`, §5) to its deterministic path `MEMORY_APP_DIR/<id>.md` (§3). Updates land on the *same* path → no duplicates.
3. **Unlink orphans:** glob `MEMORY_APP_DIR/*.md`, parse each filename's id; for any **partition file whose id ∉ S**, `os.remove` the `.md`. This makes delete real — the v1 no-op (**H1**) is gone.
4. **Unlink orphan state:** remove any per-id state artifact (e.g. a stale `<id>.md.lock` remnant or future `<id>.state`) whose id ∉ S, so deleted memories leave nothing behind.

Because steps 3–4 glob **only** `MEMORY_APP_DIR`, `save()` can never see, move, or rewrite a note in another folder. The first full-set `save()` from `memory_extractor` reconciles `bertos/` against the app's own entry list and leaves the 721 notes byte-for-byte untouched. **H2 is impossible by construction.**

> **Deferred / out-of-scope (honest):** broad *read-only* retrieval across the full vault (surfacing the 721 human/agent notes as searchable context) is a **future enhancement**, not in Slice 2. It would be a separate `read_vault_readonly()` path that **never writes** and is never wired into `save()`. Slice 2 deliberately ships the narrow, safe partition first.

---

## 3. STABLE FILENAMES + IN-PLACE UPDATE (eliminates H3)

- **Filename is deterministic from id:** `MEMORY_APP_DIR/<id>.md`. The app mints `id = uuid4()` in `add_entry` (keeps the Chroma `search→by_id` join stable) and the filename is `f"{id}.md"`. No slug, so no slug↔id drift. Path is recomputed identically on every write → pin/`increment_uses` rewrite the **same file**, never a duplicate. **H3 gone.**
- **On read, stash the real path:** the loader sets `entry["_vault_path"] = <abs path of the .md>`. On write, if `entry.get("_vault_path")` is present and inside `MEMORY_APP_DIR`, write back to exactly that path; else fall back to the canonical `<id>.md`. `_vault_path` is a private, non-persisted key (stripped before serialization). This guarantees an edit→save round-trip rewrites in place.

---

## 4. LOSSLESS ROUND-TRIP — frontmatter-only schema (eliminates H4 + M6)

**Decision: frontmatter-only. No JSON sidecar.** v1 carried a `.bertos.json` machine-mirror to survive hostile human edits of `extra:`. In an **app-owned partition there are effectively no human editors** — Will does not hand-edit `Memory/bertos/`, it is not a topic folder he browses — so the sidecar's only justification evaporates. Frontmatter-only is simpler, is a true single-file unit (clean delete, clean Obsidian render), and eliminates an entire class of `.md`↔sidecar divergence bugs. The text-guarantee + salvage path (below) still makes reads crash-proof.

**Native frontmatter (one consistent app schema — H4 is moot because there are no human notes to preserve here):**

| frontmatter key | in-memory key | type on disk | notes |
|---|---|---|---|
| `id` | `id` | quoted string | uuid4; also the filename stem. |
| `ts` | `timestamp` (int epoch) | quoted ISO-8601 `…Z` | write `datetime.utcfromtimestamp(ts).isoformat()+"Z"`; read ISO→`int(epoch)`, tolerate `+00:00`. |
| `kind` | `category` | quoted string | intentional name divergence; exact round-trip. Default `"fact"`. |
| `source` | `source` | quoted string | default `"bertos"`. |
| `tags` | (constant) | block sequence | always `["bertos","memory"]`; identifies app notes at a glance. |
| `extra:` | everything else | nested map | **all** non-native in-memory keys: `uses, pinned, session_id, owner, metadata`, **and any unknown key**, preserved verbatim. |

- **`session_id` round-trips reliably** inside `extra:` — the app owns this frontmatter, no human reflows it, so the `routes/memory_routes.py:138` session filter stays correct. **M6 gone** (no fragile union, no demotion).
- **Body = `text`.** Write `entry["text"]` as the note body after the closing `---`.
- **`extra` computation on write:** `NATIVE = {"id","text","timestamp","source","category"}`; `extra = {k:v for k,v in entry.items() if k not in NATIVE and k != "_vault_path"}`. On read: native keys from frontmatter/body, then `entry.update(extra)`. A key the app has never seen survives `save→load` unchanged.
- **`text` guarantee (bracket-`entry["text"]` is always safe):** every loaded entry gets a **string** `text` via priority fallbacks — body→ `(empty)` body uses `extra.title`/filename-stem/`"(empty memory)"`; no-frontmatter file → whole file is body; YAML parse failure → **salvage** (`id`=filename stem, `text`=raw file minus `---` fences, defaults for the rest) rather than crash. One malformed note never blanks the partition. Final invariant: `assert isinstance(entry["text"], str)` before append.

**Example — `Memory/bertos/a1b2c3d4-1111-2222-3333-444455556666.md`:**
```yaml
---
id: "a1b2c3d4-1111-2222-3333-444455556666"
ts: "2026-06-08T18:30:00.000Z"
kind: "fact"
source: "bertos"
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

---

## 5. ATOMIC + SAFE (eliminates M5)

- **Per-file atomic write:** each `.md` is written to `MEMORY_APP_DIR/.<id>.md.tmp` then `os.replace`'d onto `<id>.md` — same atomicity guarantee the old single-file `os.replace` gave, now per note.
- **Serialized `save()`:** wrap the whole 4-step reconciliation in an `flock` on `MEMORY_APP_DIR/.bertos.lock` (`fcntl.flock(LOCK_EX)`). This closes the `increment_uses`-vs-POST race: a concurrent `increment_uses` (load→bump→save) and an API `add`/`delete` can no longer interleave their reconciliations and clobber each other. Lock is partition-local.

---

## 6. PRESERVED SURFACE + MIGRATION

- **`self.memory_file` attribute is kept** (`src/memory.py:37`), still `os.path.join(data_dir, "memory.json")`, with the file kept as an empty `[]` stub. `memory_extractor.py:33`’s `os.path.dirname(self.memory_file)` keeps resolving `memory_tidy_state.json` — no `AttributeError`. The **path** must exist; it is no longer the store of truth.
- **All 14 methods retained.** `load`/`load_all`/`save`/`add_entry`/`increment_uses`/`claim_ownerless`/`_validate_entries`/`find_duplicates`/`get_relevant_memories`/etc. keep their signatures; only their bodies switch to the `MEMORY_APP_DIR` partition I/O. `load(owner)` filters on reconstructed `extra.owner` exactly as `src/memory.py:134` does.
- **Migration of `data/memory.json`:** on `__init__`, if it is a **non-empty** JSON list, `save()` each entry into `MEMORY_APP_DIR` via the normal write path, then rename to `memory.json.migrated-<ISO>` (keep the safety copy). On this machine it is `[]` → loop runs 0 times → **verified no-op**. `_migrate_from_legacy` (`memory.txt`) likewise feeds the same partition path.

---

## 7. SAFETY — backup + blast radius

- **One-time backup before first write** (run once, manually, gate the first deploy on it):
  ```bash
  tar -czf ~/Documents/BertOS-Vault/Memory.backup-$(date +%Y%m%dT%H%M%SZ).tgz \
      -C ~/Documents/BertOS-Vault Memory
  ```
- **Blast radius is the partition only.** Because every read/write/unlink is rooted at `MEMORY_APP_DIR` (= `Memory/bertos/`), the worst case for any bug — including a buggy reconcile — is limited to files the app itself created in `bertos/`. The 721 human/agent notes in the other 34 folders are outside every code path and cannot be read, moved, rewritten, or deleted.

---

## 8. v1 HIGH/MEDIUM findings — eliminated by construction

- **H1 (delete no-op):** `save()` step 3 unlinks app-partition orphans (id ∉ desired set) + step 4 their state. Delete is now real.
- **H2 (relocate/rewrite all 721 notes — worst):** the app reads/writes/unlinks **only** `Memory/bertos/`; it never globs or constructs a path outside its own partition, so it literally cannot load, move, rewrite, or delete any of the 721 notes.
- **H3 (slug duplicate on pin/increment):** filenames are `<id>.md` (deterministic from uuid4) + write-back to `entry["_vault_path"]`; same id → same file, no duplicate.
- **H4 (role/tags/projectId round-trip contradiction):** the partition has no human notes to preserve, so one consistent app schema (no `projectId`/`role`, constant `tags`) is used — the contradiction is moot.
- **M5 (non-atomic, no lock):** per-file tmp + `os.replace`, whole `save()` under an `flock` lockfile.
- **M6 (session_id demoted to fragile union):** the app owns its own frontmatter; `session_id` round-trips reliably in `extra:`, keeping the `memory_routes.py:138` filter correct.


---

## Re-critique — implementation MUST-FIXES (fold in before/while coding)

> The v2 NO-TOUCH (cross-partition) guarantee was confirmed sound. These are intra-partition data-loss holes the orphan-unlink step introduces; each has a fix. Implement with these baked in + the round-trip/hostile-edit/restart tests.

I now have a complete and verified picture. Every `save()` caller passes a **full** entry set EXCEPT one critical case I need to flag in `audit_memories`. Let me confirm the one genuinely dangerous case: `audit_memories` reconstructs the full set via `load_all()`, but when `owner=None` it saves only `final_entries` (this owner's audited slice) — under the orphan-unlink design, that means everything else gets unlinked. Let me re-verify that branch precisely.

I have all the evidence. The `audit_memories` `owner=None` branch (line 637-638) sets `saved_entries = final_entries` — only the audited entries, NOT a full reconstruction. Under v1 (JSON full-rewrite) this was already a latent bug, but under v2's orphan-unlink it becomes a **data-loss event within the partition**. That's my key NO-DATA-LOSS finding. Let me write up the complete adversarial review.

Based on my trace of the design against the real code and vault, here is the adversarial review.

---

# ADVERSARIAL RE-REVIEW — SLICE2-MEMORY-DESIGN-v2.md

**Verdict: The NO-TOUCH guarantee (Guarantee 1) holds by construction and is genuinely robust. The NO-DATA-LOSS guarantee (Guarantee 2) has one HIGH hole and two MEDIUM holes that the design's own orphan-unlink step *introduces*.** Ground truth re-verified: 721 notes, 33 partitions, `Memory/bertos` does not exist, no non-`.md` canary files, `data/memory.json` is `[]`. Vault root has a stray `README.md` but it is **outside** `Memory/`, so the partition glob never sees it — good.

---

## GUARANTEE 1 — NO-TOUCH (cross-partition): HOLDS

I traced every `save()` / `load_all()` / `load()` caller. **All write paths reconstruct from `load_all()`**, which the design re-roots to `MEMORY_APP_DIR/*.md` only:
- `memory_extractor.extract_and_store` (load_all→append→save), `audit_memories` (load_all merge-back), `memory_routes` pin/update/delete/add, `mcp_servers/memory_server` add/edit/delete, `memory_provider` remember/delete, `backup_routes` import, `memory.py::claim_ownerless`, `builtin_actions`. None construct a vault path themselves; they all go through `MemoryManager`. Since every glob/read/write/unlink is rooted at `MEMORY_APP_DIR` and ids are `uuid4()` (no `/`, `..`, or separators — confirmed no user/LLM-supplied id reaches the filename), **no caller can pass an entry that escapes the partition.** H2 is genuinely impossible by construction. This guarantee is sound.

One LOW caveat below (admin_wipe), but it is a *stale-data* problem, not a cross-partition touch.

---

## GUARANTEE 2 — NO-DATA-LOSS within partition: HAS A HIGH HOLE

### HIGH — `audit_memories(owner=None)` + orphan-unlink = mass deletion of the partition
`services/memory/memory_extractor.py:637-638`: when `owner` is falsy, `saved_entries = final_entries` — i.e. **only the LLM-returned audited slice**, NOT a full reconstruction. Under v1 this overwrote `memory.json` (already a bug). Under v2 §2 step 3, `save()` will additionally **`os.remove` every `bertos/*.md` whose id ∉ final_entries** — so any entry the audit LLM dropped/merged, plus any entry added concurrently, is now *unlinked from disk*, not just absent from a list. The audit's own "unsafe_removal" guard (line 620) only fires at >50% shrink and `before>=8`; a 40% silent cull sails through and is now permanent. The design's claim that orphan-unlink "makes delete real" is correct — but it also makes *every accidental omission* a real delete. **This is the single most dangerous interaction the design does not address.**
**Fix:** In `save()`, gate step-3 orphan-unlink behind an explicit `delete_orphans=True` kwarg that only the true delete paths pass; audit/extract/import pass `False` (upsert-only). Or: make `audit_memories` always reconstruct the full set (`saved_entries = final_entries + everyone_else`) for the `owner=None` branch too.

### MEDIUM — concurrent add lost to orphan-unlink despite the flock
The flock (§5) serializes the *reconciliation*, but each caller's sequence is `load_all()` → mutate in Python → `save()`, and the lock is only held *inside* `save()`. Two overlapping requests both `load_all()` the same set; request A adds id X and saves; request B (which loaded before X existed) saves its set without X → step-3 unlinks `X.md`. The lock prevents torn writes but **not** the lost-update-then-orphan-delete. v1 had the same lost-update window, but v1 never *deleted* — it just overwrote, so X survived in A's file until B clobbered it the same way; v2 actively `os.remove`s X.
**Fix:** Hold the flock across the whole read-modify-write (expose `with manager.locked(): entries = load_all(); …; save(entries)`), or have `save()` re-read under the lock and union rather than treating absence as delete.

### MEDIUM — `_validate_entries` mutates caller entries; `load(owner)` filter depends on a key the schema buries
`load(owner)` filters on `e.get("owner") == owner` (`memory.py:134`). In v2 `owner` lives inside `extra:` and is reconstructed via `entry.update(extra)` on read — fine *if* read always runs. But `memory_routes` `_verify_memory_owner` (404s on mismatch) and the audit's `other_entries` partition (`memory_extractor.py:631`) both hinge on `owner` being a **top-level** key. The design says §4 `entry.update(extra)` lifts it back to top-level — confirm that lift is unconditional even on the salvage path (malformed note → salvage sets "defaults for the rest"). If a salvaged note gets `owner=None` when the real owner was non-null, `_verify_memory_owner` will 404 the real owner out of their own memory and the audit will mis-bucket it into `other_entries` (cross-tenant leak into the saved set).
**Fix:** State explicitly that salvage **must not invent `owner`** — a note whose frontmatter is unparseable must be quarantined (kept on disk, excluded from the active list) rather than loaded with `owner=None`, so a malformed note can never silently change ownership.

### Round-trip preservation (uses/pinned/session_id/unknown keys): HOLDS, with one assertion gap
The `extra:` map carrying all non-native keys is correct and `session_id` round-trips (M6 genuinely gone). But the in-memory contract includes `metadata` (a nested dict) and arbitrary backup-import keys. YAML serialization of a nested `metadata: {}` and re-parse is lossless **only if** the writer uses a real YAML emitter (not f-string templating). The example in §4 is hand-written YAML; if implemented as string interpolation, a `text`/value containing `:` or a leading `-` in `extra` values will produce invalid YAML that then hits the salvage path on next read.
**Fix:** Mandate `yaml.safe_dump` for the frontmatter block (and `text` in the body, not frontmatter — which the design already does). Add the §4 `assert isinstance(entry["text"], str)` as a real runtime guard, not just prose.

### Atomic write + lock correctness: MOSTLY CORRECT, one gap
Per-file `.tmp` + `os.replace` is atomic per note. But step 3/4 unlink and step 2 upsert are **not atomic as a set** — a crash mid-`save()` leaves the partition in a half-reconciled state (some upserts done, some orphans not yet removed). That's acceptable (next save reconciles), but the lock is an `flock` on `.bertos.lock` inside the partition — confirm `mkdir -p MEMORY_APP_DIR` runs **before** the lockfile is opened on first `__init__`, or the very first `save()` races the very first `mkdir`. Minor, but the design's ordering ("mkdir on `__init__`", lock "on save") leaves it implicit.
**Fix:** Create the lockfile in `__init__` right after `mkdir -p`, and `flock` an already-existing fd.

---

## ADDITIONAL FINDINGS

### LOW (NO-TOUCH-adjacent) — `admin_wipe` "memory" now lies
`routes/admin_wipe_routes.py::_wipe_memory_files()` only blanks `data/memory.json` + `memory_tidy_state.json`. Once the vault partition is the source of truth, "Danger Zone → wipe memory" will **leave every `bertos/*.md` on disk** and the next `load_all()` resurrects them all. Not a cross-partition touch (good — it won't reach into the 721), but it's a silent failure of a user-facing delete.
**Fix:** Add `MEMORY_APP_DIR/*.md` unlink to `_wipe_memory_files()` (scoped to the partition, never the parent `Memory/`).

### LOW — `scripts/claim_ownerless.py` and `core/database.py:1184` still hand-edit `MEMORY_FILE` directly
Both bypass `MemoryManager` and read/write `data/memory.json` as raw JSON. Post-migration that file is a dead `[]` stub, so these become **silent no-ops** (claim-ownerless won't claim vault memories; legacy owner-migration won't touch them). Honest, but the design's §6 "all 14 methods retained" doesn't mention these two out-of-band writers.
**Fix:** Note them as known-stale in §6, or route them through `MemoryManager.claim_ownerless` / `load_all`+`save`.

---

## EMPTY-START / DEFERRAL HONESTY: ADEQUATELY STATED, ONE OMISSION
§2's deferred note correctly and honestly flags that broad read-only retrieval of the 721 notes is out of scope. **But it does not state the user-visible consequence:** on first boot after migration, since `data/memory.json` is `[]` and `bertos/` is empty, the app's memory view starts **completely empty** — every previously-extracted memory the user had is gone from the UI until re-extracted. That's the honest expected behavior (the old store was empty too, `wc -c`=2), so it is *not* a regression here — but the design should say so in one line so it isn't read later as a bug. **Recommend adding:** "First boot shows an empty memory list; this is correct — the prior `memory.json` held 0 entries. No existing memories are lost because there were none."

---

## IF YOU SHIP IT — THE 2 RISKIEST REMAINING ASSUMPTIONS
1. **"Every `save()` caller passes a full set, so orphan-unlink is safe."** False today: `audit_memories(owner=None)` passes a filtered slice (HIGH above). Until step-3 is gated by an explicit delete intent, *any* current or future caller that loads-filtered-then-saves silently becomes a bulk deleter. This is the assumption most likely to cause real data loss.
2. **"No human ever edits `Memory/bertos/`, so frontmatter-only is safe and salvage is rare."** Obsidian *will* index and can reflow/rename these `.md` files (graph view, templater, sync conflicts produce ` (conflicted copy).md`), and a conflicted-copy filename breaks the `<id>.md` ↔ id identity that the whole in-place-update + orphan-unlink scheme depends on. The partition being app-owned does not make it human/Obsidian-inert.

**Files reviewed (all absolute):** `/Users/willlambert/Documents/odysseus/src/memory.py`, `/Users/willlambert/Documents/odysseus/services/memory/memory_extractor.py`, `/Users/willlambert/Documents/odysseus/routes/memory_routes.py`, `/Users/willlambert/Documents/odysseus/routes/backup_routes.py`, `/Users/willlambert/Documents/odysseus/routes/admin_wipe_routes.py`, `/Users/willlambert/Documents/odysseus/src/memory_provider.py`, `/Users/willlambert/Documents/odysseus/mcp_servers/memory_server.py`, `/Users/willlambert/Documents/odysseus/scripts/claim_ownerless.py`, `/Users/willlambert/Documents/odysseus/core/database.py`, `/Users/willlambert/Documents/odysseus/src/builtin_actions.py`, `/Users/willlambert/Documents/odysseus/services/memory/service.py`, `/Users/willlambert/Documents/odysseus/src/constants.py`. Vault verified at `/Users/willlambert/Documents/BertOS-Vault`.