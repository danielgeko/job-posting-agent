from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Posting:
    source: str  # greenhouse | lever | ashby | simplify
    external_id: str
    company: str
    title: str
    url: str
    locations: list[str] = field(default_factory=list)
    description: str = ""
    posted_at: datetime | None = None
    salary_min: float | None = None  # annual USD
    salary_max: float | None = None
    salary_raw: str = ""

    # Set during the pipeline.
    db_id: int | None = None

    @property
    def location_str(self) -> str:
        return "; ".join(self.locations)


@dataclass
class Score:
    fit_score: int
    matched_skills: list[str]
    gaps: list[str]
    reason: str


@dataclass
class Lead:
    """One row destined for the Job Leads tab (columns A–I)."""

    company: str
    link: str
    salary: str
    role: str
    location: str
    fit_score: int
    why: str
    date_posted: str
    date_found: str

    def to_row(self) -> list:
        return [
            self.company,
            self.link,
            self.salary,
            self.role,
            self.location,
            self.fit_score,
            self.why,
            self.date_posted,
            self.date_found,
        ]
