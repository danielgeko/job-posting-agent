from jobsearch.dedupe import KnownJobs, group_postings
from jobsearch.models import Posting


def post(company, title, url, locs, source="greenhouse", desc=""):
    return Posting(source=source, external_id=url, company=company, title=title, url=url,
                   locations=locs, description=desc)


def test_known_by_url_then_company_title():
    known = KnownJobs()
    known.add("Stripe", "https://stripe.com/jobs/search?gh_jid=1&utm=x", "Software Engineer, New Grad")
    assert known.contains(post("Other", "Different", "https://stripe.com/jobs/search?gh_jid=1", []))
    assert known.contains(post("Stripe, Inc.", "Software Engineer - New Grad (2027)", "https://new", []))
    assert not known.contains(post("Stripe", "Software Engineer, Backend", "https://new", []))


def test_rovisys_multi_city_collapses_to_one_group():
    cities = ["Aurora, OH", "Houston, TX", "Goodyear, AZ", "Charlotte, NC",
              "Newark, DE", "Ann Arbor, MI", "Pittsburgh, PA", "Detroit, MI"]
    posts = [post("RoviSys", "Entry Level Engineer/Developer", f"https://r/{i}", [c])
             for i, c in enumerate(cities)]
    posts.append(post("RoviSys", "Entry Level Engineer/Developer - Remote", "https://r/9", ["Detroit, MI"]))
    groups = group_postings(posts)
    assert len(groups) == 1
    assert groups[0].locations == cities


def test_group_prefers_ats_source_with_description():
    a = post("Acme", "Software Engineer", "https://simplify", ["NY"], source="simplify")
    b = post("Acme", "Software Engineer", "https://gh", ["SF"], source="greenhouse", desc="full text")
    (g,) = group_postings([a, b])
    assert g.primary is b
    assert set(g.locations) == {"NY", "SF"}
