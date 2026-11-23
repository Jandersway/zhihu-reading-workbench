"""知乎专栏发现：专题召回、可解释的目标重排和内容标签。"""
from collections import Counter
from html import unescape
import re
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StringConstraints

from zhihu_agent.ranking import rank_candidates
from zhihu_agent.recommend import Preferences, candidate_url

from .discover_preferences import DiscoverPreferences, similarity
from .embeddings import collection_profile, has_current_profile, semantic_matches
from .feed_filter import is_promotion, suspected_promotion
from .library import cli_read
from .pairwise_ranker import FEATURES, PRIOR, feature_row, score

router = APIRouter(prefix='/discover', tags=['article-discovery'])

TOPICS = {
    'agent': ('Agent 工程', 'Agent 工程实践', ('agent', '智能体', '工具调用', 'harness', 'langgraph')),
    'retrieval': ('RAG 与知识图谱', 'RAG 知识图谱', ('rag', '检索增强', '知识图谱', '向量检索', 'neo4j')),
    'training': ('大模型训练', '大模型训练', ('预训练', '微调', 'sft', 'lora', 'grpo', '强化学习', '蒸馏')),
    'inference': ('推理与部署', '大模型推理部署', ('vllm', 'sglang', '推理部署', '模型部署', '量化', '推理加速')),
    'fullstack': ('全栈开发', '全栈开发', ('全栈', 'nuxt', 'vue', 'fastapi', 'sqlite', '前端', '后端')),
}
RANKING_VERSION = 'discovery-v6'
EARLIER_RANKING_VERSIONS = {'discovery-v2', 'discovery-v3'}
SUPPORTED_RANKING_VERSIONS = EARLIER_RANKING_VERSIONS | {'discovery-v4', 'discovery-v5', RANKING_VERSION}
FORMATS = {
    'tutorial': ('动手教程', ('教程', '实战', '手把手', '代码示例', '从零', '搭建', '上手')),
    'principle': ('原理解析', ('原理', '详解', '概念', '机制', '解读', '推导', '为什么')),
    'case': ('项目案例', ('落地', '生产级', '复盘', '案例', '上线', '踩坑', '实践经验')),
}
GENERAL_TITLE_TERMS = frozenset(Preferences.terms(' '.join(
    [term for label, _, aliases in TOPICS.values() for term in (label, *aliases)]
    + [term for label, aliases in FORMATS.values() for term in (label, *aliases)]
    + ['the and for with from about this that into guide tutorial introduction explained', '经验分享 注意事项 常见问题']
)))


