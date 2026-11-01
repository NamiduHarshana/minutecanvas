"""Live Flow Map: steps, links, bugs, gaps and contradictions built by the Spark Flow agent.

Every tool call must carry a quote. The quote is checked against the real transcript
(check_quote) before the call is accepted; who said it and when come from that transcript
line, never from the model. Rejected calls get an error back so the agent can fix them.
"""
from __future__ import annotations

import re
import os
from dataclasses import asdict, dataclass, field
from difflib import SequenceMatcher

from .security import clean_optional, clean_text

STEP_KINDS = ("start", "step", "decision", "end")
LINK_TYPES = ("normal", "fail")

# v6.9 bench: the 32-min leetcode meeting hit 40 steps at 26:32 and the last 7 steps said after that were refused
# ("Express time frames", "Schedule one-on-one session"...). The big-map layout (connector chips, follow zoom) is
# what keeps a long map readable, not this cap; it only protects the prompt size.
MAX_STEPS = int(os.environ.get("MAX_FLOW_STEPS", "80") or 80)
MAX_LINKS = 2 * MAX_STEPS
MAX_BUGS = 30
MAX_OPEN_GAPS = 5
MAX_GAPS = 40
MAX_CONTRADICTIONS = 20
MAX_EVIDENCE = 5

LABEL_MAX = 40
PART_MAX = 30
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


_STRONG_FAIL = re.compile(r"\b(?:fail\w*|wrong|reject\w*|declin\w*|cancel\w*|timeout|time out|expire\w*|miss\w*|"
                          r"refund\w*|not|no|never|doesnt|dont|cant|error|invalid|late|lost)\b")
_NEXT_ASK = re.compile(r"\b(?:what|where)\b.{0,30}\b(?:after|next|then|once)\b|^(?:after|next)\b")


def is_next_gap(branch: str, question: str) -> bool:
    """ "What happens after X?" also when X is about money ("…after the session fee is charged?"): an arrow out of the
    step answers it. Failure / drop-off branches never are."""
    b, q = " ".join(tokens(branch)), " ".join(tokens(question))
    if _STRONG_FAIL.search(f"{b} {q}"):
        return False
    return is_after_gap(branch, question) or bool(_NEXT_ASK.search(q) or _NEXT_ASK.search(b))


def is_after_gap(branch: str, question: str) -> bool:
    """A "what comes next" gap (not a failure / money / drop-off branch)."""
    text = " ".join(tokens(f"{branch} {question}"))
    return bool(_AFTER_GAP.search(text)) and not _FAIL_GAP.search(text)


_PAY_GAP = re.compile(r"\b(?:how (?:do|does|will|can|should|would) .{0,40}\bpay\b|payment method|how .{0,30}\b(?:charged|billed)|"
                      r"pay for|how pay)\b")


_PAY_WORD = re.compile(r"\b(?:pay\w*|charg\w*|card|deposit|fee|bill\w*|checkout|transaction|transfer)\b")


def is_pay_fail_gap(branch: str, question: str) -> bool:
    """"What if the deposit payment fails?" / "…if the card is declined?" (v6.7.2 bench: the final pass spent its
    second gap on "remaining balance payment fails" next to "deposit payment fails", one decision for the team, and
    the real drop-off gap "owner doesn't show up" was never asked)."""
    text = " ".join(tokens(f"{branch} {question}"))
    return bool(_PAY_WORD.search(text)) and bool(re.search(r"\b(?:fail\w*|declin\w*|bounce\w*|reject\w*)\b", text))


def is_pay_gap(branch: str, question: str) -> bool:
    """"How do customers pay the $100?" (not "What if the payment fails?", which is a different branch)."""
    text = " ".join(tokens(f"{branch} {question}"))
    return bool(_PAY_GAP.search(text)) and not re.search(r"\b(?:fail\w*|declin\w*|refund\w*|cancel\w*)\b", text)


# "…placement network. Then I want to show that we tell them…": the speaker's own next point, not the user's next step
# (v6.6 bench: this blocked the gap "What happens after tapping the placement network?" twice and told the agent to
# add a step "i want to show that we are tell").
_PRESENTER = re.compile(r"^(?:so |um |uh |like |also )*(?:i|we)(?: really| just| also| kind of| basically)? "
                        r"(?:want|wanted|would like|d like|need|have|am going|was going|m going|are going)"
                        r" to(?: kind of| basically| also| just)? (?:show|tell|add|talk|say|explain|discuss|highlight|"
                        r"mention|cover|present|describe|go over|walk)\b|^(?:let me|lets) (?:show|talk|discuss|go|move)\b")


