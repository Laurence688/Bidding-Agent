"""Durable public activity. Never persist model content in activity records."""
import json
import time
from contextlib import contextmanager
from app.domain.identifiers import utc_now


class ActivityService:
    def __init__(self, repo):self.repo=repo

    @contextmanager
    def step(self, run_id, job_id, node, clause_id=None):
        if not job_id:
            yield None
            return
        job=self.repo.get('job',job_id)
        if job.get('kind') not in {'task','document_task'} or job.get('run_id')!=run_id:
            yield None
            return
        run=self.repo.get('run',run_id)
        with self.repo.transaction():
            step=self.repo.put('activity_step',{'node':node,'clause_id':clause_id,'status':'running','started_at':time.time(),'elapsed_seconds':None},run['project_id'],run_id)
            self.repo.event(job_id,'activity.started',{'step_id':step['id']})
        started=time.monotonic()
        try:
            yield step['id']
        except BaseException:
            self.end(step,job_id,'failed',time.monotonic()-started)
            raise
        else:
            self.end(step,job_id,'completed',time.monotonic()-started)

    def end(self, step, job_id, status, elapsed):
        with self.repo.transaction():
            current=self.repo.get('activity_step',step['id'])
            self.repo.update('activity_step',step['id'],{'status':status if current['status']=='running' else current['status'],'elapsed_seconds':elapsed})
            self.repo.event(job_id,'activity.finished',{'step_id':step['id']})

    def reused(self, step_id):
        if step_id:self.repo.update('activity_step',step_id,{'status':'reused'})

    def usage(self, run_id):
        with self.repo.connection() as db:
            rows=[json.loads(r['data']) for r in db.execute('SELECT data FROM model_attempts WHERE run_id=?',(run_id,))]
        values=[r.get('usage') or {} for r in rows]
        return {key:sum(v[key] for v in values if isinstance(v.get(key),int)) if any(isinstance(v.get(key),int) for v in values) else None for key in ('input_tokens','output_tokens')} | {'partial':any(v.get('input_tokens') is None or v.get('output_tokens') is None for v in values)}

    def finish(self, run_id):
        run=self.repo.get('run',run_id)
        self.repo.update('run',run_id,{'activity_usage':self.usage(run_id),'activity_finished_at':time.time()})

    def snapshot(self, run_id):
        with self.repo.transaction() as db:
            run=self.repo.get('run',run_id)
            steps=self.repo.list('activity_step',run['project_id'],run_id,limit=100000)
            jobs=[j for j in self.repo.list('job',run['project_id'],run_id,limit=100000) if j.get('run_id')==run_id and j.get('kind') in {'task','document_task'}]
            job=jobs[-1] if jobs else None
            cursor=db.execute('SELECT max(event_id) FROM job_events WHERE job_id=?',(job['id'] if job else '',)).fetchone()[0] or 0
            start=run.get('activity_started_at') or min((s['started_at'] for s in steps),default=None)
            end=run.get('activity_finished_at')
            return {'steps':steps,'cursor':cursor,'job':job,'usage':run['activity_usage'] if 'activity_usage' in run else self.usage(run_id), 'elapsed_seconds':max(0,(end or time.time())-start) if start else None}
