"""Run-scoped, read-only diagnostics; human annotations never alter business results."""
import json
from collections import Counter, defaultdict
from datetime import datetime
from app.domain.errors import DomainError
from app.domain.identifiers import canonical, content_hash
from app.model_gateway.usage import summarize_usage
from app.api.contracts import AnnotationBody
from app.services.result_sources import result_sources
from app.model_gateway.diagnostics import failure_category
from app.domain.source_context import model_source_context

DECISIONS = ('FC', 'PC', 'NC', 'UNKNOWN')


class RunReportService:
    def __init__(self, repo, settings):
        self.repo, self.settings = repo, settings

    def clauses(self, run_id):
        run = self.repo.get('run', run_id)
        histories = defaultdict(list)
        for item in self.repo.list('clause_annotation', run['project_id'], limit=100000):
            if item['run_id'] == run_id:
                histories[item['parent_id']].append(item)
        rows = []
        with self.repo.connection() as db:
            bindings = db.execute('''SELECT b.*, r.data AS result_data FROM records b
                LEFT JOIN records r ON r.id=json_extract(b.data,'$.result_id') AND r.user_id=b.user_id
                WHERE b.kind='run_binding' AND b.parent_id=? AND b.user_id=? AND b.deleted=0
                ORDER BY b.created_at,b.id''', (run_id, self.repo.local_user_id)).fetchall()
            for row in bindings:
                binding = self.repo.decode(row)
                record = json.loads(row['result_data']) if row['result_data'] else None
                initial_id = binding.get('initial_result_id') or binding.get('result_id')
                initial = record
                if initial_id and initial_id != binding.get('result_id'):
                    initial = self.repo.get('clause_result', initial_id, run['project_id'])
                if initial:
                    # Pre-observability runs keep the original result in their immutable parent chain.
                    while initial.get('parent_result_id'):
                        initial_id = initial['parent_result_id']
                        initial = self.repo.get('clause_result', initial_id, run['project_id'])
                history = sorted(histories[binding['id']], key=lambda a: a['revision'])
                annotation = history[-1] if history else None
                rows.append({**binding, 'result': record['result'] if record else None,
                             'sources': result_sources(record['result'] if record else None),
                             'machine_result': initial['result'] if initial else None, 'machine_result_id': initial_id,
                             'annotation': annotation, 'annotation_history': history,
                             'annotation_stale': bool(annotation and annotation['result_id'] != binding.get('result_id'))})
        return rows

    def annotate(self, run_id, clause_id, body):
        mutation = AnnotationBody.model_validate(body)
        with self.repo.transaction():
            run = self.repo.get('run', run_id)
            binding = next((b for b in self.repo.list('run_binding', run['project_id'], run_id, limit=100000) if b['clause_id'] == clause_id), None)
            if binding is None:
                raise DomainError('CLAUSE_NOT_FOUND', '条款不属于此运行', 404)
            if not binding.get('result_id') or binding['result_id'] != mutation.result_id:
                raise DomainError('VERSION_CONFLICT', '结果已变化，请载入最新结果再标注', 409)
            history = self.repo.list('clause_annotation', run['project_id'], binding['id'], limit=100000)
            revision = max((a['revision'] for a in history), default=0)
            if mutation.expected_version != revision:
                raise DomainError('VERSION_CONFLICT', '标注已被更新，请刷新后保存', 409)
            item = self.repo.put('clause_annotation', {**mutation.model_dump(exclude={'expected_version'}),
                'run_id': run_id, 'clause_id': clause_id, 'revision': revision + 1}, run['project_id'], binding['id'])
            self.repo.audit(run['project_id'], 'annotation.saved', {'run_id': run_id, 'clause_id': clause_id,
                'result_id': mutation.result_id, 'annotation_id': item['id']})
            return item

    def attempts(self, run_id):
        run = self.repo.get('run', run_id)
        ids = [run_id] + [j['id'] for j in self.repo.list('job', run['project_id'], run_id, limit=100000) if j.get('kind') == 'review_validation']
        # Select only diagnostic fields: prompts, model reasoning and raw responses never leave this service.
        with self.repo.connection() as db:
            rows = db.execute(f'''SELECT attempt_id,run_id,clause_id,claim_id,logical_node_name,attempt_index,provider,model,status,request_hash,
                json_extract(data,'$.call_kind') AS call_kind,
                json_extract(data,'$.invocation_id') AS invocation_id,
                json_extract(data,'$.invocation_attempt') AS invocation_attempt,
                json_extract(data,'$.usage') AS usage,json_extract(data,'$.latency') AS latency,
                json_extract(data,'$.error_code') AS error_code,json_extract(data,'$.started_at') AS started_at,
                json_extract(data,'$.ended_at') AS ended_at,json_extract(data,'$.context_estimate') AS context_estimate,
                json_extract(data,'$.provider_settings') AS provider_settings,
                json_extract(data,'$.diagnostics') AS diagnostics,
                json_extract(data,'$.response.usage.completion_tokens_details.reasoning_tokens') AS reasoning_tokens
                FROM model_attempts WHERE run_id IN ({','.join('?' for _ in ids)}) ORDER BY rowid''', ids).fetchall()
        result = []
        previous = {}
        for row in rows:
            item = dict(row)
            key = (item['run_id'], item['clause_id'], item['claim_id'], item['logical_node_name'])
            prior = previous.get(key)
            item['classification_source'] = 'recorded' if item['call_kind'] else 'historical_request_hash'
            if item['call_kind']:
                item['is_retry'] = (item['invocation_attempt'] or 1) > 1
            else:
                changed = prior and prior['request_hash'] != item['request_hash']
                item['is_retry'] = bool(prior and not changed and prior['status'] == 'failed' and
                    (prior.get('error_code') in {'TIMEOUT', 'MODEL_TIMEOUT', 'MODEL_CONNECTION', 'HTTP_429'} or str(prior.get('error_code', '')).startswith('HTTP_5')))
                item['call_kind'] = ('supplement' if changed and item['logical_node_name'] in {'judge_claim', 'verify_semantic_support'} else
                                     prior['call_kind'] if item['is_retry'] else 'initial' if not prior else 'other_followup')
            previous[key] = dict(item)
            item['usage'] = json.loads(item['usage']) if item['usage'] else {}
            if item['usage'].get('reasoning_tokens') is None:
                item['usage']['reasoning_tokens'] = item.pop('reasoning_tokens')
            else:
                item.pop('reasoning_tokens')
            item['provider_settings'] = json.loads(item['provider_settings']) if item['provider_settings'] else None
            item['diagnostics'] = json.loads(item['diagnostics']) if item['diagnostics'] else None
            item['failure_category'] = failure_category(item.get('error_code'))
            item['phase'] = 'processing' if item['run_id'] == run_id else 'review_validation'
            result.append(item)
        return result

    @staticmethod
    def distribution(rows, field):
        counts = {d: sum(bool(r[field]) and r[field]['decision'] == d for r in rows) for d in DECISIONS}
        processed = sum(counts.values())
        return {'counts': counts, 'rates': {d: counts[d] / processed if processed else None for d in DECISIONS},
                'processed': processed, 'pending': len(rows) - processed, 'denominator': 'processed'}

    def report(self, run_id):
        run = self.repo.get('run', run_id)
        if run['mode'] not in {'SOC_SINGLE','SOC_BATCH'}:raise DomainError('MODE_INVALID','此报告仅适用于 SOC 运行；文档任务请查看独立结果',422)
        rows, attempts = self.clauses(run_id), self.attempts(run_id)
        config = self.repo.get('config_snapshot', run['config_snapshot_id'])
        snapshot = self.repo.get('input_snapshot', run['input_snapshot_id'], run['project_id'])
        jobs = [j for j in self.repo.list('job', run['project_id'], run_id) if j.get('kind') == 'task']
        job = jobs[-1] if jobs else None
        start, end = None, None
        if job:
            with self.repo.connection() as db:
                events = db.execute('SELECT type,data,created_at FROM job_events WHERE job_id=? ORDER BY event_id', (job['id'],)).fetchall()
            starts = [e['created_at'] for e in events if e['type'] == 'job.status' and json.loads(e['data']).get('status') == 'running']
            ends = [e['created_at'] for e in events if e['type'] in {'job.status', 'job.error'} and (e['type'] == 'job.error' or json.loads(e['data']).get('status') in {'completed', 'waiting_review', 'failed', 'cancelled'})]
            start = starts[0] if starts else None
            end = ends[-1] if ends and job['status'] in {'completed', 'waiting_review', 'failed', 'cancelled'} else None
        nodes = []
        with self.repo.connection() as db:
            timings = {r['node_name']: dict(r) for r in db.execute('SELECT node_name,COUNT(*) AS executions,SUM(elapsed_seconds) AS wall_seconds FROM node_timings WHERE run_id=? GROUP BY node_name', (run_id,))}
        for name in sorted({a['logical_node_name'] for a in attempts} | set(timings)):
            calls = [a for a in attempts if a['logical_node_name'] == name]
            nodes.append({'node': name, 'calls': len(calls), 'failed': sum(a['status'] == 'failed' for a in calls),
                'retries': sum(a['is_retry'] for a in calls),
                'supplement_calls': sum(a['call_kind'] == 'supplement' for a in calls),
                'supplement_judgments': sum(a['call_kind'] == 'supplement' and not a['is_retry'] for a in calls),
                'other_followups': sum(a['call_kind'] == 'other_followup' for a in calls),
                'supplement_usage': summarize_usage([a for a in calls if a['call_kind'] == 'supplement']),
                'classification_inferred': any(a['classification_source'] != 'recorded' for a in calls),
                'errors': dict(Counter(a['error_code'] for a in calls if a.get('error_code'))),
                'usage': summarize_usage(calls), 'timing': timings.get(name)})
        annotations = [r['annotation'] for r in rows if r['annotation'] and not r['annotation_stale']]
        return {'run_id': run_id, 'task_id': run['task_id'], 'project_id': run['project_id'], 'created_at': run['created_at'],
            'product_id': run['product_id'], 'mode': run['mode'], 'status': job['status'] if job else None,
            'total': len(rows), 'machine': self.distribution(rows, 'machine_result'), 'current': self.distribution(rows, 'result'),
            'annotations': {'count': len(annotations), 'stale_count': sum(r['annotation_stale'] for r in rows),
                            'verdicts': dict(Counter(a['verdict'] for a in annotations)), 'stages': dict(Counter(a['stage'] for a in annotations if a.get('stage')))},
            'reason_counts': dict(Counter(reason for r in rows if r['machine_result'] for reason in set(r['machine_result']['reason_codes']))),
            'usage': summarize_usage(attempts), 'processing_usage': summarize_usage([a for a in attempts if a['phase'] == 'processing']),
            'started_at': start, 'ended_at': end,
            'wall_seconds': (datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds() if start and end else None,
            'nodes': nodes, 'identity': {'file_sha256': snapshot.get('file_sha256'), 'snapshot_hash': snapshot['content_hash'],
                'input_hash': content_hash(sorted([{'raw_text': r['raw_text'], 'sheet': (r.get('coordinate') or {}).get('sheet'),
                    'requirement_context': model_source_context(r.get('source_context')),
                    'input_cell': (r.get('coordinate') or {}).get('input_cell')} for r in rows], key=canonical)),
                'config_hash': config['content_hash'], 'prompt_hash': content_hash(config['prompts']),
                'knowledge_snapshot_id': run.get('knowledge_snapshot_id'), 'workflow_version': config.get('workflow_version', 'legacy'),
                'processing': config.get('processing'),
                'models': {k: {n: v.get(n) for n in ['provider', 'model_id', 'max_output_tokens', 'structured_thinking']} for k, v in config['models']['providers'].items()}}}

    def versions(self, project_id):
        self.repo.get('project', project_id)
        items = []
        for run in reversed(self.repo.list('run', project_id, limit=100000)):
            if run['mode'] not in {'SOC_SINGLE','SOC_BATCH'}:
                continue
            rows = self.clauses(run['id'])
            snapshot = self.repo.get('input_snapshot', run['input_snapshot_id'], project_id)
            file = self.repo.get('file', snapshot['file_id'], project_id) if snapshot.get('file_id') else None
            items.append({k: run[k] for k in ['id', 'task_id', 'created_at', 'product_id', 'mode']}
                         | {'total': len(rows), 'machine': self.distribution(rows, 'machine_result'), 'file_name': file.get('name') if file else None})
        return {'items': items}

    def compare(self, run_id, baseline_id):
        current = self.repo.get('run', run_id)
        self.repo.get('run', baseline_id, current['project_id'])
        def group(rows):
            grouped = defaultdict(list)
            for row in rows:
                coordinate = row.get('coordinate') or {}
                key = content_hash([coordinate.get('sheet'), coordinate.get('input_cell'), row['raw_text']])
                grouped[key].append(row)
            return grouped
        left, right = group(self.clauses(run_id)), group(self.clauses(baseline_id))
        pairs = [(left[k][0], right[k][0]) for k in left.keys() & right.keys() if len(left[k]) == len(right[k]) == 1]
        changes = []
        transitions = Counter()
        for new, old in pairs:
            after = new['machine_result']['decision'] if new['machine_result'] else 'PENDING'
            before = old['machine_result']['decision'] if old['machine_result'] else 'PENDING'
            transitions[f'{before} → {after}'] += 1
            if before != after:
                changes.append({'clause_id': new['clause_id'], 'coordinate': new.get('coordinate'), 'raw_text': new['raw_text'], 'before': before, 'after': after})
        return {'baseline': self.report(baseline_id), 'matched_count': len(pairs),
                'context_changed_count': sum(model_source_context(new.get('source_context')) != model_source_context(old.get('source_context')) for new, old in pairs),
                'unmatched_current': sum(map(len, left.values())) - len(pairs), 'unmatched_baseline': sum(map(len, right.values())) - len(pairs),
                'transitions': dict(transitions), 'changed_decisions': sorted(changes, key=lambda v: ((v.get('coordinate') or {}).get('row', 0), v['clause_id']))}

    def trace(self, run_id, clause_id):
        run = self.repo.get('run', run_id)
        if not any(b['clause_id'] == clause_id for b in self.repo.list('run_binding', run['project_id'], run_id, limit=100000)):
            raise DomainError('CLAUSE_NOT_FOUND', '条款不属于此运行', 404)
        with self.repo.connection() as db:
            nodes = db.execute('''SELECT c.node_name,c.created_at,c.output,t.elapsed_seconds FROM node_commits c
                LEFT JOIN node_timings t USING(run_id,clause_id,node_name,input_hash)
                WHERE c.run_id=? AND c.clause_id=? ORDER BY c.created_at''', (run_id, clause_id)).fetchall()
        steps = []
        for node in nodes:
            output = json.loads(node['output'])
            details = {k: output[k] for k in ['fatal', 'reason_codes', 'coverage_complete', 'decision', 'output_valid', 'excluded_reasons', 'diagnostics', 'retrieval_diagnostics'] if k in output}
            for field in ['candidates_by_claim', 'admitted_by_claim']:
                if field in output:
                    details[field] = {k: len(v) for k, v in output[field].items()}
            steps.append({'node': node['node_name'], 'created_at': node['created_at'], 'elapsed_seconds': node['elapsed_seconds'], 'details': details})
        return {'steps': steps, 'attempts': [a for a in self.attempts(run_id) if a['clause_id'] == clause_id]}

    def export(self, run_id):
        from app.services.review_feedback import ReviewFeedbackService
        report = self.report(run_id)
        rows = self.clauses(run_id)
        comments = self.repo.list('review_comment', report['project_id'], limit=100000)
        for row in rows:
            row['comments'] = [c for c in comments if c['parent_id'] == row['id']]
        with self.repo.connection() as db:
            timings = [dict(r) for r in db.execute('SELECT * FROM node_timings WHERE run_id=?', (run_id,))]
        return {'format_version': 1, 'report': report, 'clauses': rows, 'attempts': self.attempts(run_id), 'node_timings': timings,
                'review_feedbacks': ReviewFeedbackService(self.repo, self.settings).list_for_run(run_id)}
