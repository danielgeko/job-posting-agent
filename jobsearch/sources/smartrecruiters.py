"""SmartRecruiters public Posting API: https://developers.smartrecruiters.com/docs/posting-api

  GET https://api.smartrecruiters.com/v1/companies/{company}/postings?q=...&country=us
  GET https://api.smartrecruiters.com/v1/companies/{company}/postings/{id}

`token` is the company identifier from jobs.smartrecruiters.com/<token>/...
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

import httpx

from jobsearch.models import Posting
from jobsearch.normalize import html_to_text, parse_salary_text
from jobsearch.sources.base import SourceError, get_json, parse_iso

log = logging.getLogger(__name__)

URL = "https://api.smartrecruiters.com/v1/companies/{token}/postings"
PAGE_SIZE = 100
MAX_PAGES_PER_SEARCH = 3
DETAIL_DELAY_S = 0.2
DEFAULT_SEARCHES = ["software engineer", "software developer"]
SECTION_ORDER = ["jobDescription", "qualifications", "additionalInformation"]


def _location(loc: dict) -> str:
    if not loc:
        return ""
    parts = [loc.get("city"), loc.get("region"), (loc.get("country") or "").upper()]
    text = ", ".join(p for p in parts if p)
    if loc.get("remote"):
        text = f"Remote ({text})" if text else "Remote"
    return text


def parse_detail(d: dict, company: str, token: str) -> Posting:
    sections = (d.get("jobAd") or {}).get("sections") or {}
    desc = html_to_text(
        "\n".join((sections.get(k) or {}).get("text") or "" for k in SECTION_ORDER)
    )
    lo, hi = parse_salary_text(desc)
    return Posting(
        source="smartrecruiters",
        external_id=f"{token}:{d['id']}",
        company=company,
        title=(d.get("name") or "").strip(),
        url=d.get("postingUrl") or f"https://jobs.smartrecruiters.com/{token}/{d['id']}",
        locations=[loc] if (loc := _location(d.get("location") or {})) else [],
        description=desc,
        posted_at=parse_iso(d.get("releasedDate")),
        salary_min=lo,
        salary_max=hi,
    )


def fetch(
    client: httpx.Client,
    company: dict,
    title_ok: Callable[[str], bool] = lambda t: True,
    max_age_days: int = 30,
    now: datetime | None = None,
) -> list[Posting]:
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=max_age_days)
    token = company["token"]
    country = company.get("country", "us")

    candidates: dict[str, dict] = {}
    for q in company.get("search") or DEFAULT_SEARCHES:
        for page in range(MAX_PAGES_PER_SEARCH):
            params = {"q": q, "limit": PAGE_SIZE, "offset": page * PAGE_SIZE}
            if country:
                params["country"] = country
            data = get_json(client, URL.format(token=token), params=params)
            batch = data.get("content") or []
            for j in batch:
                released = parse_iso(j.get("releasedDate"))
                if title_ok(j.get("name") or "") and not (released and released < cutoff):
                    candidates.setdefault(str(j["id"]), j)
            if len(batch) < PAGE_SIZE or (page + 1) * PAGE_SIZE >= (data.get("totalFound") or 0):
                break

    out: list[Posting] = []
    for job_id in candidates:
        try:
            d = get_json(client, f"{URL.format(token=token)}/{job_id}")
        except SourceError as e:
            log.warning("smartrecruiters detail failed for %s %s: %s", token, job_id, e)
            continue
        out.append(parse_detail(d, company["name"], token))
        time.sleep(DETAIL_DELAY_S)
    return out
