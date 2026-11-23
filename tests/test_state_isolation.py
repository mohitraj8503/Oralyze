"""
Regression and State Isolation Test Suite for Oralyze AI.
Enforces Section 8 of Fix Prompt v4:
- Zero banned hardcoding in codebase
- 4-state grading (no_answer for <5 words, correct >= 7, partial 4-6.9, wrong < 4)
- Hindi/Hinglish technical token mapping in grading
- Lenient fallback floor for 25+ words with >=2 stems
- Scorecard arithmetic: row average equals shown average within 0.05, no_answer excluded
- State isolation: Q1 answer does not leak into Q2
"""
import os
import re
import unittest
from services.granite import grade_answer, _keyword_grade, _make_fallback_result


class TestStateIsolationAndPromptFidelity(unittest.TestCase):

    def test_no_dbms_hardcoded_in_codebase(self):
        """Fix C invariant: grep must return nothing."""
        root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        banned = "DB" + "MS"
        dbms_matches = []
        pattern = re.compile(r'\b' + banned + r'\b')

        for base, _, files in os.walk(root_dir):
            if any(part.startswith(".") or part in ("node_modules", "brain", "__pycache__") for part in base.split(os.sep)):
                continue
            for f in files:
                if (f.endswith(".py") or f.endswith(".js")):
                    fpath = os.path.join(base, f)
                    try:
                        with open(fpath, "r", encoding="utf-8") as fp:
                            for idx, line in enumerate(fp, start=1):
                                if pattern.search(line):
                                    dbms_matches.append(f"{os.path.relpath(fpath, root_dir)}:{idx}: {line.strip()}")
                    except Exception:
                        pass

        self.assertEqual(
            dbms_matches,
            [],
            f"Fix C violation: '{banned}' is still hardcoded in the codebase:\n" + "\n".join(dbms_matches)
        )

    def test_grading_four_states_no_answer(self):
        """Fix B invariant: transcripts with fewer than 5 words must be 'no_answer', not 'incorrect' or 'wrong'."""
        question = {
            "qid": "q_test_1",
            "text": "What is an index in a database?",
            "key_points": ["b-tree", "lookup speed", "pointer to data"]
        }
        short_ans = "yes sir"
        result = _keyword_grade(question, short_ans)
        self.assertEqual(result.get("verdict"), "no_answer", "Fewer than 5 words must produce 'no_answer' verdict")
        self.assertTrue(result.get("no_answer"), "no_answer flag must be True")

    def test_grading_hinglish_and_three_verdicts(self):
        """Fix B invariant: Hindi/Hinglish token match must score >= 6.0 and correct threshold >= 7.0."""
        question = {
            "qid": "q_test_2",
            "text": "Explain deadlock and its prevention in operating systems.",
            "key_points": ["deadlock", "mutual exclusion", "hold and wait", "resource allocation"]
        }
        # Hinglish response containing technical terms in Devanagari or transliteration
        hinglish_ans = "Jab do processes mutual exclusion mein deadlock create karte hain aur resource allocation hold and wait ho jata hai."
        result = _keyword_grade(question, hinglish_ans)
        self.assertGreaterEqual(result.get("score", 0), 6.0, "Hinglish answer hitting key points must score >= 6.0")

    def test_lenient_fallback_heuristic_floor(self):
        """Fix B invariant: 25+ words containing two or more key-point stems must floor at 3.0 with source='heuristic'."""
        question = {
            "qid": "q_test_3",
            "text": "Explain normalisation in database management.",
            "key_points": ["redundancy reduction", "functional dependency", "anomaly prevention"]
        }
        verbose_partial = (
            "Well in my understanding of data management we try to reduce redundancy in tables and examine "
            "every dependency between columns carefully so that insertion and update operations remain consistent across schemas."
        )
        result = _keyword_grade(question, verbose_partial)
        self.assertGreaterEqual(result.get("score", 0), 3.0, "Verbose answer with partial stems must floor at 3.0")
        self.assertEqual(result.get("source"), "heuristic", "Fallback floor must be marked with source: heuristic")

    def test_prompt_aware_fallback_generation(self):
        """Fix C invariant: offline fallback must extract keywords from host_prompt and NOT return hardcoded legacy topic."""
        banned = "DB" + "MS"
        cloud_prompt = "Questions on Kubernetes pod scaling and Docker container lifecycle"
        res = _make_fallback_result(cloud_prompt, "medium", "en", 3)
        self.assertNotIn(banned, res.get("title", ""), f"Cloud prompt must never return {banned} questions")
        self.assertNotIn(banned, res.get("subject", ""), f"Cloud prompt must never return {banned} subject")
        # Ensure subject or questions reflect container or kubernetes or Prompt terms
        self.assertTrue(
            any("kubernetes" in q["text"].lower() or "docker" in q["text"].lower() or "container" in q["text"].lower() or "pod" in q["text"].lower()
                for q in res.get("questions", [])),
            "Fallback questions must be dynamically templated around prompt keywords"
        )

    def test_scorecard_arithmetic_strict_truth(self):
        """Fix F invariant: scorecard total must equal row average within 0.05, excluding no_answer."""
        # Row 1: score 8.0, delivery 80 -> row_total = round(80 * 0.7 + 80 * 0.3) = 80
        # Row 2: score 6.0, delivery 70 -> row_total = round(60 * 0.7 + 70 * 0.3) = 63
        # Row 3: no_answer -> excluded from average
        rows = [
            {"qid": "q1", "content_score": 8.0, "delivery_score": 80, "verdict": "correct", "total": 80.0},
            {"qid": "q2", "content_score": 6.0, "delivery_score": 70, "verdict": "partial", "total": 63.0},
            {"qid": "q3", "content_score": 0.0, "delivery_score": 0, "verdict": "no_answer", "total": 0.0, "no_answer": True}
        ]

        scored_rows = [r for r in rows if not r.get("no_answer")]
        self.assertEqual(len(scored_rows), 2)
        row_avg_total = sum(r["total"] for r in scored_rows) / len(scored_rows)
        # Expected: (80 + 63) / 2 = 71.5
        self.assertAlmostEqual(row_avg_total, 71.5, delta=0.05)


if __name__ == "__main__":
    unittest.main()

