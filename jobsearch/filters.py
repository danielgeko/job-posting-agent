"""Deterministic (no-LLM) filters. Each rule returns a drop reason or None."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from jobsearch.models import Posting
from jobsearch.normalize import us_locations

# "3+ years", "3-5 years", "3 to 5 yrs", "three years" is rare enough to ignore.
_YEARS_RE = re.compile(
    r"(?<![\d.])(\d{1,2})\s*\+?\s*(?:(?:-|–|—|to)\s*\d{1,2}\s*\+?\s*)?(?:years?|yrs?)\b"
    r"(?:\s*of)?[^.\n]{0,60}?\bexperience",
    re.I,
)


def min_years_required(description: str) -> int | None:
    """Smallest 'N years ... experience' figure in the text (None if none found).

    Using the minimum keeps postings like '0-2 years required, 5+ preferred'.
    """
    nums = [
        int(m.group(1))
        for m in _YEARS_RE.finditer(description or "")
        # "Bachelor's or 4 years of equivalent experience" is a degree substitute, not a bar.
        if "equivalent" not in m.group(0).lower()
    ]
    return min(nums) if nums else None


@dataclass
class FilterConfig:
    exclude_title: list[re.Pattern]
    include_title: list[re.Pattern]
    max_years: int
    max_age_days: int
    us_only: bool

    @classmethod
    def from_preferences(cls, prefs: dict, max_age_days: int) -> "FilterConfig":
        f = prefs.get("filters", {})
        comp = lambda pats: [re.compile(p, re.I) for p in pats or []]  # noqa: E731
        return cls(
            exclude_title=comp(f.get("exclude_title_patterns")),
            include_title=comp(f.get("include_title_patterns")),
            max_years=int(f.get("max_years_experience", 2)),
            max_age_days=max_age_days,
            us_only=bool(prefs.get("locations", {}).get("us_only", True)),
        )


def title_reason(title: str, cfg: FilterConfig) -> str | None:
    for pat in cfg.exclude_title:
        if pat.search(title):
            return f"title_excluded:{pat.pattern[:40]}"
    if cfg.include_title and not any(pat.search(title) for pat in cfg.include_title):
        return "not_software"
    return None


def check(p: Posting, cfg: FilterConfig, now: datetime | None = None) -> str | None:
    """Return a drop reason, or None if the posting passes. May trim p.locations to US ones."""
    reason = title_reason(p.title, cfg)
    if reason:
        return reason

    now = now or datetime.now(timezone.utc)
    if p.posted_at and p.posted_at < now - timedelta(days=cfg.max_age_days):
        return "stale"

    years = min_years_required(p.description)
    if years is not None and years > cfg.max_years:
        return f"experience_{years}plus"

    if cfg.us_only:
        kept = us_locations(p.locations)
        if kept is None:
            return "non_us_location"
        p.locations = kept

    return None
