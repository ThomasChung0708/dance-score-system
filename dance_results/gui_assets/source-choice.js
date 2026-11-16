'use strict';
window.SourceChoice = (() => {
  const token = document.querySelector('meta[name="dance-token"]').content;
  const node = (tag, text) => {
    const item = document.createElement(tag);
    if (text) item.textContent = text;
    return item;
  };
  function render(root, prompt, onChanged) {
    const version = Symbol();
    root.sourceChoiceVersion = version;
    root.replaceChildren();
    root.hidden = !prompt;
    root.dataset.sessionId = prompt?.session_id || '';
    if (!prompt) return;
    root.append(node('h3', prompt.question), node('p', 'Session ' + prompt.session_id));
    const controls = node('div');
    controls.className = 'monitor-inline';
    const copies = node('div');
    const status = node('p');
    status.setAttribute('role', 'status');
    root.append(controls, copies, status);
    const active = () => root.sourceChoiceVersion === version && root.isConnected;
    async function choose(choice) {
      for (const button of root.querySelectorAll('button')) button.disabled = true;
      try {
        const response = await fetch('/api/reviews/source-choice', {
          method: 'POST', headers: {'X-Dance-Token': token, 'Content-Type': 'application/json'},
          body: JSON.stringify({session_id: prompt.session_id, review_id: prompt.review_id,
            revision: prompt.revision, ...choice})
        });
        const data = await response.json();
        if (!active()) return;
        if (!response.ok) throw new Error(data.error || '來源選擇失敗');
        root.hidden = true;
        root.dataset.sessionId = '';
        onChanged?.(data);
      } catch (error) {
        if (!active()) return;
        status.textContent = error.message;
        for (const button of root.querySelectorAll('button')) button.disabled = false;
      }
    }
    function button(parent, label, action) {
      const item = node('button', label);
      item.type = 'button';
      item.addEventListener('click', action);
      parent.append(item);
      return item;
    }
    function showCopies() {
      copies.replaceChildren();
      for (const candidate of prompt.candidates.filter(c => c.source_type === 'custom')) {
        const charts = candidate.difficulties.map(d => d.difficulty + ' / LV ' +
          (d.game_level ?? '未知') + ' / ' + (d.total_notes ?? '未知') + ' 音符').join('；');
        button(copies, (candidate.exact_match ? '【難度／LV 相符】' : '') +
          (candidate.pack_name || '未命名 Pack') + ' / ' + candidate.folder_name +
          ' · #' + candidate.song_id + ' · ' + (charts || '尚無譜面資料'),
          () => choose({source_type: 'custom', song_id: candidate.song_id}));
      }
    }
    if (prompt.kind === 'source') {
      button(controls, '官方譜', () => choose({source_type: 'official'}));
      button(controls, '自製譜', () => {
        const custom = prompt.candidates.filter(c => c.source_type === 'custom');
        if (custom.length === 1) choose({source_type: 'custom', song_id: custom[0].song_id});
        else {
          showCopies();
          copies.querySelector('button')?.focus();
        }
      });
    } else showCopies();
    button(controls, '稍後到待確認處理', () => choose({dismiss: true}));
  }
  return {render};
})();
