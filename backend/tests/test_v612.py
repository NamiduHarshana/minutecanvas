"""v6.12: fixes traced in the v6.11 bench run (bench 20261101-222244)."""
from __future__ import annotations

import json

from app.flow import Flow
from app.meta_api import MetaClient
from app.session import Session
from bench.bench import score
from bench.meetings import MEETINGS
from scripts.replay import replay


def _lines(*texts, step=6.0, speaker="A"):
    return [{"id": i, "turn": f"1-{i}", "speaker": speaker, "t": i * step, "t_end": i * step + 3, "text": t}
            for i, t in enumerate(texts)]


def _ok(r):
    assert r.ok, r.message
    return r


def _pairs(f: Flow) -> set[tuple[str, str]]:
    return {(k.src, k.dst) for k in f.links.values()}


COACH = _lines(
    "for the coaching they get feedback and then they do mock interviews with us",       # 0
    "for system design there are four sessions",                                         # 1
    "then you have follow-on practice sessions and mock interviews right",               # 2
)


def _two_parts() -> Flow:
    f = Flow()
    _ok(f.apply("add_step", {"label": "Get feedback", "part": "Coaching",
                             "quote": "they get feedback and then they do mock interviews"}, COACH, 5))
    _ok(f.apply("add_step", {"label": "Do mock interviews", "after": "s1", "part": "Coaching",
                             "quote": "they do mock interviews with us"}, COACH, 5))
    _ok(f.apply("add_step", {"label": "Do four sessions", "part": "System design",
                             "quote": "for system design there are four sessions"}, COACH, 10))
    _ok(f.apply("add_step", {"label": "Practice sessions", "after": "s3", "part": "System design",
                             "quote": "then you have follow-on practice sessions"}, COACH, 15))
    f.round_new = set()
    return f


# ---------------- 1. same label in another part = that part's own box ----------------
# leetcode: add_step "Do mock interviews" part "System design" after "Practice follow-on sessions" reused Behavioral
# coaching's box and drew an arrow from the System design row into the Behavioral row.
def test_same_label_in_another_existing_part_is_a_new_box():
    f = _two_parts()
    r = _ok(f.apply("add_step", {"label": "Do mock interviews", "after": "s4", "part": "System design",
                                 "quote": "follow-on practice sessions and mock interviews right"}, COACH, 20))
    assert r.message["note"] == "added" and r.message["id"] == "s5"
    assert f.steps["s5"].part == "System design" and f.steps["s2"].part == "Coaching"
    assert ("s4", "s5") in _pairs(f) and ("s4", "s2") not in _pairs(f)


def test_same_label_same_part_is_still_reused():
    f = _two_parts()
    r = _ok(f.apply("add_step", {"label": "Do mock interviews", "after": "s1", "part": "Coaching",
                                 "quote": "they do mock interviews with us"}, COACH, 20))
    assert r.message["id"] == "s2" and "reused" in r.message["note"]
    assert len(f.steps) == 4


def test_same_label_without_part_is_still_reused():
    f = _two_parts()
    r = _ok(f.apply("add_step", {"label": "Do mock interviews", "after": "s1",
                                 "quote": "they do mock interviews with us"}, COACH, 20))
    assert r.message["id"] == "s2" and len(f.steps) == 4


def test_after_by_label_prefers_the_box_of_the_same_part():
    f = _two_parts()
    _ok(f.apply("add_step", {"label": "Do mock interviews", "after": "s4", "part": "System design",
                             "quote": "follow-on practice sessions and mock interviews right"}, COACH, 20))
    r = _ok(f.apply("add_step", {"label": "Get design review", "after": "Do mock interviews", "part": "System design",
                                 "quote": "follow-on practice sessions and mock interviews right"}, COACH, 20))
    assert ("s5", r.message["id"]) in _pairs(f)


def test_a_new_invented_part_name_does_not_split_a_box():
    # a part that is not on the map yet: keep the old behaviour (reuse), the agent can't make a second box that way
    f = _two_parts()
    r = _ok(f.apply("add_step", {"label": "Do mock interviews", "after": "s1", "part": "Mock prep",
                                 "quote": "they do mock interviews with us"}, COACH, 20))
    assert r.message["id"] == "s2" and len(f.steps) == 4


