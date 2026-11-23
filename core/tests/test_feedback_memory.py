import json
from unittest import TestCase

import test_recommend as fixtures
from test_recommend import ScriptedClient
from zhihu_agent.feedback_memory import FeedbackMemory, active_feedback, evidence_window, feedback_metrics
from zhihu_agent.reading_review import rate, review
from zhihu_agent.recommend import recommend
from zhihu_agent.workflow import ReadingWorkflow, Stage


class FeedbackMemoryTests(TestCase):
    setUp = fixtures.RecommendationTests.setUp
    entry = fixtures.RecommendationTests.entry

    def saved_report(self):
        return recommend(self.session, ScriptedClient([
            ("get_recommend_feed", {}), ("read_candidate", {"candidate_id": "answer:2"}),
            ("submit_recommendations", {"items": [self.entry()]}),
        ]))

    def test_window_keeps_tail_evidence_as_exact_excerpt(self):
        body = "无关背景。" * 140 + "工具调用失败应返回错误原因，并限制重试次数。" * 8
        result = evidence_window(body, {"工具": 1, "重试": 1, "失败": 1})
        self.assertNotIn("工具", body[:700])
        self.assertIn("工具", result)
        self.assertIn(result, body)
        self.assertEqual(len(result), 700)

    def test_window_never_loses_weighted_query_coverage_against_same_width_prefix(self):
        for position in range(0, 1000, 57):
            body = "甲" * position + "工具调用重试" + "乙" * (1000 - position)
            weights = {"工具": 3, "调用": 1, "重试": 2}
            score = lambda text: sum(w for term, w in weights.items() if term in text)
            self.assertGreaterEqual(score(evidence_window(body, weights)), score(body[:700]))

    def test_rate_by_number_and_scope_are_saved(self):
        report = self.saved_report()
        result = rate(self.store, 1, "dislike", run_id=report["run_id"], scope="topic", topic="量子计算", note="最近少看")
        self.assertEqual(result["candidate_id"], "answer:2")
        event = self.store.events()[0]
        self.assertEqual(event["scope"], "topic")
        self.assertEqual(event["source"], "user")
        with self.assertRaises(ValueError):
            rate(self.store, 2, "like")
        with self.assertRaises(ValueError):
            rate(self.store, 1, "dislike", scope="topic")

    def test_latest_rating_replaces_prior_signal_and_clear_removes_it(self):
        self.saved_report()
        rate(self.store, 1, "dislike")
        rate(self.store, 1, "like", note="补看正文后觉得不错")
        self.assertEqual([e["verdict"] for e in active_feedback(self.store.events())], ["like"])
        rate(self.store, 1, "clear")
        self.assertEqual(active_feedback(self.store.events()), [])
        self.assertIn("answer:2", self.store.excluded())  # still previously recommended

    def test_related_feedback_not_last_ten_noise(self):
        events = [{"candidate_id": "answer:2", "title": "量子计算原理", "verdict": "too_shallow", "note": "要推导"}]
        events += [{"candidate_id": str(i), "title": "烹饪红烧肉", "verdict": "like"} for i in range(12)]
        memory = FeedbackMemory(events)
        matches = memory.recall("量子计算如何纠错")
        self.assertEqual(matches[0]["candidate_id"], "answer:2")
        self.assertEqual(matches[0]["scope"], "article")
        self.assertEqual(memory.recall("汽车发动机"), [])

    def test_article_dislike_does_not_become_topic_rule(self):
        event = {"candidate_id": "answer:2", "title": "量子计算基础", "verdict": "dislike", "scope": "article"}
        recalled = FeedbackMemory([event]).recall("量子计算原理")
        self.assertEqual(recalled[0]["scope"], "article")
        self.assertEqual(recalled[0]["topic"], "")

    def test_explicit_topic_can_recall_across_different_original_title(self):
        event = {"candidate_id": "answer:2", "title": "一些随笔", "verdict": "dislike", "scope": "topic", "topic": "AI"}
        self.assertTrue(FeedbackMemory([event]).recall("AI工程实践"))
        self.assertFalse(FeedbackMemory([event]).recall("fair play"))

    def test_workflow_receives_relevant_feedback_and_records_usage(self):
        self.saved_report()
        rate(self.store, 1, "too_shallow", note="想看具体数学推导")
        workflow = ReadingWorkflow(self.session, None)
        workflow.state.stage = Stage.PLAN
        context = json.loads(workflow.context()[1]["content"])
        row = next(x for x in context["candidates"] if x["candidate_id"] == "answer:2")
        self.assertEqual(row["related_user_feedback"][0]["verdict"], "too_shallow")
        self.assertIn("answer:2", workflow.feedback_used)

    def test_fake_favorite_quote_rejected(self):
        self.session.get_feed()
        self.session.read_candidate("answer:2")
        with self.assertRaises(ValueError):
            self.session.validate([dict(self.entry(), preference_quote="不存在的收藏原文引用文字", preference_reason="说不通")])

    def test_no_ratings_means_unknown_not_zero_quality(self):
        report = self.saved_report()
        self.assertIsNone(feedback_metrics([report], [])["like_rate_among_rated"])
        rate(self.store, 1, "seen")
        self.assertIsNone(self.store.feedback_summary()["like_rate_among_rated"])
        rate(self.store, 1, "like")
        self.assertEqual(self.store.feedback_summary()["like_rate_among_rated"], 1)

    def test_interactive_skip_and_quit_do_not_write_preferences(self):
        self.saved_report()
        review(self.store, read_input=lambda _: "", write=lambda _: None)
        self.assertEqual(self.store.events(), [])

    def test_interactive_feedback_pinned_to_report(self):
        report = self.saved_report()
        responses = iter(["invalid", "3", "需要具体案例"])
        review(self.store, read_input=lambda _: next(responses), write=lambda _: None)
        self.assertEqual(self.store.events()[0]["run_id"], report["run_id"])
        self.assertEqual(self.store.events()[0]["verdict"], "too_shallow")

    def test_report_path_injection_rejected(self):
        with self.assertRaises(ValueError):
            self.store.report_by_id("../../secret")
