"""Oralyze Flask application: Live Room Architecture.

Flow:
- Teacher creates viva room -> 6-character code (e.g. A7K2QX) + question generation.
- Teacher opens live monitor -> polls /api/viva/<code>/live every 2s.
- Student joins on phone/tab with 6-character code -> receives questions + audio.
- Student speaks answer -> browser live speech preview + Watson STT word timings.
- Answer evaluated (Granite content + fluency analytics) -> teacher's live monitor updates in real time.
- Final scorecard archived to Cloudant / local JSON store.

API endpoints:
POST /api/viva                 {subject, topic, level, language, count}
GET  /api/viva/<code>          -> viva info + questions
POST /api/viva/<code>/answer   multipart: qid, audio, language, live_transcript
GET  /api/viva/<code>/live     -> live state for teacher polling
GET  /api/viva/<code>/scorecard-> completed scorecard
GET  /api/dashboard            -> past sessions list
GET  /api/health               -> system status
"""
import base64
import datetime
import io
import logging
import os
import random
import string
import uuid
from flask import Flask, jsonify, request, send_from_directory, send_file
from config import Config
from services.fluency import analyze_fluency
from services.granite import (
    generate_questions,
    grade_answer,
    preview_viva_from_prompt,
    detect_scripted_response,
    generate_adaptive_probe,
    build_hybrid_questions,
)
from services.speech import synthesize, transcribe
from services import voice
from services import store

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s"
)
logger = logging.getLogger("oralyze")

app = Flask(__name__, static_folder="static")

# Enable CORS for GitHub Pages and local development
# Allowed origins only; never wildcard '*' on routes that spend IBM tokens
ALLOWED_ORIGINS = {
    "https://mohitraj8503.github.io",
    "http://localhost:5000",
    "http://127.0.0.1:5000",
    "http://localhost:3000"
}


@app.after_request
def add_cors_headers(response):
    origin = request.headers.get("Origin")
    if origin and (origin in ALLOWED_ORIGINS or origin.endswith(".github.io")):
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
        response.headers["Access-Control-Allow-Credentials"] = "true"
    return response


@app.route("/", defaults={"path": ""}, methods=["OPTIONS"])
@app.route("/<path:path>", methods=["OPTIONS"])
def options_handler(path):
    response = jsonify({"status": "ok"})
    origin = request.headers.get("Origin")
    if origin and (origin in ALLOWED_ORIGINS or origin.endswith(".github.io")):
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    return response

# In-memory room store: code -> room dict
# code: 6 uppercase alphanumeric chars
VIVAS: dict[str, dict] = {}


def _generate_code() -> str:
    """Generate a clean 6-character alphanumeric code avoiding ambiguous chars."""
    chars = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    for _ in range(20):
        code = "".join(random.choices(chars, k=6))
        if code not in VIVAS:
            return code
    return uuid.uuid4().hex[:6].upper()


def _get_or_synthesize_audio(text: str, language: str) -> tuple[str, str]:
    """Synthesize question text to base64 audio (Sarvam -> Watson fallback)."""
    if Config.SARVAM_API_KEY and not Config.MOCK_MODE:
        try:
            _, b64, _ = voice.synthesize_speech(text, language_code=language)
            if b64:
                return b64, "audio/wav"
        except Exception as e:
            logger.warning("[viva] Voice synthesis fallback: %s", e)
    mp3_bytes = synthesize(text, language=language)
    b64 = base64.b64encode(mp3_bytes).decode("ascii")
    return b64, "audio/mp3"


@app.route("/")
def index():
    return send_from_directory("static", "index.html")


@app.route("/join")
def join_redirect():
    return send_from_directory("static", "index.html")


@app.route("/dashboard")
def dashboard_view():
    return send_from_directory("static", "index.html")


@app.route("/api/health", methods=["GET"])
def health_check():
    missing = Config.missing_keys()
    cloudant_active = store.cloudant_enabled()
    mode = "demo" if Config.MOCK_MODE else "live"
    provider = os.getenv("LLM_PROVIDER", "ibm")
    return jsonify({
        "ok": True,
        "mode": mode,
        "provider": provider,
        "mock_mode": Config.MOCK_MODE,
        "cloudant": cloudant_active,
        "missing_keys": missing
    })


