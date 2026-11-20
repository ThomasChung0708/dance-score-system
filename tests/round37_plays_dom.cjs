const assert = require('node:assert/strict');
const {chromium} = require(process.argv[2]);
const [base, songId, output] = process.argv.slice(3);

async function main() {
  const browser = await chromium.launch({headless: true, channel: 'msedge'});
  try {
    const page = await browser.newPage({viewport: {width: 1440, height: 1000}});
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    for (const route of ['/', '/history', '/plays']) {
      await page.goto(base + route);
      const links = page.locator('nav[aria-label="主要導覽"] a');
      assert.deepEqual(await links.evaluateAll(nodes => nodes.map(n => [n.textContent.trim(), n.getAttribute('href')])),
        [['歌曲庫', '/'], ['歷史成績', '/history'], ['遊玩排行', '/plays']]);
      assert.equal(await page.locator('a[aria-current="page"]').getAttribute('href'), route);
    }
    await page.waitForSelector('.plays-row');
    assert.equal(await page.locator('.plays-row').count(), 3);
    assert.deepEqual(await page.locator('.plays-table th').allTextContents(),
      ['名次', '歌曲 / 歌手', '總遊玩次數', '最佳燈號', 'BEST EX', '最後遊玩']);
    const known = page.locator(`.plays-row[data-song-id="${songId}"]`);
    assert.equal(await known.locator('.plays-total').textContent(), '3');
    assert.equal(await known.locator('.plays-ex').textContent(), '1482');
    assert.equal(await known.locator('.clear-lamp').getAttribute('data-lamp'), 'AC');
    await page.screenshot({path: output + '/plays-desktop.png', fullPage: true});
    await known.locator('a').focus();
    await page.keyboard.press('Enter');
    await page.waitForURL(url => url.pathname === '/history' && url.searchParams.get('song_id') === songId);
    await page.waitForFunction(() => document.querySelector('#history-play-count').textContent.includes('3 次'));
    await page.goto(base + '/plays');
    await page.getByRole('link', {name: '未入庫歌曲', exact: true}).click();
    await page.waitForURL(url => url.searchParams.get('title') === '未入庫歌曲');
    await page.waitForFunction(() => document.querySelector('#history-count').textContent.includes('共 1 筆'));
    assert.equal(await page.locator('.record-row .record-title').textContent(), '未入庫歌曲');
    await page.goto(base + '/');
    await page.waitForSelector('.song-row');
    await page.locator('#sort').selectOption('plays_desc');
    await page.waitForFunction(id => document.querySelector('.song-row')?.dataset.songId === id, songId);
    await page.locator(`.song-row[data-song-id="${songId}"]`).click();
    await page.waitForFunction(() => document.querySelector('.detail-play-count')?.textContent.includes('總遊玩 3 次'));
    assert.match(await page.locator('.detail-play-count').textContent(), /N 1.*H 2/);
    assert.match(await page.locator(`.song-row[data-song-id="${songId}"] .song-play-count`).textContent(), /遊玩 3 次/);
    await page.goto(base + '/plays');
    await page.waitForSelector('.plays-row');
    await page.setViewportSize({width: 390, height: 844});
    await page.screenshot({path: output + '/plays-mobile.png', fullPage: true});
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    await page.setViewportSize({width: 320, height: 640});
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    await page.route('**/api/plays/top?*', route => route.fulfill({json: {
      songs: [{rank: 1, song_id: Number(songId), title: 'Updated', artist: 'Artist', play_count: 4,
        best_lamp: 'ACP', best_ex_score: 1494, last_played_at: '2026-11-20T12:00:00Z'}], total: 1,
    }}));
    await page.evaluate(() => window.dispatchEvent(new CustomEvent('dance-record-update')));
    await page.waitForFunction(() => document.querySelector('.plays-total')?.textContent === '4');
    assert.equal(await page.locator('.clear-lamp').getAttribute('data-lamp'), 'ACP');
    await page.unroute('**/api/plays/top?*');
    await page.route('**/api/plays/top?*', route => route.fulfill({json: {songs: [], total: 0}}));
    await page.evaluate(() => window.dispatchEvent(new StorageEvent('storage', {key: 'dance-record-update'})));
    await page.waitForSelector('.empty-records');
    assert.match(await page.locator('.empty-records').textContent(), /尚無遊玩紀錄/);
    assert.deepEqual(errors, []);
  } finally {
    await browser.close();
  }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