_DISCOURSE = {"finally", "final", "eventually", "lastly", "ultimately", "essentially", "afterwards", "afterward", "step"}


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
        # v6.13 bench: the step was drawn from the words AFTER a "then" in its own quote ("…system designing and
        # then you have follow-on practice sessions and mock interviews" -> "System mock interviews"; "…if they
        # click on that then take them to that page where we have all the network thing" -> "See network info
        # page"). Those words are the step itself, not what comes after it.
        cuts = [0] + [x.end() for x in _NEXT_MARK.finditer(qf)] + [len(qf)]
        segs = [set(_stems(_content(qf[cuts[i]:cuts[i + 1]].split()))) for i in range(len(cuts) - 1)]
        best = max((len(sg & name) for sg in segs), default=0)
        if best:
            for sg in segs:
                if len(sg & name) == best:
                    own |= sg
    marks = list(_NEXT_MARK.finditer(flat))
    for k in range(len(marks) - 1, -1, -1):
        m = marks[k]
        before = set(_stems(flat[:m.start()].split()[-NEXT_WINDOW:]))   # the step is named just before "then"
        end = marks[k + 1].start() if k + 1 < len(marks) else len(flat)   # up to the next "then"
        rest = flat[m.end():end].split()[:8]
        # v6.15 run: "…and then finally what you do is you" (the sentence goes on in the next line) is not a next step
        if (before & name and len(rest) >= 2 and set(_stems(_content(rest))) - own - _DISCOURSE
                and not _PRESENTER.search(" ".join(rest))):
            return " ".join(rest)
    return None


# ---------- numbers and what they count (contradiction checks and hints) ----------
_NUM_WORDS = {w: i for i, w in enumerate(
    "zero _ two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen "
    "seventeen eighteen nineteen twenty".split()) if w != "_"}           # "one" left out: "one-on-one", "no one"
_NUM_WORDS.update({"thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "hundred": 100, "thousand": 1000})

# Spoken numbers, compounds joined (v6.7.2 bench: "twenty four hours" was read as 20 and 4, so the 24-vs-12 hours
# contradiction never matched the card "Free cancellation up to 24 hours").
_SMALL = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen "
    "seventeen eighteen nineteen".split())}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}
_SCALE = {"hundred": 100, "thousand": 1000}


def _below_100(words: list[str], i: int) -> tuple[int, int] | None:
    """"twenty four" / "twenty" / "seven" at i -> (value, next index)."""
    if i >= len(words):
        return None
    w = words[i]
    if w in _TENS:
        if i + 1 < len(words) and words[i + 1] in _SMALL and 0 < _SMALL[words[i + 1]] < 10:
            return _TENS[w] + _SMALL[words[i + 1]], i + 2
        return _TENS[w], i + 1
    if w in _SMALL:
        return _SMALL[w], i + 1
    return None


def spoken_numbers(words: list[str], lone_one: bool = True) -> list[tuple[int, int, int]]:
    """[(start, end, value)] for the number words in a token list: "twenty four" -> 24, "fourteen hundred" -> 1400,
    "two hundred and fifty" -> 250, "a thousand" -> 1000, "five" -> 5. lone_one=False skips a bare "one"
    ("one-on-one", "no one"); "one hundred" still counts."""
    out: list[tuple[int, int, int]] = []
    i = 0
    while i < len(words):
        w = words[i]
        if w == "a" and i + 1 < len(words) and words[i + 1] in _SCALE:
            first = (1, i + 1)
        else:
            first = _below_100(words, i)
        if first is None:
            if w in _SCALE:                                    # "hundred" alone
                out.append((i, i + 1, _SCALE[w]))
            i += 1
            continue
        val, j = first
        total = 0
        while j < len(words) and words[j] in _SCALE:            # "fourteen hundred", "two thousand five hundred"
            total += val * _SCALE[words[j]]
            j += 1
            if j < len(words) and words[j] == "and" and _below_100(words, j + 1):
                j += 1
            nxt = _below_100(words, j)
            if nxt is None:
                val = 0
                break
            val, j = nxt
        total += val
        if j == i + 1 and w == "one" and not lone_one:
            i += 1
            continue
        out.append((i, j, total))
        i = j
    return out
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
    spoken = {a: (b, v) for a, b, v in spoken_numbers(toks, lone_one=False)}
    inside = {k for a, (b, _) in spoken.items() for k in range(a + 1, b)}
    for i, t in enumerate(toks):
        val, nxt = None, i + 1
        if i in inside:
            continue                     # "four" of "twenty four"
        if t[0] in "$0123456789":
            try:
                raw = t.lstrip("$").replace(",", "")
                val = float(raw[:-1]) * 1000 if raw.endswith("k") else float(raw)
            except ValueError:
                continue
        elif i in spoken:
            nxt, v = spoken[i]
            val = float(v)
        if val is None:
            continue
        units: set[str] = set()
        if t.startswith("$"):
            units.add("money")
        skipped, nouns, per = 0, 0, False
        for w in toks[nxt:nxt + 5]:
            if w[0] in "$0123456789" or w in _NUM_WORDS or w in _TENS:
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


def key_words(s: str) -> set[str]:
    """Content-word stems of a label / question / line (for word-overlap checks)."""
    return set(_stems(_content(tokens(s or ""))))


_REPLACE_CUE = re.compile(r"\b(?:instead|replac\w*|rather than|no longer|not anymore|anymore|switch\w*|chang\w*|"
                          r"swap\w*|move to|moving to|use .{0,30} not|better to)\b")


def says_replace(text: str) -> bool:
    """The line says one thing replaces another ("instead of a Trello board … a form")."""
    return bool(_REPLACE_CUE.search(" ".join(tokens(text))))


