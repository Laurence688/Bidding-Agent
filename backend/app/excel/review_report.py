"""An independent review report; this never bypasses formal workbook export gates."""
from io import BytesIO
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from app.services.result_sources import result_sources, source_label

STATUS = {'verified_support': '核验支持（子项）', 'verified_gap': '核验不满足（子项）', 'candidate': '候选/未核验支持'}


def parameter_display(value):
    unit = value.get('unit', '')
    return value['actual_value'] + (' ' + unit if unit and not value['actual_value'].endswith(unit) else '')


def review_workbook(data):
    workbook = Workbook()
    summary = workbook.active
    summary.title = '运行概览'
    report = data['report']
    summary.append(['审阅报告（非正式投标导出）'])
    summary.append(['运行 ID', report['run_id']])
    summary.append(['条款总数', report['total']])
    summary.append(['结论', '机器条数', '机器占比'])
    counts = report['machine']['counts']
    denominator = sum(counts.values())
    for decision, count in counts.items():
        summary.append([decision, count, count / denominator if denominator else None])
        summary.cell(summary.max_row, 3).number_format = '0.00%'
    summary.append(['说明', 'C 列为人工备注；候选来源不等于支持；原始运行结果未改写。'])
    results = workbook.create_sheet('逐条结果')
    results.append(['序号', '原始行', '人工批注', '原文', '机器结论', '英文应答', '原因', '来源', '审核状态', '原表位置', '结果ID', '要求值与位置', '产品实际值与条件', '数值判断与来源片段'])
    refs = workbook.create_sheet('引用明细')
    refs.append(['序号', '原表位置', '核验状态', '文档', '页码/章节', '资料版本', '来源版本ID', '引用原文', '片段ID'])
    for index, row in enumerate(data['clauses'], 1):
        result = row.get('result') or {}
        coordinate = row.get('coordinate') or {}
        address = f'{coordinate.get("sheet", "")}!{coordinate.get("input_cell", "")}'
        sources = result_sources(result)
        labels = '\n'.join(f'{STATUS[s["status"]]}：{source_label(s)}' for s in sources)
        required = '\n'.join(f'{v["cell"]}: {v.get("comparator", "unspecified")} {v["raw_value"]} {v.get("unit", "")}'
            for v in row.get('source_context', {}).get('requirement_values', result.get('diagnostics', {}).get('requirement_values', [])))
        actual = '\n'.join(f'{v["name"]}: {parameter_display(v)} ({v.get("conditions", "")}) [{a["verdict"]}]'
            for a in result.get('assessments', []) for v in a.get('parameter_values', []))
        comparisons = '\n'.join(f'{a["explanation"]} | ' + ', '.join(v['evidence_chunk_id'] for v in a['parameter_values'])
            for a in result.get('assessments', []) if a.get('parameter_values'))
        results.append([index, coordinate.get('row'), (row.get('annotation') or {}).get('notes', ''), row['raw_text'],
                        result.get('decision', 'PENDING'), result.get('response_en'), '; '.join(result.get('reason_codes', [])),
                        labels, row['review_status'], address, row.get('result_id'), required, actual, comparisons])
        for ref in sources:
            refs.append([index, address, STATUS[ref['status']], ref['source_name'], ref['location'], ref['version_label'],
                         ref['source_version_id'], ref['quote'], ref['chunk_id']])
    for sheet in workbook:
        sheet.freeze_panes = 'A2'
        sheet.auto_filter.ref = sheet.dimensions
        for row in sheet:
            for cell in row:
                if isinstance(cell.value, str):
                    # Raw spreadsheet/model text must not become executable Excel formulas.
                    cell.data_type = 's'
                cell.alignment = Alignment(vertical='top', wrap_text=True)
                if cell.row == 1:
                    cell.font = Font(bold=True, color='FFFFFF')
                    cell.fill = PatternFill('solid', fgColor='243B63')
        for column in sheet.columns:
            sheet.column_dimensions[column[0].column_letter].width = 24
    for column in ['D', 'F', 'H']:
        results.column_dimensions[column].width = 64
    refs.column_dimensions['H'].width = 85
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()
