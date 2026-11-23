"""Regularized linear ranking of displayed candidates using explicit comparisons."""
from collections import defaultdict
from itertools import combinations
import math

FEATURES = ('rule', 'semantic', 'tutorial', 'principle', 'case', 'zhihu')
PRIOR = (1.0, 0.12, 0.0, 0.0, 0.0, 0.0)
MIN_BATCHES = 6
MIN_PAIRS = 18
VALIDATION_BATCHES = 2


def cosine_feature(cosine):
    # BGE scores are similarities, not probabilities. Ignore weak matches and cap their impact.
    return min(1.0, max(0.0, (cosine - 0.58) / 0.32))


def feature_row(row, semantic):
    return [row['score'], cosine_feature(semantic['cosine']) if semantic else 0.0,
            *[float(row['reading_type'] == kind) for kind in ('tutorial', 'principle', 'case')],
            1.0 / row['original_rank']]


def score(weights, features):
    return sum(weight * value for weight, value in zip(weights, features, strict=True))


def training_groups(records):
    groups = defaultdict(list)
    for record in records:
        groups[record['batch_id']].append(record)
    unique = {}
    for rows in groups.values():
        # Reopening the same goal and candidate set does not give the experiment another batch.
        key = (rows[0]['goal_key'], rows[0]['cohort_key'])
        ranked_pairs = []
        for first, second in combinations(rows, 2):
            a, b = first, second
            if a['band'] != b['band'] or a['grade'] == b['grade']:
                continue
            if a['grade'] < b['grade']:
                a, b = b, a
            ranked_pairs.append([x - y for x, y in zip(a['features'], b['features'], strict=True)])
        if ranked_pairs:
            unique.setdefault(key, (rows, ranked_pairs))
    return list(unique.values())


def fit(groups):
    weights = list(PRIOR)
    for _ in range(140):
        gradient = [0.18 * (value - base) for value, base in zip(weights, PRIOR, strict=True)]
        for _, pairs in groups:
            weight = 1 / (len(groups) * len(pairs))
            for delta in pairs:
                error = -weight / (1 + math.exp(max(-50.0, min(50.0, score(weights, delta)))))
                for index, feature in enumerate(delta):
                    gradient[index] += error * feature
        weights = [min(base + 0.30, max(base - 0.30, value - 0.65 * step))
                   for value, step, base in zip(weights, gradient, PRIOR, strict=True)]
        weights[1] = max(0.0, weights[1])
    return tuple(round(value, 8) for value in weights)


def gain(grades):
    return sum((2 ** grade - 1) / math.log2(i + 2) for i, grade in enumerate(grades[:3]))


def ndcg(rows, weights):
    if weights is None:
        ordered = sorted(rows, key=lambda row: row['original_rank'])
    else:
        ordered = sorted(rows, key=lambda row: (-row['band'], -score(weights, row['features']), row['original_rank']))
    ideal = gain(sorted((row['grade'] for row in rows), reverse=True))
    return gain([row['grade'] for row in ordered]) / ideal


def train(records):
    groups = training_groups(records)
    count = sum(len(pairs) for _, pairs in groups)
    result = {'eligible_batches': len(groups), 'eligible_pairs': count, 'min_batches': MIN_BATCHES,
              'min_pairs': MIN_PAIRS, 'validation_batches': 0, 'validation': None,
              'weights': None, 'preview_weights': None}
    if len(groups) < MIN_BATCHES or count < MIN_PAIRS:
        return result
    validation = groups[-VALIDATION_BATCHES:]
    cutoff = validation[0][0][0]['shown_at']
    holdout_items = {row['candidate_id'] for rows, _ in validation for row in rows}
    earlier = [row for rows, _ in groups[:-VALIDATION_BATCHES] for row in rows
               if row['rated_at'] < cutoff and row['candidate_id'] not in holdout_items]
    training = training_groups(earlier)
    if len(training) < MIN_BATCHES - VALIDATION_BATCHES or sum(len(pairs) for _, pairs in training) < 12:
        return result
    weights = fit(training)
    rules = (1.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    metrics = {
        name: round(sum(ndcg(rows, vector) for rows, _ in validation) / len(validation), 6)
        for name, vector in (('zhihu', None), ('rules', rules), ('cold_start', PRIOR), ('learned', weights))
    }
    result.update(validation_batches=len(validation), validation=metrics, preview_weights=weights)
    if metrics['learned'] > max(metrics['rules'], metrics['cold_start']):
        result['weights'] = weights
    return result
