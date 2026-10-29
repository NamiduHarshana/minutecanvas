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


# ---------- "what happens after X?" gaps the transcript already answers ----------
# "we provide feedback and then like schedule a session": the line itself says what comes next.
_NEXT_MARK = re.compile(r"\b(?:and then|then|after that|afterwards|after which|followed by|once thats done)\b")
NEXT_WINDOW = 10      # words before a "then" that must name the step ("…provide feedback and then …")
_AFTER_GAP = re.compile(r"\b(?:after|next|then|afterwards|once)\b")
_FAIL_GAP = re.compile(r"\b(?:fail\w*|wrong|reject\w*|declin\w*|cancel\w*|timeout|time out|expire\w*|miss\w*|refund\w*|"
                       r"not|no|never|doesnt|dont|cant|pay\w*|price|fee|cost|error|invalid|late|lost)\b")
_FILLER = {"like", "just", "basically", "actually", "really", "kind", "sort", "maybe", "also", "they", "them", "their",
           "theyre", "well", "you", "your", "will", "would", "can", "could", "going", "gonna", "that", "this",
           "there", "then", "the", "and", "for", "with", "into", "from", "some", "okay", "yeah", "right", "see", "figure",
           "decide", "discuss", "talk", "later", "out", "something", "stuff", "thing", "things", "know", "think", "get",
           "gets", "got", "have", "has", "had", "are", "was", "were", "does", "its", "user", "users", "customer",
           "customers", "she", "him", "her", "his", "our", "able", "need", "needs", "want", "wants", "should", "must",
           "what", "when", "where", "how", "who", "now", "here", "all", "any", "one", "next", "first", "last",
           "say", "said", "says", "let", "lets", "make", "sure"}


def _same_ask(a: str, b: str) -> bool:
    """Two gap questions that ask the same thing in other words ("…after the mock interviews?" / "…after the mock
    interview?"): same content words once plurals and verb endings are cut."""
    x, y = set(_stems(_content(tokens(a)))), set(_stems(_content(tokens(b))))
    if len(x) < 2 or len(y) < 2:
        return False
    return len(x & y) / len(x | y) >= 0.7


def _content(words: list[str]) -> set[str]:
    return {w for w in words if len(w) > 2 and w not in _FILLER}


def is_after_gap(branch: str, question: str) -> bool:
    """A "what comes next" gap (not a failure / money / drop-off branch)."""
    text = " ".join(tokens(f"{branch} {question}"))
    return bool(_AFTER_GAP.search(text)) and not _FAIL_GAP.search(text)


_PAY_GAP = re.compile(r"\b(?:how (?:do|does|will|can|should|would) .{0,40}\bpay\b|payment method|how .{0,30}\b(?:charged|billed)|"
                      r"pay for|how pay)\b")


def is_pay_gap(branch: str, question: str) -> bool:
    """"How do customers pay the $100?" (not "What if the payment fails?", which is a different branch)."""
    text = " ".join(tokens(f"{branch} {question}"))
    return bool(_PAY_GAP.search(text)) and not re.search(r"\b(?:fail\w*|declin\w*|refund\w*|cancel\w*)\b", text)


def said_next(text: str, label: str, own_quotes: list[str] | None = None) -> str | None:
    """The words after "then …" in a line, when the step is named before the "then" and the words after it
    name something else ("provide feedback and then schedule a session" for step "Receive feedback").
    own_quotes: the step's own evidence; the words before any "then" in them also describe the step itself
    (so "…then we'll get back to them with the exact time" is not "what comes after" step "Confirm exact time")."""
    flat = " ".join(tokens(text))
    name = set(_stems(_content(tokens(label))))
    if not name:
        return None
    own = set(name)
    for q in own_quotes or []:
        qf = " ".join(tokens(q))
        m = _NEXT_MARK.search(qf)
        own |= set(_stems(_content((qf[:m.start()] if m else qf).split())))
    marks = list(_NEXT_MARK.finditer(flat))
    for k in range(len(marks) - 1, -1, -1):
        m = marks[k]
        before = set(_stems(flat[:m.start()].split()[-NEXT_WINDOW:]))   # the step is named just before "then"
        end = marks[k + 1].start() if k + 1 < len(marks) else len(flat)   # up to the next "then"
        rest = flat[m.end():end].split()[:8]
        if before & name and len(rest) >= 2 and set(_stems(_content(rest))) - own:
            return " ".join(rest)
    return None


# ---------- numbers and what they count (contradiction checks and hints) ----------
_NUM_WORDS = {w: i for i, w in enumerate(
    "zero _ two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen "
    "seventeen eighteen nineteen twenty".split()) if w != "_"}           # "one" left out: "one-on-one", "no one"
