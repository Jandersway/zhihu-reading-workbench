"""Offline tabular Q-learning over a simulated reading workflow."""
from collections import defaultdict
from dataclasses import dataclass
from functools import lru_cache, partial
import json
from random import Random

ACTION_LABELS = {
    'read_1': '读候选 1', 'read_2': '读候选 2',
    'verify_1': '校验候选 1', 'verify_2': '校验候选 2',
    'publish': '发布推荐', 'stop': '停止并说明证据不足',
}
READ_COST = .07
VERIFY_COST = .03
MAX_STEPS = 5


@dataclass(frozen=True)
class Source:
    body: str | None
    quote: str
    supports_goal: bool


@dataclass(frozen=True)
class Case:
    topic: str
    pattern: str
    sources: tuple[Source, Source]


def cases_for(topics, patterns):
    cases = []
    for topic in topics:
        quote = f'{topic}应在运行前验证依赖是否满足。'
        supported = Source(f'实践指南。{quote}验证后再继续。', quote, True)
        irrelevant = Source('本文只介绍工作台的界面配色。', '本文只介绍工作台的界面配色。', False)
        missing_quote = Source('可用资料只包含目录。', quote, False)
        unavailable = Source(None, '', False)
        variants = {
            'first_supported': (supported, irrelevant),
            'second_supported': (irrelevant, supported),
            'first_unavailable': (unavailable, supported),
            'first_missing_quote': (missing_quote, supported),
            'both_supported': (supported, supported),
            'no_support': (irrelevant, missing_quote),
            'no_source': (unavailable, unavailable),
            'second_missing_quote': (supported, missing_quote),
        }
        cases.extend(Case(topic, name, variants[name]) for name in patterns)
    return tuple(cases)


TRAIN = cases_for(('任务队列', '向量检索', '引用生成'), (
    'first_supported', 'second_supported', 'first_unavailable', 'first_missing_quote',
    'both_supported', 'no_support', 'no_source', 'second_missing_quote',
))
TEST = cases_for(('模型路由', '文档索引', '日志追踪'), (
    'first_supported', 'second_supported', 'first_unavailable',
    'both_supported', 'no_support', 'second_missing_quote',
))


def allowed(state):
    actions = []
    for index, status in enumerate(state, 1):
        if status == 'unread':
            actions.append(f'read_{index}')
        elif status == 'has_quote':
            actions.append(f'verify_{index}')
    if 'verified' in state:
        actions.append('publish')
    return (*actions, 'stop')


def take_step(case, state, action):
    if action not in allowed(state):
        raise ValueError('Action is unavailable in this state')
    if action == 'publish':
        return state, 1.0, 'published'
    if action == 'stop':
        can_answer = any(source.supports_goal for source in case.sources)
        return state, -.6 if can_answer else .4, 'missed' if can_answer else 'abstained'
    kind, number = action.split('_')
    index = int(number) - 1
    source = case.sources[index]
    next_state = list(state)
    if kind == 'read':
        if not source.body:
            next_state[index] = 'unavailable'
        elif not source.quote or source.quote not in source.body:
            next_state[index] = 'missing_quote'
        else:
            next_state[index] = 'has_quote'
        return tuple(next_state), -READ_COST, None
    # An exact quote alone cannot prove support. The simulator uses a fixture label.
    next_state[index] = 'verified' if source.supports_goal else 'rejected'
    return tuple(next_state), -VERIFY_COST, None


def rollout(case, choose, update=None):
    state = ('unread', 'unread')
    trajectory = []
    outcome = None
    for _ in range(MAX_STEPS):
        action = choose(state)
        after, reward, outcome = take_step(case, state, action)
        trajectory.append({'before': state, 'action': action, 'after': after, 'reward': reward})
        if update:
            update(state, action, after, reward, bool(outcome))
        state = after
        if outcome:
            break
    if outcome is None:
        raise AssertionError('Reading episode exceeded its action budget')
    return outcome, trajectory


def train(seed, episodes):
    rng = Random(seed)
    values = defaultdict(float)

    def greedy(state):
        return max(allowed(state), key=lambda action: values[state, action])

    def explore(state):
        return rng.choice(allowed(state)) if rng.random() < .2 else greedy(state)

    def update(state, action, after, reward, terminal):
        future = 0 if terminal else max(values[after, choice] for choice in allowed(after))
        key = state, action
        # Terminal reward propagates back to earlier read / verify actions.
        values[key] += .08 * (reward + future - values[key])

    for _ in range(episodes):
        rollout(rng.choice(TRAIN), explore, update)
    return greedy


def rule(state, *, single=False, exhaustive=False):
    actions = allowed(state)
    if exhaustive:
        for action in ('read_1', 'read_2'):
            if action in actions:
                return action
    if 'publish' in actions:
        return 'publish'
    for action in ('verify_1', 'read_1', 'verify_2', 'read_2'):
        if action in actions and (not single or action.endswith('_1')):
            return action
    return 'stop'


def measure(policy):
    results = [(case, *rollout(case, policy)) for case in TEST]
    denominator = len(results)
    return {
        'mean_reward': round(sum(step['reward'] for _, _, steps in results for step in steps) / denominator, 3),
        'published': sum(outcome == 'published' for _, outcome, _ in results),
        'correct_abstentions': sum(outcome == 'abstained' for _, outcome, _ in results),
        'missed': sum(outcome == 'missed' for _, outcome, _ in results),
        'avg_reads': round(sum(step['action'].startswith('read_') for _, _, steps in results for step in steps) / denominator, 3),
        'avg_verifications': round(sum(step['action'].startswith('verify_') for _, _, steps in results for step in steps) / denominator, 3),
    }


@lru_cache(maxsize=1)
def experiment():
    seed, episodes = 2026, 3000
    learned = train(seed, episodes)
    strategies = (
        ('首篇规则', partial(rule, single=True)),
        ('全部先读', partial(rule, exhaustive=True)),
        ('失败时回退规则', rule),
        ('Q-learning', learned),
    )
    traces = [(case, *rollout(case, learned)) for case in TEST]
    example, outcome, steps = max(traces, key=lambda row: (row[1] == 'published', len(row[2])))
    return {
        'version': 'reading-policy-v1', 'seed': seed, 'training_episodes': episodes,
        'training_cases': len(TRAIN), 'test_cases': len(TEST),
        'reward': {'publish': 1.0, 'abstain': .4, 'miss': -.6, 'read': -READ_COST, 'verify': -VERIFY_COST},
        'strategies': [dict(name=name, **measure(policy)) for name, policy in strategies],
        'seeds': [dict(seed=current, **measure(learned if current == seed else train(current, episodes)))
                  for current in (2026, 2027, 2028, 2029, 2030)],
        'example': {'topic': example.topic, 'outcome': outcome,
                    'steps': [dict(row, label=ACTION_LABELS[row['action']]) for row in steps]},
    }


if __name__ == '__main__':
    print(json.dumps(experiment(), ensure_ascii=False, indent=2))
