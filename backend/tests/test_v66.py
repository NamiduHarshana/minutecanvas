"""v6.6: fixes for the root causes found in the v6.5 run (debug json 20261029-071849-f7db2c08)."""
import array
import json
import math
import random

import pytest

from app.audio import AudioBuffer, SILENCE_RMS
from app.config import BYTES_PER_SEC, settings
from app.flow import Flow, replaced_things, with_next
from app.meta_api import MetaClient
from app.session import Session

BPS = BYTES_PER_SEC


def _lines(*texts, speakers=None, step_s=10.0):
    return [{"id": i, "turn": f"1-{i}", "speaker": (speakers[i] if speakers else "A"), "t": i * step_s, "text": t}
            for i, t in enumerate(texts)]


# ---------------- parts (one band per part of the product) ----------------
def test_new_part_starts_without_after_and_inherits_part():
    f = Flow()
    L = _lines("people sign up with their phone number", "then they pick a doctor and a time",
               "on the admin side the nurse sees the bookings for the day", "and she can cancel a booking")
    assert f.apply("add_step", {"label": "Sign up", "kind": "start", "part": "Booking", "quote": "sign up with their phone"}, L, 1).ok
    assert f.apply("add_step", {"label": "Pick doctor", "after": "s1", "quote": "they pick a doctor"}, L, 2).ok
    assert f.steps["s2"].part == "Booking"                                    # inherits the part it comes after
    r = f.apply("add_step", {"label": "See day bookings", "part": "Admin side", "quote": "the nurse sees the bookings"}, L, 3)
    assert r.ok and not f.links_into(r.message["id"])                          # first step of a new part: no arrow
    # a plain step without "after" in an existing part is still rejected (v6.1 rule)
    r = f.apply("add_step", {"label": "Cancel booking", "part": "Admin side", "quote": "she can cancel a booking"}, L, 4)
    assert not r.ok and "part" in r.message["error"]
    assert f.apply("add_step", {"label": "Cancel booking", "after": "s3", "quote": "she can cancel a booking"}, L, 4).ok
    assert f.steps["s4"].part == "Admin side"
    assert f.for_agent()["parts"] == ["Booking", "Admin side"] and f.for_agent()["unlinked_steps"] == []


def test_part_names_are_matched():
    f = Flow()
    L = _lines("people sign up with their phone number", "the nurse sees the bookings for the day")
    f.apply("add_step", {"label": "Sign up", "kind": "start", "part": "Admin side", "quote": "sign up with their phone"}, L, 1)
    f.apply("add_step", {"label": "See bookings", "part": "admin  side", "after": "s1", "quote": "nurse sees the bookings"}, L, 2)
    assert f.steps["s2"].part == "Admin side"
    r = f.apply("update_step", {"step": "s2", "part": "Nurse view", "quote": "nurse sees the bookings"}, L, 3)
    assert r.ok and f.steps["s2"].part == "Nurse view"


# ---------------- gaps ----------------
def test_gap_on_the_topic_still_being_discussed_waits():
    """v6.5: "Who reviews the Trello board?" was flagged 14 s after the step was last talked about."""
    f = Flow()
    L = _lines("first they fill in the intake form", "then the clinic calls them back", step_s=30)
    f.apply("add_step", {"label": "Fill intake form", "kind": "start", "quote": "they fill in the intake form"}, L, 1)
    f.mode, f.latest_t = "gap_check", 30.0
    args = {"step": "s1", "missing_branch": "who reviews", "question": "Who reviews the intake form?",
            "quote": "they fill in the intake form"}
    r = f.apply("flag_gap", args, L, 40)
    assert not r.ok and "in the last 90 s" in r.message["error"]
    f.latest_t = 200.0                                    # the group moved on long ago
    assert f.apply("flag_gap", args, L, 210).ok
    f2 = Flow()
    f2.apply("add_step", {"label": "Fill intake form", "kind": "start", "quote": "they fill in the intake form"}, L, 1)
    f2.mode, f2.latest_t = "gap_review", 30.0             # the user pressed "Check gaps": allowed
    assert f2.apply("flag_gap", args, L, 40).ok


