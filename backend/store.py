"""SQLite persistence. Every mutation is transactional; raw inputs stay immutable."""
import hashlib
import json
import secrets
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from domain import Problem, stable_json


class Store:
    def __init__(self, path):
        self.path=path
        path.parent.mkdir(parents=True,exist_ok=True)
        self.lock=threading.RLock()
        with self.transaction() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS sessions(owner TEXT PRIMARY KEY,csrf TEXT NOT NULL,created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS imports(id TEXT PRIMARY KEY,owner TEXT NOT NULL,payload TEXT NOT NULL,created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY,owner TEXT NOT NULL,request_id TEXT NOT NULL,
                fingerprint TEXT NOT NULL,payload TEXT NOT NULL,created REAL NOT NULL,UNIQUE(owner,request_id));
            CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT,task_id TEXT NOT NULL,
                kind TEXT NOT NULL,payload TEXT NOT NULL,created REAL NOT NULL);
            ''')

    @contextmanager
    def transaction(self):
        with self.lock:
            db=sqlite3.connect(self.path,timeout=10)
            db.row_factory=sqlite3.Row
            try:
                db.execute('PRAGMA foreign_keys=ON')
                db.execute('BEGIN IMMEDIATE')
                yield db
                db.commit()
            except Exception:
                db.rollback()
                raise
            finally:
                db.close()

    @staticmethod
    def owner(token):
        return hashlib.sha256(token.encode()).hexdigest()

    def session(self, token=None):
        with self.transaction() as db:
            if token and len(token)<=150:
                owner=self.owner(token)
                row=db.execute('SELECT * FROM sessions WHERE owner=?',(owner,)).fetchone()
                if row and time.time()-row['created']<30*86400:
                    return token,owner,row['csrf']
            token=secrets.token_urlsafe(32)
            owner=self.owner(token);csrf=secrets.token_urlsafe(32)
            db.execute('INSERT INTO sessions VALUES(?,?,?)',(owner,csrf,time.time()))
            return token,owner,csrf

    def authorize(self, token, csrf=None):
        if not token or len(token)>150:
            raise Problem(401,'session_required','请先建立当前浏览器会话。')
        owner=self.owner(token)
        with self.transaction() as db:
            row=db.execute('SELECT * FROM sessions WHERE owner=?',(owner,)).fetchone()
            if not row or time.time()-row['created']>=30*86400:
                raise Problem(401,'session_expired','会话已失效，请重新建立会话。')
            if csrf is not None and not secrets.compare_digest(csrf,row['csrf']):
                raise Problem(403,'csrf_failed','请求校验失败，请刷新当前会话。')
        return owner

    def save_import(self, owner, payload):
        identifier=uuid.uuid4().hex
        with self.transaction() as db:
            db.execute('INSERT INTO imports VALUES(?,?,?,?)',(identifier,owner,stable_json(payload),time.time()))
        return identifier

    @staticmethod
    def task_row(db, identifier, owner=None):
        if owner is None:
            row=db.execute('SELECT * FROM tasks WHERE id=?',(identifier,)).fetchone()
        else:
            row=db.execute('SELECT * FROM tasks WHERE id=? AND owner=?',(identifier,owner)).fetchone()
        if not row:
            raise Problem(404,'task_not_found','任务不存在或不属于当前会话。')
        return row,json.loads(row['payload'])

    @staticmethod
    def event(db, identifier, kind, payload):
        db.execute('INSERT INTO events(task_id,kind,payload,created) VALUES(?,?,?,?)',
                   (identifier,kind,stable_json(payload),time.time()))

    @staticmethod
    def put_task(db, task):
        db.execute('UPDATE tasks SET payload=? WHERE id=?',(stable_json(task),task['task_id']))

    def create_task(self, owner, import_id, request_id, name, product_scope, dataset_kind, settings, platform='', product_name=''):
        from batches import business_name
        identity=[import_id,name,product_scope,dataset_kind]
        if platform or product_name:
            identity += [platform,product_name]
        fingerprint=hashlib.sha256(stable_json(identity).encode()).hexdigest()
        with self.transaction() as db:
            old=db.execute('SELECT * FROM tasks WHERE owner=? AND request_id=?',(owner,request_id)).fetchone()
            if old:
                if json.loads(old['payload']).get('deleted_at'):
                    raise Problem(409,'task_deleted','该任务在回收站，请先恢复或重新导入。')
                if old['fingerprint']!=fingerprint:
                    raise Problem(409,'request_id_conflict','同一请求编号不能用于不同输入。')
                return json.loads(old['payload']),False
            active=db.execute('SELECT payload FROM tasks WHERE owner=?',(owner,)).fetchall()
            if any(json.loads(r['payload'])['status'] in ['queued','running'] for r in active):
                raise Problem(409,'task_active','本会话已有分析任务，请等待完成。')
            row=db.execute('SELECT * FROM imports WHERE id=? AND owner=?',(import_id,owner)).fetchone()
            if not row or time.time()-row['created']>86400:
                raise Problem(404,'import_not_found','预览不存在、已过期或不属于当前会话。')
            data=json.loads(row['payload'])
            if platform or product_name:
                name=business_name(platform, product_name, len(data['records']))
            identifier=uuid.uuid4().hex
            task={'task_id':identifier,'name':name,'product_scope':product_scope,'dataset_kind':dataset_kind,
                  'platform':platform,'product_name':product_name,
                  'status':'queued','version':1,'records':data['records'],'settings':settings,
                  'created_at':time.time(),'billing_uncertain':False}
            db.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?)',(identifier,owner,request_id,fingerprint,stable_json(task),time.time()))
            self.event(db,identifier,'task_created',{'count':len(task['records']),'settings':settings})
            return task,True

    def get_task(self, owner, identifier):
        with self.transaction() as db:
            task=self.task_row(db,identifier,owner)[1]
            if task.get('deleted_at'):
                raise Problem(410,'task_deleted','批次已移入回收站，可在管理批次中恢复。')
            return task

    def current_task(self, owner):
        with self.transaction() as db:
            for row in db.execute('SELECT payload FROM tasks WHERE owner=? ORDER BY created DESC',(owner,)):
                task=json.loads(row['payload'])
                if not task.get('deleted_at'):return task
            return None

    def list_tasks(self, owner, deleted=False):
        with self.transaction() as db:
            return [json.loads(row['payload']) for row in db.execute(
                'SELECT payload FROM tasks WHERE owner=? ORDER BY created DESC', (owner,)).fetchall()
                if bool(json.loads(row['payload']).get('deleted_at'))==deleted][:100]

    def set_deleted(self, owner, identifier, version, deleted):
        with self.transaction() as db:
            _,task=self.task_row(db,identifier,owner)
            if task['version']!=version:
                raise Problem(409,'version_conflict','批次已变化，请刷新后操作。')
            if task['status'] in ['queued','running']:
                raise Problem(409,'task_active','运行中的批次不能删除。')
            if bool(task.get('deleted_at'))==deleted:
                raise Problem(409,'invalid_archive_state','批次状态已变化，请刷新。')
            task['deleted_at']=time.time() if deleted else None
            task['version']+=1
            self.event(db,identifier,'task_deleted' if deleted else 'task_restored',{'version':task['version']})
            self.put_task(db,task)
            return task

    def save_review_batch(self, owner, data):
        # Only the fixed, locally authored public-video sample reaches this method.
        # It creates an editable assessment copy, never queues model execution.
        with self.transaction() as db:
            rows = db.execute('SELECT payload FROM tasks WHERE owner=?', (owner,)).fetchall()
            if any(json.loads(row['payload'])['status'] in ['queued','running'] for row in rows):
                raise Problem(409,'task_active','请先完成当前任务，再打开复盘样本。')
            for row in rows:
                task = json.loads(row['payload'])
                if task.get('sample_key') == 'douyin-38-20261029' and not task.get('deleted_at'):
                    return task
            identifier = uuid.uuid4().hex
            task = {'task_id':identifier, 'name':data['name'], 'sample_key':'douyin-38-20261029',
                    'product_scope':'foyes增肌粉相关视频留言，非购买评价；按四维适配复盘',
                    'dataset_kind':'assistant_review', 'analysis_source':'assistant_review',
                    'status':'completed', 'version':1, 'records':data['records'],
                    'settings':{'model':None, 'analysis_source':'assistant_review','program_version':'0.2.0'},
                    'created_at':time.time(), 'billing_uncertain':False}
            db.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?)',
                       (identifier,owner,'sample-'+identifier,'sample',stable_json(task),time.time()))
            self.event(db,identifier,'assistant_review_loaded',{'count':len(task['records']),'model_calls':0,'source_sha256':data['source_sha256']})
            return task

    def events(self, owner, identifier):
        with self.transaction() as db:
            self.task_row(db,identifier,owner)
            return [{'id':row['id'],'kind':row['kind'],'payload':json.loads(row['payload']),'created_at':row['created']}
                    for row in db.execute('SELECT * FROM events WHERE task_id=? ORDER BY id',(identifier,)).fetchall()]

    def export_snapshot(self, owner, identifier):
        # The result and audit must represent the same transactional version.
        with self.transaction() as db:
            task=self.task_row(db,identifier,owner)[1]
            if task.get('deleted_at'):
                raise Problem(410,'task_deleted','请先从回收站恢复批次，再导出。')
            events=[{'id':row['id'],'kind':row['kind'],'payload':json.loads(row['payload']),'created_at':row['created']}
                    for row in db.execute('SELECT * FROM events WHERE task_id=? ORDER BY id',(identifier,)).fetchall()]
            return task,events

    def recover(self, reason='restart_interrupted'):
        # Restart never resubmits queued or in-flight paid requests automatically.
        with self.transaction() as db:
            for row in db.execute('SELECT * FROM tasks').fetchall():
                task=json.loads(row['payload'])
                if task['status'] not in ['queued','running']:
                    continue
                uncertain=False
                for review in task['records']:
                    if review['execution']=='running':
                        review['execution']='interrupted';uncertain=True
                task['status']='interrupted';task['version']+=1
                task['billing_uncertain']=uncertain or task.get('billing_uncertain',False)
                self.put_task(db,task)
                self.event(db,task['task_id'],reason,{'billing_uncertain':uncertain})
