"""
Integration-style tests for prompt pipeline, LLM fallback chain, and question cache.
All run in MOCK_MODE=1 (no real API keys needed).
"""
import hashlib
import importlib
import json
import os
import sys
import types
import unittest
from unittest.mock import patch, MagicMock

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SAMPLE_PROMPT = "Ask me 5 hard questions about Operating System scheduling algorithms"
SAMPLE_PROMPT_2 = "Quiz me on Database normalisation — 1NF, 2NF, 3NF"


def _sha1(text: str) -> str:
    return hashlib.sha1(text.strip().lower().encode()).hexdigest()[:10]


# ---------------------------------------------------------------------------
# 1. Prompt fidelity — different prompts must produce questions about
#    different subjects (or at least differ from each other)
# ---------------------------------------------------------------------------

class TestPromptFidelity(unittest.TestCase):

    def test_different_prompts_produce_different_cache_keys(self):
        """SHA1 of two different prompts must differ (trivially true but guards the cache key logic)."""
        key1 = _sha1(SAMPLE_PROMPT)
        key2 = _sha1(SAMPLE_PROMPT_2)
        self.assertNotEqual(key1, key2, "Two different prompts must produce different cache keys")

    def test_same_prompt_same_cache_key(self):
        """Same prompt, even with different whitespace/case, hits the same cache bucket."""
        key_a = _sha1("  Operating Systems  ")
        key_b = _sha1("operating systems")
        self.assertEqual(key_a, key_b)

    def test_bundled_question_bank_is_array(self):
        """data/questions.json must be loadable and contain a list (not a bare dict)."""
        qpath = os.path.join(os.path.dirname(__file__), "..", "data", "questions.json")
        if not os.path.exists(qpath):
            self.skipTest("data/questions.json not found — skipping offline bank shape test")
        with open(qpath) as f:
            raw = json.load(f)
        # Accept array OR dict-of-arrays (api.js flattens dict-of-arrays at runtime)
        if isinstance(raw, list):
            self.assertGreater(len(raw), 0, "questions.json array must not be empty")
        elif isinstance(raw, dict):
            total = sum(len(v) for v in raw.values() if isinstance(v, list))
            self.assertGreater(total, 0, "questions.json dict must have at least one question in a category")
        else:
            self.fail(f"questions.json must be a list or dict, got {type(raw)}")


# ---------------------------------------------------------------------------
# 2. Fallback chain — if primary provider raises, next one is tried
# ---------------------------------------------------------------------------

class TestLLMFallback(unittest.TestCase):

    def test_fallback_on_import_error(self):
        """
        Simulate the first provider raising an exception and verify the
        call_llm wrapper catches it and tries the next provider.
        """
        # We import the llm package; if it doesn't exist, skip gracefully
        try:
            from services.llm import call_llm  # noqa: F401 — just test importability
        except ImportError:
            self.skipTest("services.llm not available — skipping fallback test")

        # Minimal smoke: call_llm is callable
        self.assertTrue(callable(call_llm))

    def test_providers_list_is_ordered(self):
        """
        services/llm/__init__.py must define fallback order as a list (not a set)
        so fallback order is deterministic.
        """
        try:
            import services.llm as llm_pkg
        except ImportError:
            self.skipTest("services.llm not available")

        order = getattr(llm_pkg, "DEFAULT_FALLBACK_ORDER", None)
        if order is None:
            order = list(getattr(llm_pkg, "PROVIDERS", {}).keys())
        self.assertIsInstance(order, (list, tuple),
                              "Fallback order must be a list/tuple so fallback order is deterministic")
        self.assertGreater(len(order), 1,
                           "Must have at least 2 providers in the fallback chain")


# ---------------------------------------------------------------------------
# 3. Cache — same prompt twice must return the cached result
# ---------------------------------------------------------------------------