def test_after_gap_answered_in_the_next_line():
    """v6.5: "…they can solve it and then" | "then their peers will critique their code" -> not a gap."""
    L = _lines("every morning you get one puzzle in the group", "puzzles and then they can solve it and then",
               "you know then their teammates will review their answer", "and that is the whole thing",
               speakers=["B", "B", "B", "A"])
    assert "teammates will review" in with_next(L, 1)
    f = Flow()
    f.apply("add_step", {"label": "Get daily puzzle", "kind": "start", "quote": "you get one puzzle in the group"}, L, 1)
    f.apply("add_step", {"label": "Solve the puzzle", "after": "s1", "quote": "every morning you get one puzzle"}, L, 1)
    f.mode, f.latest_t = "final", 40.0
    r = f.apply("flag_gap", {"step": "s2", "missing_branch": "after solving",
                             "question": "What happens after solving the puzzle?",
                             "quote": "every morning you get one puzzle in the group"}, L, 50)
    assert not r.ok and "teammates will review" in r.message["error"]


def test_continuation_stops_at_another_speaker_or_a_long_pause():
    L = _lines("they pay and then", "what about refunds though", "then they get a receipt",
               speakers=["A", "B", "A"])
    assert with_next(L, 0) == "they pay and then"                 # B's real sentence ends A's line
    L2 = [{"id": 0, "speaker": "A", "t": 0, "text": "they pay and then"},
          {"id": 1, "speaker": "B", "t": 2, "text": "mm-hmm"},
          {"id": 2, "speaker": "A", "t": 4, "text": "they get a receipt by sms"},
          {"id": 3, "speaker": "A", "t": 60, "text": "later something else"}]
    assert with_next(L2, 0) == "they pay and then they get a receipt by sms"   # "mm-hmm" doesn't break it


def _session():
    return Session(MetaClient(), [], "Test", "127.0.0.1")


async def test_gap_check_sees_lines_not_mapped_yet(monkeypatch):
    s = _session()
    s.transcript = [{"id": i, "turn": f"1-{i}", "speaker": "A", "t": i * 20.0, "t_end": i * 20.0 + 3,
                     "text": f"line number {i} about the intake form"} for i in range(6)]
    s.flow.apply("add_step", {"label": "Fill intake form", "kind": "start", "quote": "line number 0 about the intake form"},
                 s.transcript, 1)
    s.flow_done_idx = 4                                   # lines 4 and 5 arrived after the last live round
    seen = []

    async def fake_tools(messages, tools):
        seen.append(json.loads(messages[1]["content"]))
        return {"role": "assistant", "content": "done"}
    monkeypatch.setattr(s.meta, "spark_tools", fake_tools)
    await s._flow_round("gap_check")
    p = seen[0]
    assert [x.split("] ", 1)[1] for x in p["latest_lines_not_mapped_yet"]] == \
        ["Speaker A: line number 4 about the intake form", "Speaker A: line number 5 about the intake form"]
    assert s.flow_done_idx == 4                           # still mapped by the next live round
    assert s.flow.mode == "gap_check" and s.flow.latest_t == 100.0


