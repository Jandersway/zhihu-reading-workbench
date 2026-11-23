"""Deterministic provenance for recommendation claims; not a truth classifier."""
from __future__ import annotations

import hashlib
import json


def digest(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def locate(text: str, quote: str) -> dict:
    start = text.find(quote)
    if not quote or start < 0:
        raise ValueError('Evidence quote is absent from the source snapshot')
    end = start + len(quote)
    return {'quote': quote, 'start': start, 'end': end,
            'offset_unit': 'python_unicode_codepoint', 'match_policy': 'first_exact_occurrence',
            'source_sha256': digest(text), 'source_chars': len(text),
            'context': text[max(0, start - 160):min(len(text), end + 160)]}


def evidence_packets(session, draft: dict) -> list[dict]:
    """Called only after session.validate; retain excerpts, never full raw bodies."""
    packets = []
    for entry in draft['items']:
        cid = entry['candidate_id']
        candidate = session.candidates[cid]
        claims = {key: entry[key] for key in ('why', 'value', 'drawback', 'preference_reason')}
        body = locate(session.bodies[cid], entry['evidence_quote'])
        favorites = []
        for favorite in candidate['similar_favorites']:
            if favorite['answer_id'] in entry['favorite_ids'] and entry['preference_quote'] in favorite['excerpt']:
                favorites.append({'answer_id': favorite['answer_id'],
                                  'source_scope': 'supplied_favorite_excerpt',
                                  **locate(favorite['excerpt'], entry['preference_quote'])})
        if not favorites:
            raise ValueError('No preference evidence can be anchored')
        packet = {'candidate_id': cid, 'url': candidate['url'], 'author': candidate['author'],
                  'claims': claims, 'body_evidence': body, 'preference_evidence': favorites,
                  'attribution': 'source_author_claim_not_independently_verified'}
        # Bind a review to both the draft claims and the exact evidence snapshot.
        packet['evidence_id'] = digest(json.dumps(packet, ensure_ascii=False, sort_keys=True))
        packets.append(packet)
    return packets


def review_issues(packets: list[dict], checks: list[dict]) -> list[str]:
    expected = {p['candidate_id']: p for p in packets}
    ids = [c['candidate_id'] for c in checks]
    if len(ids) != len(set(ids)) or set(ids) != set(expected):
        raise ValueError('Review checks must cover every draft candidate exactly once')
    issues = []
    for check in checks:
        cid = check['candidate_id']
        if check['evidence_id'] != expected[cid]['evidence_id']:
            raise ValueError('Review evidence_id does not match the current validated draft')
        if not check['explanation'].strip():
            raise ValueError('Each evidence review requires a concrete explanation')
        if check['claim_support'] != 'supported' or check['preference_match'] != 'specific':
            issues.append(f"{cid}: support={check['claim_support']}, preference={check['preference_match']}; "
                          + check['explanation'])
    return issues
