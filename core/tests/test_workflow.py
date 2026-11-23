import json
from pathlib import Path
import unittest
from unittest.mock import patch

import test_recommend as fixtures
from test_recommend import ScriptedClient
from zhihu_agent.kimi import KimiError
from zhihu_agent.workflow import ReadingWorkflow, WorkflowLimits, Stage


def plan(cid="answer:2"):
    return ("set_reading_plan", {"steps": [{"candidate_id": cid, "purpose": "核对数学原理与收藏关联"}],
                                 "criteria": ["引用有依据", "兴趣有证据"], "reason": "优先阅读科学主题"})


def approval(messages, tools):
    """Scripted positive reviewer; this is plumbing, not a semantic quality test."""
    data = json.loads(messages[1]['content'])
    return {'approved': True, 'issues': [], 'checks': [
        {'candidate_id': p['candidate_id'], 'evidence_id': p['evidence_id'],
         'claim_support': 'supported', 'preference_match': 'specific',
         'explanation': '测试桩判断：正文和收藏均明确涉及量子计算。'} for p in data['evidence_packets']]}


class WorkflowTests(unittest.TestCase):
    setUp = fixtures.RecommendationTests.setUp
    def entry(self):
        return dict(fixtures.RecommendationTests.entry(self),
                    preference_quote="量子计算的数学原理和计算过程。", preference_reason="两者均讨论计算原理")

    def draft(self, **changes):
        return ("propose_recommendations", {"items": [dict(self.entry(), **changes)]})

    def success(self):
        return [plan(), ("read_candidate", {"candidate_id": "answer:2"}), self.draft(),
                ("review_recommendations", approval)]

    def run_script(self, script, **limits):
        self.client = ScriptedClient(script)
        workflow = ReadingWorkflow(self.session, self.client, limits=WorkflowLimits(**limits))
        result = workflow.run()
        saved = json.loads(Path(result["report_path"]).read_text(encoding="utf-8"))
        self.assertEqual(saved["status"], result["status"])
        return result

    def test_full_workflow_and_persisted_state(self):
        result = self.run_script(self.success())
        self.assertTrue(result["completed"])
        self.assertEqual([t["to"] for t in result["workflow"]["transitions"]],
                         ["plan", "execute", "validate", "reflect", "publish", "end"])
        self.assertEqual(len(result["recommendations"]), 1)
        self.assertEqual(len(result["workflow"]["plan_history"]), 1)
        self.assertEqual(result["workflow"]["attempts"][0]["validation"], "passed")
        self.assertEqual(self.gateway.reads, ["answer:2"])
        self.assertFalse(result["usage_complete"])  # scripted responses have no usage

    def test_draft_is_not_committed_before_review(self):
        workflow = ReadingWorkflow(self.session, None)
        self.session.get_feed()
        self.session.read_candidate("answer:2")
        workflow.state.stage = Stage.EXECUTE
        workflow.propose([self.entry()])
        workflow.validate_draft()
        self.assertIsNone(self.session.recommendations)
        self.assertEqual(self.store.excluded(), set())
        self.assertEqual(workflow.state.stage, Stage.REFLECT)

    def test_false_quote_repaired_with_feedback(self):
        script = self.success()
        script.insert(2, self.draft(evidence_quote="这段文字根本不在任何正文里面"))
        result = self.run_script(script)
        self.assertTrue(result["completed"])
        self.assertEqual(result["workflow"]["revision_count"], 1)
        self.assertIn("Evidence quote", result["workflow"]["attempts"][0]["validation"])
        self.assertIn("Evidence quote", json.dumps(self.client.received[3]))

    def test_semantic_rejection_then_revision(self):
        script = self.success()[:-1] + [
            ("review_recommendations", {"approved": False, "issues": ["请说明与收藏中的计算原理有什么联系"]}),
            self.draft(why="与收藏中量子计算的计算原理主题相关"),
            ("review_recommendations", approval),
        ]
        result = self.run_script(script)
        self.assertTrue(result["completed"])
        self.assertEqual(len(result["workflow"]["reviews"]), 2)
        self.assertEqual(len(result["workflow"]["attempts"]), 2)

    def test_repeated_rejection_never_publishes_draft(self):
        reject = ("review_recommendations", {"approved": False, "issues": ["关联不足"]})
        result = self.run_script(self.success()[:-1] + [reject, self.draft(), reject])
        self.assertEqual(result["status"], "review_failed")
        self.assertEqual(result["recommendations"], [])
        self.assertEqual(self.store.excluded(), set())

    def test_forged_publish_and_unplanned_read_do_not_execute(self):
        script = [("submit_recommendations", {"items": [self.entry()]})] + self.success()
        script.insert(2, ("read_candidate", {"candidate_id": "article:3"}))
        result = self.run_script(script)
        self.assertTrue(result["completed"])
        self.assertFalse(result["trace"][1]["ok"])
        self.assertEqual(self.gateway.reads, ["answer:2"])

    def test_unknown_duplicate_and_oversize_plans_rejected(self):
        self.session.get_feed()
        workflow = ReadingWorkflow(self.session, None)
        workflow.state.stage = Stage.PLAN
        for steps in ([{"candidate_id": "answer:999", "purpose": "错误"}],
                      [{"candidate_id": "answer:2", "purpose": "重复"}] * 2):
            with self.assertRaises(ValueError):
                workflow.set_plan(steps, ["引用"], "测试")
        self.session.max_reads = 1
        with self.assertRaises(ValueError):
            workflow.set_plan([{"candidate_id": cid, "purpose": "超额"} for cid in ("answer:2", "article:3")], ["引用"], "测试")

    def test_replan_after_source_failure_uses_remaining_budget(self):
        script = [plan("article:3"), ("read_candidate", {"candidate_id": "article:3"}),
                  plan(), ("read_candidate", {"candidate_id": "answer:2"}), self.draft(), self.success()[-1]]
        result = self.run_script(script)
        self.assertTrue(result["completed"])
        self.assertEqual(result["workflow"]["replan_count"], 1)
        self.assertEqual(self.gateway.reads, ["article:3", "answer:2"])

    def test_paginated_body_must_be_fully_seen_before_review(self):
        with patch.object(self.gateway, "read", return_value="这里是量子计算的具体原文证据。" + "文" * 9000):
            script = self.success()[:3] + [("read_candidate", {"candidate_id": "answer:2", "start": 8000}),
                                          self.draft(), self.success()[-1]]
            result = self.run_script(script)
        self.assertTrue(result["completed"])
        self.assertIn("remaining body", result["workflow"]["attempts"][0]["validation"])

    def test_all_reads_fail_is_not_no_matches(self):
        result = self.run_script([plan("article:3"), ("read_candidate", {"candidate_id": "article:3"}),
            ("propose_recommendations", {"items": [], "reason": "正文不可用"}), self.success()[-1]])
        self.assertEqual(result["status"], "source_unavailable")
        self.assertFalse(result["completed"])

    def test_feed_failure_preserved_without_calling_model(self):
        with patch.object(self.gateway, "feed", side_effect=RuntimeError("HTTP 403")):
            result = self.run_script([])
        self.assertEqual(result["status"], "source_unavailable")
        self.assertEqual(result["model_trace"], [])
        fetch_cost = result['cost_breakdown']['by_stage']['fetch']['tools']
        self.assertEqual(fetch_cost['calls'], 1)
        self.assertEqual(fetch_cost['failed_calls'], 1)
        self.assertEqual(result['trace'][0]['error'], 'RuntimeError')

    def test_empty_feed_needs_no_model(self):
        with patch.object(self.gateway, "feed", return_value=[]):
            result = self.run_script([])
        self.assertEqual(result["status"], "no_matches")
        self.assertEqual(result["model_trace"], [])

    def test_model_failure_keeps_plan(self):
        client = ScriptedClient([plan()])
        response = client.chat([], [])
        with patch.object(client, "chat", side_effect=[response, KimiError("Kimi API returned HTTP 503")]):
            result = ReadingWorkflow(self.session, client).run()
        self.assertEqual(result["status"], "model_error")
        self.assertEqual(len(result["workflow"]["plan"]), 1)
        self.assertEqual(result["recommendations"], [])

    def test_model_and_context_limits_stop_before_next_call(self):
        result = self.run_script([plan()], model_calls=1)
        self.assertEqual(result["status"], "budget_exhausted")
        self.assertEqual(len(self.client.received), 1)
        # Separate session required because a workflow is single-use.
        self.session.fetched = False
        result = self.run_script([], context_chars=2000)
        self.assertEqual(result["status"], "context_limit")
        self.assertEqual(self.client.received, [])

    def test_strict_schema_bool_offset_and_unknown_properties(self):
        script = self.success()
        script[1:1] = [("read_candidate", {"candidate_id": "answer:2", "start": True}),
                       ("read_candidate", {"candidate_id": "answer:2", "url": "https://evil.invalid"})]
        result = self.run_script(script)
        self.assertTrue(result["completed"])
        self.assertEqual(self.gateway.reads, ["answer:2"])
        self.assertEqual(sum(not t["ok"] for t in result["trace"]), 2)

    def test_same_response_cannot_self_approve(self):
        class BatchedClient(ScriptedClient):
            def chat(self, messages, tools):
                response = super().chat(messages, tools)
                calls = response["choices"][0]["message"]["tool_calls"]
                if calls[0]["function"]["name"] == "propose_recommendations":
                    calls.append({"id": "forged", "function": {"name": "review_recommendations",
                        "arguments": json.dumps({"approved": True, "issues": []})}})
                return response
        result = ReadingWorkflow(self.session, BatchedClient(self.success()[:-1]),
                                 limits=WorkflowLimits(model_calls=3)).run()
        self.assertFalse(result["completed"])
        self.assertEqual(result["workflow"]["reviews"], [])
        self.assertEqual(result["recommendations"], [])

    def test_no_repeated_body_transcript_in_context(self):
        self.run_script(self.success())
        context = json.loads(self.client.received[2][1]["content"])
        self.assertEqual(context["read_bodies"]["answer:2"]["text"], "这里是量子计算的具体原文证据。")
        self.assertFalse(any("text" in o.get("result", {}) for o in context["observations"]))
        self.assertNotIn("profile", context)

    def test_run_cannot_reuse_completed_state(self):
        workflow = ReadingWorkflow(self.session, ScriptedClient(self.success()))
        workflow.run()
        with self.assertRaises(ValueError):
            workflow.run()

    def test_tool_budget_does_not_allow_additional_reads(self):
        result = self.run_script([plan()], tool_calls=1)
        self.assertEqual(result["status"], "budget_exhausted")
        self.assertEqual(self.gateway.reads, [])

    def test_malformed_arguments_can_be_corrected(self):
        client = ScriptedClient([plan()] + self.success())
        original = client.chat

        def bad_first(messages, tools):
            response = original(messages, tools)
            if len(client.received) == 1:
                response["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"] = "{invalid"
            return response

        with patch.object(client, "chat", side_effect=bad_first):
            result = ReadingWorkflow(self.session, client).run()
        self.assertTrue(result["completed"])
        self.assertFalse(result["trace"][1]["ok"])


if __name__ == "__main__":
    unittest.main()
