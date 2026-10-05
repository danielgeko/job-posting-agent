from datetime import datetime, timedelta, timezone

import pytest

from jobsearch.filters import FilterConfig, check, min_years_required
from jobsearch.models import Posting

NOW = datetime(2026, 9, 28, tzinfo=timezone.utc)


def post(title, desc="", locs=("Detroit, MI",), age_days=1):
    return Posting(
        source="greenhouse", external_id="1", company="Acme", title=title, url="https://x",
        locations=list(locs), description=desc, posted_at=NOW - timedelta(days=age_days),
    )


@pytest.fixture
def cfg(preferences):
    return FilterConfig.from_preferences(preferences, max_age_days=30)


@pytest.mark.parametrize("title", [
    "Senior Software Engineer", "Sr. Software Engineer", "Staff Engineer", "Engineering Manager",
    "Software Engineer II", "Software Engineer 2 - Payments", "Tech Lead, Platform",
    "Software Engineering Intern", "Principal Architect",
])
def test_drops_senior_and_intern_titles(cfg, title):
    assert check(post(title), cfg, NOW).startswith("title_excluded")


@pytest.mark.parametrize("title", [
    "Software Engineer, New Grad", "Software Engineer I", "Application Developer",
    "Forward Deployed Engineer", "Embedded Software Engineer", "Software Quality Engineer",
    "Entry Level Engineer/Developer", "Technology Development Program Associate",
    "Associate Software Engineer", "Backend Engineer",
])
def test_keeps_entry_level_software(cfg, title):
    assert check(post(title), cfg, NOW) is None


@pytest.mark.parametrize("title", ["Account Executive", "Recruiter", "Data Analyst"])
def test_drops_non_software(cfg, title):
    assert check(post(title), cfg, NOW) == "not_software"


def test_drops_mechanical(cfg):
    assert check(post("Mechanical Engineer"), cfg, NOW) is not None


def test_experience_requirement(cfg):
    assert check(post("Software Engineer", "Requires 3+ years of professional experience."), cfg, NOW) == "experience_3plus"
    assert check(post("Software Engineer", "5-7 years of relevant software experience"), cfg, NOW) == "experience_5plus"
    assert check(post("Software Engineer", "0-2 years of experience; 5+ years of experience preferred"), cfg, NOW) is None
    assert check(post("Software Engineer", "BS in CS or 4 years of equivalent experience"), cfg, NOW) is None
    assert check(post("Software Engineer", "Founded 10 years ago, we build software."), cfg, NOW) is None


def test_min_years_required():
    assert min_years_required("3 to 5 yrs of experience") == 3
    assert min_years_required("nothing here") is None


def test_stale(cfg):
    assert check(post("Software Engineer", age_days=45), cfg, NOW) == "stale"


def test_location(cfg):
    assert check(post("Software Engineer", locs=["London", "Toronto"]), cfg, NOW) == "non_us_location"
    p = post("Software Engineer", locs=["London", "New York, NY"])
    assert check(p, cfg, NOW) is None and p.locations == ["New York, NY"]


@pytest.mark.parametrize("title", ["Full Stack Engineer - 4 (JavaScript)", "Software Engineering Student"])
def test_drops_level_suffix_and_students(cfg, title):
    assert check(post(title), cfg, NOW).startswith("title_excluded")


@pytest.mark.parametrize("title", [
    "Product Development Engineer - Hypoid Gear and Differential",
    "Diesel Emissions Systems Engineer",
    "Electrical Systems Engineer",
    "Chassis Systems Engineer - Ford Racing",
    "Audio Control Module - Product Development Engineer",
])
def test_drops_physical_engineering(cfg, title):
    assert check(post(title), cfg, NOW) is not None


@pytest.mark.parametrize("title", [
    "Embedded Software Engineer - Battery Management",
    "In-Vehicle Network Software Developer",
    "Systems Engineer, Email Service",
    "Software/Systems Quality Engineer",
])
def test_keeps_software_titles_with_hardware_words(cfg, title):
    assert check(post(title), cfg, NOW) is None


@pytest.mark.parametrize("title", [
    "Plant Quality Engineer", "Laboratory Quality engineer", "Controls & Automation Engineer",
    "Associate Process Engineer", "Associate Environmental Engineer - Coke",
    "Engineering Entry Program (EEP) Engineer", "Application Engineer",
])
def test_drops_industrial_engineering_titles(cfg, title):
    assert check(post(title), cfg, NOW) is not None


@pytest.mark.parametrize("title", ["QA Engineer", "SDET I", "Test Automation Engineer", "SCADA Ignition Developer"])
def test_keeps_software_testing_titles(cfg, title):
    assert check(post(title), cfg, NOW) is None


@pytest.mark.parametrize("title", ["Reliability Engineer", "Rotating Reliability Engineer", "Control Systems Engineer"])
def test_drops_plant_reliability_and_controls(cfg, title):
    assert check(post(title), cfg, NOW) is not None


@pytest.mark.parametrize("title", ["Site Reliability Engineer", "Software Engineer (.NET & AI)", "Systems Engineer, Email Service"])
def test_keeps_sre_and_software(cfg, title):
    assert check(post(title), cfg, NOW) is None
