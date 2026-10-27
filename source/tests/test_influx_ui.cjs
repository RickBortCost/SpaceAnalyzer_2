// Pagina InfluxDB: connessione, elenco automatico delle serie, periodo, riduzione automatica dei dati, zoom con più dettaglio.
// Pagina e script reali caricati dal servizio C#; il server InfluxDB 2.x è influx_peer.py (dati deterministici, 1 Hz per un'ora).
const assert = require('node:assert/strict');
const {JSDOM, VirtualConsole} = require(process.env.JSDOM_PATH || 'jsdom');
const [base] = process.argv.slice(2);
const influxUrl = 'http://127.0.0.1:' + process.env.TEST_INFLUX_PORT;
const errors = [], pages = [];
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
const wait = async (fn, label, timeout = 15000) => {const end = Date.now() + timeout; while (Date.now() < end) {if (await fn()) {console.error('PASS ' + label); return;} await pause(30);} throw Error('Timeout ' + label + '; ' + errors.join('; '));};
const T0 = Date.UTC(2026, 9, 20, 8, 0, 0);   // primo campione del server di prova
const pressure1 = i => 100 + 10 * Math.sin(i / 30);

async function page() {
  const html = await (await fetch(base)).text(), vc = new VirtualConsole();
  vc.on('jsdomError', e => errors.push(e.message));
  const dom = new JSDOM(html, {url: base, runScripts: 'dangerously', resources: 'usable', pretendToBeVisual: true, virtualConsole: vc, beforeParse(w) {
    w.FormData = globalThis.FormData; w.AbortController = globalThis.AbortController;
    w.fetch = async (url, options) => {try {return await fetch(new URL(url, base), options);} catch (e) {errors.push('fetch ' + url + ': ' + e.message); throw e;}};
    w.ResizeObserver = class {observe() {} disconnect() {}};
    w.HTMLCanvasElement.prototype.getBoundingClientRect = () => ({x: 0, y: 0, left: 0, top: 0, width: 950, height: 560, right: 950, bottom: 560});
    const ctx = new Proxy({measureText: text => ({width: String(text).length * 7})}, {get: (obj, key) => key in obj ? obj[key] : (() => {})});
    w.HTMLCanvasElement.prototype.getContext = () => ctx;
    w.addEventListener('error', e => errors.push(e.message));
  }});
  pages.push(dom); const w = dom.window;
  await new Promise(resolve => w.addEventListener('load', resolve, {once: true}));
  assert.deepEqual(errors, [], 'The real page must load without JavaScript errors');
  const $ = id => w.document.getElementById(id);
  await wait(() => $('endpoint').value, 'configuration loaded'); await w.opcuaConfigReady;
  w.eval('window.stopForTest=()=>{polling=false;clearTimeout(pollTimer);clearTimeout(workspaceTimer);}'); w.stopForTest();
  return {dom, w, $, t: w.uiInflux};
}
const rows = ($, key) => [...$('influx-series').querySelectorAll('.influx-series-row')].find(r => r.dataset.key === key);
const keyOf = (t, measurement, field, tag) => t.IFX.series.find(s => s.measurement === measurement && s.field === field && (!tag || JSON.stringify(s.tags) === JSON.stringify(tag)))?.key;
const tick = ($, key, on) => {const input = rows($, key).querySelector('input'); input.checked = on; input.dispatchEvent(new input.ownerDocument.defaultView.Event('change', {bubbles: true}));};
const graphSeries = (t, key) => t.influxCharts.active.series[key];

