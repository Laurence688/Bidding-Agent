from app.schemas.core import EvidenceAdmission
from app.workflows.routing import product_conflicts
from app.rag.lifecycle import roadmap_reason
import re


def admit_evidence(refs, context, knowledge, required_version=None, project_fact=False, products=None, claim_text=''):
    admitted, excluded, reasons = [], [], []
    for ref in refs:
        reason = None
        if ref.product_id != context.product_id:
            reason = 'PRODUCT_CONFLICT'
        elif project_fact:
            reason = 'PROJECT_FACT_MISSING'
        elif ref.document_type == 'roadmap':
            reason = roadmap_reason(ref, context, claim_text)
        elif product_conflicts(knowledge.product_scope_text(ref), context.product_id, products or []):
            reason = 'PRODUCT_CONFLICT'
        elif re.search(r'\b(?:IETF|RFC)\b', claim_text, re.I) and re.search(r'\bInternet[ -]Draft\b|\bdraft-ietf-', ref.quote, re.I):
            reason = 'STANDARD_DRAFT_ONLY'
        if not reason and required_version and ref.version_label != required_version:
            reason = 'VERSION_UNCERTAIN'
        if not reason and not knowledge.verify_reference(ref):
            reason = 'CITATION_INVALID'
        if reason:
            excluded.append(ref)
            reasons.append(reason)
        else:
            admitted.append(ref)
    if not admitted and not reasons:
        reasons.append('EVIDENCE_MISSING')
    return EvidenceAdmission(admitted=admitted, excluded=excluded, reason_codes=list(dict.fromkeys(reasons)))
