"""v6.25: "Ask about this meeting" on the replay page.

- answers come only from the meeting: the model returns line ids, the server sends back the REAL lines
- ids that don't exist are dropped; "covered" without one real line = "The meeting didn't cover this."
- Muse Wonder first, Muse Spark as the fallback; both down -> 502
- limits per IP, per replay and server-wide (credits); deleted / unknown replays -> 404; Origin checked
"""
import gzip
import json

import httpx
import pytest

from app.ask import NOT_COVERED, Asker, AskError, AskLimit, meeting_from_replay
from app.config import settings
from app.main import app
from app.meta_api import MetaClient
from app.replay import LocalStore, events_key
from tests.test_e2e import ORIGIN, server  # noqa: F401  (pytest fixture)
from tests.test_v617_replay import _meeting

WONDER = "muse-wonder-1.0"


def _replay_bytes() -> bytes:
    events = [
        [1.0, {"type": "state", "state": "recording"}],
        [5.0, {"type": "transcript_final", "id": 0, "speaker": "A", "text": "Delivery takes 24 hours.", "t": 4.0}],
        [9.0, {"type": "transcript_partial", "speaker": "B", "text": "earl"}],
        [12.0, {"type": "transcript_final", "id": 1, "speaker": "B", "text": "Earlier you said 48 hours.", "t": 10.0}],
        [20.0, {"type": "transcript_final", "id": 2, "speaker": "A", "text": "Card payment <b>only</b>.", "t": 18.0}],
    ]
    body = {"v": 1, "title": "FreshFold", "date": "2026-11-04", "duration_s": 30, "speakers": {"A": "Owner"},
            "findings": [{"kind": "conflict", "text": "24 or 48 hours?", "step": "Deliver"}], "events": events}
    return gzip.compress(json.dumps(body).encode())


class _Store:
    def __init__(self, raw: bytes | None):
        self.raw = raw
        self.reads = 0

    async def get(self, key):
        self.reads += 1
        return self.raw


def _reply(content) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(content)}}]})


def _asker(handler) -> Asker:
    return Asker(MetaClient(httpx.AsyncClient(transport=httpx.MockTransport(handler))))


@pytest.fixture(autouse=True)
def _real_api(monkeypatch):
    monkeypatch.delenv("MOCK_META", raising=False)
    monkeypatch.setattr(settings, "ask_model", WONDER)


def test_meeting_from_replay():
    m = meeting_from_replay(_replay_bytes())
    assert m["title"] == "FreshFold"
    assert [(x["id"], x["speaker"], x["t"]) for x in m["lines"]] == [(0, "Owner", 4.0), (1, "Speaker B", 10.0),
                                                                      (2, "Owner", 18.0)]
    assert m["lines"][2]["text"] == "Card payment only."           # tags removed (clean_text)
    assert m["findings"][0]["text"] == "24 or 48 hours?"


async def test_answer_uses_real_lines_only():
    sent = []

    def handler(req):
        body = json.loads(req.content)
        sent.append(body)
        # 99 doesn't exist, "L1" written as a string, 0 twice; the model's own words are never used as quotes
        return _reply({"covered": True, "answer": "Owner said 24 hours, Speaker B said 48: not settled.",
                       "lines": [1, 99, "L0", 0]})

    a = _asker(handler)
    out = await a.ask("r" * 22, _Store(_replay_bytes()), "How long does delivery take?", "1.2.3.4")
    assert out["covered"] is True and out["by"] == "Muse Wonder"
    assert out["quotes"] == [{"t": 4.0, "speaker": "Owner", "text": "Delivery takes 24 hours."},
                             {"t": 10.0, "speaker": "Speaker B", "text": "Earlier you said 48 hours."}]
    user = json.loads(sent[0]["messages"][1]["content"])
    assert user["question"] == "How long does delivery take?"
    assert user["transcript"][0] == "L0 [00:04] Owner: Delivery takes 24 hours."
    assert sent[0]["model"] == WONDER and sent[0]["response_format"]["json_schema"]["name"] == "ask_meeting"


@pytest.mark.parametrize("content", [
    {"covered": True, "answer": "Made up.", "lines": [42]},          # no real line -> no answer
    {"covered": True, "answer": "Made up.", "lines": []},
    {"covered": False, "answer": "", "lines": [0]},
    {"answer": "no covered field"},
])
async def test_no_evidence_no_answer(content):
    a = _asker(lambda req: _reply(content))
    out = await a.ask("r" * 22, _Store(_replay_bytes()), "What is the refund policy?", "1.2.3.4")
    assert out == {"answer": NOT_COVERED, "covered": False, "quotes": [], "by": "Muse Wonder"}


async def test_wonder_down_falls_back_to_spark():
    models = []

    def handler(req):
        model = json.loads(req.content)["model"]
        models.append(model)
        if model == WONDER:
            return httpx.Response(500, json={"error": "overloaded"})
        return _reply({"covered": True, "answer": "Card only.", "lines": [2]})

    out = await _asker(handler).ask("r" * 22, _Store(_replay_bytes()), "How do they pay?", "1.2.3.4")
    assert models == [WONDER, WONDER, settings.spark_model] and out["by"] == "Muse Spark"
    assert out["quotes"][0]["text"] == "Card payment only."


