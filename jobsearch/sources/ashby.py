"""Ashby public Job Posting API: https://developers.ashbyhq.com/docs/public-job-posting-api"""

from __future__ import annotations

import httpx

from jobsearch.models import Posting
from jobsearch.normalize import HOURS_PER_YEAR, parse_salary_text
from jobsearch.sources.base import get_json, parse_iso

URL = "https://api.ashbyhq.com/posting-api/job-board/{token}"


def _salary(j: dict, desc: str) -> tuple[float | None, float | None, str]:
    comp = j.get("compensation") or {}
    summary = comp.get("scrapeableCompensationSalarySummary") or ""
    for c in comp.get("summaryComponents") or []:
        if c.get("compensationType") == "Salary" and c.get("minValue") and c.get("currencyCode") in (None, "USD"):
            mult = HOURS_PER_YEAR if "HOUR" in (c.get("interval") or "").upper() else 1
            return c["minValue"] * mult, (c.get("maxValue") or c["minValue"]) * mult, summary
    lo, hi = parse_salary_text(desc)
    return lo, hi, summary


def parse(data: dict, company: str) -> list[Posting]:
    out = []
    for j in data.get("jobs", []):
        if j.get("isListed") is False:
            continue
        desc = j.get("descriptionPlain") or ""
        lo, hi, raw = _salary(j, desc)
        locs = [j.get("location") or ""] + [
            s.get("location", "") for s in j.get("secondaryLocations") or []
        ]
        out.append(
            Posting(
                source="ashby",
                external_id=j["id"],
                company=company,
                title=(j.get("title") or "").strip(),
                url=j.get("jobUrl") or "",
                locations=[loc.strip() for loc in locs if loc and loc.strip()],
                description=desc,
                posted_at=parse_iso(j.get("publishedAt")),
                salary_min=lo,
                salary_max=hi,
                salary_raw=raw,
            )
        )
    return out


def fetch(client: httpx.Client, company: dict) -> list[Posting]:
    data = get_json(
        client, URL.format(token=company["token"]), params={"includeCompensation": "true"}
    )
    return parse(data, company["name"])
