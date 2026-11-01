def failure_category(code):
    if not code:
        return None
    if code in {'MODEL_TIMEOUT', 'TIMEOUT', 'MODEL_CONNECTION'}:
        return 'timeout'
    if code == 'HTTP_429':
        return 'rate_limit'
    if code in {'OUTPUT_TRUNCATED', 'CONTEXT_LIMIT'}:
        return 'truncation'
    if code in {'OUTPUT_INVALID', 'OUTPUT_STOP_INVALID'}:
        return 'schema'
    if code in {'CLAIM_INCOMPLETE', 'TRANSLATION_AMBIGUOUS'}:
        return 'coverage'
    if code in {'EVIDENCE_MISSING', 'INSUFFICIENT_EVIDENCE', 'ROADMAP_ONLY', 'PROJECT_FACT_MISSING', 'PRODUCT_CONFLICT', 'VERSION_UNCERTAIN', 'LIFECYCLE_CONTEXT_MISSING', 'LIFECYCLE_FACT_UNVERIFIED', 'LIFECYCLE_DURATION_UNPROVEN'}:
        return 'admission'
    if code in {'CITATION_INVALID', 'SEMANTIC_UNSUPPORTED'}:
        return 'reasoning'
    if code in {'STANDARD_DRAFT_ONLY', 'EVIDENCE_INSUFFICIENT'}:
        return 'admission'
    return 'provider'
