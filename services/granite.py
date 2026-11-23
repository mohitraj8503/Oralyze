from services.llm import call_llm
"""IBM Granite LLM service wrapper.

Handles viva question generation from teacher natural-language prompts and rubric-based answer grading.
Provides deterministic local offline fallbacks when in MOCK_MODE or on network error.
"""
import hashlib
import json
import logging
import os
import re
import time
import requests
from config import Config
from services.ibm_auth import get_token, IAMAuthError

logger = logging.getLogger("granite")

# Persistent HTTP session for Granite with connection pooling
_session = requests.Session()

# In-memory grading cache: sha1(qid + transcript) -> grading_result
_grading_cache: dict[str, dict] = {}

# Rate limiting for IBM watsonx Lite plan (2 req/sec)
_last_granite_call_time = 0.0

# In-memory prompt preview cache: sha1 -> (timestamp, preview_result)
_prompt_cache: dict[str, tuple[float, dict]] = {}
CACHE_TTL = 600  # 10 minutes

# Hinglish & Hindi transliteration normalisation dictionary (Fix B)
HINGLISH_MAPPINGS = {
    "इंडेक्स": "index", "इंडैक्स": "index",
    "टेबल": "table",
    "की": "key",
    "ट्री": "tree",
    "डेडलॉक": "deadlock",
    "क्वेरी": "query",
    "डेटाबेस": "database",
    "मेमोरी": "memory",
    "प्रोसेस": "process",
    "एल्गोरिदम": "algorithm", "एल्गोरिथम": "algorithm",
    "नेटवर्क": "network",
    "कैश": "cache",
    "थ्रेड": "thread",
    "सर्वर": "server",
    "क्लाइंट": "client",
}

def _normalize_tokens(text: str) -> str:
    """Normalize Hindi/Hinglish text and strip punctuation."""
    cleaned = (text or "").lower()
    for devanagari, eng in HINGLISH_MAPPINGS.items():
        cleaned = cleaned.replace(devanagari, eng)
    cleaned = re.sub(r'[^a-zA-Z0-9\s]', ' ', cleaned)
    return re.sub(r'\s+', ' ', cleaned).strip()

def _extract_prompt_keywords(prompt: str) -> list[str]:
    """Extract significant keywords from natural language prompt."""
    stopwords = {
        "ask", "me", "questions", "question", "about", "quiz", "oral", "exam",
        "viva", "assessment", "interview", "test", "hard", "easy", "medium",
        "simple", "complex", "please", "give", "generate", "five", "three",
        "four", "ten", "the", "for", "and", "with", "from", "into", "that",
        "this", "have", "been", "will", "would", "what", "how", "why", "when",
        "some", "such", "than", "them", "then", "there", "these", "they", "candidate"
    }
    tokens = re.findall(r'[a-zA-Z0-9_\-\+]+', (prompt or "").lower())
    return [t for t in tokens if len(t) > 2 and t not in stopwords]

# Path to offline question bank
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
QUESTIONS_FILE = os.path.join(DATA_DIR, "questions.json")

# In-memory question bank cache
_question_bank: dict | None = None


def _rate_limit_granite():
    """Ensure at least 550ms between requests to respect IBM Lite 2 req/sec rate limit."""
    global _last_granite_call_time
    now = time.time()
    elapsed = now - _last_granite_call_time
    if elapsed < 0.55:
        time.sleep(0.55 - elapsed)
    _last_granite_call_time = time.time()


def load_question_bank() -> dict:
    """Load local questions.json file."""
    global _question_bank
    if _question_bank is None:
        try:
            with open(QUESTIONS_FILE, "r", encoding="utf-8") as f:
                _question_bank = json.load(f)
        except Exception as e:
            logger.warning("[granite] Failed to load offline questions.json: %s", e)
            _question_bank = {
                "default": [
                    {
                        "text": "What is the difference between synchronous and asynchronous operations?",
                        "level": "understand",
                        "key_points": ["blocking", "non-blocking", "event loop", "callback", "concurrency"]
                    }
                ]
            }
    return _question_bank


