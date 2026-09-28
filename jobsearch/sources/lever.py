"""Lever Postings API: https://github.com/lever/postings-api"""

from __future__ import annotations

import httpx

from jobsearch.models import Posting
from jobsearch.normalize import HOURS_PER_YEAR, html_to_text, parse_salary_text
from jobsearch.sources.base import from_epoch, get_json

URL = "https://api.lever.co/v0/postings/{token}"


def _salary(j: dict, desc: str) -> tuple[float | None, float | None]:
    sr = j.get("salaryRange") or {}
    if sr.get("min") and (sr.get("currency") or "USD") == "USD":
        mult = HOURS_PER_YEAR if "hour" in (sr.get("interval") or "").lower() else 1
        return sr["min"] * mult, (sr.get("max") or sr["min"]) * mult
    return parse_salary_text(desc)


def parse(data: list, company: str) -> list[Posting]:
    out = []
    for j in data:
        cats = j.get("categories") or {}
        locs = cats.get("allLocations") or ([cats["location"]] if cats.get("location") else [])
        parts = [j.get("descriptionPlain") or ""]
        for lst in j.get("lists") or []:
            parts.append(lst.get("text", ""))
            parts.append(lst.get("content", ""))
        parts.append(j.get("additionalPlain") or "")
        desc = html_to_text("\n".join(parts))
        lo, hi = _salary(j, desc)
        out.append(
            Posting(
                source="lever",
                external_id=j["id"],
                company=company,
                title=(j.get("text") or "").strip(),
                url=j.get("hostedUrl") or "",
                locations=locs,
                description=desc,
                posted_at=from_epoch(j.get("createdAt"), millis=True),
                salary_min=lo,
                salary_max=hi,
            )
        )
    return out


def fetch(client: httpx.Client, company: dict) -> list[Posting]:
    data = get_json(client, URL.format(token=company["token"]), params={"mode": "json"})
    return parse(data, company["name"])
