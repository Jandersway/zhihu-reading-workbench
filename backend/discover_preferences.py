"""Local preference signals for discovery, independent of the explicit reading goal."""
from dataclasses import replace
from functools import lru_cache
import json
import math
import re
from time import monotonic

from zhihu_agent.config import Settings
from zhihu_agent.corpus import Corpus
from zhihu_agent.feedback_memory import active_feedback
from zhihu_agent.recommend import Preferences, ReadingStore

SIMILAR_DISLIKE_MIN = 0.38
SIMILAR_DISLIKE_CAP = 0.12
NOTE_BONUS_CAP = 0.08


def note_terms(excerpt):
    sections = re.split(r'(?m)^##[ \t]+([^\n]+)\n', excerpt)
    content = ' '.join(body for heading, body in zip(sections[1::2], sections[2::2], strict=True)
                       if heading.strip() in {'回答', '我的笔记', '结论'})
    if len(sections) == 1:
        content = excerpt
    content = re.sub(r'\[S\d+\]|https?://\S+', '', content)
    return [set(Preferences.terms(line[:180])) for line in re.split(r'[。！？\n]+', content)[:8] if len(line.strip()) >= 6]


@lru_cache(maxsize=1)
def favorites_for(settings, refresh_window):
    return Preferences(Corpus(settings))


