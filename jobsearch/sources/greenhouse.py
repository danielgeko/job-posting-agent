"""Greenhouse Job Board API: https://developers.greenhouse.io/job-board.html"""

from __future__ import annotations

import httpx

from jobsearch.models import Posting
from jobsearch.normalize import html_to_text, parse_salary_text
from jobsearch.sources.base import get_json, parse_iso

URL = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs"


def parse(data: dict, company: str) -> list[Posting]:
    out = []
    for j in data.get("jobs", []):
        desc = html_to_text(j.get("content") or "")
        lo, hi = parse_salary_text(desc)
        loc = (j.get("location") or {}).get("name") or ""
        out.append(
            Posting(
                source="greenhouse",
                external_id=str(j["id"]),
                company=company or j.get("company_name") or "",
                title=(j.get("title") or "").strip(),
                url=j.get("absolute_url") or "",
                locations=[s.strip() for s in loc.split(";") if s.strip()],
                description=desc,
                posted_at=parse_iso(j.get("first_published") or j.get("updated_at")),
                salary_min=lo,
                salary_max=hi,
            )
        )
    return out


def fetch(client: httpx.Client, company: dict) -> list[Posting]:
    data = get_json(client, URL.format(token=company["token"]), params={"content": "true"})
    return parse(data, company["name"])
