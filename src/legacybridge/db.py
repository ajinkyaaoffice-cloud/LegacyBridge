"""SQLite blackboard: the single source of truth shared by all agents.

Agents never talk to each other; they read and write rows here. Every state transition also
appends to `events`, which the dashboard streams over SSE.

The connection is shared by the asyncio event loop (one thread), and guarded by a lock so the
dashboard (another process) can read concurrently thanks to WAL mode.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from enum import StrEnum
from pathlib import Path
from typing import Any, Iterable


class Status(StrEnum):
    """Lifecycle of a migration unit."""

    PENDING = "pending"          # waiting for dependencies
    ORACLE = "oracle"            # building characterization tests
    READY = "ready"              # deps verified, oracle built, queued
    TRANSLATING = "translating"
    VERIFYING = "verifying"
    REPAIRING = "repairing"
    FUZZING = "fuzzing"
    VERIFIED = "verified"
    BLOCKED = "blocked"          # failed after max attempts (or oracle failed)
    DEP_BLOCKED = "dep_blocked"  # a dependency is blocked; cannot proceed

    @property
    def terminal(self) -> bool:
        return self in (Status.VERIFIED, Status.BLOCKED, Status.DEP_BLOCKED)


SCHEMA = """
CREATE TABLE IF NOT EXISTS run_meta (key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE IF NOT EXISTS modules (
    name TEXT PRIMARY KEY,            -- COBOL program-id or copybook name
    kind TEXT NOT NULL,               -- program | subprogram | copybook
    path TEXT NOT NULL,
    unit TEXT NOT NULL,               -- migration unit (SCC) this module belongs to
    source TEXT NOT NULL,
    info_json TEXT NOT NULL,          -- parser output (layouts, calls, paragraphs, features)
    summary TEXT,
    python_code TEXT,                 -- final verified translation
    harness_code TEXT
);

CREATE TABLE IF NOT EXISTS units (
    name TEXT PRIMARY KEY,
    members_json TEXT NOT NULL,       -- module names in the SCC
    status TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    fuzz_rounds_clean INTEGER NOT NULL DEFAULT 0,
    fuzz_bugs_found INTEGER NOT NULL DEFAULT 0,
    oracle_done INTEGER NOT NULL DEFAULT 0,
    model_used TEXT,
    error TEXT,
    started_at REAL,
    finished_at REAL,
    updated_at REAL
);

CREATE TABLE IF NOT EXISTS edges (
    src TEXT NOT NULL,                -- dependent
    dst TEXT NOT NULL,                -- dependency
    kind TEXT NOT NULL,               -- call | copy
    PRIMARY KEY (src, dst, kind)
);

CREATE TABLE IF NOT EXISTS attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    unit TEXT NOT NULL,
    attempt_no INTEGER NOT NULL,
    kind TEXT NOT NULL,               -- translate | repair
    model TEXT,
    code_json TEXT NOT NULL,          -- {module: python source}
    verdict TEXT,                     -- pass | fail | error
    diff_json TEXT,
    lessons_json TEXT,                -- lesson ids put in the prompt
    stage TEXT,                       -- recorded | fuzz
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS test_cases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    module TEXT NOT NULL,
    origin TEXT NOT NULL,             -- llm | property | random | fuzz_llm | fuzz_mutation | hidden
    name TEXT,
    input TEXT NOT NULL,              -- stdin text
    input_hash TEXT NOT NULL,
    expected_stdout TEXT NOT NULL,
    expected_exit INTEGER NOT NULL,
    created_at REAL NOT NULL,
    UNIQUE (module, input_hash)
);

CREATE TABLE IF NOT EXISTS verification_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    attempt_id INTEGER NOT NULL,
    module TEXT NOT NULL,
    test_case_id INTEGER NOT NULL,
    passed INTEGER NOT NULL,
    actual_stdout TEXT,
    diff_json TEXT
);

CREATE TABLE IF NOT EXISTS lessons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT NOT NULL,
    tags_json TEXT NOT NULL,
    source_unit TEXT,
    uses INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS llm_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    role TEXT,
    unit TEXT,
    provider TEXT,
    model TEXT,
    prompt_tokens INTEGER,
    completion_tokens INTEGER,
    latency_ms REAL,
    cost REAL NOT NULL DEFAULT 0,
    cache_hit INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,             -- ok | rate_limited | error | timeout | invalid_json | skipped
    error TEXT
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    type TEXT NOT NULL,
    unit TEXT,
    payload_json TEXT
);

