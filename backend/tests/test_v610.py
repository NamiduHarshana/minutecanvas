"""v6.10: fixes traced in the v6.9 bench run (bench 20261101-182514)."""
from __future__ import annotations

from app import flow as flow_mod
from app.flow import Flow
from app.prompts import FLOW_SYSTEM, FLOW_TOOLS
from bench.bench import _any, score
from bench.meetings import LEETCODE_KEY, MEETINGS


def _lines(*texts, step=6.0, speaker="A"):
    return [{"id": i, "turn": f"1-{i}", "speaker": speaker, "t": i * step, "t_end": i * step + 3, "text": t}
            for i, t in enumerate(texts)]


def _ok(r):
    assert r.ok, r.message
    return r


GROOM = _lines(
    "So first the owner downloads the app and signs in with their phone number.",           # 0
    "So for the groomers themselves, they have a simple screen with today's bookings.",       # 1
    "They log in with a staff account on the salon tablet.",                                  # 2
    "They see the pet, the service and any notes like the dog bites.",                        # 3
)


def _groom_flow() -> Flow:
    f = Flow()
    _ok(f.apply("add_step", {"label": "Sign in with phone", "kind": "start", "part": "Customer app",
                             "quote": "the owner downloads the app and signs in with their phone number"}, GROOM, 5))
    _ok(f.apply("add_step", {"label": "View today's bookings", "kind": "step", "part": "Groomer app",
                             "quote": "they have a simple screen with today's bookings"}, GROOM, 10))
    _ok(f.apply("add_step", {"label": "See pet service notes", "kind": "step", "after": "s2",
                             "quote": "They see the pet, the service and any notes"}, GROOM, 20))
    return f


# ---------------- 1. a step said later that comes BEFORE a part's first box ----------------
# grooming: "Log in with staff" (said after "today's bookings") was refused for having no "after", so the agent made
# a one-box part "Staff login" to get it in.
def test_before_makes_new_first_step_of_the_part():
    f = _groom_flow()
    r = _ok(f.apply("add_step", {"label": "Log in with staff", "kind": "step", "before": "s2",
                                 "quote": "They log in with a staff account on the salon tablet"}, GROOM, 20))
    sid = r.message["id"]
    assert r.message["before"] == "s2"
    assert f.steps[sid].part == "Groomer app"              # joins the part of the box it comes before
    assert (sid, "s2") in {(k.src, k.dst) for k in f.links.values()}
    assert f.part_first("Groomer app") == sid               # the part now begins at the login
    assert f.parts() == ["Customer app", "Groomer app"]     # no one-box "Staff login" part
    assert f.unlinked() == []                               # flow not broken anywhere


def test_before_ignores_an_invented_part_name():
    f = _groom_flow()
    r = _ok(f.apply("add_step", {"label": "Log in with staff", "kind": "step", "before": "s2", "part": "Staff login",
                                 "quote": "They log in with a staff account on the salon tablet"}, GROOM, 20))
    assert f.steps[r.message["id"]].part == "Groomer app"
    assert "Staff login" not in f.parts()


def test_before_refused_inside_a_chain_and_before_start():
    f = _groom_flow()
    r = f.apply("add_step", {"label": "Log in with staff", "kind": "step", "before": "s3",
                             "quote": "They log in with a staff account on the salon tablet"}, GROOM, 20)
    assert not r.ok and "after" in r.message["error"] and "s2" in r.message["error"]
    r = f.apply("add_step", {"label": "Log in with staff", "kind": "step", "before": "s1",
                             "quote": "They log in with a staff account on the salon tablet"}, GROOM, 20)
    assert not r.ok and "start" in r.message["error"]
    r = f.apply("add_step", {"label": "Log in with staff", "kind": "step", "before": "s99",
                             "quote": "They log in with a staff account on the salon tablet"}, GROOM, 20)
    assert not r.ok and "not found" in r.message["error"]
    assert len(f.steps) == 3


