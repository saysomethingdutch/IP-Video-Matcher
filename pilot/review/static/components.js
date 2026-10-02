/* Reusable controls. No case title, column letter or category. */
window.ReviewComponents={
 escape(value){return String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))},
 time(t){return `${Math.floor(t/60)}:${String(Math.floor(t%60)).padStart(2,'0')}`},
 fixedSelect(options,value,attributes=''){
  const e=this.escape;return `<select ${attributes}>${options.includes(value)?'':`<option value="" disabled selected>请选择（当前：${e(value)}）</option>`}${options.map(v=>`<option value="${e(v)}" ${v===value?'selected':''}>${e(v)}</option>`).join('')}</select>`
 },
 removeItem(text,index){const items=String(text||'').split('\n').map(x=>x.replace(/^\s*\d+[.、．]\s*/,'').trim()).filter(x=>x&&x!=='无');items.splice(index,1);return items.map((x,i)=>`${i+1}.${x}`).join('\n')},
 alternative(alt,label){if(!alt?.eligible)return '';const e=this.escape;return `<div class="alternative-block"><button type="button" id="apply-alternative" ${alt.ready?'':'disabled'}>${e(alt.ready?label:'备用版生成中')}</button>${alt.ready?`<details><summary>预览备用版</summary><pre>${e(Object.entries(alt.values).map(([k,v])=>k+'：'+v).join('\n'))}</pre></details>`:''}</div>`},
 evidence(field){
  const e=this.escape,entries=field.support||[];
  const list=values=>`<ul>${values.map(s=>`<li><button type="button" class="seek" data-seek="${Number(s.t)}">${this.time(s.t)}${Number.isFinite(s.end)?'–'+this.time(s.end):''}</button> <span>${e(s.quote)}</span><small> · ${e(s.kind)}</small>${s.thumbnail?` <a target="_blank" rel="noopener" href="${e(s.thumbnail)}">截图</a>`:''}</li>`).join('')}</ul>`;
  return `<div class="evidence-basis"><strong>核验依据</strong>${field.evidence_missing?'<p class="invalid-value">缺少时间点和原话/画面依据</p>':''}${entries.length?list(entries.slice(0,3))+(entries.length>3?`<details><summary>更多依据（${entries.length-3}）</summary>${list(entries.slice(3))}</details>`:''):'<p class="muted">未提供可跳转的依据</p>'}</div>`
 },
 async syncText(api,endpoint,text,grade,mapping={text:'text',grade:'grade'}){return api(endpoint,{method:'POST',body:JSON.stringify({[mapping.text]:text,[mapping.grade]:grade})})},
 confirmation(proposal,label='确认修改'){return `<label><input type="checkbox" data-batch-consent>我已查看旧值、新值和理由，同意记录这些修改</label><button type="button" data-batch-confirm disabled>${this.escape(label)}</button>`}
};
ReviewComponents.batchPage=function(container,api,onDone){
 const e=this.escape;
 container.innerHTML=`<section class="panel"><h1>Owner 代办修改清单</h1><p>只应用旧值仍一致的格子；查看清单不会修改核验记录。</p><label>导入 JSON 清单 <input type="file" accept=".json" data-batch-file></label><div data-batch-preview></div></section>`;
 const view=container.querySelector('[data-batch-preview]');
 container.querySelector('[data-batch-file]').onchange=async ev=>{
  try{
   const data=JSON.parse(await ev.target.files[0].text()),items=Array.isArray(data)?data:data.items;
   const p=await api('/api/batch-preview',{method:'POST',body:JSON.stringify({items})});
   view.innerHTML=`<p>可修改 ${p.matched} 格；跳过 ${p.skipped} 格。</p>${p.items.map(i=>`<article class="panel"><b>${e(i.row)} · ${e(i.column)}${i.apply?'':' · 跳过：'+e(i.skip_reason)}</b><p>理由：${e(i.reason)}</p><pre>旧值：${e(i.old)}\n当前：${e(i.current)}\n新值：${e(i.new)}</pre></article>`).join('')}<label>代办人姓名 <input data-batch-delegate></label><p>${this.confirmation(p)}</p><p data-batch-result role="status"></p>`;
   const consent=view.querySelector('[data-batch-consent]'),button=view.querySelector('[data-batch-confirm]'),delegate=view.querySelector('[data-batch-delegate]');
   const ready=()=>button.disabled=!consent.checked||!delegate.value.trim()||!p.matched;consent.onchange=ready;delegate.oninput=ready;
   button.onclick=async()=>{
    button.disabled=true;
    try{const result=await api('/api/batch-apply',{method:'POST',body:JSON.stringify({items,token:p.token,delegate:delegate.value.trim(),confirmed:consent.checked})});
     view.querySelector('[data-batch-result]').textContent=`已记录 ${result.applied} 格，跳过 ${result.skipped.length} 格。`;if(onDone)onDone();
    }catch(err){view.querySelector('[data-batch-result]').textContent=err.message;ready()}
   };
  }catch(err){view.textContent='清单无法读取：'+err.message}
 };
};
