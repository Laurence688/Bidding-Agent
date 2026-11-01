from app.telemetry.storage import records
from uuid import uuid5, NAMESPACE_URL
from app.domain.errors import DomainError
from app.domain.identifiers import content_hash
from app.evaluation.datasets import DatasetService, Gold
from app.telemetry.tracing import Recorder, sanitize


class FeedbackService:
    def __init__(self, repo, settings):
        self.repo, self.settings = repo, settings

    def roots(self, run_id=None):
        return [i for i in records(self.repo, 'cloud_outbox') if i['operation'] == 'trace'
                and not i['payload'].get('parent_run_id') and (run_id is None or i.get('run_id') == run_id)]

    def binding(self, trace):
        meta = trace['payload']['extra']['metadata']
        bindings = self.repo.list('run_binding', parent_id=trace.get('run_id'), limit=100000)
        return next((b for b in bindings if b['clause_id'] == meta.get('clause_id')), None)

    def annotate(self, run_id, clause_ids):
        if not self.settings.langsmith.get('enabled'):
            raise DomainError('SYNC_DISABLED', '请先配置并启用 LangSmith 同步', 409)
        queue = self.settings.langsmith.get('annotation_queue_id')
        if not queue:
            raise DomainError('QUEUE_REQUIRED', '请配置云端标注队列 ID', 422)
        roots = self.roots(run_id)
        selected = []
        for clause_id in dict.fromkeys(clause_ids):
            trace = next((t for t in roots if t['payload']['extra']['metadata'].get('clause_id') == clause_id), None)
            binding = self.binding(trace) if trace else None
            if not binding or binding.get('result_id') != trace['payload']['extra']['metadata'].get('result_id'):
                raise DomainError('RESULT_VERSION_CONFLICT', '条目未同步或结果已变化，请检查对应版本', 409)
            selected.append(trace)
        if not selected:
            raise DomainError('SELECTION_REQUIRED', '请选择条目', 422)
        return Recorder(self.repo, self.settings).enqueue(content_hash([queue, *[t['id'] for t in selected]]), 'annotation',
            {'queue_id': queue, 'trace_ids': [t['payload']['id'] for t in selected]}, run_id, [t['id'] for t in selected])

    def pull(self, run_id):
        roots = self.roots(run_id)
        if not roots:
            raise DomainError('TRACE_REQUIRED', '没有可关联的轨迹', 409)
        from app.domain.identifiers import new_id
        return Recorder(self.repo, self.settings).enqueue(new_id(), 'feedback_pull',
            {'trace_ids': [t['payload']['id'] for t in roots]}, run_id, [t['id'] for t in roots])

    def preview(self, feedback):
        roots = {t['payload']['id']: t for t in self.roots()}
        items, unmatched = [], 0
        with self.repo.transaction():
            for value in feedback:
                trace = roots.get(str(value.get('run_id')))
                if not trace or not value.get('id'):
                    unmatched += 1
                    continue
                safe = sanitize({k: value.get(k) for k in ('id', 'run_id', 'key', 'value', 'score', 'comment', 'correction', 'modified_at')},
                    [v for k, v in self.settings.secrets.items() if k.endswith('_API_KEY') and v])
                digest = content_hash(safe)
                ident = str(uuid5(NAMESPACE_URL, 'feedback:' + str(value['id']) + ':' + digest))
                try:
                    item = self.repo.get('cloud_feedback', ident)
                except DomainError as exc:
                    if exc.code != 'NOT_FOUND':
                        raise
                    previous = [i for i in records(self.repo, 'cloud_feedback') if i['feedback_id'] == str(value['id'])]
                    for old in previous:
                        self.repo.update('cloud_feedback', old['id'], {'status': 'conflict'})
                    correction = safe.get('correction') or {}
                    gold = correction.get('gold', correction) if isinstance(correction, dict) else {}
                    if safe.get('key') == 'expected_decision' and safe.get('value') in {'FC', 'PC', 'NC', 'UNKNOWN'}:
                        gold = {**gold, 'decision': safe['value']}
                    binding = self.binding(trace)
                    expected_result = trace['payload']['extra']['metadata'].get('result_id')
                    stale = not binding or not expected_result or binding.get('result_id') != expected_result
                    status = 'stale' if stale else ('conflict' if previous else ('ready' if gold.get('decision') else 'missing_gold'))
                    item = self.repo.put('cloud_feedback', {'feedback_id': str(value['id']), 'feedback': safe, 'trace_id': trace['payload']['id'],
                        'trace_record_id': trace['id'], 'result_id': expected_result, 'binding_id': binding['id'] if binding else None,
                        'run_id': trace.get('run_id'), 'gold': gold, 'status': status}, record_id=ident)
                if item['id'] not in {i['id'] for i in items}:
                    items.append(item)
        return {'items': items, 'unmatched_count': unmatched}

    def resolve(self, ident, expected_version, gold):
        # Explicit local confirmation may resolve feedback conflicts, never stale results.
        item = self.repo.get('cloud_feedback', ident)
        self._check_current(item)
        validated = Gold.model_validate(gold).model_dump(mode='json')
        return self.repo.update('cloud_feedback', ident, {'gold': validated, 'status': 'ready'}, expected_version)

    def _check_current(self, item):
        if not item.get('binding_id') or not item.get('result_id'):
            raise DomainError('RESULT_VERSION_CONFLICT', '无法关联本地结果版本', 409)
        binding = self.repo.get('run_binding', item['binding_id'])
        if binding.get('result_id') != item['result_id']:
            raise DomainError('RESULT_VERSION_CONFLICT', '旧标注不能覆盖新结果', 409)
        return binding

    def publish(self, ids, name):
        with self.repo.transaction():
            ids = sorted(set(ids))
            if not ids:
                raise DomainError('SELECTION_REQUIRED', '请选择已确认标注', 422)
            items = [self.repo.get('cloud_feedback', ident) for ident in ids]
            cases, seen = [], set()
            for item in items:
                binding = self._check_current(item)
                if item['status'] != 'ready':
                    raise DomainError('GOLD_CONFIRMATION_REQUIRED', '缺少标准答案、反馈冲突或版本过期，需先确认', 409)
                if item['trace_id'] in seen:
                    raise DomainError('DUPLICATE_CASE', '同一结果只能选择一份已确认 Gold', 422)
                seen.add(item['trace_id'])
                run = self.repo.get('run', item['run_id'])
                trace = self.repo.get('cloud_outbox', item['trace_record_id'])
                cases.append({'case_id': item['trace_id'], 'product_id': run['product_id'], 'raw_text': binding['raw_text'],
                    'source_context': binding.get('source_context', {}), 'lifecycle_context': trace['payload']['inputs'].get('lifecycle_context', {}),
                    'gold': Gold.model_validate(item['gold']).model_dump(mode='json')})
            key = content_hash([(i['id'], i['gold']) for i in items])
            published = [r for r in self.repo.list('feedback_publication') if r['content_hash'] == key]
            if published:
                return self.repo.get('dataset_version', published[0]['dataset_version_id'])
            service = DatasetService(self.repo)
            dataset = service.create(name)
            version = service.add_cases(dataset['id'], cases)
            self.repo.put('feedback_publication', {'content_hash': key, 'feedback_ids': ids, 'dataset_version_id': version['id']})
            return version
