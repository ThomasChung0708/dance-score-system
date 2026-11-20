'use strict';
const $=id=>document.getElementById(id),token=document.querySelector('meta[name="dance-token"]').content;
let offset=0,imageUrl=null,loadVersion=0,detailVersion=0;
const element=(tag,cls,value)=>{const n=document.createElement(tag);if(cls)n.className=cls;if(value!==undefined)n.textContent=value;return n;};
function localTime(v){if(!v)return '時間未知';const d=new Date(v);return Number.isNaN(d.valueOf())?'時間未知':d.toLocaleString(undefined,{timeZoneName:'short'});}
function text(v){return v===null||v===undefined?'未確認':String(v);}
async function api(path){const response=await fetch('/api/history'+path,{headers:{'X-Dance-Token':token}}),data=await response.json();if(!response.ok)throw new Error(data.error||'讀取失敗');return data;}
function message(value){$('history-message').hidden=!value;$('history-message').textContent=value;}
const songInput = $('history-song'), songIdInput = $('history-song-id');
const songOptions = $('history-song-options');
let songSuggestions = [], activeSong = -1, songSearchTimer, songSearchVersion = 0;
let songComposing = false;
let unboundTitle = new URLSearchParams(location.search).get('title');
let unboundArtist = new URLSearchParams(location.search).get('artist');
if (unboundTitle) songInput.value = unboundTitle;

function closeSongOptions() {
 clearTimeout(songSearchTimer);
 ++songSearchVersion;
 songSuggestions = [];
 activeSong = -1;
 songOptions.hidden = true;
 songOptions.replaceChildren();
 songInput.setAttribute('aria-expanded', 'false');
 songInput.removeAttribute('aria-activedescendant');
 $('history-song-status').textContent = '';
}

function pickSong(song) {
 unboundTitle = null;
 unboundArtist = null;
 songInput.value = song.title;
 songIdInput.value = song.song_id;
 closeSongOptions();
}

function highlightSong(index) {
 activeSong = index;
 for (const [i, option] of [...songOptions.children].entries()) {
  option.setAttribute('aria-selected', String(i === index));
 }
 const option = songOptions.children[index];
 if (option) {
  songInput.setAttribute('aria-activedescendant', option.id);
  option.scrollIntoView({block: 'nearest'});
 }
}

async function searchSongs() {
 const query = songInput.value;
 const version = ++songSearchVersion;
 if (!query.trim() || songIdInput.value) return;
 try {
  const data = await api('/song-search?' + new URLSearchParams({q: query, limit: 20}));
  if (version !== songSearchVersion) return;
  songSuggestions = data.songs;
  activeSong = -1;
  songInput.removeAttribute('aria-activedescendant');
  songOptions.replaceChildren(...songSuggestions.map((song, index) => {
   const option = element('li', 'history-song-option');
   option.id = 'history-song-option-' + index;
   option.setAttribute('role', 'option');
   option.setAttribute('aria-selected', 'false');
   const source = song.source_type === 'official' ? 'Official / K' :
    (song.pack_name || song.relative_folder || '自製歌曲');
   option.append(element('strong', '', song.title),
    element('span', '', song.artist || '歌手未設定'),
    element('small', '', source + (song.relative_folder ? ' · ' + song.relative_folder : '')));
   option.addEventListener('pointerdown', event => event.preventDefault());
   option.addEventListener('click', () => pickSong(song));
   return option;
  }));
  if (!songSuggestions.length) {
   const empty = element('li', 'history-song-empty', '找不到符合的歌曲');
   empty.setAttribute('role', 'presentation');
   songOptions.append(empty);
  }
  songOptions.hidden = false;
  songInput.setAttribute('aria-expanded', 'true');
  $('history-song-status').textContent = songSuggestions.length + ' 首符合歌曲';
  return songSuggestions;
 } catch (error) {
  if (version === songSearchVersion) {
   closeSongOptions();
   message(error.message);
  }
 }
}

function scheduleSongSearch() {
 closeSongOptions();
 if (!songComposing && songInput.value.trim() && !songIdInput.value) {
  songSearchTimer = setTimeout(searchSongs, 200);
 }
}

