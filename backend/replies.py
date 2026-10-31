"""Evidence-bounded reply templates. No model calls or platform posting."""
import hashlib
import json
from domain import comment_type, needs_review, simple_praise


def praise_reply_allowed(record):
    return (record.get('execution')=='completed' and comment_type(record)=='general_positive'
            and not (needs_review(record) and not record.get('reviewed')))


def handling_guidance(record):
    """Suggest checks and next steps, without asserting disputed business facts."""
    if praise_reply_allowed(record):
        return ([{'title':'直接致谢', 'detail':'保留整体认可，使用简短回复即可，无需额外核查。'}],
                '感谢您的认可与支持！很高兴这次购物让您满意，期待再次为您服务。')
    feedback=record.get('feedback',[])
    if feedback and not record.get('service_notes') and not needs_review(record) and all(f.get('attitude')=='正向' for f in feedback):
        return ([{'title':'记录正向体验', 'detail':'保留评价中明确提及的使用感受，简短回应，不追加无必要核查。'}],
                '感谢您分享具体的使用体验，也谢谢您对产品的认可与支持！')
    context=' '.join(str(record.get(k,'') or '') for k in ('main_text','followup_text','review_note','specification'))
    context+=' '+' '.join(n.get('summary','') for n in record.get('service_notes',[]))
    topics=[]
    def add(words,label,title,detail,information):
        if any(word in context for word in words):
            topics.append((label,{'title':title,'detail':detail},information))
    add(('临期','有效期','保质期','到期'),'临期提示', '核对购买时的临期标识',
        '对照所购SKU、下单时页面与包装有效期，确认购买前提示是否展示；当前页面不能替代下单时证据。','下单时页面与包装日期')
    add(('错发','发错','发成','混发'),'实收商品', '比对订单与实收商品',
        '逐项核对订单口味、规格和实收包装；确认差异后，再按店铺适用流程处理。','订单规格与实收包装照片')
    add(('赠品','补发','漏发','补寄'),'赠品履约', '核实赠品当前进展',
        '核对承诺内容、补发及签收记录；用户已报告收到的事项记为处理进展，不直接列作未解决投诉。','订单及约定赠品信息')
    add(('结块','摇不开','摇不匀','颗粒','咀嚼','溶解'),'冲调体验', '确认实际冲调情况',
        '确认具体口味、配比及冲调方式，并核对产品使用说明；不要仅凭反讽或颗粒描述判定成因。','具体口味与冲调方式')
    add(('破损','破了','漏粉','封口'),'包装情况', '核对受损对象',
        '区分外箱、桶体和封口是否受损，结合到货照片核查，再按适用售后流程处理。','到货时包装照片')
    add(('半桶','净含量','缺斤','少两','分量不足'),'装填与规格', '核对标示净含量和所购规格',
        '对照订单规格、包装标识及可用称量信息；不能仅凭桶内空间认定缺量。','订单规格、包装标识及可用称量信息')
    if topics:
        actions=[t[1] for t in topics]
        paths={
            '临期提示':'若下单时已有提示，说明对应位置与有效期；若提示缺失或实收不符，交由客服按适用售后规则处理。',
            '实收商品':'确认不符后核对适用的换货或售后方案；若商品一致，说明订单与实收的对应关系，未安排的处理不能说成已完成。',
            '赠品履约':'仍有欠缺时核对约定及可用补发流程；已收到且无剩余诉求时记录处理进展，不重复列作未解决投诉。',
            '冲调体验':'依据产品说明核对操作；若仍有异常，收集批次及表现交由售后核查，不自行断定产品成因。',
            '包装情况':'分别记录外箱与商品受损情况；有封口异常或漏粉时交由售后核对适用方案，不能把外箱损伤直接写成商品损坏。',
            '装填与规格':'先核对标示与可用称量信息；有差异时按适用规则处理，证据不足时先补资料，不以半桶外观直接判定缺量。'}
        next_step=paths[topics[0][0]] if len(topics)==1 else '由客服逐项汇总核实情况，按店铺适用规则提出处理方案；信息不足的事项先补充材料。'
        actions.append({'title':'根据核查结果选择处理办法','detail':next_step})
        labels='、'.join(t[0] for t in topics)
        information='、'.join(dict.fromkeys(t[2] for t in topics))
        recommendation=f'您好，关于您反馈的{labels}，请通过店铺客服提供{information}，便于逐项核对。感谢您反馈具体情况。'
        return actions,recommendation
    negative=list(dict.fromkeys(f['dimension'] for f in feedback if f.get('attitude') in ('负向','不明确')))
    if negative:
        topic='、'.join(negative)
        return ([{'title':'确认反馈对象与具体表现','detail':f'围绕{topic}核对实际商品和使用情况；记录主观感受，未核实前不认定责任或产品成因。'},
                 {'title':'核实后给出处理办法','detail':'对照产品说明及店铺适用规则，明确下一步由谁核查、需要什么信息，再准备正式答复。'}],
                f'感谢您的如实反馈。关于您提到的{topic}体验，方便的话请通过店铺客服补充具体情况，便于针对性核查。')
    return ([{'title':'明确当前诉求','detail':'确认用户是在咨询、补充处理进展，还是仍有未解决问题，再决定是否交接客服。'}],
            '感谢您的留言。如仍有需要协助的事项，请通过店铺客服说明具体问题，便于进一步处理。')


def reply_basis(record):
    fields = {key: record.get(key) for key in (
        'main_text', 'followup_text', 'comment_type', 'feedback',
        'service_notes', 'review_resolution', 'review_required')}
    return hashlib.sha256(json.dumps(fields, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def reply_plan(record):
    if record.get('execution') != 'completed':
        return {'text': '', 'status': '待分析', 'source': '', 'can_copy': False,
                'recommendation':'', 'actions':[]}
    actions,recommendation=handling_guidance(record)
    pending = needs_review(record) and not record.get('reviewed')
    resolution = record.get('review_resolution', '').strip()
    text = ('您好，关于您反馈的问题，核查答复如下：' + resolution + ' 如还有疑问，欢迎继续联系店铺客服。') if resolution and not pending and not simple_praise(record) else recommendation
    saved = record.get('reply_text', '')
    stale = bool(saved and record.get('reply_basis') != reply_basis(record))
    confirmed=bool(record.get('reply_confirmed') and saved and not stale and not pending)
    alternatives=[{'label':'推荐回复','text':recommendation}]
    if praise_reply_allowed(record):
        alternatives += [{'label':'简洁致谢','text':'感谢您的认可与支持，期待再次为您服务！'},
                         {'label':'亲切回应','text':'谢谢您的支持，很高兴收到您的好评！欢迎再次光临。'}]
    else:
        alternatives += [{'label':'礼貌回应','text':'您好，感谢您反馈具体情况。'+recommendation.removeprefix('您好，')},
                         {'label':'核查后回复','text':text}]
    seen=set();options=[]
    for item in alternatives:
        if item['text'] and item['text'] not in seen:options.append(item);seen.add(item['text'])
    return {
        'text': saved or text,
        'status': '草稿需更新' if stale else '待核查后回复' if pending else '已确认回复' if confirmed else '已保存' if saved else '回复草稿就绪',
        'source': '人工编辑' if saved else '核查答复模板' if resolution and not pending else '模板草稿',
        'can_copy': not pending and not stale,
        'stale': stale,
        'recommendation': recommendation,
        'actions': actions,
        'can_adopt_praise': praise_reply_allowed(record),
        'confirmed':confirmed,
        'options':options,
    }
