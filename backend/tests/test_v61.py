"""v6.1: "what happens after" gaps the transcript already answers, unlinked steps, cards that repeat a step,
cards applied only after the Flow round of the same tick."""
import asyncio

from app.board import Board, restates_step
from app.flow import Flow, is_after_gap, said_next
from app.meta_api import MetaClient
from app.session import Session

LINES = [
    {"id": 0, "speaker": "A", "t": 1180.0, "text": "where they answer questions and then they self self-evaluate themselves right"},
    {"id": 1, "speaker": "A", "t": 1500.0, "text": "copy and paste the answer into the form and submit it"},
    {"id": 2, "speaker": "A", "t": 1513.0,
     "text": "someone's going to look at your answers and provide feedback and then like schedule a session with us"},
    {"id": 3, "speaker": "B", "t": 1520.0, "text": "for the session they click the Calendly link and pick a time"},
    {"id": 4, "speaker": "A", "t": 1600.0, "text": "they pay for the session right away when they book it"},
]


def _flow() -> Flow:
    f = Flow()
    assert f.apply("add_step", {"label": "Submit Answers Form", "kind": "start",
                                "quote": "copy and paste the answer into the form"}, LINES, 1600).ok
    assert f.apply("add_step", {"label": "Receive Feedback", "kind": "step", "after": "s1",
                                "quote": "look at your answers and provide feedback"}, LINES, 1600).ok
    assert f.apply("add_step", {"label": "Schedule Session", "kind": "step", "after": "s1",
                                "quote": "they click the Calendly link and pick a time"}, LINES, 1600).ok
    f.gap_budget = 2
    return f


def test_said_next_helpers():
    assert said_next(LINES[2]["text"], "Receive Feedback") == "like schedule a session with us"
    assert said_next("They submit the story map, then get feedback.", "Receive Feedback") is None   # "then" = the step
    assert said_next("They submit then we review and give feedback", "Receive Feedback") is None   # before the step
    assert is_after_gap("after feedback", "What happens after users receive feedback?")
    assert not is_after_gap("payment fails", "What happens if the payment fails?")
    assert not is_after_gap("no reply", "What happens if they don't reply after the call?")


def test_after_gap_rejected_when_the_line_says_then_and_points_to_the_step():
    f = _flow()
    r = f.apply("flag_gap", {"step": "s2", "missing_branch": "after feedback",
                             "question": "What happens after users receive feedback?",
                             "quote": "provide feedback and then like schedule a session"}, LINES, 1600)
    assert not r.ok and "not a gap" in r.message["error"] and "link_steps from s2 to s3" in r.message["error"]
    assert not f.gaps
    # a failure branch on the same step is still a gap
    assert f.apply("flag_gap", {"step": "s3", "missing_branch": "no slot free", "question": "What if no slot is free?",
                                "quote": "they click the Calendly link and pick a time"}, LINES, 1600).ok


def test_after_gap_still_allowed_when_nothing_says_what_comes_next():
    f = _flow()
    assert f.apply("flag_gap", {"step": "s3", "missing_branch": "after booking",
                                "question": "What happens after they book the session?",
                                "quote": "they click the Calendly link and pick a time"}, LINES, 1600).ok


def test_new_step_without_after_is_rejected_but_first_and_second_start_are_kept():
    f = Flow()
    assert f.apply("add_step", {"label": "Submit Answers Form", "kind": "step",
                                "quote": "copy and paste the answer into the form"}, LINES, 1600).ok      # first step
    r = f.apply("add_step", {"label": "Select Session Type", "kind": "step",
                             "quote": "they click the Calendly link and pick a time"}, LINES, 1600)
    assert not r.ok and "after" in r.message["error"] and len(f.steps) == 1
    r = f.apply("add_step", {"label": "Pay For Session", "kind": "step", "after": "s9",
                             "quote": "they pay for the session right away"}, LINES, 1600)
    assert not r.ok and "not found" in r.message["error"]
    # reusing an existing step needs no link
    assert f.apply("add_step", {"label": "submit answers form", "kind": "step",
                                "quote": "copy and paste the answer into the form"}, LINES, 1600).ok
    # a separate process (second start) is kept as a step and shows up as unlinked for the agent
    f.apply("add_step", {"label": "Book Coaching", "kind": "start", "quote": "they click the Calendly link"}, LINES, 1600)
    r = f.apply("add_step", {"label": "Book Coaching 2", "kind": "start", "quote": "they pay for the session right away"},
                LINES, 1600)
    assert r.ok and "already a start" in r.message["warning"]


