"""Muse Voice Transcribe realtime client (DIARIZATION) with a real-time pacer.

Rules from the Oct 27 test:
- Meta closes with 1008 "Ingress below real-time" if audio stalls -> pacer sends silence.
- Never burst (e.g. a reconnect resend) -> pacer sends at most PACER_LEAD_S ahead of real time.
- A Meta session lasts max 60 min and cannot resume -> rotate before that; a new
  session means new speaker labels (shown as "A·2").
- Oct 29 run: Meta closed a session with "Ingress audio slower than real-time" in the middle of a turn and that
  turn was lost -> the audio Meta has not finished yet is sent again to the new session (AudioBuffer.requeue),
  and pacer stalls are logged so the cause shows next time.
- Line times: each turn gets the meeting time its speech STARTED (from speechStart), not when the final text came.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from typing import Awaitable, Callable

import websockets

from .audio import AudioBuffer
from .config import BYTES_PER_SEC, settings
from .mock_script import MOCK_LINES

log = logging.getLogger("minutecanvas.voice")
DEBUG_VOICE = os.environ.get("DEBUG_VOICE") == "1"

def parse_event(msg: dict) -> tuple[str, dict] | None:
    """Map a Meta ASR message to ("partial"|"final"|"speaker"|"error"|"ignore", data).

    Format confirmed by the Oct 27 test (mc_api_test.py, DIARIZATION):
      {"type":"transcript","transcript":"...","final":?}   live partial text
      {"type":"speaker","label":"A"}                         who is talking now (separate event)
      {"type":"speechEnd","turnId":...}
      {"type":"speechComplete","turnId":...,"transcript":"..."}   final text of the turn
      {"type":"error", ...}
    """
    t = str(msg.get("type", ""))
    turn = msg.get("turnId")
    turn = str(turn) if turn is not None else None
    if t == "error":
        return "error", {"message": json.dumps(msg)[:300]}
    if t == "speechStart":
        return "turn_start", {"turn": turn}
    if t == "speaker":
        label = msg.get("label")
        return ("speaker", {"label": str(label), "turn": turn}) if label not in (None, "") else ("ignore", {})
    text = msg.get("transcript")
    if not isinstance(text, str):
        return "ignore", {}
    if t == "speechComplete":
        kind = "final"
    elif t == "transcript":
        kind = "partial"          # live text only; the turn's final text comes in speechComplete
    else:
        return "ignore", {}
    sp = msg.get("speaker") or msg.get("label")
    return kind, {"text": text.strip(), "speaker": str(sp) if sp else None, "turn": turn}


EventCb = Callable[[str, dict], Awaitable[None]]


class VoiceStream:
    def __init__(self, audio: AudioBuffer, on_event: EventCb, keywords: list[str]):
        self.audio = audio
        self.on_event = on_event          # ("partial"|"final"|"status", data)
        self.keywords = keywords
        self.gen = 0
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None
        self.sent_bytes_total = 0
        self.stalls = 0                     # pacer ticks that came > 1 s late (event loop or network blocked)
        self._turn_start: dict[str, int] = {}   # open turns of this session: turn id -> start offset (bytes)

    def start(self):
        if not self._task:
            self._task = asyncio.create_task(self._run())

    async def stop(self, wait_s: float = 8.0):
        self._stop.set()
        if self._task:
            try:
                await asyncio.wait_for(asyncio.shield(self._task), timeout=wait_s)
            except (asyncio.TimeoutError, Exception):
                self._task.cancel()

    def handshake(self) -> dict:
        hs = {
            "mode": "DIARIZATION",
            "authorization": {"accessToken": f"Bearer {settings.meta_api_key}"},
            "audioEncoding": "PCM_24KHZ",
            "model": settings.voice_model,
            "emitAudioProgress": False,
        }
        if settings.voice_language:
            hs["languageBias"] = [settings.voice_language]
        if self.keywords:
            hs["keywords"] = self.keywords
        return hs

    async def _run(self):
        failures = 0
        while not self._stop.is_set():
            self.gen += 1
            error = ""
            try:
                await self.on_event("status", {"voice": "connecting", "gen": self.gen})
                clean = await self._one_session()
                failures = 0 if clean else failures + 1
                if not clean:
                    error = "closed without finishing"
            except Exception as e:  # noqa: BLE001
                failures += 1
                error = str(e)[:300]
                clean = False
                log.warning("voice session %d failed: %s", self.gen, e)
            # what Meta never finished in this session is sent again to the next one (not after Stop)
            resent = 0 if (clean or self._stop.is_set()) else self.audio.requeue_unconfirmed()
            if resent:
                log.info("voice session %d: %.1f s of unfinished audio will be sent again", self.gen, resent / BYTES_PER_SEC)
            await self.on_event("gen_end", {"gen": self.gen, "resent_s": round(resent / BYTES_PER_SEC, 1),
                                            "error": error, "stalls": self.stalls})
            if self._stop.is_set():
                break
            if failures:
                if failures > 8:
                    await self.on_event("status", {"voice": "failed"})
                    return
                await self.on_event("status", {"voice": "reconnecting", "gen": self.gen})
                # keep the backlog (audio waits in the buffer), back off a little
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=min(8, 0.5 * 2 ** failures))
                except asyncio.TimeoutError:
                    pass
        await self.on_event("status", {"voice": "stopped"})

    async def _one_session(self) -> bool:
        """Returns True when the session ended cleanly (rotation or stop)."""
        if settings.mock_meta:
            return await self._mock_session()
        self._speaker = None                  # labels restart with every Meta session
        self._speaker_by_turn: dict[str, str] = {}
        self._turn = None                     # partials carry no turnId: take it from speechStart
        self._turn_start = {}
        url = f"{settings.meta_asr_url}?sessionId=mc-{uuid.uuid4()}"
        async with websockets.connect(url, max_size=2**22, open_timeout=30,
                                      ping_interval=20, ping_timeout=20) as ws:
            await ws.send(json.dumps(self.handshake(), separators=(",", ":")))
            first = json.loads(await asyncio.wait_for(ws.recv(), timeout=15))
            if DEBUG_VOICE:
                log.info("voice <- %s", first)
            if first.get("type") == "error" or not first.get("sessionId"):
                raise RuntimeError(f"handshake rejected: {str(first)[:300]}")
            await self.on_event("status", {"voice": "live", "gen": self.gen})

            ending = asyncio.Event()
            sender = asyncio.create_task(self._sender(ws, ending))
            try:
                async for raw in ws:
                    if isinstance(raw, bytes):
                        continue
                    try:
                        msg = json.loads(raw)
                    except ValueError:
                        continue
                    if DEBUG_VOICE:
                        log.info("voice <- %s", raw[:400])
                    ev = parse_event(msg)
                    if not ev:
                        continue
                    kind, data = ev
                    if kind == "error":
                        raise RuntimeError(data["message"])
                    if kind == "turn_start":
                        self._turn = data["turn"]
                        if data["turn"] is not None:
                            self._turn_start[data["turn"]] = self.speech_start_offset()
                    elif kind == "speaker":
                        # Meta sends the label at the END of a turn (just before speechComplete)
                        self._speaker = data["label"]
                        if data["turn"]:
                            self._speaker_by_turn[data["turn"]] = data["label"]
                    elif kind in ("partial", "final"):
                        data["turn"] = data["turn"] or self._turn
                        start = self._turn_start.get(data["turn"] or "")
                        if start is not None:
                            data["t_start"] = round(start / BYTES_PER_SEC, 1)
                        if kind == "partial":
                            data["speaker"] = None        # not known yet while the person is talking
                        else:
                            data["speaker"] = (data["speaker"] or self._speaker_by_turn.get(data["turn"] or "")
                                               or self._speaker)
                        if kind == "final" and data["turn"]:
                            self._speaker_by_turn.pop(data["turn"], None)
                            self._turn_done(data["turn"])
                        await self.on_event(kind, self._label(data))
                        if kind == "final":
                            self._confirm()
            finally:
                sender.cancel()
                try:
                    await sender
                except (asyncio.CancelledError, Exception):
                    pass
            code = ws.close_code
            log.info("voice session %d closed code=%s reason=%s", self.gen, code, ws.close_reason)
            return ending.is_set() and code in (1000, None)

    def speech_start_offset(self) -> int:
        """Meeting-clock offset (bytes) where the speech that Meta just detected started."""
        off = self.audio.front - int(settings.speech_start_lag_s * BYTES_PER_SEC)
        return max(0, off - off % 2)

    def _turn_done(self, turn: str):
        done = self._turn_start.pop(turn, None)
        if done is not None:          # older turns that never got a final won't get one now
            self._turn_start = {k: v for k, v in self._turn_start.items() if v > done}

    def _confirm(self):
        """A turn finished: audio before the oldest still-open turn never has to be sent again
        (a small margin is kept for speech that started but isn't detected yet)."""
        if self._turn_start:
            until = min(self._turn_start.values())
        else:
            until = self.audio.front - int(0.7 * BYTES_PER_SEC)
        self.audio.confirm_until(until)

    def _label(self, data: dict) -> dict:
        sp = data.get("speaker")
        if sp is not None:
            data["speaker"] = sp if self.gen == 1 else f"{sp}·{self.gen}"
        if data.get("turn") is not None:
            data["turn"] = f"{self.gen}-{data['turn']}"
        else:
            data["turn"] = f"{self.gen}-live"
        return data

    async def _sender(self, ws, ending: asyncio.Event):
        """Real-time pacer: audio from the buffer, silence when it is empty."""
        tick = settings.pacer_tick_s
        lead = int(settings.pacer_lead_s * BYTES_PER_SEC)
        grace = int(settings.pacer_grace_s * BYTES_PER_SEC)
        started = time.monotonic()
        sent = 0
        base = self.sent_bytes_total            # all sessions together (debug: audio + silence sent to Meta)
        last_tick = started
        while True:
            now = time.monotonic()
            if now - last_tick > 1.0:
                self.stalls += 1
                log.warning("voice pacer stalled %.1f s (gen %d): the event loop or the network was blocked",
                            now - last_tick, self.gen)
                await self.on_event("debug", {"pacer_stall_s": round(now - last_tick, 1), "gen": self.gen})
            last_tick = now
            elapsed = time.monotonic() - started
            if self._stop.is_set() or elapsed >= settings.voice_session_max_s:
                ending.set()
                # flush what is already due, then end the stream
                tail = self.audio.take(int(settings.pacer_lead_s * BYTES_PER_SEC)) if self._stop.is_set() else b""
                if tail:
                    await ws.send(tail)
                await ws.send(json.dumps({"type": "endStream"}))
                await asyncio.sleep(4)           # last finals arrive, then force close
                await ws.close()
                return
            due = int(elapsed * BYTES_PER_SEC) - sent
            out = self.audio.take(max(0, due + lead))
            if out:
                t_send = time.monotonic()
                await ws.send(out)
                if time.monotonic() - t_send > 0.5:
                    log.warning("voice: sending %.1f s of audio to Meta took %.1f s (slow upload?)",
                                len(out) / BYTES_PER_SEC, time.monotonic() - t_send)
                sent += len(out)
                due -= len(out)
            if due > grace:                       # browser late / paused / disconnected
                n = due - (due % 2)
                await ws.send(b"\x00" * n)
                sent += n
            self.sent_bytes_total = base + sent
            await asyncio.sleep(tick)

    async def _mock_session(self) -> bool:
        await self.on_event("status", {"voice": "live", "gen": self.gen})
        n = 0
        while not self._stop.is_set():
            await asyncio.sleep(3)
            self.sent_bytes_total += len(self.audio.take(10 * BYTES_PER_SEC))
            n += 1
            sp, text = MOCK_LINES[(n - 1) % len(MOCK_LINES)]     # scripted client meeting (sofa shop app)
            t_start = round(max(0.0, self.audio.front / BYTES_PER_SEC - 2.5), 1)
            await self.on_event("partial", self._label({"text": text[:20], "speaker": sp, "turn": str(n),
                                                        "t_start": t_start}))
            await self.on_event("final", self._label({"text": text, "speaker": sp, "turn": str(n),
                                                      "t_start": t_start}))
            self.audio.confirm_until(self.audio.front)
        return True
