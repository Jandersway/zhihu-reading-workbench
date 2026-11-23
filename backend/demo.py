"""Deterministic dependencies for running the actual workflow without network or secrets."""
import json
from dataclasses import replace
from tempfile import TemporaryDirectory
from pathlib import Path
from types import SimpleNamespace

from zhihu_agent.agent import ZhihuAgent
from zhihu_agent.config import Settings
from zhihu_agent.corpus import Corpus
from zhihu_agent.recommend import Preferences, ReadingStore, RecommendationSession
from zhihu_agent.runtime_skills import load_skill
from zhihu_agent.workflow import recommend_workflow

TITLE = '如何给 Agent 的工具调用加上证据校验？'
BODY_QUOTE = '先校验引用是否逐字出现在正文中，再把语义判断交给评审阶段。'
DEMO_BODY = BODY_QUOTE + '当文本存在但论据不足时，评审应拒绝草稿。示例只讨论文本，没有涉及图片和表格。'
FAVORITE_QUOTE = 'Agent 工具调用需要明确输入边界和证据校验。'
CANDIDATE = 'article:2'
RESEARCH_QUESTION = '工具调用的证据校验应该分哪两步？'


class DemoGateway:
    def feed(self, limit):
        return [
            {'target_type': 'article', 'id': '3', 'title': '周末怎么安排独处时间？',
             'excerpt': '从读书、做饭和散步开始。'},
            {'target_type': 'article', 'id': '2', 'title': TITLE,
             'excerpt': '用 Python 状态机控制 Agent 工具调用和文章证据校验，再加入独立评审步骤。'},
            {'target_type': 'article', 'id': '4', 'title': '从零认识知识图谱的节点和关系',
             'excerpt': '介绍本体、实体与关系的基本含义。'},
        ][:limit]

    def read(self, item):
        if item['candidate_id'] != CANDIDATE:
            raise ValueError('Demo body unavailable')
        return DEMO_BODY


class DemoClient:
    def chat(self, messages, tools):
        context = json.loads(messages[1]['content'])
        names = {tool['function']['name'] for tool in tools}
        if 'review_recommendations' in names:
            name = 'review_recommendations'
            arguments = {'approved': True, 'issues': [], 'checks': [
                {'candidate_id': packet['candidate_id'], 'evidence_id': packet['evidence_id'],
                 'claim_support': 'supported', 'preference_match': 'specific',
                 'explanation': '演示脚本按固定材料返回，正文和示例收藏都涉及工具调用的证据校验。'}
                for packet in context['evidence_packets']]}
        elif 'propose_recommendations' in names and BODY_QUOTE in context['read_bodies'].get(CANDIDATE, {}).get('text', ''):
            name = 'propose_recommendations'
            arguments = {'items': [{'candidate_id': CANDIDATE,
                'why': '把原文引用的程序校验与语义评审拆成两个步骤。',
                'value': '可以用逐字校验拦截不存在的引用，再检查推论与原文是否一致。',
                'drawback': '只讨论文本，未覆盖图文资料。', 'evidence_quote': BODY_QUOTE,
                'favorite_ids': ['1'], 'preference_quote': FAVORITE_QUOTE,
                'preference_reason': '候选与示例收藏都把证据校验用于约束工具调用。'}]}
        elif 'read_candidate' in names:
            name, arguments = 'read_candidate', {'candidate_id': CANDIDATE}
        else:
            name = 'set_reading_plan'
            arguments = {'steps': [{'candidate_id': CANDIDATE, 'purpose': '阅读并核对证据校验的实现步骤'}],
                         'criteria': ['原文引用可定位', '收藏关联有具体主题'], 'reason': '演示脚本固定阅读目标候选'}
        return {'choices': [{'message': {'role': 'assistant', 'content': None, 'tool_calls': [{
            'id': name, 'type': 'function', 'function': {'name': name, 'arguments': json.dumps(arguments)}
        }]}}]}


def run_demo(focus, on_progress):
    scratch = Path(__file__).resolve().parents[1] / '.runtime'
    scratch.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix='demo-', dir=scratch) as temp:
        root = Path(temp)
        favorite = root / 'favorite.md'
        favorite.write_text(FAVORITE_QUOTE, encoding='utf-8')
        corpus = SimpleNamespace(articles={'1': SimpleNamespace(collection='favorites', file=favorite,
            title='Agent 工具调用及证据校验', author='演示资料', source='https://www.zhihu.com/answer/1')})
        session = RecommendationSession(corpus, Preferences(corpus), ReadingStore(root / 'reading'),
                                        DemoGateway(), count=1)
        return recommend_workflow(session, DemoClient(), focus, on_progress=on_progress,
                                  skill=load_skill('personalized-reading', mode='recommend'))


class DemoResearchClient:
    def chat(self, messages, tools):
        observations = [row for row in messages if row['role'] == 'tool']
        if not observations:
            if messages[-1]['content'] != RESEARCH_QUESTION:
                return {'choices': [{'message': {'content': ''}}]}
            name, arguments = 'search_answers', {'query': '工具调用 证据校验'}
        else:
            result = json.loads(observations[-1]['content'])
            if 'text' in result:
                answer = ('第一步做可程序化的检查，逐字定位原文引文。第二步判断引文能否支持结论，'
                          '这需要语义评审。示例资料只涵盖文本证据 [S1]。')
                return {'choices': [{'message': {'content': answer}}]}
            if not result.get('results'):
                return {'choices': [{'message': {'content': ''}}]}
            name, arguments = 'read_answer', {'answer_id': result['results'][0]['answer_id']}
        return {'choices': [{'message': {'content': None, 'tool_calls': [{
            'id': name, 'type': 'function', 'function': {'name': name, 'arguments': json.dumps(arguments)}
        }]}}]}


def research_demo(question):
    scratch = Path(__file__).resolve().parents[1] / '.runtime'
    scratch.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix='research-', dir=scratch) as temp:
        root = Path(temp)
        answers = root / 'answers'
        answers.mkdir()
        (answers / '2.md').write_text('---\nsource: https://www.zhihu.com/answer/2\n'
            f'title: {TITLE}\nauthor: 演示资料\n---\n{BODY_QUOTE}这个示例只涵盖文本证据。\n', encoding='utf-8')
        corpus = Corpus(replace(Settings.from_env(), project_root=root, data_dir=answers))
        return ZhihuAgent(corpus, DemoResearchClient(), skill=load_skill('source-grounded-research', mode='ask')).ask(question)
