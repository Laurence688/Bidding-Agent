"""Only original cell contents may enter model context; never human annotations."""
def model_source_context(source):
    if not source:
        return {}
    return {'sheet': source.get('sheet'),
            'preceding_context': [{'raw_text': item['raw_text'], 'coordinate': {k: item.get('coordinate', {}).get(k) for k in ['sheet', 'input_cell']}}
                                  for item in source.get('preceding_context', [])],
            'adjacent_cells': [{'cell': item['cell'], 'text': item['text']} for item in source.get('adjacent_cells', [])],
            'requirement_values': [{k: item.get(k, '') for k in ('cell', 'raw_value', 'unit', 'comparator')}
                                   for item in source.get('requirement_values', [])]}


def context_query(source):
    clean = model_source_context(source)
    return '\n'.join([item['raw_text'] for item in clean.get('preceding_context', [])] +
                     [item['text'] for item in clean.get('adjacent_cells', [])])
