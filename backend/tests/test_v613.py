"""v6.13: fix traced in the v6.12 bench run (bench 20261101-225329)."""
from __future__ import annotations

from app.flow import Flow
from tests.test_v612 import _lines, _ok

L = _lines(
    "for coaching they do mock interviews with us experts",        # 0
    "for system design there are four sessions",                   # 1
    "the design track also has mock interviews at the end",        # 2
    "they can also book extra practice sessions",                  # 3
)


# leetcode final pass: flag_gap "after mock interviews" on Behavioral's "Do mock interviews" box matched (by branch
# only) g5 "…after the system design mock interviews?" on System design's own "Do mock interviews" box and moved it
# into the Behavioral part, still saying "system design". The behavioral gap itself was never made.
def _with_design_mock_gap() -> Flow:
    f = Flow()
    _ok(f.apply("add_step", {"label": "Do mock interviews", "part": "Coaching",
                             "quote": "they do mock interviews with us experts"}, L, 5))
    _ok(f.apply("add_step", {"label": "Do four sessions", "part": "System design",
                             "quote": "for system design there are four sessions"}, L, 10))
    _ok(f.apply("add_step", {"label": "Do mock interviews", "after": "s2", "part": "System design",
                             "quote": "the design track also has mock interviews at the end"}, L, 15))
    assert f.steps["s3"].part == "System design"
    f.round_new = set()
    f.mode, f.gap_budget = "final", 3
    _ok(f.apply("flag_gap", {"step": "s3", "missing_branch": "after mock interviews",
                             "question": "What happens after the system design mock interviews?",
                             "quote": "the design track also has mock interviews at the end"}, L, 25))
    _ok(f.apply("add_step", {"label": "Book practice sessions", "after": "s2", "part": "System design",
                             "quote": "they can also book extra practice sessions"}, L, 25))
    f.round_new = set()
    return f


def test_same_branch_on_another_parts_box_is_a_new_gap():
    f = _with_design_mock_gap()
    r = _ok(f.apply("flag_gap", {"step": "s1", "missing_branch": "after mock interviews",
                                 "question": "What happens after the behavioral mock interviews?",
                                 "quote": "they do mock interviews with us experts"}, L, 30))
    assert r.message["id"] == "g2" and "note" not in r.message
    assert f.gaps["g1"].step == "s3" and f.gaps["g2"].step == "s1"
    assert "system design" in f.gaps["g1"].question


def test_same_after_branch_on_another_box_of_the_same_part_is_a_new_gap():
    f = _with_design_mock_gap()
    r = _ok(f.apply("flag_gap", {"step": "s4", "missing_branch": "after mock interviews",
                                 "question": "What happens after booking practice sessions?",
                                 "quote": "they can also book extra practice sessions"}, L, 30))
    assert r.message["id"] == "g2" and f.gaps["g1"].step == "s3"


def test_similar_question_still_moves_and_same_box_still_repeats():
    f = _with_design_mock_gap()
    r = f.apply("flag_gap", {"step": "s3", "missing_branch": "after mock interviews",
                             "question": "What happens after the mock interviews?",
                             "quote": "the design track also has mock interviews at the end"}, L, 30)
    assert not r.ok and "already flagged as g1" in str(r.message)
    r = _ok(f.apply("flag_gap", {"step": "s4", "missing_branch": "design mocks",
                                 "question": "What happens after the system design mock interview?",
                                 "quote": "they can also book extra practice sessions"}, L, 30))
    assert r.message == {"ok": True, "id": "g1", "note": "moved to this step"} and f.gaps["g1"].step == "s4"


def test_same_failure_branch_in_the_same_part_still_moves():
    f = _with_design_mock_gap()
    _ok(f.apply("flag_gap", {"step": "s2", "missing_branch": "session cancelled",
                             "question": "What if a design session is cancelled?",
                             "quote": "for system design there are four sessions"}, L, 25))
    r = _ok(f.apply("flag_gap", {"step": "s4", "missing_branch": "session cancelled",
                                 "question": "What if the expert calls off a practice booking?",
                                 "quote": "they can also book extra practice sessions"}, L, 30))
    assert r.message["note"] == "moved to this step" and f.gaps["g2"].step == "s4"


def test_same_failure_branch_in_another_part_is_a_new_gap():
    f = _with_design_mock_gap()
    _ok(f.apply("flag_gap", {"step": "s2", "missing_branch": "session cancelled",
                             "question": "What if a design session is cancelled?",
                             "quote": "for system design there are four sessions"}, L, 25))
    r = _ok(f.apply("flag_gap", {"step": "s1", "missing_branch": "session cancelled",
                                 "question": "What if the coach calls off a mock interview?",
                                 "quote": "they do mock interviews with us experts"}, L, 30))
    assert r.message["id"] == "g3" and f.gaps["g2"].step == "s2"
