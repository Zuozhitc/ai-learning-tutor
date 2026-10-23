import copy
import tempfile
import unittest
from pathlib import Path

from tutor_rag.engine import ROOT
from tutor_rag.workflow import Workflow, read_json, replay_demo, write_json


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.w = Workflow()
        self.state = self.w.start("probability_vs_regression")

    def response(self, request, **changes):
        result = {"request_id": request["request_id"], "kind": request["kind"],
                  "text": "合成测试文本，测试不评估这段文字的教学有效性。",
                  "status": "grounded", "citations": [request["materials"][0]["id"]] if request["materials"] else []}
        result.update(changes)
        return result

    def explained(self):
        state, request = self.w.request(self.state, "explain")
        return self.w.apply(state, self.response(request))

    def assessment_pending(self):
        state = self.w.event(self.explained(), "checkpoint")
        state = self.w.event(state, "answer", "合成学习者回答")
        return self.w.request(state, "assess")

    def test_no_checkpoint_before_explanation(self):
        with self.assertRaises(ValueError):
            self.w.event(self.state, "checkpoint")
        self.assertNotIn("checkpoint_question", self.explained())

    def test_main_materials_and_rubric_are_separate(self):
        _, request = self.w.request(self.state, "explain")
        self.assertEqual({m["id"] for m in request["materials"]},
                         {"classification", "regression", "probability_vs_regression"})
        self.assertNotIn("checkpoint", request)
        self.assertNotIn("rubric", str(request))
        _, request = self.assessment_pending()
        self.assertTrue(request["checkpoint"]["rubric"])

    def test_invalid_output_does_not_mutate_state(self):
        state, request = self.w.request(self.state, "explain")
        original = copy.deepcopy(state)
        with self.assertRaisesRegex(ValueError, "Citation"):
            self.w.apply(state, self.response(request, citations=["invented"]))
        self.assertEqual(state, original)

    def test_response_cannot_redirect_unit(self):
        state, request = self.w.request(self.state, "explain")
        with self.assertRaisesRegex(ValueError, "unknown fields"):
            self.w.apply(state, self.response(request, unit_id="classification_vs_clustering"))

    def test_stale_duplicate_and_parallel_output_are_rejected(self):
        state, request = self.w.request(self.state, "explain")
        with self.assertRaises(ValueError):
            self.w.request(state, "explain")
        with self.assertRaisesRegex(ValueError, "mismatched"):
            self.w.apply(state, self.response(request, request_id="old"))
        updated = self.w.apply(state, self.response(request))
        with self.assertRaises(ValueError):
            self.w.apply(updated, self.response(request))

    def test_cancel_and_retry_preserve_stage(self):
        state, old = self.w.request(self.state, "explain")
        state = self.w.event(state, "cancel_request")
        state, new = self.w.request(state, "explain")
        self.assertEqual(state["phase"], "ready")
        self.assertNotEqual(old["request_id"], new["request_id"])
        with self.assertRaises(ValueError):
            self.w.apply(state, self.response(old))

    def test_pause_reload_resume_retains_pending_request(self):
        state, request = self.w.request(self.state, "explain")
        state = self.w.event(state, "pause")
        workspace_temp = ROOT / "output"
        workspace_temp.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=workspace_temp) as directory:
            path = Path(directory) / "session.json"
            self.assertTrue(path.resolve().is_relative_to(ROOT.resolve()))
            write_json(path, state)
            restored = read_json(path)
            with self.assertRaises(ValueError):
                self.w.apply(restored, self.response(request))
            restored = self.w.event(restored, "resume")
            restored = self.w.apply(restored, self.response(request))
        self.assertEqual(restored["phase"], "checkpoint_ready")

    def test_deferred_question_preserves_goal_and_pending_request(self):
        state, request = self.w.request(self.state, "explain")
        state = self.w.event(state, "defer", "以后再学阈值")
        self.assertEqual(state["goal"], self.state["goal"])
        self.assertEqual(state["pending"]["request_id"], request["request_id"])
        self.assertEqual(state["deferred"][0]["status"], "unresolved")

    def test_clarification_returns_to_original_checkpoint_and_marks_help(self):
        state = self.w.event(self.explained(), "checkpoint")
        state, request = self.w.request(state, "clarify", "分类输出概率为什么不是回归？")
        state = self.w.apply(state, self.response(request))
        self.assertEqual(state["phase"], "awaiting_answer")
        state = self.w.event(state, "answer", "有帮助后的合成回答")
        self.assertTrue(state["answer_history"][-1]["assistance_used"])

    def test_insufficient_material_does_not_advance_or_fabricate(self):
        state = self.explained()
        state, request = self.w.request(state, "clarify", "明天深圳会下雨吗？")
        self.assertEqual(request["evidence_status"], "no_materials")
        with self.assertRaises(ValueError):
            self.w.apply(state, self.response(request, citations=[]))
        updated = self.w.apply(state, self.response(request, status="insufficient_evidence", citations=[]))
        self.assertEqual(updated["phase"], "checkpoint_ready")

    def test_uncertain_assessment_does_not_pass_unit(self):
        state, request = self.assessment_pending()
        state = self.w.apply(state, self.response(request, verdict="uncertain"))
        self.assertEqual(state["phase"], "review_required")
        self.assertEqual(state["learning_claim"], "not_established")
        with self.assertRaises(ValueError):
            self.w.request(state, "summarize")
        clarified, request = self.w.request(state, "clarify", "分类输出概率为什么不是回归？")
        clarified = self.w.apply(clarified, self.response(request))
        self.assertEqual(clarified["phase"], "review_required")

    def test_retry_replay_records_help_and_never_proves_mastery(self):
        report = replay_demo(self.w)
        state = report["final_state"]
        self.assertEqual(state["phase"], "completed")
        self.assertEqual(state["attempts"], 2)
        self.assertFalse(state["answer_history"][0]["assistance_used"])
        self.assertTrue(state["answer_history"][1]["assistance_used"])
        self.assertEqual(state["learning_claim"], "workflow_closed_assessment_unverified")
        self.assertFalse(report["real_model_tested"])
        self.assertFalse(report["small_model_capability_established"])

    def test_only_explicit_next_unit_changes_goal_and_retains_backlog(self):
        with self.assertRaises(ValueError):
            self.w.next_unit(self.state, "classification_vs_clustering")
        completed = replay_demo(self.w)["final_state"]
        new = self.w.next_unit(completed, "classification_vs_clustering")
        self.assertEqual(new["phase"], "ready")
        self.assertEqual(new["session_id"], completed["session_id"])
        self.assertEqual(new["deferred"], completed["deferred"])
        self.assertEqual(len(new["previous_units"]), 1)
        self.assertNotEqual(new["goal"], completed["goal"])

    def test_long_output_is_rejected(self):
        state, request = self.w.request(self.state, "explain")
        with self.assertRaisesRegex(ValueError, "length"):
            self.w.apply(state, self.response(request, text="字" * 1201))

    def test_skipping_checkpoint_closes_without_learning_evidence(self):
        state = self.w.event(self.explained(), "skip_checkpoint")
        state, request = self.w.request(state, "summarize")
        state = self.w.apply(state, self.response(request))
        self.assertEqual(state["phase"], "completed")
        self.assertEqual(state["learning_claim"], "workflow_closed_checkpoint_skipped")
        self.assertEqual(state["attempts"], 0)


if __name__ == "__main__":
    unittest.main()
