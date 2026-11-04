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
    # v6.24: the end-of-meeting summary + takeaways (one request after Stop) use Muse Wonder, Meta's most capable
    # model (same request shape as Spark). If it fails, the summary falls back to Muse Spark. Empty = Spark only
    report_model: str = field(default_factory=lambda: _env("REPORT_MODEL", "muse-wonder-1.0"))
    # Muse Wonder gets this long per try (2 tries: the second only if it rejects reasoning_effort/strict)
    report_timeout_s: float = field(default_factory=lambda: _float("REPORT_TIMEOUT_S", 60.0))
    # v6.25: "Ask about this meeting" on the replay page (Muse Wonder, Muse Spark as the fallback). Every question
    # costs credits and anyone with a replay link can ask: limits per IP, per replay and for the whole server (24 h)
    ask_model: str = field(default_factory=lambda: _env("ASK_MODEL", "muse-wonder-1.0"))
    ask_timeout_s: float = field(default_factory=lambda: _float("ASK_TIMEOUT_S", 45.0))
    ask_per_ip_per_hour: int = field(default_factory=lambda: _int("ASK_PER_IP_PER_HOUR", 20))
    ask_per_replay_per_day: int = field(default_factory=lambda: _int("ASK_PER_REPLAY_PER_DAY", 30))
    ask_daily_max: int = field(default_factory=lambda: _int("ASK_DAILY_MAX", 150))     # 0 = no server-wide limit
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
    # v6.22 (no sign-in, public site): nobody may hold the live-meeting slots or empty the Meta credits.
    # A created meeting that is not started within this time no longer counts as live and is removed (the app sends
    # "start" right after it connects, so a real browser needs ~1 s)
    unstarted_ttl_s: float = field(default_factory=lambda: _float("UNSTARTED_TTL_S", 120.0))
    # recording, browser connected, but no audio frame at all for this long -> auto Stop (report + replay are kept).
    # A real browser sends audio the whole time it records (quiet rooms too); paused meetings are not affected
    no_audio_stop_s: float = field(default_factory=lambda: _float("NO_AUDIO_STOP_S", 180.0))
    # server-wide: minutes of meetings (Start -> Stop, the time Meta Voice is billed) in the last 24 h; when used up,
    # new meetings are refused until older ones leave the 24 h window. 0 = no limit
    daily_meeting_minutes: float = field(default_factory=lambda: _float("DAILY_MEETING_MINUTES", 600.0))
    # WebSocket flood limits
    ws_msgs_per_s: float = field(default_factory=lambda: _float("WS_MSGS_PER_S", 10.0))
    ws_msg_burst: int = field(default_factory=lambda: _int("WS_MSG_BURST", 40))
    ws_max_text: int = field(default_factory=lambda: _int("WS_MAX_TEXT", 8192))
    ws_max_audio_frame: int = field(default_factory=lambda: _int("WS_MAX_AUDIO_FRAME", 192_008))   # 4 s + header

    # Per-session caps (protect credits)
    max_session_minutes: int = field(default_factory=lambda: _int("MAX_SESSION_MINUTES", 180))
    max_spark_calls: int = field(default_factory=lambda: _int("MAX_SPARK_CALLS", 150))
    max_images: int = field(default_factory=lambda: _int("MAX_IMAGES", 80))
    max_flow_calls: int = field(default_factory=lambda: _int("MAX_FLOW_CALLS", 150))   # Flow agent rounds
    flow_enabled: bool = field(default_factory=lambda: _env("FLOW_ENABLED", "1") != "0")
    # second-opinion check before a contradiction is shown (a separate Spark call that sees both lines in context)
    verify_conflicts: bool = field(default_factory=lambda: _env("VERIFY_CONFLICTS", "1") != "0")
    max_conflict_checks: int = field(default_factory=lambda: _int("MAX_CONFLICT_CHECKS", 12))
    # second-opinion checks on the Flow Map: "does this line really answer the gap?" and "was this gap already
    # answered anywhere in the meeting?" (the v6.5 arrow check was removed: it rejected right arrows, see session.py)
    verify_flow: bool = field(default_factory=lambda: _env("VERIFY_FLOW", "1") != "0")
    max_flow_checks: int = field(default_factory=lambda: _int("MAX_FLOW_CHECKS", 40))
    # every automatic gap check also re-checks the open gaps against everything said since (answered later -> answered,
    # answered before it was flagged -> removed, renamed -> question updated); max this many gaps per check
    recheck_gaps: bool = field(default_factory=lambda: _env("RECHECK_GAPS", "1") != "0")
    max_gap_rechecks: int = field(default_factory=lambda: _int("MAX_GAP_RECHECKS", 5))
    gap_review_cooldown_s: float = field(default_factory=lambda: _float("GAP_REVIEW_COOLDOWN_S", 30.0))
    # automatic gap check during the meeting (live rounds only see ~45 s, so gaps need a wider look); 0 = off
    auto_gap_check_s: float = field(default_factory=lambda: _float("AUTO_GAP_CHECK_S", 240.0))
    auto_gap_min_steps: int = field(default_factory=lambda: _int("AUTO_GAP_MIN_STEPS", 3))
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
    # Meta dropped the voice session mid-turn: resend at most this much unconfirmed audio to the new session
    voice_resend_max_s: float = field(default_factory=lambda: _float("VOICE_RESEND_MAX_S", 45.0))
    # a transcript line's time = when the speech started: Meta's speechStart comes this long after the audio
    # (0.5 s pacer lead + voice detection)
    speech_start_lag_s: float = field(default_factory=lambda: _float("SPEECH_START_LAG_S", 1.0))

    # Replay: the share link plays the meeting from 00:00 (text, board, Flow Map, card images; never audio).
    # REPLAY_BUCKET set -> Cloud Storage bucket, else the local folder REPLAY_DIR (relative = inside backend/)
    replay_enabled: bool = field(default_factory=lambda: _env("REPLAY_ENABLED", "1") != "0")
    replay_dir: str = field(default_factory=lambda: _env("REPLAY_DIR", "replays"))
    replay_bucket: str = field(default_factory=lambda: _env("REPLAY_BUCKET"))
    replay_reads_per_ip_per_min: int = field(default_factory=lambda: _int("REPLAY_READS_PER_IP_PER_MIN", 600))
    replay_max_bytes: int = field(default_factory=lambda: _int("REPLAY_MAX_BYTES", 150_000_000))
    # kept this many days after the meeting, then refused + deleted; PINNED_REPLAYS (comma-separated ids) are kept
    replay_keep_days: float = field(default_factory=lambda: _float("REPLAY_KEEP_DAYS", 30))
    pinned_replays: set[str] = field(
        default_factory=lambda: {x.strip() for x in _env("PINNED_REPLAYS").split(",") if x.strip()})
    replay_sweep_s: float = field(default_factory=lambda: _float("REPLAY_SWEEP_S", 6 * 3600))

    # Google sign-in (only for "My meetings"; starting a meeting never needs it).
    # GOOGLE_CLIENT_ID empty -> sign-in is off. AUTH_SECRET signs our own login token (set it on Cloud Run, or
    # every restart signs everyone out).
    google_client_id: str = field(default_factory=lambda: _env("GOOGLE_CLIENT_ID"))
    auth_secret: str = field(default_factory=lambda: _env("AUTH_SECRET"))
    auth_days: float = field(default_factory=lambda: _float("AUTH_DAYS", 14))
    logins_per_ip_per_hour: int = field(default_factory=lambda: _int("LOGINS_PER_IP_PER_HOUR", 30))

    # DEBUG_DUMP=1 (local testing only): writes transcript + every Spark call to backend/debug/<session>.json
    debug_dump: bool = field(default_factory=lambda: _env("DEBUG_DUMP") == "1")
    debug_dir: str = field(default_factory=lambda: _env("DEBUG_DIR", "debug"))

    @property
    def mock_meta(self) -> bool:
        return _env("MOCK_META") == "1"


SAMPLE_RATE = 24_000          # browser must send PCM16 mono 24 kHz
BYTES_PER_SEC = SAMPLE_RATE * 2
FRAME_HEADER = 8               # uint64 little-endian byte offset before each audio frame

settings = Settings()