def _extract_json(text: str) -> dict | list | None:
    """Extract valid JSON from LLM response text, handling markdown fences and prose."""
    if not text:
        return None
    cleaned = re.sub(r"^```(?:json)?\s*", "", text.strip(), flags=re.MULTILINE)
    cleaned = re.sub(r"\s*```$", "", cleaned.strip(), flags=re.MULTILINE)

    try:
        return json.loads(cleaned)
    except Exception:
        pass

    # Look for { ... } object first
    first_brace = cleaned.find("{")
    last_brace = cleaned.rfind("}")
    if first_brace != -1 and last_brace > first_brace:
        try:
            return json.loads(cleaned[first_brace:last_brace + 1])
        except Exception:
            pass

    # Look for [ ... ] array
    first_bracket = cleaned.find("[")
    last_bracket = cleaned.rfind("]")
    if first_bracket != -1 and last_bracket > first_bracket:
        try:
            return json.loads(cleaned[first_bracket:last_bracket + 1])
        except Exception:
            pass

    return None


def chat(messages: list[dict], max_tokens: int = 1200, temperature: float = 0.4, top_p: float = 0.9) -> str:
    """Call IBM watsonx.ai text chat API for Granite.
    
    API Contract:
    POST {WATSONX_URL}/ml/v1/text/chat?version=2023-05-29
    Authorization: Bearer <token>
    {"model_id": "ibm/granite-3-3-8b-instruct", "project_id": "<PROJECT_ID>", ...}
    """
    _rate_limit_granite()
    token = get_token()
    url = f"{Config.WATSONX_URL}/ml/v1/text/chat?version=2023-05-29"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json"
    }
    payload = {
        "model_id": Config.GRANITE_MODEL_ID,
        "project_id": Config.WATSONX_PROJECT_ID,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "top_p": top_p
    }

    resp = _session.post(url, headers=headers, json=payload, timeout=30)
    if resp.status_code != 200:
        raise RuntimeError(f"Granite API error (HTTP {resp.status_code}): {resp.text}")

    data = resp.json()
    choices = data.get("choices", [])
    if not choices or "message" not in choices[0]:
        raise RuntimeError(f"Malformed Granite response: {data}")

    return choices[0]["message"].get("content", "")


def get_bank_questions(subject: str, n: int = 5) -> list[dict]:
    """Retrieve up to n questions from the local question bank."""
    bank = load_question_bank()
    key = subject.lower().strip()

    matches = []
    for k, questions in bank.items():
        if k == key or k in key or key in k:
            matches = questions
            break

    if not matches:
        matches = bank.get("default", [])

    selected = matches[:n]
    if len(selected) < n and bank.get("default"):
        for q in bank["default"]:
            if len(selected) >= n:
                break
            if q not in selected:
                selected.append(q)

    # Normalize fields
    normalized = []
    for idx, q in enumerate(selected[:n]):
        normalized.append({
            "id": f"q{idx+1}",
            "text": q.get("text", ""),
            "bloom": q.get("level", q.get("bloom", "understand")),
            "key_points": q.get("key_points", []),
            "ideal_seconds": 45
        })
    return normalized


def sanitize_prompt(raw_prompt: str) -> str:
    """Sanitize teacher prompt before sending to model.
    
    Collapse whitespace, strip backticks, {{ }}, control chars, and system:/assistant: prefixes.
    Truncate at 500 chars.
    """
    if not raw_prompt:
        return ""
    # Strip prefixes
    text = re.sub(r"^(?:system|assistant|user)\s*:\s*", "", raw_prompt.strip(), flags=re.IGNORECASE)
    # Strip backticks and curly braces
    text = re.sub(r"[`{}]+", "", text)
    # Strip control characters
    text = re.sub(r"[\x00-\x1f\x7f-\x9f]", " ", text)
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text[:500]


def _similarity(s1: str, s2: str) -> float:
    """Word overlap ratio between two question strings."""
    w1 = set(re.findall(r"\w+", s1.lower()))
    w2 = set(re.findall(r"\w+", s2.lower()))
    if not w1 or not w2:
        return 0.0
    return len(w1 & w2) / max(len(w1), len(w2))


