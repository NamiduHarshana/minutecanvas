"""Muse Voice Transcribe realtime client (DIARIZATION) with a real-time pacer.

Rules from the Oct 27 test:
- Meta closes with 1008 "Ingress below real-time" if audio stalls -> pacer sends silence.
- Never burst (e.g. a reconnect resend) -> pacer sends at most PACER_LEAD_S ahead of real time.
- A Meta session lasts max 60 min and cannot resume -> rotate before that; a new
  session means new speaker labels (shown as "A·2").
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Awaitable, Callable

import websockets

from .audio import AudioBuffer
from .config import BYTES_PER_SEC, settings

log = logging.getLogger("minutecanvas.voice")
DEBUG_VOICE = os.environ.get("DEBUG_VOICE") == "1"

_SPEAKER_KEYS = ("speaker", "speakerLabel", "speakerId", "speaker_label", "speaker_id", "speakerTag", "label")
_TEXT_KEYS = ("transcript", "text")


def _find(msg: dict, keys) -> object:
    for k in keys:
        if k in msg and msg[k] not in (None, ""):
            return msg[k]
    for v in msg.values():                       # one level of nesting (e.g. {"turn": {...}})
        if isinstance(v, dict):
            for k in keys:
                if k in v and v[k] not in (None, ""):
                    return v[k]
    return None


def parse_event(msg: dict) -> tuple[str, dict] | None:
    """Map a Meta ASR message to ("partial"|"final"|"error"|"ignore", data)."""
    t = str(msg.get("type", ""))
    if t == "error" or (t == "" and "error" in msg):
        return "error", {"message": json.dumps(msg)[:300]}
    if t in ("speechStart", "speechEnd", "audioProgress") or "sessionId" in msg and not _find(msg, _TEXT_KEYS):
        return "ignore", {}
    text = _find(msg, _TEXT_KEYS)
    if not isinstance(text, str):
        return "ignore", {}
    speaker = _find(msg, _SPEAKER_KEYS)
    turn = msg.get("turnId") or _find(msg, ("turnId", "turn_id"))
    final = t in ("speechComplete", "final", "transcriptFinal", "turnComplete") or bool(
        msg.get("isFinal") or msg.get("is_final") or msg.get("final")
    )
    return ("final" if final else "partial"), {
        "text": text.strip(),
        "speaker": str(speaker) if speaker is not None else None,
        "turn": str(turn) if turn is not None else None,
    }


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
            "authorization": {"accessToken": settings.meta_api_key},
            "audioEncoding": "PCM_24KHZ",
            "model": settings.voice_model,
            "partialMode": "CUMULATIVE",
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
            try:
                await self.on_event("status", {"voice": "connecting", "gen": self.gen})
                clean = await self._one_session()
                failures = 0 if clean else failures + 1
            except Exception as e:  # noqa: BLE001
                failures += 1
                log.warning("voice session %d failed: %s", self.gen, e)
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
        async with websockets.connect(settings.meta_asr_url, max_size=2**22, open_timeout=15,
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
                    if kind in ("partial", "final"):
                        await self.on_event(kind, self._label(data))
            finally:
                sender.cancel()
                try:
                    await sender
                except (asyncio.CancelledError, Exception):
                    pass
            code = ws.close_code
            log.info("voice session %d closed code=%s reason=%s", self.gen, code, ws.close_reason)
            return ending.is_set() and code in (1000, None)

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
        while True:
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
                await ws.send(out)
                sent += len(out)
                due -= len(out)
            if due > grace:                       # browser late / paused / disconnected
                n = due - (due % 2)
                await ws.send(b"\x00" * n)
                sent += n
            self.sent_bytes_total = sent
            await asyncio.sleep(tick)

    async def _mock_session(self) -> bool:
        await self.on_event("status", {"voice": "live", "gen": self.gen})
        n = 0
        while not self._stop.is_set():
            await asyncio.sleep(3)
            self.audio.take(10 * BYTES_PER_SEC)
            n += 1
            sp = "A" if n % 2 else "B"
            await self.on_event("partial", self._label({"text": "This is a mock", "speaker": sp, "turn": str(n)}))
            await self.on_event("final", self._label({
                "text": f"This is mock sentence {n} about the login API and the payment screen.", "speaker": sp, "turn": str(n)}))
        return True
