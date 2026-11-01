"""v6.11: fixes traced in the v6.10 bench run (bench 20261101-211918)."""
from __future__ import annotations

import json

from app.flow import Flow
from app.meta_api import MetaClient
from app.session import Session
from bench.bench import score
from bench.meetings import MEETINGS


def _lines(*texts, step=6.0, speaker="A"):
    return [{"id": i, "turn": f"1-{i}", "speaker": speaker, "t": i * step, "t_end": i * step + 3, "text": t}
            for i, t in enumerate(texts)]


def _ok(r):
    assert r.ok, r.message
    return r


def _pairs(f: Flow) -> set[tuple[str, str]]:
    return {(k.src, k.dst) for k in f.links.values()}


GROOM = _lines(
    "So first the owner downloads the app and signs in with their phone number.",   # 0
    "They get a code by SMS and they type it in.",                                    # 1
    "What if the slot they want is already taken?",                                   # 2
    "Then we just show the next free slots for that groomer.",                        # 3
    "And when they pick the slot they pay a deposit by card.",                        # 4
)


# ---------------- 1. a box put between two steps leaves no second path ----------------
# grooming: "Enter phone number" was added after "Download app" and linked to "Enter SMS code"; the old arrow
# "Download app -> Enter SMS code" stayed, so the map showed two paths for one (leetcode had 4 more).
def test_inserted_box_removes_the_old_direct_arrow():
    f = Flow()
    _ok(f.apply("add_step", {"label": "Download app", "kind": "start", "part": "App",
                             "quote": "the owner downloads the app and signs in"}, GROOM, 5))
    _ok(f.apply("add_step", {"label": "Enter SMS code", "after": "s1", "quote": "They get a code by SMS"}, GROOM, 10))
    _ok(f.apply("add_step", {"label": "Enter phone number", "after": "s1",
                             "quote": "signs in with their phone number"}, GROOM, 12))
    r = _ok(f.apply("link_steps", {"from_step": "s3", "to_step": "s2", "quote": "They get a code by SMS"}, GROOM, 12))
    assert _pairs(f) == {("s1", "s3"), ("s3", "s2")}
    assert "s1 -> s2" in r.message["note"]


def test_decision_branch_that_rejoins_is_kept():
    # "Check slot" -> (taken) "Show other slots" -> "Pay deposit", and "Check slot" -> "Pay deposit": two real paths
    f = Flow()
    _ok(f.apply("add_step", {"label": "Pick slot", "kind": "start", "part": "App",
                             "quote": "What if the slot they want is already taken?"}, GROOM, 5))
    _ok(f.apply("add_step", {"label": "Check slot", "kind": "decision", "after": "s1",
                             "quote": "What if the slot they want is already taken?"}, GROOM, 5))
    _ok(f.apply("add_step", {"label": "Pay deposit", "after": "s2", "quote": "they pay a deposit by card"}, GROOM, 25))
    _ok(f.apply("add_step", {"label": "Show other slots", "after": "s2", "quote": "show the next free slots",
                             "link_label": "taken", "link_type": "fail"}, GROOM, 25))
    _ok(f.apply("link_steps", {"from_step": "s4", "to_step": "s3", "quote": "they pay a deposit by card"}, GROOM, 25))
    assert ("s2", "s3") in _pairs(f) and ("s2", "s4") in _pairs(f) and ("s4", "s3") in _pairs(f)
    # and a plain step's labelled side path ("skip") is kept too
    f2 = Flow()
    _ok(f2.apply("add_step", {"label": "Download app", "kind": "start", "part": "App",
                              "quote": "the owner downloads the app"}, GROOM, 5))
    _ok(f2.apply("add_step", {"label": "Enter SMS code", "after": "s1", "quote": "They get a code by SMS"}, GROOM, 10))
    _ok(f2.apply("add_step", {"label": "Enter phone number", "after": "s1", "link_label": "new user",
                              "quote": "signs in with their phone number"}, GROOM, 12))
    _ok(f2.apply("link_steps", {"from_step": "s3", "to_step": "s2", "quote": "They get a code by SMS"}, GROOM, 12))
    assert ("s1", "s2") in _pairs(f2)


