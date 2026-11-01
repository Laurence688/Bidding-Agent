"""Trusted source formatting and conservative normalization, separate from capability prose."""
import re
from decimal import Decimal
from app.services.result_sources import source_label


def source_suffix(assessments):
    labels = list(dict.fromkeys(source_label(ref.model_dump(mode='json') if hasattr(ref, 'model_dump') else ref)
        for assessment in assessments
        for ref in (assessment.evidence_refs if hasattr(assessment, 'evidence_refs') else assessment['evidence_refs'])))
    return '\nSources: ' + '; '.join(labels) if labels else ''


def response_body(result):
    text = result.response_en or ''
    suffix = source_suffix(result.assessments)
    if suffix and text.endswith(suffix):
        return text[:-len(suffix)]
    # Validation may ignore exact trusted labels embedded in legacy prose.
    for assessment in result.assessments:
        for ref in assessment.evidence_refs:
            text=text.replace(source_label(ref.model_dump(mode='json')), '')
    return text


def number_atoms(text):
    atoms = set()
    def standard(match):
        atoms.add(('standard', re.sub(r'\s+', '', match.group()).upper()))
        return ' '
    text = re.sub(r'\bRFC\s*\d+\b|\bIEEE\s*\d+(?:\.\d+)*(?:[a-z]+)?\b', standard, text, flags=re.I)
    # Count multipliers are decimal. Unit/meaning applicability still requires the semantic verifier.
    for match in re.finditer(r'(?<![\w.])(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)(?:\s*([kKMG])\b)?', text):
        number = Decimal(match.group(1).replace(',', ''))
        scale = {'K': 1000, 'M': 1000000, 'G': 1000000000}.get((match.group(2) or '').upper(), 1)
        atoms.add(('quantity', str((number * scale).normalize())))
    return atoms


def actual_value_present(value, body, unit=''):
    from app.domain.parameters import literal_value_in
    if unit and not value.rstrip().endswith(unit):
        value = value.rstrip() + unit
    return literal_value_in(value, body) or bool(number_atoms(value)) and number_atoms(value).issubset(number_atoms(body))


def display_result(result):
    if not result or 'response_en' not in result:return result
    text=result.get('response_en') or ''
    suffix=source_suffix(result.get('assessments',[]))
    body=text[:-len(suffix)] if suffix and text.endswith(suffix) else text
    return {**result,'display_response':body}
