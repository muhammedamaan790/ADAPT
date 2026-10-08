"""Ask ADAPT end to end: every agent tool on a bootstrapped fixture workspace, and the tool-calling loop through
POST /copilot/chat against a scripted Groq endpoint (no network): tool steps streamed, figures grounded, one
corrective retry, ungrounded figures listed, offline template, conversation history passed through."""

import json

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from adapt.agent.atoms import narrative_config
from adapt.agent.groq_client import GroqClient
from adapt.api import agent
from adapt.api.main import create_app
from adapt.api.runtime import bootstrap
from adapt.config.settings import Settings

MODEL = "openai/gpt-oss-120b"


def hdr(n) -> dict:
    return {"X-Request-ID": f"a-{n}", "Idempotency-Key": f"a-{n}"}


@pytest.fixture
def api(world, tmp_path):
    settings = Settings(data_dir=tmp_path / "data", workspace="agent")
    bootstrap(settings, world_client=world)
    with TestClient(create_app(settings, world_client=world, sync_jobs=True)) as c:
        yield c


def fake_groq(api, replies: list[dict]) -> list[dict]:
    """Install a GroqClient whose transport serves `replies` in order; returns the captured request bodies."""
    seen: list[dict] = []

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": MODEL}]})
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": replies[len(seen) - 1]}]})

    cfg = narrative_config()
    api.app.state.runtime._agent_client = GroqClient(
        "test-key", http=httpx.Client(transport=httpx.MockTransport(handle)), sleep=lambda s: None,
        cfg={**cfg["llm"], **cfg["copilot"]["llm"]})
    return seen


def call(name: str, args: dict | None = None, cid: str = "c1") -> dict:
    return {"role": "assistant", "content": "", "tool_calls": [
        {"id": cid, "type": "function", "function": {"name": name, "arguments": json.dumps(args or {})}}]}


def ask(api, message: str, n: int, history: list | None = None) -> list[dict]:
    r = api.post("/api/v1/copilot/chat", json={"message": message, "history": history or []}, headers=hdr(n))
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream"), r.text
    return [json.loads(line[5:]) for line in r.text.splitlines() if line.startswith("data:")]


def answer_of(events: list[dict]) -> dict:
    assert events[-1] == {"type": "done"}
    return next(e for e in events if e["type"] == "answer")["reply"]


def test_every_tool_answers_on_a_real_workspace(api):
    req = Request({"type": "http", "app": api.app, "method": "POST", "path": "/", "headers": [],
                   "query_string": b""})
    did = api.get("/api/v1/decisions").json()[0]["decision_id"]
    anomalies = api.get("/api/v1/anomalies").json()
    limit = narrative_config()["copilot"]["tool_result_chars"]
    for tool in agent.TOOLS:
        args = {"decision_id": did} if tool.name == "get_decision" else {}
        if tool.name == "get_anomaly":
            if not anomalies:
                continue
            args = {"anomaly_id": anomalies[0]["anomaly_id"]}
        raw, text = agent.run_tool(req, tool.name, args)
        assert raw is not None, f"{tool.name}: {text}"
        assert '"error"' not in text[:40] and len(text) <= limit + 20, tool.name
    assert agent.run_tool(req, "get_decision", {"decision_id": "nope"})[1].count("error")
    assert agent.run_tool(req, "drop_tables", {})[0] is None


def test_agent_reads_the_workspace_and_answers_with_grounded_figures(api):
    revenue = next(m for m in api.get("/api/v1/overview").json()["metrics"] if m["format"] == "money")
    text = f"Hi! {revenue['label']} over the last 7 days is ₹{revenue['value']:,.0f}."
    seen = fake_groq(api, [call("get_overview"), {"role": "assistant", "content": text}])
    events = ask(api, "hello, how are we doing?", 1)
    assert {"type": "status", "text": "Reading the Command Center"} in events
    reply = answer_of(events)
    assert reply["mode"] == "LLM" and reply["model"] == MODEL and reply["verified"] is True
    assert reply["text"] == text and reply["tools"] == ["get_overview"]
    assert reply["evidence"] == [{"label": "Command Center", "href": "/"}]
    assert seen[0]["tools"] and seen[0]["messages"][0]["role"] == "system"
    assert seen[1]["messages"][-1]["role"] == "tool" and revenue["label"] in seen[1]["messages"][-1]["content"]


def test_an_ungrounded_figure_gets_one_retry_then_is_listed(api):
    bad = {"role": "assistant", "content": "Net revenue is ₹98,76,543 this week."}
    seen = fake_groq(api, [call("get_overview"), bad, bad])
    events = ask(api, "what is net revenue?", 2)
    assert {"type": "status", "text": "Double-checking the figures"} in events
    reply = answer_of(events)
    assert reply["verified"] is False and reply["unverified"] == ["₹98,76,543"]
    assert len(seen) == 3 and "₹98,76,543" in seen[2]["messages"][-1]["content"]


def test_the_retry_can_repair_the_answer(api):
    decisions = api.get("/api/v1/decisions").json()
    good = {"role": "assistant", "content": f"There are {len(decisions)} decisions; open the Decision Center."}
    fake_groq(api, [call("list_decisions"), {"role": "assistant", "content": "Spend fell 87.3%."}, good])
    reply = answer_of(ask(api, "how many decisions?", 3))
    assert reply["verified"] is True and reply["text"] == good["content"]
    assert reply["evidence"] == [{"label": "Decision Center", "href": "/decisions"}]


def test_history_reaches_the_model_and_tool_steps_are_capped(api):
    steps = narrative_config()["copilot"]["max_tool_steps"]
    seen = fake_groq(api, [call("get_world_state", cid=f"c{i}") for i in range(steps)]
                     + [{"role": "assistant", "content": "Here is what I found."}])
    history = [{"role": "user", "content": "earlier question"}, {"role": "assistant", "content": "earlier answer"}]
    reply = answer_of(ask(api, "keep checking the world", 4, history))
    assert reply["text"] == "Here is what I found."
    assert [m["content"] for m in seen[0]["messages"][1:3]] == ["earlier question", "earlier answer"]
    assert len(seen) == steps + 1 and "tools" not in seen[-1]       # the last round must answer


def test_offline_mode_greets_and_answers_from_templates(api):
    api.app.state.runtime._agent_client = GroqClient(None)
    reply = answer_of(ask(api, "hi", 5))
    assert reply["mode"] == "TEMPLATE" and reply["text"].startswith("Hi!") and "GROQ_API_KEY" in reply["note"]
    reply = answer_of(ask(api, "what is pending?", 6))
    assert reply["mode"] == "TEMPLATE" and reply["evidence"]


def test_a_failing_model_falls_back_to_the_template(api):
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": MODEL}]})
        return httpx.Response(500)

    cfg = narrative_config()
    api.app.state.runtime._agent_client = GroqClient(
        "k", http=httpx.Client(transport=httpx.MockTransport(handle)), cfg={**cfg["llm"], **cfg["copilot"]["llm"]})
    reply = answer_of(ask(api, "what is pending?", 7))
    assert reply["mode"] == "TEMPLATE" and "AI unavailable" in reply["note"]
