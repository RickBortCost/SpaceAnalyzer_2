"""Definizione delle figure della guida: ogni funzione porta l'interfaccia nello stato voluto e chiama sh.shot() con i controlli da numerare.

Il numero N di ogni riquadro corrisponde alla riga N della tabella del capitolo (tools/guide/content/*/*.md).
"""
ORDER = []
PRE = []   # figure da fare prima della connessione (la fonte dati si cambia solo a connessione chiusa)


def figure(fn):
    ORDER.append(fn.__name__.removeprefix('fig_'))
    return fn


def pre(fn):
    PRE.append(fn.__name__.removeprefix('fig_'))
    ORDER.append(fn.__name__.removeprefix('fig_'))
    return fn


SIGNALS = ['Pressione', 'Forza', 'Posizione', 'Velocità', 'Marcia', 'Temperatura', 'Setpoint', 'Potenza']


# ---- preparazione: connessione, scansione, segnali, due grafici ---------------------------------------------------------------

def connect_opcua(sh, stack):
    sh.click('#nav-connection')
    sh.wait(400)
    sh.page.select_option('#data-source', 'opcua')
    sh.wait(300)
    sh.js('(e)=>{const i=document.getElementById("endpoint");i.value=e;i.dispatchEvent(new Event("input",{bubbles:true}));i.dispatchEvent(new Event("change",{bubbles:true}));}', stack.opc_endpoint)
    sh.js('()=>{const s=document.getElementById("sample_ms");if(s){s.value="100";s.dispatchEvent(new Event("change",{bubbles:true}));}}')
    sh.page.select_option('#security', 'None')
    sh.page.select_option('#security_mode', 'None')
    sh.click('#connect')
    sh.page.wait_for_function('()=>document.getElementById("conn-dot")&&document.getElementById("conn-dot").className.includes("ok")||connected===true', timeout=30000)
    sh.wait(800)


def close_dialog(sh, selector='#connection-close'):
    sh.js('(s)=>{const b=document.querySelector(s);if(b)b.click();}', selector)
    sh.wait(300)


def scan_and_select(sh):
    sh.click('#nav-live')
    sh.wait(300)
    sh.js('()=>{const d=document.querySelector("#live-sidebar details:has(#scan)");if(d)d.open=true;}')
    sh.click('#scan')
    sh.page.wait_for_function('()=>/nodi|nodes|Completa|completata/i.test(document.getElementById("scan-status")?.textContent||"")&&!document.getElementById("scan")?.disabled', timeout=60000)
    sh.wait(600)
    for name in SIGNALS:
        sh.js('(n)=>{const s=document.getElementById("search");s.value=n;s.dispatchEvent(new Event("input",{bubbles:true}));}', name)
        sh.wait(250)
        sh.click('#select-filtered')
        sh.wait(250)
    sh.js('()=>{const s=document.getElementById("search");s.value="";s.dispatchEvent(new Event("input",{bubbles:true}));}')
    sh.wait(600)


def node_id(sh, name):
    return sh.js('(n)=>{const e=[...selected].find(([id,x])=>(x.display_name||x.browse_name)===n);return e?e[0]:null;}', name)


def arrange_charts(sh):
    """Due grafici verticali: quattro segnali nel primo, tre nel secondo."""
    sh.page.select_option('#live-layout', 'vertical')
    sh.click('#live-add-chart')
    sh.wait(500)
    first = ['Pressione', 'Forza', 'Posizione', 'Velocità']
    second = ['Temperatura', 'Setpoint', 'Potenza']
    sh.js("""([a,b,ids])=>{const ws=liveCharts,g1=ws.charts[0],g2=ws.charts[1];
      for(const id of ids.all)ws.assignTo(g1,id,ids.first.includes(id));
      for(const id of ids.all)ws.assignTo(g2,id,ids.second.includes(id));ws.activate(g1);}""",
          [0, 0, {'all': [node_id(sh, n) for n in first + second + ['Marcia']], 'first': [node_id(sh, n) for n in first], 'second': [node_id(sh, n) for n in second]}])
    sh.wait(500)


