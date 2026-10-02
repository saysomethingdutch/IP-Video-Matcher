const app = document.getElementById('app');
const actor = document.getElementById('actor');
const progress = document.getElementById('progress');
const toast = document.getElementById('toast');
const masthead = document.querySelector('.masthead');
const syncHeaderHeight = () => document.documentElement.style.setProperty('--review-header-height', `${Math.ceil(masthead.getBoundingClientRect().height)}px`);
syncHeaderHeight();
new ResizeObserver(syncHeaderHeight).observe(masthead);
const rowLayoutObserver = new ResizeObserver(syncRowLayout);
function syncRowLayout(){
  for(const [selector,property] of [['#row-toolbar','--review-toolbar-height'],['.video-panel','--review-player-height']]){
    const node=app.querySelector(selector);
    document.documentElement.style.setProperty(property, `${node?Math.ceil(node.getBoundingClientRect().height):0}px`);
  }
}
function watchRowLayout(){
  rowLayoutObserver.disconnect();syncRowLayout();
  app.querySelectorAll('#row-toolbar,.video-panel').forEach(node=>rowLayoutObserver.observe(node));
}
actor.value = localStorage.getItem('tsxk-review-actor') || '';
function actorWarning(){
  let banner=document.getElementById('actor-warning');
  if(!banner){banner=document.createElement('div');banner.id='actor-warning';banner.className='actor-warning';masthead.insertAdjacentElement('afterend',banner)}
  banner.hidden=actor.value.trim()!=='WO104 临时试跑';banner.textContent='核验人仍是“WO104 临时试跑”，请在右上方改成自己的名字。';
}
actorWarning();
actor.addEventListener('input', () => {localStorage.setItem('tsxk-review-actor', actor.value.trim());actorWarning()});
let rows = [];
let current = null;
let filters = {flag: 'all', state: 'all', category: 'all', query: ''};
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt = n => n!==null && n!==undefined && n!=='' && Number.isFinite(Number(n)) ? `${Math.floor(Number(n)/60)}:${String(Math.floor(Number(n)%60)).padStart(2,'0')}` : '—';
const safeBili = value => {try {const u = new URL(value); return u.protocol === 'https:' && (u.hostname === 'bilibili.com' || u.hostname.endsWith('.bilibili.com')) ? u.href : ''} catch {return ''}};
function notify(message){toast.textContent=message;toast.hidden=false;clearTimeout(notify.timer);notify.timer=setTimeout(()=>toast.hidden=true,5500)}
async function api(path, options={}){const response=await fetch(path,{...options,headers:{'Content-Type':'application/json',...(options.headers||{})}});const body=await response.json();if(!response.ok)throw Error(body.error||`HTTP ${response.status}`);return body}
function updateProgress(){const done=rows.filter(r=>r.reviewed===r.total).length;const cells=rows.reduce((n,r)=>n+r.reviewed,0);progress.textContent=`${done}/${rows.length} 行已核 · ${cells}/${rows.length*16} 列已核`}
async function reloadRows(){rows=await api('/api/rows');updateProgress()}
function pills(row){return `${row.red?`<span class="pill red">重点红 ${row.red}</span>`:''}${row.yellow?`<span class="pill yellow">重点黄 ${row.yellow}</span>`:''}${row.technical?`<span class="pill quiet">技术说明 ${row.technical}</span>`:''}`||'<span class="muted">无提示</span>'}
function listPage(){
  current=null;
  const categories=[...new Set(rows.map(r=>r.category))].sort();
  const visible=rows.filter(r=>(filters.flag==='all'||(filters.flag==='focus'&&r.priority_unreviewed)||(filters.flag==='red'&&r.red>0)||(filters.flag==='technical'&&r.technical>0))&&(filters.state==='all'||r.reviewed<r.total)&&(filters.category==='all'||r.category===filters.category)&&(!filters.query||`${r.row_id} ${r.title}`.toLowerCase().includes(filters.query.toLowerCase())));
  app.innerHTML=`<div class="pagehead"><div><h1>沈瑞曦 · 7813–8212</h1><p>逐列核对机器值；未核验的列导出时仍用机器值。</p></div><div class="count">当前显示 ${visible.length} / ${rows.length} 行</div></div>
    <p><button type="button" id="cleanup-preview">查看已核行【其他】清理清单</button> <span class="muted">查看不修改；确认后才记录为你的修改。</span></p><div class="filters"><label>提示 <select id="flag-filter"><option value="all">全部</option><option value="focus">待核重点</option><option value="red">有标红</option><option value="technical">有技术说明</option></select></label><label>核验 <select id="state-filter"><option value="all">全部</option><option value="unreviewed">只看未核验</option></select></label><label>类别 <select id="category-filter"><option value="all">全部类别</option>${categories.map(c=>`<option value="${esc(c)}">${esc(c)}</option>`).join('')}</select></label><label>搜索 <input id="query-filter" value="${esc(filters.query)}" placeholder="序号或标题"></label></div>
    <div class="tablewrap"><table><thead><tr><th>序号</th><th>视频标题</th><th>类别</th><th>核验</th><th>提示</th></tr></thead><tbody>${visible.map(r=>`<tr><td><a href="/row/${r.row_id}">${r.row_id}</a></td><td><a href="/row/${r.row_id}">${esc(r.title||'（无标题）')}</a></td><td>${esc(r.category)}</td><td>${r.reviewed}/${r.total} ${esc(r.status)}</td><td>${pills(r)}</td></tr>`).join('')}</tbody></table></div>`;
  document.getElementById('cleanup-preview').onclick=cleanupPage;
  document.getElementById('cleanup-preview').textContent='查看已核行清理与一致性修改清单';
  watchRowLayout();
  for(const [id,key] of [['flag-filter','flag'],['state-filter','state'],['category-filter','category']]){const node=document.getElementById(id);node.value=filters[key];node.onchange=()=>{filters[key]=node.value;listPage()}}
  document.getElementById('query-filter').oninput=e=>{filters.query=e.target.value;const start=e.target.selectionStart;listPage();const next=document.getElementById('query-filter');next.focus();next.setSelectionRange(start,start)};
}
function seekButton(t,label){return Number.isFinite(Number(t))?`<button class="seek" type="button" data-seek="${Number(t)}">${esc(label||fmt(t))}</button>`:''}
function textEntries(items){return items.length?items.map(item=>{const value=typeof item==='string'?{text:item}:item;return `<li>${seekButton(value.recording_start,'录像 '+fmt(value.recording_start))}<span>${esc(value.text||'')}</span></li>`}).join(''):'<li class="muted">无</li>'}
function ledgerHTML(d){const l=d.ledger||{};return `<section class="panel"><h2>网页台账</h2><div class="facts"><div><b>目标播放</b><br>${fmt(l.play_start)}–${fmt(l.play_end)}（录屏）</div><div><b>实际内容</b><br>${fmt(l.content_start)}–${fmt(l.content_end)}（录屏）</div><div><b>R 完整播放</b><br>${l.complete===true?'是':l.complete===false?'否':'待核'}${l.complete_needs_review?' · 需复核':''}</div></div><p><b>内容区间依据：</b>${esc(l.content_basis||'—')}</p><p><b>R 依据：</b>${esc(l.complete_reason||l.complete_basis||'—')}</p>${l.passes?.length?`<details><summary>逐遍记录（${l.passes.length}）</summary><pre>${esc(JSON.stringify(l.passes,null,2))}</pre></details>`:''}${l.other_pages?.length?`<details><summary>目标视频后的其他页面（${l.other_pages.length}）</summary><ul class="other-pages">${l.other_pages.map(p=>`<li>${seekButton(p.start,'录像 '+fmt(p.start))}–${fmt(p.end)} · ${esc(p.title||p.bv||'其他页面')}${p.autoplay?' · 自动连播':''}</li>`).join('')}</ul></details>`:''}</section>`}
function timelineHTML(d){const segments=d.timeline?.segments||[];return `<section class="panel"><h2>内容时间线</h2>${segments.length?`<div class="segments">${segments.map(s=>`<article class="segment">${s.thumbnail?`<img src="${esc(s.thumbnail)}" alt="录像 ${fmt(s.start)} 的画面">`:'<div class="noimage">无缩略图</div>'}<div><b>${esc(s.content||'未定')}${s.form?' · '+esc(s.form):''}</b><div>${seekButton(s.start,`录像 ${fmt(s.start)}`)}–${fmt(s.end)}</div><small>${esc((s.sources||[]).join('、'))}</small></div></article>`).join('')}</div>`:'<p class="muted">暂无时间线</p>'}</section>`}
function soundHTML(d){const sound=d.sound||{};return `<section class="panel"><h2>声音</h2><p class="muted">${sound.status==='ok'?'本机声音处理完成':'声音状态：'+esc(sound.status||'未生成')} · 播放 ${fmt(sound.played_seconds)}</p><h3>人声转写（候选）</h3><ul class="utterances">${textEntries(sound.uploader_speech?.length?sound.uploader_speech:sound.uploader_text||[])}</ul><h3>推广口播</h3><ul class="utterances">${textEntries(sound.promotional_speech||[])}</ul></section>`}
function evidenceHTML(d){
  const player=d.video_url?`<section class="panel video-panel"><h2>本地取证录像</h2><video id="evidence-video" controls preload="metadata" src="${esc(d.video_url)}"></video><p class="muted">播放器随滚动保持可见；点击时间点跳到录像对应位置。</p></section>`:'<section class="panel"><h2>本地取证录像</h2><p class="muted">文件未挂载</p></section>';
  return `<aside class="evidence-column">${player}<div class="evidence-details">${timelineHTML(d)}${ledgerHTML(d)}${soundHTML(d)}</div></aside>`;
}
function focusHTML(flags,jump=false){
  return flags.map(f=>`<div class="focus-item"><p class="${esc(f.level)}"><b>${f.level==='red'?'标红':'标黄'}：</b>${esc(f.reason)} ${jump?'':`<small>${esc(f.columns.join('、'))}</small>`}</p>${jump?`<div class="focus-columns"><small>核对列：</small>${f.columns.map(col=>`<button type="button" data-focus-col="${esc(col)}">${esc(col)}</button>`).join('')}</div>`:''}</div>`).join('');
}
function rowToolsHTML(d,before,after,flags){
  const level=flags.some(f=>f.level==='red')?'red':'yellow';
  return `<div class="rowtools" id="row-toolbar" aria-label="当前行核验操作">
    <strong class="row-context">${d.row_id} · ${d.reviewed}/${d.total} 列已核</strong>
    <div class="dock-nav">${before?`<a href="/row/${before.row_id}">← 上一行</a>`:''}${after?`<a href="/row/${after.row_id}">下一行 →</a>`:''}</div>
    <button id="approve-rest" type="button">本行其余全部批准</button><button id="approve-next" type="button">批准并到下一行</button><button id="next-focus" type="button">下一个重点行</button>
    <button id="focus-toggle" class="${flags.length?level:''}" type="button" aria-expanded="false" aria-controls="focus-menu" ${flags.length?'':'disabled'}>重点提示（${flags.length}）</button>
    ${d.view_count?`<a class="views-badge" href="#view-count">AD 播放量 ${esc(d.view_count.value === '' ? '待核' : d.view_count.value)}${d.view_count.reviewed?' · 已确认':''}</a>`:''}
    <span class="shortcuts">K/J 上下行 · A 批准余列 · N 下一重点 · P 提示；输入时停用</span>
    ${flags.length?`<section id="focus-menu" class="focus-menu" aria-label="${d.row_id} 的重点提示" hidden><div class="focus-menu-head"><b>${d.row_id} · 重点提示</b><button id="focus-close" type="button" aria-label="关闭重点提示">关闭</button></div>${focusHTML(flags,true)}</section>`:''}
  </div>`;
}
function toggleFocusMenu(force){
  const menu=document.getElementById('focus-menu'),button=document.getElementById('focus-toggle');
  if(!menu||!button)return;
  const open=typeof force==='boolean'?force:menu.hidden;
  menu.hidden=!open;button.setAttribute('aria-expanded',String(open));
}
function fieldHTML(f){
  const ac=f.column==='AC', source=f.by?`由 ${esc(f.by)}`:'机器';
  const state=f.reviewed?(f.modified?'已修改':'已批准'):'未核验';
  const focus=f.flag_reasons.length?`<div class="reasons">${f.flag_reasons.map(x=>`<p><b>重点：</b>${esc(x)}</p>`).join('')}</div>`:'';
  const technical=f.technical_reasons.length?`<details class="technical"><summary>技术说明（${f.technical_reasons.length}）</summary>${f.technical_reasons.map(x=>`<p>${esc(x)}</p>`).join('')}</details>`:'';
  const retain=f.flag_level?`<div class="retain"><label><input type="checkbox" data-retain ${f.retain_mark?'checked':''}>导出时保留标记</label><textarea data-mark-note rows="2" placeholder="保留标记时填写所有者说明">${esc(f.mark_note||'')}</textarea>${f.reviewed?'<button type="button" data-action="mark">保存导出标记</button>':''}</div>`:'';
  return `<section class="field ${f.invalid_value?'red':!f.reviewed?esc(f.flag_level||''):''}" data-col="${f.column}"><div class="fieldtop"><h3>${f.column} · ${esc(f.name)}</h3><span class="state">${state}</span></div>${f.invalid_value?'<p class="invalid-value">当前值不合规，请从下拉框重选并保存。</p>':''}${focus}${technical}<details><summary>机器值与依据</summary><div class="machine">${esc(f.machine_value||'（空）')}</div><p class="muted">${source}；${esc(f.basis||'未给依据')}</p></details>${ac?`<div class="ac-sections">${f.sections.map(s=>`<div class="ac-section"><label>${esc(s.heading)}<textarea data-ac-heading="${esc(s.heading)}">${esc(s.text)}</textarea></label>${["【新增创作要素情况】","【其他】"].includes(s.heading)?`<div class="ac-item-buttons" data-item-heading="${esc(s.heading)}">${listItems(s.text).map((item,i)=>`<p><span>${i+1}. ${esc(item)}</span><button type="button" data-delete-item="${i}" data-delete-heading="${esc(s.heading)}" aria-label="删除${esc(s.heading)}第${i+1}条">删除此条</button></p>`).join('')}</div>`:''}</div>`).join('')}</div>`:f.options?`<select data-value="${f.column}" aria-label="${esc(f.column)} ${esc(f.name)}">${f.invalid_value?`<option value="" disabled selected>请选择（当前：${esc(f.value)}）</option>`:''}${f.options.map(v=>`<option value="${esc(v)}" ${v===f.value?'selected':''}>${esc(v)}</option>`).join('')}</select>`:`<textarea data-value="${f.column}" rows="${Math.max(2,Math.min(5,Math.ceil(f.value.length/70)))}">${esc(f.value)}</textarea>`}${retain}<div class="fieldactions"><button type="button" data-action="approve">批准当前值</button><button type="button" data-action="modify">保存修改</button><small>${f.reviewer?`${esc(f.reviewer)} · ${esc(f.reviewed_at)}`:''}</small></div></section>`
}
function viewCountHTML(view){
  if(!view)return '';
  const value=view.value === '' ? '待核' : String(view.value);
  return `<section class="field views-card" id="view-count" aria-label="AD 播放量"><div class="fieldtop"><h3>AD · 播放量</h3><span class="state">${view.reviewed?'已确认':'待确认'}</span></div><p class="views-value">${esc(value)}</p><p class="muted">页面读数：${esc(view.raw||'未读到')}${view.reviewed?' · 所有者已确认':''} · 随工作簿副本导出</p>${view.crop_url?`<details><summary>查看播放量截图</summary><a href="${esc(view.crop_url)}" target="_blank" rel="noopener"><img class="views-crop" src="${esc(view.crop_url)}" loading="lazy" alt="本行标题下方的播放量截图"></a></details>`:''}</section>`;
}
function alternativeHTML(alt){
  if(!alt?.eligible)return '';
  return `<div class="alternative-block"><button type="button" id="apply-alternative" ${alt.ready?'':'disabled'}>${alt.ready?'改判单IP混剪（换用备好的版本）':'备用版生成中'}</button>${alt.ready?`<details><summary>预览备用版</summary><p>Y：${esc(alt.values.Y)} · U：${esc(alt.values.U)} · V：${esc(alt.values.V)} · Z：${esc(alt.values.Z)}</p><pre>${esc(alt.values.AC)}</pre></details>`:''}</div>`;
}
async function applyAlternative(){
  if(app.querySelector('.field[data-dirty="1"]')){notify('先保存当前修改，再换用备用版');return}
  const who=actor.value.trim();if(!who||who==='WO104 临时试跑'){actor.focus();notify('请先填写自己的核验人名字');return}
  const button=document.getElementById('apply-alternative');button.disabled=true;
  try{
    const rid=current.row_id;
    await api('/api/alternative',{method:'POST',body:JSON.stringify({row_id:rid,actor:who,token:current.alternative.token})});
    await reloadRows();await loadRow(rid);notify('已换用备好的单IP混剪版，Y、U、V、Z、AC 记为你的修改');
  }catch(e){notify(e.message)}finally{if(button.isConnected)button.disabled=false}
}
let opinionSequence=0;
async function previewOpinion(input){
  const card=app.querySelector('.field[data-col="AC"]'),zCard=input.closest('.field'),rid=current.row_id,z=input.value,sequence=++opinionSequence;
  const button=zCard.querySelector('[data-action="modify"]');button.disabled=true;zCard.dataset.previewPending='1';
  try{
    const result=await api('/api/opinion-preview',{method:'POST',body:JSON.stringify({AC:fieldValue(card,'AC'),Z:z})});
    if(current?.row_id!==rid||sequence!==opinionSequence)return;
    const opinion=result.AC.split('【比对意见】')[1]?.split('【其他】')[0]?.trim();
    const node=card.querySelector('[data-ac-heading="【比对意见】"]');
    if(opinion!==undefined&&node.value!==opinion){node.value=opinion;card.dataset.dirty='1'}
    let note=zCard.querySelector('.opinion-sync');if(!note){note=document.createElement('p');note.className='opinion-sync muted';zCard.append(note)}
    note.textContent='比对意见预览：'+opinion+'（保存 Z 时一并记录）';
    zCard.dataset.previewPending='0';button.disabled=false;
  }catch(e){notify('比对意见预览失败：'+e.message)}
}
function rowPage(d){current=d;const pos=rows.findIndex(r=>r.row_id===d.row_id);const before=rows[pos-1],after=rows[pos+1],bili=safeBili(d.bilibili_url),focusFlags=d.flags.filter(f=>f.tier==='focus'),technicalFlags=d.flags.filter(f=>f.tier==='technical');app.innerHTML=`<div class="pagehead"><div><a href="/">← 返回列表</a><h1>${d.row_id} · ${esc(d.title)}</h1><p>${bili?`<a href="${esc(bili)}" target="_blank" rel="noopener">B 站播放页</a> · `:''}沈瑞曦表第 ${d.excel_row} 行 · ${d.reviewed}/${d.total} 列已核</p></div><div class="nav">${before?`<a href="/row/${before.row_id}">← 上一行</a>`:''}${after?`<a href="/row/${after.row_id}">下一行 →</a>`:''}</div></div>
    ${rowToolsHTML(d,before,after,focusFlags)}
    ${focusFlags.length?`<div class="flagbox"><b>重点核对</b>${focusHTML(focusFlags)}</div>`:''}
    ${technicalFlags.length?`<details class="flagbox technical"><summary>技术说明（${technicalFlags.length}，不标色）</summary>${technicalFlags.map(f=>`<p>${esc(f.reason)} <small>${esc(f.columns.join('、'))}</small></p>`).join('')}</details>`:''}
    <div class="review-grid">${evidenceHTML(d)}<div><section class="panel"><h2>机器填值与逐列核验</h2><p class="muted">批准或保存修改会立即写入本地追加记录。AC 分五段编辑，导出时合为一个单元格。</p><div class="fields">${d.fields.map(fieldHTML).join('')}${viewCountHTML(d.view_count)}</div></section><section class="panel"><h2>修改与批准记录</h2>${d.history.length?`<div class="history">${d.history.map(e=>`<p><b>${esc(e.field)}</b> ${esc(e.action==='modify'?'修改':e.action==='mark'?'导出标记':'批准')} · ${esc(e.who)} · ${esc(e.when)}${e.retain_mark?' · 保留标记：'+esc(e.mark_note):''}<br>${esc(e.old)} → ${esc(e.new)}</p>`).join('')}</div>`:'<p class="muted">尚无记录</p>'}</section></div></div>`;
  watchRowLayout();
  const yCard=app.querySelector('.field[data-col="Y"]');yCard.querySelector('.fieldactions').insertAdjacentHTML('beforebegin',alternativeHTML(d.alternative));
  const altButton=document.getElementById('apply-alternative');if(altButton&&d.alternative?.ready)altButton.onclick=applyAlternative;
  app.querySelector('.field[data-col="Z"] [data-value]').onchange=e=>previewOpinion(e.target);
  app.querySelectorAll('.field button[data-action]').forEach(button=>button.onclick=()=>saveField(button.closest('.field'),button.dataset.action));
  app.querySelectorAll('.field [data-value],.field textarea[data-ac-heading]').forEach(input=>input.oninput=()=>{input.closest('.field').dataset.dirty='1'});
  app.querySelectorAll('[data-delete-item]').forEach(button=>button.onclick=()=>deleteItem(button));
  document.getElementById('approve-rest').onclick=()=>approveRest(false);
  document.getElementById('approve-next').onclick=()=>approveRest(true);
  document.getElementById('next-focus').onclick=nextFocus;
  document.getElementById('focus-toggle').onclick=()=>toggleFocusMenu();
  const close=document.getElementById('focus-close');if(close)close.onclick=()=>toggleFocusMenu(false);
  app.querySelectorAll('[data-focus-col]').forEach(button=>button.onclick=()=>{
    toggleFocusMenu(false);
    app.querySelector(`.field[data-col="${button.dataset.focusCol}"]`)?.scrollIntoView({behavior:'smooth',block:'start'});
  });
  app.querySelectorAll('[data-seek]').forEach(button=>button.onclick=()=>{const video=document.getElementById('evidence-video');if(!video){notify('本地录像不可用');return}const t=Number(button.dataset.seek);const go=()=>{video.currentTime=t;video.play().catch(()=>{})};if(video.readyState)go();else video.addEventListener('loadedmetadata',go,{once:true})});
}
function listItems(text){return String(text||'').split('\n').map(x=>x.replace(/^\s*\d+[.、．]\s*/, '').trim()).filter(x=>x&&x!=='无')}
async function deleteItem(button){
  const card=button.closest('.field'),heading=button.dataset.deleteHeading;
  if(card.dataset.dirty==='1'){notify('先保存现有修改，再删除条目');return}
  const node=[...card.querySelectorAll('[data-ac-heading]')].find(n=>n.dataset.acHeading===heading);
  const items=listItems(node.value);items.splice(Number(button.dataset.deleteItem),1);
  node.value=items.length?items.map((x,i)=>`${i+1}.${x}`).join('\n'):(heading==='【新增创作要素情况】'?'无':'');
  card.dataset.dirty='1';await saveField(card,'modify');
}
async function cleanupPage(){
  try{
    const proposal=await api('/api/cleanup-other');current=null;
    app.innerHTML=`<div class="pagehead"><div><a href="/">← 返回列表</a><h1>已核行清理与一致性修改清单</h1><p>${proposal.row_count} 行 · ${proposal.sentence_count} 条模型说明 · ${proposal.cell_count} 格修改。确认后才生效。</p></div></div><div class="cleanup-list">${proposal.rows.map(r=>`<section class="panel"><h2>${esc(r.row)}</h2><ul>${r.removed.map(s=>`<li>删除：${esc(s)}</li>`).join('')}${Object.entries(r.edits).map(([col,e])=>`<li>${esc(col)}：${esc(e.reason)}${col==='AC'?'':`（${esc(e.old)} → ${esc(e.new)}）`}</li>`).join('')}</ul>${r.edits.AC?`<details><summary>预览清理后的完整视频说明</summary><pre>${esc(r.edits.AC.new)}</pre></details>`:''}</section>`).join('')}</div><section class="panel"><label><input id="cleanup-consent" type="checkbox">我已查看清单，同意将这些修改记为我的核验结果</label><p><button id="cleanup-confirm" type="button" disabled>确认修改这 ${proposal.row_count} 行</button></p></section>`;
    watchRowLayout();document.getElementById('cleanup-consent').onchange=e=>document.getElementById('cleanup-confirm').disabled=!e.target.checked||!proposal.row_count;
    document.getElementById('cleanup-confirm').onclick=async()=>{
      const who=actor.value.trim();if(!who||who==='WO104 临时试跑'){actor.focus();notify('请先填写自己的核验人名字');return}
      const result=await api('/api/cleanup-other',{method:'POST',body:JSON.stringify({token:proposal.token,actor:who,confirmed:true})});
      await reloadRows();listPage();notify(`已清理 ${result.rows} 行、${result.sentences} 条，记录为 ${who} 的修改`);
    };
  }catch(e){notify(e.message)}
}
function fieldValue(card,col){if(col!=='AC')return card.querySelector('[data-value]').value;return [...card.querySelectorAll('[data-ac-heading]')].map(node=>`${node.dataset.acHeading}\n${node.value.trim()}`).join('\n\n')}
async function saveField(card,action){
  if(!current)return;
  const who=actor.value.trim();if(!who){actor.focus();notify('请先填写核验人');return}
  const col=card.dataset.col, f=current.fields.find(x=>x.column===col), changed=card.dataset.dirty==='1';
  if(col==='AC'&&app.querySelector('.field[data-col="Z"][data-dirty="1"]')){notify('请先保存 Z；视频说明会同时保存');return}
  if(col==='Z'&&card.dataset.previewPending==='1'){notify('正在生成比对意见预览');return}
  const value=action==='modify'?fieldValue(card,col):f.value;
  if(action==='modify'&&(!changed||value===f.value)){notify('内容没有变化');return}
  if(action!=='modify'&&changed){notify('内容已修改，请先点“保存修改”');return}
  const retain_mark=Boolean(card.querySelector('[data-retain]')?.checked);
  const mark_note=retain_mark?(card.querySelector('[data-mark-note]')?.value.trim()||''):'';
  if(retain_mark&&!mark_note){notify('保留标记时请填写所有者说明');return}
  const button=card.querySelector(`[data-action="${action}"]`);button.disabled=true;
  try{
    const coupled=col==='Z'&&action==='modify'?{opinion_preview:fieldValue(app.querySelector('.field[data-col="AC"]'),'AC'),opinion_base:current.fields.find(x=>x.column==='AC').value}:{};
    await api('/api/decision',{method:'POST',body:JSON.stringify({row_id:current.row_id,action,actor:who,edits:{[col]:value},retain_mark,mark_note,...coupled})});
    const rid=current.row_id;await reloadRows();await loadRow(rid);
    notify(`${rid} ${col} 已${action==='modify'?'修改':action==='mark'?'保存导出标记':'批准'}`);
  }catch(e){notify(e.message)}finally{button.disabled=false}
}
function jumpTo(rid){if(!rid)return;history.pushState({},'',`/row/${rid}`);route();scrollTo(0,0)}
function adjacent(delta){if(!current)return;const pos=rows.findIndex(r=>r.row_id===current.row_id);jumpTo(rows[pos+delta]?.row_id)}
function nextFocus(){
  if(!current)return;const pos=rows.findIndex(r=>r.row_id===current.row_id);
  for(let step=1;step<rows.length;step++){const candidate=rows[(pos+step)%rows.length];if(candidate.priority_unreviewed){jumpTo(candidate.row_id);return}}
  notify('没有其他待核的重点行');
}
async function approveRest(goNext){
  if(!current)return;const who=actor.value.trim();if(!who){actor.focus();notify('请先填写核验人');return}
  if(app.querySelector('.field[data-dirty="1"]')){notify('本行有未保存的修改，请先保存');return}
  if(current.fields.some(f=>!f.reviewed&&app.querySelector(`.field[data-col="${f.column}"] [data-retain]`)?.checked)){
    notify('有待核格勾选了保留标记，请先单格批准并填写说明');return;
  }
  const rid=current.row_id, next=rows[rows.findIndex(r=>r.row_id===rid)+1]?.row_id;
  for(const id of ['approve-rest','approve-next'])document.getElementById(id).disabled=true;
  try{
    const result=await api('/api/approve-row',{method:'POST',body:JSON.stringify({row_id:rid,actor:who})});
    await reloadRows();if(goNext&&next)jumpTo(next);else await loadRow(rid);
    notify(`${rid} 本次批准 ${result.approved_now} 列${goNext&&next?'，已到下一行':''}`);
  }catch(e){notify(e.message)}finally{
    for(const id of ['approve-rest','approve-next']){const node=document.getElementById(id);if(node)node.disabled=false}
  }
}
document.addEventListener('keydown',event=>{
  const menu=document.getElementById('focus-menu');
  if(event.key==='Escape'&&menu&&!menu.hidden){toggleFocusMenu(false);document.getElementById('focus-toggle')?.focus();return}
  if(!current||event.repeat||event.isComposing||event.ctrlKey||event.metaKey||event.altKey||event.target.closest('input,textarea,select,[contenteditable="true"]'))return;
  const key=event.key.toLowerCase();
  if(key==='k'){event.preventDefault();adjacent(-1)}
  else if(key==='j'){event.preventDefault();adjacent(1)}
  else if(key==='a'){event.preventDefault();approveRest(false)}
  else if(key==='n'){event.preventDefault();nextFocus()}
  else if(key==='p'){event.preventDefault();toggleFocusMenu()}
});
document.addEventListener('click',event=>{if(!event.target.closest('#row-toolbar'))toggleFocusMenu(false)});
async function loadRow(rid){const d=await api('/api/row/'+rid);rowPage(d)}
function route(){const match=location.pathname.match(/^\/row\/(\d+)$/);if(match)loadRow(match[1]).catch(e=>{app.innerHTML=`<p class="empty">${esc(e.message)}</p>`});else listPage()}
document.addEventListener('click',event=>{const link=event.target.closest('a[href^="/row/"],a[href="/"]');if(link&&!event.metaKey&&!event.ctrlKey){event.preventDefault();history.pushState({},'',link.getAttribute('href'));route();scrollTo(0,0)}});
window.addEventListener('popstate',route);
document.getElementById('export').onclick=async()=>{const button=document.getElementById('export');button.disabled=true;try{const result=await api('/api/export',{method:'POST',body:'{}'});notify('已导出副本和说明：'+result.path)}catch(e){notify('导出失败：'+e.message)}finally{button.disabled=false}};
reloadRows().then(route).catch(e=>{app.innerHTML=`<p class="empty">读取失败：${esc(e.message)}</p>`});
