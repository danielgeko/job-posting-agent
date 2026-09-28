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
    for noisy in ("httpx", "httpcore", "anthropic", "urllib3", "google"):
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


def _probe_workday(client, url: str) -> int | None:
    """Return the site's total job count, or None if the site doesn't respond."""
    from jobsearch.sources import workday

    try:
        base, tenant, site = workday.parse_site_url(url)
        r = client.post(
            f"{base}/wday/cxs/{tenant}/{site}/jobs",
            json={"appliedFacets": {}, "limit": 1, "offset": 0, "searchText": ""},
        )
    except Exception:
        return None
    return r.json().get("total") if r.status_code == 200 else None


def cmd_check_sources(args, settings) -> int:
    from jobsearch.sources.base import make_client

    companies = load_companies(settings.companies_path)
    bad = 0
    with make_client() as client:
        for c in companies:
            ats = c.get("ats")
            if not c.get("enabled", True) or (ats not in PROBE_URLS and ats != "workday"):
                print(f"  skip  {c['name']} ({ats}, enabled={c.get('enabled', True)})")
                continue
            if ats == "workday":
                where = c.get("url", "")
                n = _probe_workday(client, where)
            else:
                where = c["token"]
                n = _probe(client, ats, where)
            ok = n is not None
            bad += not ok
            print(f"  {'ok ' if ok else 'FAIL'}  {c['name']:30} {ats:10} {where[:60]:60} "
                  f"{'' if n is None else f'{n} jobs'}")
    return 1 if bad else 0


def _slug_candidates(name: str) -> list[str]:
    base = re.sub(r"[^a-z0-9 ]", "", name.lower()).strip()
    words = base.split()
    cands = ["".join(words), "-".join(words)]
    return list(dict.fromkeys(c for c in cands if c))


# Workday site names that aren't the public careers site.
_WORKDAY_SKIP_RE = re.compile(r"restricted|contractor|confidential|internal|subsidiary", re.I)
# Site names worth suggesting alongside the main one.
_WORKDAY_EARLY_RE = re.compile(r"new.?grad|early|university|campus|graduate|student|futureforce", re.I)
_HOST_HINTS = [
    ("myworkdayjobs.com", "Workday, but its API didn't respond"),
    ("oraclecloud.com", "Oracle Recruiting Cloud"),
    ("icims.com", "iCIMS"),
    ("smartrecruiters.com", "SmartRecruiters"),
    ("avature.net", "Avature"),
    ("bamboohr.com", "BambooHR"),
    ("workable.com", "Workable"),
    ("applytojob.com", "JazzHR"),
    ("successfactors", "SAP SuccessFactors"),
    ("taleo.net", "Taleo"),
]


class SimplifyIndex:
    """Company -> career hosts / Workday sites, built from the SimplifyJobs apply URLs."""

    def __init__(self, entries: list[dict]):
        from collections import Counter, defaultdict

        from jobsearch.normalize import normalize_company
        from jobsearch.sources import workday

        self._norm = normalize_company
        self.sites: dict[str, Counter] = defaultdict(Counter)
        self.hosts: dict[str, Counter] = defaultdict(Counter)
        for j in entries:
            key = normalize_company(j.get("company_name") or "")
            url = j.get("url") or ""
            if not key or not url.startswith("http"):
                continue
            self.hosts[key][url.split("/")[2]] += 1
            try:
                base, _, site = workday.parse_site_url(url)
            except ValueError:
                continue
            self.sites[key][f"{base}/{site}"] += 1

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

    def workday_sites(self, name: str) -> list[str]:
        """Main public site first, then any early-career sites."""
        key = self._key(name)
        if not key:
            return []
        ranked = [u for u, _ in self.sites[key].most_common() if not _WORKDAY_SKIP_RE.search(u)]
        # Case variants of the same site ("EXTERNAL_CAREERS" / "external_careers") collapse.
        ranked = list({u.lower(): u for u in reversed(ranked)}.values())[::-1]
        if not ranked:
            return []
        return [ranked[0]] + [u for u in ranked[1:] if _WORKDAY_EARLY_RE.search(u)]

    def host_hint(self, name: str) -> str:
        key = self._key(name)
        if not key:
            return "not in SimplifyJobs data"
        host = self.hosts[key].most_common(1)[0][0]
        system = next((label for frag, label in _HOST_HINTS if frag in host), "custom site")
        return f"{system} ({host})"


def cmd_find_boards(args, settings) -> int:
    """Probe Greenhouse/Lever/Ashby for each company name, fall back to Workday sites
    seen in the SimplifyJobs data, and print companies.yaml entries."""
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
            for url in index.workday_sites(name):
                n = _probe_workday(client, url)
                if n is not None:
                    found = True
                    print(f"  - {{name: {name!r}, ats: workday, url: {url!r}, enabled: true}}  # {n} jobs")
            if not found:
                misses.append((name, index.host_hint(name)))
    if misses:
        print("\n# Not found automatically (careers system in parentheses, from SimplifyJobs links).")
        print("# For Workday, copy the careers URL (https://<co>.wdN.myworkdayjobs.com/<Site>) into an")
        print("# `ats: workday` entry. Other systems aren't supported yet; SimplifyJobs still covers them.")
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
    parser = argparse.ArgumentParser(prog="jobsearch", description=__doc__)
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="fetch, filter, score, and write leads")
    p_run.add_argument("--dry-run", action="store_true", help="print leads instead of writing to the Sheet")
    p_run.add_argument("--no-score", action="store_true", help="stop after dedupe (implies --dry-run)")
    p_run.add_argument(
        "--source", action="append", choices=["greenhouse", "lever", "ashby", "workday", "simplify"],
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