def preview_viva_from_prompt(
    prompt: str,
    difficulty: str = "medium",
    language: str = "English",
    count: int = 5,
    mode: str = "university_viva",
    candidate_seed: str = ""
) -> tuple[dict, int]:
    """Generate or retrieve cached viva questions from teacher prompt.
    
    Returns (result_dict, status_code).
    """
    cleaned = sanitize_prompt(prompt)
    if len(cleaned) < 5:
        return {
            "error": "need_more_detail",
            "message": "Tell me the subject and the topic."
        }, 422

    # Clamp count 3-7
    target_count = max(3, min(int(count or 5), 7))
    difficulty = difficulty.lower() if difficulty in ("easy", "medium", "hard") else "medium"
    language = "Hindi" if "hindi" in str(language).lower() else "English"
    mode = str(mode or "university_viva").lower().strip()

    # Cache key per Fix C: sha1(host_prompt | mode | count | difficulty | language | candidate_seed)
    cache_key = hashlib.sha1(f"{cleaned}|{mode}|{target_count}|{difficulty}|{language}|{candidate_seed}".encode("utf-8")).hexdigest()
    now = time.time()
    if cache_key in _prompt_cache:
        cached_time, cached_val = _prompt_cache[cache_key]
        if now - cached_time < CACHE_TTL:
            logger.info("[granite] Returning cached preview for prompt: %s...", cleaned[:30])
            return cached_val, 200

    if Config.MOCK_MODE or not Config.IBM_API_KEY:
        fallback_res = _make_fallback_result(cleaned, difficulty, language, target_count, mode)
        fallback_res["source"] = "fallback"
        _prompt_cache[cache_key] = (now, fallback_res)
        return fallback_res, 200

    # System prompt specified verbatim in B2, adapted for mode
    system_prompt = f"""You are an autonomous oral assessment agent conducting an evaluation in mode: {mode}.
You write oral assessment questions that a candidate answers out loud in 30 to 60 seconds.

You always answer with a single JSON object and nothing else. No explanation, no markdown, no code fences.

JSON shape:
{{
  "title": "<short viva title, max 6 words>",
  "subject": "<subject name, max 4 words>",
  "topic": "<topic or chapter, max 6 words>",
  "difficulty": "easy|medium|hard",
  "questions": [
    {{
      "text": "<the question, one or two sentences, spoken style>",
      "bloom": "remember|understand|apply|analyse",
      "key_points": ["<point an ideal answer must contain>", "<point>", "<point>"],
      "ideal_seconds": <integer 20-60>
    }}
  ]
}}

Hard rules:
- Produce exactly the number of questions requested.
- Never write yes/no questions, never multiple choice, never "define X" alone.
- At least half the questions must be apply or analyse level, not recall.
- key_points: 2 to 5 short phrases, each under 12 words.
- Use plain English that a student can understand when heard once, out loud.
- No two questions may test the same idea.
- If the request mentions a language other than English, write the question text in that language but keep the JSON keys in English."""

    user_message = f"""Write an assessment from this request:

<prompt>{cleaned}</prompt>

Settings: mode={mode}, difficulty={difficulty}, number of questions={target_count}, language={language}.
Return only the JSON object."""

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message}
    ]

    parsed = None
    for attempt in range(2):
        try:
            raw_res = chat(messages, max_tokens=1200, temperature=0.4, top_p=0.9)
            parsed = _extract_json(raw_res)
            if isinstance(parsed, dict) and "questions" in parsed and isinstance(parsed["questions"], list):
                break
        except Exception as e:
            logger.warning("[granite] attempt %d generation failed: %s", attempt + 1, e)

        # Retry once with JSON repair message
        if attempt == 0:
            messages.append({"role": "assistant", "content": raw_res if 'raw_res' in locals() else ""})
            messages.append({
                "role": "user",
                "content": "Your previous reply was not valid JSON. Reply again with only the JSON object, no other text."
            })

    # Validate parsed JSON according to Section B5
    validated_questions = []
    if isinstance(parsed, dict):
        raw_qs = parsed.get("questions", [])
        for item in raw_qs:
            if not isinstance(item, dict):
                continue
            text = str(item.get("text", "")).strip()
            bloom = str(item.get("bloom", item.get("level", "understand"))).lower()
            if bloom not in ("remember", "understand", "apply", "analyse", "analyze"):
                bloom = "understand"
            if bloom == "analyze":
                bloom = "analyse"

            # Check: text 20-260 chars
            if len(text) < 15 or len(text) > 280:
                continue

            kps = [str(k).strip() for k in item.get("key_points", []) if str(k).strip()]
            if len(kps) < 2:
                continue

            ideal_sec = int(item.get("ideal_seconds", 40))
            ideal_sec = max(15, min(ideal_sec, 90))

            # Deduplicate: if shares >70% words with existing question
            is_dup = False
            for existing in validated_questions:
                if _similarity(text, existing["text"]) > 0.70:
                    is_dup = True
                    break
            if is_dup:
                continue

            validated_questions.append({
                "id": f"q{len(validated_questions)+1}",
                "text": text,
                "bloom": bloom,
                "key_points": kps[:5],
                "ideal_seconds": ideal_sec,
                "type": "DYNAMIC"
            })

    # If valid questions count is acceptable (allow target_count-1, never fewer than 3)
    if len(validated_questions) >= min(3, target_count - 1):
        res = {
            "title": str(parsed.get("title", f"{cleaned[:20]} Assessment")).strip()[:40],
            "subject": str(parsed.get("subject", "Systems")).strip()[:30],
            "topic": str(parsed.get("topic", cleaned[:30])).strip()[:40],
            "difficulty": difficulty,
            "language": language,
            "mode": mode,
            "questions": validated_questions[:target_count],
            "source": "granite"
        }
        _prompt_cache[cache_key] = (now, res)
        return res, 200

    # Fallback to prompt-aware templates if model generation fails
    logger.warning("[granite] Validation failed or insufficient questions generated. Using prompt-aware fallback.")
    fallback_res = _make_fallback_result(cleaned, difficulty, language, target_count, mode)
    fallback_res["source"] = "fallback"
    _prompt_cache[cache_key] = (now, fallback_res)
    return fallback_res, 200


