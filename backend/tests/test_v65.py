"""v6.5: voice resend after a dropped session, line times from speech start, flow second opinions, gap rules."""
import asyncio
import json
import time

import pytest

from app.audio import AudioBuffer
from app.board import Board
from app.config import BYTES_PER_SEC, settings
from app.flow import Flow
from app.meta_voice import VoiceStream
from tests.test_e2e import Browser, create, server  # noqa: F401  (fixture)
from app.meta_api import MetaClient
from app.session import Session


def _session() -> Session:
    return Session(MetaClient(), [], "Test", "127.0.0.1")

BPS = BYTES_PER_SEC
LOUD = b"\x00\x20"   # one loud PCM16 sample (silence would be skipped while behind)


# ---------------- audio resend ----------------
def test_audio_requeue_unconfirmed():
    a = AudioBuffer(60, resend_max_s=5)
    a.append_at(0, LOUD * (BPS // 2 * 3))          # 3 s of audio
    out = a.take(BPS * 2)
    assert len(out) == BPS * 2 and a.front == BPS * 2
    a.confirm_until(BPS)                                   # Meta finished a turn ending at 1 s
    assert a.hist_front == BPS and len(a.hist) == BPS
    n = a.requeue_unconfirmed()                            # session died: 1 s goes back in front
    assert n == BPS and a.front == BPS and a.backlog_s == pytest.approx(2.0)
    assert a.received_bytes == 3 * BPS                     # meeting clock is not touched
    assert a.take(BPS) == out[BPS:]                        # the same audio again, in order


def test_audio_resend_history_is_capped_and_breaks_on_drop():
    a = AudioBuffer(60, resend_max_s=1)
    a.append_at(0, LOUD * (BPS // 2 * 3))
    a.take(BPS * 3)
    assert len(a.hist) == BPS and a.hist_front == 2 * BPS   # only the last second is kept
    b = AudioBuffer(1, resend_max_s=10)                     # backlog cap 1 s -> audio dropped before sending
    b.append_at(0, LOUD * (BPS // 2))
    b.take(BPS // 2)
    b.append_at(BPS // 2, LOUD * (BPS // 2 * 2))      # 1 s more: 0 dropped (cap 1 s)
    b.append_at(BPS * 3 // 2 + BPS // 2, LOUD * (BPS // 2))   # gap filled, overflow drops the front
    b.take(BPS)
    assert b.hist_front + len(b.hist) == b.front           # history stays contiguous with what was sent


def test_confirm_keeps_open_turns():
    a = AudioBuffer(60)
    v = VoiceStream(a, None, [])
    a.append_at(0, LOUD * (BPS // 2 * 10))
    a.take(BPS * 10)
    v._turn_start = {"7": 4 * BPS}
    v._confirm()
    assert a.hist_front == 4 * BPS                         # audio of the open turn stays for a resend
    v._turn_start = {}
    v._confirm()
    assert a.hist_front == int(10 * BPS - 0.7 * BPS)


async def test_dropped_session_resends_and_clears_partial(server):  # noqa: F811
    asr = server["asr"]
    asr.fail_first_at_s = 2.2
    r = (await create(server)).json()
    b = Browser(server["ws"], r["session_id"], r["token"])
    await b.connect()
    await b.ws.send(json.dumps({"type": "start"}))
    t0 = time.monotonic()
    for i in range(70):
        b.capture(0.1)
        await b.ws.send((len(b.captured) - 4800).to_bytes(8, "little") + bytes(b.captured[-4800:]))
        await asyncio.sleep(max(0, t0 + (i + 1) * 0.1 - time.monotonic()))
    await b.ws.send(json.dumps({"type": "stop"}))
    for _ in range(300):
        if b.of("report_ready"):
            break
        await asyncio.sleep(0.1)
    assert asr.sessions >= 2
    # session 2 heard the unfinished audio of session 1 again
    assert sum(asr.per_session) > len(b.captured), (asr.per_session, len(b.captured))
    # the dead session's partial was cleared when it died, never added at the end
    cleared = [e["turn"] for e in b.of("transcript_partial_clear")]
    assert any(t.startswith("1-") for t in cleared), cleared
    texts = [f["text"] for f in b.of("transcript_final")]
    assert not any("Half a sentence" in t for t in texts), texts
    ts = [f["t"] for f in b.of("transcript_final")]
    assert ts == sorted(ts)
    assert all(f["t"] <= f["t_end"] for f in b.of("transcript_final"))
    assert b.of("report_ready")


# ---------------- line times ----------------
async def test_line_time_is_speech_start(monkeypatch):
    s = _session()
    s.audio.received_bytes = 100 * BPS
    await s._on_voice("final", {"turn": "1-1", "speaker": "A", "text": "first line here", "t_start": 80.0})
    await s._on_voice("final", {"turn": "1-2", "speaker": "B", "text": "overlapping turn", "t_start": 75.0})
    await s._on_voice("final", {"turn": "1-3", "speaker": "B", "text": "no start known"})
    await s._on_voice("final", {"turn": "1-4", "speaker": "B", "text": "start in the future", "t_start": 500.0})
    t = [x["t"] for x in s.transcript]
    assert t == [80.0, 80.0, 100.0, 100.0]
    assert all(x["t_end"] == 100.0 for x in s.transcript)


async def test_gen_end_drops_or_keeps_partials():
    s = _session()
    s.state = "recording"
    s.audio.received_bytes = 50 * BPS
    await s._on_voice("partial", {"turn": "1-live", "speaker": None, "text": "half a turn", "t_start": 40.0})
    await s._on_voice("gen_end", {"gen": 1, "resent_s": 12.0, "error": "x", "stalls": 0})
    assert not s.partials and not s.transcript              # resent -> session 2 writes it again
    await s._on_voice("partial", {"turn": "2-live", "speaker": None, "text": "other half", "t_start": 45.0})
    await s._on_voice("gen_end", {"gen": 2, "resent_s": 0, "error": "x", "stalls": 0})
    assert not s.partials and [x["text"] for x in s.transcript] == ["other half"]
    assert s.transcript[0]["t"] == 45.0                     # at the time it was said, not at the end
    assert len(s.voice_log) == 2


# ---------------- flow rules ----------------
def _lines(*texts, gap_s=10.0):
    return [{"id": i, "speaker": "A", "t": i * gap_s, "text": t} for i, t in enumerate(texts)]


def test_new_link_closes_after_gap():
    f = Flow()
    L = _lines("people open the website and see the plans", "they book a slot in the coach calendar",
               "after booking they get a confirmation email with the zoom link")
    f.apply("add_step", {"label": "Open website", "kind": "start", "quote": "people open the website"}, L, 1)
    f.apply("add_step", {"label": "Book a slot", "kind": "step", "after": "s1", "quote": "they book a slot"}, L, 1)
    r = f.apply("flag_gap", {"step": "s2", "missing_branch": "after booking", "question": "What happens after booking?",
                             "quote": "they book a slot in the coach calendar"}, L, 15)
    assert r.ok, r.message
    r = f.apply("add_step", {"label": "Get confirmation email", "kind": "step", "after": "s2",
                             "quote": "they get a confirmation email with the zoom link"}, L, 25)
    assert r.ok and r.resolved_gap is not None and f.gaps["g1"].resolved
    assert f.gaps["g1"].answer == "Next: Get confirmation email"
    assert any(e["kind"] == "gap_resolved" for e in r.events)
    # and a new "what happens after" gap on a step that already has an arrow out is rejected
    r = f.apply("flag_gap", {"step": "s2", "missing_branch": "next step", "question": "What comes next after the slot?",
                             "quote": "they book a slot in the coach calendar"}, L, 30)
    assert not r.ok and "already shows what comes after" in r.message["error"]


def test_rename_to_unrelated_label_much_later_is_rejected():
    f = Flow()
    L = _lines("people open the website", "they fill the trello board with their stories",
               *["filler line number %d about other things" % i for i in range(8)],
               "then they watch the system design videos",
               "instead of a trello board it might be better to just put a form there")
    f.apply("add_step", {"label": "Open website", "kind": "start", "quote": "people open the website"}, L, 1)
    f.apply("add_step", {"label": "Fill Trello board", "kind": "step", "after": "s1",
                         "quote": "they fill the trello board with their stories"}, L, 10)
    r = f.apply("update_step", {"step": "s2", "label": "Watch design videos",
                                "quote": "then they watch the system design videos"}, L, 100)
    assert not r.ok and "add_step" in r.message["error"]
    r = f.apply("update_step", {"step": "s2", "label": "Submit answers via form",
                                "quote": "instead of a trello board it might be better to just put a form there"}, L, 110)
    assert r.ok and f.steps["s2"].label == "Submit answers via form"     # a replacement is a real rename


# ---------------- second opinions on flow changes ----------------
def _fake_verify(monkeypatch, s, answers: dict):
    seen = []

    async def fake(system, user, name, schema, history=None):
        assert name == "verify_flow"
        p = json.loads(user)
        seen.append(p)
        return answers[p["check"]]
    monkeypatch.setattr(s.meta, "spark_json", fake)
    monkeypatch.setattr(settings, "verify_flow", True)
    return seen


class _Acc:
    def __init__(self):
        self.accepted = self.rejected = 0
        self.events, self.cards_added, self.cards_updated = [], [], []


def _meeting(s):
    texts = ["you meet daily and practice questions", "we keep track of notes and focus on areas where we are weak",
             "i also wanted to add one more section where every day you get a question",
             "and the review sessions what we do is basically we review your answers give you feedback",
             "this is a 200 dollar price point per session for coaching",
             "they click a calendly link and then they schedule the session",
             "someone is going to look at your submitted answers and provide feedback later on"]
    s.transcript = [{"id": i, "turn": f"1-{i}", "speaker": "A", "t": i * 60.0, "text": t} for i, t in enumerate(texts)]
    return s.transcript


async def test_far_link_is_not_second_guessed(monkeypatch):
    """v6.6: the v6.5 arrow check was removed (it rejected right arrows); a new part starts without "after"."""
    s = _session()
    L = _meeting(s)
    seen = _fake_verify(monkeypatch, s, {})
    acc = _Acc()
    await s._apply_flow_call("add_step", {"label": "Practice questions daily", "kind": "start", "part": "Practice",
                                          "quote": "you meet daily and practice questions"}, L, acc)
    out = await s._apply_flow_call("add_step", {"label": "Focus weak areas", "after": "s1", "kind": "step",
                                                "quote": "focus on areas where we are weak"}, L, acc)
    assert out["ok"] and s.flow.steps["s2"].part == "Practice"        # same part as the step before it
    out = await s._apply_flow_call("add_step", {"label": "Get daily question", "kind": "step", "part": "Daily question",
                                                "quote": "every day you get a question"}, L, acc)
    assert out["ok"] and not s.flow.links_into("s3") and not seen
    assert s.flow.for_agent()["unlinked_steps"] == []                  # first step of its part: not "unlinked"


async def test_wrong_gap_answer_rejected_and_answered_gap_not_flagged(monkeypatch):
    s = _session()
    L = _meeting(s)
    acc = _Acc()
    seen = _fake_verify(monkeypatch, s, {"link": {"yes": True, "why": "", "line": ""},
                                         "answer": {"yes": False, "why": "about scheduling", "line": ""},
                                         "already_answered": {"yes": True, "why": "we review your answers", "line": "03:00"}})
    await s._apply_flow_call("add_step", {"label": "Practice questions daily", "kind": "start",
                                          "quote": "you meet daily and practice questions"}, L, acc)
    await s._apply_flow_call("add_step", {"label": "Submit answers", "after": "s1", "kind": "step",
                                          "quote": "look at your submitted answers and provide feedback"}, L, acc)
    out = await s._apply_flow_call("flag_gap", {"step": "s2", "missing_branch": "reviewer hand-off",
                                                "question": "Who reviews the submitted answers?",
                                                "quote": "look at your submitted answers"}, L, acc)
    assert not out["ok"] and "already answered at 03:00" in out["error"]
    assert any("we review your answers" in l for l in seen[-1]["earlier_lines"])
    # a gap that was not answered: kept, then a wrong "answer" is rejected
    s.flow_checks = 0
    seen.clear()
    monkeypatch.setattr(s, "_verify_flow", _verify_by_check({"already_answered": {"yes": False},
                                                              "answer": {"yes": False, "why": "about scheduling"}}))
    out = await s._apply_flow_call("flag_gap", {"step": "s2", "missing_branch": "payment fails",
                                                "question": "What happens if the payment fails?",
                                                "quote": "look at your submitted answers"}, L, acc)
    assert out["ok"]
    out = await s._apply_flow_call("resolve", {"item_id": "g1", "answer": "schedule via Calendly",
                                               "quote": "and then they schedule the session"}, L, acc)
    assert not out["ok"] and "does not answer" in out["error"] and not s.flow.gaps["g1"].resolved


def _verify_by_check(answers):
    async def fake(payload):
        return answers.get(payload["check"])
    return fake


async def test_flow_checks_fail_open_and_are_capped(monkeypatch):
    s = _session()
    L = _meeting(s)
    acc = _Acc()

    async def boom(*a, **k):
        raise RuntimeError("meta down")
    monkeypatch.setattr(s.meta, "spark_json", boom)
    monkeypatch.setattr(settings, "verify_flow", True)
    await s._apply_flow_call("add_step", {"label": "Submit answers", "kind": "start",
                                          "quote": "look at your submitted answers and provide feedback"}, L, acc)
    s.flow.mode = "final"
    out = await s._apply_flow_call("flag_gap", {"step": "s1", "missing_branch": "reviewer hand-off",
                                                "question": "Who reviews the submitted answers?",
                                                "quote": "look at your submitted answers"}, L, acc)
    assert out["ok"]                                          # check failed -> change kept
    s.flow_checks = settings.max_flow_checks
    called = []
    monkeypatch.setattr(s, "_verify_flow", lambda p: called.append(p))
    s.flow.gap_budget = 5
    out = await s._apply_flow_call("flag_gap", {"step": "s1", "missing_branch": "payment fails",
                                                "question": "What happens if the payment fails?",
                                                "quote": "look at your submitted answers"}, L, acc)
    assert out["ok"] and not called


async def test_gap_answer_time_on_card():
    s = _session()
    L = _meeting(s)
    acc = _Acc()
    settings_verify = settings.verify_flow
    settings.verify_flow = False
    try:
        await s._apply_flow_call("add_step", {"label": "Practice questions daily", "kind": "start",
                                              "quote": "you meet daily and practice questions"}, L, acc)
        await s._apply_flow_call("flag_gap", {"step": "s1", "missing_branch": "payment fails",
                                              "question": "What happens if the payment fails?",
                                              "quote": "you meet daily and practice questions"}, L, acc)
        card = s.board.cards[s.flow.gaps["g1"].card_id]
        await s._apply_flow_call("resolve", {"item_id": "g1", "answer": "they retry",
                                             "quote": "they click a calendly link"}, L, acc)
        assert card.resolved and card.answer_t == 300.0       # the answer line's time, not "now"
    finally:
        settings.verify_flow = settings_verify


def test_board_reopen_clears_answer_time():
    b = Board()
    c, _ = b.add_gap_question("g1", "What happens if payment fails?", "x", 10)
    b.resolve_gap_question(c.id, "retry", 50, 40)
    assert c.answer_t == 40
    b.user_edit(c.id, {"resolved": False})
    assert c.answer_t is None


def test_next_gap_on_a_money_step_is_closed_by_an_arrow():
    f = Flow()
    L = _lines("people open the website and see the plans", "i want to basically charge them money right away",
               "then they see the companies in our network")
    f.apply("add_step", {"label": "Open website", "kind": "start", "quote": "people open the website"}, L, 1)
    f.apply("add_step", {"label": "Charge session fee", "kind": "step", "after": "s1",
                         "quote": "charge them money right away"}, L, 1)
    r = f.apply("flag_gap", {"step": "s2", "missing_branch": "after payment",
                             "question": "What happens after the session fee is charged?",
                             "quote": "charge them money right away"}, L, 15)
    assert r.ok
    f.apply("flag_gap", {"step": "s2", "missing_branch": "payment fails", "question": "What happens if the payment fails?",
                         "quote": "charge them money right away"}, L, 16)
    f.gap_budget = 5
    r = f.apply("add_step", {"label": "View placement network", "kind": "step", "after": "s2",
                             "quote": "they see the companies in our network"}, L, 30)
    assert r.resolved_gap is f.gaps["g1"]
    assert [g.id for g in f.open_gaps()] == [k for k in f.gaps if k != "g1"]   # "payment fails" stays open


async def test_voice_turn_start_and_orphan_turns():
    a = AudioBuffer(60)
    got = []

    async def on_event(kind, data):
        got.append((kind, data))
    v = VoiceStream(a, on_event, [])
    a.append_at(0, LOUD * (BPS // 2 * 20))
    a.take(BPS * 5)
    v._turn_start = {"3": 2 * BPS, "4": 4 * BPS}   # turn 3 never finished
    v._turn_done("4")
    v._confirm()
    assert not v._turn_start and a.hist_front == int(5 * BPS - 0.7 * BPS)
    assert v.speech_start_offset() == 4 * BPS