# ---------------- a rejected step and the steps chained after it ----------------
async def test_chained_steps_after_a_rejected_step_get_a_clear_error(monkeypatch):
    s = _session()
    s.transcript = _lines("people open the clinic app", "they answer a few health questions",
                          "then the app suggests a doctor")
    replies = [{"role": "assistant", "content": None, "tool_calls": [
        {"id": "a", "type": "function", "function": {"name": "add_step", "arguments": json.dumps(
            {"label": "Open app", "kind": "start", "quote": "people open the clinic app"})}},
        {"id": "b", "type": "function", "function": {"name": "add_step", "arguments": json.dumps(
            {"label": "Answer questions", "after": "Open app", "quote": "this was never said"})}},
        {"id": "c", "type": "function", "function": {"name": "add_step", "arguments": json.dumps(
            {"label": "Doctor suggested", "after": "Answer questions", "quote": "then the app suggests a doctor"})}},
    ]}, {"role": "assistant", "content": "done"}]
    sent = []

    async def fake_tools(messages, tools):
        sent.append(messages)
        return replies[len(sent) - 1]
    monkeypatch.setattr(s.meta, "spark_tools", fake_tools)
    await s._flow_round("live")
    tool_msgs = [json.loads(m["content"]) for m in sent[1] if m["role"] == "tool"]
    assert tool_msgs[0]["ok"] and not tool_msgs[1]["ok"]
    assert "rejected above" in tool_msgs[2]["error"] and "Answer questions" in tool_msgs[2]["error"]


# ---------------- stale cards after a replacement ----------------
def test_replaced_things():
    assert replaced_things("instead of linking them to the shared drive because they can't sign in") == [{"shar", "driv"}]
    assert replaced_things("i like your idea, instead of an email we just send an sms") == [{"email"}]
    assert replaced_things("we use the email for everything") == []


def test_stale_card_hint_lists_cards_naming_the_old_thing():
    s = _session()
    ops = {"new_cards": [
        {"column": "decisions", "title": "Same price for all clinics", "detail": "Only the email template changes per clinic.",
         "owner": None, "due": None, "status": None, "said_at": "00:10", "image_prompt": "x"},
        {"column": "actions", "title": "Hire a second nurse", "detail": "For the evening shift.",
         "owner": None, "due": None, "status": None, "said_at": "00:20", "image_prompt": "x"}],
        "updates": [], "resolved_questions": [], "meeting_title": None}
    s.board.apply_spark(ops, 30)
    hints = s._stale_card_hints([{"id": 9, "speaker": "A", "t": 40, "text": "instead of an email we send an sms reminder"}])
    assert len(hints) == 1 and [c["title"] for c in hints[0]["cards_to_check"]] == ["Same price for all clinics"]


# ---------------- voice: catching up after a stall, and Stop ----------------
def _speechlike(seconds: float, floor_rms: int, seed: int = 1) -> bytes:
    """Talk in bursts with short pauses, on top of a steady noise floor (a meeting played through a mic + tab)."""
    rnd = random.Random(seed)
    out = array.array("h")
    t = 0.0
    while t < seconds:
        talk = rnd.uniform(1.0, 4.0)
        pause = rnd.uniform(0.6, 1.6)
        for k in range(int(talk * 24000)):
            env = 0.6 + 0.4 * math.sin(k / 900)
            out.append(int(max(-32000, min(32000, rnd.gauss(0, 3200 * env) + rnd.gauss(0, floor_rms)))))
        for _ in range(int(pause * 24000)):
            out.append(int(rnd.gauss(0, floor_rms)))
        t += talk + pause
    return out.tobytes()


