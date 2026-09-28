"""Google Sheets access: read the tracker and Leads tab, append new leads to Leads A–I.

Guardrails enforced here:
- The main tracker tab is only ever read (A–F).
- Leads writes are limited to columns A–I of rows below existing data, so Decision (J)
  and Notes (K) are never touched, and existing rows are never modified.
"""

from __future__ import annotations

import logging

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


def check_tracker_headers(header_row: list[str]) -> None:
    for idx, expected in TRACKER_HEADERS.items():
        got = _norm(header_row[idx]) if idx < len(header_row) else ""
        if got != expected:
            raise SheetLayoutError(
                f"Tracker row {HEADER_ROW} column {chr(65 + idx)} is {got!r}, expected {expected!r}."
            )


def check_leads_headers(header_row: list[str]) -> None:
    got = [_norm(h) for h in header_row[: len(LEADS_HEADERS)]]
    got += [""] * (len(LEADS_HEADERS) - len(got))
    if got != LEADS_HEADERS:
        raise SheetLayoutError(
            f"Leads tab row {HEADER_ROW} headers A–{LEADS_LAST_COL} are {got}, expected "
            f"{LEADS_HEADERS}. Add the missing columns (see README) before writing leads."
        )


def read_tracker(ws) -> KnownJobs:
    rows = ws.get(f"A{HEADER_ROW}:F")
    if not rows:
        raise SheetLayoutError("Tracker tab is empty.")
    check_tracker_headers(rows[0])
    known = KnownJobs()
    for r in rows[1:]:
        r = r + [""] * (6 - len(r))
        known.add(company=r[0], url=r[2], title=r[5])
    return known


def read_leads(ws, known: KnownJobs) -> int:
    """Add Leads-tab rows to `known`; return the number of rows occupied in A–I."""
    rows = ws.get(f"A{HEADER_ROW}:{LEADS_LAST_COL}")
    if not rows:
        raise SheetLayoutError("Leads tab has no header row.")
    check_leads_headers(rows[0])
    data = rows[1:]
    for r in data:
        r = r + [""] * (4 - len(r))
        known.add(company=r[0], url=r[1], title=r[3])
    # ws.get trims trailing empty rows but keeps interior blanks, so len(data) is the
    # index of the last non-empty row.
    return len(data)


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
    return rng


class SheetConfigError(Exception):
    pass


def open_worksheets(sa_json_path, spreadsheet_id: str, tracker_tab: str, leads_tab: str):
    import gspread

    if not spreadsheet_id:
        raise SheetConfigError("SPREADSHEET_ID is not set in .env.")
    if not sa_json_path.exists():
        raise SheetConfigError(f"Service account key not found at {sa_json_path} (GOOGLE_SA_JSON in .env).")
    gc = gspread.service_account(filename=str(sa_json_path))
    sh = gc.open_by_key(spreadsheet_id)
    return sh.worksheet(tracker_tab), sh.worksheet(leads_tab)
