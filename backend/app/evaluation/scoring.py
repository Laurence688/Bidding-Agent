SCORING_VERSION = 'id-aligned-v2'


def score(cases, results):
    labels = ['FC', 'PC', 'NC', 'UNKNOWN']
    matrix = {a: {p: 0 for p in labels} for a in labels}
    errors, coverage, precision, recall, hits, parameters = [], [], [], [], [], []
    counts = dict(completed=0, failed=0, cancelled=0, interrupted=0, not_executed=0)
    by_id = {}
    for result in results:
        key = result.get('case_id')
        if key is not None:
            if key in by_id:
                raise ValueError('Duplicate evaluation result case_id')
            by_id[key] = result
    false_fc = false_pc = 0
    for index, case in enumerate(cases):
        # Legacy records remain readable, with an explicit alignment warning.
        result = by_id.get(case['case_id']) if case.get('case_id') else (results[index] if index < len(results) else None)
        status = result.get('status', 'completed') if result else 'not_executed'
        if result and result.get('diagnostics', {}).get('failure'):
            status = 'failed'
        if status != 'completed':
            counts[status if status in counts else 'not_executed'] += 1
            continue
        predicted = result.get('decision')
        if predicted not in labels:
            counts['failed'] += 1
            continue
        counts['completed'] += 1
        expected = case['gold']['decision']
        matrix[expected][predicted] += 1
        if expected != predicted:
            errors.append({'case_index': index, 'case_id': case.get('case_id'), 'expected': expected, 'actual': predicted, 'reason_codes': result.get('reason_codes', [])})
        false_fc += predicted == 'FC' and expected != 'FC'
        false_pc += predicted == 'PC' and expected in {'NC', 'UNKNOWN'}
        gold_claims = case['gold'].get('claims', [])
        if gold_claims:
            texts = [c['text'].strip() for c in result.get('claims', [])]
            coverage.append(sum(any(c.strip() in text for text in texts) for c in gold_claims) / len(gold_claims))
        expected_refs = set(case['gold'].get('evidence_ids', []))
        actual_refs = {r['id'] for a in result.get('assessments', []) for r in a.get('evidence_refs', [])}
        if expected_refs:
            precision.append(len(actual_refs & expected_refs) / len(actual_refs) if actual_refs else 0)
            recall.append(len(actual_refs & expected_refs) / len(expected_refs))
            hits.append(float(bool(expected_refs & {r['id'] for r in result.get('candidates', [])})))
        gold_params = case['gold'].get('parameters', [])
        if gold_params:
            actual = [p for a in result.get('assessments', []) for p in a.get('parameter_values', [])]
            fields = ('name', 'actual_value', 'unit', 'conditions')
            parameters.append(sum(any(all(p.get(k, '') == a.get(k, '') for k in fields) for a in actual) for p in gold_params) / len(gold_params))
    mean = lambda values: sum(values) / len(values) if values else None
    per_class = {}
    for label in labels:
        tp, actual_count, predicted_count = matrix[label][label], sum(matrix[label].values()), sum(matrix[a][label] for a in labels)
        per_class[label] = {'precision': tp / predicted_count if predicted_count else None, 'recall': tp / actual_count if actual_count else None}
    complete = counts['completed'] == len(cases)
    return {'case_count': len(cases), **{k + '_count': v for k, v in counts.items()}, 'complete': complete,
        'confusion_matrix': matrix, 'accuracy': sum(matrix[x][x] for x in labels) / len(cases) if cases else None,
        'per_class': per_class, 'false_affirmatives': false_fc + false_pc, 'false_fc': false_fc, 'false_pc': false_pc, 'errors': errors,
        'claim_coverage': mean(coverage), 'claim_coverage_method': 'literal_span_only',
        'citation_correctness': mean(precision), 'citation_recall': mean(recall), 'selected_candidate_gold_hit': mean(hits),
        'parameter_exact_match': mean(parameters), 'citation_method': 'gold_evidence_id_match',
        'scoring_version': SCORING_VERSION, 'legacy_alignment': any(not c.get('case_id') for c in cases),
        'input_warnings': [{'case_id': c.get('case_id'), 'missing_fields': c.get('input_gaps', [k for k in ('source_context', 'lifecycle_context') if k not in c])}
            for c in cases if c.get('input_gaps') or any(k not in c for k in ('source_context', 'lifecycle_context'))],
        'key_gap_quality': None, 'human_edit_rate': None, 'quality_accepted': False,
        'acceptance_note': '需独立人工金标准及约定阈值；自动字段比对不替代语义验收'}
