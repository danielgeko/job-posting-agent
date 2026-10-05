"""Command line entry point: `python -m jobsearch <command>`."""

from __future__ import annotations

import argparse
import logging
import re
import sys
from datetime import date

from jobsearch.config import load_companies, load_settings

log = logging.getLogger("jobsearch")

PROBE_URLS = {
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{token}/jobs",
    "lever": "https://api.lever.co/v0/postings/{token}?mode=json&limit=1",
    "ashby": "https://api.ashbyhq.com/posting-api/job-board/{token}",
}


def setup_logging(logs_dir, verbose: bool) -> None:
    logs_dir.mkdir(exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    file_handler = logging.FileHandler(logs_dir / f"{date.today().isoformat()}.log")
    file_handler.setFormatter(fmt)
    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(fmt)
    console.setLevel(logging.DEBUG if verbose else logging.INFO)
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    root.handlers[:] = [file_handler, console]
    for noisy in ("httpx", "httpx2", "httpcore", "anthropic", "urllib3", "google"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def cmd_run(args, settings) -> int:
    from jobsearch.pipeline import RunOptions, run

    opts = RunOptions(
        dry_run=args.dry_run or args.no_score,
        score=not args.no_score,
        sources=set(args.source) if args.source else None,
    )
    res = run(settings, opts)
    s = res.stats
    print(
        f"\nfetched {s.get('fetched', 0)} ({s.get('new', 0)} new) → "
        f"{s.get('passed_filters', 0)} passed filters → "
        f"{s.get('candidate_leads', 0)} not already known"
    )
    if s.get("filtered"):
        print("filtered out:", ", ".join(f"{k}={v}" for k, v in sorted(s["filtered"].items())))
    if res.unscored:
        print(f"\n{len(res.unscored)} unscored candidates:")
        for g in res.unscored:
            p = g.primary
            print(f"  {p.company[:24]:24} | {p.title[:60]:60} | {'; '.join(g.locations)[:40]}")
    if "scored" in s:
        print(f"scored: {s['scored']}; {s.get('above_threshold', 0)} at or above {settings.score_threshold}")
    for lead in res.leads:
        print(f"  {lead.fit_score:3} | {lead.company[:22]:22} | {lead.role[:50]:50} | {lead.why}")
    if opts.dry_run and res.leads:
        print("\n(dry run: nothing written to the Sheet)")
    elif not opts.dry_run:
        print(f"wrote {s.get('written', 0)} leads to '{settings.leads_tab}'")
    if res.errors:
        print(f"\n{len(res.errors)} errors (see logs/):", *res.errors[:10], sep="\n  ")
    return 0


def _probe(client, ats: str, token: str) -> int | None:
    """Return the number of open jobs on the board, or None if it doesn't exist."""
    try:
        r = client.get(PROBE_URLS[ats].format(token=token))
    except Exception:
        return None
    if r.status_code != 200:
        return None
    data = r.json()
    if ats == "lever":
        return len(data) if isinstance(data, list) else None
    return len(data.get("jobs", []))


def _probe_search_site(client, ats: str, where: str) -> int | None:
    """Job count for a Workday / Oracle / SmartRecruiters / SuccessFactors site, or None if it
    doesn't respond. SuccessFactors reports only the first page of results."""
    from jobsearch.sources import oracle, smartrecruiters, successfactors, workday

    try:
        if ats == "workday":
            base, tenant, site = workday.parse_site_url(where)
            r = client.post(
                f"{base}/wday/cxs/{tenant}/{site}/jobs",
                json={"appliedFacets": {}, "limit": 1, "offset": 0, "searchText": ""},
            )
            return r.json().get("total") if r.status_code == 200 else None
        if ats == "oracle":
            host, site = oracle.parse_site_url(where)
            return int(oracle.search_page(client, host, site, "", 0).get("TotalJobsCount") or 0)
        if ats == "smartrecruiters":
            r = client.get(smartrecruiters.URL.format(token=where), params={"limit": 1})
            return r.json().get("totalFound") if r.status_code == 200 else None
        if ats == "successfactors":
            # No total count in the page; report rows on the first page of an empty search.
            r = client.get(f"{successfactors.base_url(where)}/search/", params={"q": "", "startrow": 0})
            if r.status_code != 200:
                return None
            rows = successfactors.parse_search_page(r.text)
            return len(rows) if rows else None
    except Exception:
        return None
    return None


def _company_location(c: dict) -> str:
    return c.get("url") or c.get("token") or ""


def cmd_check_sources(args, settings) -> int:
    from jobsearch.sources import ALL_ATS
    from jobsearch.sources.base import make_client

    companies = load_companies(settings.companies_path)
    bad = 0
    with make_client() as client:
        for c in companies:
            ats = c.get("ats")
            if not c.get("enabled", True) or ats not in ALL_ATS:
                print(f"  skip  {c['name']} ({ats}, enabled={c.get('enabled', True)})")
                continue
            where = _company_location(c)
            n = _probe(client, ats, where) if ats in PROBE_URLS else _probe_search_site(client, ats, where)
            ok = n is not None
            bad += not ok
            print(f"  {'ok ' if ok else 'FAIL'}  {c['name']:30} {ats:15} {where[:60]:60} "
                  f"{'' if n is None else f'{n} jobs'}")
    return 1 if bad else 0


def _slug_candidates(name: str) -> list[str]:
    base = re.sub(r"[^a-z0-9 ]", "", name.lower()).strip()
    words = base.split()
    cands = ["".join(words), "-".join(words)]
    return list(dict.fromkeys(c for c in cands if c))


# Career site names that aren't the public careers site.
_SITE_SKIP_RE = re.compile(r"restricted|contractor|confidential|internal|subsidiary", re.I)
# Site names worth suggesting alongside the main one.
_SITE_EARLY_RE = re.compile(r"new.?grad|early|university|campus|graduate|student|futureforce", re.I)
_HOST_HINTS = [
    ("myworkdayjobs.com", "Workday, but its API didn't respond"),
    ("oraclecloud.com", "Oracle Recruiting Cloud, but its API didn't respond"),
    ("smartrecruiters.com", "SmartRecruiters, but its API didn't respond"),
    ("icims.com", "iCIMS"),
    ("avature.net", "Avature"),
    ("bamboohr.com", "BambooHR"),
    ("workable.com", "Workable"),
    ("applytojob.com", "JazzHR"),
    ("successfactors", "SAP SuccessFactors (add its career site URL with ats: successfactors)"),
    ("taleo.net", "Taleo"),
]


def _board_from_url(url: str) -> tuple[str, str] | None:
    """Map a posting URL to (ats, companies.yaml location) for search-based systems."""
    from jobsearch.sources import oracle, workday

    try:
        base, _, site = workday.parse_site_url(url)
        return "workday", f"{base}/{site}"
    except ValueError:
        pass
    try:
        host, site = oracle.parse_site_url(url)
        return "oracle", f"https://{host}/hcmUI/CandidateExperience/en/sites/{site}"
    except ValueError:
        pass
    m = re.match(r"https://(?:jobs|careers)\.smartrecruiters\.com/([^/?#]+)", url)
    if m:
        return "smartrecruiters", m.group(1)
    return None


class SimplifyIndex:
    """Company -> career hosts / known boards, built from the SimplifyJobs apply URLs."""

    def __init__(self, entries: list[dict]):
        from collections import Counter, defaultdict

        from jobsearch.normalize import normalize_company

        self._norm = normalize_company
        self.boards: dict[str, Counter] = defaultdict(Counter)  # key -> (ats, location) counts
        self.hosts: dict[str, Counter] = defaultdict(Counter)
        for j in entries:
            key = normalize_company(j.get("company_name") or "")
            url = j.get("url") or ""
            if not key or not url.startswith("http"):
                continue
            self.hosts[key][url.split("/")[2]] += 1
            board = _board_from_url(url)
            if board:
                self.boards[key][board] += 1

    @classmethod
    def fetch(cls, client) -> "SimplifyIndex":
        from jobsearch.sources import github_lists
        from jobsearch.sources.base import get_json

        return cls(get_json(client, github_lists.SIMPLIFY_URL))

    def _key(self, name: str) -> str | None:
        """Exact normalized match, else the biggest company whose name starts with this one
        (or vice versa), e.g. 'Auto-Owners' -> 'Auto-Owners Insurance'."""
        n = self._norm(name)
        if n in self.hosts:
            return n
        if len(n) < 4:
            return None
        cands = [k for k in self.hosts if len(k) >= 4 and (k.startswith(n) or n.startswith(k))]
        return max(cands, key=lambda k: sum(self.hosts[k].values()), default=None)

    def boards_for(self, name: str) -> list[tuple[str, str]]:
        """(ats, location) candidates: the main public site first, then any early-career sites."""
        key = self._key(name)
        if not key:
            return []
        ranked = [b for b, _ in self.boards[key].most_common() if not _SITE_SKIP_RE.search(b[1])]
        # Case variants of the same site ("EXTERNAL_CAREERS" / "external_careers") collapse.
        ranked = list({(a, loc.lower()): (a, loc) for a, loc in reversed(ranked)}.values())[::-1]
        if not ranked:
            return []
        return [ranked[0]] + [b for b in ranked[1:] if _SITE_EARLY_RE.search(b[1])]

    def host_hint(self, name: str) -> str:
        key = self._key(name)
        if not key:
            return "not in SimplifyJobs data"
        host = self.hosts[key].most_common(1)[0][0]
        system = next((label for frag, label in _HOST_HINTS if frag in host), "custom site")
        return f"{system} ({host})"


def cmd_find_boards(args, settings) -> int:
    """Probe Greenhouse/Lever/Ashby for each company name, fall back to Workday / Oracle /
    SmartRecruiters sites seen in the SimplifyJobs data, and print companies.yaml entries."""
    from jobsearch.sources.base import make_client

    names = args.names
    if not names:
        from jobsearch import sheets

        tracker_ws, _ = sheets.open_worksheets(
            settings.google_sa_json, settings.spreadsheet_id, settings.tracker_tab, settings.leads_tab
        )
        names = [r[0] for r in tracker_ws.get("A3:A") if r and r[0].strip()]
    names = list(dict.fromkeys(n.strip() for n in names))
    existing = {c["name"].lower() for c in load_companies(settings.companies_path)}
    print("# Paste the hits into companies.yaml (verify each name/token first).")
    misses = []
    with make_client() as client:
        index = SimplifyIndex.fetch(client)
        for name in names:
            if name.lower() in existing:
                continue
            hit = None
            for token in _slug_candidates(name):
                for ats in PROBE_URLS:
                    n = _probe(client, ats, token)
                    if n:  # a board with 0 jobs is indistinguishable from a squatted slug
                        hit = (ats, token, n)
                        break
                if hit:
                    break
            if hit:
                print(f"  - {{name: {name!r}, ats: {hit[0]}, token: {hit[1]}, enabled: true}}  # {hit[2]} jobs")
                continue
            found = False
            for ats, where in index.boards_for(name):
                n = _probe_search_site(client, ats, where)
                if n:
                    found = True
                    field = "token" if ats == "smartrecruiters" else "url"
                    print(f"  - {{name: {name!r}, ats: {ats}, {field}: {where!r}, enabled: true}}  # {n} jobs")
            if not found:
                misses.append((name, index.host_hint(name)))
    if misses:
        print("\n# Not found automatically (careers system in parentheses, from SimplifyJobs links).")
        print("# Workday / Oracle / SmartRecruiters sites can be added by URL; other systems aren't")
        print("# supported yet, and SimplifyJobs still covers them.")
        width = max(len(m) for m, _ in misses)
        for m, hint in misses:
            print(f"#   {m:{width}}  {hint}")
    return 0


def _check_dependencies() -> None:
    try:
        import anthropic, gspread, httpx, yaml  # noqa: F401, E401
    except ModuleNotFoundError as e:
        sys.exit(
            f"Missing dependency '{e.name}': this Python ({sys.executable}) isn't the project's.\n"
            "Use the project virtualenv:  .venv/bin/python -m jobsearch ...\n"
            "or activate it first:        source .venv/bin/activate"
        )


def main(argv: list[str] | None = None) -> int:
    _check_dependencies()
    from jobsearch.sources import ALL_ATS
    parser = argparse.ArgumentParser(prog="jobsearch", description=__doc__)
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="fetch, filter, score, and write leads")
    p_run.add_argument("--dry-run", action="store_true", help="print leads instead of writing to the Sheet")
    p_run.add_argument("--no-score", action="store_true", help="stop after dedupe (implies --dry-run)")
    p_run.add_argument(
        "--source", action="append", choices=[*ALL_ATS, "simplify"],
        help="limit to a source (repeatable)",
    )
    p_run.set_defaults(func=cmd_run)

    p_check = sub.add_parser("check-sources", help="verify every board in companies.yaml responds")
    p_check.set_defaults(func=cmd_check_sources)

    p_find = sub.add_parser("find-boards", help="find job boards for company names (default: tracker column A)")
    p_find.add_argument("names", nargs="*")
    p_find.set_defaults(func=cmd_find_boards)

    args = parser.parse_args(argv)
    settings = load_settings()
    setup_logging(settings.logs_dir, args.verbose)
    from jobsearch.sheets import SheetConfigError, SheetLayoutError

    try:
        return args.func(args, settings)
    except (SheetConfigError, SheetLayoutError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