@app.route("/api/viva/preview", methods=["POST"])
def preview_viva():
    """Generate question preview from teacher natural language prompt."""
    data = request.get_json(silent=True) or {}
    host_prompt = (data.get("host_prompt") or data.get("prompt") or "").strip()
    logger.info(f"[viva] host_prompt: {host_prompt[:120]}")
    mode = (data.get("mode") or "university_viva").strip()
    difficulty = (data.get("difficulty") or "medium").strip().lower()
    language = (data.get("language") or "English").strip()
    count = data.get("count", 5)

    result, status_code = preview_viva_from_prompt(
        prompt=host_prompt,
        difficulty=difficulty,
        language=language,
        count=count,
        mode=mode
    )
    return jsonify(result), status_code


@app.route("/api/viva", methods=["POST"])
def create_viva():
    """Teacher creates a viva room from prompt or custom questions list."""
    data = request.get_json(silent=True) or {}
    host_prompt = (data.get("host_prompt") or data.get("prompt") or "").strip()
    logger.info(f"[viva] host_prompt: {host_prompt[:120]}")
    mode = (data.get("mode") or "university_viva").strip()
    difficulty = (data.get("difficulty") or "medium").strip().lower()
    language = (data.get("language") or "English").strip()
    custom_questions = data.get("questions")
    count = data.get("count", 5)

    try:
        count = int(count)
    except (ValueError, TypeError):
        count = 5
    count = max(3, min(count, 7))

    # If questions list was already edited and provided by teacher
    if isinstance(custom_questions, list) and len(custom_questions) >= 1:
        subject = (data.get("subject") or "Viva Assessment").strip()
        topic = (data.get("topic") or "Oral Exam").strip()
        title = (data.get("title") or f"{subject} Assessment").strip()
        raw_questions = custom_questions
        source = data.get("source", "teacher")
    else:
        # Generate from prompt if no questions list provided
        if not host_prompt:
            host_prompt = (data.get("subject") or "Computer Systems") + " oral assessment questions"
        preview_res, status_code = preview_viva_from_prompt(
            prompt=host_prompt,
            difficulty=difficulty,
            language=language,
            count=count,
            mode=mode,
            candidate_seed=data.get("candidate_id", "")
        )
        if status_code != 200:
            return jsonify(preview_res), status_code

        title = preview_res.get("title", "Assessment Examination")
        subject = preview_res.get("subject", "Systems")
        topic = preview_res.get("topic", "Assessment")
        difficulty = preview_res.get("difficulty", difficulty)
        language = preview_res.get("language", language)
        raw_questions = preview_res.get("questions", [])
        source = preview_res.get("source", "granite")

    code = _generate_code()
    fixed_questions = data.get("fixed_questions")
    candidate_id = (data.get("candidate_id") or f"ORL-{code}").strip().upper()
    anti_cheating = data.get("anti_cheating") or {
        "detect_tab_switch": True,
        "strictness": "HIGH",
        "anti_script_cross_exam": True
    }

    # Build hybrid queue with fixed questions + candidate-seeded dynamic questions
    hybrid_queue = build_hybrid_questions(
        fixed_questions=fixed_questions,
        dynamic_questions=raw_questions,
        candidate_seed=candidate_id
    )

    questions = []
    audio_cache = {}

    for idx, q in enumerate(hybrid_queue):
        qid = q.get("id") or f"q_{idx}"
        q_item = {
            "id": qid,
            "index": idx,
            "text": q.get("text", ""),
            "bloom": q.get("bloom", q.get("level", "understand")),
            "level": q.get("bloom", q.get("level", "understand")),
            "key_points": q.get("key_points", []),
            "ideal_seconds": q.get("ideal_seconds", 40),
            "type": q.get("type", "DYNAMIC")
        }
        questions.append(q_item)

    # Pre-synthesize question 0 audio
    if questions:
        lang_code = "hi" if "hindi" in str(language).lower() else "en"
        b64, _ = _get_or_synthesize_audio(questions[0]["text"], lang_code)
        audio_cache[0] = b64

    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    room = {
        "code": code,
        "candidate_id": candidate_id,
        "title": title,
        "subject": subject,
        "topic": topic,
        "prompt": host_prompt,
        "host_prompt": host_prompt,
        "mode": mode,
        "difficulty": difficulty,
        "language": language,
        "source": source,
        "total": len(questions),
        "questions": questions,
        "audio_cache": audio_cache,
        "status": "waiting",
        "current_index": 0,
        "student_name": "Student",
        "answers": [],
        "integrity_log": [],
        "anti_cheating": anti_cheating,
        "probes_count": 0,
        "max_probes": 2,
        "created_at": now_iso
    }
    VIVAS[code] = room

    client_questions = [{
        "id": q["id"],
        "index": q["index"],
        "text": q["text"],
        "bloom": q["bloom"],
        "level": q["level"],
        "key_points": q["key_points"],
        "ideal_seconds": q["ideal_seconds"],
        "type": q.get("type", "DYNAMIC")
    } for q in questions]

    return jsonify({
        "code": code,
        "candidate_id": candidate_id,
        "title": title,
        "subject": subject,
        "topic": topic,
        "difficulty": difficulty,
        "language": language,
        "source": source,
        "questions": client_questions,
        "total": len(questions)
    })


