from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Settings:
    anthropic_api_key: str | None
    scoring_model: str
    score_threshold: int
    max_score_per_run: int
    google_sa_json: Path
    spreadsheet_id: str
    tracker_tab: str
    leads_tab: str
    db_path: Path
    max_posting_age_days: int
    root: Path = ROOT
    preferences: dict = field(default_factory=dict)

    @property
    def companies_path(self) -> Path:
        return self.root / "companies.yaml"

    @property
    def resume_path(self) -> Path:
        return self.root / "profile" / "resume.yaml"

    @property
    def prompt_path(self) -> Path:
        return self.root / "prompts" / "score.md"

    @property
    def logs_dir(self) -> Path:
        return self.root / "logs"


def _path(value: str) -> Path:
    p = Path(value).expanduser()
    return p if p.is_absolute() else ROOT / p


def load_settings() -> Settings:
    load_dotenv(ROOT / ".env")
    prefs_file = ROOT / "profile" / "preferences.yaml"
    prefs = yaml.safe_load(prefs_file.read_text()) if prefs_file.exists() else {}
    return Settings(
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY") or None,
        scoring_model=os.getenv("SCORING_MODEL", "claude-haiku-4-5"),
        score_threshold=int(os.getenv("SCORE_THRESHOLD", "65")),
        max_score_per_run=int(os.getenv("MAX_SCORE_PER_RUN", "150")),
        google_sa_json=_path(os.getenv("GOOGLE_SA_JSON", "service-account.json")),
        spreadsheet_id=os.getenv("SPREADSHEET_ID", ""),
        tracker_tab=os.getenv("TRACKER_TAB", "Sheet1"),
        leads_tab=os.getenv("LEADS_TAB", "Job Leads"),
        db_path=_path(os.getenv("DB_PATH", "jobsearch.db")),
        max_posting_age_days=int(os.getenv("MAX_POSTING_AGE_DAYS", "30")),
        preferences=prefs or {},
    )


def load_companies(path: Path) -> list[dict]:
    if not path.exists():
        return []
    data = yaml.safe_load(path.read_text()) or {}
    return data.get("companies", []) or []
