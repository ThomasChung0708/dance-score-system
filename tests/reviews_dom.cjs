/* Real rendered-DOM tests against a disposable GUI server. No live game. */
const assert = require('node:assert/strict');
const {chromium} = require(process.argv[2]);
const base = process.argv[3];
const mode = process.argv[4];

async function main() {
  const browser = await chromium.launch({headless: true, channel: 'msedge'});
  try {
    const page = await browser.newPage({viewport: {width: 1500, height: 1050}});
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    await page.goto(base + '/reviews');
    if (mode === 'player-setting') {
      assert.equal(await page.locator('#result-player').inputValue(), 'TC');
      await page.route('**/api/monitor/windows?**', route => route.fulfill({
        status: 200, contentType: 'application/json', body: JSON.stringify({windows: []})
      }));
      await page.goto(base + '/monitor');
      const input = page.locator('#monitor-player');
      assert.equal(await input.inputValue(), 'TC');
      const saved = page.waitForResponse(r => r.url().endsWith('/api/player-settings') && r.request().method() === 'POST');
      await input.fill('Friend☆');
      await input.blur();
      assert.equal((await saved).status(), 200);
      await page.goto(base + '/monitor');
      assert.equal(await page.locator('#monitor-player').inputValue(), 'Friend☆');
      await page.goto(base + '/reviews');
      assert.equal(await page.locator('#result-player').inputValue(), 'Friend☆');
      assert.deepEqual(errors, []);
      console.log(JSON.stringify({mode, passed: true}));
      return;
    }
    if (mode.startsWith('source-')) {
      await page.route('**/api/monitor/windows?**', route => route.fulfill({
        status: 200, contentType: 'application/json', body: JSON.stringify({windows: []})
      }));
      await page.goto(base + '/monitor');
      const prompt = page.locator('#source-choice');
      await prompt.waitFor({state: 'visible'});
      if (mode === 'source-folder' || mode === 'source-custom') {
        if (mode === 'source-custom') await prompt.getByRole('button', {name: '自製譜', exact: true}).click();
        else assert.match(await prompt.textContent(), /這首是哪個資料夾的譜面？/);
        assert.equal(await prompt.getByRole('button', {name: /Pack A/}).count(), 2);
        assert.match(await prompt.textContent(), /HARD \/ LV 17 \/ 11 音符/);
        const copy = prompt.getByRole('button', {name: /Folder B/});
        await copy.focus();
        await page.keyboard.press('Enter');
      } else if (mode === 'source-dismiss') {
        await prompt.getByRole('button', {name: '稍後到待確認處理'}).click();
      } else {
        assert.match(await prompt.textContent(), /這首是官方譜還是自製譜？/);
        await prompt.getByRole('button', {name: '官方譜', exact: true}).click();
      }
      await prompt.waitFor({state: 'hidden'});
      await page.waitForTimeout(1800);
      assert.equal(await prompt.isVisible(), false);
      await page.goto(base + '/reviews');
      await page.waitForSelector('#review-inputs:not([disabled])');
      if (mode === 'source-dismiss') {
        assert.equal(await page.locator('#source-choice').isVisible(), true);
      } else {
        assert.equal(await page.locator('#candidate-source').inputValue(), ['source-folder', 'source-custom'].includes(mode) ? 'custom' : 'official');
        assert.ok(await page.locator('#review-song').inputValue());
      }
      assert.deepEqual(errors, []);
      console.log(JSON.stringify({mode, passed: true}));
      return;
    }
    if (mode === 'chartless') {
      await page.goto(base + '/history');
      await page.waitForSelector('.empty-records');
      assert.match(await page.locator('#history-count').textContent(), /共 0 筆成績/);
      assert.equal(await page.locator('.empty-records').textContent(), '尚無符合條件的正式成績。');
      assert.equal(await page.locator('.record-title').count(), 0);
      assert.deepEqual(errors, []);
      console.log(JSON.stringify({mode, passed: true}));
      return;
    }
    if (mode === 'title-bound' || mode === 'title-fallback') {
      await page.waitForSelector('#review-inputs:not([disabled])');
      const summary = await page.locator('#bound-summary').textContent();
      if (mode === 'title-bound') {
        assert.match(summary, /^Watch Out Pt\.2 · #1/);
        assert.ok(!summary.includes('beatmania'));
        assert.ok(!summary.includes('歌曲待確認'));
      } else {
        assert.match(summary, /^歌曲待確認 · /);
        await page.goto(base + '/history');
        await page.waitForSelector('.record-title');
        assert.equal(await page.locator('.record-title').textContent(), 'Watch Out Pt.2');
        await page.goto(base + '/');
        await page.waitForSelector('.song-row[data-song-id="1"]');
        assert.match(await page.locator('.song-row[data-song-id="1"] .song-title').textContent(),
          /^Watch Out Pt\.2/);
        await page.getByRole('button', {name: /Recent Update/}).click();
        await page.waitForSelector('.recent-update-row');
        assert.match(await page.locator('.recent-update-row .song-title').textContent(),
          /^Watch Out Pt\.2 \/ HARD/);
        await page.route('**/api/monitor/windows?**', route => route.fulfill({
          status: 200, contentType: 'application/json', body: JSON.stringify({windows: []})
        }));
        await page.goto(base + '/monitor');
        await page.waitForFunction(() =>
          document.querySelector('#monitor-song dd')?.textContent === 'Watch Out Pt.2');
        assert.match(await page.locator('#monitor-session').textContent(), /歌曲：Watch Out Pt\.2/);
      }
      assert.deepEqual(errors, []);
      console.log(JSON.stringify({mode, passed: true}));
      return;
    }
    if (mode === 'responsive') {
      // Read-only monitor fixture: no OS window enumeration or capture needed.
      await page.route('**/api/monitor/windows?**', route => route.fulfill({
        status:200, contentType:'application/json', body:JSON.stringify({windows:[]})
      }));
      for (const route of ['/', '/reviews', '/history', '/monitor']) {
        const gameResponse = route === '/' || route === '/history'
          ? page.waitForResponse(response => new URL(response.url()).pathname === '/api/game')
          : null;
        await page.goto(base + route);
        await page.waitForSelector('.motion-control');
        if (gameResponse) {
          assert.equal((await gameResponse).status(), 200);
          assert.equal(await page.locator('a[href="/monitor"], a[href="/reviews"]').count(), 0);
        }
        for (const width of [390, 700, 768]) {
          await page.setViewportSize({width, height:932});
          const actual = await page.evaluate(() => ({
            viewport:innerWidth, document:document.documentElement.scrollWidth
          }));
          assert.ok(actual.document <= actual.viewport + 1, route + ': ' + JSON.stringify(actual));
        }
      }
      assert.deepEqual(errors, []);
      console.log(JSON.stringify({mode, passed:true}));
      return;
    }
    if (mode === 'unprocessed') {
      await page.waitForSelector('#process-panel:not([hidden])');
      await page.locator('#discard-review').click();
      await page.locator('#commit-discard').click();
      await page.waitForSelector('#review-empty:not([hidden])');
      assert.equal(await page.locator('#selected-card').isVisible(), false);
      assert.deepEqual(errors, []);
      console.log(JSON.stringify({mode, passed:true}));
      return;
    }
    await page.waitForSelector('#review-inputs:not([disabled])');
    if (mode === 'safety') {
      const items = page.locator('.review-item');
      assert.equal(await items.count(), 2);
      const selected = await page.locator('.review-item.selected').getAttribute('data-session-id');
      const other = page.locator('.review-item:not(.selected)');
      const otherId = await other.getAttribute('data-session-id');
      await items.first().focus();
      // Compare actual node identity inside the page, including a poll whose
      // payload changes. The button itself must survive both updates.
      assert.equal(await page.evaluate(async () => {
        const before = document.activeElement;
        await reload();
        return before === document.activeElement && before.isConnected;
      }), true);
      await page.locator('#value-editor summary').click();
      await page.locator('#review-perfect').fill('9');
      assert.equal(await page.evaluate(() => {
        const event = new Event('beforeunload', {cancelable: true});
        window.dispatchEvent(event);
        return event.defaultPrevented;
      }), true);
      page.once('dialog', dialog => dialog.dismiss());
      await other.click();
      assert.match(await page.locator('#review-identity').textContent(), new RegExp(selected));
      assert.equal(await page.locator('#review-perfect').inputValue(), '9');
      let release;
      const gate = new Promise(resolve => { release = resolve; });
      let started;
      const requested = new Promise(resolve => { started = resolve; });
      await page.route('**/api/reviews/save', async route => {
        started();
        await gate;
        await route.continue();
      });
      await page.locator('#advanced-tools summary').first().click();
      await page.locator('#save-review-draft').click();
      await requested;
      page.once('dialog', dialog => dialog.accept());
      await other.click();
      await page.waitForFunction(id => document.querySelector('#review-identity').textContent.includes(id)
        && !document.querySelector('#review-inputs').disabled, otherId);
      const response = page.waitForResponse('**/api/reviews/save');
      release();
      await response;
      await page.waitForFunction(() => !busy);
      assert.match(await page.locator('#review-identity').textContent(), new RegExp(otherId));
      assert.equal(await page.locator('#review-perfect').inputValue(), '10');
      assert.equal(await page.evaluate(() => dirty), false);
      assert.deepEqual(errors, []);
      console.log(JSON.stringify({mode, passed: true}));
      return;
    }
    assert.equal(await page.locator('.review-item').count(), 1);
    assert.equal(await page.locator('#selected-card').isVisible(), true);
    assert.equal(await page.locator('#review-image').isVisible(), true);
    assert.equal(await page.locator('#values-summary dd').count(), 8);
    assert.match(await page.locator('#review-reason').textContent(), /核對|確認/);
    assert.match(await page.locator('#bound-summary').textContent(), /Pack A/);
    assert.equal(await page.locator('.primary-actions button.primary').count(), 1);
    assert.equal(await page.getByRole('button', {name: '確認資料（另存）', exact: true}).count(), 0);
    assert.equal(await page.locator('#advanced-tools').getAttribute('open'), null);
    assert.equal(await page.locator('#song-picker').getAttribute('open'), null);
    const api = (path) => page.evaluate(async path => {
      const token = document.querySelector('meta[name="dance-token"]').content;
      const response = await fetch('/api/reviews' + path, {headers: {'X-Dance-Token': token}});
      return response.json();
    }, path);
    if (mode === 'discard') {
      await page.locator('#discard-review').click();
      assert.equal(await page.locator('#discard-dialog').isVisible(), true);
      await page.locator('#cancel-discard').click();
      assert.equal((await api('')).results.length, 1);
      await page.locator('#discard-review').click();
      await page.locator('#commit-discard').click();
      await page.waitForSelector('#review-empty:not([hidden])');
      assert.equal(await page.locator('#selected-card').isVisible(), false);
      assert.equal((await api('')).results.length, 0);
    } else {
      await page.locator('#song-picker summary').first().click();
      await page.locator('#review-search').fill('JOINT');
      await page.locator('#search-candidates').click();
      await page.waitForFunction(() => document.getElementById('candidate-count').textContent.includes('2 首'));
      assert.equal(await page.locator('#review-song option').count(), 3);
      await page.locator('#value-editor summary').click();
      await page.locator('#review-perfect').fill('9');
      await page.locator('#review-cool').fill('2');
      await page.waitForFunction(() => document.querySelector('#values-summary dd').textContent === '9');
      await page.locator('#advanced-tools summary').first().click();
      await page.locator('#save-review-draft').click();
      await page.waitForFunction(() => document.getElementById('review-save-state').textContent.includes('草稿已保存'));
      const row = (await api('')).results[0];
      assert.equal(row.status, 'draft');
      assert.equal(row.values.perfect, 9);
      await page.locator('#review-player-verified').check();
      await page.locator('#review-identity-verified').check();
      await page.waitForFunction(() => !document.getElementById('open-submission').disabled);
      await page.locator('#open-submission').click();
      await page.waitForSelector('#submission-dialog[open]');
      // Opening and cancelling the summary must not write a confirmed sidecar.
      assert.equal((await api('')).results[0].status, 'draft');
      await page.locator('#cancel-submission').click();
      assert.equal((await api('')).results.length, 1);
      await page.locator('#open-submission').click();
      await page.locator('#submission-final-ack').check();
      if (mode === 'retry') {
        await page.route('**/api/reviews/submit', route => route.fulfill({
          status:409, contentType:'application/json', body:JSON.stringify({error:'測試用儲存失敗'})
        }));
        await page.locator('#commit-submission').click();
        await page.waitForFunction(() => document.getElementById('submission-error').textContent.includes('測試用儲存失敗'));
        assert.equal((await api('')).results[0].status, 'draft');
        await page.unroute('**/api/reviews/submit');
      }
      if (mode === 'receipt') {
        await page.route('**/api/reviews/submit', async route => {
          const result = await route.fetch();
          assert.equal(result.status(), 200);
          await route.fulfill({status:503, contentType:'application/json',
            body:JSON.stringify({error:'測試用提交後遺失回應'})});
        });
      }
      await page.locator('#commit-submission').click();
      await page.waitForSelector('#review-empty:not([hidden])');
      assert.equal(await page.locator('#selected-card').isVisible(), false);
      assert.equal((await api('')).results.length, 0);
    }
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({mode, passed: true, checks: [
      'one_card', 'screenshot', 'values', 'pack', 'plain_reason', 'one_primary_save',
      'no_duplicate_confirm', 'advanced_disclosures', 'empty_state', mode
    ]}));
  } finally {
    await browser.close();
  }
}

main().catch(e => {
  console.error(e.stack);
  process.exitCode = 1;
});
