'use strict';
const DIMS = ['口味', '冲调', '包装', '规格'];
const ATTITUDES = ['正向', '负向', '描述性', '不明确'];
const STORE = 'xiyan.frontend.v1';
const $ = s => document.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const feedback = (dim, attitude, summary, evidence) => ({id:crypto.randomUUID(), dim, attitude, summary, evidence});
function demo() {
  const rows = [
    ['酸奶味有点甜，兑多一些水就能接受。', [feedback('口味','负向','用户认为酸奶味偏甜。','酸奶味有点甜')]],
    ['摇了很久还是有小块，杯底也有残留。', [feedback('冲调','负向','用户报告难摇匀且有杯底残留。','摇了很久还是有小块，杯底也有残留')]],
    ['收到时罐子已经凹了，封口边缘也破了。', [feedback('包装','负向','用户报告罐体凹陷和封口破损。','罐子已经凹了，封口边缘也破了')]],
    ['桶看起来挺大，打开只有半桶，没称过，不知道是不是少了。', [feedback('规格','不明确','用户对装填量有疑虑，净含量尚未核实。','打开只有半桶，没称过，不知道是不是少了')]],
    ['主商品香草味太甜，赠品奶茶味倒是不错。', [feedback('口味','负向','用户认为主商品香草味太甜；不将赠品好评归入主商品。','主商品香草味太甜')]],
    ['真不错，喝一杯还能顺便练咀嚼能力。', [feedback('冲调','不明确','可能以反讽表达颗粒感，需要核对。','喝一杯还能顺便练咀嚼能力')]],
    ['问了几次退货怎么办，一直没给我说明白。', []],
    ['香草味我喜欢，不过封口边缘有一点破损。', [feedback('口味','正向','用户喜欢香草口味。','香草味我喜欢'),feedback('包装','负向','用户报告封口边缘破损。','封口边缘有一点破损')]]
  ];
  return {version:1,mode:'demo',name:'蛋白粉 · 差评示例',demoRevision:3,records:rows.map(([text,items],i)=>({id:`EX-${String(i+1).padStart(3,'0')}`,text,followup:'',source:'虚构演示材料',feedback:items,reviewed:[0,2].includes(i),analyzed:true})),changes:[]};
}
let state;
try { const saved=JSON.parse(localStorage.getItem(STORE)); state=validState(saved)?(saved.mode==='demo'&&!saved.changes.length&&saved.demoRevision!==3?demo():saved):demo(); } catch { state=demo(); }
function validState(s){return s?.version===1&&['demo','import'].includes(s.mode)&&typeof s.name==='string'&&Array.isArray(s.records)&&s.records.every(r=>typeof r.id==='string'&&typeof r.text==='string'&&Array.isArray(r.feedback)&&r.feedback.every(f=>DIMS.includes(f.dim)&&ATTITUDES.includes(f.attitude)&&typeof f.evidence==='string'&&typeof f.summary==='string'))&&Array.isArray(s.changes);}
let commentType='all';
const PAGE_SIZE=10;
let currentPage=1,paginationKey='';
let dimension='全部',query='',status='all',selectedId=null,pendingImport=null;
let toastTimer;
function toast(message){$('#toast').textContent=message;$('#toast').classList.add('visible');clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('#toast').classList.remove('visible'),3200);}
function askReplace(){return new Promise(resolve=>{const d=$('#replace-dialog');let accepted=false;$('#accept-replace').onclick=()=>{accepted=true;d.close();};$('#cancel-replace').onclick=()=>d.close();d.addEventListener('close',()=>resolve(accepted),{once:true});d.showModal();});}
function persist(){try{localStorage.setItem(STORE,JSON.stringify(state));$('#save-state').innerHTML='<span class="live-dot"></span> 修改保留在本机';return true;}catch{$('#save-state').textContent='本次修改未保存';toast('本机保存失败，请及时导出结果');return false;}}
function stats(){return DIMS.map(dim=>({dim,mentioned:state.records.filter(r=>r.feedback.some(f=>f.dim===dim)).length,negative:state.records.filter(r=>r.feedback.some(f=>f.dim===dim&&f.attitude==='负向')).length,uncertain:state.records.filter(r=>r.feedback.some(f=>f.dim===dim&&f.attitude==='不明确')).length}));}
function statusScopeRecords(){return state.records.filter(r=>(commentType==='all'||commentKind(r)===commentType)&&(dimension==='全部'||r.feedback.some(f=>f.dim===dimension))&&(!query||recordSearchText(r).includes(query.toLowerCase())));}
function filtered(){return statusScopeRecords().filter(statusMatches);}
function tag(f){return `<span class="tag ${f.attitude==='负向'?'tag-negative':f.attitude==='不明确'?'tag-uncertain':''}">${esc(f.dim)}</span>`;}
function needsReview(r){if(isSimplePraise(r))return false;return Boolean(r.analyzed&&(r.reviewRequired||r.feedback.some(f=>f.needs_review||f.attitude==='不明确')||hasCorrections(r)));}
function isSimplePraise(r){return Boolean(r.analyzed&&r.commentType==='general_positive'&&!r.feedback.length&&!r.serviceNotes?.length&&!r.reviewRequired);}
function listReviewStatus(r){
  const kind=!r.analyzed?'pending':r.reviewed?'reviewed':needsReview(r)?'attention':'organized';
  const plan=replyPlan(r),saved=r.reply?.source==='人工编辑';
  return `<div class="status-stack"><span class="state status-primary status-${kind}">${esc(reviewState(r))}</span>${saved?`<span class="reply-ready ${plan.confirmed?'reply-confirmed':'reply-saved'}">${plan.confirmed?'<span aria-hidden="true">✓</span> 已确认回复':'回复已保存'}</span>`:''}</div>`;
}
function reviewState(r){return !r.analyzed?'未完成分析':r.reviewed?'已人工复核':needsReview(r)?'待复核':'已整理';}
function hasCorrections(r){return Boolean(r.corrected||r.reviewNote||(r.baseline&&JSON.stringify(r.feedback)!==JSON.stringify(r.baseline)));}
function commentKind(r){return !r.analyzed?'pending':r.feedback.length?'experience':r.commentType==='general_positive'?'general_positive':'other';}
function commentLabel(r){return ({experience:'具体体验反馈',general_positive:'一般好评',other:'其他评价',pending:'未分析'})[commentKind(r)];}
function serviceSummary(r){return (r.serviceNotes||[]).map(n=>n.summary).join('；');}
function statusMatches(r){return status==='all'||status==='reviewed'&&r.analyzed&&r.reviewed||status==='unreviewed'&&r.analyzed&&!r.reviewed&&needsReview(r)||status==='organized'&&r.analyzed&&!r.reviewed&&!needsReview(r);}
function recordSearchText(r){return [r.text,r.followup,r.id,r.reviewNote,r.reviewResolution,r.reply?.text,commentLabel(r),serviceSummary(r),...(r.emojiAlts||[]),...(r.scopeNotes||[]),...r.feedback.map(f=>f.summary)].join(' ').toLowerCase();}
function setReviewFilter(value){status=['all','organized','unreviewed','reviewed'].includes(value)?value:'all';$('#status-filter').value=status;render();}
function reviewReason(r){return [r.reviewNote,...r.feedback.filter(f=>f.needs_review||f.attitude==='不明确').map(f=>f.review_reason),...(r.reviewRequired?(r.scopeNotes||[]):[])].filter(Boolean).join('；')||(hasCorrections(r)?'修正或补充已保存，待确认本次修改。':'请核对标记不明确或待确认的反馈。');}
function handlingPoints(r){
  const clean=t=>String(t||'').replace(/^助手复核建议[：:]\s*/,'').trim();
  const points=[r.reviewNote,...r.feedback.filter(f=>f.needs_review||f.attitude==='不明确').map(f=>f.review_reason),...(r.reviewRequired?(r.scopeNotes||[]):[])].map(clean).filter(Boolean);
  const key=t=>t.replace(/[\s，。；：、,.!！?？:;]/g,'');
  return points.filter((p,i)=>!points.some((other,j)=>j!==i&&key(other).includes(key(p))&&(key(other)!==key(p)||j<i)));
}
function replyPlan(r){
  const recommendation=isSimplePraise(r)?'感谢您的认可与支持！很高兴这次购物让您满意，期待再次为您服务。':'感谢您的留言与反馈。如还有需要协助的事项，欢迎通过店铺客服进一步沟通。';
  const fallback={text:!r.analyzed?'':r.reviewResolution?'您好，关于您反馈的问题，核查答复如下：'+r.reviewResolution+' 如还有疑问，欢迎继续联系店铺客服。':recommendation,status:!r.analyzed?'待分析':needsReview(r)&&!r.reviewed?'待核查后回复':'回复草稿就绪',source:'模板草稿',can_copy:r.analyzed&&(!needsReview(r)||r.reviewed)};
  return {...fallback,...(r.reply||{}),recommendation:r.reply?.recommendation||recommendation};
}
let expandedNotesFor=null,expandedHumanFor=null;
function serviceEvidence(r){
  if(!(r.serviceNotes||[]).length)return '';
  return `<div class="service-notes"><h4>其他反馈与依据</h4>${r.serviceNotes.map(n=>`<article><p>${esc(n.summary)}</p><details><summary>查看原文</summary><blockquote>${esc(n.evidence)}</blockquote></details></article>`).join('')}</div>`;
}
function reviewPanel(r){
  const simple=isSimplePraise(r),plan=replyPlan(r);
  const actions=plan.actions?.length?plan.actions:handlingPoints(r).map(text=>({title:'确认待核查事项',detail:text}));
  const items=actions.length?actions:[{title:simple?'直接致谢':'明确下一步',detail:simple?'使用简短回复即可，无需额外核查。':'结合原文确认诉求和下一步处理办法。'}];
  const options=plan.options?.length?plan.options:[{label:'推荐回复',text:plan.recommendation||plan.text}];
  return `<section class="detail-section handling-section" aria-labelledby="handling-title"><div class="detail-section-heading"><h3 id="handling-title">核查与回应要点</h3></div>
    <div class="handling-step points-step"><h4>处理要点</h4><ol id="saved-review-note" class="action-checklist">${items.map(p=>`<li><strong>${esc(p.title)}</strong><p>${esc(p.detail)}</p></li>`).join('')}</ol><details class="handling-notes" id="handling-notes" ${expandedNotesFor===r.id?'open':''} ${simple?'hidden':''}><summary>处理备注</summary><p class="saved-note-text">${esc(r.reviewNote||'暂无备注')}</p><button class="text-button" id="edit-review-note">${r.reviewNote?'修改备注':'添加备注'}</button><div id="review-note-editor"></div></details></div>
    <div class="handling-step human-review"><details class="resolution-area" ${needsReview(r)||r.reviewResolution?'open':''} ${simple||!r.analyzed?'hidden':''}><summary>人工核查</summary><div class="feedback-heading"><h4>核查结论</h4><button class="text-button" id="edit-resolution">${r.reviewResolution?'修改结论':'填写结论'}</button></div><p id="saved-resolution" ${r.reviewResolution?'':'hidden'}>${esc(r.reviewResolution||'')}</p><div id="resolution-editor"></div></details>
    <form id="reply-form" class="reply-area"><div class="reply-heading"><label for="reply-text">最终回复</label><span id="reply-status" class="reply-status" ${plan.status==='回复草稿就绪'?'hidden':''}>${esc(plan.status)}</span></div><textarea id="reply-text" name="reply_text" class="edit-input" maxlength="2000" ${!r.analyzed?'disabled':''}>${esc(plan.text)}</textarea>
    <div class="reply-options" role="group" aria-label="推荐回复选项">${options.map((o,i)=>`<button type="button" class="reply-option" data-reply-option="${i}" title="${esc(o.text)}" ${!r.analyzed?'disabled':''}>${esc(o.label)}</button>`).join('')}</div>
    <div class="reply-actions"><button class="button button-outline button-small" type="submit" ${!r.analyzed?'disabled':''}>保存</button><button class="button button-primary button-small" id="confirm-reply" type="button" ${!plan.can_copy||plan.confirmed?'disabled':''}>${plan.confirmed?'已确认回复':'确认回复'}</button></div><p id="reply-error" class="inline-error" role="alert"></p></form></div>${historyPanel()}</section>`;
}
function editResolution(){
  const r=current(),version=typeof liveTask!=='undefined'?liveTask?.version:undefined;
  expandedHumanFor=r.id;$('.human-review').open=true;
  $('#resolution-editor').innerHTML=`<form><label class="field-label">核查结论（用于最终回复）<textarea name="review_resolution" class="edit-input" maxlength="1500" placeholder="填写已核实的情况，或需要向客户澄清的问题…" required>${esc(r.reviewResolution||'')}</textarea></label><p class="section-description">记录已核实的情况或需澄清的问题，不承诺未经批准的处理。</p><div class="edit-actions"><button class="text-button" type="button" data-cancel>取消</button><button class="button button-primary button-small" type="submit">保存结论并确认复核</button></div><p class="inline-error" role="alert"></p></form>`;
  const form=$('#resolution-editor form');form.querySelector('[data-cancel]').onclick=renderDetail;
  form.onsubmit=e=>{e.preventDefault();saveResolution(r,form.elements.review_resolution.value.trim(),version);};
  $('#mark-reviewed').disabled=true;form.elements.review_resolution.focus();
}
function saveResolution(r,value){r.reviewResolution=value;r.reviewed=true;r.reply=null;state.changes.push({at:new Date().toISOString(),reviewId:r.id,action:'resolution',after:value});persist();render();renderDetail();toast('核查答复已保存，已确认复核');}
function saveReply(r,text,version,confirmed=false){
  if(!text){$('#reply-error').textContent='请填写回复内容。';return;}
  r.reply={...replyPlan(r),text,source:'人工编辑',confirmed,status:confirmed?'已确认回复':'已保存'};
  state.changes.push({at:new Date().toISOString(),reviewId:r.id,action:confirmed?'reply_confirm':'reply',after:text});persist();render();renderDetail();toast(confirmed?'回复已确认':'已保存');
}
function bindReply(r){
  $('#edit-resolution').onclick=editResolution;
  const plan=replyPlan(r),version=typeof liveTask!=='undefined'?liveTask?.version:undefined;
  $('#reply-form').onsubmit=e=>{e.preventDefault();saveReply(r,$('#reply-text').value.trim(),version,false);};
  $('#confirm-reply').onclick=()=>saveReply(r,$('#reply-text').value.trim(),version,true);
  $('#reply-text').oninput=()=>{$('#reply-form').dataset.dirty='true';$('#confirm-reply').disabled=!plan.can_copy||!$('#reply-text').value.trim();$('#reply-status').hidden=false;$('#reply-status').textContent='未保存';};
  document.querySelectorAll('[data-reply-option]').forEach(button=>button.onclick=()=>{
    const options=plan.options?.length?plan.options:[{text:plan.recommendation||plan.text}];
    $('#reply-text').value=options[Number(button.dataset.replyOption)].text;
    $('#reply-text').dispatchEvent(new Event('input'));$('#reply-text').focus();
  });
}
function historyPanel(){return `<details class="detail-section history-section" id="history-section"><summary><span class="section-number">04</span><span>复核记录</span><span class="history-hint">展开查看修改与确认记录</span></summary><div id="review-history"><p class="muted">尚无人工复核记录</p></div></details>`;}
function localReviewHistory(r){const events=state.changes.filter(e=>e.reviewId===r.id);$('#review-history').innerHTML=events.length?events.slice().reverse().map(e=>`<p class="detail-kicker">${esc(new Date(e.at).toLocaleString())} · ${esc(e.action==='review'?(e.after?'确认复核':'撤销复核'):e.action==='note'?'保存复核说明':'修正/补充反馈')}</p>`).join(''):'<p class="muted">尚无人工复核记录</p>';}
function saveReviewNote(record,value){const before=record.reviewNote||'';record.reviewNote=value;record.reviewed=false;state.changes.push({at:new Date().toISOString(),reviewId:record.id,action:'note',before,after:value});const saved=persist();render();renderDetail();if(saved)toast('复核说明已保存，等待确认复核');}
function editReviewNote(){renderDetail();const record=current(),expectedVersion=typeof liveTask!=='undefined'?liveTask?.version:undefined;expandedNotesFor=record.id;$('#handling-notes').open=true;$('#review-note-editor').innerHTML=`<form class="feedback-editor"><label class="field-label">处理要点<textarea class="edit-input" name="review_note" maxlength="1500" required>${esc(record.reviewNote||'')}</textarea></label><p class="inline-error" role="alert"></p><div class="edit-actions"><button type="button" class="text-button" data-cancel>取消</button><button type="submit" class="button button-primary button-small">保存处理要点</button></div></form>`;const form=$('#review-note-editor form');$('#mark-reviewed').disabled=true;form.querySelector('[data-cancel]').onclick=renderDetail;form.onsubmit=event=>{event.preventDefault();saveReviewNote(record,form.elements.review_note.value.trim(),expectedVersion);};}
function render(){
  $('#batch-title').textContent=state.name;
  $('#batch-type').textContent=state.mode==='demo'?'交互演示':'已导入';
  $('#total-count').textContent=state.records.length;
  $('#metrics').innerHTML=stats().map((s,i)=>{const total=state.records.length||1;return `<button class="metric ${dimension===s.dim?'selected':''}" data-dim="${s.dim}" aria-pressed="${dimension===s.dim}"><div class="metric-label">${s.dim}<span>${['<svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M8 3h8l-1 17H9L8 3Z"/><path d="M9 8h6M11 12h2"/></svg>', '<svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 8c3-5 5 5 8 0s5 5 8 0M4 16c3-5 5 5 8 0s5 5 8 0"/></svg>', '<svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m3 7 9-4 9 4v10l-9 4-9-4V7Z"/><path d="m3 7 9 4 9-4M12 11v10M7 5l9 4"/></svg>', '<svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="7" width="18" height="10" rx="2"/><path d="M7 7v4M11 7v3M15 7v4M18 7v3"/></svg>'][i]}</span></div><div class="metric-number">${s.mentioned}<small>条评价提及</small></div><div class="metric-track"><i class="negative" style="width:${s.negative/total*100}%"></i><i style="width:${(s.mentioned-s.negative)/total*100}%"></i></div><div class="metric-caption"><span class="negative-count">含负面 ${s.negative}</span><span>待确认 ${s.uncertain}</span></div></button>`;}).join('');
  $('#comment-type-filters').innerHTML=[['all','全部评价'],['experience','具体体验反馈'],['general_positive','一般好评'],['other','其他评价']].map(([value,label])=>`<button class="filter-button ${commentType===value?'active':''}" data-comment-type="${value}" aria-pressed="${commentType===value}">${label} ${value==='all'?state.records.length:state.records.filter(r=>commentKind(r)===value).length}</button>`).join('');
  $('#dimension-filter-row').hidden=commentType!=='experience';
  $('#dimension-filters').innerHTML=['全部',...DIMS].map(d=>`<button class="filter-button ${dimension===d?'active':''}" data-dim="${d}" aria-pressed="${dimension===d}">${d}</button>`).join('');
  const matches=filtered();
  const nextKey=JSON.stringify([commentType,dimension,query,status,state.records.map(r=>r.id)]);
  if(nextKey!==paginationKey){currentPage=1;paginationKey=nextKey;}
  const totalPages=Math.max(1,Math.ceil(matches.length/PAGE_SIZE));
  currentPage=Math.max(1,Math.min(currentPage,totalPages));
  const start=(currentPage-1)*PAGE_SIZE,visible=matches.slice(start,start+PAGE_SIZE);
  $('#pagination').innerHTML=`<span class="page-size">每页10条</span><button class="page-button" data-page="${currentPage-1}" ${currentPage===1?'disabled':''}>上一页</button>${Array.from({length:Math.min(totalPages,7)},(_,i)=>Math.min(Math.max(1,currentPage-3),Math.max(1,totalPages-6))+i).map(n=>`<button class="page-button ${n===currentPage?'active':''}" data-page="${n}" aria-label="第${n}页" ${n===currentPage?'aria-current="page"':''}>${n}</button>`).join('')}<button class="page-button" data-page="${currentPage+1}" ${currentPage===totalPages?'disabled':''}>下一页</button><span class="page-size">第${currentPage}/${totalPages}页</span>`;

  $('#review-list').innerHTML=visible.length?visible.map((r,i)=>`<tr class="${r.analyzed&&!r.reviewed&&needsReview(r)?'review-attention':''}"><td><span class="review-id">${start+i+1}</span></td><td><div class="review-text">${esc(r.text||((r.emojiAlts||[]).filter(Boolean).join(' ')?'〔仅表情〕':'〔空正文〕'))}</div></td><td><span class="comment-kind">${commentLabel(r)}</span>${new Set(r.feedback.map(f=>f.dim)).size>1?'<span class="composite-tag">复合体验</span>':''}<div class="tags">${r.feedback.length?[...new Set(r.feedback.map(f=>f.dim))].map(d=>tag(r.feedback.find(f=>f.dim===d&&f.attitude==='负向')||r.feedback.find(f=>f.dim===d))).join(''):`<span class="muted">${r.analyzed?(r.serviceNotes?.length?'含补充说明':''):'待分析'}</span>`}</div></td><td class="status-cell">${listReviewStatus(r)}</td><td><button class="row-open" data-open="${esc(r.id)}" aria-label="核对评价 ${esc(r.id)}">↗</button></td></tr>`).join(''):'<tr><td colspan="5"><div class="empty"><strong>这里暂时没有评价</strong>试试其他筛选条件，或导入一批评价。</div></td></tr>';
  $('#list-summary').textContent=`显示 ${matches.length?start+1:0}–${start+visible.length} / 筛选结果 ${matches.length} 条（本批 ${state.records.length} 条） · 已人工复核 ${state.records.filter(r=>r.reviewed).length} 条`;
  const scoped=statusScopeRecords();
  $('#review-shortcuts').innerHTML='<span class="filter-label">处理状态</span>'+[['all','全部',scoped.length],['organized','已整理',scoped.filter(r=>r.analyzed&&!r.reviewed&&!needsReview(r)).length],['unreviewed','待复核',scoped.filter(r=>r.analyzed&&!r.reviewed&&needsReview(r)).length],['reviewed','已人工复核',scoped.filter(r=>r.analyzed&&r.reviewed).length]].map(([value,label,count])=>`<button class="filter-button ${status===value?'active':''}" data-review-filter="${value}" aria-pressed="${status===value}">${label} ${count}</button>`).join('');
  $('#export-button').disabled=!state.records.length;
}
document.addEventListener('click',e=>{
  const pager=e.target.closest('[data-page]');if(pager&&!pager.disabled){currentPage=Number(pager.dataset.page);render();return;}
  const reviewFilter=e.target.closest('[data-review-filter]');if(reviewFilter){setReviewFilter(reviewFilter.dataset.reviewFilter);return;}
  const kind=e.target.closest('[data-comment-type]');if(kind){commentType=kind.dataset.commentType;dimension='全部';render();return;}
  const dim=e.target.closest('[data-dim]');if(dim){commentType='experience';dimension=dimension===dim.dataset.dim?'全部':dim.dataset.dim;render();}
  const open=e.target.closest('[data-open]');if(open)openReview(open.dataset.open);
  if(e.target.closest('[data-action="import"]'))openImport();
});
$('#search').addEventListener('input',e=>{query=e.target.value.trim();render();});
$('#status-filter').addEventListener('change',e=>{status=e.target.value;render();});

