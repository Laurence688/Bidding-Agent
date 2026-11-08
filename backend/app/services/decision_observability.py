"""Agent decision statistics from immutable result lineage, never from review labels."""
import re
from collections import Counter, defaultdict
from contextlib import contextmanager

from app.domain.errors import DomainError

DECISIONS = ('FC', 'PC', 'NC', 'UNKNOWN')
NON_FC = ('PC', 'NC', 'UNKNOWN')
CATEGORIES = {
    'capability_gap': '能力或指标不满足',
    'evidence_gap': '证据不足',
    'evidence_conflict': '证据相互矛盾',
    'project_fact': '项目事实待补证',
    'requirement_input': '要求值或条件缺失',
    'parameter_issue': '实际参数值缺失或校验失败',
    'input_issue': '原文、翻译或拆分问题',
    'scope_conflict': '型号或版本适用性问题',
    'citation_issue': '引用或来源校验失败',
    'model_issue': '模型调用或生成异常',
    'output_issue': '输出校验未通过',
    'other': '其他已记录原因',
    'unrecorded': '原因未记录',
}
CODE_GROUPS = {
    'evidence_gap': {'EVIDENCE_MISSING', 'INSUFFICIENT_EVIDENCE', 'EVIDENCE_INSUFFICIENT', 'EVIDENCE_GAP',
                     'NO_EVIDENCE', 'NO_RELEVANT_EVIDENCE', 'INSUFFICIENT_EVIDENCE_FOR_CONDITION',
                     'INSUFFICIENT_EVIDENCE_FOR_MINIMUM', 'INSUFFICIENT_EVIDENCE_FOR_MINIMUM_CAPACITY', 'ROADMAP_ONLY'},
    'evidence_conflict': {'CONFLICTING_EVIDENCE', 'EVIDENCE_CONFLICT'},
    'project_fact': {'PROJECT_FACT_MISSING'},
    'requirement_input': {'REQUIREMENT_VALUE_MISSING', 'REQUIREMENT_THRESHOLD_MISSING', 'REQUIREMENT_AMBIGUOUS'},
    'parameter_issue': {'PARAMETER_VALUE_MISSING', 'PARAMETER_VALUE_INVALID'},
    'input_issue': {'CLAIM_INCOMPLETE', 'TRANSLATION_AMBIGUOUS', 'AMBIGUOUS_CLAIM', 'UNDERSPECIFIED_CLAIM',
                    'INPUT_INVALID', 'UNTRUSTED_INPUT'},
    'scope_conflict': {'PRODUCT_CONFLICT', 'VERSION_UNCERTAIN'},
    'citation_issue': {'CITATION_INVALID', 'SOURCE_INVALID', 'CITATION_OR_COVERAGE_INVALID'},
    'model_issue': {'MODEL_UNAVAILABLE', 'MODEL_CONFIG_MISSING', 'AUTH_FAILED', 'PROVIDER_CONFIG_ERROR',
                    'MODEL_TIMEOUT', 'MODEL_CONNECTION', 'PROVIDER_UNEXPECTED_ERROR',
                    'ATTEMPT_BUDGET_EXHAUSTED', 'ATTEMPT_INTERRUPTED', 'CONTEXT_LIMIT'},
    'output_issue': {'OUTPUT_INVALID', 'OUTPUT_TRUNCATED', 'OUTPUT_STOP_INVALID', 'PARAMETER_VALUE_OMITTED',
                     'BODY_LANGUAGE_OR_SOURCE_FORMAT', 'UNSUPPORTED_NUMBER', 'SEMANTIC_UNSUPPORTED', 'EMPTY_RESPONSE', 'NC_WORDING_INVALID',
                     'INTERNAL_CLAIM_ID_IN_RESPONSE'},
}
CODE_CATEGORY = {code: category for category, codes in CODE_GROUPS.items() for code in codes}
NEUTRAL_CODES = {'', 'OK', 'SUPPORTED'}
GAP_CATEGORIES = {'evidence': 'evidence_gap', 'requirement_input': 'requirement_input', 'project_fact': 'project_fact',
                  'scope': 'scope_conflict', 'conflict': 'evidence_conflict'}


def category_for(code):
    normalized = code.strip().upper()
    if normalized in CODE_CATEGORY:
        return CODE_CATEGORY[normalized]
    if re.fullmatch(r'HTTP_(429|5\d\d)', normalized):
        return 'model_issue'
    return 'other'


