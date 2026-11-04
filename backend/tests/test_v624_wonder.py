"""v6.24: the end-of-meeting summary + takeaways use Muse Wonder (REPORT_MODEL), with Muse Spark as the fallback.

- same request shape, only the model id changes; Spark calls keep using muse-spark-1.3
- a 400 from Muse Wonder (reasoning_effort / strict) never strips the extras from Muse Spark calls
- any Wonder failure -> the summary is written by Muse Spark; the report says which model wrote it
- usage counts Muse Wonder requests (Usage page, replay)
"""
import json

import httpx
import pytest

from app.config import settings
from app.main import app
from app.meta_api import MetaAPIError, MetaClient, build_spark_request
from app.prompts import SUMMARY_SCHEMA
from app.replay import LocalStore, meta_key
from app.session import Session
from tests.test_e2e import ORIGIN, server  # noqa: F401  (pytest fixture)
from tests.test_v617_replay import _meeting

WONDER = "muse-wonder-1.0"
GOOD = {"title": "T", "summary": "We agreed to ship login.", "takeaways": ["Login ships Friday"], "image_prompt": "rocket"}


def _reply(content: dict) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(content)}}]})


def _client(handler) -> MetaClient:
    return MetaClient(httpx.AsyncClient(transport=httpx.MockTransport(handler)))


@pytest.fixture(autouse=True)
def _real_api(monkeypatch):
    monkeypatch.delenv("MOCK_META", raising=False)
    monkeypatch.setattr(settings, "report_model", WONDER)


def test_request_body_model_override():
    spark = build_spark_request("s", "u", "summary", SUMMARY_SCHEMA)
    wonder = build_spark_request("s", "u", "summary", SUMMARY_SCHEMA, model=WONDER)
    assert spark["model"] == settings.spark_model and wonder["model"] == WONDER
    assert {k: v for k, v in wonder.items() if k != "model"} == {k: v for k, v in spark.items() if k != "model"}
    assert wonder["response_format"]["json_schema"]["strict"] is True and wonder["reasoning_effort"]


async def test_wonder_400_keeps_spark_extras():
    seen = []

    def handler(req):
        body = json.loads(req.content)
        seen.append((body["model"], "reasoning_effort" in body))
        if body["model"] == WONDER and "reasoning_effort" in body:
            return httpx.Response(400, json={"error": "unknown parameter"})
        return _reply(GOOD)

    m = _client(handler)
    assert await m.spark_json("s", "u", "summary", SUMMARY_SCHEMA, model=WONDER, attempts=2) == GOOD
    assert m.model_extras[WONDER] is False and m.spark_extras is True
    await m.spark_json("s", "u", "summary", SUMMARY_SCHEMA)               # a Spark call afterwards
    assert seen == [(WONDER, True), (WONDER, False), (settings.spark_model, True)]


async def test_spark_model_id_is_plain_spark():
    """model == the Spark id is the normal Spark path (shared extras flag), not a second flag."""
    def handler(req):
        return httpx.Response(400, json={"error": "x"}) if "reasoning_effort" in json.loads(req.content) else _reply(GOOD)

    m = _client(handler)
    await m.spark_json("s", "u", "summary", SUMMARY_SCHEMA, model=settings.spark_model)
    assert m.spark_extras is False and m.model_extras == {}


def _session(handler) -> Session:
    s = Session(_client(handler), [], "Laundry app", "1.2.3.4")
    s.transcript = [{"id": 0, "turn": "t0", "speaker": "A", "text": "we ship login on Friday", "t": 5.0, "t_end": 8.0}]
    return s


async def test_report_uses_wonder():
    models = []

    def handler(req):
        if req.url.path.endswith("/responses"):
            return httpx.Response(500)                                  # cover image: not part of this test
        models.append(json.loads(req.content)["model"])
        return _reply(GOOD)

    s = _session(handler)
    rep = await s._build_report()
    assert models == [WONDER]
    assert rep["summary_by"] == "Muse Wonder" and rep["takeaways"] == ["Login ships Friday"]
    assert s.wonder_requests == 1 and s.spark_requests == 0
    assert s.usage_info()["wonder_calls"] == 1


@pytest.mark.parametrize("failure", ["http500", "timeout", "bad_json"])
async def test_report_falls_back_to_spark(failure):
    models = []

    def handler(req):
        if req.url.path.endswith("/responses"):
            return httpx.Response(500)
        model = json.loads(req.content)["model"]
        models.append(model)
        if model == WONDER:
            if failure == "timeout":
                raise httpx.ReadTimeout("slow", request=req)
            if failure == "bad_json":
                return httpx.Response(200, json={"choices": [{"message": {"content": "not json"}}]})
            return httpx.Response(500, json={"error": "preview overloaded"})
        return _reply(GOOD)

    s = _session(handler)
    rep = await s._build_report()
    assert models == [WONDER, WONDER, settings.spark_model]            # 2 short tries, then Spark
    assert rep["summary_by"] == "Muse Spark" and rep["summary"] == "We agreed to ship login."
    assert s.wonder_requests == 1 and s.spark_requests == 1


async def test_report_model_empty_is_spark_only(monkeypatch):
    monkeypatch.setattr(settings, "report_model", "")
    models = []

    def handler(req):
        if req.url.path.endswith("/responses"):
            return httpx.Response(500)
        models.append(json.loads(req.content)["model"])
        return _reply(GOOD)

    s = _session(handler)
    rep = await s._build_report()
    assert models == [settings.spark_model] and rep["summary_by"] == "Muse Spark" and s.wonder_requests == 0


async def test_both_fail_no_summary():
    def handler(req):
        return httpx.Response(500, json={"error": "down"})

    s = _session(handler)
    rep = await s._build_report()
    assert rep["summary"] == "" and rep["takeaways"] == [] and rep["summary_by"] is None


async def test_no_transcript_no_request():
    def handler(req):
        raise AssertionError("no request expected")

    s = Session(_client(handler), [], "Empty", "1.2.3.4")
    rep = await s._build_report()
    assert rep["summary_by"] is None and s.wonder_requests == 0


async def test_meeting_end_to_end(server, tmp_path):
    """Real server + fake Meta API (which rejects reasoning_effort): the report summary goes to Muse Wonder,
    its 400 is retried with the plain body, the live Spark calls are unaffected, usage is saved with the replay."""
    app.state.sessions.store = LocalStore(str(tmp_path))
    app.state.meta.model_extras.clear()
    r, b = await _meeting(server)
    rep = b.of("report_ready")[0]["report"]
    assert rep["summary_by"] == "Muse Wonder" and rep["summary"]
    wonder = [x for x in server["spark"] if x["model"] == WONDER]
    assert len(wonder) == 1 and wonder[0]["response_format"]["json_schema"]["name"] == "summary"
    assert all(x["model"] == settings.spark_model for x in server["spark"] if x is not wonder[0])
    assert app.state.meta.model_extras.get(WONDER) is False
    ready = b.of("replay_ready")[0]
    assert ready["info"]["usage"]["wonder_calls"] == 1
    meta = json.loads((tmp_path / meta_key(ready["replay_id"])).read_text())
    assert meta["usage"]["wonder_calls"] == 1
