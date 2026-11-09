// Execute the actual generated report script with a small DOM fixture.
// This is a unit check, not a browser or a substitute for visual verification.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');

const report = fs.readFileSync(process.argv[2], 'utf8');
const match = report.match(/<script>([\s\S]*?)<\/script>/);
assert.ok(match, 'report script is present');
const handlers = new Map();
const makeControl = value => ({ value, addEventListener: (name, callback) => handlers.set(name, callback) });
const controls = {
  search: makeControl(''), pack: makeControl(''), status: makeControl(''),
  'visible-count': { textContent: '' }, 'no-matches': { hidden: true },
};
const rows = [
  { dataset: { search: 'EOS Ginkiha', pack: 'Blue\'s 1st step', status: 'ok', format: 'sm' }, hidden: false },
  { dataset: { search: 'EOS Ginkiha', pack: 'Blue\'s 1st step', status: 'partial', format: 'dwi' }, hidden: false },
  { dataset: { search: '測試歌 Artist', pack: 'connect', status: 'failed', format: 'osu' }, hidden: false },
];
const document = {
  getElementById: id => controls[id],
  querySelectorAll: selector => selector === '#songs tbody tr' ? rows : [],
};
const context = vm.createContext({ document });
vm.runInContext(match[1], context, { timeout: 1000 });
const refresh = () => vm.runInContext('filterRows()', context, { timeout: 1000 });
assert.equal(controls['visible-count'].textContent, '顯示 3 / 3 個歌曲資料夾');
controls.search.value = 'gINKiha'; refresh();
assert.deepEqual(rows.map(r => r.hidden), [false, false, true]);
controls.status.value = 'partial'; refresh();
assert.deepEqual(rows.map(r => r.hidden), [true, false, true]);
controls.status.value = 'failed'; refresh();
assert.ok(rows.every(r => r.hidden));
assert.equal(controls['no-matches'].hidden, false);
controls.search.value = ''; controls.status.value = '';
controls.pack.value = 'CONNECT'; refresh();
assert.deepEqual(rows.map(r => r.hidden), [true, true, false]);
assert.equal(controls['no-matches'].hidden, true);
console.log('Report script: initial count, case-insensitive search, combined filters, empty state, pack filter PASS');
