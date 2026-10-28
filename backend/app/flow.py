"""Live Flow Map: steps, links, bugs, gaps and contradictions built by the Spark Flow agent.

Every tool call must carry a quote. The quote is checked against the real transcript
(check_quote) before the call is accepted; who said it and when come from that transcript
line, never from the model. Rejected calls get an error back so the agent can fix them.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from difflib import SequenceMatcher

from .security import clean_optional, clean_text

STEP_KINDS = ("start", "step", "decision", "end")
LINK_TYPES = ("normal", "fail")

MAX_STEPS = 40
MAX_LINKS = 80
MAX_BUGS = 30
MAX_OPEN_GAPS = 5
MAX_GAPS = 40
MAX_CONTRADICTIONS = 20
MAX_EVIDENCE = 5

LABEL_MAX = 40
LINK_LABEL_MAX = 30
TEXT_MAX = 160
QUOTE_MAX = 300
QUOTE_MIN_WORDS = 3
QUOTE_MAX_WORDS = 45

# "[04:12] Kasun: ..." prefix the agent may copy along with the quote
_PREFIX = re.compile(r"^\s*\[?\d{1,2}:\d{2}(?::\d{2})?\]?\s*(?:[^:\n]{1,40}:)?\s*")
_QUOTES = str.maketrans({"’": "", "'": "", "‘": "", "“": " ", "”": " ", '"': " "})


def tokens(s: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", s.lower().translate(_QUOTES))


@dataclass
class Evidence:
    line: int        # transcript line id
    speaker: str     # speaker label (frontend shows the current name)
    t: float         # meeting time of the line
    quote: str       # the checked words (or the whole line if the agent's copy was slightly off)


def check_quote(quote, lines: list[dict]) -> tuple[Evidence | None, str]:
    """Find the transcript line a quote comes from. Returns (evidence, "") or (None, reason)."""
    if not isinstance(quote, str) or not quote.strip():
        return None, "quote is missing"
    raw = _PREFIX.sub("", quote.strip(), count=1) if _PREFIX.match(quote) else quote.strip()
    raw = raw.strip().strip('"“”').strip()
    q = tokens(raw)
    if len(q) < QUOTE_MIN_WORDS:
        return None, f"quote too short: copy at least {QUOTE_MIN_WORDS} words exactly from one transcript line"
    if len(q) > QUOTE_MAX_WORDS:
        return None, f"quote too long: copy at most {QUOTE_MAX_WORDS} words from one line"
    needle = " " + " ".join(q) + " "
    best, best_score = None, 0.0
    for line in reversed(lines):              # newest first
        lt = tokens(line.get("text", ""))
        if not lt:
            continue
        if needle in " " + " ".join(lt) + " ":
            return _ev(line, clean_text(raw, QUOTE_MAX)), ""
        if len(q) >= 5:                       # ASR wording: tolerate a word or two
            blocks = SequenceMatcher(None, q, lt, autojunk=False).get_matching_blocks()
            matched = sum(b.size for b in blocks)
            used = [b for b in blocks if b.size]
            if used:
                span = used[-1].b + used[-1].size - used[0].b
                score = matched / len(q)
                if score >= 0.85 and span <= len(q) + 3 and score > best_score:
                    best, best_score = line, score
    if best is not None:
        return _ev(best, clean_text(best.get("text", ""), QUOTE_MAX)), ""
    return None, "quote not found in the transcript: copy the exact words from one transcript line"


def _ev(line: dict, quote: str) -> Evidence:
    return Evidence(line=int(line.get("id", 0)), speaker=str(line.get("speaker") or "A"),
                    t=float(line.get("t", 0.0)), quote=quote)


def _similar(a: str, b: str) -> bool:
    x, y = set(tokens(a)), set(tokens(b))
    if not x or not y:
        return a.strip().lower() == b.strip().lower()
    return len(x & y) / len(x | y) >= 0.75


@dataclass
class Step:
    id: str
    label: str
    kind: str
    t: float
    evidence: list[Evidence] = field(default_factory=list)
    updated_t: float | None = None


@dataclass
class Link:
    id: str
    src: str
    dst: str
    label: str
    type: str
    t: float
    evidence: list[Evidence] = field(default_factory=list)


@dataclass
class Bug:
    id: str
    step: str
    text: str
    t: float
    evidence: list[Evidence] = field(default_factory=list)
    fixed: bool = False
    fix: Evidence | None = None


@dataclass
class Gap:
    id: str
    step: str
    branch: str
    question: str
    t: float
    evidence: list[Evidence] = field(default_factory=list)
    resolved: bool = False
    answer: str | None = None
    resolved_by: Evidence | None = None
    dismissed: bool = False          # the user deleted its question card
    card_id: str | None = None       # the Open questions card made from this gap


@dataclass
class Contradiction:
    id: str
    step: str | None
    note: str
    a: Evidence
    b: Evidence
    t: float
    resolved: bool = False
    answer: str | None = None
    resolved_by: Evidence | None = None


@dataclass
class Result:
    ok: bool
    message: dict
    events: list[dict] = field(default_factory=list)
    new_gap: Gap | None = None
    resolved_gap: Gap | None = None


class Flow:
    def __init__(self) -> None:
        self.steps: dict[str, Step] = {}
        self.links: dict[str, Link] = {}
        self.bugs: dict[str, Bug] = {}
        self.gaps: dict[str, Gap] = {}
        self.contradictions: dict[str, Contradiction] = {}
        self._n = {"s": 0, "l": 0, "b": 0, "g": 0, "c": 0}
        self.gap_budget = 1          # new gaps allowed this round (session sets it per mode)

    def _id(self, p: str) -> str:
        self._n[p] += 1
        return f"{p}{self._n[p]}"

    # ---------- views ----------
    def public(self) -> dict:
        return {
            "steps": [asdict(x) for x in self.steps.values()],
            "links": [asdict(x) for x in self.links.values()],
            "bugs": [asdict(x) for x in self.bugs.values()],
            "gaps": [asdict(x) for x in self.gaps.values() if not x.dismissed],
            "contradictions": [asdict(x) for x in self.contradictions.values()],
        }

    def empty(self) -> bool:
        return not self.steps

    def unlinked(self) -> list[Step]:
        """Steps nobody leads into (except start steps): the flow is broken there."""
        has_in = {k.dst for k in self.links.values()}
        return [s for s in self.steps.values() if s.kind != "start" and s.id not in has_in]

    def for_agent(self) -> dict:
        return {
            "steps": [{"id": s.id, "label": s.label, "kind": s.kind} for s in self.steps.values()],
            "unlinked_steps": [s.id for s in self.unlinked()],
            "open_ends": self.open_ends(),
            "links": [{"from": k.src, "to": k.dst, "label": k.label, "type": k.type} for k in self.links.values()],
            "open_bugs": [{"id": b.id, "step": b.step, "text": b.text} for b in self.bugs.values() if not b.fixed],
            "gaps": [{"id": g.id, "step": g.step, "question": g.question, "resolved": g.resolved}
                     for g in self.gaps.values() if not g.dismissed],
            "contradictions": [{"id": c.id, "note": c.note, "resolved": c.resolved} for c in self.contradictions.values()],
        }

    def open_ends(self) -> dict:
        """Hints for gap review: decisions with one outcome, and steps where the flow just stops."""
        out_count: dict[str, int] = {}
        for k in self.links.values():
            out_count[k.src] = out_count.get(k.src, 0) + 1
        gapped = {g.step for g in self.gaps.values() if not g.dismissed}
        one_way = [s.id for s in self.steps.values() if s.kind == "decision" and out_count.get(s.id, 0) < 2 and s.id not in gapped]
        stops = [s.id for s in self.steps.values() if s.kind != "end" and out_count.get(s.id, 0) == 0 and s.id not in gapped]
        return {"decisions_with_one_outcome": one_way, "flow_stops_here": stops}

    def open_gaps(self) -> list[Gap]:
        return [g for g in self.gaps.values() if not g.resolved and not g.dismissed]

    def find_step(self, ref) -> Step | None:
        """By id ("s3") or by label (so one batch can chain steps it just added)."""
        if not isinstance(ref, str) or not ref.strip():
            return None
        ref = ref.strip()
        if ref in self.steps:
            return self.steps[ref]
        low = ref.lower()
        for s in self.steps.values():
            if s.label.lower() == low:
                return s
        for s in self.steps.values():
            if _similar(s.label, ref):
                return s
        return None

    # ---------- agent tools ----------
    def apply(self, name: str, args: dict, lines: list[dict], now_t: float) -> Result:
        fn = getattr(self, f"_t_{name}", None) if isinstance(name, str) and name.isidentifier() else None
        if fn is None:
            return _err(f"unknown tool {name!r}")
        if not isinstance(args, dict):
            return _err("arguments must be a JSON object")
        try:
            return fn(args, lines, round(now_t, 1))
        except (TypeError, ValueError) as e:
            return _err(f"bad arguments: {e}")

    def _t_add_step(self, a: dict, lines, now) -> Result:
        label = clean_text(a.get("label"), LABEL_MAX)
        if not label:
            return _err("label is required")
        ev, why = check_quote(a.get("quote"), lines)
        if not ev:
            return _err(why)
        kind = a.get("kind") if a.get("kind") in STEP_KINDS else "step"
        after = a.get("after")
        src = self.find_step(after) if after else None
        events: list[dict] = []
        start = next((x for x in self.steps.values() if x.kind == "start"), None)
        demoted = False
        if kind == "start" and start is not None and start.label.lower() != label.lower():
            kind, demoted = "step", True     # one journey = one start; keeps the map one connected flow
        s = self.find_step(label)
        if s:
            _add_ev(s.evidence, ev)
            note = "step already existed, reused it"
        else:
            if len(self.steps) >= MAX_STEPS:
                return _err("flow is full (max steps)")
            s = Step(id=self._id("s"), label=label, kind=kind, t=ev.t, evidence=[ev])
            self.steps[s.id] = s
            events.append({"kind": "step_added", "id": s.id, "text": f"New step: {s.label}"})
            note = "added"
        msg = {"ok": True, "id": s.id, "note": note}
        if demoted:
            msg["warning"] = f"there is already a start step ({start.id}); added as a normal step: link it with after"
        if after and not src:
            msg["warning"] = f"step {after!r} not found, so no link was made"
        if src and src.id != s.id:
            r = self._link(src, s, a.get("link_label"), a.get("link_type"), ev, now)
            events += r
        return Result(True, msg, events)

    def _t_link_steps(self, a: dict, lines, now) -> Result:
        src, dst = self.find_step(a.get("from_step")), self.find_step(a.get("to_step"))
        if not src or not dst:
            return _err("from_step and to_step must be existing step ids")
        if src.id == dst.id:
            return _err("a step can't link to itself")
        ev, why = check_quote(a.get("quote"), lines)
        if not ev:
            return _err(why)
        events = self._link(src, dst, a.get("label"), a.get("type"), ev, now)
        lk = self._find_link(src.id, dst.id)
        return Result(True, {"ok": True, "id": lk.id if lk else None}, events)

    def _link(self, src: Step, dst: Step, label, typ, ev: Evidence, now: float) -> list[dict]:
        label = clean_text(label, LINK_LABEL_MAX) if label else ""
        typ = typ if typ in LINK_TYPES else "normal"
        lk = self._find_link(src.id, dst.id)
        if lk:
            _add_ev(lk.evidence, ev)
            changed = False
            if label and label != lk.label:
                lk.label, changed = label, True
            if typ != lk.type:
                lk.type, changed = typ, True
            return [{"kind": "link_updated", "id": lk.id, "text": f"{src.label} → {dst.label}"}] if changed else []
        if len(self.links) >= MAX_LINKS:
            return []
        lk = Link(id=self._id("l"), src=src.id, dst=dst.id, label=label, type=typ, t=ev.t, evidence=[ev])
        self.links[lk.id] = lk
        return [{"kind": "link_added", "id": lk.id, "text": f"{src.label} → {dst.label}"}]

    def _find_link(self, src: str, dst: str) -> Link | None:
        return next((k for k in self.links.values() if k.src == src and k.dst == dst), None)

    def _t_update_step(self, a: dict, lines, now) -> Result:
        s = self.find_step(a.get("step"))
        if not s:
            return _err("step must be an existing step id")
        ev, why = check_quote(a.get("quote"), lines)
        if not ev:
            return _err(why)
        label = clean_text(a.get("label"), LABEL_MAX)
        kind = a.get("kind") if a.get("kind") in STEP_KINDS else None
        other = self.find_step(label) if label else None
        if other and other.id != s.id:
            return _err(f"another step already has that label ({other.id})")
        changed = False
        if label and label != s.label:
            s.label, changed = label, True
        if kind and kind != s.kind:
            s.kind, changed = kind, True
        _add_ev(s.evidence, ev)
        if not changed:
            return Result(True, {"ok": True, "id": s.id, "note": "nothing changed"})
        s.updated_t = now
        return Result(True, {"ok": True, "id": s.id}, [{"kind": "step_updated", "id": s.id, "text": f"Step updated: {s.label}"}])

    def _t_report_bug(self, a: dict, lines, now) -> Result:
        s = self.find_step(a.get("step"))
        if not s:
            return _err("step must be an existing step id (add the step first)")
        text = clean_text(a.get("text"), TEXT_MAX)
        if not text:
            return _err("text is required")
        ev, why = check_quote(a.get("quote"), lines)
        if not ev:
            return _err(why)
        for b in self.bugs.values():
            if b.step == s.id and not b.fixed and _similar(b.text, text):
                _add_ev(b.evidence, ev)
                return Result(True, {"ok": True, "id": b.id, "note": "same bug already pinned"})
        if len(self.bugs) >= MAX_BUGS:
            return _err("too many bugs")
        b = Bug(id=self._id("b"), step=s.id, text=text, t=ev.t, evidence=[ev])
        self.bugs[b.id] = b
        s.updated_t = now
        return Result(True, {"ok": True, "id": b.id},
                      [{"kind": "bug", "id": b.id, "step": s.id, "text": f"Bug on {s.label}: {text}"}])

    def _t_flag_gap(self, a: dict, lines, now) -> Result:
        s = self.find_step(a.get("step"))
        if not s:
            return _err("step must be an existing step id")
        branch = clean_text(a.get("missing_branch"), LINK_LABEL_MAX)
        question = clean_text(a.get("question"), TEXT_MAX)
        if not branch or not question:
            return _err("missing_branch and question are required")
        question = question.rstrip(".!") + ("" if question.endswith("?") else "?")
        ev, why = check_quote(a.get("quote"), lines)
        if not ev:
            return _err(why)
        for g in self.gaps.values():
            if g.dismissed or not (_similar(g.branch, branch) or _similar(g.question, question)):
                continue
            if g.step == s.id or g.resolved:
                return _err(f"already flagged as {g.id}")
            old = g.step                       # same gap, better step: move it
            g.step = s.id
            _add_ev(g.evidence, ev)
            return Result(True, {"ok": True, "id": g.id, "note": "moved to this step"},
                          [{"kind": "gap_moved", "id": g.id, "step": s.id, "text": g.question, "from": old}])
        if self.gap_budget <= 0:
            return _err("gap limit for this round reached: keep only the most important gap")
        if len(self.open_gaps()) >= MAX_OPEN_GAPS or len(self.gaps) >= MAX_GAPS:
            return _err("too many open gaps: only flag one if it is more important than the open ones")
        g = Gap(id=self._id("g"), step=s.id, branch=branch, question=question, t=now, evidence=[ev])
        self.gaps[g.id] = g
        self.gap_budget -= 1
        return Result(True, {"ok": True, "id": g.id},
                      [{"kind": "gap", "id": g.id, "step": s.id, "text": question}], new_gap=g)

    def _t_flag_contradiction(self, a: dict, lines, now) -> Result:
        note = clean_text(a.get("note"), TEXT_MAX)
        if not note:
            return _err("note is required")
        ea, why = check_quote(a.get("quote_a"), lines)
        if not ea:
            return _err(f"quote_a: {why}")
        eb, why = check_quote(a.get("quote_b"), lines)
        if not eb:
            return _err(f"quote_b: {why}")
        if ea.line == eb.line:
            return _err("quote_a and quote_b must come from two different transcript lines")
        pair = {ea.line, eb.line}
        for c in self.contradictions.values():
            if {c.a.line, c.b.line} == pair:
                return _err(f"already flagged as {c.id}")
        if len(self.contradictions) >= MAX_CONTRADICTIONS:
            return _err("too many contradictions")
        s = self.find_step(a.get("step")) if a.get("step") else None
        if ea.line > eb.line:            # keep them in the order they were said
            ea, eb = eb, ea
        c = Contradiction(id=self._id("c"), step=s.id if s else None, note=note, a=ea, b=eb, t=now)
        self.contradictions[c.id] = c
        return Result(True, {"ok": True, "id": c.id},
                      [{"kind": "contradiction", "id": c.id, "step": c.step, "text": note}])

    def _t_resolve(self, a: dict, lines, now) -> Result:
        item = str(a.get("item_id") or "").strip()
        answer = clean_optional(a.get("answer"), TEXT_MAX)
        ev, why = check_quote(a.get("quote"), lines)
        if not ev:
            return _err(why)
        if item in self.gaps and not self.gaps[item].dismissed:
            g = self.gaps[item]
            if g.resolved:
                return Result(True, {"ok": True, "note": "already resolved"})
            g.resolved, g.answer, g.resolved_by = True, answer, ev
            return Result(True, {"ok": True}, [{"kind": "gap_resolved", "id": g.id, "step": g.step,
                                                "text": f"Gap answered: {g.question}"}], resolved_gap=g)
        if item in self.bugs:
            b = self.bugs[item]
            if not b.fixed:
                b.fixed, b.fix = True, ev
                return Result(True, {"ok": True}, [{"kind": "bug_fixed", "id": b.id, "step": b.step,
                                                    "text": f"Bug fixed: {b.text}"}])
            return Result(True, {"ok": True, "note": "already fixed"})
        if item in self.contradictions:
            c = self.contradictions[item]
            if not c.resolved:
                c.resolved, c.answer, c.resolved_by = True, answer, ev
                return Result(True, {"ok": True}, [{"kind": "contradiction_resolved", "id": c.id, "step": c.step,
                                                    "text": f"Settled: {c.note}"}])
            return Result(True, {"ok": True, "note": "already settled"})
        return _err("item_id must be an existing gap (g…), bug (b…) or contradiction (c…) id")

    # ---------- user actions (from the question card made from a gap) ----------
    def gap_by_card(self, card_id: str) -> Gap | None:
        return next((g for g in self.gaps.values() if g.card_id == card_id), None)

    def user_resolve_gap(self, g: Gap, resolved: bool, now: float) -> bool:
        if g.resolved == resolved:
            return False
        g.resolved = resolved
        if not resolved:
            g.answer, g.resolved_by = None, None
        return True


def _add_ev(lst: list[Evidence], ev: Evidence) -> None:
    if any(x.line == ev.line for x in lst):
        return
    lst.append(ev)
    del lst[:-MAX_EVIDENCE]


def _err(msg: str) -> Result:
    return Result(False, {"ok": False, "error": msg})
