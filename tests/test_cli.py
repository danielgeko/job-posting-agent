from jobsearch.cli import SimplifyIndex


def entry(company, url):
    return {"company_name": company, "url": url}


def test_simplify_index_workday_sites_and_hints():
    wd = "https://vanguard.wd5.myworkdayjobs.com"
    idx = SimplifyIndex(
        [entry("Vanguard", f"{wd}/en-US/contractors_restricted/job/{i}") for i in range(5)]
        + [entry("Vanguard", f"{wd}/en-US/vanguard_external/job/{i}") for i in range(3)]
        + [entry("Salesforce", "https://salesforce.wd12.myworkdayjobs.com/External_Career_Site/job/1")] * 3
        + [entry("Salesforce", "https://salesforce.wd12.myworkdayjobs.com/Futureforce_NewGradRoles/job/2")]
        + [entry("Salesforce", "https://salesforce.wd12.myworkdayjobs.com/Other_Site/job/3")]
        + [entry("Auto-Owners Insurance", "https://aoins.wd5.myworkdayjobs.com/AutoOwners/job/1")]
        + [entry("Ford Motor Company", "https://efds.fa.em5.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/job/9")]
        + [entry("Domino's", "https://jobs.smartrecruiters.com/Dominos/7440001")]
    )
    # Restricted/contractor sites are skipped even when more common.
    assert idx.boards_for("Vanguard") == [("workday", f"{wd}/vanguard_external")]
    # Main site first, plus early-career sites; unrelated extra sites dropped.
    assert idx.boards_for("Salesforce") == [
        ("workday", "https://salesforce.wd12.myworkdayjobs.com/External_Career_Site"),
        ("workday", "https://salesforce.wd12.myworkdayjobs.com/Futureforce_NewGradRoles"),
    ]
    # Prefix matching on company names.
    assert idx.boards_for("Auto-Owners") == [("workday", "https://aoins.wd5.myworkdayjobs.com/AutoOwners")]
    # Oracle and SmartRecruiters boards are recognized from posting URLs too.
    assert idx.boards_for("Ford") == [
        ("oracle", "https://efds.fa.em5.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1")]
    assert idx.host_hint("Ford").startswith("Oracle Recruiting Cloud")
    assert idx.host_hint("GM") == "not in SimplifyJobs data"
    assert idx.boards_for("Domino's") == [("smartrecruiters", "Dominos")]


def test_company_slugs():
    from jobsearch.cli import _company_slugs

    assert _company_slugs("General Mills")[0] == "generalmills"
    assert _company_slugs("AT&T")[0] == "att"
    assert _company_slugs("Ameriprise Financial")[0] == "ameriprise"  # 'financial' dropped


def test_boards_in_page_finds_supported_links():
    import httpx
    import respx

    from jobsearch.cli import _boards_in_page

    html = """<a href="https://usbank.wd1.myworkdayjobs.com/en-US/US_Bank_Careers/job/x">Jobs</a>
              <a href="https://job-boards.greenhouse.io/acme/jobs/1">GH</a>
              <a href="https://careers.example.com/search">Search</a>"""
    with respx.mock:
        respx.get("https://www.example.com/careers").mock(return_value=httpx.Response(200, text=html))
        with httpx.Client() as client:
            boards, hosts = _boards_in_page(client, "https://www.example.com/careers")
    assert ("workday", "https://usbank.wd1.myworkdayjobs.com/US_Bank_Careers") in boards
    assert ("greenhouse", "acme") in boards
    assert "careers.example.com" in hosts
