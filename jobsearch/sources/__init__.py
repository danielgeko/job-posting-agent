from jobsearch.sources import ashby, greenhouse, lever, oracle, smartrecruiters, successfactors, workday

# Full-board fetchers: fetch(client, company) returns every open posting.
ATS_FETCHERS = {
    "greenhouse": greenhouse.fetch,
    "lever": lever.fetch,
    "ashby": ashby.fetch,
}

# Search-based fetchers: fetch(client, company, title_ok=..., max_age_days=...) searches the
# site and fetches details only for postings whose title and age pass the filters.
SEARCH_FETCHERS = {
    "workday": workday.fetch,
    "oracle": oracle.fetch,
    "smartrecruiters": smartrecruiters.fetch,
    "successfactors": successfactors.fetch,
}

ALL_ATS = [*ATS_FETCHERS, *SEARCH_FETCHERS]
