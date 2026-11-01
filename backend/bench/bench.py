"""Fast transcript test bench: runs meetings through the real Cards + Flow pipeline (no audio, no images) and scores
the result against an answer key. A 32-min meeting takes a few minutes instead of 32.

From backend/ (uses META_API_KEY from .env; images are off, so it only costs Spark calls):
  python -m bench.bench                              # all meetings
  python -m bench.bench --only grooming_app music_school
  python -m bench.bench --score debug/<file>.json --meeting leetcode_site    # score a live run (no API calls)
  python -m bench.bench --extract debug/<file>.json leetcode_site            # save that run's transcript for the bench

Meetings and answer keys: bench/meetings.py. The real Vimeo transcript stays out of git (bench/private/).
Results: bench/out/<time>/<meeting>.json (same shape as a DEBUG_DUMP json: scripts/replay.py can read it) + summary.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from app.board import numbers  # noqa: E402
from app.config import BYTES_PER_SEC, settings  # noqa: E402
from app.meta_api import MetaClient  # noqa: E402
from app.session import Session  # noqa: E402
from bench.meetings import LEETCODE_KEY, MEETINGS  # noqa: E402

PRIVATE = HERE / "private"
OUT = HERE / "out"
FRONTEND = HERE.parent.parent / "frontend"


# ---------------------------------------------------------------- meetings
def load_meetings(only: list[str] | None) -> dict:
    ms = dict(MEETINGS)
    lc = PRIVATE / "leetcode_site.json"
    if lc.exists():
        ms["leetcode_site"] = {"title": "LeetCode site planning (Vimeo, real ASR)",
                               "lines": json.loads(lc.read_text())["transcript"], "key": LEETCODE_KEY}
    if only:
        missing = [m for m in only if m not in ms]
        if missing:
            sys.exit(f"unknown meeting(s): {missing}. Known: {sorted(ms)}"
                     + ("" if lc.exists() else "  (leetcode_site needs --extract first)"))
        ms = {k: v for k, v in ms.items() if k in only}
    return ms


def extract(debug_json: str, name: str):
    d = json.loads(Path(debug_json).read_text())
    PRIVATE.mkdir(exist_ok=True)
    keep = [{k: x[k] for k in ("id", "turn", "speaker", "text", "t", "t_end") if k in x} for x in d["transcript"]]
    (PRIVATE / f"{name}.json").write_text(json.dumps({"source": Path(debug_json).name, "transcript": keep}, indent=1))
    print(f"{len(keep)} lines -> {PRIVATE / (name + '.json')}")


# ---------------------------------------------------------------- run one meeting
class BenchSession(Session):
    """A Session fed from a transcript: the meeting clock is the transcript's clock."""

    def __init__(self, meta: MetaClient, title: str):
        super().__init__(meta, [], title, "bench")
        self.state = "recording"
        self._last_check_mt = 0.0
        self.snapshots: list[dict] = []

    def set_clock(self, t: float):
        self.audio.received_bytes = int(t * BYTES_PER_SEC)

    async def auto_gap_check_mt(self) -> bool:
        """Session.auto_gap_check on the meeting clock (the real one uses wall time)."""
        if settings.auto_gap_check_s <= 0 or len(self.flow.steps) < settings.auto_gap_min_steps:
            return False
        if self.meeting_t - self._last_check_mt < settings.auto_gap_check_s:
            return False
        shape = (len(self.flow.steps), len(self.flow.links))
        if shape == self.auto_check_shape:
            return False
        self._last_check_mt = self.meeting_t
        self.auto_check_shape = shape
        async with self.spark_lock:
            await self.recheck_gaps()
            await self._flow_round("gap_check")
        return True

    def snap(self, mode: str):
        self.snapshots.append({"meeting_t": round(self.meeting_t, 1), "mode": mode, "flow": self.flow.public()})