def _catch_up(floor_rms: int, adaptive: bool) -> float:
    """Seconds of a 23 s backlog left after 3 minutes of real-time sending."""
    a = AudioBuffer(60)
    if not adaptive:
        a.pause_level = lambda: SILENCE_RMS           # the v6.5 rule: fixed RMS 350
    pcm = _speechlike(240, floor_rms)
    a.append_at(0, pcm[: 23 * BPS])                   # the stall left 23 s waiting
    fed = 23 * BPS
    for _ in range(1800):                             # 3 minutes of pacer ticks (0.1 s each)
        a.append_at(fed, pcm[fed:fed + BPS // 10])    # the browser keeps sending in real time
        fed += BPS // 10
        a.take(BPS // 10)
    return a.backlog_s


def test_backlog_catches_up_when_pauses_are_not_silent():
    """v6.5: 23 s behind for the rest of the meeting, 0.5 s skipped in 16 min (pauses never under RMS 350)."""
    old = _catch_up(700, adaptive=False)
    new = _catch_up(700, adaptive=True)
    assert old > 20                                   # the v6.5 rule doesn't catch up at all
    assert new < 2, new                               # pauses are trimmed (400 ms kept each) until caught up


def test_steady_noise_is_never_skipped():
    a = AudioBuffer(60)
    rnd = random.Random(3)
    noise = array.array("h", (int(rnd.gauss(0, 2000)) for _ in range(30 * 24000))).tobytes()
    a.append_at(0, noise)
    for _ in range(200):
        a.take(BPS // 10)
    assert a.skipped_bytes == 0                       # music / constant sound: no "pauses" in it


async def test_stop_sends_the_whole_backlog(monkeypatch):
    """v6.5: Stop waited 10 s at most; ~13 s of backlog were never sent (the last lines of the meeting were lost)."""
    s = _session()
    s.state = "recording"
    s.audio.append_at(0, b"\x10\x00" * (BPS // 2 * 14))        # 14 s waiting

    class _Voice:
        sent_bytes_total = 0

        async def stop(self, wait_s=0):
            pass
    s.voice = _Voice()

    async def drain():
        import asyncio
        while s.audio.buf:
            s.audio.take(BPS * 2)
            await asyncio.sleep(0.05)
    import asyncio
    task = asyncio.create_task(drain())

    async def no_spark(final=False):
        return None

    async def no_report():
        return {}
    monkeypatch.setattr(s, "spark_once", no_spark)
    monkeypatch.setattr(s, "_build_report", no_report)
    await s.stop()
    await task
    assert s.audio.backlog_s < 0.3


# ---------------- open gaps are re-checked as the meeting goes on ----------------
def _gap_session(answer: dict):
    s = _session()
    s.transcript = [{"id": i, "turn": f"1-{i}", "speaker": "A", "t": i * 60.0, "t_end": i * 60.0 + 3, "text": t}
                    for i, t in enumerate(["the nurse fills the intake form for the patient",
                                           "we talked about parking for a while now",
                                           "oh and the doctor checks the intake form before the visit"])]
    s.flow.apply("add_step", {"label": "Fill intake form", "kind": "start", "quote": "the nurse fills the intake form"},
                 s.transcript, 1)
    s.flow.mode = "final"
    r = s.flow.apply("flag_gap", {"step": "s1", "missing_branch": "who reviews", "question": "Who reviews the intake form?",
                                  "quote": "the nurse fills the intake form"}, s.transcript, 70)
    g = r.new_gap
    card, is_new = s.board.add_gap_question(g.id, g.question, "x", 70)
    g.card_id, g.card_owned = card.id, is_new
    calls = []

    async def fake(system, user, name, schema, history=None):
        assert name == "gap_status"
        calls.append(json.loads(user))
        return answer
    s.meta.spark_json = fake
    return s, g, card, calls


async def test_gap_answered_later_is_resolved_with_the_real_line():
    s, g, card, calls = _gap_session({"status": "answered_later", "line": "02:00", "answer": "The doctor checks it",
                                      "new_question": "", "why": "x"})
    ch = await s.recheck_gaps()
    assert g.resolved and g.resolved_by.line == 2 and card.resolved and card.answer_t == 120.0
    assert ch[0]["kind"] == "gap_resolved"
    assert "the doctor checks the intake form" in " ".join(calls[0]["lines_said_after_it_was_flagged"])
    assert await s.recheck_gaps() == []                    # no new lines since: not asked again


async def test_gap_answer_line_must_exist():
    s, g, card, _ = _gap_session({"status": "answered_later", "line": "07:30", "answer": "x", "new_question": "", "why": ""})
    await s.recheck_gaps()
    assert not g.resolved and not card.resolved           # Spark pointed at a line that isn't there: gap stays


async def test_false_gap_is_removed():
    s, g, card, _ = _gap_session({"status": "answered_before", "line": "00:00", "answer": "nurse", "new_question": "", "why": ""})
    ch = await s.recheck_gaps()
    assert g.dismissed and card.id not in s.board.cards and ch[0]["kind"] == "gap_removed"
    assert all(x["id"] != g.id for x in s.flow.public()["gaps"])


async def test_renamed_gap_is_updated():
    s, g, card, _ = _gap_session({"status": "reworded", "line": "", "answer": "",
                                  "new_question": "Who reviews the online intake form", "why": "", "line": "02:00"})
    await s.recheck_gaps()
    assert g.question == "Who reviews the online intake form?" and card.title == g.question and not g.resolved


async def test_false_gap_cannot_come_back_and_user_choices_stay():
    s, g, card, calls = _gap_session({"status": "answered_before", "line": "00:00", "answer": "", "new_question": "", "why": ""})
    await s.recheck_gaps()
    s.flow.mode, s.flow.gap_budget = "final", 2
    r = s.flow.apply("flag_gap", {"step": "s1", "missing_branch": "who reviews", "question": "Who reviews the intake form?",
                                  "quote": "the nurse fills the intake form"}, s.transcript, 200)
    assert not r.ok and "already answered at 00:00" in r.message["error"]
    s2, g2, card2, calls2 = _gap_session({"status": "answered_later", "line": "02:00", "answer": "x", "new_question": "", "why": ""})
    s2.flow.user_resolve_gap(g2, True, 80)
    s2.flow.user_resolve_gap(g2, False, 90)                   # the user reopened it
    await s2.recheck_gaps()
    assert not g2.resolved and not calls2                     # left alone, no Spark call


async def test_false_gap_keeps_a_question_card_it_did_not_make():
    s, g, card, _ = _gap_session({"status": "answered_before", "line": "00:00", "answer": "", "new_question": "", "why": ""})
    g.card_owned = False                                      # it was tied to a question someone had asked
    await s.recheck_gaps()
    assert g.dismissed and card.id in s.board.cards and card.flow_ref is None


def test_side_branch_line_is_not_what_comes_next():
    f = Flow()
    L = _lines("people submit the claim form online", "if they submit the claim form twice then we just ignore the duplicate one",
               "the weather was nice today", step_s=60)
    f.apply("add_step", {"label": "Submit claim form", "kind": "start", "quote": "people submit the claim form online"}, L, 1)
    f.mode, f.latest_t = "final", 120.0
    r = f.apply("flag_gap", {"step": "s1", "missing_branch": "after submit", "question": "What happens after the claim form is submitted?",
                             "quote": "people submit the claim form online"}, L, 130)
    assert r.ok, r.message


async def test_recent_gap_rejected_before_any_paid_check(monkeypatch):
    s = _session()
    s.transcript = _lines("they fill in the intake form", "then the clinic calls them back", step_s=20)
    s.flow.apply("add_step", {"label": "Fill intake form", "kind": "start", "quote": "they fill in the intake form"}, s.transcript, 1)
    s.flow.mode, s.flow.latest_t = "live", 20.0
    paid = []

    async def fake(*a, **k):
        paid.append(1)
        return {"yes": False, "why": "", "line": ""}
    monkeypatch.setattr(s.meta, "spark_json", fake)
    monkeypatch.setattr(settings, "verify_flow", True)

    class A:
        accepted = rejected = 0
        events, cards_added, cards_updated = [], [], []
    out = await s._apply_flow_call("flag_gap", {"step": "s1", "missing_branch": "who reviews", "question": "Who reviews the form?",
                                                "quote": "they fill in the intake form"}, s.transcript, A())
    assert not out["ok"] and "in the last 90 s" in out["error"] and not paid
