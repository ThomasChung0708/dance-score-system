/* Actual history HTML/JS against a disposable catalog and server. */
const assert = require('node:assert/strict');
const {chromium} = require(process.argv[2]);
const base = process.argv[3];
const exactId = process.argv[4];
const markupId = process.argv[5];

async function main() {
  const browser = await chromium.launch({headless: true, channel: 'msedge'});
  try {
    const page = await browser.newPage({viewport: {width: 1200, height: 900}});
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(base + '/history?song_id=' + exactId + '&difficulty=HARD');
    await page.waitForFunction(() => document.querySelector('#history-song').value === 'MAGIC &LOVE');
    const input = page.locator('#history-song');
    const selected = page.locator('#history-song-id');
    const options = page.locator('#history-song-options');
    assert.equal(await selected.inputValue(), exactId);
    assert.equal(await page.locator('#history-difficulty').inputValue(), 'HARD');
    const rowLink = page.locator('.record-title').first();
    await rowLink.waitFor();
    assert.match(await rowLink.getAttribute('href'), new RegExp('song_id=' + exactId));
    await rowLink.click();
    await page.waitForFunction(() => document.querySelector('#history-song').value === 'MAGIC &LOVE');

    const queries = [];
    page.on('request', request => {
      const url = new URL(request.url());
      if (url.pathname === '/api/history/song-search') queries.push(url.searchParams.get('q'));
    });
    await input.fill('m');
    await input.fill('ma');
    await input.fill('magic');
    assert.equal(await selected.inputValue(), '');
    await options.waitFor({state: 'visible'});
    assert.deepEqual(queries, ['magic']);
    const first = options.getByRole('option').first();
    assert.match(await first.textContent(), /MAGIC &LOVE.*Composer.*Pack A/);
    assert.equal(await input.getAttribute('aria-expanded'), 'true');
    await input.press('ArrowDown');
    const active = await input.getAttribute('aria-activedescendant');
    await input.press('ArrowDown');
    assert.notEqual(await input.getAttribute('aria-activedescendant'), active);
    await input.press('ArrowUp');
    assert.equal(await input.getAttribute('aria-activedescendant'), active);
    await input.press('Enter');
    assert.equal(await selected.inputValue(), exactId);
    assert.equal(await input.inputValue(), 'MAGIC &LOVE');
    assert.equal(await input.getAttribute('aria-expanded'), 'false');

    await page.locator('#history-player').fill('TC');
    let requestPromise = page.waitForRequest(request => new URL(request.url()).pathname === '/api/history');
    await page.getByRole('button', {name: '查詢成績'}).click();
    let args = new URL((await requestPromise).url()).searchParams;
    assert.equal(args.get('song_id'), exactId);
    assert.equal(args.get('difficulty'), 'HARD');
    assert.equal(args.get('player_name'), 'TC');

    const historyQueries = [];
    page.on('request', request => {
      const url = new URL(request.url());
      if (url.pathname === '/api/history') historyQueries.push(url.searchParams);
    });
    // Enter before the debounce opens the dropdown must resolve the typed text.
    await input.fill('日本語');
    requestPromise = page.waitForRequest(request => new URL(request.url()).pathname === '/api/history');
    await input.press('Enter');
    args = new URL((await requestPromise).url()).searchParams;
    assert.ok(args.get('song_id'));
    assert.notEqual(args.get('song_id'), exactId);
    assert.equal(await input.inputValue(), '桜の日本語歌曲');
    assert.equal(await selected.inputValue(), args.get('song_id'));

    for (const [query, note, count] of [
      ['magic', '請從清單選擇歌曲', 5],
      ['no such song', '找不到符合的歌曲', 0],
    ]) {
      const before = historyQueries.length;
      await input.fill(query);
      await input.press('Enter');
      await page.waitForFunction(note => document.querySelector('#history-message').textContent === note, note);
      assert.equal(historyQueries.length, before);
      assert.equal(await selected.inputValue(), '');
      assert.equal(await input.inputValue(), query);
      assert.equal(await input.getAttribute('aria-expanded'), 'true');
      assert.equal(await options.getByRole('option').count(), count);
      // Clicking submit after the dropdown opens follows the same validation.
      await page.getByRole('button', {name: '查詢成績'}).click();
      await page.waitForFunction(() => document.querySelector('#history-song').getAttribute('aria-expanded') === 'true');
      assert.equal(historyQueries.length, before);
      assert.equal(await page.locator('#history-message').textContent(), note);
    }

    // State tracking also covers browsers that omit KeyboardEvent.isComposing.
    const beforeComposition = historyQueries.length;
    await input.fill('日本語');
    await input.dispatchEvent('compositionstart');
    const compositionKeys = await input.evaluate(node => {
      const arrow = new KeyboardEvent('keydown', {key: 'ArrowDown', bubbles: true, cancelable: true});
      const enter = new KeyboardEvent('keydown', {key: 'Enter', bubbles: true, cancelable: true});
      node.dispatchEvent(arrow);
      node.dispatchEvent(enter);
      document.querySelector('#history-filter').requestSubmit();
      return {arrowPrevented: arrow.defaultPrevented, enterPrevented: enter.defaultPrevented};
    });
    assert.deepEqual(compositionKeys, {arrowPrevented: false, enterPrevented: true});
    assert.equal(historyQueries.length, beforeComposition);
    assert.equal(await selected.inputValue(), '');
    assert.equal(await input.getAttribute('aria-expanded'), 'false');
    await input.dispatchEvent('compositionend');
    await options.getByRole('option').waitFor();
    await input.dispatchEvent('keydown', {key: 'Enter', isComposing: true});
    await input.dispatchEvent('keydown', {key: 'ArrowDown', isComposing: true});
    assert.equal(await selected.inputValue(), '');
    assert.equal(await input.getAttribute('aria-activedescendant'), null);
    assert.equal(historyQueries.length, beforeComposition);

    await input.fill('日本語');
    await options.waitFor({state: 'visible'});
    await input.press('Escape');
    assert.equal(await input.getAttribute('aria-expanded'), 'false');
    assert.equal(await input.getAttribute('aria-activedescendant'), null);
    await input.fill('日本 語');
    await options.waitFor({state: 'visible'});
    await page.locator('h1').click();
    await options.waitFor({state: 'hidden'});
    await input.focus();
    await options.waitFor({state: 'visible'});
    await input.press('Tab');
    await options.waitFor({state: 'hidden'});
    await input.fill('日本語');
    await options.waitFor({state: 'visible'});
    await options.getByRole('option').click();
    assert.equal(await input.inputValue(), '桜の日本語歌曲');
    assert.ok(await selected.inputValue());

    await input.fill('');
    assert.equal(await selected.inputValue(), '');
    requestPromise = page.waitForRequest(request => new URL(request.url()).pathname === '/api/history');
    await page.getByRole('button', {name: '查詢成績'}).click();
    args = new URL((await requestPromise).url()).searchParams;
    assert.equal(args.has('song_id'), false);
    assert.equal(args.get('difficulty'), 'HARD');
    assert.equal(args.get('player_name'), 'TC');

    let releaseSlow, sawSlow;
    const slowSeen = new Promise(resolve => { sawSlow = resolve; });
    const slowRelease = new Promise(resolve => { releaseSlow = resolve; });
    await page.route('**/api/history/song-search?**', async route => {
      if (new URL(route.request().url()).searchParams.get('q') !== 'slow') return route.continue();
      sawSlow();
      await slowRelease;
      await route.fulfill({json: {songs: [{song_id: 999, title: 'Stale song'}], limit: 20}});
    });
    await input.fill('slow');
    await slowSeen;
    await input.fill('日本語');
    await options.waitFor({state: 'visible'});
    assert.match(await options.textContent(), /桜の日本語歌曲/);
    const oldResponse = page.waitForResponse(response => new URL(response.url()).searchParams.get('q') === 'slow');
    releaseSlow();
    await oldResponse;
    await page.waitForTimeout(100);
    assert.match(await options.textContent(), /桜の日本語歌曲/);
    assert.doesNotMatch(await options.textContent(), /Stale song/);

    await input.fill('no such song');
    await page.locator('.history-song-empty').waitFor();
    assert.match(await options.textContent(), /找不到/);
    await input.fill('<img');
    await options.getByRole('option').waitFor();
    assert.match(await options.textContent(), /<img src=x onerror=alert\(1\)>/);
    assert.equal(await options.locator('img, b').count(), 0);
    await options.getByRole('option').click();
    assert.equal(await selected.inputValue(), markupId);
    await page.setViewportSize({width: 375, height: 812});
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));

    historyQueries.length = 0;
    await page.goto(base + '/history?song_id=999999');
    await page.waitForFunction(() => document.querySelector('#history-song-status').textContent.includes('找不到歌曲標題'));
    await page.locator('.empty-records').waitFor();
    assert.equal(await selected.inputValue(), '999999');
    assert.equal(await input.inputValue(), 'Song #999999');
    assert.equal(historyQueries.length, 1);
    assert.equal(historyQueries[0].get('song_id'), '999999');
    requestPromise = page.waitForRequest(request => new URL(request.url()).pathname === '/api/history');
    await page.getByRole('button', {name: '查詢成績'}).click();
    args = new URL((await requestPromise).url()).searchParams;
    assert.equal(args.get('song_id'), '999999');
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({passed: true}));
  } finally {
    await browser.close();
  }
}

main().catch(error => {
  console.error(error);
  process.exitCode = 1;
});

