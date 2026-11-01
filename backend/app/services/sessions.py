from app.domain.errors import DomainError
from app.services.tasks import TaskService


class SessionService:
    def __init__(self, repo, settings):
        self.repo, self.settings = repo, settings

    def update(self,session_id,expected_version,changes):
        changes=dict(changes)
        if not changes or set(changes)-{'name','pinned'}:
            raise DomainError('SESSION_PATCH_INVALID','请选择名称或置顶状态',422)
        if 'name' in changes:
            changes['name']=changes['name'].strip()
            if not changes['name'] or len(changes['name'])>100:
                raise DomainError('SESSION_NAME_INVALID','对话名称须为 1 至 100 字符',422)
        if 'pinned' in changes and type(changes['pinned']) is not bool:
            raise DomainError('SESSION_PATCH_INVALID','置顶状态须为布尔值',422)
        return self.repo.update('session',session_id,changes,expected_version)

    def copy(self,session_id,expected_version,target_project_id,idempotency_key):
        from app.services.session_copies import copy_session
        return copy_session(self.repo,self.settings,session_id,expected_version,target_project_id,idempotency_key)

    def delete(self, session_id, expected_version, cancel_running=False):
        with self.repo.transaction() as db:
            session = self.repo.get_session(session_id)
            if expected_version != session['version']:
                raise DomainError('VERSION_CONFLICT', '会话已变化，请刷新', 409)
            tasks = [t for t in self.repo.list('task', session['project_id']) if t.get('session_id') == session_id]
            task_ids = {t['id'] for t in tasks}
            jobs = [j for j in self.repo.list('job', session['project_id']) if j.get('task_id') in task_ids and j['status'] in {'queued', 'running', 'interrupted', 'waiting_review'}]
            if jobs and not cancel_running:
                raise DomainError('SESSION_HAS_JOBS', '会话有未结束任务，请确认取消后删除', 409)
            for job in jobs:
                TaskService(self.repo, self.settings).cancel(job['id'], job['version'])
            db.execute('UPDATE records SET deleted=1,version=version+1 WHERE id=?', (session_id,))
        return {'deleted': True, 'cancel_requested_jobs': [j['id'] for j in jobs]}
