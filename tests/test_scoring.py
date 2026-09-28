import json
from types import SimpleNamespace

from jobsearch.models import Posting
from jobsearch.scoring import SCORE_SCHEMA, Scorer, build_system_prompt, posting_message

ROOT = __import__("pathlib").Path(__file__).parent.parent


class FakeMessages:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            stop_reason="end_turn",
            content=[SimpleNamespace(type="text", text=json.dumps(self.payload))],
        )


def test_scorer_request_and_parse(preferences):
    prompt = build_system_prompt(ROOT / "prompts" / "score.md", ROOT / "missing.yaml", preferences)
    assert "{resume}" not in prompt and "exclude_title_patterns" not in prompt
    s = Scorer(api_key="test", model="claude-haiku-4-5", system_prompt=prompt)
    fake = FakeMessages({"fit_score": 140, "matched_skills": ["C#"], "gaps": [], "reason": "Strong .NET match"})
    s.client = SimpleNamespace(messages=fake)
    p = Posting(source="greenhouse", external_id="1", company="Acme", title="Software Engineer I",
                url="https://x", description="x" * 10000)
    result = s.score(p, ["Detroit, MI"])
    assert result.fit_score == 100  # clamped
    call = fake.calls[0]
    assert call["model"] == "claude-haiku-4-5"
    assert call["output_config"]["format"]["schema"] == SCORE_SCHEMA
    assert call["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "[description truncated]" in call["messages"][0]["content"]


def test_posting_message_without_description():
    p = Posting(source="simplify", external_id="1", company="Acme", title="SWE", url="https://x")
    assert "No description available" in posting_message(p, [])
