from app.schemas.core import ClauseResult
from app.domain.output_checks import validate_response
from app.domain.identifiers import content_hash


def result_blockers(result, product_id, products, knowledge):
    blockers = []
    if result.decision.value == 'UNKNOWN':
        blockers.append('UNKNOWN')
    if 'PRODUCT_CONFLICT' in result.reason_codes:
        blockers.append('PRODUCT_CONFLICT')
    if not result.output_valid or not validate_response(result, product_id, products):
        blockers.append('OUTPUT_INVALID')
    for assessment in result.assessments:
        if assessment.verdict.value != 'UNKNOWN' and not assessment.evidence_refs:
            blockers.append('CITATION_INVALID')
        for ref in assessment.evidence_refs:
            if not knowledge.verify_reference(ref) or ref.product_id != product_id:
                blockers.append('SOURCE_INVALID')
    return list(dict.fromkeys(blockers))


def validation_digest(result, source_states):
    return content_hash({'result': result, 'sources': source_states})
