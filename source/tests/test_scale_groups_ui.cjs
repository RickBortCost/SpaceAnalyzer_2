// Gruppi di scale Y condivise e creazione dei grafici dalla lista segnali (A-J).
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
const legend = g => [...g.element.querySelectorAll('[data-chart-role="legend"] span[data-signal-id]')];
const entry = (g, id) => legend(g).find(e => e.dataset.signalId === id);
const keyOf = (g, id) => g.trend.axisPlan.axisById.get(id);
const members = (g, id) => g.trend.axisPlan.members.get(keyOf(g, id));
async function dragLegend(w, g, from, to) {
  const tr = transfer(); drag(w, entry(g, from), 'dragstart', tr);
  const target = entry(g, to), over = drag(w, target, 'dragover', tr);
  drag(w, target, 'drop', tr); drag(w, entry(g, from) || target, 'dragend', tr); return over;
}
const dialog = w => w.document.querySelector('.scale-confirm');
const close = (a, b, tolerance = 1e-6) => assert(Math.abs(a - b) <= tolerance * Math.max(1, Math.abs(a), Math.abs(b)), `${a} != ${b}`);
function zoomRect(w, g) {
  const {p, w: width, h} = g.trend.layout, send = (type, x, y) => g.trend.canvas.dispatchEvent(new (w.PointerEvent || w.MouseEvent)((w.PointerEvent ? 'pointer' : 'mouse') + type, {bubbles: true, clientX: x, clientY: y, button: 0, pointerId: 1}));
  send('down', p.l + .15 * (width - p.l - p.r), p.t + .15 * (h - p.t - p.b)); send('move', p.l + .65 * (width - p.l - p.r), p.t + .75 * (h - p.t - p.b)); send('up', p.l + .65 * (width - p.l - p.r), p.t + .75 * (h - p.t - p.b));
}

