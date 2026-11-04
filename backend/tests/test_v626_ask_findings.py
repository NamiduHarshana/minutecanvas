"""v6.26: "gaps?" / "what is still open?" came back "The meeting didn't cover this." on a meeting with 2 gaps and
2 conflicts (live test 4 Nov): a gap is what nobody said, so no transcript line can show it, and the server dropped
every answer without a line. Now the answer may cite this meeting's findings (by id); the server keeps only real ids
and sends back the real finding — still no evidence, no answer."""
import gzip
import json

import httpx
import pytest

from app.ask import NOT_COVERED, Asker, meeting_from_replay
from app.config import settings
from app.meta_api import MetaClient

FINDINGS = [
    {"id": "g1", "kind": "gap", "text": "How do customers pay for the sessions?", "step": "Do mock interview", "t": 150.0,
     "quotes": [{"t": 60.0, "speaker": "Speaker A", "text": "then you go ahead and do a mock interview"}]},
    {"id": "c1", "kind": "conflict", "text": "Single session price: $200 vs $250", "step": None, "t": 140.0,
     "settled": False, "quotes": []},
    {"id": "g2", "kind": "answered", "text": "What if no driver accepts?", "answer": "Owner assigns by hand", "t": 90.0},
    {"id": "x9", "kind": "something-else", "text": "ignored kind", "t": 1.0},
]


def _raw(findings=FINDINGS) -> bytes:
    events = [[3.0, {"type": "transcript_final", "id": 0, "speaker": "A", "text": "five sessions for 1000", "t": 2.0}],
              [9.0, {"type": "transcript_final", "id": 1, "speaker": "A", "text": "250 per session", "t": 8.0}]]
    return gzip.compress(json.dumps({"v": 1, "speakers": {}, "findings": findings, "events": events}).encode())


class _Store:
    def __init__(self, raw):
        self.raw = raw

    async def get(self, key):
        return self.raw


def _asker(handler) -> Asker:
    return Asker(MetaClient(httpx.AsyncClient(transport=httpx.MockTransport(handler))))


def _reply(content) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(content)}}]})


@pytest.fixture(autouse=True)
def _real_api(monkeypatch):
    monkeypatch.delenv("MOCK_META", raising=False)


def test_findings_sent_with_ids():
    m = meeting_from_replay(_raw())
    assert [f["id"] for f in m["findings"]] == ["g1", "c1", "g2"]           # unknown kinds left out
    assert m["findings"][2]["answer"] == "Owner assigns by hand"


async def test_gaps_question_answered_from_findings():
    sent = []

    def handler(req):
        body = json.loads(req.content)
        sent.append(json.loads(body["messages"][1]["content"]))
        # no transcript line (a gap is what nobody said), two real findings + one made-up id
        return _reply({"covered": True, "answer": "Nobody said how customers pay; the single price is unclear.",
                       "lines": [], "findings": ["c1", "g1", "zz"]})

    out = await _asker(handler).ask("r" * 22, _Store(_raw()), "gaps?", "1.2.3.4")
    assert out["covered"] is True and out["quotes"] == []
    assert [(f["id"], f["kind"], f["t"]) for f in out["findings"]] == [("c1", "conflict", 140.0), ("g1", "gap", 150.0)]
    assert out["findings"][1]["text"] == "How do customers pay for the sessions?"
    assert "quotes" not in sent[0]["findings"][0]                        # the model gets ids + text, not quotes
    assert sent[0]["findings"][0]["id"] == "g1"


async def test_lines_and_findings_together():
    out = await _asker(lambda req: _reply({"covered": True, "answer": "250 per session was said; still unclear.",
                                           "lines": [1], "findings": ["c1"]})
                       ).ask("r" * 22, _Store(_raw()), "What does one session cost?", "1.2.3.4")
    assert [q["text"] for q in out["quotes"]] == ["250 per session"] and [f["id"] for f in out["findings"]] == ["c1"]


@pytest.mark.parametrize("content", [
    {"covered": True, "answer": "Made up.", "lines": [], "findings": ["nope"]},     # only fake ids
    {"covered": True, "answer": "", "lines": [0], "findings": ["g1"]},             # no words
    {"covered": False, "answer": "x", "lines": [0], "findings": ["g1"]},
])
async def test_still_no_evidence_no_answer(content):
    out = await _asker(lambda req: _reply(content)).ask("r" * 22, _Store(_raw()), "gaps?", "1.2.3.4")
    assert out == {"answer": NOT_COVERED, "covered": False, "quotes": [], "findings": [], "by": "Muse Wonder"}


async def test_old_replay_without_finding_ids():
    """Replays saved before v6.19 have no findings list: lines still work, finding ids are ignored."""
    out = await _asker(lambda req: _reply({"covered": True, "answer": "Five for 1000.", "lines": [0], "findings": ["g1"]})
                       ).ask("r" * 22, _Store(_raw(findings=None)), "Price of five?", "1.2.3.4")
    assert out["covered"] and out["findings"] == [] and out["quotes"][0]["text"] == "five sessions for 1000"


def test_schema_requires_findings():
    from app.prompts import ASK_SCHEMA
    assert "findings" in ASK_SCHEMA["required"] and settings.ask_model