_REPLACED = re.compile(r"\b(?:instead of|rather than|no longer use|not use|replace|replacing)\s+((?:\w+\s*){1,7})")
_PHRASE_END = {"because", "but", "so", "and", "then", "since", "as", "which", "that", "it", "we", "i", "you", "they",
               "maybe", "with", "for", "if", "or", "now", "anymore"}


_GERUND_NEXT = {"them", "it", "the", "a", "an", "to", "our", "their", "people", "users", "customers", "with", "on", "in"}


def replaced_things(text: str) -> list[set[str]]:
    """Key words of what a line says is replaced: "instead of linking them to the Trello board because…" ->
    {trello, board}; "instead of a tarot board it might be better…" -> {tarot, board}."""
    flat = " ".join(tokens(text))
    out = []
    for m in _REPLACED.finditer(flat):
        words = m.group(1).split()
        if len(words) > 1 and words[0].endswith("ing") and words[1] in _GERUND_NEXT:
            words = words[1:]                             # "linking them to the board": the thing comes after
        phrase = []
        for w in words:
            if w in _PHRASE_END:
                break
            phrase.append(w)
        kw = key_words(" ".join(phrase))
        if kw:
            out.append(kw)
    return out


def with_usual_names(words: set[str], earlier: list[dict]) -> set[str]:
    """The replaced thing's words plus the name the meeting usually gives it, when a word of it was never said before
    (the ASR misheard it). v6.7.2 bench: "instead of a tarot board … a form" ("tarot" = "Trello"): the cards were
    matched only on "board", so the two cards that said "Trello questions" / "the Trello … workflow" were never listed
    and kept naming the dropped Trello board. The meeting said "Trello board" 8 times before: "board" usually comes
    after "trello", so "trello" is added."""
    seen = [set(_stems(tokens(x.get("text", "")))) for x in earlier]
    rare = {w for w in words if not any(w in s for s in seen)}
    if not rare:
        return set(words)
    out = set(words)
    for head in words - rare:
        before: dict[str, int] = {}
        for x in earlier:
            toks = _stems(tokens(x.get("text", "")))
            for k in range(1, len(toks)):
                m = toks[k - 1]
                if toks[k] == head and m not in words and m not in _FILLER and len(m) > 2 and m not in _UNIT_SKIP:
                    before[m] = before.get(m, 0) + 1
        if before:
            m, n = max(before.items(), key=lambda kv: (kv[1], kv[0]))
            if n >= 2:
                out.add(m)
    return out


# v6.8 bench (music school): the agent added the new "See announcements" step, the rename of the old WhatsApp step to
# it was refused as a duplicate label, so it renamed the old step "WhatsApp group removed". A box names what the user
# does; a replaced thing is renamed (or merged into its new version), never kept as a "removed" box.
# only as the status word at the end ("WhatsApp group removed") or "no longer" / "not used" anywhere:
# "Restore deleted account", "Refund unused sessions", "Retry dropped payment" are real steps
_DEAD_LABEL = re.compile(r"(?:\b(?:removed|deleted|dropped|replaced|discontinued|retired|obsolete|deprecated|unused)$"
                         r"|\bno longer\b|\bnot used\b)")


def dead_label(label: str) -> bool:
    return bool(_DEAD_LABEL.search(" ".join(tokens(label))))


RENAME_FAR_S = 60.0   # a rename this long after the step was last talked about, with no shared word, is another step
# v6.5 run: a gap check flagged "Who reviews the Trello board?" 14 s after the step was last talked about; the answer
# came in the very next line. A step the group is still talking about is not a gap yet in live rounds and automatic
# checks (the "Check gaps" button and the final pass still may flag it).
RECENT_TOPIC_S = 90.0
_SIDE_BRANCH = re.compile(r"\b(?:if|when|unless|in case|otherwise)\b")
CONTINUE_S = 20.0     # ASR splits one sentence over lines: "…solve it and then" | "then their peers critique the code"


def with_next(lines: list[dict], line_id: int, n: int = 2) -> str:
    """The text of a line plus up to n following lines of the same speaker said right after it (one sentence that the
    ASR cut into several lines)."""
    i = next((k for k, x in enumerate(lines) if x.get("id") == line_id), None)
    if i is None:
        return ""
    text = [lines[i].get("text", "")]
    sp, last_t = lines[i].get("speaker"), float(lines[i].get("t", 0))
    for x in lines[i + 1:i + 1 + 4]:
        if len(text) > n:
            break
        if x.get("speaker") != sp:
            if len(tokens(x.get("text", ""))) <= 2:     # "Mm-hmm" from the other person doesn't end the sentence
                continue
            break
        if float(x.get("t", 0)) - last_t > CONTINUE_S:
            break
        text.append(x.get("text", ""))
        last_t = float(x.get("t", 0))
    return " ".join(text)