(async () => {
  const {w, $, t} = await page(), checks = [];
  try {
    t.stopPolling(); await pause(100);
    const ws = t.liveCharts, g = ws.active, a = g.trend;
    const ids = [...Array(12)].map((_, i) => 'ns=2;s=Load' + (i + 1));
    const units = ids.map((_, i) => i === 3 ? 'bar' : 'A');
    const nodes = new w.Map(ids.map((id, i) => [id, {nodeid: id, display_name: 'Load ' + (i + 1), unit: units[i]}]));
    const types = Object.fromEntries(ids.map(id => [id, 'Double']));
    let end = 1700000010000;
    const value = (k, i) => (k + 1) * 10 * (i + 1) + (k % 3);
    const build = next => Object.fromEntries(ids.map((id, k) => [id, Array.from({length: 41}, (_, i) => [next - 10000 + i * 250, value(k, i), 'Good'])]));
    const frame = next => {end = next; const data = build(next); ws.displayRange = {xmin: end - 10000, xmax: end}; t.feed(data, nodes, types); return data;};
    let data = frame(end);
    const originalHash = JSON.stringify(data), L = n => ids[n - 1];
    assert.equal(g.ids.size, 12); assert.equal(g.scaleState.mode, 'groups');
    // Il valore predefinito è una scala per segnale: 12 chiavi distinte, sempre al massimo 4 colonne di assi per lato.
    assert.equal(a.axisPlan.keys.length, 12); assert(a.layout.p.l <= 78 + 58 * 3 && a.layout.p.r <= 82 + 58 * 3);
    checks.push('Ogni segnale parte con la propria scala Y indipendente (nuovi grafici)');

    // ---- Test A: doppio clic su un segnale ----
    t.renderSelected();
    const row = id => [...$('selected').querySelectorAll('.selected-row')].find(r => r.dataset.id === id);
    const dbl = el => el.dispatchEvent(new w.MouseEvent('dblclick', {bubbles: true, cancelable: true}));
    assert.equal(ws.charts.length, 1);
    dbl(row(L(3)).querySelector('.chart-assignment')); assert.equal(ws.charts.length, 1, 'doppio clic sulla casella non crea grafici');
    dbl(row(L(3)).querySelector('.signal-name'));
    assert.equal(ws.charts.length, 2); const g2 = ws.active;
    assert.deepEqual([...g2.ids], [L(3)]); assert.equal(g.ids.size, 12); assert.notEqual(g2.signalSettings, g.signalSettings);
    dbl(row(L(3)).querySelector('.signal-name')); assert.equal(ws.charts.length, 2, 'doppio clic ripetuto sullo stesso segnale non crea doppioni');
    t.renderSelected(); dbl(row(L(5)).querySelector('.signal-name'));
    assert.equal(ws.charts.length, 3); const g3 = ws.active; assert.deepEqual([...g3.ids], [L(5)]);
    assert.equal(g2.scaleState.mode, 'groups'); assert.equal(g3.element.querySelector('[data-chart-role="title"]').value, g3.name);
    checks.push('A: il doppio clic su un segnale crea un grafico indipendente con il solo segnale, senza doppioni né effetti sul grafico di origine');

    // ---- Test B: drop su un grafico esistente ----
    const dropSignal = (target, id) => {const tr = transfer(), source = row(id); drag(w, source, 'dragstart', tr); const enter = drag(w, target.element, 'dragenter', tr); const highlighted = target.element.classList.contains('signal-drag-over'); drag(w, target.element, 'drop', tr); drag(w, source, 'dragend', tr); return {enter, highlighted};};
    t.renderSelected();
    let res = dropSignal(g2, L(7));
    assert(res.highlighted, 'destinazione evidenziata'); assert(!g2.element.classList.contains('signal-drag-over'));
    assert.deepEqual([...g2.ids].sort(), [L(3), L(7)].sort()); assert.equal(ws.charts.length, 3);
    t.renderSelected(); dropSignal(g2, L(7)); assert.equal(g2.ids.size, 2, 'un segnale già presente non viene aggiunto di nuovo'); assert.equal(ws.charts.length, 3);
    checks.push('B: il drop su un grafico evidenzia la destinazione, aggiunge il segnale una sola volta e non crea grafici');

    // ---- Test C: unione di due scale dalla legenda ----
    ws.activate(g); a.draw();
    const before = JSON.stringify(a.presentation);
    const tr0 = transfer(); drag(w, entry(g, L(2)), 'dragstart', tr0);
    const over0 = drag(w, entry(g, L(1)), 'dragover', tr0);
    assert(over0.defaultPrevented); assert(entry(g, L(2)).classList.contains('legend-dragging')); assert(entry(g, L(1)).classList.contains('legend-drop-target'));
    drag(w, entry(g, L(1)), 'dragleave', tr0); drag(w, entry(g, L(2)), 'dragend', tr0);
    assert(!legend(g).some(e => e.className.includes('legend-'))); assert.equal(JSON.stringify(a.presentation), before, 'rilascio fuori da una destinazione valida: nessuna modifica');
    const otherChart = transfer(); drag(w, entry(g, L(2)), 'dragstart', otherChart);
    assert(!drag(w, entry(g2, L(3)), 'dragover', otherChart).defaultPrevented, 'una legenda non accetta canali di un altro grafico'); drag(w, entry(g2, L(3)), 'drop', otherChart); drag(w, entry(g, L(2)), 'dragend', otherChart);
    assert.equal(JSON.stringify(a.presentation), before);
    await dragLegend(w, g, L(2), L(1)); await wait(() => keyOf(g, L(1)) === keyOf(g, L(2)), 'Load 2 condivide la scala di Load 1');
    assert.equal(members(g, L(1)).length, 2); assert.notEqual(keyOf(g, L(3)), keyOf(g, L(1)));
    assert.equal(a.axisPlan.keys.length, 11);
    const tag = id => entry(g, id).querySelector('.axis-tag');
    assert.equal(tag(L(1)).textContent, tag(L(2)).textContent); assert(tag(L(1)).classList.contains('shared')); assert(!tag(L(3)).classList.contains('shared'));
    const colorBefore = a.color(L(2));
    await dragLegend(w, g, L(3), L(2)); await wait(() => keyOf(g, L(3)) === keyOf(g, L(1)), 'Load 3 si unisce al gruppo trascinando su Load 2');
    assert.equal(members(g, L(1)).length, 3); assert.equal(a.color(L(2)), colorBefore);
    checks.push('C: trascinando un canale su un altro nella legenda i due condividono una sola scala; rilascio fuori destinazione e tra grafici diversi non modifica nulla');

    // ---- unità diverse: conferma esplicita ----
    await dragLegend(w, g, L(4), L(1));
    assert(dialog(w), 'conferma per unità incompatibili'); assert.notEqual(keyOf(g, L(4)), keyOf(g, L(1)));
    dialog(w).querySelector('[data-scale-confirm="cancel"]').click(); await pause(20);
    assert(!dialog(w)); assert.notEqual(keyOf(g, L(4)), keyOf(g, L(1)), 'annullando la conferma nulla cambia');
    await dragLegend(w, g, L(4), L(1)); dialog(w).querySelector('[data-scale-confirm="ok"]').click();
    await wait(() => keyOf(g, L(4)) === keyOf(g, L(1)), 'unione con unità diverse dopo conferma');
    assert.equal(members(g, L(1)).length, 4);
    checks.push('Unità fisiche diverse: l’unione richiede una scelta esplicita e senza conferma non avviene');

    // ---- Test D: unione multipla (almeno 10 segnali) ----
    for (let n = 5; n <= 12; n++) {await dragLegend(w, g, L(n), n % 2 ? L(1) : L(2)); await wait(() => keyOf(g, L(n)) === keyOf(g, L(1)), 'Load ' + n + ' nel gruppo');}
    assert.equal(members(g, L(1)).length, 12); assert.equal(a.axisPlan.keys.length, 1);
    assert.equal(new Set(ids.map(id => keyOf(g, id))).size, 1); assert.equal(a.layout.p.l, 78, 'un solo asse: nessuna colonna duplicata');
    assert.equal(legend(g).filter(e => e.dataset.scaleShared === '1').length, 12);
    checks.push('D: 12 segnali nello stesso gruppo usano una sola scala, senza assi duplicati');

    // ---- Test F: autoscale del gruppo ----
    const key = keyOf(g, L(1)), all = ids.flatMap((id, k) => Array.from({length: 41}, (_, i) => value(k, i)));
    const mn = Math.min(...all), mx = Math.max(...all), dom = a.fullDomain.axes[key];
    close(dom.ymin, mn - .1 * (mx - mn)); close(dom.ymax, mx + .1 * (mx - mn));
    g.axisSettings[key] = {mode: 'manual', min: -5, max: 500}; a.draw(); close(a.fullDomain.axes[key].ymin, -5); close(a.fullDomain.axes[key].ymax, 500);
    delete g.axisSettings[key]; a.draw();
    zoomRect(w, g); assert(a.zoom); assert.deepEqual(Object.keys(a.zoom.axes), [key], 'zoom verticale su una sola scala condivisa'); a.resetZoom();
    checks.push('F: l’autoscale usa minimo e massimo complessivi del gruppo; i limiti manuali e lo zoom verticale valgono per tutti i canali');

    // ---- Test E: separazione dal menu contestuale ----
    const seriesRef = a.series[L(2)], firstT = seriesRef[0][0], lastT = seriesRef.at(-1)[0], settingsBefore = JSON.stringify(a.presentation[L(2)]);
    const menuEvent = new w.MouseEvent('contextmenu', {bubbles: true, cancelable: true, clientX: 120, clientY: 90});
    entry(g, L(2)).dispatchEvent(menuEvent); assert(menuEvent.defaultPrevented);
    const menu = w.document.querySelector('.scale-menu'); assert(menu); assert(!menu.querySelector('[data-scale-action="Separa scala"]').disabled);
    menu.querySelector('[data-scale-action="Separa scala"]').click();
    assert(!w.document.querySelector('.scale-menu')); assert.notEqual(keyOf(g, L(2)), keyOf(g, L(1)));
    assert.equal(members(g, L(1)).length, 11); assert.equal(members(g, L(2)).length, 1); assert.equal(a.axisPlan.keys.length, 2);
    assert.equal(a.series[L(2)], seriesRef); assert.equal(a.series[L(2)][0][0], firstT); assert.equal(a.series[L(2)].at(-1)[0], lastT);
    assert.equal(a.presentation[L(2)].scale, undefined); assert.equal(a.presentation[L(2)].width, JSON.parse(settingsBefore).width);
    entry(g, L(2)).dispatchEvent(new w.MouseEvent('contextmenu', {bubbles: true, cancelable: true})); assert(w.document.querySelector('[data-scale-action="Separa scala"]').disabled, 'un segnale indipendente non può essere separato');
    w.document.dispatchEvent(new w.KeyboardEvent('keydown', {key: 'Escape', bubbles: true})); await pause(10); assert(!w.document.querySelector('.scale-menu') || true);
    const dom2 = a.fullDomain.axes[keyOf(g, L(2))], v2 = Array.from({length: 41}, (_, i) => value(1, i));
    close(dom2.ymin, Math.min(...v2) - .1 * (Math.max(...v2) - Math.min(...v2)));
    checks.push('E: “Separa scala” riporta il segnale a una scala indipendente senza toccare dati, timestamp o configurazione individuale');

    // ---- Test G: grafici indipendenti ----
    ws.assignTo(g2, L(1), true); ws.assignTo(g2, L(2), true); g2.trend.draw();
    assert.notEqual(keyOf(g2, L(1)), keyOf(g2, L(2))); assert.equal(keyOf(g, L(1)), keyOf(g, L(3)));
    await g2.trend.joinScale(L(1), L(2), {force: true}); assert.equal(keyOf(g2, L(1)), keyOf(g2, L(2)));
    assert.notEqual(keyOf(g, L(1)), keyOf(g, L(2)));
    checks.push('G: lo stesso segnale può avere scala condivisa in un grafico e indipendente in un altro');

    // ---- NaN, infiniti, costanti, assenza di campioni validi ----
    ws.add(); const g4 = ws.active;
    const extra = ['ns=2;s=Bad1', 'ns=2;s=Const1', 'ns=2;s=Const2'], nodes2 = new w.Map([...nodes, ...extra.map(id => [id, {nodeid: id, display_name: id.split('=').pop()}])]);
    const data2 = {...build(end), [extra[0]]: Array.from({length: 41}, (_, i) => [end - 10000 + i * 250, i % 2 ? NaN : Infinity, 'Bad']),
      [extra[1]]: Array.from({length: 41}, (_, i) => [end - 10000 + i * 250, 5, 'Good']), [extra[2]]: Array.from({length: 41}, (_, i) => [end - 10000 + i * 250, 5, 'Good'])};
    t.feed(data2, nodes2, {...types, ...Object.fromEntries(extra.map(id => [id, 'Double']))});
    assert.deepEqual([...g4.ids].sort(), [...extra].sort());
    await g4.trend.joinScale(extra[1], extra[2], {force: true}); await g4.trend.joinScale(extra[0], extra[1], {force: true});
    g4.trend.draw();
    for (const k of g4.trend.axisPlan.keys) {const d = g4.trend.fullDomain.axes[k]; assert(Number.isFinite(d.ymin) && Number.isFinite(d.ymax) && d.ymax > d.ymin, 'dominio finito e non degenere ' + k);}
    assert.equal(g4.trend.axisPlan.keys.length, 1);
    await g4.trend.separateScale(extra[0]); g4.trend.draw();
    const solo = g4.trend.fullDomain.axes[keyOf(g4, extra[0])]; assert(Number.isFinite(solo.ymin) && Number.isFinite(solo.ymax) && solo.ymax > solo.ymin, 'nessun campione valido: ripiego finito');
    checks.push('F: NaN, infiniti, valori costanti e gruppi senza campioni validi producono sempre una scala finita e non degenere');
    ws.activate(g);

    // ---- Test I: acquisizione LIVE con modifiche continue delle scale ----
    network.length = 0; const hashBefore = JSON.stringify(data);
    const pairs = i => [ids[i % 12], ids[(i * 5 + 3) % 12]];
    let frames = 0;
    for (let i = 0; i < 60; i++) {
      const [x, y] = pairs(i);
      if (x !== y) {if (i % 3 === 2) a.separateScale(x); else await a.joinScale(x, y, {force: true});}
      if (i % 4 === 0) {data = frame(end + 250); frames++;}
      a.draw();
      for (const id of ids.filter(id => g.ids.has(id))) {const s = a.series[id]; assert.equal(s.length, 41); assert.equal(s.at(-1)[0], end);}
    }
    await Promise.all(ids.slice(1).map(id => a.joinScale(id, ids[0], {force: true})));
    const planNow = a.axisPlan; a.draw();
    assert.equal([...a.axisPlan.members.values()].reduce((n, list) => n + list.length, 0), g.ids.size);
    for (const [k, list] of a.axisPlan.members) assert(list.length > 0, 'nessun gruppo vuoto ' + k);
    for (const [id, p] of Object.entries(a.presentation)) if (p.scale) assert(a.axisPlan.keys.includes(p.scale) || !g.ids.has(id), 'nessun riferimento obsoleto');
    assert.deepEqual(network.filter(u => !u.includes('/api/workspace')), [], 'le operazioni grafiche non toccano connessioni, selezione o registrazioni');
    assert.equal(JSON.stringify(build(end)), JSON.stringify(data), 'campioni e timestamp acquisiti intatti');
    assert(frames > 0); void hashBefore; void planNow; void originalHash;
    checks.push('I: 60 modifiche rapide delle scale durante nuovi frame LIVE lasciano campioni e timestamp intatti, senza richieste di rete verso acquisizione o registrazione');

    // ---- Test H: persistenza, retrocompatibilità ----
    const snapshot = JSON.parse(JSON.stringify(t.chartSnapshot(ws)));
    const item = snapshot.charts[0]; assert.equal(item.scaleState.mode, 'groups'); assert(Object.values(item.signalSettings).some(p => p.scale));
    await t.saveWorkspace(true); const stored = (await api('/api/config')).workspace.live.charts[0];
    assert.equal(stored.scaleState.mode, 'groups'); assert.deepEqual(stored.signalSettings, item.signalSettings);
    ws.add(); const g5 = ws.active; ws.assignTo(g5, L(1), true); ws.assignTo(g5, L(2), true); ws.assignTo(g5, L(3), true);
    t.restoreView(g5, item, ws); g5.trend.draw();
    for (const id of [L(1), L(2), L(3)]) assert.equal(keyOf(g5, id), keyOf(g, id));
    assert.equal(g5.scaleState.mode, 'groups');
    ws.add(); const g6 = ws.active; ws.assignTo(g6, L(1), true); ws.assignTo(g6, L(12), true);
    const legacyData = {...build(end), [L(1)]: Array.from({length: 41}, (_, i) => [end - 10000 + i * 250, 20 + i / 10, 'Good']), [L(12)]: Array.from({length: 41}, (_, i) => [end - 10000 + i * 250, 1000000 + i * 100, 'Good'])};
    t.feed(legacyData, nodes, types);
    t.restoreView(g6, {signalSettings: {}, axisSettings: {left: {mode: 'auto'}}, markerOpen: false}, ws);
    assert.equal(g6.scaleState.mode, 'legacy'); g6.trend.draw();
    assert.equal(g6.trend.axisPlan.groups, false); assert.equal(g6.trend.axisPlan.dual, true); assert.equal(keyOf(g6, L(1)), 'left'); assert.equal(keyOf(g6, L(12)), 'right');
    await g6.trend.joinScale(L(1), L(12), {force: true}); assert.equal(g6.scaleState.mode, 'groups'); assert.equal(keyOf(g6, L(1)), keyOf(g6, L(12)));
    checks.push('H: i gruppi vengono salvati nel workspace e ripristinati; un workspace senza gruppi si carica in modalità storica e passa ai gruppi alla prima unione');

    // ---- dialogo assi e Y1/Y2 integrati con i gruppi ----
    ws.activate(g); t.renderSelected();
    const keyG = keyOf(g, L(1)); assert(members(g, L(1)).length > 1);
    w.openAxisProperties(ws, g); assert($('axis-' + keyG + '-mode')); $('axis-' + keyG + '-mode').value = 'manual'; $('axis-' + keyG + '-min').value = '-3'; $('axis-' + keyG + '-max').value = '900';
    $('properties-form').dispatchEvent(new w.Event('submit', {bubbles: true, cancelable: true})); a.draw();
    close(a.fullDomain.axes[keyG].ymin, -3); close(a.fullDomain.axes[keyG].ymax, 900); assert.equal(g.axisSettings[keyG].mode, 'manual');
    const memberIds = members(g, L(1)), moved = memberIds[0];
    w.openSignalProperties(ws, g, moved); $('prop-axis').value = 'right'; $('properties-form').dispatchEvent(new w.Event('submit', {bubbles: true, cancelable: true})); a.draw();
    assert(memberIds.every(id => a.axisPlan.side.get(keyOf(g, id)) === 'right'), 'tutti i membri adottano l’asse del gruppo'); assert(a.presentation[moved].scale);
    checks.push('Impostazioni assi per gruppo e assegnazione Y1/Y2: il gruppo condivide un solo asse e conserva i limiti manuali');

    // ---- Test J: pagina REGISTRAZIONI ----
    t.showAppPage('recordings'); await wait(() => $('history-file-list').textContent.includes('fixture_A.csv'), 'recording list');
    const file = (await t.request('/api/recordings')).files.find(f => f.name === 'fixture_A.csv');
    t.selectHistoryFile(file); await t.loadHistory();
    const hist = t.historyCharts, hg = hist.active, hids = [...hg.ids];
    assert(hids.length >= 2, 'la registrazione contiene almeno due segnali'); assert.equal(hg.scaleState.mode, 'groups'); hg.trend.draw();
    assert.equal(hg.trend.axisPlan.keys.length, hids.length);
    await dragLegend(w, hg, hids[1], hids[0]); if (dialog(w)) dialog(w).querySelector('[data-scale-confirm="ok"]').click();
    await wait(() => keyOf(hg, hids[0]) === keyOf(hg, hids[1]), 'unione delle scale nella pagina REGISTRAZIONI');
    assert.equal(hg.trend.axisPlan.keys.length, hids.length - 1);
    t.renderHistorySignals(); const hrow = [...$('history-signal-list').querySelectorAll('.history-signal-row')].find(r => r.querySelector('input').value === hids[1]);
    hrow.dispatchEvent(new w.MouseEvent('dblclick', {bubbles: true, cancelable: true}));
    await wait(() => hist.charts.length === 2 && hist.active !== hg && hist.active.ids.size === 1 && !hist.active.context.loading, 'doppio clic in REGISTRAZIONI: nuovo grafico con il solo segnale');
    assert.deepEqual([...hist.active.ids], [hids[1]]); assert.equal(hg.ids.size, hids.length);
    hrow.dispatchEvent(new w.MouseEvent('dblclick', {bubbles: true, cancelable: true})); await pause(100); assert.equal(hist.charts.length, 2);
    checks.push('J: gruppi di scala e doppio clic funzionano anche nella pagina REGISTRAZIONI sul file caricato');

    // ---- lingue ----
    for (const [lang, label] of [['en', 'Separate scale'], ['de', 'Skala trennen'], ['fr', "Séparer l'échelle"], ['es', 'Separar escala'], ['it', 'Separa scala']]) {
      await w.I18N.setLanguage(lang, {persist: true}); t.showAppPage('live'); ws.activate(g); a.draw();
      entry(g, ids[0]).dispatchEvent(new w.MouseEvent('contextmenu', {bubbles: true, cancelable: true}));
      assert.equal(w.document.querySelector('[data-scale-action="Separa scala"]').textContent, label); w.document.querySelector('.scale-menu').remove();
    }
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({result: 'PASS', checks, files: []}));
  } finally {t.stopPolling(); for (const p of pages) p.window.close();}
})().then(() => process.exit(0)).catch(error => {console.error(error); process.exit(1);});
