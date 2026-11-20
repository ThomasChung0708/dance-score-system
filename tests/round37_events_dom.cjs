const assert = require('node:assert/strict');
const {chromium} = require(process.argv[2]);
const base = process.argv[3], songId = process.argv[4];

async function emptyLibraryDoesNotRetry(browser) {
  const context = await browser.newContext();
  const page = await context.newPage();
  await page.route('**/api/bootstrap', route => route.fulfill({
    json: {database: 'empty.sqlite3', roots: []},
  }));
  await page.addInitScript(() => {
    window.progressRetries = 0;
    const timeout = window.setTimeout;
    window.setTimeout = (callback, delay, ...args) => {
      if (String(callback).includes('refreshProgress')) ++window.progressRetries;
      return timeout(callback, delay, ...args);
    };
  });
  await page.goto(base);
  await page.waitForFunction(() => document.querySelector('#totals').textContent === '歌曲庫是空的');
  await page.evaluate(() => {
    window.dispatchEvent(new Event('focus'));
    window.dispatchEvent(new CustomEvent('dance-record-update', {detail: {record_id: 999}}));
    document.dispatchEvent(new Event('game-results-imported'));
  });
  await page.waitForTimeout(1600);
  assert.equal(await page.evaluate(() => window.progressRetries), 0);
  await context.close();
}

async function polledNotificationsAreQueued(browser, reducedMotion) {
  const context = await browser.newContext({reducedMotion});
  const page = await context.newPage();
  let events = [];
  await page.route('**/api/reviews/storage', route => route.fulfill({json: {events}}));
  await page.addInitScript(() => {
    window.storagePolls = 0;
    window.addEventListener('dance-storage-status', () => ++window.storagePolls);
  });
  await page.clock.install({time: new Date('2026-11-20T00:00:00Z')});
  await page.goto(base + '/history');
  await page.waitForFunction(() => window.storagePolls > 0);
  await page.clock.pauseAt(new Date('2026-11-20T00:01:00Z'));
  await page.waitForFunction(() => window.storagePolls >= 2);
  events = [99001, 99002].map(record_id => ({
    record_id, catalog_path: 'queue.sqlite3', title: 'Queued result', difficulty: 'HARD',
    ex_score: 100, lamp_status: 'CLEAR', update_summary: {kind: 'NO_UPDATE', label: 'SAVED'},
  }));
  await page.clock.runFor(2000);
  const card = page.locator('.achievement-toast');
  const area = page.locator('#achievement-notifications');
  await card.waitFor();
  assert.equal(await card.getAttribute('data-record-id'), '99001');
  assert.equal(await area.getAttribute('data-delivery-count'), '1');
  assert.equal(JSON.parse(await area.getAttribute('data-last-event')).motion,
    reducedMotion === 'reduce' ? 'reduce' : 'standard');
  assert.deepEqual(await page.evaluate(records => [
    DanceFX.notify(records[1]),
    DanceFX.notify({...records[1], record_id: 99003}, {idempotent: true}),
  ], events), [false, false]);
  await page.clock.runFor(2799);
  assert.equal(await card.getAttribute('data-record-id'), '99001');
  assert.equal(await card.evaluate(node => node.classList.contains('toast-leave')), false);
  await page.clock.runFor(1);
  assert.equal(await card.evaluate(node => node.classList.contains('toast-leave')), true);
  await page.clock.runFor(219);
  assert.equal(await card.getAttribute('data-record-id'), '99001');
  await page.clock.runFor(1);
  assert.equal(await card.getAttribute('data-record-id'), '99002');
  assert.equal(await area.getAttribute('data-delivery-count'), '2');
  await page.clock.runFor(3020);
  assert.equal(await card.count(), 0);
  assert.equal(await area.getAttribute('data-delivery-count'), '2');
  await context.close();
}

