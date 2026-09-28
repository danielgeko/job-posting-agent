"""SimplifyJobs New-Grad-Positions community list (a JSON file in the repo).

Entries carry company, title, locations, date posted and a direct apply URL but no
description, so the experience filter can't run on them and scoring sees less context.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx

from jobsearch.models import Posting
from jobsearch.sources.base import from_epoch, get_json

SIMPLIFY_URL = (
    "https://raw.githubusercontent.com/SimplifyJobs/New-Grad-Positions/dev/.github/scripts/listings.json"
)
DEFAULT_CATEGORIES = {"Software", "Software Engineering", "AI/ML/Data"}


def parse(
    data: list,
    max_age_days: int,
    categories: set[str] = DEFAULT_CATEGORIES,
    now: datetime | None = None,
) -> list[Posting]:
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=max_age_days)
    out = []
    for j in data:
        if not (j.get("active") and j.get("is_visible", True)):
            continue
        if j.get("category") not in categories:
            continue
        posted = from_epoch(j.get("date_posted"))
        # The file holds ~20k historical entries; drop old ones before they hit the DB.
        if posted and posted < cutoff:
            continue
        out.append(
            Posting(
                source="simplify",
                external_id=j["id"],
                company=(j.get("company_name") or "").strip(),
                title=(j.get("title") or "").strip(),
                url=j.get("url") or "",
                locations=list(j.get("locations") or []),
                posted_at=posted,
            )
        )
    return out


def fetch(client: httpx.Client, max_age_days: int, categories: set[str] | None = None) -> list[Posting]:
    data = get_json(client, SIMPLIFY_URL)
    return parse(data, max_age_days, categories or DEFAULT_CATEGORIES)
