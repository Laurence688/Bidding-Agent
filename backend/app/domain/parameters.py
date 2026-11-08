"""Parameter facts are extracted from product evidence, never from requested values."""
import re


def is_parameter_requirement(text, context=None):
    values = (context or {}).get('requirement_values', [])
    if any(re.search(r'\d', v.get('raw_value', '')) for v in values):
        return True
    if re.search(r'(?<!\w)[+-]?\d[\d,.]*\s*(?:%|°C\b|Gbps\b|Mbps\b|pps\b|ms\b|kg\b|mm\b|V\b)|'
                 r'(?:\bat least\b|\bat most\b|[<>≤≥]=?)\s*\d|'
                 r'\b(?:minimum|maximum)\s+(?:Class\b|(?:total\s+)?(?:rack units|slots?|ports?|peers?|neighbors?))', text, re.I):
        return True
    # Monitoring or limiting a count is a capability, unless a threshold/measurement is
    # actually supplied. "minimum SNMP/XML" likewise lists required protocols.
    without_count_operations = text
    if not re.search(r'(?<![A-Za-z0-9.])\d', text):
        without_count_operations = re.sub(r'\b(?:monitor|count|limit)\s+(?:the\s+)?number of\b', '', text, flags=re.I)
    return bool(re.search(r'\b(?:capacity|number of|entries|weight|dimensions?|latency|throughput|buffer size)\b|'
                          r'容量|数量|重量|尺寸|时延|吞吐', without_count_operations, re.I))


def parameter_facts(values):
    """Boolean support markers cannot stand in for an actual capacity or class."""
    markers = {'support', 'supported', 'yes', 'true', 'no', 'false', 'not support', 'not supported', 'unsupported', '支持', '不支持'}
    return [v for v in values if v['actual_value'].strip().casefold() not in markers]


def needs_parameter_value(text, context, claim_count):
    # A row threshold is not a request for a number on every boolean child claim.
    if is_parameter_requirement(text):
        return True
    row_parameter = is_parameter_requirement(text, context)
    boolean_support = bool(re.search(r'\bsupport(?:s|ed)?\b|是否支持', text, re.I)) and not re.search(
        r'(?<![A-Za-z0-9])\d|\b(?:peers?|neighbors?|entries|sessions?|ports?|routes?|limits?|scale)\b', text, re.I)
    return row_parameter and (claim_count == 1 or not boolean_support)


def missing_parameter_threshold(text, context):
    """Catch explicit Excel min/max labels that name a metric but supply no bound."""
    if not re.match(r'^\s*(?:the\s+)?(?:minimum|maximum)\s+(?:number\b|(?:[\w/-]+\s+){0,4}(?:capacity|count|rack units)\b)', text, re.I):
        return False
    inline = re.sub(r'\b(?:IEEE|RFC)\s*[\d.]+[a-z]*\b', '', text, flags=re.I)
    supplied = [inline, *(value.get('raw_value', '') for value in (context or {}).get('requirement_values', []))]
    return not any(re.search(r'(?<![\w.])\d|\b(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|'
                             r'(?:thir|four|fif|six|seven|eigh|nine)teen|twenty|thirty|forty|fifty|sixty|seventy|'
                             r'eighty|ninety|hundred|thousand|million|billion|same as)\b', value, re.I) for value in supplied)


def literal_value_in(value, text):
    # Boundaries prevent requested 512 from matching a supported value of 5120.
    return bool(value.strip()) and bool(re.search(r'(?<![\w.])' + re.escape(value.strip()) + r'(?!\w|\.\d)', text))


def capability_text(quote):
    # Structured record metadata is a locator, not a product capability.
    return re.sub(r'^(?:Source|Page|Product|Line No\.|Row|Version):[^\n]*(?:\n|$)', '', quote, flags=re.M)


def validate_parameter_values(values, refs):
    by_chunk = {ref.chunk_id: ref for ref in refs}
    for value in values:
        ref = by_chunk.get(value['evidence_chunk_id'])
        if not ref:
            return False
        quote = capability_text(ref.quote)
        structured_values = re.findall(r'^Value:\s*(.*)$', quote, re.M) if ref.document_type == 'feature_list' else []
        actual_text = '\n'.join(structured_values) if structured_values else quote
        unit = value.get('unit', '')
        variants = [value['actual_value']]
        if unit:
            variants.extend([value['actual_value'] + unit, value['actual_value'] + ' ' + unit])
        if not any(literal_value_in(v, actual_text) for v in variants):
            return False
        if unit and not re.search(r'(?<![A-Za-z])' + re.escape(unit) + r'(?![A-Za-z0-9])', quote):
            return False
        if value.get('conditions') and value['conditions'] not in ref.quote:
            return False
    return True
