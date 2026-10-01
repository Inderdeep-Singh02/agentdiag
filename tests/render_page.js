// Loads a Report file's scripts under node with a small DOM stub, lets boot() run, then
// routes to each hash given and prints one JSON line per route: what #main holds and
// whether it is a render error. A test helper for tests/test_report.py (ticket 15).
//
//   node tests/render_page.js <report.html> '<json list of hashes>' [<routes.json>]
//
// A Report file must never fetch, so without <routes.json> fetch throws. With it (a page with
// no view embedded, as `agentdiag serve` would send it; ticket 28), fetch answers each /api/...
// path the file maps with that JSON, and every other path with a 404.
//
// Each printed line also lists the scrolls the route made, in order: {top: true} for
// window.scrollTo, {into: <data-id>} for an element's scrollIntoView (ticket 28's ?cite=). An
// entry of the hash list may instead be {"click": {<dataset>}}: a click on an element carrying
// that dataset goes to the page's delegated onCite, and the line says where the page went
// and which Spans it marked cited. An entry {"eval": "<expression>"} evaluates the expression in
// the page (a pure function of the renderer, such as selectionExpression) and prints its value
// (ticket 16).
const fs = require('fs'), vm = require('vm');
const html = fs.readFileSync(process.argv[2], 'utf8');
const hashes = JSON.parse(process.argv[3] || '[""]');
const routes = process.argv[4] ? JSON.parse(fs.readFileSync(process.argv[4], 'utf8')) : null;
const served = path => Object.prototype.hasOwnProperty.call(routes, path)
  ? Promise.resolve({ok: true, status: 200, json: () => Promise.resolve(routes[path])})
  : Promise.resolve({ok: false, status: 404, json: () => Promise.resolve(null)});
const scripts = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m => m[1]);
const els = {};
const mk = id => els[id] || (els[id] = {id, innerHTML: '', style: {}, textContent: '', value: '',
  classList: {toggle() {}, add() {}, remove() {}}, insertAdjacentHTML() {}});
let scrolls = [];
const byDataId = () => [...(els.main ? els.main.innerHTML : '').matchAll(/data-id="([^"]*)"/g)]
  .map(m => ({dataset: {id: m[1]}, classList: {toggle() {}}, scrollIntoView() { scrolls.push({into: m[1]}); }}));
const document = {body: {classList: {toggle() {}}, insertAdjacentHTML() {}},
  documentElement: {setAttribute() {}, removeAttribute() {}}, getElementById: mk,
  querySelectorAll: selector => selector === '[data-id]' ? byDataId() : [],
  querySelector: () => null, addEventListener() {}};
const window = {document, addEventListener() {}, scrollTo() { scrolls.push({top: true}); }, innerWidth: 1200};
window.window = window;
const location = {hash: ''};
const context = {window, document, location, localStorage: {getItem: () => null, setItem() {}},
  console, fetch: routes ? served : () => { throw new Error('the Report file fetched'); }};
vm.createContext(context);
scripts.forEach(s => vm.runInContext(s, context));
(async () => {
  for (const hash of hashes) {
    scrolls = [];
    if (typeof hash === 'object' && 'eval' in hash) {
      console.log(JSON.stringify({eval: hash.eval, value: vm.runInContext(hash.eval, context)}));
      continue;
    }
    if (typeof hash === 'object') {
      const el = {dataset: hash.click, classList: {contains: () => false}};
      context.__click = {target: {closest: () => el}};
      vm.runInContext('onCite(__click)', context);
      console.log(JSON.stringify({click: hash.click, hash: location.hash,
        cited: vm.runInContext('[...citedSpans]', context), scrolls}));
      continue;
    }
    location.hash = hash;
    await vm.runInContext('route()', context);
    const main = els.main.innerHTML;
    console.log(JSON.stringify({hash, error: main.includes('render error') ? main.slice(0, 1500) : null,
      main, scrolls}));
  }
})().catch(e => { console.error(e.stack || e); process.exit(1); });