def record_session(sh, name, seconds):
    sh.click('#record')
    sh.wait(600)
    set_value(sh, '#record-session', name)
    sh.click('#record-start')
    sh.wait(seconds * 1000)
    sh.click('#record-stop')
    sh.wait(5000)


def prepare(sh, stack):
    connect_opcua(sh, stack)
    close_dialog(sh)
    scan_and_select(sh)
    arrange_charts(sh)
    record_session(sh, 'Rilievo di prova', 14)   # registrazione reale dell'applicazione, usata dai capitoli su Registrazioni e Conversione


def run(sh, stack, only):
    sh.open()
    for name in PRE:
        if only and name not in only:
            continue
        print('>', name)
        globals()['fig_' + name](sh, stack)
    prepare(sh, stack)
    for name in ORDER:
        if name in PRE or (only and name not in only):
            continue
        print('>', name)
        globals()['fig_' + name](sh, stack)


# ---- figure -----------------------------------------------------------------------------------------------------------------

@figure
def fig_topbar(sh, stack):
    sh.js('()=>{document.querySelector("#nav-tools").closest("details").open=false;}')
    sh.shot('topbar', [(1, '#nav-live'), (2, '#nav-recordings'), (3, '#nav-database'), (4, '#nav-tools'), (5, '#nav-connection'), (6, '#global-pause'),
                       (7, '#global-live-zoom'), (8, '#record'), (9, '#record-stop'), (10, '#global-reset'), (11, '#ui-language'), (12, '#theme-mode'),
                       (13, '#app-clock'), (14, '#save'), (15, '#shutdown')], clip=(0, 0, 1920, 54))


@figure
def fig_connection_opcua(sh, stack):
    sh.click('#nav-connection')
    sh.wait(500)
    sh.shot('connection_opcua', [(1, '#data-source'), (2, '#endpoint'), (3, '#discover'), (4, '#security'), (5, '#security_mode'), (6, '#username'),
                                 (7, '#mqtt-parallel-option'), (8, '#sample_ms'), (9, '#window_seconds'), (10, '#auto_reconnect'), (11, '#connect'), (12, '#disconnect'),
                                 (13, '#connection-save')], selector='#connection-dialog')
    close_dialog(sh)


def source_dialog(sh, name, value):
    sh.click('#nav-connection')
    sh.wait(300)
    sh.page.select_option('#data-source', value)
    sh.wait(500)
    sh.js("()=>{const d=document.getElementById('connection-dialog');d.scrollTop=0;}")
    sh.shot(name, [], selector='#connection-dialog')
    close_dialog(sh)


@pre
def fig_connection_ads(sh, stack):
    source_dialog(sh, 'connection_ads', 'ads')


@pre
def fig_connection_s7(sh, stack):
    source_dialog(sh, 'connection_s7', 's7')


@pre
def fig_connection_s7_time(sh, stack):
    sh.click('#nav-connection')
    sh.wait(300)
    sh.page.select_option('#data-source', 's7')
    sh.wait(500)
    sh.js("()=>{const d=document.getElementById('s7-time-fields');d.open=true;d.scrollIntoView({block:'center'});}")
    sh.wait(400)
    sh.page.set_viewport_size({'width': 1920, 'height': 1500})
    sh.wait(400)
    sh.js("()=>{const d=document.getElementById('s7-time-fields');d.scrollIntoView({block:'center'});}")
    sh.wait(300)
    sh.shot('connection_s7_time', [(1, '#s7_time_tag'), (2, '#s7_time_unit')], selector='#connection-dialog')
    sh.page.set_viewport_size({'width': 1920, 'height': 1080})
    close_dialog(sh)


@pre
def fig_connection_mqtt(sh, stack):
    source_dialog(sh, 'connection_mqtt', 'mqtt')


