/**
 * Oralyze Keyword-Overlap Grader (Browser Runtime)
 * Matches the exact logic and scoring weights of services/granite.py `_keyword_grade`
 */
(function() {
  const FILLERS = ['um', 'uh', 'like', 'actually', 'basically', 'you know', 'sort of', 'kind of'];

  const HINGLISH_MAP = {
    'इंडेक्स': 'index', 'इंडैक्स': 'index',
    'टेबल': 'table',
    'की': 'key',
    'ट्री': 'tree',
    'डेडलॉक': 'deadlock',
    'क्वेरी': 'query',
    'डेटाबेस': 'database',
    'मेमोरी': 'memory',
    'प्रोसेस': 'process',
    'एल्गोरिदम': 'algorithm', 'एल्गोरिथम': 'algorithm',
    'नेटवर्क': 'network',
    'कैश': 'cache',
    'थ्रेड': 'thread',
    'सर्वर': 'server',
    'क्लाइंट': 'client'
  };

  const graderCache = new Map();

  function normalizeText(text) {
    let cleaned = (text || '').toLowerCase();
    for (const [dev, eng] of Object.entries(HINGLISH_MAP)) {
      cleaned = cleaned.replaceAll(dev, eng);
    }
    return cleaned.replace(/[^a-z0-9\s]/g, ' ').replace(/\s+/g, ' ').trim();
  }

  function gradeAnswer(question, transcript, language = 'en') {
    if (!question) {
      throw new Error("GradeAnswer requires a valid question object.");
    }
    const rawTranscript = (transcript || '').trim();
    const clean = normalizeText(rawTranscript);
    const words = clean ? clean.split(' ') : [];
    const keyPoints = (question.key_points || []).map(kp => (kp || '').trim()).filter(Boolean);
    const qid = String(question.id || question.qid || '');

    // Check cache
    const cacheKey = `${qid}_${clean}`;
    if (graderCache.has(cacheKey)) {
      return graderCache.get(cacheKey);
    }

    // Section 3 / Fix B: Transcripts with fewer than 5 words must be 'no_answer'
    if (words.length < 5) {
      const noAnsResult = {
        score: 0.0,
        verdict: 'no_answer',
        no_answer: true,
        source: 'heuristic',
        correct_points: [],
        missing_points: keyPoints,
        feedback: 'We could not hear an answer — record again or type it',
        ideal_answer: keyPoints.length ? keyPoints.join('. ') + '.' : 'A comprehensive technical explanation covering key principles.'
      };
      graderCache.set(cacheKey, noAnsResult);
      return noAnsResult;
    }

    const correctPoints = [];
    const missingPoints = [];
    let matchedStemsCount = 0;

    for (const kp of keyPoints) {
      const kpClean = normalizeText(kp);
      const kpTokens = kpClean.split(' ').filter(t => t.length > 2);
      let matched = false;

      if (clean.includes(kpClean)) {
        matched = true;
      } else if (kpTokens.length > 0) {
        let matchCount = 0;
        for (const token of kpTokens) {
          if (clean.includes(token)) {
            matchCount++;
          }
        }
        if (matchCount / kpTokens.length >= 0.5) {
          matched = true;
        } else if (matchCount >= 1) {
          matchedStemsCount++;
        }
      }

      if (matched) {
        correctPoints.push(kp);
        matchedStemsCount++;
      } else {
        missingPoints.push(kp);
      }
    }

    const totalPts = Math.max(keyPoints.length, 1);
    const matchedRatio = correctPoints.length / totalPts;
    const lengthBonus = Math.min(words.length / 30.0, 1.0) * 1.5;
    const rawScore = (matchedRatio * 8.5) + lengthBonus;

    let score = Math.min(Math.max(rawScore, 1.0), 10.0);
    score = Math.round(score * 10) / 10;

    // Section 3 / Fix B: Lenient fallback floor for 25+ words with >= 2 stems
    let source = 'keyword';
    if (words.length >= 25 && matchedStemsCount >= 2) {
      if (score < 3.0) {
        score = 3.0;
      }
      source = 'heuristic';
    }

    // Three verdicts, not two: correct (>= 7.0), partial (4.0 - 6.9), wrong (< 4.0)
    let verdict = 'wrong';
    if (score >= 7.0) {
      verdict = 'correct';
    } else if (score >= 4.0) {
      verdict = 'partial';
    } else {
      verdict = 'wrong';
    }

    let feedback = '';
    if (verdict === 'correct') {
      feedback = 'Solid explanation covering the essential concepts clearly.';
    } else if (verdict === 'partial') {
      const missingHint = missingPoints.slice(0, 2).join(' and ');
      feedback = missingHint ? `Good attempt, but make sure to elaborate on ${missingHint}.` : 'Fair answer, could be more comprehensive.';
    } else {
      feedback = 'The answer missed core technical elements. Review the key concepts.';
    }

    const idealAnswer = keyPoints.length ? keyPoints.join('. ') + '.' : 'A comprehensive technical explanation covering key principles.';

    const result = {
      score,
      verdict,
      source,
      correct_points: correctPoints,
      missing_points: missingPoints,
      feedback,
      ideal_answer: idealAnswer
    };

    graderCache.set(cacheKey, result);
    return result;
  }

  function calculateDeliveryScore(wordTimestamps, durationSeconds) {
    if (!wordTimestamps || wordTimestamps.length === 0) {
      return {
        delivery_score: 50,
        wpm: 120,
        filler_ratio: 0,
        filler_count: 0,
        pause_count: 0,
        pace_label: 'moderate'
      };
    }

    const totalWords = wordTimestamps.length;
    const duration = Math.max(durationSeconds || 1, 1);
    const wpm = Math.round((totalWords / duration) * 60);

    let fillerCount = 0;
    for (const item of wordTimestamps) {
      const w = normalizeText(item.word || item);
      if (FILLERS.includes(w)) fillerCount++;
    }
    const fillerRatio = Math.round((fillerCount / Math.max(totalWords, 1)) * 100);

    let pauseCount = 0;
    for (let i = 1; i < wordTimestamps.length; i++) {
      const prevEnd = wordTimestamps[i-1].end;
      const currStart = wordTimestamps[i].start;
      if (prevEnd !== undefined && currStart !== undefined && (currStart - prevEnd) > 0.8) {
        pauseCount++;
      }
    }

    // Pace penalty (ideal 110-165 wpm)
    let paceScore = 100;
    if (wpm < 110) paceScore = Math.max(40, 100 - (110 - wpm) * 0.8);
    else if (wpm > 165) paceScore = Math.max(40, 100 - (wpm - 165) * 0.8);

    const fillerScore = Math.max(30, 100 - (fillerRatio * 6));
    const pauseScore = Math.max(40, 100 - (pauseCount * 8));

    const deliveryScore = Math.round((paceScore * 0.4) + (fillerScore * 0.35) + (pauseScore * 0.25));

    let paceLabel = 'moderate';
    if (wpm < 110) paceLabel = 'hesitant';
    else if (wpm > 165) paceLabel = 'rapid';

    return {
      delivery_score: Math.min(Math.max(deliveryScore, 10), 99),
      wpm,
      filler_ratio: fillerRatio,
      filler_count: fillerCount,
      pause_count: pauseCount,
      pace_label: paceLabel
    };
  }

  function detectScriptedResponse(transcript, latencySec = 0, wordCount = 0) {
    const raw = (transcript || '').trim();
    const words = raw.split(/\s+/).filter(Boolean);
    const totalWords = wordCount > 0 ? wordCount : words.length;

    if (totalWords < 5) {
      return {
        is_suspect: false,
        risk_level: 'CLEAN',
        risk_score: 0,
        reasons: [],
        suggests_probe: false,
        lexical_density: 0.0
      };
    }

    const reasons = [];
    let riskScore = 0;

    // 1. Prompt-typing latency hesitation before instant recital
    if (latencySec >= 4.8 && latencySec <= 9.2 && totalWords >= 15) {
      riskScore += 40;
      reasons.push(`Prompt-typing delay detected (${latencySec.toFixed(1)}s hesitation before fluent recital)`);
    }

    // 2. Formal written/textbook script markers
    const scriptMarkers = [
      "furthermore", "moreover", "in conclusion", "to summarize", "crucial aspect",
      "it is essential to note", "firstly", "secondly", "subsequently", "delve into",
      "in summary", "pivotal role", "seamlessly", "testament to"
    ];
    const textLow = raw.toLowerCase();
    const matched = scriptMarkers.filter(m => textLow.includes(m));
    if (matched.length > 0) {
      riskScore += Math.min(matched.length * 20, 45);
      reasons.push(`Formal written-script markers detected (${matched.slice(0, 3).join(', ')})`);
    }

    // 3. Polysyllabic lexical density
    const longWords = words.filter(w => w.length >= 8);
    const lexicalDensity = Math.round((longWords.length / Math.max(totalWords, 1)) * 100) / 100;
    if (lexicalDensity >= 0.30 && totalWords >= 20) {
      riskScore += 25;
      reasons.push(`High written lexical density (${Math.round(lexicalDensity * 100)}% polysyllabic tokens)`);
    }

    let riskLevel = 'CLEAN';
    if (riskScore >= 60) riskLevel = 'HIGH';
    else if (riskScore >= 30) riskLevel = 'MEDIUM';
    else if (riskScore >= 15) riskLevel = 'LOW';

    const isSuspect = riskLevel === 'HIGH' || riskLevel === 'MEDIUM';

    return {
      is_suspect: isSuspect,
      risk_level: riskLevel,
      risk_score: riskScore,
      reasons: reasons,
      suggests_probe: isSuspect || riskScore >= 25,
      lexical_density: lexicalDensity
    };
  }

  function generateAdaptiveProbe(prevQuestion, prevAnswer, topic = '', reason = 'deepen') {
    const ansLow = (prevAnswer || '').toLowerCase();
    let text = "You noted that key concept—what is a specific edge-case scenario where that mechanism would fail?";
    let keyPoints = ["edge case limitation", "failure mode", "alternative approach"];

    if (ansLow.includes('index') || ansLow.includes('b+') || ansLow.includes('b tree') || ansLow.includes('clustered')) {
      text = "You mentioned using an index there—what happens to write throughput and page splits when inserting 10,000 rows per second?";
      keyPoints = ["write latency penalty", "page splits", "index maintenance overhead"];
    } else if (ansLow.includes('acid') || ansLow.includes('transaction') || ansLow.includes('isolation')) {
      text = "Under high concurrent load, what specific isolation anomaly could still occur with that setup?";
      keyPoints = ["phantom read", "dirty read", "locking overhead", "serializability"];
    } else if (ansLow.includes('deadlock') || ansLow.includes('semaphore') || ansLow.includes('mutex')) {
      text = "How would you prevent circular wait dynamically in code without locking every resource upfront?";
      keyPoints = ["resource ordering", "lock timeout", "banker's algorithm", "try-lock"];
    } else if (ansLow.includes('tcp') || ansLow.includes('udp') || ansLow.includes('handshake')) {
      text = "If packet loss suddenly increases to 20%, how does the congestion window scale down?";
      keyPoints = ["slow start", "additive decrease", "retransmission timeout", "throughput drop"];
    } else if (ansLow.includes('bfs') || ansLow.includes('dfs') || ansLow.includes('tree') || ansLow.includes('graph')) {
      text = "If the graph contains cycles or deep paths, how does memory consumption compare between BFS and DFS?";
      keyPoints = ["call stack overflow in DFS", "queue memory in wide BFS", "visited set"];
    } else if (reason === 'script_check') {
      text = "Can you briefly walk me through a real project or situation where you had to debug that exact trade-off?";
      keyPoints = ["personal implementation experience", "trade-off explanation", "practical troubleshooting"];
    }

    return {
      id: `probe_${Date.now() % 100000}`,
      text: text,
      type: "FOLLOW-UP",
      bloom: "analyse",
      key_points: keyPoints,
      ideal_seconds: 35,
      probe_reason: reason
    };
  }

  function calculateCompetencies(contentScore, deliveryScore, correctPointsCount, pauseCount) {
    const conceptDepth = Math.round(Math.min(100, (contentScore || 0) * 10));
    const clarity = Math.round(deliveryScore || 0);
    const reasoning = Math.round(Math.min(100, ((correctPointsCount || 0) * 25) + ((contentScore || 0) * 5)));
    const confidence = Math.round(Math.max(30, Math.min(100, (deliveryScore || 0) - ((pauseCount || 0) * 3.5))));

    return {
      concept_depth: conceptDepth,
      clarity: clarity,
      reasoning: reasoning,
      confidence: confidence
    };
  }

  window.OralyzeGrader = {
    gradeAnswer,
    calculateDeliveryScore,
    detectScriptedResponse,
    generateAdaptiveProbe,
    calculateCompetencies
  };
})();
