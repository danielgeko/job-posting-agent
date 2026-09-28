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


def cmd_check_sources(args, settings) -> int:
    from jobsearch.sources.base import make_client

    companies = load_companies(settings.companies_path)
    bad = 0
    with make_client() as client:
        for c in companies:
            if c.get("ats") not in PROBE_URLS or not c.get("enabled", True):
                print(f"  skip  {c['name']} ({c.get('ats')}, enabled={c.get('enabled', True)})")
                continue
            n = _probe(client, c["ats"], c["token"])
            ok = n is not None
            bad += not ok
            print(f"  {'ok ' if ok else 'FAIL'}  {c['name']:30} {c['ats']:10} {c['token']:25} "
                  f"{'' if n is None else f'{n} jobs'}")
    return 1 if bad else 0


def _slug_candidates(name: str) -> list[str]:
    base = re.sub(r"[^a-z0-9 ]", "", name.lower()).strip()
    words = base.split()
    cands = ["".join(words), "-".join(words)]
    return list(dict.fromkeys(c for c in cands if c))


def cmd_find_boards(args, settings) -> int:
    """Probe Greenhouse/Lever/Ashby for each company name and print companies.yaml entries."""
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
            else:
                misses.append(name)
    if misses:
        print("\n# Not found on Greenhouse/Lever/Ashby (likely Workday or custom):")
        for m in misses:
            print(f"#   {m}")
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
        "--source", action="append", choices=["greenhouse", "lever", "ashby", "simplify"],
        help="limit to a source (repeatable)",
    )
    p_run.set_defaults(func=cmd_run)

    p_check = sub.add_parser("check-sources", help="verify every board in companies.yaml responds")
    p_check.set_defaults(func=cmd_check_sources)

    p_find = sub.add_parser("find-boards", help="probe ATS boards for company names (default: tracker column A)")
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
