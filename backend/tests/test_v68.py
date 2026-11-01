"""v6.8: fixes traced in the v6.7.2 bench run (bench-20261101-170706)."""
from __future__ import annotations

import json

from app.board import Board, Card, numbers
from app.config import settings
from app.flow import Flow, number_facts, number_changes, spoken_numbers, with_usual_names
from app.meta_api import MetaClient
from app.session import Session


def _session():
    return Session(MetaClient(), [], "Test", "127.0.0.1")


def _lines(*texts, step=6.0, speaker="A"):
    return [{"id": i, "turn": f"1-{i}", "speaker": speaker, "t": i * step, "t_end": i * step + 3, "text": t}
            for i, t in enumerate(texts)]


# ---------------- 1. spoken compound numbers ----------------
# grooming: "cancel for free up to twenty four hours" was read as 20 and 4, so the card "Free cancellation up to
# 24 hours" was never tied to the "24 vs 12 hours" contradiction.
def test_spoken_compound_numbers():
    assert numbers("up to twenty four hours before") == {"24"}
    assert numbers("fourteen hundred") == {"1400"}
    assert numbers("two hundred and fifty dollars") == {"250"}
    assert numbers("a thousand bucks") == {"1000"}
    assert numbers("two thousand five hundred") == {"2500"}
    assert numbers("rate them from one to five stars") == {"1", "5"}
    assert numbers("$1,000 for five sessions") == {"1000", "5"}
    assert numbers("ten twelve") == {"10", "12"}               # two numbers, not one


def test_lone_one_still_skipped_in_number_facts():
    assert number_facts("a one-on-one session") == []
    assert number_facts("one hundred questions")[0][0] == 100
    assert number_facts("up to twenty four hours")[0][0] == 24
    assert [v for _, _, v in spoken_numbers("no one came".split(), lone_one=False)] == []


def test_number_change_hint_uses_whole_number():
    a = {"text": "they can cancel for free up to twenty four hours before"}
    b = {"text": "cancellation is free up to twelve hours before"}
    assert number_changes(a, b) == "hour"
    fa = number_facts(a["text"])
    assert [v for v, _ in fa] == [24.0]


def test_card_tagged_for_spoken_number_conflict():
    lines = _lines("Oh and they can cancel for free up to twenty four hours before.",
                   "Yes a reminder SMS the day before.",
                   "And on the booking screen write that cancellation is free up to twelve hours before.")
    f = Flow()
    r = f.apply("flag_contradiction", {"quote_a": "they can cancel for free up to twenty four hours before",
                                       "quote_b": "cancellation is free up to twelve hours before",
                                       "note": "Free cancellation window: 24 vs 12 hours"}, lines, 20)
    assert r.ok
    b = Board()
    b.cards["x"] = Card(id="x", column="decisions", title="Free cancellation up to 24 hours",
                        detail="Customers can cancel for free up to 24 hours before.", t=0.0)
    b.order.append("x")
    changed = b.link_conflicts(list(f.contradictions.values()))
    assert [c.id for c in changed] == ["x"] and b.cards["x"].conflict_ref == "c1"


# ---------------- 2. a contradiction is not settled by one of its own lines ----------------
# grooming: the agent "resolved" 24 vs 12 hours by quoting the 12-hours line itself (the AI picking the newer value).
def _conflict_flow():
    lines = _lines("the delivery fee is four dollars for every order",
                   "okay",
                   "and the delivery fee is three dollars on the order page",
                   "okay, three dollars",
                   "so let's go with three dollars, not four, for the delivery fee")
    f = Flow()
    assert f.apply("flag_contradiction", {"quote_a": "the delivery fee is four dollars for every order",
                                          "quote_b": "the delivery fee is three dollars on the order page",
                                          "note": "Delivery fee $4 vs $3"}, lines, 20).ok
    return f, lines


