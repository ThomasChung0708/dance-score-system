'use strict';
(() => {
  const input = document.getElementById('monitor-player') || document.getElementById('result-player');
  const token = document.querySelector('meta[name="dance-token"]').content;
  if (!input) return;
  input.addEventListener('change', async () => {
    try {
      const response = await fetch('/api/player-settings', {
        method: 'POST', headers: {'X-Dance-Token': token, 'Content-Type': 'application/json'},
        body: JSON.stringify({player_name: input.value})
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || '暱稱儲存失敗');
      input.setCustomValidity('');
    } catch (error) {
      input.setCustomValidity(error.message);
      input.reportValidity();
    }
  });
})();