def _make_fallback_result(prompt: str, difficulty: str, language: str, count: int, mode: str = "university_viva") -> dict:
    """Create structured viva result dynamically generated around prompt keywords.
    
    Guarantees zero hardcoded legacy subjects, prompt-driven topic echo, and mode support.
    """
    keywords = _extract_prompt_keywords(prompt)
    if keywords:
        topic_phrase = " ".join(t.capitalize() for t in keywords[:4])
        subject_phrase = " ".join(t.capitalize() for t in keywords[:2])
    else:
        topic_phrase = "System Architecture"
        subject_phrase = "Computer Systems"

    # Dynamic prompt-aware question templates
    primary_kw = keywords[0] if keywords else "the architecture"
    secondary_kw = keywords[1] if len(keywords) > 1 else primary_kw
    tertiary_kw = keywords[2] if len(keywords) > 2 else secondary_kw

    templates = [
        (
            f"Explain the architectural foundations, core mechanisms, and primary design goals of {primary_kw}.",
            "understand",
            [f"{primary_kw} architecture", "fundamental principles", "core components"],
            35
        ),
        (
            f"How do you address performance bottlenecks, scaling constraints, or failure recovery in {secondary_kw}?",
            "analyse",
            [f"{secondary_kw} scaling", "bottleneck isolation", "failure recovery", "latency optimization"],
            45
        ),
        (
            f"Compare the major trade-offs and operational practices when managing {tertiary_kw} versus conventional alternatives.",
            "analyse",
            [f"{tertiary_kw} trade-offs", "operational overhead", "resource trade-offs"],
            45
        ),
        (
            f"Describe a real-world production outage or failure scenario in {primary_kw} and how you would troubleshoot it.",
            "apply",
            ["troubleshooting steps", "root cause identification", "preventive remediation"],
            50
        ),
        (
            f"How does {topic_phrase} guarantee fault tolerance, data integrity, and strict consistency under high load?",
            "understand",
            ["fault tolerance", "consistency mechanisms", "load handling"],
            40
        ),
        (
            f"What security practices and access boundary controls are mandatory when deploying {secondary_kw}?",
            "apply",
            ["access control", "least privilege", "encryption and isolation"],
            40
        ),
        (
            f"Walk through your step-by-step verification and observability strategy for {primary_kw}.",
            "apply",
            ["metrics telemetry", "health checks", "logging and auditing"],
            40
        )
    ]

    questions = []
    # If mode has baseline question (e.g. university_viva roll number, interview intro)
    if mode == "interview":
        questions.append({
            "id": "q1",
            "text": f"Walk through your direct background and specific production experience with {topic_phrase}.",
            "bloom": "understand",
            "type": "FIXED",
            "key_points": ["prior production experience", "role and scope", "system impact"],
            "ideal_seconds": 45
        })
    elif mode == "university_viva":
        questions.append({
            "id": "q1",
            "text": f"State your full name, student registration number, and your core topic focus for {subject_phrase}.",
            "bloom": "remember",
            "type": "FIXED",
            "key_points": ["candidate identity", "enrollment id", "topic scope"],
            "ideal_seconds": 25
        })

    for idx, (q_text, bloom, kps, ideal_sec) in enumerate(templates):
        if len(questions) >= count:
            break
        questions.append({
            "id": f"q{len(questions)+1}",
            "text": q_text,
            "bloom": bloom,
            "type": "DYNAMIC",
            "key_points": kps,
            "ideal_seconds": ideal_sec
        })

    return {
        "title": f"{subject_phrase} Oral Assessment",
        "subject": subject_phrase,
        "topic": topic_phrase,
        "difficulty": difficulty,
        "language": language,
        "mode": mode,
        "questions": questions[:count],
        "source": "fallback"
    }


