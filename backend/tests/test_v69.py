"""v6.9: fixes traced in the v6.8 bench run (bench 20261101-174236)."""
from __future__ import annotations

import asyncio

from app.flow import Flow, dead_label
from app.meta_api import MetaClient
from app.prompts import FLOW_SYSTEM, VERIFY_CONFLICT_SYSTEM, VERIFY_FLOW_SYSTEM
from app.session import Session, _FlowRound
from bench.bench import score
from bench.meetings import LEETCODE_KEY, MEETINGS


def _lines(*texts, step=6.0, speaker="A"):
    return [{"id": i, "turn": f"1-{i}", "speaker": speaker, "t": i * step, "t_end": i * step + 3, "text": t}
            for i, t in enumerate(texts)]


MUSIC = _lines(
    "Right, so on the classes page they see the timetable for the term.",               # 0
    "Then they just come to class and the teacher marks attendance.",                    # 1
    "Right now we have a WhatsApp group for the parents for all the announcements.",     # 2
    "Yes parents see the homework when they log in, on their kid's page.",               # 3
    "Oh and actually, instead of the WhatsApp group, let's put the announcements in the parent portal.",  # 4
)


def _music_flow() -> Flow:
    """Rounds 1-2 as in the v6.8 bench: the WhatsApp step hangs after "Mark attendance" in Group classes."""
    f = Flow()
    ok = lambda r: r.ok or (_ for _ in ()).throw(AssertionError(r.message))  # noqa: E731
    ok(f.apply("add_step", {"label": "See timetable", "kind": "step", "part": "Group classes",
                            "quote": "on the classes page they see the timetable for the term"}, MUSIC, 10))
    ok(f.apply("add_step", {"label": "Mark attendance", "kind": "step", "after": "s1",
                            "quote": "the teacher marks attendance"}, MUSIC, 10))
    ok(f.apply("add_step", {"label": "Receive WhatsApp updates", "kind": "step", "after": "s2",
                            "quote": "we have a WhatsApp group for the parents for all the announcements"}, MUSIC, 20))
    ok(f.apply("add_step", {"label": "Parent log in", "kind": "step", "part": "Parent portal",
                            "quote": "parents see the homework when they log in"}, MUSIC, 25))
    f.round_new = set()                                   # what the session does when a new round starts
    return f


# ---------------- 1. replaced step: merge with its new version, never a "removed" box ----------------
# music_school: the agent added "See announcements" (s23), the rename of s10 to it was refused as a duplicate label,
# so it renamed s10 "WhatsApp group removed" (a dead box with an arrow from "Mark attendance").
def test_rename_onto_new_version_merges():
    f = _music_flow()
    r = f.apply("add_step", {"label": "See announcements", "kind": "step", "after": "s4", "part": "Parent portal",
                             "quote": "let's put the announcements in the parent portal"}, MUSIC, 30)
    assert r.ok and r.message["id"] == "s5"
    r = f.apply("update_step", {"step": "s3", "label": "See announcements", "part": "Parent portal",
                                "quote": "instead of the WhatsApp group, let's put the announcements in the parent portal"},
                MUSIC, 30)
    assert r.ok and r.message["merged"] == "s5" and r.message["id"] == "s3"
    assert "s5" not in f.steps
    s3 = f.steps["s3"]
    assert (s3.label, s3.part) == ("See announcements", "Parent portal")
    pairs = {(k.src, k.dst) for k in f.links.values()}
    assert ("s4", "s3") in pairs                       # the new version's arrow moved onto the kept box
    assert ("s2", "s3") not in pairs                   # the old place in Group classes is gone
    assert s3.t == MUSIC[4]["t"]                       # bands go by time: Parent portal keeps its place
    assert list(f.steps) == ["s1", "s2", "s3", "s4"]   # numbers on screen (order added) did not change
    assert f.unlinked() == []                          # "Parent log in" is still the first step of its part


def test_rename_onto_step_from_earlier_round_is_still_refused():
    f = _music_flow()
    assert f.apply("add_step", {"label": "See announcements", "kind": "step", "after": "s4",
                                "quote": "let's put the announcements in the parent portal"}, MUSIC, 30).ok
    f.round_new = set()                                # s5 is on screen now: merging it away would renumber nothing
    r = f.apply("update_step", {"step": "s3", "label": "See announcements",
                                "quote": "instead of the WhatsApp group, let's put the announcements in the parent portal"},
                MUSIC, 40)                             # ... but it is a box people saw: no silent delete
    assert not r.ok and "already has that label" in r.message["error"]


def test_rename_onto_new_step_without_replace_line_is_refused():
    f = _music_flow()
    assert f.apply("add_step", {"label": "See announcements", "kind": "step", "after": "s4",
                                "quote": "let's put the announcements in the parent portal"}, MUSIC, 30).ok
    r = f.apply("update_step", {"step": "s3", "label": "See announcements",
                                "quote": "Yes parents see the homework when they log in"}, MUSIC, 30)
    assert not r.ok and "s3" in f.steps and "s5" in f.steps


