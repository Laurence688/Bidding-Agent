import json
from app.domain.errors import DomainError


def read_events(repo, job_id, after=0):
    repo.get('job', job_id)
    with repo.connection() as db:
        bounds = db.execute('SELECT min(event_id) AS first,max(event_id) AS last FROM job_events WHERE job_id=?', (job_id,)).fetchone()
        if after and bounds['first'] and after < bounds['first'] - 1:
            raise DomainError('EVENT_RESYNC_REQUIRED', '事件窗口已变化，请重新获取任务状态', 409)
        return [{**dict(row), 'data': json.loads(row['data'])} for row in db.execute('SELECT * FROM job_events WHERE job_id=? AND event_id>? ORDER BY event_id LIMIT 200', (job_id, after))]
