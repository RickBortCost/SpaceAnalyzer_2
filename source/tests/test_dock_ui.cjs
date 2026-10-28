// Analisi X1 ↔ X2 generale (dock), riduzione a barra e riordino drag&drop dei grafici.
// Pagina e script reali caricati dal servizio C#; canvas simulato, dati LIVE deterministici,
// registrazione fixture_A.csv per la pagina REGISTRAZIONI.
const assert = require('node:assert/strict');
const {JSDOM, VirtualConsole} = require(process.env.JSDOM_PATH || 'jsdom');
const [base] = process.argv.slice(2);
const errors = [], pages = [];
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
const wait = async (fn, label, timeout = 15000) => {const end = Date.now() + timeout; while (Date.now() < end) {if (await fn()) {console.error('PASS ' + label); return;} await pause(30);} throw Error('Timeout ' + label + '; ' + errors.join('; '));};
const api = async route => (await fetch(base + route)).json();
const network = [];

async function page() {
  const html = await (await fetch(base)).text(), vc = new VirtualConsole();
  vc.on('jsdomError', e => errors.push(e.message));
  const dom = new JSDOM(html, {url: base, runScripts: 'dangerously', resources: 'usable', pretendToBeVisual: true, virtualConsole: vc, beforeParse(w) {
    w.FormData = globalThis.FormData; w.AbortController = globalThis.AbortController;
    w.fetch = async (url, options) => {network.push(String(url)); try {return await fetch(new URL(url, base), options);} catch (e) {errors.push('fetch ' + url + ': ' + e.message); throw e;}};
    w.ResizeObserver = class {observe() {} disconnect() {}};
    w.HTMLCanvasElement.prototype.getBoundingClientRect = () => ({x: 0, y: 0, left: 0, top: 0, width: 950, height: 560, right: 950, bottom: 560});
    const ctx = new Proxy({measureText: text => ({width: String(text).length * 7})}, {get: (obj, key) => key in obj ? obj[key] : (() => {})});
    w.HTMLCanvasElement.prototype.getContext = () => ctx;
    w.addEventListener('error', e => errors.push(e.message));
  }});
  pages.push(dom); const w = dom.window;
  await new Promise(resolve => w.addEventListener('load', resolve, {once: true}));
  assert.deepEqual(errors, [], 'The real page must load without JavaScript errors');
  w.eval('window.uiTest={get liveCharts(){return liveCharts},get historyCharts(){return historyCharts},showAppPage,saveWorkspace,renderSelected,renderHistorySignals,selectHistoryFile,loadHistory,request,restoreView,chartSnapshot,newHistoryChartFromSignal,feed(data,nodes,newTypes){series=data;selected=new Map(nodes);types=newTypes;liveCharts.update(series,selected,types);},stopPolling(){polling=false;clearTimeout(pollTimer);clearTimeout(workspaceTimer);}};');
  const $ = id => w.document.getElementById(id);
  await wait(() => $('endpoint').value, 'configuration loaded'); await w.opcuaConfigReady;
  return {dom, w, $, t: w.uiTest};
}

const transfer = () => ({store: {}, types: [], effectAllowed: '', dropEffect: '', setData(k, v) {this.store[k] = String(v); if (!this.types.includes(k)) this.types.push(k);}, getData(k) {return this.store[k] || '';}});
function drag(w, el, type, tr) {const e = new w.Event(type, {bubbles: true, cancelable: true}); Object.defineProperty(e, 'dataTransfer', {value: tr}); el.dispatchEvent(e); return e;}

