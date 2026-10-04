"""Website accounts, sessions and runs (SQLite `web.db`, separate from each run's blackboard).

Passwords are hashed with scrypt (stdlib). Session tokens are random; only their SHA-256 is stored,
so a leaked database cannot be replayed as cookies.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  handle TEXT UNIQUE NOT NULL,
  pw_hash BLOB NOT NULL,
  salt BLOB NOT NULL,
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
  token_hash TEXT PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_at REAL NOT NULL,
  expires_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
  id TEXT PRIMARY KEY,
  user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,   -- NULL: the public specimen run
  title TEXT NOT NULL,
  source_kind TEXT NOT NULL,          -- demo | upload
  dry_run INTEGER NOT NULL,
  fuzz INTEGER NOT NULL,
  status TEXT NOT NULL,               -- queued | running | done | failed
  exit_code INTEGER,
  note TEXT,
  created_at REAL NOT NULL,
  started_at REAL,
  finished_at REAL,
  summary_json TEXT
);
"""

HANDLE_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{2,31}$")
MIN_PASSPHRASE = 8
SESSION_TTL_S = 30 * 24 * 3600
_SCRYPT = {"n": 2**14, "r": 8, "p": 1, "dklen": 32}


class AuthError(ValueError):
    """Invalid handle/passphrase, or a taken handle."""


def hash_passphrase(passphrase: str, salt: bytes) -> bytes:
    """scrypt hash of a passphrase."""
    return hashlib.scrypt(passphrase.encode(), salt=salt, **_SCRYPT)


