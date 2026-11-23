"""Offline human-label evaluation. No model judges its own recommendation."""
from __future__ import annotations

FIELDS = ('relevant', 'quote_correct', 'claims_supported', 'preference_supported')


def annotation_template(report: dict) -> dict:
    return {'run_id': report['run_id'], 'annotator': '', 'label_source': 'human',
            'instructions': '查看来源及推荐理由后填写 true/false；无法判断保留 null，各指标单独计算覆盖率。AI 评分必须改 label_source=ai_proxy，不能冒充人工反馈。',
            'items': [{'candidate_id': r['candidate_id'],
                       'evidence_id': r.get('evidence', {}).get('evidence_id'),
                       **{field: None for field in FIELDS}, 'notes': ''}
                      for r in report['recommendations']]}


def evaluate_reading(report: dict, annotations: dict) -> dict:
    if not isinstance(annotations, dict):
        raise ValueError('Annotations must be a JSON object')
    if annotations.get('run_id') != report['run_id']:
        raise ValueError('Annotation run_id must match the selected report')
    annotator = annotations.get('annotator')
    if not isinstance(annotator, str) or not annotator.strip():
        raise ValueError('Provide the annotator name or pseudonym')
    label_source = annotations.get('label_source')
    if not isinstance(label_source, str) or label_source not in {'human', 'ai_proxy'}:
        raise ValueError('label_source must be human or ai_proxy; AI estimates must not be labeled as user feedback')
    rows = annotations.get('items')
    if not isinstance(rows, list) or not rows:
        raise ValueError('Provide at least one labeled recommendation')
    recommendations = {r['candidate_id']: r for r in report['recommendations']}
    seen = set()
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get('candidate_id'), str):
            raise ValueError('Invalid annotation item')
        cid = row['candidate_id']
        if cid not in recommendations or cid in seen:
            raise ValueError('Annotation IDs must be unique recommendations in this run')
        seen.add(cid)
        expected = recommendations[cid].get('evidence', {}).get('evidence_id')
        if 'evidence_id' not in row or row['evidence_id'] != expected:
            raise ValueError('Annotation evidence_id must match this recommendation version')
        if any(field not in row or (row[field] is not None and type(row[field]) is not bool) for field in FIELDS):
            raise ValueError('Labels must be true/false or null for unassessed, not strings or numbers')
    assessed_rows = sum(any(row[field] is not None for field in FIELDS) for row in rows)
    if not assessed_rows:
        raise ValueError('Provide at least one assessed label; a blank template is not an evaluation')
    names = ('relevance_precision', 'citation_precision', 'evidence_support_rate', 'preference_support_rate')
    counts = {name: sum(row[field] is not None for row in rows) for name, field in zip(names, FIELDS)}
    return {'run_id': report['run_id'], 'annotator': annotator.strip(), 'label_source': label_source,
            'assessed_count': assessed_rows, 'recommendation_count': len(recommendations),
            'annotation_coverage': assessed_rows / len(recommendations), 'metric_denominators': counts,
            'metrics': {name: (sum(row[field] is True for row in rows) / counts[name] if counts[name] else None)
                        for name, field in zip(names, FIELDS)},
            'runtime_completed': report.get('completed'), 'elapsed_ms': report.get('elapsed_ms'),
            'usage_totals': report.get('usage_totals'), 'usage_complete': report.get('usage_complete'),
            'limitations': 'Rates apply only to labeled published recommendations; no Recall@K/NDCG or overall task success is inferred. Labels are supplied, not independently verified; ai_proxy is not user satisfaction.'}
