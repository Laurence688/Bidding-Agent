import re
from app.domain.decisions import NC_RESPONSE
from app.workflows.routing import product_conflicts, product_name_pattern
from app.services.product_catalog import product_names
from app.services.result_sources import source_label
from app.domain.requirement_relations import requires_or_structure, valid_alternatives, ignorable_boundary
from app.domain.response_contract import response_body, number_atoms, actual_value_present
from app.domain.parameters import capability_text, needs_parameter_value, parameter_facts


def validate_coverage(raw_text, claims):
    if not claims:
        return False
    covered = set()
    for claim in claims:
        if not claim.source_spans:
            return False
        pieces = []
        previous_end = None
        for span in claim.source_spans:
            if not (0 <= span.start < span.end <= len(raw_text)):
                return False
            if previous_end is not None and span.start != previous_end:
                return False
            previous_end = span.end
            covered.update(range(span.start, span.end))
            pieces.append(raw_text[span.start:span.end])
        if claim.text != ''.join(pieces):
            return False
        if requires_or_structure(claim.text):
            if not valid_alternatives(claim):
                return False
    return all(ignorable_boundary(raw_text, index) or index in covered for index, char in enumerate(raw_text))


def validate_response(result, product_id, products):
    return not response_issues(result, product_id, products)


def response_issues(result, product_id, products):
    if result.decision.value == 'UNKNOWN':
        return ['UNKNOWN_NOT_DELIVERABLE']
    if not result.coverage_complete or not result.checks or not all(c.locatable and c.literal_match and c.applicable and c.semantic_support == 'SUPPORTED' for c in result.checks):
        return ['CITATION_OR_COVERAGE_INVALID']
    if result.decision.value == 'NC':
        return [] if result.response_en == NC_RESPONSE else ['NC_WORDING_INVALID']
    text = response_body(result)
    # Remove only exact trusted labels/product names from numeric policing. A page number
    # must not license an invented capability number elsewhere in the sentence.
    issues = []
    if not text.strip():
        issues.append('EMPTY_RESPONSE')
    if re.search(r'\bSOC_CLAIM_\d+\b', text, re.I) or any(
        len(c.id) == 36 and c.id.lower() in text.lower() for c in result.claims
    ):
        issues.append('INTERNAL_CLAIM_ID_IN_RESPONSE')
    if re.search(r'[\u4e00-\u9fff]', text):
        issues.append('BODY_LANGUAGE_OR_SOURCE_FORMAT')
    if product_conflicts(text, product_id, products):
        issues.append('PRODUCT_CONFLICT')
    names = {product_id}
    for product in products:
        if product['id'] == product_id:
            names.update(product_names(product))
    text = re.sub('|'.join(product_name_pattern(name) for name in sorted(names, key=len, reverse=True)), '', text, flags=re.I)
    permitted = result.raw_text + ' '.join(capability_text(ref.quote) for a in result.assessments for ref in a.evidence_refs)
    permitted += ' ' + ' '.join(v.get('raw_value', '') + v.get('unit', '') for v in result.diagnostics.get('requirement_values', []))
    # 新数字不能由语言模型自行添加；此门不替代独立语义核对。
    if not number_atoms(text).issubset(number_atoms(permitted)):
        issues.append('UNSUPPORTED_NUMBER')
    context = {'requirement_values': result.diagnostics.get('requirement_values', [])}
    claims = {c.id: c for c in result.claims}
    for assessment in result.assessments:
        claim = claims.get(assessment.claim_id)
        if not needs_parameter_value(claim.text if claim else result.raw_text, context, len(result.claims)):
            continue
        facts = parameter_facts([v.model_dump(mode='json') for v in assessment.parameter_values])
        if not facts:
            issues.append('PARAMETER_VALUE_MISSING')
        elif any(not actual_value_present(v['actual_value'], text, v.get('unit', '')) for v in facts):
            issues.append('PARAMETER_VALUE_OMITTED')
    return list(dict.fromkeys(issues))
