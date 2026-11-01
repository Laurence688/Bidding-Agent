"""Import offline human feedback without changing results, reviews, or model work."""
from typing import Any

from pydantic import Field

from app.api.contracts import AnnotationBody
from app.domain.errors import DomainError
from app.schemas.core import StrictModel
from app.services.files import FileService
from app.services.run_reports import RunReportService


class SourceIssue(StrictModel):
    cell: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=12000)


class Improvement(StrictModel):
    priority: str = Field(min_length=1, max_length=40)
    title: str = Field(min_length=1, max_length=500)
    action: str = Field(min_length=1, max_length=12000)
    acceptance: str = Field(min_length=1, max_length=6000)
    evidence_status: str = Field(min_length=1, max_length=200)


class FeedbackAnnotation(AnnotationBody):
    clause_id: str
    raw_text: str
    coordinate: dict[str, Any]
    source_cell: str = Field(min_length=1, max_length=200)


class FeedbackImport(StrictModel):
    source_file_id: str
    title: str = Field(min_length=1, max_length=500)
    summary: str = Field(min_length=1, max_length=12000)
    source_issues: list[SourceIssue]
    improvements: list[Improvement]
    import_summary: dict[str, Any]
    annotations: list[FeedbackAnnotation] = Field(max_length=10000)


class ReviewFeedbackService:
    def __init__(self, repo, settings):
        self.repo, self.settings = repo, settings

    def list_for_run(self, run_id):
        run = self.repo.get('run', run_id)
        return [self.with_updates(item) for item in self.repo.list('review_feedback', run['project_id'], run_id, limit=100000)]

    def with_updates(self, feedback):
        updates = [u for u in self.repo.list('review_feedback_update', feedback['project_id'], feedback['id'], limit=100000)
                   if u.get('run_id') == feedback['run_id']]
        return {**feedback, 'implementation_updates': updates} if updates else feedback

    def enrich_messages(self, session, messages):
        """Resolve only feedback belonging to this session, project, task, and run."""
        self.repo.get_session(session['id'], session['project_id'])
        feedbacks = {item['id']: self.with_updates(item) for item in self.repo.list('review_feedback', session['project_id'], limit=100000)
                     if item.get('session_id') == session['id']}
        result = []
        for message in messages:
            if not message.get('feedback_id'):
                result.append(message)
                continue
            feedback = feedbacks.get(message['feedback_id'])
            if feedback and (feedback.get('task_id') != message.get('task_id') or feedback.get('run_id') != message.get('run_id')):
                feedback = None
            result.append({**message, 'review_feedback': feedback})
        return result

    def import_feedback(self, run_id, body):
        mutation = FeedbackImport.model_validate(body)
        run = self.repo.get('run', run_id)
        source = self.repo.get('file', mutation.source_file_id, run['project_id'])
        if source.get('purpose') != 'bidding' or not source['name'].lower().endswith('.xlsx'):
            raise DomainError('FEEDBACK_FILE_TYPE', '人工反馈来源必须是已归档的 Excel 文件', 422)
        FileService(self.repo).resolve(source['id'], run['project_id'])

        def create(_db):
            # Idempotent wraps this entire action in one write transaction. No annotation
            # is committed until every identity, source file, and revision is checked.
            current_run = self.repo.get('run', run_id, run['project_id'])
            task = self.repo.get('task', current_run['task_id'], run['project_id'])
            session_id = current_run.get('session_id')
            if task.get('session_id') != session_id:
                raise DomainError('FEEDBACK_SCOPE_MISMATCH', '任务与运行的会话归属不一致', 409)
            if session_id:
                self.repo.get_session(session_id, run['project_id'])
            FileService(self.repo).resolve(source['id'], run['project_id'])
            bindings = self.repo.list('run_binding', run['project_id'], run_id, limit=100000)
            by_clause = {binding['clause_id']: binding for binding in bindings}
            if len(by_clause) != len(bindings):
                raise DomainError('FEEDBACK_SCOPE_MISMATCH', '此运行的条款标识重复，无法导入', 409)
            latest = {}
            for item in self.repo.list('clause_annotation', run['project_id'], limit=100000):
                if item['run_id'] == run_id:
                    latest[item['parent_id']] = max(latest.get(item['parent_id'], 0), item['revision'])
            clause_ids, cells = set(), set()
            for item in mutation.annotations:
                if item.clause_id in clause_ids or item.source_cell in cells:
                    raise DomainError('FEEDBACK_DUPLICATE', '导入条款或来源批注单元格重复', 422)
                clause_ids.add(item.clause_id)
                cells.add(item.source_cell)
                binding = by_clause.get(item.clause_id)
                if binding is None:
                    raise DomainError('CLAUSE_NOT_FOUND', '条款不属于此运行', 404)
                if not binding.get('result_id') or binding['result_id'] != item.result_id:
                    raise DomainError('VERSION_CONFLICT', '结果已变化，请重新核对人工审核文件', 409)
                record = self.repo.get('clause_result', item.result_id, run['project_id'])
                if record.get('clause_id') != item.clause_id or record.get('run_id') != run_id:
                    raise DomainError('FEEDBACK_SCOPE_MISMATCH', '结果与条款的运行归属不一致', 409)
                if item.raw_text != binding['raw_text'] or item.raw_text != record['result']['raw_text'] or item.coordinate != binding.get('coordinate'):
                    raise DomainError('FEEDBACK_INPUT_MISMATCH', '原文或原始位置与运行记录不一致', 409)
                if item.expected_version != latest.get(binding['id'], 0):
                    raise DomainError('VERSION_CONFLICT', '人工标注已更新，请重新核对后导入', 409)
                if not item.notes.strip():
                    raise DomainError('FEEDBACK_NOTE_REQUIRED', '空白批注不能作为人工标注导入', 422)
            annotated_rows = mutation.import_summary.get('annotated_rows')
            if annotated_rows is not None and annotated_rows != len(mutation.annotations):
                raise DomainError('FEEDBACK_COUNT_MISMATCH', '导入统计与成功关联的批注数量不一致', 422)

            service = RunReportService(self.repo, self.settings)
            links = []
            for item in mutation.annotations:
                annotation_body = {key: value for key, value in item.model_dump().items() if key in AnnotationBody.model_fields}
                annotation = service.annotate(run_id, item.clause_id, annotation_body)
                links.append({**item.model_dump(exclude={'expected_version'}), 'annotation_id': annotation['id']})
            feedback = self.repo.put('review_feedback', {
                'source_file_id': source['id'], 'source_filename': source['name'], 'source_sha256': source['sha256'],
                'run_id': run_id, 'task_id': current_run['task_id'], 'session_id': session_id,
                'title': mutation.title, 'summary': mutation.summary,
                'source_issues': [issue.model_dump() for issue in mutation.source_issues],
                'improvements': [item.model_dump() for item in mutation.improvements],
                'import_summary': mutation.import_summary, 'annotation_links': links,
            }, run['project_id'], run_id)
            if session_id:
                self.repo.put('session_message', {'role': 'user', 'text': mutation.summary,
                    'feedback_id': feedback['id'], 'task_id': current_run['task_id'], 'run_id': run_id}, run['project_id'], session_id)
            self.repo.audit(run['project_id'], 'review_feedback.imported', {
                'run_id': run_id, 'feedback_id': feedback['id'], 'source_file_id': source['id'],
                'source_sha256': source['sha256'], 'annotation_count': len(links)})
            return feedback

        return self.repo.idempotent(run['project_id'], f'review-feedback-import:{run_id}', source['sha256'], mutation.model_dump(), create)