def test_old_box_linked_later_is_not_an_insertion():
    # both boxes are from earlier rounds: an arrow between them is the agent's own choice, nothing is removed
    f = Flow()
    _ok(f.apply("add_step", {"label": "Download app", "kind": "start", "part": "App",
                             "quote": "the owner downloads the app"}, GROOM, 5))
    _ok(f.apply("add_step", {"label": "Enter SMS code", "after": "s1", "quote": "They get a code by SMS"}, GROOM, 10))
    _ok(f.apply("add_step", {"label": "Enter phone number", "after": "s1",
                             "quote": "signs in with their phone number"}, GROOM, 12))
    f.round_new = set()                        # next round
    _ok(f.apply("link_steps", {"from_step": "s3", "to_step": "s2", "quote": "They get a code by SMS"}, GROOM, 40))
    assert ("s1", "s2") in _pairs(f)


# ---------------- 2. one repeat turn doesn't end the round ----------------
# release_sync: the final pass began by re-reporting the already-pinned OTP bug (ok, nothing changed); the loop
# stopped right there and the gap sweep never ran ("transfer fails at the bank" missed).
def _call(cid, name, args):
    return {"id": cid, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


async def test_final_pass_goes_on_after_a_repeat_turn(monkeypatch):
    s = Session(MetaClient(), [], "Test", "127.0.0.1")
    s.transcript = _lines("people open the clinic app and pick a doctor")
    _ok(s.flow.apply("add_step", {"label": "Open app", "kind": "start", "part": "App",
                                  "quote": "people open the clinic app"}, s.transcript, 5))
    s.flow.round_new = set()
    replies = [
        {"role": "assistant", "content": "Final sweep.", "tool_calls": [_call("a", "add_step", {
            "label": "Open app", "kind": "start", "quote": "people open the clinic app"})]},      # repeat: no change
        {"role": "assistant", "content": None, "tool_calls": [_call("b", "add_step", {
            "label": "Pick doctor", "after": "s1", "quote": "pick a doctor"})]},
        {"role": "assistant", "content": "done"},
    ]
    n = []

    async def fake_tools(messages, tools):
        n.append(1)
        return replies[len(n) - 1]
    monkeypatch.setattr(s.meta, "spark_tools", fake_tools)
    await s._flow_round("final")
    assert [x.label for x in s.flow.steps.values()] == ["Open app", "Pick doctor"]
    assert len(n) == 3


# ---------------- 3. bench key ----------------
def test_key_no_free_slot_for_late_arrival_is_not_the_answered_late_gap():
    key = MEETINGS["grooming_app"]["key"]
    gaps = [{"id": "g1", "step": "s25", "branch": "no free slot",
             "question": "What happens if there is no free slot for a late arrival?", "resolved": False},
            {"id": "g2", "step": "s15", "branch": "owner late", "question": "What if the owner is late?",
             "resolved": False}]
    r = score({"steps": [], "links": [], "gaps": gaps, "contradictions": []}, {"cards": []}, key)
    assert len(r["gaps"]["false"]) == 1 and "owner is late" in r["gaps"]["false"][0]
    assert r["gaps"]["unjudged"] == ["What happens if there is no free slot for a late arrival?"]


def test_key_music_parent_homework_needs_no_login_box():
    key = MEETINGS["music_school"]["key"]
    assert "parent.login" not in key["steps"]
    steps = [{"id": "s1", "label": "See child homework", "part": "Parent portal"}]
    r = score({"steps": steps, "links": [], "gaps": [], "contradictions": []}, {"cards": []}, key)
    assert "parent.homework" not in r["steps"]["missing"]
    assert not any("parent.login" in x for x in r["links"]["missing"])
