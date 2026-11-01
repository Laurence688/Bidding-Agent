import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from app.domain.identifiers import new_id, utc_now, canonical, content_hash, session_name
from app.domain.errors import DomainError


class Repository:
    """用短事务持久化逻辑实体；所有实体访问统一检查身份和项目范围。"""
    def __init__(self, data_dir):
        self.data_dir = Path(data_dir).resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.data_dir / 'app.db'
        self._local = threading.local()
        with self.connection() as db:
            for path in sorted((Path(__file__).parent / 'migrations').glob('*.sql')):
                db.executescript(path.read_text('utf-8'))
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT id FROM local_users LIMIT 1').fetchone()
            self.local_user_id = row['id'] if row else new_id()
            if row is None:
                db.execute('INSERT INTO local_users VALUES(?,?)', (self.local_user_id, utc_now()))
            db.commit()

    @contextmanager
    def connection(self):
        existing = getattr(self._local, 'db', None)
        if existing is not None:
            yield existing
            return
        db = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('PRAGMA busy_timeout=15000')
        try:
            yield db
        finally:
            db.close()

    @contextmanager
    def transaction(self):
        if getattr(self._local, 'db', None) is not None:
            yield self._local.db
            return
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            self._local.db = db
            try:
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise
            finally:
                self._local.db = None

    @staticmethod
    def decode(row):
        return {**json.loads(row['data']), 'id': row['id'], 'version': row['version'],
                'project_id': row['project_id'], 'parent_id': row['parent_id'],
                'created_at': row['created_at'], 'updated_at': row['updated_at']}

    def put(self, kind, data, project_id=None, parent_id=None, record_id=None, created_at=None):
        ident, now = record_id or new_id(), created_at or utc_now()
        with self.transaction() as db:
            if project_id:
                self.get('project', project_id)
                if kind in {'session', 'task', 'run', 'job', 'file', 'input_snapshot'}:
                    self.assert_project_active(project_id)
            db.execute('INSERT INTO records(id,kind,user_id,project_id,parent_id,data,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)',
                       (ident, kind, self.local_user_id, project_id, parent_id, canonical(data), now, now))
        return self.get(kind, ident, project_id=project_id)

    def get(self, kind, ident, project_id=None, include_deleted=False):
        with self.connection() as db:
            row = db.execute('SELECT * FROM records WHERE id=? AND kind=? AND user_id=?', (ident, kind, self.local_user_id)).fetchone()
        if row is None or (row['deleted'] and not include_deleted) or (project_id is not None and row['project_id'] != project_id):
            raise DomainError('NOT_FOUND', '资源不存在或不属于当前项目', 404)
        return self.decode(row)

    def list(self, kind, project_id=None, parent_id=None, after=None, limit=1000, trashed=False):
        sql, args = 'SELECT * FROM records WHERE kind=? AND user_id=? AND deleted=0', [kind, self.local_user_id]
        if kind == 'project' and trashed is not None:
            sql += " AND COALESCE(json_extract(data,'$.trashed'),0)=?"
            args.append(int(trashed))
        for name, val in [('project_id', project_id), ('parent_id', parent_id)]:
            if val is not None:
                sql += f' AND {name}=?'
                args.append(val)
        if after:
            cursor = self.get(kind, after, project_id)
            sql += ' AND (created_at,id)>(?,?)'
            args.extend([cursor['created_at'], cursor['id']])
        sql += ' ORDER BY created_at,id LIMIT ?'
        args.append(limit)
        with self.connection() as db:
            return [self.decode(row) for row in db.execute(sql, args)]

    def update(self, kind, ident, changes, expected_version=None, project_id=None):
        with self.transaction() as db:
            obj = self.get(kind, ident, project_id)
            if expected_version is not None and obj['version'] != expected_version:
                raise DomainError('VERSION_CONFLICT', '内容已更新，请刷新后重新操作', 409)
            data = {k: v for k, v in obj.items() if k not in {'id', 'version', 'project_id', 'parent_id', 'created_at', 'updated_at'}}
            data.update(changes)
            db.execute('UPDATE records SET data=?,version=version+1,updated_at=? WHERE id=?', (canonical(data), utc_now(), ident))
        return self.get(kind, ident, project_id)

    def create_project(self, name, product_id):
        return self.put('project', {'name': name, 'default_product_id': product_id})

    def update_project(self, ident, expected_version, changes):
        with self.transaction():
            self.assert_project_active(ident)
            return self.update('project', ident, changes, expected_version)

    def assert_project_active(self, ident):
        project = self.get('project', ident)
        if project.get('trashed'):
            raise DomainError('PROJECT_TRASHED', '项目已在回收站，请先恢复项目后再操作', 409)
        return project

    def set_project_trashed(self, ident, expected_version, trashed):
        with self.transaction():
            project = self.get('project', ident)
            if project['version'] != expected_version:
                raise DomainError('VERSION_CONFLICT', '项目已更新，请刷新后重新操作', 409)
            if trashed:
                with self.connection() as db:
                    busy = db.execute("SELECT 1 FROM records WHERE kind='job' AND project_id=? AND user_id=? AND deleted=0 AND json_extract(data,'$.status') IN ('queued','running') LIMIT 1", (ident, self.local_user_id)).fetchone()
                if busy:
                    raise DomainError('PROJECT_BUSY', '项目仍有排队或运行任务，请先在任务记录中处理后再移入回收站', 409)
            return self.update('project', ident, {'trashed': trashed, 'trashed_at': utc_now() if trashed else None}, expected_version)

    def create_session(self, project_id, name=None):
        now = utc_now()
        return self.put('session', {'name': name if name is not None else session_name(now)}, project_id=project_id, created_at=now)

    def get_session(self, ident, project_id=None):
        return self.get('session', ident, project_id)

    def idempotent(self, project_id, operation, key, body, action):
        if not key or len(key) > 200:
            raise DomainError('IDEMPOTENCY_REQUIRED', '需要有效的 Idempotency-Key', 422)
        digest = content_hash(body)
        scope = (self.local_user_id, project_id or '', operation, key)
        with self.transaction() as db:
            row = db.execute('SELECT * FROM idempotency_records WHERE user_id=? AND project_id=? AND operation=? AND key=?', scope).fetchone()
            if row:
                if row['body_hash'] != digest:
                    raise DomainError('IDEMPOTENCY_CONFLICT', '相同操作标识对应的内容不同', 409)
                return json.loads(row['response'])
            response = action(db)
            db.execute('INSERT INTO idempotency_records VALUES(?,?,?,?,?,?)', (*scope, digest, canonical(response)))
            return response

    def event(self, job_id, event_type, data):
        with self.transaction() as db:
            return db.execute('INSERT INTO job_events(job_id,type,data,created_at) VALUES(?,?,?,?)', (job_id, event_type, canonical(data), utc_now())).lastrowid

    def audit(self, project_id, action, data):
        return self.put('audit', {'action': action, 'details': data}, project_id)