@app.route("/api/viva/<code>", methods=["GET"])
def get_viva(code: str):
    """Student joins and fetches viva details by 6-char code."""
    code = code.strip().upper()
    room = VIVAS.get(code)
    if not room:
        return jsonify({"error": "Viva room not found. Check the code."}), 404

    # If student is joining, update status if waiting
    if room["status"] == "waiting":
        room["status"] = "answering"

    curr_idx = room["current_index"]
    q_curr = room["questions"][curr_idx] if curr_idx < len(room["questions"]) else None

    # Fetch/cache audio for current question
    audio_b64 = ""
    if q_curr:
        if curr_idx in room["audio_cache"]:
            audio_b64 = room["audio_cache"][curr_idx]
        else:
            audio_b64, _ = _get_or_synthesize_audio(q_curr["text"], room["language"])
            room["audio_cache"][curr_idx] = audio_b64

    return jsonify({
        "code": room["code"],
        "candidate_id": room.get("candidate_id", f"ORL-{room['code']}"),
        "subject": room["subject"],
        "topic": room["topic"],
        "level": room.get("level") or room.get("difficulty") or "medium",
        "difficulty": room.get("difficulty") or room.get("level") or "medium",
        "language": room["language"],
        "total": room["total"],
        "current_index": curr_idx,
        "question": {
            "id": q_curr["id"],
            "index": q_curr["index"],
            "text": q_curr["text"],
            "level": q_curr["level"],
            "bloom": q_curr.get("bloom", q_curr["level"]),
            "type": q_curr.get("type", "DYNAMIC")
        } if q_curr else None,
        "audio_b64": audio_b64,
        "created_at": room["created_at"]
    })