function openReview(id){selectedId=id;renderDetail();$('#review-dialog').showModal();}
function current(){return state.records.find(r=>r.id===selectedId);}
function renderDetail(){
  const r=current();if(!r)return;
  $('#detail-id').textContent=`${r.id} / ${state.mode==='demo'?'虚构演示':'已导入评价'}`;
  $('#detail-content').innerHTML=`<div class="detail-body"><section class="detail-section source-section" aria-labelledby="source-title"><div class="detail-section-heading"><div><span class="section-number">01</span><h3 id="source-title">客户原始评价</h3></div><span class="detail-state">${reviewState(r)}</span></div><div class="source-text" id="source-text">${esc(r.text)}${(r.emojiAlts||[]).some(Boolean)?`<div class="detail-kicker">表情补充：${esc(r.emojiAlts.filter(Boolean).join(' '))}</div>`:''}${r.followup?`<div class="followup"><span class="detail-kicker">关联追评</span><br>${esc(r.followup)}</div>`:''}</div>${r.source?`<p class="source-reference">来源：${esc(r.source)}</p>`:''}</section><section class="detail-section analysis-section" aria-labelledby="analysis-title"><div class="detail-section-heading"><div><span class="section-number">03</span><h3 id="analysis-title">评价分类与依据</h3><span class="tag">${commentLabel(r)}</span></div><button class="text-button" data-add>＋ 补充分析</button></div><div id="feedback-items">${r.feedback.length?r.feedback.map(f=>`<article class="feedback-item"><div class="feedback-top">${tag(f)}<span class="muted" style="font-size:11px">${esc(f.attitude)}</span><button class="text-button" data-edit="${esc(f.id)}">编辑</button></div><p class="feedback-summary">${esc(f.summary)}</p><blockquote class="evidence-text">“${esc(f.evidence)}”</blockquote><div id="editor-${esc(f.id)}"></div></article>`).join(''):`<div class="empty">${r.analyzed?(commentKind(r)==='general_positive'?'整体认可，未提及具体体验。':''):'尚未自动分析。可核对原文并手动补充反馈。'}</div>`}</div><div id="new-editor"></div>${serviceEvidence(r)}</section>${reviewPanel(r)}</div><div class="detail-actions"><span>${r.reviewed?'这条评价已人工复核':isSimplePraise(r)?'已整理 · 回复草稿就绪':needsReview(r)?'待人工核查':'已整理，可直接使用或按需抽查'}</span><button class="button button-primary" id="mark-reviewed">${r.reviewed?'撤销人工复核':needsReview(r)?'填写核查结论':'确认人工复核'} <span aria-hidden="true">✓</span></button></div>`;
  $('#edit-review-note').onclick=editReviewNote;localReviewHistory(r);bindReply(r);$('#mark-reviewed').hidden=isSimplePraise(r)&&!r.reviewed||!r.analyzed;
  $('#detail-content').querySelector('[data-add]').onclick=()=>showEditor(null);
  $('#detail-content').querySelectorAll('[data-edit]').forEach(b=>b.onclick=()=>showEditor(b.dataset.edit));
  $('#mark-reviewed').onclick=()=>{if(!r.reviewed&&needsReview(r)){editResolution();return;}const old=r.reviewed;r.reviewed=!old;state.changes.push({at:new Date().toISOString(),reviewId:r.id,action:'review',before:old,after:r.reviewed});const saved=persist();render();renderDetail();if(saved)toast(r.reviewed?'已确认复核':'已撤销复核');};
}
function showEditor(id){
  renderDetail();const r=current(),f=r.feedback.find(f=>f.id===id),target=id?document.getElementById(`editor-${id}`):$('#new-editor');
  target.innerHTML=`<form class="feedback-editor"><div class="edit-grid"><label>维度<select name="dim" class="edit-input">${DIMS.map(d=>`<option ${d===f?.dim?'selected':''}>${d}</option>`).join('')}</select></label><label>态度<select name="attitude" class="edit-input">${ATTITUDES.map(a=>`<option ${a===f?.attitude?'selected':''}>${a}</option>`).join('')}</select></label><label class="wide">反馈摘要<textarea class="edit-input" name="summary" required maxlength="500">${esc(f?.summary||'')}</textarea></label><label class="wide">原文依据<textarea class="edit-input" name="evidence" required maxlength="3000" placeholder="从初评或追评中复制一段完整原句">${esc(f?.evidence||'')}</textarea></label></div><p class="inline-error" role="alert"></p><div class="edit-actions">${id?'<button type="button" class="text-button" data-delete>删除此反馈</button>':''}<button type="button" class="text-button" data-cancel>取消</button><button class="button button-primary button-small" type="submit">保存修改</button></div></form>`;
  const form=target.querySelector('form');form.querySelector('[data-cancel]').onclick=renderDetail;
  if(id)form.querySelector('[data-delete]').onclick=()=>{const before=structuredClone(f);r.feedback=r.feedback.filter(item=>item.id!==id);r.reviewed=false;r.corrected=true;state.changes.push({at:new Date().toISOString(),reviewId:r.id,action:'delete',before});const saved=persist();render();renderDetail();if(saved)toast('已删除反馈并更新统计');};
  form.onsubmit=e=>{e.preventDefault();const fd=new FormData(form),evidence=fd.get('evidence').trim(),summary=fd.get('summary').trim();if(!summary||!evidence){form.querySelector('[role="alert"]').textContent='摘要和原文依据均不能为空。';return;}if(!r.text.includes(evidence)&&!(r.followup||'').includes(evidence)){form.querySelector('[role="alert"]').textContent='这段依据未在初评或追评中找到，请逐字复制原句。';return;}const next={id:id||crypto.randomUUID(),dim:fd.get('dim'),attitude:fd.get('attitude'),summary,evidence};const before=f?structuredClone(f):null;if(f)Object.assign(f,next);else r.feedback.push(next);r.reviewed=false;r.corrected=true;state.changes.push({at:new Date().toISOString(),reviewId:r.id,action:f?'edit':'add',before,after:structuredClone(next)});const saved=persist();render();renderDetail();if(saved)toast('已保存，四维统计已更新');};
  form.querySelector('[name="summary"]').focus();
}

