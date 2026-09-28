from jobsearch.normalize import (
    is_us_location,
    normalize_title,
    normalize_url,
    parse_salary_text,
    salary_bucket,
)


def test_normalize_title_strips_noise():
    assert normalize_title("Software Engineer I (2027) - Remote") == "software engineer 1"
    assert normalize_title("Software Engineer 1") == "software engineer 1"
    assert normalize_title("Entry Level Engineer/Developer") == "entry level engineer/developer"


def test_normalize_url():
    assert normalize_url("https://Jobs.Lever.co/x/123/?utm_source=li") == "https://jobs.lever.co/x/123"
    assert normalize_url("https://stripe.com/jobs/search?gh_jid=42&x=1") == "https://stripe.com/jobs/search?gh_jid=42"


def test_parse_salary_text():
    assert parse_salary_text("The range is $95,000 - $120,000 per year.") == (95000, 120000)
    assert parse_salary_text("Pay: $110K–$140K") == (110000, 140000)
    lo, hi = parse_salary_text("$40/hour")
    assert lo == 40 * 2080
    assert parse_salary_text("We raised $50 million") == (None, None)


def test_salary_bucket():
    assert salary_bucket(None, None) == "N/A"
    assert salary_bucket(60000, 70000) == "60k - 70k"
    assert salary_bucket(75000, 80000) == "70k - 80k"
    assert salary_bucket(80000, 95000) == "80k - 90k"
    assert salary_bucket(90000, 110000) == "100k+"


def test_us_location():
    assert is_us_location("Detroit, MI") is True
    assert is_us_location("Remote - USA") is True
    assert is_us_location("Bangalore, IN") is False
    assert is_us_location("Dublin") is False
    assert is_us_location("Remote") is None


def test_normalize_title_keeps_specializations():
    assert normalize_title("Software Engineer - Payments") != normalize_title("Software Engineer - Search")
    assert normalize_title("Software Engineer, New Grad") == normalize_title("Software Engineer - New Grad (2027)")
    assert normalize_title("Forward Deployed Engineer - US Government") == "forward deployed engineer us government"
    assert normalize_title("Entry Level Engineer/Developer - Detroit, MI") == "entry level engineer/developer"
    assert normalize_title("Backend Engineer - Remote") == "backend engineer"
