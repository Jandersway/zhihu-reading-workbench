"""Rank the existing candidate pool for an explicitly stated reading goal."""
from collections import Counter
from math import log

from .corpus import tokenize

METHOD = 'focus-term-coverage-v1'


def rank_candidates(candidates: list[dict], focus: str) -> list[dict]:
    """Rare goal terms count more. Equal scores preserve the homepage order."""
    query = set(tokenize(focus[:1000]))
    titles = [set(tokenize(row['title'])) & query for row in candidates]
    excerpts = [set(tokenize(row.get('excerpt', ''))) & query for row in candidates]
    frequencies = Counter(term for title, excerpt in zip(titles, excerpts) for term in title | excerpt)
    weights = {term: 1 + log((1 + len(candidates)) / (1 + frequencies[term])) for term in sorted(query)}
    total_weight = sum(weights.values())
    rows = []
    for rank, (candidate, title, excerpt) in enumerate(zip(candidates, titles, excerpts), 1):
        score = sum(weights[term] * (1 if term in title else 0.65) for term in sorted(title | excerpt))
        rows.append({'candidate_id': candidate['candidate_id'], 'home_rank': rank,
                     'score': round(score / total_weight, 8) if total_weight else 0.0,
                     'matched_terms': sorted(title | excerpt)[:12]})
    rows.sort(key=lambda row: (-row['score'], row['home_rank']))
    for rank, row in enumerate(rows, 1):
        row['focus_rank'] = rank
    return rows
