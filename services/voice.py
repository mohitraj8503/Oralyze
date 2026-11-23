"""Oralyze Voice Layer: Sarvam AI Integration.

Features:
- Primary voice provider: Sarvam AI (Indian languages & accents)
- STT with word-level timings via Saaras (model: saaras:v3)
- TTS with persistent in-memory caching via Bulbul (model: bulbul:v3)
- Translation via Mayura (model: mayura:v1)
- Language identification (LID)
- Rate limiting (20 req/min/IP), daily caps, and retry with jitter
- Strict metrics logging: zero text or secret logging
"""
import base64
import datetime
import hashlib
import logging
import random
import time
from typing import Any, Tuple
import requests
from config import Config

logger = logging.getLogger("voice")

# Reusable HTTP Session
_voice_session = requests.Session()

# TTS In-Memory Cache: sha1(text + lang + speaker) -> (audio_bytes, audio_b64)
_tts_cache: dict[str, Tuple[bytes, str]] = {}

# Usage and metrics tracking
_today_date: str = ""
_today_calls: int = 0
_cache_hits: int = 0
_cache_misses: int = 0
_estimated_spend_inr: float = 0.0

# IP-based rate limiting: ip -> list of timestamps within last 60 seconds
_ip_rate_limits: dict[str, list[float]] = {}


def _reset_daily_metrics_if_needed():
    global _today_date, _today_calls, _cache_hits, _cache_misses, _estimated_spend_inr
    current_date = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    if _today_date != current_date:
        _today_date = current_date
        _today_calls = 0
        _cache_hits = 0
        _cache_misses = 0
        _estimated_spend_inr = 0.0


def check_rate_limit(ip: str) -> bool:
    """Return True if request is allowed, False if IP exceeded 20 requests/minute."""
    now = time.time()
    cutoff = now - 60.0
    timestamps = [t for t in _ip_rate_limits.get(ip, []) if t > cutoff]
    if len(timestamps) >= 20:
        _ip_rate_limits[ip] = timestamps
        return False
    timestamps.append(now)
    _ip_rate_limits[ip] = timestamps
    return True


def _check_and_increment_daily(op_type: str, char_count: int = 0) -> bool:
    """Check daily budget guard and increment call count.
    
    Returns True if allowed, False if limit reached.
    """
    global _today_calls, _estimated_spend_inr
    _reset_daily_metrics_if_needed()
    
    limit = Config.SARVAM_DAILY_CALL_LIMIT
    if _today_calls >= limit:
        logger.warning("[voice] op=%s daily call limit reached (%d/%d)", op_type, _today_calls, limit)
        return False

    _today_calls += 1

    # Warn at 70% threshold
    if _today_calls == int(limit * 0.70):
        logger.warning("[voice] Warning: 70%% daily call quota reached (%d/%d)", _today_calls, limit)

    # Estimate spend
    if op_type == "tts":
        _estimated_spend_inr += char_count * 0.00025
    elif op_type == "stt":
        _estimated_spend_inr += 0.20  # ~30s chunk rate
    elif op_type == "translate":
        _estimated_spend_inr += char_count * 0.00010
    elif op_type == "lid":
        _estimated_spend_inr += 0.00005

    return True


def get_health() -> dict:
    """Return provider health status conforming to Section 0 spec."""
    return {
        "provider": "sarvam",
        "key_present": bool(Config.SARVAM_API_KEY),
        "credits_ok": True
    }


def get_usage() -> dict:
    """Return current usage and metrics conforming to Section 4 spec."""
    _reset_daily_metrics_if_needed()
    total_cached_reqs = _cache_hits + _cache_misses
    hit_rate = round(_cache_hits / total_cached_reqs, 2) if total_cached_reqs > 0 else 0.0
    return {
        "today_calls": _today_calls,
        "cached_hit_rate": hit_rate,
        "estimated_spend_inr": round(_estimated_spend_inr, 2),
        "daily_limit": Config.SARVAM_DAILY_CALL_LIMIT
    }


def _get_headers() -> dict:
    return {
        "api-subscription-key": Config.SARVAM_API_KEY,
        "Accept": "application/json"
    }


