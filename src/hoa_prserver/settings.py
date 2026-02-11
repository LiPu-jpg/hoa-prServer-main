"""Settings loader.

Loads configuration from environment variables for:
- GitHub org name + token
- optional API key
- sqlite path
- poll interval
- optional SMTP settings
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    org_name: str
    github_token: str | None

    api_key: str | None

    db_path: Path
    poll_interval_seconds: int

    smtp_host: str | None
    smtp_port: int
    smtp_user: str | None
    smtp_password: str | None
    smtp_from: str | None
    admin_email: str | None

    allowed_repos: frozenset[str] | None

    final_dir: Path | None


def _parse_allowed_repos(raw: str | None) -> frozenset[str] | None:
    if raw is None:
        return None
    parts = [p.strip() for p in raw.split(",")]
    parts = [p for p in parts if p]
    return frozenset(parts) if parts else None


def is_repo_allowed(settings: Settings, repo: str) -> bool:
    if settings.allowed_repos is None:
        return True
    return repo in settings.allowed_repos


def load_settings() -> Settings:
    org_name = os.getenv("ORG_NAME", "HITSZ-OpenAuto")
    github_token = os.getenv("GITHUB_TOKEN")

    api_key = os.getenv("API_KEY")

    db_path = Path(os.getenv("HOA_PRSERVER_DB", "./data/hoa_prserver.sqlite3"))
    poll_interval_seconds = int(os.getenv("POLL_INTERVAL_SECONDS", "3600"))

    smtp_host = os.getenv("SMTP_HOST")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_user = os.getenv("SMTP_USER")
    smtp_password = os.getenv("SMTP_PASSWORD")
    smtp_from = os.getenv("SMTP_FROM")
    admin_email = os.getenv("ADMIN_EMAIL")

    allowed_repos = _parse_allowed_repos(os.getenv("ALLOWED_REPOS"))

    final_dir_raw = os.getenv("FINAL_DIR")
    final_dir = Path(final_dir_raw).resolve() if final_dir_raw else None

    return Settings(
        org_name=org_name,
        github_token=github_token,
        api_key=api_key,
        db_path=db_path,
        poll_interval_seconds=poll_interval_seconds,
        smtp_host=smtp_host,
        smtp_port=smtp_port,
        smtp_user=smtp_user,
        smtp_password=smtp_password,
        smtp_from=smtp_from,
        admin_email=admin_email,
        allowed_repos=allowed_repos,
        final_dir=final_dir,
    )