def generate_questions(subject: str, level: str = "undergraduate", n: int = 5) -> list[dict]:
    """Compatibility wrapper for question generation."""
    res, _ = preview_viva_from_prompt(f"{subject} {level} oral assessment", count=n)
    return res.get("questions", [])


def _keyword_grade(question: dict, transcript: str) -> dict:
    """Deterministic keyword-based grading fallback when offline or on API failure."""
    raw_transcript = (transcript or "").strip()
    norm_transcript = _normalize_tokens(raw_transcript)
    words = norm_transcript.split() if norm_transcript else []
    key_points = [kp.strip() for kp in question.get("key_points", []) if kp]

    # Section 3 / Fix B: Transcripts with fewer than 5 words must be 'no_answer'
    if len(words) < 5:
        return {
            "score": 0.0,
            "verdict": "no_answer",
            "no_answer": True,
            "source": "heuristic",
            "correct_points": [],
            "missing_points": key_points,
            "feedback": "We could not hear an answer — record again or type it",
            "ideal_answer": f"Key concepts that should be explained: {', '.join(key_points)}."
        }

    correct_points = []
    missing_points = []
    norm_kps = [_normalize_tokens(kp) for kp in key_points]

    matched_stems_count = 0
    for original_kp, norm_kp in zip(key_points, norm_kps):
        kp_tokens = [t for t in norm_kp.split() if len(t) > 2]
        matched = False
        if norm_kp and norm_kp in norm_transcript:
            matched = True
        elif kp_tokens:
            token_matches = sum(1 for t in kp_tokens if t in norm_transcript)
            if token_matches / len(kp_tokens) >= 0.5:
                matched = True
            elif token_matches >= 1:
                matched_stems_count += 1
        if matched:
            correct_points.append(original_kp)
            matched_stems_count += 1
        else:
            missing_points.append(original_kp)

    total_pts = max(len(key_points), 1)
    matched_ratio = len(correct_points) / total_pts
    length_bonus = min(len(words) / 30.0, 1.0) * 1.5

    raw_score = (matched_ratio * 8.5) + length_bonus
    score = round(min(max(raw_score, 1.0), 10.0), 1)

    # Section 3 / Fix B: Lenient fallback: 25+ words with >= 2 stems floors at 3.0 and source="heuristic"
    source = "keyword"
    if len(words) >= 25 and matched_stems_count >= 2:
        if score < 3.0:
            score = 3.0
        source = "heuristic"

    # Three verdicts, not two: correct (>= 7.0), partial (4.0 - 6.9), wrong (< 4.0)
    if score >= 7.0:
        verdict = "correct"
        feedback = "Solid conceptual explanation covering the key principles accurately."
    elif score >= 4.0:
        verdict = "partial"
        feedback = "Identified several essential points, but missed some key details."
    else:
        verdict = "wrong"
        feedback = "The answer did not adequately address the core concepts of the question."

    return {
        "score": score,
        "verdict": verdict,
        "source": source,
        "correct_points": correct_points,
        "missing_points": missing_points,
        "feedback": feedback,
        "ideal_answer": f"Key concepts that should be explained: {', '.join(key_points)}."
    }