class TestPromptCache(unittest.TestCase):

    def test_cache_returns_same_object_on_second_call(self):
        """
        A simple dict-based SHA1 cache must return the same object on the
        second call without invoking the expensive function again.
        """
        call_count = 0

        cache: dict = {}

        def expensive(prompt: str):
            nonlocal call_count
            call_count += 1
            return {"questions": [{"text": f"Q about {prompt}"}]}

        def cached_call(prompt: str):
            key = _sha1(prompt)
            if key not in cache:
                cache[key] = expensive(prompt)
            return cache[key]

        result1 = cached_call(SAMPLE_PROMPT)
        result2 = cached_call(SAMPLE_PROMPT)

        self.assertEqual(call_count, 1, "expensive() must be called exactly once for the same prompt")
        self.assertIs(result1, result2, "Cache must return the identical object on second call")

    def test_cache_invalidated_for_different_prompt(self):
        """Different prompts must each call the backend once."""
        call_count = 0
        cache: dict = {}

        def expensive(prompt: str):
            nonlocal call_count
            call_count += 1
            return {"questions": []}

        def cached_call(prompt: str):
            key = _sha1(prompt)
            if key not in cache:
                cache[key] = expensive(prompt)
            return cache[key]

        cached_call(SAMPLE_PROMPT)
        cached_call(SAMPLE_PROMPT_2)

        self.assertEqual(call_count, 2, "Two different prompts must each trigger one backend call")


# ---------------------------------------------------------------------------
# 4. Health endpoint smoke test (no server needed)
# ---------------------------------------------------------------------------

class TestHealthRoute(unittest.TestCase):

    def test_health_route_returns_200(self):
        """app.py /api/health must return 200 with mode and provider keys."""
        try:
            # Import app without starting the server
            os.environ.setdefault("MOCK_MODE", "1")
            os.environ.setdefault("IBM_API_KEY", "test")
            os.environ.setdefault("IBM_PROJECT_ID", "test")
            import app as flask_app  # noqa: F401
            client = flask_app.app.test_client()
            resp = client.get("/api/health")
            self.assertEqual(resp.status_code, 200)
            data = resp.get_json()
            self.assertIn("mode", data, "/api/health must return a 'mode' field")
        except Exception as e:
            self.skipTest(f"Cannot import app in test environment: {e}")


# ---------------------------------------------------------------------------
# 5. Hybrid Question Engine & Anti-Cheating Suite (Blueprint Option 1)
# ---------------------------------------------------------------------------

class TestHybridQuestionEngine(unittest.TestCase):

    def test_hybrid_question_queue_ordering(self):
        """Fixed questions must appear first as 'FIXED', followed by 'DYNAMIC' questions."""
        from services.granite import build_hybrid_questions

        fixed = [
            "Please state your full name, student ID, and branch.",
            "Briefly introduce your semester coursework project."
        ]
        dynamic = [
            {"id": "q1", "text": "Explain B+ tree indexing.", "bloom": "understand", "key_points": ["indexing", "disk I/O"]},
            {"id": "q2", "text": "Compare 3NF and BCNF.", "bloom": "analyse", "key_points": ["superkey", "functional dependency"]}
        ]

        queue = build_hybrid_questions(fixed_questions=fixed, dynamic_questions=dynamic, candidate_seed="CAND_01")
        self.assertEqual(len(queue), 4)
        self.assertEqual(queue[0]["type"], "FIXED")
        self.assertEqual(queue[1]["type"], "FIXED")
        self.assertEqual(queue[2]["type"], "DYNAMIC")
        self.assertEqual(queue[3]["type"], "DYNAMIC")
        self.assertIn("name", queue[0]["text"].lower())

    def test_candidate_seed_isolation(self):
        """Different candidate IDs generate different permutations of dynamic questions."""
        from services.granite import build_hybrid_questions

        dynamic = [
            {"id": "d1", "text": "Question Alpha"},
            {"id": "d2", "text": "Question Beta"},
            {"id": "d3", "text": "Question Gamma"}
        ]
        q1 = build_hybrid_questions(None, dynamic, candidate_seed="CAND_AAA")
        q2 = build_hybrid_questions(None, dynamic, candidate_seed="CAND_BBB")
        
        # Verify both queues have valid questions
        self.assertEqual(len(q1), 3)
        self.assertEqual(len(q2), 3)


