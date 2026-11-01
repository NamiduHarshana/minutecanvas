"""Replay a DEBUG_DUMP json through the CURRENT Flow code, offline (no Meta API, no credits).

Every Flow agent call recorded in the json is applied again, in the same order, to a fresh Flow:
  - calls that were accepted in the run are applied again (current code may now reject them),
  - calls that were rejected in the run are applied again too with --all (to see what current code says).
Second-opinion Spark checks are NOT re-run (they need the API); with --all their recorded verdicts are ignored.

Usage (from backend/):
  python scripts/replay.py debug/<file>.json                 # summary of differences
  python scripts/replay.py debug/<file>.json --snapshots out.json   # flow after every round (for the layout check)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.flow import Flow  # noqa: E402


def _args(raw):
    if isinstance(raw, str):
        try:
            return json.loads(raw or "{}")
        except ValueError:
            return None
    return raw


_REFS = ("after", "before", "step", "from_step", "to_step", "item_id")


def replay(dump: dict, all_calls: bool = False) -> tuple[Flow, list[dict], list[dict]]:
    transcript = dump["transcript"]
    flow = Flow()
    snaps: list[dict] = []
    diffs: list[dict] = []
    # ids the run gave to new things -> ids current code gives them. When current code adds a box the run did not
    # (or the other way round), later ids shift; the recorded calls still use the run's ids.
    ids: dict[str, str] = {}
    for c in dump.get("calls", []):
        if c.get("kind") != "flow":
            continue
        mt = float(c.get("meeting_t", 0))
        lines = [x for x in transcript if float(x.get("t_end", x.get("t", 0))) <= mt + 0.5]
        mode = c.get("mode")
        flow.gap_budget = 1 if mode in ("live", "gap_check") else 2
        flow.mode = mode
        flow.latest_t = lines[-1]["t"] if lines else None
        flow.round_new = set()
        for turn in c.get("turns", []):
            for call in turn.get("calls", []) or []:
                was_ok = bool((call.get("result") or {}).get("ok"))
                verifier = "second-opinion" in str((call.get("result") or {}).get("error", "")) or \
                    "already answered at" in str((call.get("result") or {}).get("error", ""))
                if not was_ok and not all_calls:
                    continue
                if not was_ok and verifier:
                    pass                            # recorded verdict ignored with --all
                args = _args(call.get("arguments"))
                if isinstance(args, dict):
                    args = {k: (ids.get(v, v) if k in _REFS and isinstance(v, str) else v) for k, v in args.items()}
                res = flow.apply(call.get("name"), args, lines, mt)
                then = call.get("result") or {}
                if was_ok and res.ok and isinstance(res.message, dict) and then.get("id") and res.message.get("id") \
                        and str(then.get("note", "added")).startswith("added") \
                        and str(res.message.get("note", "added")).startswith("added"):
                    ids[then["id"]] = res.message["id"]
                if res.ok != was_ok:
                    diffs.append({"meeting_t": mt, "mode": mode, "name": call.get("name"), "args": args,
                                  "then": call.get("result"), "now": res.message})
        flow.round_new = set()
        snaps.append({"meeting_t": mt, "mode": mode, "flow": flow.public()})
    return flow, snaps, diffs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dump")
    ap.add_argument("--all", action="store_true", help="also re-apply calls that were rejected in the run")
    ap.add_argument("--snapshots", help="write the flow after every round to this json file")
    a = ap.parse_args()
    dump = json.loads(Path(a.dump).read_text())
    flow, snaps, diffs = replay(dump, a.all)
    print(f"rounds {len(snaps)}  steps {len(flow.steps)}  links {len(flow.links)}  gaps {len(flow.gaps)}  "
          f"contradictions {len(flow.contradictions)}")
    for d in diffs:
        print(f"[{d['meeting_t']:7.1f} {d['mode']}] {d['name']} {json.dumps(d['args'])[:160]}")
        print(f"     then: {json.dumps(d['then'])[:200]}")
        print(f"     now:  {json.dumps(d['now'])[:200]}")
    if a.snapshots:
        Path(a.snapshots).write_text(json.dumps(snaps, indent=1, default=str))
        print("snapshots ->", a.snapshots)


if __name__ == "__main__":
    main()
