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
from .flow import Flow, Result
from .meta_api import MetaClient, ToolsUnsupported, image_mime
from .meta_voice import VoiceStream
from .prompts import (FLOW_JSON_NOTE, FLOW_JSON_SCHEMA, FLOW_SYSTEM, FLOW_TOOLS, SPARK_SCHEMA, SPARK_SYSTEM,
                      SUMMARY_SCHEMA, SUMMARY_SYSTEM)
from .security import RateLimiter, clean_text

log = logging.getLogger("minutecanvas.session")

TRANSCRIPT_SNAPSHOT = 300   # turns sent in "hello" after a reconnect
CONTEXT_LINES = 6           # already-processed lines given to the Flow agent for continuity
CARDS_CONTEXT_LINES = 16    # cards round: enough to see a question answered right after it was asked
NUMBER_LINES = 30           # older lines with numbers shown to the Flow agent (contradictions across the meeting)
MAX_SPARK_LINES = 120       # cap per call (long meetings: only new transcript + board)
FLOW_MAX_TURNS = 3          # agent turns per round (2nd/3rd only to fix rejected calls)
FLOW_MAX_CALLS_PER_TURN = 20
REVIEW_CONTEXT_LINES = 80   # gap review / final pass: recent lines shown for context
_HAS_NUMBER = re.compile(r"\d|\b(?:two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|fifteen|twenty|thirty|"
                         r"forty|fifty|hundred|thousand)\b", re.I)


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
        self.audio = AudioBuffer(settings.max_backlog_s)
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
        self.flow_stats = {"accepted": 0, "rejected": 0, "rounds": 0}
        self.last_gap_review = -1e9
        self.last_auto_check = time.monotonic()
        self.auto_check_shape = (0, 0)   # (steps, links) at the last automatic gap check
        self.debug_calls: list[dict] = []
        self.report: dict | None = None
        self.socket: WebSocket | None = None
        self.send_lock = asyncio.Lock()
        self.tasks: list[asyncio.Task] = []
        self.voice_status = "idle"
        self.recording_mono = time.monotonic()

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
            "flow": self.flow.public(),
            "flow_stats": self.flow_stats,
            "transcript": self.transcript[-TRANSCRIPT_SNAPSHOT:],
            "partials": list(self.partials.values()),
            "speakers": self.speaker_names,
            "voice": self.voice_status,
            "report": self.report,
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
        # let the pacer send what is left (normally < 1 s), then end the voice stream
        for _ in range(100):
            if self.audio.backlog_s < 0.3:
                break
            await asyncio.sleep(0.1)
        if self.voice:
            await self.voice.stop(wait_s=15)
        for p in list(self.partials.values()):          # unfinished turn -> keep it
            if p.get("text"):
                await self._add_final(p)
        self.partials.clear()

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
        await self.send({"type": "state", "state": self.state})

    # ---------------- transcript ----------------
    async def _on_voice(self, kind: str, data: dict):
        if kind == "status":
            self.voice_status = data.get("voice", self.voice_status)
            await self.send({"type": "voice_status", **data})
            return
        turn = data["turn"]
        if kind == "partial":
            if not data.get("text"):
                return
            p = {"turn": turn, "speaker": data.get("speaker"), "text": data["text"]}   # speaker None = "speaking…"
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

    def _last_speaker(self) -> str:
        return self.transcript[-1]["speaker"] if self.transcript else "A"

    async def _add_final(self, data: dict):
        item = {
            "id": len(self.transcript),
            "turn": data["turn"],
            "speaker": data.get("speaker") or self._last_speaker(),
            "text": clean_text(data["text"], 4000),
            "t": round(self.meeting_t, 1),
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
            await self._flow_round("gap_check")
        return True

    async def spark_once(self, final: bool = False):
        """One round: the cards call and the Flow agent run in parallel (no extra delay)."""
        async with self.spark_lock:
            await asyncio.gather(self._cards_round(final), self._flow_round("final" if final else "live"))

    async def _cards_round(self, final: bool):
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
            ch = self.board.apply_spark(ops, self.meeting_t, steps)
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
        await self.send({"type": "flow", "flow": self.flow.public(), "events": events, "stats": self.flow_stats})

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
        n_ctx = CONTEXT_LINES if mode == "live" else REVIEW_CONTEXT_LINES
        lines_all = len(self.transcript)
        context = self.transcript[max(0, start - n_ctx):start]
        older = self.transcript[:max(0, start - n_ctx)]
        payload = {
            "mode": mode,
            "current_flow": self.flow.for_agent(),
            "earlier_lines_with_numbers": [self._line(x) for x in older if _HAS_NUMBER.search(x["text"])][-NUMBER_LINES:],
            "earlier_lines_for_context_only": [self._line(x) for x in context],
            "new_transcript": [self._line(x) for x in new],
        }
        lines = self.transcript[:lines_all]      # quotes may come from any line said so far
        self.flow.gap_budget = 1 if mode in ("live", "gap_check") else 2
        self.flow_calls += 1
        await self.send({"type": "flow_status", "state": "thinking", "mode": mode})
        t0 = time.monotonic()
        acc = _FlowRound()
        try:
            if self.meta.tools_ok:
                try:
                    await self._flow_tools(payload, lines, acc)
                except ToolsUnsupported as e:
                    log.warning("tool calling not available, Flow agent uses JSON calls: %s", e)
                    self.meta.tools_ok = False
            if not self.meta.tools_ok and not acc.turns:
                await self._flow_json(payload, lines, acc)
        except Exception as e:  # noqa: BLE001 - first turn failed: keep the lines for next round
            log.warning("flow round failed: %s", e)
            acc.log.append({"error": str(e)})
            if not acc.turns:
                self._debug("flow", mode=mode, payload=payload, turns=acc.log)
                await self.send({"type": "flow_status", "state": "error", "mode": mode})
                return
        self.flow_done_idx = end
        self.flow_stats["accepted"] += acc.accepted
        self.flow_stats["rejected"] += acc.rejected
        self.flow_stats["rounds"] += 1
        log.info("flow %s %.1fs: %d turns, %d accepted, %d rejected", mode, time.monotonic() - t0,
                 acc.turns, acc.accepted, acc.rejected)
        self._debug("flow", mode=mode, seconds=round(time.monotonic() - t0, 1), payload=payload, turns=acc.log)
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

    def _apply_flow_call(self, name, args, lines: list[dict], acc: "_FlowRound") -> dict:
        if isinstance(args, str):
            try:
                args = json.loads(args or "{}")
            except ValueError:
                args = None
        res: Result = self.flow.apply(name, args, lines, self.meeting_t)
        if res.ok:
            acc.accepted += 1
        else:
            acc.rejected += 1
            log.info("flow call rejected: %s %s -> %s", name, json.dumps(args)[:200], res.message.get("error"))
        acc.events += res.events
        if res.new_gap:
            g = res.new_gap
            step = self.flow.steps.get(g.step)
            detail = f"Gap in the flow: nobody covered this at “{step.label}”." if step else "Gap in the flow: nobody covered this."
            card, is_new = self.board.add_gap_question(g.id, g.question, detail, self.meeting_t)
            if card:
                g.card_id = card.id
                (acc.cards_added if is_new else acc.cards_updated).append(card)
        if res.resolved_gap:
            c = self.board.resolve_gap_question(res.resolved_gap.card_id, res.resolved_gap.answer, self.meeting_t)
            if c and c not in acc.cards_added:
                acc.cards_updated.append(c)
        return res.message

    async def _flow_tools(self, payload: dict, lines: list[dict], acc: "_FlowRound"):
        messages = [{"role": "system", "content": FLOW_SYSTEM},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]
        for _ in range(FLOW_MAX_TURNS):
            try:
                msg = await self.meta.spark_tools(messages, FLOW_TOOLS)
            except ToolsUnsupported:
                raise
            except Exception:
                if not acc.turns:
                    raise
                break                      # a retry turn failed: keep what was accepted
            acc.turns += 1
            calls = [c for c in (msg.get("tool_calls") or []) if isinstance(c, dict)]
            if not calls:
                acc.log.append({"content": msg.get("content"), "calls": []})
                break
            messages.append({"role": "assistant", "content": msg.get("content"), "tool_calls": calls})
            rejected = 0
            turn_log: list[dict] = []
            acc.log.append({"content": msg.get("content"), "calls": turn_log})
            for k, c in enumerate(calls):
                fn = c.get("function") or {}
                if k < FLOW_MAX_CALLS_PER_TURN:
                    out = self._apply_flow_call(fn.get("name"), fn.get("arguments"), lines, acc)
                else:
                    out = {"ok": False, "error": "too many calls in one turn; skipped"}
                turn_log.append({"name": fn.get("name"), "arguments": fn.get("arguments"), "result": out})
                rejected += 0 if out.get("ok") else 1
                messages.append({"role": "tool", "tool_call_id": c.get("id", f"call_{k}"),
                                 "content": json.dumps(out, ensure_ascii=False)})
            if not rejected:
                break                      # all accepted: no extra turn (saves time and credits)

    async def _flow_json(self, payload: dict, lines: list[dict], acc: "_FlowRound"):
        """Fallback when the API has no tool calling: the same calls as one JSON object."""
        user = json.dumps(payload, ensure_ascii=False)
        history: list[dict] = []
        for _ in range(2):
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
                break
            results = [self._apply_flow_call(c.get("name"), c.get("arguments"), lines, acc) for c in calls]
            acc.log.append({"calls": [{"name": c.get("name"), "arguments": c.get("arguments"), "result": r}
                                      for c, r in zip(calls, results)]})
            if all(r.get("ok") for r in results):
                break
            history += [{"role": "assistant", "content": json.dumps({"calls": calls}, ensure_ascii=False)},
                        {"role": "user", "content": "Results of your calls (same order). Fix or drop the rejected ones:\n"
                                                    + json.dumps(results, ensure_ascii=False)}]

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


class _FlowRound:
    def __init__(self) -> None:
        self.turns = 0
        self.accepted = 0
        self.rejected = 0
        self.events: list[dict] = []
        self.cards_added: list[Card] = []
        self.cards_updated: list[Card] = []
        self.log: list[dict] = []          # every agent turn (debug dump)


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
        self._cleanup: asyncio.Task | None = None

    def start(self):
        self._cleanup = asyncio.create_task(self._cleanup_loop())

    async def shutdown(self):
        if self._cleanup:
            self._cleanup.cancel()
        for s in list(self.sessions.values()):
            await s.close()
        self.sessions.clear()

    def active_count(self) -> int:
        return sum(1 for s in self.sessions.values() if s.state != "done")

    def create(self, keywords: list[str], title: str | None, ip: str) -> Session:
        s = Session(self.meta, keywords, title, ip)
        self.sessions[s.id] = s
        return s

    def get(self, sid: str) -> Session | None:
        return self.sessions.get(sid)

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