@dataclass
class Step:
    id: str
    label: str
    kind: str
    t: float
    evidence: list[Evidence] = field(default_factory=list)
    updated_t: float | None = None
    # the part of the product it belongs to ("Behavioral coaching"): the map draws one band per part. v6.5 run: without
    # it, a website with five sections became a star of arrows out of the first step.
    part: str | None = None


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
    dismissed: bool = False          # the user deleted its question card (or the re-check found it answered before)
    card_id: str | None = None       # the Open questions card made from this gap
    card_owned: bool = False         # that card was made for this gap (not an existing question it was tied to)
    user_set: bool = False           # the user resolved / reopened it: the automatic re-check leaves it alone
    removed_why: str | None = None   # the re-check found it was answered before it was flagged


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
        self.mode = "live"           # round mode (session sets it): the final pass may flag gaps on the last topic
        self.latest_t: float | None = None    # time of the newest transcript line said so far (session sets it)
        self.hinted: set[frozenset] = set()   # number-change line pairs already suggested to the agent
        # steps added in the current round: not shown to anyone yet, so they can still be merged away without
        # changing the fixed number of any box on screen (session resets it every round)
        self.round_new: set[str] = set()
        # the first step of each part, as it was when the part began (a merge may move an older step into a part)
        self.part_heads: dict[str, str] = {}

    def _id(self, p: str) -> str:
        self._n[p] += 1
        return f"{p}{self._n[p]}"

    # ---------- views ----------
    def public(self, shown: bool = False) -> dict:
        """shown = what goes to the browser: steps added in a round that is still running stay hidden until it ends
        (a merge may still take one away, and its fixed number must never have been on screen)."""
        hide = self.round_new if shown else set()
        return {
            "steps": [asdict(x) for x in self.steps.values() if x.id not in hide],
            "links": [asdict(x) for x in self.links.values() if x.src not in hide and x.dst not in hide],
            "bugs": [asdict(x) for x in self.bugs.values() if x.step not in hide],
            "gaps": [asdict(x) for x in self.gaps.values() if not x.dismissed and x.step not in hide],
            "contradictions": [dict(asdict(x), step=None if x.step in hide else x.step)
                               for x in self.contradictions.values()],
        }

    def empty(self) -> bool:
        return not self.steps

    def unlinked(self) -> list[Step]:
        """Steps nobody leads into (except start steps and the first step of each part): the flow is broken there."""
        has_in = {k.dst for k in self.links.values()}
        firsts = {self.part_first(p) for p in self.parts()}
        return [s for s in self.steps.values() if s.kind != "start" and s.id not in has_in and s.id not in firsts]

    def parts(self) -> list[str]:
        out: list[str] = []
        for s in self.steps.values():
            if s.part and s.part not in out:
                out.append(s.part)
        return out

    def part_first(self, part: str) -> str | None:
        h = self.part_heads.get(part)
        if h in self.steps and self.steps[h].part == part:
            return h
        return next((s.id for s in self.steps.values() if s.part == part), None)

    def _note_heads(self) -> None:
        for s in self.steps.values():
            if s.part and not (self.part_heads.get(s.part) in self.steps
                               and self.steps[self.part_heads[s.part]].part == s.part):
                self.part_heads[s.part] = s.id

    def canon_part(self, raw) -> str | None:
        """The agent's part name, matched to an existing part ("behavioral coaching" = "Behavioral Coaching")."""
        name = clean_text(raw, PART_MAX) if isinstance(raw, str) else ""
        if not name:
            return None
        for p in self.parts():
            if p.lower() == name.lower() or _similar(p, name):
                return p
        return name

    def for_agent(self) -> dict:
        return {
            "parts": self.parts(),
            # "at": when the step was first said, so the agent sees that the last step it mapped may belong to a part
            # people left minutes ago
            "steps": [{"id": s.id, "label": s.label, "kind": s.kind, **({"part": s.part} if s.part else {}),
                       "at": _mmss(min([e.t for e in s.evidence] or [s.t]))}
                      for s in self.steps.values()],
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

    def same_gap(self, g: Gap, step: Step | None, branch: str, question: str) -> bool:
        """Is a new flag_gap the same gap as g (a repeat, or a move to a better step)? A similar question is. A similar
        branch alone is only when it is about the same box, or (not a "what comes after X" gap) another box of the same
        part. v6.12 bench (leetcode): final pass flagged "after mock interviews" / "…after the behavioral mock
        interviews?" on Behavioral's box; the branch matched g5 "…after the system design mock interviews?" on System
        design's own "Do mock interviews" box, so g5 was moved into the Behavioral part with its system-design text."""
        old = self.steps.get(g.step)
        # v6.14: a "what comes after X" gap belongs to its own box's part. "…after the system mock interviews?" on
        # System design's box is not "…after the mock interviews?" on Behavioral's box, however alike the words.
        if (step is not None and old is not None and old.part != step.part
                and is_after_gap(branch, question) and is_after_gap(g.branch, g.question)):
            return False
        if _similar(g.question, question) or _same_ask(g.question, question):
            return True
        if not _similar(g.branch, branch):
            return False
        if step is None or g.step == step.id:
            return True
        if old is None:
            return True                       # its box is gone: the new step is where it belongs now
        return old.part == step.part and not is_after_gap(branch, question)

    def find_step(self, ref, part: str | None = None) -> Step | None:
        """By id ("s3") or by label (so one batch can chain steps it just added). With part: a step of that part wins
        over one with the same label in another part (two parts may each have a "Do mock interviews" box)."""
        if not isinstance(ref, str) or not ref.strip():
            return None
        ref = ref.strip()
        if ref in self.steps:
            return self.steps[ref]
        low = ref.lower()
        order = sorted(self.steps.values(), key=lambda s: 0 if part and s.part == part else 1)
        for s in order:
            if s.label.lower() == low:
                return s
        for s in order:
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
            pool = older[-4:] + new
            for x in new:
                if x.get("id", -1) > last_ev and said_next(with_next(pool, x.get("id", -1)) or x.get("text", ""),
                                                           s.label, [e.quote for e in s.evidence]):
                    answers.append((g, x))
                    break
        return {"number_changes": changes, "gap_answers": answers}

    def _said_next(self, s: Step, ev: Evidence, lines: list[dict]) -> str | None:
        """Does a line about the step already say what happens next? Looks at the gap's own line, the lines the step
        came from and every other line that names the step, each joined with the lines that finish its sentence
        (v6.5 run: "…they can solve it and then" | "then their peers will critique their code" was flagged as
        "What happens after solving the daily question?")."""
        want = key_words(s.label)
        cands = [e.line for e in [ev, *s.evidence]]
        if len(want) >= 2:
            # other lines naming the step, but not a side branch
            # ("if they submit the form twice then we ignore it" does not say what normally comes next)
            # (not only lines after the step's quote: v6.5 run, "…they can solve it and then" came 40 s before it)
            # v6.15 run: Behavioral "Do mock interviews" took System design's line "…mock interviews and then there's
            # going to be experts in that pool": a line of another part is not about this step
            # (it is another part's box evidence, or nearer to that part's box with the same name)
            mine = [e.line for e in s.evidence]
            other = {e.line for st in self.steps.values() if st.part != s.part for e in st.evidence}
            twins = [e.line for st in self.steps.values() if st.part != s.part and key_words(st.label) == want
                     for e in st.evidence]

            def foreign(i: int) -> bool:
                if i in other:
                    return True
                if twins and mine:
                    return min(abs(i - j) for j in twins) < min(abs(i - j) for j in mine)
                return False

            cands += [int(x.get("id", -1)) for x in lines
                      if not _SIDE_BRANCH.search(" ".join(tokens(x.get("text", ""))))
                      and len(want & key_words(x.get("text", ""))) * 3 >= len(want) * 2
                      and not foreign(int(x.get("id", -1)))]
        seen: set[int] = set()
        own = [x.quote for x in s.evidence]
        for line_id in cands:
            if line_id in seen:
                continue
            seen.add(line_id)
            text = with_next(lines, line_id) or (ev.quote if line_id == ev.line else "")
            nxt = said_next(text, s.label, own) if text else None
            if nxt:
                return nxt
        return None

    def _step_like(self, words: str, not_this: Step) -> Step | None:
        """An existing step the "then …" words point to (shares most of its words)."""
        w = set(_stems(_content(tokens(words))))
        best, best_n = None, 0
        # never point back to a box that already leads into this one (v6.13 bench: hint "link s24 -> s23" while
        # s23 -> s24 was already drawn)
        before = {k.src for k in self.links.values() if k.dst == not_this.id}
        for st in self.steps.values():
            if st.id == not_this.id or st.id in before:
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
        self._note_heads()
        try:
            return fn(args, lines, round(now_t, 1))
        except (TypeError, ValueError) as e:
            return _err(f"bad arguments: {e}")
        finally:
            self._note_heads()

    def _t_add_step(self, a: dict, lines, now) -> Result:
        label = clean_text(a.get("label"), LABEL_MAX)
        if not label:
            return _err("label is required")
        if dead_label(label):
            return _err(_DEAD_MSG)
        ev, why = check_quote(a.get("quote"), lines)
        if not ev:
            return _err(why)
        kind = a.get("kind") if a.get("kind") in STEP_KINDS else "step"
        after = a.get("after")
        part = self.canon_part(a.get("part"))
        src = self.find_step(after, part) if after else None
        events: list[dict] = []
        start = next((x for x in self.steps.values() if x.kind == "start"), None)
        demoted = False
        if kind == "start" and start is not None and start.label.lower() != label.lower():
            kind, demoted = "step", True     # one journey = one start; keeps the map one connected flow
        s = self.find_step(label, part)
        if s and part and s.part and s.part != part and part in self.parts():
            # same words, but the agent puts it in ANOTHER part that is already on the map: a box of that part, not the
            # other part's box. v6.11 bench (leetcode): add_step "Do mock interviews" part "System design" after
            # "Practice follow-on sessions" reused Behavioral coaching's "Do mock interviews" and drew an arrow from the
            # System design row into the Behavioral row; the agent then added "System mock interviews" as well, and the
            # stray cross-part arrow stayed. A near-same box already in that part is still reused.
            s = next((x for x in self.steps.values() if x.part == part
                      and (x.label.lower() == label.lower() or _similar(x.label, label))), None)
        # before = a step already on the map that this one comes BEFORE: a new first step of that step's part.
        # v6.9 bench (grooming): "They log in with a staff account" was said after "today's bookings" (the Groomer
        # app's first box); with only "after" the agent could not place it, was refused, and made a one-box part
        # "Staff login" to get it in.
        before, nxt = a.get("before"), None
        if before and not src and not s:
            nxt = self.find_step(before, part)
            if nxt is None:
                return _err(f"step {before!r} not found: 'before' must be an existing step id")
            if nxt.kind == "start":
                return _err(f"{nxt.id} is the start step (the entry point): nothing comes before it")
            if any(k.type == "normal" for k in self.links_into(nxt.id)):
                ins = ", ".join(self.steps[k.src].id for k in self.links_into(nxt.id) if k.src in self.steps)
                return _err(f"{nxt.id} already has a step before it ({ins}): use after = the step that comes before "
                            "this one instead")
            if part is None or part not in self.parts():
                part = nxt.part
        new_part = part is not None and part not in self.parts()
        if not s and self.steps and not demoted and kind != "start" and not src and not new_part and not nxt:
            # a plain step with no arrow into it breaks the flow: say where it fits, or start another part, or wait
            if after:
                return _err(f"step {after!r} not found: 'after' must be an existing step id, or the label of a step "
                            "added earlier in this batch")
            return _err("say where this step fits: after = the step that comes before it. If it comes BEFORE a step "
                        "already on the map (a new first step of that part), give before = that step. If it begins "
                        "another part of the product, give that part's name in part (no after). Never invent a part "
                        "just to place one step. If the transcript doesn't say yet, don't add it now")
        if part is None and src is not None:
            part = src.part                   # same part as the step it comes after
        if s:
            _add_ev(s.evidence, ev)
            if part and not s.part:
                s.part = part
            note = "step already existed, reused it"
        else:
            if len(self.steps) >= MAX_STEPS:
                return _err("flow is full (max steps)")
            s = Step(id=self._id("s"), label=label, kind=kind, t=ev.t, evidence=[ev], part=part)
            self.steps[s.id] = s
            self.round_new.add(s.id)
            events.append({"kind": "step_added", "id": s.id, "text": f"New step: {s.label}"})
            note = "added"
        msg = {"ok": True, "id": s.id, "note": note}
        if s.part:
            msg["part"] = s.part
        if demoted:
            msg["warning"] = f"there is already a start step ({start.id}); added as a normal step: link it with after"
        if after and not src:
            msg["warning"] = f"step {after!r} not found, so no link was made"
        if nxt is not None and nxt.id != s.id:
            head = s.part is not None and s.part == nxt.part and self.part_first(nxt.part) == nxt.id
            events += self._link(s, nxt, a.get("link_label"), a.get("link_type"), ev, now)
            if head:
                self.part_heads[s.part] = s.id     # the new box is now where this part begins
            msg["before"] = nxt.id
        closed = None
        if src and src.id != s.id:
            r = self._link(src, s, a.get("link_label"), a.get("link_type"), ev, now)
            events += r
            closed = self._close_after_gap(src, s, ev, r, events)
            dropped = self._drop_shortcut(src, s) if any(e["kind"] == "link_added" for e in r) else None
            if dropped:
                msg["note"] = f"{note}; {dropped}"
        return Result(True, msg, events, resolved_gap=closed)

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
        closed = self._close_after_gap(src, dst, ev, list(events), events)
        msg = {"ok": True, "id": lk.id if lk else None}
        dropped = self._drop_shortcut(src, dst) if any(e["kind"] == "link_added" for e in events) else None
        if dropped:
            msg["note"] = dropped
        return Result(True, msg, events, resolved_gap=closed)

    def _drop_shortcut(self, src: Step, dst: Step) -> str | None:
        """A new box X put between two steps that already had an arrow: A -> X -> Y with the old A -> Y still there.
        v6.10 bench: the agent added "Enter phone number" after "Download app" and linked it to "Enter SMS code", and
        "Download app -> Enter SMS code" stayed as a second path (leetcode had 4 more like it, e.g. "Click answer form"
        put between "Access questions page" and "Answer behavioral questions"). Only for a box new in this round with
        one plain arrow in and one plain arrow out, and a plain old arrow that isn't a decision's branch: then the old
        arrow is the same path and goes away. Returns a note for the agent, or None."""
        def plain(k: Link | None) -> bool:
            return k is not None and k.type == "normal" and not k.label
        new = self._find_link(src.id, dst.id)
        if not plain(new):
            return None
        for x in (src, dst):
            if x.id not in self.round_new:
                continue
            ins, outs = self.links_into(x.id), [k for k in self.links.values() if k.src == x.id]
            if len(ins) != 1 or len(outs) != 1 or not plain(ins[0]) or not plain(outs[0]):
                continue
            a, y = self.steps.get(ins[0].src), self.steps.get(outs[0].dst)
            if not a or not y or a.id == y.id or a.kind == "decision":
                continue
            short = self._find_link(a.id, y.id)
            if not plain(short):
                continue
            del self.links[short.id]
            return f"{x.id} now sits between {a.id} and {y.id}: removed the old direct arrow {a.id} -> {y.id}"
        return None

    def _close_after_gap(self, src: Step, dst: Step, ev: Evidence, link_events: list[dict], events: list[dict]):
        """A new normal arrow out of a step answers its open "what happens after" gap (v6.4: "Charge session fee"
        had an arrow out and still showed "What happens after the fee is charged?")."""
        lk = self._find_link(src.id, dst.id)
        if not lk or lk.type != "normal" or not any(e["kind"] == "link_added" for e in link_events):
            return None
        for g in self.open_gaps():
            if g.step == src.id and is_next_gap(g.branch, g.question):
                g.resolved, g.answer, g.resolved_by = True, f"Next: {dst.label}", ev
                events.append({"kind": "gap_resolved", "id": g.id, "step": g.step, "text": f"Gap answered: {g.question}"})
                return g
        return None

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

    def links_into(self, step_id: str) -> list[Link]:
        return [k for k in self.links.values() if k.dst == step_id]

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
        part = self.canon_part(a.get("part"))
        if label and dead_label(label):
            return _err(_DEAD_MSG)
        other = self.find_step(label) if label else None
        if other and other.id != s.id:
            text = next((x.get("text", "") for x in lines if x.get("id") == ev.line), ev.quote)
            if other.id in self.round_new and s.id not in self.round_new and says_replace(text):
                return self._merge_into(s, other, label, kind, part, ev, now)
            return _err(f"another step already has that label ({other.id})")
        if label and label != s.label and not (key_words(label) & key_words(s.label)):
            # v6.4 run: a rename keeps the step's place and arrows. Fine when the client replaces one thing with
            # another ("instead of a Trello board … a form"); a new topic minutes later is another step.
            last_t = max((e.t for e in s.evidence), default=s.t)
            text = next((x.get("text", "") for x in lines if x.get("id") == ev.line), ev.quote)
            if ev.t - last_t > RENAME_FAR_S and not says_replace(text):
                return _err(f"{label!r} shares no words with {s.label!r} and was said much later: if it is a new step, "
                            f"add_step it (after = the step before it). Rename only when this line says the old step "
                            "is replaced or clarified")
        changed = False
        if label and label != s.label:
            s.label, changed = label, True
        if kind and kind != s.kind:
            s.kind, changed = kind, True
        if part and part != s.part:
            s.part, changed = part, True
        _add_ev(s.evidence, ev)
        if not changed:
            return Result(True, {"ok": True, "id": s.id, "note": "nothing changed"})
        s.updated_t = now
        return Result(True, {"ok": True, "id": s.id}, [{"kind": "step_updated", "id": s.id, "text": f"Step updated: {s.label}"}])

    def _merge_into(self, s: Step, new: Step, label: str, kind, part, ev: Evidence, now: float) -> Result:
        """The line says the old step `s` is replaced, and the agent already added its new version `new` in this
        round: one box. `s` keeps its place in the order (its fixed number) and takes the new name, part and arrows;
        `new` (not on anyone's screen yet) goes away. If the step moves to another part, its old arrows inside the old
        part described where the replaced thing sat ("Mark attendance -> WhatsApp group") and are dropped."""
        old_part = s.part
        to_part = part or new.part or s.part
        moved = bool(old_part and to_part and to_part != old_part)
        if moved:
            olds = [k for k in self.links.values() if s.id in (k.src, k.dst)
                    and (far := self.steps.get(k.dst if k.src == s.id else k.src)) and far.part == old_part]
            # the old part's chain goes on without the box that left: A -> old -> B becomes A -> B
            for a in (k for k in olds if k.dst == s.id):
                for b in (k for k in olds if k.src == s.id):
                    if a.src != b.dst and not self._find_link(a.src, b.dst):
                        lk = Link(id=self._id("l"), src=a.src, dst=b.dst, label=b.label, type=b.type, t=b.t,
                                  evidence=list(a.evidence + b.evidence)[-MAX_EVIDENCE:])
                        self.links[lk.id] = lk
            for k in olds:
                del self.links[k.id]
        for k in list(self.links.values()):
            if new.id not in (k.src, k.dst):
                continue
            src = s.id if k.src == new.id else k.src
            dst = s.id if k.dst == new.id else k.dst
            del self.links[k.id]
            if src == dst:
                continue
            same = self._find_link(src, dst)
            if same:
                for e in k.evidence:
                    _add_ev(same.evidence, e)
                if k.label and not same.label:
                    same.label = k.label
            else:
                k.src, k.dst = src, dst
                self.links[k.id] = k
        for g in self.gaps.values():
            if g.step == new.id:
                g.step = s.id
        for b in self.bugs.values():
            if b.step == new.id:
                b.step = s.id
        for c in self.contradictions.values():
            if c.step == new.id:
                c.step = s.id
        for e in new.evidence:
            _add_ev(s.evidence, e)
        _add_ev(s.evidence, ev)
        if moved:
            s.t = new.t       # the box is the new thing now: its band keeps its place on the map (bands go by time)
        s.label, s.part = label, to_part
        # the start / end of the journey stays one (add_step already turned a second "start" into a plain step);
        # otherwise a kind the new version was given (a check = "decision") wins over a plain step
        s.kind = kind or (s.kind if s.kind in ("start", "end") or new.kind == "step" else new.kind)
        s.updated_t = now
        del self.steps[new.id]
        self.round_new.discard(new.id)
        return Result(True, {"ok": True, "id": s.id, "merged": new.id,
                             "note": f"{new.id} was the new version of this step: merged into {s.id} (use {s.id})"},
                      [{"kind": "step_updated", "id": s.id, "text": f"Step updated: {s.label}"}])

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
            # v6.7 bench: the final pass pinned the fixed bug b1 again as b2 from the very same line; a bug quoted from
            # a line an existing bug already came from is that bug (fixed or not). A new report on a NEW line after a
            # fix is a new bug (it came back).
            if any(e.line == ev.line for e in b.evidence):
                return Result(True, {"ok": True, "id": b.id, "note": "already pinned from that line"
                                     + (" (and marked fixed)" if b.fixed else "")})
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

    def gap_precheck(self, a: dict, lines) -> str | None:
        """Free checks of a flag_gap call, run BEFORE any paid second-opinion Spark call. Returns the error or None."""
        if not isinstance(a, dict):
            return None
        s = self.find_step(a.get("step"))
        branch = clean_text(a.get("missing_branch"), LINK_LABEL_MAX)
        question = clean_text(a.get("question"), TEXT_MAX)
        if not s or not branch or not question:
            return None                                   # _t_flag_gap gives the exact error
        same = [g for g in self.gaps.values() if self.same_gap(g, s, branch, question)]
        gone = next((g for g in same if g.dismissed and g.removed_why), None)
        if gone:
            return f"not a gap: {gone.removed_why}"
        if any(not g.dismissed for g in same):
            return None                                   # a repeat or a move: _t_flag_gap handles it
        if is_pay_gap(branch, question):
            other = next((g for g in self.open_gaps() if is_pay_gap(g.branch, g.question)), None)
            if other:
                return (f"there is already an open payment gap ({other.id}: {other.question!r}): ask how people pay "
                        "once for the whole flow, not again for each product or price")
        if is_pay_fail_gap(branch, question):
            other = next((g for g in self.open_gaps() if is_pay_fail_gap(g.branch, g.question)), None)
            if other:
                return (f"there is already an open payment-failure gap ({other.id}: {other.question!r}): one "
                        "\"what if a payment fails\" question covers every payment in the flow. Flag another kind of "
                        "gap if one matters more (a drop-off, a hand-off, a rejection)")
        ev, _ = check_quote(a.get("quote"), lines)
        if self.mode in ("live", "gap_check") and self.latest_t is not None:
            last = max([e.t for e in s.evidence] + ([ev.t] if ev else []))
            if self.latest_t - last < RECENT_TOPIC_S:
                return (f"{s.label!r} (or the line you quoted) was talked about at {_mmss(last)}, in the last "
                        f"{int(RECENT_TOPIC_S)} s: they may still answer it. Don't flag a gap on it yet (the check "
                        "after the meeting still can)")
        return None             # (round budget / open-gap limits: _t_flag_gap, after its more useful checks)

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
        pre = self.gap_precheck(a, lines)
        if pre:
            return _err(pre)
        out = [k for k in self.links.values() if k.src == s.id and k.type == "normal"]
        if out and is_next_gap(branch, question):
            names = ", ".join(self.steps[k.dst].label for k in out if k.dst in self.steps)
            return _err(f"not a gap: the map already shows what comes after {s.label!r} (→ {names})")
        if is_after_gap(branch, question):
            nxt = self._said_next(s, ev, lines)
            if nxt:
                hint = self._step_like(nxt, s)
                todo = f"link_steps from {s.id} to {hint.id} ({hint.label})" if hint else f"add_step with after = {s.id}"
                return _err(f"not a gap: the transcript already says what comes after {s.label!r} "
                            f"(\"…then {nxt}\"). Map it instead: {todo}")
        for g in self.gaps.values():
            if g.dismissed or not self.same_gap(g, s, branch, question):
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
                # v6.7.2 bench: "24 h vs 12 h" was settled by quoting its own second line ("…free up to twelve hours
                # before"): that is the AI picking the newer value, not the team settling it
                if ev.line in (c.a.line, c.b.line) or ev.line < c.b.line:
                    return _err("a contradiction is settled only by a LATER line (after both statements) where the "
                                "group clearly picks one value or says the other one is dropped. One of the two "
                                "conflicting lines is not that: leave it open")
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
        g.user_set = True
        g.resolved = resolved
        if not resolved:
            g.answer, g.resolved_by = None, None
        return True


def _mmss(t: float) -> str:
    t = int(t)
    return f"{t // 3600}:{t // 60 % 60:02d}:{t % 60:02d}" if t >= 3600 else f"{t // 60:02d}:{t % 60:02d}"


def _add_ev(lst: list[Evidence], ev: Evidence) -> None:
    if any(x.line == ev.line for x in lst):
        return
    lst.append(ev)
    del lst[:-MAX_EVIDENCE]


_DEAD_MSG = ("a step names what the user does now, never that something is removed or replaced. When the group "
             "replaces something, rename the old step to the new thing (update_step, quoting the line that says it)")


def _err(msg: str) -> Result:
    return Result(False, {"ok": False, "error": msg})
