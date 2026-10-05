import httpx
import respx

from jobsearch.sources import ashby, github_lists, greenhouse, lever
from jobsearch.sources.base import make_client


def test_greenhouse_parse(load_fixture):
    posts = greenhouse.parse(load_fixture("greenhouse.json"), "Stripe")
    assert len(posts) == 3
    p = posts[0]
    assert p.source == "greenhouse" and p.company == "Stripe"
    assert p.url.startswith("https://") and p.external_id.isdigit()
    assert p.posted_at is not None and p.posted_at.tzinfo is not None
    assert "<" not in p.description and "&lt;" not in p.description
    assert p.locations


def test_lever_parse_uses_salary_range(load_fixture):
    posts = lever.parse(load_fixture("lever.json"), "Palantir")
    assert posts[0].salary_min == 120000 and posts[0].salary_max == 150000
    assert posts[0].url.startswith("https://jobs.lever.co/")
    assert posts[0].posted_at is not None
    assert "<div>" not in posts[0].description


def test_ashby_parse_compensation(load_fixture):
    posts = ashby.parse(load_fixture("ashby.json"), "Ramp")
    p = posts[0]
    assert p.salary_min and p.salary_max and p.salary_min <= p.salary_max
    assert p.salary_raw.startswith("$")
    assert p.locations and p.url.startswith("https://jobs.ashbyhq.com/")


def test_simplify_skips_inactive_and_old(load_fixture):
    from datetime import datetime, timezone

    data = load_fixture("simplify.json")
    newest = max(j["date_posted"] for j in data if j["active"])
    now = datetime.fromtimestamp(newest, tz=timezone.utc)
    posts = github_lists.parse(data, max_age_days=3650, now=now)
    assert len(posts) == 3  # the inactive entry is dropped
    assert all(p.source == "simplify" and p.description == "" for p in posts)
    assert github_lists.parse(data, max_age_days=0, now=now.replace(year=now.year + 1)) == []


@respx.mock
def test_fetch_retries_on_429(load_fixture, monkeypatch):
    monkeypatch.setattr("jobsearch.sources.base.time.sleep", lambda s: None)
    route = respx.get("https://boards-api.greenhouse.io/v1/boards/stripe/jobs").mock(
        side_effect=[httpx.Response(429), httpx.Response(200, json=load_fixture("greenhouse.json"))]
    )
    with make_client() as client:
        posts = greenhouse.fetch(client, {"name": "Stripe", "token": "stripe"})
    assert route.call_count == 2 and len(posts) == 3


def test_workday_parse_site_url():
    from jobsearch.sources.workday import parse_site_url

    assert parse_site_url("https://generalmotors.wd5.myworkdayjobs.com/en-US/Careers_GM/job/x") == (
        "https://generalmotors.wd5.myworkdayjobs.com", "generalmotors", "Careers_GM")
    assert parse_site_url("https://intel.wd1.myworkdayjobs.com/en-us/External")[2] == "External"


def test_workday_posted_on():
    from datetime import datetime, timedelta, timezone

    from jobsearch.sources.workday import parse_posted_on

    now = datetime(2026, 9, 28, tzinfo=timezone.utc)
    assert parse_posted_on("Posted Today", now) == now
    assert parse_posted_on("Posted Yesterday", now) == now - timedelta(days=1)
    assert parse_posted_on("Posted 30+ Days Ago", now) == now - timedelta(days=30)


@respx.mock
def test_workday_fetch_filters_titles_before_detail_calls(load_fixture, monkeypatch):
    from datetime import datetime, timezone

    from jobsearch.sources import workday

    monkeypatch.setattr(workday.time, "sleep", lambda s: None)
    base = "https://rockwellautomation.wd1.myworkdayjobs.com/wday/cxs/rockwellautomation/Early"
    search = respx.post(f"{base}/jobs").mock(
        return_value=httpx.Response(200, json=load_fixture("workday_search.json")))
    first = load_fixture("workday_search.json")["jobPostings"][0]
    detail = respx.get(f"{base}{first['externalPath']}").mock(
        return_value=httpx.Response(200, json=load_fixture("workday_detail.json")))
    company = {"name": "Rockwell", "url": "https://rockwellautomation.wd1.myworkdayjobs.com/Early",
               "search": ["software"]}
    with make_client() as client:
        posts = workday.fetch(client, company, title_ok=lambda t: t == first["title"],
                              now=datetime(2026, 9, 28, tzinfo=timezone.utc))
    assert search.call_count == 1 and detail.call_count == 1  # other titles never fetched
    (p,) = posts
    assert p.source == "workday" and p.external_id.startswith("rockwellautomation:")
    assert p.locations[0] == "Mayfield Heights, Ohio, United States" and len(p.locations) == 2
    assert p.description and "<" not in p.description
    assert p.url.startswith("https://rockwellautomation.wd1.myworkdayjobs.com/")


