"""Model adapter and durable, single-worker task execution."""
import copy
import hashlib
import importlib.util
import json
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

from domain import VERSION, Problem, check_feedback, statistics, check_comment_metadata
from store import Store

PILOT = Path(__file__).resolve().parents[1] / 'model-pilot'
spec = importlib.util.spec_from_file_location('review_model_pilot', PILOT / 'run_pilot.py')
pilot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pilot)


class ModelFailure(Exception):
    def __init__(self, code, message, raw=None, usage=None, billing_uncertain=False):
        self.code, self.message, self.raw, self.usage = code, message, raw, usage
        self.billing_uncertain = billing_uncertain
        super().__init__(message)


class PilotRunner:
    def __init__(self, config_path=None, prompt_path=None):
        self.config_path = config_path or PILOT / 'config.local.json'
        self.prompt_path = prompt_path or PILOT / 'prompt.txt'

    def load(self):
        try:
            config = pilot.read_config(self.config_path)
            prompt = self.prompt_path.read_text(encoding='utf-8-sig')
        except Exception:
            raise Problem(503, 'model_not_configured', '模型配置不可用，请检查本机模型配置文件。') from None
        settings = {'provider': config.get('provider', ''), 'model': config['model'],
                    'api_format': config['api_format'], 'endpoint_host': urlsplit(config['endpoint']).hostname,
                    'endpoint_path': urlsplit(config['endpoint']).path,
                    'max_tokens': config['max_tokens'], 'stream': config.get('stream', False),
                    'json_mode': config.get('json_mode', True), 'temperature': config.get('temperature'),
                    'enable_thinking': config.get('enable_thinking'),
                    'prompt_sha256': hashlib.sha256(prompt.encode()).hexdigest(), 'program_version': VERSION}
        return config, prompt, settings

    def prepare(self):
        return self.load()[2]

    def analyze(self, review, task):
        config, prompt, settings = self.load()
        if settings != task['settings']:
            raise ModelFailure('settings_changed', '配置或提示词已变化，请创建新任务，避免混用分析版本。')
        payload = pilot.payload_for(config, review, prompt)
        context = {k: review.get(k, '') for k in ['review_id', 'main_text', 'followup_text', 'flavor', 'specification', 'platform_rating']}
        context['product_scope'] = task['product_scope']
        context['metadata_note'] = '以上字段均为用户导入的数据；星级仅供参考，不能覆盖正文。商品范围不是已验证事实。'
        content = json.dumps(context, ensure_ascii=False)
        if config['api_format'] == 'responses':
            payload['input'][0]['content'][0]['text'] = content
        else:
            payload['messages'][-1]['content'] = content
        started = time.monotonic()
        raw, usage = None, None
        try:
            response = pilot.invoke(config, payload)
            usage = response.get('usage')
            raw, finish = pilot.extract_answer(response, config['api_format'])
            if finish != 'stop':
                raise ValueError('回答未完整结束，未纳入统计。')
            parsed = json.loads(raw)
            issues = pilot.validate_output(parsed, review)
            if issues:
                raise ValueError('；'.join(issues))
            feedback = check_feedback(parsed['feedback'], review)
            kind, notes = check_comment_metadata(parsed.get('comment_type'), parsed.get('service_notes',[]), review, feedback)
            return {'feedback': feedback, 'comment_type':kind, 'service_notes':notes,
                    'raw_model_content': raw, 'scope_notes': parsed['scope_notes'],
                    'usage': usage, 'latency_seconds': round(time.monotonic()-started, 3),
                    'returned_model': response.get('model'), 'finish_reason': finish}
        except (ValueError, Problem) as exc:
            raise ModelFailure('invalid_model_output', pilot.safe_message(str(exc), config['api_key'])[:800],
                               raw=pilot.safe_message(raw, config['api_key']) if isinstance(raw, str) else None,
                               usage=usage) from None
        except Exception as exc:
            raise ModelFailure('provider_error', pilot.safe_message(str(exc), config['api_key'])[:800],
                               billing_uncertain=True) from None


