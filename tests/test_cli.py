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
        + [entry("Ford Motor Company", "https://efds.fa.em5.oraclecloud.com/hcmUI/x")]
    )
    # Restricted/contractor sites are skipped even when more common.
    assert idx.workday_sites("Vanguard") == [f"{wd}/vanguard_external"]
    # Main site first, plus early-career sites; unrelated extra sites dropped.
    assert idx.workday_sites("Salesforce") == [
        "https://salesforce.wd12.myworkdayjobs.com/External_Career_Site",
        "https://salesforce.wd12.myworkdayjobs.com/Futureforce_NewGradRoles",
    ]
    # Prefix matching on company names.
    assert idx.workday_sites("Auto-Owners") == ["https://aoins.wd5.myworkdayjobs.com/AutoOwners"]
    assert idx.host_hint("Ford").startswith("Oracle Recruiting Cloud")
    assert idx.host_hint("GM") == "not in SimplifyJobs data"