@app.route("/api/viva/<code>/answer", methods=["POST"])
def submit_viva_answer(code: str):
    """Student submits an answer. Evaluates STT + Granite + Fluency. Updates room live state."""
    code = code.strip().upper()
    room = VIVAS.get(code)
    if not room:
        return jsonify({"error": "Viva room not found"}), 404

    qid = request.form.get("qid", "").strip()
    language = request.form.get("language", "").strip().lower() or room["language"]
    live_transcript = request.form.get("live_transcript", "").strip()

    # Find corresponding question
    matched_q = None
    for q in room["questions"]:
        if q["id"] == qid or str(q["index"]) == qid:
            matched_q = q
            break

    if not matched_q:
        curr_idx = room["current_index"]
        if curr_idx < len(room["questions"]):
            matched_q = room["questions"][curr_idx]
        else:
            return jsonify({"error": "Question not found"}), 400

    q_idx = matched_q["index"]

    audio_file = request.files.get("audio")
    audio_bytes = b""
    content_type = "audio/webm"
    if audio_file:
        audio_bytes = audio_file.read()
        content_type = audio_file.content_type or "audio/webm"

    # 1. Primary STT: Sarvam Saaras -> Watson STT fallback -> browser live_transcript fallback
    stt_result = {"transcript": "", "words": []}
    if Config.SARVAM_API_KEY and not Config.MOCK_MODE and audio_bytes:
        try:
            stt_result = voice.transcribe_audio(
                audio_bytes=audio_bytes,
                language=language,
                filename=audio_file.filename if audio_file else "audio.webm"
            )
        except Exception as e:
            logger.warning("[viva] Voice STT fallback: %s", e)

    if not stt_result.get("transcript"):
        try:
            stt_result = transcribe(audio_bytes=audio_bytes, content_type=content_type, language=language)
        except Exception as e:
            logger.error("[viva] STT fallback error: %s", e)
            stt_result = {"transcript": "", "words": []}

    transcript = stt_result.get("transcript", "").strip()
    words = stt_result.get("words", [])

    # If STT transcript was empty but browser gave a live transcript, use browser transcript as text fallback
    if not transcript and live_transcript:
        transcript = live_transcript
        if not words:
            # Construct simulated word timestamps from live transcript
            t_words = live_transcript.split()
            cur_t = 0.5
            for w in t_words:
                words.append({
                    "word": w,
                    "start": round(cur_t, 2),
                    "end": round(cur_t + 0.3, 2),
                    "confidence": 0.90
                })
                cur_t += 0.38

    # 2. Content Evaluation (Granite / Keyword fallback)
    try:
        content_eval = grade_answer(question=matched_q, transcript=transcript, language=language)
    except Exception as e:
        logger.error("[viva] Grading error: %s", e)
        content_eval = {
            "score": 5.0,
            "verdict": "partially correct",
            "correct_points": [],
            "missing_points": [],
            "feedback": "Answer recorded.",
            "ideal_answer": ""
        }

    # 3. Delivery Fluency Analytics
    fluency_eval = analyze_fluency(words=words, transcript=transcript, language=language)

    # 4. Anti-Cheating & Telemetry Ingestion
    tab_switches = int(request.form.get("tab_switches", 0) or 0)
    blur_events = int(request.form.get("blur_events", 0) or 0)
    paste_events = int(request.form.get("paste_events", 0) or 0)
    try:
        latency_sec = float(request.form.get("response_latency_sec", 0.0) or 0.0)
    except (ValueError, TypeError):
        latency_sec = 0.0

    script_check = detect_scripted_response(
        transcript=transcript,
        latency_sec=latency_sec,
        word_count=len(words)
    )

    if "integrity_log" not in room:
        room["integrity_log"] = []

    now_iso_ts = datetime.datetime.now(datetime.timezone.utc).isoformat()
    if tab_switches > 0:
        room["integrity_log"].append({
            "timestamp": now_iso_ts,
            "question_id": matched_q["id"],
            "trigger": f"Tab switch detected ({tab_switches} events)",
            "severity": "HIGH" if tab_switches >= 3 else "MEDIUM",
            "type": "TAB_SWITCH"
        })
    if paste_events > 0:
        room["integrity_log"].append({
            "timestamp": now_iso_ts,
            "question_id": matched_q["id"],
            "trigger": f"Clipboard paste detected ({paste_events} times)",
            "severity": "HIGH",
            "type": "CLIPBOARD_PASTE"
        })
    if script_check["is_suspect"]:
        room["integrity_log"].append({
            "timestamp": now_iso_ts,
            "question_id": matched_q["id"],
            "trigger": f"AI Script/Recital Anomaly: {'; '.join(script_check['reasons'])}",
            "severity": script_check["risk_level"],
            "type": "SCRIPTED_RECITATION"
        })

    # 5. Core Competency Scores (0 to 100)
    content_score = float(content_eval.get("score", 0.0))
    content_pct = round(content_score * 10.0, 1)
    fluency_score = float(fluency_eval.get("fluency_score", 0.0))
    total = round((0.70 * content_pct) + (0.30 * fluency_score), 1)

    concept_depth = round(min(100.0, content_score * 10.0), 1)
    clarity = round(fluency_score, 1)
    reasoning = round(min(100.0, (len(content_eval.get("correct_points", [])) * 25.0) + (content_score * 5.0)), 1)
    confidence = round(max(30.0, min(100.0, fluency_score - (len(fluency_eval.get("pauses", [])) * 3.5))), 1)

    competencies = {
        "concept_depth": concept_depth,
        "clarity": clarity,
        "reasoning": reasoning,
        "confidence": confidence
    }

    # 6. Adaptive Follow-up Probe Trigger
    adaptive_probe_triggered = False
    probe_question = None
    if (
        matched_q.get("type") != "FOLLOW-UP"
        and room.get("probes_count", 0) < room.get("max_probes", 2)
        and (script_check["suggests_probe"] or content_score < 4.5 or content_score >= 8.5)
    ):
        reason = "script_check" if script_check["suggests_probe"] else ("deepen" if content_score >= 8.5 else "clarify")
        probe = generate_adaptive_probe(
            prev_question=matched_q,
            prev_answer=transcript,
            topic=room.get("topic", ""),
            reason=reason
        )
        insert_idx = q_idx + 1
        probe["index"] = insert_idx
        room["questions"].insert(insert_idx, probe)

        # Re-index all remaining questions
        for idx, q_obj in enumerate(room["questions"]):
            q_obj["index"] = idx
        room["total"] = len(room["questions"])
        room["probes_count"] = room.get("probes_count", 0) + 1
        adaptive_probe_triggered = True
        probe_question = probe

    next_index = q_idx + 1
    is_done = next_index >= room["total"]

    answer_record = {
        "qid": matched_q["id"],
        "index": q_idx,
        "type": matched_q.get("type", "DYNAMIC"),
        "question_text": matched_q["text"],
        "transcript": transcript,
        "content": content_eval,
        "delivery": fluency_eval,
        "competencies": competencies,
        "anti_cheating": {
            "script_check": script_check,
            "latency_sec": latency_sec,
            "tab_switches": tab_switches
        },
        "content_pct": content_pct,
        "total": total
    }

    # Update room answers
    replaced = False
    for i, a in enumerate(room["answers"]):
        if a["qid"] == matched_q["id"]:
            room["answers"][i] = answer_record
            replaced = True
            break
    if not replaced:
        room["answers"].append(answer_record)

    room["current_index"] = next_index
    if is_done:
        room["status"] = "done"
        _persist_viva_room(room)
    else:
        room["status"] = "answering"

    return jsonify({
        "qid": matched_q["id"],
        "index": q_idx,
        "type": matched_q.get("type", "DYNAMIC"),
        "transcript": transcript,
        "content": content_eval,
        "delivery": fluency_eval,
        "competencies": competencies,
        "total": total,
        "next_index": next_index,
        "done": is_done,
        "adaptive_probe_triggered": adaptive_probe_triggered,
        "probe_question": probe_question
    })


