"""Local, bounded repair history. Remembered results are evidence, never authority.

The store contains JSON descriptors, never loads them as code, and needs no document bytes. A retrieved
candidate still needs current wording checks, template binding, planning and independent
verification on every file. ``applicable`` only determines whether trying a candidate
automatically is eligible; it does not approve wording or establish correctness.

Structural verification and advisory visual review are deliberately separate fields.
Negative user feedback permanently blocks that case from automatic reuse. Failed cases
remain searchable so the investigator can avoid repeating an unsuccessful attempt.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Mapping, Optional, Sequence

from . import __version__
from .settings import config_dir

MAX_CASE_BYTES = 128 * 1024
MAX_CASES = 2000
MAX_TEXT = 16 * 1024
MAX_DEPTH = 24
MAX_NODES = 10000
MAX_RESULTS = 100
SCHEMA_VERSION = 2
_PATH_KEYS = {"path", "paths", "source_path", "source_paths", "input_path", "input_paths",
              "output_path", "output_paths", "files", "file_path", "file_paths", "folder",
              "out_dir", "out_root"}
_ABSOLUTE_PATH = re.compile(r"^(?:/|\\\\|[A-Za-z]:[\\/])")
_HASH = re.compile(r"^[0-9a-f]{64}$")
_CASE_FIELDS = {"request", "pattern_id", "failure_code", "candidate", "status", "structural_verified",
                "visual_review", "template_hashes", "goal_fingerprint", "app_version", "usage", "notes"}


class RepairMemoryError(ValueError):
    """Invalid case data, or an unavailable/unreadable local repair database."""


def memory_path() -> Path:
    return config_dir() / "repair-memory.sqlite3"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def normalized_request(request: str) -> str:
    """Only normalize line endings and outer whitespace; wording and case remain exact."""
    return request.replace("\r\n", "\n").replace("\r", "\n").strip()


def _json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError, RecursionError) as exc:
        raise RepairMemoryError("repair data must contain only finite JSON values") from exc


def _bounded(value: Any, *, redact: bool = False) -> Any:
    """Copy ordinary JSON with depth/size limits, dropping explicit local path fields.

    Portable ``template:<sha256>`` references survive. Absolute path values are replaced,
    not reduced to filenames that could accidentally resolve to a different local file.
    Freeform wording is otherwise retained verbatim; callers should pass summaries rather
    than raw tracebacks or document content.
    """
    count = 0

    def walk(obj: Any, depth: int) -> Any:
        nonlocal count
        count += 1
        if count > MAX_NODES or depth > MAX_DEPTH:
            raise RepairMemoryError("repair data is too deeply nested or contains too many values")
        if obj is None or type(obj) is bool or type(obj) is int:
            return obj
        if type(obj) is float:
            if not math.isfinite(obj):
                raise RepairMemoryError("repair data must contain only finite JSON values")
            return obj
        if type(obj) is str:
            if len(obj) > MAX_TEXT:
                raise RepairMemoryError("repair text is too long")
            return "[local path omitted]" if redact and _ABSOLUTE_PATH.match(obj) else obj
        if type(obj) is list:
            return [walk(v, depth + 1) for v in obj]
        if type(obj) is dict:
            out = {}
            for key, val in obj.items():
                if not isinstance(key, str) or len(key) > 256:
                    raise RepairMemoryError("repair JSON object keys must be short strings")
                if not redact or key.casefold() not in _PATH_KEYS:
                    out[key] = walk(val, depth + 1)
            return out
        raise RepairMemoryError("repair data must contain only JSON values")

    out = walk(value, 0)
    try:
        size = len(_json(out).encode("utf-8"))
    except UnicodeError as exc:
        raise RepairMemoryError("repair text must be valid Unicode") from exc
    if size > MAX_CASE_BYTES:
        raise RepairMemoryError("repair data exceeds the local case size limit")
    return out


def goal_fingerprint(goal_spec: Mapping[str, Any]) -> str:
    """Hash the complete goal without changing wording. Bind portable templates first.

    The caller may replace template paths with content hash tokens before calling this.
    This helper never omits request text, literal fills, selectors or assertions.
    """
    if not isinstance(goal_spec, Mapping):
        raise RepairMemoryError("the repair goal must be a JSON object")
    return hashlib.sha256(_json(_bounded(dict(goal_spec))).encode("utf-8")).hexdigest()


def _text(value: Any, name: str, default: str = "") -> str:
    if value is None:
        return default
    if not isinstance(value, str) or len(value) > MAX_TEXT:
        raise RepairMemoryError(f"repair {name} must be text of at most {MAX_TEXT} characters")
    return value


def _templates(value: Any) -> List[str]:
    if value is None:
        return []
    if not isinstance(value, (list, tuple)) or len(value) > 100:
        raise RepairMemoryError("repair template_hashes must be a list of at most 100 hashes")
    if any(not isinstance(v, str) or not v or len(v) > 256 for v in value):
        raise RepairMemoryError("repair template hashes must be nonempty strings")
    return sorted(set(value))


def _case(value: Mapping[str, Any]) -> Dict[str, Any]:
    if not isinstance(value, Mapping):
        raise RepairMemoryError("a repair case must be a JSON object")
    status = value.get("status", "failed")
    if status not in ("success", "failed"):
        raise RepairMemoryError("repair status must be success or failed")
    candidate = value.get("candidate", {})
    visual = value.get("visual_review", {})
    usage = value.get("usage", {})
    if not all(isinstance(v, dict) for v in (candidate, visual, usage)):
        raise RepairMemoryError("repair candidate, visual_review and usage must be JSON objects")
    structural = value.get("structural_verified", False)
    if type(structural) is not bool:
        raise RepairMemoryError("repair structural_verified must be a boolean")
    goal = _text(value.get("goal_fingerprint"), "goal_fingerprint")
    if goal and not _HASH.fullmatch(goal):
        raise RepairMemoryError("repair goal_fingerprint must be a SHA-256 hex digest")
    result = {
        "request": _text(value.get("request"), "request"),
        "pattern_id": _text(value.get("pattern_id"), "pattern_id"),
        "failure_code": _text(value.get("failure_code"), "failure_code"),
        "candidate": _bounded(candidate, redact=True),
        "status": status,
        "structural_verified": structural,
        "visual_review": _bounded(visual, redact=True),
        "template_hashes": _templates(value.get("template_hashes")),
        "goal_fingerprint": goal,
        "app_version": _text(value.get("app_version"), "app_version", __version__),
        "usage": _bounded(usage, redact=True),
        "notes": _text(value.get("notes"), "notes"),
    }
    return _bounded(result)


def _repair_identity(case: Mapping[str, Any]) -> str:
    """One repair under one exact goal/context; feedback follows all its occurrences."""
    identity = {k: case[k] for k in ("pattern_id", "goal_fingerprint", "template_hashes", "app_version", "candidate")}
    identity["request"] = normalized_request(case["request"])
    return hashlib.sha256(_json(identity).encode("utf-8")).hexdigest()


def applicable(case: Mapping[str, Any], *, request: str, pattern_id: str, goal_fingerprint: str,
               template_hashes: Sequence[str] = (), app_version: str = __version__) -> bool:
    """Conservative eligibility for trying a remembered candidate, never authorization.

    Callers MUST validate the candidate against the current user's approved wording and
    templates, and execute with fresh per-file verification. Similarity is insufficient.
    Advisory visual review never upgrades an unverified case to an eligible one.
    """
    try:
        if not isinstance(case, Mapping) or not _CASE_FIELDS.issubset(case):
            return False
        clean = _case(case)
        feedback = case.get("feedback", [])
        if not isinstance(feedback, list) or any(not isinstance(f, dict) or type(f.get("accepted")) is not bool
                                                 for f in feedback):
            return False
        return bool(
            normalized_request(request) and pattern_id and _HASH.fullmatch(goal_fingerprint)
            and clean["status"] == "success" and clean["structural_verified"] is True
            and clean["candidate"] and case.get("reuse_blocked") is False
            and not any(f["accepted"] is False for f in feedback)
            and normalized_request(clean["request"]) == normalized_request(request)
            and clean["pattern_id"] == pattern_id and clean["goal_fingerprint"] == goal_fingerprint
            and clean["template_hashes"] == _templates(template_hashes)
            and clean["app_version"] == app_version
        )
    except (RepairMemoryError, TypeError, AttributeError):
        return False


class CaseStore:
    """One short SQLite transaction per operation; safe across threads and processes.

    Constructing a store and reading an absent store create no files. A malformed database
    raises ``RepairMemoryError`` and is left untouched. Individual malformed case records
    are skipped on read; ``warnings`` describes skipped records without leaking their data.
    Old cases are pruned at the bounded capacity, and explicit ``forget`` removes a case.
    """

    def __init__(self, path: Optional[Path] = None, *, max_cases: int = MAX_CASES):
        if type(max_cases) is not int or not 1 <= max_cases <= MAX_CASES:
            raise RepairMemoryError(f"max_cases must be between 1 and {MAX_CASES}")
        self.path = Path(path) if path is not None else memory_path()
        self.max_cases = max_cases
        self.warnings: List[str] = []

    @contextmanager
    def _connect(self, *, write: bool = False) -> Iterator[sqlite3.Connection]:
        con = None
        try:
            if write:
                self.path.parent.mkdir(parents=True, exist_ok=True)
            # A read racing with deletion must not recreate an empty database.
            target = str(self.path) if write else self.path.resolve().as_uri() + "?mode=ro"
            con = sqlite3.connect(target, timeout=10, uri=not write)
            con.row_factory = sqlite3.Row
            con.execute("PRAGMA busy_timeout = 10000")
            version = con.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1, SCHEMA_VERSION):
                raise RepairMemoryError("repair memory uses a newer database format")
            if write:
                con.execute("PRAGMA journal_mode = WAL")
                con.execute("PRAGMA secure_delete = ON")
                con.execute("BEGIN IMMEDIATE")
                con.execute("""CREATE TABLE IF NOT EXISTS cases (
                    id TEXT PRIMARY KEY, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    payload TEXT NOT NULL, reuse_blocked INTEGER NOT NULL DEFAULT 0)""")
                con.execute("CREATE TABLE IF NOT EXISTS blocked_repairs (identity TEXT PRIMARY KEY)")
                if version < SCHEMA_VERSION:
                    # Preserve corrections recorded before repair identity was explicit.
                    for row in con.execute("SELECT * FROM cases WHERE reuse_blocked = 1"):
                        case = self._read(row)
                        if case is not None:
                            con.execute("INSERT OR IGNORE INTO blocked_repairs(identity) VALUES (?)",
                                        (_repair_identity(case),))
                    self._propagate_blocks(con)
                con.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            yield con
            if write:
                con.commit()
        except (OSError, sqlite3.Error) as exc:
            if con is not None:
                con.rollback()
            raise RepairMemoryError(f"local repair memory could not be read or saved ({type(exc).__name__})") from exc
        except BaseException:
            if con is not None:
                con.rollback()
            raise
        finally:
            if con is not None:
                con.close()

    @staticmethod
    def _limit(limit: int) -> int:
        if type(limit) is not int or not 1 <= limit <= MAX_RESULTS:
            raise RepairMemoryError(f"repair result limit must be between 1 and {MAX_RESULTS}")
        return limit

    def _read(self, row: sqlite3.Row) -> Optional[Dict[str, Any]]:
        try:
            if not isinstance(row["payload"], str) or len(row["payload"].encode("utf-8")) > MAX_CASE_BYTES:
                raise RepairMemoryError("oversized case")
            value = json.loads(row["payload"])
            if not isinstance(value, dict) or not (_CASE_FIELDS | {"feedback"}).issubset(value):
                raise RepairMemoryError("incomplete case")
            result = _case(value)
            feedback = value.get("feedback", [])
            if not isinstance(feedback, list) or len(feedback) > 50:
                raise RepairMemoryError("invalid feedback")
            for item in feedback:
                if not isinstance(item, dict) or type(item.get("accepted")) is not bool:
                    raise RepairMemoryError("invalid feedback")
                _text(item.get("note"), "feedback note")
                _text(item.get("created_at"), "feedback date")
            if row["reuse_blocked"] not in (0, 1):
                raise RepairMemoryError("invalid reuse state")
            result.update(id=row["id"], created_at=row["created_at"], updated_at=row["updated_at"],
                          feedback=_bounded(feedback), reuse_blocked=bool(row["reuse_blocked"]) or
                          any(f["accepted"] is False for f in feedback))
            return result
        except (ValueError, TypeError, AttributeError, RecursionError):
            self.warnings.append("A malformed local repair case was skipped; it cannot be reused.")
            self.warnings[:] = self.warnings[-100:]
            return None

    def record(self, case: Mapping[str, Any]) -> str:
        value = _case(case)
        value["feedback"] = []
        payload = _json(_bounded(value))
        case_id, now = uuid.uuid4().hex, _now()
        with self._connect(write=True) as con:
            blocked = con.execute("SELECT 1 FROM blocked_repairs WHERE identity = ?",
                                  (_repair_identity(value),)).fetchone() is not None
            con.execute("INSERT INTO cases(id, created_at, updated_at, payload, reuse_blocked) VALUES (?, ?, ?, ?, ?)",
                        (case_id, now, now, payload, int(blocked)))
            con.execute("DELETE FROM cases WHERE id IN (SELECT id FROM cases ORDER BY created_at DESC, id DESC "
                        "LIMIT -1 OFFSET ?)", (self.max_cases,))
            self._prune_blocks(con)
        return case_id

    def _propagate_blocks(self, con: sqlite3.Connection) -> None:
        blocked = {r[0] for r in con.execute("SELECT identity FROM blocked_repairs")}
        ids = []
        for row in con.execute("SELECT * FROM cases WHERE reuse_blocked = 0"):
            value = self._read(row)
            if value is not None and _repair_identity(value) in blocked:
                ids.append((value["id"],))
        con.executemany("UPDATE cases SET reuse_blocked = 1 WHERE id = ?", ids)

    def _prune_blocks(self, con: sqlite3.Connection) -> None:
        """No hidden unbounded history: forgetting/evicting the last occurrence forgets its correction."""
        retained = set()
        for row in con.execute("SELECT * FROM cases WHERE reuse_blocked = 1"):
            value = self._read(row)
            if value is not None:
                retained.add(_repair_identity(value))
        remove = [(r[0],) for r in con.execute("SELECT identity FROM blocked_repairs") if r[0] not in retained]
        con.executemany("DELETE FROM blocked_repairs WHERE identity = ?", remove)

    def get(self, case_id: str) -> Optional[Dict[str, Any]]:
        if not self.path.exists():
            return None
        with self._connect() as con:
            row = con.execute("SELECT * FROM cases WHERE id = ?", (case_id,)).fetchone()
            return self._read(row) if row is not None else None

    def list_cases(self, limit: int = 100) -> List[Dict[str, Any]]:
        limit = self._limit(limit)
        if not self.path.exists():
            return []
        with self._connect() as con:
            rows = con.execute("SELECT * FROM cases ORDER BY updated_at DESC, id DESC LIMIT ?", (MAX_CASES,))
            result = []
            for row in rows:
                value = self._read(row)
                if value is not None:
                    result.append(value)
                    if len(result) == limit:
                        break
            return result

    def search(self, request: str = "", pattern_id: str = "", failure_code: str = "",
               template_hashes: Optional[Sequence[str]] = None, limit: int = 5) -> List[Dict[str, Any]]:
        """Return relevant cases including failures/corrections; similarity confers no trust."""
        limit = self._limit(limit)
        want = normalized_request(_text(request, "request"))
        pattern_id = _text(pattern_id, "pattern_id")
        failure_code = _text(failure_code, "failure_code")
        templates = set(_templates(template_hashes))
        tokens = set(re.findall(r"\w+", want.casefold()))
        if not self.path.exists():
            return []
        hits = []
        with self._connect() as con:
            for row in con.execute("SELECT * FROM cases ORDER BY updated_at DESC, id DESC LIMIT ?", (MAX_CASES,)):
                case = self._read(row)
                if case is None:
                    continue
                other = set(re.findall(r"\w+", case["request"].casefold()))
                score = (12 if want and normalized_request(case["request"]) == want else 0)
                score += 8 if pattern_id and case["pattern_id"] == pattern_id else 0
                score += 6 if failure_code and case["failure_code"] == failure_code else 0
                score += 4 * len(templates.intersection(case["template_hashes"]))
                score += len(tokens & other) / max(1, len(tokens | other))
                if score or not (want or pattern_id or failure_code or templates):
                    hits.append((score, case))
        # Corrections stay visible as warnings to the investigator; automatic eligibility is
        # a separate exact check. Equal relevance preserves newest-first database order.
        hits.sort(key=lambda hit: hit[0], reverse=True)
        return [case for _, case in hits[:limit]]

    def feedback(self, case_id: str, accepted: bool, note: str = "") -> bool:
        if type(accepted) is not bool:
            raise RepairMemoryError("repair feedback accepted must be a boolean")
        note = _text(note, "feedback note")
        if not self.path.exists():
            return False
        with self._connect(write=True) as con:
            row = con.execute("SELECT * FROM cases WHERE id = ?", (case_id,)).fetchone()
            value = self._read(row) if row is not None else None
            if value is None:
                return False
            now = _now()
            value["feedback"] = (value["feedback"] + [{"accepted": accepted, "note": note,
                                                       "created_at": now}])[-50:]
            payload = {key: val for key, val in value.items()
                       if key not in ("id", "created_at", "updated_at", "reuse_blocked")}
            con.execute("UPDATE cases SET payload = ?, updated_at = ?, reuse_blocked = ? WHERE id = ?",
                        (_json(_bounded(payload)), now, int(value["reuse_blocked"] or not accepted), case_id))
            if not accepted:
                con.execute("INSERT OR IGNORE INTO blocked_repairs(identity) VALUES (?)", (_repair_identity(value),))
                self._propagate_blocks(con)
        return True

    def forget(self, case_id: str) -> bool:
        if not self.path.exists():
            return False
        with self._connect(write=True) as con:
            changed = con.execute("DELETE FROM cases WHERE id = ?", (case_id,)).rowcount > 0
            self._prune_blocks(con)
        return changed
