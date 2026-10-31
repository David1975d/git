'use strict';
// v0.2 same-origin integration. Preserve the existing demo and visual system.
let liveTask = null, csrfToken = '', onlineReady = false, operationBusy = false, pollHandle = null;
const originalRender = render, originalStats = stats, originalDetail = renderDetail;
const originalFiltered = filtered;
const originalEditor = showEditor, originalReadFile = readFile, originalOpenImport = openImport;
const activeTask = () => state.mode === 'live' && ['queued','running'].includes(liveTask?.status);
const isLive = () => state.mode === 'live';
const taskLabels = {queued:'排队中',running:'分析中',completed:'分析完成',partial_failed:'部分未完成',failed:'分析失败',interrupted:'已中断'};
const datasetLabel = kind => kind==='assistant_review'?'辅助整理材料':kind==='synthetic_development'?'演示数据':'用户提供评论';
const asUiFeedback = f => ({id:f.feedback_id,dim:f.dimension,attitude:f.attitude,summary:f.summary,evidence:f.evidence,stage:f.stage,needs_review:f.needs_review,review_reason:f.review_reason});
const auditCache = new Map();
const finalReplySaved = r => r.reply?.source==='人工编辑';
const bulkPraiseButton=document.createElement('button');
bulkPraiseButton.id='bulk-organized-reply';bulkPraiseButton.type='button';
bulkPraiseButton.className='button button-primary button-small';
bulkPraiseButton.title='保存并确认当前筛选范围内所有分页的已整理评价回复；已有文本保留。';
bulkPraiseButton.hidden=true;$('#review-shortcuts').after(bulkPraiseButton);
bulkPraiseButton.onclick=async()=>{
  if(operationBusy||activeTask()||!isLive())return;
  const reviewIds=statusScopeRecords().filter(r=>r.analyzed&&!r.reviewed&&!needsReview(r)).map(r=>r.id);
  if(!reviewIds.length)return;
  operationBusy=true;render();
  try {
    const result=await jsonApi(taskPath()+'/replies/adopt-organized',{method:'POST',body:JSON.stringify({version:liveTask.version,review_ids:reviewIds})});
    await refreshTask();await refreshHistory();
    const a=result.adoption;
    toast(a.confirmed?`已确认${a.confirmed}条回复${a.pending?`，${a.pending}条待核查`:''}`:
      a.pending?'部分回复依据已变化，请先核查':a.existing?'回复均已确认':'当前没有可确认的回复');
  } catch(error){showError(error);}
  finally{operationBusy=false;render();}
};
const startButton = document.createElement('button');
startButton.id = 'start-analysis'; startButton.className = 'button button-primary';
startButton.hidden = true; startButton.textContent = '开始分析';
$('#export-button').before(startButton);
const refreshButton = document.createElement('button');
refreshButton.id = 'refresh-task'; refreshButton.className = 'text-button';
refreshButton.textContent = '刷新进度'; refreshButton.hidden = true;
$('#list-summary').after(refreshButton);
const exportAudit = document.createElement('button');
exportAudit.id = 'export-audit'; exportAudit.className = 'button button-outline button-small';
exportAudit.textContent = '导出完整记录'; exportAudit.hidden = true;
$('.batch-actions').append(exportAudit);
$('.import-note').textContent = '支持 CSV，每批最多100条。导入仅预览；开始分析后计费。';
$('#batch-name').previousElementSibling.textContent = '商品名称';
$('#batch-name').maxLength = 60;
$('#batch-name').placeholder = '例如：康比特分离乳清蛋白粉';
$('label[for="batch-name"]').insertAdjacentHTML('beforebegin', '<label class="field-label live-field" for="batch-platform">平台</label><select id="batch-platform" class="input"><option>京东</option><option>淘宝／天猫</option><option>抖音</option><option>iHerb</option><option>其他平台</option></select>');
$('#batch-name').insertAdjacentHTML('afterend', '<label class="field-label live-field" for="product-scope">主商品范围</label><input id="product-scope" name="product_scope" autocomplete="off" class="input" maxlength="1000" placeholder="例如：某品牌乳清蛋白某系列，不含赠品…"><label class="field-label live-field" for="dataset-kind">材料类型</label><select id="dataset-kind" name="dataset_kind" class="input"><option value="user_supplied">用户提供的评论</option><option value="synthetic_development">示例评论</option></select>');
const appStatus = document.createElement('p');
appStatus.id = 'app-status'; appStatus.className = 'batch-description runtime-status';
appStatus.setAttribute('role','status'); $('.metrics').before(appStatus);