@app.route("/api/viva/<code>/live", methods=["GET"])
def get_viva_live(code: str):
    """Teacher's Live Monitor polls this every 2 seconds."""
    code = code.strip().upper()
    room = VIVAS.get(code)
    if not room:
        return jsonify({"error": "Viva room not found"}), 404

    # Extract clean list of answers for live feed
    clean_answers = []
    for a in room.get("answers", []):
        clean_answers.append({
            "qid": a["qid"],
            "index": a["index"],
            "question_text": a.get("question_text", ""),
            "content": a["content"],
            "delivery": a["delivery"],
            "total": a["total"]
        })

    return jsonify({
        "code": room["code"],
        "subject": room["subject"],
        "topic": room["topic"],
        "status": room["status"],  # "waiting", "answering", "done"
        "current_index": room["current_index"],
        "total": room["total"],
        "answers": clean_answers,
        "student": room.get("student_name", "Student")
    })


@app.route("/api/viva/<code>/scorecard", methods=["GET"])
def get_viva_scorecard(code: str):
    """Retrieve full Blueprint-compliant scorecard for completed viva."""
    code = code.strip().upper()
    room = VIVAS.get(code)

    if room and room.get("answers"):
        answers = room["answers"]
        # Fix F: Exclude no_answer answers from denominator
        scored = [a for a in answers if not a.get("no_answer") and a.get("verdict") != "no_answer"]
        n = max(len(scored), 1)

        avg_content = round(sum(a["content_pct"] for a in scored) / n, 1) if scored else 0.0
        avg_delivery = round(sum(a["delivery"]["fluency_score"] for a in scored) / n, 1) if scored else 0.0
        row_avg_total = round(sum(a["total"] for a in scored) / n, 1) if scored else 0.0

        # Assert arithmetic truth
        computed_formula = round((avg_content * 0.7) + (avg_delivery * 0.3), 1)
        if abs(row_avg_total - computed_formula) > 0.05:
            logger.warning(f"[scorecard] arithmetic mismatch: row_avg={row_avg_total}, formula={computed_formula}. Using row average.")
        avg_total = row_avg_total

        # Aggregate competency ratings (0 to 100)
        avg_concept = round(sum(a.get("competencies", {}).get("concept_depth", a["content_pct"]) for a in scored) / n, 1) if scored else 0.0
        avg_clarity = round(sum(a.get("competencies", {}).get("clarity", a["delivery"]["fluency_score"]) for a in scored) / n, 1) if scored else 0.0
        avg_reasoning = round(sum(a.get("competencies", {}).get("reasoning", a["content_pct"]) for a in scored) / n, 1) if scored else 0.0
        avg_confidence = round(sum(a.get("competencies", {}).get("confidence", a["delivery"]["fluency_score"]) for a in scored) / n, 1) if scored else 0.0

        competencies = {
            "concept_depth": avg_concept,
            "clarity": avg_clarity,
            "reasoning": avg_reasoning,
            "confidence": avg_confidence
        }

        # Anti-Cheating & Integrity Audit Status
        integrity_log = room.get("integrity_log", [])
        if any(item.get("severity") == "HIGH" for item in integrity_log):
            integrity_status = "FLAGGED"
        elif any(item.get("severity") == "MEDIUM" for item in integrity_log):
            integrity_status = "SUSPICIOUS"
        else:
            integrity_status = "CLEAN"

        # Final recommendation
        if integrity_status == "FLAGGED":
            recommendation = "REVIEW (Integrity Anomaly)"
            recommendation_reason = "Multiple proctoring flags detected (tab switching or artificial recital pattern)."
        elif avg_total >= 80:
            recommendation = "PASS — Strong"
            recommendation_reason = "Demonstrated deep conceptual mastery and clear, confident oral reasoning under cross-questioning."
        elif avg_total >= 60:
            recommendation = "PASS"
            recommendation_reason = "Satisfied syllabus baseline requirements with adequate verbal comprehension."
        else:
            recommendation = "NEEDS WORK"
            recommendation_reason = "Conceptual gaps noted in key rubric areas. Recommended for further revision."

        band = "Excellent" if avg_total >= 85 else ("Good" if avg_total >= 70 else ("Average" if avg_total >= 55 else "Needs work"))

        # Calculate session duration
        duration_str = "05 mins 12 secs"
        try:
            c_time = datetime.datetime.fromisoformat(room["created_at"])
            elapsed_sec = int((datetime.datetime.now(datetime.timezone.utc) - c_time).total_seconds())
            mins, secs = divmod(max(elapsed_sec, 30), 60)
            duration_str = f"{mins:02d} mins {secs:02d} secs"
        except Exception:
            pass

        return jsonify({
            "code": room["code"],
            "candidate_id": room.get("candidate_id", f"ORL-{room['code']}"),
            "subject": room["subject"],
            "topic": room.get("topic", ""),
            "level": room.get("level", "undergraduate"),
            "language": room.get("language", "en"),
            "duration": duration_str,
            "overall_score": avg_total,
            "average_total": avg_total,
            "average_content": avg_content,
            "average_delivery": avg_delivery,
            "band": band,
            "recommendation": recommendation,
            "recommendation_reason": recommendation_reason,
            "competencies": competencies,
            "integrity_status": integrity_status,
            "integrity_log": integrity_log,
            "answers": answers,
            "created_at": room["created_at"]
        })

    # Fallback to store
    doc = store.get(code)
    if doc:
        return jsonify(doc)

    return jsonify({"error": "Scorecard not found"}), 404


