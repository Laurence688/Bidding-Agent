"""Only retrievable gaps justify another model judgment."""
import re

SUPPLEMENT_REASONS = {'EVIDENCE_MISSING', 'INSUFFICIENT_EVIDENCE', 'EVIDENCE_INSUFFICIENT', 'STANDARD_DRAFT_ONLY', 'PARAMETER_VALUE_MISSING'}


def supplement_reason(assessment):
    if assessment['verdict'] != 'UNKNOWN' or assessment['reason_code'] not in SUPPLEMENT_REASONS:
        return 'NOT_RETRIEVABLE_REASON'
    if assessment.get('gap_type', 'evidence') not in {'evidence'}:
        return 'GAP_' + assessment['gap_type'].upper()
    gap = assessment.get('explanation', '')
    if re.search(r'(?:threshold|required value|requirement|target value).{0,45}(?:not specified|not provided|missing)|'
                 r'(?:no|missing|unspecified).{0,25}(?:threshold|target value)|未(?:提供|指定).{0,12}(?:阈值|要求值)', gap, re.I):
        return 'GAP_REQUIREMENT_INPUT'
    return None


def select_feature_candidates(features, others, limit, max_characters=float('inf'), weight=lambda ref: 0):
    """Reserve fallback slots, then refill unused capacity from either ranked pool."""
    selected_features, selected_others = [], []
    remaining = max_characters
    reserve = min(max(1, limit // 4), max(0, limit - 1))
    feature_cost = min((weight(ref) for ref in features if weight(ref) <= remaining), default=0)
    for ref in others:
        if len(selected_others) >= reserve:
            break
        if weight(ref) <= remaining - feature_cost:
            selected_others.append(ref)
            remaining -= weight(ref)
    for pool, selected in ((features, selected_features), (others, selected_others)):
        for ref in pool:
            if len(selected_features) + len(selected_others) >= limit:
                break
            if ref not in selected and weight(ref) <= remaining:
                selected.append(ref)
                remaining -= weight(ref)
    return selected_features + selected_others


def bound_candidates(new, old=(), limit=8, max_characters=12000, prefer_features=False, *, partition_features=False):
    merged = list({r['chunk_id']: r for r in [*new, *old]}.values())
    old_ids = {r['chunk_id'] for r in old}
    # Expanded top-k usually starts with the old top-k. Reserve room for actual novelty.
    merged.sort(key=lambda ref: (ref['chunk_id'] in old_ids,
                                ref['document_type'] != 'feature_list' if prefer_features else False))
    if partition_features:
        kept = select_feature_candidates(
            [r for r in merged if r['document_type'] == 'feature_list'],
            [r for r in merged if r['document_type'] != 'feature_list'], limit, max_characters, lambda r: len(r['quote']))
    else:
        kept, size = [], 0
        for ref in merged:
            length = len(ref['quote'])
            if len(kept) < limit and size + length <= max_characters:
                kept.append(ref)
                size += length
    selected = {ref['chunk_id'] for ref in kept}
    dropped = [ref['chunk_id'] for ref in merged if ref['chunk_id'] not in selected]
    size = sum(len(ref['quote']) for ref in kept)
    return kept, {'candidate_limit': limit, 'character_limit': max_characters, 'selected_characters': size,
                  'available_count': len(merged), 'dropped_chunk_ids': dropped}