def test_resolve_with_own_line_rejected():
    f, lines = _conflict_flow()
    r = f.apply("resolve", {"item_id": "c1", "answer": "$3",
                            "quote": "the delivery fee is three dollars on the order page"}, lines, 30)
    assert not r.ok and "LATER line" in r.message["error"]
    r = f.apply("resolve", {"item_id": "c1", "answer": "$4",
                            "quote": "the delivery fee is four dollars for every order"}, lines, 30)
    assert not r.ok
    assert not f.contradictions["c1"].resolved


def test_resolve_with_later_line_accepted():
    f, lines = _conflict_flow()
    r = f.apply("resolve", {"item_id": "c1", "answer": "$3",
                            "quote": "let's go with three dollars, not four, for the delivery fee"}, lines, 30)
    assert r.ok and f.contradictions["c1"].resolved


async def test_settles_second_opinion(monkeypatch):
    s = _session()
    f, lines = _conflict_flow()
    s.flow, s.transcript = f, lines
    monkeypatch.setattr(settings, "verify_flow", True)
    seen = []

    async def verify(payload):
        seen.append(payload)
        return {"yes": False, "why": "only repeats the value", "line": ""}
    monkeypatch.setattr(s, "_verify_flow", verify)
    why = await s._check_flow_call("resolve", {"item_id": "c1", "quote": "okay, three dollars", "answer": "$3"}, lines)
    assert why and "does not settle" in why
    assert seen[0]["check"] == "settles" and "four dollars" in seen[0]["statement_a"]
    # a quote from one of the two lines: no paid call, the code rejects it
    seen.clear()
    why = await s._check_flow_call("resolve", {"item_id": "c1", "quote": "the delivery fee is three dollars on the order page"}, lines)
    assert why is None and seen == []


# ---------------- 3. one "what if the payment fails" gap per meeting ----------------
# grooming final pass: "deposit payment fails" then "remaining balance payment fails" (one decision for the team);
# the drop-off gap "owner doesn't show up" never got its turn.
def _pay_flow():
    lines = _lines("they pay a deposit by card when they book the slot",
                   "then they pay the rest at the counter or in the app",
                   "and on the day they come to the salon and check in",
                   "how do people pay for the class pass anyway")
    f = Flow()
    f.mode, f.gap_budget = "final", 5
    f.apply("add_step", {"label": "Pay deposit", "kind": "start", "part": "App", "quote": "they pay a deposit by card"}, lines, 30)
    f.apply("add_step", {"label": "Pay remaining balance", "kind": "step", "after": "s1",
                         "quote": "they pay the rest at the counter"}, lines, 30)
    f.apply("add_step", {"label": "Check in", "kind": "step", "after": "s2", "quote": "they come to the salon and check in"}, lines, 30)
    return f, lines


def test_second_payment_failure_gap_rejected():
    f, lines = _pay_flow()
    r = f.apply("flag_gap", {"step": "s1", "missing_branch": "payment fails", "question": "What if the deposit payment fails?",
                             "quote": "they pay a deposit by card"}, lines, 30)
    assert r.ok
    a = {"step": "s2", "missing_branch": "balance payment fails",
         "question": "What happens if the remaining balance payment fails?", "quote": "they pay the rest at the counter"}
    assert "payment-failure gap" in (f.gap_precheck(a, lines) or "")      # free check: before any paid second opinion
    assert not f.apply("flag_gap", a, lines, 30).ok
    r = f.apply("flag_gap", {"step": "s3", "missing_branch": "no-show", "question": "What happens if the owner doesn't show up?",
                             "quote": "they come to the salon and check in"}, lines, 30)
    assert r.ok
    # "how do they pay" is another kind of gap: still allowed next to a payment-failure gap
    r = f.apply("flag_gap", {"step": "s2", "missing_branch": "payment method", "question": "How do people pay for the class pass?",
                             "quote": "how do people pay for the class pass"}, lines, 30)
    assert r.ok


