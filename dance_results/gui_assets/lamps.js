/* Shared clear lamps and achievement presentation. No score decisions. */
(()=>{'use strict';
const states=new Set(['UNPLAYED','FAILED','CLEAR','AC','ACP','PENDING']);
const el=(tag,cls,text)=>{const n=document.createElement(tag);n.className=cls||'';if(text!==undefined)n.textContent=text;return n;};
let manualReduce=false;
try{manualReduce=localStorage.getItem('dance-motion')==='reduce';}catch(_e){}
const media=window.matchMedia('(prefers-reduced-motion: reduce)');
const visibility='IntersectionObserver'in window?new IntersectionObserver(entries=>{for(const e of entries)e.target.classList.toggle('fx-visible',e.isIntersecting);}):null;
function lampStatus(status){
 return status===null?'PENDING':states.has(status)?status:'UNPLAYED';
}
function observeLamp(node){
 if(visibility)visibility.observe(node);
 else node.classList.add('fx-visible');
}
function lampStrip(node,status){
 node.dataset.lamp=lampStatus(status);
 node.classList.add('lamp-strip-host');
 const strip=el('i','lamp-strip');
 strip.setAttribute('aria-hidden','true');
 if(node.tagName==='TR')node.firstElementChild.prepend(strip);
 else node.prepend(strip);
 observeLamp(node);
 return node;
}
function reduced(){return manualReduce||media.matches;}
function updateMotion(){document.documentElement.dataset.reducedMotion=String(reduced());for(const b of document.querySelectorAll('.motion-control')){b.textContent=manualReduce?'動畫：減少':media.matches?'動畫：依系統（減少）':'動畫：標準';b.setAttribute('aria-pressed',String(manualReduce));}}
function motionControl(){const b=el('button','motion-control');b.type='button';b.title='切換減少動畫偏好；系統偏好仍會生效';b.addEventListener('click',()=>{manualReduce=!manualReduce;try{localStorage.setItem('dance-motion',manualReduce?'reduce':'system');}catch(_e){}updateMotion();});return b;}
media.addEventListener('change',updateMotion);window.addEventListener('storage',e=>{if(e.key==='dance-motion'){manualReduce=e.newValue==='reduce';updateMotion();}});
function lamp(status,{compact=false,upgrade=false,failure=false}={}){
 const s=lampStatus(status);
 const n=el('span','clear-lamp lamp-'+s.toLowerCase()+(compact?' compact':'')+
  (upgrade&&s!=='FAILED'?' lamp-upgrade':'')+(failure&&s==='FAILED'?' lamp-failure':''));
 n.dataset.lamp=s;
 n.dataset.entryKind=upgrade&&s!=='FAILED'?'upgrade':failure&&s==='FAILED'?'failure':'entry';
 const markStart=e=>{
  if(e.target===n&&!e.pseudoElement){
   n.dataset.entryAnimation=e.animationName;
   n.removeEventListener('animationstart',markStart);
  }
 };
 n.addEventListener('animationstart',markStart);
 n.setAttribute('aria-label','?? '+s);
 n.append(el('i','lamp-core'),el('span','lamp-label',s));
 n.firstChild.setAttribute('aria-hidden','true');
 observeLamp(n);
 const settle=event=>{
  if(event.target!==n||event.pseudoElement)return;
  n.classList.add('lamp-settled');
  n.classList.remove('lamp-upgrade','lamp-failure');
  n.removeEventListener('animationend',settle);
 };
 n.addEventListener('animationend',settle);
 return n;
}
function when(record){if(!record?.played_at_source)return '時間未確認';const d=new Date(record.played_at);return Number.isNaN(d.valueOf())?'時間未確認':d.toLocaleString();}
function recordLink(record,songId,difficulty){const a=el('a','record-link','#'+record.record_id);a.href='/history?'+new URLSearchParams({record_id:record.record_id,...(songId?{song_id:songId}:{}),...(difficulty?{difficulty}: {})});a.setAttribute('aria-label','查看 Record #'+record.record_id+' 原始結算截圖');return a;}
function scorePanel(record,{animate=false}={}){
 const status=record.score_status||{ex_score:record.ex_score,max_score:record.max_status==='confirmed'?record.max_score:null,display:record.display,max_verified:record.max_status==='confirmed'};
 const n=el('div','score-status'),line=el('div','score-line');line.append(el('span','score-prefix','EX'),el('strong','score-number'+(animate?' score-refresh':''),status.ex_score??'—'));
 if(status.max_verified&&status.max_score>0)line.append(el('span','score-denominator','/ '+status.max_score));
 n.append(line,el('div','score-caption',status.comparison_paused?'比較暫停':status.max_verified?((status.max_policy==='custom-judged-v1'?'自訂 MAX · ':'')+(status.display||'MAX 未確認')):'MAX 未確認'));
 return n;
}
function updateSummary(summary,{animate=false}={}){
 const s=summary||{kind:'UNAVAILABLE',label:'更新證據未保存'},n=el('div','update-summary update-'+s.kind.toLowerCase()+(animate?' update-live':''));n.dataset.updateKind=s.kind;
 if(s.kind==='NO_UPDATE'||s.kind==='UNAVAILABLE'){
  n.append(el('span','update-badge quiet',s.label));if(s.reason)n.append(el('small','update-muted',s.reason==='comparison_paused'?'比較資格暫停':'缺少可比較的更新證據'));return n;
 }
 const badges=el('div','update-badges');if(s.kind==='DOUBLE_UPDATE')badges.append(el('span','update-badge double','DOUBLE UPDATE'));
 for(const k of ['score','lamp'])if(s[k])badges.append(el('span','update-badge '+k,s[k].label));n.append(badges);
 if(s.score){const t=s.score,line=el('div','update-transition score-transition');line.append(el('span','transition-label','SCORE'));
  if(!t.first)line.append(el('span','before-value',t.previous),el('span','update-arrow','→'));
  line.append(el('strong','after-value'+(animate?' score-refresh':''),t.current));
  if(t.delta!==null&&t.delta!==undefined)line.append(el('span','ex-gain'+(animate?' gain-live':''),'+'+t.delta+' EX'));n.append(line);
 }
 if(s.lamp){const t=s.lamp,line=el('div','update-transition lamp-transition');line.append(el('span','transition-label','LAMP'));
  if(!t.first)line.append(el('span','before-value',t.previous),el('span','update-arrow','→'));
  line.append(el('strong','after-value',t.current));n.append(line);
 }
 return n;
}
function bestPair(p,{songId,upgrade=false,scoreHighlight=false}={}){
 const pair=el('div','best-pair'),best=p.best_score_record,source=p.best_lamp_record;
 const score=el('section','best-card best-score');score.setAttribute('aria-label','My Best Score '+p.difficulty);score.append(el('span','best-eyebrow','MY BEST SCORE'));
 if(best){score.append(el('strong','best-number'+(scoreHighlight?' score-refresh':''),'EX '+best.ex_score),el('p','best-metric',p.comparison_paused?'比較暫停':best.display||'MAX 未確認'),el('p','best-date','取得 '+when(best)),recordLink(best,songId,p.difficulty),el('p','best-caption','當次 '+(best.lamp_status||'PENDING')));}else score.append(el('strong','best-number','—'),el('p','best-caption','尚無有效 EX'));
 const clear=el('section','best-card best-lamp');clear.setAttribute('aria-label','Best Clear Lamp '+p.difficulty);clear.append(el('span','best-eyebrow','BEST CLEAR LAMP'),lamp(p.best_lamp,{upgrade}));lampStrip(clear,p.best_lamp);
 if(source)clear.append(el('p','best-date','首次達成 '+when(source)),recordLink(source,songId,p.difficulty),el('p','best-metric','當次 EX '+source.ex_score));else clear.append(el('p','best-caption','尚未取得燈號'));
 pair.append(score,clear);return pair;
}
function chartProgress(p,{compact=false,upgrade=false,scoreHighlight=false,songId}={}){
 const n=el('div',compact?'chart-progress compact-progress':'chart-progress');n.dataset.difficulty=p.difficulty;if(compact)lampStrip(n,p.best_lamp);
 const head=el('div','progress-head');head.append(el('span','chart-label',p.difficulty));if(compact)head.append(lamp(p.best_lamp,{compact:true}));else head.append(el('span','chart-level','LV '+(p.game_level??'未設定')));n.append(head);
 if(compact){n.append(el('div','progress-score',p.best_ex_score===null?'BEST EX —':'BEST EX '+p.best_ex_score),el('div','progress-max',p.comparison_paused?'比較暫停':p.best_score_record?.display||'MAX 未確認'));}
 else{n.append(bestPair(p,{songId,upgrade,scoreHighlight}));if(p.comparison_paused)n.append(el('p','comparison-warning','完整基準衝突：暫停排名與最佳刷新，保留原成績。'));
 const last=p.latest_record;if(last)n.append(el('p','progress-note','最近 #'+last.record_id+' · EX '+last.ex_score+' · '+(last.lamp_status||'PENDING')));
 if(p.last_update)n.append(updateSummary(p.last_update));}
 return n;
}
function progressHistory({songId,difficulty='HARD'}={}){
 const section=el('section','progress-history');section.dataset.songId=songId;section.setAttribute('aria-label','Progress History / 成長履歷');section.append(el('span','eyebrow','PROGRESS HISTORY'),el('h3','','成長履歷'),el('p','progress-note','分數與燈號各自前進。更新標記來自當時提交證據。'));
 const controls=el('div','growth-controls'),chart=el('select',''),sort=el('select','');chart.setAttribute('aria-label','成長履歷難度');sort.setAttribute('aria-label','成長履歷排序');
 for(const value of ['EASY','NORMAL','HARD']){const o=el('option','',value);o.value=value;chart.append(o);}chart.value=difficulty;
 for(const [v,t] of [['desc','最新 → 最舊'],['asc','最舊 → 最新']]){const o=el('option','',t);o.value=v;sort.append(o);}
 const list=el('ol','growth-timeline'),count=el('p','growth-count'),pager=el('div','growth-pager'),prev=el('button','','上一頁履歷'),next=el('button','','下一頁履歷');prev.type=next.type='button';pager.append(prev,next);controls.append(chart,sort);section.append(controls,count,list,pager);
 let offset=0,request=null,generation=0;
 async function load(){if(!section.isConnected)return;if(request)request.abort();request=new AbortController();const g=++generation;prev.disabled=next.disabled=true;list.setAttribute('aria-busy','true');
  try{const args=new URLSearchParams({song_id:songId,difficulty:chart.value,order:sort.value,offset,limit:20}),r=await fetch('/api/progress-history?'+args,{headers:{'X-Dance-Token':document.querySelector('meta[name="dance-token"]').content},signal:request.signal}),data=await r.json();if(!r.ok)throw new Error(data.error||'履歷讀取失敗');if(g!==generation||!section.isConnected)return;
   list.replaceChildren();count.textContent=data.total?'第 '+(offset+1)+'–'+(offset+data.records.length)+' / '+data.total+' 次遊玩':'尚無正式遊玩紀錄';
   for(const record of data.records){const entry=el('li','growth-entry'+(['NO_UPDATE','UNAVAILABLE'].includes(record.update_summary.kind)?' growth-quiet':''));entry.dataset.recordId=record.record_id;entry.dataset.updateKind=record.update_summary.kind;
    const top=el('div','growth-top');top.append(el('time','',record.played_at_verified===false?'時間未確認':when(record)),recordLink(record,songId,record.difficulty));entry.append(top,scorePanel(record),lamp(record.lamp_status,{compact:true}),updateSummary(record.update_summary));list.append(entry);
   }prev.disabled=offset===0;next.disabled=offset+data.records.length>=data.total;
  }catch(e){if(e.name!=='AbortError'&&g===generation){list.replaceChildren(el('li','progress-note',e.message));count.textContent='履歷無法載入，可切換難度或重試。';}}
  finally{if(g===generation)list.setAttribute('aria-busy','false');}
 }
 section.addEventListener('dance-progress-refresh', load);
 chart.addEventListener('change',()=>{offset=0;load();});sort.addEventListener('change',()=>{offset=0;load();});prev.addEventListener('click',()=>{offset=Math.max(0,offset-20);load();});next.addEventListener('click',()=>{offset+=20;load();});queueMicrotask(load);return section;
}
function receiptKey(r){return [r.catalog_path||(r.evidence&&r.evidence.review&&r.evidence.review.catalog_path),r.session_id,r.review_id,r.record_id].join('|');}
const seen=new Set();
const notificationQueue=[];
function notify(record,{idempotent=false}={}){
 const key=receiptKey(record);if(idempotent||seen.has(key))return false;try{if(localStorage.getItem('dance-celebrated:'+key))return false;localStorage.setItem('dance-celebrated:'+key,'1');}catch(_e){}seen.add(key);
 notificationQueue.push(record);
 if(notificationQueue.length===1)showNotification();
 return true;
}
function showNotification(){
 while(notificationQueue.length){
  try{
   const record=notificationQueue[0];
   const summary=record.update_summary||{kind:'UNAVAILABLE',label:'更新證據未保存'},f=record.achievement_feedback||{},changed=!!(summary.score||summary.lamp),primary=summary.kind==='DOUBLE_UPDATE'?'DOUBLE UPDATE':changed?summary.label:'SAVED',tone=changed?(f.tone||'SCORE'):'SAVED';
   let area=document.getElementById('achievement-notifications');if(!area){area=el('div','achievement-notifications');area.id='achievement-notifications';area.setAttribute('role','status');area.setAttribute('aria-live','polite');area.setAttribute('aria-atomic','true');document.body.append(area);}
   area.replaceChildren();const card=el('div','achievement-toast tone-'+tone.toLowerCase()+(summary.kind==='DOUBLE_UPDATE'?' double-update':''));card.dataset.recordId=record.record_id;
   card.append(el('span','achievement-kicker','RESULT CONFIRMED'),el('strong','achievement-title',primary),el('p','achievement-song',record.title+' · '+(record.difficulty || '難度未設定')),scorePanel(record,{animate:!!summary.score}),updateSummary(summary,{animate:changed}),lamp(record.lamp_status,{upgrade:!!summary.lamp&&record.lamp_status!=='FAILED',failure:changed&&record.lamp_status==='FAILED'}));
   area.dataset.lastEvent=JSON.stringify({record_id:record.record_id,primary,events:f.events||['SAVED'],updateKind:summary.kind,tone,motion:reduced()?'reduce':'standard'});area.dataset.deliveryCount=String(Number(area.dataset.deliveryCount||0)+1);area.dataset.animationTrace='[]';
   card.addEventListener('animationstart',event=>{const trace=JSON.parse(area.dataset.animationTrace);trace.push({name:event.animationName,pseudo:event.pseudoElement||'',target:event.target===card?'toast':event.target.classList.contains('clear-lamp')?'lamp':'label'});area.dataset.animationTrace=JSON.stringify(trace);if(event.target.classList.contains('clear-lamp')&&!event.pseudoElement)area.dataset.lastLampAnimation=event.animationName;if(event.target===card)area.dataset.lastToastAnimation=event.animationName;});area.append(card);
   setTimeout(()=>{card.classList.add('toast-leave');setTimeout(()=>{
    card.remove();
    notificationQueue.shift();
    if(notificationQueue.length)showNotification();
   },220);},2800);
   return;
  }catch(_error){
   notificationQueue.shift();
  }
 }
}
function receiptSummary(container,record,{animate=false}={}){
 const summary=record.update_summary||{kind:'UNAVAILABLE',label:'更新證據未保存'};container.replaceChildren();container.dataset.sessionId=record.session_id;container.hidden=false;
 container.append(el('span','eyebrow','FORMAL RESULT / #'+record.record_id),el('h3','',record.title+' · '+(record.difficulty || '難度未設定')),lamp(record.lamp_status,{upgrade:animate&&!!summary.lamp&&record.lamp_status!=='FAILED',failure:animate&&!!summary.score&&record.lamp_status==='FAILED'}),scorePanel(record,{animate:animate&&!!summary.score}),updateSummary(summary,{animate}));
 if(record.comparison?.ranking_paused)container.append(el('p','note-exception-mark','跨場次基準衝突：本次不更新最佳或排名。'));
 const f=record.achievement_feedback;if(f)container.append(el('p','progress-note','提交當時 BEST EX '+(f.best_ex_score??'—')+' · 最佳燈號 '+f.best_lamp+'（與當次燈號分開）'));
 const link=el('a','page-link','查看歌曲與各難度最佳成績');const root=record.root_id||record.evidence?.song?.root_id;link.href='/?'+new URLSearchParams({song_id:record.song_id,...(root?{root_id:root}:{})});container.append(link);
}
function dispatchRecordUpdate(record,{broadcast=false}={}){
 const detail={
  song_id:record.song_id,
  difficulty:record.difficulty,
  record_id:record.record_id,
  update_summary:record.update_summary,
  at:Date.now(),
  database:record.catalog_path||(record.evidence&&record.evidence.review&&record.evidence.review.catalog_path),
 };
 if(broadcast){try{localStorage.setItem('dance-record-update',JSON.stringify(detail));}catch(_e){}}
 window.dispatchEvent(new CustomEvent('dance-record-update',{detail}));
}
function publish(record,{idempotent=false}={}){if(!idempotent)dispatchRecordUpdate(record,{broadcast:true});}
window.DanceFX={lamp,lampStrip,chartProgress,bestPair,scorePanel,updateSummary,progressHistory,notify,receiptSummary,publish,dispatchRecordUpdate,reduced,motionControl};
document.addEventListener('DOMContentLoaded',()=>{if(visibility)new MutationObserver(records=>{for(const r of records)for(const removed of r.removedNodes){if(removed.nodeType!==1||removed.isConnected)continue;if(removed.matches('.clear-lamp, .lamp-strip-host'))visibility.unobserve(removed);for(const l of removed.querySelectorAll('.clear-lamp, .lamp-strip-host'))visibility.unobserve(l);}}).observe(document.body,{childList:true,subtree:true});const actions=document.querySelector('[data-motion-slot]')||document.querySelector('.header-actions');if(actions&&!actions.querySelector('.motion-control'))actions.prepend(motionControl());updateMotion();});
})();

// Both automatic import paths publish receipts only after the transaction commits.
(() => {
 const token = document.querySelector('meta[name="dance-token"]')?.content;
 if (!token) return;
 const delivered = new Set();
 let polling = false, initialized = false;
 async function poll() {
  if (polling) return;
  polling = true;
  try {
   const response = await fetch('/api/reviews/storage', {headers: {'X-Dance-Token': token}});
   if (!response.ok) return;
   const data = await response.json();
   window.dispatchEvent(new CustomEvent('dance-storage-status', {detail: data}));
   for (const record of data.events || []) {
    const key = [record.catalog_path, record.record_id].join('|');
    if (delivered.has(key)) continue;
    // Establish the opening snapshot without celebrating an older batch.
    if (initialized && document.visibilityState !== 'visible') continue;
    delivered.add(key);
    if (!initialized) continue;
    DanceFX.notify(record);
    // Every page refreshes, even when another tab already celebrated this play.
    DanceFX.dispatchRecordUpdate(record);
   }
   initialized = true;
  } catch (_error) {
   // The existing polling loop retries a transient connection failure.
  } finally {
   polling = false;
  }
 }
 poll();
 setInterval(poll, 2000);
})();
