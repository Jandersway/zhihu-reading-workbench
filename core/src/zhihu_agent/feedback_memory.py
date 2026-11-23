"""User-authored feedback, latest-wins corrections, relevance-based recall.

No LLM writes user preferences. An article dislike is not a topic ban.
"""
from __future__ import annotations

from collections import Counter
import math
import re

from .corpus import tokenize


def active_feedback(events: list[dict]) -> list[dict]:
    latest = {}
    for order, event in enumerate(events):
        latest[event["candidate_id"]] = (order, event)
    return [event for _, event in sorted(latest.values(), key=lambda pair: pair[0])
            if event["verdict"] not in {"clear", "seen"}]


class FeedbackMemory:
    def __init__(self, events: list[dict]):
        self.events = active_feedback(events)

    def recall(self, title: str, excerpt: str = "", limit: int = 3) -> list[dict]:
        query = set(tokenize(title + " " + excerpt[:300]))
        ranked = []
        for order, event in enumerate(self.events):
            scope = event.get("scope", "article")
            topic = event.get("topic", "")
            text = topic if scope == "topic" else event["title"]
            terms = set(tokenize(text))
            overlap = query & terms
            # Two matching terms avoids most accidental single-character links.
            pattern = re.escape(topic.casefold())
            if re.fullmatch(r"[a-zA-Z0-9]+", topic):
                pattern = r"(?<![a-z0-9])" + pattern + r"(?![a-z0-9])"
            exact_topic = bool(topic and re.search(pattern, (title + " " + excerpt).casefold()))
            if len(overlap) < 2 and not exact_topic:
                continue
            score = len(overlap) / math.sqrt(max(1, len(query) * len(terms))) + (1 if exact_topic else 0)
            ranked.append((score, order, event))
        result = []
        for score, _, event in sorted(ranked, key=lambda x: (x[0], x[1]), reverse=True)[:limit]:
            result.append({k: event.get(k, "") for k in
                           ("candidate_id", "title", "verdict", "scope", "topic", "note", "at")})
            result[-1]["scope"] = event.get("scope", "article")
            result[-1]["retrieval_score"] = round(score, 3)
        return result


def evidence_window(text: str, weights: dict[str, float], width: int = 700) -> str:
    """Return one contiguous original excerpt maximizing weighted term coverage.

    Positions are found once. Sliding windows cover the entire retrieved chunk,
    including its tail; no model paraphrase is inserted into evidence.
    """
    if len(text) <= width:
        return text
    lower = text.lower()
    matches = [(term, weight, [m.start() for m in re.finditer(re.escape(term.lower()), lower)])
               for term, weight in weights.items() if term]
    starts = list(range(0, len(text) - width + 1, 80)) + [len(text) - width]
    def score(start):
        return sum(weight for term, weight, positions in matches
                   if any(start <= p and p + len(term) <= start + width for p in positions))
    start = max(starts, key=score)
    return text[start:start + width]


def feedback_metrics(reports: list[dict], events: list[dict]) -> dict:
    shown = {item["candidate_id"] for report in reports for item in report.get("recommendations", [])}
    latest = {}
    for event in events:
        latest[event["candidate_id"]] = event
    counts = Counter(e["verdict"] for cid, e in latest.items() if cid in shown and e["verdict"] != "clear")
    judged = sum(counts[k] for k in ("like", "dislike", "too_shallow"))
    return {"unique_recommendations": len(shown), "rated": judged, "verdicts": dict(counts),
            "rating_coverage": round(judged / len(shown), 3) if shown else None,
            "like_rate_among_rated": round(counts["like"] / judged, 3) if judged else None,
            "notice": "只统计用户已评价的样本；未评价不是不喜欢，seen 不计入好坏判断；不是无偏的全站推荐准确率。"}
