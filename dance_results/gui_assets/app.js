'use strict';
const $=id=>document.getElementById(id);
const sessionToken=document.querySelector('meta[name="dance-token"]').content;
const state={rootId:null,scanRootId:null,locationId:null,packId:null,category:'all',label:'全部歌曲',query:'',order:'asc',offset:0,total:0,limit:60,selectedId:null,song:null,packs:null,scanning:false,importing:false,canScan:false};
let listRequest=null,detailRequest=null,searchTimer=null,scanTimer=null;
let progressRefreshing=false,lastProgressRefresh=0,databasePath='';
const EMPTY_LIBRARY='歌曲庫目前是空的：遊戲資料夾設定好之後，官方歌與外部歌會自動匯入。';
const lampAnimations=new Map();
function node(tag,className,text){const e=document.createElement(tag);if(className)e.className=className;if(text!==undefined)e.textContent=text;return e;}
function text(value){return value===null||value===undefined||value===''?'未設定':String(value);}
function message(value,type=''){const e=$('message');e.textContent=value;e.className='message '+type;e.hidden=!value;}
async function api(path,body,signal){
  const response=await fetch(path,{method:body===undefined?'GET':'POST',headers:{'X-Dance-Token':sessionToken,...(body===undefined?{}:{'Content-Type':'application/json'})},body:body===undefined?undefined:JSON.stringify(body),signal});
  const result=await response.json();if(!response.ok)throw new Error(result.error||'操作失敗，請重新載入。');return result;
}
function reportError(error){if(error.name!=='AbortError')message(error.message||'無法連接本機服務，請確認啟動視窗仍開啟。','error');}
function dirty(){return [...document.querySelectorAll('.difficulty-card')].some(f=>f.dataset.dirty==='true');}
function mayLeave(){return !dirty()||window.confirm('尚有未儲存的難度資料。要放棄這些變更嗎？');}
function clearDetail(){if(detailRequest)detailRequest.abort();state.selectedId=null;state.song=null;$('details').replaceChildren(emptyDetail());}
function emptyDetail(){const e=node('div','empty-details');e.append(node('span','record-symbol','◎'),node('h2','','選擇一首歌曲'),node('p','','查看來源資訊，並設定遊戲內的 EASY、NORMAL、HARD。'),node('span','small-tag','未知 LV 與音符數顯示「未設定」'));return e;}
// Sidebar: one tree. 全部 / Recent / 官方, then 自製歌曲 → song folders (遊戲 ADDON, StepMania…) → packs.
// What is expanded is remembered per browser.
const SIDEBAR_KEY='dance-sidebar-open';
function sidebarState(){try{return JSON.parse(localStorage.getItem(SIDEBAR_KEY))||{};}catch(_e){return {};}}
function isOpen(key,fallback){const saved=sidebarState();return key in saved?saved[key]===true:fallback;}
function setOpen(key,open){const saved=sidebarState();saved[key]=open;try{localStorage.setItem(SIDEBAR_KEY,JSON.stringify(saved));}catch(_e){}renderPacks();}
function navItem(label,count,{packId=null,category='all',locationId=null,depth=0,toggleKey=null,open=false}={}){
  const row=node('div','nav-row depth-'+depth);
  if(toggleKey){
    const toggle=node('button','nav-chevron'+(open?' open':''));toggle.type='button';
    toggle.setAttribute('aria-expanded',String(open));toggle.setAttribute('aria-label',(open?'收合 ':'展開 ')+label);
    toggle.append(node('span','chevron'));toggle.addEventListener('click',()=>setOpen(toggleKey,!open));row.append(toggle);
  }else row.append(node('span','nav-chevron-space'));
  const b=node('button','nav-item');b.type='button';
  const selected=state.packId===packId&&state.category===category&&state.locationId===locationId;
  b.classList.toggle('selected',selected);b.setAttribute('aria-pressed',String(selected));b.title=label;
  b.append(node('span','nav-label',label),node('span','nav-count',typeof count==='number'?count.toLocaleString():count));
  b.addEventListener('click',()=>{if(!mayLeave())return;clearDetail();state.packId=packId;state.category=category;state.locationId=locationId;state.label=label;state.offset=0;renderPacks();loadSongs().catch(reportError);});
  row.append(b);return row;
}
function songFolders(packs){
  const folders=new Map();
  for(const pack of packs){
    const id=pack.root_id===undefined?0:pack.root_id;
    if(!folders.has(id))folders.set(id,{rootId:id,label:pack.root_label||'遊戲 ADDON',packs:[],count:0});
    const folder=folders.get(id);folder.packs.push(pack);folder.count+=pack.song_count;
  }
  return [...folders.values()];
}
function renderPacks(){
  const data=state.packs;if(!data)return;const nav=$('pack-list');nav.replaceChildren();
  nav.append(navItem('全部歌曲',data.song_count),navItem('Recent Update','最新 30',{category:'recent'}),navItem('官方歌曲',data.categories.official,{category:'official'}));
  const customOpen=isOpen('custom',true);
  nav.append(navItem('自製歌曲',data.categories.custom,{category:'custom',toggleKey:data.packs.length?'custom':null,open:customOpen}));
  if(customOpen){
    const folders=songFolders(data.packs);
    if(folders.length>1){
      for(const folder of folders){
        const key='folder:'+folder.rootId,open=isOpen(key,false);
        nav.append(navItem(folder.label,folder.count,{category:'custom',locationId:folder.rootId,depth:1,toggleKey:folder.packs.length?key:null,open}));
        if(open)for(const pack of folder.packs)nav.append(navItem(pack.name,pack.song_count,{packId:pack.pack_id,category:'custom',depth:2}));
      }
    }else for(const pack of data.packs)nav.append(navItem(pack.name,pack.song_count,{packId:pack.pack_id,category:'custom',depth:1}));
  }
  if(data.categories.unknown)nav.append(navItem('來源未知',data.categories.unknown,{category:'unknown'}));
  $('totals').replaceChildren(node('strong','',data.pack_count.toLocaleString()),document.createTextNode(' Packs'),node('strong','',data.song_count.toLocaleString()),document.createTextNode(' Songs'),node('strong','',data.source_count.toLocaleString()),document.createTextNode(' Sources'));
}
async function loadPacks(){const rootId=state.rootId;const data=await api('/api/packs'+(rootId?'?root_id='+rootId:''));if(rootId!==state.rootId)return;state.packs=data;renderPacks();}
async function loadSongs(){
  if(state.category==='recent')return loadRecent();
  if(listRequest)listRequest.abort();const request=new AbortController();listRequest=request;
  $('search').disabled=false;$('sort').disabled=false;$('list-title').textContent=state.label;$('song-list').setAttribute('aria-busy','true');if(!$('song-list').children.length)$('song-list').replaceChildren(node('p','list-empty','正在查詢歌曲…'));
  const params=new URLSearchParams({q:state.query,category:state.category,order:state.order,offset:state.offset,limit:state.limit});const scope=state.locationId||state.rootId;if(scope)params.set('root_id',scope);
  if(state.packId!==null)params.set('pack_id',state.packId);
  try{
    const data=await api('/api/songs?'+params,undefined,request.signal);if(listRequest!==request)return;
    state.total=data.total;$('result-count').textContent='共 '+data.total.toLocaleString()+' 首歌曲';const list=$('song-list');list.replaceChildren();
    if(!data.songs.length)list.append(node('p','list-empty',state.category==='official'?'官方歌曲尚未匯入。\n沒有推測或套用官方歌曲分類。':'沒有符合條件的歌曲。\n可以換個歌名、Pack 或資料夾搜尋。'));
    for(const song of data.songs){
      const row=node('button','song-row');row.type='button';row.dataset.songId=song.song_id;row.setAttribute('role','listitem');row.setAttribute('aria-label',song.display_title+'，Song ID '+song.song_id+'，'+song.relative_folder);
      row.classList.toggle('selected',song.song_id===state.selectedId);
      const heading=node('div','song-title-row'),title=node('span','song-title',song.display_title);
      if(song.latest_status!=='ok')title.append(node('span','status-tag','待確認'));
      const official=song.source_type==='official';
      heading.append(title,node('span','song-id','#'+song.song_id));row.append(heading,node('div','song-pack',official?'官方歌曲 · K · fileId '+song.file_id:(song.pack_name||'未歸類 Pack')),node('div','song-folder',official?song.gn:song.relative_folder));
      if(!song.is_present)row.append(node('span','status-tag','來源暫時未找到，資料保留'));
      const progress=node('div','song-progress');for(const p of song.progress||[])progress.append(DanceFX.chartProgress(p,{compact:true}));row.append(progress);row.addEventListener('click',()=>selectSong(song.song_id).catch(reportError));list.append(row);
    }
    $('page-info').textContent=data.total?'顯示 '+(data.offset+1)+'–'+(data.offset+data.songs.length)+' / '+data.total.toLocaleString():'0 首歌曲';
    $('previous').disabled=state.offset===0;$('next').disabled=state.offset+state.limit>=data.total;list.scrollTop=0;
  }finally{if(listRequest===request)$('song-list').setAttribute('aria-busy','false');}
}
function metadataLine(list,label,value){list.append(node('dt','',label),node('dd','',text(value)));}
function sourceName(value){return ({manual:'手動設定',game_screen:'遊戲畫面',completed_judgment_sum:'完成後判定合計',official_csv:'官方 CSV'})[value]||'未設定';}
function cardValues(form){return JSON.stringify([form.elements.exists.checked,form.elements.level.value,form.elements.notes.value]);}
function refreshCard(form){
  const exists=form.elements.exists.checked,busy=state.scanning||state.importing||form.dataset.saving==='true';
  form.elements.exists.disabled=form.dataset.existed==='true'||busy;
  form.elements.level.disabled=!exists||busy;form.elements.notes.disabled=!exists||busy;form.querySelector('button').disabled=!exists||busy;
  form.dataset.dirty=String(cardValues(form)!==form.dataset.initial);
}
function nullableNumber(input){if(input.value==='')return null;const value=Number(input.value);if(!Number.isSafeInteger(value))throw new Error('LV 與音符數必須是整數。');return value;}
function difficultyCard(song,data){
  if(song.official){
    const card=node('section','official-difficulty');card.dataset.label=data.difficulty;card.append(node('h3','',data.difficulty));
    const meta=node('dl','metadata');metadataLine(meta,'歌曲 LV',data.game_level);metadataLine(meta,'總音符數',data.total_notes);metadataLine(meta,'參考 MAX 候選（未驗證）',data.max_score);card.append(meta);
    card.append(node('p','provenance',data.not_provided?'未設定 · CSV 未提供有效難度（0／空白）；未建立難度資料。':'來源：官方 CSV · K 鍵盤譜'));return card;
  }
  const form=node('form','difficulty-card');form.dataset.label=data.difficulty;form.dataset.existed=String(data.exists);form.dataset.saving='false';
  const header=node('header'),existsLabel=node('label','exists-check'),exists=node('input');exists.type='checkbox';exists.name='exists';exists.checked=data.exists;exists.setAttribute('aria-label',data.difficulty+' 難度已確認存在');
  existsLabel.append(exists,document.createTextNode(data.exists?'已建立':'確認此難度存在'));header.append(node('h3','',data.difficulty),existsLabel);form.append(header);
  const inputs=node('div','difficulty-inputs');
  for(const [key,label,value,min] of [['level','歌曲 LV',data.game_level,0],['notes','總音符數',data.total_notes,1]]){
    const wrapper=node('label','',label),input=node('input');input.type='number';input.step='1';input.min=String(min);input.max='9007199254740991';input.name=key;input.value=value===null?'':String(value);input.placeholder='未設定';input.setAttribute('aria-label',data.difficulty+' '+label);wrapper.append(input);inputs.append(wrapper);
  }
  const footer=node('div','card-footer'),save=node('button','save-difficulty','儲存 '+data.difficulty);save.type='submit';
  footer.append(node('span','provenance','LV：'+sourceName(data.level_source)+' · 音符：'+sourceName(data.notes_source)),save);
  form.append(inputs,node('p','max-score','依參考數的 MAX 候選（未驗證）：'+text(data.max_score)),footer);const error=node('p','card-error');error.hidden=true;error.setAttribute('role','alert');form.append(error);
  form.dataset.initial=cardValues(form);form.dataset.dirty='false';
  form.addEventListener('input',()=>refreshCard(form));form.addEventListener('change',()=>refreshCard(form));refreshCard(form);
  form.addEventListener('submit',async event=>{
    event.preventDefault();if(!form.reportValidity())return;
    try{
      const body={root_id:song.root_id,song_id:song.song_id,song_folder:song.relative_folder,difficulty:data.difficulty,exists:exists.checked,game_level:nullableNumber(form.elements.level),total_notes:nullableNumber(form.elements.notes),token:data.token};
      form.dataset.saving='true';error.hidden=true;refreshCard(form);
      const saved=await api('/api/difficulty',body);
      if(state.selectedId===song.song_id&&(state.rootId===null||state.rootId===song.root_id)&&form.isConnected){
        state.song.difficulties=state.song.difficulties.map(d=>d.difficulty===saved.difficulty?saved:d);form.replaceWith(difficultyCard(song,saved));const p=state.song.progress?.find(p=>p.difficulty===saved.difficulty);if(p){Object.assign(p,{difficulty_id:saved.difficulty_id,game_level:saved.game_level,total_notes:saved.total_notes,notes_verified:saved.notes_verified});const card=$('details').querySelector('.song-result-panel .chart-progress[data-difficulty='+saved.difficulty+']');if(card){const replacement=DanceFX.chartProgress(p,{songId:song.song_id});replacement.hidden=card.hidden;card.replaceWith(replacement);}}
      }
      message('已儲存 '+data.difficulty+' · Song ID '+song.song_id+' · '+song.relative_folder,'success');
    }catch(problem){if(form.isConnected){error.textContent=problem.message;error.hidden=false;}else reportError(problem);}
    finally{form.dataset.saving='false';if(form.isConnected)refreshCard(form);}
  });return form;
}
function buildProgressPanel(song,update=null){
 const scores=node('section','song-result-panel');scores.setAttribute('aria-label','各難度正式最佳成績');scores.append(node('h3','','個人最佳 / PERSONAL BESTS'));const choice=node('select','best-difficulty-select');choice.setAttribute('aria-label','最佳紀錄難度');scores.append(choice);const cards=[];
 for(const p of [...song.progress||[]].sort((a,b)=>b.record_count-a.record_count)){
  const key=databasePath+':'+song.song_id+':'+p.difficulty+':'+update?.record_id,live=update?.song_id===song.song_id&&update?.difficulty===p.difficulty;
  let seen=false;try{seen=sessionStorage.getItem('dance-progress-fx:'+key)==='1';}catch(_e){}
  const summary=live&&!seen?update.update_summary:null;
  const card=DanceFX.chartProgress(p,{songId:song.song_id,upgrade:!!summary?.lamp&&p.best_lamp_record_id===update?.record_id,scoreHighlight:!!summary?.score&&p.best_score_record?.record_id===update?.record_id});cards.push(card);scores.append(card);const o=node('option','',p.difficulty);o.value=p.difficulty;choice.append(o);
  if(summary)try{sessionStorage.setItem('dance-progress-fx:'+key,'1');}catch(_e){}
 }if(update?.song_id===song.song_id&&['EASY','NORMAL','HARD'].includes(update.difficulty))choice.value=update.difficulty;const show=()=>{for(const c of cards)c.hidden=c.dataset.difficulty!==choice.value;};choice.addEventListener('change',show);show();return scores;
}
function renderDetail(song,update=null){
  const target=$('details');target.replaceChildren();const top=node('div','detail-top'),reload=node('button','','重新載入');reload.type='button';reload.setAttribute('aria-label','重新載入歌曲資訊');reload.addEventListener('click',()=>{if(mayLeave())selectSong(song.song_id,true).catch(reportError);});top.append(node('span','eyebrow','SONG DETAILS'),reload);
  target.append(top,node('h2','detail-title',song.display_title),node('p','artist-list',song.artists.length?song.artists.join(' / '):'Artist 未設定'));
  const meta=node('dl','metadata');metadataLine(meta,'Song ID',song.song_id);metadataLine(meta,'Pack',song.official?'不適用 · 熱舞官方譜面':(song.pack_name||'未歸類'));metadataLine(meta,'SongFolder',song.official?'不適用':song.folder_name);metadataLine(meta,'來源格式',song.official?'官方 CSV':song.formats.map(f=>f.toUpperCase()).join(' / '));
  if(song.official){metadataLine(meta,'fileId',song.official.file_id);metadataLine(meta,'mode',song.official.mode);metadataLine(meta,'BPM',song.official.bpm_raw);metadataLine(meta,'gn',song.official.gn);}
  if(song.latest_title!==song.saved_title&&song.saved_title)metadataLine(meta,'顯示名稱','沿用上次可確認名稱；原始候選保留於來源資訊');target.append(meta,node('div','full-path',song.official?'來源 CSV：'+song.official.source_name+' · 記錄 '+song.official.csv_record_number:song.original_path));
  const warnings=song.diagnostics.filter(d=>d.severity!=='info');if(warnings.length){const list=node('ul','detail-diagnostics');for(const d of warnings)list.append(node('li','',d.message));target.append(list);}
  if(!song.is_present)target.append(node('p','detail-diagnostics','來源暫時未找到；歌曲與歷史資料仍保留。'));
  const editHeading=node('div','edit-heading');editHeading.append(node('h3','','遊戲難度'),node('span','',song.official?'官方遊戲資料 · 來源檢視':'僅手動輸入遊戲實際數值'));target.append(editHeading,node('div','identity-note',(song.official?'目前查看':'目前編輯')+' Song ID '+song.song_id+'\n'+(song.official?'fileId '+song.official.file_id+' · '+song.official.gn:song.relative_folder)));
  for(const difficulty of song.difficulties)target.append(difficultyCard(song,difficulty));
  const sources=node('details','source-details');sources.append(node('summary','','原始來源資訊（'+song.sources.length+'）'));
  if(song.official){sources.querySelector('summary').textContent='官方來源識別與匯入證據';sources.append(node('pre','official-evidence','fileId：'+song.official.file_id+'\nmode：K\ngn：'+song.official.gn+'\nCSV SHA-256：'+song.official.csv_sha256+'\n原始資料：'+JSON.stringify(song.official.raw,null,2)));}
  for(const s of song.sources){
    const file=node('details','source-file');file.append(node('summary','',s.format.toUpperCase()+' · '+s.relative_path.split(/[\\/]/).pop()));
    file.append(node('pre','',[
      'Source ID：'+s.source_id,'Title：'+text(s.title),'Subtitle：'+text(s.subtitle),'Artist：'+text(s.artist),
      ...(s.format==='dwi'?['來源 BPM（基本原值）：'+text(s.source_bpm),'來源 BPM 候選原值：'+JSON.stringify(s.source_bpm_fields)]:[]),
      '上次可確認 Title：'+text(s.saved_title),'上次可確認 Artist：'+text(s.saved_artist),
      '原始路徑：'+s.original_path,'編碼：'+text(s.encoding),'欄位編碼：'+JSON.stringify(s.metadata_encodings),
      '原始名稱欄位：'+JSON.stringify(s.title_fields),'原始 Artist 欄位：'+JSON.stringify(s.artist_fields),
      ...(Object.keys(s.metadata_candidates).length?['解碼候選（尚未採用）：'+JSON.stringify(s.metadata_candidates)]:[]),
      ...s.diagnostics.map(d=>d.message),...(!s.is_present?['來源暫時未找到，原資料保留。']:[])
    ].join('\n')));sources.append(file);
  }
  const scores=buildProgressPanel(song,update);target.insertBefore(scores,meta);const played=[...song.progress||[]].sort((a,b)=>b.record_count-a.record_count)[0];target.insertBefore(DanceFX.progressHistory({songId:song.song_id,difficulty:played?.difficulty||'HARD'}),meta);target.append(sources);
}
async function selectSong(id,force=false,update=null){
  if(!force&&id===state.selectedId&&state.song)return;if(!force&&!mayLeave())return;
  if(detailRequest)detailRequest.abort();const request=new AbortController();detailRequest=request;
  const same=id===state.selectedId;state.selectedId=id;state.song=null;const rootId=state.rootId;if(!same)$('details').replaceChildren(node('p','list-empty','載入歌曲資訊…'));else $('details').classList.add('refreshing');
  for(const row of document.querySelectorAll('.song-row'))row.classList.toggle('selected',Number(row.dataset.songId)===id);
  try{const song=await api('/api/songs/'+id+(rootId?'?root_id='+rootId:''),undefined,request.signal);if(state.selectedId!==id||state.rootId!==rootId)return;state.song=song;renderDetail(song,update);$('details').classList.remove('refreshing');}
  catch(error){$('details').classList.remove('refreshing');if(error.name!=='AbortError'&&state.selectedId===id){$('details').replaceChildren(node('p','list-empty',error.message));throw error;}}
}
function setScanning(value){state.scanning=value;$('scan-button').disabled=value||state.importing||!state.canScan;$('scan-button').textContent=value?'↻ 更新中…':'↻ 更新歌曲庫';for(const form of document.querySelectorAll('.difficulty-card'))refreshCard(form);}
function setupRoots(roots){
  const current=roots.find(r=>r.root_id===state.rootId)||roots[0];$('root').replaceChildren();
  for(const root of roots){const option=node('option','',root.root_kind==='official_catalog'?'熱舞官方譜面':root.path);option.value=String(root.root_id);option.dataset.kind=root.root_kind;$('root').append(option);}
  const scanRoot=roots.find(r=>r.root_kind==='filesystem');
  state.rootId=null;state.scanRootId=scanRoot?scanRoot.root_id:null;state.canScan=!!scanRoot;if(current)$('root').value=String(current.root_id);setScanning(state.scanning);
}
async function pollScan(){
  const status=await api('/api/scan'),box=$('scan-status');
  if(status.status==='running'){setScanning(true);box.hidden=false;box.className='scan-status running';box.textContent='正在更新歌曲庫 · 已讀取 '+status.processed+' 個來源'+(status.current_file?' · '+status.current_file:'');scanTimer=setTimeout(()=>pollScan().catch(error=>{setScanning(false);reportError(error);}),900);return;}
  if(status.status==='completed'){
    const c=status.changes;box.hidden=false;box.className='scan-status';box.textContent='更新完成 · 新增歌曲 '+c.new_songs+' · 新增來源 '+c.new_sources+' · 更新歌曲 '+c.updated_songs+' · 更新來源 '+c.updated_sources+' · 警告 '+status.warning_count+(status.complete_scan?'':' · 未完整掃描，未觀測來源保留');
    try{await loadPacks();await loadSongs();if(state.selectedId)await selectSong(state.selectedId,true);}finally{setScanning(false);}
  }else{setScanning(false);if(status.status==='failed'){box.hidden=false;box.textContent='更新失敗：'+status.message;message('更新沒有完成；請檢查來源路徑與啟動視窗。','error');}}
}
$('search').addEventListener('input',()=>{clearTimeout(searchTimer);searchTimer=setTimeout(()=>{state.query=$('search').value;state.offset=0;loadSongs().catch(reportError);},160);});
$('clear-search').addEventListener('click', () => {
  if (!mayLeave()) return;
  clearTimeout(searchTimer);
  clearDetail();
  Object.assign(state, {
    rootId: null,
    locationId: null,
    packId: null,
    category: 'all',
    label: '全部歌曲',
    query: '',
    order: 'asc',
    offset: 0,
  });
  $('search').value = '';
  $('sort').value = 'asc';
  history.replaceState(null, '', location.pathname);
  Promise.all([loadPacks(), loadSongs()]).catch(reportError);
});
$('sort').addEventListener('change',()=>{state.order=$('sort').value;state.offset=0;loadSongs().catch(reportError);});
$('previous').addEventListener('click',()=>{state.offset=Math.max(0,state.offset-state.limit);loadSongs().catch(reportError);});
$('next').addEventListener('click',()=>{state.offset+=state.limit;loadSongs().catch(reportError);});
$('root').addEventListener('change',()=>{if(!mayLeave()){$('root').value=String(state.rootId);return;}clearDetail();state.rootId=Number($('root').value);state.canScan=$('root').selectedOptions[0].dataset.kind==='filesystem';setScanning(state.scanning);state.packId=null;state.category='all';state.label='全部歌曲';state.offset=0;Promise.all([loadPacks(),loadSongs()]).catch(reportError);});
$('scan-button').addEventListener('click',async()=>{if(!mayLeave())return;try{message('');setScanning(true);
  const game=await api('/api/game');
  if(game.game_dir){const status=await api('/api/game/sync',{});const run=status.last_run||{};await reloadLibrary();setScanning(false);
    if(status.state==='error')message('從遊戲同步失敗：'+(status.last_error||''),'error');
    else message('已從遊戲同步 · '+(run.imported?'新增 '+run.imported+' 筆成績':'沒有新成績')+(run.catalog_changed?' · 歌單已更新':' · 歌單沒有變動'),'success');return;}
  await api('/api/scan',{root_id:state.scanRootId});await pollScan();}catch(error){setScanning(false);reportError(error);}});
