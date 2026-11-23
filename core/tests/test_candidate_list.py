import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from zhihu_agent.candidate_list import load_snapshot, make_candidate_list, validate_labels


class CandidateListTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "snapshot.json"
        self.path.write_text(json.dumps({"items": [
            {"type": "answer", "id": "1", "question_id": "10", "title": "原始问题", "author": "甲"},
            {"type": "answer", "id": "1", "question_id": "10", "title": "同一回答另一个标题", "author": "甲"},
            {"type": "article", "id": "2", "title": "原始文章", "author": "乙"},
        ]}), encoding="utf-8-sig")
        self.snapshot = load_snapshot(self.path)
        self.labels = [{"candidate_id": "article:2", "topic": "技术", "reading_question": "可关注具体方法"},
                       {"candidate_id": "answer:1", "topic": "科学", "reading_question": "可关注问题机制"}]

    def test_dedup_preserves_ids_titles_and_order(self):
        items = validate_labels(self.snapshot, self.labels)
        self.assertEqual([i["number"] for i in items], [1, 3])
        self.assertEqual(items[0]["title"], "原始问题")
        self.assertEqual(items[0]["feed_positions"], [1, 2])
        self.assertEqual(items[0]["url"], "https://www.zhihu.com/question/10/answer/1")

    def test_missing_duplicate_or_unknown_candidates_rejected(self):
        for labels in (self.labels[:1], [self.labels[0], self.labels[0]],
                       [self.labels[0], dict(self.labels[1], candidate_id="answer:999")]):
            with self.assertRaises(ValueError):
                validate_labels(self.snapshot, labels)

    def test_model_repairs_incomplete_list_without_feed_refresh(self):
        responses = iter([self.labels[:1], self.labels])
        received = []
        class Client:
            def chat(self, messages, tools):
                received.append(json.loads(json.dumps(messages)))
                return {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [{
                    "id": str(len(received)), "type": "function", "function": {"name": "present_candidates",
                    "arguments": json.dumps({"items": next(responses)})}}]}}]}
        report = make_candidate_list(self.snapshot, Client())
        self.assertEqual(len(report["items"]), 2)
        self.assertFalse(report["body_read"])
        self.assertTrue(any(m["role"] == "tool" and "error" in m["content"] for m in received[1]))
