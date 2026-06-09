Critical finding confirmed: The real BertOS-Vault frontmatter has NO `owner`, NO `uses`, NO `pinned`, NO `category` field — it uses `kind` (not `category`), `projectId` (not `owner`), and adds `role`/`source`/`tags` that memory.json doesn't have. The `vault` in the Odysseus repo is Vaultwarden/Bitwarden — completely unrelated to the Obsidian memory vault. There is NO existing Obsidian integration in the repo. This is a significant schema-mismatch risk.

I have everything needed. Here is the deliverable.

---

# Phase-1 Memory→Vault Integration Surface: Recon Notes

**Repo:** `/Users/willlambert/Documents/odysseus` · branch `bertos` (from `dev`) · `src/memory.py` read in full (388 lines) · 730 `.py` files grepped exhaustively.

## VERDICT (top line): "Only reimplement the 5 methods" is a **TRAP**, not safe.

The 5 named methods (`load_all`/`load`/`save`/`add_entry`/`get_relevant_memories`) are necessary but **not sufficient**. A vault-backed `MemoryManager` that implements only those will silently break **at least 6 other methods that real callers invoke**, plus several **out-of-band consumers that bypass `MemoryManager` entirely and read `memory.json` directly**. Worse, the **entry dict schema is the real contract** — callers mutate `entry["pinned"]`, `entry["session_id"]`, `entry["uses"]`, `entry["metadata"]`, `entry["owner"]` directly and round-trip them through `save()`. The real Obsidian vault frontmatter **does not carry those fields**, so a naive `.md`-per-memory store will drop them on every save. Details below.

---

## 1. MemoryManager — FULL public surface (`src/memory.py`)

Constructor: `__init__(self, data_dir)` → sets `self.memory_file = data_dir/memory.json` (L37), calls `ensure_file_exists()`.

| Method | Line | What it does | Return shape / contract |
|---|---|---|---|
| `extract_memory_from_chat(chat_history, session_id=None)` | 40 | Fallback bullet/numbered-line scraper from assistant messages when LLM extraction fails. | `List[Dict]` of `{"text", "timestamp"(int), "session_id"}` — **partial entries, no id/source/category**. Caller: `routes/memory_routes.py:249`. |
| `process_inline_memory_command(message)` | 87 | Regex match `remember/memorize/save/note/store: X`. | `Tuple[bool, str]` = `(is_command, extracted_text)`. Caller: `chat_handler.py:299`. |
| `ensure_file_exists()` | 107 | Creates empty `[]` JSON file. | None. Called in `__init__`. |
| **`load_all()`** | 113 | Reads `memory.json`, runs `_validate_entries`, on JSONDecodeError/PermissionError falls to `_migrate_from_legacy()`. | `List[Dict]` — **unfiltered, all tenants**. Heavily used. |
| **`load(owner=None)`** | 129 | `load_all()` then filters `e.get("owner")==owner`. | `List[Dict]` filtered by owner. |
| `claim_ownerless(owner)` | 136 | Mutates every entry lacking `owner`, sets it, `save()`s. | None (side effect). **No in-app caller** — only the method exists; `scripts/claim_ownerless.py` reimplements this independently against raw JSON (see §4). |
| `_validate_entries(entries)` | 150 | Backfills missing `id`(uuid4)/`timestamp`(int)/`source`("unknown")/`category`("fact")/`uses`(0); drops non-dict rows. | `List[Dict]`. Internal, but defines the **canonical entry shape**. |
| `_migrate_from_legacy()` | 169 | Converts `memory.txt` → entries on corrupt JSON. | `List[Dict]`. Internal. |
| **`save(entries)`** | 196 | Backfills id/timestamp/source/category, atomic write via `.tmp`+`os.replace`. | None. **Persists the WHOLE list (all tenants) every call.** |
| **`add_entry(text, source="user", category="fact", owner=None)`** | 215 | Builds one entry dict. Raises `ValueError` on empty text. **Does NOT persist** — caller must `load_all`/`append`/`save`. | `Dict`: `{id, text, timestamp(int), source, category, uses:0}` + `owner` only if truthy. |
| `increment_uses(ids)` | 232 | `load_all`, bump `uses` for matching ids, `save` if changed. | None. Caller: `chat_processor.py:241`. |
| `find_duplicates(text, entries=None)` | 247 | Exact case-insensitive text match. **Indexes `entry["text"]` with `[]` (KeyError if missing).** | `List[Dict]`. Callers: chat_handler, memory_routes, memory_extractor. |
| `categorize_memory_by_relevance(message, memories)` | 255 | Buckets into contacts/preferences/facts/tasks. **No caller found in repo** (likely dead). | `Dict[str,List]`. |
| **`get_relevant_memories(query, memories, threshold=0.05, max_items=8)`** | 291 | Keyword-type + Jaccard scoring fallback retrieval. Indexes `memory["text"]`. | `List[Dict]` (top-k entries, score stripped). |

