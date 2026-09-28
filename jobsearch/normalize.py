"""Pure helpers for cleaning up posting fields. No I/O."""

from __future__ import annotations

import html
import re
from urllib.parse import urlsplit, urlunsplit

# --- text -------------------------------------------------------------------

_TAG_RE = re.compile(r"<[^>]+>")
_BLOCK_TAG_RE = re.compile(r"</?(p|div|br|li|ul|ol|h[1-6]|tr)[^>]*>", re.I)
_WS_RE = re.compile(r"[ \t\xa0]+")


def html_to_text(raw: str) -> str:
    """Strip HTML (including entity-escaped HTML, as Greenhouse returns) to plain text."""
    if not raw:
        return ""
    # Greenhouse entity-escapes its HTML: unescape once to get tags, again after stripping them.
    text = html.unescape(raw)
    text = _BLOCK_TAG_RE.sub("\n", text)
    text = _TAG_RE.sub("", text)
    text = html.unescape(text)
    text = _WS_RE.sub(" ", text)
    lines = [ln.strip() for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln)


# --- titles & companies -----------------------------------------------------

_PARENS_RE = re.compile(r"\(.*?\)|\[.*?\]")  # "(2027)", "(Remote)", "[Hybrid]"
_SEGMENT_SPLIT_RE = re.compile(r"\s+[-–—|]\s+|,\s*")
_ROMAN = {"i": "1", "ii": "2", "iii": "3", "iv": "4"}


def normalize_title(title: str) -> str:
    """Canonical form for dedupe. Keeps specializations ("..., Payments") but drops
    trailing location suffixes ("... - Remote", "..., Detroit, MI") and parentheticals."""
    t = _PARENS_RE.sub(" ", title)
    segments = [s for s in _SEGMENT_SPLIT_RE.split(t) if s.strip()]
    while len(segments) > 1 and _looks_like_location(segments[-1]):
        segments.pop()
    t = " ".join(segments).lower()
    t = re.sub(r"[^a-z0-9/+ ]", " ", t)
    words = [_ROMAN.get(w, w) for w in t.split()]
    return " ".join(words)


_COMPANY_SUFFIX = re.compile(
    r"\b(inc|llc|ltd|corp|corporation|co|company|technologies|technology|group|holdings)\b\.?", re.I
)


def normalize_company(name: str) -> str:
    c = _COMPANY_SUFFIX.sub(" ", name.lower())
    return re.sub(r"[^a-z0-9]", "", c)


def normalize_url(url: str) -> str:
    """Lowercase host, drop query/fragment and trailing slash — for link-based dedupe.

    Exception: Greenhouse-hosted company pages carry the job id in ?gh_jid=, so keep it.
    """
    if not url:
        return ""
    parts = urlsplit(url.strip())
    query = ""
    m = re.search(r"(?:^|&)(gh_jid=\d+)", parts.query)
    if m:
        query = m.group(1)
    path = parts.path.rstrip("/")
    return urlunsplit((parts.scheme.lower() or "https", parts.netloc.lower(), path, query, ""))


# --- salary -----------------------------------------------------------------

SALARY_BUCKETS = ["N/A", "Below 60k", "60k - 70k", "70k - 80k", "80k - 90k", "100k+"]
HOURS_PER_YEAR = 2080

_MONEY_RE = re.compile(
    r"\$\s?(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)\s*([kK])?"
    r"(?:\s*(?:-|–|—|to)\s*\$?\s?(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)\s*([kK])?)?"
    r"(?:\s*(?:/|per)\s*(hour|hr|year|yr|annum|annually))?",
    re.I,
)


def _money(num: str, k: str | None) -> float:
    v = float(num.replace(",", ""))
    return v * 1000 if k else v


def parse_salary_text(text: str) -> tuple[float | None, float | None]:
    """Find the first plausible pay range in free text. Returns annual USD (min, max)."""
    for m in _MONEY_RE.finditer(text or ""):
        lo = _money(m.group(1), m.group(2))
        hi = _money(m.group(3), m.group(4)) if m.group(3) else lo
        unit = (m.group(5) or "").lower()
        if unit in ("hour", "hr"):
            lo, hi = lo * HOURS_PER_YEAR, hi * HOURS_PER_YEAR
        elif hi < 1000:  # "$50 million", "$40" with no unit: not an annual salary
            continue
        if 30_000 <= lo <= 1_000_000 and lo <= hi:
            return lo, hi
    return None, None