CREATE INDEX IF NOT EXISTS ix_tc_module ON test_cases(module);
CREATE INDEX IF NOT EXISTS ix_att_unit ON attempts(unit);
CREATE INDEX IF NOT EXISTS ix_calls_ts ON llm_calls(ts);
"""


def sha(text: str) -> str:
    """Short stable hash used for de-duplicating test inputs."""
    return hashlib.sha256(text.encode()).hexdigest()[:24]


class Blackboard:
    """Thin, typed wrapper around the SQLite state database."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(self.path, check_same_thread=False, timeout=30)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    # ------------------------------------------------------------ low level

    def execute(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self.conn.execute(sql, tuple(params))
            self.conn.commit()
            return cur

    def query(self, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return list(self.conn.execute(sql, tuple(params)).fetchall())

    def one(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Row | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def close(self) -> None:
        with self._lock:
            self.conn.close()

    # ------------------------------------------------------------ meta

    def set_meta(self, key: str, value: Any) -> None:
        self.execute("INSERT OR REPLACE INTO run_meta(key, value) VALUES (?, ?)", (key, json.dumps(value)))

    def get_meta(self, key: str, default: Any = None) -> Any:
        row = self.one("SELECT value FROM run_meta WHERE key=?", (key,))
        return json.loads(row["value"]) if row else default

    # ------------------------------------------------------------ events

    def emit(self, type_: str, unit: str | None = None, **payload: Any) -> None:
        """Append an event (consumed by the dashboard SSE stream)."""
        self.execute(
            "INSERT INTO events(ts, type, unit, payload_json) VALUES (?, ?, ?, ?)",
            (time.time(), type_, unit, json.dumps(payload, default=str)),
        )

    def events_after(self, last_id: int, limit: int = 500) -> list[dict[str, Any]]:
        rows = self.query("SELECT * FROM events WHERE id > ? ORDER BY id LIMIT ?", (last_id, limit))
        return [
            {"id": r["id"], "ts": r["ts"], "type": r["type"], "unit": r["unit"], **json.loads(r["payload_json"] or "{}")}
            for r in rows
        ]

    # ------------------------------------------------------------ modules / units / edges

    def upsert_module(self, name: str, kind: str, path: str, unit: str, source: str, info: dict[str, Any]) -> None:
        self.execute(
            """INSERT INTO modules(name, kind, path, unit, source, info_json) VALUES (?,?,?,?,?,?)
               ON CONFLICT(name) DO UPDATE SET kind=excluded.kind, path=excluded.path, unit=excluded.unit,
               source=excluded.source, info_json=excluded.info_json""",
            (name, kind, path, unit, source, json.dumps(info)),
        )

    def module(self, name: str) -> dict[str, Any] | None:
        row = self.one("SELECT * FROM modules WHERE name=?", (name,))
        return _module_dict(row) if row else None

    def modules(self) -> list[dict[str, Any]]:
        return [_module_dict(r) for r in self.query("SELECT * FROM modules ORDER BY name")]

    def set_module_fields(self, name: str, **fields: Any) -> None:
        cols = ", ".join(f"{k}=?" for k in fields)
        self.execute(f"UPDATE modules SET {cols} WHERE name=?", (*fields.values(), name))

    def upsert_unit(self, name: str, members: list[str], status: Status = Status.PENDING) -> None:
        """Create a unit if missing (existing status is preserved for resume)."""
        self.execute(
            """INSERT INTO units(name, members_json, status, updated_at) VALUES (?,?,?,?)
               ON CONFLICT(name) DO UPDATE SET members_json=excluded.members_json""",
            (name, json.dumps(members), status.value, time.time()),
        )

    def unit(self, name: str) -> dict[str, Any] | None:
        row = self.one("SELECT * FROM units WHERE name=?", (name,))
        return _unit_dict(row) if row else None

    def units(self) -> list[dict[str, Any]]:
        return [_unit_dict(r) for r in self.query("SELECT * FROM units ORDER BY name")]

    def set_status(self, unit: str, status: Status, **fields: Any) -> None:
        """Transition a unit's status and emit a `status` event."""
        fields["status"] = status.value
        fields["updated_at"] = time.time()
        cols = ", ".join(f"{k}=?" for k in fields)
        self.execute(f"UPDATE units SET {cols} WHERE name=?", (*fields.values(), unit))
        self.emit("status", unit, status=status.value, **{k: v for k, v in fields.items() if k not in ("status", "updated_at")})

    def update_unit(self, unit: str, **fields: Any) -> None:
        fields["updated_at"] = time.time()
        cols = ", ".join(f"{k}=?" for k in fields)
        self.execute(f"UPDATE units SET {cols} WHERE name=?", (*fields.values(), unit))

    def add_edge(self, src: str, dst: str, kind: str) -> None:
        self.execute("INSERT OR IGNORE INTO edges(src, dst, kind) VALUES (?,?,?)", (src, dst, kind))

    def edges(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self.query("SELECT * FROM edges ORDER BY src, dst")]

    # ------------------------------------------------------------ attempts

    def add_attempt(self, unit: str, attempt_no: int, kind: str, model: str | None, code: dict[str, str],
                    lessons: list[int] | None = None) -> int:
        cur = self.execute(
            """INSERT INTO attempts(unit, attempt_no, kind, model, code_json, lessons_json, created_at)
               VALUES (?,?,?,?,?,?,?)""",
            (unit, attempt_no, kind, model, json.dumps(code), json.dumps(lessons or []), time.time()),
        )
        return int(cur.lastrowid)

    def finish_attempt(self, attempt_id: int, verdict: str, diff: Any, stage: str) -> None:
        self.execute(
            "UPDATE attempts SET verdict=?, diff_json=?, stage=? WHERE id=?",
            (verdict, json.dumps(diff, default=str), stage, attempt_id),
        )

    def attempts(self, unit: str) -> list[dict[str, Any]]:
        rows = self.query("SELECT * FROM attempts WHERE unit=? ORDER BY id", (unit,))
        out = []
        for r in rows:
            d = dict(r)
            d["code"] = json.loads(d.pop("code_json"))
            d["diff"] = json.loads(d.pop("diff_json") or "null")
            d["lessons"] = json.loads(d.pop("lessons_json") or "[]")
            out.append(d)
        return out

    # ------------------------------------------------------------ test cases

    def add_test_case(self, module: str, origin: str, input_text: str, expected_stdout: str,
                      expected_exit: int, name: str | None = None) -> int | None:
        """Insert a golden test case; returns its id, or None if this input already exists."""
        cur = self.execute(
            """INSERT OR IGNORE INTO test_cases(module, origin, name, input, input_hash, expected_stdout,
               expected_exit, created_at) VALUES (?,?,?,?,?,?,?,?)""",
            (module, origin, name, input_text, sha(input_text), expected_stdout, expected_exit, time.time()),
        )
        return int(cur.lastrowid) if cur.rowcount else None

    def test_cases(self, module: str, origins: Iterable[str] | None = None) -> list[dict[str, Any]]:
        if origins is None:
            rows = self.query("SELECT * FROM test_cases WHERE module=? ORDER BY id", (module,))
        else:
            origins = list(origins)
            marks = ",".join("?" * len(origins))
            rows = self.query(
                f"SELECT * FROM test_cases WHERE module=? AND origin IN ({marks}) ORDER BY id", (module, *origins)
            )
        return [dict(r) for r in rows]

    def has_input(self, module: str, input_text: str) -> bool:
        return self.one("SELECT 1 FROM test_cases WHERE module=? AND input_hash=?", (module, sha(input_text))) is not None

    def add_verification_results(self, attempt_id: int, results: list[dict[str, Any]]) -> None:
        with self._lock:
            self.conn.executemany(
                """INSERT INTO verification_results(attempt_id, module, test_case_id, passed, actual_stdout, diff_json)
                   VALUES (?,?,?,?,?,?)""",
                [
                    (attempt_id, r["module"], r["test_case_id"], int(r["passed"]), r.get("actual_stdout"),
                     json.dumps(r.get("diff")) if r.get("diff") else None)
                    for r in results
                ],
            )
            self.conn.commit()

    # ------------------------------------------------------------ lessons

    def add_lesson(self, text: str, tags: list[str], source_unit: str | None) -> int:
        cur = self.execute(
            "INSERT INTO lessons(text, tags_json, source_unit, created_at) VALUES (?,?,?,?)",
            (text, json.dumps(sorted(set(tags))), source_unit, time.time()),
        )
        return int(cur.lastrowid)

    def lessons(self) -> list[dict[str, Any]]:
        rows = self.query("SELECT * FROM lessons ORDER BY id")
        return [{**dict(r), "tags": json.loads(r["tags_json"])} for r in rows]

    def bump_lesson_uses(self, ids: list[int]) -> None:
        for i in ids:
            self.execute("UPDATE lessons SET uses = uses + 1 WHERE id=?", (i,))

    # ------------------------------------------------------------ llm calls

    def log_llm_call(self, **row: Any) -> None:
        row.setdefault("ts", time.time())
        cols = ", ".join(row)
        marks = ", ".join("?" * len(row))
        self.execute(f"INSERT INTO llm_calls({cols}) VALUES ({marks})", tuple(row.values()))

    def llm_usage_since(self, provider: str, model: str | None, since_ts: float) -> tuple[int, int]:
        """(requests, tokens) made to a provider (optionally a model) since a timestamp, excluding cache hits."""
        sql = """SELECT COUNT(*) AS n, COALESCE(SUM(COALESCE(prompt_tokens,0)+COALESCE(completion_tokens,0)),0) AS t
                 FROM llm_calls WHERE provider=? AND ts>=? AND cache_hit=0 AND status IN ('ok','invalid_json')"""
        params: list[Any] = [provider, since_ts]
        if model is not None:
            sql += " AND model=?"
            params.append(model)
        row = self.one(sql, params)
        return (int(row["n"]), int(row["t"])) if row else (0, 0)


def _module_dict(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    d["info"] = json.loads(d.pop("info_json"))
    return d


def _unit_dict(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    d["members"] = json.loads(d.pop("members_json"))
    d["status"] = Status(d["status"])
    return d