async def run_meeting(name: str, m: dict, meta: MetaClient, log) -> dict:
    s = BenchSession(meta, m["title"])
    lines = sorted(m["lines"], key=lambda x: x["t_end"])
    i = 0

    def feed(until: float):
        nonlocal i
        while i < len(lines) and lines[i]["t_end"] <= until:
            x = lines[i]
            s.transcript.append({"id": len(s.transcript), "turn": x.get("turn", f"1-{i}"), "speaker": x["speaker"],
                                 "text": x["text"], "t": x["t"], "t_end": x["t_end"]})
            i += 1

    t, end = 0.0, lines[-1]["t_end"] if lines else 0
    t0 = time.monotonic()
    while t < end + settings.spark_interval_s:
        t += settings.spark_interval_s
        feed(t)
        s.set_clock(t)
        new = s.transcript[s.spark_done_idx:]
        if sum(len(x["text"]) for x in new) >= settings.spark_min_new_chars:
            r0 = time.monotonic()
            await s.spark_once()
            took = time.monotonic() - r0
            s.snap("live")
            t += took                     # lines keep coming while Spark works (they wait for the next round)
            feed(t)
            s.set_clock(t)
        if await s.auto_gap_check_mt():
            s.snap("gap_check")
        log(f"{name}: {t / 60:5.1f} min, {len(s.flow.steps)} steps, {len(s.board.cards)} cards")
    feed(float("inf"))
    s.set_clock(max(t, end))
    s.state = "stopping"
    await s.spark_once(final=True)
    s.snap("final")
    s.state = "done"
    calls = s.debug_calls
    return {"session": s.id, "meeting": name, "title": m["title"], "state": "done", "speakers": {},
            "transcript": s.transcript, "board": s.board.public(), "flow": s.flow.public(), "flow_stats": s.flow_stats,
            "calls": calls, "snapshots": s.snapshots, "wall_s": round(time.monotonic() - t0, 1),
            "spark_requests": count_requests(calls)}


def count_requests(calls: list[dict]) -> int:
    n = 0
    for c in calls:
        if c.get("kind") == "flow":
            n += max(1, len(c.get("turns") or []))
        elif c.get("kind") in ("cards", "flow_check", "conflict_check", "gap_recheck"):
            n += 1
    return n


