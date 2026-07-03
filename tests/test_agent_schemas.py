from app.schemas import AskRequest, AskResponse


def test_ask_request_requires_query():
    req = AskRequest.model_validate({"query": "when does 100T play next"})
    assert req.query == "when does 100T play next"


def test_ask_response_debug_fields_optional():
    resp = AskResponse(answer="hi")
    assert resp.tools_used is None and resp.model is None
    full = AskResponse(answer="hi", tools_used=[{"tool": "search", "args": {}}], model="gpt-5.4")
    assert full.model == "gpt-5.4"