def classify(result):
    """Count structural failures; keep unknown provider/model codes visible, without guessing."""
    if not result or result['decision'] == 'FC':
        return [], [], [], {}
    claims = {c['id']: c.get('text', '') for c in result.get('claims', [])}
    gap_assessments = [a for a in result.get('assessments', []) if a['verdict'] != 'SUPPORTED']
    codes = {c for c in result.get('reason_codes', []) if c.strip().upper() not in NEUTRAL_CODES}
    codes.update(a['reason_code'] for a in gap_assessments if a.get('reason_code', '').strip().upper() not in NEUTRAL_CODES)
    specific_model_failure = any(category_for(c) in {'model_issue', 'output_issue'} and c.strip().upper() != 'MODEL_UNAVAILABLE' for c in codes)
    grouped = defaultdict(set)
    structured_categories = defaultdict(set)
    for assessment in gap_assessments:
        if assessment.get('gap_type') in GAP_CATEGORIES and assessment.get('reason_code'):
            structured_categories[assessment['reason_code']].add(GAP_CATEGORIES[assessment['gap_type']])
    # CONTRADICTED is the structured evidence that a requirement is not met; its arbitrary
    # explanation code need not be known to this reporting code. SUPPORTED codes are ignored.
    contradicted_codes = set()
    for assessment in gap_assessments:
        if assessment['verdict'] == 'CONTRADICTED':
            grouped['capability_gap']  # This category exists even for legacy reason_code='OK'.
            code = assessment.get('reason_code', '')
            if code.strip().upper() not in NEUTRAL_CODES:
                grouped['capability_gap'].add(code)
                contradicted_codes.add(code)
    for code in codes:
        if code in contradicted_codes:
            continue
        # Nodes wrap all gateway failures with MODEL_UNAVAILABLE. Preserve the code for
        # audit, but let an explicit timeout/configuration/output cause classify this clause.
        if code.strip().upper() == 'MODEL_UNAVAILABLE' and specific_model_failure:
            continue
        if code.strip().upper() == 'UNKNOWN_NOT_DELIVERABLE' and len(codes) > 1:
            continue  # Export consequence, not another failure cause; retain the raw code.
        category = category_for(code)
        targets = structured_categories.get(code) if category == 'other' else None
        for target in targets or {category}:
            grouped[target].add(code)
    if not grouped:
        grouped['unrecorded']
    categories = [{'key': key, 'label': label} for key, label in CATEGORIES.items() if key in grouped]
    gaps = [{'text': claims.get(a['claim_id'], ''), 'verdict': a['verdict'], 'reason_code': a.get('reason_code', ''),
             'explanation': a.get('explanation', '')} for a in gap_assessments]
    return categories, sorted(codes), gaps, dict(grouped)


def summarize_outcomes(items):
    counts = {decision: sum(item['decision'] == decision for item in items) for decision in DECISIONS}
    processed = sum(counts.values())
    non_fc = processed - counts['FC']
    reasons = []
    for key, label in CATEGORIES.items():
        affected = [i for i in items if i['decision'] in NON_FC and key in i['_category_codes']]
        if not affected:
            continue
        codes = Counter(c for i in affected for c in i['_category_codes'][key])
        reasons.append({'key': key, 'label': label, 'count': len(affected), 'rate': len(affected) / non_fc,
            'decisions': {d: sum(i['decision'] == d for i in affected) for d in NON_FC},
            'codes': [{'code': c, 'count': n} for c, n in sorted(codes.items(), key=lambda pair: (-pair[1], pair[0]))]})
    reasons.sort(key=lambda reason: -reason['count'])
    return {'basis': 'machine_initial', 'total': len(items), 'processed': processed, 'pending': len(items) - processed,
        'non_fc': non_fc, 'counts': counts, 'rates': {d: counts[d] / processed if processed else None for d in DECISIONS},
        'reasons': reasons}


