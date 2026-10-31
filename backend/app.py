"""Local backend. Launch one worker on 127.0.0.1; frontend integration is separate."""
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, StrictBool
from starlette.middleware.trustedhost import TrustedHostMiddleware

from domain import MAX_BODY, MAX_REVIEWS, VERSION, Problem, export_csv, parse_csv, statistics, public_review
from service import PilotRunner, Service
from store import Store

COOKIE = 'review_session'


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=False)


class ImportInput(StrictModel):
    csv_text: str = Field(min_length=1, max_length=MAX_BODY)
    column_map: dict[str, str] = Field(default_factory=dict, max_length=30)


class TaskInput(StrictModel):
    import_id: str = Field(pattern=r'^[a-f0-9]{32}$')
    request_id: str = Field(pattern=r'^[A-Za-z0-9_-]{8,100}$')
    name: str = Field(min_length=1, max_length=120)
    product_scope: str = Field(min_length=1, max_length=1000)
    dataset_kind: Literal['user_supplied', 'synthetic_development']
    platform: str = Field(default='', max_length=20)
    product_name: str = Field(default='', max_length=60)


class Feedback(StrictModel):
    feedback_id: str | None = Field(default=None, max_length=100)
    dimension: Literal['口味', '冲调', '包装', '规格']
    attitude: Literal['正向', '负向', '描述性', '不明确']
    summary: str = Field(min_length=1, max_length=500)
    evidence: str = Field(min_length=1, max_length=10000)
    stage: Literal['main', 'followup']
    needs_review: StrictBool
    review_reason: str = Field(max_length=500)


class EditInput(StrictModel):
    version: int = Field(ge=1, strict=True)
    feedback: list[Feedback] = Field(max_length=12)
    reviewed: StrictBool
    change_reason: str = Field(min_length=1, max_length=500)
    review_note: str | None = Field(default=None, max_length=1500)
    review_resolution: str | None = Field(default=None, max_length=1500)
    reply_text: str | None = Field(default=None, max_length=2000)
    reply_confirmed: StrictBool | None = None


class ResumeInput(StrictModel):
    version: int = Field(ge=1, strict=True)


class AdoptRepliesInput(ResumeInput):
    review_ids: list[str] = Field(min_length=1, max_length=MAX_REVIEWS)


class BodyLimit:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type']!='http' or scope['method'] not in ['POST','PATCH','PUT']:
            return await self.app(scope, receive, send)
        body = bytearray()
        while True:
            message = await receive()
            if message['type']=='http.disconnect':
                return
            body.extend(message.get('body', b''))
            if len(body)>MAX_BODY:
                return await JSONResponse({'error': {'code': 'body_too_large', 'message': '请求超过2MB。'}}, status_code=413)(scope, receive, send)
            if not message.get('more_body'):
                break
        delivered = False
        async def replay():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {'type': 'http.request', 'body': bytes(body), 'more_body': False}
            return await receive()
        return await self.app(scope, replay, send)


def public_task(task):
    if task is None:
        return None
    return {**{k:v for k,v in task.items() if k!='records'}, 'statistics': statistics(task['records'])}