def grade_answer(question: dict, transcript: str, language: str = "en") -> dict:
    """Grade viva answer using IBM Granite rubric prompt with deterministic keyword fallback."""
    raw_transcript = (transcript or "").strip()
    words = raw_transcript.split()
    if len(words) < 5:
        return _keyword_grade(question, transcript)

    qid = str(question.get("qid") or question.get("id") or "")
    cache_token = hashlib.sha1(f"{qid}_{raw_transcript}".encode("utf-8")).hexdigest()
    if cache_token in _grading_cache:
        return _grading_cache[cache_token]

    if Config.MOCK_MODE or not Config.IBM_API_KEY:
        res = _keyword_grade(question, transcript)
        _grading_cache[cache_token] = res
        return res

    prompt = f"""You are an expert oral assessment examiner grading a candidate's spoken answer.

Question: {question.get('text', '')}
Expected Key Points: {json.dumps(question.get('key_points', []))}
Candidate's Spoken Transcript: "{raw_transcript}"
Language: {language}

Evaluate the candidate's answer fairly based on conceptual understanding. Spoken answers may have colloquial phrasing.
Respond ONLY with a valid JSON object matching this schema, with no markdown fences, no formatting:
{{
  "score": <float between 0.0 and 10.0>,
  "verdict": "<correct|partial|wrong>",
  "correct_points": ["<points the candidate mentioned correctly>"],
  "missing_points": ["<key points the candidate omitted or got wrong>"],
  "feedback": "<2-3 sentence constructive critique for the candidate>",
  "ideal_answer": "<concise 2-sentence reference answer>"
}}
"""

    try:
        raw_res = chat([{"role": "user", "content": prompt}], max_tokens=400, temperature=0.2)
        parsed = _extract_json(raw_res)
        if isinstance(parsed, dict) and "score" in parsed:
            score = float(parsed.get("score", 0.0))
            score = max(0.0, min(10.0, round(score, 1)))
            raw_v = str(parsed.get("verdict", "")).lower()
            if score >= 7.0 or raw_v == "correct":
                verdict = "correct"
            elif score >= 4.0 or "part" in raw_v:
                verdict = "partial"
            else:
                verdict = "wrong"

            eval_res = {
                "score": score,
                "verdict": verdict,
                "source": "granite",
                "correct_points": [str(p) for p in parsed.get("correct_points", [])],
                "missing_points": [str(p) for p in parsed.get("missing_points", [])],
                "feedback": str(parsed.get("feedback", "")).strip() or "Good effort.",
                "ideal_answer": str(parsed.get("ideal_answer", "")).strip() or f"Key concepts: {', '.join(question.get('key_points', []))}."
            }
            _grading_cache[cache_token] = eval_res
            return eval_res
    except Exception as e:
        logger.warning("[granite] Answer grading failed via Granite, falling back to keyword grader: %s", e)

    res = _keyword_grade(question, transcript)
    _grading_cache[cache_token] = res
    return res


