from app.audio import AudioBuffer
from app.board import Board
from app.meta_api import extract_image
from app.meta_voice import parse_event
from app.security import clean_text
from tests.fake_meta import WEBP_1PX
import base64


def test_clean_text_strips_html():
    assert clean_text("<img src=x onerror=alert(1)>Hi <b>there</b>", 100) == "Hi there"
    assert "<" not in clean_text("a < b > c", 100)
    assert clean_text("x" * 50, 10).endswith("…")


def test_audio_overlap_and_gap():
    a = AudioBuffer(60)
    assert a.append_at(0, b"\x01\x00" * 100) == 200
    assert a.append_at(100, b"\x02\x00" * 100) == 100        # overlap trimmed
    assert a.received_bytes == 300
    a.append_at(400, b"\x03\x00" * 10)                        # gap -> silence filled
    assert a.received_bytes == 420
    assert a.append_at(0, b"\x00" * 50) == 0                  # old resend ignored


def test_audio_skips_long_pauses_only_when_behind():
    a = AudioBuffer(60)
    a.append_at(0, b"\x00" * 48000 * 3)                       # 3 s silence backlog
    out = a.take(4800 * 5)
    assert len(out) < 48000 * 3 and a.skipped_bytes > 0
    b = AudioBuffer(60)
    b.append_at(0, b"\x00" * 4800 * 3)                        # small backlog: nothing skipped
    assert len(b.take(4800 * 3)) == 4800 * 3


def test_board_merges_same_topic_update():
    b = Board()
    b.apply_spark({"new_cards": [{"column": "updates", "title": "Payment screen progress", "detail": "60%",
                                  "status": "in_progress", "owner": None, "due": None, "image_prompt": "x"}]}, 10)
    ch = b.apply_spark({"new_cards": [{"column": "updates", "title": "Payment screen progress", "detail": "blocked by API",
                                       "status": "blocked", "owner": None, "due": None, "image_prompt": "x"}]}, 20)
    assert len(b.cards) == 1 and not ch.added and ch.updated
    assert list(b.cards.values())[0].status == "blocked"


def test_board_user_delete_blocks_readd_and_lock():
    b = Board()
    ch = b.apply_spark({"new_cards": [{"column": "goals", "title": "Launch MVP in December", "detail": "",
                                       "status": None, "owner": None, "due": None, "image_prompt": ""}]}, 1)
    cid = ch.added[0].id
    b.user_edit(cid, {"title": "Launch MVP mid December"})
    b.apply_spark({"updates": [{"id": cid, "title": "AI title"}]}, 2)
    assert b.cards[cid].title == "Launch MVP mid December"
    b.user_delete(cid)
    ch = b.apply_spark({"new_cards": [{"column": "goals", "title": "Launch MVP mid December", "detail": "",
                                       "status": None, "owner": None, "due": None, "image_prompt": ""}]}, 3)
    assert not ch.added


def test_resolve_question():
    b = Board()
    ch = b.apply_spark({"new_cards": [{"column": "questions", "title": "Who hosts staging?", "detail": "",
                                       "status": None, "owner": None, "due": None, "image_prompt": ""}]}, 1)
    qid = ch.added[0].id
    b.apply_spark({"resolved_questions": [{"id": qid, "answer": "Client hosts it"}]}, 5)
    assert b.cards[qid].resolved and b.cards[qid].answer == "Client hosts it"


def test_parse_event_real_format():
    assert parse_event({"type": "speechComplete", "turnId": "3", "transcript": "hi"})[0] == "final"
    assert parse_event({"type": "transcript", "transcript": "h"})[0] == "partial"
    assert parse_event({"type": "transcript", "final": True, "transcript": "h"})[0] == "partial"   # no double final
    assert parse_event({"type": "speaker", "label": "C"}) == ("speaker", {"label": "C", "turn": None})
    assert parse_event({"type": "error", "message": "x"})[0] == "error"
    assert parse_event({"type": "speechStart", "turnId": "1"})[0] == "ignore"


def test_extract_image_nested():
    b64 = base64.b64encode(WEBP_1PX * 20).decode()
    assert extract_image({"output": [{"content": [{"result": b64}]}]})
    assert extract_image({"data": [{"b64_json": b64}]})
    assert extract_image({"output": []}) is None


def test_client_ip_not_spoofable():
    from app.security import client_ip
    # client sent a fake XFF; Cloud Run appended the real IP on the right
    assert client_ip({"x-forwarded-for": "1.2.3.4, 203.0.113.9"}, "10.0.0.1", 1) == "203.0.113.9"
    assert client_ip({}, "10.0.0.1", 1) == "10.0.0.1"
    assert client_ip({"x-forwarded-for": "1.2.3.4"}, "10.0.0.1", 0) == "10.0.0.1"
