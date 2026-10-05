"""SAP SuccessFactors career sites built with Career Site Builder (e.g. careers.dteenergy.com).

No JSON API, so this reads two server-rendered pages that are stable across these sites:
  GET {base}/search/?q=...&startrow=N   result table: title link, location, sometimes a date
  GET {base}/job/<slug>/<id>/           schema.org JobPosting microdata: title, datePosted,
                                        address, description

The site's RSS feed (/services/rss/job/) has full descriptions but caps out at 20 items,
so it isn't used. Rows are filtered on title, age and country before any job page is fetched,
and only US postings are returned (like the Oracle and SmartRecruiters fetchers).
"""

from __future__ import annotations

import html
import html.entities
import logging
import re
import time
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

import httpx

from jobsearch.models import Posting
from jobsearch.normalize import html_to_text, parse_salary_text
from jobsearch.sources.base import SourceError, get_text

log = logging.getLogger(__name__)

MAX_PAGES_PER_SEARCH = 6
DETAIL_DELAY_S = 0.3
DEFAULT_SEARCHES = ["software engineer", "software developer"]

_ROW_RE = re.compile(r'<tr class="data-row.*?</tr>', re.S)
_HREF_RE = re.compile(r'href="(/job/[^"]+/(\d+)/)"')
_LOCATION_RE = re.compile(r'<span class="jobLocation">\s*(.*?)\s*</span>', re.S)
_ROW_DATE_RE = re.compile(r'<span class="jobDate">\s*(.*?)\s*</span>', re.S)
_META_RE = re.compile(r'<meta itemprop="(\w+)" content="([^"]*)"')


def base_url(url: str) -> str:
    parts = urlsplit(url.strip())
    if not parts.netloc:
        raise ValueError(f"not a URL: {url}")
    return f"https://{parts.netloc}"


def _clean(fragment: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", fragment)).split())


def _parse_date(text: str) -> datetime | None:
    text = (text or "").strip()
    for fmt in ("%a %b %d %H:%M:%S UTC %Y", "%b %d, %Y", "%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def parse_search_page(page: str) -> list[dict]:
    """Result rows -> [{id, title, path, location, posted}]."""
    rows = []
    for row in _ROW_RE.findall(page):
        href = _HREF_RE.search(row)
        title = re.search(r'class="jobTitle-link"[^>]*>(.*?)</a>', row, re.S)
        if not href or not title:
            continue
        loc = _LOCATION_RE.search(row)
        date = _ROW_DATE_RE.search(row)
        rows.append({
            "id": href.group(2),
            "path": html.unescape(href.group(1)),
            "title": _clean(title.group(1)),
            "location": _clean(loc.group(1)) if loc else "",
            "posted": _parse_date(_clean(date.group(1))) if date else None,
        })
    return rows


class _ItempropText(HTMLParser):
    """Collect the inner HTML of the first element with itemprop=<name>."""

    def __init__(self, name: str):
        super().__init__(convert_charrefs=False)
        self.name, self.depth, self.done, self.parts = name, 0, False, []

    def handle_starttag(self, tag, attrs):
        if self.done:
            return
        if self.depth:
            self.depth += 1
            self.parts.append(self.get_starttag_text())
        elif dict(attrs).get("itemprop") == self.name:
            self.depth = 1

    def handle_endtag(self, tag):
        if self.depth and not self.done:
            self.depth -= 1
            if self.depth == 0:
                self.done = True
            else:
                self.parts.append(f"</{tag}>")

    def handle_data(self, data):
        if self.depth and not self.done:
            self.parts.append(data)

    def handle_entityref(self, name):
        # A bare "&" ("R&D") also lands here; only re-add ";" for real entities.
        self.handle_data(f"&{name};" if name in html.entities.name2codepoint else f"&{name}")

    def handle_charref(self, name):
        self.handle_data(f"&#{name};")


def _itemprop_html(page: str, name: str) -> str:
    p = _ItempropText(name)
    p.feed(page)
    return "".join(p.parts)


def page_country(page: str) -> str:
    """The posting's ISO country code from its microdata ('' if absent)."""
    return dict(_META_RE.findall(page)).get("addressCountry", "").strip().upper()


def _row_country(location: str) -> str:
    """Country code from a result-row location ('' if unclear). Formats vary by site:
    'Valladolid, VA, ES', 'Southfield, MI, US, 48033', or 'Rabat, MA, 10000' (no country).
    Only parts after city and region are considered, so a region code is never taken for one."""
    parts = [p.strip() for p in location.split(",")]
    return next((p.upper() for p in parts[2:] if re.fullmatch(r"[A-Za-z]{2}", p)), "")


def parse_job_page(page: str, company: str, url: str, job_id: str, row: dict | None = None) -> Posting:
    meta = dict(_META_RE.findall(page))
    # 'City, ST' only: SuccessFactors' region codes collide with US state codes
    # (Valladolid, VA, ES / Rabat, MA), so non-US postings are dropped by country in fetch().
    loc_parts = [meta.get("addressLocality"), meta.get("addressRegion")]
    location = ", ".join(p.strip() for p in loc_parts if p and p.strip())
    if not location and row:
        location = row.get("location", "")
    desc = html_to_text(_itemprop_html(page, "description"))
    title = _clean(_itemprop_html(page, "title")) or (row or {}).get("title", "")
    lo, hi = parse_salary_text(desc)
    return Posting(
        source="successfactors",
        external_id=f"{urlsplit(url).netloc}:{job_id}",
        company=company,
        title=title,
        url=url,
        locations=[location] if location else [],
        description=desc,
        posted_at=_parse_date(meta.get("datePosted", "")) or (row or {}).get("posted"),
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
    base = base_url(company["url"])

    candidates: dict[str, dict] = {}
    seen: set[str] = set()
    for q in company.get("search") or DEFAULT_SEARCHES:
        start = 0
        for _ in range(MAX_PAGES_PER_SEARCH):
            rows = parse_search_page(get_text(client, f"{base}/search/", params={"q": q, "startrow": start}))
            new = [r for r in rows if r["id"] not in seen]
            seen.update(r["id"] for r in new)
            for r in new:
                too_old = r["posted"] is not None and r["posted"] < cutoff
                foreign = _row_country(r["location"]) not in ("", "US")
                if title_ok(r["title"]) and not too_old and not foreign:
                    candidates[r["id"]] = r
            if not rows or not new:
                break
            start += len(rows)

    out: list[Posting] = []
    for job_id, row in candidates.items():
        url = urljoin(base, row["path"])
        try:
            page = get_text(client, url)
        except SourceError as e:
            log.warning("successfactors detail failed for %s %s: %s", company["name"], job_id, e)
            continue
        time.sleep(DETAIL_DELAY_S)
        if page_country(page) not in ("", "US"):
            continue
        out.append(parse_job_page(page, company["name"], url, job_id, row))
    return out
