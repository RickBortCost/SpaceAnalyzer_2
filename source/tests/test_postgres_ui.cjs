// Pagina PostgreSQL: menu Database, connessione con password, tabelle e viste, colonne del tempo e delle etichette, periodo, riduzione dei dati, esportazione.
// Pagina e script reali caricati dal servizio C#; il server è un vero PostgreSQL temporaneo (postgres_peer.py, dati deterministici da T0).
const assert = require('node:assert/strict');
const fs = require('node:fs'), os = require('node:os'), path = require('node:path');
const {JSDOM, VirtualConsole} = require(process.env.JSDOM_PATH || 'jsdom');
const [base] = process.argv.slice(2);
const port = Number(process.env.TEST_PG_PORT), password = process.env.TEST_PG_PASSWORD;
const errors = [], pages = [];
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
const wait = async (fn, label, timeout = 20000) => {const end = Date.now() + timeout; while (Date.now() < end) {if (await fn()) {console.error('PASS ' + label); return;} await pause(30);} throw Error('Timeout ' + label + '; ' + errors.join('; '));};
const T0 = Date.UTC(2026, 9, 20, 8, 0, 0);   // primo campione del server di prova
const temperature = i => 20 + 5 * Math.sin(i / 60);

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
  return {dom, w, $, t: w.uiPg};
}
const rows = $ => [...$('pg-series').querySelectorAll('.pg-series-row')];
const row = ($, key) => rows($).find(r => r.dataset.key === key);
const tick = ($, key, on) => {const input = row($, key).querySelector('input'); input.checked = on; input.dispatchEvent(new input.ownerDocument.defaultView.Event('change', {bubbles: true}));};
const graphSeries = (t, key) => t.pgCharts.active.series[key];
const change = (w, el, value) => {el.value = value; el.dispatchEvent(new w.Event('change', {bubbles: true}));};