window.addEventListener('beforeunload',event=>{if(dirty()){event.preventDefault();event.returnValue='';}});
async function start(){
  const bootstrap=await api('/api/bootstrap');databasePath=bootstrap.database;$('database-path').textContent=bootstrap.database;$('database-path').title=bootstrap.database;
  setupRoots(bootstrap.roots);
  if(!bootstrap.roots.length){$('totals').textContent='歌曲庫是空的';message(EMPTY_LIBRARY);return;}
  await Promise.all([loadPacks(),loadSongs()]);
  const requested=Number(new URLSearchParams(location.search).get('song_id'));if(Number.isSafeInteger(requested)&&requested>0)await selectSong(requested);
  const scan=await api('/api/scan');if(scan.status==='running')await pollScan();
}
start().catch(error=>{reportError(error);$('scan-button').disabled=true;});
// 遊戲資料夾模式：歌單在背景同步，進來了就直接載入；新成績進來就刷新燈號。
async function reloadLibrary(){
  const bootstrap=await api('/api/bootstrap');setupRoots(bootstrap.roots);if(!bootstrap.roots.length)return;
  if($('message').textContent===EMPTY_LIBRARY)message('');
  await Promise.all([loadPacks(),loadSongs()]);
}
document.addEventListener('game-catalog-updated',()=>{if(!state.scanning&&!state.importing)reloadLibrary().catch(reportError);});
document.addEventListener('game-results-imported',()=>refreshProgress());

