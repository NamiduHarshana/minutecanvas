"""v6.7: fixes traced in the v6.6 bench run (bench-20261101-155817)."""
from __future__ import annotations

import json

from app.meta_api import MetaClient
from app.session import Session


def _session():
    return Session(MetaClient(), [], "Test", "127.0.0.1")


def _lines(*texts, step=6.0, speaker="A"):
    return [{"id": i, "turn": f"1-{i}", "speaker": speaker, "t": i * step, "t_end": i * step + 3, "text": t}
            for i, t in enumerate(texts)]


def _call(cid, name, args):
    return {"id": cid, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


# ---------------- 1. the agent works in several turns: it must be allowed to finish ----------------
# v6.6 bench: in every round with calls the code stopped after the agent's first turn (all calls accepted -> break),
# e.g. grooming "Mapping … adding the staff login and bookings view" -> 1 call, then cut. The agent never said "done".
async def test_agent_keeps_going_until_done(monkeypatch):
    s = _session()
    s.transcript = _lines("so first the owner downloads the app and signs in with the phone number",
                          "they get a code by sms and they type it in",
                          "then the first time they add their pet like the name and the breed")
    replies = [
        {"role": "assistant", "content": "Mapping the sign-in.", "tool_calls": [_call("a", "add_step", {
            "label": "Sign in with phone", "kind": "start", "part": "App", "quote": "signs in with the phone number"})]},
        {"role": "assistant", "content": None, "tool_calls": [_call("b", "add_step", {
            "label": "Enter SMS code", "kind": "step", "after": "s1", "quote": "they get a code by sms"})]},
        {"role": "assistant", "content": None, "tool_calls": [_call("c", "add_step", {
            "label": "Add pet", "kind": "step", "after": "s2", "quote": "the first time they add their pet"})]},
        {"role": "assistant", "content": "done"},
    ]
    sent = []

    async def fake_tools(messages, tools):
        sent.append(len(messages))
        return replies[len(sent) - 1]
    monkeypatch.setattr(s.meta, "spark_tools", fake_tools)
    await s._flow_round("live")
    assert [x.label for x in s.flow.steps.values()] == ["Sign in with phone", "Enter SMS code", "Add pet"]
    assert len(s.flow.links) == 2
    assert len(sent) == 4                       # the agent got its results back each time and said "done" itself


async def test_agent_loop_has_a_turn_cap_and_stops_when_nothing_changes(monkeypatch):
    s = _session()
    s.transcript = _lines("people open the clinic app and pick a doctor")
    same = {"role": "assistant", "content": None, "tool_calls": [_call("a", "add_step", {
        "label": "Open app", "kind": "start", "quote": "people open the clinic app"})]}
    n = []

    async def fake_tools(messages, tools):
        n.append(1)
        return same                             # keeps sending the same call: reused, nothing new
    monkeypatch.setattr(s.meta, "spark_tools", fake_tools)
    await s._flow_round("live")
    assert len(s.flow.steps) == 1
    assert len(n) == 3                          # 2nd and 3rd turns changed nothing -> stop (v6.11: one repeat turn is allowed)


async def test_turn_cap(monkeypatch):
    from app.session import FLOW_MAX_TURNS
    s = _session()
    s.transcript = _lines(*[f"then they open screen number {i} of the app" for i in range(12)])
    n = []

    async def fake_tools(messages, tools):
        n.append(1)
        k = len(n)
        return {"role": "assistant", "content": None, "tool_calls": [_call(f"x{k}", "add_step", {
            "label": f"Screen {k}", "kind": "start" if k == 1 else "step", **({"after": f"s{k - 1}"} if k > 1 else {}),
            "quote": f"they open screen number {k} of the app"})]}
    monkeypatch.setattr(s.meta, "spark_tools", fake_tools)
    await s._flow_round("live")
    assert len(n) == FLOW_MAX_TURNS["live"]
    await s._flow_round("final")
    assert len(n) == FLOW_MAX_TURNS["live"] + FLOW_MAX_TURNS["final"]


async def test_json_fallback_also_continues(monkeypatch):
    s = _session()
    s.meta.tools_ok = False
    s.transcript = _lines("so first the owner downloads the app and signs in with the phone number",
                          "they get a code by sms and they type it in")
    replies = [{"calls": [{"name": "add_step", "arguments": json.dumps(
                   {"label": "Sign in", "kind": "start", "quote": "signs in with the phone number"})}]},
               {"calls": [{"name": "add_step", "arguments": json.dumps(
                   {"label": "Enter code", "kind": "step", "after": "s1", "quote": "they get a code by sms"})}]},
               {"calls": []}]
    n = []

    async def fake_json(system, user, name, schema, history=None):
        n.append(1)
        return replies[len(n) - 1]
    monkeypatch.setattr(s.meta, "spark_json", fake_json)
    await s._flow_round("live")
    assert [x.label for x in s.flow.steps.values()] == ["Sign in", "Enter code"]
    assert len(n) == 3


# ---------------- 2. live context: the last ~2.5 min without fillers, so the agent knows the section ----------------
def test_live_context_skips_fillers_and_reaches_the_section_name():
    from app.session import CONTEXT_MAX_LINES, is_filler, live_context_start
    texts = ["for the personal training I want something similar on the page", "Mm-hmm.",
             "the price there is eighty dollars per session"]
    texts += [x for k in range(8) for x in ("Oh, yeah.", f"it is more about form and goals number {k} you know")]
    texts += ["so I want to show them how to book this session here"]       # the new line
    lines = _lines(*texts, step=5.0)
    start = len(lines) - 1
    first = live_context_start(lines, start)
    ctx = [x["text"] for x in lines[first:start] if not is_filler(x)]
    assert ctx[0].startswith("for the personal training")                 # 6 plain lines would not reach it
    assert not any(is_filler({"text": t}) for t in ctx)
    # a long meeting: never more than CONTEXT_MAX_LINES, and nothing older than CONTEXT_S once 6 lines are in
    many = _lines(*[f"a long line about topic number {k} of the meeting" for k in range(200)], step=4.0)
    f2 = live_context_start(many, 150)
    assert 150 - f2 <= CONTEXT_MAX_LINES
    f3 = live_context_start(_lines(*[f"a long line about topic number {k}" for k in range(50)], step=60.0), 40)
    assert 40 - f3 == 6


async def test_live_round_sends_the_wider_context(monkeypatch):
    s = _session()
    s.transcript = _lines("for the personal training I want something similar on the page", "Uh-huh.",
                          "the price there is eighty dollars per session", "Oh, yeah.",
                          "it is more about form and goals you know", "Ah, I guess", "Mm-hmm.", "Right.",
                          "so I want to show them how to book this session here", step=8.0)
    s.flow_done_idx = 8
    seen = []

    async def fake_tools(messages, tools):
        seen.append(json.loads(messages[1]["content"]))
        return {"role": "assistant", "content": "done"}
    monkeypatch.setattr(s.meta, "spark_tools", fake_tools)
    await s._flow_round("live")
    ctx = seen[0]["earlier_lines_for_context_only"]
    assert len(ctx) == 3 and "personal training" in ctx[0]


def test_steps_carry_their_time_for_the_agent():
    from app.flow import Flow
    f = Flow()
    lines = _lines("people open the gym app on their phone", step=75.0)
    f.apply("add_step", {"label": "Open app", "kind": "start", "quote": "people open the gym app"}, lines, 1)
    assert f.for_agent()["steps"][0]["at"] == "00:00"


# ---------------- 3. smaller fixes ----------------
def test_presenter_talk_is_not_the_next_step():
    from app.flow import said_next
    text = "yeah the partner network at top companies. Then I want to show that we tell them it is active"
    assert said_next(text, "Join partner network") is None
    assert said_next("they pay the deposit and then they get the receipt by sms", "Pay deposit") == \
        "they get the receipt by sms"
    assert said_next("they pay the deposit and then we want them to rate the groomer", "Pay deposit")


def test_flag_gap_not_blocked_by_presenter_talk():
    from app.flow import Flow
    f = Flow()
    f.mode = "final"
    lines = _lines("just register a session then you join our partner network",
                   "yeah the partner network at top companies",
                   "Then I want to show that we tell them that the network is active", step=10.0)
    f.apply("add_step", {"label": "Register session", "kind": "start", "quote": "just register a session"}, lines, 1)
    f.apply("add_step", {"label": "Join partner network", "after": "s1", "quote": "then you join our partner network"},
            lines, 1)
    r = f.apply("flag_gap", {"step": "s2", "missing_branch": "after joining", "question": "What happens after they join?",
                             "quote": "then you join our partner network"}, lines, 30)
    assert r.ok, r.message


def test_replacement_hint_marks_open_questions_and_skips_answered_ones():
    s = _session()
    ops = {"new_cards": [
        {"column": "questions", "title": "Where should the gym rules live?", "detail": "Shared drive or the website.",
         "owner": None, "due": None, "status": None, "said_at": "00:10", "image_prompt": "x"},
        {"column": "questions", "title": "Who owns the shared drive?", "detail": "Shared drive access for trainers.",
         "owner": None, "due": None, "status": None, "said_at": "00:12", "image_prompt": "x"},
        {"column": "decisions", "title": "Rules on the shared drive", "detail": "Trainers read rules there.",
         "owner": None, "due": None, "status": None, "said_at": "00:14", "image_prompt": "x"}],
        "updates": [], "resolved_questions": [], "meeting_title": None}
    s.board.apply_spark(ops, 20)
    owner_q = next(c for c in s.board.cards.values() if c.title.startswith("Who owns"))
    owner_q.resolved = True
    hints = s._stale_card_hints(_lines("instead of the shared drive let's just put a page on the website"))
    cards = hints[0]["cards_to_check"]
    assert {c["title"] for c in cards} == {"Where should the gym rules live?", "Rules on the shared drive"}
    assert next(c for c in cards if c["title"].startswith("Where")).get("open_question") is True
    assert "open_question" not in next(c for c in cards if c["title"].startswith("Rules"))


def test_bench_scorer_ignores_answered_questions_for_stale_cards():
    from bench.bench import score
    board = {"cards": [
        {"column": "questions", "title": "How do people get the questions?", "detail": "Trello board needs sign in",
         "resolved": True},
        {"column": "questions", "title": "Where do questions live?", "detail": "Trello board maybe", "resolved": False}]}
    r = score({"steps": [], "links": [], "gaps": [], "contradictions": []}, board,
              {"stale": [{"old": ["trello"], "new": ["form", "page"]}]})
    assert r["stale_cards"] == ["questions: Where do questions live? | Trello board maybe"]


# ---------------- v6.7 bench: one call per turn, 5-turn cap cut 9 live rounds ----------------
async def test_cut_round_is_told_to_the_next_live_round(monkeypatch):
    from app.session import FLOW_MAX_TURNS
    s = _session()
    s.transcript = _lines(*[f"then they open screen number {i} of the app" for i in range(30)])
    s.transcript = s.transcript[:15]
    seen, n = [], []

    async def fake_tools(messages, tools):
        if len(messages) == 2:
            seen.append(json.loads(messages[1]["content"]))
        n.append(1)
        k = len(n)
        return {"role": "assistant", "content": None, "tool_calls": [_call(f"x{k}", "add_step", {
            "label": f"Screen {k}", "kind": "start" if k == 1 else "step", **({"after": f"s{k - 1}"} if k > 1 else {}),
            "quote": f"they open screen number {k} of the app"})]}
    monkeypatch.setattr(s.meta, "spark_tools", fake_tools)
    await s._flow_round("live")
    assert len(n) == FLOW_MAX_TURNS["live"] and s.flow_cut
    assert "previous_round_cut_off" not in seen[0]
    s.transcript += _lines(*[f"then they open screen number {i} of the app" for i in range(30)])[15:20]
    await s._flow_round("live")
    assert seen[1].get("previous_round_cut_off") is True


async def test_round_time_limit(monkeypatch):
    import app.session as session_mod
    monkeypatch.setitem(session_mod.FLOW_ROUND_MAX_S, "live", 0.0)
    s = _session()
    s.transcript = _lines("people open the clinic app", "then they pick a doctor from the list")
    n = []

    async def fake_tools(messages, tools):
        n.append(1)
        k = len(n)
        args = ({"label": "Open app", "kind": "start", "quote": "people open the clinic app"} if k == 1 else
                {"label": "Pick doctor", "after": "s1", "quote": "they pick a doctor from the list"})
        return {"role": "assistant", "content": None, "tool_calls": [_call(f"x{k}", "add_step", args)]}
    monkeypatch.setattr(s.meta, "spark_tools", fake_tools)
    await s._flow_round("live")
    assert len(n) == 1 and s.flow_cut                      # no 2nd turn once the time is up


def test_same_bug_from_the_same_line_is_not_pinned_twice():
    from app.flow import Flow
    f = Flow()
    lines = _lines("they type the code on the otp screen", "but there's a bug, the otp screen freezes on old phones",
                   "i just pushed the fix for the otp freeze", step=10.0)
    f.apply("add_step", {"label": "Enter OTP", "kind": "start", "quote": "they type the code on the otp screen"}, lines, 1)
    assert f.apply("report_bug", {"step": "s1", "text": "OTP screen freezes", "quote": "the otp screen freezes"}, lines, 12).ok
    f.apply("resolve", {"item_id": "b1", "quote": "i just pushed the fix for the otp freeze"}, lines, 25)
    r = f.apply("report_bug", {"step": "s1", "text": "OTP screen freezes on old phones",
                               "quote": "but there's a bug, the otp screen freezes on old phones"}, lines, 40)
    assert r.ok and r.message["id"] == "b1" and len(f.bugs) == 1
