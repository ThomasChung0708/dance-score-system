const assert = require('node:assert/strict');
const {chromium} = require(process.argv[2]);
const base = process.argv[3];

async function main() {
  const browser = await chromium.launch({headless: true, channel: 'msedge'});
  try {
    const page = await browser.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(base + '/?song_id=1&q=Silly');
    await page.waitForFunction(() => document.querySelector('#result-count').textContent === '共 4 首歌曲');
    await page.getByRole('button', {name: '官方歌曲', exact: false}).click();
    await page.locator('#sort').selectOption('desc');
    await page.locator('#search').fill('missing');
    const songsRequest = page.waitForRequest(request => {
      const url = new URL(request.url());
      return url.pathname === '/api/songs' && url.searchParams.get('category') === 'all';
    });
    await page.locator('#clear-search').focus();
    await page.keyboard.press('Enter');
    const songs = new URL((await songsRequest).url()).searchParams;
    assert.equal(songs.get('q'), '');
    assert.equal(songs.get('category'), 'all');
    assert.equal(songs.get('order'), 'asc');
    assert.equal(songs.get('offset'), '0');
    assert.equal(songs.has('pack_id'), false);
    assert.equal(songs.has('root_id'), false);
    await page.waitForFunction(() => document.querySelector('#result-count').textContent === '共 4 首歌曲');
    assert.equal(await page.locator('#search').inputValue(), '');
    assert.equal(await page.locator('#sort').inputValue(), 'asc');
    assert.equal(new URL(page.url()).search, '');

    await page.goto(base + '/history?song_id=999999&difficulty=HARD&q=old&record_id=1');
    await page.locator('.empty-records').waitFor();
    await page.locator('#history-player').fill('別人');
    await page.locator('#history-song').fill('Silly');
    const historyRequest = page.waitForRequest(request => new URL(request.url()).pathname === '/api/history');
    await page.locator('#history-clear').focus();
    await page.keyboard.press('Space');
    const history = new URL((await historyRequest).url()).searchParams;
    assert.equal(history.get('offset'), '0');
    for (const key of ['song_id', 'difficulty', 'player_name']) assert.equal(history.has(key), false);
    for (const id of ['history-song', 'history-song-id', 'history-difficulty', 'history-player']) {
      assert.equal(await page.locator('#' + id).inputValue(), '');
    }
    await page.waitForFunction(() => document.querySelector('#history-count').textContent === '共 4 筆成績 · 本頁 4 筆');
    assert.equal(new URL(page.url()).search, '');
    assert.equal(await page.locator('#history-song-options').isHidden(), true);
    assert.deepEqual(errors, []);
  } finally {
    await browser.close();
  }
}

main().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
