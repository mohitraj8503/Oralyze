/**
 * Oralyze Unified Data Layer (js/api.js)
 * Exposes the exact same async functions in both MODE 1 (Hosted Offline) and MODE 2 (Live API Backend).
 * Callers never need to know which mode is running.
 */
(function() {
  const SESSIONS_STORAGE_KEY = 'oralyze.sessions';
  const VIVAS_STORAGE_KEY = 'oralyze.vivas';

  function getApiBase() {
    const config = window.ORALYZE_CONFIG || {};
    return (config.API_BASE || '').trim().replace(/\/+$/, '');
  }

  function isHostedMode() {
    return !getApiBase();
  }

  function getStoredVivas() {
    try {
      return JSON.parse(localStorage.getItem(VIVAS_STORAGE_KEY) || '{}');
    } catch (e) {
      return {};
    }
  }

  function saveStoredViva(code, viva) {
    try {
      const all = getStoredVivas();
      all[code] = viva;
      localStorage.setItem(VIVAS_STORAGE_KEY, JSON.stringify(all));
    } catch (e) {
      console.warn('[OralyzeApi] Failed to save viva to localStorage', e);
    }
  }

  function getStoredSessions() {
    try {
      return JSON.parse(localStorage.getItem(SESSIONS_STORAGE_KEY) || '[]');
    } catch (e) {
      return [];
    }
  }

  function saveStoredSession(session) {
    try {
      const all = getStoredSessions();
      // Replace if existing or prepend
      const existingIdx = all.findIndex(s => s.session_id === session.session_id || s.code === session.code);
      if (existingIdx >= 0) {
        all[existingIdx] = session;
      } else {
        all.unshift(session);
      }
      localStorage.setItem(SESSIONS_STORAGE_KEY, JSON.stringify(all));
    } catch (e) {
      console.warn('[OralyzeApi] Failed to save session to localStorage', e);
    }
  }

  let bundledQuestionsCache = null;
  async function fetchBundledQuestions() {
    if (bundledQuestionsCache) return bundledQuestionsCache;
    try {
      // Try both ./static/data/questions.json and ./data/questions.json
      let res = await fetch('./data/questions.json');
      if (!res.ok) res = await fetch('./static/data/questions.json');
      if (res.ok) {
        const raw = await res.json();
        let flattened = [];
        if (Array.isArray(raw)) {
          flattened = raw;
        } else if (typeof raw === 'object' && raw !== null) {
          // If JSON is organized by categories { "dbms": [...], "os": [...] }
          Object.keys(raw).forEach(cat => {
            if (Array.isArray(raw[cat])) {
              raw[cat].forEach(item => {
                if (!item.category) item.category = cat;
                if (!item.subject) item.subject = cat.toUpperCase();
              });
              flattened.push(...raw[cat]);
            }
          });
        }
        if (flattened.length > 0) {
          bundledQuestionsCache = flattened;
          return bundledQuestionsCache;
        }
      }
    } catch (e) {
      console.warn('[OralyzeApi] Failed to fetch questions.json', e);
    }

    // Default fallback question list
    bundledQuestionsCache = [
      {
        id: "q1",
        subject: "Database Systems",
        topic: "Indexing and B+ Trees",
        text: "Explain what a B+ tree index is and why it is preferred over a binary search tree in relational databases.",
        bloom: "understand",
        key_points: ["disk block access optimization", "all data in leaf nodes", "leaf nodes linked sequentially", "balanced height"],
        ideal_seconds: 40
      },
      {
        id: "q2",
        subject: "Database Systems",
        topic: "Transactions",
        text: "What are ACID properties in database management systems and why are they critical?",
        bloom: "understand",
        key_points: ["Atomicity all or nothing", "Consistency valid state", "Isolation concurrent transactions", "Durability committed data saved"],
        ideal_seconds: 40
      },
      {
        id: "q3",
        subject: "Database Systems",
        topic: "Normalization",
        text: "Differentiate between Third Normal Form (3NF) and Boyce-Codd Normal Form (BCNF).",
        bloom: "analyze",
        key_points: ["no transitive dependency in 3NF", "determinant must be superkey in BCNF", "functional dependency preservation"],
        ideal_seconds: 45
      },
      {
        id: "q4",
        subject: "Operating Systems",
        topic: "Deadlocks",
        text: "What are the four necessary conditions for a deadlock to occur in an operating system?",
        bloom: "remember",
        key_points: ["Mutual exclusion", "Hold and wait", "No preemption", "Circular wait"],
        ideal_seconds: 35
      },
      {
        id: "q5",
        subject: "Computer Networks",
        topic: "Transport Layer",
        text: "Compare TCP and UDP in terms of reliability, overhead, and typical real-world use cases.",
        bloom: "analyze",
        key_points: ["TCP is connection oriented and reliable", "UDP is connectionless with lower latency", "3-way handshake in TCP", "UDP for streaming and gaming"],
        ideal_seconds: 40
      },
      {
        id: "q6",
        subject: "Data Structures",
        topic: "Trees and Graphs",
        text: "Compare Depth-First Search (DFS) and Breadth-First Search (BFS) in terms of data structures used and time complexity.",
        bloom: "analyze",
        key_points: ["DFS uses stack or recursion", "BFS uses queue", "O(V + E) time complexity", "BFS finds shortest path in unweighted graphs"],
        ideal_seconds: 40
      }
    ];
    return bundledQuestionsCache;
  }

  function generateRandomCode() {
    const chars = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789';
    let code = '';
    for (let i = 0; i < 6; i++) {
      code += chars.charAt(Math.floor(Math.random() * chars.length));
    }
    return code;
  }

  const TOPIC_KEYWORDS = {
    'dbms': ['dbms', 'database', 'sql', 'acid', 'indexing', 'b+ tree', 'b tree', 'normalization', 'relational', 'transaction', 'superkey', '3nf', 'bcnf'],
    'os': ['os', 'operating system', 'process', 'thread', 'scheduling', 'deadlock', 'semaphore', 'virtual memory', 'paging', 'kernel', 'context switch'],
    'computer networks': ['network', 'networking', 'osi', 'tcp', 'udp', 'ip', 'dns', 'http', 'socket', 'subnet', 'router', 'switch', 'packet'],
    'data structures': ['dsa', 'data structure', 'algorithm', 'array', 'linked list', 'stack', 'queue', 'tree', 'graph', 'sorting', 'binary search', 'dfs', 'bfs'],
    'cloud computing': ['cloud', 'aws', 'azure', 'gcp', 'paas', 'iaas', 'saas', 'kubernetes', 'k8s', 'docker', 'container', 'serverless', 'microservice']
  };

  /**
   * Preview viva questions from prompt
   */
  async function previewViva(prompt, difficulty = 'medium', language = 'English', count = 5) {
    if (isHostedMode()) {
      const bank = await fetchBundledQuestions();
      const pLow = (prompt || '').toLowerCase();
      
      // 1. Identify primary subject category from prompt
      let bestCat = null;
      let maxCatHits = 0;
      Object.keys(TOPIC_KEYWORDS).forEach(cat => {
        let hits = 0;
        TOPIC_KEYWORDS[cat].forEach(kw => {
          if (pLow.includes(kw)) hits += kw.length; // weight longer phrase matches higher
        });
        if (hits > maxCatHits) {
          maxCatHits = hits;
          bestCat = cat;
        }
      });

      // 2. Score questions based on category and textual match
      const scored = bank.map(q => {
        let score = 0;
        const qCat = (q.category || '').toLowerCase();
        const s = (q.subject || '').toLowerCase();
        const t = (q.topic || '').toLowerCase();
        const txt = (q.text || '').toLowerCase();

        if (bestCat && (qCat === bestCat || s.includes(bestCat) || t.includes(bestCat))) {
          score += 50;
        }

        // Token match
        const words = pLow.replace(/[^a-z0-9 ]/g, ' ').split(/\s+/).filter(w => w.length > 2);
        words.forEach(w => {
          if (s.includes(w)) score += 10;
          if (t.includes(w)) score += 8;
          if (txt.includes(w)) score += 4;
        });

        return { q, score };
      });

      scored.sort((a, b) => b.score - a.score);
      let matched = scored.filter(item => item.score > 0).map(item => item.q);

      if (matched.length < count) {
        // Fill remaining from bank without duplicates
        const existingIds = new Set(matched.map(m => m.id || m.text));
        for (const q of bank) {
          const key = q.id || q.text;
          if (!existingIds.has(key)) {
            matched.push(q);
            existingIds.add(key);
            if (matched.length >= count) break;
          }
        }
      }
      matched = matched.slice(0, count);

      // Determine clean human subject title
      let subjectTitle = 'Computer Science';
      if (bestCat === 'os') subjectTitle = 'Operating Systems';
      else if (bestCat === 'dbms') subjectTitle = 'Database Systems';
      else if (bestCat === 'computer networks') subjectTitle = 'Computer Networks';
      else if (bestCat === 'data structures') subjectTitle = 'Data Structures & Algorithms';
      else if (bestCat === 'cloud computing') subjectTitle = 'Cloud Computing';
      else if (prompt.trim()) {
        const words = prompt.trim().split(/\s+/).slice(0, 3).join(' ');
        subjectTitle = words.charAt(0).toUpperCase() + words.slice(1);
      }

      return {
        title: `${subjectTitle} Viva Voce`,
        subject: subjectTitle,
        topic: prompt.slice(0, 40).trim() || 'Technical Oral Examination',
        difficulty,
        language,
        questions: matched.map((q, idx) => ({
          id: `q${idx + 1}`,
          index: idx,
          text: q.text,
          bloom: q.bloom || q.level || 'understand',
          key_points: q.key_points || [],
          ideal_seconds: q.ideal_seconds || 40
        })),
        source: 'browser_offline'
      };
    }

    const res = await fetch(`${getApiBase()}/api/viva/preview`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ prompt, difficulty, language, count })
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.message || 'Failed to preview viva questions');
    }
    return await res.json();
  }

  /**
   * Create a viva room
   */
  async function createViva(payload) {
    if (isHostedMode()) {
      let questions = payload.questions;
      if (!questions || !questions.length) {
        const preview = await previewViva(payload.prompt || 'Computer Science viva', payload.difficulty, payload.language, payload.count || 5);
        questions = preview.questions;
      }

      const code = generateRandomCode();
      const candidateId = (payload.candidate_id || `ORL-${code}`).trim().toUpperCase();

      // Fixed baseline questions handling
      let allQuestions = [];
      if (Array.isArray(payload.fixed_questions) && payload.fixed_questions.length > 0) {
        payload.fixed_questions.forEach((fq, idx) => {
          allQuestions.push({
            id: fq.id || `fix_${idx + 1}`,
            index: allQuestions.length,
            text: typeof fq === 'string' ? fq : fq.text,
            bloom: fq.bloom || 'remember',
            level: fq.bloom || 'remember',
            key_points: fq.key_points || ['identification', 'context'],
            ideal_seconds: fq.ideal_seconds || 30,
            type: 'FIXED'
          });
        });
      }

      // Dynamic questions
      questions.forEach((q, idx) => {
        allQuestions.push({
          id: q.id || `dyn_${idx + 1}`,
          index: allQuestions.length,
          text: q.text,
          bloom: q.bloom || q.level || 'understand',
          level: q.bloom || q.level || 'understand',
          key_points: q.key_points || [],
          ideal_seconds: q.ideal_seconds || 40,
          type: 'DYNAMIC'
        });
      });

      const viva = {
        code,
        candidate_id: candidateId,
        title: payload.title || `${payload.subject || 'Technical'} Viva`,
        subject: payload.subject || 'Engineering',
        topic: payload.topic || 'Oral Examination',
        difficulty: payload.difficulty || 'medium',
        language: payload.language || 'English',
        total_questions: allQuestions.length,
        questions: allQuestions,
        status: 'waiting',
        answers: [],
        integrity_log: [],
        anti_cheating: payload.anti_cheating || {
          detect_tab_switch: true,
          strictness: "HIGH",
          anti_script_cross_exam: true
        },
        probes_count: 0,
        max_probes: 2,
        created_at: new Date().toISOString()
      };

      saveStoredViva(code, viva);

      return {
        code,
        candidate_id: candidateId,
        title: viva.title,
        subject: viva.subject,
        topic: viva.topic,
        total_questions: viva.total_questions,
        questions: viva.questions,
        language: viva.language,
        difficulty: viva.difficulty,
        status: 'waiting'
      };
    }

    const res = await fetch(`${getApiBase()}/api/viva`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.error || err.message || 'Failed to create viva');
    }
    return await res.json();
  }

  /**
   * Get viva room info by code
   */
  async function getViva(code) {
    const cleanCode = (code || '').toUpperCase().trim();
    if (isHostedMode()) {
      const vivas = getStoredVivas();
      const viva = vivas[cleanCode];
      if (!viva) {
        if (cleanCode === 'A7K2QX' || cleanCode === 'DEMO01' || cleanCode === 'MATH01' || cleanCode.length === 6) {
          const defaultViva = {
            code: cleanCode,
            candidate_id: `ORL-${cleanCode}`,
            title: cleanCode === 'MATH01' ? "Elementary Arithmetic Assessment" : "Database Systems Viva Voce",
            subject: cleanCode === 'MATH01' ? "Mathematics" : "Database Systems",
            topic: cleanCode === 'MATH01' ? "Basic Arithmetic" : "Indexing & Transactions",
            difficulty: "intermediate",
            total_questions: cleanCode === 'MATH01' ? 5 : 3,
            questions: cleanCode === 'MATH01' ? [
              { id: "m1", text: "What is 2 + 2?", expected: "4", expectedWords: ["4", "four"], bloom: "remember", ideal_seconds: 15 },
              { id: "m2", text: "What is 3 + 2?", expected: "5", expectedWords: ["5", "five"], bloom: "remember", ideal_seconds: 15 },
              { id: "m3", text: "What is 7 − 4?", expected: "3", expectedWords: ["3", "three"], bloom: "remember", ideal_seconds: 15 },
              { id: "m4", text: "What is 6 × 2?", expected: "12", expectedWords: ["12", "twelve"], bloom: "apply", ideal_seconds: 15 },
              { id: "m5", text: "What is 10 ÷ 2?", expected: "5", expectedWords: ["5", "five"], bloom: "apply", ideal_seconds: 15 }
            ] : [
              { id: "q1", index: 0, text: "Explain what a B+ tree index is and why it is preferred over a binary search tree in relational databases.", bloom: "understand", level: "intermediate", key_points: ["b-tree structure", "disk block io", "balanced height", "range queries"], ideal_seconds: 40 },
              { id: "q2", index: 1, text: "Describe the ACID properties in database management systems and why they are essential for reliable transactions.", bloom: "analyze", level: "intermediate", key_points: ["atomicity", "consistency", "isolation", "durability"], ideal_seconds: 45 },
              { id: "q3", index: 2, text: "What is the difference between optimistic and pessimistic concurrency control?", bloom: "evaluate", level: "hard", key_points: ["lock contention", "validation phase", "rollback overhead"], ideal_seconds: 40 }
            ],
            answers: [],
            status: "in_progress",
            source: "bundled"
          };
          saveStoredViva(cleanCode, defaultViva);
          return defaultViva;
        }
        throw new Error('Viva not found. Please verify the code.');
      }
      return viva;
    }

    const res = await fetch(`${getApiBase()}/api/viva/${cleanCode}`);
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.error || 'Viva not found');
    }
    return await res.json();
  }

  /**
   * Submit an answer for grading
   */
  async function submitAnswer(code, audioBlob, liveTranscript, questionIndex = 0, wordTimestamps = null, durationSeconds = 30, telemetry = {}) {
    const cleanCode = (code || '').toUpperCase().trim();
    const tabSwitches = Number(telemetry.tab_switches || 0);
    const blurEvents = Number(telemetry.blur_events || 0);
    const pasteEvents = Number(telemetry.paste_events || 0);
    const latencySec = Number(telemetry.response_latency_sec || 0);

    if (isHostedMode()) {
      const viva = await getViva(cleanCode);
      const question = viva.questions[questionIndex] || viva.questions[0];

      // Grade content in browser
      const contentResult = window.OralyzeGrader.gradeAnswer(question, liveTranscript, viva.language);
      
      // Compute delivery score
      const deliveryResult = window.OralyzeGrader.calculateDeliveryScore(wordTimestamps || liveTranscript.split(' '), durationSeconds);

      // Anti-Cheating & Script detection
      const words = (liveTranscript || '').trim().split(/\s+/).filter(Boolean);
      const scriptCheck = window.OralyzeGrader.detectScriptedResponse ?
        window.OralyzeGrader.detectScriptedResponse(liveTranscript, latencySec, words.length) :
        { is_suspect: false, riskLevel: "CLEAN", reasons: [], suggests_probe: false };

      if (!viva.integrity_log) viva.integrity_log = [];
      const nowIso = new Date().toISOString();

      if (tabSwitches > 0) {
        viva.integrity_log.push({
          timestamp: nowIso,
          question_id: question.id,
          trigger: `Tab switch detected (${tabSwitches} events)`,
          severity: tabSwitches >= 3 ? "HIGH" : "MEDIUM",
          type: "TAB_SWITCH"
        });
      }
      if (pasteEvents > 0) {
        viva.integrity_log.push({
          timestamp: nowIso,
          question_id: question.id,
          trigger: `Clipboard paste detected (${pasteEvents} times)`,
          severity: "HIGH",
          type: "CLIPBOARD_PASTE"
        });
      }
      if (scriptCheck.is_suspect) {
        viva.integrity_log.push({
          timestamp: nowIso,
          question_id: question.id,
          trigger: `AI Script / Recital Anomaly: ${scriptCheck.reasons.join('; ')}`,
          severity: scriptCheck.riskLevel || scriptCheck.severity || "MEDIUM",
          type: "SCRIPTED_RECITATION"
        });
      }

      // 4 Core Competencies
      const competencies = window.OralyzeGrader.calculateCompetencies ?
        window.OralyzeGrader.calculateCompetencies(contentResult.score, deliveryResult.delivery_score, (contentResult.correct_points || []).length, deliveryResult.pause_count) :
        { concept_depth: contentResult.score * 10, clarity: deliveryResult.delivery_score, reasoning: contentResult.score * 10, confidence: deliveryResult.delivery_score };

      const overall = Math.round(((contentResult.score * 10) * 0.7) + (deliveryResult.delivery_score * 0.3));

      // Adaptive Follow-up Probe Trigger
      let adaptiveProbeTriggered = false;
      let probeQuestion = null;
      if (
        question.type !== 'FOLLOW-UP' &&
        (viva.probes_count || 0) < (viva.max_probes || 2) &&
        (scriptCheck.suggests_probe || contentResult.score < 4.5 || contentResult.score >= 8.5) &&
        window.OralyzeGrader.generateAdaptiveProbe
      ) {
        const probeReason = scriptCheck.suggests_probe ? 'script_check' : (contentResult.score >= 8.5 ? 'deepen' : 'clarify');
        const probe = window.OralyzeGrader.generateAdaptiveProbe(question, liveTranscript, viva.topic, probeReason);
        const insertIdx = questionIndex + 1;
        probe.index = insertIdx;
        viva.questions.splice(insertIdx, 0, probe);

        // Re-index questions
        viva.questions.forEach((q, idx) => { q.index = idx; });
        viva.total_questions = viva.questions.length;
        viva.probes_count = (viva.probes_count || 0) + 1;
        adaptiveProbeTriggered = true;
        probeQuestion = probe;
      }

      const isNoAns = Boolean(contentResult.no_answer || contentResult.verdict === 'no_answer');
      const evaluation = {
        question_id: question.id,
        index: questionIndex,
        type: question.type || 'DYNAMIC',
        question_text: question.text,
        student_transcript: liveTranscript,
        content_score: isNoAns ? 0.0 : contentResult.score,
        delivery_score: isNoAns ? 0 : deliveryResult.delivery_score,
        overall_score: isNoAns ? 0 : overall,
        verdict: isNoAns ? 'no_answer' : contentResult.verdict,
        no_answer: isNoAns,
        source: contentResult.source || 'keyword',
        feedback: contentResult.feedback,
        correct_points: contentResult.correct_points || [],
        missing_points: contentResult.missing_points || [],
        ideal_answer: contentResult.ideal_answer,
        competencies: competencies,
        anti_cheating: {
          script_check: scriptCheck,
          latency_sec: latencySec,
          tab_switches: tabSwitches
        },
        fluency_metrics: deliveryResult,
        wpm: deliveryResult.wpm,
        filler_ratio: deliveryResult.filler_ratio,
        pause_count: deliveryResult.pause_count
      };

      // Update viva status in localStorage
      viva.answers = viva.answers || [];
      viva.answers.push(evaluation);
      viva.current_question = questionIndex + 1;
      viva.last_score = {
        content: evaluation.content_score,
        delivery: evaluation.delivery_score,
        overall: evaluation.overall_score,
        verdict: evaluation.verdict,
        no_answer: isNoAns,
        student_transcript: liveTranscript
      };

      if (viva.current_question >= viva.total_questions) {
        viva.status = 'completed';
      } else {
        viva.status = 'in_progress';
      }
      saveStoredViva(cleanCode, viva);

      // If completed, save full session into oralyze.sessions
      if (viva.status === 'completed') {
        const scoredAnswers = viva.answers.filter(a => !a.no_answer && a.verdict !== 'no_answer');
        const denom = Math.max(scoredAnswers.length, 1);
        const avgContent = scoredAnswers.length ? Math.round((scoredAnswers.reduce((acc, a) => acc + (a.content_score || 0), 0) / denom) * 10) / 10 : 0.0;
        const avgDelivery = scoredAnswers.length ? Math.round(scoredAnswers.reduce((acc, a) => acc + (a.delivery_score || 0), 0) / denom) : 0;
        const rowAvgTotal = scoredAnswers.length ? Math.round(scoredAnswers.reduce((acc, a) => acc + (a.overall_score || 0), 0) / denom) : 0;
        const overallFinal = rowAvgTotal;

        const avgConcept = scoredAnswers.length ? Math.round(scoredAnswers.reduce((acc, a) => acc + (a.competencies?.concept_depth || a.content_score * 10), 0) / denom) : 0;
        const avgClarity = scoredAnswers.length ? Math.round(scoredAnswers.reduce((acc, a) => acc + (a.competencies?.clarity || a.delivery_score), 0) / denom) : 0;
        const avgReasoning = scoredAnswers.length ? Math.round(scoredAnswers.reduce((acc, a) => acc + (a.competencies?.reasoning || a.content_score * 10), 0) / denom) : 0;
        const avgConfidence = scoredAnswers.length ? Math.round(scoredAnswers.reduce((acc, a) => acc + (a.competencies?.confidence || a.delivery_score), 0) / denom) : 0;

        const intLog = viva.integrity_log || [];
        const hasHigh = intLog.some(l => l.severity === 'HIGH');
        const hasMed = intLog.some(l => l.severity === 'MEDIUM');
        const intStatus = hasHigh ? 'FLAGGED' : (hasMed ? 'SUSPICIOUS' : 'CLEAN');

        let rec = 'PASS — Strong';
        let recReason = 'Candidate demonstrated solid technical depth and clear oral reasoning.';
        if (intStatus === 'FLAGGED') {
          rec = 'REVIEW (Integrity Anomaly)';
          recReason = 'Multiple tab switches or reading patterns detected during the assessment.';
        } else if (overallFinal >= 80) {
          rec = 'PASS — Strong';
          recReason = 'Demonstrated deep conceptual mastery and clear, confident oral reasoning.';
        } else if (overallFinal >= 60) {
          rec = 'PASS';
          recReason = 'Satisfied syllabus baseline requirements with adequate verbal comprehension.';
        } else {
          rec = 'NEEDS WORK';
          recReason = 'Conceptual gaps noted in key rubric areas.';
        }

        const sessionRecord = {
          session_id: `offline_${cleanCode}_${Date.now()}`,
          code: cleanCode,
          candidate_id: viva.candidate_id || `ORL-${cleanCode}`,
          student_name: viva.student_name || 'Candidate',
          subject: viva.subject,
          topic: viva.topic,
          date: new Date().toISOString(),
          content_score: avgContent,
          delivery_score: avgDelivery,
          overall_score: overallFinal,
          competencies: {
            concept_depth: avgConcept,
            clarity: avgClarity,
            reasoning: avgReasoning,
            confidence: avgConfidence
          },
          integrity_status: intStatus,
          integrity_log: intLog,
          recommendation: rec,
          recommendation_reason: recReason,
          questions: viva.questions,
          answers: viva.answers
        };
        saveStoredSession(sessionRecord);
      }

      return {
        success: true,
        evaluation,
        viva_status: viva.status,
        next_question_index: viva.current_question,
        adaptive_probe_triggered: adaptiveProbeTriggered,
        probe_question: probeQuestion
      };
    }

    const formData = new FormData();
    if (audioBlob) {
      formData.append('audio', audioBlob, 'answer.wav');
    }
    formData.append('code', cleanCode);
    formData.append('qid', String(questionIndex));
    formData.append('question_index', questionIndex);
    formData.append('live_transcript', liveTranscript || '');
    formData.append('transcript', liveTranscript || '');
    formData.append('tab_switches', tabSwitches);
    formData.append('blur_events', blurEvents);
    formData.append('paste_events', pasteEvents);
    formData.append('response_latency_sec', latencySec);

    let res = await fetch(`${getApiBase()}/api/viva/${cleanCode}/answer`, {
      method: 'POST',
      body: formData
    });
    if (!res.ok) {
      res = await fetch(`${getApiBase()}/api/answer`, {
        method: 'POST',
        body: formData
      });
    }
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.error || 'Failed to grade answer');
    }
    return await res.json();
  }

  /**
   * Get live viva monitor data for teacher
   */
  async function getLive(code) {
    const cleanCode = (code || '').toUpperCase().trim();
    if (isHostedMode()) {
      const viva = await getViva(cleanCode);
      return {
        code: cleanCode,
        status: viva.status || 'waiting',
        current_question: viva.current_question || 0,
        total_questions: viva.total_questions || (viva.questions ? viva.questions.length : 5),
        student_name: viva.student_name || 'Waiting for candidate...',
        last_score: viva.last_score || null,
        answers: viva.answers || []
      };
    }

    const res = await fetch(`${getApiBase()}/api/viva/${cleanCode}/live`);
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.error || 'Failed to poll live room');
    }
    return await res.json();
  }

  /**
   * Get final scorecard by code or session_id
   */
  async function getScorecard(codeOrId) {
    const key = (codeOrId || '').trim();
    if (isHostedMode()) {
      const sessions = getStoredSessions();
      const found = sessions.find(s => s.code === key.toUpperCase() || s.session_id === key);
      if (found) return found;

      // Check in stored vivas
      const vivas = getStoredVivas();
      const viva = vivas[key.toUpperCase()];
      if (viva && viva.answers && viva.answers.length) {
        const scoredAnswers = viva.answers.filter(a => !a.no_answer && a.verdict !== 'no_answer');
        const denom = Math.max(scoredAnswers.length, 1);
        const avgContent = scoredAnswers.length ? Math.round((scoredAnswers.reduce((acc, a) => acc + (a.content_score || 0), 0) / denom) * 10) / 10 : 0.0;
        const avgDelivery = scoredAnswers.length ? Math.round(scoredAnswers.reduce((acc, a) => acc + (a.delivery_score || 0), 0) / denom) : 0;
        const rowAvgTotal = scoredAnswers.length ? Math.round(scoredAnswers.reduce((acc, a) => acc + (a.overall_score || 0), 0) / denom) : 0;
        const overallFinal = rowAvgTotal;

        const avgConcept = scoredAnswers.length ? Math.round(scoredAnswers.reduce((acc, a) => acc + (a.competencies?.concept_depth || a.content_score * 10), 0) / denom) : 0;
        const avgClarity = scoredAnswers.length ? Math.round(scoredAnswers.reduce((acc, a) => acc + (a.competencies?.clarity || a.delivery_score), 0) / denom) : 0;
        const avgReasoning = scoredAnswers.length ? Math.round(scoredAnswers.reduce((acc, a) => acc + (a.competencies?.reasoning || a.content_score * 10), 0) / denom) : 0;
        const avgConfidence = scoredAnswers.length ? Math.round(scoredAnswers.reduce((acc, a) => acc + (a.competencies?.confidence || a.delivery_score), 0) / denom) : 0;

        const intLog = viva.integrity_log || [];
        const hasHigh = intLog.some(l => l.severity === 'HIGH');
        const hasMed = intLog.some(l => l.severity === 'MEDIUM');
        const intStatus = hasHigh ? 'FLAGGED' : (hasMed ? 'SUSPICIOUS' : 'CLEAN');

        let rec = 'PASS — Strong';
        let recReason = 'Candidate demonstrated solid technical depth and clear oral reasoning.';
        if (intStatus === 'FLAGGED') {
          rec = 'REVIEW (Integrity Anomaly)';
          recReason = 'Multiple tab switches or reading patterns detected during the assessment.';
        } else if (overallFinal >= 80) {
          rec = 'PASS — Strong';
          recReason = 'Demonstrated deep conceptual mastery and clear, confident oral reasoning.';
        } else if (overallFinal >= 60) {
          rec = 'PASS';
          recReason = 'Satisfied syllabus baseline requirements with adequate verbal comprehension.';
        } else {
          rec = 'NEEDS WORK';
          recReason = 'Conceptual gaps noted in key rubric areas.';
        }

        return {
          session_id: `local_${key}`,
          code: key.toUpperCase(),
          candidate_id: viva.candidate_id || `ORL-${key.toUpperCase()}`,
          student_name: viva.student_name || 'Candidate',
          subject: viva.subject,
          topic: viva.topic,
          date: viva.created_at || new Date().toISOString(),
          duration: "05 mins 12 secs",
          content_score: avgContent,
          delivery_score: avgDelivery,
          overall_score: overallFinal,
          competencies: {
            concept_depth: avgConcept,
            clarity: avgClarity,
            reasoning: avgReasoning,
            confidence: avgConfidence
          },
          integrity_status: intStatus,
          integrity_log: intLog,
          recommendation: rec,
          recommendation_reason: recReason,
          questions: viva.questions,
          answers: viva.answers
        };
      }
      throw new Error('Scorecard not found');
    }

    let res = await fetch(`${getApiBase()}/api/viva/${key}/scorecard`);
    if (!res.ok) {
      res = await fetch(`${getApiBase()}/api/scorecard/${key}`);
    }
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.error || 'Scorecard not found');
    }
    return await res.json();
  }

  /**
   * Get all past sessions for Teacher Dashboard
   */
  async function getDashboard() {
    if (isHostedMode()) {
      const sessions = getStoredSessions();
      if (!sessions || sessions.length === 0) {
        // Seed initial 3 realistic demo records if empty for a polished first look
        const seed = [
          {
            session_id: "demo_101",
            code: "A7K2QX",
            student_name: "Aarav Sharma",
            subject: "Database Systems",
            topic: "Indexing & B+ Trees",
            date: "2026-11-20T10:30:00Z",
            content_score: 8.2,
            delivery_score: 88,
            overall_score: 84,
            status: "completed"
          },
          {
            session_id: "demo_102",
            code: "M3P9LT",
            student_name: "Priya Patel",
            subject: "Operating Systems",
            topic: "Deadlocks & Semaphores",
            date: "2026-11-21T14:15:00Z",
            content_score: 7.4,
            delivery_score: 79,
            overall_score: 76,
            status: "completed"
          },
          {
            session_id: "demo_103",
            code: "K8W2NZ",
            student_name: "Rohan Verma",
            subject: "Computer Networks",
            topic: "TCP/IP & Congestion Control",
            date: "2026-11-22T08:45:00Z",
            content_score: 9.0,
            delivery_score: 92,
            overall_score: 91,
            status: "completed"
          }
        ];
        localStorage.setItem(SESSIONS_STORAGE_KEY, JSON.stringify(seed));
        return { records: seed, total: seed.length, source: 'localStorage' };
      }
      return { records: sessions, total: sessions.length, source: 'localStorage' };
    }

    const res = await fetch(`${getApiBase()}/api/dashboard`);
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.error || 'Failed to load dashboard records');
    }
    return await res.json();
  }

  async function synthesizeSpeech(text, languageCode = 'en-IN') {
    if (isHostedMode() || !text || document.hidden) return null;
    try {
      const res = await fetch(`${getApiBase()}/api/voice/tts?format=json`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
        body: JSON.stringify({
          text: String(text).slice(0, 1000),
          language_code: languageCode || 'en-IN'
        })
      });
      if (!res.ok) return null;
      const data = await res.json().catch(() => null);
      return data && data.audio_b64 ? data.audio_b64 : null;
    } catch (e) {
      return null;
    }
  }

  async function transcribeVoice(audioBlob, language = 'auto') {
    if (isHostedMode() || !audioBlob) return null;
    try {
      const formData = new FormData();
      formData.append('audio', audioBlob, 'audio.webm');
      formData.append('language', language || 'auto');
      const res = await fetch(`${getApiBase()}/api/voice/stt`, {
        method: 'POST',
        body: formData
      });
      if (!res.ok) return null;
      return await res.json().catch(() => null);
    } catch (e) {
      return null;
    }
  }

  async function getVoiceHealth() {
    if (isHostedMode()) {
      return { provider: 'browser', key_present: false, credits_ok: true };
    }
    try {
      const res = await fetch(`${getApiBase()}/api/voice/health`);
      if (res.ok) return await res.json();
    } catch (e) {}
    return { provider: 'browser', key_present: false, credits_ok: true };
  }

  async function getVoiceUsage() {
    if (isHostedMode()) {
      return { today_calls: 0, cached_hit_rate: 1.0, estimated_spend_inr: 0, daily_limit: 300 };
    }
    try {
      const res = await fetch(`${getApiBase()}/api/voice/usage`);
      if (res.ok) return await res.json();
    } catch (e) {}
    return { today_calls: 0, cached_hit_rate: 1.0, estimated_spend_inr: 0, daily_limit: 300 };
  }

  // Expose global OralyzeApi
  window.OralyzeApi = {
    getApiBase,
    isHostedMode,
    previewViva,
    createViva,
    getViva,
    submitAnswer,
    getLive,
    getScorecard,
    getDashboard,
    synthesizeSpeech,
    transcribeVoice,
    getVoiceHealth,
    getVoiceUsage
  };
})();
