"""Flow Map: quote check + tool semantics (no network)."""
from app.board import Board
from app.flow import Flow, check_quote

LINES = [
    {"id": 0, "speaker": "A", "t": 10.0, "text": "Customers must log in with an OTP before checkout."},
    {"id": 1, "speaker": "B", "t": 20.0, "text": "The OTP expires in five minutes."},
    {"id": 2, "speaker": "A", "t": 30.0, "text": "Let's keep it simple, the code is valid for ten minutes."},
    {"id": 3, "speaker": "B", "t": 40.0, "text": "Then they go to checkout and pay by card."},
]


def test_quote_exact_prefix_case_punct():
    ev, _ = check_quote("[00:20] Kasun: the OTP EXPIRES in five minutes!", LINES)
    assert ev and ev.line == 1 and ev.speaker == "B" and ev.t == 20.0
    ev, _ = check_quote("“log in with an OTP”", LINES)
    assert ev and ev.line == 0


def test_quote_rejects_invented_short_and_long():
    assert check_quote("the payment uses PayHere gateway", LINES)[0] is None
    assert "too short" in check_quote("OTP", LINES)[1]
    assert "too long" in check_quote("word " * 60, LINES)[1]
    assert check_quote(None, LINES)[0] is None


def test_quote_fuzzy_one_word_off_uses_real_line():
    ev, _ = check_quote("they go to the checkout and pay by card", LINES)
    assert ev and ev.line == 3 and ev.quote == LINES[3]["text"]    # shows the real words, not the model's


def test_chain_by_label_in_one_batch_and_reuse():
    f = Flow()
    assert f.apply("add_step", {"label": "OTP login", "kind": "start", "quote": "log in with an OTP"}, LINES, 50).ok
    r = f.apply("add_step", {"label": "Checkout", "kind": "decision", "after": "OTP login", "link_label": "verified",
                             "quote": "go to checkout and pay by card"}, LINES, 50)
    assert r.ok and [e["kind"] for e in r.events] == ["step_added", "link_added"]
    r = f.apply("add_step", {"label": "otp login", "kind": "step", "quote": "must log in with an OTP"}, LINES, 50)
    assert r.message["note"].startswith("step already existed") and len(f.steps) == 2
    assert not f.apply("link_steps", {"from_step": "s1", "to_step": "s1", "quote": "log in with an OTP"}, LINES, 50).ok
    assert not f.apply("nope", {}, LINES, 50).ok and not f.apply("add_step", "x", LINES, 50).ok


def test_gap_dedupe_and_resolve():
    f = Flow()
    f.apply("add_step", {"label": "Checkout", "kind": "decision", "quote": "go to checkout and pay by card"}, LINES, 50)
    r = f.apply("flag_gap", {"step": "s1", "missing_branch": "payment fails", "question": "What if payment fails",
                             "quote": "by card"}, LINES, 50)
    assert not r.ok                                               # quote too short
    r = f.apply("flag_gap", {"step": "s1", "missing_branch": "payment fails", "question": "What if payment fails",
                             "quote": "go to checkout and pay by card"}, LINES, 50)
    assert r.ok and r.new_gap.question == "What if payment fails?"
    assert "already flagged" in f.apply("flag_gap", {"step": "s1", "missing_branch": "payment fails",
                                                     "question": "Payment failure?", "quote": "go to checkout and pay by card"},
                                        LINES, 50).message["error"]
    r = f.apply("resolve", {"item_id": "g1", "answer": "retry", "quote": "the OTP expires in five minutes"}, LINES, 60)
    assert r.ok and r.resolved_gap and f.gaps["g1"].resolved_by.line == 1


def test_contradiction_needs_two_lines_and_keeps_order():
    f = Flow()
    assert not f.apply("flag_contradiction", {"quote_a": "the OTP expires in five", "quote_b": "OTP expires in five minutes",
                                              "note": "x"}, LINES, 50).ok
    r = f.apply("flag_contradiction", {"quote_a": "the code is valid for ten minutes", "quote_b": "OTP expires in five minutes",
                                       "note": "OTP expiry 5 or 10 min"}, LINES, 50)
    c = f.contradictions[r.message["id"]]
    assert r.ok and c.a.line == 1 and c.b.line == 2
    assert not f.apply("flag_contradiction", {"quote_a": "OTP expires in five minutes", "quote_b": "the code is valid for ten minutes",
                                              "note": "again"}, LINES, 50).ok