(async () => {
  const checks = [];
  try {
    const {w, $, t} = await page();
    w.eval("showAppPage('influx')");
    assert(!$('influx-page').hidden); assert($('live-page').hidden); assert($('recordings-page').hidden); assert($('nav-influx').classList.contains('active'));
    await wait(() => t.IFX.settingsLoaded, 'pagina aperta');
    checks.push('Pagina InfluxDB e navigazione');

    // Token errato: messaggio chiaro, nessuno stato di connessione.
    $('influx-url').value = influxUrl.replace('http://', ''); $('influx-token').value = 'sbagliato'; $('influx-connect').click();
    await wait(() => !$('influx-error').hidden, 'errore di token'); assert.match($('influx-error').textContent, /token non valido/); assert.equal(t.IFX.connected, false);
    assert.equal($('influx-state').classList.contains('ok'), false);
    checks.push('Token errato: errore chiaro');

    // Connessione corretta: organizzazione, bucket ed elenco delle serie in automatico.
    $('influx-token').value = 'test-token'; $('influx-connect').click();
    await wait(() => t.IFX.connected && t.IFX.series.length > 0, 'connessione e catalogo');
    assert.match($('influx-state').textContent, /v2\.7\.12/); assert.match($('influx-state').textContent, /danieli/); assert.equal($('influx-org').value, 'danieli');
    assert.equal($('influx-token').value, ''); assert.equal($('influx-url').value, influxUrl);
    assert.deepEqual([...$('influx-bucket').options].map(o => o.value), ['plant']);
    $('influx-system').checked = true; $('influx-system').dispatchEvent(new w.Event('change', {bubbles: true}));
    assert.deepEqual([...$('influx-bucket').options].map(o => o.value).sort(), ['_monitoring', '_tasks', 'plant']);
    $('influx-system').checked = false; $('influx-system').dispatchEvent(new w.Event('change', {bubbles: true}));
    await wait(() => $('influx-bucket').value === 'plant' && t.IFX.series.length === 5, 'serie di plant');
    const settings = await (await fetch(base + '/api/influx/settings', {headers: {}})).text();
    assert(!settings.includes('test-token'), 'il token non viene mai restituito');
    checks.push('Connessione, organizzazione e bucket automatici; il token non esce dal servizio');

    // Elenco: misure, campi, tag, tipi; le serie di testo non si possono scegliere.
    assert.deepEqual(t.IFX.series.map(s => s.measurement + '.' + s.field + ':' + s.data_type).sort(), ['counter.count:Int64', 'pressure.value:Double', 'pressure.value:Double', 'state.mode:String', 'temp.alarm:Boolean']);
    const mode = keyOf(t, 'state', 'mode'); assert(rows($, mode).querySelector('input').disabled); assert(rows($, mode).classList.contains('disabled'));
    $('influx-search').value = 'line=2'; $('influx-search').dispatchEvent(new w.Event('input', {bubbles: true}));
    assert.equal($('influx-series').querySelectorAll('.influx-series-row').length, 1); $('influx-search').value = ''; $('influx-search').dispatchEvent(new w.Event('input', {bubbles: true}));
    assert.equal($('influx-series').querySelectorAll('.influx-series-row').length, 5);
    assert(t.IFX.series.every(s => Number.isFinite(s.last_ms))); assert.equal(t.IFX.series[0].last_ms, T0 + 3599000);
    checks.push('Elenco automatico di misure, campi, tag e tipi con filtro; testo non selezionabile');

    // Prima vista automatica: la prima misura numerica viene aperta da sola, anche se i dati non sono di adesso.
    await wait(() => Object.keys(t.influxCharts.active.series || {}).length > 0, 'prima vista automatica ' + JSON.stringify([$('influx-error').textContent, $('influx-status').textContent, [...t.influxCharts.active.influx.selected]]));
    const counter = keyOf(t, 'counter', 'count');
    assert.deepEqual([...t.influxCharts.active.influx.selected], [counter]);
    const auto = graphSeries(t, counter); assert(auto.length > 1000 && auto.length < 2400, 'ridotta sotto il limite: ' + auto.length);
    assert.match($('influx-status').textContent, /ogni 2 s|mean/);
    checks.push('Prima vista automatica ancorata all’ultimo dato, ridotta con finestra automatica');

    // Tutti i punti: valori e tempi esatti (tag con virgolette e ${ } nel nome).
    $('influx-aggregate').value = 'raw'; $('influx-aggregate').dispatchEvent(new w.Event('change', {bubbles: true}));
    await wait(() => graphSeries(t, counter)?.length === 3600, 'tutti i punti');
    const raw = graphSeries(t, counter); assert.equal(raw.t[0], T0); assert.equal(raw.t[3599], T0 + 3599000); assert.equal(raw.v[0], 1000); assert.equal(raw.v[3599], 1000 + 3599);
    checks.push('Tutti i punti esatti; tag con virgolette, backslash e ${ } nel filtro Flux');

    // Due serie, preset 5 minuti: valori identici ai dati del server.
    tick($, counter, false); const p1 = keyOf(t, 'pressure', 'value', {line: '1'}), p2 = keyOf(t, 'pressure', 'value', {line: '2'});
    tick($, p1, true); tick($, p2, true); t.setInfluxPreset(300); $('influx-load').click();
    await wait(() => graphSeries(t, p1)?.length === 300 && graphSeries(t, p2)?.length === 300, 'ultimi 5 minuti');
    const s1 = graphSeries(t, p1); assert.equal(s1.t[0], T0 + 3300000); assert.equal(s1.t[299], T0 + 3599000);
    for (let k = 0; k < 300; k += 37) assert(Math.abs(s1.v[k] - pressure1(3300 + k)) < 1e-9, 'valore ' + k);
    assert(!t.influxCharts.active.influx.selected.has(counter));
    checks.push('Periodo 5 min: tempi e valori identici ai dati del server');

    // Riduzione automatica: 1 h con pochi punti disponibili.
    $('influx-aggregate').value = 'auto'; $('influx-aggregate').dispatchEvent(new w.Event('change', {bubbles: true}));
    t.setInfluxPreset(3600); $('influx-load').click(); await wait(() => (graphSeries(t, p1)?.length || 0) > 1700 && graphSeries(t, p1).length < 2000, 'media a finestre');
    const mean = graphSeries(t, p1); const mid = mean.t[Math.floor(mean.length / 2)];
    assert.equal(mean.t[1] - mean.t[0], 2000, 'finestra di 2 s'); assert(Math.abs(mean.v[100] - pressure1((mean.t[100] - T0) / 1000)) < 0.3);
    assert(mid > T0 && mid < T0 + 3600000);
    checks.push('Riduzione automatica a finestre di 2 s con tempi al centro della finestra');

    // Booleano: la media è la frazione di tempo acceso (0..1).
    const alarm = keyOf(t, 'temp', 'alarm'); tick($, p1, false); tick($, p2, false); tick($, alarm, true); $('influx-aggregate').value = 'mean'; $('influx-aggregate').dispatchEvent(new w.Event('change', {bubbles: true}));
    $('influx-load').click(); await wait(() => (graphSeries(t, alarm)?.length || 0) > 1000, 'booleano aggregato');
    const b = graphSeries(t, alarm); assert(b.v.every(v => v === 0 || v === 1)); assert.equal(b.v[0], 0); assert(b.v.includes(1));
    checks.push('Serie booleana disegnata (0/1) anche con riduzione');

    // Intervallo a scelta.
    tick($, alarm, false); tick($, p1, true); t.setInfluxPreset(0); assert(!$('influx-custom').hidden);
    const local = ms => {const d = new Date(ms), p = (v, n = 2) => String(v).padStart(n, '0'); return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;};
    $('influx-aggregate').value = 'raw'; $('influx-aggregate').dispatchEvent(new w.Event('change', {bubbles: true}));
    $('influx-from').value = local(T0 + 600000); $('influx-to').value = local(T0 + 660000); $('influx-load').click();
    await wait(() => graphSeries(t, p1)?.length === 60, 'intervallo a scelta');
    assert.equal(graphSeries(t, p1).t[0], T0 + 600000); assert(Math.abs(graphSeries(t, p1).v[0] - pressure1(600)) < 1e-9);
    $('influx-to').value = $('influx-from').value; $('influx-load').click(); await wait(() => /seguire/.test($('influx-error').textContent), 'intervallo non valido');
    checks.push('Intervallo a scelta e controllo di validità');

    // Zoom: più dettaglio sull'intervallo visibile; ripristino torna al periodo scelto.
    t.setInfluxPreset(3600); $('influx-aggregate').value = 'auto'; $('influx-aggregate').dispatchEvent(new w.Event('change', {bubbles: true}));
    await wait(() => (graphSeries(t, p1)?.length || 0) > 1700, 'ripristino 1 h');
    const graph = t.influxCharts.active, trend = graph.trend, before = graphSeries(t, p1).length;
    trend.onViewChanged({xmin: T0 + 1200000, xmax: T0 + 1320000});
    await wait(() => graphSeries(t, p1)?.length === 120, 'dettaglio nello zoom');
    assert.equal(graphSeries(t, p1).t[0], T0 + 1200000);
    trend.onViewChanged(null); await wait(() => graphSeries(t, p1)?.length === before, 'ripristino dal zoom');
    checks.push('Zoom: rilettura con più dettaglio e ripristino del periodo');

    // Più grafici indipendenti.
    $('influx-add-chart').click(); await wait(() => t.influxCharts.charts.length === 2, 'secondo grafico');
    assert.equal(t.influxCharts.active.influx.selected.size, 0); assert.equal(rows($, p1).querySelector('input').checked, false);
    tick($, p2, true); $('influx-load').click(); await wait(() => graphSeries(t, p2)?.length > 1000, 'secondo grafico con dati');
    assert(!t.influxCharts.charts[0].series[p2]); assert(t.influxCharts.charts[0].series[p1].length > 1000);
    t.influxCharts.activate(t.influxCharts.charts[0]); assert.equal(rows($, p1).querySelector('input').checked, true); assert.equal(rows($, p2).querySelector('input').checked, false);
    checks.push('Più grafici con selezioni indipendenti');

    // Il token salvato permette di ricollegarsi da solo; "Dimentica" lo cancella.
    const second = await page(); second.w.eval("showAppPage('influx')");
    await wait(() => second.t.IFX.connected && second.t.IFX.series.length === 5, 'riconnessione automatica con il token salvato');
    assert.match(second.$('influx-token').placeholder, /salvato/);
    second.$('influx-forget').click(); await wait(() => !second.t.IFX.connected, 'token dimenticato');
    const saved = (await (await fetch(base + '/api/influx/settings')).json()).settings; assert.equal(saved.token_saved, false); assert.equal(saved.url, influxUrl);
    checks.push('Riconnessione automatica e "Dimentica il token"');

    assert.deepEqual(errors, []);
    console.log(JSON.stringify({result: 'PASS', checks, files: []}));
  } finally {
    for (const dom of pages) dom.window.close();
  }
})().then(() => process.exit(0)).catch(e => {console.error(e.stack); process.exit(1);});