@figure
def fig_scan(sh, stack):
    sh.click('#nav-live')
    sh.js("()=>{const d=document.querySelector('#live-sidebar details:has(#scan)');if(d){d.open=true;d.scrollIntoView();}}")
    sh.wait(400)
    sh.shot('scan', [(1, '#root_nodeid'), (2, '#scan_limit'), (3, '#browse-root'), (4, '#scan'), (5, '#view-list'), (6, '#search'), (7, '#variables-only'),
                     (8, '#select-filtered')], selector='#live-sidebar details:has(#scan)', pad=6)


@figure
def fig_live(sh, stack):
    sh.js("()=>{const d=document.querySelector('#live-sidebar details:has(#scan)');if(d)d.open=false;}")
    sh.click('#nav-live')
    sh.page.select_option('#live-time-view', 'all')
    sh.wait(600)
    sh.shot('live', [(1, '#live-active-chart'), (2, '#selected'), (3, '#live-time-view'), (4, '#live-layout'), (5, '#live-add-chart'),
                     (6, '#live-charts [data-chart-role="analysis"]'), (7, '#live-charts .chartwrap'),
                     (8, '#live-analysis-dock')])


# ---- grafici LIVE: legenda, rotella, menu, marker ---------------------------------------------------------------------------

def chart_area(sh, n=0, top=0, height=None):
    b = sh.box(f'#live-charts .workspace-chart >> nth={n}')
    return (b['x'], b['y'] + top, b['width'], height or b['height'])


@figure
def fig_legend_menu(sh, stack):
    sh.click('#nav-live')
    sh.page.locator('#live-charts [data-chart-role="legend"] span[data-signal-id]').nth(1).click(button='right')
    sh.wait(400)
    b = sh.box('#live-charts .workspace-chart >> nth=0')
    sh.shot('legend_menu', [], clip=(b['x'], b['y'], 760, 330))
    sh.page.keyboard.press('Escape')


@figure
def fig_legend_shared(sh, stack):
    ids = [node_id(sh, 'Pressione'), node_id(sh, 'Forza')]
    sh.js('async(ids)=>{await liveCharts.charts[0].trend.joinScale(ids[1],ids[0]);}', ids)
    sh.wait(900)
    b = sh.box('#live-charts .workspace-chart >> nth=0')
    sh.shot('legend_shared', [], clip=(b['x'], b['y'], b['width'], 330))


@figure
def fig_wheel(sh, stack):
    sh.js('async()=>{for(const g of liveCharts.charts)await g.trend.separateAllScales?.();}')
    box = sh.box('#live-charts .workspace-chart >> nth=0 >> canvas')
    sh.page.mouse.move(box['x'] + box['width'] * .5, box['y'] + box['height'] * .5)
    for _ in range(4):
        sh.page.mouse.wheel(0, -100)
        sh.wait(250)
    sh.wait(700)
    b = sh.box('#live-charts .workspace-chart >> nth=0')
    sh.shot('wheel', [], clip=(b['x'] - 6, 128, b['width'] + 12, b['height'] + 24))
    sh.js('()=>{liveCharts.setLiveSpan(null);}')
    sh.wait(500)


@figure
def fig_chart_menu(sh, stack):
    sh.page.locator('#live-charts .workspace-chart >> nth=0 >> details.chart-menu > summary').click()
    sh.wait(400)
    b = sh.box('#live-charts .workspace-chart >> nth=0')
    sh.shot('chart_menu', [], clip=(b['x'] + b['width'] - 760, b['y'], 760, 420))
    sh.page.keyboard.press('Escape')
    sh.js('()=>{document.querySelectorAll(".chart-menu").forEach(d=>d.open=false);}')


