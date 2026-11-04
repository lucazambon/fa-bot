"""Persistenza su SQLite: impostazioni, profilo, offerte già viste/scartate/salvate."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from .models import Job, Profile

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS jobs (
    id         TEXT PRIMARY KEY,
    data       TEXT NOT NULL,
    status     TEXT NOT NULL DEFAULT 'sent',   -- sent | dismissed | saved
    first_seen TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def _load_job(raw: str) -> Job:
    data = json.loads(raw)
    data.setdefault("verified", False)  # offerte salvate prima dell'introduzione del campo
    return Job.model_validate(data)


def posting_key(title: str, company: str) -> str:
    """Chiave di deduplica: stesso titolo + azienda = stessa offerta, anche se pubblicata su siti diversi."""
    norm = lambda s: re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()  # noqa: E731
    raw = f"{norm(title)}|{norm(company)}"
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def job_key(job: Job) -> str:
    return posting_key(job.title, job.company)


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    # --- settings -------------------------------------------------------
    def get(self, key: str, default: Any = None) -> Any:
        row = self.conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return json.loads(row["value"]) if row else default

    def set(self, key: str, value: Any) -> None:
        self.conn.execute(
            "INSERT INTO settings(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, json.dumps(value, ensure_ascii=False)),
        )
        self.conn.commit()

    def get_profile(self) -> Profile | None:
        data = self.get("profile")
        if not data:
            return None
        data.setdefault("match_terms", [])  # profili creati prima dell'introduzione del campo
        return Profile.model_validate(data)

    def set_profile(self, profile: Profile) -> None:
        self.set("profile", profile.model_dump())

    # --- jobs -----------------------------------------------------------
    def is_known(self, key: str) -> bool:
        return self.conn.execute("SELECT 1 FROM jobs WHERE id = ?", (key,)).fetchone() is not None

    def add_sent(self, key: str, job: Job) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO jobs(id, data) VALUES(?, ?)", (key, job.model_dump_json())
        )
        self.conn.commit()

    def set_status(self, key: str, status: str) -> Job | None:
        self.conn.execute("UPDATE jobs SET status = ? WHERE id = ?", (status, key))
        self.conn.commit()
        row = self.conn.execute("SELECT data FROM jobs WHERE id = ?", (key,)).fetchone()
        return _load_job(row["data"]) if row else None

    def jobs_by_status(self, status: str, limit: int = 50) -> list[Job]:
        rows = self.conn.execute(
            "SELECT data FROM jobs WHERE status = ? ORDER BY first_seen DESC LIMIT ?", (status, limit)
        ).fetchall()
        return [_load_job(r["data"]) for r in rows]

    def recent_titles(self, limit: int = 60) -> list[str]:
        rows = self.conn.execute(
            "SELECT data FROM jobs ORDER BY first_seen DESC LIMIT ?", (limit,)
        ).fetchall()
        out = []
        for r in rows:
            j = _load_job(r["data"])
            out.append(f"{j.title} — {j.company}")
        return out
