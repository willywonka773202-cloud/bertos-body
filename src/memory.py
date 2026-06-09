
import json
import logging
import os
import time
import uuid
import re
import glob
from typing import List, Dict, Tuple, Optional
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Frontmatter (de)serialization.
#
# The app fully owns the `Memory/bertos/` partition schema (§4 of the v2
# design), so we control every byte we emit. We PREFER PyYAML when it is
# importable (the re-critique mandate: "use a real YAML emitter, not f-string
# templating") and fall back to a small, self-contained safe emitter/parser
# when it is not, so the engine never hard-crashes on a missing optional dep.
# Either way, reads are crash-proof: a YAML parse failure salvages the note
# rather than blanking the partition.
# ---------------------------------------------------------------------------
try:  # pragma: no cover - import availability is environment-specific
    import yaml as _yaml
except Exception:  # pragma: no cover
    _yaml = None


def tokenize(text: str) -> List[str]:
    """Simple tokenizer that splits on whitespace and removes punctuation."""
    return [word.strip('.,!?";') for word in text.split()]


def get_text_similarity(text1: str, text2: str) -> float:
    """Calculate Jaccard similarity between two texts."""
    if not text1 or not text2:
        return 0.0

    tokens1 = set(tokenize(text1.lower()))
    tokens2 = set(tokenize(text2.lower()))

    if not tokens1 and not tokens2:
        return 1.0
    if not tokens1 or not tokens2:
        return 0.0

    intersection = tokens1.intersection(tokens2)
    union = tokens1.union(tokens2)

    return len(intersection) / len(union)


# A memory id is safe as a bare filename stem iff it starts with an
# alphanumeric and contains ONLY [A-Za-z0-9_.-] thereafter (<=128 chars). This
# bans '/', '\\', '..', leading dots and every path separator, so str(mid)+'.md'
# can never escape the partition. Anything else is replaced by a fresh uuid4 hex.
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


def _safe_id(mid) -> str:
    """Return a filesystem-safe id.

    ``str(mid)`` is returned verbatim when it matches ``_SAFE_ID_RE`` (no '/',
    no '..', no separators). Otherwise a fresh ``uuid4().hex`` is returned. This
    is the single source of truth for turning an (attacker-controllable) id into
    a filename stem; callers MUST stamp the returned value back onto the entry so
    the in-memory id and the on-disk filename agree (Chroma/callers key off it).
    """
    s = str(mid) if mid is not None else ""
    if _SAFE_ID_RE.match(s):
        return s
    return uuid.uuid4().hex


# Native frontmatter keys that map to dedicated top-level in-memory keys.
# Everything else in an entry lives in the `extra:` submap and round-trips
# verbatim (uses, pinned, session_id, owner, metadata, and ANY unknown key).
_NATIVE_KEYS = {"id", "text", "timestamp", "source", "category"}
# Private, non-persisted keys stripped before serialization.
_PRIVATE_KEYS = {"_vault_path"}
_DEFAULT_TAGS = ["bertos", "memory"]


def _ts_to_iso(ts) -> str:
    """int epoch -> 'YYYY-MM-DDThh:mm:ss.mmmZ' (UTC). Tolerant of junk."""
    try:
        ts_int = int(ts)
    except (TypeError, ValueError):
        ts_int = int(time.time())
    dt = datetime.fromtimestamp(ts_int, tz=timezone.utc)
    # Match the native vault style (millisecond precision, trailing Z).
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def _iso_to_ts(iso) -> int:
    """ISO-8601 (with 'Z' or '+00:00') -> int epoch. Falls back to now()."""
    if isinstance(iso, (int, float)):
        return int(iso)
    if not isinstance(iso, str) or not iso.strip():
        return int(time.time())
    s = iso.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    except ValueError:
        # Last-ditch: maybe it's a bare epoch string.
        try:
            return int(float(s))
        except ValueError:
            return int(time.time())


# --- Minimal, safe YAML fallback (only used when PyYAML is unavailable) ------

