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
    if (mode === 'responsive') {
      // Read-only monitor fixture: no OS window enumeration or capture needed.
      await page.route('**/api/monitor/windows?**', route => route.fulfill({
        status:200, contentType:'application/json', body:JSON.stringify({windows:[]})
      }));
      for (const route of ['/', '/reviews', '/history', '/monitor']) {
        await page.goto(base + route);
        await page.waitForSelector('.motion-control');
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
