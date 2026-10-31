"""CSV and review-level rules. No model or network calls."""
import csv
import io
import json
import re
import uuid

VERSION = '0.2.0'
DIMENSIONS = ('口味', '冲调', '包装', '规格')
ATTITUDES = ('正向', '负向', '描述性', '不明确')
MAX_REVIEWS = 100
MAX_BODY = 2 * 1024 * 1024
MAX_TEXT = 10000
COMMENT_TYPES = {'experience':'具体体验反馈', 'general_positive':'一般好评', 'other':'其他评价'}


def check_comment_metadata(kind, notes, review, feedback):
    # Missing legacy metadata stays unclassified; empty feedback is not praise.
    if kind is not None and kind not in COMMENT_TYPES:
        raise Problem(422, 'invalid_comment_type', '评论类型无效。')
    if kind is not None and bool(feedback) != (kind == 'experience'):
        raise Problem(422, 'invalid_comment_type', '具体体验类型须与四维依据一致。')
    if not isinstance(notes, list) or len(notes)>8:
        raise Problem(422, 'invalid_service_notes', '补充说明最多8项。')
    result=[]
    for note in notes:
        if not isinstance(note,dict):
            raise Problem(422, 'invalid_service_notes', '补充说明格式错误。')
        summary, evidence, stage = (note.get(k) for k in ['summary','evidence','stage'])
        original=review.get('main_text' if stage=='main' else 'followup_text','')
        if (not isinstance(summary,str) or not summary.strip() or len(summary)>500 or
                stage not in ['main','followup'] or not isinstance(evidence,str) or not evidence.strip() or evidence not in original):
            raise Problem(422,'invalid_service_notes','补充说明须有摘要及对应初评或追评的连续原文依据。')
        result.append({'summary':summary,'evidence':evidence,'stage':stage})
    return kind, result


def comment_type(record):
    if record['execution']!='completed':
        return 'pending'
    if record['feedback']:
        return 'experience'
    return 'general_positive' if record.get('comment_type')=='general_positive' else 'other'


def simple_praise(record):
    return (comment_type(record)=='general_positive' and not record.get('service_notes')
            and not record.get('review_required'))


def public_review(record):
    from replies import reply_plan
    return {**record, 'reply':reply_plan(record), 'comment_type':comment_type(record), 'service_notes':record.get('service_notes',[])}


class Problem(Exception):
    def __init__(self, status, code, message, details=None):
        self.status, self.code, self.message, self.details = status, code, message, details
        super().__init__(message)


def parse_csv(text, column_map=None):
    aliases = {'初评正文':'main_text','评价正文':'main_text','评价编号':'review_id',
               '追评':'followup_text','来源':'source_ref','口味':'flavor','规格':'specification','星级':'platform_rating'}
    aliases.update(column_map or {})
    allowed = {'main_text','review_id','followup_text','source_ref','flavor','specification','platform_rating'}
    try:
        rows = list(csv.reader(io.StringIO(text.lstrip('\ufeff'), newline=''), strict=True))
    except csv.Error:
        raise Problem(422, 'invalid_csv', 'CSV引号或换行格式有误，请重新保存为UTF-8 CSV。') from None
    if not rows:
        raise Problem(422, 'empty_csv', '文件为空。')
    headers = [aliases.get(h.strip(), h.strip()) for h in rows[0]]
    if len(set(headers)) != len(headers) or 'main_text' not in headers:
        raise Problem(422, 'invalid_headers', '表头重复或缺少main_text正文列。')
    records, issues, seen = [], [], set()
    for line, row in enumerate(rows[1:], start=2):
        if not row or not any(cell.strip() for cell in row):
            continue
        if len(row) != len(headers):
            issues.append({'line':line,'message':'列数与表头不一致'})
            continue
        item = {h:v for h,v in zip(headers,row) if h in allowed}
        item['review_id'] = item.get('review_id','').strip() or f'R-{line-1:04d}'
        for field in allowed:
            item.setdefault(field, '')
        if not item['main_text'].strip():
            issues.append({'line':line,'message':'正文为空'})
        if item['review_id'] in seen:
            issues.append({'line':line,'message':'评价编号重复'})
        seen.add(item['review_id'])
        if len(item['review_id']) > 100 or len(item['main_text']) + len(item['followup_text']) > MAX_TEXT:
            issues.append({'line':line,'message':'编号过长或正文超过10000字符'})
        if any(len(item[field]) > 1500 for field in ['source_ref','flavor','specification','platform_rating']):
            issues.append({'line':line,'message':'来源或元数据字段过长'})
        item.update(execution='pending', feedback=[], reviewed=False, attempts=0,
                    raw_model_content=None, model_feedback=None, error=None, usage=None)
        records.append(item)
    if issues:
        raise Problem(422, 'invalid_rows', '请修正错误行后重新预览；未静默丢弃。', issues[:30])
    if not records:
        raise Problem(422, 'no_reviews', '没有可用评价。')
    if len(records) > MAX_REVIEWS:
        raise Problem(422, 'batch_too_large', f'当前单机版每批最多{MAX_REVIEWS}条，请拆分文件。')
    return {'headers': headers, 'records': records, 'count': len(records)}


