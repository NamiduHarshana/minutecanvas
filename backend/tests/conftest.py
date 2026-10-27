import os
import sys

os.environ.setdefault("META_API_KEY", "test-key")
os.environ.setdefault("FRONTEND_ORIGIN", "http://localhost:3000")
os.environ.setdefault("SPARK_INTERVAL_S", "2")
os.environ.setdefault("SPARK_MIN_NEW_CHARS", "20")
os.environ.setdefault("HEARTBEAT_S", "0.5")
os.environ.setdefault("SESSIONS_PER_IP_PER_HOUR", "100")
os.environ.setdefault("MAX_ACTIVE_SESSIONS", "100")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
