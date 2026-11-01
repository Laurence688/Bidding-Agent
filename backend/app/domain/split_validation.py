"""Deterministic quote alignment; never paraphrase or guess repeated text."""
import re
from uuid import NAMESPACE_URL, uuid5
from app.domain.identifiers import content_hash
from app.domain.output_checks import validate_coverage
from app.schemas.core import Claim
from app.domain.requirement_relations import requires_or_structure, valid_alternatives, ignorable_boundary


def resolve_claims(raw, items, clause_id):
    claims, errors, resolved, covered = [], [], 0, set()
    for index, source in enumerate(items):
        item = dict(source)
        text, spans = item['text'], item.get('source_spans', [])
        exact = bool(spans) and all(0 <= s['start'] < s['end'] <= len(raw) for s in spans)
        exact = exact and all(a['end'] == b['start'] for a, b in zip(spans, spans[1:]))
        exact = exact and ''.join(raw[s['start']:s['end']] for s in spans) == text
        if not exact:
            starts = [m.start() for m in re.finditer('(?=' + re.escape(text) + ')', raw)] if text else []
            if len(starts) == 1:
                spans = [{'start': starts[0], 'end': starts[0] + len(text)}]
                resolved += 1
            else:
                spans = []
                errors.append('AMBIGUOUS_QUOTE' if starts else 'QUOTE_NOT_FOUND')
        item['source_spans'] = spans
        claim = Claim(**item, clause_id=clause_id, id=str(uuid5(NAMESPACE_URL, clause_id + ':' + str(index) + ':' + content_hash(item))))
        claims.append(claim)
        for span in claim.source_spans:
            covered.update(range(span.start, span.end))
        if requires_or_structure(text):
            if not valid_alternatives(claim):
                errors.append('OR_STRUCTURE_INVALID')
    missing = [i for i, char in enumerate(raw) if not ignorable_boundary(raw, i) and i not in covered]
    gaps = []
    for index in missing:
        if gaps and gaps[-1]['end'] == index:
            gaps[-1]['end'] += 1
        else:
            gaps.append({'start': index, 'end': index + 1})
    if gaps:
        errors.append('UNCOVERED_TEXT')
    markers = [{'text': m.group(), 'start': m.start(), 'end': m.end(),
                'covered': all(i in covered for i in range(m.start(), m.end()))}
               for m in re.finditer(r'\b(?:if|when|unless|provided|not|except|only|at least|at most)\b|\d+(?:\.\d+)?(?:\s*(?:ms|Gbps|years?))?|不得|至少|条件', raw, re.I)]
    complete = not errors and validate_coverage(raw, claims)
    return claims, {'schema_valid': True, 'coverage_complete': complete, 'raw_characters': len(raw),
                    'claim_count': len(claims), 'offsets_resolved': resolved, 'error_codes': list(dict.fromkeys(errors)),
                    'uncovered_spans': [{**g, 'text': raw[g['start']:g['end']]} for g in gaps],
                    'ignored_punctuation': [{'index': i, 'text': c} for i, c in enumerate(raw) if i not in covered and not c.isspace() and ignorable_boundary(raw, i)],
                    'source_spans': [[s.model_dump() for s in c.source_spans] for c in claims],
                    'condition_number_negation_markers': markers, 'semantic_scope_verified': False}
