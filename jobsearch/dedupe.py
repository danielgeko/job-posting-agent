"""Dedupe against the Sheet and collapse multi-city duplicates within a run."""

from __future__ import annotations

from dataclasses import dataclass, field

from jobsearch.models import Posting
from jobsearch.normalize import merge_locations, normalize_company, normalize_title, normalize_url


@dataclass
class KnownJobs:
    """Jobs already in the main tracker or the Leads tab."""

    urls: set[str] = field(default_factory=set)
    company_titles: set[tuple[str, str]] = field(default_factory=set)

    def add(self, company: str, url: str, title: str) -> None:
        if url:
            self.urls.add(normalize_url(url))
        if company and title:
            self.company_titles.add((normalize_company(company), normalize_title(title)))

    def contains(self, p: Posting) -> bool:
        if p.url and normalize_url(p.url) in self.urls:
            return True
        return (normalize_company(p.company), normalize_title(p.title)) in self.company_titles


def group_key(p: Posting) -> tuple[str, str]:
    return normalize_company(p.company), normalize_title(p.title)


@dataclass
class Group:
    """One lead: a primary posting plus any same-company/same-title siblings in other cities."""

    primary: Posting
    members: list[Posting]

    @property
    def locations(self) -> list[str]:
        return merge_locations([m.locations for m in self.members])


# Prefer ATS sources over the community list: they carry descriptions and salary data.
_SOURCE_RANK = {"greenhouse": 0, "lever": 0, "ashby": 0, "workday": 0, "simplify": 1}


def group_postings(postings: list[Posting]) -> list[Group]:
    groups: dict[tuple[str, str], list[Posting]] = {}
    for p in postings:
        groups.setdefault(group_key(p), []).append(p)
    out = []
    for members in groups.values():
        members.sort(
            key=lambda m: (
                _SOURCE_RANK.get(m.source, 2),
                -len(m.description),
                -(m.posted_at.timestamp() if m.posted_at else 0),
            )
        )
        out.append(Group(primary=members[0], members=members))
    return out
