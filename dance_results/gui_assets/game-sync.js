'use strict';
// Game folder mode: results.jsonl + song lists straight from the game (no OCR).
(() => {
  const $ = (id) => document.getElementById(id);
  const token = document.querySelector('meta[name="dance-token"]').content;
  const button = $('game-button');
  const dialog = $('game-dialog');
  const bar = $('game-status');
  let lastImported = null;
  let lastCatalog;

  async function api(path, body) {
    const options = {headers: {'X-Dance-Token': token}};
    if (body !== undefined) {
      options.method = 'POST';
      options.headers['Content-Type'] = 'application/json';
      options.body = JSON.stringify(body);
    }
    const response = await fetch(path, options);
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || '操作失敗');
    return data;
  }

  function time(value) {
    if (!value) return '—';
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? value : date.toLocaleTimeString();
  }

  function summary(status) {
    if (!status.game_dir) return '尚未設定遊戲資料夾：按右上「遊戲資料夾」選有 dance.exe 的資料夾，之後每一首的成績會自動存進來。';
    if (status.state === 'syncing' && !status.last_sync_at) return '正在讀取遊戲的歌單與成績…（第一次要幾秒鐘）';
    if (status.state === 'error') return '遊戲同步失敗：' + (status.last_error || '');
    const parts = ['已連結遊戲：' + status.game_dir];
    parts.push(status.character_label ? '角色 ' + status.character_label : '這個遊戲還沒有角色');
    if (status.last_sync_at) parts.push('最後檢查 ' + time(status.last_sync_at));
    if (status.imported_total) parts.push('這次開啟後新增 ' + status.imported_total + ' 筆成績');
    if (status.pending.length) parts.push(status.pending.length + ' 筆等歌單更新後再存');
    if (status.rejected.length) parts.push(status.rejected.length + ' 筆格式不正確未存');
    if (status.last_error) parts.push(status.last_error);
    return parts.join(' · ');
  }

  function renderDetail(status) {
    const detail = $('game-detail');
    if (!detail) return;
    detail.replaceChildren();
    const add = (label, value) => {
      const row = document.createElement('p');
      const strong = document.createElement('strong');
      strong.textContent = label + '：';
      row.append(strong, document.createTextNode(value));
      detail.append(row);
    };
    if (status.catalog) {
      const c = status.catalog;
      add('歌單', `官方新增 ${c.official_added || 0}、更新 ${c.official_updated || 0}；外部新增 ${c.external_added || 0}、更新 ${c.external_updated || 0}`);
      if (c.cache_roots) add('外部歌資料夾', c.cache_roots.join('；'));
      if (c.cache_missing) add('外部歌', '遊戲還沒有歌曲快取（開一次遊戲進選歌畫面就會產生）');
    }
    status.last_imported.slice(0, 5).forEach((item) => {
      add('新成績', `${item.title || ''} ${item.difficulty} ${item.lamp || ''} EX ${item.ex_score}`);
    });
    status.pending.slice(0, 3).forEach((item) => add('等待中', item.reason || ''));
    status.rejected.slice(0, 3).forEach((item) => add('未存', item.reason || ''));
  }

  function renderCharacters(status) {
    const select = $('game-character');
    if (!select || select.matches(':focus')) return;
    select.replaceChildren();
    if (!status.characters.length) {
      const option = document.createElement('option');
      option.textContent = status.game_dir ? '這個遊戲還沒有角色' : '先儲存遊戲資料夾';
      select.append(option);
      select.disabled = true;
      return;
    }
    status.characters.forEach((c) => {
      const option = document.createElement('option');
      option.value = c.key;
      option.textContent = c.label + (c.has_results ? '' : ' · 還沒有成績紀錄');
      option.selected = c.selected;
      select.append(option);
    });
    select.disabled = false;
  }

  function hideManualImports(status) {
    // With a game folder the song folders are followed automatically; a manual SONG rescan
    // would only do the same thing by hand.
    const scan = $('scan-button');
    if (scan) scan.hidden = Boolean(status.game_dir);
  }

  function render(status) {
    hideManualImports(status);
    renderCharacters(status);
    if (bar) {
      bar.textContent = summary(status);
      bar.hidden = false;
      bar.classList.toggle('error', status.state === 'error');
    }
    if (button) button.classList.toggle('linked', Boolean(status.game_dir));
    renderDetail(status);
    if (lastImported !== null && status.imported_total > lastImported) {
      // New results arrived: let the library page refresh its lamps.
      document.dispatchEvent(new CustomEvent('game-results-imported', {detail: status}));
    }
    lastImported = status.imported_total;
    const catalogAt = status.catalog ? status.catalog.synced_at : null;
    if (lastCatalog !== undefined && catalogAt && catalogAt !== lastCatalog) {
      // The song list just changed (e.g. the first sync after choosing the game folder).
      document.dispatchEvent(new CustomEvent('game-catalog-updated', {detail: status}));
    }
    lastCatalog = catalogAt;
  }

  async function refresh() {
    try {
      render(await api('/api/game'));
    } catch (error) {
      if (bar) {
        bar.textContent = error.message;
        bar.hidden = false;
      }
    }
  }

  function showResult(status) {
    const box = $('game-result');
    if (!box) return;
    if (status.state === 'error') {
      box.hidden = true;
      showError(status.last_error || '同步失敗');
      return;
    }
    const run = status.last_run || {};
    const parts = ['已同步 ' + time(status.last_sync_at)];
    parts.push(run.imported ? '新增 ' + run.imported + ' 筆成績' : '沒有新成績');
    if (run.catalog_changed) parts.push('歌單已更新');
    if (run.pending) parts.push(run.pending + ' 筆等歌單更新後再存');
    if (run.rejected) parts.push(run.rejected + ' 筆格式不正確未存');
    let text = parts.join(' · ');
    const selected = status.characters.find((c) => c.selected);
    if (selected && !selected.has_results) {
      text += '。這個角色還沒有成績紀錄檔：用會寫成績紀錄的新版遊戲打完一首歌後，就會自動出現。';
    }
    box.textContent = text;
    box.hidden = false;
  }

  async function saveAndSync(body) {
    const save = $('game-save');
    const select = $('game-character');
    showError('');
    $('game-result').hidden = true;
    save.disabled = true;
    select.disabled = true;
    const label = save.textContent;
    save.textContent = '同步中…';
    try {
      const status = await api('/api/game/settings', body);
      render(status);
      showResult(status);
    } catch (error) {
      showError(error.message);
    } finally {
      save.disabled = false;
      save.textContent = label;
      select.disabled = select.options.length === 0 || !select.value;
    }
  }

  function showError(message) {
    const box = $('game-error');
    if (!box) return;
    box.textContent = message || '';
    box.hidden = !message;
  }

  if (button && dialog) {
    button.addEventListener('click', async () => {
      showError('');
      try {
        const status = await api('/api/game');
        $('game-dir').value = status.game_dir || '';
        $('game-result').hidden = true;
        render(status);
      } catch (error) {
        showError(error.message);
      }
      dialog.showModal();
    });
    $('game-cancel').addEventListener('click', () => dialog.close());
    // Saving always runs one sync and reports it. No character here: the same folder keeps the
    // current one, another folder starts with its most recently played character.
    $('game-save').addEventListener('click', () => saveAndSync({game_dir: $('game-dir').value}));
    $('game-character').addEventListener('change', (event) => {
      saveAndSync({game_dir: $('game-dir').value, character: event.target.value});
    });
  }

  refresh();
  setInterval(refresh, 4000);
})();