def test_no_after_error_tells_about_before():
    f = _groom_flow()
    r = f.apply("add_step", {"label": "Log in with staff", "kind": "step", "part": "Groomer app",
                             "quote": "They log in with a staff account on the salon tablet"}, GROOM, 20)
    assert not r.ok and "before =" in r.message["error"] and "Never invent a part" in r.message["error"]


def test_prompt_and_schema_have_before():
    add = next(t for t in FLOW_TOOLS if t["function"]["name"] == "add_step")
    assert "before" in add["function"]["parameters"]["properties"]
    assert "before = that first step" in FLOW_SYSTEM
    assert "Never start a new part just to place one step" in FLOW_SYSTEM


# ---------------- 2. step cap: a 32-min meeting hit 40 at 26:32 and lost its last 7 steps ----------------
def test_flow_takes_more_than_40_steps():
    assert flow_mod.MAX_STEPS >= 80 and flow_mod.MAX_LINKS >= 2 * 80
    lines = _lines(*[f"then the user does thing number {i} on the page" for i in range(60)])
    f = Flow()
    _ok(f.apply("add_step", {"label": "Step 0", "kind": "start", "quote": "then the user does thing number 0 on the page"},
                lines, 1))
    for i in range(1, 60):
        _ok(f.apply("add_step", {"label": f"Step {i}", "kind": "step", "after": f"s{i}",
                                 "quote": f"then the user does thing number {i} on the page"}, lines, 1))
    assert len(f.steps) == 60 and len(f.links) == 59


# ---------------- 3. answer key / scorer ----------------
def _step(i, label, part):
    return {"id": f"s{i}", "label": label, "part": part}


def test_select_coaching_track_is_not_progress_tracking():
    k = LEETCODE_KEY["steps"]
    assert not _any("Select coaching track", k["practice.metrics"])
    assert _any("Track progress notes", k["practice.metrics"])
    assert _any("Receive daily question", k["daily.slack"])
    assert not _any("Receive questions access", k["daily.slack"])


def test_grooming_done_in_groomer_row_counts_and_split_part_is_shown():
    key = MEETINGS["grooming_app"]["key"]
    steps = [_step(1, "Check in at salon", "Customer app"), _step(2, "Receive ready photo", "Customer app"),
             _step(3, "View today's bookings", "Groomer app"), _step(4, "Mark grooming done", "Groomer app"),
             _step(5, "Log in with staff", "Staff login")]
    links = [{"src": a, "dst": b} for a, b in (("s1", "s2"), ("s3", "s4"), ("s4", "s2"), ("s5", "s3"))]
    r = score({"steps": steps, "links": links, "gaps": [], "contradictions": []}, {"cards": []}, key)
    assert "app.checkin->app|staff.done" not in r["links"]["missing"]
    assert "staff.login->staff.today" not in r["links"]["missing"]
    assert r["parts"]["split"] == {"staff": ["Groomer app", "Staff login"]}
    steps[4]["part"] = "Groomer app"
    r = score({"steps": steps, "links": links, "gaps": [], "contradictions": []}, {"cards": []}, key)
    assert r["parts"]["split"] == {}


def test_music_parent_portal_is_its_own_part():
    key = MEETINGS["music_school"]["key"]
    steps = [_step(1, "Teacher log in", "Teacher portal"), _step(2, "Mark attendance", "Teacher portal"),
             _step(3, "Post homework", "Teacher portal"), _step(4, "Parent log in", "Parent portal"),
             _step(5, "See child's homework", "Parent portal"), _step(6, "See announcements", "Parent portal")]
    links = [{"src": a, "dst": b} for a, b in (("s1", "s2"), ("s2", "s3"), ("s4", "s5"), ("s4", "s6"))]
    r = score({"steps": steps, "links": links, "gaps": [], "contradictions": []}, {"cards": []}, key)
    assert r["parts"]["purity"] == 1.0 and r["wrong_cross_part_links"] == []
    assert "parent.login->parent.homework" not in r["links"]["missing"]