def test_restates_step_uses_time_for_goals_and_decisions():
    steps = [{"label": "Tap Placement Network", "times": [987.0]},
             {"label": "Self-Evaluate Answers", "times": [1180.0]},
             {"label": "Receive Exact Time", "times": [1865.0]}]
    assert restates_step("Access Placement Network Via Session Registration", steps, 986, "decisions")
    assert restates_step("Track self-evaluation with expert gap review", steps, 1179, "goals")
    assert restates_step("Confirm exact time after Calendly request", steps, 1865, "decisions")
    # said far from the step's line: a real decision about the same thing
    assert not restates_step("Frame network as job placement", steps, 919, "decisions")
    # numbers are facts; actions are tasks (only the strict rule)
    assert not restates_step("Sell placement network access for $100", steps, 986, "decisions")
    assert not restates_step("Build the placement network page", steps, 986, "actions")
    # old strict rule still works with plain labels
    assert restates_step("Submit the story map", ["Submit story map"])


async def test_cards_wait_for_the_flow_round_of_the_same_tick(monkeypatch):
    s = Session(MetaClient(), [], "Test", "127.0.0.1")
    s.transcript = [dict(x, turn=str(x["id"])) for x in LINES]

    async def fake_cards(system, user, name, schema, history=None):
        return {"new_cards": [{"column": "decisions", "title": "Users receive feedback on answers", "detail": "x",
                               "owner": None, "due": None, "status": None, "said_at": "25:13", "image_prompt": "x"},
                              {"column": "decisions", "title": "Single session costs $250", "detail": "x",
                               "owner": None, "due": None, "status": None, "said_at": "26:40", "image_prompt": "x"}],
                "updates": [], "resolved_questions": [], "meeting_title": None}

    async def slow_flow(mode):
        await asyncio.sleep(0.2)        # the flow round finishes after the cards call
        s.flow.apply("add_step", {"label": "Receive Feedback", "kind": "start",
                                  "quote": "look at your answers and provide feedback"}, s.transcript, 1600)
    monkeypatch.setattr(s.meta, "spark_json", fake_cards)
    monkeypatch.setattr(s, "_flow_round", slow_flow)
    monkeypatch.setattr(s, "_queue_image", lambda c: None)
    await s.spark_once()
    assert [c.title for c in s.board.cards.values()] == ["Single session costs $250"]


def test_board_apply_spark_passes_time():
    b = Board()
    ch = b.apply_spark({"new_cards": [{"column": "decisions", "title": "Show network info page on tap", "detail": "",
                                       "owner": None, "due": None, "status": None, "said_at": "31:38", "image_prompt": "x"}]},
                       1950, [{"label": "View Network Info", "times": [1898.0]}])
    assert ch.added == []


# ---------------- v6.2: number checks and code hints ----------------
from app.flow import number_facts  # noqa: E402

N = [
    {"id": 0, "speaker": "A", "t": 477.0, "text": "that's price point there is $200 per session"},
    {"id": 1, "speaker": "A", "t": 756.0, "text": "so five review sessions is what I would recommend"},
    {"id": 2, "speaker": "A", "t": 809.0, "text": "if five sessions you are taking then i'd say we make it at $1,000"},
    {"id": 3, "speaker": "A", "t": 827.0, "text": "i think we'll make seven sessions i think make it 1400"},
    {"id": 4, "speaker": "A", "t": 1307.0, "text": "and then schedule sessions for four of those stories"},
    {"id": 5, "speaker": "A", "t": 1310.0, "text": "the OTP expires in five minutes"},
    {"id": 6, "speaker": "B", "t": 1320.0, "text": "the code is valid for ten minutes"},
    {"id": 7, "speaker": "A", "t": 1330.0, "text": "yeah four questions so that is four sessions"},
]


def test_number_facts_units():
    assert number_facts(N[0]["text"]) == [(200.0, {"money", "per:ses"})]
    assert number_facts(N[1]["text"]) == [(5.0, {"review", "ses"})]
    assert number_facts(N[3]["text"]) == [(7.0, {"ses"}), (1400.0, {"money"})]
    assert number_facts(N[4]["text"]) == [(4.0, {"stori"})]
    assert number_facts("it's a one-on-one session") == []          # "one" is not counted