# ---------------- 2. final pass: one more look when a gap slot is left ----------------
# grooming (v6.10 and v6.11): the final pass flagged a money gap and answered "done" with a slot left; "what if the
# owner doesn't show up?" was never weighed.
GROOM = _lines(
    "So first the owner downloads the app and books a slot.",          # 0
    "When they pick the slot they pay a deposit by card.",             # 1
    "On the day the owner comes to the salon and checks in.",          # 2
)


def _call(cid, name, args):
    return {"id": cid, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


def _groom_session() -> Session:
    s = Session(MetaClient(), [], "Test", "127.0.0.1")
    s.transcript = list(GROOM)
    f = s.flow
    _ok(f.apply("add_step", {"label": "Book slot", "kind": "start", "part": "App",
                             "quote": "the owner downloads the app and books a slot"}, s.transcript, 5))
    _ok(f.apply("add_step", {"label": "Pay deposit", "after": "s1", "quote": "they pay a deposit by card"},
                s.transcript, 10))
    _ok(f.apply("add_step", {"label": "Check in", "after": "s2", "quote": "the owner comes to the salon and checks in"},
                s.transcript, 15))
    f.round_new = set()
    s.spark_done_idx = s.flow_done_idx = len(s.transcript)
    return s


async def test_final_pass_asks_once_more_when_a_gap_slot_is_left(monkeypatch):
    s = _groom_session()
    seen: list[list[dict]] = []
    replies = [
        {"role": "assistant", "content": None, "tool_calls": [_call("a", "flag_gap", {
            "step": "s2", "question": "What if the deposit payment fails?", "missing_branch": "payment fails",
            "quote": "they pay a deposit by card"})]},
        {"role": "assistant", "content": "done"},
        {"role": "assistant", "content": None, "tool_calls": [_call("b", "flag_gap", {
            "step": "s3", "question": "What if the owner doesn't show up?", "missing_branch": "no-show",
            "quote": "the owner comes to the salon and checks in"})]},
        {"role": "assistant", "content": "done"},
    ]

    async def fake_tools(messages, tools):
        seen.append(list(messages))
        return replies[len(seen) - 1]

    async def no_check(*a, **k):
        return None
    monkeypatch.setattr(s.meta, "spark_tools", fake_tools)
    monkeypatch.setattr(s, "_verify_flow", no_check)
    await s._flow_round("final")
    assert len(seen) == 4                                   # done -> one nudge -> one more gap -> done (no 2nd nudge)
    nudge = seen[2][-1]
    assert nudge["role"] == "user" and "may still flag 1 gap" in nudge["content"]
    assert "deposit payment fails" in nudge["content"]       # it sees what is already asked
    assert sorted(g.question for g in s.flow.gaps.values()) == ["What if the deposit payment fails?",
                                                                "What if the owner doesn't show up?"]


async def test_no_nudge_when_the_slots_are_used_or_in_live_rounds(monkeypatch):
    s = _groom_session()
    n = []

    async def fake_tools(messages, tools):
        n.append(1)
        return {"role": "assistant", "content": "done"}
    monkeypatch.setattr(s.meta, "spark_tools", fake_tools)
    s.transcript.append({"id": 3, "turn": "1-3", "speaker": "A", "t": 30, "t_end": 33,
                         "text": "And then the groomer starts with the bath and the haircut."})
    await s._flow_round("live")
    assert len(n) == 1                                      # live: no nudge
    s.flow_done_idx = len(s.transcript)
    await s._flow_round("final")
    assert len(n) == 3                                      # final: done -> nudge -> done
    s.flow.gap_budget = 0
    assert s._gap_nudge() is None


# ---------------- 3. replay keeps ids in step when current code adds a box the run didn't ----------------
def test_replay_maps_shifted_ids():
    t = COACH
    calls = [{"kind": "flow", "mode": "live", "meeting_t": 20, "turns": [{"calls": [
        {"name": "add_step", "arguments": json.dumps({"label": "Get feedback", "part": "Coaching",
                                                      "quote": "they get feedback and then they do mock interviews"}),
         "result": {"ok": True, "id": "s1", "note": "added"}},
        {"name": "add_step", "arguments": json.dumps({"label": "Do mock interviews", "after": "s1", "part": "Coaching",
                                                      "quote": "they do mock interviews with us"}),
         "result": {"ok": True, "id": "s2", "note": "added"}},
        {"name": "add_step", "arguments": json.dumps({"label": "Do four sessions", "part": "System design",
                                                      "quote": "for system design there are four sessions"}),
         "result": {"ok": True, "id": "s3", "note": "added"}},
        # the run reused s2 here; current code adds s4
        {"name": "add_step", "arguments": json.dumps({"label": "Do mock interviews", "after": "s3",
                                                      "part": "System design",
                                                      "quote": "follow-on practice sessions and mock interviews right"}),
         "result": {"ok": True, "id": "s2", "note": "step already existed, reused it"}},
        {"name": "add_step", "arguments": json.dumps({"label": "Practice sessions", "after": "s3",
                                                      "part": "System design",
                                                      "quote": "then you have follow-on practice sessions"}),
         "result": {"ok": True, "id": "s4", "note": "added"}},
        {"name": "link_steps", "arguments": json.dumps({"from_step": "s4", "to_step": "s1",
                                                        "quote": "they get feedback and then they do mock interviews"}),
         "result": {"ok": True, "id": "l4"}},
    ]}]}]
    flow, _, diffs = replay({"transcript": t, "calls": calls})
    assert not diffs
    lab = {s.id: s.label for s in flow.steps.values()}
    assert lab["s5"] == "Practice sessions"
    assert ("s5", "s1") in _pairs(flow)                      # the run's "s4" is now s5


# ---------------- 4. bench key ----------------
def test_key_music_parent_steps_fit_the_rows_the_meeting_named():
    key = MEETINGS["music_school"]["key"]
    steps = [{"id": "s1", "label": "See portal announcements", "part": "Group classes"},
             {"id": "s2", "label": "View class timetable", "part": "Group classes"},
             {"id": "s3", "label": "Post class homework", "part": "Teacher side"},
             {"id": "s4", "label": "View child homework", "part": "Teacher side"}]
    r = score({"steps": steps, "links": [], "gaps": [], "contradictions": []}, {"cards": []}, key)
    assert r["parts"]["purity"] == 1.0
    steps[0]["part"] = steps[3]["part"] = "Parent portal"      # a parent row is right too
    r = score({"steps": steps, "links": [], "gaps": [], "contradictions": []}, {"cards": []}, key)
    assert r["parts"]["purity"] == 1.0 and not r["parts"]["split"]


# ---------------- review follow-ups ----------------
def test_near_same_box_in_the_named_part_is_reused_not_doubled():
    f = _two_parts()
    _ok(f.apply("add_step", {"label": "Do the mock interviews", "after": "s4", "part": "System design",
                             "quote": "follow-on practice sessions and mock interviews right"}, COACH, 20))
    r = _ok(f.apply("add_step", {"label": "Do mock interviews", "after": "s4", "part": "System design",
                                 "quote": "follow-on practice sessions and mock interviews right"}, COACH, 20))
    assert r.message["id"] == "s5" and len(f.steps) == 5


async def test_no_nudge_on_the_last_turn(monkeypatch):
    import app.session as sess
    s = _groom_session()
    n = []

    async def fake_tools(messages, tools):
        n.append(1)
        return {"role": "assistant", "content": "done"}
    monkeypatch.setattr(s.meta, "spark_tools", fake_tools)
    monkeypatch.setitem(sess.FLOW_MAX_TURNS, "final", 1)
    await s._flow_round("final")
    assert len(n) == 1 and not s.flow_cut


async def test_json_loop_nudges_once(monkeypatch):
    s = _groom_session()
    s.meta.tools_ok = False
    n = []

    async def fake_json(system, user, name, schema, history=None):
        n.append(list(history or []))
        return {"calls": []}
    monkeypatch.setattr(s.meta, "spark_json", fake_json)
    await s._flow_round("final")
    assert len(n) == 2 and "may still flag 1 gap" in n[1][-1]["content"]