@figure
def fig_markers(sh, stack):
    sh.page.select_option('#live-layout', 'single')
    sh.click('#global-pause')
    sh.wait(700)
    sh.js("""()=>{const t=liveCharts.charts[0].trend,d=t.domain;const a=d.xmin+(d.xmax-d.xmin)*.3,b=d.xmin+(d.xmax-d.xmin)*.72;
      liveCharts.charts[0].trend.setMarker('x1',a);liveCharts.charts[0].trend.setMarker('x2',b);document.getElementById('live-analysis-dock').open=true;
      document.getElementById('live-analysis-dock').dispatchEvent(new Event('toggle'));}""")
    sh.wait(1500)
    sh.shot('markers', [(1, '#live-analysis-dock summary'), (2, '#live-analysis-dock [data-chart-role="markerX1"]'), (3, '#live-analysis-dock [data-chart-role="markerX2"]'),
                        (4, '#live-analysis-dock [data-chart-role="resetMarkers"]'), (5, '#live-analysis-dock .marker-summary'),
                        (6, '#live-analysis-dock .marker-table-wrap')], clip=(340, 120, 1580, 930))
    sh.js("()=>{document.getElementById('live-analysis-dock').open=false;}")


@figure
def fig_collapse_reorder(sh, stack):
    sh.page.select_option('#live-layout', 'vertical')
    sh.wait(600)
    sh.js("()=>{document.getElementById('live-analysis-dock').open=false;}")
    sh.page.locator('#live-charts [data-chart-role="collapse"]').nth(1).click()
    sh.wait(700)
    b = sh.box('#live-charts')
    sh.shot('collapse_reorder', [(1, '#live-charts .workspace-chart >> nth=0 >> .chart-drag-handle'), (2, '#live-charts .workspace-chart >> nth=1 >> [data-chart-role="collapse"]'),
                                 (3, '#live-charts .workspace-chart >> nth=0 >> [data-chart-role="expand"]')], clip=(b['x'] - 6, b['y'] - 6, b['width'] + 12, min(b['height'], 640)))
    sh.page.locator('#live-charts [data-chart-role="collapse"]').nth(1).click()
    sh.wait(500)


@figure
def fig_signal_properties(sh, stack):
    sh.js("()=>{const id=[...selected].find(([i,n])=>(n.display_name||'')==='Pressione')[0];openSignalProperties(liveCharts,liveCharts.charts[0],id);}")
    sh.wait(600)
    sh.shot('signal_properties', [(1, '#prop-signal'), (2, '#prop-alias'), (3, '#prop-unit'), (4, '#prop-axis'), (5, '#prop-color'), (6, '#prop-width'), (7, '#prop-visible')],
            selector='#properties-dialog')
    sh.js("()=>{document.getElementById('properties-dialog').close?.();}")


@figure
def fig_menus(sh, stack):
    sh.js("()=>{document.querySelector('#database-menu').open=true;}")
    sh.wait(300)
    b = sh.box('#database-menu')
    sh.shot('menu_database', [], clip=(b['x'] - 20, 0, 420, 200))
    sh.js("()=>{document.querySelector('#database-menu').open=false;document.querySelector('#tools-menu').open=true;}")
    sh.wait(300)
    sh.shot('menu_tools', [], clip=(b['x'] - 20, 0, 520, 200))
    sh.js("()=>{document.querySelector('#tools-menu').open=false;}")


# ---- formule, registrazione -----------------------------------------------------------------------------------------------

def set_value(sh, selector, value, event='input'):
    sh.js('([s,v,e])=>{const i=document.querySelector(s);i.value=v;i.dispatchEvent(new Event(e,{bubbles:true}));if(e!=="change")i.dispatchEvent(new Event("change",{bubbles:true}));}', [selector, value, event])


