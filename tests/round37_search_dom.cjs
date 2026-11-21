const assert = require('node:assert/strict');
const {chromium} = require(process.argv[2]);
const base = process.argv[3];

async function main() {
  const browser = await chromium.launch({headless: true, channel: 'msedge'});
  try {
    const page = await browser.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(base + '/?song_id=1&q=Silly&lamp=AC');
    await page.waitForFunction(() => document.querySelector('#result-count').textContent === '共 1 首歌曲');
    assert.equal(await page.locator('#lamp-filter').inputValue(), 'AC');
    assert.deepEqual(await page.locator('#lamp-filter option').evaluateAll(options =>
      options.map(option => option.value)), ['', 'ACP', 'AC', 'CLEAR', 'FAILED', 'UNPLAYED']);
    assert.equal(await page.locator('#lamp-filter option[value="UNPLAYED"]').textContent(), 'NO PLAY');
    await page.locator('#lamp-filter').selectOption('UNPLAYED');
    await page.waitForURL(url => url.searchParams.get('lamp') === 'UNPLAYED');
    await page.reload();
    await page.waitForFunction(() => document.querySelector('#result-count').textContent === '共 3 首歌曲');
    assert.equal(await page.locator('#lamp-filter').inputValue(), 'UNPLAYED');
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
    assert.equal(songs.has('lamp'), false);
    await page.waitForFunction(() => document.querySelector('#result-count').textContent === '共 4 首歌曲');
    assert.equal(await page.locator('#search').inputValue(), '');
    assert.equal(await page.locator('#sort').inputValue(), 'asc');
    assert.equal(await page.locator('#lamp-filter').inputValue(), '');
    assert.equal(new URL(page.url()).search, '');
    assert.equal(await page.locator('.song-row .song-play-count').count(), 1);
    for (const row of await page.locator('.song-row').all()) {
      assert.equal(await row.locator('.lamp-strip').count(), 1);
      assert.equal(await row.locator(':scope > .lamp-strip').count(), 1);
      assert.equal(await row.locator('.compact-progress .lamp-strip').count(), 0);
      assert.equal(await row.locator('.compact-progress .clear-lamp').count(), 3);
    }
    assert.equal(await page.locator('.song-row').first().evaluate(row => {
      const strip = row.querySelector(':scope > .lamp-strip').getBoundingClientRect();
      return strip.left < row.querySelector('.song-title').getBoundingClientRect().left;
    }), true);

    for (const lamp of ['UNPLAYED', 'UNCLEARED', 'CLEARED']) {
      await page.goto(base + '/history?lamp=' + lamp);
      await page.waitForFunction(() => document.querySelector('#history-count').textContent === '共 2 筆成績 · 本頁 2 筆');
      assert.equal(await page.locator('#history-lamp').inputValue(), '');
    }

    await page.goto(base + '/history?lamp=AC&difficulty=NORMAL&player_name=Eithwa');
    await page.waitForFunction(() => document.querySelector('#history-count').textContent === '共 1 筆成績 · 本頁 1 筆');
    assert.equal(await page.locator('#history-lamp').inputValue(), 'AC');
    assert.equal(await page.locator('.record-row > td:first-child').first()
      .evaluate(cell => getComputedStyle(cell).paddingLeft), '19px');
    assert.deepEqual(await page.locator('#history-lamp option').evaluateAll(options =>
      options.map(option => option.value)), ['', 'ACP', 'AC', 'CLEAR', 'FAILED']);
    assert.equal(await page.locator('#history-lamp option').filter({hasText: 'NO PLAY'}).count(), 0);
    await page.locator('#history-lamp').selectOption('FAILED');
    await page.getByRole('button', {name: '查詢成績', exact: true}).click();
    await page.locator('.empty-records').waitFor();
    assert.equal(new URL(page.url()).searchParams.get('lamp'), 'FAILED');
    await page.reload();
    await page.locator('.empty-records').waitFor();
    assert.equal(await page.locator('#history-lamp').inputValue(), 'FAILED');

    await page.goto(base + '/history?song_id=999999&difficulty=HARD&lamp=FAILED&q=old&record_id=1');
    await page.locator('.empty-records').waitFor();
    await page.locator('#history-player').fill('別人');
    await page.locator('#history-song').fill('Silly');
    const historyRequest = page.waitForRequest(request => new URL(request.url()).pathname === '/api/history');
    await page.locator('#history-clear').focus();
    await page.keyboard.press('Space');
    const history = new URL((await historyRequest).url()).searchParams;
    assert.equal(history.get('offset'), '0');
    for (const key of ['song_id', 'difficulty', 'lamp', 'player_name']) assert.equal(history.has(key), false);
    for (const id of ['history-song', 'history-song-id', 'history-difficulty', 'history-lamp', 'history-player']) {
      assert.equal(await page.locator('#' + id).inputValue(), '');
    }
    await page.waitForFunction(() => document.querySelector('#history-count').textContent === '共 2 筆成績 · 本頁 2 筆');
    assert.equal(await page.locator('.record-row[data-update-kind="NO_UPDATE"]').count(), 0);
    assert.equal(new URL(page.url()).search, '');
    assert.equal(await page.locator('#history-song-options').isHidden(), true);
    await verifyLamps(page);
    assert.deepEqual(errors, []);
  } finally {
    await browser.close();
  }
}