def test_merge_in_same_part_keeps_arrows():
    lines = _lines("they pay and then we email them the receipt",
                   "and then they get a reminder the day before",
                   "instead of email let's send the receipt on WhatsApp")
    f = Flow()
    assert f.apply("add_step", {"label": "Pay", "kind": "step", "part": "App", "quote": "they pay and then we email"}, lines, 5).ok
    assert f.apply("add_step", {"label": "Email receipt", "kind": "step", "after": "s1",
                                "quote": "we email them the receipt"}, lines, 5).ok
    assert f.apply("add_step", {"label": "Get reminder", "kind": "step", "after": "s2",
                                "quote": "they get a reminder the day before"}, lines, 10).ok
    f.round_new = set()
    assert f.apply("add_step", {"label": "WhatsApp receipt", "kind": "step", "after": "s1",
                                "quote": "let's send the receipt on WhatsApp"}, lines, 15).ok
    r = f.apply("update_step", {"step": "s2", "label": "WhatsApp receipt",
                                "quote": "instead of email let's send the receipt on WhatsApp"}, lines, 15)
    assert r.ok and r.message.get("merged") == "s4"
    pairs = {(k.src, k.dst) for k in f.links.values()}
    assert pairs == {("s1", "s2"), ("s2", "s3")}       # one arrow Pay -> receipt (merged), receipt -> reminder kept
    assert f.steps["s2"].label == "WhatsApp receipt" and f.steps["s2"].t == lines[0]["t"]


def test_merge_keeps_start_and_end_kinds():
    lines = _lines("first they sign up with their email address", "at the end we email them the receipt",
                   "instead of email let's sign up with the phone number",
                   "and instead of email we send the receipt on WhatsApp")
    f = Flow()
    assert f.apply("add_step", {"label": "Sign up with email", "kind": "start", "part": "App",
                                "quote": "they sign up with their email address"}, lines, 5).ok
    assert f.apply("add_step", {"label": "Email receipt", "kind": "end", "after": "s1",
                                "quote": "we email them the receipt"}, lines, 8).ok
    f.round_new = set()
    assert f.apply("add_step", {"label": "Sign up with phone", "kind": "start", "part": "App",
                                "quote": "sign up with the phone number"}, lines, 15).ok      # demoted to "step"
    assert f.apply("update_step", {"step": "s1", "label": "Sign up with phone",
                                   "quote": "instead of email let's sign up with the phone number"}, lines, 15).message["merged"] == "s3"
    assert f.apply("add_step", {"label": "WhatsApp receipt", "kind": "step", "after": "s1",
                                "quote": "we send the receipt on WhatsApp"}, lines, 20).ok
    assert f.apply("update_step", {"step": "s2", "label": "WhatsApp receipt",
                                   "quote": "instead of email we send the receipt on WhatsApp"}, lines, 20).message["merged"] == "s4"
    assert f.steps["s1"].kind == "start" and f.steps["s2"].kind == "end"
    assert {(k.src, k.dst) for k in f.links.values()} == {("s1", "s2")}


def test_merge_moving_part_bridges_old_chain_and_moves_gaps():
    lines = _lines("the teacher marks attendance", "then we post it in the WhatsApp group",
                   "then the teacher sends the homework", "parents log in to the parent portal",
                   "instead of the WhatsApp group we post it in the parent portal")
    f = Flow()
    assert f.apply("add_step", {"label": "Mark attendance", "kind": "step", "part": "Classes",
                                "quote": "the teacher marks attendance"}, lines, 5).ok
    assert f.apply("add_step", {"label": "Post in WhatsApp", "kind": "step", "after": "s1",
                                "quote": "we post it in the WhatsApp group"}, lines, 8).ok
    assert f.apply("add_step", {"label": "Send homework", "kind": "step", "after": "s2",
                                "quote": "the teacher sends the homework"}, lines, 12).ok
    assert f.apply("add_step", {"label": "Parent log in", "kind": "step", "part": "Parent portal",
                                "quote": "parents log in to the parent portal"}, lines, 20).ok
    f.round_new = set()
    assert f.apply("add_step", {"label": "Portal post", "kind": "step", "after": "s4",
                                "quote": "we post it in the parent portal"}, lines, 30).ok
    f.mode, f.latest_t = "final", None
    assert f.apply("flag_gap", {"step": "s5", "missing_branch": "nobody reads it", "question": "Who reads the post?",
                                "quote": "we post it in the parent portal"}, lines, 30).ok
    shown = f.public(shown=True)                     # mid-round: the new box is not on screen yet
    assert "s5" not in {x["id"] for x in shown["steps"]} and not shown["gaps"]
    r = f.apply("update_step", {"step": "s2", "label": "Portal post",
                                "quote": "instead of the WhatsApp group we post it in the parent portal"}, lines, 30)
    assert r.message["merged"] == "s5"
    pairs = {(k.src, k.dst) for k in f.links.values()}
    assert pairs == {("s1", "s3"), ("s4", "s2")}     # Mark attendance -> Send homework; Parent log in -> Portal post
    assert f.unlinked() == [] and f.part_first("Classes") == "s1" and f.part_first("Parent portal") == "s4"
    assert next(iter(f.gaps.values())).step == "s2"