_NUM_WORDS.update({"thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "hundred": 100, "thousand": 1000})
_UNIT_SKIP = {"of", "those", "these", "the", "a", "an", "per", "each", "more", "like", "um", "uh", "and", "or", "to",
              "for", "at", "in", "on", "about", "around", "maybe", "so", "extra", "other", "different", "again",
              "instead", "total", "only", "plus", "then", "also", "including", "included"}
_MONEY = {"dollar", "dollars", "bucks", "usd", "rupees", "rs", "lkr", "euro", "euros", "pounds"}
TIME_UNITS = {"minut", "hour", "day", "week", "month", "year", "second"}


def number_facts(text: str) -> list[tuple[float, set[str]]]:
    """[(value, units)] for each number in a line: "$200 per session" -> (200, {money, session}),
    "four of those stories" -> (4, {stori}), "make it 1400" -> (1400, {money}) (big bare numbers are prices)."""
    toks = re.findall(r"\$?\d[\d,]*(?:\.\d+)?k?|[a-z]+", (text or "").lower().translate(_QUOTES))
    out: list[tuple[float, set[str]]] = []
    for i, t in enumerate(toks):
        val = None
        if t[0] in "$0123456789":
            try:
                raw = t.lstrip("$").replace(",", "")
                val = float(raw[:-1]) * 1000 if raw.endswith("k") else float(raw)
            except ValueError:
                continue
        elif t in _NUM_WORDS:
            val = float(_NUM_WORDS[t])
        if val is None:
            continue
        units: set[str] = set()
        if t.startswith("$"):
            units.add("money")
        skipped, nouns, per = 0, 0, False
        for w in toks[i + 1:i + 6]:
            if w[0] in "$0123456789" or w in _NUM_WORDS:
                break
            if w in _MONEY:
                units.add("money")
                continue
            if w == "per" and not nouns:
                per = True               # "$200 per session": a rate, not a count of sessions
                continue
            if w in _UNIT_SKIP or len(w) < 3 or w in _FILLER:
                if nouns:                # "five sessions you are…": the counted thing ends at the first filler
                    break
                skipped += 1
                if skipped >= 3:
                    break
                continue
            units.update(("per:" + x) if per else x for x in _stems([w]))   # "five review sessions": up to two words
            nouns += 1
            if nouns >= 2 or per:
                break
        if per or (val >= 100 and not (units - {"money"})):
            units.add("money")
        out.append((val, units))
    return out


def units_of(text: str) -> set[str]:
    return set().union(*[u for _, u in number_facts(text)]) if number_facts(text) else set()


def number_changes(older: dict, newer: dict) -> str | None:
    """A unit counted with different numbers in two lines, e.g. "five sessions … $1,000" then "seven sessions … 1400".
    Only units that come with prices in both lines, or time units, so two unrelated counts are not suggested."""
    fa, fb = number_facts(older.get("text", "")), number_facts(newer.get("text", ""))
    if not fa or not fb:
        return None
    money_a = any("money" in u for _, u in fa)
    money_b = any("money" in u for _, u in fb)
    for unit in sorted(set().union(*[u for _, u in fa]) & set().union(*[u for _, u in fb]) - {"money"}):
        va = {v for v, u in fa if unit in u}
        vb = {v for v, u in fb if unit in u}
        if va and vb and not (va & vb) and (unit in TIME_UNITS or (money_a and money_b)):
            return unit
    return None


def _stems(words) -> list[str]:
    """Very small stemmer: map / mapping / maps, submit / submitted, schedule / scheduling."""
    out = []
    for w in words:
        for suf in ("ions", "ion", "ings", "ing", "ed", "es", "s"):
            if len(w) - len(suf) >= 3 and w.endswith(suf):
                w = w[: -len(suf)]
                break
        if len(w) >= 5 and w.endswith("e"):        # schedule -> schedul (= scheduling)
            w = w[:-1]
        if len(w) >= 4 and w[-1] == w[-2]:         # submitt -> submit, mapp -> map
            w = w[:-1]
        out.append(w)
    return out


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
        self.hinted: set[frozenset] = set()   # number-change line pairs already suggested to the agent

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

    def hints(self, new: list[dict], older: list[dict], max_changes: int = 3) -> dict:
        """Things code noticed in the new lines, for the agent to check (it decides, code never flags by itself):
        - number_changes: an older line and a new line that count the same thing with different numbers
          (the model missed "five sessions for $1,000" -> "seven sessions, make it 1400" in one live run);
        - gap_answers: a new line that says what comes after the step of an open "what happens after" gap."""
        flagged = {frozenset((c.a.line, c.b.line)) for c in self.contradictions.values()} | self.hinted
        changes: list[tuple[dict, dict, str]] = []
        for b in reversed(new):
            for a in reversed(older + [x for x in new if x.get("id", 0) < b.get("id", 0)]):
                if frozenset((a.get("id"), b.get("id"))) in flagged:
                    continue
                unit = number_changes(a, b)
                if unit:
                    changes.append((a, b, unit))
                    self.hinted.add(frozenset((a.get("id"), b.get("id"))))   # each pair is suggested once
                    break                       # one older line per new line is enough
            if len(changes) >= max_changes:
                break
        answers: list[tuple[Gap, dict]] = []
        for g in self.open_gaps():
            s = self.steps.get(g.step)
            if not s or not is_after_gap(g.branch, g.question):
                continue
            last_ev = max((e.line for e in s.evidence + g.evidence), default=-1)
            for x in new:
                if x.get("id", -1) > last_ev and said_next(x.get("text", ""), s.label, [e.quote for e in s.evidence]):
                    answers.append((g, x))
                    break
        return {"number_changes": changes, "gap_answers": answers}

    def _said_next(self, s: Step, ev: Evidence, lines: list[dict]) -> str | None:
        """Does the gap's own line, or a line the step came from, already say what happens next?"""
        by_id = {int(x.get("id", -1)): x for x in lines}
        seen: set[int] = set()
        for e in [ev, *s.evidence]:
            if e.line in seen:
                continue
            seen.add(e.line)
            text = by_id.get(e.line, {}).get("text") or e.quote
            nxt = said_next(text, s.label, [x.quote for x in s.evidence])
            if nxt:
                return nxt
        return None

    def _step_like(self, words: str, not_this: Step) -> Step | None:
        """An existing step the "then …" words point to (shares most of its words)."""
        w = set(_stems(_content(tokens(words))))
        best, best_n = None, 0
        for st in self.steps.values():
            if st.id == not_this.id:
                continue
            lab = set(_stems(_content(tokens(st.label))))
            n = len(lab & w)
            if lab and n * 2 >= len(lab) and n > best_n:
                best, best_n = st, n
        return best

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
        if not s and self.steps and not demoted and kind != "start" and not src:
            # a plain step with no arrow into it breaks the flow: say where it fits, or wait.
            # (A second "start" = a separate process people began describing: kept, and listed as unlinked.)
            if after:
                return _err(f"step {after!r} not found: 'after' must be an existing step id, or the label of a step "
                            "added earlier in this batch")
            return _err("say where this step fits: add after = the step that comes before it. If the transcript "
                        "doesn't say yet, don't add it now")
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
        if is_after_gap(branch, question):
            nxt = self._said_next(s, ev, lines)
            if nxt:
                hint = self._step_like(nxt, s)
                todo = f"link_steps from {s.id} to {hint.id} ({hint.label})" if hint else f"add_step with after = {s.id}"
                return _err(f"not a gap: the transcript already says what comes after {s.label!r} "
                            f"(\"…then {nxt}\"). Map it instead: {todo}")
        for g in self.gaps.values():
            if g.dismissed or not (_similar(g.branch, branch) or _similar(g.question, question) or _same_ask(g.question, question)):
                continue
            if g.step == s.id or g.resolved:
                return _err(f"already flagged as {g.id}")
            old = g.step                       # same gap, better step: move it
            g.step = s.id
            _add_ev(g.evidence, ev)
            return Result(True, {"ok": True, "id": g.id, "note": "moved to this step"},
                          [{"kind": "gap_moved", "id": g.id, "step": s.id, "text": g.question, "from": old}])
        if is_pay_gap(branch, question):
            other = next((g for g in self.open_gaps() if is_pay_gap(g.branch, g.question)), None)
            if other:
                return _err(f"there is already an open payment gap ({other.id}: {other.question!r}): ask how people pay "
                            "once for the whole flow, not again for each product or price")
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
        # what the numbers are about ("$200 per session" and "five sessions" are both about sessions)
        ua = {u.removeprefix("per:") for u in units_of(ea.quote)} - {"money"}
        ub = {u.removeprefix("per:") for u in units_of(eb.quote)} - {"money"}
        if ua and ub and not (ua & ub):
            return _err("the numbers count different things (" + ", ".join(sorted(ua)) + " vs " + ", ".join(sorted(ub))
                        + "): a contradiction is two values for the SAME fact")
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