songInput.addEventListener('input', event => {
 unboundTitle = null;
 unboundArtist = null;
 songIdInput.value = '';
 closeSongOptions();
 if (!event.isComposing && !songComposing) scheduleSongSearch();
});
songInput.addEventListener('compositionstart', () => {
 songComposing = true;
 closeSongOptions();
});
songInput.addEventListener('compositionend', () => {
 songComposing = false;
 scheduleSongSearch();
});
songInput.addEventListener('focus', scheduleSongSearch);
songInput.addEventListener('keydown', event => {
 if (event.isComposing || songComposing) {
  if (event.key === 'Enter') event.preventDefault();
  return;
 }
 if (event.key === 'Escape') {
  event.preventDefault();
  closeSongOptions();
 } else if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
  event.preventDefault();
  if (songOptions.hidden) {
   clearTimeout(songSearchTimer);
   searchSongs();
  } else if (songSuggestions.length) {
   const step = event.key === 'ArrowDown' ? 1 : -1;
   const next = activeSong < 0 ? (step > 0 ? 0 : songSuggestions.length - 1) :
    (activeSong + step + songSuggestions.length) % songSuggestions.length;
   highlightSong(next);
  }
 } else if (event.key === 'Enter' && !songOptions.hidden && songSuggestions.length) {
  event.preventDefault();
  pickSong(songSuggestions[activeSong < 0 ? 0 : activeSong]);
 }
});
document.addEventListener('pointerdown', event => {
 if (!$('history-song-search').contains(event.target)) closeSongOptions();
});
$('history-song-search').addEventListener('focusout', event => {
 if (!$('history-song-search').contains(event.relatedTarget)) closeSongOptions();
});

