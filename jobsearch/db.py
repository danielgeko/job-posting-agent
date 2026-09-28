"""SQLite storage for postings, scores, and run history (plain sqlite3)."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from jobsearch.models import Posting, Score

SCHEMA = """
CREATE TABLE IF NOT EXISTS postings (
    id            INTEGER PRIMARY KEY,
    source        TEXT NOT NULL,
    external_id   TEXT NOT NULL,
    company       TEXT NOT NULL,
    title         TEXT NOT NULL,
    url           TEXT NOT NULL,
    locations     TEXT NOT NULL DEFAULT '[]',
    description   TEXT NOT NULL DEFAULT '',
    posted_at     TEXT,
    salary_min    REAL,
    salary_max    REAL,
    salary_raw    TEXT NOT NULL DEFAULT '',
    first_seen    TEXT NOT NULL,
    last_seen     TEXT NOT NULL,
    filter_reason TEXT,             -- NULL = passed filters (or not yet filtered)
    written_at    TEXT,             -- when it was appended to the Leads tab
    UNIQUE (source, external_id)
);

CREATE TABLE IF NOT EXISTS scores (
    posting_id     INTEGER NOT NULL REFERENCES postings(id),
    prompt_hash    TEXT NOT NULL,
    model          TEXT NOT NULL,
    fit_score      INTEGER NOT NULL,
    matched_skills TEXT NOT NULL,
    gaps           TEXT NOT NULL,
    reason         TEXT NOT NULL,
    scored_at      TEXT NOT NULL,
    PRIMARY KEY (posting_id, prompt_hash, model)
);

CREATE TABLE IF NOT EXISTS runs (
    id          INTEGER PRIMARY KEY,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    dry_run     INTEGER NOT NULL DEFAULT 0,
    stats       TEXT NOT NULL DEFAULT '{}',
    errors      TEXT NOT NULL DEFAULT '[]'
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class DB:
    def __init__(self, path: Path | str):
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # --- postings -----------------------------------------------------------

    def upsert_posting(self, p: Posting) -> tuple[int, bool]:
        """Insert or refresh a posting. Returns (id, is_new)."""
        now = _now()
        row = self.conn.execute(
            "SELECT id FROM postings WHERE source = ? AND external_id = ?",
            (p.source, p.external_id),
        ).fetchone()
        fields = (
            p.company,
            p.title,
            p.url,
            json.dumps(p.locations),
            p.description,
            p.posted_at.isoformat() if p.posted_at else None,
            p.salary_min,
            p.salary_max,
            p.salary_raw,
        )
        if row:
            self.conn.execute(
                """UPDATE postings SET company=?, title=?, url=?, locations=?, description=?,
                   posted_at=?, salary_min=?, salary_max=?, salary_raw=?, last_seen=?
                   WHERE id=?""",
                (*fields, now, row["id"]),
            )
            p.db_id = row["id"]
            return row["id"], False
        cur = self.conn.execute(
            """INSERT INTO postings (company, title, url, locations, description, posted_at,
               salary_min, salary_max, salary_raw, source, external_id, first_seen, last_seen)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (*fields, p.source, p.external_id, now, now),
        )
        p.db_id = cur.lastrowid
        return cur.lastrowid, True

    def set_filter_reason(self, posting_id: int, reason: str | None) -> None:
        self.conn.execute("UPDATE postings SET filter_reason = ? WHERE id = ?", (reason, posting_id))

    def mark_written(self, posting_ids: list[int]) -> None:
        now = _now()
        self.conn.executemany(
            "UPDATE postings SET written_at = ? WHERE id = ?", [(now, i) for i in posting_ids]
        )

    def written_ids(self) -> set[int]:
        rows = self.conn.execute("SELECT id FROM postings WHERE written_at IS NOT NULL")
        return {r["id"] for r in rows}

    # --- scores -------------------------------------------------------------

    def get_score(self, posting_id: int, prompt_hash: str, model: str) -> Score | None:
        row = self.conn.execute(
            "SELECT * FROM scores WHERE posting_id = ? AND prompt_hash = ? AND model = ?",
            (posting_id, prompt_hash, model),
        ).fetchone()
        if not row:
            return None
        return Score(
            fit_score=row["fit_score"],
            matched_skills=json.loads(row["matched_skills"]),
            gaps=json.loads(row["gaps"]),
            reason=row["reason"],
        )

    def save_score(self, posting_id: int, prompt_hash: str, model: str, s: Score) -> None:
        self.conn.execute(
            """INSERT OR REPLACE INTO scores
               (posting_id, prompt_hash, model, fit_score, matched_skills, gaps, reason, scored_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                posting_id,
                prompt_hash,
                model,
                s.fit_score,
                json.dumps(s.matched_skills),
                json.dumps(s.gaps),
                s.reason,
                _now(),
            ),
        )

    # --- runs ---------------------------------------------------------------

    def start_run(self, dry_run: bool) -> int:
        cur = self.conn.execute(
            "INSERT INTO runs (started_at, dry_run) VALUES (?, ?)", (_now(), int(dry_run))
        )
        self.conn.commit()
        return cur.lastrowid

    def finish_run(self, run_id: int, stats: dict, errors: list[str]) -> None:
        self.conn.execute(
            "UPDATE runs SET finished_at = ?, stats = ?, errors = ? WHERE id = ?",
            (_now(), json.dumps(stats), json.dumps(errors), run_id),
        )
        self.conn.commit()

    def commit(self) -> None:
        self.conn.commit()
