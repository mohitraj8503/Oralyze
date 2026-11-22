# Oralize AI — Autonomous Voice Interview, Viva & Assessment Platform
**Comprehensive Product Blueprint, System Prompt & Technical Architecture**

---

## 1. Executive Vision & Core Concept

**Oralize AI** is an end-to-end **Autonomous Voice Interview, Academic Viva, and Skill Assessment Agent**. Originally conceptualized as an AI-driven oral viva tool for educators, Oralize expands into a full-scale **conversational screening, scheduling, live proctoring, and evaluation platform** built for:
- **Universities & Higher Education:** Semester vivas, lab assessments, project defenses, and seminar evaluations.
- **Corporate Recruitment & HR Tech:** Automated first-round screening interviews, technical walkthroughs, and soft-skill/communication checks.
- **EdTech & Bootcamps:** Certification vivas, mock interview practice, and cohort readiness evaluations.
- **Enterprise Onboarding & Compliance:** Policy comprehension checks and customer-facing role simulations.

### How It Works at a Glance
1. **Natural Language Setup:** The teacher, recruiter, or evaluator writes a single natural language prompt defining the topic/role, evaluation criteria, fixed questions (if any), and anti-cheating strictness.
2. **Voice-Based Scheduling & Code Issuance:** Candidates can schedule or reschedule slots conversationally via voice. Each candidate receives a unique **Access Code / Candidate ID**.
3. **Hybrid Question Engine (Fixed + Dynamic):** When a candidate logs in with their unique code, Oralize asks any host-mandated **fixed baseline questions** (e.g., *"What is your name, roll number, and background?"*) and then generates **100% fresh, non-repetitive dynamic questions** tailored to that specific candidate—ensuring no two candidates ever get the same question bank.
4. **Live Anti-Cheating & Proctoring:** Active voice cadence analysis, spontaneous cross-questioning, tab/focus monitoring, and multi-voice anomaly detection prevent reading from scripts or external AI tools.
5. **Instant Structured Evaluation:** Evaluators receive an auditable scorecard with transcript timestamps, audio playback, concept depth scores, communication metrics, and integrity risk flags.

---

## 2. Master System Prompt for the Oralize AI Agent

```markdown
# ROLE & IDENTITY
You are **Oralize AI**, an autonomous, fair, and sharp Voice Interview, Academic Viva, and Assessment Agent. You manage the entire lifecycle of spoken assessments: conversational scheduling, session code verification, hybrid (fixed + dynamic) question generation, adaptive follow-up probing, anti-cheating enforcement, and structured post-session evaluation.

# OPERATIONAL WORKFLOW

## PHASE 1: VOICE SCHEDULING & SESSION INITIALIZATION
1. **Conversational Scheduling (Pre-Session Mode):**
   - Assist candidates in booking, confirming, or rescheduling their interview/viva time slots via natural voice interaction.
   - Verify slot availability, timezone alignment, and prerequisite details before confirming a booking.
2. **Session Authentication (Live Assessment Mode):**
   - Greet the candidate professionally and verify their unique **Session Access Code** and **Candidate ID**.
   - Load the exact host configuration linked to that code (Topic/Role, Difficulty, Question Count, Fixed Questions, Language Mode, and Proctoring Rules).
   - Never expose internal configuration JSON, rubric weights, or upcoming questions to the candidate.

## PHASE 2: HYBRID & NON-REPETITIVE QUESTION ENGINE
1. **Fixed Baseline Questions (Host-Defined):**
   - If the host configured mandatory fixed questions (e.g., *"State your full name and enrollment ID"* or *"Briefly introduce your final year project"*), ask them verbatim at the designated stage.
2. **Dynamic & Unique Question Generation (Candidate-Specific):**
   - Generate completely fresh, non-repetitive questions for every single candidate session. Never reuse the exact same question sequence across different Candidate IDs or retakes.
   - Ensure questions test conceptual understanding, practical application, and reasoning rather than rote memorization.
3. **Adaptive Difficulty & Real-Time Follow-Ups:**
   - **Adaptive Scaling:** If a candidate answers accurately and confidently, escalate subsequent questions to deeper edge cases or system-level trade-offs. If a candidate struggles, step down to foundational concepts to accurately map their true baseline.
   - **Cross-Examination / Counter-Questioning:** Ask spontaneous follow-ups based directly on the candidate's previous sentence (e.g., *"You mentioned using indexing there—what happens to write latency when the table scales to 50 million rows?"*).
4. **Voice-First Delivery Rules:**
   - Ask **one clear, concise question at a time**.
   - Avoid formatting symbols, bullet points, or long monologues that sound unnatural when synthesized via Text-to-Speech (TTS).

## PHASE 3: ACTIVE ANTI-CHEATING & INTEGRITY GUARDRAILS
Monitor and enforce integrity silently throughout the call without unnecessarily disrupting genuine candidates:
1. **Anti-Script & AI-Read-Aloud Detection:**
   - Detect unnatural monotone reading cadence, sudden shifts from hesitant speech to overly polished textbook syntax, or delayed starts followed by uninterrupted recital.
   - Immediately trigger a spontaneous **"Why / How / What-If"** pivot question on the exact phrase they just spoke to verify genuine comprehension.
2. **Behavioral & Acoustic Anomaly Checks:**
   - Monitor turn-taking latency (flagging suspiciously consistent 5–8 second delays indicative of typing prompts into an external AI).
   - Flag secondary background voices (whispering/coaching), sudden microphone muting, or browser/tab-switch telemetry events passed by the client.
3. **Code & Attempt Enforcement:**
   - Enforce single-use or time-windowed Access Codes. If an expired or already-consumed code is used, deny entry politely and instruct the candidate to contact the host.
   - Record all integrity events with exact timestamps and severity levels (`LOW`, `MEDIUM`, `HIGH`) for the final report.

## PHASE 4: GRACEFUL CLOSING & STRUCTURED EVALUATION OUTPUT
1. **Session Closure:**
   - Once all required fixed and dynamic questions are completed, thank the candidate, confirm their responses have been recorded, and end the call cleanly.
2. **Post-Session Evaluator JSON/Markdown Report:**
   - Immediately output a structured evaluation report for the host containing:
     - Candidate Metadata (`Candidate ID`, `Session Code`, `Timestamp`, `Duration`)
     - Question-by-Question Breakdown (Question Type: `FIXED` vs `DYNAMIC` vs `FOLLOW-UP`, Candidate Summary, Accuracy Score)
     - Core Competency Ratings (`Concept Depth`, `Communication & Clarity`, `Problem-Solving / Reasoning`, `Confidence`)
     - Integrity & Anti-Cheating Audit Log (Timestamps, Trigger Type, Risk Score)
     - Final Hire / Pass Recommendation with concise justification.

# TONE & GUARDRAILS
- Maintain a calm, encouraging, yet rigorous interviewer persona.
- If audio is unclear or background noise interrupts, ask the candidate once to repeat clearly before logging an audio quality note.
- Support natural Indian English accents and bilingual code-mixing (Hinglish) whenever the host enables `FLEXIBLE_LANGUAGE` mode, grading on conceptual accuracy rather than accent bias.
```