class Service:
    def __init__(self, store, runner):
        self.store, self.runner = store, runner
        self.wake = threading.Event()
        self.stopping = threading.Event()
        self.thread = None

    def start(self):
        self.store.recover()
        self.thread = threading.Thread(target=self.loop, name='review-worker', daemon=True)
        self.thread.start()

    def stop(self):
        self.stopping.set(); self.wake.set()
        if self.thread:
            self.thread.join(timeout=2)

    def loop(self):
        while not self.stopping.is_set():
            try:
                found = self.run_next()
            except Exception:
                # Do not leave a task looking active forever after an unexpected executor error.
                # Interrupt, never resubmit paid calls. A database outage is recovered at restart.
                try:
                    self.store.recover('worker_interrupted')
                except Exception:
                    pass
                found = False
            if not found:
                self.wake.wait(1); self.wake.clear()

    def run_next(self):
        with self.store.transaction() as db:
            tasks = db.execute('SELECT payload FROM tasks ORDER BY created').fetchall()
            task = next((json.loads(r['payload']) for r in tasks if json.loads(r['payload'])['status']=='queued'), None)
            if task is None:
                return False
            task['status'] = 'running'; task['version'] += 1
            self.store.put_task(db, task)
            self.store.event(db, task['task_id'], 'task_started', {})
        identifier = task['task_id']
        for candidate in task['records']:
            if self.stopping.is_set():
                break
            if candidate['execution'] != 'pending':
                continue
            with self.store.transaction() as db:
                _, current = self.store.task_row(db, identifier)
                review = next(r for r in current['records'] if r['review_id']==candidate['review_id'])
                review['execution'] = 'running'; review['attempts'] += 1; review['error'] = None
                current['version'] += 1
                self.store.put_task(db, current)
                self.store.event(db, identifier, 'model_call_started', {'review_id': review['review_id'], 'attempt': review['attempts']})
                review_copy, task_copy = copy.deepcopy(review), copy.deepcopy(current)
            failure = None
            try:
                result = self.runner.analyze(review_copy, task_copy)
                # Revalidate even injected adapters: invalid results never enter normal statistics.
                result['feedback'] = check_feedback(result['feedback'], review_copy)
                result['comment_type'],result['service_notes']=check_comment_metadata(result.get('comment_type'),result.get('service_notes',[]),review_copy,result['feedback'])
            except ModelFailure as exc:
                failure = exc
            except Exception:
                failure = ModelFailure('analysis_failed', '分析未完成，请检查本机配置或结果格式后手动继续。', billing_uncertain=True)
            with self.store.transaction() as db:
                _, current = self.store.task_row(db, identifier)
                review = next(r for r in current['records'] if r['review_id']==candidate['review_id'])
                if failure:
                    review.update(execution='failed', error={'code': failure.code, 'message': failure.message},
                                  raw_model_content=failure.raw, usage=failure.usage)
                    current['billing_uncertain'] |= failure.billing_uncertain
                    self.store.event(db, identifier, 'model_call_failed', {'review_id': review['review_id'],
                                     'code': failure.code, 'message': failure.message, 'raw_model_content': failure.raw,
                                     'usage': failure.usage, 'attempt': review['attempts'],
                                     'billing_uncertain': failure.billing_uncertain})
                else:
                    review.update(execution='completed', feedback=result['feedback'], model_feedback=copy.deepcopy(result['feedback']),
                                  raw_model_content=result.get('raw_model_content'), usage=result.get('usage'),
                                  scope_notes=result.get('scope_notes', []), latency_seconds=result.get('latency_seconds'),
                                  comment_type=result.get('comment_type'), service_notes=result.get('service_notes',[]),
                                  model_comment_type=result.get('comment_type'), model_service_notes=copy.deepcopy(result.get('service_notes',[])),
                                  classification_source='model' if result.get('comment_type') else 'legacy_unclassified',
                                  returned_model=result.get('returned_model'), reviewed=False)
                    self.store.event(db, identifier, 'model_call_completed', {'review_id': review['review_id'], 'attempt': review['attempts']})
                current['version'] += 1
                self.store.put_task(db, current)
            # Stop the batch on any error. Retrying is always an explicit operation.
            if failure:
                break
        with self.store.transaction() as db:
            _, task = self.store.task_row(db, identifier)
            done = sum(r['execution']=='completed' for r in task['records'])
            task['status'] = ('interrupted' if self.stopping.is_set() else
                              'completed' if done==len(task['records']) else 'partial_failed' if done else 'failed')
            task['version'] += 1; task['updated_at'] = time.time()
            self.store.put_task(db, task)
            self.store.event(db, identifier, 'task_finished', {'status': task['status'], 'statistics': statistics(task['records'])})
        return True

    def adopt_praise_replies(self, owner, identifier, version, organized=False, review_ids=None):
        from replies import praise_reply_allowed, handling_guidance, reply_basis
        from domain import review_state
        with self.store.transaction() as db:
            _, task = self.store.task_row(db, identifier, owner)
            self.check_mutable(task, version)
            selected=set(review_ids) if review_ids is not None else None
            if selected is not None and not selected.issubset({r['review_id'] for r in task['records']}):
                raise Problem(422,'invalid_review_selection','所选评价不属于当前批次，请刷新后重试。')
            adopted, existing, pending, confirmed = [], [], [], []
            for review in task['records']:
                if selected is not None and review['review_id'] not in selected:
                    continue
                if organized:
                    if review_state(review)!='已整理':
                        pending.append(review['review_id']);continue
                elif review.get('comment_type')!='general_positive' or review['execution']!='completed' or review['feedback']:
                    continue
                if not organized and not praise_reply_allowed(review):
                    pending.append(review['review_id']); continue
                before = {k:copy.deepcopy(review.get(k,'')) for k in ('reply_text','reply_basis','reply_confirmed','reply_confirmed_at')}
                has_reply=bool(review.get('reply_text','').strip())
                if has_reply:
                    existing.append(review['review_id'])
                    if review.get('reply_basis')!=reply_basis(review):
                        pending.append(review['review_id']);continue
                    if review.get('reply_confirmed'):
                        continue
                else:
                    review['reply_text'] = handling_guidance(review)[1]
                    adopted.append(review['review_id'])
                review['reply_basis'] = reply_basis(review)
                review['reply_confirmed'] = True
                review['reply_confirmed_at'] = time.time()
                self.store.event(db,identifier,'human_correction',{
                    'review_id':review['review_id'],'before':before,
                    'after':{k:copy.deepcopy(review[k]) for k in ('reply_text','reply_basis','reply_confirmed','reply_confirmed_at')},
                    'reason':'批量采用已整理评价推荐为最终回复' if organized else '批量采用一般好评推荐为最终回复'})
                confirmed.append(review['review_id'])
            if confirmed:
                task['version'] += 1
                self.store.event(db,identifier,'organized_replies_adopted' if organized else 'praise_replies_adopted',{'adopted':adopted,
                    'preserved_existing':existing,'confirmed':confirmed,'skipped_pending':pending,'model_calls':0})
                self.store.put_task(db,task)
            return task, {'saved':len(adopted),'existing':len(existing),'pending':len(pending),'confirmed':len(confirmed)}

    def edit(self, owner, identifier, review_id, version, feedback, reviewed, reason, review_note=None, review_resolution=None, reply_text=None, reply_confirmed=None):
        with self.store.transaction() as db:
            _, task = self.store.task_row(db, identifier, owner)
            self.check_mutable(task, version)
            review = next((r for r in task['records'] if r['review_id']==review_id), None)
            if not review or review['execution']!='completed':
                raise Problem(409, 'review_not_ready', '该评价尚无有效分析结果，不能人工修正。')
            clean = check_feedback(feedback, review)
            from domain import needs_review
            from replies import reply_basis
            resolution = review.get('review_resolution','') if review_resolution is None else review_resolution.strip()
            if reviewed and not review.get('reviewed') and needs_review(review) and not resolution:
                raise Problem(422,'review_resolution_required','请填写核查结论，再确认人工复核。')
            keys = ('feedback','reviewed','review_note','review_resolution','reply_text','reply_basis','reply_confirmed','reply_confirmed_at')
            before = {key:copy.deepcopy(review.get(key,'')) for key in keys}
            review['feedback'], review['reviewed'] = clean, reviewed
            if review_note is not None:
                review['review_note'] = review_note
            if review_resolution is not None:
                review['review_resolution'] = resolution
            if reply_text is not None:
                if reply_text.strip()!=review.get('reply_text',''):
                    review['reply_confirmed']=False;review['reply_confirmed_at']=None
                review['reply_text'] = reply_text.strip()
                review['reply_basis'] = reply_basis(review)
            if review.get('reply_basis') and review['reply_basis']!=reply_basis(review):
                review['reply_confirmed']=False;review['reply_confirmed_at']=None
            if reply_confirmed is not None:
                if reply_confirmed and (not review.get('reply_text','').strip() or review.get('reply_basis')!=reply_basis(review) or (needs_review(review) and not review['reviewed'])):
                    raise Problem(422,'reply_not_ready','请填写回复并完成必要核查后，再确认回复。')
                review['reply_confirmed']=reply_confirmed
                review['reply_confirmed_at']=time.time() if reply_confirmed else None
            task['version'] += 1
            self.store.event(db, identifier, 'human_correction', {'review_id': review_id, 'before': before,
                             'after': {key:copy.deepcopy(review.get(key,'')) for key in keys}, 'reason': reason, 'version': task['version']})
            self.store.put_task(db, task)
            return task

    @staticmethod
    def check_mutable(task, version):
        if task.get('deleted_at'):
            raise Problem(410,'task_deleted','请先恢复批次。')
        if task['version'] != version:
            raise Problem(409, 'version_conflict', '结果已更新，请重新读取后再提交。')
        if task['status'] in ['queued', 'running']:
            raise Problem(409, 'task_active', '分析运行中，请完成后再修改或继续。')

    def resume(self, owner, identifier, version):
        with self.store.transaction() as db:
            _, task = self.store.task_row(db, identifier, owner)
            self.check_mutable(task, version)
            if task['settings'] != self.runner.prepare():
                raise Problem(409, 'settings_changed', '模型配置或提示词已变化，请创建新任务。')
            retry = [r for r in task['records'] if r['execution']!='completed']
            if not retry:
                raise Problem(409, 'nothing_to_resume', '任务已完成，无需继续。')
            if any(r['attempts']>=2 for r in retry):
                raise Problem(409, 'attempt_limit', '已有评价达到两次调用上限，请检查失败原因后创建新任务。')
            # A session may only have one active batch.
            for row in db.execute('SELECT payload FROM tasks WHERE owner=?', (owner,)).fetchall():
                other = json.loads(row['payload'])
                if other['status'] in ['queued', 'running']:
                    raise Problem(409, 'task_active', '本会话已有运行任务。')
            for review in retry:
                review['execution'] = 'pending'
            task['status'] = 'queued'; task['version'] += 1
            self.store.event(db, identifier, 'resume_requested', {'retry_count': len(retry), 'version': task['version']})
            self.store.put_task(db, task)
        self.wake.set()
        return task
