"""Server-side audio buffer between the browser and Meta.

Audio is only held in memory until it is sent to Meta, then dropped (never saved).
"""
from __future__ import annotations

import array
import math

from .config import BYTES_PER_SEC

CHUNK = BYTES_PER_SEC // 10          # 100 ms
SILENCE_RMS = 350                    # PCM16 RMS below this = silence
KEEP_SILENT_CHUNKS = 4               # keep 400 ms of every pause (turn boundaries / diarization)
CATCHUP_AFTER_S = 1.0                # start skipping long pauses when this far behind


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
    def __init__(self, max_backlog_s: float):
        self.buf = bytearray()
        self.max_bytes = int(max_backlog_s * BYTES_PER_SEC)
        self.received_bytes = 0      # total bytes accepted from the browser (meeting clock)
        self.dropped_bytes = 0       # dropped because backlog was too big
        self.skipped_bytes = 0       # long pauses skipped while catching up
        self._silent_run = 0

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
            behind = self.backlog_s > CATCHUP_AFTER_S
            if n == CHUNK and rms(chunk) < SILENCE_RMS:
                self._silent_run += 1
                if behind and self._silent_run > KEEP_SILENT_CHUNKS:
                    self.skipped_bytes += n
                    continue
            else:
                self._silent_run = 0
            out.extend(chunk)
        return bytes(out)
