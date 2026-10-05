# Job Search Assistant

Each day this finds new-grad and entry-level software engineering postings, filters them, scores them against my resume with Claude, and appends the good ones to the **Job Leads** tab of my Work Tracking Spreadsheet. It only discovers postings: it never applies, logs in, or edits the main tracker.

```
discover (Greenhouse, Lever, Ashby, Workday, Oracle, SmartRecruiters, SuccessFactors, SimplifyJobs) → SQLite
  → deterministic filters (seniority, experience, staleness, non-software, non-US)
  → dedupe (vs. tracker + Leads tab; merge multi-city duplicates)
  → Claude Haiku 4.5 fit score (cached per posting + prompt)
  → append rows ≥ SCORE_THRESHOLD to Leads columns A–I
```

## Setup

```bash
/usr/local/bin/python3.13 -m venv .venv
.venv/bin/pip install -e ".[dev]"
cp .env.example .env    # then fill it in
```

### One-time manual steps

1. **Leads tab columns.** Row 2 of the Leads tab must have these headers in A–K: `Company, Link, Salary, Role, Location, Fit Score, Why, Date Posted, Date Found, Decision, Notes`. Make **Decision** (J) a dropdown with the options *Interested* and *Skip*. The run checks A–I and refuses to write if they don't match.
2. **`.env`.** Copy `.env.example` to `.env` (a file, not a folder). Set `TRACKER_TAB` and `LEADS_TAB` to the exact tab names shown at the bottom of the sheet.
3. **Service account.**
   - In Google Cloud, create a project, enable the **Google Sheets API**, and create a service account with a JSON key. Save the key as `service-account.json` in the repo; it's gitignored.
   - Share the spreadsheet with the service account's email as **Editor**. Sheets can't restrict access per tab, so the read-only rule for the tracker is enforced in code (`jobsearch/sheets.py`).
   - Set `SPREADSHEET_ID` to the long ID in the sheet's URL.
4. **Anthropic key.** Set `ANTHROPIC_API_KEY` in `.env`.
5. **Resume.** Put my resume in `profile/`, then convert it to `profile/resume.yaml`: education, experience, projects, and skills tagged `strong` / `working` / `familiar`. Scoring runs without it but is much less useful.

## Usage

Run commands with the project virtualenv's Python. Conda's `base` Python doesn't have the dependencies. Either prefix commands with `.venv/bin/python` as shown below, or run `source .venv/bin/activate` once per shell.

```bash
.venv/bin/python -m jobsearch run --no-score        # fetch + filter + dedupe, print candidates (no API cost)
.venv/bin/python -m jobsearch run --dry-run         # also score, print leads, write nothing
.venv/bin/python -m jobsearch run                   # full run: writes to the Leads tab
.venv/bin/python -m jobsearch run --source simplify # limit to one source (repeatable)
.venv/bin/python -m jobsearch check-sources         # verify every board in companies.yaml responds
.venv/bin/python -m jobsearch find-boards           # probe ATS boards for every company in the tracker
.venv/bin/python -m jobsearch find-boards "Acme" "Globex"
```

Each run writes a log to `logs/YYYY-MM-DD.log` and a row to the `runs` table in `jobsearch.db`. The row records counts per stage (fetched, filtered by reason, known, scored, written) and any errors.

## Daily schedule (launchd)

```bash
cp scripts/com.jobsearch.daily.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.jobsearch.daily.plist
launchctl start com.jobsearch.daily   # run once now to test
```

The job runs at 8:00 each day. If the Mac is asleep then, it runs on wake.

## Tuning

| What | Where |
|---|---|
| Score threshold, model, and per-run scoring cap | `.env`: `SCORE_THRESHOLD`, `SCORING_MODEL`, `MAX_SCORE_PER_RUN` |
| Scoring rubric and prompt | `prompts/score.md`. Editing it changes the prompt hash, so postings get re-scored. |
| Title include/exclude regexes, experience cutoff, Simplify categories | `profile/preferences.yaml` → `filters` |
| Target companies | `companies.yaml` |
| Posting age cutoff | `.env`: `MAX_POSTING_AGE_DAYS` |

To see why postings were dropped, query the database:

```bash
sqlite3 jobsearch.db "select filter_reason, count(*) from postings group by 1 order by 2 desc"
```

## Notes and limits

- **Salary buckets.** Salary uses the tracker's buckets, placed by the midpoint of the range. The sheet has no 90k–100k bucket, so midpoints of 90k and up go to `100k+`. The Leads tab's dropdown only covers pre-formatted rows, so when leads are written below them, the dropdown is copied down too.
- **Sheets tables.** Both tabs can be Sheets tables. For the Jobs tab, the header can be in row 1 or row 2. For the Leads tab, the table's header must be row 2, with the title in row 1. When new leads would go past the end of the Leads table, the table is extended first so the new rows get its formatting, dropdown, date and checkbox columns. Greenhouse has no structured pay field, so its salary is parsed from the description's pay-transparency text when present.
- **SimplifyJobs entries.** These have no description, so the experience filter can't check them and Claude scores them from the title and company only.
- **Multi-city postings.** Postings with the same company and title (ignoring a trailing location like " - Detroit, MI") are combined into one lead with the locations merged.
- **Workday, Oracle Recruiting Cloud, SmartRecruiters and SAP SuccessFactors** are searched rather than downloaded whole. SuccessFactors sites have no JSON API, so the fetcher reads the career site's search and job pages, which follow a standard layout across companies. SmartRecruiters has an official public API. Workday and Oracle use the undocumented JSON endpoints behind their career sites. Each search uses the default terms "software engineer" and "software developer". Titles and ages that fail the filters are dropped, and full details are fetched only for the rest. Companies are fetched 8 at a time, so a full run takes about a minute and a half. If one of these companies starts failing in `check-sources`, check its careers URL first.
- **Finding boards.** `find-boards` probes Greenhouse, Lever and Ashby by company name. For Workday, Oracle and SmartRecruiters, it looks up career sites in the SimplifyJobs data. Anything it can't find is printed at the end with the hiring system it uses (iCIMS, Avature and custom sites aren't supported).
