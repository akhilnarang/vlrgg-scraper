from app.agent.prompt import SYSTEM_PROMPT


def test_prompt_covers_grounding_and_narrowing():
    p = SYSTEM_PROMPT.lower()
    assert "only" in p and "tool" in p  # grounding
    assert "pages=0" in p  # full history instruction
    assert "narrow" in p  # too-large behavior
    assert "perspective" in p  # score perspective
    assert "count_team_matches" in p and "limit" in p  # counting guidance (avoid limit undercount)
    assert "plan" in p and "budget" in p  # plan minimal tool set within the call budget
    assert len(SYSTEM_PROMPT.split()) < 400  # stays compact (no glossary)
