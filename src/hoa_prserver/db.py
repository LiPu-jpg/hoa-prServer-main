"""SQLite persistence for pending requests.

When a target repo doesn't exist yet, we store the submitted TOML + metadata here.
The background poller later checks GitHub and creates a PR once the repo appears.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class PendingRequest:
    id: int
    org: str
    repo: str
    course_code: str
    course_name: str
    repo_type: str
    toml_text: str
    status: str
    created_at: str
    updated_at: str
    last_error: str
    pr_url: str


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def init_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS pending_requests (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              org TEXT NOT NULL,
              repo TEXT NOT NULL,
              course_code TEXT NOT NULL,
              course_name TEXT NOT NULL,
              repo_type TEXT NOT NULL,
              toml_text TEXT NOT NULL,
              status TEXT NOT NULL,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              last_error TEXT NOT NULL DEFAULT '',
              pr_url TEXT NOT NULL DEFAULT ''
            );
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_pending_status ON pending_requests(status);"
        )
        conn.commit()


def insert_pending(
    db_path: Path,
    *,
    org: str,
    repo: str,
    course_code: str,
    course_name: str,
    repo_type: str,
    toml_text: str,
    status: str,
) -> int:
    now = _utc_now()
    with sqlite3.connect(str(db_path)) as conn:
        cur = conn.execute(
            """
            INSERT INTO pending_requests
              (org, repo, course_code, course_name, repo_type, toml_text, status, created_at, updated_at)
            VALUES
              (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (org, repo, course_code, course_name, repo_type, toml_text, status, now, now),
        )
        conn.commit()
        lastrowid = cur.lastrowid
        if lastrowid is None:
            raise RuntimeError("sqlite did not return lastrowid")
        return int(lastrowid)


def list_by_status(db_path: Path, status: str, *, limit: int = 50) -> list[PendingRequest]:
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM pending_requests WHERE status = ? ORDER BY id ASC LIMIT ?",
            (status, limit),
        ).fetchall()

    out: list[PendingRequest] = []
    for r in rows:
        out.append(
            PendingRequest(
                id=int(r["id"]),
                org=str(r["org"]),
                repo=str(r["repo"]),
                course_code=str(r["course_code"]),
                course_name=str(r["course_name"]),
                repo_type=str(r["repo_type"]),
                toml_text=str(r["toml_text"]),
                status=str(r["status"]),
                created_at=str(r["created_at"]),
                updated_at=str(r["updated_at"]),
                last_error=str(r["last_error"]),
                pr_url=str(r["pr_url"]),
            )
        )
    return out


def update_status(db_path: Path, request_id: int, *, status: str, last_error: str = "", pr_url: str = "") -> None:
    now = _utc_now()
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            """
            UPDATE pending_requests
            SET status = ?, updated_at = ?, last_error = ?, pr_url = ?
            WHERE id = ?
            """,
            (status, now, last_error, pr_url, request_id),
        )
        conn.commit()


def get_request(db_path: Path, request_id: int) -> PendingRequest | None:
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM pending_requests WHERE id = ?",
            (request_id,),
        ).fetchone()
    if not row:
        return None
    r = row
    return PendingRequest(
        id=int(r["id"]),
        org=str(r["org"]),
        repo=str(r["repo"]),
        course_code=str(r["course_code"]),
        course_name=str(r["course_name"]),
        repo_type=str(r["repo_type"]),
        toml_text=str(r["toml_text"]),
        status=str(r["status"]),
        created_at=str(r["created_at"]),
        updated_at=str(r["updated_at"]),
        last_error=str(r["last_error"]),
        pr_url=str(r["pr_url"]),
    )
