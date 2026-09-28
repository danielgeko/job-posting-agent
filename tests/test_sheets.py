import pytest

from jobsearch import sheets
from jobsearch.models import Lead

LEADS_HEADER = ["Company", "Link", "Salary", "Role", "Location", "Fit Score", "Why",
                "Date Posted", "Date Found", "Decision", "Notes"]


class FakeWorksheet:
    """Minimal gspread.Worksheet stand-in backed by a dict of (row, col) -> value."""

    def __init__(self, rows, row_count=1000):
        self.cells = {}
        for r, row in enumerate(rows, start=1):
            for c, v in enumerate(row, start=1):
                if v != "":
                    self.cells[(r, c)] = v
        self.row_count = row_count
        self.updates = []

    @staticmethod
    def _col(letter):
        return ord(letter) - 64

    def _parse(self, a1):
        import re
        m = re.fullmatch(r"([A-Z])(\d+):([A-Z])(\d*)", a1)
        c1, r1, c2, r2 = m.groups()
        return int(r1), self._col(c1), int(r2) if r2 else None, self._col(c2)

    def get(self, a1):
        r1, c1, r2, c2 = self._parse(a1)
        last = max([r for r, _ in self.cells] + [0])
        r2 = r2 or last
        out = [[self.cells.get((r, c), "") for c in range(c1, c2 + 1)] for r in range(r1, r2 + 1)]
        out = [self._rstrip(row) for row in out]
        while out and not out[-1]:
            out.pop()
        return out

    @staticmethod
    def _rstrip(row):
        while row and row[-1] == "":
            row = row[:-1]
        return row

    def update(self, values, range_name, value_input_option=None):
        r1, c1, r2, c2 = self._parse(range_name)
        self.updates.append(range_name)
        for i, row in enumerate(values):
            assert len(row) <= c2 - c1 + 1
            for j, v in enumerate(row):
                self.cells[(r1 + i, c1 + j)] = v

    def add_rows(self, n):
        self.row_count += n


def lead(n):
    return Lead(f"Co{n}", f"https://x/{n}", "N/A", "Software Engineer", "Detroit, MI", 80,
                "good fit", "2026-09-20", "2026-09-28")


def test_append_only_touches_a_through_i_below_existing_rows():
    ws = FakeWorksheet([
        ["Job Leads"],
        LEADS_HEADER,
        ["Old Co", "https://old", "N/A", "SWE", "MI", 70, "ok", "", "2026-09-01", "Interested", "call"],
        ["", "", "", "", "", "", "", "", "", "Skip", "user note on blank row"],
    ])
    known = sheets.KnownJobs()
    occupied = sheets.read_leads(ws, known)
    assert occupied == 1
    assert known.contains(type("P", (), {"url": "https://old", "company": "x", "title": "y"}))

    rng = sheets.append_leads(ws, [lead(1), lead(2)], occupied)
    assert rng == "A4:I5"
    # Decision / Notes (J, K) untouched everywhere.
    assert ws.cells[(3, 10)] == "Interested" and ws.cells[(3, 11)] == "call"
    assert ws.cells[(4, 10)] == "Skip" and ws.cells[(4, 11)] == "user note on blank row"
    assert all(c <= 9 for (r, c) in ws.cells if r >= 4 and (r, c) not in {(4, 10), (4, 11)})
    # Existing row unchanged.
    assert ws.cells[(3, 1)] == "Old Co"


def test_header_mismatch_fails_loudly():
    ws = FakeWorksheet([["Job Leads"], ["Company", "Link", "Salary", "Role", "Location"]])
    with pytest.raises(sheets.SheetLayoutError, match="Add the missing columns"):
        sheets.read_leads(ws, sheets.KnownJobs())


def test_read_tracker():
    ws = FakeWorksheet([
        ["Applications"],
        ["Company", "Status", "Link", "Done?", "Salary", "Role", "Location"],
        ["RoviSys", "Applied", "", True, "N/A", "Entry Level Engineer/Developer", "Numerous"],
    ])
    known = sheets.read_tracker(ws)
    assert ("rovisys", "entry level engineer/developer") in known.company_titles


def test_formula_like_values_are_escaped():
    ws = FakeWorksheet([["Job Leads"], LEADS_HEADER])
    bad = lead(1)
    bad.company = "=IMPORTXML(1)"
    sheets.append_leads(ws, [bad], 0)
    assert ws.cells[(3, 1)] == "'=IMPORTXML(1)"
