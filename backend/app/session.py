"""One meeting = one Session (in memory; Cloud Run must run with --max-instances 1)."""
from __future__ import annotations

import asyncio
import json
import logging
import secrets
import time
import uuid
from datetime import datetime, timezone

from fastapi import WebSocket

from .audio import AudioBuffer
from .board import Board, Card
from .config import BYTES_PER_SEC, settings
from .meta_api import MetaClient, image_mime
from .meta_voice import VoiceStream
from .prompts import SPARK_SCHEMA, SPARK_SYSTEM, SUMMARY_SCHEMA, SUMMARY_SYSTEM
from .security import RateLimiter, clean_text

log = logging.getLogger("minutecanvas.session")

TRANSCRIPT_SNAPSHOT = 300   # turns sent in "hello" after a reconnect
CONTEXT_LINES = 6           # already-processed lines given to Spark for continuity
MAX_SPARK_LINES = 120       # cap per call (long meetings: only new transcript + board)


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
            p = {"turn": turn, "speaker": data.get("speaker") or self._last_speaker(), "text": data["text"]}
            self.partials[turn] = p
            await self.send({"type": "transcript_partial", **p})
        elif kind == "final":
            self.partials.pop(turn, None)
            if data.get("text"):
                await self._add_final(data)
            else:
                await self.send({"type": "transcript_partial_clear", "turn": turn})

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
        if name:
            self.speaker_names[label] = name
        else:
            self.speaker_names.pop(label, None)
        await self.send({"type": "speakers", "speakers": self.speaker_names})

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

    async def spark_once(self, final: bool = False):
        async with self.spark_lock:
            end = len(self.transcript)
            new = self.transcript[self.spark_done_idx:end]
            if not new:
                return
            if self.spark_calls >= settings.max_spark_calls:
                await self.send({"type": "notice", "message": "AI card limit for this session reached."})
                return
            new = new[-MAX_SPARK_LINES:]
            context = self.transcript[max(0, self.spark_done_idx - CONTEXT_LINES):self.spark_done_idx]
            payload = {
                "current_board": self.board.for_spark(),
                "board_title": self.board.title,
                "deleted_titles": self.board.deleted_titles[-30:],
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
                await self.send({"type": "spark_status", "state": "error"})
                return
            self.spark_done_idx = end
            ch = self.board.apply_spark(ops, self.meeting_t)
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

    def _line(self, x: dict) -> str:
        return f"[{fmt_t(x['t'])}] {self.speaker_name(x['speaker'])}: {x['text']}"

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
        if self.transcript:
            full = [self._line(x) for x in self.transcript]
            if len(full) > 600:     # keep the request small for very long meetings
                full = full[:100] + ["[...]"] + full[-500:]
            try:
                out = await self.meta.spark_json(
                    SUMMARY_SYSTEM,
                    json.dumps({"board": self.board.for_spark(), "transcript": full}, ensure_ascii=False),
                    "summary", SUMMARY_SCHEMA)
                summary["title"] = clean_text(out.get("title"), 80) or summary["title"]
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
            "cover_image_url": cover,
            "speakers": {k: self.speaker_name(k) for k in sorted({x["speaker"] for x in self.transcript})},
            "goals": [pub(c) for c in cards if c.column == "goals"],
            "updates": [pub(c) for c in sorted((c for c in cards if c.column == "updates"),
                                                key=lambda c: status_rank.get(c.status or "", 3))],
            "decisions": [pub(c) for c in cards if c.column == "decisions"],
            "actions": [pub(c) for c in cards if c.column == "actions"],
            "open_questions": [pub(c) for c in cards if c.column == "questions" and not c.resolved],
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