(async () => {
  const checks = [];
  try {
    const {w, $, t} = await page();
    // Menu Database: InfluxDB e PostgreSQL al posto della scheda InfluxDB.
    const menu = $('database-menu'); assert(menu && menu.classList.contains('tools-menu')); assert.equal($('nav-database').textContent.trim(), 'Database');
    assert.deepEqual([...menu.querySelectorAll('.detail-content .page-tab')].map(b => b.id), ['nav-influx', 'nav-pg']);
    menu.open = true; $('nav-pg').click();
    assert(!$('pg-page').hidden); assert($('influx-page').hidden); assert($('live-page').hidden);
    assert($('nav-pg').classList.contains('active')); assert($('nav-database').classList.contains('active'));
    await wait(() => t.PGX.settingsLoaded, 'pagina aperta');
    $('nav-influx').click(); assert(!$('influx-page').hidden && $('pg-page').hidden); assert($('nav-database').classList.contains('active'));
    $('nav-pg').click(); assert(!$('pg-page').hidden);
    checks.push('Menu Database con InfluxDB e PostgreSQL');

    // Password errata: messaggio chiaro, nessuna connessione.
    $('pg-host').value = '127.0.0.1'; $('pg-port').value = String(port); $('pg-database').value = 'plant'; $('pg-user').value = 'tester'; $('pg-password').value = 'sbagliata';
    $('pg-connect').click();
    await wait(() => !$('pg-error').hidden, 'errore di password'); assert.match($('pg-error').textContent, /utente o password non validi/); assert.equal(t.PGX.connected, false);
    checks.push('Password errata: errore chiaro');

    // Connessione: tabelle e viste con colonna del tempo e colonne numeriche (config esclusa), prima vista automatica.
    $('pg-password').value = password; $('pg-connect').click();
    await wait(() => t.PGX.connected && t.PGX.series.length > 0, 'connessione e catalogo', 20000).catch(e => {throw Error(e.message + ' | ' + [$('pg-error').textContent, $('pg-list-status').textContent, $('pg-state').textContent, $('pg-table').value, $('pg-time-column').value].join(' | '));});
    assert.match($('pg-state').textContent, /PostgreSQL \d+/); assert.equal($('pg-password').value, '');
    assert.deepEqual([...$('pg-table').options].map(o => o.value), ['plant.hot', 'plant.line_data', 'public.samples']);
    const settings = await (await fetch(base + '/api/postgres/settings')).text(); assert(!settings.includes(password), 'la password non viene mai restituita');
    change(w, $('pg-table'), 'plant.line_data');
    await wait(() => t.PGX.series.length === 4 && t.PGX.series[0].table === 'line_data', 'serie di line_data');
    assert.deepEqual(t.PGX.series.map(s => s.column + ':' + s.data_type), ['temperature:Double', 'pressure:Float', 'speed:Int32', 'running:Boolean']);
    assert.equal($('pg-time-column').value, 'ts'); assert.deepEqual([...$('pg-label-column').options].map(o => o.value), ['', 'speed', 'note']);
    assert(t.PGX.series.every(s => s.last_ms === T0 + 3599000));
    checks.push('Connessione, tabelle e viste, colonne numeriche e tipi; la password non esce dal servizio');

    // Prima vista: le serie della tabella, ancorate all'ultimo dato e ridotte.
    const key = col => t.PGX.series.find(s => s.column === col).key, temp = key('temperature');
    await wait(() => (graphSeries(t, temp)?.length || 0) > 1000, 'prima vista automatica');
    assert.equal(t.pgCharts.active.pg.selected.size, 4);
    t.PGX.maxPoints = 1900; $('pg-load').click();
    await wait(() => graphSeries(t, temp)?.length === 1800, 'media a finestre di 2 s');
    const mean = graphSeries(t, temp); assert.equal(mean.t[1] - mean.t[0], 2000); assert.equal(mean.t[0], T0 + 1000);
    assert(Math.abs(mean.v[0] - (temperature(0) + temperature(1)) / 2) < 1e-9);
    assert.match($('pg-status').textContent, /mean/);
    checks.push('Riduzione sul server a finestre di 2 s con tempi al centro della finestra');

    // Tutti i punti originali: tempi e valori esatti; booleano 0/1.
    change(w, $('pg-aggregate'), 'raw');
    await wait(() => graphSeries(t, temp)?.length === 3600, 'tutti i punti');
    const raw = graphSeries(t, temp); assert.equal(raw.t[0], T0); assert.equal(raw.t[3599], T0 + 3599000);
    for (let k = 0; k < 3600; k += 311) assert(Math.abs(raw.v[k] - temperature(k)) < 1e-9, 'valore ' + k);
    const run = graphSeries(t, key('running')); assert.equal(run.v[0], 1); assert.equal(run.v[600], 0); assert.equal(graphSeries(t, key('speed')).v[1234], 1234);
    checks.push('Tutti i punti: tempi e valori identici al database, booleano 0/1');

    // Formato "lungo": una riga per segnale e istante, nome del segnale in una colonna (timestamp senza fuso letto come UTC).
    change(w, $('pg-table'), 'public.samples');
    await wait(() => t.PGX.series[0]?.table === 'samples', 'tabella samples');
    change(w, $('pg-label-column'), 'sensor'); assert(!$('pg-lookback-field').hidden);
    change(w, $('pg-lookback'), '315360000');
    await wait(() => t.PGX.series.length === 2 && t.PGX.series.every(s => s.label_column === 'sensor'), 'segnali dalla colonna sensor');
    assert.deepEqual(t.PGX.series.map(s => s.label), ['A', 'B']);
    const [a, b] = t.PGX.series.map(s => s.key); tick($, a, true); tick($, b, true); $('pg-load').click();
    await wait(() => graphSeries(t, a)?.length === 360 && graphSeries(t, b)?.length === 360, 'segnali A e B');
    assert.equal(graphSeries(t, a).t[0], T0); assert.equal(graphSeries(t, a).v[5], 5); assert.equal(graphSeries(t, b).v[5], -5); assert.equal(graphSeries(t, b).t[5], T0 + 50000);
    checks.push('Formato lungo: segnali dalla colonna delle etichette');

    // Esportazione CSV delle serie del grafico: tutti i punti originali nella cartella indicata.
    const outDir = fs.mkdtempSync(path.join(os.tmpdir(), 'pg-export-'));
    $('pg-export-open').click(); assert($('pg-export-dialog').open);
    assert.match($('pg-export-summary').textContent, /2\s+serie/);
    $('pg-export-directory').value = outDir; $('pg-export-format').value = 'csv'; $('pg-export-name').value = 'pg';
    $('pg-export-form').dispatchEvent(new w.Event('submit', {bubbles: true, cancelable: true}));
    await wait(() => /completata|non riuscita|errore/i.test($('pg-export-status').textContent) && !$('pg-export-start').disabled, 'esportazione CSV', 60000);
    assert.match($('pg-export-status').textContent, /completata:\s+720\s+punti\s+in\s+1\s+file/, $('pg-export-status').textContent);
    const csv = fs.readdirSync(outDir).filter(f => f.endsWith('.csv')); assert.equal(csv.length, 1);
    const lines = fs.readFileSync(path.join(outDir, csv[0]), 'utf8').trim().split(/\r?\n/); assert.equal(lines.length, 721);
    const head = lines[0].split(','), first = lines[1].split(',');
    assert.equal(Number(first[head.indexOf('timestamp_ms')]), T0); assert.equal(first[head.indexOf('source_protocol')], 'postgresql');
    const meta = JSON.parse(fs.readFileSync(path.join(outDir, csv[0].replace(/\.csv$/, '.metadata.json')), 'utf8'));
    assert.equal(meta.exported_from, 'PostgreSQL'); assert(!JSON.stringify(meta).includes(password)); assert.match(meta.endpoint, /^postgresql:\/\/tester@127\.0\.0\.1:/);
    $('pg-export-close').click(); fs.rmSync(outDir, {recursive: true, force: true});
    checks.push('Esportazione CSV con tutti i punti originali e metadati senza password');

    // Vista tabellare: tutte le colonne della tabella a pagine, ordinabile, solo il periodo scelto a richiesta.
    const grid = () => $('pg-grid'), gridRows = () => [...grid().querySelectorAll('tbody tr')], cells = tr => [...tr.querySelectorAll('td')].slice(1).map(td => td.textContent);
    change(w, $('pg-table'), 'plant.line_data'); await wait(() => t.PGX.series[0]?.table === 'line_data', 'tabella line_data');
    $('pg-view-table').click();
    assert(!$('pg-data').hidden && $('pg-graph-main').classList.contains('pg-view-table'), 'vista tabella attiva');
    await wait(() => gridRows().length === 500, 'prima pagina della tabella: ' + $('pg-data-status').textContent);
    assert.deepEqual([...grid().querySelectorAll('thead th[data-col]')].map(th => th.childNodes[0].textContent), ['ts', 'temperature', 'pressure', 'speed', 'running', 'note']);
    assert.deepEqual([...grid().querySelectorAll('thead th[data-col] small')].map(x => x.textContent), ['timestamp with time zone', 'double precision', 'real', 'integer', 'boolean', 'text']);
    assert.equal(cells(gridRows()[0])[0], '2026-10-20 08:59:59Z', 'più recenti prima'); assert.equal(cells(gridRows()[0])[3], '3599'); assert.equal(cells(gridRows()[0])[5], 'ok');
    assert.match($('pg-data-page').textContent, /Righe\s+1.500/); assert($('pg-data-prev').disabled && !$('pg-data-next').disabled);
    $('pg-data-next').click(); await wait(() => t.PGD.result?.page === 2, 'seconda pagina'); assert.equal(t.PGD.result.first_row, 501); assert.equal(cells(gridRows()[0])[3], '3099');
    $('pg-data-prev').click(); await wait(() => t.PGD.result?.page === 1, 'prima pagina di nuovo');
    // Ordinamento sulla pagina: clic sull'intestazione (numerico, poi decrescente).
    grid().querySelector('th[data-col="3"]').click(); assert.equal(cells(gridRows()[0])[3], '3100'); assert.equal(cells(gridRows().at(-1))[3], '3599');
    grid().querySelector('th[data-col="3"]').click(); assert.equal(cells(gridRows()[0])[3], '3599');
    change(w, $('pg-data-order'), 'asc'); await wait(() => t.PGD.result?.sql.includes('ASC') && gridRows().length === 500, 'ordine crescente'); assert.equal(cells(gridRows()[0])[0], '2026-10-20 08:00:00Z');
    change(w, $('pg-data-size'), '100'); await wait(() => gridRows().length === 100, 'cento righe per pagina');
    $('pg-data-period').checked = true; $('pg-data-period').dispatchEvent(new w.Event('change', {bubbles: true})); await wait(() => /WHERE/.test(t.PGD.result?.sql || ''), 'periodo scelto nella query');
    $('pg-data-period').checked = false; $('pg-data-period').dispatchEvent(new w.Event('change', {bubbles: true})); await wait(() => !/WHERE/.test(t.PGD.result?.sql || ''), 'senza periodo');
    change(w, $('pg-table'), 'public.samples'); await wait(() => t.PGD.result?.sql.includes('"samples"') && gridRows().length === 100, 'altra tabella');
    assert.deepEqual([...grid().querySelectorAll('thead th[data-col]')].map(th => th.childNodes[0].textContent), ['time', 'sensor', 'value']);
    checks.push('Vista tabellare: colonne e tipi, pagine, ordinamento, periodo, cambio tabella');

    // Query SQL: esecuzione, errori chiari, sola lettura imposta anche dal server.
    const radio = document => [...w.document.querySelectorAll('input[name="pg-data-source"]')].find(r => r.value === 'query');
    radio().checked = true; radio().dispatchEvent(new w.Event('change', {bubbles: true})); assert(!$('pg-query').hidden && $('pg-data-table-options').hidden);
    const runSql = async sql => {$('pg-query-sql').value = sql; $('pg-data-run').click(); await wait(() => !t.PGD.busy, 'query eseguita');};
    await runSql("SELECT sensor, count(*) AS n, min(value) AS lo FROM public.samples GROUP BY sensor ORDER BY sensor;");
    assert.deepEqual(gridRows().map(cells), [['A', '360', '0'], ['B', '360', '-359']]);
    assert.deepEqual(t.PGD.result.columns.map(c => c.type), ['text', 'bigint', 'double precision']); assert(grid().querySelectorAll('tbody td.num').length >= 4, 'numeri allineati a destra');
    for (const [sql, message] of [['DELETE FROM plant.line_data', /Sono ammesse solo query di lettura/], ['SELECT 1; SELECT 2', /una sola istruzione/], ['SELECT FROM WHERE', /PostgreSQL: .*\(carattere \d+ della query\)/],
        ['WITH d AS (DELETE FROM plant.line_data RETURNING *) SELECT * FROM d', /sola lettura/], ["SELECT * FROM tabella_inesistente", /non trovata|does not exist/], ['', /Scrivi la query/]]) {
      await runSql(sql); assert.match($('pg-data-status').textContent, message, sql + ' -> ' + $('pg-data-status').textContent); assert($('pg-data-status').classList.contains('error'));
    }
    const count = await w.request('/api/postgres/query', {mode: 'query', sql: 'SELECT count(*) AS n FROM plant.line_data'});
    assert.equal(count.result.rows[0][0], 3600, 'nessun dato modificato dai tentativi di scrittura');
    await runSql("SELECT i, 'riga ' || i AS testo, i % 2 = 0 AS pari, NULL::int AS vuoto FROM generate_series(1, 25) AS i ORDER BY i");
    assert.deepEqual(gridRows().slice(0, 2).map(cells), [['1', 'riga 1', 'false', 'NULL'], ['2', 'riga 2', 'true', 'NULL']]); assert.equal(gridRows().length, 25);
    $('pg-data-size').value = '100'; await runSql("SELECT i FROM generate_series(1, 250) AS i ORDER BY i");
    assert.equal(gridRows().length, 100); assert(!$('pg-data-next').disabled); $('pg-data-next').click(); await wait(() => t.PGD.result?.page === 2, 'pagina 2 della query');
    $('pg-data-next').click(); await wait(() => t.PGD.result?.page === 3, 'pagina 3 della query'); assert.equal(gridRows().length, 50); assert($('pg-data-next').disabled); assert.equal(t.PGD.result.first_row, 201);
    await runSql("SELECT 'a,b' AS x, 'dice \"ciao\"' AS y, NULL::int AS z, true AS ok");
    assert.equal(w.pgdRowsText(',', true), 'x,y,z,ok\r\n"a,b","dice ""ciao""",,true\r\n'); assert.equal(grid().querySelector('.pg-null').textContent, 'NULL');
    checks.push('Query SQL: risultati a pagine, tipi, valori nulli, errori chiari, nessuna scrittura possibile');

    // Query salvate: salvataggio con nome, sostituzione con conferma, caricamento, persistenza nel servizio, eliminazione.
    w.confirm = () => true;
    $('pg-query-name').value = 'Conteggio sensori'; $('pg-query-sql').value = 'SELECT sensor, count(*) AS n FROM public.samples GROUP BY sensor ORDER BY sensor';
    $('pg-query-save').click(); await wait(() => t.PGD.saved.length === 1, 'query salvata ' + $('pg-query-status').textContent);
    assert.match($('pg-query-status').textContent, /Query salvata:\s+Conteggio sensori/); const id = t.PGD.saved[0].id; assert.equal($('pg-query-saved').value, id);
    $('pg-query-new').click(); assert.equal($('pg-query-sql').value, ''); assert.equal($('pg-query-saved').value, '');
    $('pg-query-name').value = 'conteggio SENSORI'; $('pg-query-sql').value = 'SELECT 1 AS uno';
    $('pg-query-save').click(); await wait(() => /Esiste già una query/.test($('pg-query-status').textContent), 'nome già presente'); assert.equal(t.PGD.saved.length, 1);
    $('pg-query-save').click(); await wait(() => t.PGD.saved[0]?.sql === 'SELECT 1 AS uno', 'sostituita dopo la conferma'); assert.equal(t.PGD.saved.length, 1);
    $('pg-query-name').value = 'Seconda'; $('pg-query-new').click(); $('pg-query-name').value = 'Seconda'; $('pg-query-sql').value = 'VALUES (1), (2)';
    $('pg-query-save').click(); await wait(() => t.PGD.saved.length === 2, 'seconda query');
    $('pg-query-status').textContent = ''; $('pg-query-save').click(); await wait(() => /Query salvata/.test($('pg-query-status').textContent) && t.PGD.saved.length === 2, 'riscrittura della stessa query');
    $('pg-query-name').value = ''; $('pg-query-save').click(); await wait(() => /Dai un nome/.test($('pg-query-status').textContent), 'nome obbligatorio ' + $('pg-query-status').textContent);
    $('pg-query-name').value = 'Pericolosa'; $('pg-query-sql').value = 'DROP TABLE plant.line_data'; $('pg-query-new').click(); $('pg-query-name').value = 'Pericolosa'; $('pg-query-sql').value = 'DROP TABLE plant.line_data';
    $('pg-query-save').click(); await wait(() => /Sono ammesse solo/.test($('pg-query-status').textContent), 'query di scrittura non salvabile'); assert.equal(t.PGD.saved.length, 2);
    const listed = (await w.request('/api/postgres/queries')).saved.queries; assert.deepEqual(listed.map(q => q.name), ['conteggio SENSORI', 'Seconda']);
    const other = await page(); other.w.eval("showAppPage('pg')"); await wait(() => other.t.PGD.saved.length === 2, 'le query salvate tornano dopo il ricaricamento');
    assert.deepEqual([...other.$('pg-query-saved').options].map(o => o.textContent), ['— query salvate —', 'conteggio SENSORI', 'Seconda']);
    other.$('pg-query-saved').value = id; other.$('pg-query-saved').dispatchEvent(new other.w.Event('change', {bubbles: true}));
    assert.equal(other.$('pg-query-sql').value, 'SELECT 1 AS uno'); assert.equal(other.$('pg-query-name').value, 'conteggio SENSORI');
    $('pg-query-saved').value = id; $('pg-query-saved').dispatchEvent(new w.Event('change', {bubbles: true})); assert.equal($('pg-query-sql').value, 'SELECT 1 AS uno'); assert(!$('pg-query-delete').disabled);
    $('pg-query-delete').click(); await wait(() => t.PGD.saved.length === 1, 'query eliminata'); assert.equal(t.PGD.saved[0].name, 'Seconda'); assert.equal($('pg-query-name').value, '');
    $('pg-query-saved').value = t.PGD.saved[0].id; $('pg-query-saved').dispatchEvent(new w.Event('change', {bubbles: true})); $('pg-query-delete').click(); await wait(() => t.PGD.saved.length === 0, 'tutte eliminate');
    checks.push('Query salvate: nome, sostituzione con conferma, elenco dopo il ricaricamento, eliminazione, scritture rifiutate');
    $('pg-view-chart').click(); assert($('pg-data').hidden && !$('pg-graph-main').classList.contains('pg-view-table'));

    // Ora del PC per i timestamp senza fuso: stessa ora di parete, spostata dell'offset locale.
    $('pg-connection').open = true; change(w, $('pg-timezone'), 'local'); $('pg-connect').click();
    await wait(() => t.PGX.connected && t.PGX.info?.timezone === 'local', 'riconnessione con ora del PC');
    checks.push('Opzione ora del PC per i timestamp senza fuso orario');

    // Lingua: il menu si traduce.
    $('ui-language').value = 'en'; $('ui-language').dispatchEvent(new w.Event('change', {bubbles: true}));
    await wait(() => $('nav-database').textContent === 'Database' && $('pg-connect').textContent === 'Connect', 'inglese');
    checks.push('Traduzione');

    // Password salvata: riconnessione automatica; "Dimentica la password" la cancella.
    const second = await page(); second.w.eval("showAppPage('pg')");
    await wait(() => second.t.PGX.connected, 'riconnessione automatica con la password salvata');
    second.$('pg-forget').click(); await wait(() => !second.t.PGX.connected, 'password dimenticata');
    const saved = (await (await fetch(base + '/api/postgres/settings')).json()).settings; assert.equal(saved.password_saved, false); assert.equal(saved.database, 'plant');
    checks.push('Riconnessione automatica e "Dimentica la password"');

    assert.deepEqual(errors, []);
    console.log(JSON.stringify({result: 'PASS', checks, files: []}));
  } finally {
    for (const dom of pages) dom.window.close();
  }
})().then(() => process.exit(0)).catch(e => {console.error(e.stack); process.exit(1);});