def synthesize_speech(
    text: str,
    language_code: str = "en-IN",
    speaker: str | None = None,
    pace: float = 1.0
) -> Tuple[bytes | None, str | None, bool]:
    """Synthesize text into speech using Sarvam Bulbul model.
    
    Returns (audio_bytes, audio_b64, is_cached).
    """
    global _cache_hits, _cache_misses
    clean_text = (text or "").strip()[:1000]
    if not clean_text:
        return None, None, False

    # Normalize language code
    lang = language_code.strip() if language_code else "en-IN"
    if lang in ("en", "en-US", "en_IN"):
        lang = "en-IN"
    elif lang in ("hi", "hi_IN"):
        lang = "hi-IN"

    chosen_speaker = (speaker or Config.SARVAM_DEFAULT_SPEAKER).strip() or "shubh"

    cache_hash = hashlib.sha1(f"{clean_text}:{lang}:{chosen_speaker}".encode("utf-8")).hexdigest()
    if cache_hash in _tts_cache:
        _cache_hits += 1
        logger.info("op=tts chars=%d ms=0 cached=true", len(clean_text))
        cached_bytes, cached_b64 = _tts_cache[cache_hash]
        return cached_bytes, cached_b64, True

    _cache_misses += 1

    if not Config.SARVAM_API_KEY or Config.MOCK_MODE:
        return None, None, False

    if not _check_and_increment_daily("tts", len(clean_text)):
        return None, None, False

    url = "https://api.sarvam.ai/text-to-speech"
    headers = _get_headers()
    headers["Content-Type"] = "application/json"
    payload = {
        "inputs": [clean_text],
        "target_language_code": lang,
        "speaker": chosen_speaker,
        "model": Config.SARVAM_TTS_MODEL,
        "pace": float(pace or 1.0)
    }

    t0 = time.time()
    for attempt in range(2):
        try:
            resp = _voice_session.post(url, headers=headers, json=payload, timeout=20)
            elapsed_ms = int((time.time() - t0) * 1000)
            if resp.status_code == 200:
                data = resp.json()
                audios = data.get("audios", [])
                if audios:
                    b64 = audios[0]
                    audio_bytes = base64.b64decode(b64)
                    _tts_cache[cache_hash] = (audio_bytes, b64)
                    logger.info("op=tts chars=%d ms=%d cached=false", len(clean_text), elapsed_ms)
                    return audio_bytes, b64, False
            elif resp.status_code in (429, 500, 502, 503, 504) and attempt == 0:
                time.sleep(0.300 + random.uniform(0.0, 0.05))
                continue
            else:
                logger.warning("op=tts http=%d ms=%d", resp.status_code, elapsed_ms)
                break
        except Exception as e:
            elapsed_ms = int((time.time() - t0) * 1000)
            if attempt == 0:
                time.sleep(0.300 + random.uniform(0.0, 0.05))
                continue
            logger.warning("op=tts error ms=%d", elapsed_ms)
            break

    return None, None, False


def transcribe_audio(
    audio_bytes: bytes,
    language: str = "auto",
    filename: str = "audio.webm"
) -> dict:
    """Transcribe audio bytes using Sarvam Saaras model with word timings."""
    if not audio_bytes or len(audio_bytes) > 10 * 1024 * 1024:
        return {"transcript": "", "error": "invalid_audio", "provider": "sarvam"}

    if not Config.SARVAM_API_KEY or Config.MOCK_MODE:
        return {"transcript": "", "error": "stt_disabled_or_mock", "provider": "sarvam"}

    if not _check_and_increment_daily("stt"):
        return {"transcript": "", "error": "daily_limit_reached", "provider": "sarvam"}

    lang_code = "unknown"
    if language in ("hi", "hi-IN", "hi_IN"):
        lang_code = "hi-IN"
    elif language in ("en", "en-IN", "en-US", "en_IN"):
        lang_code = "en-IN"

    url = "https://api.sarvam.ai/speech-to-text"
    headers = _get_headers()
    files = {"file": (filename, audio_bytes, "audio/webm")}
    data = {
        "model": Config.SARVAM_STT_MODEL,
        "language_code": lang_code,
        "with_timestamps": "true"
    }

    t0 = time.time()
    for attempt in range(2):
        try:
            resp = _voice_session.post(url, headers=headers, files=files, data=data, timeout=20)
            elapsed_ms = int((time.time() - t0) * 1000)
            if resp.status_code == 200:
                res_json = resp.json()
                transcript = res_json.get("transcript", "")
                detected_lang = res_json.get("language_code", lang_code)
                timestamps = res_json.get("timestamps", {}) or {}
                raw_words = timestamps.get("words", []) or []
                starts = timestamps.get("start_time_seconds", []) or []
                ends = timestamps.get("end_time_seconds", []) or []

                words = []
                for i, w in enumerate(raw_words):
                    words.append({
                        "word": w,
                        "start": starts[i] if i < len(starts) else 0.0,
                        "end": ends[i] if i < len(ends) else 0.0
                    })

                logger.info("op=stt words=%d ms=%d", len(words), elapsed_ms)
                return {
                    "transcript": transcript,
                    "language": detected_lang,
                    "words": words,
                    "provider": "sarvam",
                    "ms": elapsed_ms
                }
            elif resp.status_code in (429, 500, 502, 503, 504) and attempt == 0:
                time.sleep(0.300 + random.uniform(0.0, 0.05))
                continue
            else:
                logger.warning("op=stt http=%d ms=%d", resp.status_code, elapsed_ms)
                break
        except Exception:
            elapsed_ms = int((time.time() - t0) * 1000)
            if attempt == 0:
                time.sleep(0.300 + random.uniform(0.0, 0.05))
                continue
            logger.warning("op=stt error ms=%d", elapsed_ms)
            break

    return {"transcript": "", "error": "stt_failed", "provider": "sarvam"}


