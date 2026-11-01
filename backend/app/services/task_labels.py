"""Read current project/session labels in one user-scoped query; never mutate snapshots."""
import json
from app.domain.identifiers import session_name


def task_labels(repo, project_id):
    project = repo.get('project', project_id)
    with repo.connection() as db:
        records = db.execute("SELECT id,data,deleted,created_at FROM records WHERE kind='session' AND user_id=? AND project_id=?", (repo.local_user_id, project_id)).fetchall()
    sessions = {r['id']: {**json.loads(r['data']), 'deleted':bool(r['deleted']), 'created_at':r['created_at']} for r in records}
    def label(task):
        session = sessions.get(task.get('session_id'), {})
        name = session.get('name') or '未关联会话'
        if name == '新会话': name = session_name(session['created_at'])
        deleted = bool(session.get('deleted'))
        return {'project_name':project['name'], 'session_name':name, 'session_deleted':deleted,
                'display_name':f"{project['name']} - {name}" + ('（已删除）' if deleted else '')}
    return label
