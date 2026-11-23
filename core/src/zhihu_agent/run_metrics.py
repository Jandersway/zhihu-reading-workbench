"""Observed provider usage and phase costs. Unknown usage stays unknown."""
from __future__ import annotations


TOKEN_KEYS = ('prompt_tokens', 'completion_tokens', 'total_tokens')


def valid_usage(usage) -> dict:
    return {key: usage[key] for key in TOKEN_KEYS
            if isinstance(usage, dict) and type(usage.get(key)) is int and usage[key] >= 0}


def summarize_calls(calls: list[dict]) -> dict:
    known = {key: sum(valid_usage(c.get('usage')).get(key, 0) for c in calls) for key in TOKEN_KEYS}
    complete = {key: all(key in valid_usage(c.get('usage')) for c in calls) for key in TOKEN_KEYS}
    return {'calls': len(calls), 'failed_calls': sum(not c.get('ok', False) for c in calls),
            'elapsed_ms': sum(c.get('elapsed_ms', 0) for c in calls),
            'context_chars': sum(c.get('context_chars', 0) for c in calls),
            'known_usage': known, 'usage_complete': all(complete.values()),
            'total_tokens': known['total_tokens'] if complete['total_tokens'] else None}


def stage_metrics(model_trace: list[dict], tool_trace: list[dict]) -> dict:
    phases = dict.fromkeys(['fetch', 'plan', 'execute', 'validate', 'reflect', 'publish'])
    result = {}
    for stage in phases:
        models = [c for c in model_trace if c['stage'] == stage]
        tools = [c for c in tool_trace if c.get('stage') == stage]
        result[stage] = {'model': summarize_calls(models),
                         'tools': {'calls': len(tools), 'failed_calls': sum(not c.get('ok', False) for c in tools),
                                   'elapsed_ms': sum(c.get('elapsed_ms', 0) for c in tools),
                                   'body_chars_returned': sum(c.get('body_chars_returned', 0) for c in tools)}}
    return {'by_stage': result,
            'revision_model_calls': summarize_calls([c for c in model_trace if c.get('revision', 0) > 0]),
            'note': 'Provider-reported usage; null means unknown. Phase timings are operation timings, not wall-clock partitions.'}
