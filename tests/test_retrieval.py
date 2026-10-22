import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tutor_rag.engine import ROOT, Retriever
from tutor_rag.__main__ import evaluate


class RetrievalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.r = Retriever()

    def test_probability_question_finds_bridge_in_both_modes(self):
        for mode in ("bm25", "concept-bm25"):
            hits = self.r.search("分类给了0.7的概率，为啥不是回归？", mode)
            self.assertEqual(hits[0].card_id, "probability_vs_regression")

    def test_clustering_number_does_not_return_regression_first(self):
        hits = self.r.search("聚类组号的大小是不是客户价值的高低？")
        self.assertEqual(hits[0].card_id, "cluster_id")

    def test_no_model_answer_is_fabricated(self):
        result = self.r.result("分类概率与回归的关系")
        self.assertTrue(result["matches"])
        self.assertIsNone(result["answer"])
        self.assertEqual(result["generation_status"], "not_configured")

    def test_unrelated_query_does_not_get_context(self):
        for mode in ("bm25", "concept-bm25"):
            result = self.r.prompt("明天深圳的天气如何？", mode)
            self.assertEqual(result["status"], "no_context")
            self.assertEqual(result["messages"], [])

    def test_bridge_context_retains_prerequisites_and_source_urls(self):
        result = self.r.prompt("分类输出概率为什么不是回归？", top_k=1)
        self.assertTrue({"classification", "regression", "probability_vs_regression"}
                        <= set(result["included_ids"]))
        data = json.loads(result["messages"][1]["content"])
        self.assertIn("https://", data["candidate_materials"])
        for card_id in result["included_ids"]:
            self.assertIn(self.r.by_id[card_id]["content"], data["candidate_materials"])

    def test_budget_drops_whole_cards_and_reports_omissions(self):
        result = self.r.prompt("分类输出概率为什么不是回归？", max_chars=500)
        self.assertLessEqual(result.get("context_chars", 0), 500)
        self.assertTrue(result["omitted_ids"])
        if result["messages"]:
            text = json.loads(result["messages"][1]["content"])["candidate_materials"]
            for key in result["included_ids"]:
                self.assertIn(self.r.by_id[key]["content"], text)

    def test_diagnostic_answers_are_not_in_corpus_or_prompt(self):
        encoded = json.dumps(self.r.cards, ensure_ascii=False)
        prompt = json.dumps(self.r.prompt("分类输出概率为什么不是回归？"), ensure_ascii=False)
        self.assertNotIn("relevant_ids", encoded + prompt)
        self.assertNotIn("overlap_unsupported", encoded + prompt)

    def test_unknown_source_is_rejected(self):
        workspace_temp = ROOT / "output"
        workspace_temp.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=workspace_temp) as temp:
            target = Path(temp)
            self.assertTrue(target.resolve().is_relative_to(ROOT.resolve()))
            for filename in ("cards.json", "sources.json", "concepts.json"):
                shutil.copyfile(ROOT / "kb" / filename, target / filename)
            cards = json.loads((target / "cards.json").read_text(encoding="utf-8"))
            cards[0]["sources"] = ["invented-source"]
            (target / "cards.json").write_text(json.dumps(cards), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "source"):
                Retriever(target)

    def test_unsupported_cases_have_separate_denominator(self):
        report = evaluate(self.r, [
            {"id":"known", "question":"分类输出概率为什么不是回归？", "kind":"bridge",
             "relevant_ids":["probability_vs_regression"]},
            {"id":"unknown", "question":"明天深圳天气如何？", "kind":"unsupported", "relevant_ids":[]}])
        for result in report["reports"].values():
            self.assertEqual(result["in_scope_count"], 1)
            self.assertEqual(result["unsupported_count"], 1)
            self.assertEqual(result["hit_at_3"], 1.0)

    def test_cli_rejects_empty_question(self):
        proc = subprocess.run([sys.executable, "-m", "tutor_rag", "search", " "], cwd=ROOT,
                              capture_output=True, encoding="utf-8")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("1..2000", proc.stderr)


if __name__ == "__main__":
    unittest.main()
