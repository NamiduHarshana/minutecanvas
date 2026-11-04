"""v6.25: "Ask about this meeting" on the replay page.

Someone with the replay link asks a question; Muse Wonder (ASK_MODEL, Muse Spark as the fallback) answers ONLY from
the meeting's own transcript and names the transcript lines it used. Like the Flow Map's quote check, the model never
writes the quotes itself: it returns line ids, and the server keeps only ids that exist in this meeting and sends back
the real line (time, speaker, words). An answer that claims the meeting covered something but cites no real line is
turned into "not covered" — no evidence, no answer.

Anyone with a replay link can ask, so every question costs Meta credits: limits per IP, per replay and server-wide
(rolling 24 h, in memory = reset on backend restart). Nothing is stored: questions and answers are not saved.
"""
from __future__ import annotations

import gzip
import json
import logging
import time
from collections import OrderedDict, deque

from .config import settings
from .prompts import ASK_SCHEMA, ASK_SYSTEM
from .security import RateLimiter, clean_text

log = logging.getLogger("minutecanvas.ask")

MAX_LINES = 2500          # a 70-minute meeting has ~700 lines; longer logs keep the first 500 + the last 2000
MAX_QUOTES = 4
CACHE_SIZE = 16           # parsed transcripts kept in memory (replays being asked about right now)
NOT_COVERED = "The meeting didn't cover this."


class AskLimit(Exception):
    """A limit was reached; the message is shown to the person."""


class AskError(Exception):
    pass


def fmt_t(t: float) -> str:
    t = int(t)
    return f"{t // 3600}:{t // 60 % 60:02d}:{t % 60:02d}" if t >= 3600 else f"{t // 60:02d}:{t % 60:02d}"


def meeting_from_replay(raw: bytes) -> dict:
    """events.json.gz -> {"title", "lines": [{id, t, speaker (name), text}], "findings": [...]}.
    Speaker names: the final names of the meeting (a rename during the meeting applies to every line)."""
    data = json.loads(gzip.decompress(raw))
    names = data.get("speakers") or {}
    lines = []
    for _t, msg in data.get("events") or []:
        if isinstance(msg, dict) and msg.get("type") == "transcript_final" and msg.get("text"):
            label = str(msg.get("speaker") or "")
            lines.append({"id": int(msg.get("id", len(lines))), "t": float(msg.get("t") or 0),
                          "speaker": clean_text(names.get(label) or f"Speaker {label}", 40),
                          "text": clean_text(msg.get("text"), 4000)})
    if len(lines) > MAX_LINES:
        lines = lines[:500] + lines[-(MAX_LINES - 500):]
    found = [{"kind": f.get("kind"), "text": clean_text(f.get("text"), 200), "step": clean_text(f.get("step"), 80)}
             for f in (data.get("findings") or []) if isinstance(f, dict)]
    return {"title": clean_text(data.get("title"), 80), "lines": lines, "findings": found}


class Asker:
    def __init__(self, meta):
        self.meta = meta
        self.ip_limiter = RateLimiter(settings.ask_per_ip_per_hour, 3600)
        self.per_replay: dict[str, deque] = {}
        self.all: deque = deque()
        self.cache: OrderedDict[str, dict] = OrderedDict()

    # ---------------- limits ----------------
    def _take(self, rid: str, ip: str) -> None:
        now = time.time()
        day = 86400
        while self.all and now - self.all[0] > day:
            self.all.popleft()
        if settings.ask_daily_max > 0 and len(self.all) >= settings.ask_daily_max:
            raise AskLimit("Questions are paused for today. Please try again tomorrow.")
        q = self.per_replay.setdefault(rid, deque())
        while q and now - q[0] > day:
            q.popleft()
        if len(q) >= settings.ask_per_replay_per_day:
            raise AskLimit("This meeting has reached today's question limit. Please try again tomorrow.")
        if not self.ip_limiter.allow(ip):
            raise AskLimit("You've asked a lot of questions. Please wait a bit and try again.")
        q.append(now)
        self.all.append(now)
        # replays nobody asked about for a day leave memory
        for k in [k for k, v in self.per_replay.items() if not v or now - v[-1] > day]:
            del self.per_replay[k]

    def forget(self, rid: str) -> None:
        """The replay was deleted: drop its cached transcript."""
        self.cache.pop(rid, None)

    # ---------------- answer ----------------
    async def meeting(self, rid: str, store) -> dict:
        m = self.cache.get(rid)
        if m is None:
            from .replay import events_key
            raw = await store.get(events_key(rid))
            if raw is None:
                raise AskError("no replay")
            m = meeting_from_replay(raw)
            self.cache[rid] = m
            while len(self.cache) > CACHE_SIZE:
                self.cache.popitem(last=False)
        self.cache.move_to_end(rid)
        return m

    async def ask(self, rid: str, store, question: str, ip: str) -> dict:
        question = clean_text(question, 300)
        if len(question) < 3:
            raise AskError("empty question")
        m = await self.meeting(rid, store)
        if not m["lines"]:
            return {"answer": "Nobody spoke in this meeting, so there is nothing to answer from.", "covered": False,
                    "quotes": [], "by": None}
        self._take(rid, ip)
        user = json.dumps({"meeting_title": m["title"], "question": question,
                           "transcript": [f"L{x['id']} [{fmt_t(x['t'])}] {x['speaker']}: {x['text']}" for x in m["lines"]],
                           "findings": m["findings"]}, ensure_ascii=False)
        out, by = await self._request(user)
        by_id = {x["id"]: x for x in m["lines"]}
        ids: list[int] = []
        for v in out.get("lines") if isinstance(out.get("lines"), list) else []:
            try:
                i = int(str(v).lstrip("Ll"))
            except ValueError:
                continue
            if i in by_id and i not in ids:
                ids.append(i)
        ids = sorted(ids[:MAX_QUOTES], key=lambda i: by_id[i]["t"])
        covered = bool(out.get("covered")) and bool(ids)
        answer = clean_text(out.get("answer"), 700) if covered else NOT_COVERED
        quotes = [{"t": round(by_id[i]["t"], 1), "speaker": by_id[i]["speaker"], "text": clean_text(by_id[i]["text"], 400)}
                  for i in ids] if covered else []
        return {"answer": answer or NOT_COVERED, "covered": covered and bool(answer), "quotes": quotes, "by": by}

    async def _request(self, user: str) -> tuple[dict, str]:
        """Muse Wonder first (short tries), then Muse Spark — the same pattern as the report summary."""
        model = settings.ask_model
        if model and model != settings.spark_model:
            try:
                out = await self.meta.spark_json(ASK_SYSTEM, user, "ask_meeting", ASK_SCHEMA, model=model,
                                                 attempts=2, timeout=settings.ask_timeout_s)
                return (out if isinstance(out, dict) else {}), "Muse Wonder"
            except Exception as e:  # noqa: BLE001
                log.warning("ask with %s failed, using Muse Spark: %s", model, e)
        try:
            out = await self.meta.spark_json(ASK_SYSTEM, user, "ask_meeting", ASK_SCHEMA, attempts=2,
                                             timeout=settings.ask_timeout_s)
        except Exception as e:  # noqa: BLE001
            raise AskError(str(e)) from None
        return (out if isinstance(out, dict) else {}), "Muse Spark"
