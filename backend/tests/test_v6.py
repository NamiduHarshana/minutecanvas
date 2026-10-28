"""v6: conflicts are not decisions, cards that repeat flow steps, automatic gap check, debug dump."""
import json
import os

from app import session as session_mod
from app.board import Board, numbers, restates_step
from app.config import settings
from app.flow import Flow
from app.meta_api import MetaClient
from app.session import Session

LINES = [
    {"id": 0, "speaker": "A", "t": 60.0, "text": "I think five sessions for 1000 dollars is the plan."},
    {"id": 1, "speaker": "B", "t": 90.0, "text": "Okay customers fill in the story map and submit it."},
    {"id": 2, "speaker": "A", "t": 400.0, "text": "I think we'll make seven sessions, make it 1400."},
]


def _decision(b: Board, title: str, said: str):
    return b.apply_spark({"new_cards": [{"column": "decisions", "title": title, "detail": "", "owner": None, "due": None,
                                         "status": None, "said_at": said, "image_prompt": "x"}]}, 500)


def test_numbers_and_restates_step():
    assert numbers("Offer five sessions for $1,000") == {"5", "1000"}
    assert numbers("2.5k budget") >= {"2500"}
    assert restates_step("Submit the story map", ["Submit story map"])
    assert not restates_step("Offer seven sessions for $1400", ["Offer sessions"])   # has numbers: a real fact
    assert not restates_step("Use Stripe for payments", ["Payment page"])


def test_decision_that_repeats_a_flow_step_is_dropped():
    b = Board()
    ch = b.apply_spark({"new_cards": [
        {"column": "decisions", "title": "Customers submit the story map", "detail": "", "owner": None, "due": None,
         "status": None, "said_at": "01:30", "image_prompt": "x"},
        {"column": "decisions", "title": "Seven sessions for $1400", "detail": "", "owner": None, "due": None,
         "status": None, "said_at": "06:40", "image_prompt": "x"},
    ]}, 500, ["Submit story map"])
    assert [c.title for c in ch.added] == ["Seven sessions for $1400"]


def test_both_sides_of_a_conflict_are_tagged_and_freed_when_settled():
    b, f = Board(), Flow()
    _decision(b, "Offer five sessions for $1000", "01:00")
    _decision(b, "Offer seven sessions for $1400", "06:40")
    _decision(b, "Coaching starts in January", "06:40")          # same line time, no shared number: untouched
    r = f.apply("flag_contradiction", {"quote_a": "five sessions for 1000 dollars", "quote_b": "seven sessions, make it 1400",
                                       "note": "Package: 5 for $1000 or 7 for $1400?"}, LINES, 500)
    assert r.ok
    changed = b.link_conflicts(list(f.contradictions.values()))
    tagged = {c.title for c in b.cards.values() if c.conflict_ref}
    assert tagged == {"Offer five sessions for $1000", "Offer seven sessions for $1400"} and len(changed) == 2
    assert all(x.get("conflict") for x in b.for_spark() if x["title"] in tagged)
    f.apply("resolve", {"item_id": "c1", "answer": "seven", "quote": "I think we'll make seven sessions"}, LINES, 520)
    b.link_conflicts(list(f.contradictions.values()))
    assert not any(c.conflict_ref for c in b.cards.values())


def _session() -> Session:
    return Session(MetaClient(), [], "Test", "127.0.0.1")


async def test_auto_gap_check_runs_only_when_due_and_map_changed(monkeypatch):
    s = _session()
    s.transcript = [dict(x, turn=str(x["id"])) for x in LINES]
    s.flow_done_idx = 3
    s.flow.apply("add_step", {"label": "Story map", "kind": "start", "quote": "fill in the story map"}, s.transcript, 100)
    s.flow.apply("add_step", {"label": "Submit", "kind": "step", "after": "s1", "quote": "fill in the story map and submit"},
                 s.transcript, 100)
    modes = []

    async def fake_round(mode):
        modes.append(mode)
    monkeypatch.setattr(s, "_flow_round", fake_round)
    monkeypatch.setattr(settings, "auto_gap_check_s", 240.0)
    monkeypatch.setattr(settings, "auto_gap_min_steps", 3)

    assert not await s.auto_gap_check()                             # only 2 steps
    s.flow.apply("add_step", {"label": "Pay", "kind": "step", "after": "s2", "quote": "five sessions for 1000 dollars"},
                 s.transcript, 100)
    assert not await s.auto_gap_check()                             # not due yet
    s.last_auto_check -= 300
    assert await s.auto_gap_check() and modes == ["gap_check"]
    s.last_auto_check -= 300
    assert not await s.auto_gap_check()                             # map unchanged since the last check


async def test_gap_check_round_sends_numbers_and_budget_one(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "debug_dump", True)
    monkeypatch.setattr(settings, "debug_dir", str(tmp_path))
    s = _session()
    filler = [{"id": i, "speaker": "A", "t": 100.0 + i, "text": f"filler line about the design videos part {i}"}
              for i in range(3, 100)]
    s.transcript = [dict(x, turn=str(x["id"])) for x in LINES[:2]] + [dict(x, turn=str(x["id"])) for x in filler]
    for i, x in enumerate(s.transcript):
        x["id"] = i
    s.flow.apply("add_step", {"label": "Story map", "kind": "start", "quote": "fill in the story map"}, s.transcript, 100)
    s.flow_done_idx = len(s.transcript)
    seen = {}

    async def fake_tools(messages, tools):
        seen["payload"] = json.loads(messages[1]["content"])
        seen["budget"] = s.flow.gap_budget
        return {"role": "assistant", "content": "done"}
    monkeypatch.setattr(s.meta, "spark_tools", fake_tools)
    s.meta.tools_ok = True
    await s._flow_round("gap_check")
    p = seen["payload"]
    assert p["mode"] == "gap_check" and p["new_transcript"] == [] and seen["budget"] == 1
    assert any("1000 dollars" in x for x in p["earlier_lines_with_numbers"])   # 20 minutes back, still compared
    dump = json.load(open(os.path.join(tmp_path, os.listdir(tmp_path)[0])))
    assert dump["calls"][-1]["kind"] == "flow" and dump["calls"][-1]["mode"] == "gap_check"
    assert len(dump["transcript"]) == len(s.transcript)


def test_debug_dump_off_writes_nothing(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "debug_dump", False)
    monkeypatch.setattr(settings, "debug_dir", str(tmp_path))
    _session()._debug("cards", payload={})
    assert os.listdir(tmp_path) == []


def test_constants():
    assert session_mod.CARDS_CONTEXT_LINES >= 12 and settings.max_images >= 80


def test_conflict_tag_from_a_nearby_line_needs_a_specific_number():
    b, f = Board(), Flow()
    _decision(b, "Package price is $1000", "01:20")        # 20 s after the quoted line, shares 1000
    _decision(b, "Five sessions planned", "01:20")         # shares only "5": too common to match from another line
    f.apply("flag_contradiction", {"quote_a": "five sessions for 1000 dollars", "quote_b": "seven sessions, make it 1400",
                                   "note": "Package size and price"}, LINES, 500)
    b.link_conflicts(list(f.contradictions.values()))
    assert {c.title for c in b.cards.values() if c.conflict_ref} == {"Package price is $1000"}
