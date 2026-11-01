"""A narrow Roadmap exception for dated lifecycle facts, never future features."""
import re
from datetime import date

LIFECYCLE = re.compile(r'\b(?:EOL|EOS|end[ -]of[ -](?:life|sale|support)|life[ -]?cycle)\b|生命周期|停产|停止销售|停止支持', re.I)


def event_kind(label):
    normalized = re.sub(r'[ -]', '', label).lower()
    return {'eol': 'life', 'endoflife': 'life', 'eos': 'sale', 'endofsale': 'sale',
            'endofsupport': 'support', '停产': 'production', '停止销售': 'sale', '停止支持': 'support'}.get(normalized)


def roadmap_reason(ref, context, claim_text):
    if not LIFECYCLE.search(claim_text or ''):
        return 'ROADMAP_ONLY'
    life = context.lifecycle_context
    if not life.reference_date or not life.exact_model or not life.source_version_id:
        return 'LIFECYCLE_CONTEXT_MISSING'
    if life.source_version_id != ref.source_version_id:
        return 'VERSION_UNCERTAIN'
    # A family selection does not silently become an arbitrary chassis selection.
    model = life.exact_model.upper()
    valid_model = model == context.product_id.upper() or (context.product_id == 'M6000-SE' and re.fullmatch(r'M6000-\d+SE', model))
    if not valid_model:
        return 'PRODUCT_CONFLICT'
    model_pattern = r'(?<![\w-])' + re.escape(model) + r'(?![\w-])'
    if not re.search(model_pattern, ref.quote, re.I):
        return 'PRODUCT_CONFLICT'
    # A mixed model row/page is ambiguous; do not pair another column's date with this model.
    models = {s.upper() for s in re.findall(r'\bM6000-\d+[A-Za-z]*\b|\b6120H-S\b', ref.quote, re.I)}
    if models - {model}:
        return 'PRODUCT_CONFLICT'
    date_pattern = r'\b(20\d{2})(?:[-/](\d{1,2})(?:[-/](\d{1,2}))?|\s*Q([1-4]))\b'
    matches = list(re.finditer(date_pattern, ref.quote, re.I))
    # Date must be directly attached to a lifecycle event label, never a release/GA date.
    required_kinds = {event_kind(m.group()) for m in LIFECYCLE.finditer(claim_text)} - {None}
    if len(required_kinds) != 1:
        return 'LIFECYCLE_FACT_UNVERIFIED'
    attached = [re.match(r'^[\s():|=\-–]*' + date_pattern, ref.quote[event.end():], re.I)
                for event in LIFECYCLE.finditer(ref.quote) if event_kind(event.group()) in required_kinds]
    attached = [m for m in attached if m]
    if len(matches) != 1 or len(attached) != 1:
        return 'LIFECYCLE_FACT_UNVERIFIED'
    for term in [r'\bEOL\b|end[ -]of[ -]life', r'end[ -]of[ -]support', r'end[ -]of[ -]sale', r'\bEOS\b']:
        if re.search(term, claim_text, re.I) and not re.search(term, ref.quote, re.I):
            return 'LIFECYCLE_FACT_UNVERIFIED'
    duration = re.search(r'\b(\d+)\s*years?\b|(\d+)\s*年', claim_text, re.I)
    if duration:
        negative_within = bool(re.search(r'\bnot\b.{0,80}\bwithin\b|不.{0,30}内.{0,20}(停产|停止)', claim_text, re.I))
        if not negative_within and not re.search(r'at least|minimum|no earlier than|not less than|至少|不少于', claim_text, re.I):
            return 'LIFECYCLE_DURATION_UNPROVEN'
        # More than one date needs a structured row; never pair dates by position.
        if len(matches) != 1:
            return 'LIFECYCLE_FACT_UNVERIFIED'
        match = matches[0]
        try:
            # A quarter promises no precise day. Use its earliest possible date.
            end = date(int(match[1]), (int(match[4])-1)*3+1 if match[4] else int(match[2]), int(match[3] or 1))
            years = int(duration[1] or duration[2])
            target_year = life.reference_date.year + years
            try:
                minimum = life.reference_date.replace(year=target_year)
            except ValueError:
                minimum = date(target_year, 3, 1)
        except ValueError:
            return 'LIFECYCLE_FACT_UNVERIFIED'
        if end < minimum or (negative_within and end == minimum):
            return 'LIFECYCLE_DURATION_UNPROVEN'
    # Admission only allows consideration; the judge still checks dates/duration and semantic support.
    return None
