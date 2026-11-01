import re


def alternative_text(text):
    # Version/quantity lower bounds are one requirement, not two alternative features.
    mask = lambda match: re.sub(r'\bor\b', '  ', match.group(), flags=re.I)
    semantic = re.sub(r'(\b\d+[\w.-]*)\s+or\s+(?:later|higher|newer|above|more\s+advanced)\b', mask, text, flags=re.I)
    semantic = re.sub(r'\bwhether\s+or\s+not\b', mask, semantic, flags=re.I)
    if re.search(r'\b(?:specif(?:y|ies)|state|indicate|declare)\b', semantic, re.I):
        semantic = re.sub(r'\bwith\s+or\s+without\b', mask, semantic, flags=re.I)
    return semantic


def requires_or_structure(text):
    return bool(re.search(r'\bor\b|或者|或等效', alternative_text(text), re.I))


def valid_alternatives(claim):
    groups = claim.alternative_groups or ([claim.alternatives] if claim.relation == 'OR' else [])
    valid = bool(groups) and all(len(set(group)) >= 2 and all(a and a in claim.text for a in group) for group in groups)
    if not valid:
        return False
    operators = {m.start() for m in re.finditer(r'\bor\b|或者|或等效', alternative_text(claim.text), re.I)}
    covered = set()
    for group in groups:
        spans = []
        for alternative in group:
            matches = list(re.finditer(re.escape(alternative), claim.text))
            if len(matches) != 1:
                return False
            spans.append(matches[0].span())
        for left, right in zip(spans, spans[1:]):
            between = {index for index in operators if left[1] <= index < right[0]}
            if len(between) != 1 or between & covered or re.search(r'\band\b|;|；', claim.text[left[1]:right[0]], re.I):
                return False
            covered.update(between)
    return covered == operators


def ignorable_boundary(raw, index):
    char = raw[index]
    if char.isspace() or char in ';；':
        return True
    # Decimal points, thousands separators and all logical words remain mandatory.
    if char in ',.，。':
        return not (index > 0 and index + 1 < len(raw) and raw[index-1].isdigit() and raw[index+1].isdigit()) and (
            index + 1 == len(raw) or raw[index+1].isspace())
    return False


def project_fact_reason(text, model_flag):
    obligation = re.search(r'\b(?:shall|must|will|and)\s+(?:also\s+)?(?:submit|deliver|supply|commit|undertake)\b|'
        r'\b(?:shall|must)\b.{0,50}\b(?:be supplied|be provided|licenses? included|include.{0,12}licen)\b|'
        r'\b(?:provide|include|purchase)\b.{0,35}\b(?:licen[cs]es?|roadmap document|equipment|routers?)\b|'
        r'本项目.{0,15}(?:提供|供货|许可)|提交.{0,15}(?:文件|路线图)|承诺|交付', text, re.I)
    if obligation:
        return True, 'explicit_project_obligation'
    inquiry = re.search(r'\b(?:specif(?:y|ies)|state|indicate|describe|list|clarify|declare)\b|请说明|请列出', text, re.I)
    capability = re.search(r'\b(?:support\w*|capabilit\w*|features?|protocols?|standards?|weight|dimensions?|whether|capacity)\b|支持|能力|标准|重量|尺寸', text, re.I)
    if inquiry and capability:
        return False, 'product_capability_inquiry'
    return bool(model_flag), 'model_classification'