def detect_scripted_response(
    transcript: str,
    latency_sec: float = 0.0,
    word_count: int = 0
) -> dict:
    """Anti-cheating heuristic & semantic detector.
    
    Identifies reading from pre-generated AI scripts, prompt-typing delays (5-8s latency
    followed by rapid textbook answers), and unnatural lexical density.
    """
    clean_text = (transcript or "").strip()
    words = clean_text.split()
    total_words = len(words) if word_count <= 0 else word_count

    if total_words < 5:
        return {
            "is_suspect": False,
            "risk_level": "CLEAN",
            "risk_score": 0,
            "reasons": [],
            "suggests_probe": False,
            "lexical_density": 0.0
        }

    reasons = []
    risk_score = 0  # 0 to 100

    # 1. Suspicious prompt-typing latency check (4.8s to 9.2s delay before instant recital)
    if 4.8 <= latency_sec <= 9.2 and total_words >= 15:
        risk_score += 40
        reasons.append(f"Prompt-typing delay detected ({latency_sec:.1f}s hesitation before fluent recital)")

    # 2. Textbook & AI-scripted transition markers
    script_markers = [
        "furthermore", "moreover", "in conclusion", "to summarize", "crucial aspect",
        "it is essential to note", "firstly", "secondly", "subsequently", "delve into",
        "in summary", "pivotal role", "seamlessly", "testament to"
    ]
    matched_markers = [m for m in script_markers if m in clean_text.lower()]
    if matched_markers:
        risk_score += min(len(matched_markers) * 20, 45)
        reasons.append(f"Formal written-script markers detected ({', '.join(matched_markers[:3])})")

    # 3. Lexical density (disproportionate number of long academic words)
    long_words = [w for w in words if len(w) >= 8]
    lexical_density = round(len(long_words) / max(total_words, 1), 2)
    if lexical_density >= 0.30 and total_words >= 20:
        risk_score += 25
        reasons.append(f"Unusually high written lexical density ({int(lexical_density * 100)}% polysyllabic tokens)")

    # Determine risk level
    if risk_score >= 60:
        risk_level = "HIGH"
    elif risk_score >= 30:
        risk_level = "MEDIUM"
    elif risk_score >= 15:
        risk_level = "LOW"
    else:
        risk_level = "CLEAN"

    is_suspect = risk_level in ("HIGH", "MEDIUM")
    suggests_probe = is_suspect or (risk_score >= 25)

    return {
        "is_suspect": is_suspect,
        "risk_level": risk_level,
        "risk_score": risk_score,
        "reasons": reasons,
        "suggests_probe": suggests_probe,
        "lexical_density": lexical_density
    }


def generate_adaptive_probe(
    prev_question: dict | str,
    prev_answer: str,
    topic: str = "",
    reason: str = "deepen"
) -> dict:
    """Generate a spontaneous 'Why / How / What-If' follow-up probe.
    
    Used for cross-examining candidate answers, checking script comprehension,
    or probing deeper edge cases.
    """
    q_text = prev_question.get("text", "") if isinstance(prev_question, dict) else str(prev_question)
    ans_text = (prev_answer or "").strip()

    # If LLM is available and not in mock mode, attempt dynamic model-generated probe
    if not Config.MOCK_MODE and Config.IBM_API_KEY:
        system_prompt = """You are Oralize AI, an expert viva examiner.
A candidate just answered a question. You must ask ONE concise, spontaneous spoken follow-up counter-question.
Ask a direct "Why / How / What-If" question testing their genuine understanding of the exact point they made.
Return ONLY a valid JSON object:
{
  "text": "<one single spoken sentence, max 25 words>",
  "key_points": ["<expected point 1>", "<expected point 2>"]
}"""
        user_msg = f"""Original Question: {q_text}
Candidate's Spoken Answer: "{ans_text}"
Topic: {topic}
Probe Intent: {reason} (e.g. verify understanding or explore edge case).
Produce the single counter-question JSON:"""

        try:
            raw = chat([
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_msg}
            ], max_tokens=250, temperature=0.5)
            parsed = _extract_json(raw)
            if isinstance(parsed, dict) and parsed.get("text"):
                return {
                    "id": f"probe_{int(time.time() * 1000) % 100000}",
                    "text": str(parsed["text"]).strip(),
                    "type": "FOLLOW-UP",
                    "bloom": "analyse",
                    "key_points": [str(k) for k in parsed.get("key_points", ["practical trade-off", "real-world edge case"])],
                    "ideal_seconds": 35,
                    "probe_reason": reason
                }
        except Exception as e:
            logger.warning("[granite] Adaptive probe LLM generation failed, falling back: %s", e)

    # Deterministic context-aware probe fallback
    ans_lower = ans_text.lower()
    probe_text = "You noted that key concept—what is a specific edge-case scenario where that mechanism would fail?"
    key_points = ["edge case limitation", "failure mode", "alternative approach"]

    if "index" in ans_lower or "b+" in ans_lower or "b tree" in ans_lower or "clustered" in ans_lower:
        probe_text = "You mentioned using an index there—what happens to write throughput and page splits when inserting 10,000 rows per second?"
        key_points = ["write latency penalty", "page splits", "index maintenance overhead"]
    elif "acid" in ans_lower or "transaction" in ans_lower or "isolation" in ans_lower:
        probe_text = "Under high concurrent load, what specific isolation anomaly could still occur with that setup?"
        key_points = ["phantom read", "dirty read", "locking overhead", "serializability"]
    elif "deadlock" in ans_lower or "semaphore" in ans_lower or "mutex" in ans_lower:
        probe_text = "How would you prevent circular wait dynamically in code without locking every resource upfront?"
        key_points = ["resource ordering", "lock timeout", "banker's algorithm", "try-lock"]
    elif "tcp" in ans_lower or "udp" in ans_lower or "handshake" in ans_lower:
        probe_text = "If packet loss suddenly increases to 20%, how does the congestion window scale down?"
        key_points = ["slow start", "additive decrease", "retransmission timeout", "throughput drop"]
    elif "bfs" in ans_lower or "dfs" in ans_lower or "tree" in ans_lower or "graph" in ans_lower:
        probe_text = "If the graph contains cycles or deep paths, how does memory consumption compare between BFS and DFS?"
        key_points = ["call stack overflow in DFS", "queue memory in wide BFS", "visited set"]
    elif reason == "script_check":
        probe_text = "Can you briefly walk me through a real project or situation where you had to debug that exact trade-off?"
        key_points = ["personal implementation experience", "trade-off explanation", "practical troubleshooting"]

    return {
        "id": f"probe_{int(time.time() * 1000) % 100000}",
        "text": probe_text,
        "type": "FOLLOW-UP",
        "bloom": "analyse",
        "key_points": key_points,
        "ideal_seconds": 35,
        "probe_reason": reason
    }