def create_app(data_dir=None, runner=None, start_worker=True):
    directory = Path(data_dir or os.environ.get('REVIEW_DATA_DIR', Path(__file__).parent / 'data'))
    store = Store(directory / 'reviews.sqlite3')
    service = Service(store, runner or PilotRunner())

    @asynccontextmanager
    async def lifespan(app):
        # OS lock prevents two processes from recovering/executing the same task.
        lock = None
        if start_worker:
            lock = (directory / 'worker.lock').open('a+b')
            try:
                if os.name == 'nt':
                    import msvcrt
                    lock.seek(0); lock.write(b'0'); lock.flush(); lock.seek(0)
                    msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                lock.close()
                raise RuntimeError('当前数据目录已有后端运行，请勿重复启动。') from None
            app.state.worker_lock = lock
            service.start()
        try:
            yield
        finally:
            service.stop()
            # Keep lock held if a paid call is still in-flight during graceful shutdown.
            if lock and (not service.thread or not service.thread.is_alive()):
                lock.close()

    app = FastAPI(title='析言 · 评论复盘后端', version=VERSION, lifespan=lifespan)
    app.state.service = service
    app.add_middleware(BodyLimit)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=['127.0.0.1','localhost','testserver'])

    @app.exception_handler(Problem)
    async def problem_handler(request, exc):
        return JSONResponse({'error': {'code': exc.code, 'message': exc.message, 'details': exc.details}}, status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request, exc):
        # Pydantic's default response echoes input; never return supplied secrets/text in errors.
        details = [{'field': '.'.join(map(str,e['loc'])), 'type': e['type']} for e in exc.errors()]
        return JSONResponse({'error': {'code': 'invalid_request', 'message': '请求字段或类型有误。', 'details': details}}, status_code=422)

    @app.exception_handler(Exception)
    async def internal_handler(request, exc):
        return JSONResponse({'error': {'code': 'internal_error', 'message': '服务处理失败，已保存的结果仍保留。'}}, status_code=500)

    @app.middleware('http')
    async def response_headers(request, call_next):
        response = await call_next(request)
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        return response

    def owner(request, write=False):
        if write:
            origin = request.headers.get('origin')
            if origin:
                actual = urlsplit(str(request.base_url))
                supplied = urlsplit(origin)
                if (supplied.scheme, supplied.netloc) != (actual.scheme, actual.netloc):
                    raise Problem(403, 'origin_failed', '当前来源不允许修改数据。')
        return store.authorize(request.cookies.get(COOKIE), request.headers.get('x-csrf-token','') if write else None)

    @app.get('/')
    def index():
        return FileResponse(Path(__file__).resolve().parents[1] / 'frontend' / 'index.html')

    @app.get('/{asset}', include_in_schema=False)
    def frontend_asset(asset: str):
        # Exact allowlist: never expose config, SQLite, logs or research materials.
        if asset not in {'index.html','styles.css','app.js','live.js'}:
            raise Problem(404, 'not_found', '文件不存在。')
        return FileResponse(Path(__file__).resolve().parents[1] / 'frontend' / asset)

    @app.get('/api/health')
    def health():
        return {'status': 'ok', 'service_id': 'xiyan-review', 'version': VERSION,
                'workspace_connected': True, 'max_reviews': MAX_REVIEWS, 'mode': 'local_single_worker',
                'review_notes_and_history': True}

    @app.get('/api/session')
    def session(request: Request, response: Response):
        token, _, csrf = store.session(request.cookies.get(COOKIE))
        response.set_cookie(COOKIE, token, httponly=True, samesite='strict', secure=False, max_age=30*86400, path='/')
        return {'csrf_token': csrf, 'expires_in_seconds': 30*86400}

    @app.post('/api/imports/preview', status_code=201)
    def preview(body: ImportInput, request: Request):
        identity = owner(request, True)
        allowed = {'main_text','followup_text','review_id','source_ref','flavor','specification','platform_rating'}
        if any(len(k)>150 or v not in allowed for k,v in body.column_map.items()):
            raise Problem(422, 'invalid_column_map', '列映射目标无效。')
        data = parse_csv(body.csv_text, body.column_map)
        identifier = store.save_import(identity, data)
        return {'import_id': identifier, 'count': data['count'], 'headers': data['headers'],
                'preview': [{k:v for k,v in r.items() if k in allowed} for r in data['records']], 'model_called': False}

    @app.post('/api/tasks', status_code=202)
    def create_task(body: TaskInput, request: Request):
        identity = owner(request, True)
        settings = service.runner.prepare()
        task, created = store.create_task(identity, body.import_id, body.request_id, body.name,
                                          body.product_scope, body.dataset_kind, settings,
                                          platform=body.platform, product_name=body.product_name)
        service.wake.set()
        return {**public_task(task), 'created': created}

    @app.get('/api/tasks/current')
    def current_task(request: Request):
        return {'task': public_task(store.current_task(owner(request)))}

    @app.get('/api/tasks')
    def task_history(request: Request, deleted: bool=False):
        return {'tasks': [public_task(task) for task in store.list_tasks(owner(request), deleted)]}

    @app.delete('/api/tasks/{task_id}')
    def delete_task(task_id: str, body: ResumeInput, request: Request):
        return public_task(store.set_deleted(owner(request,True),task_id,body.version,True))

    @app.post('/api/tasks/{task_id}/restore')
    def restore_task(task_id: str, body: ResumeInput, request: Request):
        return public_task(store.set_deleted(owner(request,True),task_id,body.version,False))

    @app.post('/api/examples/douyin-38', status_code=201)
    def load_douyin_review(request: Request):
        identity = owner(request, True)
        path = Path(__file__).resolve().parents[1] / 'samples' / 'douyin-video-2026-10-28' / 'analysis-2026-10-29' / 'analysis.json'
        if not path.exists():
            raise Problem(404, 'sample_missing', '本机复盘样本尚未准备。')
        data = json.loads(path.read_text(encoding='utf-8'))
        return public_task(store.save_review_batch(identity, data))

    @app.get('/api/tasks/{task_id}')
    def get_task(task_id: str, request: Request):
        return public_task(store.get_task(owner(request), task_id))

    @app.get('/api/tasks/{task_id}/reviews')
    def get_reviews(task_id: str, request: Request, offset: int=0, limit: int=20):
        task = store.get_task(owner(request), task_id)
        if offset<0 or limit<1 or limit>20:
            raise Problem(422, 'invalid_pagination', 'offset须非负，limit须在1至20之间。')
        return {'version': task['version'], 'total': len(task['records']), 'records': [public_review(r) for r in task['records'][offset:offset+limit]]}

    @app.patch('/api/tasks/{task_id}/reviews/{review_id}')
    def edit(task_id: str, review_id: str, body: EditInput, request: Request):
        task = service.edit(owner(request, True), task_id, review_id, body.version,
                            [f.model_dump(exclude_none=True) for f in body.feedback], body.reviewed, body.change_reason, body.review_note, body.review_resolution, body.reply_text, body.reply_confirmed)
        return public_task(task)

    @app.post('/api/tasks/{task_id}/replies/adopt-praise')
    def adopt_praise(task_id: str, body: ResumeInput, request: Request):
        task, result = service.adopt_praise_replies(owner(request, True), task_id, body.version)
        return {**public_task(task), 'adoption':result}

    @app.post('/api/tasks/{task_id}/replies/adopt-organized')
    def adopt_organized(task_id: str, body: AdoptRepliesInput, request: Request):
        task, result = service.adopt_praise_replies(owner(request, True), task_id, body.version,
                                                  organized=True, review_ids=body.review_ids)
        return {**public_task(task), 'adoption':result}

    @app.post('/api/tasks/{task_id}/resume', status_code=202)
    def resume(task_id: str, body: ResumeInput, request: Request):
        return public_task(service.resume(owner(request, True), task_id, body.version))

    @app.get('/api/tasks/{task_id}/audit')
    def audit(task_id: str, request: Request):
        return {'events': store.events(owner(request), task_id)}

    @app.get('/api/tasks/{task_id}/export')
    def export(task_id: str, request: Request, format: Literal['csv','json']='csv', version: int | None=None):
        identity = owner(request)
        task, events = store.export_snapshot(identity, task_id)
        if version is not None and version != task['version']:
            raise Problem(409, 'version_conflict', '导出版本已变化，请重新读取。')
        if format=='csv':
            content, media = export_csv(task), 'text/csv'
        else:
            content = json.dumps({**task, 'records':[public_review(r) for r in task['records']], 'statistics': statistics(task['records']), 'audit': events}, ensure_ascii=False)
            media = 'application/json'
        return Response(content, media_type=media, headers={'Content-Disposition': f'attachment; filename="reviews-{task_id}.{format}"'})

    return app


app = create_app()