async function api(path, options = {}) {
  const response = await fetch(path, {credentials:'same-origin', ...options,
    headers:{...(options.body ? {'Content-Type':'application/json'} : {}),
      ...(options.method && options.method!=='GET' ? {'X-CSRF-Token':csrfToken} : {}), ...options.headers}});
  if (!response.ok) {
    let data; try { data = await response.json(); } catch {}
    const error = new Error(data?.error?.message || `请求失败（${response.status}）`);
    error.code = data?.error?.code; error.status = response.status;
    error.details = data?.error?.details; throw error;
  }
  return response;
}
async function jsonApi(path, options) { return (await api(path, options)).json(); }
const taskPath = () => '/api/tasks/' + encodeURIComponent(liveTask.task_id);
function uiRecord(r) {
  return {id:r.review_id,text:r.main_text,followup:r.followup_text,source:r.source_ref,
    flavor:r.flavor,specification:r.specification,rating:r.platform_rating,
    execution:r.execution,attempts:r.attempts,error:r.error,reviewed:r.reviewed,reviewResolution:r.review_resolution||'',reply:r.reply,
    commentType:r.comment_type||'other',serviceNotes:r.service_notes||[],reviewNote:r.review_note||'',reviewRequired:r.review_required||false,scopeNotes:r.scope_notes||[],
    emojiAlts:r.emoji_alts||[],assessmentSummary:r.assessment_summary||'',
    baseline:(r.model_feedback||r.initial_feedback)?.map(asUiFeedback),
    analyzed:r.execution==='completed',feedback:(r.feedback||[]).map(asUiFeedback)};
}
function serverFeedback(f) {
  return {feedback_id:f.id,dimension:f.dim,attitude:f.attitude,summary:f.summary,evidence:f.evidence,
    stage:f.stage,needs_review:Boolean(f.needs_review),review_reason:f.review_reason||''};
}
stats = function() {
  if (!isLive() || !liveTask?.statistics) return originalStats();
  return DIMS.map(dim=>({dim,mentioned:liveTask.statistics.dimensions[dim].mentioned,
    negative:liveTask.statistics.dimensions[dim].negative,uncertain:liveTask.statistics.dimensions[dim].needs_review}));
};
filtered = function() {
  if(!isLive()) return originalFiltered();
  return statusScopeRecords().filter(statusMatches);
};
render = function() {
  originalRender();
  const live = isLive(), active = activeTask();
  const organized=statusScopeRecords().filter(r=>r.analyzed&&!r.reviewed&&!needsReview(r));
  bulkPraiseButton.hidden=!live||status!=='organized'||!organized.length;
  bulkPraiseButton.textContent='一键回复';
  bulkPraiseButton.disabled=active||operationBusy;
  startButton.hidden = !(state.mode==='import' && state.importId) && !(live && ['partial_failed','failed','interrupted'].includes(liveTask?.status));
  startButton.textContent = live ? '继续未完成分析' : '开始分析';
  startButton.disabled = operationBusy || active || !onlineReady;
  refreshButton.hidden = !live; refreshButton.disabled = operationBusy;
  exportAudit.hidden = !live; exportAudit.disabled = operationBusy;
  document.querySelectorAll('[data-action="import"]').forEach(b=>b.disabled = active || operationBusy || !onlineReady);
  $('#export-button').disabled = !state.records.length || operationBusy;
  $('#task-history').disabled = active || operationBusy;
  $('#manage-history').disabled = !onlineReady || active || operationBusy;
  if (live) {
    $('#batch-type').textContent = `${datasetLabel(liveTask.dataset_kind)} · ${liveTask.dataset_kind==='assistant_review'?'初步复盘完成':taskLabels[liveTask.status]}`;
    $('#save-state').textContent = '已保存';
    appStatus.textContent = liveTask.billing_uncertain ? '部分中断调用可能已计费，继续前请核查。已完成结果不会重跑。' :
      active ? '正在分析。刷新页面会恢复本次任务，不会重新提交。' : ['partial_failed','failed','interrupted'].includes(liveTask.status) ? '有评价尚未完成分析，可继续处理或导出已有结果。' : '';
    document.querySelectorAll('[data-open]').forEach(button=>{
      const record = state.records.find(r=>r.id===button.dataset.open);
      if (!record.analyzed) {
        const cells = button.closest('tr').querySelectorAll('td');
        cells[3].textContent = ({pending:'待分析',running:'分析中',failed:'分析失败',interrupted:'已中断'})[record.execution]||'待分析';
      }
    });
  } else {
    appStatus.textContent = !onlineReady ? '正在连接本机服务…' : state.mode==='demo' ? '' : '原文已导入。请核对商品范围后开始分析。';
  }
  appStatus.hidden = !appStatus.textContent;
};
function schedulePoll() {
  clearTimeout(pollHandle);
  if (activeTask()) pollHandle = setTimeout(()=>refreshTask().catch(showError),1200);
}
async function refreshTask() {
  if (!liveTask || !isLive()) return;
  const task = await jsonApi(taskPath());
  const data = await jsonApi(taskPath()+'/reviews');
  for (let offset=data.records.length;offset<data.total;) {
    const page=await jsonApi(taskPath()+'/reviews?offset='+offset+'&limit=20');
    if(page.version!==data.version){pollHandle=setTimeout(()=>refreshTask().catch(showError),400);return;}
    if(!page.records.length)throw new Error('评论分页读取未完成，请刷新进度。');
    data.records.push(...page.records);offset=data.records.length;
  }
  // Do not combine result rows and statistics read from different task versions.
  if (task.version!==data.version) { pollHandle=setTimeout(()=>refreshTask().catch(showError),400); return; }
  liveTask = task;
  state = {version:1,mode:'live',name:task.name,records:data.records.map(uiRecord),changes:[]};
  const currentUrl=new URL(location.href);currentUrl.searchParams.set('task',task.task_id);history.replaceState(null,'',currentUrl);
  render();
  if ($('#review-dialog').open && !$('#detail-content .feedback-editor, #resolution-editor form, #reply-form[data-dirty]')) renderDetail();
  schedulePoll();
}
function showError(error) {
  if(error.code==='version_conflict') error.message='结果已更新，请取消编辑并重新核对。当前草稿仍保留。';
  appStatus.hidden = false;
  appStatus.textContent = error.message;
  toast(error.message);
  if (error.code==='version_conflict') refreshTask().catch(e=>{appStatus.textContent=e.message;});
  // Polling pauses on transport errors; user can explicitly refresh without creating a new task.
}
async function commitReview(record, items, reviewed, reason, expectedVersion=liveTask.version, reviewNote=record.reviewNote||'', extra={}) {
  if (operationBusy || activeTask()) return false;
  operationBusy=true; render();
  try {
    await jsonApi(taskPath()+'/reviews/'+encodeURIComponent(record.id), {method:'PATCH',body:JSON.stringify({
      version:expectedVersion,feedback:items.map(serverFeedback),reviewed,change_reason:reason,review_note:reviewNote,...extra})});
    await refreshTask(); await refreshHistory().catch(()=>{}); renderDetail(); return true;
  } catch(error) { showError(error); const inline=$('#resolution-editor .inline-error')||$('#detail-content .feedback-editor .inline-error')||$('#reply-error');if(inline)inline.textContent=error.message; return false; }
  finally {
    operationBusy=false; render();
    if($('#review-dialog').open && !$('#detail-content .feedback-editor, #resolution-editor form, #reply-form[data-dirty]')) renderDetail();
  }
}
renderDetail = function() {
  originalDetail();
  if (!isLive()) {
    if(state.mode==='import'&&state.importId) $('#detail-content').querySelectorAll('[data-add],[data-edit],#mark-reviewed').forEach(b=>b.disabled=true);
    return;
  }
  const record = current(); if (!record) return;
  $('#detail-id').textContent = `${record.id} / ${datasetLabel(liveTask.dataset_kind)}`;
  const disabled = activeTask() || !record.analyzed || operationBusy;
  $('#detail-content').querySelectorAll('[data-add],[data-edit],#mark-reviewed,#edit-resolution,#reply-form button,#reply-text').forEach(b=>b.disabled=disabled||b.id==='confirm-reply'&&(!replyPlan(record).can_copy||replyPlan(record).confirmed));
  $('#edit-review-note').disabled=disabled;
  if(replyPlan(record).confirmed)$('.detail-actions > span').textContent='回复已确认';
  else if(finalReplySaved(record)&&replyPlan(record).can_copy&&!replyPlan(record).stale)$('.detail-actions > span').textContent='回复已保存';
  showReviewHistory(record).catch(()=>{if(current()?.id===record.id)$('#review-history').textContent='复核记录暂未读取成功，请重新打开详情。';});
  if (!record.analyzed) $('#feedback-items').textContent = record.error?.message || '该评价尚无有效分析结果，暂不能复核。';
  $('#mark-reviewed').onclick = async()=>{if(!record.reviewed&&needsReview(record)){editResolution();return;}if(await commitReview(record,record.feedback,!record.reviewed,'人工核对状态调整')) toast('复核状态已保存');};
  $('#detail-content').querySelectorAll('[data-edit]').forEach(b=>b.onclick=()=>showEditor(b.dataset.edit));
  $('#detail-content').querySelector('[data-add]').onclick=()=>showEditor(null);
  record.feedback.forEach(f=>{
    const article = document.getElementById('editor-'+f.id)?.closest('article');
    if (article) {
      const note = document.createElement('div'); note.className='detail-kicker';
      note.textContent = `${f.stage==='followup'?'追评依据':'初评依据'}`;
      article.append(note);
    }
  });
};
saveReviewNote = async function(record,value,expectedVersion) {
  if(!isLive()){
    const before=record.reviewNote||'';record.reviewNote=value;record.reviewed=false;
    state.changes.push({at:new Date().toISOString(),reviewId:record.id,action:'note',before,after:value});
    if(persist()){render();renderDetail();toast('复核说明已保存，等待确认复核');}return;
  }
  if(await commitReview(record,record.feedback,false,'填写/修改复核说明',expectedVersion,value))toast('复核说明已保存，等待确认复核');
};
const originalSaveResolution=saveResolution,originalSaveReply=saveReply;
saveResolution=async function(record,value,version){
  if(!isLive())return originalSaveResolution(record,value);
  if(await commitReview(record,record.feedback,true,'保存核查答复并确认复核',version,record.reviewNote||'',{review_resolution:value}))toast('核查答复已保存，回复草稿已准备');
};
saveReply=async function(record,text,version,confirmed=false){
  if(!isLive())return originalSaveReply(record,text,version,confirmed);
  if(!text){$('#reply-error').textContent='请填写回复内容。';return;}
  if(await commitReview(record,record.feedback,record.reviewed,confirmed?'确认回复':'保存回复',version,record.reviewNote||'',{reply_text:text,reply_confirmed:confirmed}))toast(confirmed?'回复已确认':'已保存');
};
async function showReviewHistory(record) {
  const identifier=liveTask.task_id, version=liveTask.version, key=identifier+':'+version;
  let data=auditCache.get(key);
  if(!data){data=await jsonApi('/api/tasks/'+encodeURIComponent(identifier)+'/audit');auditCache.set(key,data);}
  if(!isLive()||liveTask.task_id!==identifier||current()?.id!==record.id)return;
  const events=data.events.filter(e=>e.kind==='human_correction'&&e.payload.review_id===record.id);
  $('#review-history').innerHTML=events.length?events.slice().reverse().map(e=>{
    const change=e.payload, after=change.after||{}, labels=[];
    if(JSON.stringify(change.before?.feedback)!==JSON.stringify(after.feedback))labels.push('修正/补充反馈');
    if((change.before?.review_note||'')!==(after.review_note||''))labels.push('保存复核说明');
    if(change.before?.reviewed!==after.reviewed)labels.push(after.reviewed?'确认复核':'撤销复核');
    if((change.before?.review_resolution||'')!==(after.review_resolution||''))labels.push('保存核查答复');
    if((change.before?.reply_text||'')!==(after.reply_text||''))labels.push('保存回复草稿');
    if(Boolean(change.before?.reply_confirmed)!==Boolean(after.reply_confirmed))labels.push(after.reply_confirmed?'确认回复':'撤销回复确认');
    const feedbackChange=labels.includes('修正/补充反馈')?`<details class="detail-kicker"><summary>查看反馈修改内容</summary><p>修改前：${esc((change.before?.feedback||[]).map(f=>f.summary).join('；')||'无四维反馈')}</p><p>修改后：${esc((after.feedback||[]).map(f=>f.summary).join('；')||'无四维反馈')}</p></details>`:'';
    return `<p class="detail-kicker">${esc(new Date(e.created_at*1000).toLocaleString())} · ${esc(labels.join('、')||'保存')}<br>${esc(change.reason)}${after.review_note?'<br>说明：'+esc(after.review_note):''}${after.review_resolution?'<br>核查答复：'+esc(after.review_resolution):''}${after.reply_text?'<br>回复草稿：'+esc(after.reply_text):''}</p>${feedbackChange}`;
  }).join(''):'<p class="muted">尚无人工复核记录</p>';
}