def _fallback_scalar(value) -> str:
    """Serialize a scalar for the fallback emitter. Always double-quoted for
    strings so ':' / leading '-' / '#' can never break the document."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    s = str(value)
    s = s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\t", "\\t")
    return f'"{s}"'


def _fallback_key(key) -> str:
    """Serialize a mapping KEY for the fallback emitter.

    A key containing ':' or '#' (or surrounding whitespace) would otherwise let
    the parser split in the wrong place — so we double-quote it, reusing the
    same escaping as scalar values. Plain keys are emitted bare for readability.
    """
    s = str(key)
    if (":" in s) or ("#" in s) or s != s.strip() or s == "":
        return _fallback_scalar(s)
    return s


def _fallback_dump_value(value, indent: int) -> List[str]:
    """Emit a nested value (dict/list/scalar) for the fallback `extra:` map."""
    pad = "  " * indent
    lines: List[str] = []
    if isinstance(value, dict):
        if not value:
            return ["{}"]  # inline empty map; caller appends on same line
        for k, v in value.items():
            ek = _fallback_key(k)
            if isinstance(v, (dict, list)) and v:
                lines.append(f"{pad}{ek}:")
                lines.extend(_fallback_dump_value(v, indent + 1))
            elif isinstance(v, dict):
                lines.append(f"{pad}{ek}: {{}}")
            elif isinstance(v, list):
                lines.append(f"{pad}{ek}: []")
            else:
                lines.append(f"{pad}{ek}: {_fallback_scalar(v)}")
    elif isinstance(value, list):
        if not value:
            return ["[]"]
        for item in value:
            lines.append(f"{pad}- {_fallback_scalar(item)}")
    else:
        lines.append(f"{pad}{_fallback_scalar(value)}")
    return lines


def _dump_frontmatter(fm: Dict) -> str:
    """Serialize the frontmatter mapping to a YAML block (no fences)."""
    if _yaml is not None:
        # default_flow_style=False -> block style; sort_keys=False -> stable order;
        # allow_unicode=True -> keep emoji/accents readable.
        return _yaml.safe_dump(
            fm, default_flow_style=False, sort_keys=False, allow_unicode=True
        )
    # ---- fallback emitter ----
    lines: List[str] = []
    for key, value in fm.items():
        if key == "tags" and isinstance(value, list):
            lines.append("tags:")
            for tag in value:
                lines.append(f"  - {_fallback_scalar(tag)}")
        elif key == "extra" and isinstance(value, dict):
            if not value:
                lines.append("extra: {}")
            else:
                lines.append("extra:")
                lines.extend(_fallback_dump_value(value, 1))
        elif isinstance(value, dict):
            ek = _fallback_key(key)
            if not value:
                lines.append(f"{ek}: {{}}")
            else:
                lines.append(f"{ek}:")
                lines.extend(_fallback_dump_value(value, 1))
        elif isinstance(value, list):
            ek = _fallback_key(key)
            if not value:
                lines.append(f"{ek}: []")
            else:
                lines.append(f"{ek}:")
                for item in value:
                    lines.append(f"  - {_fallback_scalar(item)}")
        else:
            lines.append(f"{_fallback_key(key)}: {_fallback_scalar(value)}")
    return "\n".join(lines) + "\n"


def _parse_frontmatter(block: str) -> Dict:
    """Parse a frontmatter YAML block into a dict.

    Uses PyYAML when present. The fallback parser handles exactly the shape
    this app emits (top-level scalars, a `tags` sequence, and a nested
    `extra:` map). Raises on anything it cannot parse so the caller can
    trigger the salvage path."""
    if _yaml is not None:
        data = _yaml.safe_load(block)
        if data is None:
            return {}
        if not isinstance(data, dict):
            raise ValueError("frontmatter is not a mapping")
        return data
    return _fallback_parse_frontmatter(block)


def _coerce_scalar(token: str):
    token = token.strip()
    if token == "" :
        return ""
    if token in ("null", "~", "None"):
        return None
    if token in ("true", "True"):
        return True
    if token in ("false", "False"):
        return False
    if token in ("{}", "[]"):
        return {} if token == "{}" else []
    if (token.startswith('"') and token.endswith('"')) or (
        token.startswith("'") and token.endswith("'")
    ):
        inner = token[1:-1]
        inner = inner.replace('\\"', '"').replace("\\n", "\n").replace("\\t", "\t").replace("\\\\", "\\")
        return inner
    # Number?
    try:
        if re.fullmatch(r"-?\d+", token):
            return int(token)
        return float(token)
    except ValueError:
        return token


def _fallback_split_kv(line: str) -> Tuple[str, str]:
    """Split a fallback ``key: value`` line into (key, rest).

    Handles a double/single-QUOTED key verbatim (its closing quote ends the
    key, so a ':' inside the key never mis-splits). For a bare key we split on
    the first ``": "`` (colon-followed-by-space) and fall back to a trailing
    bare colon ("key:") so block values still parse — a colon WITHOUT a
    following space inside a bare key still mis-splits, which is exactly why the
    emitter quotes any key containing ':' . Returns (decoded_key, rest)."""
    s = line.strip()
    if s and s[0] in ("\"", "'"):
        quote = s[0]
        # Find the matching closing quote, honoring backslash escapes.
        i = 1
        while i < len(s):
            ch = s[i]
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                break
            i += 1
        key_token = s[: i + 1] if i < len(s) else s
        key = _coerce_scalar(key_token)
        rest = s[i + 1 :].lstrip()
        if rest.startswith(":"):
            rest = rest[1:].strip()
        return (str(key), rest)
    # Bare key: prefer the first ": " so a ':' inside an unquoted VALUE is safe.
    idx = s.find(": ")
    if idx != -1:
        return (s[:idx].strip(), s[idx + 2 :].strip())
    # Trailing bare colon (block value follows) -> empty rest.
    if s.endswith(":"):
        return (s[:-1].strip(), "")
    key, _, rest = s.partition(":")
    return (key.strip(), rest.strip())


def _fallback_parse_frontmatter(block: str) -> Dict:
    """Indentation-aware parser for the constrained app schema."""
    result: Dict = {}
    lines = block.splitlines()
    i = 0
    n = len(lines)
    while i < n:
        raw = lines[i]
        if not raw.strip():
            i += 1
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        if indent != 0:
            # Stray nested line at top level — skip defensively.
            i += 1
            continue
        line = raw.strip()
        if ":" not in line:
            raise ValueError(f"unparseable frontmatter line: {line!r}")
        key, rest = _fallback_split_kv(line)
        if rest:
            result[key] = _coerce_scalar(rest)
            i += 1
            continue
        # Block value follows on indented lines.
        block_lines = []
        i += 1
        while i < n:
            nxt = lines[i]
            if not nxt.strip():
                block_lines.append(nxt)
                i += 1
                continue
            nxt_indent = len(nxt) - len(nxt.lstrip(" "))
            if nxt_indent == 0:
                break
            block_lines.append(nxt)
            i += 1
        result[key] = _fallback_parse_block(block_lines)
    return result


def _fallback_parse_block(block_lines: List[str]):
    """Parse an indented block as either a sequence or a mapping."""
    meaningful = [ln for ln in block_lines if ln.strip()]
    if not meaningful:
        return {}
    if all(ln.strip().startswith("- ") or ln.strip() == "-" for ln in meaningful):
        seq = []
        for ln in meaningful:
            item = ln.strip()[1:].strip()
            seq.append(_coerce_scalar(item))
        return seq
    # Mapping (one level of nesting is all the app emits in `extra:`).
    base_indent = min(len(ln) - len(ln.lstrip(" ")) for ln in meaningful)
    mapping: Dict = {}
    i = 0
    while i < len(meaningful):
        ln = meaningful[i]
        indent = len(ln) - len(ln.lstrip(" "))
        if indent != base_indent:
            i += 1
            continue
        content = ln.strip()
        key, rest = _fallback_split_kv(content)
        if rest:
            mapping[key] = _coerce_scalar(rest)
            i += 1
        else:
            sub = []
            i += 1
            while i < len(meaningful):
                sub_ln = meaningful[i]
                sub_indent = len(sub_ln) - len(sub_ln.lstrip(" "))
                if sub_indent <= base_indent:
                    break
                sub.append(sub_ln)
                i += 1
            mapping[key] = _fallback_parse_block(sub)
    return mapping


class MemoryManager:
    """Vault-backed memory store.

    The in-memory entry-dict contract is unchanged (id/text/timestamp/source/
    category/uses/owner/session_id/metadata/pinned + arbitrary keys). On disk,
    each entry is a single markdown note at ``MEMORY_APP_DIR/<id>.md`` with the
    §4 frontmatter schema. Every glob/read/write/unlink is rooted at
    ``MEMORY_APP_DIR`` and never escapes it; the ~721 human/agent notes in the
    rest of the vault are structurally unreachable.
    """

    def __init__(self, data_dir: str, app_dir: Optional[str] = None):
        # Preserve the legacy attribute EXACTLY: memory_extractor.py derives
        # os.path.dirname(self.memory_file) for its sidecar. It stays under
        # data_dir as an empty [] stub; it is no longer the store of truth.
        self.memory_file = os.path.join(data_dir, "memory.json")
        self.app_dir = self._resolve_app_dir(data_dir, app_dir)
        self.lock_path = os.path.join(self.app_dir, ".bertos.lock")
        os.makedirs(self.app_dir, exist_ok=True)
        # Create the lockfile right after mkdir so the very first save() never
        # races the very first mkdir (re-critique: atomic-write ordering gap).
        if not os.path.exists(self.lock_path):
            try:
                with open(self.lock_path, "a", encoding="utf-8"):
                    pass
            except OSError:
                pass
        self.ensure_file_exists()
        self._migrate_legacy_json()

    @staticmethod
    def _resolve_app_dir(data_dir: str, app_dir: Optional[str]) -> str:
        """Resolve the app-owned partition directory.

        Precedence (safety-first — tests must auto-isolate to a temp dir and
        NEVER touch the real vault):
          1. explicit ``app_dir`` kwarg,
          2. ``BERTOS_MEMORY_APP_DIR`` env override,
          3. if ``data_dir`` is the real production DATA_DIR -> the real
             vault partition (constants.MEMORY_APP_DIR),
          4. otherwise (a test/temp data_dir) -> ``<data_dir>/Memory/bertos``
             so MemoryManager(tmp_path) is fully isolated.
        """
        if app_dir:
            return os.path.abspath(app_dir)
        env_override = os.environ.get("BERTOS_MEMORY_APP_DIR")
        if env_override:
            return os.path.abspath(env_override)
        # SAFETY: under a pytest session, NEVER resolve to the real vault —
        # even if a test constructs MemoryManager(DATA_DIR) (several existing
        # consolidation tests do). Isolate to a data_dir-derived partition so a
        # test run can never read/write/unlink the ~721 real notes. Production
        # is not under pytest, so it still gets the real vault below.
        in_pytest = (
            "PYTEST_CURRENT_TEST" in os.environ or "pytest" in os.environ.get("_", "")
        )
        if not in_pytest:
            # Read constants LIVE (so tests can monkeypatch) and only point at
            # the real vault when this manager is the production one.
            try:
                from src import constants as _consts

                real_data = os.path.abspath(getattr(_consts, "DATA_DIR", ""))
                if os.path.abspath(data_dir) == real_data:
                    return os.path.abspath(_consts.MEMORY_APP_DIR)
            except Exception:
                pass
        return os.path.abspath(os.path.join(data_dir, "Memory", "bertos"))

    # ------------------------------------------------------------------
    # Locking (serialize the whole save() reconciliation, §5)
    # ------------------------------------------------------------------
    class _FileLock:
        def __init__(self, path: str):
            self.path = path
            self._fd = None

        def __enter__(self):
            try:
                import fcntl

                self._fd = open(self.path, "a+")
                fcntl.flock(self._fd.fileno(), fcntl.LOCK_EX)
            except Exception:
                # On a platform without fcntl (or any lock failure) degrade to
                # a best-effort no-op lock rather than crashing. Atomicity of
                # each individual file still holds via os.replace.
                if self._fd:
                    try:
                        self._fd.close()
                    except Exception:
                        pass
                self._fd = None
            return self

        def __exit__(self, *exc):
            if self._fd is not None:
                try:
                    import fcntl

                    fcntl.flock(self._fd.fileno(), fcntl.LOCK_UN)
                except Exception:
                    pass
                try:
                    self._fd.close()
                except Exception:
                    pass
                self._fd = None
            return False

    def _locked(self):
        return self._FileLock(self.lock_path)

    # ------------------------------------------------------------------
    # Extraction / command helpers (UNCHANGED behavior)
    # ------------------------------------------------------------------
    def extract_memory_from_chat(self, chat_history: List[Dict], session_id: str = None) -> List[Dict]:
        """
        Extract memory entries from chat history as a fallback when LLM fails.
        """
        memories = []

        for msg in chat_history:
            if not isinstance(msg, dict):
                continue
            if msg.get("role") == "assistant":
                content = str(msg.get("content", ""))
                lines = content.split('\n')

                for line in lines:
                    line = line.strip()
                    # Look for bullet points or numbered lists that might contain memories
                    if re.match(r'^[-*•]|\d+\.', line):
                        # Extract the text after the bullet/number. Group both
                        # markers so the capture applies to either — the previous
                        # `^[-*•]|\d+\.\s*(.*)` put the group on the numbered branch
                        # only, so a bullet line matched with group(1)=None and
                        # crashed on .strip().
                        text_match = re.match(r'^(?:[-*•]|\d+\.)\s*(.*)', line)
                        if text_match:
                            text = text_match.group(1).strip()
                            if text:
                                memories.append({
                                    "text": text,
                                    "timestamp": int(datetime.now().timestamp()),
                                    "session_id": session_id
                                })
                    # If we see a heading that suggests memories
                    elif re.search(r'memory|fact|note|remember', line, re.I):
                        pass
                    # If we see a clear separator or end
                    elif re.match(r'^={3,}|-{3,}|_{3,}', line):
                        pass

        return memories

    def process_inline_memory_command(self, message: str) -> Tuple[bool, str]:
        """
        Check if a message is an inline memory command (e.g. "remember: X").
        """
        # Pattern for memory commands: "remember: X", "memorize: X", "save: X", etc.
        pattern = r'^(?:remember|memorize|save|note|store)[:\-]?\s+(.+)$'
        match = re.match(pattern, message.strip(), re.IGNORECASE)

        if match:
            return True, match.group(1).strip()
        else:
            return False, ""

    def ensure_file_exists(self):
        """Ensure the legacy memory.json stub and the app partition both exist.

        The vault partition is the store of truth now; memory.json is kept as
        an empty [] stub purely so self.memory_file resolves and
        memory_extractor's dirname() sidecar keeps working.
        """
        os.makedirs(self.app_dir, exist_ok=True)
        if not os.path.exists(self.memory_file):
            os.makedirs(os.path.dirname(self.memory_file), exist_ok=True)
            with open(self.memory_file, 'w', encoding='utf-8') as f:
                json.dump([], f, ensure_ascii=False, indent=2)

    # ------------------------------------------------------------------
    # Disk <-> entry mapping
    # ------------------------------------------------------------------
    def _entry_to_note(self, entry: Dict) -> str:
        """Serialize an in-memory entry dict to the §4 markdown note."""
        fm = {
            "id": str(entry.get("id", "")),
            "ts": _ts_to_iso(entry.get("timestamp", int(time.time()))),
            "kind": str(entry.get("category", "fact") or "fact"),
            "source": str(entry.get("source", "bertos") or "bertos"),
            "tags": list(_DEFAULT_TAGS),
        }
        extra = {
            k: v
            for k, v in entry.items()
            if k not in _NATIVE_KEYS and k not in _PRIVATE_KEYS
        }
        fm["extra"] = extra  # always present (even if {}) for a stable schema
        body = entry.get("text", "")
        if not isinstance(body, str):
            body = str(body)
        front = _dump_frontmatter(fm).rstrip("\n")
        return f"---\n{front}\n---\n{body}\n"

    def _note_to_entry(self, path: str, raw: str) -> Dict:
        """Parse a markdown note back into an entry dict.

        GUARANTEES a string ``text`` on every returned entry — falls back
        through body -> title -> filename-stem -> "(empty memory)", and
        SALVAGES (never raises) on malformed YAML so one bad note can never
        blank the partition.
        """
        stem = os.path.splitext(os.path.basename(path))[0]
        entry: Dict = {}

        fm_block = ""
        body = raw
        if raw.startswith("---"):
            # Split on the first two '---' fences.
            parts = raw.split("\n")
            if parts and parts[0].strip() == "---":
                closing = None
                for idx in range(1, len(parts)):
                    if parts[idx].strip() == "---":
                        closing = idx
                        break
                if closing is not None:
                    fm_block = "\n".join(parts[1:closing])
                    body = "\n".join(parts[closing + 1:])
                else:
                    # No closing fence — whole thing is body.
                    fm_block = ""
                    body = raw

        fm: Dict = {}
        if fm_block.strip():
            try:
                fm = _parse_frontmatter(fm_block)
                if not isinstance(fm, dict):
                    fm = {}
            except Exception as e:
                # SALVAGE: keep the note, do not crash, do NOT invent owner.
                logger.warning("Salvaging malformed memory note %s: %s", path, e)
                salvaged_text = raw
                # Strip leading/trailing fence lines from the salvaged body.
                salvaged_text = re.sub(r"^---\n", "", salvaged_text)
                salvaged_text = re.sub(r"\n?---\n?", "\n", salvaged_text, count=1)
                entry = {
                    "id": stem,
                    "text": (salvaged_text.strip() or stem or "(empty memory)"),
                    "timestamp": int(time.time()),
                    "source": "bertos",
                    "category": "fact",
                    "uses": 0,
                    "_vault_path": os.path.abspath(path),
                    "_salvaged": True,
                }
                # Salvage must NOT invent an owner (re-critique MEDIUM): leave
                # `owner` absent so the caller's owner filter behaves as if the
                # note has no owner, rather than mis-attributing it.
                return entry

        # Native fields.
        entry["id"] = str(fm.get("id") or stem)
        entry["timestamp"] = _iso_to_ts(fm.get("ts"))
        entry["category"] = fm.get("kind", "fact")
        entry["source"] = fm.get("source", "bertos")

        # Merge extra back to top-level (uses/pinned/session_id/owner/metadata
        # and any unknown key survive verbatim).
        extra = fm.get("extra")
        if isinstance(extra, dict):
            for k, v in extra.items():
                entry[k] = v

        # Body -> text, with the guaranteed-string fallback chain.
        text = body.strip("\n")
        if not text.strip():
            text = (
                (str(extra.get("title")) if isinstance(extra, dict) and extra.get("title") else "")
                or stem
                or "(empty memory)"
            )
        entry["text"] = text if isinstance(text, str) else str(text)

        entry["_vault_path"] = os.path.abspath(path)

        # FINAL invariant — bracket-entry["text"] is always safe.
        if not isinstance(entry["text"], str):
            entry["text"] = str(entry["text"])
        return entry

    # ------------------------------------------------------------------
    # Load
    # ------------------------------------------------------------------
    def load_all(self) -> List[Dict]:
        """Load all memory entries from the app partition (unfiltered).

        Reads ONLY ``MEMORY_APP_DIR/*.md`` — never the other 33 vault folders.
        """
        entries: List[Dict] = []
        try:
            paths = sorted(glob.glob(os.path.join(self.app_dir, "*.md")))
        except OSError as e:
            logger.error("Error globbing memory partition %s: %s", self.app_dir, e)
            return []
        for path in paths:
            try:
                with open(path, "r", encoding="utf-8") as f:
                    raw = f.read()
            except OSError as e:
                logger.warning("Skipping unreadable memory note %s: %s", path, e)
                continue
            try:
                entry = self._note_to_entry(path, raw)
            except Exception as e:  # ultra-defensive: never let one note crash load
                logger.warning("Failed to parse memory note %s, salvaging: %s", path, e)
                stem = os.path.splitext(os.path.basename(path))[0]
                entry = {
                    "id": stem,
                    "text": raw.strip() or stem or "(empty memory)",
                    "timestamp": int(time.time()),
                    "source": "bertos",
                    "category": "fact",
                    "uses": 0,
                    "_vault_path": os.path.abspath(path),
                    "_salvaged": True,
                }
            entries.append(entry)
        return self._validate_entries(entries)

    def load(self, owner: str = None) -> List[Dict]:
        """Load memory entries, optionally filtered by owner."""
        entries = self.load_all()
        if owner is None:
            return entries
        return [e for e in entries if e.get("owner") == owner]

    def claim_ownerless(self, owner: str):
        """Assign all ownerless memory entries to the given owner."""
        entries = self.load_all()
        changed = False
        claimed = 0
        for entry in entries:
            if not entry.get("owner"):
                entry["owner"] = owner
                changed = True
                claimed += 1
        if changed:
            # Upsert-only: we are mutating existing entries, not deleting any.
            self.save(entries)
            logger.info("Claimed %d ownerless memories for %s", claimed, owner)

    # ------------------------------------------------------------------
    # Validate / migrate
    # ------------------------------------------------------------------
    def _validate_entries(self, entries: List[Dict]) -> List[Dict]:
        """Ensure all entries have required fields (incl. a string ``text``)."""
        validated = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            if "id" not in entry:
                entry["id"] = str(uuid.uuid4())
            if "timestamp" not in entry:
                entry["timestamp"] = int(time.time())
            if "source" not in entry:
                entry["source"] = "unknown"
            if "category" not in entry:
                entry["category"] = "fact"
            if "uses" not in entry:
                entry["uses"] = 0
            # Text guarantee: find_duplicates / get_relevant_memories index
            # entry["text"] with brackets — it must always be a string.
            if not isinstance(entry.get("text"), str):
                entry["text"] = str(entry.get("text") or "(empty memory)")
            validated.append(entry)
        return validated

    def _migrate_from_legacy(self) -> List[Dict]:
        """Migrate from old text format (memory.txt) into the app partition."""
        legacy_path = os.path.join(os.path.dirname(self.memory_file), "memory.txt")
        if not os.path.exists(legacy_path):
            return []

        logger.info("Converting legacy memory.txt to vault partition")
        try:
            with open(legacy_path, "r", encoding="utf-8") as f:
                lines = [ln.strip() for ln in f.readlines() if ln.strip()]

            entries = []
            for line in lines:
                entries.append({
                    "id": str(uuid.uuid4()),
                    "text": line,
                    "timestamp": int(time.time()),
                    "source": "user",
                    "category": "fact",
                })

            if entries:
                # Importing legacy data is upsert-only; never delete.
                self.save(entries, delete_orphans=False)
            return entries
        except Exception as e:
            logger.error("Failed to convert legacy memory: %s", e)
            return []

    def _migrate_legacy_json(self) -> None:
        """One-time import of a NON-EMPTY data/memory.json into the partition.

        On this machine memory.json is `[]` -> the loop runs 0 times (verified
        no-op). A non-empty legacy list is upserted into the partition (never
        deletes) and the file is renamed to keep a safety copy.
        """
        try:
            if not os.path.exists(self.memory_file):
                return
            with open(self.memory_file, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            return
        if not isinstance(data, list) or not data:
            return  # empty [] -> verified no-op
        try:
            cleaned = [e for e in data if isinstance(e, dict)]
            if cleaned:
                # Upsert-only import — must NOT delete the partition.
                self.save(cleaned, delete_orphans=False)
            ts = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            os.replace(self.memory_file, self.memory_file + f".migrated-{ts}")
            # Re-create the empty stub so self.memory_file keeps resolving.
            with open(self.memory_file, "w", encoding="utf-8") as f:
                json.dump([], f, ensure_ascii=False, indent=2)
            logger.info("Migrated %d legacy memory.json entries into the vault partition", len(cleaned))
        except Exception as e:
            logger.error("Legacy memory.json migration failed: %s", e)

    # ------------------------------------------------------------------
    # Save — the heart of the design (§2 reconciliation)
    # ------------------------------------------------------------------
    def save(self, entries: List[Dict], delete_orphans: bool = False):
        """Persist ``entries`` to the app partition.

        Upserts every entry atomically (tmp file + os.replace) to its stable
        ``<id>.md`` path. When ``delete_orphans=True`` (ONLY the genuine
        delete paths pass this), any partition file whose id is absent from
        ``entries`` is unlinked — that is what makes delete real. Audit /
        extract / import callers leave it False (default) so an omitted entry
        is treated as upsert-only and is NEVER silently deleted.

        The entire reconciliation runs under an flock on the partition so a
        concurrent increment_uses and an API add/delete cannot clobber.
        """
        # Validate (preserve legacy backfill semantics).
        for entry in entries:
            if "id" not in entry:
                entry["id"] = str(uuid.uuid4())
            if "timestamp" not in entry:
                entry["timestamp"] = int(time.time())
            if "source" not in entry:
                entry["source"] = "user"
            if "category" not in entry:
                entry["category"] = "fact"

        with self._locked():
            os.makedirs(self.app_dir, exist_ok=True)
            desired_ids = set()

            # Step 1+2: upsert every entry.
            for entry in entries:
                # SANITIZE the id before it ever becomes a filename. If the id
                # was unsafe ("../human/precious", "a/b"), _safe_id() mints a
                # fresh uuid; STAMP it back so the in-memory id and the on-disk
                # filename agree (callers/Chroma key off entry["id"]).
                mid = _safe_id(entry.get("id"))
                if entry.get("id") != mid:
                    entry["id"] = mid
                desired_ids.add(mid)
                # Write back to the exact path we read from when present and
                # inside the partition (no duplicates on pin/increment_uses);
                # otherwise the canonical <id>.md.
                target = entry.get("_vault_path")
                if not (
                    target
                    and self._within_partition(target)
                    and os.path.splitext(os.path.basename(target))[0] == mid
                ):
                    target = os.path.join(self.app_dir, f"{mid}.md")
                self._atomic_write(target, self._entry_to_note(entry))

            # Step 3: orphan-unlink — ONLY when explicitly asked.
            # NOTE: delete-by-absence is racy (see MemoryManager.delete for the
            # race-safe locked delete-by-id that genuine delete callers use).
            # Kept for back-compat; every os.remove is fenced to the partition.
            if delete_orphans:
                for path in glob.glob(os.path.join(self.app_dir, "*.md")):
                    file_id = os.path.splitext(os.path.basename(path))[0]
                    if file_id not in desired_ids:
                        if not self._within_partition(path):
                            continue  # fence: never unlink outside the partition
                        try:
                            os.remove(path)
                        except OSError as e:
                            logger.warning("Could not unlink orphan %s: %s", path, e)
                        # Step 4: clean any stale per-id state artifacts.
                        for ext in (".md.tmp", ".tmp"):
                            stale = os.path.join(self.app_dir, f".{file_id}{ext}")
                            if os.path.exists(stale) and self._within_partition(stale):
                                try:
                                    os.remove(stale)
                                except OSError:
                                    pass

    def _within_partition(self, path: str) -> bool:
        """True iff ``path`` resolves strictly inside the app partition."""
        return os.path.abspath(path).startswith(os.path.abspath(self.app_dir) + os.sep)

    def _atomic_write(self, path: str, content: str) -> None:
        """Atomic per-file write: tmp + os.replace, scoped to the partition.

        CENTRAL CONTAINMENT FENCE: this is the single chokepoint through which
        every write flows, so we reject ANY target that resolves outside the
        partition here — regardless of how the id/path was constructed. A crafted
        id ("../human/precious") can never escape Memory/bertos/.
        """
        if not self._within_partition(path):
            raise ValueError(
                f"refusing to write outside memory partition: {path!r} (app_dir={self.app_dir!r})"
            )
        d = os.path.dirname(path)
        os.makedirs(d, exist_ok=True)
        base = os.path.basename(path)
        tmp = os.path.join(d, f".{base}.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)

    def delete(self, ids_to_remove: List[str]) -> int:
        """Race-safe delete-by-id. Returns the number of notes unlinked.

        Unlinks EXACTLY the ``<id>.md`` note for each given id (sanitized via
        _safe_id + the partition fence) and NOTHING else. Unlike
        ``save(delete_orphans=True)``, this never deletes by absence, so a note
        that a concurrent writer added between a caller's ``load_all()`` and this
        call is left untouched (closes the delete TOCTOU). Runs under the same
        flock as save() so it cannot interleave with a concurrent reconcile.
        """
        if not ids_to_remove:
            return 0
        # Sanitize the same way save() does, so we target the on-disk filename
        # the entry was actually written under (callers pass entry["id"]).
        targets = {_safe_id(mid) for mid in ids_to_remove if mid is not None}
        removed = 0
        with self._locked():
            for fid in targets:
                path = os.path.join(self.app_dir, f"{fid}.md")
                if not self._within_partition(path):
                    continue  # fence: never unlink outside the partition
                if os.path.exists(path):
                    try:
                        os.remove(path)
                        removed += 1
                    except OSError as e:
                        logger.warning("Could not unlink memory note %s: %s", path, e)
                # Clean any stale per-id tmp artifacts for this exact id.
                for ext in (".md.tmp", ".tmp"):
                    stale = os.path.join(self.app_dir, f".{fid}{ext}")
                    if os.path.exists(stale) and self._within_partition(stale):
                        try:
                            os.remove(stale)
                        except OSError:
                            pass
        return removed

    # ------------------------------------------------------------------
    # Entry construction / mutation
    # ------------------------------------------------------------------
    def add_entry(self, text: str, source: str = "user", category: str = "fact", owner: str = None) -> Dict:
        """Add a new memory entry (returns the dict; caller persists it)."""
        if not text.strip():
            raise ValueError("Memory text cannot be empty")

        entry = {
            "id": str(uuid.uuid4()),
            "text": text.strip(),
            "timestamp": int(time.time()),
            "source": source,
            "category": category,
            "uses": 0,
        }
        if owner:
            entry["owner"] = owner
        return entry

    def increment_uses(self, ids: List[str]) -> None:
        """Bump the uses counter for each memory id."""
        if not ids:
            return
        id_set = set(ids)
        entries = self.load_all()
        changed = False
        for e in entries:
            if e.get("id") in id_set:
                e["uses"] = int(e.get("uses", 0) or 0) + 1
                changed = True
        if changed:
            # Upsert-only: bumping uses must never delete other entries.
            self.save(entries, delete_orphans=False)

    def find_duplicates(self, text: str, entries: List[Dict] = None) -> List[Dict]:
        """Find duplicate memory entries based on text content."""
        if entries is None:
            entries = self.load()

        text_lower = text.strip().lower()
        return [entry for entry in entries if entry["text"].lower() == text_lower]

    def categorize_memory_by_relevance(self, message: str, memories: list):
        """Categorize memories by type and relevance"""
        categories = {
            "contacts": [],
            "preferences": [],
            "facts": [],
            "tasks": []
        }

        msg_lower = message.lower()

        for mem in memories:
            text_lower = mem["text"].lower()

            # Contact info
            if any(word in text_lower for word in ["phone", "email", "address", "lives", "works"]):
                if any(word in msg_lower for word in ["contact", "phone", "address", "email"]):
                    categories["contacts"].append(mem)

            # Personal preferences
            elif any(word in text_lower for word in ["likes", "dislikes", "prefers", "favorite"]):
                if any(word in msg_lower for word in ["like", "prefer", "favorite", "want"]):
                    categories["preferences"].append(mem)

            # Tasks and todos
            elif any(word in text_lower for word in ["todo", "task", "remind", "meeting"]):
                if any(word in msg_lower for word in ["todo", "task", "schedule", "remind"]):
                    categories["tasks"].append(mem)

            # General facts - only if very relevant
            else:
                if get_text_similarity(message, mem["text"]) > 0.4:
                    categories["facts"].append(mem)

        return categories

    def get_relevant_memories(self, query: str, memories: list, threshold: float = 0.05, max_items: int = 8):
        """Get memories that are relevant to the query based on text similarity and semantic keyword matching."""
        if not memories or not query.strip():
            return []

        # Define keyword categories for semantic matching
        identity_words = ["name", "who", "i", "am", "called", "identity", "myself", "me", "my"]
        contact_words = ["phone", "email", "address", "contact", "number", "where", "located", "reach"]
        preference_words = ["like", "prefer", "favorite", "want", "love", "hate", "dislike", "enjoy", "interested"]
        task_words = ["todo", "task", "remind", "meeting", "appointment", "schedule", "deadline"]
        fact_words = ["what", "when", "where", "how", "why", "explain", "describe", "information", "know"]

        query_lower = query.lower()

        # Determine query type based on keywords
        query_type = None
        if any(word in query_lower for word in identity_words):
            query_type = "identity"
        elif any(word in query_lower for word in contact_words):
            query_type = "contact"
        elif any(word in query_lower for word in preference_words):
            query_type = "preference"
        elif any(word in query_lower for word in task_words):
            query_type = "task"
        elif any(word in query_lower for word in fact_words):
            query_type = "fact"

        relevant = []
        identity_memories = []
        other_memories = []

        # Separate identity memories from others
        for memory in memories:
            memory_text = memory["text"].lower()
            # Check if this is an identity memory (contains name patterns or identity indicators)
            is_identity = any([
                re.search(r'\b[A-Z][a-z]+ [A-Z][a-z]+\b', memory["text"]),
                any(word in memory_text for word in ["name is", "i'm", "i am", "called", "my name", "named", "call me"])
            ])
            if is_identity:
                identity_memories.append(memory)
            else:
                other_memories.append(memory)

        # For identity queries, include all identity memories regardless of similarity
        if query_type == "identity" and identity_memories:
            # Give them high scores to ensure they're included first
            for memory in identity_memories:
                relevant.append((0.9, memory))  # High score for identity memories in identity queries

        # Process other memories with similarity scoring
        for memory in other_memories:
            memory_text = memory["text"].lower()
            memory_tokens = set(tokenize(memory_text))
            query_tokens = set(tokenize(query_lower))

            # Calculate base Jaccard similarity
            if not query_tokens or not memory_tokens:
                continue

            base_similarity = len(query_tokens & memory_tokens) / len(query_tokens | memory_tokens)
            final_score = base_similarity

            # Apply boosts based on semantic matching
            if query_type == "contact":
                # Boost memories with contact information
                has_contact_info = any(word in memory_text for word in ["@gmail.com", "@", ".com",
                                                                     "phone", "number", "address",
                                                                     "http", "www", "tel:"])
                if has_contact_info:
                    final_score *= 1.4  # 40% boost for contact-related memories

            elif query_type == "preference":
                # Boost memories with preference indicators
                has_preference = any(word in memory_text for word in ["like", "love", "hate", "dislike",
                                                                   "prefer", "favorite", "enjoy", "interested"])
                if has_preference:
                    final_score *= 1.3  # 30% boost for preference-related memories

            elif query_type == "task":
                # Boost memories with task indicators
                has_task = any(word in memory_text for word in ["todo", "task", "remind", "meeting",
                                                              "appointment", "schedule", "deadline", "need to"])
                if has_task:
                    final_score *= 1.3  # 30% boost for task-related memories

            # Always consider exact phrase matches as highly relevant
            if query.lower() in memory["text"].lower():
                final_score = max(final_score, 0.8)  # Ensure high relevance for exact matches

            # Include memory if it meets threshold after boosts
            if final_score >= threshold:
                relevant.append((final_score, memory))

        # Sort by final score (descending) and return top matches
        relevant.sort(key=lambda x: x[0], reverse=True)
        return [mem for _, mem in relevant[:max_items]]
