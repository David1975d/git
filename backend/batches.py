"""Business batch naming and explicit consolidation of already analysed records."""
import copy
import hashlib
import json
import time
import uuid
from datetime import datetime, timedelta, timezone
from domain import MAX_REVIEWS, Problem, stable_json


def business_name(platform, product_name, count, date=None):
    if not platform.strip() or not product_name.strip():
        raise Problem(422, 'batch_identity_required', '请填写平台和商品名称。')
    date = date or datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d')
    return f'{platform.strip()}｜{product_name.strip()}｜{date}｜{count}条'


def consolidate_completed(store, owner, source_ids, platform, product_name, date=None):
    """Atomic, idempotent local maintenance action; no model execution or hard delete."""
    ids = sorted(set(source_ids))
    key = 'merge-' + hashlib.sha256(stable_json(ids).encode()).hexdigest()[:32]
    with store.transaction() as db:
        old = db.execute('SELECT payload FROM tasks WHERE owner=? AND request_id=?', (owner,key)).fetchone()
        if old:
            return json.loads(old['payload'])
        sources = [store.task_row(db, identifier, owner)[1] for identifier in ids]
        if not sources or any(t['status'] in ['queued','running'] for t in sources):
            raise Problem(409,'merge_not_ready','请先完成或停止相关批次。')
        if len({stable_json(t['settings']) for t in sources}) != 1:
            raise Problem(409,'merge_settings_conflict','分析规则不同，不能合并为同一批次。')
        chosen, inputs = {}, {}
        for task in sources:
            for r in task['records']:
                rid=r['review_id']
                identity=[r.get(k,'') for k in ('main_text','followup_text','specification','platform_rating')]
                if rid in inputs and inputs[rid] != identity:
                    raise Problem(409,'merge_source_conflict','相同评价编号的原文不一致。')
                inputs[rid]=identity
                if r['execution']=='completed':
                    if rid in chosen:
                        raise Problem(409,'merge_duplicate_success','同一评价存在多份成功结果，请先选择保留版本。')
                    chosen[rid]={**copy.deepcopy(r),'source_task_id':task['task_id']}
        if set(chosen) != set(inputs):
            raise Problem(409,'merge_incomplete','仍有评价没有成功结果，不能标记整批完成。')
        if not 1 <= len(chosen) <= MAX_REVIEWS:
            raise Problem(422,'batch_too_large',f'每批最多{MAX_REVIEWS}条。')
        now=time.time(); identifier=uuid.uuid4().hex
        task={'task_id':identifier,'name':business_name(platform,product_name,len(chosen),date),
              'platform':platform,'product_name':product_name,'dataset_kind':sources[0]['dataset_kind'],
              'product_scope':sources[0]['product_scope'],'status':'completed','version':1,
              'records':[chosen[k] for k in sorted(chosen)],'settings':sources[0]['settings'],
              'created_at':now,'billing_uncertain':any(t.get('billing_uncertain',False) for t in sources),
              'source_task_ids':ids,'consolidated':True}
        db.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?)',
                   (identifier,owner,key,key,stable_json(task),now))
        for source in sources:
            for event in db.execute('SELECT * FROM events WHERE task_id=? ORDER BY id',(source['task_id'],)).fetchall():
                payload=json.loads(event['payload'])
                payload.update(source_task_id=source['task_id'],source_event_id=event['id'])
                db.execute('INSERT INTO events(task_id,kind,payload,created) VALUES(?,?,?,?)',
                           (identifier,event['kind'],stable_json(payload),event['created']))
            original_name=source['name']
            source.update(name=business_name(platform,product_name,len(source['records']),date),
                          platform=platform,product_name=product_name,deleted_at=now,merged_into=identifier,
                          version=source['version']+1)
            store.event(db,source['task_id'],'task_consolidated',{'target_task_id':identifier,'previous_name':original_name})
            store.put_task(db,source)
        store.event(db,identifier,'batch_consolidated',{'source_task_ids':ids,'count':len(chosen),'new_model_calls':0})
        return task