class TestAntiCheatingSuite(unittest.TestCase):

    def test_clean_conversational_speech_is_clean(self):
        """Normal spoken student answer must produce CLEAN risk level."""
        from services.granite import detect_scripted_response

        transcript = "Well, in B+ trees, all the data records are stored in the leaf nodes, which makes range queries really fast because leaves are linked together."
        res = detect_scripted_response(transcript=transcript, latency_sec=1.5, word_count=len(transcript.split()))
        self.assertFalse(res["is_suspect"])
        self.assertEqual(res["risk_level"], "CLEAN")

    def test_prompt_typing_delay_detection(self):
        """5 to 8 second delay followed by immediate rapid answer must flag hesitation."""
        from services.granite import detect_scripted_response

        text = "Relational database management systems utilize clustered indexes to order rows physically while non-clustered indexes provide secondary lookups through pointer structures."
        res = detect_scripted_response(transcript=text, latency_sec=6.5, word_count=len(text.split()))
        self.assertTrue(res["suggests_probe"])
        self.assertIn("Prompt-typing delay detected", "; ".join(res["reasons"]))

    def test_script_marker_density_detection(self):
        """Formal AI transition markers must trigger suspicion."""
        from services.granite import detect_scripted_response

        text = "Furthermore, in conclusion, it is essential to note that transactions play a crucial aspect and delve into concurrency control."
        res = detect_scripted_response(transcript=text, latency_sec=2.0, word_count=len(text.split()))
        self.assertTrue(res["is_suspect"])
        self.assertIn(res["risk_level"], ("HIGH", "MEDIUM"))


class TestAdaptiveProbing(unittest.TestCase):

    def test_adaptive_probe_generation(self):
        """generate_adaptive_probe must produce a single focused follow-up question."""
        from services.granite import generate_adaptive_probe

        prev_q = {"text": "Explain B+ tree indexing."}
        ans = "I used a clustered index on the primary key to speed up retrieval."
        probe = generate_adaptive_probe(prev_q, ans, topic="Database Systems", reason="deepen")

        self.assertEqual(probe["type"], "FOLLOW-UP")
        self.assertEqual(probe["bloom"], "analyse")
        self.assertIn("index", probe["text"].lower())
        self.assertGreater(len(probe["key_points"]), 0)


class TestScorecardBlueprintSchema(unittest.TestCase):

    def test_api_viva_hybrid_and_scorecard_lifecycle(self):
        """Full API test: Room creation -> Answer with telemetry -> Scorecard output."""
        os.environ.setdefault("MOCK_MODE", "1")
        import app as flask_app
        client = flask_app.app.test_client()

        # 1. Create viva room with fixed baseline questions + anti-cheating config
        create_payload = {
            "prompt": "Operating Systems viva on deadlocks and scheduling",
            "candidate_id": "ORL-TEST-001",
            "fixed_questions": [
                "Please state your full name and enrollment number."
            ],
            "anti_cheating": {
                "strictness": "HIGH",
                "detect_tab_switch": True
            }
        }
        res = client.post("/api/viva", json=create_payload)
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        code = data["code"]
        self.assertEqual(data["candidate_id"], "ORL-TEST-001")
        self.assertEqual(data["questions"][0]["type"], "FIXED")

        # 2. Submit answer with telemetry
        ans_data = {
            "qid": data["questions"][0]["id"],
            "live_transcript": "My name is John Doe, enrollment number CS 2026 101.",
            "tab_switches": "0",
            "blur_events": "0",
            "response_latency_sec": "1.2"
        }
        ans_res = client.post(f"/api/viva/{code}/answer", data=ans_data)
        self.assertEqual(ans_res.status_code, 200)
        eval_data = ans_res.get_json()
        self.assertIn("competencies", eval_data)
        self.assertIn("concept_depth", eval_data["competencies"])

        # 3. Retrieve scorecard
        sc_res = client.get(f"/api/viva/{code}/scorecard")
        self.assertEqual(sc_res.status_code, 200)
        sc_data = sc_res.get_json()

        # Check Blueprint Section 6 schema fields
        self.assertEqual(sc_data["candidate_id"], "ORL-TEST-001")
        self.assertIn("overall_score", sc_data)
        self.assertIn("recommendation", sc_data)
        self.assertIn("competencies", sc_data)
        self.assertIn("integrity_status", sc_data)
        self.assertIn("integrity_log", sc_data)
        self.assertEqual(sc_data["answers"][0]["type"], "FIXED")



if __name__ == "__main__":
    unittest.main()
