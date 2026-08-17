const $ = id => document.getElementById(id);
const SCENES = new Set(['search','recommend','evolve','audit']);
const state = {
  conversation:null,
  scene:'search',
  lastResult:null,
  seenEvents:0,
  activeRuns:new Map(),
  attachments:[],
  uploading:0,
  network:false,
  networkAvailable:false,
  visionReady:false,
  dragDepth:0,
};

const scenePrompt = {
  search:'最近搜索“露营灯”的结果不太准，帮我复现问题、定位原因，并探索一个可验证的改进方向，但先不要改变当前策略。',
  recommend:'帮我看看用户 u-lin 的推荐首屏，检查重复、新鲜度和冷启动风险，并告诉我最值得先处理的问题。',
  evolve:'检查当前搜索和推荐体验，自己判断哪一侧更值得优化；证据足够时自主探索候选策略，但先不要改变当前策略。',
  audit:'做一次全局体检，告诉我搜索和推荐里现在最值得先解决的三个问题。',
};

function esc(s){return String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
function bytes(n){const v=Number(n)||0;if(v<1024)return `${v} B`;if(v<1024*1024)return `${(v/1024).toFixed(v<10240?1:0)} KB`;return `${(v/1024/1024).toFixed(1)} MB`}
function safeExternalUrl(value){try{const u=new URL(String(value));return ['http:','https:'].includes(u.protocol)?u.href:''}catch{return ''}}

async function api(path,opt={}){
  const {headers={},...rest}=opt;
  const r=await fetch(path,{...rest,headers:{...(opt.body instanceof FormData?{}:{'Content-Type':'application/json'}),...headers}});
  if(!r.ok){let t=await r.text();try{t=JSON.parse(t).detail||t}catch{}throw new Error(typeof t==='string'?t:JSON.stringify(t))}
  const type=r.headers.get('content-type')||'';
  return type.includes('application/json')?r.json():r.text();
}

function toast(t){const el=$('toast');el.textContent=t;el.classList.add('show');clearTimeout(toast.t);toast.t=setTimeout(()=>el.classList.remove('show'),2300)}
function md(text){let s=esc(text||'');s=s.replace(/^###\s+(.+)$/gm,'<h3>$1</h3>');const lines=s.split('\n'),out=[];let list=false;for(const line of lines){if(line.startsWith('- ')){if(!list){out.push('<ul>');list=true}out.push(`<li>${line.slice(2)}</li>`)}else{if(list){out.push('</ul>');list=false}if(line.trim())out.push(`<p>${line}</p>`)}}if(list)out.push('</ul>');return out.join('')}
function autoSize(){const el=$('input');el.style.height='auto';el.style.height=Math.min(150,Math.max(58,el.scrollHeight))+'px'}
function scrollBottom(){requestAnimationFrame(()=>{$('scrollArea').scrollTop=$('scrollArea').scrollHeight})}
function currentBusy(){return !!(state.conversation?.id&&state.activeRuns.has(state.conversation.id))}
function updateSendState(){const busy=currentBusy();$('sendBtn').disabled=busy||state.uploading>0;$('runStopBtn').disabled=!busy;$('taskState').textContent=busy?'正在自主执行':state.conversation?'可继续追问':'等待输入'}
function updateSceneNav(){document.querySelectorAll('.scene').forEach(x=>{const active=x.dataset.scene===state.scene;x.classList.toggle('active',active);x.setAttribute('aria-pressed',String(active))})}
function updateNetworkButton(){const b=$('networkBtn');b.disabled=!state.networkAvailable;b.setAttribute('aria-pressed',String(state.network&&state.networkAvailable));b.title=state.networkAvailable?'允许本次任务检索公开网络资料':'当前没有配置联网研究服务'}

async function loadStatus(){
  const s=await api('/api/status'),d=s.data;
  $('dataName').textContent=d.name;$('dataMeta').textContent=`${d.items} 内容 · ${d.users} 用户`;$('datasetTitle').textContent=d.name;
  $('dataGrid').innerHTML=[['内容',d.items],['用户',d.users],['行为',d.interactions],['复核问题',d.queries]].map(([k,v])=>`<div class="data-cell"><b>${v}</b><span>${k}</span></div>`).join('');
  state.networkAvailable=!!s.network?.available;state.visionReady=!!s.multimodal?.vision_ready;
  $('visionStatus').textContent=`图片感知 · ${state.visionReady?'已就绪':'可接收'}`;
  $('networkStatus').textContent=`联网研究 · ${state.networkAvailable?'已就绪':'待配置'}`;
  $('visionCapability').textContent=state.visionReady?'视觉已就绪':'文件已就绪';
  $('networkCapability').textContent=state.networkAvailable?'已就绪':'待配置';
  if(!state.networkAvailable)state.network=false;
  updateNetworkButton();
}

async function loadHistory(){
  const rows=await api('/api/conversations');
  $('historyList').innerHTML=rows.length?rows.map(x=>`<button type="button" class="history-item ${(x.active||state.activeRuns.has(x.id))?'running':''}" data-id="${esc(x.id)}"><b>${esc(x.title)}</b><small>${new Date(x.updated_at*1000).toLocaleString('zh-CN',{month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit'})}</small></button>`).join(''):'<div class="empty">还没有历史任务。</div>';
  document.querySelectorAll('.history-item').forEach(x=>x.onclick=()=>{void openConversation(x.dataset.id).catch(e=>toast(e.message))});
  return rows;
}

function clearResult(){
  state.lastResult=null;state.seenEvents=0;$('stateText').textContent='等待开始';$('running').hidden=true;$('copyBtn').disabled=true;
  $('timeline').innerHTML='<div class="empty">开始一个任务后，这里会记录系统实际做过的每一步。</div>';
  $('evidenceList').innerHTML='<div class="empty">还没有执行结果。</div>';
  $('suggestions').innerHTML='<button>先做一次全局体检</button><button>检查一个具体搜索</button>';
  bindSuggestionButtons();
}

function clearComposerContext(){state.attachments=[];state.network=false;renderAttachments();updateNetworkButton()}

function startDraft(scene=state.scene){
  state.scene=SCENES.has(scene)?scene:'audit';state.conversation=null;clearResult();clearComposerContext();updateSceneNav();
  renderMessages([]);$('taskTitle').textContent='新的体验任务';$('taskState').textContent='等待输入';$('input').value=scenePrompt[state.scene]||'';autoSize();updateSendState();$('input').focus();
}

async function openConversation(id){
  const c=await api(`/api/conversations/${id}`);state.conversation=c;state.scene=SCENES.has(c.scene)?c.scene:'audit';state.seenEvents=0;clearComposerContext();updateSceneNav();
  state.lastResult=[...c.messages].reverse().find(x=>x.role==='assistant')?.payload||null;$('taskTitle').textContent=c.title;renderMessages(c.messages);
  if(state.lastResult)renderResult(state.lastResult);else clearResult();
  if(c.active_run?.run_id&&!state.activeRuns.has(c.id)){state.activeRuns.set(c.id,c.active_run.run_id);if(c.active_run.events?.length)renderRunning(c.active_run.events);void pollRun(c.active_run.run_id,c.id)}
  updateSendState();await loadHistory();
}

function attachmentHtml(x){
  const isImage=(x.mime||'').startsWith('image/');
  return `<div class="message-attachment">${isImage?`<img src="${esc(x.url||'')}" alt="${esc(x.name||'图片附件')}" loading="lazy">`:'<span class="doc-mark">DOC</span>'}<div><b>${esc(x.name||'附件')}</b><small>${esc(bytes(x.size))}</small></div></div>`;
}
function messageContextHtml(m){
  const attachments=m.payload?.attachments||[];const chips=[];
  if(m.payload?.allow_network)chips.push('<span class="context-chip network">联网证据已开启</span>');
  if(attachments.length)chips.push(`<span class="context-chip">${attachments.length} 个附件</span>`);
  const files=attachments.length?`<div class="message-attachments">${attachments.map(attachmentHtml).join('')}</div>`:'';
  return `${chips.length?`<div class="msg-meta">${chips.join('')}</div>`:''}${files}`;
}
function userMessageHtml(m){return `<div class="msg user"><div class="bubble"><div class="bubble-text">${esc(m.content)}</div>${messageContextHtml(m)}</div></div>`}
function assistantMessageHtml(m){return `<div class="msg assistant"><div class="bubble"><div class="assistant-label"><b>灵境</b><span>执行完成</span></div><div class="answer">${md(m.content)}</div></div></div>`}
function renderMessages(rows){$('welcome').hidden=rows.length>0;$('messageList').innerHTML=rows.map(m=>m.role==='user'?userMessageHtml(m):assistantMessageHtml(m)).join('');scrollBottom()}
function appendUser(text,payload){$('welcome').hidden=true;$('messageList').insertAdjacentHTML('beforeend',userMessageHtml({content:text,payload}));scrollBottom()}
function appendAssistant(msg){$('messageList').insertAdjacentHTML('beforeend',assistantMessageHtml(msg));scrollBottom()}

function renderEvent(ev,index,total){const done=ev.progress>=100||index<total-1;return `<div class="timeline-row ${done?'done':''}"><span>${done?'✓':String(index+1).padStart(2,'0')}</span><div><b>${esc(ev.title)}</b><small>${esc(ev.detail)}</small></div></div>`}
function renderRunning(events=[]){if(!events.length)return;const ev=events.at(-1);$('running').hidden=false;$('runStopBtn').disabled=false;$('runStopBtn').textContent='停止';$('runTitle').textContent=ev.title;$('runDetail').textContent=ev.detail;$('runPercent').textContent=`${ev.progress}%`;$('runBar').style.width=`${ev.progress}%`;$('stateText').textContent=ev.progress>=100?'已完成':'正在执行';$('taskState').textContent=ev.progress>=100?'已完成':'正在自主执行';$('timeline').innerHTML=events.map((x,i)=>renderEvent(x,i,events.length)).join('');state.seenEvents=events.length;scrollBottom()}
function evidenceHtml(x,i){const url=safeExternalUrl(x.url);return `<div class="evidence-item"><span>${x.kind==='external'?'公开来源':'依据'} ${String(i+1).padStart(2,'0')}</span><b>${esc(x.title)}</b><small>${esc(x.detail||'已在本次执行中复核')}</small>${url?`<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">打开公开来源 ↗</a>`:''}</div>`}
function bindSuggestionButtons(){$('suggestions').querySelectorAll('button').forEach(b=>b.onclick=()=>{$('input').value=b.textContent;autoSize();$('input').focus()})}
function renderResult(r){
  if(!r)return clearResult();state.lastResult=r;$('running').hidden=true;$('copyBtn').disabled=false;$('stateText').textContent='已完成';$('taskState').textContent='已完成';
  const events=r.events||[];$('timeline').innerHTML=events.length?events.map((ev,i)=>renderEvent({...ev,progress:100},i,events.length)).join(''):'<div class="empty">本次没有执行记录。</div>';
  const evidence=r.evidence||[];$('evidenceList').innerHTML=evidence.length?evidence.map(evidenceHtml).join(''):'<div class="empty">本次没有生成具体结果证据。</div>';
  $('suggestions').innerHTML=(r.suggestions||[]).map(x=>`<button>${esc(x)}</button>`).join('')||'<button>继续检查一个具体问题</button>';bindSuggestionButtons();
}

async function pollRun(id,conversationId){
  let failures=0;
  let reconnectNotified=false;
  while(state.activeRuns.get(conversationId)===id){
    let r;
    try{
      r=await api(`/api/runs/${id}`);failures=0;reconnectNotified=false;
    }catch(e){
      failures+=1;const isCurrent=state.conversation?.id===conversationId;
      if(isCurrent){$('taskState').textContent='连接中断 · 正在重连';$('runDetail').textContent='任务仍在后台执行，正在重新连接…'}
      if(!reconnectNotified){toast('连接暂时中断，任务仍在后台执行');reconnectNotified=true}
      const delay=Math.min(5000,400*Math.pow(1.7,Math.min(failures,5)));
      await new Promise(res=>setTimeout(res,delay));
      continue;
    }
    const isCurrent=state.conversation?.id===conversationId;
    if(isCurrent)renderRunning(r.events||[]);
    if(r.status==='completed'){
      state.activeRuns.delete(conversationId);if(isCurrent){appendAssistant(r.message);renderResult(r.result)}await loadHistory();updateSendState();return;
    }
    if(r.status==='cancel_requested'){if(isCurrent){$('runStopBtn').disabled=true;$('runStopBtn').textContent='停止中';$('runDetail').textContent='正在安全结束当前动作…'}}
    if(r.status==='cancelled'){
      state.activeRuns.delete(conversationId);if(isCurrent){$('running').hidden=true;$('stateText').textContent='已停止';$('taskState').textContent='已停止';toast('本次执行已停止')}await loadHistory();updateSendState();return;
    }
    if(r.status==='failed'){
      state.activeRuns.delete(conversationId);if(isCurrent){$('running').hidden=true;$('stateText').textContent='执行失败';$('taskState').textContent='需要处理';toast('执行失败：'+(r.error||'未知错误'))}await loadHistory();updateSendState();return;
    }
    await new Promise(res=>setTimeout(res,500));
  }
}



async function cancelCurrentRun(){
  const conversationId=state.conversation?.id,runId=conversationId?state.activeRuns.get(conversationId):null;
  if(!runId)return;
  const button=$('runStopBtn');button.disabled=true;button.textContent='停止中';
  try{await api(`/api/runs/${runId}/cancel`,{method:'POST',body:JSON.stringify({})});$('runDetail').textContent='正在安全结束当前动作…'}
  catch(e){button.disabled=false;button.textContent='停止';toast(e.message)}
}

async function ensureConversation(){if(state.conversation)return state.conversation;const c=await api('/api/conversations',{method:'POST',body:JSON.stringify({scene:state.scene,title:'新的体验任务'})});state.conversation=c;$('taskTitle').textContent=c.title;await loadHistory();return c}

async function send(){
  if(currentBusy())return toast('当前任务仍在执行，可以切到另一个任务继续工作');
  if(state.uploading>0)return toast('附件还在上传');
  let text=$('input').value.trim();if(!text&&state.attachments.length)text='请基于这些附件检查搜索或推荐体验，先复现可验证问题，再给出结论。';if(!text)return;
  try{
    const c=await ensureConversation(),conversationId=c.id;
    const payload={content:text,attachments:state.attachments.map(x=>x.id),allow_network:state.network&&state.networkAvailable};
    const displayPayload={attachments:[...state.attachments],allow_network:payload.allow_network};
    const res=await api(`/api/conversations/${conversationId}/messages`,{method:'POST',body:JSON.stringify(payload)});
    appendUser(text,displayPayload);state.activeRuns.set(conversationId,res.run_id);$('input').value='';clearComposerContext();autoSize();$('stateText').textContent='正在执行';$('taskState').textContent='正在自主执行';$('timeline').innerHTML='<div class="empty">正在建立本次执行上下文…</div>';$('running').hidden=false;updateSendState();void pollRun(res.run_id,conversationId);await loadHistory();
  }catch(e){toast(e.message);updateSendState()}
}

function renderAttachments(){
  const tray=$('attachmentTray');tray.hidden=!state.attachments.length;
  tray.innerHTML=state.attachments.map((x,i)=>{const image=(x.mime||'').startsWith('image/');return `<div class="attachment-card" data-index="${i}">${image?`<img src="${esc(x.url||'')}" alt="">`:'<span class="file-mark">DOC</span>'}<div><b>${esc(x.name)}</b><small>${esc(bytes(x.size))}</small></div><button aria-label="移除 ${esc(x.name)}">×</button></div>`}).join('');
  tray.querySelectorAll('.attachment-card button').forEach((b,i)=>b.onclick=()=>{state.attachments.splice(i,1);renderAttachments()});
}

async function uploadAttachment(file){
  if(state.attachments.length>=8){toast('每次最多 8 个附件');return}
  if(file.size>12*1024*1024){toast(`${file.name} 超过 12MB`);return}
  state.uploading++;updateSendState();
  try{const fd=new FormData();fd.append('file',file,file.name||'attachment');const row=await api('/api/attachments',{method:'POST',body:fd});state.attachments.push(row);renderAttachments()}
  catch(e){toast(`${file.name||'附件'}：${e.message}`)}
  finally{state.uploading--;updateSendState()}
}
async function addFiles(files){for(const file of [...files].slice(0,8-state.attachments.length))await uploadAttachment(file)}

async function importDataset(file){const fd=new FormData();fd.append('file',file);try{const r=await api('/api/data/import-file',{method:'POST',body:fd});toast(`已导入 ${r.data.items} 条内容`);await loadStatus();startDraft('audit');await loadHistory()}catch(e){toast(e.message)}}

function openInspector(){const el=$('inspector');el.classList.add('open');$('inspectorToggle').setAttribute('aria-expanded','true')}
function closeInspector(){const el=$('inspector');el.classList.remove('open');$('inspectorToggle').setAttribute('aria-expanded','false')}

function bind(){
  document.querySelectorAll('.scene').forEach(b=>b.onclick=()=>startDraft(b.dataset.scene));
  document.querySelectorAll('[data-prompt]').forEach(b=>b.onclick=()=>{$('input').value=b.dataset.prompt;autoSize();$('input').focus()});
  document.querySelectorAll('.tab').forEach(b=>b.onclick=()=>{document.querySelectorAll('.tab').forEach(x=>{const active=x===b;x.classList.toggle('active',active);x.setAttribute('aria-selected',String(active))});document.querySelectorAll('.panel').forEach(x=>x.classList.remove('active'));$(`panel-${b.dataset.tab}`).classList.add('active')});
  $('newTaskBtn').onclick=()=>startDraft(state.scene);$('refreshBtn').onclick=()=>{void loadHistory().catch(e=>toast(e.message))};$('sendBtn').onclick=send;$('runStopBtn').onclick=()=>{void cancelCurrentRun()};
  $('input').oninput=autoSize;$('input').onkeydown=e=>{if(e.isComposing||e.keyCode===229)return;if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();void send()}};
  $('input').addEventListener('paste',e=>{const files=[...(e.clipboardData?.files||[])].filter(f=>f.type.startsWith('image/'));if(files.length)void addFiles(files)});
  $('attachBtn').onclick=()=>$('fileInput').click();$('fileInput').onchange=e=>{if(e.target.files?.length)void addFiles(e.target.files);e.target.value=''};
  $('networkBtn').onclick=()=>{if(!state.networkAvailable)return toast('联网研究尚未配置；配置后可对单次任务显式开启');state.network=!state.network;updateNetworkButton()};
  $('importBtn').onclick=$('importBtnSide').onclick=()=>$('dataFileInput').click();$('dataFileInput').onchange=e=>{if(e.target.files?.[0])void importDataset(e.target.files[0]);e.target.value=''};
  $('copyBtn').onclick=async()=>{if(!state.lastResult)return toast('还没有可复制的结论');const text=state.lastResult.answer||'';try{if(navigator.clipboard?.writeText){await navigator.clipboard.writeText(text)}else{const ta=document.createElement('textarea');ta.value=text;document.body.appendChild(ta);ta.select();document.execCommand('copy');ta.remove()}toast('结论已复制')}catch{toast('复制失败，请手动复制')}};
  $('inspectorToggle').onclick=openInspector;$('inspectorClose').onclick=closeInspector;
  const wrap=$('composerWrap');
  wrap.addEventListener('dragenter',e=>{e.preventDefault();state.dragDepth++;wrap.classList.add('dragging')});
  wrap.addEventListener('dragover',e=>{e.preventDefault();wrap.classList.add('dragging')});
  wrap.addEventListener('dragleave',e=>{e.preventDefault();state.dragDepth=Math.max(0,state.dragDepth-1);if(!state.dragDepth)wrap.classList.remove('dragging')});
  wrap.addEventListener('drop',e=>{e.preventDefault();state.dragDepth=0;wrap.classList.remove('dragging');if(e.dataTransfer?.files?.length)void addFiles(e.dataTransfer.files)});
  document.addEventListener('keydown',e=>{if(e.key==='Escape')closeInspector()});
}

async function boot(){
  bind();autoSize();renderAttachments();$('copyBtn').disabled=true;await loadStatus();const rows=await loadHistory();if(rows.length)await openConversation(rows[0].id);else startDraft('search');document.body.classList.add('ready');
}
boot().catch(e=>{document.body.classList.add('ready');toast(e.message)});