@figure
def fig_calc_quick(sh, stack):
    sh.click('#nav-live')
    sh.page.select_option('#live-layout', 'single')
    sh.js("()=>{document.getElementById('live-analysis-dock').open=false;const q=document.getElementById('quick-calculations');q.open=true;q.scrollIntoView();}")
    sh.wait(500)
    sh.page.select_option('#quick-bindings select >> nth=0', label='[OPC UA] Temperatura')
    sh.click('#quick-add-variable')
    sh.wait(300)
    sh.page.select_option('#quick-bindings select >> nth=1', label='[OPC UA] Setpoint')
    set_value(sh, '#quick-expression', '[A]-[B]')
    set_value(sh, '#quick-name', 'Errore temperatura')
    set_value(sh, '#quick-unit', '°C')
    sh.page.select_option('#quick-operation', 'max')
    sh.click('#quick-run')
    sh.wait(2500)
    sh.page.set_viewport_size({'width': 1920, 'height': 1500})
    sh.wait(500)
    sh.js("()=>{document.getElementById('quick-calculations').scrollIntoView({block:'start'});}")
    sh.wait(400)
    sh.shot('calc_quick', [(1, '#quick-bindings'), (2, '#quick-expression'), (3, '#quick-alignment'), (4, '#quick-operation'), (5, '#quick-value'), (6, '#quick-save')],
            selector='#quick-calculations', pad=4)
    sh.page.set_viewport_size({'width': 1920, 'height': 1080})


@figure
def fig_calc_page(sh, stack):
    sh.click('#nav-tools')
    sh.click('#nav-calculations')
    sh.wait(1200)
    set_value(sh, '#calc-name', 'Errore temperatura')
    set_value(sh, '#calc-unit', '°C')
    set_value(sh, '#calc-expression', '[Temperatura]-[Setpoint]')
    sh.wait(500)
    sh.click('#calc-validate')
    sh.wait(1200)
    sh.shot('calc_page', [(1, '#calc-name'), (2, '#calc-live'), (3, '#calc-source-list'), (4, '#calc-expression'), (5, '#calc-validate'), (6, '#calc-test'), (7, '#calc-save')])
    sh.click('#nav-live')


@figure
def fig_record_dialog(sh, stack):
    sh.click('#nav-live')
    sh.js("()=>{liveCharts.setLiveSpan?.(null);}")
    sh.click('#record')
    sh.wait(700)
    set_value(sh, '#record-session', 'Prova laminazione')
    set_value(sh, '#record-machine', 'Linea 2')
    set_value(sh, '#record-operator', 'Rossi')
    sh.page.set_viewport_size({'width': 1920, 'height': 1300})
    sh.wait(500)
    sh.js("()=>{document.getElementById('record-form').parentElement.setAttribute('data-shot','1');}")
    sh.shot('record_dialog', [(1, '#record-form fieldset >> nth=0'), (2, '#record-parquet-profile'), (3, '#record-session'), (4, '#record-duration'), (5, '#record-max-mb'),
                              (6, '#record-buffer'), (7, '#record-directory'), (8, '#record-start')], selector='[data-shot]')
    sh.page.set_viewport_size({'width': 1920, 'height': 1080})
    sh.click('#record-cancel')
    sh.wait(400)


@figure
def fig_record_bar(sh, stack):
    sh.click('#nav-live')
    sh.click('#record')
    sh.wait(600)
    set_value(sh, '#record-session', 'Prova laminazione')
    set_value(sh, '#record-machine', 'Linea 2')
    sh.click('#record-start')
    sh.wait(14000)
    sh.shot('record_bar', [], clip=(340, 56, 1580, 80))
    sh.click('#record-stop')
    sh.wait(5000)
    sh.shot('record_saved', [], clip=(340, 56, 1580, 130))


# ---- Registrazioni, FFT, conversione, confronto ----------------------------------------------------------------------------------

def pick_file(sh, name):
    sh.page.locator('#history-file-list .history-file', has_text=name).first.click()
    sh.wait(700)


