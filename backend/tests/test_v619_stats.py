"""v6.19: per-meeting Meta usage, the findings list and the replay timeline lanes are saved with the replay
(meta.json for Dashboard / Findings / Usage, events.json.gz for the replay page)."""
import json

import httpx

from app.config import BYTES_PER_SEC
from app.flow import Bug, Contradiction, Evidence, Gap, Step
from app.main import app
from app.replay import LocalStore, meta_key, summary
from app.session import Session
from tests.test_e2e import ORIGIN, server  # noqa: F401  (pytest fixture)
from tests.test_v617_replay import _meeting


def _ev(line, speaker, t, quote):
    return Evidence(line=line, speaker=speaker, t=t, quote=quote)


def _session() -> Session:
    s = Session(object(), [], "Coaching site", "1.2.3.4")
    s.speaker_names = {"A": "Namidu"}
    s.transcript = [
        {"id": 0, "turn": "t0", "speaker": "A", "text": "people open the coaching page", "t": 10.0, "t_end": 14.0},
        {"id": 1, "turn": "t1", "speaker": "A", "text": "and then they see the price", "t": 14.5, "t_end": 30.0},
        {"id": 2, "turn": "t2", "speaker": "B", "text": "price point is 200 per session", "t": 20.0, "t_end": 25.0},
        {"id": 3, "turn": "t3", "speaker": "A", "text": "the practice group meets daily", "t": 200.0, "t_end": 204.0},
    ]
    f = s.flow
    f.steps["s1"] = Step("s1", "Open coaching page", "step", 10.0, [_ev(0, "A", 10.0, "people open the coaching page")],
                         part="Behavioral coaching")
    f.steps["s2"] = Step("s2", "See session price", "step", 20.0, [_ev(2, "B", 20.0, "price point is 200 per session")],
                         part="Behavioral coaching")
    f.steps["s3"] = Step("s3", "Meet daily", "step", 200.0, [_ev(3, "A", 200.0, "the practice group meets daily")],
                         part="Practice group")
    f.gaps["g1"] = Gap("g1", "s2", "payment", "How do customers pay the $200?", 120.0,
                       [_ev(2, "B", 20.0, "price point is 200 per session")])
    f.gaps["g2"] = Gap("g2", "s1", "after opening", "What happens after the page?", 60.0,
                       [_ev(0, "A", 10.0, "people open the coaching page")], resolved=True, answer="they see the price",
                       resolved_by=_ev(1, "A", 14.5, "and then they see the price"))
    f.gaps["g3"] = Gap("g3", "s1", "x", "Dismissed one?", 70.0, [], dismissed=True)
    f.contradictions["c1"] = Contradiction("c1", "s3", "Daily or weekly?", _ev(3, "A", 200.0, "meets daily"),
                                           _ev(2, "B", 20.0, "price point is 200 per session"), 210.0)
    f.bugs["b1"] = Bug("b1", "s2", "Price page crashes", 90.0, [_ev(2, "B", 20.0, "price point is 200 per session")])
    s.flow_stats = {"accepted": 12, "rejected": 3, "rounds": 4}
    s.spark_requests = 9
    s.images = {"a" * 32: (b"x", "image/webp"), "b" * 32: (b"y", "image/webp")}
    s.audio.received_bytes = int(240 * BYTES_PER_SEC)
    return s


def test_findings_usage_counts():
    s = _session()
    info = s.replay_info()
    kinds = [(f["id"], f["kind"]) for f in info["findings"]]
    # oldest first, dismissed gaps left out, a resolved gap is "answered"
    assert kinds == [("g2", "answered"), ("b1", "bug"), ("g1", "gap"), ("c1", "conflict")]
    g1 = next(f for f in info["findings"] if f["id"] == "g1")
    assert g1["text"] == "How do customers pay the $200?" and g1["step"] == "See session price"
    assert g1["part"] == "Behavioral coaching" and g1["branch"] == "payment"
    assert g1["quotes"] == [{"t": 20.0, "speaker": "Speaker B", "text": "price point is 200 per session"}]
    g2 = next(f for f in info["findings"] if f["id"] == "g2")
    assert g2["answer"] == "they see the price" and g2["quotes"][-1]["speaker"] == "Namidu"      # answer quote last
    c1 = next(f for f in info["findings"] if f["id"] == "c1")
    assert c1["settled"] is False and len(c1["quotes"]) == 2 and c1["part"] == "Practice group"
    assert info["gaps"] == 1 and info["conflicts"] == 1 and info["bugs"] == 1 and info["answered"] == 1
    assert info["usage"] == {"voice_s": 240.0, "spark_calls": 9, "tool_calls": 15, "images": 2}
    json.dumps(info)                                       # stored as JSON


def test_timeline_lanes():
    tl = _session().timeline()
    # A's two lines join into one block (the late arrival time of line 1 is cut at B's start), then B, then A
    assert tl["speakers"] == [{"speaker": "A", "t": 10.0, "t_end": 20.0}, {"speaker": "B", "t": 20.0, "t_end": 25.0},
                              {"speaker": "A", "t": 200.0, "t_end": 204.0}]
    # one block per part: quotes within 60 s join, the block runs 10 s past the last quote
    assert tl["parts"] == [{"part": "Behavioral coaching", "t": 10.0, "t_end": 30.0},
                           {"part": "Practice group", "t": 200.0, "t_end": 210.0}]


def test_summary_keeps_new_fields_and_old_rows():
    old = summary({"rid": "r" * 22, "title": "Old", "date": "2026-11-01", "duration_s": 5, "gaps": 0, "conflicts": 0,
                   "created": 1})
    assert old["usage"] is None and old["findings"] is None and old["bugs"] is None    # saved before v6.19
    assert "key_sha" not in old and "owner" not in old


async def test_saved_with_replay(server, tmp_path):
    app.state.sessions.store = LocalStore(str(tmp_path))
    r, b = await _meeting(server)
    ready = b.of("replay_ready")[0]
    rid, info = ready["replay_id"], ready["info"]
    s = app.state.sessions.get(r["session_id"])
    assert info["usage"]["spark_calls"] == s.spark_requests > 0
    assert info["usage"]["images"] == len(s.images) > 0
    assert info["usage"]["voice_s"] > 5 and isinstance(info["findings"], list)
    meta = json.loads((tmp_path / meta_key(rid)).read_text())
    assert meta["usage"] == info["usage"] and meta["findings"] == info["findings"]
    async with httpx.AsyncClient() as c:
        rep = (await c.get(f"{server['http']}/api/replays/{rid}", headers={"Origin": ORIGIN})).json()
    assert rep["usage"] == info["usage"] and rep["findings"] == info["findings"]
    assert rep["timeline"]["speakers"] and all(x["t_end"] > x["t"] for x in rep["timeline"]["speakers"])
    assert rep["events"]                                  # the log itself is unchanged
