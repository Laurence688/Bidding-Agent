"""Display-only task category from immutable input evidence."""
def display_category(mode,snapshot):
    if mode!='TRANSLATION':return mode
    kind=snapshot.get('input_kind')
    if kind in {'file','text'}:return 'TRANSLATION_'+kind.upper()
    files=snapshot.get('files')
    if not isinstance(files,list):return 'TRANSLATION_UNKNOWN'
    if any(isinstance(f,dict) and isinstance(f.get('id'),str) and f['id']!='text' for f in files):return 'TRANSLATION_FILE'
    if (files and all(isinstance(f,dict) and f.get('id')=='text' for f in files)) or str(snapshot.get('raw_text') or snapshot.get('resolved_source') or '').strip():return 'TRANSLATION_TEXT'
    return 'TRANSLATION_UNKNOWN'
