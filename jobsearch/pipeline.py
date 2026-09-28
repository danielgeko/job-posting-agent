"""Orchestrates one run: discover → filter → dedupe → score → write."""

from __future__ import annotations

import logging
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

from jobsearch import filters
from jobsearch.config import Settings, load_companies
from jobsearch.db import DB
from jobsearch.dedupe import Group, KnownJobs, group_postings
from jobsearch.models import Lead, Posting, Score
from jobsearch.normalize import normalize_company, salary_bucket
from jobsearch.sources import ATS_FETCHERS, github_lists, workday
from jobsearch.sources.base import SourceError, make_client

log = logging.getLogger(__name__)

MAX_LOCATIONS_SHOWN = 4
SCORING_WORKERS = 4


@dataclass
class RunOptions:
    dry_run: bool = False
    score: bool = True
    sources: set[str] | None = None  # None = all; else subset of {greenhouse, lever, ashby, workday, simplify}


@dataclass
class RunResult:
    stats: dict = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    leads: list[Lead] = field(default_factory=list)
    unscored: list[Group] = field(default_factory=list)


# --- stages -----------------------------------------------------------------


def discover(settings: Settings, opts: RunOptions, errors: list[str]) -> list[Posting]:
    companies = [c for c in load_companies(settings.companies_path) if c.get("enabled", True)]
    cfg = filters.FilterConfig.from_preferences(settings.preferences, settings.max_posting_age_days)
    fetchers = {
        **ATS_FETCHERS,
        "workday": lambda client, c: workday.fetch(
            client, c,
            title_ok=lambda t: filters.title_reason(t, cfg) is None,
            max_age_days=settings.max_posting_age_days,
        ),
    }
    wanted = lambda src: opts.sources is None or src in opts.sources  # noqa: E731
    postings: list[Posting] = []
    ats_companies: set[str] = set()
    with make_client() as client:
        for c in companies:
            fetch = fetchers.get(c.get("ats", ""))
            if not fetch:
                continue
            ats_companies.add(normalize_company(c["name"]))
            if not wanted(c["ats"]):
                continue
            try:
                got = fetch(client, c)
                log.info("%-10s %-30s %4d postings", c["ats"], c["name"], len(got))
                postings.extend(got)
            except (SourceError, ValueError) as e:
                errors.append(f"{c['ats']}:{c['name']}: {e}")
                log.error("fetch failed for %s (%s): %s", c["name"], c["ats"], e)
        if wanted("simplify"):
            cats = set(settings.preferences.get("filters", {}).get("simplify_categories") or [])
            try:
                got = github_lists.fetch(client, settings.max_posting_age_days, cats or None)
                # Companies polled directly via their ATS are covered better there.
                got = [p for p in got if normalize_company(p.company) not in ats_companies]
                log.info("%-10s %-30s %4d postings", "simplify", "New-Grad-Positions", len(got))
                postings.extend(got)
            except SourceError as e:
                errors.append(f"simplify: {e}")
                log.error("fetch failed for SimplifyJobs list: %s", e)
    return postings


def apply_filters(db: DB, postings: list[Posting], cfg: filters.FilterConfig) -> tuple[list[Posting], Counter]:
    reasons: Counter = Counter()
    survivors = []
    for p in postings:
        reason = filters.check(p, cfg)
        db.set_filter_reason(p.db_id, reason)
        if reason:
            reasons[reason.split(":")[0]] += 1
        else:
            survivors.append(p)
    db.commit()
    return survivors, reasons


def dedupe(db: DB, survivors: list[Posting], known: KnownJobs) -> tuple[list[Group], int]:
    written = db.written_ids()
    groups = group_postings(survivors)
    fresh = [
        g for g in groups
        if not any(m.db_id in written or known.contains(m) for m in g.members)
    ]
    return fresh, len(groups) - len(fresh)


