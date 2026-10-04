// 実際のテンプレートの同期コードを、通信しないDBの偽物で検証する。
const fs = require('fs');
const vm = require('vm');
const assert = require('assert');
const html = fs.readFileSync(process.argv[2], 'utf8');
const start = html.indexOf('  var remoteDb = null');
const stop = html.indexOf('  function setFeedback', start);
const startSync = html.indexOf('  function startSync(db)');
const stopSync = html.indexOf('  showSyncState(false);', startSync);
const code = html.slice(start, stop) + html.slice(startSync, stopSync);
const collections = {read: {'2026-10-03': {ids: {legacy: 1}}}, read_items: {}, later: {}, feedback: {}};
const listeners = {};
const writes = [];
function snapshot(name) {
  return {docs: Object.entries(collections[name]).map(([id, body]) => ({id, data: () => body}))};
}
const db = {
  collection(name) {
    return {onSnapshot(callback) {
      (listeners[name] ||= []).push(callback);
      callback(snapshot(name));
    }};
  },
  doc(path) {
    return {set(body) {
      const [name, id] = path.split('/');
      collections[name][id] = JSON.parse(JSON.stringify(body));
      writes.push(path);
      for (const callback of listeners[name] || []) callback(snapshot(name));
      return Promise.resolve();
    }, delete() {throw new Error('既読の解除は旧記録より優先するため、削除しない');}};
  }
};
function client(localRead = {}) {
  const context = vm.createContext({
    read: localRead, later: {}, feedback: {}, byId: {}, SRC: {}, D: {date: '2026-10-04'},
    saveRead() {}, saveStore() {}, apply() {}, renderMap() {}, addRetained() {}, syncLater() {},
    setTimeout, Promise, LATER_KEY: 'later'
  });
  vm.runInContext(code + '\nshowSyncState = function(){}; startSync(db);', vm.createContext(Object.assign(context, {db})));
  return context;
}
async function flush() {for (let i = 0; i < 4; i++) await new Promise(resolve => setImmediate(resolve));}
(async () => {
  const a = client(), b = client();
  assert.strictEqual(a.read.legacy, 1);
  assert.strictEqual(b.read.legacy, 1);
  a.syncRead('first', true, '2026-10-04');
  b.syncRead('second', true, '2026-10-04');
  await flush();
  for (const c of [a, b]) {
    assert.strictEqual(c.read.first, 1);
    assert.strictEqual(c.read.second, 1);
  }
  a.syncRead('legacy', false, '2026-10-03');
  await flush();
  assert.strictEqual(a.read.legacy, undefined);
  assert.strictEqual(b.read.legacy, undefined);
  // 古い端末に既読が残っていても、明示した未読を初期移行で復活させない。
  const c = client({legacy: 1});
  await flush();
  assert.strictEqual(c.read.legacy, undefined);
  assert(writes.every(path => path.startsWith('read_items/')));
  assert.strictEqual(collections.read['2026-10-03'].ids.legacy, 1);
})().catch(error => {console.error(error); process.exitCode = 1;});
