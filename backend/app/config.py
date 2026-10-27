"""All settings come from environment variables (see .env.example)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _load_dotenv() -> None:
    """Local dev: read backend/.env if present (Cloud Run uses real env vars)."""
    f = Path(__file__).resolve().parent.parent / ".env"
    if not f.exists():
        return
    for line in f.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv()


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _int(name: str, default: int) -> int:
    v = _env(name)
    return int(v) if v else default


def _float(name: str, default: float) -> float:
    v = _env(name)
    return float(v) if v else default


@dataclass
class Settings:
    # Meta Model API
    meta_api_key: str = field(default_factory=lambda: _env("META_API_KEY"))
    meta_api_base: str = field(default_factory=lambda: _env("META_API_BASE", "https://api.meta.ai/v1"))
    meta_asr_url: str = field(default_factory=lambda: _env("META_ASR_URL", "wss://api.meta.ai/v1/asr/realtime"))
    voice_model: str = field(default_factory=lambda: _env("VOICE_MODEL", "muse-voice-transcribe-1.0"))
    voice_language: str = field(default_factory=lambda: _env("VOICE_LANGUAGE", "English"))
    spark_model: str = field(default_factory=lambda: _env("SPARK_MODEL", "muse-spark-1.3"))
    spark_reasoning: str = field(default_factory=lambda: _env("SPARK_REASONING", "low"))
    image_model: str = field(default_factory=lambda: _env("IMAGE_MODEL", "muse-image-1.0"))
    image_reasoning: str = field(default_factory=lambda: _env("IMAGE_REASONING", "low"))

    # Web / security
    frontend_origins: list[str] = field(
        default_factory=lambda: [o.strip().rstrip("/") for o in _env("FRONTEND_ORIGIN", "http://localhost:3000").split(",") if o.strip()]
    )
    access_code: str = field(default_factory=lambda: _env("ACCESS_CODE"))  # optional; if set, needed to start a session
    sessions_per_ip_per_hour: int = field(default_factory=lambda: _int("SESSIONS_PER_IP_PER_HOUR", 6))
    max_active_sessions: int = field(default_factory=lambda: _int("MAX_ACTIVE_SESSIONS", 3))
    trusted_proxy_hops: int = field(default_factory=lambda: _int("TRUSTED_PROXY_HOPS", 1))
    # WebSocket flood limits
    ws_msgs_per_s: float = field(default_factory=lambda: _float("WS_MSGS_PER_S", 10.0))
    ws_msg_burst: int = field(default_factory=lambda: _int("WS_MSG_BURST", 40))
    ws_max_text: int = field(default_factory=lambda: _int("WS_MAX_TEXT", 8192))
    ws_max_audio_frame: int = field(default_factory=lambda: _int("WS_MAX_AUDIO_FRAME", 192_008))   # 4 s + header

    # Per-session caps (protect credits)
    max_session_minutes: int = field(default_factory=lambda: _int("MAX_SESSION_MINUTES", 180))
    max_spark_calls: int = field(default_factory=lambda: _int("MAX_SPARK_CALLS", 150))
    max_images: int = field(default_factory=lambda: _int("MAX_IMAGES", 40))
    image_concurrency: int = field(default_factory=lambda: _int("IMAGE_CONCURRENCY", 2))
    session_ttl_minutes: int = field(default_factory=lambda: _int("SESSION_TTL_MINUTES", 240))

    # Timing
    spark_interval_s: float = field(default_factory=lambda: _float("SPARK_INTERVAL_S", 40.0))
    spark_min_new_chars: int = field(default_factory=lambda: _int("SPARK_MIN_NEW_CHARS", 120))
    heartbeat_s: float = field(default_factory=lambda: _float("HEARTBEAT_S", 2.0))
    auth_timeout_s: float = field(default_factory=lambda: _float("AUTH_TIMEOUT_S", 5.0))
    voice_session_max_s: float = field(default_factory=lambda: _float("VOICE_SESSION_MAX_S", 58 * 60))
    # Audio pacer (Meta needs real-time ingress: never below, never far ahead)
    pacer_tick_s: float = field(default_factory=lambda: _float("PACER_TICK_S", 0.1))
    pacer_lead_s: float = field(default_factory=lambda: _float("PACER_LEAD_S", 0.5))
    pacer_grace_s: float = field(default_factory=lambda: _float("PACER_GRACE_S", 0.3))
    max_backlog_s: float = field(default_factory=lambda: _float("MAX_BACKLOG_S", 60.0))

    @property
    def mock_meta(self) -> bool:
        return _env("MOCK_META") == "1"


SAMPLE_RATE = 24_000          # browser must send PCM16 mono 24 kHz
BYTES_PER_SEC = SAMPLE_RATE * 2
FRAME_HEADER = 8               # uint64 little-endian byte offset before each audio frame

settings = Settings()