def score_groups(db: DB, scorer, groups: list[Group], limit: int, errors: list[str]) -> tuple[list[tuple[Group, Score]], dict]:
    cached: list[tuple[Group, Score]] = []
    todo: list[Group] = []
    for g in groups:
        s = db.get_score(g.primary.db_id, scorer.hash, scorer.model)
        if s:
            cached.append((g, s))
        else:
            todo.append(g)
    # Newest first, so the cap drops the stalest postings.
    todo.sort(key=lambda g: g.primary.posted_at or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    skipped = max(0, len(todo) - limit)
    todo = todo[:limit]

    def work(g: Group):
        try:
            return g, scorer.score(g.primary, g.locations), None
        except Exception as e:  # one bad posting shouldn't sink the run
            return g, None, e

    fresh: list[tuple[Group, Score]] = []
    with ThreadPoolExecutor(max_workers=SCORING_WORKERS) as pool:
        for g, s, err in pool.map(work, todo):
            if err:
                errors.append(f"score:{g.primary.company}:{g.primary.title}: {err}")
                log.error("scoring failed for %s / %s: %s", g.primary.company, g.primary.title, err)
                continue
            db.save_score(g.primary.db_id, scorer.hash, scorer.model, s)
            fresh.append((g, s))
    db.commit()
    return cached + fresh, {"cached": len(cached), "new": len(fresh), "over_cap": skipped}


def to_lead(g: Group, s: Score, today: date) -> Lead:
    p = g.primary
    salaried = next((m for m in g.members if m.salary_min or m.salary_max), p)
    locs = g.locations
    loc = "; ".join(locs[:MAX_LOCATIONS_SHOWN])
    if len(locs) > MAX_LOCATIONS_SHOWN:
        loc += f" (+{len(locs) - MAX_LOCATIONS_SHOWN} more)"
    return Lead(
        company=p.company,
        link=p.url,
        salary=salary_bucket(salaried.salary_min, salaried.salary_max),
        role=p.title,
        location=loc,
        fit_score=s.fit_score,
        why=s.reason,
        date_posted=p.posted_at.astimezone().date().isoformat() if p.posted_at else "",
        date_found=today.isoformat(),
    )


# --- run --------------------------------------------------------------------


def run(settings: Settings, opts: RunOptions) -> RunResult:
    from jobsearch import sheets  # imported lazily so tests don't need gspread configured

    res = RunResult()
    db = DB(settings.db_path)
    run_id = db.start_run(opts.dry_run)
    stats = res.stats
    try:
        # 1. Discover
        postings = discover(settings, opts, res.errors)
        new = 0
        for p in postings:
            _, is_new = db.upsert_posting(p)
            new += is_new
        db.commit()
        stats.update(fetched=len(postings), new=new)

        # 2. Filter
        cfg = filters.FilterConfig.from_preferences(settings.preferences, settings.max_posting_age_days)
        survivors, reasons = apply_filters(db, postings, cfg)
        stats.update(passed_filters=len(survivors), filtered=dict(reasons))

        # 3. Dedupe against the Sheet
        known = KnownJobs()
        leads_ws = None
        occupied = 0
        if settings.spreadsheet_id and settings.google_sa_json.exists():
            tracker_ws, leads_ws = sheets.open_worksheets(
                settings.google_sa_json, settings.spreadsheet_id, settings.tracker_tab, settings.leads_tab
            )
            known = sheets.read_tracker(tracker_ws)
            occupied = sheets.read_leads(leads_ws, known)
        elif not opts.dry_run:
            raise RuntimeError("SPREADSHEET_ID / GOOGLE_SA_JSON not configured; use --dry-run.")
        else:
            log.warning("Sheet not configured: skipping tracker/Leads dedupe for this dry run.")
        groups, dupes = dedupe(db, survivors, known)
        stats.update(already_known=dupes, candidate_leads=len(groups))

        # 4. Score
        if not opts.score:
            res.unscored = groups
            return res
        from jobsearch.scoring import Scorer, build_system_prompt

        system_prompt = build_system_prompt(settings.prompt_path, settings.resume_path, settings.preferences)
        scorer = Scorer(settings.anthropic_api_key, settings.scoring_model, system_prompt)
        scored, score_stats = score_groups(db, scorer, groups, settings.max_score_per_run, res.errors)
        stats["scored"] = score_stats

        # 5. Write
        today = date.today()
        leads = [
            (g, to_lead(g, s, today)) for g, s in scored if s.fit_score >= settings.score_threshold
        ]
        leads.sort(key=lambda gl: gl[1].fit_score, reverse=True)
        res.leads = [lead for _, lead in leads]
        stats["above_threshold"] = len(leads)
        if opts.dry_run:
            stats["written"] = 0
        else:
            rng = sheets.append_leads(leads_ws, res.leads, occupied)
            db.mark_written([m.db_id for g, _ in leads for m in g.members])
            db.commit()
            stats["written"] = len(leads)
            if rng:
                log.info("wrote %d leads to %s!%s", len(leads), settings.leads_tab, rng)
        return res
    except Exception as e:
        res.errors.append(f"fatal: {e}")
        log.exception("run failed")
        raise
    finally:
        log.info("run stats: %s", stats)
        if res.errors:
            log.warning("%d errors: %s", len(res.errors), res.errors)
        db.finish_run(run_id, stats, res.errors)
        db.close()
