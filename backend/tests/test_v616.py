"""v6.16: fixes traced in the v6.15 real Vimeo run (debug 20261101-184815-137ac9af)."""
from __future__ import annotations

from app.flow import Flow, said_next
from tests.test_v612 import _lines, _ok

MAP = ("right and then once you have your stories then map your stories to the questions that this story maps to this "
       "question this story is good for this question okay and then finally what you do is you")
SUBMIT = "you finally get a you know submit that to us thats what i really want us to get"


# final pass 31:58 / 32:14 + gap_check 31:20: "after customers submit the story mapping?" on s43 refused 3 times as
# "not a gap … (…then finally what you do is you)". "finally what you do is you" is filler, not a next step.
def test_discourse_words_after_then_are_not_a_next_step():
    assert said_next(MAP, "Submit story mapping", ["submit that to us thats what i really want us to get"]) is None


def test_story_mapping_gap_not_refused():
    L = _lines(MAP, SUBMIT)
    f = Flow()
    _ok(f.apply("add_step", {"label": "Submit story mapping", "part": "Behavioral coaching",
                             "quote": "submit that to us thats what i really want us to get"}, L, 5))
    f.round_new = set()
    f.mode, f.gap_budget = "final", 3
    _ok(f.apply("flag_gap", {"step": "s1", "missing_branch": "after mapping submitted",
                             "question": "What happens after customers submit the story mapping?",
                             "quote": "submit that to us thats what i really want us to get"}, L, 30))


# final 31:20: Behavioral "after the behavioral mock interviews?" on s23 refused with System design's line
# "…mock interviews and then there's going to be experts in that pool" (evidence of a System design box).
def test_other_parts_line_is_not_what_comes_after():
    L = _lines("then after you have reviewed the sessions then you go ahead and do a mock interviews with our experts",  # 0
               "there are four questions and then you have follow-on practice sessions and mock interviews right",      # 1
               "and after that it is mostly practice and mock interviews right",                                       # 2
               "simply go ahead and schedule one on one sessions for mock interviews and then theres going to be "
               "experts in that pool as well and ill put a calendly link there",                                       # 3
               step=60.0)   # minutes apart, as in the run (no line joins the next one)
    f = Flow()
    _ok(f.apply("add_step", {"label": "Do mock interviews", "part": "Behavioral coaching",
                             "quote": "then you go ahead and do a mock interviews with our experts"}, L, 5))
    _ok(f.apply("add_step", {"label": "Do mock interviews", "part": "System design",
                             "quote": "then you have follow-on practice sessions and mock interviews right"}, L, 10))
    _ok(f.apply("add_step", {"label": "Schedule 1-on-1 sessions", "after": "s2", "part": "System design",
                             "quote": "schedule one on one sessions for mock interviews"}, L, 20))
    f.round_new = set()
    f.mode, f.gap_budget = "final", 3
    r = _ok(f.apply("flag_gap", {"step": "s1", "missing_branch": "after mock interviews",
                                 "question": "What happens after the behavioral mock interviews?",
                                 "quote": "then after you have reviewed the sessions then you go ahead"}, L, 30))
    assert f.gaps[r.message["id"]].step == "s1"


def test_same_part_line_still_answers():
    # a later line of the SAME part that says what comes after the step still blocks the gap
    L = _lines("they do mock interviews with our experts",
               "after the mock interviews and then we send them the written report")
    f = Flow()
    _ok(f.apply("add_step", {"label": "Do mock interviews", "part": "P",
                             "quote": "they do mock interviews with our experts"}, L, 5))
    f.round_new = set()
    f.mode, f.gap_budget = "final", 3
    r = f.apply("flag_gap", {"step": "s1", "missing_branch": "after mock interviews",
                             "question": "What happens after the mock interviews?",
                             "quote": "they do mock interviews with our experts"}, L, 30)
    assert not r.ok and "already says what comes after" in r.message["error"]
