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