def test_dead_labels_rejected():
    assert dead_label("WhatsApp group removed") and dead_label("Email no longer used") and dead_label("Old form replaced")
    for ok in ("Cancel booking free", "Remove item from cart", "Drop off the dog", "Return instrument", "Replace card",
               "Refund unused sessions", "Retry dropped payment", "Restore deleted account", "Show removed items",
               "Check obsolete stock", "Collect retired instruments"):
        assert not dead_label(ok), ok
    f = _music_flow()
    r = f.apply("update_step", {"step": "s3", "label": "WhatsApp group removed",
                                "quote": "instead of the WhatsApp group, let's put the announcements in the parent portal"},
                MUSIC, 30)
    assert not r.ok and "rename the old step" in r.message["error"]
    r = f.apply("add_step", {"label": "WhatsApp dropped", "kind": "step", "after": "s2",
                             "quote": "instead of the WhatsApp group"}, MUSIC, 30)
    assert not r.ok


def test_session_drops_event_of_merged_step():
    s = Session(MetaClient(), [], "Test", "127.0.0.1")
    s.transcript = list(MUSIC)
    s.flow = _music_flow()
    acc = _FlowRound()

    async def go():
        await s._apply_flow_call("add_step", {"label": "See announcements", "kind": "step", "after": "s4",
                                              "quote": "let's put the announcements in the parent portal"}, MUSIC, acc)
        acc.events.append({"kind": "gap", "id": "g9", "step": "s5", "text": "test"})   # an event naming the step
        return await s._apply_flow_call("update_step", {"step": "s3", "label": "See announcements",
                                                        "quote": "instead of the WhatsApp group, let's put the announcements in the parent portal"},
                                        MUSIC, acc)
    msg = asyncio.run(go())
    assert msg["merged"] == "s5"
    assert not any(e.get("id") == "s5" for e in acc.events)
    assert all(e.get("step") != "s5" for e in acc.events)
    assert any(e.get("id") == "g9" and e.get("step") == "s3" for e in acc.events)
    assert any(e["kind"] == "step_updated" and e["id"] == "s3" for e in acc.events)


# ---------------- 2. prompts: usual case of an exception rule; made-up example numbers ----------------
# grooming: "more than fifteen minutes late -> move them" got the gap "what if they arrive on time or less than 15
# minutes late?" (that is the normal flow). leetcode: "they might have 15 stories and answer like 50 questions" vs
# "if there are 15 stories and there are 40 questions" (two made-up examples) was flagged as 50 vs 40.
def test_prompts_cover_usual_case_and_examples():
    assert "usual case of a rule that only names an exception" in FLOW_SYSTEM
    assert "usual case of a rule that names an exception" in VERIFY_FLOW_SYSTEM
    assert "made-up example" in VERIFY_CONFLICT_SYSTEM and "made-up example" in FLOW_SYSTEM
    assert "Rename the old step" in FLOW_SYSTEM
    for p in (FLOW_SYSTEM, VERIFY_FLOW_SYSTEM, VERIFY_CONFLICT_SYSTEM):      # no bench examples in prompts
        low = p.lower()
        for w in ("groom", "fifteen minutes", "15 stories", "whatsapp group", "trello"):
            assert w not in low, w


# ---------------- 3. answer key: matchers that hit the wrong step ----------------
def _flow(steps, links):
    return {"steps": [{"id": i, "label": l, "part": p} for i, l, p in steps],
            "links": [{"src": a, "dst": b} for a, b in links], "gaps": [], "contradictions": []}


def test_key_groomer_notes_not_owner_steps():
    f = _flow([("s21", "See today's bookings", "Groomer side"), ("s22", "View pet service notes", "Groomer side")],
              [("s21", "s22")])
    assert score(f, {"cards": []}, MEETINGS["grooming_app"]["key"])["wrong_cross_part_links"] == []


def test_key_rental_email_and_parent_login():
    f = _flow([("s12", "Check stock availability", "Instrument rental"), ("s13", "Receive availability email", "Instrument rental"),
               ("s14", "Pick up instrument", "Instrument rental"), ("s21", "Parent log in", "Parent portal")],
              [("s12", "s13"), ("s13", "s14")])
    r = score(f, {"cards": []}, MEETINGS["music_school"]["key"])
    assert r["wrong_cross_part_links"] == []
    assert "teach.login" in r["steps"]["missing"] and "trial.email" in r["steps"]["missing"]


def test_key_see_behavioral_questions():
    f = _flow([("s38", "See behavioral questions", "Behavioral coaching"), ("s19", "Write question answers", "Behavioral coaching"),
               ("s20", "Create 10-15 stories", "Behavioral coaching")], [("s38", "s19"), ("s19", "s20")])
    r = score(f, {"cards": []}, LEETCODE_KEY)
    assert "behav.questions->behav.stories" not in r["links"]["missing"]