def _persist_viva_room(room: dict) -> None:
    """Save finalized viva room to Cloudant or local fallback."""
    answers = room.get("answers", [])
    if not answers:
        return

    n = len(answers)
    avg_content = round(sum(a["content_pct"] for a in answers) / n, 1)
    avg_delivery = round(sum(a["delivery"]["fluency_score"] for a in answers) / n, 1)
    avg_total = round(sum(a["total"] for a in answers) / n, 1)

    avg_concept = round(sum(a.get("competencies", {}).get("concept_depth", a["content_pct"]) for a in answers) / n, 1)
    avg_clarity = round(sum(a.get("competencies", {}).get("clarity", a["delivery"]["fluency_score"]) for a in answers) / n, 1)
    avg_reasoning = round(sum(a.get("competencies", {}).get("reasoning", a["content_pct"]) for a in answers) / n, 1)
    avg_confidence = round(sum(a.get("competencies", {}).get("confidence", a["delivery"]["fluency_score"]) for a in answers) / n, 1)

    competencies = {
        "concept_depth": avg_concept,
        "clarity": avg_clarity,
        "reasoning": avg_reasoning,
        "confidence": avg_confidence
    }

    integrity_log = room.get("integrity_log", [])
    if any(item.get("severity") == "HIGH" for item in integrity_log):
        integrity_status = "FLAGGED"
    elif any(item.get("severity") == "MEDIUM" for item in integrity_log):
        integrity_status = "SUSPICIOUS"
    else:
        integrity_status = "CLEAN"

    recommendation = "PASS — Strong" if avg_total >= 80 else ("PASS" if avg_total >= 60 else "NEEDS WORK")
    if integrity_status == "FLAGGED":
        recommendation = "REVIEW (Integrity Anomaly)"

    doc = {
        "_id": room["code"],
        "code": room["code"],
        "candidate_id": room.get("candidate_id", f"ORL-{room['code']}"),
        "session_id": room["code"],
        "subject": room["subject"],
        "topic": room.get("topic", ""),
        "level": room.get("level", "undergraduate"),
        "language": room.get("language", "en"),
        "average_content": avg_content,
        "average_delivery": avg_delivery,
        "average_total": avg_total,
        "overall_score": avg_total,
        "recommendation": recommendation,
        "competencies": competencies,
        "integrity_status": integrity_status,
        "integrity_log": integrity_log,
        "answers": answers,
        "questions_count": room["total"],
        "created_at": room["created_at"]
    }
    store.save(doc)