function parseCSV(text){
  text=text.replace(/^\uFEFF/,'');const rows=[];let row=[],cell='',quoted=false,closed=false;
  for(let i=0;i<text.length;i++){const ch=text[i];if(quoted){if(ch==='"'){if(text[i+1]==='"'){cell+='"';i++;}else{quoted=false;closed=true;}}else cell+=ch;continue;}if(ch==='"'){if(cell||closed)throw Error('CSV 引号格式有误，请用表格软件重新保存。');quoted=true;continue;}if(ch===','){row.push(cell);cell='';closed=false;continue;}if(ch==='\n'||ch==='\r'){if(ch==='\r'&&text[i+1]==='\n')i++;row.push(cell);if(row.some(v=>v.trim()))rows.push(row);row=[];cell='';closed=false;continue;}if(closed)throw Error('CSV 引号后存在多余字符。');cell+=ch;}
  if(quoted)throw Error('CSV 中有未闭合的引号。');if(cell||row.length){row.push(cell);if(row.some(v=>v.trim()))rows.push(row);}return rows;
}
function recordsFromCSV(text){
  const rows=parseCSV(text);if(rows.length<2)throw Error('文件中没有可用评价，请保留表头并填写正文。');
  const aliases={'初评正文':'main_text','评价正文':'main_text','评价编号':'review_id','追评':'followup_text','来源':'source_ref'};
  const headers=rows.shift().map(s=>aliases[s.trim()]||s.trim());if(new Set(headers).size!==headers.length)throw Error('表头存在重复列名，请检查。');if(!headers.includes('main_text'))throw Error('缺少正文列：请使用 main_text、初评正文或评价正文。');if(rows.length>500)throw Error('当前原型最多预览500条，请拆分文件。');
  const seen=new Set(),issues=[];const records=rows.map((row,i)=>{if(row.length!==headers.length)issues.push(`第 ${i+2} 行：列数与表头不一致`);const obj=Object.fromEntries(headers.map((h,j)=>[h,row[j]??'']));const id=obj.review_id?.trim()||`IM-${String(i+1).padStart(3,'0')}`;if(!obj.main_text?.trim())issues.push(`第 ${i+2} 行：正文为空`);if(seen.has(id))issues.push(`第 ${i+2} 行：编号 ${id} 重复`);seen.add(id);return{id,text:obj.main_text||'',followup:obj.followup_text||'',source:obj.source_ref||'',flavor:obj.flavor||'',specification:obj.specification||'',rating:obj.platform_rating||'',feedback:[],reviewed:false,analyzed:false};});
  if(issues.length)throw Error(issues.slice(0,6).join('\n')+(issues.length>6?'\n还有其他错误，请检查全部记录。':''));return records;
}
function openImport(){pendingImport=null;$('#batch-name').value='';$('#file-label').textContent='选择 CSV 文件，或拖到这里';$('#csv-file').value='';$('#import-error').textContent='';$('#import-preview').hidden=true;$('#confirm-import').disabled=true;$('#import-dialog').showModal();}
async function readFile(file){pendingImport=null;$('#confirm-import').disabled=true;$('#import-preview').hidden=true;$('#import-error').textContent='';try{if(!file) return;if(!file.name.toLowerCase().endsWith('.csv'))throw Error('请选择 CSV 文件。');if(file.size>2*1024*1024)throw Error('文件超过2MB，请拆分后导入。');const text=await file.text();if(text.includes('\ufffd'))throw Error('文字编码无法识别，请另存为 CSV UTF-8 后重试。');const records=recordsFromCSV(text);pendingImport={name:file.name.replace(/\.csv$/i,''),records};$('#file-label').textContent=file.name;$('#import-preview').textContent=`已读取 ${records.length} 条评价。导入后可预览原文，尚未生成分析结果。`;$('#import-preview').hidden=false;$('#confirm-import').disabled=false;if(!$('#batch-name').value)$('#batch-name').value=pendingImport.name;}catch(e){$('#import-error').textContent=e.message;}}
$('#csv-file').addEventListener('change',e=>readFile(e.target.files[0]));
$('#upload-zone').addEventListener('dragover',e=>{e.preventDefault();$('#upload-zone').classList.add('dragging');});
$('#upload-zone').addEventListener('dragleave',()=>$('#upload-zone').classList.remove('dragging'));
$('#upload-zone').addEventListener('drop',e=>{e.preventDefault();$('#upload-zone').classList.remove('dragging');readFile(e.dataTransfer.files[0]);});
$('#confirm-import').onclick=async()=>{if(!pendingImport)return;if(!await askReplace())return;state={version:1,mode:'import',name:$('#batch-name').value.trim()||pendingImport.name,records:pendingImport.records,changes:[]};dimension='全部';query='';status='all';$('#search').value='';$('#status-filter').value='all';persist();render();$('#import-dialog').close();toast('原文已导入，尚未自动分析');};
function download(content,name,type){const url=URL.createObjectURL(new Blob([content],{type})),a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
function csvCell(value){let s=String(value??'');if(/^[\s]*[=+\-@\t\r]/.test(s))s="'"+s;return '"'+s.replace(/"/g,'""')+'"';}
$('#download-template').onclick=()=>download('\uFEFFmain_text,review_id,followup_text,source_ref,flavor,specification,platform_rating\r\n','差评导入模板.csv','text/csv;charset=utf-8');
$('#export-button').onclick=()=>{const rows=[['评价编号','原文','追评','来源','维度','态度','摘要','原文依据','复核状态','数据类型']];state.records.forEach(r=>(r.feedback.length?r.feedback:[null]).forEach(f=>rows.push([r.id,r.text,r.followup,r.source,f?.dim||'',f?.attitude||'',f?.summary||(r.analyzed?'未提取四维相关反馈':'未分析'),f?.evidence||'',reviewState(r),state.mode==='demo'?'虚构演示':'用户导入'])));download('\uFEFF'+rows.map(row=>row.map(csvCell).join(',')).join('\r\n'),`${state.mode==='demo'?'虚构演示-':''}评价复盘.csv`,'text/csv;charset=utf-8');toast('已导出全部评价及当前复核状态');};
render();

