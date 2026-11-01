from app.schemas.core import ClaimAssessment, ClaimVerdict, ClauseDecision

NC_RESPONSE = 'Not compliant with the requirement. Refer to the cited evidence.'


def aggregate_claims(assessments: list[ClaimAssessment], coverage_complete: bool) -> ClauseDecision:
    # 覆盖失败优先于所有正面证据；空集合绝不能因 all([]) 判 FC。
    if not coverage_complete or not assessments:
        return ClauseDecision.UNKNOWN
    verdicts = {a.verdict for a in assessments}
    if ClaimVerdict.UNKNOWN in verdicts:
        return ClauseDecision.UNKNOWN
    if verdicts == {ClaimVerdict.SUPPORTED}:
        return ClauseDecision.FC
    if verdicts == {ClaimVerdict.CONTRADICTED}:
        return ClauseDecision.NC
    return ClauseDecision.PC
