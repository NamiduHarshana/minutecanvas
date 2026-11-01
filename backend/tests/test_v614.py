"""v6.14: fix traced in the v6.13 bench run (bench 20261101-232316)."""
from __future__ import annotations

from app.flow import Flow, said_next
from tests.test_v612 import _lines, _ok

DESIGN = ("yeah and i want to kind of tell them hey there are four questions that i do that gives you a good "
          "understanding of how you should go about system designing and then you have follow-on practice sessions "
          "and mock interviews right")
NETWORK = ("network yeah and then if they click on that then take them to that page where we have all the network "
           "thing the information about our network right now current network whatever")


# leetcode gap_check 26:13: "after the system mock interviews?" on s24 was refused as "not a gap … (…then you have
# follow on practice sessions and mock)" with hint "link s24 -> s23" (s23 -> s24 already drawn). The words after
# "then" were the step's OWN quote, not what comes after it. Same in the final pass for "See network info page".
def test_words_after_then_that_are_the_step_itself_are_not_next():
    assert said_next(DESIGN, "System mock interviews",
                     ["then you have follow-on practice sessions and mock interviews right"]) is None
    assert said_next(NETWORK, "See network info page",
                     ["if they click on that then take them to that page where we have all the network thing"]) is None


def test_real_next_still_found():
    # the step is the part BEFORE "then" in its own quote: the words after it are still what comes next
    assert said_next("they pay the deposit and then they get the receipt by sms", "Pay deposit",
                     ["they pay the deposit and then they get the receipt by sms"]) == "they get the receipt by sms"


def test_system_design_mock_gap_is_flagged_in_its_own_part():
    L = _lines("and then schedule a review with us this is the workflow essentially the five review sessions is "
               "what i would recommend and then after you have reviewed the sessions then you go ahead and do a "
               "mock interviews with us experts and the review sessions what we do is basically we review your "
               "answers give you feedback and then you get better at that",                # 0 (real line 138)
               "mock interviews yeah that should be at $200 including in the five sessions right",   # 1
               "there are four questions so that is four sessions",                       # 2
               DESIGN,                                                                    # 3 (real line 157)
               "something else entirely about pricing for later",                          # 4
               )
    f = Flow()
    _ok(f.apply("add_step", {"label": "Do mock interviews", "part": "Behavioral coaching",
                             "quote": "then you go ahead and do a mock interviews with us experts"}, L, 5))
    _ok(f.apply("add_step", {"label": "Do four sessions", "part": "System design preparation",
                             "quote": "four questions so that is four sessions"}, L, 10))
    _ok(f.apply("add_step", {"label": "Follow-on practice sessions", "after": "s2", "part": "System design preparation",
                             "quote": "then you have follow-on practice sessions and mock interviews right"}, L, 10))
    _ok(f.apply("add_step", {"label": "System mock interviews", "after": "s3", "part": "System design preparation",
                             "quote": "then you have follow-on practice sessions and mock interviews right"}, L, 10))
    f.round_new = set()
    f.mode, f.gap_budget = "final", 3
    _ok(f.apply("flag_gap", {"step": "s1", "missing_branch": "after mock interviews",
                             "question": "What happens after the mock interviews?",
                             "quote": "then you go ahead and do a mock interviews with us experts"}, L, 30))
    r = _ok(f.apply("flag_gap", {"step": "s4", "missing_branch": "after mock interviews",
                                 "question": "What happens after the system mock interviews?",
                                 "quote": "then you have follow-on practice sessions and mock interviews right"}, L, 30))
    assert r.message["id"] == "g2" and f.gaps["g2"].step == "s4" and f.gaps["g1"].step == "s1"


def test_hint_never_points_back_to_a_box_before_this_one():
    L = _lines("they review the answers", "then they do mock interviews", "after mock interviews then review answers")
    f = Flow()
    _ok(f.apply("add_step", {"label": "Review answers", "part": "P", "quote": "they review the answers"}, L, 5))
    _ok(f.apply("add_step", {"label": "Do mock interviews", "after": "s1", "part": "P",
                             "quote": "then they do mock interviews"}, L, 6))
    assert f._step_like("review answers", f.steps["s2"]) is None
