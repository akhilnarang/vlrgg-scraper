"""The /ask model-tool loop: drives gpt-5.4 over the tool registry via the Responses API."""

import asyncio
import json

from openai import AsyncOpenAI
from pydantic import BaseModel

from app.agent.prompt import SYSTEM_PROMPT
from app.agent.tools import build_tools
from app.core.config import settings
from app.schemas import AskResponse


def _to_json(result) -> str:
    """Serialize a tool result, handling Pydantic models nested anywhere."""
    return json.dumps(result, default=lambda o: o.model_dump(mode="json") if isinstance(o, BaseModel) else str(o))


def _make_client(client):
    """Return the injected client, or build one pointed at the configured LLM proxy."""
    if client is not None:
        return client
    return AsyncOpenAI(api_key=settings.LLM_API_KEY, base_url=settings.LLM_BASE_URL)


async def _dispatch_one(dispatch, name, raw_args, trace):
    """Run one tool call, recording it in the trace and returning its JSON result (errors become {error}).

    ``raw_args`` is the model's raw JSON argument string; malformed JSON is fed back as a tool
    error rather than raising (a bad model emission must not 500 the endpoint).
    """
    try:
        args = json.loads(raw_args or "{}")
        if not isinstance(args, dict):
            raise ValueError("tool arguments must be a JSON object")
    except (json.JSONDecodeError, ValueError) as e:
        if trace is not None:
            trace.append({"tool": name, "args": raw_args, "error": "bad_arguments"})
        return _to_json({"error": f"malformed arguments: {e}"})
    if trace is not None:
        trace.append({"tool": name, "args": args})
    fn = dispatch.get(name)  # look up outside the try so a tool-internal KeyError isn't misreported
    if fn is None:
        return _to_json({"error": f"unknown tool {name}"})
    try:
        return _to_json(await fn(**args))
    except Exception as e:  # tool failure fed back to the model, not a 500
        return _to_json({"error": f"{type(e).__name__}: {e}"})


async def run_ask(query: str, redis_client, client=None) -> AskResponse:
    """Run the gpt-5.4 tool-calling loop (OpenAI Responses API) and return the answer."""
    model = settings.LLM_MODEL
    schemas, dispatch = build_tools(redis_client)
    trace: list[dict] | None = [] if settings.LLM_DEBUG else None
    if trace is not None:
        trace.append({"tool": "_registered", "args": {"names": list(dispatch.keys())}})
    client = _make_client(client)

    rk = {"reasoning": {"effort": settings.LLM_REASONING_EFFORT}} if settings.LLM_REASONING_EFFORT else {}
    input_list = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": query}]
    answer = "I couldn't complete that within the step limit."
    budget = settings.LLM_MAX_TOOL_CALLS
    calls_used = 0
    for step in range(settings.LLM_MAX_STEPS):
        last_step = step == settings.LLM_MAX_STEPS - 1
        # offer tools only while steps and the total tool-call budget both remain; else force an answer
        tools = schemas if not last_step and calls_used < budget else []
        resp = await client.responses.create(model=model, input=input_list, tools=tools, **rk)
        fcs = [o for o in resp.output if getattr(o, "type", None) == "function_call"]
        if not fcs:
            answer = resp.output_text or "I wasn't able to produce an answer for that."
            break
        # resubmit function_call items by value; drop reasoning items (the proxy doesn't persist responses)
        for o in fcs:
            input_list.append({"type": "function_call", "call_id": o.call_id, "name": o.name, "arguments": o.arguments})
        # honour the total budget: run calls up to the remaining allowance, deny the overflow
        allowed = fcs[: max(0, budget - calls_used)]
        denied = fcs[len(allowed):]
        # pass raw argument strings; _dispatch_one parses inside its try so malformed JSON can't 500
        results = await asyncio.gather(*[
            _dispatch_one(dispatch, fc.name, fc.arguments, trace) for fc in allowed])
        calls_used += len(allowed)
        for fc, content in zip(allowed, results):
            input_list.append({"type": "function_call_output", "call_id": fc.call_id, "output": content})
        for fc in denied:
            if trace is not None:
                trace.append({"tool": fc.name, "args": fc.arguments, "denied": "budget"})
            input_list.append({"type": "function_call_output", "call_id": fc.call_id,
                               "output": _to_json({"error": "tool-call budget exhausted; answer with the data you have"})})

    if settings.LLM_DEBUG:
        return AskResponse(answer=answer, tools_used=trace, model=model)
    return AskResponse(answer=answer)
