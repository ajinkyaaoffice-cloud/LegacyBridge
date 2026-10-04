"""Optional Supabase integration: hosted auth (email + GitHub OAuth) and a mirror of profiles/runs.

Off unless SUPABASE_URL and SUPABASE_ANON_KEY are set, so the site keeps working offline with
local punch-card accounts. The run blackboards stay local SQLite (the single source of truth for a
run); Supabase receives a best-effort, write-through copy of each run's ledger row and the user's
profile when SUPABASE_SERVICE_ROLE_KEY is also set. Keys come only from the environment and are
never logged or sent to the browser (except the public anon key, which is designed for that).
"""

from __future__ import annotations

import logging
import os
import queue
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

import httpx

log = logging.getLogger(__name__)

RUN_FIELDS = ("id", "title", "source_kind", "dry_run", "fuzz", "status", "exit_code", "created_at",
              "started_at", "finished_at", "summary")


@dataclass(frozen=True)
class SupabaseConfig:
    url: str
    anon_key: str
    service_key: str | None = None

    @classmethod
    def from_env(cls) -> "SupabaseConfig | None":
        url, anon = os.environ.get("SUPABASE_URL", "").strip(), os.environ.get("SUPABASE_ANON_KEY", "").strip()
        if not url or not anon:
            return None
        return cls(url.rstrip("/"), anon, os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip() or None)

    def public(self) -> dict[str, Any]:
        """What the browser may know (the anon key is public by design; RLS protects the tables)."""
        return {"url": self.url, "anon_key": self.anon_key}


class SupabaseAuthError(Exception):
    """The access token was rejected (or Supabase could not be reached)."""


Verifier = Callable[[str], dict[str, Any]]


def http_verifier(cfg: SupabaseConfig, timeout: float = 8.0) -> Verifier:
    """Verify an access token by asking Supabase Auth who it belongs to (works for any JWT signing key)."""
    cache: dict[str, tuple[float, dict[str, Any]]] = {}

    def verify(token: str) -> dict[str, Any]:
        hit = cache.get(token)
        if hit and hit[0] > time.time():
            return hit[1]
        try:
            r = httpx.get(f"{cfg.url}/auth/v1/user", timeout=timeout,
                          headers={"apikey": cfg.anon_key, "Authorization": f"Bearer {token}"})
        except httpx.HTTPError as exc:
            raise SupabaseAuthError(f"Supabase unreachable: {type(exc).__name__}") from None
        if r.status_code != 200:
            raise SupabaseAuthError("Supabase rejected the session")
        user = r.json()
        cache[token] = (time.time() + 60, user)
        return user
    return verify


def identity(user: dict[str, Any]) -> tuple[str, str, str]:
    """(supabase id, preferred handle, provider) from a Supabase user object."""
    meta = user.get("user_metadata") or {}
    provider = (user.get("app_metadata") or {}).get("provider") or "email"
    pref = meta.get("user_name") or meta.get("preferred_username") or (user.get("email") or "").split("@")[0]
    return str(user["id"]), str(pref or "punched"), provider


class Mirror:
    """Background write-through of profiles and run rows to Supabase (PostgREST, service role).

    Never blocks a request and never raises: a Supabase outage only delays the mirror.
    """

    def __init__(self, cfg: SupabaseConfig, owner_of: Callable[[int | None], str | None]):
        self.cfg = cfg
        self.owner_of = owner_of          # local user id -> supabase uid (or None: not mirrored)
        self.q: queue.Queue[tuple[str, str, dict[str, Any] | None] | None] = queue.Queue()
        self.t = threading.Thread(target=self._work, name="supabase-mirror", daemon=True)
        self.t.start()

    def _headers(self) -> dict[str, str]:
        key = self.cfg.service_key or ""
        return {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json",
                "Prefer": "resolution=merge-duplicates,return=minimal"}

    def profile(self, uid: str, handle: str, provider: str) -> None:
        self.q.put(("upsert", "lb_profiles", {"id": uid, "handle": handle, "provider": provider}))

    def run_changed(self, run: dict[str, Any] | None, run_id: str) -> None:
        if run is None:
            self.q.put(("delete", "lb_runs", {"id": run_id}))
            return
        owner = self.owner_of(run.get("user_id"))
        if owner is None:
            return
        row = {k: run.get(k) for k in RUN_FIELDS}
        row["owner"] = owner
        for k in ("created_at", "started_at", "finished_at"):
            if row[k] is not None:
                row[k] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(row[k]))
        self.q.put(("upsert", "lb_runs", row))

    def close(self) -> None:
        self.q.put(None)

    def _work(self) -> None:
        with httpx.Client(timeout=10) as client:
            while (job := self.q.get()) is not None:
                op, table, row = job
                url = f"{self.cfg.url}/rest/v1/{table}"
                try:
                    if op == "upsert":
                        r = client.post(url, json=row, headers=self._headers())
                    else:
                        r = client.delete(url, params={"id": f"eq.{row['id']}"}, headers=self._headers())
                    if r.status_code >= 300:
                        log.warning("supabase mirror %s %s: HTTP %s", op, table, r.status_code)
                except httpx.HTTPError as exc:
                    log.warning("supabase mirror %s %s: %s", op, table, type(exc).__name__)