async def test_both_down_is_an_error():
    a = _asker(lambda req: httpx.Response(500, json={"error": "down"}))
    with pytest.raises(AskError):
        await a.ask("r" * 22, _Store(_replay_bytes()), "How do they pay?", "1.2.3.4")


async def test_transcript_cached_once():
    store = _Store(_replay_bytes())
    a = _asker(lambda req: _reply({"covered": False, "answer": "", "lines": []}))
    for _ in range(3):
        await a.ask("r" * 22, store, "Anything about refunds?", "1.2.3.4")
    assert store.reads == 1
    a.forget("r" * 22)
    await a.ask("r" * 22, store, "Anything about refunds?", "1.2.3.4")
    assert store.reads == 2


async def test_limits(monkeypatch):
    monkeypatch.setattr(settings, "ask_per_replay_per_day", 2)
    a = _asker(lambda req: _reply({"covered": False, "answer": "", "lines": []}))
    store = _Store(_replay_bytes())
    await a.ask("a" * 22, store, "question one?", "1.1.1.1")
    await a.ask("a" * 22, store, "question two?", "2.2.2.2")
    with pytest.raises(AskLimit, match="This meeting"):
        await a.ask("a" * 22, store, "question three?", "3.3.3.3")
    await a.ask("b" * 22, store, "other meeting?", "3.3.3.3")       # another replay still works

    monkeypatch.setattr(settings, "ask_daily_max", 3)
    with pytest.raises(AskLimit, match="paused for today"):
        await a.ask("c" * 22, store, "server-wide?", "4.4.4.4")


async def test_ip_limit(monkeypatch):
    monkeypatch.setattr(settings, "ask_per_ip_per_hour", 2)
    a = _asker(lambda req: _reply({"covered": False, "answer": "", "lines": []}))
    store = _Store(_replay_bytes())
    for rid in ("a" * 22, "b" * 22):
        await a.ask(rid, store, "which step?", "2402:4000:1:2::1")
    with pytest.raises(AskLimit, match="a lot of questions"):
        await a.ask("c" * 22, store, "which step?", "2402:4000:1:2::ffff")   # same IPv6 /64 = same client


async def test_empty_meeting_costs_nothing():
    raw = gzip.compress(json.dumps({"v": 1, "speakers": {}, "events": []}).encode())

    def handler(req):
        raise AssertionError("no request expected")

    out = await _asker(handler).ask("r" * 22, _Store(raw), "What happened?", "1.2.3.4")
    assert out["covered"] is False and out["quotes"] == [] and out["by"] is None


async def test_ask_endpoint_end_to_end(server, tmp_path):
    """Real server + a real saved replay: the answer's quotes are the meeting's own lines; Origin, unknown id,
    empty question and a deleted replay are refused."""
    app.state.sessions.store = LocalStore(str(tmp_path))
    r, b = await _meeting(server)
    ready = b.of("replay_ready")[0]
    rid, key = ready["replay_id"], ready["replay_key"]
    raw = (tmp_path / events_key(rid)).read_bytes()
    lines = meeting_from_replay(raw)["lines"]
    assert lines

    def handler(req):
        body = json.loads(req.content)
        assert body["model"] == WONDER
        return _reply({"covered": True, "answer": "They talked about it.", "lines": [lines[0]["id"]]})

    asker = app.state.asker
    asker.meta = MetaClient(httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    url = f"{server['http']}/api/replays/{rid}/ask"
    async with httpx.AsyncClient() as c:
        ok = await c.post(url, json={"question": "What did they say first?"}, headers={"Origin": ORIGIN})
        assert ok.status_code == 200, ok.text
        out = ok.json()
        assert out["covered"] and out["by"] == "Muse Wonder"
        assert out["quotes"][0]["text"] == lines[0]["text"] and out["quotes"][0]["t"] == round(lines[0]["t"], 1)
        assert out["quotes"][0]["speaker"] == "Namidu"                   # the rename during the meeting

        bad = await c.post(url, json={"question": "x?"}, headers={"Origin": "https://evil.example"})
        assert bad.status_code == 403
        assert (await c.post(url, json={"question": "  "}, headers={"Origin": ORIGIN})).status_code in (400, 422)
        nope = await c.post(f"{server['http']}/api/replays/{'z' * 22}/ask", json={"question": "hello?"},
                            headers={"Origin": ORIGIN})
        assert nope.status_code == 404

        d = await c.delete(f"{server['http']}/api/replays/{rid}", headers={"Origin": ORIGIN, "X-Replay-Key": key})
        assert d.status_code == 200
        assert rid not in asker.cache
        gone = await c.post(url, json={"question": "What did they say first?"}, headers={"Origin": ORIGIN})
        assert gone.status_code == 404
