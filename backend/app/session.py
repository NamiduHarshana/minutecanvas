"""One meeting = one Session (in memory; Cloud Run must run with --max-instances 1)."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import secrets
import time
import uuid
from datetime import datetime, timezone

from fastapi import WebSocket

from .audio import AudioBuffer
from .board import Board, Card
from .config import BYTES_PER_SEC, settings
from .flow import (MAX_OPEN_GAPS, Flow, Result, _similar, check_quote, key_words, replaced_things, tokens,
                   with_usual_names)
from .meta_api import MetaClient, ToolsUnsupported, image_mime
from .replay import Recorder, events_key, image_key, make_store, new_owner_key, new_replay_id
from .meta_voice import VoiceStream
from .prompts import (FLOW_JSON_NOTE, FLOW_JSON_SCHEMA, FLOW_SYSTEM, FLOW_TOOLS, SPARK_SCHEMA, SPARK_SYSTEM,
                      GAP_STATUS_SCHEMA, GAP_STATUS_SYSTEM, SUMMARY_SCHEMA, SUMMARY_SYSTEM, VERIFY_CONFLICT_SCHEMA, VERIFY_CONFLICT_SYSTEM,
                      VERIFY_FLOW_SCHEMA, VERIFY_FLOW_SYSTEM)
from .security import RateLimiter, clean_text

log = logging.getLogger("minutecanvas.session")

TRANSCRIPT_SNAPSHOT = 300   # turns sent in "hello" after a reconnect
# Already-mapped lines shown to the Flow agent in live rounds, so it knows which part of the product people are on.
# v6.6 bench: the last 6 lines were half fillers ("uh-huh", "Oh, yeah.") and none named the section ("for behavioral
# coaching…" was 22 lines back), so "Schedule session" was hung after the last step of another part. Now: the lines
# of the last CONTEXT_S seconds, fillers left out, at least CONTEXT_MIN_LINES and at most CONTEXT_MAX_LINES.
CONTEXT_S = 150.0
CONTEXT_MIN_LINES = 6
CONTEXT_MAX_LINES = 25
FILLER_MAX_WORDS = 3        # "Mm-hmm.", "Oh, yeah.", "Ah, I guess"
CARDS_CONTEXT_LINES = 16    # cards round: enough to see a question answered right after it was asked
NUMBER_LINES = 30           # older lines with numbers shown to the Flow agent (contradictions across the meeting)
MAX_SPARK_LINES = 120       # cap per call (long meetings: only new transcript + board)
# Agent turns per round. The agent works in turns: it sends some calls, reads the results (new step ids) and goes on
# until it answers "done". v6.6 stopped after the first turn whenever all its calls were accepted: in the v6.6 bench
# every round with calls was cut there ("Mapping … adding the staff login and bookings view" -> 1 call, cut), so steps,
# links and the final pass's gaps were lost. The cap only stops a runaway agent.
# v6.7 bench: the agent sends about ONE call per turn, so a 5-turn cap cut 9 live rounds (release sync: "Success
# screen", "Account locked" and the bug were left for the final pass). Caps are higher now, with a time limit per
# round, and a round that is still cut tells the next live round (previous_round_cut_off).
FLOW_MAX_TURNS = {"live": 10, "gap_check": 4, "gap_review": 10, "final": 12}
FLOW_ROUND_MAX_S = {"live": 60.0, "gap_check": 40.0, "gap_review": 90.0, "final": 120.0}   # no new turn after this
FLOW_MAX_CALLS_PER_TURN = 20
REVIEW_CONTEXT_LINES = 80   # gap review / final pass: recent lines shown for context
HINT_LOOKBACK = 300         # older lines compared with the new ones for number changes
FLOW_WAIT_S = 60.0         # cards wait at most this long for the Flow round of the same tick (it may take a few turns)
_HAS_NUMBER = re.compile(r"\d|\b(?:two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|fifteen|twenty|thirty|"
                         r"forty|fifty|hundred|thousand)\b", re.I)


def is_filler(x: dict) -> bool:
    return len(tokens(x.get("text", ""))) <= FILLER_MAX_WORDS


def live_context_start(lines: list[dict], start: int) -> int:
    """Index of the first context line before lines[start] for a live round (see CONTEXT_S)."""
    if start <= 0:
        return 0
    t_new = float(lines[start]["t"]) if start < len(lines) else float(lines[start - 1]["t"])
    kept, i = 0, start
    while i > 0 and kept < CONTEXT_MAX_LINES:
        x = lines[i - 1]
        if kept >= CONTEXT_MIN_LINES and t_new - float(x["t"]) > CONTEXT_S:
            break
        i -= 1
        if not is_filler(x):
            kept += 1
    return i


def fmt_t(t: float) -> str:
    t = int(t)
    return f"{t // 3600}:{t // 60 % 60:02d}:{t % 60:02d}" if t >= 3600 else f"{t // 60:02d}:{t % 60:02d}"


class Session:
    def __init__(self, meta: MetaClient, keywords: list[str], title: str | None, ip: str):
        self.id = uuid.uuid4().hex
        self.token = secrets.token_urlsafe(32)
        self.ip = ip
        self.meta = meta
        self.created_mono = time.monotonic()
        self.created_wall = datetime.now(timezone.utc)
        self.last_seen = time.monotonic()
        self.keywords = keywords
        self.state = "created"            # created / recording / paused / stopping / done
        self.audio = AudioBuffer(settings.max_backlog_s, settings.voice_resend_max_s)
        self.voice: VoiceStream | None = None
        self.board = Board()
        if title:
            self.board.title = title
        self.transcript: list[dict] = []  # final turns
        self.partials: dict[str, dict] = {}
        self.speaker_names: dict[str, str] = {}
        self.spark_done_idx = 0
        self.spark_calls = 0
        self.spark_lock = asyncio.Lock()
        self.images: dict[str, tuple[bytes, str]] = {}
        self.images_made = 0
        self.image_sem = asyncio.Semaphore(settings.image_concurrency)
        self.image_tasks: set[asyncio.Task] = set()
        self.flow = Flow()
        self.flow_done_idx = 0
        self.flow_calls = 0
        self.flow_cut = False          # the last live round ran out of turns / time with the agent still mapping
        self.flow_stats = {"accepted": 0, "rejected": 0, "rounds": 0}
        self.conflict_checks = 0
        self.flow_checks = 0
        self.gap_seen_upto: dict[str, int] = {}   # gap id -> transcript length at its last re-check
        self.last_gap_review = -1e9
        self.last_auto_check = time.monotonic()
        self.auto_check_shape = (0, 0)   # (steps, links) at the last automatic gap check
        self.debug_calls: list[dict] = []
        self.report: dict | None = None
        self.socket: WebSocket | None = None
        self.send_lock = asyncio.Lock()
        self.tasks: list[asyncio.Task] = []
        self.voice_status = "idle"
        self.voice_log: list[dict] = []   # voice sessions that ended: errors, resent audio, pacer stalls (debug)
        self.recording_mono = time.monotonic()
        self.recorder: Recorder | None = Recorder() if settings.replay_enabled else None
        self.store = None                  # replay storage, set by SessionManager
        self.library = None                # accounts.Library, set by SessionManager
        self.owner: dict | None = None     # signed-in user who started it ({"uid","email","name"}), or None
        self.replay_id: str | None = None
        self.replay_key: str | None = None # secret owner key (deletes the replay); only this browser gets it

    # ---------------- clock ----------------
    @property
    def meeting_t(self) -> float:
        """Meeting time = audio received (session-wide, survives reconnects)."""
        return self.audio.received_bytes / BYTES_PER_SEC

    # ---------------- socket ----------------
    async def attach(self, ws: WebSocket):
        old, self.socket = self.socket, ws
        self.last_seen = time.monotonic()
        if old is not None and old is not ws:
            asyncio.create_task(_close_quietly(old))   # don't await the dead socket
        await self.send({"type": "hello", **self.snapshot()})

    def detach(self, ws: WebSocket):
        if self.socket is ws:
            self.socket = None
        self.last_seen = time.monotonic()

    async def send(self, msg: dict):
        if self.recorder is not None:
            self.recorder.add(self.meeting_t, msg)       # kept even while no browser is connected
        ws = self.socket                                  # always the newest socket
        if ws is None:
            return
        try:
            async with self.send_lock:
                await asyncio.wait_for(ws.send_text(json.dumps(msg, separators=(",", ":"))), timeout=5)
        except Exception:  # noqa: BLE001 - dead socket; client will reconnect
            if self.socket is ws:
                self.socket = None

    def snapshot(self) -> dict:
        return {
            "session_id": self.id,
            "state": self.state,
            "audio_bytes_received": self.audio.received_bytes,
            "meeting_t": round(self.meeting_t, 1),
            "board": self.board.public(),
            "flow": self.flow.public(shown=True),
            "flow_stats": self.flow_stats,
            "transcript": self.transcript[-TRANSCRIPT_SNAPSHOT:],
            "partials": list(self.partials.values()),
            "speakers": self.speaker_names,
            "voice": self.voice_status,
            "report": self.report,
            "replay_id": self.replay_id,
            "replay_key": self.replay_key,
            "replay_info": self.replay_info() if self.replay_id else None,
            "limits": {"images_left": max(0, settings.max_images - self.images_made),
                       "spark_calls_left": max(0, settings.max_spark_calls - self.spark_calls)},
        }

    # ---------------- audio in ----------------
    def on_audio_frame(self, frame: bytes):
        if self.state not in ("recording", "paused", "stopping") or len(frame) <= 8:
            return
        offset = int.from_bytes(frame[:8], "little")
        pcm = frame[8:]
        # A mic can't make audio faster than real time: anything beyond (elapsed + 5 s) is fake.
        # This also stops a huge offset from allocating a giant "gap" of silence.
        allowed_end = int((time.monotonic() - self.recording_mono + 5) * BYTES_PER_SEC)
        if offset >= allowed_end:
            return
        if offset + len(pcm) > allowed_end:
            pcm = pcm[: allowed_end - offset]
        self.audio.append_at(offset, pcm)

    # ---------------- lifecycle ----------------
    async def start(self):
        if self.state != "created":
            return
        self.state = "recording"
        self.recording_mono = time.monotonic()
        self.voice = VoiceStream(self.audio, self._on_voice, self.keywords)
        self.voice.start()
        self.tasks.append(asyncio.create_task(self._spark_loop()))
        await self.send({"type": "state", "state": self.state})

    async def set_paused(self, paused: bool):
        if self.state in ("recording", "paused"):
            self.state = "paused" if paused else "recording"
            await self.send({"type": "state", "state": self.state})

    async def stop(self):
        if self.state not in ("recording", "paused"):
            return
        self.state = "stopping"
        await self.send({"type": "state", "state": self.state, "step": "transcript"})
        # let the pacer send what is left, then end the voice stream. v6.5 run: after a network stall the backlog was
        # ~23 s; Stop waited 10 s at most and the last ~13 s of the meeting were never transcribed.
        limit = time.monotonic() + min(settings.max_backlog_s, self.audio.backlog_s * 1.3 + 5)
        while self.audio.backlog_s >= 0.3 and time.monotonic() < limit:
            await asyncio.sleep(0.1)
        if self.voice:
            await self.voice.stop(wait_s=15)
        for p in list(self.partials.values()):          # unfinished turn -> keep it (at the time it was said)
            if p.get("text"):
                await self._add_final(p)
        self.partials.clear()
        self._debug("voice", sent_s=round(self.voice.sent_bytes_total / BYTES_PER_SEC, 1) if self.voice else 0,
                    received_s=round(self.meeting_t, 1), dropped_s=round(self.audio.dropped_bytes / BYTES_PER_SEC, 1),
                    skipped_s=round(self.audio.skipped_bytes / BYTES_PER_SEC, 1),
                    resent_s=round(self.audio.resent_bytes / BYTES_PER_SEC, 1), sessions=self.voice_log)

        await self.send({"type": "state", "state": self.state, "step": "cards"})
        await self.spark_once(final=True)

        await self.send({"type": "state", "state": self.state, "step": "images"})
        await self._wait_images(60)

        await self.send({"type": "state", "state": self.state, "step": "report"})
        self.report = await self._build_report()
        self.state = "done"
        self._debug("report", report=self.report)
        for t in self.tasks:
            t.cancel()
        await self.send({"type": "report_ready", "report": self.report})
        # the browser closes its socket on "done", so the replay id must arrive before it
        done = {"type": "state", "state": self.state}
        await self._save_replay(last=done)
        await self.send(done)

    # ---------------- replay ----------------
    async def _save_replay(self, last: dict):
        """After Stop: the message log (+ `last`, the closing "done" message) and the card images -> storage; the
        browser gets the share id."""
        if self.recorder is None or self.store is None or not self.recorder.events:
            return
        self.recorder.add(self.meeting_t, last)
        rid = new_replay_id()
        key = new_owner_key()
        sem = asyncio.Semaphore(8)

        async def put_image(img_id: str, raw: bytes, mime: str):
            async with sem:
                await self.store.put(image_key(rid, img_id), raw, mime)

        try:
            await asyncio.gather(*(put_image(i, raw, mime) for i, (raw, mime) in list(self.images.items())))
            data = self.recorder.payload(self.id, rid, self.board.title or (self.report or {}).get("title"),
                                         self.created_wall.isoformat(), self.meeting_t,
                                         {k: self.speaker_name(k) for k in sorted({x["speaker"] for x in self.transcript})})
            await self.store.put(events_key(rid), data, "application/gzip")
            self.replay_id = rid
            if self.library is not None:
                await self.library.saved(rid, key, self.replay_info(), self.owner)
        except Exception as e:  # noqa: BLE001
            log.warning("replay save failed: %s", e)
            self.replay_id = None
            await self.send({"type": "notice", "message": "The replay couldn't be saved."})
            return
        self.replay_key = key
        self.recorder = None                  # saved; free the memory
        log.info("replay %s saved (%d KB)", rid, len(data) // 1024)
        # not in Recorder.KEEP: the owner key never goes into the replay itself
        await self.send({"type": "replay_ready", "replay_id": rid, "replay_key": key, "info": self.replay_info()})

    def replay_info(self) -> dict:
        """Dashboard row: title, date, length, open gaps / conflicts at the end."""
        gaps = sum(1 for g in self.flow.gaps.values() if not g.resolved and not g.dismissed)
        conflicts = sum(1 for c in self.flow.contradictions.values() if not c.resolved)
        return {"title": self.board.title or (self.report or {}).get("title") or None,
                "date": self.created_wall.isoformat(), "duration_s": round(self.meeting_t, 1),
                "gaps": gaps, "conflicts": conflicts}

    # ---------------- transcript ----------------
    async def _on_voice(self, kind: str, data: dict):
        if kind == "status":
            self.voice_status = data.get("voice", self.voice_status)
            await self.send({"type": "voice_status", **data})
            return
        if kind == "debug":
            self._debug("voice_debug", **data)
            return
        if kind == "gen_end":
            await self._voice_gen_end(data)
            return
        turn = data["turn"]
        if kind == "partial":
            if not data.get("text"):
                return
            p = {"turn": turn, "speaker": data.get("speaker"), "text": data["text"]}   # speaker None = "speaking…"
            if data.get("t_start") is not None:
                p["t_start"] = data["t_start"]
            self.partials[turn] = p
            await self.send({"type": "transcript_partial", **p})
        elif kind == "final":
            # Meta partials have no turnId (key "<gen>-live") but finals do, so a final
            # clears both; otherwise Stop re-adds the old partial as a duplicate line.
            gen = turn.split("-", 1)[0]
            for key in {turn, f"{gen}-live"}:
                if self.partials.pop(key, None) is not None:
                    await self.send({"type": "transcript_partial_clear", "turn": key})
            if data.get("text"):
                await self._add_final(data)

    async def _voice_gen_end(self, data: dict):
        """A Meta voice session ended. Its unfinished partials must not stay on screen until Stop (Oct 29 run: one
        sat in the sidebar from 04:28 to the end, then was added at 31:55). If its audio is sent again to the next
        session, that session writes the turn again -> drop them; otherwise keep the text at the time it was said."""
        gen = str(data.get("gen"))
        self.voice_log.append({**data, "meeting_t": round(self.meeting_t, 1)})
        self._debug("voice_session_end", **data)
        for key, p in list(self.partials.items()):
            if key.split("-", 1)[0] != gen:
                continue
            self.partials.pop(key, None)
            await self.send({"type": "transcript_partial_clear", "turn": key})
            if not data.get("resent_s") and p.get("text") and self.state in ("recording", "paused"):
                await self._add_final(p)

    def _last_speaker(self) -> str:
        return self.transcript[-1]["speaker"] if self.transcript else "A"

    def _line_time(self, data: dict) -> float:
        """Line time = when the speech STARTED (Meta speechStart), not when the final text arrived at the end of the
        turn. Kept in order (never before the previous line) and never in the future."""
        now = self.meeting_t
        t = data.get("t_start")
        t = now if t is None else min(float(t), now)
        if self.transcript:
            t = max(t, self.transcript[-1]["t"])
        return round(max(0.0, t), 1)

    async def _add_final(self, data: dict):
        item = {
            "id": len(self.transcript),
            "turn": data["turn"],
            "speaker": data.get("speaker") or self._last_speaker(),
            "text": clean_text(data["text"], 4000),
            "t": self._line_time(data),
            "t_end": round(self.meeting_t, 1),
        }
        self.transcript.append(item)
        await self.send({"type": "transcript_final", **item})

    def speaker_name(self, label: str) -> str:
        return self.speaker_names.get(label) or f"Speaker {label}"

    async def rename_speaker(self, label: str, name: str):
        label = clean_text(label, 12)
        name = clean_text(name, 40)
        if not label:
            return
        if name and label not in self.speaker_names and len(self.speaker_names) >= 50:
            return
        old_display = self.speaker_name(label)
        if name:
            self.speaker_names[label] = name
        else:
            self.speaker_names.pop(label, None)
        new_display = self.speaker_name(label)
        changed = [c for c in self.board.cards.values() if c.owner and c.owner == old_display]
        for c in changed:
            c.owner = new_display
        await self.send({"type": "speakers", "speakers": self.speaker_names})
        if changed:
            await self.send({"type": "cards_updated", "cards": [c.public() for c in changed]})

    # ---------------- Spark ----------------
    async def _spark_loop(self):
        while self.state in ("recording", "paused"):
            await asyncio.sleep(settings.spark_interval_s)
            if self.state not in ("recording", "paused"):
                break
            if time.monotonic() - self.created_mono > settings.max_session_minutes * 60:
                await self.send({"type": "notice", "message": "Session time limit reached. Stopping."})
                asyncio.create_task(self.stop())
                break
            new = self.transcript[self.spark_done_idx:]
            if sum(len(x["text"]) for x in new) >= settings.spark_min_new_chars:
                await self.spark_once()
            if self.state in ("recording", "paused"):
                await self.auto_gap_check()

    async def auto_gap_check(self) -> bool:
        """Every few minutes: one gap check over the whole flow. Live rounds only see ~45 s of talk, but a missing
        path ("what happens after they submit?") only shows once the group has moved on to something else."""
        if settings.auto_gap_check_s <= 0 or len(self.flow.steps) < settings.auto_gap_min_steps:
            return False
        if time.monotonic() - max(self.last_auto_check, self.last_gap_review) < settings.auto_gap_check_s:
            return False
        shape = (len(self.flow.steps), len(self.flow.links))
        if shape == self.auto_check_shape:      # nothing new on the map since the last check
            return False
        self.last_auto_check = time.monotonic()
        self.auto_check_shape = shape
        async with self.spark_lock:
            await self.recheck_gaps()
            await self._flow_round("gap_check")
        return True

    # ---------------- open gaps: re-checked while the meeting goes on ----------------
    async def recheck_gaps(self) -> list[dict]:
        """A gap is not checked once and forgotten: each automatic check (and the final pass) asks again, for every
        open gap with new lines since its last check, whether it was answered later, was already answered before it
        was flagged (then it was a false gap and is removed), or was renamed. Code checks the line Spark points to.
        Gaps the user resolved or reopened are left alone."""
        if not settings.recheck_gaps:
            return []
        changes: list[dict] = []
        cards_changed: list[Card] = []
        cards_deleted: list[str] = []
        lines = list(self.transcript)
        todo = [g for g in self.flow.open_gaps() if not g.user_set and self.gap_seen_upto.get(g.id, -1) < len(lines)]
        for g in todo[:settings.max_gap_rechecks]:
            self.gap_seen_upto[g.id] = len(lines)
            step = self.flow.steps.get(g.step)
            ev = (g.evidence or (step.evidence if step else []) or [None])[0]
            if not step or ev is None:
                continue
            about = self._answer_search_lines(step, ev, g.question, g.branch, lines)
            since = [self._line(x) for x in lines if x["t"] >= g.t - 5][-30:]
            payload = {"gap": g.question, "step": step.label, "flagged_at": fmt_t(g.t),
                       "lines_about_the_step": about, "lines_said_after_it_was_flagged": since}
            try:
                out = await self.meta.spark_json(GAP_STATUS_SYSTEM, json.dumps(payload, ensure_ascii=False),
                                                 "gap_status", GAP_STATUS_SCHEMA)
            except Exception as e:  # noqa: BLE001 - keep the gap as it is
                log.warning("gap re-check failed: %s", e)
                continue
            self._debug("gap_recheck", payload=payload, response=out)
            ch = self._apply_gap_status(g, out if isinstance(out, dict) else {}, lines, cards_changed, cards_deleted)
            if ch:
                changes.append(ch)
        for cid in cards_deleted:
            await self.send({"type": "card_deleted", "id": cid})
        if cards_changed:
            await self.send({"type": "cards_updated", "cards": [c.public() for c in cards_changed]})
        if changes:
            await self.send_flow(changes)
        return changes

    def _line_at(self, lines: list[dict], at: str, before: float | None = None, after: float | None = None) -> dict | None:
        at = (at or "").strip().strip("[]")
        for x in lines:
            if fmt_t(x["t"]) == at and (before is None or x["t"] < before) and (after is None or x["t"] >= after):
                return x
        return None

    def _apply_gap_status(self, g, out: dict, lines: list[dict], changed: list[Card], deleted: list[str]) -> dict | None:
        from .flow import _ev
        status = out.get("status")
        card = self.board.cards.get(g.card_id or "")
        if status == "answered_later":
            x = self._line_at(lines, out.get("line"), after=g.t - 5)
            if not x:
                return None                                  # no real line after the gap: keep it open
            g.resolved, g.answer = True, clean_text(out.get("answer"), 160) or g.answer
            g.resolved_by = _ev(x, clean_text(x.get("text", ""), 300))
            c = self.board.resolve_gap_question(g.card_id, g.answer, self.meeting_t, x["t"])
            if c:
                changed.append(c)
            return {"kind": "gap_resolved", "id": g.id, "step": g.step, "text": f"Gap answered: {g.question}"}
        if status == "answered_before":
            x = self._line_at(lines, out.get("line"), before=g.t)
            if not x:
                return None
            g.dismissed = True
            g.removed_why = f"already answered at {fmt_t(x['t'])} (\"{clean_text(x.get('text', ''), 80)}\")"
            if card and g.card_owned and not card.user_locked:
                self.board.user_delete(card.id)              # also stops Spark from adding it again
                deleted.append(card.id)
            elif card:                                       # a question that existed before the gap: keep it
                card.flow_ref = None
                changed.append(card)
            return {"kind": "gap_removed", "id": g.id, "step": g.step,
                    "text": f"Not a gap (answered at {fmt_t(x['t'])}): {g.question}"}
        if status == "reworded":
            q = clean_text(out.get("new_question"), 160)
            if not q or q == g.question or not self._line_at(lines, out.get("line"), after=g.t - 5):
                return None                                  # the renaming line must really be there
            g.question = q.rstrip(".!") + ("" if q.endswith("?") else "?")
            if card and not card.user_locked:
                card.title = clean_text(g.question, 80)
                changed.append(card)
            return {"kind": "gap_moved", "id": g.id, "step": g.step, "text": g.question}
        return None

    async def spark_once(self, final: bool = False):
        """One round: the cards call and the Flow agent run in parallel (no extra delay). The cards are applied
        only after the Flow round, so a card that repeats a step added in this same round is still caught."""
        async with self.spark_lock:
            flow_done = asyncio.Event()

            async def flow():
                try:
                    if final:
                        await self.recheck_gaps()
                    await self._flow_round("final" if final else "live")
                finally:
                    flow_done.set()
            await asyncio.gather(self._cards_round(final, flow_done), flow())

    def _steps_for_cards(self) -> list[dict]:
        return [{"label": st.label, "times": [e.t for e in st.evidence]} for st in self.flow.steps.values()][:60]

    async def _cards_round(self, final: bool, flow_done: asyncio.Event | None = None):
        if True:
            end = len(self.transcript)
            new = self.transcript[self.spark_done_idx:end]
            if not new:
                return
            if self.spark_calls >= settings.max_spark_calls:
                await self.send({"type": "notice", "message": "AI card limit for this session reached."})
                return
            new = new[-MAX_SPARK_LINES:]
            context = self.transcript[max(0, self.spark_done_idx - CARDS_CONTEXT_LINES):self.spark_done_idx]
            steps = [st.label for st in self.flow.steps.values()][:40]
            payload = {
                "current_board": self.board.for_spark(),
                "board_title": self.board.title,
                "deleted_titles": self.board.deleted_titles[-30:],
                "flow_steps": steps,
                "open_conflicts": [{"id": c.id, "note": c.note, "a": c.a.quote, "b": c.b.quote}
                                   for c in self.flow.contradictions.values() if not c.resolved][:10],
                "earlier_lines_for_context_only": [self._line(x) for x in context],
                "new_transcript": [self._line(x) for x in new],
                "is_final_pass": final,
            }
            stale = self._stale_card_hints(new)
            if stale:
                payload["code_hints"] = {"replaced": stale}
            self.spark_calls += 1
            await self.send({"type": "spark_status", "state": "thinking"})
            t0 = time.monotonic()
            try:
                ops = await self.meta.spark_json(SPARK_SYSTEM, json.dumps(payload, ensure_ascii=False), "board_ops", SPARK_SCHEMA)
            except Exception as e:  # noqa: BLE001 - keep the lines, retry next tick
                log.warning("spark failed: %s", e)
                self._debug("cards", final=final, payload=payload, error=str(e))
                await self.send({"type": "spark_status", "state": "error"})
                return
            self.spark_done_idx = end
            self._debug("cards", final=final, seconds=round(time.monotonic() - t0, 1), payload=payload, response=ops)
            if flow_done is not None and not flow_done.is_set():
                try:                       # usually a few seconds; never hold the cards for long
                    await asyncio.wait_for(flow_done.wait(), timeout=FLOW_WAIT_S)
                except asyncio.TimeoutError:
                    pass
            ch = self.board.apply_spark(ops, self.meeting_t, self._steps_for_cards())
            for c in self.board.link_conflicts(list(self.flow.contradictions.values())):
                if c not in ch.added and c not in ch.updated:
                    ch.updated.append(c)
            log.info("spark %.1fs: +%d cards, %d updated", time.monotonic() - t0, len(ch.added), len(ch.updated))
            if self.board.title and ops.get("meeting_title"):
                await self.send({"type": "board_title", "title": self.board.title})
            for c in ch.added:
                self._queue_image(c)
            if ch.added:
                await self.send({"type": "cards_added", "cards": [c.public() for c in ch.added]})
            if ch.updated:
                await self.send({"type": "cards_updated", "cards": [c.public() for c in ch.updated]})
            await self.send({"type": "spark_status", "state": "idle", "seconds": round(time.monotonic() - t0, 1)})
            # a gap question answered on the board -> the gap on the Flow Map is answered too
            gap_changed = False
            for c in ch.updated:
                g = self.flow.gaps.get(c.flow_ref or "")
                if g and c.resolved and not g.resolved:
                    g.resolved, g.answer = True, c.answer
                    gap_changed = True
            if gap_changed:
                await self.send_flow([])

    def _stale_card_hints(self, new: list[dict]) -> list[dict]:
        """v6.5 run: "instead of the Trello board … a form" — Spark updated 3 cards but one card still said "Trello
        questions" (and on the first replacement it updated none). Code lists the cards that still name the replaced
        thing; Spark decides which really still describe the old plan.
        v6.6 bench: "instead of a Trello board … just put a form there" listed the open question "Where should
        behavioral questions live?" only as a card to update, and Spark left it open. Open questions are marked, so
        the prompt can say the line may answer them; answered questions are history and are not listed."""
        out = []
        for x in new:
            earlier = [y for y in self.transcript if y.get("id", 0) < x.get("id", 0)]
            for words in replaced_things(x.get("text", "")):
                words = with_usual_names(words, earlier)
                cards = [{"id": c.id, "title": c.title, **({"open_question": True} if c.column == "questions" else {})}
                         for c in self.board.cards.values()
                         if not c.user_locked and not (c.column == "questions" and c.resolved)
                         and words & key_words(f"{c.title} {c.detail}")]
                if cards:
                    out.append({"line": self._line(x), "old_thing_words": sorted(words), "cards_to_check": cards[:8]})
        return out[:3]

    # ---------------- debug dump (DEBUG_DUMP=1, local testing only) ----------------
    def _debug(self, kind: str, **data):
        if not settings.debug_dump:
            return
        self.debug_calls.append({"kind": kind, "meeting_t": round(self.meeting_t, 1),
                                 "wall": datetime.now(timezone.utc).isoformat(timespec="seconds"), **data})
        snap = {
            "session": self.id, "created": self.created_wall.isoformat(), "state": self.state,
            "speakers": self.speaker_names, "transcript": self.transcript, "board": self.board.public(),
            "flow": self.flow.public(), "flow_stats": self.flow_stats, "calls": self.debug_calls,
        }
        path = os.path.join(settings.debug_dir, f"{self.created_wall.strftime('%Y%m%d-%H%M%S')}-{self.id[:8]}.json")
        try:
            os.makedirs(settings.debug_dir, exist_ok=True)
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(snap, f, ensure_ascii=False, indent=1, default=str)
            os.replace(tmp, path)
        except OSError as e:
            log.warning("debug dump failed: %s", e)

    def _line(self, x: dict) -> str:
        return f"[{fmt_t(x['t'])}] {self.speaker_name(x['speaker'])}: {x['text']}"

    # ---------------- Flow Map (Spark Flow agent, tool calling) ----------------
    async def send_flow(self, events: list[dict]):
        await self.send({"type": "flow", "flow": self.flow.public(shown=True), "events": events, "stats": self.flow_stats})

    async def gap_review(self) -> bool:
        """The user pressed "Check gaps". Runs one review round now (cooldown protects credits)."""
        if self.state not in ("recording", "paused") or self.flow.empty():
            return False
        if time.monotonic() - self.last_gap_review < settings.gap_review_cooldown_s:
            await self.send({"type": "notice", "message": "Gap check already ran a moment ago."})
            return False
        self.last_gap_review = time.monotonic()
        async with self.spark_lock:
            await self._flow_round("gap_review")
        return True

    async def _flow_round(self, mode: str):
        """mode: live (new lines) / gap_review (user asked) / gap_check (automatic) / final (after Stop)."""
        if not settings.flow_enabled:
            return
        end = len(self.transcript)
        new = self.transcript[self.flow_done_idx:end]
        if mode in ("gap_review", "gap_check"):   # only review; unprocessed lines stay for the next live round
            end = self.flow_done_idx
            new = []
        if mode == "live" and not new:
            return
        if mode != "live" and self.flow.empty() and not new:
            return
        if self.flow_calls >= settings.max_flow_calls:
            if mode != "final":
                await self.send({"type": "notice", "message": "Flow Map limit for this session reached."})
            return
        new = new[-MAX_SPARK_LINES:]
        start = end - len(new)
        lines_all = len(self.transcript)
        if mode == "live":
            first = live_context_start(self.transcript, start)
            context = [x for x in self.transcript[first:start] if not is_filler(x)]
        else:
            first = max(0, start - REVIEW_CONTEXT_LINES)
            context = self.transcript[first:start]
        older = self.transcript[:first]
        payload = {
            "mode": mode,
            "current_flow": self.flow.for_agent(),
            "earlier_lines_with_numbers": [self._line(x) for x in older if _HAS_NUMBER.search(x["text"])][-NUMBER_LINES:],
            "earlier_lines_for_context_only": [self._line(x) for x in context],
            "new_transcript": [self._line(x) for x in new],
        }
        if mode in ("live", "final") and self.flow_cut:
            payload["previous_round_cut_off"] = True
        if mode in ("gap_review", "gap_check") and lines_all > end:
            # v6.5 run: a gap check flagged "Who reviews the Trello board?" while the line that answers it was
            # already said but not mapped yet (the live round maps it next)
            payload["latest_lines_not_mapped_yet"] = [self._line(x) for x in self.transcript[end:lines_all]]
        hint_new = new or context                 # review rounds: look at the recent lines
        if hint_new:
            h0 = hint_new[0].get("id", 0)
            hints = self.flow.hints(hint_new, [x for x in self.transcript[max(0, h0 - HINT_LOOKBACK):h0]])
            code_hints = {}
            if hints["number_changes"]:
                code_hints["number_changes"] = [{"older": self._line(a), "newer": self._line(b), "counts": unit}
                                                for a, b, unit in hints["number_changes"]]
            if hints["gap_answers"]:
                code_hints["gap_answers"] = [{"gap": g.id, "question": g.question, "line": self._line(x)}
                                             for g, x in hints["gap_answers"]]
            if code_hints:
                payload["code_hints"] = code_hints
        lines = self.transcript[:lines_all]      # quotes may come from any line said so far
        self.flow.gap_budget = 1 if mode in ("live", "gap_check") else 2
        self.flow.mode = mode
        self.flow.latest_t = lines[-1]["t"] if lines else None
        self.flow.round_new = set()
        self.flow_calls += 1
        await self.send({"type": "flow_status", "state": "thinking", "mode": mode})
        t0 = time.monotonic()
        acc = _FlowRound()
        try:
            if self.meta.tools_ok:
                try:
                    await self._flow_tools(payload, lines, acc, FLOW_MAX_TURNS.get(mode, 3), FLOW_ROUND_MAX_S.get(mode, 60.0))
                except ToolsUnsupported as e:
                    log.warning("tool calling not available, Flow agent uses JSON calls: %s", e)
                    self.meta.tools_ok = False
            if not self.meta.tools_ok and not acc.turns:
                await self._flow_json(payload, lines, acc, FLOW_MAX_TURNS.get(mode, 3), FLOW_ROUND_MAX_S.get(mode, 60.0))
        except Exception as e:  # noqa: BLE001 - first turn failed: keep the lines for next round
            log.warning("flow round failed: %s", e)
            acc.log.append({"error": str(e)})
            if not acc.turns:
                self.flow.round_new = set()
                self._debug("flow", mode=mode, payload=payload, turns=acc.log)
                await self.send({"type": "flow_status", "state": "error", "mode": mode})
                return
        self.flow_done_idx = end
        self.flow.round_new = set()              # the round is over: its steps are shown from now on
        if mode in ("live", "final"):
            self.flow_cut = acc.cut
        self.flow_stats["accepted"] += acc.accepted
        self.flow_stats["rejected"] += acc.rejected
        self.flow_stats["rounds"] += 1
        log.info("flow %s %.1fs: %d turns, %d accepted, %d rejected", mode, time.monotonic() - t0,
                 acc.turns, acc.accepted, acc.rejected)
        self._debug("flow", mode=mode, seconds=round(time.monotonic() - t0, 1), payload=payload, turns=acc.log, cut=acc.cut)
        if any(e["kind"].startswith("contradiction") for e in acc.events):
            for c in self.board.link_conflicts(list(self.flow.contradictions.values())):
                if c not in acc.cards_added and c not in acc.cards_updated:
                    acc.cards_updated.append(c)
        for c in acc.cards_added:
            self._queue_image(c)
        if acc.cards_added:
            await self.send({"type": "cards_added", "cards": [c.public() for c in acc.cards_added]})
        if acc.cards_updated:
            await self.send({"type": "cards_updated", "cards": [c.public() for c in acc.cards_updated]})
        if acc.events or mode != "live":
            await self.send_flow(acc.events)
        await self.send({"type": "flow_status", "state": "idle", "mode": mode,
                         "seconds": round(time.monotonic() - t0, 1),
                         "accepted": acc.accepted, "rejected": acc.rejected,
                         "gaps": sum(1 for e in acc.events if e["kind"] == "gap")})

    async def _apply_flow_call(self, name, args, lines: list[dict], acc: "_FlowRound") -> dict:
        if isinstance(args, str):
            try:
                args = json.loads(args or "{}")
            except ValueError:
                args = None
        if name == "flag_contradiction" and isinstance(args, dict):
            why = await self._second_opinion(args, lines)
            if why:
                acc.rejected += 1
                log.info("contradiction rejected by the second-opinion check: %s", why)
                return {"ok": False, "error": why}
        if name == "flag_gap" and isinstance(args, dict):
            pre = self.flow.gap_precheck(args, lines)       # free checks first: no paid second opinion for a sure "no"
            if pre:
                acc.rejected += 1
                log.info("flow call flag_gap rejected: %s", pre)
                return {"ok": False, "error": pre}
        if name in ("add_step", "link_steps", "flag_gap", "resolve") and isinstance(args, dict):
            why = await self._check_flow_call(name, args, lines)
            if why:
                acc.rejected += 1
                log.info("flow call %s rejected by the second-opinion check: %s", name, why)
                return {"ok": False, "error": why}
        res: Result = self.flow.apply(name, args, lines, self.meeting_t)
        if res.ok:
            acc.accepted += 1
        else:
            acc.rejected += 1
            log.info("flow call rejected: %s %s -> %s", name, json.dumps(args)[:200], res.message.get("error"))
        merged = res.message.get("merged") if res.ok else None
        if merged:     # that step was never shown: drop its "New step" event, its arrows/gaps now name the kept step
            keep = res.message.get("id")
            acc.events = [dict(e, step=keep) if e.get("step") == merged else e for e in acc.events
                          if not (e.get("kind") == "step_added" and e.get("id") == merged)]
        acc.events += res.events
        if res.new_gap:
            g = res.new_gap
            step = self.flow.steps.get(g.step)
            detail = f"Gap in the flow: nobody covered this at “{step.label}”." if step else "Gap in the flow: nobody covered this."
            card, is_new = self.board.add_gap_question(g.id, g.question, detail, self.meeting_t)
            if card:
                g.card_id, g.card_owned = card.id, is_new
                (acc.cards_added if is_new else acc.cards_updated).append(card)
        if res.resolved_gap:
            rg = res.resolved_gap
            c = self.board.resolve_gap_question(rg.card_id, rg.answer, self.meeting_t,
                                                rg.resolved_by.t if rg.resolved_by else None)
            if c and c not in acc.cards_added:
                acc.cards_updated.append(c)
        return res.message

    async def _second_opinion(self, args: dict, lines: list[dict]) -> str | None:
        """Before a contradiction is shown, a separate Spark call looks at both lines WITH the lines said just before
        them and says whether they are about the same fact ("$250 per session" for coaching vs "$200 one-on-one" for
        system design are not). Returns an error for the agent, or None to go on. Fails open: no check, no block."""
        if not settings.verify_conflicts or self.conflict_checks >= settings.max_conflict_checks:
            return None
        ea, _ = check_quote(args.get("quote_a"), lines)
        eb, _ = check_quote(args.get("quote_b"), lines)
        if not ea or not eb or ea.line == eb.line:
            return None                      # Flow.apply gives the agent the exact error
        def ctx(ev):
            i = next((k for k, x in enumerate(lines) if x.get("id") == ev.line), None)
            return [] if i is None else [self._line(x) for x in lines[max(0, i - 3):i + 1]]
        a, b = (ea, eb) if ea.line < eb.line else (eb, ea)
        payload = {"finding": clean_text(args.get("note"), 160), "statement_a_with_lines_before": ctx(a),
                   "statement_b_with_lines_before": ctx(b)}
        self.conflict_checks += 1
        try:
            out = await self.meta.spark_json(VERIFY_CONFLICT_SYSTEM, json.dumps(payload, ensure_ascii=False),
                                             "verify_conflict", VERIFY_CONFLICT_SCHEMA)
        except Exception as e:  # noqa: BLE001
            log.warning("conflict check failed, keeping the finding: %s", e)
            return None
        self._debug("conflict_check", payload=payload, response=out)
        if isinstance(out, dict) and out.get("same_fact") is False:
            return (f"second-opinion check: not the same fact (A is about {clean_text(out.get('a_is_about'), 60)}, "
                    f"B is about {clean_text(out.get('b_is_about'), 60)}). Don't flag it")
        return None

    # ---- second opinion on flow changes (v6.4 run: a wrong arrow, a wrong answer, a gap answered 8 min before) ----
    def _ctx(self, lines: list[dict], line_id: int, before: int = 3) -> list[str]:
        i = next((k for k, x in enumerate(lines) if x.get("id") == line_id), None)
        return [] if i is None else [self._line(x) for x in lines[max(0, i - before):i + 1]]

    def _ctx_after(self, lines: list[dict], line_id: int, before: int = 1, after: int = 3) -> list[dict]:
        i = next((k for k, x in enumerate(lines) if x.get("id") == line_id), None)
        return [] if i is None else lines[max(0, i - before):i + 1 + after]

    async def _check_flow_call(self, name: str, args: dict, lines: list[dict]) -> str | None:
        """Second opinion (a separate Spark call) on gap answers and on new gaps. Returns an error for the agent, or
        None to go on. Fails open.
        Arrows are not second-guessed any more: in the v6.5 run the arrow check rejected 9 arrows, at least 4 of them
        wrongly, gave two different answers to the same question, and its "use after = s1" advice hung unrelated
        steps on the first step. Parts (one band per product part) now keep sections apart instead."""
        if not settings.verify_flow or self.flow_checks >= settings.max_flow_checks:
            return None
        f = self.flow
        ev, _ = check_quote(args.get("quote"), lines)
        if not ev or name not in ("flag_gap", "resolve"):
            return None                                   # Flow.apply gives the exact error
        if name == "resolve" and str(args.get("item_id") or "").strip() in f.contradictions:
            c = f.contradictions[str(args.get("item_id")).strip()]
            if c.resolved or ev.line in (c.a.line, c.b.line) or ev.line < c.b.line:
                return None                               # Flow.apply rejects these without a Spark call
            payload = {"check": "settles", "conflict": c.note,
                       "statement_a": self._line(next((x for x in lines if x.get("id") == c.a.line), {"text": c.a.quote, "t": c.a.t, "speaker": c.a.speaker})),
                       "statement_b": self._line(next((x for x in lines if x.get("id") == c.b.line), {"text": c.b.quote, "t": c.b.t, "speaker": c.b.speaker})),
                       "answer": clean_text(args.get("answer"), 160),
                       "settling_line_with_lines_before": self._ctx(lines, ev.line)}
            out = await self._verify_flow(payload)
            if out and out.get("yes") is False:
                return (f"second-opinion check: that line does not settle {c.note!r} "
                        f"({clean_text(out.get('why'), 100)}). Leave it open: the team must confirm which value is right")
            return None
        if name == "resolve":
            g = f.gaps.get(str(args.get("item_id") or "").strip())
            if not g or g.resolved or g.dismissed:
                return None
            step = f.steps.get(g.step)
            payload = {"check": "answer", "question": g.question, "step": step.label if step else "",
                       "lines_about_the_step": [l for e in (step.evidence[-2:] if step else []) for l in self._ctx(lines, e.line, 1)],
                       "answer": clean_text(args.get("answer"), 160),
                       "answer_line_with_lines_before": self._ctx(lines, ev.line)}
            out = await self._verify_flow(payload)
            if out and out.get("yes") is False:
                return (f"second-opinion check: that line does not answer {g.question!r} "
                        f"({clean_text(out.get('why'), 100)}). Leave the gap open")
            return None
        # flag_gap: was it already answered, maybe many minutes before?
        step = f.find_step(args.get("step"))
        question = clean_text(args.get("question"), 160)
        branch = clean_text(args.get("missing_branch"), 30)
        if not step or not question or not branch or f.gap_budget <= 0 or len(f.open_gaps()) >= MAX_OPEN_GAPS:
            return None                                   # Flow.apply rejects these without a Spark call
        if any(not g.dismissed and f.same_gap(g, step, branch, question) for g in f.gaps.values()):
            return None                                   # a repeat / move of an existing gap: no new card
        payload = {"check": "already_answered", "question": question, "step": step.label,
                   "earlier_lines": self._answer_search_lines(step, ev, question, branch, lines)}
        if not payload["earlier_lines"]:
            return None
        out = await self._verify_flow(payload)
        if out and out.get("yes") is True:
            at = clean_text(out.get("line"), 12)
            return (f"not a gap: already answered{' at ' + at if at else ''} ({clean_text(out.get('why'), 100)}). "
                    "Map that answer instead (add the step / link) if it isn't on the map")
        return None

    def _answer_search_lines(self, step, ev, question: str, branch: str, lines: list[dict]) -> list[str]:
        """Lines where an answer would be: what was said right AFTER the step's own lines (v6.5 run: "What happens
        after the 30-min sync-up?" was answered 2 lines after the step's line, which the check never saw), and right
        after other lines that share words with the question. Max ~40 lines, in meeting order."""
        picked: dict[int, dict] = {}
        for e in [*step.evidence[-3:], ev]:
            for x in self._ctx_after(lines, e.line, 1, 4):
                picked[x["id"]] = x
        want = key_words(f"{question} {branch} {step.label}")
        scored = []
        for x in lines:
            if len(x.get("text", "")) < 25:
                continue
            n = len(want & key_words(x["text"]))
            if n >= 2:
                scored.append((n, x.get("id", 0)))
        for _, line_id in sorted(scored, key=lambda z: (-z[0], z[1]))[:6]:
            for x in self._ctx_after(lines, line_id, 1, 2):
                picked[x["id"]] = x
        return [self._line(x) for x in sorted(picked.values(), key=lambda x: x["id"])][-40:]

    async def _verify_flow(self, payload: dict) -> dict | None:
        self.flow_checks += 1
        try:
            out = await self.meta.spark_json(VERIFY_FLOW_SYSTEM, json.dumps(payload, ensure_ascii=False),
                                             "verify_flow", VERIFY_FLOW_SCHEMA)
        except Exception as e:  # noqa: BLE001
            log.warning("flow check failed, keeping the change: %s", e)
            return None
        self._debug("flow_check", payload=payload, response=out)
        return out if isinstance(out, dict) else None

    async def _flow_tools(self, payload: dict, lines: list[dict], acc: "_FlowRound", max_turns: int, max_s: float):
        messages = [{"role": "system", "content": FLOW_SYSTEM},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]
        t0 = time.monotonic()
        idle = 0                               # turns in a row whose calls changed nothing
        for k_turn in range(max_turns):
            if k_turn and time.monotonic() - t0 > max_s:
                acc.cut = True                 # out of time with the agent still working
                break
            try:
                msg = await self.meta.spark_tools(messages, FLOW_TOOLS)
            except ToolsUnsupported:
                raise
            except Exception:
                if not acc.turns:
                    raise
                break                      # a later turn failed: keep what was accepted
            acc.turns += 1
            calls = [c for c in (msg.get("tool_calls") or []) if isinstance(c, dict)]
            if not calls:
                acc.log.append({"content": msg.get("content"), "calls": []})
                nudge = None if acc.nudged or not _turn_left(k_turn, max_turns, t0, max_s) else self._gap_nudge()
                if nudge:
                    acc.nudged = True
                    acc.log[-1]["nudge"] = nudge
                    messages += [{"role": "assistant", "content": msg.get("content") or "done"},
                                 {"role": "user", "content": nudge}]
                    continue
                break                      # the agent is done
            messages.append({"role": "assistant", "content": msg.get("content"), "tool_calls": calls})
            rejected = 0
            events_before = len(acc.events)
            turn_log: list[dict] = []
            acc.log.append({"content": msg.get("content"), "calls": turn_log})
            failed_labels: list[str] = []
            for k, c in enumerate(calls):
                fn = c.get("function") or {}
                if k < FLOW_MAX_CALLS_PER_TURN:
                    out = self._chained_to_failed(fn.get("name"), fn.get("arguments"), failed_labels)
                    if out is None:
                        out = await self._apply_flow_call(fn.get("name"), fn.get("arguments"), lines, acc)
                    else:
                        acc.rejected += 1
                    if not out.get("ok") and fn.get("name") == "add_step":
                        a = _json_args(fn.get("arguments"))
                        if a.get("label"):
                            failed_labels.append(str(a["label"]))
                else:
                    out = {"ok": False, "error": "too many calls in one turn; skipped"}
                turn_log.append({"name": fn.get("name"), "arguments": fn.get("arguments"), "result": out})
                rejected += 0 if out.get("ok") else 1
                messages.append({"role": "tool", "tool_call_id": c.get("id", f"call_{k}"),
                                 "content": json.dumps(out, ensure_ascii=False)})
            # calls that changed nothing (repeats): don't loop on them, but only on the SECOND such turn in a row.
            # v6.10 bench (release_sync): the final pass began with a repeat of an already-pinned bug, the loop
            # stopped there, and the gap sweep it was about to do never ran ("transfer fails" gap missed).
            idle = idle + 1 if not rejected and len(acc.events) == events_before else 0
            if idle >= 2:
                break
        else:
            acc.cut = True                     # used every turn and the agent was still sending calls

    def _gap_nudge(self) -> str | None:
        """Final pass / "any gaps?": the agent stopped with a gap slot left. Ask it once to look at the gap kinds it has
        not used. v6.10 and v6.11 bench (grooming): both final passes spent their slots on money gaps (payment fails,
        deposit refund) and answered "done" without ever weighing "what if the owner doesn't show up?" (drop-off)."""
        f = self.flow
        if f.mode not in ("final", "gap_review") or f.gap_budget <= 0 or len(f.open_gaps()) >= MAX_OPEN_GAPS \
                or len(f.steps) < 3:
            return None
        asked = [g.question for g in f.open_gaps()]
        return ("Before you finish: you may still flag 1 gap. Open gaps on the map: "
                + (json.dumps(asked, ensure_ascii=False) if asked else "none")
                + ". Look at the WHOLE flow once more for the kinds under flag_gap that none of these is about "
                  "(a failure or \"no\" outcome, money, drop-off, hand-off, the end). Flag one only if it is important "
                  "and nobody covered it in the meeting, quoting the line about that step. Otherwise answer \"done\".")

    def _chained_to_failed(self, name, args, failed: list[str]) -> dict | None:
        """v6.5 run: "Answer questions" was rejected, so "Self-evaluate answers" (after = "Answer questions") and the
        step after it failed with "not found" and the agent never sent them again. Say why, so it resends the chain."""
        if name != "add_step" or not failed:
            return None
        after = str(_json_args(args).get("after") or "").strip()
        if not after or self.flow.find_step(after):
            return None
        if any(after.lower() == f.lower() or _similar(after, f) for f in failed):
            return {"ok": False, "error": f"not added: the step it comes after ({after!r}) was rejected above. Fix that "
                                          "step first, then send this one again in the same batch"}
        return None

    async def _flow_json(self, payload: dict, lines: list[dict], acc: "_FlowRound", max_turns: int, max_s: float):
        """Fallback when the API has no tool calling: the same calls as one JSON object per turn. Like the tool loop,
        the agent sees the results (new step ids, errors) and goes on until it returns no calls."""
        user = json.dumps(payload, ensure_ascii=False)
        history: list[dict] = []
        t0 = time.monotonic()
        idle = 0
        for k_turn in range(max_turns):
            if k_turn and time.monotonic() - t0 > max_s:
                acc.cut = True
                break
            try:
                out = await self.meta.spark_json(FLOW_SYSTEM + FLOW_JSON_NOTE, user, "flow_calls",
                                                 FLOW_JSON_SCHEMA, history)
            except Exception:
                if not acc.turns:
                    raise
                break
            acc.turns += 1
            calls = [c for c in (out.get("calls") or []) if isinstance(c, dict)][:FLOW_MAX_CALLS_PER_TURN]
            if not calls:
                nudge = None if acc.nudged or not _turn_left(k_turn, max_turns, t0, max_s) else self._gap_nudge()
                if not nudge:
                    break
                acc.nudged = True
                acc.log.append({"calls": [], "nudge": nudge})
                history += [{"role": "assistant", "content": json.dumps({"calls": []})},
                            {"role": "user", "content": nudge + ' Return {"calls": []} when you are done.'}]
                continue
            results, failed = [], []
            events_before = len(acc.events)
            for c in calls:
                r = self._chained_to_failed(c.get("name"), c.get("arguments"), failed)
                if r is None:
                    r = await self._apply_flow_call(c.get("name"), c.get("arguments"), lines, acc)
                else:
                    acc.rejected += 1
                if not r.get("ok") and c.get("name") == "add_step" and _json_args(c.get("arguments")).get("label"):
                    failed.append(str(_json_args(c.get("arguments"))["label"]))
                results.append(r)
            acc.log.append({"calls": [{"name": c.get("name"), "arguments": c.get("arguments"), "result": r}
                                      for c, r in zip(calls, results)]})
            idle = idle + 1 if all(r.get("ok") for r in results) and len(acc.events) == events_before else 0
            if idle >= 2:                  # (see _flow_tools: a single repeat turn doesn't end the round)
                break
            history += [{"role": "assistant", "content": json.dumps({"calls": calls}, ensure_ascii=False)},
                        {"role": "user", "content": "Results of your calls (same order). Fix or drop the rejected ones "
                                                    "and send what is still missing; return {\"calls\": []} when you "
                                                    "are done:\n" + json.dumps(results, ensure_ascii=False)}]
        else:
            acc.cut = True

    async def user_gap_card_changed(self, c: Card | None, deleted_id: str | None = None):
        """The user resolved / reopened / deleted a question card made from a gap."""
        g = self.flow.gap_by_card(deleted_id or (c.id if c else ""))
        if not g:
            return
        if deleted_id:
            g.dismissed = True
        elif c is not None and not self.flow.user_resolve_gap(g, c.resolved, self.meeting_t):
            return
        await self.send_flow([])

    # ---------------- images ----------------
    def _queue_image(self, c: Card):
        if self.images_made >= settings.max_images:
            c.image_status = "skipped"
            return
        self.images_made += 1
        c.image_status = "pending"
        task = asyncio.create_task(self._make_image(c))
        self.image_tasks.add(task)
        task.add_done_callback(self.image_tasks.discard)

    async def _make_image(self, c: Card):
        async with self.image_sem:
            url = await self._generate_image(c.image_prompt or c.title)
        if c.id not in self.board.cards:      # deleted meanwhile
            return
        c.image_url, c.image_status = (url, "ready") if url else (None, "failed")
        await self.send({"type": "cards_updated", "cards": [c.public()]})

    async def _generate_image(self, prompt: str) -> str | None:
        try:
            raw = await self.meta.image(prompt)
        except Exception as e:  # noqa: BLE001
            log.warning("image failed: %s", e)
            return None
        mime = image_mime(raw) or "image/webp"
        img_id = uuid.uuid4().hex
        self.images[img_id] = (raw, mime)
        return f"/api/sessions/{self.id}/images/{img_id}"

    async def _wait_images(self, timeout: float):
        if self.image_tasks:
            await asyncio.wait(set(self.image_tasks), timeout=timeout)

    # ---------------- report ----------------
    async def _build_report(self) -> dict:
        cards = [self.board.cards[i] for i in self.board.order if i in self.board.cards]
        summary = {"title": self.board.title or "Meeting notes", "summary": "", "image_prompt": ""}
        takeaways: list[str] = []
        if self.transcript:
            full = [self._line(x) for x in self.transcript]
            if len(full) > 600:     # keep the request small for very long meetings
                full = full[:100] + ["[...]"] + full[-500:]
            try:
                out = await self.meta.spark_json(
                    SUMMARY_SYSTEM,
                    json.dumps({"board": self.board.for_spark(), "transcript": full,
                                "conflicts": [{"note": c.note, "a": c.a.quote, "b": c.b.quote}
                                              for c in self.flow.contradictions.values() if not c.resolved]},
                               ensure_ascii=False),
                    "summary", SUMMARY_SCHEMA)
                # keep the title the board showed all meeting; the summary title is only a fallback
                if not self.board.title:
                    summary["title"] = clean_text(out.get("title"), 80) or summary["title"]
                raw = out.get("takeaways") if isinstance(out.get("takeaways"), list) else []
                takeaways = [t for t in (clean_text(x, 160) for x in raw[:5]) if t]
                summary["summary"] = clean_text(out.get("summary"), 1200)
                summary["image_prompt"] = clean_text(out.get("image_prompt"), 300)
            except Exception as e:  # noqa: BLE001
                log.warning("summary failed: %s", e)
        cover = None
        if summary["image_prompt"]:
            cover = await self._generate_image(summary["image_prompt"])   # 1 extra image, outside the card cap

        status_rank = {"blocked": 0, "in_progress": 1, "done": 2}
        pub = lambda c: c.public()  # noqa: E731
        return {
            "title": summary["title"],
            "date": self.created_wall.isoformat(),
            "duration_s": round(self.meeting_t),
            "summary": summary["summary"],
            "takeaways": takeaways,
            "cover_image_url": cover,
            "speakers": {k: self.speaker_name(k) for k in sorted({x["speaker"] for x in self.transcript})},
            "goals": [pub(c) for c in cards if c.column == "goals"],
            "updates": [pub(c) for c in sorted((c for c in cards if c.column == "updates"),
                                                key=lambda c: status_rank.get(c.status or "", 3))],
            # one side of an unsettled conflict is not a decision (it is on the "What was missed" slide)
            "decisions": [pub(c) for c in cards if c.column == "decisions" and not c.conflict_ref],
            "actions": [pub(c) for c in cards if c.column == "actions"],
            "open_questions": [pub(c) for c in cards if c.column == "questions" and not c.resolved],
            "answered_questions": [pub(c) for c in cards if c.column == "questions" and c.resolved],
            "flow": self.flow.public(),
        }

    async def close(self):
        for t in self.tasks:
            t.cancel()
        for t in list(self.image_tasks):
            t.cancel()
        if self.voice:
            await self.voice.stop(wait_s=2)
        if self.socket:
            await _close_quietly(self.socket)
        self.images.clear()
        self.audio.buf.clear()
        self.audio.hist.clear()


def _json_args(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    try:
        out = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return out if isinstance(out, dict) else {}


def _turn_left(k_turn: int, max_turns: int, t0: float, max_s: float) -> bool:
    """Is there still a turn (and time) for the agent to answer a nudge? Otherwise the round would look cut off."""
    return k_turn < max_turns - 1 and time.monotonic() - t0 <= max_s


class _FlowRound:
    def __init__(self) -> None:
        self.turns = 0
        self.accepted = 0
        self.rejected = 0
        self.events: list[dict] = []
        self.cards_added: list[Card] = []
        self.cards_updated: list[Card] = []
        self.log: list[dict] = []          # every agent turn (debug dump)
        self.cut = False                   # stopped by the turn / time limit while the agent was still working
        self.nudged = False                # final / gap review: asked once more for an unused gap slot


async def _close_quietly(ws: WebSocket):
    try:
        await asyncio.wait_for(ws.close(code=4000, reason="replaced"), timeout=2)
    except Exception:  # noqa: BLE001
        pass


class SessionManager:
    def __init__(self, meta: MetaClient):
        self.meta = meta
        self.sessions: dict[str, Session] = {}
        self.ip_limiter = RateLimiter(settings.sessions_per_ip_per_hour, 3600)
        self.store = make_store() if settings.replay_enabled else None     # also sets self.library
        self._cleanup: asyncio.Task | None = None
        self._sweep: asyncio.Task | None = None

    @property
    def store(self):
        return self._store

    @store.setter
    def store(self, value):
        """Replay storage; the library (owners, delete, expiry) always works on the same store."""
        from .accounts import Library         # noqa: PLC0415  (accounts imports replay, not session)
        self._store = value
        self.library = Library(value) if value is not None else None

    def start(self):
        self._cleanup = asyncio.create_task(self._cleanup_loop())
        if self.library is not None and settings.replay_sweep_s > 0:
            self._sweep = asyncio.create_task(self._sweep_loop())

    async def _sweep_loop(self):
        await asyncio.sleep(30)
        while True:
            try:
                await self.library.sweep()
            except Exception as e:  # noqa: BLE001
                log.warning("replay sweep failed: %s", e)
            await asyncio.sleep(settings.replay_sweep_s)

    async def shutdown(self):
        if self._cleanup:
            self._cleanup.cancel()
        if self._sweep:
            self._sweep.cancel()
        for s in list(self.sessions.values()):
            await s.close()
        self.sessions.clear()

    def active_count(self) -> int:
        return sum(1 for s in self.sessions.values() if s.state != "done")

    def create(self, keywords: list[str], title: str | None, ip: str) -> Session:
        s = Session(self.meta, keywords, title, ip)
        s.store = self.store
        s.library = self.library
        self.sessions[s.id] = s
        return s

    def get(self, sid: str) -> Session | None:
        return self.sessions.get(sid)

    def forget_replay(self, rid: str) -> None:
        """A deleted replay: a rejoin of its meeting must not offer the dead link (or re-add it to My meetings)."""
        for s in self.sessions.values():
            if s.replay_id == rid:
                s.replay_id = s.replay_key = None

    async def _cleanup_loop(self):
        while True:
            await asyncio.sleep(60)
            now = time.monotonic()
            for sid, s in list(self.sessions.items()):
                old = now - s.created_mono > settings.session_ttl_minutes * 60
                gone_s = now - s.last_seen if s.socket is None else 0
                if s.state in ("recording", "paused") and not old:
                    if gone_s > 10 * 60:   # browser gone 10 min: stop paying for voice, keep the report
                        log.info("auto-stop abandoned session %s", sid)
                        asyncio.create_task(s.stop())
                    continue
                if old or (s.state != "stopping" and gone_s > 30 * 60):
                    log.info("cleanup session %s (state=%s)", sid, s.state)
                    self.sessions.pop(sid, None)
                    await s.close()