---

## 3. Deep-Dive Feature Ideas & Product Upgrades

### A. Unique Code & Session Isolation Architecture
- **Cohort Master Prompt -> Unique Session Tokens:** A professor or HR manager creates one assessment campaign (e.g., `CS301-OS-VIVA` or `SDE1-FRONTEND-SCREEN`) and uploads a CSV of 200 students/applicants.
- **Per-Candidate Seeded Generation:** Oralize generates 200 unique 8-character alphanumeric codes (e.g., `ORL-894X-22`). Each code locks:
  - Candidate identity binding
  - Valid time window (e.g., valid only between 10:00 AM and 10:20 AM)
  - A unique cryptographic seed so the LLM generates a distinct set of questions for every candidate while testing the **exact same difficulty rubric and syllabus weightage**.

### B. Multi-Layered Anti-Cheating & Proctoring Suite
1. **Semantic Fingerprinting (Catching ChatGPT/Claude Copy-Readers):**
   - Compare the candidate's spontaneous conversational tone during warm-up ("What's your name and college?") against their technical answers. A sudden jump in lexical density, bullet-point phrasing, and flat prosody triggers an automatic **"Interruption & Drill-Down"** probe.
2. **Client-Side Browser Telemetry Hooks:**
   - Require full-screen lock during web interviews.
   - Log `visibilitychange` (tab switching), `blur` (window losing focus), clipboard paste attempts, and secondary monitor detection directly into the AI agent's real-time context stream so the AI can subtly say: *"Please keep your focus on the interview window as we move to the next question."*
3. **Acoustic & Voiceprint Consistency:**
   - Verify that the speaker's vocal pitch/timbre remains consistent from Question 1 to Question 7 (preventing proxy impersonation mid-call).
   - Detect background speech/whispering using Voice Activity Detection (VAD) energy thresholds.
4. **Device-Free / Kiosk Mode for Campuses:**
   - For universities where personal phones aren't allowed, support a **Lab Kiosk Mode** using webcam QR/Face check-in + headset stations with isolated acoustic booths or push-to-talk hardware keys.

### C. Smart Assessment Modes
- **1. University Viva Mode:** Syllabus-aligned, supports fixed roll-number verification, external examiner audit logs, and automatic mark-sheet export (CSV/Excel/PDF).
- **2. Recruitment Screening Mode:** Resume-aware! The candidate uploads their PDF resume; Oralize cross-examines projects listed on their resume (*"You built a real-time chat app using WebSockets—how did you handle reconnection state?"*).
- **3. Conversational Voice Scheduler:** Candidates call a number or click a web link, talk to the scheduling sub-agent (*"Can we do Tuesday at 4 PM instead?"*), and receive an automated WhatsApp/Email calendar invite with their unique Oralize Access Code.
- **4. Bilingual / Hinglish Mode (India-First Advantage):** Many bright engineering and management students explain technical architecture best when allowed to mix Hindi and English naturally. Oralize evaluates technical logic accurately without penalizing regional accents.

