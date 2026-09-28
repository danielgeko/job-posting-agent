"""LLM fit scoring with Claude structured JSON output."""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

import anthropic
import yaml

from jobsearch.models import Posting, Score

log = logging.getLogger(__name__)

MAX_DESCRIPTION_CHARS = 6000

SCORE_SCHEMA = {
    "type": "object",
    "properties": {
        "fit_score": {"type": "integer", "description": "0-100"},
        "matched_skills": {"type": "array", "items": {"type": "string"}},
        "gaps": {"type": "array", "items": {"type": "string"}},
        "reason": {"type": "string", "description": "One line, at most 120 characters."},
    },
    "required": ["fit_score", "matched_skills", "gaps", "reason"],
    "additionalProperties": False,
}


def build_system_prompt(template_path: Path, resume_path: Path, preferences: dict) -> str:
    resume = resume_path.read_text() if resume_path.exists() else "(resume.yaml not provided yet)"
    # The filter regexes are noise for the model; send only the human-facing preferences.
    prefs = {k: v for k, v in preferences.items() if k != "filters"}
    return (
        template_path.read_text()
        .replace("{resume}", resume.strip())
        .replace("{preferences}", yaml.safe_dump(prefs, sort_keys=False).strip())
    )


def prompt_hash(system_prompt: str) -> str:
    return hashlib.sha256(system_prompt.encode()).hexdigest()[:16]


def posting_message(p: Posting, locations: list[str]) -> str:
    desc = (p.description or "").strip()
    if not desc:
        desc = "(No description available — community list entry.)"
    elif len(desc) > MAX_DESCRIPTION_CHARS:
        desc = desc[:MAX_DESCRIPTION_CHARS] + "\n[description truncated]"
    posted = p.posted_at.date().isoformat() if p.posted_at else "unknown"
    return (
        f"Company: {p.company}\n"
        f"Title: {p.title}\n"
        f"Locations: {'; '.join(locations) or 'unspecified'}\n"
        f"Posted: {posted}\n"
        f"Salary: {p.salary_raw or 'not listed'}\n\n"
        f"<description>\n{desc}\n</description>"
    )


class Scorer:
    def __init__(self, api_key: str | None, model: str, system_prompt: str):
        self.client = anthropic.Anthropic(api_key=api_key, max_retries=4)
        self.model = model
        self.system_prompt = system_prompt
        self.hash = prompt_hash(system_prompt)

    def score(self, p: Posting, locations: list[str]) -> Score:
        resp = self.client.messages.create(
            model=self.model,
            max_tokens=1024,
            # The system prompt (resume + preferences) is identical for every posting; cache it.
            # Haiku 4.5 only caches prefixes of 4096+ tokens, so a short resume won't hit the cache.
            system=[
                {
                    "type": "text",
                    "text": self.system_prompt,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": posting_message(p, locations)}],
            output_config={"format": {"type": "json_schema", "schema": SCORE_SCHEMA}},
        )
        if resp.stop_reason != "end_turn":
            raise ValueError(f"unexpected stop_reason {resp.stop_reason!r}")
        text = next(b.text for b in resp.content if b.type == "text")
        data = json.loads(text)
        return Score(
            fit_score=max(0, min(100, int(data["fit_score"]))),
            matched_skills=[str(s) for s in data["matched_skills"]],
            gaps=[str(s) for s in data["gaps"]],
            reason=str(data["reason"]).strip()[:160],
        )