async function verifyLamps(page) {
  await page.evaluate(() => {
    const gallery = document.createElement('section');
    gallery.id = 'lamp-test-gallery';
    gallery.style.cssText = 'display:grid;gap:20px;padding:24px;max-width:600px';
    for (const status of ['ACP', 'AC', 'CLEAR', 'FAILED', 'UNPLAYED', null]) {
      const sample = document.createElement('div');
      sample.className = 'lamp-sample';
      const chart = DanceFX.chartProgress({difficulty: status || 'PENDING', best_lamp: status,
        best_ex_score: null}, {compact: true});
      sample.append(chart);
      DanceFX.lampStrip(sample, status);
      gallery.append(sample);
    }
    document.body.append(gallery);
    gallery.scrollIntoView();
  });
  await page.waitForFunction(() => [...document.querySelectorAll('#lamp-test-gallery .clear-lamp')]
    .every(lamp => lamp.classList.contains('lamp-settled') && lamp.classList.contains('fx-visible')));
  assert.equal(await page.locator('#lamp-test-gallery .compact-progress .lamp-strip').count(), 0);
  const lamps = await page.locator('#lamp-test-gallery .lamp-sample').evaluateAll(samples => samples.map(sample => {
    const lamp = sample.querySelector('.clear-lamp');
    const label = getComputedStyle(lamp.querySelector('.lamp-label'));
    const strip = getComputedStyle(sample.querySelector('.lamp-strip'));
    return {status: lamp.dataset.lamp, label: lamp.textContent, ariaLabel: lamp.getAttribute('aria-label'), colour: label.color,
      fill: label.backgroundImage, background: label.backgroundColor, border: label.borderTopStyle,
      labelAnimation: label.animationName, stripAnimation: strip.animationName,
      labelPlaying: label.animationPlayState, stripPlaying: strip.animationPlayState, width: strip.width};
  }));
  for (const lamp of lamps) {
    assert.equal(lamp.label, lamp.status);
    assert.equal(lamp.ariaLabel, '燈號 ' + lamp.status);
    assert.equal(lamp.width, '7px');
    const animated = ['AC', 'ACP'].includes(lamp.status);
    assert.equal(lamp.labelAnimation, animated ? 'lamp-flow' : 'none');
    assert.equal(lamp.stripAnimation, animated ? 'lamp-flow' : 'none');
    if (animated) {
      assert.equal(lamp.labelPlaying, 'running');
      assert.equal(lamp.stripPlaying, 'running');
    }
    for (const colour of lamp.fill.match(/rgb\([^)]+\)/g) || [lamp.background]) {
      if (colour === 'rgba(0, 0, 0, 0)') continue;
      assert.ok(contrast(lamp.colour, colour) >= 4.5, lamp.status + ' text contrast');
    }
  }
  assert.notEqual(lamps[0].fill, lamps[1].fill);
  assert.equal(lamps.find(lamp => lamp.status === 'PENDING').border, 'dashed');
  assert.equal(lamps.find(lamp => lamp.status === 'UNPLAYED').background, 'rgba(0, 0, 0, 0)');
  const animatedPositions = await page.locator('#lamp-test-gallery [data-lamp=ACP] > .lamp-strip, #lamp-test-gallery [data-lamp=ACP] .lamp-label')
    .evaluateAll(nodes => nodes.map(node => getComputedStyle(node).backgroundPosition));
  await page.waitForFunction(before => {
    const nodes = [...document.querySelectorAll('#lamp-test-gallery [data-lamp=ACP] > .lamp-strip, #lamp-test-gallery [data-lamp=ACP] .lamp-label')];
    return nodes.every((node, index) => getComputedStyle(node).backgroundPosition !== before[index]);
  }, animatedPositions);
  // Scroll away: both the label and strip use the existing visibility observer.
  await page.evaluate(() => {
    document.querySelector('#lamp-test-gallery').style.marginTop = '2000px';
    window.scrollTo(0, 0);
  });
  await page.waitForFunction(() => !document.querySelector('#lamp-test-gallery .lamp-sample').classList.contains('fx-visible'));
  assert.deepEqual(await page.locator('#lamp-test-gallery [data-lamp=ACP] .lamp-label, #lamp-test-gallery [data-lamp=ACP] > .lamp-strip')
    .evaluateAll(nodes => nodes.map(node => getComputedStyle(node).animationPlayState)), ['paused', 'paused']);
  await page.evaluate(() => {
    document.querySelector('#lamp-test-gallery').style.marginTop = '0';
    document.querySelector('#lamp-test-gallery').scrollIntoView();
    document.querySelector('.motion-control').click();
  });
  const staticLamps = () => page.locator('#lamp-test-gallery .lamp-label, #lamp-test-gallery .lamp-strip')
    .evaluateAll(nodes => nodes.map(node => getComputedStyle(node).animationName));
  assert.ok((await staticLamps()).every(name => name === 'none'));
  assert.equal(await page.evaluate(() => localStorage.getItem('dance-motion')), 'reduce');
  await page.evaluate(() => document.querySelector('.motion-control').click());
  await page.emulateMedia({reducedMotion: 'reduce'});
  assert.ok((await staticLamps()).every(name => name === 'none'));
  // Reduced motion stops movement without losing the rainbow/gold fill.
  assert.equal(await page.locator('#lamp-test-gallery .lamp-label').first()
    .evaluate(node => getComputedStyle(node).backgroundImage), lamps[0].fill);
  await page.emulateMedia({reducedMotion: 'no-preference'});
  await page.evaluate(() => {
    const gallery = document.querySelector('#lamp-test-gallery');
    const pair = document.createElement('div');
    pair.className = 'double-update';
    pair.id = 'lamp-test-upgrade';
    pair.append(DanceFX.lamp('ACP', {upgrade: true}), DanceFX.lamp('FAILED', {failure: true}));
    gallery.append(pair);
    pair.scrollIntoView();
    DanceFX.notify({catalog_path: 'lamp-test.sqlite3', record_id: 999001,
      title: 'ACP lamp check', difficulty: 'HARD', lamp_status: 'ACP', ex_score: 100,
      update_summary: {kind: 'LAMP_UPDATE', label: 'NEW LAMP',
        lamp: {previous: 'AC', current: 'ACP', label: 'NEW LAMP'}},
      achievement_feedback: {tone: 'ACP', events: ['NEW_LAMP']}});
  });
  await page.waitForFunction(() => [...document.querySelectorAll('#lamp-test-upgrade .clear-lamp')]
    .every(lamp => lamp.classList.contains('lamp-settled')));
  assert.deepEqual(await page.locator('#lamp-test-upgrade .clear-lamp')
    .evaluateAll(nodes => nodes.map(node => node.dataset.entryAnimation)), ['lamp-upgrade', 'lamp-failed']);
  const notification = page.locator('#achievement-notifications');
  assert.equal(await notification.getAttribute('data-last-toast-animation'), 'toast-enter');
  assert.equal(await notification.getAttribute('data-last-lamp-animation'), 'lamp-upgrade');
  assert.equal(await notification.locator('.lamp-label').textContent(), 'ACP');
  await page.locator('#lamp-test-gallery').evaluate(node => node.remove());
}

function contrast(a, b) {
  const luminance = colour => {
    const rgb = colour.match(/[\d.]+/g).slice(0, 3).map(Number).map(value => {
      value /= 255;
      return value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
    });
    return rgb[0] * 0.2126 + rgb[1] * 0.7152 + rgb[2] * 0.0722;
  };
  const [dark, light] = [luminance(a), luminance(b)].sort((x, y) => x - y);
  return (light + 0.05) / (dark + 0.05);
}

main().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
