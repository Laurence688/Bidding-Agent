"""Read-only local observations. Quality uses active results; usage retains every run."""
import json
from collections import Counter, defaultdict
from datetime import datetime, timedelta

from app.domain.errors import DomainError
from app.domain.identifiers import SHANGHAI, session_name, utc_now
from app.model_gateway.usage import summarize_usage
from app.services.run_reports import RunReportService
from app.services.decision_observability import DecisionObservabilityService, summarize_outcomes

STATES = ('queued', 'running', 'waiting_review', 'completed', 'failed', 'cancelled', 'interrupted','partial')
STOPPED = {'waiting_review', 'completed', 'failed', 'cancelled', 'interrupted','partial'}
EXTRA_ACTIONS = {'review.edit', 'review.comment', 'review.revoke'}
EVENT_FIELDS = {'status', 'processed_items', 'total_items', 'current_node', 'clause_id',
                'code', 'cancel_requested', 'elapsed_seconds'}


def rate(numerator, denominator):
    return {'numerator': numerator, 'denominator': denominator,
            'value': numerator / denominator if denominator else None}


def quality(rows):
    completed = [r for r in rows if r.get('decision')]
    annotations = [r['annotation'] for r in completed if r.get('annotation') and not r['annotation_stale']]
    verdicts = Counter(a['verdict'] for a in annotations)
    return {'adoption': rate(sum(r['approved'] for r in completed), len(completed)),
            'accuracy': rate(verdicts['correct'], verdicts['correct'] + verdicts['incorrect']),
            'intervention': rate(sum(r['intervened'] for r in completed), len(completed)),
            'annotated': len(annotations), 'stale_annotations': sum(r['annotation_stale'] for r in completed),
            'excluded_annotations': verdicts['uncertain'] + verdicts['not_requirement']}


def timing(job, events, now):
    starts = [e['created_at'] for e in events if e['type'] == 'job.status' and e['data'].get('status') == 'running']
    stops = [e['created_at'] for e in events if e['type'] == 'job.error' or
             (e['type'] == 'job.status' and e['data'].get('status') in STOPPED)]
    start = starts[0] if starts else None
    end = stops[-1] if stops and job.get('status') in STOPPED else None
    def seconds(a, b):
        return max(0, (datetime.fromisoformat(b) - datetime.fromisoformat(a)).total_seconds()) if a and b else None
    # An interrupted/finished legacy job without events has unknown elapsed time.
    until = end or (now if job.get('status') in {'queued', 'running'} else None)
    return {'started_at': start, 'ended_at': end, 'wall_seconds': seconds(start, until),
            'queue_seconds': seconds(job.get('created_at'), start or (now if job.get('status') == 'queued' else None))}