function cell(tr,label,cls){const td=element('td',cls);td.dataset.label=label;tr.append(td);return td;}
function row(record){
 const r=record,tr=element('tr','record-row');tr.dataset.recordId=r.record_id;tr.dataset.updateKind=r.update_summary.kind;
 let td=cell(tr,'遊玩日期 / Record','record-date');td.append(element('time','',localTime(r.played_at_verified===false?null:r.played_at)),element('span','record-number','RECORD #'+r.record_id));
 DanceFX.lampStrip(tr, r.lamp_status);
 td = cell(tr, '歌曲 / 難度', 'record-song');
 const title = element('a', 'record-title', r.title || '歌曲待確認');
 title.href = '/history?' + new URLSearchParams({...(r.song_id ? {song_id: r.song_id} : {title: r.title, artist: r.artist || ''}),
   ...(r.difficulty ? {difficulty: r.difficulty} : {})});
 td.append(title, element('span', 'difficulty-chip difficulty-' + (r.difficulty || 'unknown').toLowerCase(),
   (r.difficulty || '難度未設定') + ' / LV ' + text(r.game_level)));
 for (const warning of r.warnings || []) {
  if (warning.code === 'CHART_NOTES_CHANGED') td.append(element('p', 'monitor-note',
   '警告 CHART_NOTES_CHANGED：目錄 ' + text(warning.catalog_notes) + '／選曲 ' +
   text(warning.selection_notes) + ' → 結算 ' + warning.observed_notes +
   '（差 ' + text(warning.catalog_difference) + '）'));
 }
 const source=element('details','row-source'),summary=element('summary','','來源資訊');source.append(summary,element('p','', (r.source_type==='official'?'Official / K':text(r.pack_name))+' · Song '+r.song_id+' · '+text(r.player_name)),element('p','',r.relative_folder||'來源路徑未設定'));td.append(source);
 td=cell(tr,'CLEAR STATUS','record-clear');td.append(DanceFX.lamp(r.lamp_status,{compact:true}),element('span','status-caption','當次結果'));
 td=cell(tr,'SCORE STATUS','record-score');td.append(DanceFX.scorePanel(r));
 td=cell(tr,'P / C / B / M','record-judgments');const counts=element('div','judgment-grid');for(const [key,label]of [['perfect','P'],['cool','C'],['bad','B'],['miss','M']]){const value=element('span','judgment '+key);value.append(element('small','',label),element('strong','',text(r[key])));counts.append(value);}td.append(counts);
 td=cell(tr,'本次更新','record-update');td.append(DanceFX.updateSummary(r.update_summary));
 td=cell(tr,'詳情','record-actions');const button=element('button','','查看 #'+r.record_id);button.type='button';button.addEventListener('click',()=>detail(r.record_id));td.append(button);return tr;
}
async function load(){
 const version=++loadVersion;try{message('');const args=new URLSearchParams({offset,limit:60});for(const [id,key]of [['history-song-id','song_id'],['history-difficulty','difficulty'],['history-lamp','lamp'],['history-player','player_name']])if($(id).value)args.set(key,$(id).value);
 if (unboundTitle) {
  args.set('title', unboundTitle);
  if (unboundArtist !== null) args.set('artist', unboundArtist);
 }
 const data=await api('?'+args);if(version!==loadVersion)return;
 $('history-play-count').hidden = data.play_count === null;
 $('history-play-count').textContent = data.play_count === null ? '' : '這首歌共遊玩 '+data.play_count+' 次（全部難度）';
 $('history-count').textContent='共 '+data.total+' 筆成績 · 本頁 '+data.records.length+' 筆';$('history-prev').disabled=offset===0;$('history-next').disabled=offset+60>=data.total;$('history-rows').replaceChildren(...data.records.map(row));
 if(!data.records.length){const tr=element('tr','empty-records'),td=element('td','','尚無符合條件的正式成績。');td.colSpan=7;tr.append(td);$('history-rows').append(tr);}
 $('history-bests').replaceChildren();for(const d of data.bests){const group=element('section','history-best-group'),h=element('h3','chart-label',d.difficulty+(d.comparison_paused?' · 比較暫停':''));group.append(h,DanceFX.bestPair({difficulty:d.difficulty,best_score_record:d.my_best_score,best_lamp_record:d.best_lamp_record,best_lamp:d.best_clear_lamp,comparison_paused:d.comparison_paused},{songId:d.song_id}));$('history-bests').append(group);}
 }catch(e){if(version===loadVersion)message(e.message);}
}
async function detail(id){
 const version=++detailVersion;try{const r=await api('/detail?record_id='+id);if(version!==detailVersion)return;
 $('history-detail').hidden=false;$('history-detail-title').textContent=r.title+' · '+(r.difficulty || '難度未設定')+' / Record #'+r.record_id;$('history-detail-score').replaceChildren(DanceFX.lamp(r.lamp_status),DanceFX.scorePanel(r),DanceFX.updateSummary(r.update_summary));$('history-metadata').replaceChildren();
 for(const [label,value]of [['Song ID',r.song_id],['Pack',r.source_type==='official'?'Official / K':r.pack_name],['SongFolder',r.relative_folder],['難度',r.difficulty],['歌曲 LV',r.game_level],['本人暱稱',r.player_name],['遊玩模式',r.play_mode],['完成狀態',r.completion_status],['Perfect / Cool / Bad / Miss',[r.perfect,r.cool,r.bad,r.miss].map(text).join(' / ')],['最高連擊',r.max_combo],['原始總積分',r.game_score],['成績評級',r.grade],['原存 EX / MAX',r.ex_score+' / '+text(r.max_score)],['原存差值 / 百分比',text(r.max_difference)+' / '+text(r.percentage)],['MAX 計分制度',r.max_label],['跨場次比較',r.comparison?.ranking_paused?'基準衝突，比較暫停':'無已知衝突／舊紀錄'],['MAX 驗證狀態',r.max_status==='confirmed'?'已驗證當次基礎':r.max_status==='legacy_basis_unreviewed'?'歷史原值保留，基礎待稽核':'未知'],['catalog_total_notes',r.catalog_total_notes],['judged_total',r.judged_total],['max_basis_notes',r.max_basis_notes],['燈號依據',r.lamp_verification?.source==='system_result_judgments'?'本系統結算判定':'歷史／遊戲證據'],['Session',r.session_id],['Review',r.review_id],['遊玩時間',localTime(r.played_at_verified===false?null:r.played_at)],['時間來源',r.played_at_source],['確認時間',localTime(r.confirmed_at)],['確認方法',r.confirmation_method],['更新證據來源',r.update_summary.source]]){$('history-metadata').append(element('dt','',label),element('dd','',text(value)));}
 $('history-evidence').textContent=JSON.stringify(r.evidence,null,2);$('history-image').hidden=true;$('history-image-error').textContent='';if(imageUrl){URL.revokeObjectURL(imageUrl);imageUrl=null;}
 $('history-detail').scrollIntoView({block:'start',behavior:'instant'});
 const response=await fetch('/api/history/image?record_id='+id,{headers:{'X-Dance-Token':token}});if(version!==detailVersion)return;
 if(response.ok){const blob=await response.blob();if(version!==detailVersion)return;imageUrl=URL.createObjectURL(blob);$('history-image').onload=()=>{if(version===detailVersion&&!$('history-detail').hidden)$('history-detail').scrollIntoView({block:'start',behavior:'instant'});};$('history-image').src=imageUrl;$('history-image').hidden=false;}else $('history-image-error').textContent=(await response.json()).error;
 }catch(e){if(version===detailVersion)message(e.message);}
}
$('close-history-detail').addEventListener('click',()=>{++detailVersion;$('history-detail').hidden=true;});
$('history-filter').addEventListener('submit', async event => {
 event.preventDefault();
 if (songComposing) return;
 clearTimeout(songSearchTimer);
 if (songInput.value.trim() && !songIdInput.value && !unboundTitle) {
  const songs = await searchSongs();
  if (!songs) return;
  if (songs.length !== 1) {
   message(songs.length ? '請從清單選擇歌曲' : '找不到符合的歌曲');
   return;
  }
  pickSong(songs[0]);
 }
 closeSongOptions();
 offset = 0;
 const params = new URLSearchParams();
 if (unboundTitle) {
  params.set('title', unboundTitle);
  if (unboundArtist !== null) params.set('artist', unboundArtist);
 }
 for (const [id, key] of [['history-song-id', 'song_id'], ['history-difficulty', 'difficulty'],
   ['history-lamp', 'lamp'], ['history-player', 'player_name']]) {
  if ($(id).value) params.set(key, $(id).value);
 }
 history.replaceState(null, '', location.pathname + (params.size ? '?' + params : ''));
 load();
});
$('history-prev').addEventListener('click',()=>{offset=Math.max(0,offset-60);load();});$('history-next').addEventListener('click',()=>{offset+=60;load();});
$('history-clear').addEventListener('click', () => {
 closeSongOptions();
 songInput.value = '';
 songIdInput.value = '';
 unboundTitle = null;
 unboundArtist = null;
 $('history-difficulty').value = '';
 $('history-lamp').value = '';
 $('history-player').value = '';
 offset = 0;
 ++detailVersion;
 $('history-detail').hidden = true;
 history.replaceState(null, '', location.pathname);
 load();
});
window.addEventListener('dance-record-update',load);window.addEventListener('storage',e=>{if(e.key==='dance-record-update')load();});
const selectedSong = new URLSearchParams(location.search).get('song_id');
if (selectedSong && /^[1-9]\d*$/.test(selectedSong)) {
 songIdInput.value = selectedSong;
 songInput.value = 'Song #' + selectedSong;
 api('/song-search?' + new URLSearchParams({song_id: selectedSong})).then(data => {
  if (songIdInput.value === selectedSong) {
   songInput.value = data.songs[0].title;
  }
 }).catch(error => {
  if (songIdInput.value === selectedSong) {
   $('history-song-status').textContent =
    '找不到歌曲標題，仍以 Song #' + selectedSong + ' 篩選。' + error.message;
  }
 });
}
const requestedDifficulty=new URLSearchParams(location.search).get('difficulty');if(['EASY','NORMAL','HARD'].includes(requestedDifficulty))$('history-difficulty').value=requestedDifficulty;
const requestedLamp = new URLSearchParams(location.search).get('lamp');
if ([...$('history-lamp').options].some(option => option.value === requestedLamp)) {
 $('history-lamp').value = requestedLamp;
}
const requestedPlayer = new URLSearchParams(location.search).get('player_name');
if (requestedPlayer) $('history-player').value = requestedPlayer;
const initial = new URLSearchParams(location.search).get('record_id');
load().then(() => {
 if (initial && /^\d+$/.test(initial) && new URLSearchParams(location.search).get('record_id') === initial) {
  detail(Number(initial));
 }
});
