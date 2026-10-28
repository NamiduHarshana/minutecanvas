"""Board state: cards in 5 columns, Spark ops, user edits."""
from __future__ import annotations

import re
import uuid
from dataclasses import asdict, dataclass, field

from .security import clean_optional, clean_text

COLUMNS = ("goals", "decisions", "actions", "updates", "questions")
UPDATE_STATUSES = ("done", "in_progress", "blocked")
MAX_CARDS = 200

TITLE_MAX = 80
DETAIL_MAX = 280
OWNER_MAX = 40
DUE_MAX = 40
ANSWER_MAX = 280
PROMPT_MAX = 300


@dataclass
class Card:
    id: str
    column: str
    title: str
    detail: str = ""
    owner: str | None = None
    due: str | None = None
    status: str | None = None        # updates: done / in_progress / blocked
    done: bool = False               # actions: ✓ done
    resolved: bool = False           # questions
    answer: str | None = None        # questions: how it was resolved
    t: float = 0.0                   # meeting time (s) when created
    updated_t: float | None = None   # meeting time (s) of last AI update
    image_url: str | None = None
    image_status: str = "none"       # none / pending / ready / failed / skipped
    source: str = "ai"               # ai / user
    user_locked: bool = False        # user edited it -> AI won't overwrite text
    flow_ref: str | None = None      # questions made from a Flow Map gap: the gap id ("g2")
    conflict_ref: str | None = None  # states one side of a Flow Map contradiction ("c1"): not settled
    image_prompt: str = ""

    def public(self) -> dict:
        d = asdict(self)
        d.pop("image_prompt", None)
        return d


def _parse_t(v, now_t: float) -> float:
    """"01:23" / "1:02:03" -> seconds; falls back to now if missing or in the future."""
    if isinstance(v, str):
        try:
            sec = 0.0
            for part in v.strip().strip("[]").split(":"):
                sec = sec * 60 + float(part)
            if 0 <= sec <= now_t + 1:
                return sec
        except ValueError:
            pass
    return now_t


def _norm(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", s.lower()) if len(w) > 2}


def _similar(a: str, b: str) -> bool:
    x, y = _norm(a), _norm(b)
    if not x or not y:
        return a.strip().lower() == b.strip().lower()
    return len(x & y) / len(x | y) >= 0.75


_NUM_WORDS = {w: str(i) for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen "
    "sixteen seventeen eighteen nineteen twenty".split())}
_NUM_WORDS.update({"thirty": "30", "forty": "40", "fifty": "50", "sixty": "60", "seventy": "70",
                   "eighty": "80", "ninety": "90", "hundred": "100", "thousand": "1000"})


def numbers(s: str) -> set[str]:
    """Numbers in a text, normalised ("$1,000" -> "1000", "five" -> "5"); used to tie cards to conflicts."""
    s = (s or "").lower()
    out = {m.replace(",", "") for m in re.findall(r"\d[\d,]*(?:\.\d+)?", s)}
    out.update(_NUM_WORDS[w] for w in re.findall(r"[a-z]+", s) if w in _NUM_WORDS)
    out.update(str(int(float(m) * 1000)) for m in re.findall(r"(\d+(?:\.\d+)?)\s*k\b", s))
    return out


def _is_big(n: str) -> bool:
    try:
        return float(n) >= 10
    except ValueError:
        return False


_STOP = {"the", "and", "for", "with", "into", "from", "their", "they", "user", "users", "customer",
         "customers", "will", "can", "should", "must", "then", "via", "using", "through", "each", "after"}


def restates_step(title: str, steps: list[str]) -> bool:
    """A card that only repeats a Flow Map step ("Submit the story map" vs step "Submit story map")."""
    if numbers(title):
        return False                     # prices, counts, dates are real facts, keep them
    t = _norm(title) - _STOP
    if len(t) < 2:
        return False
    for label in steps:
        st = _norm(label) - _STOP
        if len(st) >= 2 and st <= t and len(t - st) <= 1:
            return True
    return False

@dataclass
class BoardChange:
    added: list[Card] = field(default_factory=list)
    updated: list[Card] = field(default_factory=list)