async function failedNotificationsDoNotBlockQueue(browser) {
  const context = await browser.newContext();
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/api/reviews/storage', route => route.fulfill({json: {events: []}}));
  await page.clock.install();
  await page.goto(base + '/history');
  await page.evaluate(() => {
    const record = {
      catalog_path: 'failed-queue.sqlite3', title: 'Queued result', difficulty: 'HARD',
      ex_score: 100, lamp_status: 'CLEAR', update_summary: {kind: 'NO_UPDATE', label: 'SAVED'},
    };
    const failed = {
      ...record, update_summary: {kind: 'SCORE_UPDATE', score: {}, label: 'SCORE'},
      achievement_feedback: {tone: {}},
    };
    // Fail at an empty queue, then skip consecutive failures between valid cards.
    DanceFX.notify({...failed, record_id: 99100});
    DanceFX.notify({...record, record_id: 99101});
    DanceFX.notify({...failed, record_id: 99102});
    DanceFX.notify({...failed, record_id: 99103});
    DanceFX.notify({...record, record_id: 99104});
  });
  const card = page.locator('.achievement-toast');
  const area = page.locator('#achievement-notifications');
  assert.equal(await card.getAttribute('data-record-id'), '99101');
  await page.clock.runFor(3020);
  assert.equal(await card.getAttribute('data-record-id'), '99104');
  assert.equal(await area.getAttribute('data-delivery-count'), '2');
  await page.clock.runFor(3020);
  assert.equal(await card.count(), 0);
  assert.deepEqual(errors, []);
  await context.close();
}

async function main() {
  const browser = await chromium.launch({headless: true, channel: 'msedge'});
  try {
    const context = await browser.newContext();
    // Isolate receipt refreshes from the separate game-status refresh path.
    await context.route('**/api/game', async route => {
      const response = await route.fetch();
      await route.fulfill({json: {...await response.json(), imported_total: 0, catalog: null}});
    });
    const library = await context.newPage();
    const history = await context.newPage();
    const errors = [], dialogs = [];
    for (const page of [library, history]) {
      page.on('pageerror', error => errors.push(error.message));
      page.on('dialog', dialog => { dialogs.push(dialog.message()); dialog.dismiss(); });
      await page.addInitScript(() => {
        window.storagePolls = 0;
        window.recordUpdates = [];
        window.recordStorageEvents = 0;
        window.addEventListener('dance-storage-status', () => ++window.storagePolls);
        window.addEventListener('dance-record-update', event => window.recordUpdates.push(event.detail));
        window.addEventListener('storage', event => {
          if (event.key === 'dance-record-update') ++window.recordStorageEvents;
        });
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
    const refreshes = {library: 0, history: 0};
    library.on('request', request => {
      if (new URL(request.url()).pathname === '/api/packs') ++refreshes.library;
    });
    history.on('request', request => {
      if (new URL(request.url()).pathname === '/api/history') ++refreshes.history;
    });

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
    await library.waitForFunction(() => document.querySelector('.detail-play-count').textContent.includes('總遊玩 62 次'));
    assert.match(await library.locator('.song-row.selected .song-play-count').textContent(), /遊玩 62 次/);
    assert.match(await history.locator('#history-play-count').textContent(), /共遊玩 62 次/);
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
    // A batch may be celebrated in one tab, where its second card is queued.
    await Promise.all([library, history].map(page => page.waitForFunction(() => window.recordUpdates.length === 2)));
    await sync();
    await history.waitForTimeout(3300);
    assert.equal(await deliveryCount(), 2);
    assert.deepEqual(refreshes, {library: 2, history: 2});
    for (const page of [library, history]) {
      const updates = await page.evaluate(() => ({
        records: window.recordUpdates, storage: window.recordStorageEvents,
        broadcast: localStorage.getItem('dance-record-update'),
      }));
      assert.equal(new Set(updates.records.map(record => record.record_id)).size, 2);
      assert.equal(updates.storage, 0);
      assert.equal(updates.broadcast, null);
      for (const record of updates.records) {
        assert.deepEqual(Object.keys(record).sort(),
          ['at', 'database', 'difficulty', 'record_id', 'song_id', 'update_summary']);
      }
    }
    // Explicit publishers still broadcast once and dispatch once locally.
    await library.evaluate(() => DanceFX.publish({song_id: 1, record_id: 99999}, {idempotent: true}));
    assert.equal(await library.evaluate(() => window.recordUpdates.length), 2);
    await library.evaluate(() => DanceFX.publish({song_id: 1, record_id: 99999}));
    await history.waitForFunction(() => window.recordStorageEvents === 1);
    assert.equal(await library.evaluate(() => window.recordUpdates.length), 3);
    assert.equal(await history.evaluate(() => window.recordUpdates.length), 2);
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
    await context.close();
    await emptyLibraryDoesNotRetry(browser);
    await failedNotificationsDoNotBlockQueue(browser);
    await polledNotificationsAreQueued(browser, 'no-preference');
    await polledNotificationsAreQueued(browser, 'reduce');
  } finally {
    await browser.close();
  }
}

main().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