def test_payment_failure_gap_allowed_again_after_the_first_is_answered():
    f, lines = _pay_flow()
    assert f.apply("flag_gap", {"step": "s1", "missing_branch": "payment fails", "question": "What if the deposit payment fails?",
                                "quote": "they pay a deposit by card"}, lines, 30).ok
    f.gaps["g1"].resolved = True
    assert f.apply("flag_gap", {"step": "s2", "missing_branch": "card declined", "question": "What if the card is declined at the counter?",
                                "quote": "they pay the rest at the counter"}, lines, 30).ok


# ---------------- 4. stale-card hints with a misheard name ----------------
# leetcode: "instead of a tarot board it might be better to just put a form there" ("tarot" = "Trello"). Cards were
# matched on "board" only, so "…only Trello questions and experts change" and "…display the Trello … workflow" were
# never listed and stayed on the old plan at the end of the meeting.
TRELLO = ["on this I have a Trello board and a list of questions",
          "they go in and they fill up this Trello board Trello board I have a Trello board with questions",
          "first go fill up a Trello board come up with 10 to 15 stories",
          "the trello board is going to change for each role",
          "i think i like your idea that instead of a tarot board it might be better to just put a form there"]


def test_usual_name_added_for_misheard_word():
    earlier = _lines(*TRELLO[:4])
    assert with_usual_names({"tarot", "board"}, earlier) == {"tarot", "board", "trello"}
    assert with_usual_names({"trello", "board"}, earlier) == {"trello", "board"}       # nothing misheard: unchanged
    assert with_usual_names({"tarot", "board"}, []) == {"tarot", "board"}


def test_stale_hint_lists_cards_named_by_usual_name():
    s = _session()
    s.transcript = _lines(*TRELLO)
    for cid, col, title, detail in [
            ("a", "decisions", "Keep same pricing across roles", "Only Trello questions and experts change."),
            ("b", "decisions", "Show review workflow", "Behavioral section displays the Trello and review-session workflow."),
            ("c", "actions", "Send sitemap by Friday", "Sitemap of the whole site.")]:
        s.board.cards[cid] = Card(id=cid, column=col, title=title, detail=detail)
        s.board.order.append(cid)
    hints = s._stale_card_hints([s.transcript[-1]])
    assert hints and "trello" in hints[0]["old_thing_words"]
    assert {c["id"] for c in hints[0]["cards_to_check"]} == {"a", "b"}


# ---------------- 5. bench scorer ----------------
def test_scorer_either_part_and_untagged_conflict_side():
    from bench.bench import score
    key = {"steps": {"app.checkin": [["check"]], "app|staff.done": [["done"]], "staff.today": [["today"]]},
           "links": [["app.checkin", "app|staff.done"]]}
    flow = {"steps": [{"id": "s1", "label": "Check in", "part": "App"},
                      {"id": "s2", "label": "Mark done", "part": "Staff"},
                      {"id": "s3", "label": "Today bookings", "part": "Staff"}],
            "links": [{"src": "s1", "dst": "s2"}], "gaps": [],
            "contradictions": [{"note": "fee 4 vs 3", "resolved": False,
                                "a": {"quote": "the fee is four dollars", "t": 10.0},
                                "b": {"quote": "the fee is three dollars", "t": 50.0}}]}
    board = {"cards": [{"column": "decisions", "title": "Delivery fee $4", "detail": "", "t": 12.0}]}
    r = score(flow, board, key)
    assert r["parts"]["purity"] == 1.0 and not r["wrong_cross_part_links"]
    assert r["links"]["found"] == 1
    assert r["conflicts"]["shown_as_decided"] == ["Delivery fee $4 (side of: fee 4 vs 3)"]
    board["cards"][0]["conflict_ref"] = "c1"
    assert score(flow, board, key)["conflicts"]["shown_as_decided"] == []


def test_prompts_have_no_bench_examples():
    """The bench meetings must not leak into the prompts (a good score must not be the prompt's own examples)."""
    from app import prompts
    text = json.dumps([getattr(prompts, n) for n in dir(prompts) if n.isupper()]).lower()
    for w in ("groom", "trello", "tarot", "calendly", "leetcode", "music school", "twelve hours", "payee", "otp freeze"):
        assert w not in text, w