class WebStore:
    """Thread-safe access to web.db."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA)
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(users)")}
        if "supabase_id" not in cols:   # accounts signed in through Supabase Auth (email or GitHub)
            self.conn.execute("ALTER TABLE users ADD COLUMN supabase_id TEXT")
            self.conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS users_supabase ON users(supabase_id)")
        self.conn.commit()
        self.run_listeners: list[Callable[[dict[str, Any] | None, str], None]] = []

    def close(self) -> None:
        with self._lock:
            self.conn.close()

    def _exec(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self.conn.execute(sql, params)
            self.conn.commit()
            return cur

    def _one(self, sql: str, params: tuple = ()) -> dict[str, Any] | None:
        with self._lock:
            row = self.conn.execute(sql, params).fetchone()
        return dict(row) if row else None

    def _all(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    # ------------------------------------------------------------ users

    def user_by_handle(self, handle: str) -> dict[str, Any] | None:
        return self._one("SELECT id, handle, created_at FROM users WHERE handle=?", (handle.lower(),))

    def create_user(self, handle: str, passphrase: str) -> dict[str, Any]:
        """Create an account. Raises AuthError on an invalid or taken handle or a short passphrase."""
        handle = handle.strip().lower()
        if not HANDLE_RE.match(handle):
            raise AuthError("Handle: 3-32 characters, a-z 0-9 . _ -, starting with a letter or digit.")
        if len(passphrase) < MIN_PASSPHRASE:
            raise AuthError(f"Passphrase: at least {MIN_PASSPHRASE} characters.")
        salt = secrets.token_bytes(16)
        try:
            cur = self._exec("INSERT INTO users(handle, pw_hash, salt, created_at) VALUES (?,?,?,?)",
                             (handle, hash_passphrase(passphrase, salt), salt, time.time()))
        except sqlite3.IntegrityError:
            raise AuthError("That handle is taken.") from None
        return {"id": cur.lastrowid, "handle": handle}

    def user_for_supabase(self, supabase_id: str, preferred: str) -> dict[str, Any]:
        """The local account linked to a Supabase user, created on first sign-in.

        The handle comes from the GitHub login or the e-mail local part, made unique. Such accounts
        get a random passphrase hash, so they can only sign in through Supabase.
        """
        row = self._one("SELECT id, handle, created_at FROM users WHERE supabase_id=?", (supabase_id,))
        if row:
            return row
        base = re.sub(r"[^a-z0-9_.-]", "-", preferred.lower()).strip("-._")[:28] or "punched"
        if len(base) < 3:
            base = (base + "-lb")[:28]
        handle, n = base, 1
        while self.user_by_handle(handle):
            n += 1
            handle = f"{base}-{n}"
        salt = secrets.token_bytes(16)
        cur = self._exec("INSERT INTO users(handle, pw_hash, salt, created_at, supabase_id) VALUES (?,?,?,?,?)",
                         (handle, secrets.token_bytes(32), salt, time.time(), supabase_id))
        return {"id": cur.lastrowid, "handle": handle, "created_at": time.time()}

    def supabase_id(self, user_id: int | None) -> str | None:
        if user_id is None:
            return None
        row = self._one("SELECT supabase_id FROM users WHERE id=?", (user_id,))
        return row["supabase_id"] if row else None

    def authenticate(self, handle: str, passphrase: str) -> dict[str, Any] | None:
        """The user if the passphrase matches, else None (constant-time compare)."""
        row = self._one("SELECT id, handle, pw_hash, salt FROM users WHERE handle=?", (handle.strip().lower(),))
        if row is None or len(row["salt"]) != 16:
            hash_passphrase(passphrase, b"0" * 16)   # same cost whether or not the handle exists
            return None
        if not hmac.compare_digest(hash_passphrase(passphrase, row["salt"]), row["pw_hash"]):
            return None
        return {"id": row["id"], "handle": row["handle"]}

    # ------------------------------------------------------------ sessions

    @staticmethod
    def _token_hash(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    def new_session(self, user_id: int) -> str:
        """A fresh session token (store only its hash)."""
        token = secrets.token_urlsafe(32)
        now = time.time()
        self._exec("INSERT INTO sessions(token_hash, user_id, created_at, expires_at) VALUES (?,?,?,?)",
                   (self._token_hash(token), user_id, now, now + SESSION_TTL_S))
        return token

    def session_user(self, token: str | None) -> dict[str, Any] | None:
        if not token:
            return None
        return self._one("SELECT u.id, u.handle, u.created_at FROM sessions s JOIN users u ON u.id = s.user_id "
                         "WHERE s.token_hash=? AND s.expires_at > ?", (self._token_hash(token), time.time()))

    def end_session(self, token: str | None) -> None:
        if token:
            self._exec("DELETE FROM sessions WHERE token_hash=?", (self._token_hash(token),))

    # ------------------------------------------------------------ runs

    def create_run(self, user_id: int | None, title: str, source_kind: str, dry_run: bool, fuzz: bool,
                   run_id: str | None = None) -> dict[str, Any]:
        rid = run_id or uuid.uuid4().hex[:12]
        self._exec("INSERT INTO runs(id, user_id, title, source_kind, dry_run, fuzz, status, created_at) "
                   "VALUES (?,?,?,?,?,?,?,?)", (rid, user_id, title, source_kind, int(dry_run), int(fuzz),
                                                "queued", time.time()))
        self._changed(rid)
        return self.run(rid)  # type: ignore[return-value]

    def _changed(self, run_id: str) -> None:
        run = self.run(run_id)
        for fn in self.run_listeners:
            try:
                fn(run, run_id)
            except Exception:  # noqa: BLE001 - a mirror must never break a run
                pass

    def run(self, run_id: str) -> dict[str, Any] | None:
        row = self._one("SELECT * FROM runs WHERE id=?", (run_id,))
        return _run_row(row) if row else None

    def runs_for(self, user_id: int) -> list[dict[str, Any]]:
        return [_run_row(r) for r in self._all("SELECT * FROM runs WHERE user_id=? ORDER BY created_at DESC",
                                               (user_id,))]

    def update_run(self, run_id: str, **fields: Any) -> None:
        if "summary" in fields:
            fields["summary_json"] = json.dumps(fields.pop("summary"))
        cols = ", ".join(f"{k}=?" for k in fields)
        self._exec(f"UPDATE runs SET {cols} WHERE id=?", (*fields.values(), run_id))
        self._changed(run_id)

    def delete_run(self, run_id: str) -> None:
        self._exec("DELETE FROM runs WHERE id=?", (run_id,))
        self._changed(run_id)

    def interrupted_runs(self) -> list[dict[str, Any]]:
        """Runs a previous server process left queued/running."""
        return [_run_row(r) for r in self._all("SELECT * FROM runs WHERE status IN ('queued', 'running')")]


def _run_row(row: dict[str, Any]) -> dict[str, Any]:
    row = dict(row)
    row["dry_run"] = bool(row["dry_run"])
    row["fuzz"] = bool(row["fuzz"])
    row["summary"] = json.loads(row.pop("summary_json") or "null")
    return row