---

## 4. Recommended Technical Architecture & Stack

| Layer | Recommended Technologies | Purpose |
| :--- | :--- | :--- |
| **Real-Time Voice & WebRTC** | LiveKit / Daily.co / WebRTC | Ultra-low latency (<500ms) bidirectional audio streaming & turn detection |
| **Speech-to-Text (STT)** | Deepgram Nova-3 / Whisper Large-v3 / Sarvam AI (Saarika) | High accuracy across Indian accents, technical jargon, and Hinglish |
| **Core Reasoning LLM** | GPT-4o / Gemini 2.5 Flash / Claude 3.7 Sonnet | Dynamic question generation, adaptive follow-ups, and rubric grading |
| **Text-to-Speech (TTS)** | ElevenLabs / Cartesia Sonic / Sarvam AI (Bulbul) | Natural, expressive human-like interviewer voice with low latency |
| **Backend & Orchestration** | Python (FastAPI) / Node.js + Redis | Session state management, unique code validation, and telemetry ingestion |
| **Database & Storage** | PostgreSQL + Supabase / AWS S3 | Storing rubrics, candidate scores, encrypted audio recordings, and audit logs |
| **Frontend Dashboard** | Next.js + TypeScript + Tailwind CSS | Host prompt builder, candidate code generator, audio playback & analytics |

---

## 5. Sample Host Configuration Schema (JSON)

```json
{
  "campaign_id": "CAMP_2026_DSA_VIVA",
  "mode": "UNIVERSITY_VIVA",
  "host_prompt": "Conduct a 7-minute viva on Data Structures focusing on Trees, Graphs, and Time Complexity. Keep it practical.",
  "language_policy": "ENGLISH_WITH_HINGLISH_ALLOWED",
  "scheduling": {
    "voice_scheduling_enabled": true,
    "slot_duration_minutes": 10,
    "buffer_minutes": 2
  },
  "questions_config": {
    "fixed_questions": [
      "Please state your full name, university roll number, and branch.",
      "Which topic from Data Structures did you find most interesting this semester and why?"
    ],
    "dynamic_question_count": 5,
    "allow_adaptive_followups": true,
    "strict_non_repetition": true
  },
  "anti_cheating": {
    "strictness": "HIGH",
    "enforce_fullscreen": true,
    "detect_tab_switch": true,
    "anti_script_cross_exam": true,
    "max_response_delay_seconds": 8
  },
  "grading_rubric": {
    "conceptual_accuracy_weight": 50,
    "problem_solving_depth_weight": 30,
    "communication_clarity_weight": 20
  }
}
```

---

## 6. Post-Interview Evaluator Report Template

```markdown
### Oralize Assessment Report — Candidate: `AJU-250609` | Code: `ORL-774K-91`
- **Assessment:** B.Tech Semester Viva — Database Management Systems (DBMS)
- **Duration:** 06 mins 42 secs | **Overall Score:** 84 / 100 (**PASS — Strong**)
- **Integrity Status:** CLEAN (0 Tab Switches | Natural Cadence | Consistent Voiceprint)

#### 1. Fixed Questions Summary
- **Q1 (Identity):** Verified name and enrollment ID cleanly.
- **Q2 (Warm-up):** Chose Indexing & Query Optimization as favorite topic.

#### 2. Dynamic & Adaptive Questions Breakdown
- **Q3 [Dynamic]:** *Explain the difference between Clustered and Non-Clustered indexes in real-world read/write workloads.*
  - **Score:** 9/10 — Accurately explained physical row ordering vs. separate pointer lookups.
- **Q4 [Adaptive Follow-Up]:** *If a table receives 10,000 inserts per second, how do multiple non-clustered indexes impact throughput?*
  - **Score:** 8.5/10 — Correctly identified index maintenance overhead and B-Tree page splits.
- **Q5 [Dynamic]:** *When would you intentionally denormalize a relational schema?*
  - **Score:** 8/10 — Gave a solid analytics/reporting read-heavy example.

#### 3. Evaluator Notes
- Candidate demonstrated genuine conceptual clarity under cross-questioning. Recommended for full viva credit.
```

---

## 7. Go-To-Market & Rollout Roadmap

1. **MVP (Weeks 1–3):** Build the Web Voice Room + Unique Access Code generator + Fixed/Dynamic Prompt Engine + Automated Transcript & Score Report.
2. **Proctoring & Resume Upgrade (Weeks 4–6):** Add tab-switch detection, anti-script follow-up triggers, audio playback timeline, and PDF Resume parsing for HR screening.
3. **Voice Scheduler & LMS/ATS Integrations (Weeks 7–10):** Launch conversational voice booking, WhatsApp/Email code dispatch, and one-click export to Google Sheets, Moodle, and Greenhouse/Lever.
