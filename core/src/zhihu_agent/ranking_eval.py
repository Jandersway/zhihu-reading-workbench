"""Paired offline evaluation on a fixed, eligible homepage candidate pool."""
from hashlib import sha256
import json
from math import log2
import random

from .ranking import METHOD, rank_candidates


def evaluation_case(report: dict, focus: str | None = None) -> dict:
    original_goal = report.get('focus') or ''
    goal = (original_goal if focus is None else focus)[:1000].strip()
    candidates = [{key: row.get(key, '') for key in ('candidate_id', 'title', 'excerpt', 'url')}
                  for row in report.get('candidates', [])]
    ids = [row['candidate_id'] for row in candidates]
    if not ids or any(not cid for cid in ids) or len(set(ids)) != len(ids):
        raise ValueError('Run must have a nonempty, unique eligible candidate pool')
    home_ids = report.get('home_candidate_ids', ids)
    if len(home_ids) != len(ids) or set(home_ids) != set(ids):
        raise ValueError('Saved homepage order must cover exactly the same candidate pool')
    by_id = {row['candidate_id']: row for row in candidates}
    candidates = [by_id[cid] for cid in home_ids]
    case = {'run_id': report['run_id'], 'focus': goal, 'candidates': candidates}
    case['case_id'] = sha256(json.dumps(case, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()
    case['home_order_source'] = 'explicit' if 'home_candidate_ids' in report else 'archived_eligible_candidate_order'
    case['focus_scores'] = rank_candidates(candidates, goal)
    case['rankings'] = {'homepage': home_ids,
                        METHOD: [row['candidate_id'] for row in case['focus_scores']]}
    agent_ids = [row['candidate_id'] for row in report.get('recommendations', [])]
    if (report.get('completed') and goal == original_goal.strip()
            and len(agent_ids) == len(set(agent_ids)) and set(agent_ids) <= set(ids)):
        case['rankings']['recorded_agent'] = agent_ids
    return case


def rating_template(case: dict) -> dict:
    items = [dict(row, grade=None) for row in case['candidates']]
    random.Random(case['case_id']).shuffle(items)
    return {'case_id': case['case_id'], 'run_id': case['run_id'], 'focus': case['focus'],
            'label_source': 'human', 'annotator': '',
            'rubric': '2 明确想读，1 可能想读，0 不想读，null 无法判断。按同一阅读目标判断，查看排序对比前先打分。',
            'items': items}


def evaluate_ranking(case: dict, annotation: dict, k: int = 3) -> dict:
    if type(k) is not int or k < 1:
        raise ValueError('k must be a positive integer')
    if not isinstance(annotation, dict) or annotation.get('case_id') != case['case_id']:
        raise ValueError('Annotations must match the fixed case_id')
    source = annotation.get('label_source')
    if source not in ('human', 'ai_proxy'):
        raise ValueError('label_source must be human or ai_proxy')
    annotator = annotation.get('annotator')
    if not isinstance(annotator, str) or not annotator.strip():
        raise ValueError('An annotator is required')
    known_ids = {row['candidate_id'] for row in case['candidates']}
    grades = {}
    items = annotation.get('items')
    if not isinstance(items, list):
        raise ValueError('items must be a list')
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get('candidate_id'), str):
            raise ValueError('Every label requires a candidate ID')
        cid = item['candidate_id']
        if cid not in known_ids or cid in grades or 'grade' not in item:
            raise ValueError('Labels must have unique IDs from the case and an explicit grade')
        grade = item['grade']
        if grade is not None and (type(grade) is not int or grade not in (0, 1, 2)):
            raise ValueError('Grade must be 0, 1, 2 or null')
        grades[cid] = grade

    effective_k = min(k, len(known_ids))
    judged = {cid: grade for cid, grade in grades.items() if grade is not None}
    complete = len(judged) == len(known_ids)
    positives = sum(grade == 2 for grade in judged.values())
    ideal = discounted_gain(sorted(judged.values(), reverse=True)[:effective_k]) if complete else None
    arms = {}
    for name, ranking in case['rankings'].items():
        top = ranking[:effective_k]
        labels = [judged[cid] for cid in top if cid in judged]
        top_complete = len(labels) == len(top)
        hits = sum(grade == 2 for grade in labels)
        arms[name] = {
            'returned_at_k': len(top),
            'coverage_at_k': len(top) / effective_k,
            'judged_in_top_k': len(labels),
            'precision_at_k': hits / effective_k if top_complete else None,
            'hit_at_k': int(bool(hits)) if top_complete else None,
            'recall_at_k_in_pool': hits / positives if complete and positives else None,
            'ndcg_at_k': discounted_gain(labels) / ideal if ideal else None,
        }
    return {'case_id': case['case_id'], 'run_id': case['run_id'], 'focus': case['focus'],
            'label_source': source, 'annotator': annotator.strip(), 'requested_k': k,
            'effective_k': effective_k, 'pool_size': len(known_ids), 'labeled_count': len(judged),
            'full_pool_labeled': complete, 'positive_count_in_pool': positives if complete else None,
            'home_order_source': case['home_order_source'], 'arms': arms}


def discounted_gain(grades: list[int]) -> float:
    return sum((2**grade - 1) / log2(rank + 1) for rank, grade in enumerate(grades, 1))


def summarize_pairs(results: list[dict], challenger: str = METHOD) -> dict:
    if len({(row['label_source'], row['requested_k']) for row in results}) > 1:
        raise ValueError('Aggregate one label source and cutoff at a time')
    if len({row['case_id'] for row in results}) != len(results):
        raise ValueError('Count each fixed case at most once')
    comparisons = {}
    for metric in ('precision_at_k', 'hit_at_k', 'recall_at_k_in_pool', 'ndcg_at_k', 'coverage_at_k'):
        pairs = []
        for row in results:
            baseline = row['arms']['homepage'][metric]
            other = row['arms'].get(challenger, {}).get(metric)
            if baseline is not None and other is not None:
                pairs.append((baseline, other))
        deltas = [other - baseline for baseline, other in pairs]
        count = len(pairs)
        comparisons[metric] = {
            'paired_cases': count,
            'homepage_mean': sum(pair[0] for pair in pairs) / count if count else None,
            'challenger_mean': sum(pair[1] for pair in pairs) / count if count else None,
            'delta': sum(deltas) / count if count else None,
            'wins': sum(delta > 1e-12 for delta in deltas),
            'ties': sum(abs(delta) <= 1e-12 for delta in deltas),
            'losses': sum(delta < -1e-12 for delta in deltas),
        }
    return {'cases': len(results), 'challenger': challenger, 'metrics': comparisons}