class Board:
    def __init__(self) -> None:
        self.cards: dict[str, Card] = {}
        self.order: list[str] = []
        self.deleted_titles: list[str] = []   # told to Spark so it does not re-add them
        self.title: str | None = None

    # ---------- views ----------
    def public(self) -> dict:
        return {"title": self.title, "cards": [self.cards[i].public() for i in self.order if i in self.cards]}

    def for_spark(self) -> list[dict]:
        out = []
        for i in self.order:
            c = self.cards.get(i)
            if not c:
                continue
            item = {"id": c.id, "column": c.column, "title": c.title}
            if c.detail:
                item["detail"] = c.detail
            if c.owner:
                item["owner"] = c.owner
            if c.due:
                item["due"] = c.due
            if c.column == "updates" and c.status:
                item["status"] = c.status
            if c.column == "actions":
                item["done"] = c.done
            if c.column == "questions":
                item["resolved"] = c.resolved
            if c.conflict_ref:
                item["conflict"] = c.conflict_ref
            out.append(item)
        return out

    # ---------- AI ops ----------
    def _find_duplicate(self, column: str, title: str) -> Card | None:
        for i in self.order:
            c = self.cards.get(i)
            if c and c.column == column and _similar(c.title, title):
                return c
        return None

    def apply_spark(self, ops: dict, now_t: float, flow_steps: list[str] | None = None) -> BoardChange:
        ch = BoardChange()
        if not isinstance(ops, dict):
            return ch

        mt = clean_optional(ops.get("meeting_title"), 80)
        if mt and not self.title:
            self.title = mt

        for u in ops.get("updates") or []:
            if not isinstance(u, dict):
                continue
            c = self.cards.get(str(u.get("id", "")))
            if c and self._apply_update(c, u, now_t) and c not in ch.updated:
                ch.updated.append(c)

        for r in ops.get("resolved_questions") or []:
            if not isinstance(r, dict):
                continue
            c = self.cards.get(str(r.get("id", "")))
            if c and c.column == "questions" and not c.resolved:
                c.resolved = True
                c.answer = clean_optional(r.get("answer"), ANSWER_MAX)
                c.updated_t = now_t
                if c not in ch.updated:
                    ch.updated.append(c)

        for n in ops.get("new_cards") or []:
            if not isinstance(n, dict) or len(self.cards) >= MAX_CARDS:
                continue
            column = n.get("column")
            title = clean_text(n.get("title"), TITLE_MAX)
            if column not in COLUMNS or not title:
                continue
            if any(_similar(t, title) for t in self.deleted_titles):
                continue
            if column in ("goals", "decisions", "actions") and flow_steps and restates_step(title, flow_steps):
                continue                 # already a box on the Flow Map
            dup = self._find_duplicate(column, title)
            if dup:  # same topic -> merge into the existing card instead of a 2nd card
                if self._apply_update(dup, n, now_t) and dup not in ch.updated and dup not in ch.added:
                    ch.updated.append(dup)
                continue
            status = n.get("status") if column == "updates" and n.get("status") in UPDATE_STATUSES else None
            c = Card(
                id=uuid.uuid4().hex[:12],
                column=column,
                title=title,
                detail=clean_text(n.get("detail"), DETAIL_MAX),
                owner=clean_optional(n.get("owner"), OWNER_MAX),
                due=clean_optional(n.get("due"), DUE_MAX),
                status=status,
                t=round(_parse_t(n.get("said_at"), now_t), 1),
                image_prompt=clean_text(n.get("image_prompt"), PROMPT_MAX) or title,
            )
            self.cards[c.id] = c
            self.order.append(c.id)
            ch.added.append(c)
        return ch

    def _apply_update(self, c: Card, u: dict, now_t: float) -> bool:
        changed = False
        if not c.user_locked:
            for key, mx in (("title", TITLE_MAX), ("detail", DETAIL_MAX)):
                v = clean_text(u.get(key), mx) if u.get(key) else ""
                if v and v != getattr(c, key):
                    setattr(c, key, v)
                    changed = True
            for key, mx in (("owner", OWNER_MAX), ("due", DUE_MAX)):
                v = clean_optional(u.get(key), mx)
                if v and v != getattr(c, key):
                    setattr(c, key, v)
                    changed = True
        if c.column == "updates" and u.get("status") in UPDATE_STATUSES and u.get("status") != c.status:
            c.status = u["status"]
            changed = True
        if c.column == "actions" and u.get("action_done") is True and not c.done:
            c.done = True
            changed = True
        if changed:
            c.updated_t = round(now_t, 1)
        return changed

    # ---------- user edits (board is append-only for AI; user can edit/delete) ----------
    def user_edit(self, card_id: str, fields: dict) -> Card | None:
        c = self.cards.get(card_id)
        if not c or not isinstance(fields, dict):
            return None
        if "title" in fields:
            t = clean_text(fields["title"], TITLE_MAX)
            if t:
                c.title = t
        if "detail" in fields:
            c.detail = clean_text(fields["detail"], DETAIL_MAX)
        if "owner" in fields:
            c.owner = clean_optional(fields["owner"], OWNER_MAX)
        if "due" in fields:
            c.due = clean_optional(fields["due"], DUE_MAX)
        if "status" in fields and c.column == "updates" and fields["status"] in UPDATE_STATUSES:
            c.status = fields["status"]
        if "done" in fields and c.column == "actions":
            c.done = bool(fields["done"])
        if "resolved" in fields and c.column == "questions":
            c.resolved = bool(fields["resolved"])
        c.user_locked = True
        return c

    def user_delete(self, card_id: str) -> bool:
        c = self.cards.pop(card_id, None)
        if not c:
            return False
        self.order.remove(card_id)
        self.deleted_titles.append(c.title)
        return True

    # ---------- Flow Map gaps -> Open questions ----------
    def add_gap_question(self, gap_id: str, question: str, detail: str, t: float) -> tuple[Card | None, bool]:
        """Returns (card, is_new). Reuses an open question that already asks the same thing."""
        title = clean_text(question, TITLE_MAX)
        if not title or any(_similar(x, title) for x in self.deleted_titles):
            return None, False
        dup = self._find_duplicate("questions", title)
        if dup and not dup.resolved and not dup.flow_ref:
            dup.flow_ref = gap_id
            return dup, False
        if len(self.cards) >= MAX_CARDS:
            return None, False
        c = Card(id=uuid.uuid4().hex[:12], column="questions", title=title,
                 detail=clean_text(detail, DETAIL_MAX), t=round(t, 1), flow_ref=gap_id,
                 image_prompt="a winding path with one missing piece, a small question mark sign beside it")
        self.cards[c.id] = c
        self.order.append(c.id)
        return c, True

    def resolve_gap_question(self, card_id: str | None, answer: str | None, now_t: float) -> Card | None:
        c = self.cards.get(card_id or "")
        if not c or c.resolved:
            return None
        c.resolved = True
        c.answer = answer or c.answer
        c.updated_t = round(now_t, 1)
        return c

    # ---------- Flow Map contradictions -> cards ----------
    def link_conflicts(self, conflicts: list) -> list[Card]:
        """Tag cards that state one side of an open contradiction (said on one of its two lines and sharing
        a number with it). Such a card is not a settled decision. Returns the cards that changed."""
        changed: list[Card] = []
        open_ids = {c.id for c in conflicts if not c.resolved}
        for c in self.cards.values():                 # a settled conflict frees its cards
            if c.conflict_ref and c.conflict_ref not in open_ids:
                c.conflict_ref = None
                changed.append(c)
        for k in conflicts:
            if k.resolved:
                continue
            for ev in (k.a, k.b):
                nums = numbers(ev.quote)
                big = {n for n in nums if _is_big(n)}      # "1400", "$250": specific enough to match from nearby lines
                for c in self.cards.values():
                    if c.conflict_ref or c.column == "questions":
                        continue
                    dt = abs(c.t - ev.t)
                    shared = nums & numbers(f"{c.title} {c.detail}")
                    if (dt <= 2.5 and shared) or (dt <= 30 and shared & big):
                        c.conflict_ref = k.id
                        if c not in changed:
                            changed.append(c)
        return changed

    def pending_images(self) -> int:
        return sum(1 for c in self.cards.values() if c.image_status == "pending")
