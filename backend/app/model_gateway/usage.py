def normalize_usage(raw, provider):
    raw = raw if isinstance(raw, dict) else {}
    tokens = {key: raw.get(source) for key, source in [('input_tokens', 'prompt_tokens'), ('output_tokens', 'completion_tokens'), ('total_tokens', 'total_tokens')]}
    for key, value in tokens.items():
        if not isinstance(value, int) or value < 0:
            tokens[key] = None
    cache = raw.get('prompt_cache_hit_tokens') if provider == 'deepseek' else (raw.get('prompt_tokens_details') or {}).get('cached_tokens')
    reasoning = (raw.get('completion_tokens_details') or {}).get('reasoning_tokens')
    return {**tokens, 'cached_tokens': cache if isinstance(cache, int) and cache >= 0 else None,
            'reasoning_tokens': reasoning if type(reasoning) is int and reasoning >= 0 else None,
            'complete': all(v is not None for v in tokens.values())}


def summarize_usage(attempts):
    usages = [a.get('usage') or {} for a in attempts]
    result = {'attempt_count': len(usages), 'complete': bool(usages) and all(u.get('complete', False) for u in usages)}
    result['reported_attempt_count'] = sum(bool(u.get('complete')) for u in usages)
    result['missing_attempt_count'] = len(usages) - result['reported_attempt_count']
    for key in ['input_tokens', 'output_tokens', 'total_tokens', 'cached_tokens', 'reasoning_tokens']:
        values = [u.get(key) for u in usages]
        result[key] = sum(v for v in values if v is not None) if values and all(v is not None for v in values) else None
        result['known_' + key] = sum(v for v in values if v is not None) if any(v is not None for v in values) else None
    latencies = [a.get('latency') for a in attempts]
    result['model_latency_seconds'] = sum(latencies) if latencies and all(v is not None for v in latencies) else None
    result['known_model_latency_seconds'] = sum(v for v in latencies if v is not None) if any(v is not None for v in latencies) else None
    estimates = [a.get('context_estimate') for a in attempts if a.get('context_estimate') is not None]
    result['context_estimate'] = max(estimates) if estimates else None
    result['context_estimate_is_estimate'] = True
    result['cost'] = None
    return result
