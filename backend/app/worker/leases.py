from app.evaluation.state import set_terminal
import os
import time
import threading
import psutil
from app.domain.errors import DomainError
from app.domain.identifiers import new_id


class WorkerLease:
    def __init__(self, repo):
        self.repo, self.owner = repo, new_id()
        self.stop = threading.Event()
        self.handle = None

    def __enter__(self):
        self.handle = open(self.repo.data_dir / 'worker.lock', 'a+b')
        if self.handle.tell() == 0:
            self.handle.write(b'0')
            self.handle.flush()
        self.handle.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.handle.close()
            raise DomainError('WORKER_ALREADY_RUNNING', '已有 worker 正在运行', 409) from exc
        with self.repo.transaction() as db:
            db.execute('INSERT OR REPLACE INTO worker_leases VALUES(?,?,?,?,?)', ('main', self.owner, os.getpid(), psutil.Process().create_time(), time.time()))
        self.thread = threading.Thread(target=self._heartbeat, daemon=True)
        self.thread.start()
        return self

    def _heartbeat(self):
        while not self.stop.wait(2):
            with self.repo.transaction() as db:
                db.execute('UPDATE worker_leases SET heartbeat=? WHERE name=? AND owner=?', (time.time(), 'main', self.owner))

    def __exit__(self, *args):
        self.stop.set()
        self.thread.join(timeout=3)
        with self.repo.transaction() as db:
            db.execute('DELETE FROM worker_leases WHERE owner=?', (self.owner,))
        if self.handle:
            self.handle.close()


def owner_alive(pid, started):
    try:
        return bool(pid) and abs(psutil.Process(pid).create_time() - float(started)) < 0.1
    except (psutil.Error, ValueError, TypeError):
        return False


def reconcile_interrupted(repo):
    with repo.transaction():
        for job in repo.list('job', limit=100000):
            if job.get('status') == 'running' and not owner_alive(job.get('owner_pid'), job.get('owner_started')):
                repo.update('job', job['id'], {'status': 'interrupted', 'error': {'code': 'WORKER_INTERRUPTED', 'message': '执行进程已停止，请显式恢复'}})
                repo.event(job['id'], 'job.status', {'status': 'interrupted'})
                if job.get('evaluation_run_id'):
                    set_terminal(repo, job['evaluation_run_id'], 'interrupted')