@figure
def fig_recordings(sh, stack):
    sh.click('#nav-recordings')
    sh.wait(1500)
    pick_file(sh, 'Rilievo_di_prova')
    sh.click('#history-select-none')
    for name in ('Pressione', 'Forza', 'Posizione', 'Velocità'):
        sh.page.locator('#history-signal-list label', has_text=name).first.locator('input').check()
    sh.click('#history-load')
    sh.wait(3000)
    sh.js("()=>{for(const e of document.querySelectorAll('#recordings-page .history-browser,#history-file-list')){e.scrollTop=0;}}")
    sh.wait(300)
    sh.shot('recordings', [(1, '#history-location-change'), (2, '#history-open-local'), (3, '#history-file-search'), (4, '#history-filter-format'),
                           (5, '#history-file-list'), (6, '#history-load'), (7, '#history-active-chart'), (8, '#history-layout')])


@figure
def fig_recording_markers(sh, stack):
    sh.js("""()=>{const g=historyCharts.active,t=g.trend,d=t.domain;t.setMarker('x1',d.xmin+(d.xmax-d.xmin)*.3);t.setMarker('x2',d.xmin+(d.xmax-d.xmin)*.7);
      document.getElementById('history-analysis-dock').open=true;document.getElementById('history-analysis-dock').dispatchEvent(new Event('toggle'));}""")
    sh.wait(1500)
    sh.shot('recording_markers', [], clip=(340, 120, 1580, 930))
    sh.js("()=>{document.getElementById('history-analysis-dock').open=false;}")


@figure
def fig_fft_params(sh, stack):
    sh.click('#nav-recordings')
    sh.click('#history-close-all')
    pick_file(sh, 'Vibrazione_1kHz')
    sh.click('#history-load')
    sh.wait(3500)
    sh.js("""()=>{const g=historyCharts.active,t=g.trend,d=t.domain;t.setMarker('x1',d.xmin+(d.xmax-d.xmin)*.2);t.setMarker('x2',d.xmin+(d.xmax-d.xmin)*.8);}""")
    sh.wait(800)
    sh.page.locator('#history-charts [data-chart-role="analysis"]').first.click()
    sh.wait(900)
    sh.shot('fft_params', [(1, '#fft-signal'), (2, '#fft-window'), (3, '#fft-resolution-mode'), (4, '#fft-overlap'), (5, '#fft-average'), (6, '#fft-output'),
                           (7, '#fft-gap-policy'), (8, '#fft-fundamental-mode'), (9, '#fft-detrend'), (10, '#fft-run')], selector='#analysis-dialog')


@figure
def fig_fft_result(sh, stack):
    sh.click('#fft-run')
    sh.wait(3500)
    sh.js("()=>{const d=document.getElementById('analysis-dialog');d.scrollTop=d.scrollHeight;}")
    sh.wait(400)
    sh.page.set_viewport_size({'width': 1920, 'height': 1400})
    sh.wait(700)
    sh.shot('fft_result', [], selector='#analysis-dialog')
    sh.page.set_viewport_size({'width': 1920, 'height': 1080})
    sh.js("()=>{document.getElementById('analysis-close').click();}")


@figure
def fig_conversion(sh, stack):
    sh.click('#nav-tools')
    sh.click('#nav-conversion')
    sh.wait(1500)
    sh.page.locator('#conv-file-list button', has_text='Rilievo_di_prova').first.click()
    sh.wait(800)
    sh.page.select_option('#conv-format', 'das')
    sh.wait(500)
    sh.shot('conversion', [(1, '#conv-search'), (2, '#conv-open-local'), (3, '#conv-file-list'), (4, '#conv-format'), (5, '#conv-all'), (6, '#conv-signal-search'),
                           (7, '#conv-interval'), (8, '#conv-start')])
    sh.click('#conv-start')
    sh.page.wait_for_function('()=>document.getElementById("conv-result")&&!document.getElementById("conv-result").hidden', timeout=60000)
    sh.wait(800)
    sh.shot('conversion_done', [(1, '#conv-status'), (2, '#conv-result-list')])