**Canonical entry dict (the real contract):** `id`(str uuid), `text`(str), `timestamp`(int epoch — **NOT `ts` ISO**), `source`(str), `category`(str — **NOT `kind`**), `uses`(int), and optionally `owner`(str), `session_id`(str), `pinned`(bool), `metadata`(dict). Note the field-name divergence from the vault (see §5).

---

## 2. EXHAUSTIVE caller surface (every non-test call site)

Instantiation sites (5 live + tests): `src/app_initializer.py:47` (the canonical app instance), `src/builtin_actions.py:83`, `mcp_servers/memory_server.py:36`, `services/memory/service.py:43`. `services/memory/memory.py` is now a **re-export shim** of `src.memory` (git `4dc11cf` "canonicalize memory imports" — the duplicate impl was deleted, so there's ONE class to reimplement, good).

| Caller (file:line) | Method | Behavior/shape relied on — what a reimpl MUST preserve |
|---|---|---|
| `chat_handler.py:299/303/305/307` | `process_inline_memory_command`, `load`, `find_duplicates`, `add_entry`, `save` | "remember: X" flow: load → dedup → add_entry → **append → save** (manual persist). |
| `chat_processor.py:205/241` | `load(owner=)`, `increment_uses` | Reads `m.get("pinned")` to split pinned vs extended; bumps `uses`. **Depends on `pinned` surviving round-trip.** |
| `app_initializer.py:65` | `load()` | Seeds vector index: `existing = memory_manager.load()` then `memory_vector.rebuild(existing)` — needs `entry['id']` + `entry['text']`. (THE Chroma seam, §3.) |
| `ai_interaction.py:964/989-992/1017-1031/1048-1063/1079-1082` | `load(owner=)`, `add_entry`, `load_all`, `save`, `get_relevant_memories` | Agent memory tool: list/add/edit/delete/search. **edit/delete mutate `m["text"]`/`m["timestamp"]` in the load_all list then save** — relies on save persisting in-place mutations to arbitrary fields. |
| `chat_processor` … via `memory_provider.py:149-242` (`NativeMemoryProvider`) | `add_entry`, `load_all`, `save`, `load`, `get_relevant_memories` | `remember()` sets `entry["session_id"]` and `entry["metadata"]` then appends+saves → **needs session_id & metadata to persist**. `delete()` filters by id+owner then `save(remaining)`. |
| `routes/memory_routes.py:73/74/105/106/109/112/114/129/135/143/151/185/249/486/491/500/511/520/533/542` | `load`, `get_relevant_memories`, `find_duplicates`, `add_entry`, `load_all`, `save`, `extract_memory_from_chat` | Full CRUD REST. **`pin_memory`(L487) sets `all_mem[i]["pinned"]`; `update_memory`(L513) sets `["text"]/["category"]/["timestamp"]` then save.** Direct-field mutation through save is the contract. |
| `routes/backup_routes.py:25/79/98` | `load(owner=)`, `load_all`, `save` | Import/export. Import appends arbitrary external dicts (may carry `owner`, any keys) and `save`s the whole store. **Reimpl must not lose unknown keys on save.** |
| `services/memory/memory_extractor.py:29/390/433/440/463/508/629/639` | **`.memory_file`(attr)**, `load_all`, `find_duplicates`, `add_entry`, `save`, `load` | Auto-extraction. **L29 reads `memory_manager.memory_file` to derive the tidy-state sidecar path** — depends on the `memory_file` *attribute* existing, not a method. Cross-tenant dedup logic (L433-463) and audit merge (L629-639) read/mutate `owner`, `pinned`, `session_id`, `category`. |
| `services/memory/service.py:43/113/118/123` | ctor, `load_all`, `save` | `get_all(limit)`, `delete(id)`. |
| `mcp_servers/memory_server.py:36/88/113-116/129/141/154/167/181/183` | ctor, `load`, `add_entry`, `load_all`, `save`, `get_relevant_memories` | MCP tool exposure — **no owner threading** (multi-tenant unaware). Mutates entries in load_all list + save. |
| `src/builtin_actions.py:83/84` | ctor, `load_all` | Memory-consolidation/tidy task; groups by `owner`, AI-rewrites `text`, save. |

**Methods with NO live caller (safe to stub/raise):** `categorize_memory_by_relevance`, and `claim_ownerless` (the in-app method — but see §4 trap).

---

## 3. Chroma-derived-index seam (confirmed)

- **`src/app_initializer.py:63-69`** is the rebuild trigger:
  - L62 `if memory_vector.count() == 0:`
  - L65 `existing = memory_manager.load()` (no owner → all-tenant)
  - L66-67 `if existing: memory_vector.rebuild(existing)`
- `entry['id']` is the vector-store key throughout: `rebuild()` (`memory_vector.py:217-222`, `mem.get("id")` + `mem.get("text")`), `add(memory_id, text)` (L104), `remove(memory_id)` (L122), `search()` returns `{"memory_id", "score", "embedding_lane"}` (L153-159). `find_similar(text, threshold)` returns a `memory_id` (L165).
- **`MemoryVectorStore`** lives in `src/memory_vector.py` (rebuild L188, search L132, count L68, add L104, remove L122, find_similar L165). It's a separate ChromaDB store under `MEMORY_VECTORS_DIR`/`CHROMA_DIR` — **NOT part of MemoryManager**, but the source of truth for vector docs is whatever `load()` returns. Reimpl invariant: `load()` must keep yielding `{id, text}` so rebuild stays correct. **Risk:** if vault uses ISO `ts` for id-stability or regenerates ids, the Chroma ids will drift and `search→by_id` join in `memory_provider.py:177-185` silently returns nothing.
- **Cross-tenant gotcha already baked in** (`memory_extractor.py:433-450`, `memory_provider.py:188`): the single Chroma collection has **no owner metadata**, so `find_similar`/`search` can return another tenant's id; the code re-checks `owner` against the *JSON* entry. A vault reimpl must keep `load()`/`load_all()` returning the `owner` field for this guard to work.

---

## 4. Out-of-band consumers that BYPASS MemoryManager (the real trap)

These read/write `memory.json` **directly** and will **silently diverge** the moment the source of truth moves to the vault — they are invisible if you only look at `MemoryManager` callers:

1. **`scripts/claim_ownerless.py:16,40,44-52`** — opens `MEMORY_FILE` raw, mutates `owner`, writes JSON. Reimplements `claim_ownerless` independently. **Will operate on a stale/empty `memory.json` after the swap.**
2. **`core/database.py:1159-1175`** — legacy migration assigns `owner` to entries in `memory.json` directly.
3. **`routes/admin_wipe_routes.py:40-47`** — blanks `memory.json` + drops `memory_tidy_state.json` sidecar directly (the "wipe memory" endpoint). **Won't wipe the vault.**
4. **`scripts/migrate_faiss_to_chroma.py:66-69`** — reads `MEMORY_FILE` to rebuild vectors.
5. **`services/memory/memory_extractor.py:29`** — `os.path.dirname(memory_manager.memory_file)` to locate `memory_tidy_state.json`. **Depends on the `.memory_file` instance attribute**, not a method. A vault reimpl must still expose a `memory_file` attribute (or this throws `AttributeError`).
6. **`src/config.py:28,163` / `src/constants.py:17`** — `memory_file`/`MEMORY_FILE` path constants other code may reference.

**Conclusion of §4:** the "5 methods" framing ignores (a) 6 other live MemoryManager methods, (b) the `.memory_file` attribute contract, and (c) ≥4 scripts/routes that read `memory.json` directly. Either make the vault writer keep `memory.json` in sync (dual-write) or these break.

---

## 5. VAULT format — and a SCHEMA-MISMATCH WARNING

The real vault at `/Users/willlambert/Documents/BertOS-Vault/` **already exists** with one-`.md`-per-memory under `Memory/<projectId>/`. ProjectId folders observed: `global`, `local`, `github`, `bench-*`, and many UUIDs. 721 `.md` files. Filename pattern: `<kind>--<slug>--<hash8>.md` (or `<kind>--<slug>.md`).

**Actual frontmatter schema (verified across global/local/github/UUID/bench notes):**
```yaml
---
id: "github-source-intelligence-memory-buff"   # string; sometimes a uuid, sometimes a slug
ts: "2026-06-04T00:05:43+00:00"                # ISO-8601 string (also seen "...733Z")
projectId: "global"                             # folder name == projectId
kind: "artifact"                                # fact | decision | artifact (NOT "category")
role: "assistant"                               # assistant | system | user
source: "hermes"                                # hermes | local-repo | ... (OPTIONAL: 30/40 notes had it)
tags:
  - "github"
  - "obsidian"
---
<freeform markdown body == the memory text>
```

**The mismatch (RISK — flag to lead):** the vault frontmatter and the `memory.json` entry dict are **NOT the same schema**:

| memory.json field | vault frontmatter | gap |
|---|---|---|
| `text` | body (after frontmatter) | mapping needed |
| `timestamp` (int epoch) | `ts` (ISO string) | **type + name change** |
| `category` | `kind` | **name change** (and value vocab differs: fact/decision/artifact vs fact/identity/...) |
| `owner` | **absent** — `projectId` is the partition | **owner≠projectId**; auth-owner semantics have no vault home |
| `uses` (int) | **absent** | **lost on round-trip** → `increment_uses` no-ops, `chat_processor` usage tracking dies |
| `pinned` (bool) | **absent** | **lost** → `chat_processor.py:208` pinned-memory injection silently stops working |
| `session_id` | **absent** | **lost** → memory_provider/extractor session linkage dies |
| `source` | `source` (optional, present in ~75%) | partial |
| `id` | `id` | OK, but vault ids are slugs/non-uuid → Chroma id-join risk (§3) |
| — | `role`, `tags` | extra vault-only fields with no memory.json equivalent |

A reimpl that maps `load()` → read `.md` frontmatter must **synthesize** `category` from `kind`, `timestamp` from `ts`, and **invent storage for `owner`/`uses`/`pinned`/`session_id`** (e.g. extra frontmatter keys) or those features regress. **No code in the Odysseus repo currently reads/writes this vault** — `grep OBSIDIAN/BERTOS_/BertOS-Vault` over all 730 files returns nothing; the only "vault" in the repo is **Vaultwarden/Bitwarden** (`routes/vault_routes.py`, `tool_security.py` — unrelated). So `BERTOS_OBSIDIAN_VAULT` is **not yet honored anywhere** and must be added new.

---

## 6. Exact files/lines to change

**Add the constant** — `src/constants.py`, insert near `MEMORY_FILE` (L17) / `CHROMA_DIR` (L44):
```python
MEMORY_VAULT_DIR = os.getenv("BERTOS_OBSIDIAN_VAULT",
    os.path.join(os.path.expanduser("~"), "Documents", "BertOS-Vault"))
# notes live under MEMORY_VAULT_DIR/Memory/<projectId>/<kind>--<slug>--<hash>.md
```
(Mirror in `core/constants.py` if that is a separate live module — confirm; both exist.)

**Reimplement** — `src/memory.py` `MemoryManager`. Minimum to not break the surface above: all of `load_all`, `load`, `save`, `add_entry`, `get_relevant_memories`, **plus** `find_duplicates`, `increment_uses`, `claim_ownerless`, `ensure_file_exists`, `extract_memory_from_chat`, `process_inline_memory_command`, and **keep a `self.memory_file` attribute** (memory_extractor.py:29 needs it). Preserve the entry dict (with owner/uses/pinned/session_id/metadata) as the in-memory return shape regardless of on-disk format.

**Wire owner↔projectId** — decide the mapping (owner is auth-email; projectId is folder). This is a **design decision the master prompt doesn't address** — flag it.

**Reconcile the bypass consumers (§4):** `scripts/claim_ownerless.py`, `core/database.py:1159`, `routes/admin_wipe_routes.py:40`, `scripts/migrate_faiss_to_chroma.py:66`. Either route them through MemoryManager or dual-write.

**Untouched but dependent (verify after):** `src/app_initializer.py:63-67` (Chroma rebuild — must still get `{id,text}`), `src/memory_provider.py` (NativeMemoryProvider — `_CORE_FIELDS` at L102-112 expects id/text/timestamp/source/category/uses/owner/session_id/metadata), `src/memory_vector.py` (unchanged, but id stability matters).

---

## 7. Uncertain / needs lead decision (explicit flags)

- **owner vs projectId mapping is undefined** — the vault has no `owner`; multi-tenant auth in Odysseus keys everything on `owner`. This is the single biggest unresolved design gap.
- **id stability**: vault ids are human slugs (e.g. `github-source-intelligence-memory-buff`), not uuids. Chroma keys on id; `add_entry` generates uuids. Mixing the two will fragment the vector join (`memory_provider.py:177`).
- **`uses`/`pinned`/`session_id` have no vault frontmatter home** — pin-based context injection and usage analytics regress unless you extend the frontmatter.
- **`save(entries)` writes the WHOLE store atomically today.** A vault is N files; "atomic save of all entries" becomes N file writes with no transaction — partial-failure semantics differ. `claim_ownerless`, audit-merge, and import all rely on bulk save.
- I did **not** execute anything (read-only as instructed); the schema claims are from reading actual `.md` files and source, not running the app.