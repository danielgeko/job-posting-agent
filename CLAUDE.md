# Job Search Assistant — Project Brief

## Overview

A personal tool that finds new-grad / entry-level software engineering job postings each day, filters and scores them against my background, and writes the good matches to a "Job Leads" tab in my existing Google Sheet. I review the leads and apply manually.

**Scope: discovery only.** The assistant never applies, never submits forms, never logs into job sites, and never writes to my application tracker's status or interview columns. It reads public job board data and writes to one tab.

## About me (for scoring context)

- CS student at Michigan State University, graduating December 2026
- Looking for full-time software engineering roles starting early 2027
- Completed a software development internship at United Wholesale Mortgage (ended Aug 2026)
- Primary stack: C#, .NET, FastAPI microservices; also AI/agent work (built an internal voice AI agent during the internship)
- Based in Michigan

## Google Sheet structure

Spreadsheet: **Work Tracking Spreadsheet**

### Main tracker tab (read-only for the assistant)

Row 1 holds section titles, row 2 holds headers, data starts at row 3. 70+ rows currently.

| Col | Header | Notes |
|---|---|---|
| A | Company | |
| B | Status | Dropdown: Not yet, Applied, OA, Reject, Closed |
| C | Link | Posting URL (sometimes blank) |
| D | Done? | Checkbox — application submitted |
| E | Salary | Dropdown: N/A, 60k - 70k, 70k - 80k, 80k - 90k, 100k+ |
| F | Role | |
| G | Location | Sometimes "Numerous" / "Multiple" |
| H | Take By | Deadline (e.g., for an OA) |
| I–L | OA, Phone Screen, Interview, Done? | Interview tracking checkboxes |

The assistant only reads **Company (A)**, **Link (C)** and **Role (F)** from this tab, to avoid suggesting jobs I've already applied to.

### Job Leads tab (the assistant writes here)

Row 1 has the "Job Leads" title, row 2 has headers, new leads are appended starting at row 3.

| Col | Header | Filled by |
|---|---|---|
| A | Company | Agent |
| B | Link | Agent |
| C | Salary | Agent — use the same buckets as the main tab; N/A if not listed |
| D | Role | Agent |
| E | Location | Agent |
| F | Fit Score | Agent (0–100) — *column to be added* |
| G | Why | Agent — one-line reason — *to be added* |
| H | Date Posted | Agent, when available — *to be added* |
| I | Date Found | Agent — *to be added* |
| J | Decision | Me — dropdown: Interested / Skip — *to be added* |
| K | Notes | Me (optional) — *to be added* |

The agent must never overwrite Decision or Notes.

## Pipeline

1. **Discover** — daily run pulls postings from sources (below), normalizes them into a local database.
2. **Dedupe** — skip anything already in the Leads tab or the main tracker (match by link, then by company + normalized title).
3. **Filter** (deterministic, no LLM) — drop senior/staff/lead/manager titles, "3+ years" or more experience requirements, stale postings, and non-software roles.
4. **Score** (LLM) — score survivors against my profile with structured output: fit score, matched skills, gaps, one-line reason.
5. **Write** — append matches above a threshold to the Leads tab.

### Dedupe note

Some companies post the same role in many locations (e.g., Rovisys has 8 rows for "Entry Level Engineer/Developer" in different cities). Treat company + same title as one lead and combine locations into one row rather than creating a row per city.

## Sources

Use public, unauthenticated job board APIs. Do not scrape LinkedIn or Indeed.

- **Greenhouse:** `https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs?content=true`
- **Lever:** `https://api.lever.co/v0/postings/{company}?mode=json`
- **Ashby:** `https://api.ashbyhq.com/posting-api/job-board/{board_name}?includeCompensation=true`
- **Community new-grad lists on GitHub** (e.g., the SimplifyJobs New-Grad-Positions repo) as a discovery feed
- **Workday:** no official API, but each career site is backed by a public JSON endpoint (`POST https://{tenant}.wd{N}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs`, max 20 per page; `GET …{externalPath}` for details). Filter on title/age before fetching details. Undocumented, so it may change.
- **Oracle Recruiting Cloud:** `GET https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions?finder=findReqs;siteNumber={site},keyword="...",sortBy=POSTING_DATES_DESC` and `recruitingCEJobRequisitionDetails?finder=ById;Id="{id}",siteNumber={site}`. Undocumented.
- **SmartRecruiters:** `https://api.smartrecruiters.com/v1/companies/{company}/postings?q=...&country=us` (official public API).

Maintain a `companies.yaml` of target companies with their ATS type and board token. Seed it from companies in my tracker and add more over time. Verify endpoint formats against current docs before relying on them.

## Profile store

Keep in `profile/`:

- `resume.yaml` — structured master resume: education, experience, projects, skills (tagged by strength)
- `preferences.yaml` — target titles, locations, seniority rules, salary floor
- (Later) `examples.yaml` — roles I applied to / marked Interested vs. Skip, used as few-shot examples for scoring

### Starting preferences (derived from my tracker — confirm/adjust)

- **Target titles:** Software Engineer / Developer (entry level, I, new grad, associate), Application Developer, Forward Deployed Engineer, rotational technology programs, embedded/systems software, software quality engineer
- **Locations:** Michigan preferred; open to relocation (have applied to roles in IL, NY, MA, TX, NC, VA, CA, OH, and others)
- **Seniority:** entry level / new grad only
- **Salary:** track what's listed; not a hard filter yet

## Tech stack

- Python 3.11+
- `httpx` for fetching
- SQLite (via SQLAlchemy or plain `sqlite3`) for postings and run history
- `gspread` + Google service account for Sheets (share the spreadsheet with the service account's email)
- Anthropic Claude API for scoring, using structured JSON output; a small/fast model (e.g., Claude Haiku 4.5) is enough for scoring
- Scheduling: cron / APScheduler / GitHub Actions for a daily run
- Config and secrets in `.env` (API key, service account JSON path, spreadsheet ID, tab names, score threshold)

A FastAPI layer is optional; a CLI (`python -m jobsearch run`) is enough for phase one.

## Suggested project structure

```
job-search-assistant/
  jobsearch/
    sources/        # greenhouse.py, lever.py, ashby.py, github_lists.py
    models.py       # Posting dataclass / DB models
    db.py
    filters.py      # deterministic rules
    scoring.py      # LLM scoring + prompt
    sheets.py       # read tracker, write leads
    pipeline.py     # orchestrates a run
    cli.py
  profile/
    resume.yaml
    preferences.yaml
  companies.yaml
  tests/
  .env.example
  README.md
```

## Phases

1. **Phase one:** fetchers (Greenhouse, Lever, Ashby), normalize + dedupe, deterministic filters, LLM scoring, write to Leads tab, daily schedule.
2. **Phase two:** add Fit Score/Why/Date columns if not done, learn from my Interested/Skip decisions, optional daily email digest.
3. **Later / optional:** Apps Script to copy "Interested" leads to the main tracker; on-demand resume tailoring (select/reorder bullets from `resume.yaml` only, never invent experience).

## Guardrails

- Read-only on the main tracker tab.
- Never modify Decision or Notes in the Leads tab.
- Respect rate limits; poll each board at most once per run.
- Log every run (postings fetched, filtered, scored, written) for debugging.
- Keep the scoring prompt and threshold in config so they're easy to tune.

## Open items

- Add columns F–K to the Leads tab
- Confirm exact tab names (main tracker tab and Leads tab)
- Create the Google Cloud service account and share the sheet with it
- Write `resume.yaml` from my current resume
- Build the initial `companies.yaml` list