def check_feedback(items, review):
    if not isinstance(items, list) or len(items) > 12:
        raise Problem(422, 'invalid_feedback', '反馈须为数组，单条评价最多12项。')
    result, seen = [], set()
    for item in items:
        if not isinstance(item, dict):
            raise Problem(422, 'invalid_feedback', '反馈条目格式错误。')
        f = {key:item.get(key) for key in ['dimension','attitude','summary','evidence','stage','needs_review','review_reason']}
        if f['dimension'] not in DIMENSIONS or f['attitude'] not in ATTITUDES:
            raise Problem(422, 'invalid_feedback', '反馈维度或态度不在首版范围内。')
        if not isinstance(f['summary'],str) or not f['summary'].strip() or len(f['summary']) > 500:
            raise Problem(422, 'invalid_feedback', '摘要不能为空或超过500字符。')
        stage = f['stage']
        original = review.get('main_text' if stage == 'main' else 'followup_text','')
        if stage not in ['main','followup'] or not isinstance(f['evidence'],str) or not f['evidence'].strip() or f['evidence'] not in original:
            raise Problem(422, 'invalid_evidence', '依据未在对应初评或追评中找到，请逐字引用原句。')
        if not isinstance(f['needs_review'],bool) or not isinstance(f['review_reason'],str) or len(f['review_reason']) > 500:
            raise Problem(422, 'invalid_feedback', '待确认标记或原因格式错误。')
        if f['needs_review'] and not f['review_reason'].strip():
            raise Problem(422, 'invalid_feedback', '待确认反馈需要说明原因。')
        fid = item.get('feedback_id') or uuid.uuid4().hex
        if not isinstance(fid,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',fid) or fid in seen:
            raise Problem(422, 'invalid_feedback_id', '反馈编号无效或重复。')
        seen.add(fid)
        f['feedback_id'] = fid
        result.append(f)
    return result


def needs_review(record):
    if record['execution'] != 'completed':
        return False
    if simple_praise(record):
        return False
    feedback = record['feedback']
    baseline = record.get('model_feedback', record.get('initial_feedback'))
    return bool(record.get('review_required') or record.get('review_note') or
                (baseline is not None and feedback != baseline) or
                any(f.get('needs_review') or f['attitude']=='不明确' for f in feedback))


def review_state(record):
    if record['execution'] != 'completed':
        return '未完成分析'
    if record['reviewed']:
        return '已人工复核'
    return '待复核' if needs_review(record) else '已整理'


def statistics(records):
    completed = [r for r in records if r['execution']=='completed']
    result = {'total_reviews':len(records), 'completed_reviews':len(completed),
              'failed_reviews':sum(r['execution']=='failed' for r in records),
              'pending_reviews':sum(r['execution'] in ['pending','running','interrupted'] for r in records),
                'reviewed_reviews':sum(r['reviewed'] for r in completed),
                'organized_reviews':sum(not r['reviewed'] and not needs_review(r) for r in completed),
                'needs_review_reviews':sum(not r['reviewed'] and needs_review(r) for r in completed),
              'no_relevant_feedback':sum(not r['feedback'] for r in completed),
              'comment_types':{k:sum(comment_type(r)==k for r in completed) for k in COMMENT_TYPES},
              'supplemental_reviews':sum(bool(r.get('service_notes')) for r in completed),
              'composite_reviews':sum(len({f['dimension'] for f in r['feedback']})>1 for r in completed),
              'feedback_items':sum(len(r['feedback']) for r in completed), 'dimensions':{}}
    for dim in DIMENSIONS:
        result['dimensions'][dim] = {
            'mentioned':sum(any(f['dimension']==dim for f in r['feedback']) for r in completed),
            'negative':sum(any(f['dimension']==dim and f['attitude']=='负向' for f in r['feedback']) for r in completed),
            'needs_review':sum(any(f['dimension']==dim and (f['needs_review'] or f['attitude']=='不明确') for f in r['feedback']) for r in completed)}
    return result


def export_csv(task):
    output=io.StringIO(newline='')
    writer=csv.writer(output)
    writer.writerow(['评价编号','初评原文','追评','来源','维度','态度','摘要','依据','发表阶段','待确认','复核状态','执行状态','数据类型','复核说明','原始分析备注','图片表情','评论类型','补充说明','补充说明依据','分类来源','核查答复','回复草稿','回复准备状态','回复来源','处理建议','回复推荐'])
    def safe(value):
        value=str(value if value is not None else '')
        return "'"+value if re.match(r'^\s*[=+\-@\t\r]',value) else value
    for r in task['records']:
        for f in r['feedback'] or [{}]:
            values=[r['review_id'],r['main_text'],r['followup_text'],r['source_ref'],f.get('dimension',''),
                    f.get('attitude',''),f.get('summary',''),f.get('evidence',''),f.get('stage',''),
                    '是' if f.get('needs_review') else '否',review_state(r),r['execution'],task['dataset_kind'],
                    r.get('review_note',''), '；'.join(r.get('scope_notes',[])), json.dumps(r.get('emoji_alts',[]),ensure_ascii=False),
                    COMMENT_TYPES.get(comment_type(r),'未分析'), '；'.join(n['summary'] for n in r.get('service_notes',[])),
                    '；'.join(n['evidence'] for n in r.get('service_notes',[])), r.get('classification_source','legacy_unclassified'), r.get('review_resolution',''),
                    public_review(r)['reply']['text'], public_review(r)['reply']['status'], public_review(r)['reply']['source'],
                    '；'.join(a['title']+'：'+a['detail'] for a in public_review(r)['reply']['actions']),
                    public_review(r)['reply']['recommendation']]
            writer.writerow([safe(v) for v in values])
    return '\ufeff'+output.getvalue()


def stable_json(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))