async function refreshHistory() {
  const data=await jsonApi('/api/tasks');
  $('#task-history').innerHTML='<option value="">选择已保存批次</option>'+data.tasks.map(t=>`<option value="${esc(t.task_id)}" ${t.task_id===liveTask?.task_id?'selected':''}>${esc(t.name)}</option>`).join('');
}
$('#task-history').onchange=async event=>{
  const id=event.target.value;if(!id||activeTask()||operationBusy)return;
  try{
    if($('#review-dialog').open)$('#review-dialog').close();
    liveTask=await jsonApi('/api/tasks/'+encodeURIComponent(id));state.mode='live';commentType='all';dimension='全部';query='';status='all';$('#search').value='';$('#status-filter').value='all';
    const url=new URL(location.href);url.searchParams.set('task',id);history.replaceState(null,'',url);
    await refreshTask();await refreshHistory();
  }catch(error){showError(error);}
};
showEditor = function(id) {
  if (!isLive()) return originalEditor(id);
  if (activeTask() || !current()?.analyzed || operationBusy) return;
  renderDetail();
  const record=current(), old=record.feedback.find(f=>f.id===id), editorVersion=liveTask.version;
  const target=id?document.getElementById('editor-'+id):$('#new-editor');
  target.innerHTML=`<form class="feedback-editor"><div class="edit-grid"><label>维度<select name="dim" class="edit-input">${DIMS.map(d=>`<option ${d===old?.dim?'selected':''}>${d}</option>`).join('')}</select></label><label>态度<select name="attitude" class="edit-input">${ATTITUDES.map(a=>`<option ${a===old?.attitude?'selected':''}>${a}</option>`).join('')}</select></label><label>引用位置<select name="stage" class="edit-input"><option value="main">初评</option><option value="followup" ${old?.stage==='followup'?'selected':''}>追评</option></select></label><label>需要进一步确认<input type="checkbox" name="needs_review" ${old?.needs_review?'checked':''}></label><label class="wide">反馈摘要<textarea name="summary" class="edit-input" required maxlength="500">${esc(old?.summary||'')}</textarea></label><label class="wide">原文依据<textarea name="evidence" class="edit-input" required maxlength="10000">${esc(old?.evidence||'')}</textarea></label><label class="wide">待确认原因<textarea name="review_reason" class="edit-input" maxlength="500">${esc(old?.review_reason||'')}</textarea></label></div><p class="inline-error" role="alert"></p><div class="edit-actions">${old?'<button type="button" class="text-button" data-delete>删除此反馈</button>':''}<button type="button" class="text-button" data-cancel>取消</button><button class="button button-primary button-small" type="submit">保存修改</button></div></form>`;
  const form=target.querySelector('form'); form.querySelector('[data-cancel]').onclick=renderDetail;
  $('#mark-reviewed').disabled=true;
  if(old) form.querySelector('[data-delete]').onclick=async()=>{if(await commitReview(record,record.feedback.filter(f=>f.id!==id),false,'人工移除反馈',editorVersion)) toast('已保存并重算统计');};
  form.onsubmit=async event=>{
    event.preventDefault(); const data=new FormData(form);
    const next={id:id||crypto.randomUUID(),dim:data.get('dim'),attitude:data.get('attitude'),
      summary:data.get('summary').trim(),evidence:data.get('evidence').trim(),stage:data.get('stage'),
      needs_review:data.has('needs_review'),review_reason:data.get('review_reason').trim()};
    const original=next.stage==='main'?record.text:record.followup;
    if (!next.evidence || !original.includes(next.evidence)) {form.querySelector('[role="alert"]').textContent='依据未在所选初评或追评中找到，请逐字引用。';return;}
    if (next.needs_review&&!next.review_reason) {form.querySelector('[role="alert"]').textContent='请填写待确认原因。';return;}
    const items=old?record.feedback.map(f=>f.id===id?next:f):[...record.feedback,next];
    if(await commitReview(record,items,false,old?'人工调整反馈':'人工补充反馈',editorVersion)) toast('修改已保存，统计已更新');
  };
};
openImport = function() {
  if (!onlineReady || activeTask()) return;
  originalOpenImport(); $('#product-scope').value=''; $('#dataset-kind').value='user_supplied';
};
readFile = async function(file) {
  pendingImport=null; $('#confirm-import').disabled=true; $('#import-preview').hidden=true; $('#import-error').textContent='';
  try {
    if (!file) return;
    if (!file.name.toLowerCase().endsWith('.csv')) throw new Error('请选择 CSV 文件。');
    if (file.size>2*1024*1024) throw new Error('文件超过2MB，请拆分后导入。');
    const csv_text=await file.text(); if(csv_text.includes('\ufffd')) throw new Error('请将文件另存为 UTF-8 CSV。');
    const data=await jsonApi('/api/imports/preview',{method:'POST',body:JSON.stringify({csv_text})});
    pendingImport={name:file.name.replace(/\.csv$/i,''),importId:data.import_id,records:data.preview.map(r=>uiRecord({...r,feedback:[],execution:'pending',reviewed:false,attempts:0}))};
    $('#file-label').textContent=file.name; $('#import-preview').textContent=`已校验 ${data.count} 条评价。预览未调用模型。`;
    $('#import-preview').hidden=false; $('#confirm-import').disabled=false;
  } catch(error) {$('#import-error').textContent=error.message+(error.details?.length?'\n'+error.details.map(d=>`第${d.line}行：${d.message}`).join('\n'):'');}
};
$('#confirm-import').onclick=async()=>{
  if(!pendingImport) return;
  if(!$('#batch-name').value.trim()) {$('#import-error').textContent='请填写商品名称。';return;}
  if(!$('#product-scope').value.trim()) {$('#import-error').textContent='请填写本批主商品范围，帮助区分赠品和其他产品。';return;}
  if(!await askReplace()) return;
  const productName=$('#batch-name').value.trim(), platform=$('#batch-platform').value;
  const batchDate=new Intl.DateTimeFormat('sv-SE',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date());
  state={version:1,mode:'import',name:`${platform}｜${productName}｜${batchDate}｜${pendingImport.records.length}条`,platform,productName,records:pendingImport.records,
    importId:pendingImport.importId,requestId:crypto.randomUUID(),productScope:$('#product-scope').value.trim(),datasetKind:$('#dataset-kind').value,changes:[]};
  clearTimeout(pollHandle); commentType='all'; dimension='全部'; query=''; status='all'; $('#search').value=''; $('#status-filter').value='all';
  persist(); render(); $('#import-dialog').close(); toast('原文已预览，点击开始分析后才调用模型');
};
startButton.onclick=async()=>{
  if(operationBusy||activeTask()) return;
  operationBusy=true; render();
  try {
    if(isLive()) liveTask=await jsonApi(taskPath()+'/resume',{method:'POST',body:JSON.stringify({version:liveTask.version})});
    else liveTask=await jsonApi('/api/tasks',{method:'POST',body:JSON.stringify({import_id:state.importId,request_id:state.requestId,
      name:state.name,product_scope:state.productScope,dataset_kind:state.datasetKind,
      platform:state.platform||'',product_name:state.productName||''})});
    state.mode='live'; await refreshTask(); schedulePoll();
  } catch(error) {showError(error);}
  finally {operationBusy=false;render();}
};
refreshButton.onclick=()=>refreshTask().catch(showError);
async function exportRemote(format) {
  try {
    const response=await api(taskPath()+`/export?format=${format}&version=${liveTask.version}`);
    const fileName=liveTask.name.replace(/[\\/:*?"<>|]/g,'-');
    download(await response.text(),`${fileName}.${format}`,format==='csv'?'text/csv;charset=utf-8':'application/json');
    toast('已导出服务端保存的结果');
  } catch(error) {showError(error);}
}
const demoExport=$('#export-button').onclick;
$('#export-button').onclick=()=>isLive()?exportRemote('csv'):demoExport();
exportAudit.onclick=()=>exportRemote('json');
let historyDeleted=false,historyRows=[];
function confirmTaskDelete(task){
  return new Promise(resolve=>{
    const dialog=$('#delete-task-dialog');let accepted=false;
    $('#delete-task-name').textContent=task.name;
    $('#confirm-task-delete').onclick=()=>{accepted=true;dialog.close();};
    $('#cancel-task-delete').onclick=()=>dialog.close();
    dialog.addEventListener('close',()=>resolve(accepted),{once:true});dialog.showModal();
  });
}
async function loadHistoryManager(){
  $('#history-error').textContent='';$('#history-items').textContent='正在读取…';
  $('#history-active').classList.toggle('active',!historyDeleted);$('#history-trash').classList.toggle('active',historyDeleted);
  const data=await jsonApi('/api/tasks'+(historyDeleted?'?deleted=true':''));historyRows=data.tasks;
  $('#history-items').innerHTML=historyRows.length?historyRows.map(t=>`<article class="history-row"><div><strong>${esc(t.name)}</strong><p>${t.statistics.total_reviews} 条评价 · ${t.statistics.reviewed_reviews} 条已人工复核${t.deleted_at?' · 已移入回收站':''}</p></div><div class="history-row-actions">${historyDeleted?`<button class="text-button" data-task-restore="${esc(t.task_id)}">恢复</button>`:`<button class="text-button" data-task-open="${esc(t.task_id)}">打开</button><button class="text-button" data-task-delete="${esc(t.task_id)}" ${['queued','running'].includes(t.status)?'disabled':''}>移至回收站</button>`}</div></article>`).join(''):'<p class="muted">'+(historyDeleted?'回收站为空':'暂无历史批次')+'</p>';
}
$('#manage-history').onclick=async()=>{if(!onlineReady||operationBusy||activeTask())return;historyDeleted=false;$('#history-dialog').showModal();try{await loadHistoryManager();}catch(e){$('#history-error').textContent=e.message;}};
$('#history-active').onclick=async()=>{historyDeleted=false;try{await loadHistoryManager();}catch(e){$('#history-error').textContent=e.message;}};
$('#history-trash').onclick=async()=>{historyDeleted=true;try{await loadHistoryManager();}catch(e){$('#history-error').textContent=e.message;}};
$('#history-items').onclick=async event=>{
  if(operationBusy||activeTask())return;
  const open=event.target.closest('[data-task-open]');
  if(open){$('#history-dialog').close();await $('#task-history').onchange({target:{value:open.dataset.taskOpen}});return;}
  const remove=event.target.closest('[data-task-delete]'),restore=event.target.closest('[data-task-restore]');
  const id=remove?.dataset.taskDelete||restore?.dataset.taskRestore;if(!id)return;
  const task=historyRows.find(t=>t.task_id===id);if(!task)return;
  if(remove&&!await confirmTaskDelete(task))return;
  operationBusy=true;render();
  $('#history-items').querySelectorAll('button').forEach(b=>b.disabled=true);
  try{
    await jsonApi('/api/tasks/'+encodeURIComponent(id)+(restore?'/restore':''),{method:restore?'POST':'DELETE',body:JSON.stringify({version:task.version})});
    if(remove&&liveTask?.task_id===id){
      if($('#review-dialog').open)$('#review-dialog').close();
      const current=await jsonApi('/api/tasks/current');clearTimeout(pollHandle);
      commentType='all';dimension='全部';query='';status='all';$('#search').value='';$('#status-filter').value='all';
      if(current.task){liveTask=current.task;state.mode='live';await refreshTask();}
      else{liveTask=null;state=demo();history.replaceState(null,'',location.pathname);render();}
    }
    await refreshHistory();await loadHistoryManager();toast(restore?'批次已恢复':'已移入回收站，可恢复');
  }catch(error){$('#history-error').textContent=error.message;try{await loadHistoryManager();$('#history-error').textContent=error.message;}catch{}}
  finally{operationBusy=false;render();}
};
async function initializeLive() {
  try {
    const health=await jsonApi('/api/health');
    if(!health.workspace_connected) throw new Error('当前服务版本较旧，请使用项目根目录的启动入口。');
    csrfToken=(await jsonApi('/api/session')).csrf_token; onlineReady=true;
    const selected=new URL(location.href).searchParams.get('task');
    let data;
    if(selected){try{data={task:await jsonApi('/api/tasks/'+encodeURIComponent(selected))};}catch{data=await jsonApi('/api/tasks/current');}}
    else data=await jsonApi('/api/tasks/current');
    if(data.task) {liveTask=data.task;state.mode='live';await refreshTask();}
    await refreshHistory().catch(()=>{});
    render();
  } catch(error) {render();appStatus.hidden=false;appStatus.textContent='服务连接失败，请使用项目根目录“启动析言.cmd”重新打开。';toast(error.message);}
}
render(); initializeLive();
