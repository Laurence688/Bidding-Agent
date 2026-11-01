"""Readable source labels, with verification status per cited claim."""
def location_text(locator):
    page = locator.get('page_start') or locator.get('physical_page')
    end = locator.get('page_end')
    return ' · '.join(str(v) for v in [
        ' / '.join(locator.get('section_path') or []) or locator.get('section'),
        f'p.{page}' + (f'–{end}' if end and end != page else '') if page else None,
        locator.get('sheet'), locator.get('cells'),
        f'table {locator["table"]}' if locator.get('table') else None,
        f'row {locator["row"]}' if locator.get('row') else None,
        f'paragraph {locator.get("paragraph_start") or locator["paragraph"]}' + (f'–{locator["paragraph_end"]}' if locator.get('paragraph_end') and locator.get('paragraph_end')!=locator.get('paragraph_start') else '') if locator.get('paragraph_start') or locator.get('paragraph') else None,
        '; '.join((f'table {v["table"]} rows {v.get("row_start",v.get("row"))}–{v.get("row_end",v.get("row_start",v.get("row")))}' if v.get('table') else '') for v in locator.get('segments',[]) if v.get('table')) or None,
    ] if v) or '定位未标注'


def result_sources(result):
    if not result:
        return []
    sources = {}
    checks = result.get('checks', [])
    for assessment in [*result.get('assessments', []), {'verdict': 'UNKNOWN', 'evidence_refs': result.get('candidates', [])}]:
        for ref in assessment.get('evidence_refs', []):
            related = [c for c in checks if c['ref_id'] == ref['id']]
            verified = related and all(c['locatable'] and c['literal_match'] and c['applicable'] and c['semantic_support'] == 'SUPPORTED' for c in related)
            status = ('verified_support' if assessment['verdict'] == 'SUPPORTED' else 'verified_gap') if verified and assessment['verdict'] != 'UNKNOWN' else 'candidate'
            key = (ref['chunk_id'], ref.get('quote', ''))
            if key in sources and status == 'candidate':
                continue
            sources[key] = {**ref, 'location': location_text(ref['locator']), 'status': status}
    return list(sources.values())


def source_label(ref):
    return f'{ref["source_name"]} [{location_text(ref["locator"])}; {ref.get("version_label", "未标注")}]'