class DiscoverPreferences:
    def __init__(self, store, *, load_history=True, load_saved=True):
        self.direct = store.discovery_ratings()
        self.saved = store.discovery_saved_items() if load_saved else {}
        self.note_terms = {cid: note_terms(item['excerpt']) for cid, item in self.saved.items()
                           if item['source'] == 'note'}
        self.history = {}
        self.favorites = None
        if not load_history:
            return
        settings = replace(Settings.from_env(), kimi_api_key='')
        try:
            self.favorites = favorites_for(settings, int(monotonic() // 300))
        except (OSError, ValueError, KeyError):
            self.favorites = None

        try:
            events = ReadingStore(settings.data_dir.parent / 'reading').events()
        except (OSError, json.JSONDecodeError):
            events = []
        valid_events = [row for row in events if isinstance(row, dict) and isinstance(row.get('candidate_id'), str)
                        and isinstance(row.get('verdict'), str) and row.get('source', 'user') == 'user']
        for event in active_feedback(valid_events):
            if event.get('scope', 'article') == 'article' and event.get('verdict') in {'like', 'dislike'}:
                grade = 2 if event['verdict'] == 'like' else 0
                self.history[event['candidate_id']] = (grade, str(event.get('title') or '')[:300])

    def freeze(self, candidates):
        return {'favorites': len(self.saved) + (len(self.favorites.records) if self.favorites else 0),
                'matches': {item['candidate_id']: self.favorite_match(item) for item in candidates},
                'history': self.history}

    def counts(self, base):
        return {'favorites': base['favorites'], 'human_ratings': len(self.direct),
                'historical_feedback': len(base['history'])}

    def favorite_match(self, candidate):
        title_terms = set(Preferences.terms(candidate['title']))
        favorite, favorite_score = None, 0.0
        origin = None
        for cid, item in self.saved.items():
            if cid == candidate['candidate_id'] or self.direct.get(cid, (None,))[0] == 0:
                continue
            title = item['title']
            is_note = item['source'] == 'note'
            score = similarity(title_terms, set(Preferences.terms(title)))
            if is_note:
                score = max([score, *(similarity(title_terms, terms, minimum_overlap=3)
                                      for terms in self.note_terms[cid])])
            bonus = score * (NOTE_BONUS_CAP if is_note else 0.10)
            if score >= 0.25 and bonus > favorite_score:
                favorite, favorite_score, origin = title, bonus, 'note' if is_note else 'library'
        if self.favorites and title_terms:
            for match in self.favorites.similar(candidate['title'], candidate['excerpt'][:300]):
                overlap = title_terms.intersection(Preferences.terms(match['title']))
                if len(overlap) >= 2 and match['lexical_score'] >= 6:
                    score = min(0.10, match['lexical_score'] / 400)
                    if score > favorite_score:
                        favorite, favorite_score, origin = match['title'], score, 'archive'
                    break

        return {'favorite_title': favorite, 'favorite_score': round(favorite_score, 6), 'favorite_origin': origin}

    def official_matches(self, candidates, titles):
        samples = [(cid, title, set(Preferences.terms(title))) for cid, title in titles.items()
                   if self.direct.get(cid, (None,))[0] != 0]
        matches = {}
        for candidate in candidates:
            cid = candidate['candidate_id']
            if self.direct.get(cid, (None,))[0] == 0:
                continue
            terms = set(Preferences.terms(candidate['title']))
            best = None
            for saved_id, title, saved_terms in samples:
                if saved_id == cid:
                    continue
                relevance = similarity(terms, saved_terms)
                if relevance >= 0.25 and (best is None or relevance > best[0]):
                    best = relevance, title
            if best:
                matches[cid] = {'score': round(best[0] * 0.10, 6), 'title': best[1]}
        return matches

    def goal_examples(self):
        known = {**self.history, **self.direct}
        examples = [
            (item['title'], 'library', 1, set(Preferences.terms(item['title'])))
            for cid, item in self.saved.items() if cid not in known and item['title']
            and not (item['source'] == 'note' and item['excerpt'].startswith('## 问题\n'))
        ]
        examples.extend((title, 'rating', 2, set(Preferences.terms(title)))
                        for grade, title in list(known.values())[-60:] if grade == 2 and title)
        return examples

    def rating_signals(self, base, *, broad_terms=None):
        known = {**base['history'], **self.direct}
        positive = [(title, set(Preferences.terms(title)))
                    for grade, title in known.values() if grade == 2 and title]
        negative = []
        if broad_terms is not None:
            negative = [(title, set(Preferences.terms(title)) - broad_terms)
                        for grade, title in known.values() if grade == 0 and title]
        return known, positive, negative

    def score(self, candidate, base, rating_signals, *, broad_terms):
        cid = candidate['candidate_id']
        known, positive, negative = rating_signals
        exact = known.get(cid)
        rating_score = 0.0
        rating_title = None
        penalty, disliked_title, shared_terms = 0.0, None, []
        if exact:
            grade = exact[0]
            rating_score = {0: -0.20, 1: 0.0, 2: 0.20}[grade]
        else:
            title_terms = set(Preferences.terms(candidate['title']))
            for title, terms in positive:
                relevance = similarity(title_terms, terms, minimum_overlap=3)
                if relevance >= 0.22 and relevance * 0.12 > rating_score:
                    rating_score = relevance * 0.12
                    rating_title = title
            if negative:
                penalty, disliked_title, shared_terms = similar_dislike(title_terms - broad_terms, negative)
        return {**base['matches'][cid],
                'rated_title': rating_title, 'rating_score': round(rating_score - penalty, 6),
                'dislike_title': disliked_title, 'dislike_terms': shared_terms, 'dislike_penalty': round(penalty, 6),
                'known_grade': exact[0] if exact else None,
                'my_grade': self.direct[cid][0] if cid in self.direct else None}


def similarity(left, right, *, minimum_overlap=2):
    overlap = left & right
    if len(overlap) < minimum_overlap:
        return 0.0
    return len(overlap) / math.sqrt(len(left) * len(right))


def similar_dislike(candidate_terms, negatives):
    best_score, best_title, best_terms = 0.0, None, []
    for title, terms in negatives:
        shared = candidate_terms & terms
        distinctive = [term for term in shared if len(term) >= 3]
        if not distinctive or (len(shared) < 4 and sum(term.isascii() for term in distinctive) < 3):
            continue
        relevance = similarity(candidate_terms, terms, minimum_overlap=3)
        if relevance >= SIMILAR_DISLIKE_MIN and relevance > best_score:
            best_score, best_title = relevance, title
            best_terms = sorted(distinctive, key=lambda term: (-len(term), term))[:3]
    # Use only the strongest negative match so repeated feedback cannot pile up penalties.
    return best_score * SIMILAR_DISLIKE_CAP, best_title, best_terms