def test_contradiction_needs_numbers_about_the_same_thing():
    f = Flow()
    r = f.apply("flag_contradiction", {"quote_a": "five review sessions is what I would recommend",
                                       "quote_b": "schedule sessions for four of those stories",
                                       "note": "Review sessions five vs four stories"}, N, 1400)
    assert not r.ok and "different things" in r.message["error"]
    assert f.apply("flag_contradiction", {"quote_a": "five sessions you are taking then i'd say we make it at $1,000",
                                          "quote_b": "we'll make seven sessions i think make it 1400",
                                          "note": "Package: 5 for $1000 or 7 for $1400?"}, N, 1400).ok
    assert f.apply("flag_contradiction", {"quote_a": "the OTP expires in five minutes", "quote_b": "the code is valid for ten minutes",
                                          "note": "OTP expiry"}, N, 1400).ok
    assert f.apply("flag_contradiction", {"quote_a": "that's price point there is $200 per session",   # rate vs count: same thing
                                          "quote_b": "five sessions you are taking then i'd say we make it at $1,000",
                                          "note": "Session price"}, N, 1400).ok


def test_hints_number_change_once_and_only_with_prices():
    f = Flow()
    h = f.hints([N[3]], N[:3])
    assert [(a["id"], b["id"], u) for a, b, u in h["number_changes"]] == [(2, 3, "ses")]
    again = f.hints([N[3]], N[:3])["number_changes"]                     # the same pair is never suggested twice
    assert all((a["id"], b["id"]) != (2, 3) for a, b, _ in again)
    assert Flow().hints([N[7]], N[:3])["number_changes"] == []        # "four sessions" without a price: not suggested
    assert [u for *_, u in Flow().hints([N[6]], N[:6])["number_changes"]] == ["minut"]


def test_hint_gap_answer_and_said_next_ignores_the_step_itself():
    lines = LINES + [{"id": 5, "speaker": "A", "t": 1883.0,
                      "text": "network yeah and then if they click on that then take them to that page with our network"}]
    f = Flow()
    f.apply("add_step", {"label": "Tap placement network", "kind": "start",
                         "quote": "copy and paste the answer into the form"}, lines, 1900)
    f.gap_budget = 1
    assert f.apply("flag_gap", {"step": "s1", "missing_branch": "after tapping", "question": "What happens after users tap in?",
                                "quote": "copy and paste the answer into the form"}, lines, 1900).ok
    h = f.hints([lines[5]], lines[:5])
    assert [(g.id, x["id"]) for g, x in h["gap_answers"]] == [("g1", 5)]
    text = ("the Calendly link will just allow them to express their time frames and then the next one they can and then "
            "then we'll get back to them with the right exact time frame")
    assert said_next(text, "Confirm exact time", ["we'll get back to them with the right exact time frame"]) is None


async def test_flow_round_sends_code_hints(monkeypatch):
    s = Session(MetaClient(), [], "Test", "127.0.0.1")
    s.transcript = [dict(x, turn=str(x["id"])) for x in N[:4]]
    s.flow_done_idx = 3
    seen = {}

    async def fake_tools(messages, tools):
        seen["payload"] = __import__("json").loads(messages[1]["content"])
        return {"role": "assistant", "content": "done"}
    monkeypatch.setattr(s.meta, "spark_tools", fake_tools)
    s.meta.tools_ok = True
    await s._flow_round("live")
    nc = seen["payload"]["code_hints"]["number_changes"]
    assert len(nc) == 1 and "$1,000" in nc[0]["older"] and "1400" in nc[0]["newer"] and nc[0]["counts"] == "ses"


def test_conflict_tag_uses_the_card_title_numbers():
    b, f = Board(), Flow()
    for title, detail in (("Price 7-session package at $1400", "Seven sessions for $1400; five sessions for $1000 was said earlier."),
                          ("Price single session at $250", "Single session $250; $200 was said earlier."),
                          ("Behavioral coaching price", "$200 per session.")):
        b.apply_spark({"new_cards": [{"column": "decisions", "title": title, "detail": detail, "owner": None, "due": None,
                                      "status": None, "said_at": "13:29", "image_prompt": "x"}]}, 900)
    lines = [{"id": 0, "speaker": "A", "t": 809.0, "text": "five sessions for $1,000 and one-on-one session we do 250"},
             {"id": 1, "speaker": "A", "t": 477.0, "text": "that's price point there is $200 per session"}]
    assert f.apply("flag_contradiction", {"quote_a": "that's price point there is $200 per session",
                                          "quote_b": "five sessions for $1,000 and one-on-one session we do 250",
                                          "note": "Session price $200 vs $250"}, lines, 900).ok
    b.link_conflicts(list(f.contradictions.values()))
    tagged = {c.title for c in b.cards.values() if c.conflict_ref}
    assert tagged == {"Price single session at $250"}      # not the 7-session package; the 3rd card is 7 min away