(async () => {
  const {w, $, t} = await page(), checks = [];
  try {
    t.stopPolling(); await pause(100);
    const ws = t.liveCharts, dock = $('live-analysis-dock');
    // ---- dock: un solo pannello, quello del grafico attivo ----
    assert.ok(dock && ws.dock === dock && !dock.open, 'dock presente e chiuso all\'apertura');
    assert.equal(w.document.querySelectorAll('.marker-analysis').length, 0, 'nessuna Analisi X1-X2 nei grafici');
    const g1 = ws.active; ws.add(); ws.add(); const [a, b, c] = ws.charts;
    assert.equal(ws.active, c);
    const owner = () => [...ws.charts].filter(g => dock.contains(g.markerPanel));
    assert.equal(JSON.stringify(owner().map(x=>x.id)), JSON.stringify([c.id]), 'il pannello del grafico attivo è nel dock');
    ws.activate(a); assert.equal(owner().length, 1); assert.equal(owner()[0], a); assert.ok(a.markerHome.querySelector('.marker-panel') === null && c.markerHome.contains(c.markerPanel));
    assert.match(dock.querySelector('.analysis-dock-graph').textContent, new RegExp(a.name));
    dock.open = true; dock.dispatchEvent(new w.Event('toggle'));
    assert.ok(ws.analysisOpen(a) && !ws.analysisOpen(b));
    assert.match(dock.textContent, /Δt/); assert.ok(dock.querySelector('[data-chart-role="markerDataBody"]'));
    checks.push('dock generale con pannello del grafico attivo');
    // il clic su un grafico lo rende attivo e sposta l'analisi
    b.element.dispatchEvent(new w.Event('pointerdown', {bubbles: true})); assert.equal(ws.active, b); assert.equal(owner()[0], b);
    // marker condivisi: il dock mostra i valori del grafico attivo
    ws.setMarkers({x1: 1000, x2: 4000}, b); assert.match(dock.querySelector('.marker-inline-delta').textContent, /3/);
    checks.push('attivazione sposta l\'analisi, Δt nel dock');
    // ---- riduzione a barra ----
    const btn = x => x.element.querySelector('[data-chart-role="collapse"]'), fs = x => x.element.querySelector('[data-chart-role="expand"]');
    assert.ok(btn(a) && btn(a).nextElementSibling === fs(a), 'tasto accanto al fullscreen');
    btn(b).click(); assert.ok(b.collapsed && b.element.classList.contains('chart-collapsed') && b.trend.collapsed);
    assert.equal(btn(b).getAttribute('aria-pressed'), 'true');
    assert.equal(ws.root.style.gridTemplateRows.split(' ').length >= 3 && ws.root.style.gridTemplateRows.includes(' auto '), true, 'la riga del grafico ridotto prende l\'altezza della barra');
    btn(b).click(); assert.ok(!b.collapsed && !b.element.classList.contains('chart-collapsed') && ws.root.style.gridTemplateRows === '');
    btn(a).click(); btn(b).click(); btn(c).click(); assert.ok(ws.charts.every(x => x.collapsed));
    const snap = w.chartSnapshot(ws); assert.equal(JSON.stringify(snap.charts.map(x => x.collapsed)), '[true,true,true]');
    ws.restore(JSON.parse(JSON.stringify(snap)), ws.nodes); assert.ok(ws.charts.every(x => x.collapsed), 'stato ridotto ripristinato');
    for (const x of ws.charts) btn(x).click();
    checks.push('riduzione a barra e persistenza');
    // ---- riordino ----
    const [g, h, k] = ws.charts, names = () => ws.charts.map(x => x.name);
    const hd = x => x.element.querySelector('.chart-drag-handle'); assert.ok(hd(g) && hd(g).draggable);
    const before = names();
    const tr = transfer(); drag(w, hd(g), 'dragstart', tr); assert.ok(tr.types.includes('application/x-opcua-chart-order'));
    w.HTMLElement.prototype.getBoundingClientRect = function () {return {left: 0, top: 0, width: 400, height: 300, right: 400, bottom: 300};};
    const over = drag(w, k.element, 'dragover', tr); assert.ok(over.defaultPrevented);
    const drop = new w.Event('drop', {bubbles: true, cancelable: true}); Object.defineProperty(drop, 'dataTransfer', {value: tr}); Object.defineProperty(drop, 'clientY', {value: 290}); Object.defineProperty(drop, 'clientX', {value: 390}); k.element.dispatchEvent(drop);
    drag(w, hd(g), 'dragend', tr);
    assert.equal(JSON.stringify(names()), JSON.stringify([before[1], before[2], before[0]]), 'primo grafico spostato in fondo');
    assert.equal(JSON.stringify([...ws.root.querySelectorAll(':scope>.workspace-chart')].map(x => x.dataset.chartId)), JSON.stringify(ws.charts.map(x => x.id)), 'ordine DOM = ordine array');
    ws.moveChart(ws.charts[2], 0); assert.equal(JSON.stringify(names()), JSON.stringify(before), 'moveChart');
    assert.equal(JSON.stringify(w.chartSnapshot(ws).charts.map(x => x.name)), JSON.stringify(before));
    // trascinare un segnale o un file non deve riordinare
    const other = transfer(); other.setData('text/plain', 'x'); const o = drag(w, ws.charts[1].element, 'dragover', other); assert.ok(!ws.dragging);
    checks.push('riordino drag&drop e da tastiera');
    // ---- pagine Registrazioni e InfluxDB hanno il proprio dock ----
    assert.ok($('history-analysis-dock') && t.historyCharts.dock === $('history-analysis-dock'));
    assert.ok($('influx-analysis-dock'));
    for (const id of ['live', 'history', 'influx']) {
      assert.equal(w.document.querySelectorAll('#' + id + '-analysis-dock').length, 1, id + ': dock unico');
      assert.ok(!$(id + '-analysis-dock').closest('.workspace-chart'), id + ': il dock sta fuori dai grafici');
      assert.ok($(id + '-analysis-dock').parentElement.classList.contains('graph-main'), id + ': il dock è alla base della pagina');
    }
    // ---- lingue ----
    for (const [lang, label] of [['en', 'Collapse to bar'], ['de', 'Auf Leiste reduzieren'], ['fr', 'Réduire en barre'], ['es', 'Reducir a barra'], ['it', 'Riduci a barra']]) {
      await w.I18N.setLanguage(lang, {persist: true}); assert.equal(btn(ws.charts[0]).title, label);
    }
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({result: 'PASS', checks, files: []}));
  } finally {t.stopPolling(); for (const p of pages) p.window.close();}
})().then(() => process.exit(0)).catch(error => {console.error(error); process.exit(1);});
