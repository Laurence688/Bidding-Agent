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


def bound_candidates(new, old=(), limit=8, max_characters=12000, prefer_features=False):
    merged = list({r['chunk_id']: r for r in [*new, *old]}.values())
    old_ids = {r['chunk_id'] for r in old}
    # Expanded top-k usually starts with the old top-k. Reserve room for actual novelty.
    merged.sort(key=lambda ref: (ref['chunk_id'] in old_ids,
                                ref['document_type'] != 'feature_list' if prefer_features else False))
    kept, dropped, size = [], [], 0
    for ref in merged:
        length = len(ref['quote'])
        if len(kept) >= limit or size + length > max_characters:
            dropped.append(ref['chunk_id'])
        else:
            kept.append(ref)
            size += length
    return kept, {'candidate_limit': limit, 'character_limit': max_characters, 'selected_characters': size,
                  'available_count': len(merged), 'dropped_chunk_ids': dropped}