def test_bug_pin_and_fix():
    f = Flow()
    f.apply("add_step", {"label": "Checkout", "quote": "go to checkout and pay by card"}, LINES, 50)
    r = f.apply("report_bug", {"step": "Checkout", "text": "Card payment crashes", "quote": "then they go to checkout"}, LINES, 50)
    assert r.ok and f.for_agent()["open_bugs"][0]["id"] == "b1"
    assert f.apply("resolve", {"item_id": "b1", "quote": "the code is valid for ten minutes"}, LINES, 60).ok
    assert f.for_agent()["open_bugs"] == []


def test_gap_question_card_reuses_open_question():
    b = Board()
    b.apply_spark({"new_cards": [{"column": "questions", "title": "What if the payment fails?", "detail": "", "owner": None,
                                  "due": None, "status": None, "said_at": "00:10", "image_prompt": ""}]}, 20)
    card, is_new = b.add_gap_question("g1", "What if the payment fails?", "gap", 30)
    assert not is_new and card.flow_ref == "g1" and len(b.cards) == 1
    card, is_new = b.add_gap_question("g2", "What happens on timeout?", "gap", 30)
    assert is_new and card.column == "questions" and card.flow_ref == "g2"
    assert b.resolve_gap_question(card.id, "retry", 40).resolved


def test_one_start_gap_budget_move_and_unlinked():
    f = Flow()
    f.apply("add_step", {"label": "OTP login", "kind": "start", "quote": "log in with an OTP"}, LINES, 50)
    r = f.apply("add_step", {"label": "Checkout", "kind": "start", "quote": "go to checkout and pay by card"}, LINES, 50)
    assert f.steps["s2"].kind == "step" and "already a start" in r.message["warning"]
    assert f.for_agent()["unlinked_steps"] == ["s2"]                       # agent is told to connect it
    f.gap_budget = 1
    assert f.apply("flag_gap", {"step": "s1", "missing_branch": "payment fails", "question": "What if payment fails?",
                                "quote": "log in with an OTP before checkout"}, LINES, 50).ok
    assert "limit" in f.apply("flag_gap", {"step": "s1", "missing_branch": "wrong code", "question": "What if the OTP is wrong?",
                                          "quote": "log in with an OTP before checkout"}, LINES, 50).message["error"]
    r = f.apply("flag_gap", {"step": "s2", "missing_branch": "payment fails", "question": "What if payment fails?",
                             "quote": "go to checkout and pay by card"}, LINES, 50)
    assert r.ok and r.message["note"] == "moved to this step" and f.gaps["g1"].step == "s2" and len(f.gaps) == 1
    f.apply("link_steps", {"from_step": "s1", "to_step": "s2", "quote": "log in with an OTP before checkout"}, LINES, 50)
    assert f.for_agent()["unlinked_steps"] == []


def test_open_ends_hints_for_gap_review():
    f = Flow()
    f.apply("add_step", {"label": "OTP login", "kind": "start", "quote": "log in with an OTP"}, LINES, 50)
    f.apply("add_step", {"label": "Checkout", "kind": "decision", "after": "OTP login", "quote": "go to checkout and pay by card"},
            LINES, 50)
    ends = f.for_agent()["open_ends"]
    co = f.find_step("Checkout").id
    assert ends["decisions_with_one_outcome"] == [co]      # only reached, never left
    assert ends["flow_stops_here"] == [co]
    f.gap_budget = 1
    assert f.apply("flag_gap", {"step": co, "missing_branch": "payment fails", "question": "What happens if the payment fails?",
                                "quote": "go to checkout and pay by card"}, LINES, 60).ok
    ends = f.for_agent()["open_ends"]
    assert ends == {"decisions_with_one_outcome": [], "flow_stops_here": []}   # already flagged -> no hint