def build_hybrid_questions(
    fixed_questions: list[str | dict] | None,
    dynamic_questions: list[dict],
    candidate_seed: str = ""
) -> list[dict]:
    """Combine host-configured fixed baseline questions with seeded unique dynamic questions.
    
    Guarantees that fixed questions come first with type 'FIXED', followed by candidate-specific
    fresh dynamic questions with type 'DYNAMIC'.
    """
    queue: list[dict] = []

    # 1. Process fixed baseline questions
    if fixed_questions:
        for idx, item in enumerate(fixed_questions):
            if isinstance(item, dict):
                queue.append({
                    "id": item.get("id") or f"fix_{idx+1}",
                    "text": item.get("text", ""),
                    "bloom": item.get("bloom", "remember"),
                    "key_points": item.get("key_points", ["identification", "context"]),
                    "ideal_seconds": item.get("ideal_seconds", 30),
                    "type": "FIXED"
                })
            elif isinstance(item, str) and item.strip():
                queue.append({
                    "id": f"fix_{idx+1}",
                    "text": item.strip(),
                    "bloom": "remember",
                    "key_points": ["candidate identity", "background introduction"],
                    "ideal_seconds": 30,
                    "type": "FIXED"
                })

    # 2. Process dynamic questions (optionally seeded by candidate ID)
    dyn_copy = [dict(q) for q in dynamic_questions]
    if candidate_seed and len(dyn_copy) > 1:
        # Deterministic rotation / permutation based on candidate seed hash
        seed_hash = sum(ord(c) for c in str(candidate_seed))
        offset = seed_hash % len(dyn_copy)
        dyn_copy = dyn_copy[offset:] + dyn_copy[:offset]

    for idx, q in enumerate(dyn_copy):
        queue.append({
            "id": q.get("id") or f"dyn_{idx+1}",
            "text": q.get("text", ""),
            "bloom": q.get("bloom", "understand"),
            "key_points": q.get("key_points", []),
            "ideal_seconds": q.get("ideal_seconds", 40),
            "type": "DYNAMIC"
        })

    # Normalize sequential index
    for i, item in enumerate(queue):
        item["index"] = i

    return queue
