import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from zhihu_agent.recommend import Preferences, ReadingStore, RecommendationSession, ZhihuGateway, candidate_url, recommend
from zhihu_agent.kimi import KimiError
from zhihu_agent.candidate_list import load_snapshot


class FakeGateway:
    def __init__(self):
        self.reads = []
        self.feed_calls = 0

    def feed(self, limit):
        self.feed_calls += 1
        return [
            {"target_type": "answer", "id": "1", "title": "量子计算已收藏"},
            {"target_type": "answer", "id": "2", "question_id": "42", "title": "量子计算新回答", "author": {"name": "作者"}},
            {"target_type": "article", "id": "3", "title": "量子计算新文章"},
            {"target_type": "pin", "id": "4"},
        ][:limit]

    def read(self, item):
        self.reads.append(item["candidate_id"])
        if item["id"] == "3":
            raise RuntimeError("HTTP 403")
        return "这里是量子计算的具体原文证据。"


class ScriptedClient:
    def __init__(self, requests):
        self.requests = iter(requests)
        self.received = []

    def chat(self, messages, tools):
        self.received.append(json.loads(json.dumps(messages)))
        name, args = next(self.requests)
        if callable(args):
            args = args(messages, tools)
        return {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [
            {"id": str(len(self.received)), "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}
        ]}}]}


class RecommendationTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        favorite = root / "favorite.md"
        favorite.write_text("量子计算的数学原理和计算过程。", encoding="utf-8")
        self.corpus = SimpleNamespace(articles={
            "1": SimpleNamespace(collection="favorites", file=favorite, title="量子计算", author="甲", source="https://www.zhihu.com/answer/1"),
            "9": SimpleNamespace(collection="author_archive", file=favorite, title="量子计算", author="乙", source="https://www.zhihu.com/answer/9"),
        })
        self.preferences = Preferences(self.corpus)
        self.store = ReadingStore(root / "reading")
        self.gateway = FakeGateway()
        self.session = RecommendationSession(self.corpus, self.preferences, self.store, self.gateway, count=1)

    def entry(self):
        return {"candidate_id": "answer:2", "why": "与量子计算收藏相关", "value": "解释具体原理", "drawback": "短文未提供推导",
                "evidence_quote": "这里是量子计算的具体原文证据。", "favorite_ids": ["1"]}

    def test_only_favorites_and_feed_dedup(self):
        self.assertEqual(set(self.preferences.records), {"1"})
        data = self.session.get_feed()
        self.session.get_feed()
        self.assertEqual(self.gateway.feed_calls, 1)
        self.assertEqual({r["candidate_id"] for r in data["candidates"]}, {"answer:2", "article:3"})
        self.assertEqual(data["candidates"][0]["similar_favorites"][0]["answer_id"], "1")

    def test_question_url_survives_feed_to_cli(self):
        candidate = self.session.get_feed()["candidates"][0]
        self.assertEqual(candidate["question_id"], "42")
        self.assertEqual(candidate["url"], "https://www.zhihu.com/question/42/answer/2")
        response = SimpleNamespace(returncode=0, stdout=json.dumps({"metadata": {"id": "2"}, "content_md": "正文"}), stderr="")
        with patch("zhihu_agent.recommend.subprocess.run", return_value=response) as run:
            self.assertEqual(ZhihuGateway("zhihu.exe").read(candidate), "正文")
        self.assertEqual(run.call_args.args[0], ["zhihu.exe", "browse", "answer", "https://www.zhihu.com/question/42/answer/2", "--json"])

    def test_complete_supplied_url_and_metadata_are_validated(self):
        supplied = "https://www.zhihu.com/question/47400905/answer/2088185177147192109"
        item = {"target_type": "answer", "id": "2088185177147192109", "question_id": "47400905", "url": "https://api.zhihu.com/answers/2088185177147192109"}
        self.assertEqual(candidate_url(item), supplied)
        item.pop("question_id")
        item["url"] = supplied
        self.assertEqual(candidate_url(item), supplied)
        item["url"] = "https://example.com/question/47400905/answer/2088185177147192109"
        self.assertEqual(candidate_url(item), "https://www.zhihu.com/answer/2088185177147192109")
        item["url"] = "https://www.zhihu.com/question/47400905/answer/123"
        self.assertEqual(candidate_url(item), "https://www.zhihu.com/answer/2088185177147192109")

    def test_unread_or_unknown_candidate_cannot_be_recommended(self):
        self.session.get_feed()
        with self.assertRaises(ValueError):
            self.session.read_candidate("answer:999")
        with self.assertRaises(ValueError):
            self.session.submit([self.entry()])
        self.assertEqual(self.gateway.reads, [])

    def test_failed_reads_not_retried(self):
        self.session.get_feed()
        with self.assertRaises(RuntimeError):
            self.session.read_candidate("article:3")
        with self.assertRaises(ValueError):
            self.session.read_candidate("article:3")
        self.assertEqual(self.gateway.reads, ["article:3"])

    def test_full_coverage_quote_and_preference_evidence_required(self):
        self.session.get_feed()
        body = "这里是量子计算的具体原文证据。" + "文" * 9000
        with patch.object(self.gateway, "read", return_value=body):
            page = self.session.read_candidate("answer:2")
        with self.assertRaises(ValueError):
            self.session.submit([self.entry()])
        with self.assertRaises(ValueError):
            self.session.read_candidate("answer:2", page["end"] + 1)
        self.session.read_candidate("answer:2", page["end"])
        with self.assertRaises(ValueError):
            self.session.submit([dict(self.entry(), evidence_quote="这是一段模型编造的引用文字")])
        with self.assertRaises(ValueError):
            self.session.submit([dict(self.entry(), favorite_ids=["9"])])
        self.assertEqual(self.session.submit([self.entry()])["accepted"], 1)

    def test_agent_loop_feedback_and_exclusion(self):
        client = ScriptedClient([
            ("get_recommend_feed", {}), ("read_candidate", {"candidate_id": "answer:2"}),
            ("submit_recommendations", {"items": [self.entry()]}),
        ])
        result = recommend(self.session, client)
        self.assertTrue(result["completed"])
        self.assertEqual(len(result["recommendations"]), 1)
        self.assertTrue(Path(result["report_path"]).is_file())
        self.assertIn("tool", [m["role"] for m in client.received[-1]])
        self.store.feedback("answer:2", "too_shallow", "我想看详细推导")
        self.assertEqual(self.store.events()[-1]["verdict"], "too_shallow")
        next_session = RecommendationSession(self.corpus, self.preferences, self.store, self.gateway)
        self.assertNotIn("answer:2", {r["candidate_id"] for r in next_session.get_feed()["candidates"]})
        with self.assertRaises(ValueError):
            self.store.feedback("answer:999", "like")

    def test_gateway_hides_stderr_and_rejects_mismatched_body(self):
        gateway = ZhihuGateway("zhihu.exe")
        with patch("zhihu_agent.recommend.subprocess.run", return_value=SimpleNamespace(returncode=1, stdout="", stderr="HTTP 403 secret-cookie")):
            with self.assertRaises(RuntimeError) as error:
                gateway.feed(3)
        self.assertNotIn("secret-cookie", str(error.exception))
        with patch.object(gateway, "run", return_value={"metadata": {"id": "999"}, "content_md": "正文"}):
            with self.assertRaises(RuntimeError):
                gateway.read({"kind": "answer", "url": "https://www.zhihu.com/answer/2", "id": "2"})

    def test_empty_submit_requires_reason(self):
        self.session.get_feed()
        with self.assertRaises(ValueError):
            self.session.submit([])
        self.assertEqual(self.session.submit([], "正文不足，无法可靠推荐")["accepted"], 0)

    def test_feed_snapshot_includes_favorites_excluded_from_recommendations(self):
        result = self.session.get_feed()
        snapshot = load_snapshot(Path(result["snapshot_path"]))
        self.assertEqual({item["candidate_id"] for item in snapshot["items"]}, {"answer:1", "answer:2", "article:3"})
        self.assertNotIn("answer:1", {item["candidate_id"] for item in result["candidates"]})
        self.assertEqual(snapshot["items"][1]["url"], "https://www.zhihu.com/question/42/answer/2")
        self.assertEqual(snapshot["fetched_at"], self.session.feed_at)

    def test_failed_feed_is_not_later_treated_as_empty_success(self):
        with patch.object(self.gateway, "feed", side_effect=RuntimeError("HTTP 403")) as request:
            for _ in range(2):
                with self.assertRaises(RuntimeError):
                    self.session.get_feed()
            self.assertEqual(request.call_count, 1)
        with self.assertRaises(ValueError):
            self.session.submit([], "无候选")

    def test_model_failure_keeps_previous_trace_and_candidates(self):
        first = ScriptedClient([("get_recommend_feed", {})])
        with patch.object(first, "chat", side_effect=[first.chat([], []), KimiError("Kimi API returned HTTP 503")]):
            result = recommend(self.session, first)
        self.assertFalse(result["completed"])
        self.assertEqual(result["status"], "model_error")
        self.assertEqual(result["trace"][0]["tool"], "get_recommend_feed")
        self.assertEqual(len(result["candidates"]), 2)
        saved = json.loads(Path(result["report_path"]).read_text(encoding="utf-8"))
        self.assertEqual(saved["model_error"], "Kimi API returned HTTP 503")
        self.assertFalse(saved["model_trace"][-1]["ok"])

    def test_all_body_reads_failed_is_incomplete_not_success(self):
        client = ScriptedClient([
            ("get_recommend_feed", {}),
            ("read_candidate", {"candidate_id": "article:3"}),
            ("submit_recommendations", {"items": [], "reason": "正文不可用"}),
        ])
        result = recommend(self.session, client)
        self.assertFalse(result["completed"])
        self.assertEqual(result["status"], "source_unavailable")
        self.assertTrue(result["failed_reads"])

    def test_no_matching_content_is_distinct_from_source_failure(self):
        client = ScriptedClient([
            ("get_recommend_feed", {}),
            ("submit_recommendations", {"items": [], "reason": "没有感兴趣的主题"}),
        ])
        result = recommend(self.session, client)
        self.assertTrue(result["completed"])
        self.assertEqual(result["status"], "no_matches")


if __name__ == "__main__":
    unittest.main()
