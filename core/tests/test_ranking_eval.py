import io
import json
from contextlib import redirect_stdout
from math import log2
import unittest
from unittest.mock import patch

import test_recommend as fixtures
from test_workflow import plan
from zhihu_agent.cli import main
from zhihu_agent.ranking import METHOD, rank_candidates
from zhihu_agent.ranking_eval import evaluation_case, evaluate_ranking, rating_template, summarize_pairs
from zhihu_agent.workflow import ReadingWorkflow, WorkflowLimits


def example_report():
    return {
        'run_id': '20261112T010000Z-12345678',
        'focus': 'Agent 工具调用',
        'completed': True,
        'candidates': [
            {'candidate_id': 'answer:1', 'title': '通勤路线'},
            {'candidate_id': 'answer:2', 'title': 'Agent 工具调用'},
            {'candidate_id': 'answer:3', 'title': '软件工程', 'excerpt': '用 Agent 辅助写测试'},
        ],
        'recommendations': [{'candidate_id': 'answer:2'}],
    }


class RankingEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.report = example_report()
        self.case = evaluation_case(self.report)
        self.labels = rating_template(self.case)
        self.labels.update(annotator='test')
        grades = {'answer:1': 0, 'answer:2': 2, 'answer:3': 1}
        for item in self.labels['items']:
            item['grade'] = grades[item['candidate_id']]

    def test_rank_uses_focus_and_preserves_home_order_without_focus(self):
        self.assertEqual(self.case['rankings'][METHOD], ['answer:2', 'answer:3', 'answer:1'])
        self.assertEqual([row['candidate_id'] for row in rank_candidates(self.report['candidates'], '')],
                         ['answer:1', 'answer:2', 'answer:3'])

    def test_paired_ranking_metrics_match_hand_calculation(self):
        row = evaluate_ranking(self.case, self.labels, 2)
        ideal = 3 + 1 / log2(3)
        self.assertAlmostEqual(row['arms']['homepage']['ndcg_at_k'], (3 / log2(3)) / ideal)
        self.assertEqual(row['arms'][METHOD]['ndcg_at_k'], 1)
        self.assertEqual(row['arms'][METHOD]['precision_at_k'], 0.5)
        self.assertEqual(row['arms'][METHOD]['recall_at_k_in_pool'], 1)
        self.assertEqual(row['arms']['recorded_agent']['coverage_at_k'], 0.5)
        self.assertEqual(row['arms']['recorded_agent']['precision_at_k'], 0.5)
        self.assertEqual(summarize_pairs([row])['metrics']['ndcg_at_k']['wins'], 1)

    def test_unknown_labels_and_no_positive_pool_do_not_become_negatives(self):
        next(item for item in self.labels['items'] if item['candidate_id'] == 'answer:2')['grade'] = None
        row = evaluate_ranking(self.case, self.labels, 1)
        self.assertEqual(row['labeled_count'], 2)
        self.assertIsNone(row['arms'][METHOD]['precision_at_k'])
        self.assertIsNone(row['arms']['homepage']['ndcg_at_k'])
        for item in self.labels['items']:
            item['grade'] = 0
        row = evaluate_ranking(self.case, self.labels)
        self.assertIsNone(row['arms'][METHOD]['recall_at_k_in_pool'])
        self.assertIsNone(row['arms'][METHOD]['ndcg_at_k'])
        self.assertEqual(row['arms'][METHOD]['precision_at_k'], 0)

    def test_changed_goal_or_candidate_snapshot_invalidates_labels(self):
        other = evaluation_case(self.report, '不同的阅读目标')
        self.assertNotIn('recorded_agent', other['rankings'])
        with self.assertRaises(ValueError):
            evaluate_ranking(other, self.labels)
        self.report['candidates'][0]['excerpt'] = 'changed'
        with self.assertRaises(ValueError):
            evaluate_ranking(evaluation_case(self.report), self.labels)

    def test_metrics_do_not_pool_ai_and_user_labels(self):
        row = evaluate_ranking(self.case, self.labels)
        with self.assertRaises(ValueError):
            summarize_pairs([row, dict(row, label_source='ai_proxy', case_id='other')])

    def test_cli_creates_labeling_form_without_model_or_corpus(self):
        buffer = io.StringIO()
        with patch('sys.argv', ['zhihu-agent', 'rank-eval']), redirect_stdout(buffer), \
                patch('zhihu_agent.cli.ReadingStore.report_by_id', return_value=self.report), \
                patch('zhihu_agent.cli.KimiClient', side_effect=AssertionError('unexpected Kimi')), \
                patch('zhihu_agent.cli.Corpus', side_effect=AssertionError('unexpected corpus')):
            main()
        result = json.loads(buffer.getvalue())
        self.assertEqual(result['case_id'], self.case['case_id'])
        self.assertNotIn('rankings', result)
        self.assertTrue(all(item['grade'] is None for item in result['items']))


class PlanningRankingTests(unittest.TestCase):
    setUp = fixtures.RecommendationTests.setUp

    def test_planner_sees_ranked_pool_and_report_keeps_homepage_baseline(self):
        client = fixtures.ScriptedClient([plan()])
        result = ReadingWorkflow(self.session, client, focus='量子计算新文章',
                                 limits=WorkflowLimits(model_calls=1)).run()
        sent = json.loads(client.received[0][1]['content'])['candidates']
        self.assertEqual([row['candidate_id'] for row in sent], ['article:3', 'answer:2'])
        self.assertEqual(sent[0]['ranking_hint']['home_rank'], 2)
        self.assertEqual(result['home_candidate_ids'], ['answer:2', 'article:3'])
        self.assertEqual(self.gateway.feed_calls, 1)
        self.assertEqual(self.gateway.reads, [])
