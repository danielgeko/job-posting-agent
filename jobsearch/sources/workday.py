"""Workday career sites, via the JSON API the career site itself calls.

Undocumented but public and unauthenticated:
  POST {base}/wday/cxs/{tenant}/{site}/jobs         search (limit <= 20 per page)
  GET  {base}/wday/cxs/{tenant}/{site}{externalPath} one posting, with description

Search results carry only a title, a relative "Posted N Days Ago" and a location
summary, so we filter on title and age first and fetch details only for survivors.
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

PAGE_SIZE = 20  # the API rejects larger pages with HTTP 400
MAX_PAGES_PER_SEARCH = 5
DETAIL_DELAY_S = 0.3
DEFAULT_SEARCHES = ["software engineer", "software developer"]

_LOCALE_RE = re.compile(r"^[a-z]{2}-[A-Za-z]{2}$")
_HOST_RE = re.compile(r"^([\w-]+)\.(wd\d+)\.myworkdayjobs\.com$", re.I)


def parse_site_url(url: str) -> tuple[str, str, str]:
    """'https://generalmotors.wd5.myworkdayjobs.com/en-US/Careers_GM' -> (base, tenant, site)."""
    parts = urlsplit(url.strip())
    m = _HOST_RE.match(parts.netloc)
    if not m:
        raise ValueError(f"not a myworkdayjobs.com URL: {url}")
    segs = [s for s in parts.path.split("/") if s]
    if segs and _LOCALE_RE.match(segs[0]):
        segs = segs[1:]
    if not segs:
        raise ValueError(f"no career site name in URL: {url}")
    return f"https://{parts.netloc}", m.group(1), segs[0]


def parse_posted_on(text: str, now: datetime) -> datetime | None:
    """'Posted Today' / 'Posted Yesterday' / 'Posted 3 Days Ago' / 'Posted 30+ Days Ago'."""
    t = (text or "").lower()
    if "today" in t:
        return now
    if "yesterday" in t:
        return now - timedelta(days=1)
    m = re.search(r"(\d+)\+?\s*days?", t)
    return now - timedelta(days=int(m.group(1))) if m else None


def _post_json(client: httpx.Client, url: str, body: dict, attempts: int = 3):
    delay = 2.0
    for attempt in range(1, attempts + 1):
        try:
            resp = client.post(url, json=body)
        except httpx.TransportError as e:
            if attempt == attempts:
                raise SourceError(f"{url}: {e}") from e
        else:
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code not in (429, 500, 502, 503, 504) or attempt == attempts:
                raise SourceError(f"{url}: HTTP {resp.status_code}")
        time.sleep(delay)
        delay *= 2
    raise SourceError(url)  # unreachable


def search(client: httpx.Client, base: str, tenant: str, site: str, text: str) -> list[dict]:
    url = f"{base}/wday/cxs/{tenant}/{site}/jobs"
    out: list[dict] = []
    for page in range(MAX_PAGES_PER_SEARCH):
        body = {"appliedFacets": {}, "limit": PAGE_SIZE, "offset": page * PAGE_SIZE, "searchText": text}
        data = _post_json(client, url, body)
        batch = data.get("jobPostings") or []
        out.extend(batch)
        if len(batch) < PAGE_SIZE or len(out) >= (data.get("total") or 0):
            break
    return out


def parse_detail(info: dict, company: str, tenant: str, fallback_posted: datetime | None) -> Posting:
    desc = html_to_text(info.get("jobDescription") or "")
    lo, hi = parse_salary_text(desc)
    locs = [info.get("location") or ""] + list(info.get("additionalLocations") or [])
    return Posting(
        source="workday",
        # Req IDs are only unique within a tenant.
        external_id=f"{tenant}:{info.get('jobReqId') or info.get('id') or info.get('externalUrl')}",
        company=company,
        title=(info.get("title") or "").strip(),
        url=info.get("externalUrl") or "",
        locations=[loc.strip() for loc in locs if loc and loc.strip()],
        description=desc,
        # startDate is the posting's start date on the site; fall back to "Posted N Days Ago".
        posted_at=parse_iso(info.get("startDate")) or fallback_posted,
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
    base, tenant, site = parse_site_url(company["url"])
    hits: dict[str, dict] = {}
    for text in company.get("search") or DEFAULT_SEARCHES:
        for j in search(client, base, tenant, site, text):
            if j.get("externalPath"):
                hits.setdefault(j["externalPath"], j)

    cutoff = now - timedelta(days=max_age_days)
    out: list[Posting] = []
    for path, j in hits.items():
        posted = parse_posted_on(j.get("postedOn", ""), now)
        if not title_ok(j.get("title") or "") or (posted and posted < cutoff):
            continue
        try:
            data = get_json(client, f"{base}/wday/cxs/{tenant}/{site}{path}")
        except SourceError as e:
            log.warning("workday detail failed for %s: %s", path, e)
            continue
        out.append(parse_detail(data.get("jobPostingInfo") or {}, company["name"], tenant, posted))
        time.sleep(DETAIL_DELAY_S)
    log.debug("workday %s: %d search hits, %d fetched", company["name"], len(hits), len(out))
    return out