def salary_bucket(lo: float | None, hi: float | None) -> str:
    """Map an annual range onto the tracker's dropdown buckets using the midpoint.

    The sheet has no 90k–100k bucket, so midpoints of 90k+ go to 100k+.
    """
    if lo is None and hi is None:
        return "N/A"
    mid = ((lo or hi) + (hi or lo)) / 2
    if mid < 60_000:
        return "Below 60k"
    if mid < 70_000:
        return "60k - 70k"
    if mid < 80_000:
        return "70k - 80k"
    if mid < 90_000:
        return "80k - 90k"
    return "100k+"


# --- locations --------------------------------------------------------------

US_STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California",
    "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware", "FL": "Florida", "GA": "Georgia",
    "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa",
    "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi",
    "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire",
    "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York", "NC": "North Carolina",
    "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania",
    "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee",
    "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington",
    "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming", "DC": "District of Columbia",
}
_US_CITIES = {
    "san francisco", "sf", "nyc", "seattle", "boston", "chicago", "austin", "los angeles",
    "detroit", "ann arbor", "denver", "atlanta", "dallas", "houston", "pittsburgh",
    "palo alto", "mountain view", "menlo park", "san jose", "san diego", "washington dc",
    "bay area", "silicon valley", "miami", "philadelphia", "raleigh", "grand rapids",
}
_US_CITY_RE = re.compile(r"\b(" + "|".join(sorted(_US_CITIES)) + r")\b", re.I)
_STATE_ABBR_RE = re.compile(r"(?:,|\s)\s*(" + "|".join(US_STATES) + r")\b")
_STATE_NAME_RE = re.compile(r"\b(" + "|".join(US_STATES.values()) + r")\b", re.I)
_US_RE = re.compile(r"\b(united states|usa|u\.s\.a?\.?|us|america)\b", re.I)
_NON_US_RE = re.compile(
    r"\b(india|canada|united kingdom|uk|england|ireland|germany|singapore|australia|japan|france|"
    r"netherlands|mexico|brazil|poland|spain|israel|china|korea|philippines|argentina|colombia|"
    r"portugal|sweden|switzerland|romania|serbia|vietnam|taiwan|hong kong|emea|apac|latam|"
    r"bangalore|bengaluru|hyderabad|pune|chennai|london|toronto|vancouver|montreal|dublin|berlin|"
    r"munich|amsterdam|tokyo|sydney|tel aviv|warsaw|krakow|paris|madrid|lisbon)\b",
    re.I,
)
_UNSPECIFIED_RE = re.compile(r"^\s*(remote|multiple|numerous|various|anywhere|hybrid)?\s*$", re.I)


def is_us_location(loc: str) -> bool | None:
    """True if clearly US, False if clearly not, None if unknown (e.g. bare 'Remote')."""
    if _UNSPECIFIED_RE.match(loc or ""):
        return None
    if _NON_US_RE.search(loc) and not _US_RE.search(loc):
        return False
    if _STATE_ABBR_RE.search(loc) or _STATE_NAME_RE.search(loc) or _US_RE.search(loc):
        return True
    return bool(_US_CITY_RE.search(loc))


def _looks_like_location(segment: str) -> bool:
    """Stricter than is_us_location: 'US Government' in a title is not a location."""
    seg = segment.strip()
    return bool(
        _UNSPECIFIED_RE.match(seg)
        or re.fullmatch(r"(remote|hybrid)?\s*-?\s*(us|usa|united states)", seg, re.I)
        or _STATE_ABBR_RE.search(" " + seg)
        or _STATE_NAME_RE.fullmatch(seg)
        or _US_CITY_RE.fullmatch(seg)
        or re.fullmatch(r"[A-Z][a-zA-Z .]+,\s*[A-Z]{2}", seg)
    )


def us_locations(locations: list[str]) -> list[str] | None:
    """Keep only US/unknown locations. Returns None if every location is clearly non-US."""
    if not locations:
        return []
    verdicts = [(loc, is_us_location(loc)) for loc in locations]
    kept = [loc for loc, v in verdicts if v is not False]
    return kept or None


def merge_locations(groups: list[list[str]]) -> list[str]:
    seen: dict[str, str] = {}
    for locs in groups:
        for loc in locs:
            key = loc.strip().lower()
            if key and key not in seen:
                seen[key] = loc.strip()
    return list(seen.values())