def translate_text(
    text: str,
    source_language_code: str = "en-IN",
    target_language_code: str = "hi-IN"
) -> dict:
    """Translate text between Indian languages using Mayura model."""
    clean_text = (text or "").strip()[:1000]
    if not clean_text:
        return {"translated_text": ""}

    if not Config.SARVAM_API_KEY or Config.MOCK_MODE:
        return {"translated_text": clean_text, "error": "mock_mode"}

    if not _check_and_increment_daily("translate", len(clean_text)):
        return {"translated_text": clean_text, "error": "daily_limit_reached"}

    url = "https://api.sarvam.ai/translate"
    headers = _get_headers()
    headers["Content-Type"] = "application/json"
    payload = {
        "input": clean_text,
        "source_language_code": source_language_code or "en-IN",
        "target_language_code": target_language_code or "hi-IN",
        "model": Config.SARVAM_TRANSLATE_MODEL
    }

    t0 = time.time()
    for attempt in range(2):
        try:
            resp = _voice_session.post(url, headers=headers, json=payload, timeout=20)
            elapsed_ms = int((time.time() - t0) * 1000)
            if resp.status_code == 200:
                res = resp.json().get("translated_text", "")
                logger.info("op=translate chars=%d ms=%d", len(clean_text), elapsed_ms)
                return {"translated_text": res}
            elif resp.status_code in (429, 500, 502, 503, 504) and attempt == 0:
                time.sleep(0.300 + random.uniform(0.0, 0.05))
                continue
            else:
                logger.warning("op=translate http=%d ms=%d", resp.status_code, elapsed_ms)
                break
        except Exception:
            elapsed_ms = int((time.time() - t0) * 1000)
            if attempt == 0:
                time.sleep(0.300 + random.uniform(0.0, 0.05))
                continue
            logger.warning("op=translate error ms=%d", elapsed_ms)
            break

    return {"translated_text": clean_text, "error": "translate_failed"}


def identify_language(text: str) -> dict:
    """Detect language and script of text."""
    clean_text = (text or "").strip()[:1000]
    if not clean_text:
        return {"language_code": "unknown"}

    if not Config.SARVAM_API_KEY or Config.MOCK_MODE:
        return {"language_code": "en-IN"}

    if not _check_and_increment_daily("lid"):
        return {"language_code": "unknown", "error": "daily_limit_reached"}

    url = "https://api.sarvam.ai/text-lid"
    headers = _get_headers()
    headers["Content-Type"] = "application/json"
    payload = {"input": clean_text}

    t0 = time.time()
    for attempt in range(2):
        try:
            resp = _voice_session.post(url, headers=headers, json=payload, timeout=20)
            elapsed_ms = int((time.time() - t0) * 1000)
            if resp.status_code == 200:
                lang_code = resp.json().get("language_code", "unknown")
                logger.info("op=lid chars=%d ms=%d", len(clean_text), elapsed_ms)
                return {"language_code": lang_code}
            elif resp.status_code in (429, 500, 502, 503, 504) and attempt == 0:
                time.sleep(0.300 + random.uniform(0.0, 0.05))
                continue
            else:
                logger.warning("op=lid http=%d ms=%d", resp.status_code, elapsed_ms)
                break
        except Exception:
            elapsed_ms = int((time.time() - t0) * 1000)
            if attempt == 0:
                time.sleep(0.300 + random.uniform(0.0, 0.05))
                continue
            logger.warning("op=lid error ms=%d", elapsed_ms)
            break

    return {"language_code": "unknown", "error": "lid_failed"}