# ---------------- v6.4: second-opinion check on contradictions ----------------
from app.config import settings  # noqa: E402

P = [
    {"id": 0, "speaker": "A", "t": 880.0, "text": "for behavioral coaching the same model again"},
    {"id": 1, "speaker": "A", "t": 890.0, "text": "uh and 250 per session if they don't want to go one by one"},
    {"id": 2, "speaker": "A", "t": 1760.0, "text": "for system design I want to sell the videos"},
    {"id": 3, "speaker": "A", "t": 1773.0, "text": "and we can do a one-on-one session for 200"},
]
ARGS = {"quote_a": "250 per session if they don't want to go one by one", "quote_b": "we can do a one-on-one session for 200",
        "note": "Single-session price: 250 vs 200"}


def _sess(monkeypatch, answer):
    s = Session(MetaClient(), [], "Test", "127.0.0.1")
    seen = {}

    async def fake_json(system, user, name, schema, history=None):
        seen["name"], seen["payload"] = name, __import__("json").loads(user)
        if isinstance(answer, Exception):
            raise answer
        return answer
    monkeypatch.setattr(s.meta, "spark_json", fake_json)
    monkeypatch.setattr(settings, "verify_conflicts", True)
    return s, seen


async def test_second_opinion_blocks_different_things(monkeypatch):
    from app.session import _FlowRound
    s, seen = _sess(monkeypatch, {"same_fact": False, "a_is_about": "behavioral coaching", "b_is_about": "system design"})
    out = await s._apply_flow_call("flag_contradiction", ARGS, P, _FlowRound())
    assert not out["ok"] and "system design" in out["error"] and not s.flow.contradictions
    assert seen["name"] == "verify_conflict"
    assert any("behavioral coaching" in x for x in seen["payload"]["statement_a_with_lines_before"])   # context lines go along


async def test_second_opinion_keeps_real_conflicts_and_fails_open(monkeypatch):
    from app.session import _FlowRound
    s, _ = _sess(monkeypatch, {"same_fact": True, "a_is_about": "price", "b_is_about": "price"})
    assert (await s._apply_flow_call("flag_contradiction", ARGS, P, _FlowRound()))["ok"]
    s, _ = _sess(monkeypatch, RuntimeError("timeout"))
    assert (await s._apply_flow_call("flag_contradiction", ARGS, P, _FlowRound()))["ok"]     # check failed: keep it


async def test_second_opinion_off(monkeypatch):
    from app.session import _FlowRound
    s, seen = _sess(monkeypatch, {"same_fact": False, "a_is_about": "x", "b_is_about": "y"})
    monkeypatch.setattr(settings, "verify_conflicts", False)
    assert (await s._apply_flow_call("flag_contradiction", ARGS, P, _FlowRound()))["ok"] and not seen


def test_said_next_needs_the_step_named_right_before_then():
    text = ("then you go ahead and do a mock interview with us experts and the review sessions what we do is "
            "basically we review your answers give you feedback and then you get better at that")
    assert said_next(text, "Conduct Mock Interviews") is None          # "then you get better" is about the review


def test_one_open_payment_gap_and_reworded_duplicates():
    from app.flow import is_pay_gap
    assert is_pay_gap("payment method", "How do customers pay the $100?")
    assert not is_pay_gap("payment fails", "What happens if the payment fails?")
    f = _flow()
    f.gap_budget = 5
    assert f.apply("flag_gap", {"step": "s1", "missing_branch": "payment method", "question": "How do customers pay the $100?",
                                "quote": "copy and paste the answer into the form"}, LINES, 1600).ok
    r = f.apply("flag_gap", {"step": "s3", "missing_branch": "pay session fee", "question": "How do customers pay the $200 session fee?",
                             "quote": "they click the Calendly link and pick a time"}, LINES, 1600)
    assert not r.ok and "already an open payment gap" in r.message["error"]
    assert f.apply("flag_gap", {"step": "s3", "missing_branch": "payment fails", "question": "What if the payment fails?",
                                "quote": "they click the Calendly link and pick a time"}, LINES, 1600).ok      # other branch
    assert f.apply("flag_gap", {"step": "s3", "missing_branch": "after booking", "question": "What happens after the bookings?",
                                "quote": "they click the Calendly link and pick a time"}, LINES, 1600).ok
    r = f.apply("flag_gap", {"step": "s3", "missing_branch": "end", "question": "What happens after the booking?",
                             "quote": "they click the Calendly link and pick a time"}, LINES, 1600)
    assert not r.ok and "already flagged" in r.message["error"]
