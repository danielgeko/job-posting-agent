"""Oracle Recruiting Cloud (Oracle Fusion HCM "Candidate Experience") career sites.

Uses the public REST resources the career site itself calls (no auth):
  GET https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions
      ?finder=findReqs;siteNumber={site},keyword="...",limit=25,offset=N,sortBy=POSTING_DATES_DESC
  GET https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails
      ?finder=ById;Id="{id}",siteNumber={site}

Search results are newest first and carry title, posted date and country, so we stop
paging at the age cutoff and fetch details only for US postings with a passing title.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

import httpx

from jobsearch.models import Posting
from jobsearch.normalize import html_to_text, parse_salary_text
from jobsearch.sources.base import SourceError, get_json, parse_iso

log = logging.getLogger(__name__)

PAGE_SIZE = 25
MAX_PAGES_PER_SEARCH = 8
DETAIL_DELAY_S = 0.3
DEFAULT_SEARCHES = ["software engineer", "software developer"]

_SITE_RE = re.compile(r"/sites/([^/?#]+)")


def parse_site_url(url: str) -> tuple[str, str]:
    """'https://jpmc.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1001/job/1' -> (host, site)."""
    parts = urlsplit(url.strip())
    m = _SITE_RE.search(parts.path)
    if not parts.netloc.endswith("oraclecloud.com") or not m:
        raise ValueError(f"not an Oracle Candidate Experience site URL: {url}")
    return parts.netloc, m.group(1)


def _api(host: str, resource: str) -> str:
    return f"https://{host}/hcmRestApi/resources/latest/{resource}"


def search_page(client: httpx.Client, host: str, site: str, keyword: str, offset: int) -> dict:
    # An empty keyword="" matches nothing, so leave it out to list everything.
    kw = f'keyword="{keyword}",' if keyword else ""
    finder = f"findReqs;siteNumber={site},{kw}limit={PAGE_SIZE},offset={offset},sortBy=POSTING_DATES_DESC"
    data = get_json(
        client,
        _api(host, "recruitingCEJobRequisitions"),
        params={"onlyData": "true", "expand": "requisitionList.secondaryLocations", "finder": finder},
    )
    items = data.get("items") or [{}]
    return items[0]


def _is_us(req: dict) -> bool:
    countries = {req.get("PrimaryLocationCountry")} | {
        s.get("CountryCode") for s in req.get("secondaryLocations") or []
    }
    countries.discard(None)
    return not countries or "US" in countries


def parse_detail(item: dict, company: str, host: str, site: str) -> Posting:
    parts = [
        item.get("ExternalDescriptionStr"),
        item.get("ExternalResponsibilitiesStr"),
        item.get("ExternalQualificationsStr"),
    ]
    desc = html_to_text("\n".join(p for p in parts if p))
    flex = " ".join(
        f.get("Value") or ""
        for f in item.get("requisitionFlexFields") or []
        if re.search(r"pay|salary|compensation", f.get("Prompt") or "", re.I)
    )
    lo, hi = parse_salary_text(flex)
    if lo is None:
        lo, hi = parse_salary_text(desc)
    locs = [item.get("PrimaryLocation") or ""] + [
        s.get("Name") or "" for s in item.get("secondaryLocations") or []
    ]
    job_id = str(item["Id"])
    return Posting(
        source="oracle",
        external_id=f"{host.split('.')[0]}:{job_id}",
        company=company,
        title=(item.get("Title") or "").strip(),
        url=f"https://{host}/hcmUI/CandidateExperience/en/sites/{site}/job/{job_id}",
        locations=[loc.strip() for loc in locs if loc and loc.strip()],
        description=desc,
        posted_at=parse_iso(item.get("ExternalPostedStartDate")),
        salary_min=lo,
        salary_max=hi,
        salary_raw=flex[:200],
    )


def fetch(
    client: httpx.Client,
    company: dict,
    title_ok: Callable[[str], bool] = lambda t: True,
    max_age_days: int = 30,
    now: datetime | None = None,
) -> list[Posting]:
    now = now or datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=max_age_days)).date().isoformat()
    host, site = parse_site_url(company["url"])

    candidates: dict[str, dict] = {}
    for keyword in company.get("search") or DEFAULT_SEARCHES:
        for page in range(MAX_PAGES_PER_SEARCH):
            result = search_page(client, host, site, keyword, page * PAGE_SIZE)
            reqs = result.get("requisitionList") or []
            for r in reqs:
                if (r.get("PostedDate") or "9999") >= cutoff and title_ok(r.get("Title") or "") and _is_us(r):
                    candidates.setdefault(str(r["Id"]), r)
            # Sorted newest first: once a page ends past the cutoff, later pages are older.
            oldest = min((r.get("PostedDate") or "9999" for r in reqs), default="")
            if len(reqs) < PAGE_SIZE or oldest < cutoff:
                break

    out: list[Posting] = []
    for job_id in candidates:
        try:
            data = get_json(
                client,
                _api(host, "recruitingCEJobRequisitionDetails"),
                params={"onlyData": "true", "expand": "all", "finder": f'ById;Id="{job_id}",siteNumber={site}'},
            )
        except SourceError as e:
            log.warning("oracle detail failed for %s %s: %s", company["name"], job_id, e)
            continue
        items = data.get("items") or []
        if items:
            out.append(parse_detail(items[0], company["name"], host, site))
        time.sleep(DETAIL_DELAY_S)
    return out