async function refreshProgress(update){if(!state.packs||progressRefreshing||state.scanning||state.importing)return;if(update&&update.database&&update.database!==databasePath)return;progressRefreshing=true;try{const scroll=$('song-list').scrollTop;await loadSongs();$('song-list').scrollTop=scroll;if(state.selectedId){if(!dirty())await selectSong(state.selectedId,true,update);else{const id=state.selectedId,root=state.rootId;const fresh=await api('/api/songs/'+id+'?root_id='+root);if(id===state.selectedId&&root===state.rootId&&state.song){state.song.progress=fresh.progress;const panel=$('details').querySelector('.song-result-panel');if(panel)panel.replaceWith(buildProgressPanel(fresh,update));}}}lastProgressRefresh=Date.now();}catch(e){reportError(e);}finally{progressRefreshing=false;}}
window.addEventListener('storage',e=>{if(e.key==='dance-record-update'&&e.newValue){try{refreshProgress(JSON.parse(e.newValue));}catch(_e){}}});
window.addEventListener('dance-record-update',e=>refreshProgress(e.detail));
window.addEventListener('focus',()=>{if(Date.now()-lastProgressRefresh>15000)refreshProgress();});

async function loadRecent(){
 if(listRequest)listRequest.abort();const request=new AbortController();listRequest=request;
 $('search').disabled=true;$('sort').disabled=true;$('list-title').textContent='Recent Update';$('song-list').setAttribute('aria-busy','true');
 try{const data=await api('/api/recent-updates',undefined,request.signal);if(listRequest!==request)return;
 const list=$('song-list');list.replaceChildren();$('result-count').textContent=data.updates.length+' 個 My Best 更新';
 if(!data.updates.length)list.append(node('p','list-empty','尚無可驗證的 My Best 刷新。下載、匯入、一般遊玩、待確認或基準衝突不會建立更新。'));
 for(const r of data.updates){const row=node('button','song-row recent-update-row');row.type='button';row.setAttribute('role','listitem');row.dataset.songId=r.song_id;row.dataset.difficulty=r.difficulty;row.dataset.recordId=r.record_id;
 row.append(node('div','song-title',r.title+' / '+r.difficulty+' / LV '+text(r.game_level)),node('div','song-pack',(r.source_type==='official'?'Official / K':'Custom / '+text(r.pack_name))+' · Song #'+r.song_id),node('div','song-folder',r.relative_folder));
 const info=node('div','song-progress');info.append(node('strong','',r.update_type+(r.improvement===null?'':' +'+r.improvement)),node('span','', 'EX '+(r.previous_best_ex===null?'—':r.previous_best_ex)+' → '+r.new_best_ex),DanceFX.lamp(r.lamp_status,{compact:true}));if(r.update_summary?.lamp)info.append(node('span','update-badge lamp',r.update_summary.lamp.label));row.append(info,node('div','progress-note',(r.scoring_version==='custom-judged-v1'?'自訂 MAX · ':'歷史制度 · ')+(r.display||'MAX 未確認')+' · '+new Date(r.played_at).toLocaleString()+' · Record #'+r.record_id));
 row.addEventListener('click',()=>{if(mayLeave())location.href='/history?record_id='+r.record_id+'&song_id='+r.song_id+'&difficulty='+r.difficulty;});list.append(row);}
 state.total=data.updates.length;$('previous').disabled=true;$('next').disabled=true;$('page-info').textContent='最近30個不同 Song ID＋難度；依刷新紀錄的 played_at 排序';
 }finally{if(listRequest===request)$('song-list').setAttribute('aria-busy','false');}}