@app.route("/api/dashboard", methods=["GET"])
def get_dashboard():
    """Retrieve sessions for teacher dashboard."""
    limit = int(request.args.get("limit", 25))
    sessions = store.recent(limit=limit)

    clean_sessions = []
    for s in sessions:
        clean_sessions.append({
            "code": s.get("code") or s.get("_id") or s.get("session_id", ""),
            "subject": s.get("subject", "Viva"),
            "topic": s.get("topic", ""),
            "level": s.get("level", "undergraduate"),
            "date": s.get("created_at", ""),
            "avg_content": s.get("average_content", 0),
            "avg_delivery": s.get("average_delivery") or s.get("average_fluency", 0),
            "avg_total": s.get("average_total", 0),
            "count": s.get("questions_count") or len(s.get("answers", []))
        })

    return jsonify({"sessions": clean_sessions})


# ==========================================
# Oralyze Voice Layer Proxy Endpoints (Sarvam AI)
# ==========================================

@app.route("/api/voice/health", methods=["GET"])
def voice_health():
    """Health check endpoint strictly returning provider status without leaking secrets."""
    return jsonify(voice.get_health())


@app.route("/api/voice/usage", methods=["GET"])
def voice_usage():
    """Usage metrics endpoint returning call counts, cache hit rate, and estimated spend."""
    return jsonify(voice.get_usage())