@figure
def fig_conversion_sac(sh, stack):
    sh.click('#nav-tools')
    sh.click('#nav-conversion')
    sh.wait(1500)
    sac = '#conv-file-list .conversion-file[title$=".sac"]'
    sh.page.locator(sac).first.click()
    sh.wait(800)
    sh.page.select_option('#conv-format', 'parquet')
    sh.wait(500)
    sh.shot('conversion_sac', [(1, sac), (2, '#conv-info'), (3, '#conv-format-note'), (4, '#conv-start')])


@figure
def fig_conversion_svdx(sh, stack):
    sh.click('#nav-tools')
    sh.click('#nav-conversion')
    sh.wait(1500)
    sh.page.locator('#conv-file-list button', has_text='Scope_Accelerometri').first.click()
    sh.wait(800)
    sh.page.select_option('#conv-format', 'parquet')
    sh.page.locator('#conv-signal-list input[type=checkbox]').nth(2).uncheck()
    sh.wait(500)
    sh.shot('conversion_svdx', [(1, '#conv-file-list'), (2, '#conv-signal-list'), (3, '#conv-interval'), (4, '#conv-start')])


@figure
def fig_compare(sh, stack):
    sh.click('#nav-recordings')
    sh.click('#history-close-all')
    sh.wait(500)
    for name in ('Confronto_1ms', 'Confronto_10ms'):
        sh.page.locator('#history-file-list .history-file-item', has_text=name).first.locator('input.history-file-check').check()
    sh.wait(400)
    sh.click('#history-compare')
    sh.wait(4000)
    sh.shot('compare', [(1, '#history-compare-mode'), (2, '#history-compare-coverage'), (3, '#history-compare-files'), (4, '#history-compare')])


@figure
def fig_ssh(sh, stack):
    sh.click('#nav-tools')
    sh.click('#nav-ssh')
    sh.wait(1200)
    sh.shot('ssh', [(1, '#ssh-host'), (2, '#ssh-username'), (3, '#ssh-auth'), (4, '#ssh-connect'), (5, '#ssh-mode'), (6, '#ssh-editor'), (7, '#ssh-run'), (8, '#ssh-export')])
    sh.click('#nav-live')


# ---- database: InfluxDB e PostgreSQL -----------------------------------------------------------------------------------------

def open_database_page(sh, nav):
    sh.click('#nav-database')
    sh.wait(300)
    sh.click(nav)
    sh.wait(900)


@figure
def fig_influx(sh, stack):
    open_database_page(sh, '#nav-influx')
    sh.js('([u,t])=>{const a=document.getElementById("influx-url"),b=document.getElementById("influx-token");a.value=u;b.value=t;}', [stack.influx_url, stack.influx_token])
    sh.click('#influx-connect')
    sh.page.wait_for_function('()=>uiInflux.IFX.connected&&uiInflux.IFX.series.length>0', timeout=30000)
    sh.wait(2500)
    # tre serie: due pressioni e il contatore
    sh.js("""()=>{const ctx=uiInflux.influxCharts.active.influx;ctx.selected.clear();
      for(const s of uiInflux.IFX.series){if(s.measurement==='pressure'||s.measurement==='counter')ctx.selected.add(s.key);}
      renderInfluxSelection();}""")
    sh.click('#influx-load')
    sh.wait(3500)
    sh.js("()=>{document.getElementById('influx-connection').open=false;for(const e of document.querySelectorAll('#influx-page .influx-browser')){e.scrollTop=0;}}")
    sh.wait(400)
    sh.js("()=>{document.getElementById('influx-connection').open=true;}")
    sh.wait(300)
    sh.shot('influx_page', [(1, '#influx-connection > summary'), (2, '#influx-bucket'), (3, '#influx-system'), (4, '#influx-lookback'), (5, '#influx-refresh'), (6, '#influx-search'),
                            (7, '#influx-presets'), (8, '#influx-presets button:last-child'), (9, '#influx-aggregate'), (10, '#influx-refresh-every'), (11, '#influx-load'),
                            (12, '#influx-clear-selection'), (13, '#influx-active-chart'), (14, '#influx-layout'), (15, '#influx-add-chart')])
    sh.js("()=>{document.getElementById('influx-connection').open=false;}")