class DiscoveryRating(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    candidate_id: Annotated[str, StringConstraints(pattern=r'^(answer|article):[0-9]{1,24}$')]
    title: str | None = Field(default=None, min_length=1, max_length=300)
    batch_id: Annotated[str, StringConstraints(pattern=r'^[a-f0-9]{32}$')] | None = None
    grade: Annotated[StrictInt, Field(ge=0, le=2)] | None


class CollectionSync(BaseModel):
    model_config = ConfigDict(extra='forbid')
    batch_id: Annotated[str, StringConstraints(pattern=r'^[a-f0-9]{32}$')] | None = None


class DiscoveryVisibility(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    candidate_id: Annotated[str, StringConstraints(pattern=r'^(answer|article):[0-9]{1,24}$')]
    hidden: bool


def classify(title, excerpt, taxonomy, *, topic=False):
    title = title.casefold()
    excerpt = excerpt.casefold()
    matches = []
    for key, entry in taxonomy.items():
        terms = entry[2] if topic else entry[1]
        title_hits = [term for term in terms if term in title]
        excerpt_hits = [term for term in terms if term in excerpt and term not in title_hits]
        matches.append((2 * len(title_hits) + len(excerpt_hits), key, title_hits + excerpt_hits))
    score, key, terms = max(matches, key=lambda match: match[0])
    return (key, terms[:4]) if score else (None, [])


def text_field(value, limit):
    return unescape(re.sub(r'</?em\b[^>]*>', '', str(value or ''), flags=re.I)).strip()[:limit]


@router.get('/topics')
def topics():
    return {'topics': [{'id': key, 'label': label, 'query': query} for key, (label, query, _) in TOPICS.items()],
            'formats': [{'id': key, 'label': label} for key, (label, _) in FORMATS.items()]}


@router.get('/articles')
def articles(request: Request, q: Annotated[str, Query(max_length=100)] = '', topic: str | None = None,
             goal: Annotated[str, Query(max_length=160)] = '',
             reading_type: Literal['tutorial', 'principle', 'case'] | None = None, auto: bool = False):
    if topic and topic not in TOPICS:
        raise HTTPException(422, '请选择已知专题。')
    query = q.strip() or (TOPICS[topic][1] if topic else '')
    if not query and not auto:
        raise HTTPException(422, '请输入搜索词或选择一个专题。')
    if query.startswith('-'):
        raise HTTPException(422, '搜索词需要以文字或数字开头。')
    source = 'search' if query else 'feed'
    args = (['search', 'article', '--limit', '16', '--max', '16', query] if query else
            ['browse', 'feed', '--type', 'recommend', '--limit', '18', '-n', '18'])
    rows = cli_read(args, '搜索知乎专栏' if query else '读取知乎首页推荐', request.state.gateway)
    if not isinstance(rows, list):
        raise HTTPException(502, '知乎返回了无法识别的数据。')
    store = request.state.store
    preferences = DiscoverPreferences(store, load_history=request.state.account_key is None)
    eligible_rows = [row for row in rows if not is_promotion(row)] if source == 'feed' else rows
    candidates = normalize_candidates(eligible_rows, source)
    hidden = store.hidden_discovery() if source == 'feed' else set()
    suggestion = suggest_goal(query, [item for item in candidates if item['candidate_id'] not in hidden], preferences)
    focus = goal.strip()
    batch = {'query': query, 'goal': focus or suggestion['text'], 'goal_origin': 'manual' if focus else suggestion['source'],
             'goal_suggestion': suggestion, 'reading_type': reading_type, 'source': source, 'candidates': candidates,
             'ranking_version': RANKING_VERSION, 'filtered_ads': len(rows) - len(eligible_rows)}
    official = store.zhihu_collections() if request.state.account_key else None
    batch['preference_base'] = preferences.freeze(candidates)
    attach_collections(batch, preferences, official)
    return save_batch(store, batch, rank_discovery(batch, preferences, hidden, store.train_discovery()), official)


def normalize_candidates(rows, source):
    candidates = {}
    for row in rows[:18]:
        if not isinstance(row, dict):
            continue
        article_id = str(row.get('id') or '')
        kind = row.get('target_type') if source == 'feed' else 'article'
        cid = f'{kind}:{article_id}'
        title = text_field(row.get('title'), 300)
        if not re.fullmatch(r'[0-9]{1,24}', article_id) or not title or cid in candidates or kind not in {'answer', 'article'}:
            continue
        excerpt = text_field(row.get('excerpt'), 600)
        author = row.get('author')
        topic_id, _ = classify(title, excerpt, TOPICS, topic=True)
        format_id, format_terms = classify(title, excerpt, FORMATS)
        try:
            url = candidate_url({**row, 'target_type': kind})
        except ValueError:
            # A malformed upstream answer URL must not discard the rest of the batch.
            url = f'https://www.zhihu.com/answer/{article_id}'
        candidates[cid] = {'candidate_id': cid, 'title': title, 'excerpt': excerpt,
                                  'author': text_field(author.get('name') if isinstance(author, dict) else author, 120),
                                  'url': url, 'topic': topic_id,
                                  'suspected_promotion': source == 'feed' and suspected_promotion(title, excerpt),
                                  'reading_type': format_id, 'format_terms': format_terms}
    return list(candidates.values())


def normalize_collections(payload):
    if (not isinstance(payload, dict) or not isinstance(payload.get('items'), list)
            or type(payload.get('collections')) is not int or type(payload.get('truncated')) is not bool):
        raise HTTPException(502, '知乎收藏接口未返回可识别的资料。')
    titles = {}
    for item in payload['items']:
        if not isinstance(item, dict):
            continue
        kind = item.get('type')
        item_id = str(item.get('id') or '')
        title = text_field(item.get('title'), 300)
        if kind in ('article', 'answer') and re.fullmatch(r'[0-9]{1,24}', item_id) and title:
            titles[f'{kind}:{item_id}'] = title
    return {'titles': titles, 'collections': payload['collections'], 'truncated': payload['truncated']}


def collection_status(snapshot):
    return {'count': len(snapshot['titles']), 'collections': snapshot['collections'],
            'truncated': snapshot['truncated'], 'updated_at': snapshot['updated_at'],
            'semantic_ready': has_current_profile(snapshot)} if snapshot else None


def attach_collections(batch, preferences, snapshot):
    titles = snapshot['titles'] if snapshot else {}
    batch['preference_base']['official_matches'] = preferences.official_matches(batch['candidates'], titles)
    if titles and batch['ranking_version'] == RANKING_VERSION:
        excluded = {cid for cid, (grade, _) in preferences.direct.items() if grade == 0}
        state, matches = semantic_matches(batch['candidates'], snapshot, excluded)
        batch.update(semantic_status=state, semantic_matches=matches,
                     evaluation_exclusions=sorted(excluded.intersection(
                         set(titles) | {item['candidate_id'] for item in batch['candidates']})))


def save_batch(store, batch, ranked, snapshot):
    batch['initial_ranks'] = {item['candidate_id']: item['rank'] for item in ranked['items']}
    batch.setdefault('collection_snapshot_at', snapshot['updated_at'] if snapshot else None)
    batch['shown_features'] = {
        item['candidate_id']: {'title': item['title'], 'original_rank': item['original_rank'], 'shown_rank': item['rank'],
                               'band': item['goal_band'], 'values': item['training_features'],
                               'rule_signals': {key: item[key] for key in (
                                   'matched_terms', 'official_favorite_title', 'favorite_title',
                                   'known_grade', 'dislike_title', 'suggested_terms',
                               )}}
        for item in ranked['items']
    }
    for item in ranked['items']:
        item.pop('training_features')
    return {**ranked, 'batch_id': store.save_discovery_batch(batch), 'ranking_version': batch['ranking_version'],
            'zhihu_collections': collection_status(snapshot)}


def training_status(model):
    return {key: value for key, value in model.items() if key not in {'weights', 'preview_weights'}}


@router.post('/collections/sync')
def sync_collections(options: CollectionSync, request: Request):
    key = request.state.account_key
    if not key:
        raise HTTPException(401, '请先登录工作台，才能同步自己的知乎收藏夹。')
    store = request.state.store
    if not store.collection_sync_lock.acquire(blocking=False):
        raise HTTPException(409, '这个账号的收藏正在同步，请稍后再试。')
    try:
        payload = cli_read(['account', 'collections', key], '同步知乎收藏', request.state.gateway)
        collection = normalize_collections(payload)
        collection['profile'] = collection_profile(collection['titles'])
        snapshot = store.set_zhihu_collections(collection)
    finally:
        store.collection_sync_lock.release()

    result = {'zhihu_collections': collection_status(snapshot), 'comparison': [], 'batch': None}
    existing = store.discovery_batch(options.batch_id) if options.batch_id else None
    if not existing or existing.get('ranking_version') not in SUPPORTED_RANKING_VERSIONS:
        return result
    preferences = DiscoverPreferences(store, load_history=False, load_saved=False)
    base = {**existing['preference_base'], 'official_matches': {}}
    batch = {**existing, 'preference_base': base, 'ranking_version': RANKING_VERSION, 'initial_ranks': {}}
    batch.pop('collection_snapshot_at', None)
    batch.pop('semantic_matches', None)
    batch.pop('semantic_status', None)
    hidden = store.hidden_discovery() if batch['source'] == 'feed' else set()
    before = {item['candidate_id']: item['rule_rank'] for item in rank_discovery(batch, preferences, hidden)['items']}
    attach_collections(batch, preferences, snapshot)
    after = rank_discovery(batch, preferences, hidden, store.train_discovery())
    result['comparison'] = [
        {'candidate_id': item['candidate_id'], 'title': item['title'], 'before': before[item['candidate_id']],
         'after': item['rule_rank'], 'favorite_title': item['official_favorite_title'], 'bonus': item['official_favorite_bonus']}
        for item in after['items']
    ]
    result['batch'] = save_batch(store, batch, after, snapshot)
    return result


def suggest_goal(query, candidates, preferences):
    examples = [example for example in preferences.goal_examples() if example[3]]
    if query:
        terms = set(Preferences.terms(query))
        minimum_overlap = 1 if len(terms) == 1 and len(next(iter(terms))) > 2 else 2
        matches = [(similarity(terms, example[3], minimum_overlap=minimum_overlap), example) for example in examples]
        relevant = [(score, example) for score, example in matches if score >= 0.24]
        if relevant:
            _, (title, signal, _, title_terms) = max(relevant, key=lambda row: (row[0], row[1][2]))
            expansion = f'{query} · {title[:min(90, 157 - len(query))]}' if title_terms - terms else query
            return {'text': expansion, 'source': 'query', 'signal': signal, 'title': title}
        return {'text': query, 'source': 'query'}

    if examples:
        def support(example):
            return example[2] + sum(similarity(example[3], other[3]) * other[2]
                                    for other in examples if other is not example)

        title, signal, _, _ = max(examples, key=support)
        return {'text': title[:160], 'source': 'history', 'signal': signal, 'title': title}

    # A new account can get a topic from the current feed when multiple candidates share it.
    topics = Counter(item['topic'] for item in candidates if item['topic'])
    most_common = topics.most_common(1)
    if most_common and most_common[0][1] >= 2:
        topic, count = most_common[0]
        return {'text': TOPICS[topic][0], 'source': 'feed', 'count': count}
    return {'text': '', 'source': 'none'}


def rank_discovery(batch, preferences, hidden=frozenset(), model=None):
    goal, query, reading_type = batch['goal'], batch['query'], batch['reading_type']
    suggestion = batch.get('goal_suggestion')
    goal_origin = batch.get('goal_origin', 'manual' if goal else 'none')
    explicit_focus = goal if goal_origin == 'manual' else query
    candidates = {row['candidate_id']: row for row in batch['candidates']}
    ranked = rank_candidates(batch['candidates'], explicit_focus)
    hint = ''
    if goal_origin == 'query' and suggestion and goal != query:
        hint = suggestion['title'][:160]
    elif goal_origin in {'history', 'feed'}:
        hint = goal
    inferred = ({row['candidate_id']: row for row in rank_candidates(batch['candidates'], hint)}
                if hint and hint != explicit_focus else {})
    base = batch['preference_base']
    broad_terms = None if batch.get('ranking_version') in EARLIER_RANKING_VERSIONS else GENERAL_TITLE_TERMS
    signals = preferences.rating_signals(base, broad_terms=broad_terms)
    model = model or {}
    trained = model.get('weights')
    preview = model.get('preview_weights')
    semantic_status = batch.get('semantic_status', 'not_synced')
    active_weights = trained or PRIOR
    comparison_weights = preview or active_weights
    stage = 'learned' if trained else 'semantic' if semantic_status == 'ready' else 'rules'
    results = []
    for rank in ranked:
        if rank['candidate_id'] in hidden:
            continue
        candidate = candidates[rank['candidate_id']]
        format_bonus = 0.12 if reading_type and reading_type == candidate['reading_type'] else 0.0
        personal = preferences.score(candidate, base, signals, broad_terms=broad_terms)
        official = base.get('official_matches', {}).get(rank['candidate_id']) if personal['known_grade'] != 0 else None
        official_bonus = round(max(0.0, official['score'] - personal['favorite_score']), 6) if official else 0.0
        goal_score = rank['score']
        baseline = round(goal_score + format_bonus, 8)
        inferred_match = inferred.get(rank['candidate_id'])
        inferred_bonus = inferred_match['score'] * (0.18 if query else 0.40) if inferred_match else 0.0
        inferred_terms = sorted(set(inferred_match['matched_terms']) - set(rank['matched_terms'])) if inferred_match else []
        # A strong explicit goal match beats a weak match. Preferences break close calls.
        goal_band = int(goal_score * 5) + 1 if (goal or query) and goal_score else 0
        results.append({**candidate, **personal, 'original_rank': rank['home_rank'], 'goal_score': goal_score,
                        'goal_band': goal_band, 'baseline_score': baseline, 'format_match': bool(format_bonus),
                        'score': round(baseline + inferred_bonus + personal['favorite_score'] + official_bonus
                                       + personal['rating_score'], 8),
                        'official_favorite_title': official['title'] if official else None,
                        'official_favorite_bonus': official_bonus,
                        'matched_terms': rank['matched_terms'], 'suggested_terms': inferred_terms})
    results.sort(key=lambda row: (-row['goal_band'], -row['baseline_score'], row['original_rank']))
    for index, row in enumerate(results, 1):
        row['baseline_rank'] = index
    results.sort(key=lambda row: (-row['goal_band'], -row['score'], row['original_rank']))
    semantic_matches_by_id = batch.get('semantic_matches', {})
    for index, row in enumerate(results, 1):
        semantic = semantic_matches_by_id.get(row['candidate_id']) if row['known_grade'] != 0 else None
        features = feature_row(row, semantic)
        row.update(rule_rank=index, training_features=features,
                   model_score=round(score(comparison_weights, features), 8),
                   semantic_title=semantic['title'] if semantic and features[1] else None,
                   semantic_cosine=semantic['cosine'] if semantic and features[1] else None,
                   model_contributions={name: round(weight * value, 5)
                                        for name, weight, value in zip(FEATURES[1:], comparison_weights[1:], features[1:], strict=True)})
    model_order = sorted(results, key=lambda row: (-row['goal_band'], -row['model_score'], row['original_rank']))
    for index, row in enumerate(model_order, 1):
        row['model_rank'] = index
    results.sort(key=lambda row: (
        -row['goal_band'], -round(score(active_weights, row['training_features']), 8), row['original_rank'],
    ))
    initial = batch.get('initial_ranks', {})
    return {'query': query, 'source': batch['source'], 'signals': preferences.counts(base),
            'filtered_ads': batch.get('filtered_ads', 0),
            'ranking_model': {**training_status(model), 'stage': stage, 'semantic_status': semantic_status,
                              'experiment_available': preview is not None},
            'goal': goal, 'goal_origin': goal_origin, 'goal_suggestion': suggestion,
            'items': [dict(row, rank=index, initial_rank=initial.get(row['candidate_id'], index))
                      for index, row in enumerate(results, 1)]}


@router.post('/batches/{batch_id}/rerank')
def rerank_batch(batch_id: Annotated[str, StringConstraints(pattern=r'^[a-f0-9]{32}$')], request: Request,
                 goal: Annotated[str | None, Query(max_length=160)] = None):
    store = request.state.store
    batch = store.discovery_batch(batch_id)
    if batch is None or batch.get('ranking_version') not in SUPPORTED_RANKING_VERSIONS:
        raise HTTPException(404, '这批候选已过期，请重新搜索或刷新推荐。')
    if goal is not None:
        focus = goal.strip()
        suggestion = batch.get('goal_suggestion') or {}
        batch = {**batch, 'goal': focus or suggestion.get('text', ''),
                 'goal_origin': 'manual' if focus else suggestion.get('source', 'none'), 'initial_ranks': {}}
    preferences = DiscoverPreferences(store, load_history=False, load_saved=False)
    hidden = store.hidden_discovery() if batch['source'] == 'feed' else set()
    ranked = rank_discovery(batch, preferences, hidden, store.train_discovery())
    if goal is not None:
        # Freeze the features shown for the new goal, using the same upstream candidates.
        return save_batch(store, batch, ranked, store.zhihu_collections())
    for item in ranked['items']:
        item.pop('training_features')
    return {**ranked, 'batch_id': batch_id, 'ranking_version': batch['ranking_version']}


@router.put('/visibility')
def set_visibility(options: DiscoveryVisibility, request: Request):
    request.state.store.set_discovery_hidden(options.candidate_id, options.hidden)
    return {'hidden': options.hidden}


@router.put('/rating')
def rate_article(rating: DiscoveryRating, request: Request):
    store = request.state.store
    if rating.batch_id:
        if not store.rate_discovery_batch(rating.batch_id, rating.candidate_id, rating.grade):
            raise HTTPException(404, '文章不在这一批候选里，或这批已过期，请刷新推荐。')
    elif rating.title:
        store.rate_discovery(rating.candidate_id, rating.title, rating.grade)
    else:
        raise HTTPException(422, '评分需要关联当前候选批次。')
    model = store.train_discovery()
    return {'grade': rating.grade, 'training': training_status(model),
            'trained_available': model['weights'] is not None}