@app.route("/api/voice/stt", methods=["POST"])
def voice_stt_proxy():
    """Proxy STT to Sarvam Saaras model with word timings."""
    ip = request.remote_addr or "127.0.0.1"
    if not voice.check_rate_limit(ip):
        return jsonify({"error": "rate_limit_exceeded"}), 429

    audio_file = request.files.get("audio") or request.files.get("file")
    if not audio_file:
        return jsonify({"transcript": "", "error": "stt_failed"}), 200

    audio_bytes = audio_file.read()
    if len(audio_bytes) > 10 * 1024 * 1024:
        return jsonify({"transcript": "", "error": "stt_failed"}), 200

    language = request.form.get("language", "auto")
    filename = audio_file.filename or "audio.webm"
    result = voice.transcribe_audio(audio_bytes=audio_bytes, language=language, filename=filename)
    return jsonify(result), 200


@app.route("/api/voice/tts", methods=["POST"])
def voice_tts_proxy():
    """Proxy TTS to Sarvam Bulbul model with sha1 caching."""
    ip = request.remote_addr or "127.0.0.1"
    if not voice.check_rate_limit(ip):
        return jsonify({"error": "rate_limit_exceeded"}), 429

    data = request.get_json(silent=True) or {}
    text = str(data.get("text", "")).strip()[:1000]
    if not text:
        return jsonify({"error": "empty_text", "audio_b64": ""}), 200

    language_code = data.get("language_code", "en-IN")
    speaker = data.get("speaker")
    pace = float(data.get("pace", 1.0) or 1.0)

    audio_bytes, audio_b64, cached = voice.synthesize_speech(
        text=text,
        language_code=language_code,
        speaker=speaker,
        pace=pace
    )

    if not audio_bytes:
        return jsonify({"error": "tts_failed", "audio_b64": ""}), 200

    # If client requests JSON or sets format=json
    if request.args.get("format") == "json" or request.headers.get("Accept") == "application/json":
        return jsonify({
            "audio_b64": audio_b64,
            "provider": "sarvam",
            "cached": cached
        }), 200

    return send_file(io.BytesIO(audio_bytes), mimetype="audio/wav")


@app.route("/api/voice/translate", methods=["POST"])
def voice_translate_proxy():
    """Proxy translation between Indian languages via Sarvam Mayura model."""
    ip = request.remote_addr or "127.0.0.1"
    if not voice.check_rate_limit(ip):
        return jsonify({"error": "rate_limit_exceeded"}), 429

    data = request.get_json(silent=True) or {}
    text = str(data.get("text", "")).strip()[:1000]
    source_lang = data.get("source_language_code", "en-IN")
    target_lang = data.get("target_language_code", "hi-IN")

    result = voice.translate_text(text, source_language_code=source_lang, target_language_code=target_lang)
    return jsonify(result), 200


@app.route("/api/voice/lid", methods=["POST"])
def voice_lid_proxy():
    """Proxy language identification via Sarvam Text-LID."""
    ip = request.remote_addr or "127.0.0.1"
    if not voice.check_rate_limit(ip):
        return jsonify({"error": "rate_limit_exceeded"}), 429

    data = request.get_json(silent=True) or {}
    text = str(data.get("text", "")).strip()[:1000]

    result = voice.identify_language(text)
    return jsonify(result), 200


# Boot announcement (Section 0)
_key_status = "present" if Config.SARVAM_API_KEY else "missing"
print(f"voice=sarvam key={_key_status}")


if __name__ == "__main__":
    logger.info("Starting Oralyze Live Viva on http://localhost:%s (Mock Mode: %s)", Config.PORT, Config.MOCK_MODE)
    app.run(host="0.0.0.0", port=Config.PORT, debug=False)
