'use strict';
(() => {
  const $ = id => document.getElementById(id);
  const token = document.querySelector('meta[name="dance-token"]').content;
  let version = 0;
  function element(tag, className, value) {
    const node = document.createElement(tag);
    node.className = className || '';
    if (value !== undefined) node.textContent = value;
    return node;
  }
  function row(song) {
    const tr = element('tr', 'record-row plays-row');
    if (song.song_id) tr.dataset.songId = song.song_id;
    const params = new URLSearchParams(song.song_id ? {song_id: song.song_id} :
      {title: song.title, artist: song.artist});
    const href = '/history?' + params;
    const title = element('a', 'record-title', song.title);
    title.href = href;
    function cell(label, className, value) {
      const td = element('td', className, value);
      td.dataset.label = label;
      tr.append(td);
      return td;
    }
    cell('名次', 'plays-rank', song.rank);
    cell('歌曲 / 歌手', 'record-song').append(title,
      element('span', 'plays-artist', song.artist || '歌手未設定'));
    cell('總遊玩次數', 'plays-total', song.play_count);
    cell('最佳燈號', 'record-clear').append(DanceFX.lamp(song.best_lamp, {compact: true}));
    cell('BEST EX', 'plays-ex', song.best_ex_score ?? '—');
    const date = new Date(song.last_played_at);
    const time = element('time', '', song.last_played_at && !Number.isNaN(date.valueOf()) ?
      date.toLocaleString(undefined, {timeZoneName: 'short'}) : '時間未知');
    if (song.last_played_at) time.dateTime = song.last_played_at;
    cell('最後遊玩', 'record-date').append(time);
    DanceFX.lampStrip(tr, song.best_lamp);
    tr.addEventListener('click', event => {
      if (!event.target.closest('a') && !window.getSelection()?.toString()) location.href = href;
    });
    return tr;
  }
  async function load() {
    const current = ++version;
    $('plays-rows').setAttribute('aria-busy', 'true');
    try {
      const response = await fetch('/api/plays/top?limit=30', {headers: {'X-Dance-Token': token}});
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || '讀取失敗，請重新整理。');
      if (current !== version) return;
      $('plays-message').hidden = true;
      $('plays-count').textContent = '共 '+data.total+' 首已遊玩歌曲 · 顯示 '+data.songs.length+' 首';
      $('plays-rows').replaceChildren(...data.songs.map(row));
      if (!data.songs.length) {
        const tr = element('tr', 'empty-records');
        const td = element('td', '', '尚無遊玩紀錄。保存第一筆成績後，排行會自動出現。');
        td.colSpan = 6;
        tr.append(td);
        $('plays-rows').append(tr);
      }
    } catch (error) {
      if (current !== version) return;
      $('plays-message').textContent = error.message;
      $('plays-message').hidden = false;
      $('plays-count').textContent = '無法載入排行，請重新整理。';
    } finally {
      if (current === version) $('plays-rows').setAttribute('aria-busy', 'false');
    }
  }
  $('plays-refresh').addEventListener('click', load);
  window.addEventListener('dance-record-update', load);
  window.addEventListener('storage', event => {
    if (event.key === 'dance-record-update') load();
  });
  load();
})();
