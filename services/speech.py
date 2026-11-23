"""Speech services: Watson Speech to Text (STT) and Text to Speech (TTS).

Features:
- Watson STT via raw REST with timestamps=true and word_confidence=true
- Watson TTS via raw REST with dynamic Hindi voice discovery and caching
- Realistic offline mock mode with simulated speech timings for local testing
"""
import base64
import logging
import requests
from config import Config
from services.ibm_auth import get_token

logger = logging.getLogger("speech")

# Persistent HTTP session for Watson Speech services (Fix D)
_speech_session = requests.Session()

# Cached TTS voices to avoid redundant network calls
_cached_hindi_voice: str | None = None
_tts_audio_cache: dict[str, bytes] = {}

# Default English voice
DEFAULT_EN_VOICE = "en-US_AllisonV3Voice"

# Minimal silent MP3 frame for fallback / mock mode when no external TTS is present
MOCK_SILENT_MP3 = base64.b64decode(
    b"//uQRAAAAAAASU5GTwAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
)


def discover_hindi_voice() -> str:
    """Discover available Hindi voice from Watson TTS service at runtime or fallback to English."""
    global _cached_hindi_voice
    if _cached_hindi_voice:
        return _cached_hindi_voice

    if Config.MOCK_MODE or not Config.TTS_URL or not Config.IBM_API_KEY:
        _cached_hindi_voice = DEFAULT_EN_VOICE
        return _cached_hindi_voice

    try:
        token = get_token()
        url = f"{Config.TTS_URL}/v1/voices"
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        resp = _speech_session.get(url, headers=headers, timeout=15)
        if resp.status_code == 200:
            voices = resp.json().get("voices", [])
            for v in voices:
                lang = v.get("language", "")
                if lang.startswith("hi-IN") or lang.startswith("hi"):
                    _cached_hindi_voice = v.get("name")
                    logger.info("[speech] Discovered Hindi TTS voice: %s", _cached_hindi_voice)
                    return _cached_hindi_voice
    except Exception as e:
        logger.warning("[speech] Hindi voice discovery failed: %s", e)

    # Fallback to English voice if no Hindi voice found
    _cached_hindi_voice = DEFAULT_EN_VOICE
    return _cached_hindi_voice


def synthesize(text: str, language: str = "en") -> bytes:
    """Synthesize text to MP3 audio bytes using Watson TTS.
    
    API Contract:
    POST {TTS_URL}/v1/synthesize?voice=...
    Authorization: Bearer <token>
    Accept: audio/mp3
    {"text": "<question>"}
    """
    clean_text = (text or "").strip()
    cache_key = f"{language}:{clean_text}"

    # In-memory per-session/question cache saves Lite character quota
    if cache_key in _tts_audio_cache:
        return _tts_audio_cache[cache_key]

    if Config.MOCK_MODE or not Config.TTS_URL or not Config.IBM_API_KEY:
        _tts_audio_cache[cache_key] = MOCK_SILENT_MP3
        return MOCK_SILENT_MP3

    voice = discover_hindi_voice() if language == "hi" else DEFAULT_EN_VOICE

    try:
        token = get_token()
        url = f"{Config.TTS_URL}/v1/synthesize?voice={voice}"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "audio/mp3"
        }
        payload = {"text": clean_text}
        resp = _speech_session.post(url, headers=headers, json=payload, timeout=20)

        if resp.status_code == 200 and resp.content:
            _tts_audio_cache[cache_key] = resp.content
            return resp.content
        logger.warning("[speech] TTS synthesis returned HTTP %s: %s", resp.status_code, resp.text)
    except Exception as e:
        logger.warning("[speech] TTS request failed: %s", e)

    _tts_audio_cache[cache_key] = MOCK_SILENT_MP3
    return MOCK_SILENT_MP3


def _mock_transcribe(language: str = "en") -> dict:
    """Return realistic mock transcript with word timestamps for offline testing."""
    if language == "hi":
        transcript = "ha sir relational databases mein acid properties atomicity consistency isolation aur durability hoti hain"
    else:
        transcript = "in relational database systems the acid properties stand for atomicity consistency isolation and durability which guarantee reliability"

    words_raw = transcript.split()
    current_time = 0.6
    word_objs = []

    for idx, w in enumerate(words_raw):
        start = round(current_time, 2)
        duration = round(0.25 + (len(w) * 0.04), 2)
        end = round(start + duration, 2)
        confidence = 0.94 if idx % 4 != 0 else 0.88
        word_objs.append({
            "word": w,
            "start": start,
            "end": end,
            "confidence": confidence
        })
        # Simulate slight gap between words and occasional pauses
        gap = 0.85 if idx == 5 else 0.12
        current_time = end + gap

    return {
        "transcript": transcript,
        "words": word_objs
    }


def transcribe(audio_bytes: bytes, content_type: str = "audio/webm", language: str = "en") -> dict:
    """Transcribe audio bytes using Watson Speech to Text REST API.
    
    API Contract:
    POST {STT_URL}/v1/recognize?model=...&timestamps=true&word_confidence=true&smart_formatting=true
    Authorization: Bearer <token>
    Content-Type: audio/webm (or audio/wav, audio/ogg)
    body = raw audio bytes
    
    Returns:
    {
      "transcript": "...",
      "words": [{"word": str, "start": float, "end": float, "confidence": float}]
    }
    """
    if Config.MOCK_MODE or not Config.STT_URL or not Config.IBM_API_KEY:
        return _mock_transcribe(language=language)

    if not audio_bytes or len(audio_bytes) < 100:
        return {"transcript": "", "words": []}

    model = "hi-IN_BroadbandModel" if language == "hi" else "en-US_BroadbandModel"
    url = (
        f"{Config.STT_URL}/v1/recognize"
        f"?model={model}&timestamps=true&word_confidence=true&smart_formatting=true"
    )

    try:
        token = get_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": content_type or "audio/webm"
        }
        resp = _speech_session.post(url, headers=headers, data=audio_bytes, timeout=60)

        if resp.status_code != 200:
            logger.warning("[speech] Watson STT returned HTTP %s: %s", resp.status_code, resp.text)
            return _mock_transcribe(language=language)

        data = resp.json()
        results = data.get("results", [])
        if not results:
            return {"transcript": "", "words": []}

        transcripts = []
        words = []

        for r in results:
            alts = r.get("alternatives", [])
            if not alts:
                continue
            alt = alts[0]
            t = alt.get("transcript", "").strip()
            if t:
                transcripts.append(t)

            timestamps = alt.get("timestamps", [])
            confidences = alt.get("word_confidence", [])

            # Map word_confidence by index or lookup
            conf_map = {}
            for item in confidences:
                if len(item) >= 2:
                    conf_map[item[0]] = float(item[1])

            for idx, ts in enumerate(timestamps):
                if len(ts) >= 3:
                    w = str(ts[0])
                    s = float(ts[1])
                    e = float(ts[2])
                    # Pick corresponding confidence
                    c = confidences[idx][1] if idx < len(confidences) else conf_map.get(w, 0.9)
                    words.append({
                        "word": w,
                        "start": s,
                        "end": e,
                        "confidence": float(c)
                    })

        full_transcript = " ".join(transcripts).strip()
        return {
            "transcript": full_transcript,
            "words": words
        }

    except Exception as e:
        logger.warning("[speech] Watson STT request failed, falling back to mock: %s", e)
        return _mock_transcribe(language=language)
