"""Tests for Oralyze Voice Layer (Sarvam AI Integration)."""
import time
from app import app
from services import voice
from config import Config


def test_voice_health():
    """Verify /api/voice/health conforms strictly to spec."""
    client = app.test_client()
    resp = client.get("/api/voice/health")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["provider"] == "sarvam"
    assert "key_present" in data
    assert data["credits_ok"] is True
    # Ensure no secrets leak
    assert "sk_" not in str(data)
    assert "api-subscription-key" not in str(data)


def test_voice_usage_and_latency():
    """Verify /api/voice/usage responds under 200ms."""
    client = app.test_client()
    t0 = time.time()
    resp = client.get("/api/voice/usage")
    elapsed_ms = (time.time() - t0) * 1000
    assert resp.status_code == 200
    assert elapsed_ms < 200
    data = resp.get_json()
    assert "today_calls" in data
    assert "cached_hit_rate" in data
    assert "estimated_spend_inr" in data
    assert "daily_limit" in data


def test_idle_invariance():
    """Verify that being idle or querying health does not increment Sarvam call count."""
    client = app.test_client()
    initial_usage = client.get("/api/voice/usage").get_json()["today_calls"]
    # Simulate idle polling on health and static pages
    client.get("/api/voice/health")
    client.get("/api/health")
    client.get("/")
    final_usage = client.get("/api/voice/usage").get_json()["today_calls"]
    assert final_usage == initial_usage, "Idle browsing must never make external Sarvam calls"


def test_tts_cache_idempotency():
    """Verify playing the same question twice only synthesizes once (2nd is cached)."""
    client = app.test_client()
    test_text = "Explain the concept of mutual exclusion in operating systems."
    
    # 1st call
    resp1 = client.post("/api/voice/tts?format=json", json={
        "text": test_text,
        "language_code": "en-IN"
    })
    assert resp1.status_code == 200
    data1 = resp1.get_json()
    assert data1.get("provider") == "sarvam"

    # 2nd call with identical parameters
    resp2 = client.post("/api/voice/tts?format=json", json={
        "text": test_text,
        "language_code": "en-IN"
    })
    assert resp2.status_code == 200
    data2 = resp2.get_json()
    assert data2.get("cached") is True, "Second call with same text must hit cache"


def test_rate_limiting():
    """Verify IP rate limiting at 20 requests per minute."""
    test_ip = "192.168.1.100"
    voice._ip_rate_limits[test_ip] = []
    # 20 requests should pass
    for _ in range(20):
        assert voice.check_rate_limit(test_ip) is True
    # 21st request in the same minute should be rejected
    assert voice.check_rate_limit(test_ip) is False


def test_stt_graceful_failure():
    """Verify STT returns HTTP 200 graceful shape on bad/missing audio."""
    client = app.test_client()
    resp = client.post("/api/voice/stt", data={})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data.get("transcript") == ""
    assert "error" in data


def test_hinglish_answer_grading():
    """Verify Hinglish answer 'virtual memory matlab RAM se bada space' is graded, not rejected."""
    from services.granite import grade_answer
    q = {
        "id": "q_vm",
        "text": "What is virtual memory and how does it work?",
        "key_points": ["virtual memory", "ram", "paging", "disk space"]
    }
    ans = "virtual memory matlab RAM se bada space hota hai jo disk par memory extend karta hai"
    result = grade_answer(q, ans, language="hi")
    assert result.get("verdict") in ("correct", "partial"), f"Expected correct/partial, got {result.get('verdict')}"
    assert result.get("score") >= 4.0, f"Expected score >= 4.0, got {result.get('score')}"
    assert not result.get("no_answer"), "Hinglish technical answer must not be flagged as no_answer"
