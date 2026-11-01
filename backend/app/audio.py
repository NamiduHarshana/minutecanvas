"""Server-side audio buffer between the browser and Meta.

Audio is only held in memory until it is sent to Meta, then dropped (never saved). The last few seconds already
sent (since Meta's last finished turn, max RESEND_MAX_S) are kept in memory too: if Meta drops the voice session
in the middle of a turn, that audio is sent again to the new session so the turn is not lost.
"""
from __future__ import annotations

import array
import math
from collections import deque

from .config import BYTES_PER_SEC

CHUNK = BYTES_PER_SEC // 10          # 100 ms
SILENCE_RMS = 350                    # PCM16 RMS below this = silence
KEEP_SILENT_CHUNKS = 4               # keep 400 ms of every pause (turn boundaries / diarization)
CATCHUP_AFTER_S = 1.0                # start skipping long pauses when this far behind
# v6.5 run: after a network stall the backlog was ~23 s and stayed for the rest of the meeting: only 0.5 s was skipped
# in 16 minutes, because the pauses of a played video (mic + tab) are never below the fixed RMS 350. The pause level
# now follows the audio: close to the quietest tenth of the last 30 s. Only runs of 500 ms+ are trimmed (400 ms kept),
# so a soft word inside a sentence is never cut.
LEVEL_WINDOW = 300                   # chunks (30 s) used to learn the pause level
FLOOR_FACTOR = 1.6                   # pause = below 1.6 x the quietest-tenth level …
MIN_RANGE = 2.2                      # … when talk is clearly louder than that (median >= 2.2 x): steady music / noise
                                     # has no pauses and is never trimmed


def rms(pcm: bytes) -> float:
    if len(pcm) < 2:
        return 0.0
    a = array.array("h")
    a.frombytes(pcm[: len(pcm) - (len(pcm) % 2)])
    if not a:
        return 0.0
    step = max(1, len(a) // 400)        # sample for speed
    s = 0
    n = 0
    for i in range(0, len(a), step):
        s += a[i] * a[i]
        n += 1
    return math.sqrt(s / n)


class AudioBuffer:
    def __init__(self, max_backlog_s: float, resend_max_s: float = 45.0):
        self.buf = bytearray()
        self.max_bytes = int(max_backlog_s * BYTES_PER_SEC)
        self.received_bytes = 0      # total bytes accepted from the browser (meeting clock)
        self.dropped_bytes = 0       # dropped because backlog was too big
        self.skipped_bytes = 0       # long pauses skipped while catching up
        self._silent_run = 0
        # meeting-clock offset of buf[0]: what Meta is hearing right now is just before `front`
        self.front = 0
        # audio already taken for Meta but not confirmed by a finished turn yet (contiguous, starts at hist_front)
        self.hist = bytearray()
        self.hist_front = 0
        self.hist_max = int(resend_max_s * BYTES_PER_SEC)
        self.resent_bytes = 0
        self._levels: deque[float] = deque(maxlen=LEVEL_WINDOW)

    def pause_level(self) -> float:
        """RMS below which a 100 ms chunk counts as a pause (only used to skip pauses while catching up)."""
        if len(self._levels) < 50:
            return SILENCE_RMS
        xs = sorted(self._levels)
        floor, median = xs[len(xs) // 10], xs[len(xs) // 2]
        if median < floor * MIN_RANGE:
            return SILENCE_RMS
        return max(SILENCE_RMS, floor * FLOOR_FACTOR)

    @property
    def backlog_s(self) -> float:
        return len(self.buf) / BYTES_PER_SEC

    def append_at(self, offset: int, pcm: bytes) -> int:
        """Append a frame that starts at absolute byte `offset`. Returns bytes accepted.

        Overlap (already received) is trimmed; a gap is filled with silence so the
        meeting clock stays correct.
        """
        if offset % 2:
            return 0
        end = offset + len(pcm)
        if end <= self.received_bytes:
            return 0
        if offset < self.received_bytes:
            pcm = pcm[self.received_bytes - offset:]
        elif offset > self.received_bytes:
            gap = offset - self.received_bytes
            self.buf.extend(b"\x00" * gap)
            self.received_bytes += gap
        pcm = pcm[: len(pcm) - (len(pcm) % 2)]
        self.buf.extend(pcm)
        self.received_bytes += len(pcm)
        over = len(self.buf) - self.max_bytes
        if over > 0:
            over += over % 2
            del self.buf[:over]
            self.dropped_bytes += over
            self.front += over
        return len(pcm)

    def take(self, max_bytes: int) -> bytes:
        """Take up to max_bytes for Meta. While behind, long pauses are skipped so we catch up
        at real-time ingress instead of staying late forever."""
        out = bytearray()
        max_bytes -= max_bytes % 2
        while len(out) < max_bytes and self.buf:
            n = min(CHUNK, max_bytes - len(out), len(self.buf))
            chunk = bytes(self.buf[:n])
            del self.buf[:n]
            self._remember(chunk)
            self.front += n
            behind = self.backlog_s > CATCHUP_AFTER_S
            level = rms(chunk) if n == CHUNK else None
            quiet = level is not None and level < self.pause_level()
            if level is not None:
                self._levels.append(level)
            if quiet:
                self._silent_run += 1
                if behind and self._silent_run > KEEP_SILENT_CHUNKS:
                    self.skipped_bytes += n
                    continue
            else:
                self._silent_run = 0
            out.extend(chunk)
        return bytes(out)

    # ---------------- resend after a dropped voice session ----------------
    def _remember(self, chunk: bytes) -> None:
        if not self.hist or self.hist_front + len(self.hist) != self.front:   # first chunk, or audio was dropped
            self.hist = bytearray()
            self.hist_front = self.front
        self.hist.extend(chunk)
        over = len(self.hist) - self.hist_max
        if over > 0:
            over += over % 2
            del self.hist[:over]
            self.hist_front += over

    def confirm_until(self, offset: int) -> None:
        """Meta finished the turns before `offset`: that audio never needs to be sent again."""
        offset -= offset % 2
        cut = min(len(self.hist), max(0, offset - self.hist_front))
        if cut:
            del self.hist[:cut]
            self.hist_front += cut

    def requeue_unconfirmed(self) -> int:
        """The voice session died: put the unconfirmed audio back in front of the buffer (the pacer still sends it
        at real time; long pauses are skipped to catch up). Returns the number of bytes put back."""
        n = len(self.hist)
        if not n:
            return 0
        self.buf[:0] = self.hist
        self.front = self.hist_front
        self.hist = bytearray()
        self.resent_bytes += n
        return n
