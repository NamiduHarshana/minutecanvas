"""Small security helpers: text sanitising, origin check, rate limiting."""
from __future__ import annotations

import re
import time
from collections import defaultdict, deque

_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f​-‏‪-‮⁦-⁩]")
_TAGS = re.compile(r"<[^>]{0,200}>")
_WS = re.compile(r"[ \t]+")


def clean_text(value, max_len: int) -> str:
    """Plain text only. The frontend must still render with textContent (never innerHTML)."""
    if value is None:
        return ""
    s = str(value)
    s = _CTRL.sub("", s)
    s = _TAGS.sub("", s)          # drop anything that looks like an HTML tag
    s = s.replace("<", "‹").replace(">", "›")
    s = _WS.sub(" ", s).strip()
    if len(s) > max_len:
        s = s[: max_len - 1].rstrip() + "…"
    return s


def clean_optional(value, max_len: int) -> str | None:
    s = clean_text(value, max_len)
    return s or None


def origin_allowed(origin: str | None, allowed: list[str]) -> bool:
    if not origin:
        return False
    return origin.rstrip("/") in allowed


def client_ip(headers, fallback: str | None, trusted_hops: int = 1) -> str:
    """Real client IP that a client cannot fake.

    Clients can send their own X-Forwarded-For; our proxy (Cloud Run's front end) APPENDS
    the real IP, so we count from the RIGHT. trusted_hops=1 -> last entry (Cloud Run).
    0 -> ignore the header (local dev, no proxy).
    """
    xff = headers.get("x-forwarded-for")
    if xff and trusted_hops > 0:
        parts = [p.strip() for p in xff.split(",") if p.strip()]
        if len(parts) >= trusted_hops:
            return parts[-trusted_hops]
    return fallback or "unknown"


class RateLimiter:
    """Sliding-window limiter, in memory (fine with --max-instances 1)."""

    def __init__(self, limit: int, window_s: float):
        self.limit = limit
        self.window_s = window_s
        self._hits: dict[str, deque] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        q = self._hits[key]
        while q and now - q[0] > self.window_s:
            q.popleft()
        if len(q) >= self.limit:
            return False
        q.append(now)
        return True


class TokenBucket:
    """Per-socket message limiter."""

    def __init__(self, rate: float, burst: int):
        self.rate, self.cap = rate, burst
        self.tokens = float(burst)
        self.t = time.monotonic()

    def take(self) -> bool:
        now = time.monotonic()
        self.tokens = min(self.cap, self.tokens + (now - self.t) * self.rate)
        self.t = now
        if self.tokens < 1:
            return False
        self.tokens -= 1
        return True
