"""Google Sheets access: read the tracker and Leads tab, append new leads to Leads A–I.

Guardrails enforced here:
- The main tracker tab is only ever read (A–F).
- Leads writes are limited to columns A–I of rows below existing data, so Decision (J)
  and Notes (K) are never touched, and existing rows are never modified.
"""

from __future__ import annotations

import logging
import re

from jobsearch.dedupe import KnownJobs
from jobsearch.models import Lead

log = logging.getLogger(__name__)

HEADER_ROW = 2
FIRST_DATA_ROW = 3

TRACKER_HEADERS = {0: "company", 2: "link", 5: "role"}  # A, C, F
LEADS_HEADERS = [
    "company", "link", "salary", "role", "location",
    "fit score", "why", "date posted", "date found",
]  # A–I
LEADS_LAST_COL = "I"


class SheetLayoutError(Exception):
    pass


def _norm(h: str) -> str:
    return " ".join(str(h).strip().lower().replace("?", "").split())


def check_tracker_headers(header_row: list[str], row_number: int = HEADER_ROW) -> None:
    for idx, expected in TRACKER_HEADERS.items():
        got = _norm(header_row[idx]) if idx < len(header_row) else ""
        if got != expected:
            raise SheetLayoutError(
                f"Tracker row {row_number} column {chr(65 + idx)} is {got!r}, expected {expected!r}."
            )


def _tracker_header_index(rows: list[list[str]]) -> int:
    """Index of the header row among the first two rows. Row 2 in the original layout;
    row 1 once the tab is converted to a Sheets table (tables use a single header row)."""
    for i, r in enumerate(rows[:2]):
        if r and _norm(r[0]) == TRACKER_HEADERS[0]:
            return i
    return HEADER_ROW - 1


def check_leads_headers(header_row: list[str]) -> None:
    got = [_norm(h) for h in header_row[: len(LEADS_HEADERS)]]
    got += [""] * (len(LEADS_HEADERS) - len(got))
    if got != LEADS_HEADERS:
        raise SheetLayoutError(
            f"Leads tab row {HEADER_ROW} headers A–{LEADS_LAST_COL} are {got}, expected "
            f"{LEADS_HEADERS}. Add the missing columns (see README) before writing leads."
        )


def read_tracker(ws) -> KnownJobs:
    rows = ws.get("A1:F")
    if not rows:
        raise SheetLayoutError("Tracker tab is empty.")
    h = _tracker_header_index(rows)
    check_tracker_headers(rows[h] if h < len(rows) else [], row_number=h + 1)
    known = KnownJobs()
    for r in rows[h + 1:]:
        r = r + [""] * (6 - len(r))
        known.add(company=r[0], url=r[2], title=r[5])
    return known


def read_leads(ws, known: KnownJobs) -> int:
    """Add Leads-tab rows to `known`; return the number of rows occupied in A–I."""
    rows = ws.get(f"A{HEADER_ROW}:{LEADS_LAST_COL}")
    if not rows:
        raise SheetLayoutError("Leads tab has no header row.")
    check_leads_headers(rows[0])
    occupied = 0
    for i, r in enumerate(rows[1:], start=1):
        r = r + [""] * (4 - len(r))
        known.add(company=r[0], url=r[1], title=r[3])
        # A row counts as used only if it has a company, link or role. Other columns can
        # be pre-filled (e.g. the Salary dropdown defaults to "N/A" on empty rows).
        if any(str(v).strip() for v in (r[0], r[1], r[3])):
            occupied = i
    return occupied


def _safe(value):
    # USER_ENTERED would evaluate text starting with these as a formula.
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@"):
        return "'" + value
    return value


def append_leads(ws, leads: list[Lead], occupied_rows: int) -> str | None:
    """Write leads to A–I starting below existing data. Returns the range written."""
    if not leads:
        return None
    start = FIRST_DATA_ROW + occupied_rows
    end = start + len(leads) - 1
    if ws.row_count < end:
        ws.add_rows(end - ws.row_count)
    rng = f"A{start}:{LEADS_LAST_COL}{end}"
    values = [[_safe(v) for v in lead.to_row()] for lead in leads]
    ws.update(values=values, range_name=rng, value_input_option="USER_ENTERED")
    _extend_salary_dropdown(ws, start, end)
    return rng


SALARY_COL_INDEX = 2  # C, zero-based


def _extend_salary_dropdown(ws, start: int, end: int) -> None:
    """Copy the Salary dropdown from C3 onto newly written rows that lack it (the sheet's
    pre-formatted rows end somewhere; leads can go past that)."""
    sheet_id = getattr(ws, "id", None)
    if sheet_id is None or not hasattr(ws, "spreadsheet"):
        return
    def grid(r1, r2):  # 1-based inclusive rows -> GridRange for column C
        return {
            "sheetId": sheet_id,
            "startRowIndex": r1 - 1, "endRowIndex": r2,
            "startColumnIndex": SALARY_COL_INDEX, "endColumnIndex": SALARY_COL_INDEX + 1,
        }
    try:
        ws.spreadsheet.batch_update({"requests": [{"copyPaste": {
            "source": grid(FIRST_DATA_ROW, FIRST_DATA_ROW),
            "destination": grid(start, end),
            "pasteType": "PASTE_DATA_VALIDATION",
        }}]})
    except Exception as e:  # cosmetic; the values are already written
        log.warning("couldn't extend the Salary dropdown to rows %d-%d: %s", start, end, e)


class SheetConfigError(Exception):
    pass


def open_worksheets(sa_json_path, spreadsheet_id: str, tracker_tab: str, leads_tab: str):
    import gspread

    if not spreadsheet_id:
        raise SheetConfigError("SPREADSHEET_ID is not set in .env.")
    if not sa_json_path.exists():
        raise SheetConfigError(f"Service account key not found at {sa_json_path} (GOOGLE_SA_JSON in .env).")
    gc = gspread.service_account(filename=str(sa_json_path))
    try:
        sh = gc.open_by_key(spreadsheet_id)
    except PermissionError as e:
        # gspread raises a bare PermissionError for any 403; the cause says which one.
        detail = str(e.__cause__ or e)
        if "has not been used" in detail or "disabled" in detail:
            url = re.search(r"https://\S+?(?=\s|$)", detail)
            raise SheetConfigError(
                "The Google Sheets API isn't enabled for the service account's Cloud project. "
                f"Enable it here, wait a minute, and retry: {url.group(0) if url else detail}"
            ) from e
        email = getattr(gc.http_client.auth, "service_account_email", "the service account")
        raise SheetConfigError(
            f"No access to the spreadsheet. Share it with {email} as Editor."
        ) from e
    except gspread.SpreadsheetNotFound as e:
        raise SheetConfigError(
            f"Spreadsheet {spreadsheet_id!r} not found. Check SPREADSHEET_ID in .env "
            "(the part of the sheet URL between /d/ and /edit)."
        ) from e
    tabs = {ws.title: ws for ws in sh.worksheets()}
    missing = [t for t in (tracker_tab, leads_tab) if t not in tabs]
    if missing:
        raise SheetConfigError(
            f"Tab(s) {missing} not found. Available tabs: {list(tabs)}. "
            "Set TRACKER_TAB / LEADS_TAB in .env to match exactly."
        )
    return tabs[tracker_tab], tabs[leads_tab]