class DecisionObservabilityService:
    def __init__(self, repo):
        self.repo = repo

    @contextmanager
    def read_snapshot(self):
        if getattr(self.repo._local, 'db', None) is not None:
            yield
            return
        with self.repo.connection() as db:
            db.execute('BEGIN')
            self.repo._local.db = db
            try:
                yield
            finally:
                self.repo._local.db = None
                db.rollback()

    def load(self, project_id, session_id=None, task_id=None):
        """Return every current SOC binding; a missing result remains pending, never UNKNOWN."""
        self.repo.get('project', project_id)
        if session_id and session_id != '__none__':
            self.repo.get('session', session_id, project_id, include_deleted=True)
        if task_id:
            task = self.repo.get('task', task_id, project_id)
            if session_id and (task.get('session_id') or '__none__') != session_id:
                raise DomainError('NOT_FOUND', '任务不属于所选会话', 404)
        sql = '''SELECT b.*, t.id AS task_id FROM records b
            JOIN records r ON r.id=b.parent_id AND r.kind='run' AND r.deleted=0
                AND r.user_id=b.user_id AND r.project_id=b.project_id
            JOIN records t ON t.id=json_extract(r.data,'$.task_id') AND t.kind='task' AND t.deleted=0
                AND t.user_id=r.user_id AND t.project_id=r.project_id
                AND json_extract(t.data,'$.active_run_id')=r.id
            WHERE b.kind='run_binding' AND b.deleted=0 AND b.user_id=? AND b.project_id=?
                AND json_extract(t.data,'$.mode') IN ('SOC_SINGLE','SOC_BATCH')'''
        args = [self.repo.local_user_id, project_id]
        if session_id:
            sql += " AND COALESCE(json_extract(t.data,'$.session_id'),'__none__')=?"
            args.append(session_id)
        if task_id:
            sql += ' AND t.id=?'
            args.append(task_id)
        with self.repo.connection() as db:
            bindings = db.execute(sql + ' ORDER BY b.created_at,b.id', args).fetchall()
        record_cache, root_cache = {}, {}

        def baseline(ident):
            trail, seen = [], set()
            while ident not in root_cache:
                if ident in seen:
                    raise DomainError('BASELINE_INVALID', '结果版本链异常，无法核对 Agent 原始结论', 409)
                seen.add(ident)
                if ident not in record_cache:
                    record_cache[ident] = self.repo.get('clause_result', ident, project_id)
                record = record_cache[ident]
                trail.append(ident)
                parent = record.get('parent_result_id')
                if not parent:
                    root_cache[ident] = record
                    break
                ident = parent
            root = root_cache[ident]
            for item in trail:
                root_cache[item] = root
            return root

        items = []
        for row in bindings:
            binding = self.repo.decode(row)
            record = baseline(binding.get('initial_result_id') or binding['result_id']) if binding.get('result_id') else None
            result = record['result'] if record else None
            categories, codes, gaps, category_codes = classify(result)
            items.append({'id': binding['id'], 'clause_id': binding['clause_id'], 'task_id': row['task_id'],
                'run_id': binding['parent_id'], 'decision': result['decision'] if result else None,
                'machine_result_id': record['id'] if record else None,
                'raw_text': binding['raw_text'], 'coordinate': binding.get('coordinate'),
                'reason_categories': categories, 'reason_codes': codes, 'gaps': gaps, '_category_codes': category_codes})
        return items

    def clauses(self, project_id, session_id=None, task_id=None, decision=None, reason=None, cursor=None, limit=20):
        if decision is not None and decision not in (*DECISIONS, 'NON_FC'):
            raise DomainError('DECISION_FILTER_INVALID', '请选择有效结论类型', 422)
        if reason is not None and reason not in CATEGORIES:
            raise DomainError('REASON_FILTER_INVALID', '请选择有效原因分类', 422)
        if not 1 <= limit <= 100:
            raise DomainError('PAGE_SIZE_INVALID', '每页条数应为 1 至 100', 422)
        with self.read_snapshot():
            items = [item for item in self.load(project_id, session_id, task_id) if item['decision'] in DECISIONS]
            if decision:
                items = [item for item in items if item['decision'] in (NON_FC if decision == 'NON_FC' else (decision,))]
            if reason:
                items = [item for item in items if reason in item['_category_codes']]
            total = len(items)
            if cursor:
                index = next((i for i, item in enumerate(items) if item['id'] == cursor), None)
                if index is None:
                    raise DomainError('DECISION_CURSOR_INVALID', '统计范围或当前运行已变化，请刷新条目列表', 422)
                items = items[index + 1:]
            return {'items': [{k: v for k, v in item.items() if not k.startswith('_')} for item in items[:limit]],
                'total': total, 'next_cursor': items[limit - 1]['id'] if len(items) > limit else None, 'basis': 'machine_initial'}
