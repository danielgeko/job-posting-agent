from datetime import datetime, timedelta, timezone

from jobsearch import pipeline, scoring, sheets
from jobsearch.config import load_settings
from jobsearch.models import Posting, Score
from tests.test_sheets import LEADS_HEADER, FakeWorksheet


def make_postings():
    now = datetime.now(timezone.utc)
    mk = lambda i, title, loc, desc="": Posting(  # noqa: E731
        source="greenhouse", external_id=str(i), company="RoviSys", title=title,
        url=f"https://boards.greenhouse.io/rovisys/jobs/{i}", locations=[loc],
        description=desc, posted_at=now - timedelta(days=2),
    )
    return [
        mk(1, "Entry Level Engineer/Developer", "Detroit, MI"),
        mk(2, "Entry Level Engineer/Developer", "Aurora, OH"),
        mk(3, "Senior Software Engineer", "Detroit, MI"),
        mk(4, "Software Engineer I", "Houston, TX", "Requires 5+ years of experience"),
        mk(5, "Associate Software Engineer", "Ann Arbor, MI"),
    ]


class FakeScorer:
    calls = 0

    def __init__(self, api_key, model, system_prompt):
        self.model, self.hash = model, "h"

    def score(self, p, locations):
        FakeScorer.calls += 1
        return Score(90 if "Entry" in p.title else 50, ["C#"], [], f"fit for {p.title}")


def test_two_runs_write_once(tmp_path, monkeypatch):
    settings = load_settings()
    settings.db_path = tmp_path / "t.db"
    settings.spreadsheet_id = "sheet"
    settings.google_sa_json = tmp_path / "sa.json"
    settings.google_sa_json.write_text("{}")
    settings.score_threshold = 65

    tracker = FakeWorksheet([["t"], ["Company", "Status", "Link", "Done?", "Salary", "Role"]])
    leads = FakeWorksheet([["Job Leads"], LEADS_HEADER])
    monkeypatch.setattr(sheets, "open_worksheets", lambda *a: (tracker, leads))
    monkeypatch.setattr(pipeline, "discover", lambda *a: make_postings())
    monkeypatch.setattr(scoring, "Scorer", FakeScorer)

    r1 = pipeline.run(settings, pipeline.RunOptions())
    assert r1.stats["passed_filters"] == 3
    assert r1.stats["candidate_leads"] == 2        # Rovisys Detroit + Aurora merged
    assert r1.stats["written"] == 1                # Associate SWE scored 50 < 65
    assert leads.cells[(3, 1)] == "RoviSys"
    assert leads.cells[(3, 5)] == "Detroit, MI; Aurora, OH"
    assert leads.cells[(3, 6)] == 90
    assert FakeScorer.calls == 2
    assert not tracker.updates                     # tracker never written

    r2 = pipeline.run(settings, pipeline.RunOptions())
    assert r2.stats["written"] == 0
    assert r2.stats["scored"] == {"cached": 1, "new": 0, "over_cap": 0}
    assert FakeScorer.calls == 2                   # below-threshold score reused from cache
    assert (4, 1) not in leads.cells