def test_oracle_parse_detail(load_fixture):
    from jobsearch.sources import oracle

    assert oracle.parse_site_url(
        "https://jpmc.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1001/job/1"
    ) == ("jpmc.fa.oraclecloud.com", "CX_1001")
    item = load_fixture("oracle_detail.json")["items"][0]
    p = oracle.parse_detail(item, "JPMorgan Chase", "jpmc.fa.oraclecloud.com", "CX_1001")
    assert p.source == "oracle" and p.external_id == f"jpmc:{item['Id']}"
    assert p.url == f"https://jpmc.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1001/job/{item['Id']}"
    assert p.locations[0] == "San Francisco, CA, United States" and "Seattle, WA, United States" in p.locations
    assert (p.salary_min, p.salary_max) == (156750, 215000)  # from the "Base Pay/Salary" flex field
    assert p.posted_at is not None and p.description and "<" not in p.description


def test_smartrecruiters_parse_detail(load_fixture):
    from jobsearch.sources import smartrecruiters

    d = load_fixture("smartrecruiters_detail.json")
    p = smartrecruiters.parse_detail(d, "ServiceNow", "ServiceNow")
    assert p.source == "smartrecruiters" and p.external_id == f"ServiceNow:{d['id']}"
    assert p.url.startswith("https://jobs.smartrecruiters.com/ServiceNow/")
    assert p.locations and p.description and p.posted_at is not None


def _fixture_text(name):
    from pathlib import Path

    return (Path(__file__).parent / "fixtures" / name).read_text()


def test_successfactors_parse_search_page():
    from jobsearch.sources import successfactors as sf

    rows = sf.parse_search_page(_fixture_text("successfactors_search.html"))
    assert len(rows) == 4
    r = rows[0]
    assert r["id"].isdigit() and r["path"].startswith("/job/") and r["path"].endswith(f"/{r['id']}/")
    assert r["title"] and "<" not in r["title"]
    assert r["location"].endswith("MI, US")
    assert r["posted"] is not None  # this site shows a date column


def test_successfactors_parse_job_page():
    from jobsearch.sources import successfactors as sf

    page = _fixture_text("successfactors_job.html")
    p = sf.parse_job_page(page, "DTE Energy", "https://careers.dteenergy.com/job/x/1377939300/", "1377939300")
    assert p.source == "successfactors" and p.external_id == "careers.dteenergy.com:1377939300"
    assert p.title == "Control Engineer II"
    assert p.locations == ["River Rouge, MI"]
    assert p.posted_at.isoformat().startswith("2026-09-20")
    assert len(p.description) > 1000 and "<" not in p.description
    assert sf.page_country(page) == "US"


def test_successfactors_row_country():
    from jobsearch.sources.successfactors import _row_country

    assert _row_country("Valladolid, VA, ES") == "ES"
    assert _row_country("Oberding, BY, DE") == "DE"
    assert _row_country("River Rouge, MI, US") == "US"
    assert _row_country("Southfield, MI, US, 48033") == "US"
    assert _row_country("Rabat, MA, 10000") == ""  # no country: handled by the location filter


@respx.mock
def test_successfactors_fetch_filters_before_detail_calls(monkeypatch):
    from datetime import datetime, timezone

    from jobsearch.sources import successfactors as sf

    monkeypatch.setattr(sf.time, "sleep", lambda s: None)
    search_html = _fixture_text("successfactors_search.html")
    rows = sf.parse_search_page(search_html)
    keep = rows[0]
    base = "https://careers.consumersenergy.com"
    respx.get(f"{base}/search/", params={"startrow": "0"}).mock(return_value=httpx.Response(200, text=search_html))
    respx.get(f"{base}/search/", params={"startrow": str(len(rows))}).mock(return_value=httpx.Response(200, text="<table></table>"))
    detail = respx.get(f"{base}{keep['path']}").mock(
        return_value=httpx.Response(200, text=_fixture_text("successfactors_job.html")))
    with make_client() as client:
        posts = sf.fetch(client, {"name": "Consumers Energy", "url": base, "search": ["software"]},
                         title_ok=lambda t: t == keep["title"], max_age_days=3650,
                         now=datetime(2026, 10, 4, tzinfo=timezone.utc))
    assert detail.call_count == 1 and len(posts) == 1  # other rows never fetched