# ---------------------------------------------------------------- scoring
def _toks(s: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", (s or "").lower().replace("'", ""))


def _has_all(text: str, words: list[str]) -> bool:
    """All words (stems) are in the text; a word written "!up" must NOT be in it."""
    tk = _toks(text)
    return all((not any(t.startswith(w[1:]) for t in tk)) if w.startswith("!") else any(t.startswith(w) for t in tk)
               for w in words)


def _any(text: str, lists: list[list[str]]) -> bool:
    return any(_has_all(text, ws) for ws in lists)


def _num_in(text: str, n: str) -> bool:
    flat = " " + " ".join(_toks(text.replace(",", ""))) + " "
    return f" {n.replace(',', '').lower()} " in flat


def score(flow: dict, board: dict, key: dict) -> dict:
    steps = flow.get("steps", [])
    links = flow.get("links", [])
    res: dict = {}
    # steps
    match = {k: [s["id"] for s in steps if _any(s["label"], v)] for k, v in key.get("steps", {}).items()}
    found = [k for k, v in match.items() if v]
    res["steps"] = {"found": len(found), "of": len(match), "missing": [k for k, v in match.items() if not v],
                    "map_steps": len(steps)}
    # links (a path of at most 2 arrows counts)
    out: dict[str, set] = {}
    for lk in links:
        out.setdefault(lk["src"], set()).add(lk["dst"])

    def reach(a, b):          # a == b: the agent made one box of both ("Pick instrument and day")
        return a == b or b in out.get(a, set()) or any(b in out.get(x, set()) for x in out.get(a, set()))
    def alts(p):              # a key link is [a, b] or a list of accepted pairs [[a, b], [a, c]]
        return p if isinstance(p[0], list) else [p]

    def link_ok(p):
        return any(reach(x, y) for a, b in alts(p) for x in match.get(a, []) for y in match.get(b, []))
    res["links"] = {"found": sum(1 for p in key.get("links", []) if link_ok(p)), "of": len(key.get("links", [])),
                    "missing": ["->".join(alts(p)[0]) for p in key.get("links", []) if not link_ok(p)]}
    # arrows between parts that nobody said
    part_of: dict[str, set] = {}
    for k, ids in match.items():
        for sid in ids:
            part_of.setdefault(sid, set()).update(_key_parts(k))
    ok_cross = {tuple(x) for x in key.get("cross_ok", [])}
    label = {s["id"]: s["label"] for s in steps}
    wrong = []
    for lk in links:
        a, b = part_of.get(lk["src"]), part_of.get(lk["dst"])
        if a and b and not (a & b) and not any((x, y) in ok_cross for x in a for y in b):
            wrong.append(f"{label.get(lk['src'])} -> {label.get(lk['dst'])}")
    res["wrong_cross_part_links"] = wrong
    # parts given by the agent: how clean is each band
    # a step the key allows in two parts ("app|staff.done": the groomer marks it done in the owner's journey) is clean
    # in a band of either part
    bands: dict[str, list[set]] = {}
    for s in steps:
        if part_of.get(s["id"]):
            bands.setdefault(s.get("part") or "", []).append(part_of[s["id"]])
    if any(k for k in bands):
        tot = sum(len(v) for v in bands.values())
        pure, shown = 0, {}
        for k, v in bands.items():
            cands = set().union(*v)
            best = max(sorted(cands), key=lambda p: sum(1 for x in v if p in x))
            pure += sum(1 for x in v if best in x)
            shown[k or "(none)"] = [best if best in x else "|".join(sorted(x)) for x in v]
        # one part of the product drawn as two rows (v6.9 grooming: "Staff login" = one box of the Groomer app)
        owner: dict[str, list[str]] = {}
        for k, v in bands.items():
            cands = set().union(*v)
            owner.setdefault(max(sorted(cands), key=lambda p: sum(1 for x in v if p in x)), []).append(k or "(none)")
        res["parts"] = {"bands": shown, "purity": round(pure / max(1, tot), 2),
                        "split": {p: ks for p, ks in owner.items() if len(ks) > 1}}
    # gaps
    gaps = [g for g in flow.get("gaps", []) if not g.get("dismissed")]
    gtext = {g["id"]: f"{g.get('question', '')} {g.get('branch', '')}" for g in gaps}
    exp = [x["name"] for x in key.get("gaps", []) if any(_any(t, x["any"]) for t in gtext.values())]
    # a real open path that got closed during the meeting (nobody really answered it): worth a look
    closed = [g["question"] for x in key.get("gaps", []) for g in gaps if _any(gtext[g["id"]], x["any"]) and g.get("resolved")]
    bad, fixed_later = [], []
    for g in gaps:                                  # each gap counts once, for the first rule it matches
        rule = next((x for x in key.get("no_gaps", []) if _any(gtext[g["id"]], x["any"])), None)
        if rule and not any(_any(gtext[g["id"]], x["any"]) for x in key.get("gaps", [])):
            # flagged early but really answered LATER in the meeting, and closed then: fine. A gap whose answer was
            # said BEFORE it was flagged is wrong even if it got closed afterwards.
            ok = g.get("resolved") and rule.get("answered") == "later"
            (fixed_later if ok else bad).append(f"{rule['name']}: {g['question']}")
    judged = {g["id"] for x in key.get("gaps", []) + key.get("no_gaps", []) for g in gaps if _any(gtext[g["id"]], x["any"])}
    res["gaps"] = {"expected_found": len(exp), "of": len(key.get("gaps", [])),
                   "missed": [x["name"] for x in key.get("gaps", []) if x["name"] not in exp],
                   "false": bad, "answered_later": fixed_later, "real_but_closed": closed, "flagged": len(gaps), "open_at_end": sum(1 for g in gaps if not g.get("resolved")),
                   "unjudged": [g["question"] for g in gaps if g["id"] not in judged]}
    # contradictions
    cons = flow.get("contradictions", [])

    def pair_in(c, pairs):
        a, b = c["a"]["quote"], c["b"]["quote"]
        return any((_num_in(a, x) and _num_in(b, y)) or (_num_in(a, y) and _num_in(b, x)) for x, y in pairs)
    cf = [x["name"] for x in key.get("conflicts", []) if any(pair_in(c, x["numbers"]) for c in cons)]
    cbad = [f"{x['name']}: {c['note']}" for x in key.get("no_conflicts", []) for c in cons if pair_in(c, x["numbers"])
            and not any(pair_in(c, y["numbers"]) for y in key.get("conflicts", []))]
    res["conflicts"] = {"expected_found": len(cf), "of": len(key.get("conflicts", [])),
                        "missed": [x["name"] for x in key.get("conflicts", []) if x["name"] not in cf], "false": cbad,
                        "flagged": len(cons)}
    # a card that states one side of an OPEN contradiction must not look decided (v6.7.2 bench: "Free cancellation up
    # to 24 hours" stayed a plain decision while "24 vs 12 hours" was open)
    undecided = []
    for k in cons:
        if k.get("resolved"):
            continue
        na, nb = numbers(k["a"]["quote"]), numbers(k["b"]["quote"])
        for side, own in ((k["a"], na - nb), (k["b"], nb - na)):
            for c in board.get("cards", []):
                if c["column"] == "decisions" and not c.get("conflict_ref") and numbers(c["title"]) & own \
                        and abs(float(c.get("t") or 0) - float(side["t"])) <= 30:
                    undecided.append(f"{c['title']} (side of: {k['note']})")
    res["conflicts"]["shown_as_decided"] = undecided
    # cards that still name a replaced thing
    stale = []
    for st in key.get("stale", []):
        for c in board.get("cards", []):
            if c["column"] == "questions" and c.get("resolved"):
                continue                 # an answered question is history ("Trello or a form?" -> "a form")
            text = f"{c.get('title', '')} {c.get('detail', '')}"
            if _has_any_word(text, st["old"]) and not _has_any_word(text, st["new"]):
                stale.append(f"{c['column']}: {c['title']} | {c.get('detail', '')[:80]}")
    res["stale_cards"] = stale
    # bugs
    if key.get("bugs"):
        bugs = flow.get("bugs", [])
        res["bugs"] = [{"name": b["name"], "found": any(_any(x["text"], b["any"]) for x in bugs),
                        "fixed": any(_any(x["text"], b["any"]) and x.get("fixed") for x in bugs)} for b in key["bugs"]]
    res["cards"] = {c: sum(1 for x in board.get("cards", []) if x["column"] == c)
                    for c in ("goals", "decisions", "actions", "updates", "questions")}
    return res


def _key_parts(key: str) -> set[str]:
    """"app.sign" -> {app}; "behav|sysd.mock" -> {behav, sysd} (the step may belong to either part)."""
    return set(key.split(".")[0].split("|"))


def _has_any_word(text: str, words: list[str]) -> bool:
    tk = set(_toks(text))
    return any(w in tk or w + "s" in tk for w in words)


# ---------------------------------------------------------------- layout check (node, optional)
def layout_check(snapshots: list[dict], out_dir: Path, name: str) -> str:
    node = shutil.which("node")
    script = FRONTEND / "scripts" / "layoutcheck.ts"
    runner = FRONTEND / "scripts" / "run-ts.mjs"       # any Node 18+ (Node 20 has no --experimental-strip-types)
    if not node or not script.exists():
        return "skipped (node not found)"
    if not (FRONTEND / "node_modules" / "typescript").exists():
        return "skipped (run npm install in frontend/ first)"
    snap = out_dir / f"{name}.snapshots.json"
    snap.write_text(json.dumps(snapshots))
    lines = []
    for w in (900, 1200):
        try:
            p = subprocess.run([node, str(runner), str(script), str(snap), str(w)],
                               capture_output=True, text=True, timeout=120, cwd=FRONTEND)
        except (OSError, subprocess.TimeoutExpired) as e:
            return f"skipped ({e})"
        if p.returncode:
            err = [x for x in p.stderr.splitlines() if "Error" in x] or p.stderr.strip().splitlines() or ["error"]
            return "skipped (layout check failed: " + err[0].strip()[:160] + ")"
        last = [x for x in p.stdout.splitlines() if x.startswith("summary:")]
        lines.append(f"{w}px " + (last[-1].removeprefix("summary: ") if last else "?"))
    return "; ".join(lines)


# ---------------------------------------------------------------- report
def fmt(name: str, r: dict, extra: str = "") -> str:
    g, c, st, lk = r["gaps"], r["conflicts"], r["steps"], r["links"]
    out = [f"== {name} {extra}",
           f"  steps {st['found']}/{st['of']} (map has {st['map_steps']})" + (f"  missing: {', '.join(st['missing'])}" if st["missing"] else ""),
           f"  links {lk['found']}/{lk['of']}" + (f"  missing: {', '.join(lk['missing'])}" if lk["missing"] else ""),
           f"  wrong arrows between parts: {len(r['wrong_cross_part_links'])}" +
           (f"  ({'; '.join(r['wrong_cross_part_links'][:6])})" if r["wrong_cross_part_links"] else "")]
    if "parts" in r:
        out.append(f"  parts purity {r['parts']['purity']}  bands: " +
                   "; ".join(f"{k}={sorted(set(v))}" for k, v in r["parts"]["bands"].items()))
        for p, ks in (r["parts"].get("split") or {}).items():
            out.append(f"    SPLIT: part {p!r} drawn as {len(ks)} rows: {', '.join(ks)}")
    out.append(f"  gaps: real found {g['expected_found']}/{g['of']}, FALSE {len(g['false'])}, flagged {g['flagged']}, "
               f"open at end {g['open_at_end']}")
    for x in g["false"]:
        out.append(f"    FALSE gap  {x}")
    for x in g["missed"]:
        out.append(f"    missed gap {x}")
    for x in g.get("real_but_closed", []):
        out.append(f"    CHECK: real gap was closed during the meeting: {x}")
    for x in g.get("answered_later", []):
        out.append(f"    flagged early, answered later (ok) {x}")
    for x in g["unjudged"]:
        out.append(f"    (not in key) {x}")
    out.append(f"  conflicts: real found {c['expected_found']}/{c['of']}, FALSE {len(c['false'])}, flagged {c['flagged']}")
    for x in c["false"]:
        out.append(f"    FALSE conflict {x}")
    for x in c["missed"]:
        out.append(f"    missed conflict {x}")
    for x in c.get("shown_as_decided", []):
        out.append(f"    NOT TAGGED: decision states one side of an open conflict: {x}")
    out.append(f"  stale cards: {len(r['stale_cards'])}" + "".join(f"\n    {x}" for x in r["stale_cards"]))
    if "bugs" in r:
        out.append("  bugs: " + ", ".join(f"{b['name']} found={b['found']} fixed={b['fixed']}" for b in r["bugs"]))
    out.append(f"  cards {r['cards']}")
    if "layout" in r:
        out.append(f"  layout: {r['layout']}")
    return "\n".join(out)


async def main_async(a):
    settings.max_images = 0                     # no Muse Image calls in the bench
    settings.debug_dump = True
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = OUT / stamp
    out_dir.mkdir(parents=True, exist_ok=True)
    settings.debug_dir = str(out_dir / "raw")
    meetings = load_meetings(a.only)
    if not settings.mock_meta and not settings.meta_api_key:
        sys.exit("META_API_KEY is not set (backend/.env)")
    meta = MetaClient()
    print(f"running {len(meetings)} meeting(s){' in MOCK mode' if settings.mock_meta else ''}: {', '.join(meetings)}")
    last = {"t": 0.0}

    def log(msg):
        if time.monotonic() - last["t"] > 15:
            last["t"] = time.monotonic()
            print("  ..", msg, flush=True)
    try:
        results = await asyncio.gather(*(run_meeting(n, m, meta, log) for n, m in meetings.items()), return_exceptions=True)
    finally:
        await meta.aclose()
    summary, total_req = {}, 0
    for (name, m), res in zip(meetings.items(), results):
        if isinstance(res, BaseException):
            print(f"== {name}: FAILED {res!r}")
            continue
        (out_dir / f"{name}.json").write_text(json.dumps(res, indent=1, default=str))
        r = score(res["flow"], res["board"], m["key"])
        r["layout"] = layout_check(res["snapshots"], out_dir, name)
        summary[name] = r
        total_req += res["spark_requests"]
        print(fmt(name, r, f"({res['spark_requests']} Spark requests, {res['wall_s']} s)"))
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=1))
    print(f"\n{total_req} Spark requests in total (about ${total_req * 0.01:.2f} at ~$0.01 each). Results: {out_dir}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", nargs="+", help="meeting names to run")
    ap.add_argument("--score", help="score a DEBUG_DUMP json (or a bench result json) instead of running")
    ap.add_argument("--meeting", help="answer key to use with --score")
    ap.add_argument("--extract", nargs=2, metavar=("DEBUG_JSON", "NAME"), help="save a run's transcript to bench/private")
    a = ap.parse_args()
    if a.extract:
        return extract(*a.extract)
    if a.score:
        keys = {k: v["key"] for k, v in MEETINGS.items()} | {"leetcode_site": LEETCODE_KEY}
        if a.meeting not in keys:
            sys.exit(f"--meeting must be one of {sorted(keys)}")
        d = json.loads(Path(a.score).read_text())
        r = score(d["flow"], d["board"], keys[a.meeting])
        snaps = d.get("snapshots")
        if snaps is None:
            from scripts.replay import replay          # live run: rebuild the map after every round
            _, snaps, _ = replay(d)
        out_dir = OUT / "scored"
        out_dir.mkdir(parents=True, exist_ok=True)
        r["layout"] = layout_check(snaps, out_dir, Path(a.score).stem)
        print(fmt(a.meeting, r, f"(scored {Path(a.score).name})"))
        return
    asyncio.run(main_async(a))


if __name__ == "__main__":
    os.chdir(HERE.parent)
    main()
