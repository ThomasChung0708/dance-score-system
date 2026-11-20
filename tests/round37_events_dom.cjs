const assert = require('node:assert/strict');
const {chromium} = require(process.argv[2]);
const base = process.argv[3], songId = process.argv[4];

async function main() {
  const browser = await chromium.launch({headless: true, channel: 'msedge'});
  try {
    const context = await browser.newContext();
    const library = await context.newPage();
    const history = await context.newPage();
    const errors = [], dialogs = [];
    for (const page of [library, history]) {
      page.on('pageerror', error => errors.push(error.message));
      page.on('dialog', dialog => { dialogs.push(dialog.message()); dialog.dismiss(); });
      await page.addInitScript(() => {
        window.storagePolls = 0;
        window.addEventListener('dance-storage-status', () => ++window.storagePolls);
      });
    }
    await library.goto(base + '/?song_id=' + songId);
    await library.locator('.difficulty-card[data-label="HARD"]').waitFor();
    await library.locator('button.nav-item[title="自製歌曲"]').click();
    await library.locator('#sort').selectOption('desc');
    await library.locator('#search').fill('外部曲');
    await library.waitForFunction(() => document.querySelector('#result-count').textContent === '共 1 首歌曲');
    // Pick the song after selecting a category, which deliberately closes details.
    await library.locator('.song-row[data-song-id="' + songId + '"]').click();
    await library.locator('.difficulty-card[data-label="HARD"]').waitFor();
    await library.locator('.best-difficulty-select').selectOption('EASY');
    await library.locator('.progress-history select[aria-label="成長履歷排序"]').selectOption('asc');
    await library.locator('.growth-pager button').last().click();
    await library.waitForFunction(() => document.querySelector('.growth-count').textContent === '第 21–40 / 61 次遊玩');
    await history.goto(base + '/history?song_id=' + songId + '&difficulty=HARD');
    await history.locator('#history-player').fill('Eithwa');
    await history.getByRole('button', {name: '查詢成績'}).click();
    await history.waitForFunction(() => document.querySelector('#history-count').textContent === '共 61 筆成績 · 本頁 60 筆');
    await history.locator('#history-next').click();
    await history.waitForFunction(() => document.querySelectorAll('.record-row').length === 1);
    await Promise.all([library, history].map(page => page.waitForFunction(() => window.storagePolls > 0)));
    assert.equal(await history.locator('.achievement-toast').count(), 0);

    let releaseDetail, detailSeen;
    const release = new Promise(resolve => { releaseDetail = resolve; });
    const seen = new Promise(resolve => { detailSeen = resolve; });
    let held = false;
    await library.route('**/api/songs/' + songId, async route => {
      if (held) return route.continue();
      held = true;
      const response = await route.fetch();
      detailSeen();
      await release;
      await route.fulfill({response});
    });
    const sync = () => history.evaluate(async () => {
      const response = await fetch('/api/game/sync', {
        method: 'POST',
        headers: {
          'X-Dance-Token': document.querySelector('meta[name="dance-token"]').content,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({}),
      });
      if (!response.ok) throw new Error(await response.text());
    });
    await sync();
    await seen;
    // Start typing while the refresh response is still in flight.
    const notes = library.locator('.difficulty-card[data-label="HARD"] input[name="notes"]');
    await notes.fill('777');
    releaseDetail();
    await library.waitForFunction(() => document.querySelector('.song-result-panel').textContent.includes('EX 1260'));
    await history.waitForFunction(() => document.querySelector('#history-count').textContent === '共 62 筆成績 · 本頁 2 筆');
    assert.equal(await notes.inputValue(), '777');
    assert.equal(await library.locator('.difficulty-card[data-label="HARD"]').getAttribute('data-dirty'), 'true');
    assert.equal(await library.locator('#search').inputValue(), '外部曲');
    assert.equal(await library.locator('#sort').inputValue(), 'desc');
    assert.equal(await library.locator('.song-row.selected').getAttribute('data-song-id'), songId);
    assert.equal(await library.locator('.best-difficulty-select').inputValue(), 'EASY');
    await library.waitForFunction(() => document.querySelector('.growth-count').textContent === '第 21–40 / 62 次遊玩');
    assert.equal(await library.locator('.progress-history select[aria-label="成長履歷排序"]').inputValue(), 'asc');
    assert.equal(await history.locator('#history-song-id').inputValue(), songId);
    assert.equal(await history.locator('#history-difficulty').inputValue(), 'HARD');
    assert.equal(await history.locator('#history-player').inputValue(), 'Eithwa');
    assert.equal(await history.locator('#history-prev').isEnabled(), true);
    const deliveryCount = async () => {
      const counts = await Promise.all([library, history].map(page => page.evaluate(() =>
        Number(document.querySelector('#achievement-notifications')?.dataset.deliveryCount || 0))));
      return counts.reduce((total, count) => total + count, 0);
    };
    await Promise.race([library, history].map(page => page.waitForFunction(() =>
      document.querySelector('#achievement-notifications'))));
    assert.equal(await deliveryCount(), 2);
    await sync();
    await history.waitForTimeout(2300);
    assert.equal(await deliveryCount(), 2);
    assert.deepEqual(dialogs, []);
    assert.deepEqual(errors, []);
    // No monitor or review page was opened during this test.
    assert.deepEqual(context.pages().map(page => new URL(page.url()).pathname).sort(), ['/', '/history']);
    // A fresh client baselines the opening snapshot, even without localStorage receipts.
    const reopened = await browser.newContext();
    const freshPage = await reopened.newPage();
    await freshPage.addInitScript(() => {
      window.storagePolls = 0;
      window.addEventListener('dance-storage-status', () => ++window.storagePolls);
    });
    await freshPage.goto(base + '/history');
    await freshPage.waitForFunction(() => window.storagePolls >= 2);
    assert.equal(await freshPage.locator('#achievement-notifications').count(), 0);
    await reopened.close();
  } finally {
    await browser.close();
  }
}

main().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