class ObservabilityService:
    def __init__(self, repo, settings):
        self.repo, self.settings = repo, settings

    def records(self, kind, project_id, include_deleted=False):
        with self.repo.connection() as db:
            rows = db.execute('SELECT * FROM records WHERE kind=? AND project_id=? AND user_id=?' +
                              ('' if include_deleted else ' AND deleted=0') + ' ORDER BY created_at,id',
                              (kind, project_id, self.repo.local_user_id)).fetchall()
        return [{**self.repo.decode(r), 'deleted': bool(r['deleted'])} for r in rows]

    def events(self, project_id):
        with self.repo.connection() as db:
            rows = db.execute('''SELECT e.* FROM job_events e JOIN records j ON j.id=e.job_id
                WHERE j.kind='job' AND j.project_id=? AND j.user_id=? ORDER BY event_id''',
                (project_id, self.repo.local_user_id)).fetchall()
        grouped = defaultdict(list)
        for row in rows:
            data = json.loads(row['data'])
            grouped[row['job_id']].append({**dict(row), 'data': {k: v for k, v in data.items() if k in EVENT_FIELDS}})
        return grouped

    def quality_rows(self, project_id):
        annotations = {}
        for item in self.records('clause_annotation', project_id):
            if item['revision'] > annotations.get(item['parent_id'], {}).get('revision', 0):
                annotations[item['parent_id']] = item
        reviews = {r['id']: r for r in self.records('review', project_id)}
        interventions = {(a['details'].get('run_id'), a['details'].get('clause_id'))
                         for a in self.records('audit', project_id) if a['action'] in EXTRA_ACTIONS}
        with self.repo.connection() as db:
            bindings = db.execute('''SELECT b.*, json_extract(r.data,'$.result.decision') AS decision,
                json_extract(r.data,'$.parent_result_id') AS parent_result_id FROM records b
                LEFT JOIN records r ON r.id=json_extract(b.data,'$.result_id') AND r.user_id=b.user_id
                WHERE b.kind='run_binding' AND b.project_id=? AND b.user_id=? AND b.deleted=0''',
                (project_id, self.repo.local_user_id)).fetchall()
        rows = [{**self.repo.decode(r), 'decision': r['decision'], 'parent_result_id': r['parent_result_id']} for r in bindings]
        by_key = {(r['parent_id'], r['clause_id']): r for r in rows}
        grouped = defaultdict(list)
        for row in rows:
            row['annotation'] = annotations.get(row['id'])
            row['annotation_stale'] = bool(row['annotation'] and row['annotation']['result_id'] != row.get('result_id'))
            approval = reviews.get(row.get('approved_review_id'), {})
            row['approved'] = bool(row.get('review_status') == 'approved' and row.get('result_id') and
                                   approval.get('result_id') == row['result_id'])
            inherited, seen = row, set()
            row['intervened'] = bool(row['parent_result_id'])
            while inherited and inherited['id'] not in seen:
                seen.add(inherited['id'])
                row['intervened'] |= (inherited['parent_id'], inherited['clause_id']) in interventions
                inherited = by_key.get((inherited.get('inherited_from_run_id'), row['clause_id']))
            grouped[row['parent_id']].append(row)
        return grouped

    def overview(self, project_id, session_id=None):
        self.repo.get('project', project_id)
        # One read snapshot avoids a mixed denominator while the worker commits results.
        with self.repo.connection() as db:
            db.execute('BEGIN')
            self.repo._local.db = db
            try:
                return self._overview(project_id, session_id)
            finally:
                self.repo._local.db = None
                db.rollback()

    def _overview(self, project_id, session_id):
        now = utc_now()
        sessions = self.records('session', project_id, include_deleted=True)
        if session_id and session_id != '__none__' and not any(s['id'] == session_id for s in sessions):
            raise DomainError('NOT_FOUND', '会话不存在或不属于当前项目', 404)
        tasks = self.records('task', project_id)
        if session_id:
            tasks = [t for t in tasks if (t.get('session_id') or '__none__') == session_id]
        task_ids = {t['id'] for t in tasks}
        runs = [r for r in self.records('run', project_id) if r['task_id'] in task_ids]
        runs_by_task, jobs_by_run = defaultdict(list), defaultdict(list)
        for run in runs:
            runs_by_task[run['task_id']].append(run)
        for job in self.records('job', project_id):
            if job.get('kind') in {'task','document_task'}:
                jobs_by_run[job.get('run_id')].append(job)
        events = self.events(project_id)
        by_run = self.quality_rows(project_id)
        decision_rows = defaultdict(list)
        for item in DecisionObservabilityService(self.repo).load(project_id, session_id):
            decision_rows[item['task_id']].append(item)
        reports = RunReportService(self.repo, self.settings)
        attempts = {r['id']: reports.attempts(r['id']) for r in runs}
        from app.services.task_labels import task_labels
        labels=task_labels(self.repo,project_id)
        session_names = {s['id']: s['name'] if s.get('name') and s['name'] != '新会话' else session_name(s['created_at']) for s in sessions}
        rows, current_quality = [], []
        for task in tasks:
            versions = runs_by_task[task['id']]
            current = next((r for r in versions if r['id'] == task['active_run_id']), {})
            jobs = jobs_by_run[current.get('id')]
            job = jobs[-1] if jobs else {}
            clauses = by_run[current.get('id')]
            soc_rows = clauses if task['mode'] in {'SOC_SINGLE','SOC_BATCH'} else []
            current_quality.extend(soc_rows)
            calls = [a for r in versions for a in attempts[r['id']]]
            count = len(clauses) if task['mode'] in {'SOC_SINGLE','SOC_BATCH'} else 1 if task['mode']=='PRODUCT_QA' else job.get('total_items',0)
            processed = sum(bool(r['decision']) for r in clauses) if task['mode'] in {'SOC_SINGLE','SOC_BATCH'} else int(bool(current.get('qa_result'))) if task['mode']=='PRODUCT_QA' else job.get('processed_items',0)
            rows.append({**labels(task),'id': task['id'], 'created_at': task['created_at'], 'mode': task['mode'], 'product_id': task.get('product_id'),
                'session_id': task.get('session_id'), 'session_name': session_names.get(task.get('session_id'), '未关联会话'),
                'active_run_id': current.get('id'), 'status': job.get('status', 'unknown'), 'current_node': job.get('current_node'),
                'error_code': (job.get('error') or {}).get('code'), 'total_items': count, 'processed_items': processed,
                **timing(job, events[job.get('id')], now), 'usage': summarize_usage(calls), 'quality': quality(soc_rows),
                'outcomes': summarize_outcomes(decision_rows[task['id']]),
                'runs': [{'id': r['id'], 'created_at': r['created_at'], 'active': r['id'] == current.get('id'),
                          'status': jobs_by_run[r['id']][-1]['status'] if jobs_by_run[r['id']] else 'unknown'} for r in reversed(versions)]})
        all_calls = [a for v in attempts.values() for a in v]
        def summary(selected, selected_calls, selected_quality):
            known_times = [t['wall_seconds'] for t in selected if t['wall_seconds'] is not None]
            return {'task_count': len(selected), 'run_count': sum(len(t['runs']) for t in selected),
                'statuses': dict(Counter(t['status'] for t in selected)), 'total_items': sum(t['total_items'] for t in selected),
                'processed_items': sum(t['processed_items'] for t in selected),
                'known_wall_seconds': sum(known_times) if known_times else None, 'timed_task_count': len(known_times),
                'usage': summarize_usage(selected_calls), 'quality': quality(selected_quality),
                'outcomes': summarize_outcomes([item for task in selected for item in decision_rows[task['id']]])}
        session_rows = []
        groups = list(sessions)
        if any(not t.get('session_id') for t in tasks):
            groups.append({'id': '__none__', 'name': '未关联会话', 'created_at': None, 'deleted': False})
        for session in groups:
            selected = [t for t in rows if (t['session_id'] or '__none__') == session['id']]
            ids = {r['id'] for t in selected for r in t['runs']}
            active = {t['active_run_id'] for t in selected if t['mode'] in {'SOC_SINGLE','SOC_BATCH'}}
            session_rows.append({'id': session['id'], 'name': session_names.get(session['id'], session['name']),
                'created_at': session['created_at'], 'deleted': session['deleted'],
                **summary(selected, [a for rid in ids for a in attempts[rid]], [r for rid in active for r in by_run[rid]])})
        today = datetime.fromisoformat(now).astimezone(SHANGHAI).date()
        daily = defaultdict(list)
        undated = 0
        for call in all_calls:
            if call.get('started_at'):
                date = datetime.fromisoformat(call['started_at']).astimezone(SHANGHAI).date().isoformat()
                daily[date].append(call)
            else:
                undated += 1
        trend = [{'date': (today - timedelta(days=i)).isoformat(),
                  'usage': summarize_usage(daily[(today - timedelta(days=i)).isoformat()])} for i in reversed(range(14))]
        node_calls = defaultdict(list)
        for call in all_calls:
            node_calls[call['logical_node_name']].append(call)
        nodes = [{'node': node, 'failed': sum(a['status'] == 'failed' for a in calls), 'usage': summarize_usage(calls)}
                 for node, calls in node_calls.items()]
        return {'project_id': project_id, 'session_id': session_id, 'generated_at': now,
            'summary': summary(rows, all_calls, current_quality), 'sessions': session_rows,
            'tasks': list(reversed(rows)), 'trend': trend, 'undated_attempt_count': undated,
            'nodes': sorted(nodes, key=lambda n: n['usage']['model_latency_seconds'] or 0, reverse=True)}

    def logs(self, run_id, cursor=None, limit=100):
        run = self.repo.get('run', run_id)
        jobs = {j['id']: j for j in self.records('job', run['project_id']) if j.get('run_id') == run_id}
        entries = []
        for job_id, events in self.events(run['project_id']).items():
            if job_id not in jobs:
                continue
            for event in events:
                entries.append({'id': f'event:{event["event_id"]}', 'created_at': event['created_at'],
                    'kind': 'event', 'type': event['type'], 'phase': jobs[job_id]['kind'], **event['data']})
        for call in RunReportService(self.repo, self.settings).attempts(run_id):
            # Only the established diagnostic allowlist is returned, never the raw model record.
            entries.append({'id': f'attempt:{call["attempt_id"]}', 'created_at': call.get('started_at') or run['created_at'],
                'timestamp_known': bool(call.get('started_at')), 'kind': 'model', **call})
        for audit in self.records('audit', run['project_id']):
            if audit['details'].get('run_id') == run_id and audit['action'] in EXTRA_ACTIONS | {'review.approve', 'annotation.saved'}:
                entries.append({'id': f'audit:{audit["id"]}', 'created_at': audit['created_at'], 'kind': 'human',
                    'type': audit['action'], 'clause_id': audit['details'].get('clause_id')})
        entries.sort(key=lambda e: (e['created_at'], e['id']), reverse=True)
        total = len(entries)
        if cursor:
            index = next((i for i, row in enumerate(entries) if row['id'] == cursor), None)
            if index is None:
                raise DomainError('LOG_CURSOR_INVALID', '日志位置已失效，请刷新日志', 422)
            entries = entries[index + 1:]
        return {'items': entries[:limit], 'next_cursor': entries[limit - 1]['id'] if len(entries) > limit else None, 'total': total}
