"""Paired comparison of four rankings with independent, account-owned human labels."""
from bisect import bisect_left
from hashlib import sha256
import logging
from typing import Annotated

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StringConstraints

from .embeddings import E5_REVISION, MODEL_REVISION, e5_matches
from .pairwise_ranker import PRIOR, gain

router = APIRouter(prefix='/discover/evaluations', tags=['article ranking evaluation'])
logger = logging.getLogger(__name__)
BATCH_ID = Annotated[str, StringConstraints(pattern=r'^[a-f0-9]{32}$')]
METHODS = ('zhihu', 'rules', 'bge', 'e5')
TOP_K = 3


class EvaluationRating(BaseModel):
    model_config = ConfigDict(extra='forbid')
    candidate_id: Annotated[str, StringConstraints(pattern=r'^(article|answer):[0-9]{1,24}$')]
    grade: Annotated[StrictInt, Field(ge=0, le=2)] | None


def semantic_bonus(matches):
    # Use identical rank-based calibration for the two encoders. Their raw cosine scales differ.
    population = sorted(match['cosine'] for match in matches.values())
    return {cid: round(PRIOR[1] * bisect_left(population, match['cosine']) / (len(population) - 1), 8)
            for cid, match in matches.items()} if len(population) > 1 else {}


def compare(batch_id, batch, snapshot):
    frozen = batch['shown_features']
    candidates = [item for item in batch['candidates'] if item['candidate_id'] in frozen]
    excluded = set(batch['evaluation_exclusions'])
    favorites = {cid: title for cid, title in snapshot['titles'].items() if cid not in excluded}
    eligible = [item for item in candidates if item['candidate_id'] not in excluded]
    matches = {cid: match for cid, match in batch['semantic_matches'].items() if cid in frozen and cid not in excluded}
    try:
        other_matches = e5_matches(eligible, favorites)
    except Exception as exc:
        logger.warning('E5 evaluation unavailable (%s)', type(exc).__name__)
        raise HTTPException(503, '本机 E5 模型暂不可用。') from None
    if not matches or not other_matches:
        raise HTTPException(409, '本批缺少可比较的收藏信号。请同步收藏后刷新推荐。')
    evidence = {'bge': matches, 'e5': other_matches}
    bonuses = {name: semantic_bonus(value) for name, value in evidence.items()}
    ids = list(frozen)
    orderings = {'zhihu': sorted(ids, key=lambda cid: frozen[cid]['original_rank'])}
    for name in METHODS[1:]:
        bonus = bonuses.get(name, {})
        orderings[name] = sorted(ids, key=lambda cid: (-frozen[cid]['band'],
            -round(frozen[cid]['values'][0] + bonus.get(cid, 0), 8), frozen[cid]['original_rank']))
    ranks = {name: {cid: index for index, cid in enumerate(orderings[name], 1)} for name in METHODS}
    pool = {cid for ordering in orderings.values() for cid in ordering[:TOP_K]}
    items = []
    for candidate in sorted(candidates, key=lambda item: sha256((batch_id + item['candidate_id']).encode()).digest()):
        cid = candidate['candidate_id']
        clues = {name: {**match, 'bonus': bonuses[name].get(cid, 0)}
                 for name, values in evidence.items() if (match := values.get(cid))}
        items.append({**{key: candidate[key] for key in ('candidate_id', 'title', 'url', 'excerpt')},
                      'in_pool': cid in pool, 'ranks': {name: ranks[name][cid] for name in METHODS},
                      'evidence': clues, 'rule_signals': frozen[cid].get('rule_signals', {})})
    return {'batch_id': batch_id, 'goal': batch['goal'], 'source': batch['source'],
            'collection_count': len(snapshot['titles']), 'collection_at': snapshot['updated_at'],
            'revision': {'protocol': 1, 'bge': MODEL_REVISION, 'e5': E5_REVISION},
            'items': items, 'orderings': orderings}


def results(payload, grades):
    pool = {item['candidate_id'] for item in payload['items'] if item['in_pool']}
    labeled = pool & grades.keys()
    ideal = gain(sorted((grades[cid] for cid in pool), reverse=True)) if pool == labeled else 0
    metrics = {}
    for name, order in payload['orderings'].items():
        top = order[:TOP_K]
        top_grades = [grades[cid] for cid in top if cid in grades]
        complete = len(top_grades) == len(top) and bool(top)
        metrics[name] = {
            'judged': len(top_grades),
            'count': len(top),
            'precision_at_3': top_grades.count(2) / len(top) if complete else None,
            'mrr_at_3': next((1 / i for i, grade in enumerate(top_grades, 1) if grade == 2), 0) if complete else None,
            'pooled_ndcg_at_3': gain(top_grades) / ideal if ideal else None,
        }
    return {**payload, 'grades': grades, 'metrics': metrics, 'pool_size': len(pool), 'pool_labeled': len(labeled)}


@router.post('/{batch_id}')
def create_evaluation(batch_id: BATCH_ID, request: Request):
    store = request.state.store
    existing = store.discovery_evaluation(batch_id)
    if existing:
        return results(*existing)
    batch = store.discovery_batch(batch_id)
    if not batch:
        raise HTTPException(404, '这批候选已过期，请到发现页开启新一批评测。')
    snapshot = store.zhihu_collections()
    if not snapshot or not snapshot['titles']:
        raise HTTPException(409, '请先同步你自己的知乎收藏，再评测这批候选。')
    if (batch.get('collection_snapshot_at') != snapshot['updated_at']
            or batch.get('semantic_status') != 'ready' or not batch.get('shown_features')
            or 'evaluation_exclusions' not in batch):
        raise HTTPException(409, '请在发现页刷新推荐，用当前收藏生成一批候选后再评测。')
    payload = compare(batch_id, batch, snapshot)
    return results(*store.save_discovery_evaluation(batch_id, payload))


@router.put('/{batch_id}/ratings')
def rate_evaluation(batch_id: BATCH_ID, rating: EvaluationRating, request: Request):
    updated = request.state.store.grade_discovery_evaluation(batch_id, rating.candidate_id, rating.grade)
    if not updated:
        raise HTTPException(404, '这篇文章不在当前评测批次。')
    return results(*updated)