@figure
def fig_influx_export(sh, stack):
    sh.click('#influx-export-open')
    sh.wait(700)
    sh.js("()=>{document.getElementById('influx-export-directory').value='D:\\\\Dati\\\\Esportazioni';}")
    sh.shot('influx_export', [(1, '#influx-export-dialog .influx-export-period'), (2, '#influx-export-format'), (3, '#influx-export-name'), (4, '#influx-export-directory'),
                              (5, '#influx-export-browse'), (6, '#influx-export-start')], selector='#influx-export-dialog')
    sh.click('#influx-export-close')


@figure
def fig_postgres(sh, stack):
    if not getattr(stack, 'pg', None):
        print('  PostgreSQL non disponibile: figure PostgreSQL saltate')
        return
    open_database_page(sh, '#nav-pg')
    pg = stack.pg
    sh.js('(p)=>{for(const [id,v] of Object.entries({"pg-host":p.host,"pg-port":p.port,"pg-database":p.database,"pg-user":p.user,"pg-password":p.password}))document.getElementById(id).value=v;}', pg)
    sh.click('#pg-connect')
    sh.page.wait_for_function('()=>uiPg.PGX.connected&&uiPg.PGX.series.length>0', timeout=30000)
    sh.wait(1500)
    sh.page.select_option('#pg-table', 'plant.line_data')
    sh.page.wait_for_function('()=>uiPg.PGX.series.length===4&&uiPg.PGX.series[0].table==="line_data"', timeout=20000)
    sh.wait(3000)
    sh.js("()=>{document.getElementById('pg-connection').open=false;}")
    sh.wait(300)
    sh.shot('pg_chart', [(1, '#pg-connection > summary'), (2, '#pg-table'), (3, '#pg-time-column'), (4, '#pg-label-column'), (5, '#pg-refresh'), (6, '#pg-search'), (7, '#pg-series'),
                         (8, '#pg-presets'), (9, '#pg-aggregate'), (10, '#pg-load'), (11, '#pg-view-chart'), (12, '#pg-view-table')])
    sh.js("()=>{document.getElementById('pg-connection').open=false;}")
    sh.click('#pg-view-table')
    sh.page.wait_for_function('()=>uiPg.PGD.result', timeout=20000)
    sh.wait(700)
    sh.shot('pg_table', [(1, '#pg-data-table-name'), (2, '#pg-data-period'), (3, '#pg-data-order'), (4, '#pg-data-size'), (5, '#pg-data-run'), (6, '#pg-data-next'),
                         (7, '#pg-data-csv'), (8, '#pg-grid-wrap')])
    sh.js("""()=>{const r=document.querySelector('input[name="pg-data-source"][value="query"]');r.checked=true;r.dispatchEvent(new Event('change',{bubbles:true}));
      document.getElementById('pg-query-name').value='Medie per periodo';
      document.getElementById('pg-query-sql').value="SELECT date_trunc('minute', ts) AS minuto,\\n       round(avg(temperature)::numeric, 2) AS temperatura_media,\\n       max(pressure) AS pressione_max\\nFROM plant.line_data\\nGROUP BY 1\\nORDER BY 1\\nLIMIT 20";
      document.getElementById('pg-query-save').click();}""")
    sh.wait(900)
    sh.click('#pg-data-run')
    sh.page.wait_for_function('()=>!uiPg.PGD.busy&&uiPg.PGD.result&&uiPg.PGD.result.mode==="query"', timeout=20000)
    sh.wait(700)
    sh.shot('pg_query', [(1, '#pg-query-saved'), (2, '#pg-query-name'), (3, '#pg-query-save'), (4, '#pg-query-new'), (5, '#pg-query-delete'), (6, '#pg-query-sql'), (7, '#pg-data-run'),
                         (8, '#pg-grid-wrap')])
    sh.js("()=>{uiPg.setPgView('chart');}")
