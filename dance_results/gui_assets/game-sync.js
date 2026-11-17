'use strict';
// Game folder mode: results.jsonl + song lists straight from the game (no OCR).
(() => {
  const $ = (id) => document.getElementById(id);
  const token = document.querySelector('meta[name="dance-token"]').content;
  const button = $('game-button');
  const dialog = $('game-dialog');
  const bar = $('game-status');
  let lastImported = null;

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

  function hideOcrPages(status) {
    if (status.ocr_available) return;
    // The packaged exe leaves OCR out; its pages would only show errors.
    document.querySelectorAll('a.page-link[href="/reviews"], a.page-link[href="/monitor"]').forEach((link) => {
      link.hidden = true;
    });
  }

  function time(value) {
    if (!value) return '—';
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? value : date.toLocaleTimeString();
  }

  function summary(status) {
    if (!status.game_dir) return '尚未設定遊戲資料夾：設定後會自動讀取每一首的成績。';
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
      option.textContent = c.label + (c.has_results ? '' : ' · 還沒有成績');
      option.selected = c.selected;
      select.append(option);
    });
    select.disabled = false;
  }

  function render(status) {
    hideOcrPages(status);
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
        render(status);
      } catch (error) {
        showError(error.message);
      }
      dialog.showModal();
    });
    $('game-cancel').addEventListener('click', () => dialog.close());
    $('game-save').addEventListener('click', async () => {
      showError('');
      try {
        render(await api('/api/game/settings', {game_dir: $('game-dir').value}));
      } catch (error) {
        showError(error.message);
      }
    });
    $('game-character').addEventListener('change', async (event) => {
      showError('');
      try {
        const current = await api('/api/game');
        render(await api('/api/game/settings', {game_dir: current.game_dir, character: event.target.value}));
      } catch (error) {
        showError(error.message);
      }
    });
    $('game-sync-now').addEventListener('click', async () => {
      showError('');
      try {
        render(await api('/api/game/sync', {}));
      } catch (error) {
        showError(error.message);
      }
    });
  }

  refresh();
  setInterval(refresh, 4000);
})();
