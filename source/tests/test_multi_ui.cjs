// DOM/component test of the real HTML and JS against a running local test service.
// Canvas calls are stubbed: this test does not claim pixel rendering or native Windows QA.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {JSDOM, VirtualConsole} = require(process.env.JSDOM_PATH || 'jsdom');
const [base, endpoint, rootNode, ...ids] = process.argv.slice(2);
const errors = [];
const pages = [];
const wait = async (condition, label, timeout=12000) => {
  const end=Date.now()+timeout;
  while(Date.now()<end){if(await condition()){console.error('PASS '+label);return;}await new Promise(r=>setTimeout(r,35));}
  throw new Error('Timeout: '+label+'; errors: '+errors.join('; '));
};
const api = async route => (await fetch(base+route)).json();

async function page(){
  const html=await (await fetch(base)).text();
  const vc=new VirtualConsole();vc.on('jsdomError',e=>errors.push(e.message));
  const dom=new JSDOM(html,{url:base,runScripts:'outside-only',pretendToBeVisual:true,virtualConsole:vc});
  pages.push(dom);
  const w=dom.window;
  w.AbortController=globalThis.AbortController;w.FormData=globalThis.FormData;
  w.fetch=async(url,options)=>{try{return await fetch(new URL(url,base),options);}catch(e){errors.push('fetch '+url+': '+e.message);throw e;}};
  w.ResizeObserver=class{observe(){} disconnect(){}};
  w.HTMLCanvasElement.prototype.getBoundingClientRect=()=>({x:0,y:0,left:0,top:0,width:w.__canvasWidth||950,height:560,right:w.__canvasWidth||950,bottom:560});
  const ctx=new Proxy({measureText:text=>({width:String(text).length*7})},{get:(obj,key)=>key in obj?obj[key]:(()=>{})});
  w.HTMLCanvasElement.prototype.getContext=()=>ctx;
  w.addEventListener('error',e=>errors.push(e.message));
  w.eval(['i18n-catalog.js','i18n.js','trend.js','workspace.js','app.js','history.js','advanced.js','fft-ui.js','source.js','s7.js','calculations.js','studio.js','ssh.js','live-extras.js'].map(file=>fs.readFileSync(path.join(__dirname,'../static',file),'utf8')).join('\n')+'\nwindow.uiTest={get trend(){return trend},get selected(){return selected},get record(){return recordState},get connected(){return connected},get liveCharts(){return liveCharts},get historyCharts(){return historyCharts},selectHistoryFile,loadHistory,importHistoryLocalFile};');
  const $=id=>w.document.getElementById(id);
  try{await wait(()=>$('endpoint').value,'configuration loaded');}catch(e){console.error('UI notice:', $('msg').textContent);throw e;}
  await w.opcuaConfigReady;return {dom,w,$,t:w.uiTest};
}

const clickRole=(graph,role)=>graph.element.querySelector('[data-chart-role="'+role+'"]').click();
const control=(graph,role)=>(graph.element.querySelector('[data-chart-role="'+role+'"]')||graph.markerPanel?.querySelector('[data-chart-role="'+role+'"]'));
function pointer(w,graph,type,x,y){const target=type==='up'&&!w.PointerEvent?w:graph.trend.canvas;target.dispatchEvent(new (w.PointerEvent||w.MouseEvent)((w.PointerEvent?'pointer':'mouse')+(type==='down'?'down':type==='up'?'up':'move'),{bubbles:true,clientX:x,clientY:y,button:0,pointerId:1}));}
function place(w,graph,which,time){clickRole(graph,which==='x1'?'markerX1':'markerX2');const t=graph.trend,b=t.domain,{p,w:width}=t.layout;pointer(w,graph,'down',p.l+(time-b.xmin)/(b.xmax-b.xmin)*(width-p.l-p.r),200);}
(async()=>{
  const {dom,w,$,t}=await page();const checks=[];
  try{
    assert.ok($('connection-dialog').contains($('endpoint')));assert.ok($('live-sidebar').contains($('selected')));assert.ok(!$('live-page').contains($('endpoint')));
    assert.ok($('live-page-splitter'));const beforeSplit=$('live-page-splitter').getAttribute('aria-valuenow');$('live-page-splitter').dispatchEvent(new w.KeyboardEvent('keydown',{key:'ArrowRight',bubbles:true}));assert.notEqual($('live-page-splitter').getAttribute('aria-valuenow'),beforeSplit);
    $('nav-connection').click();assert.equal($('connection-dialog').open,true);
    $('endpoint').value=endpoint;$('security').value='None';$('sample_ms').value='10';$('root_nodeid').value=rootNode;
    $('connect').click();await wait(()=>t.connected,'connect in centered dialog');assert.equal($('connection-dialog').open,false);
    $('nav-connection').click();$('connection-dialog').dispatchEvent(new w.KeyboardEvent('keydown',{key:'Escape',bubbles:true}));assert.equal($('connection-dialog').open,false);assert.ok(t.connected);checks.push('connection popup and sidebar');
    for(const id of ids){$('manual-node').value=id;$('add-node').click();await wait(()=>t.selected.has(id)&&!$('add-node').disabled,'acquire signal');}
    await wait(()=>t.liveCharts.charts[0].trend.domain,'live points');
    const live=t.liveCharts,g1=live.charts[0];$('live-add-chart').click();assert.equal(live.charts.length,2);const g2=live.charts[1];
    assert.equal(g2.ids.size,0);assert.equal(g1.ids.size,2);assert.equal(t.selected.size,2);
    const dragged=$('selected').querySelector('.selected-row[data-id="'+ids[1]+'"]');assert.equal(dragged.draggable,true);
    const drop=new w.Event('drop',{bubbles:true,cancelable:true});Object.defineProperty(drop,'dataTransfer',{value:{types:['application/x-opcua-signal'],getData:type=>type==='application/x-opcua-signal'?ids[1]:'',dropEffect:'copy'}});g2.element.dispatchEvent(drop);
    assert.equal(g2.ids.size,1);assert.equal(live.active,g2);assert.equal(g1.ids.size,2);assert.equal(t.selected.size,2);
    control(g2,'title').value='Temperatura';control(g2,'title').dispatchEvent(new w.Event('change',{bubbles:true}));
    clickRole(g2,'pausePlay');const frozen=JSON.stringify(g2.trend.series),oldLive=JSON.stringify(g1.trend.series);
    await wait(()=>JSON.stringify(g1.trend.series)!==oldLive,'other chart remains live');assert.equal(JSON.stringify(g2.trend.series),frozen);checks.push('independent signal assignment and pause');
    const a=g1.trend.domain.xmin+(g1.trend.domain.xmax-g1.trend.domain.xmin)*.3,b=g2.trend.domain.xmin+(g2.trend.domain.xmax-g2.trend.domain.xmin)*.8;
    place(w,g1,'x1',a);place(w,g2,'x2',b);
    assert.equal(g1.trend.markers.x1,g2.trend.markers.x1);assert.equal(g1.trend.markers.x2,g2.trend.markers.x2);
    assert.equal(control(g1,'markerX1Time').textContent,control(g2,'markerX1Time').textContent);assert.equal(control(g1,'markerDeltaTime').textContent,control(g2,'markerDeltaTime').textContent);
    assert.equal(control(g1,'markerDataBody').children.length,2);assert.equal(control(g2,'markerDataBody').children.length,1);
    const shared=JSON.stringify(live.markers);
    pointer(w,g1,'down',240,110);pointer(w,g1,'move',720,450);pointer(w,g1,'up',720,450);assert.ok(g1.trend.zoom);
    const slider=control(g1,'timeScroll');assert.equal(slider.disabled,false);slider.value='850';slider.dispatchEvent(new w.Event('input',{bubbles:true}));assert.equal(JSON.stringify(live.markers),shared);assert.equal(JSON.stringify(g1.trend.markers),JSON.stringify(g2.trend.markers));checks.push('time-aligned live markers with independent zoom and X scroll');
    const g1Snapshot=JSON.stringify(g1.trend.series);clickRole(g2,'clear');assert.equal(JSON.stringify(g1.trend.series),g1Snapshot);checks.push('clear affects one chart');
    // Layout changes do not change the fixed acquisition selection during Record.
    $('record').click();await wait(()=>!$('record-modal').hidden,'Record dialog');$('record-form').dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));await wait(()=>t.record.active,'Record with multiple charts');
    $('live-add-chart').click();assert.equal(live.charts.length,3);clickRole(live.charts[2],'remove');assert.equal(live.charts.length,2);
    await wait(()=>t.record.cycles_received>=30,'Record continues after chart removal');$('record-stop').click();await wait(()=>t.record.state==='stopped','Stop multi-chart recording');assert.equal(t.record.rows_written,t.record.cycles_received*2);checks.push('recording independent of layout');
    $('save').click();await wait(async()=>((await api('/api/config')).saved_server_data?.chart_layout?.charts?.length===2),'persist graph layout');
    const saved=await page();await wait(()=>saved.t.liveCharts.charts.length===2,'restore saved graphs');assert.equal(saved.t.liveCharts.charts[1].name,'Temperatura');assert.equal(saved.t.liveCharts.charts[1].ids.size,1);checks.push('layout survives reload');
    // Each recording chart opens its own file; markers have the same absolute timestamps.
    $('nav-recordings').click();await wait(()=>$('history-file-list').textContent.includes('fixture_A.csv'),'recording repository');
    const choose=name=>[...$('history-file-list').querySelectorAll('button')].find(e=>e.textContent.includes(name)).click();
    const hist=t.historyCharts;choose('fixture_A.csv');$('history-load').click();await wait(()=>hist.active.context.loaded,'CSV chart load');const h1=hist.active;
    assert.equal(h1.nodes.size,2);assert.equal(h1.context.stats.decimated,false);assert.equal(h1.context.stats.full_resolution,true);
    $('history-add-chart').click();const h2=hist.active;assert.equal(hist.charts.length,2);choose('fixture_B.parquet');
    const hb=$('history-signal-list').querySelector('input[value="'+ids[1]+'"]');hb.checked=false;hb.dispatchEvent(new w.Event('change',{bubbles:true}));$('history-load').click();await wait(()=>h2.context.loaded,'Parquet chart load');
    assert.equal(h2.nodes.size,1);assert.equal(h1.nodes.size,2);assert.equal(h1.context.file.name,'fixture_A.csv');assert.equal(h2.context.file.name,'fixture_B.parquet');
    place(w,h1,'x1',1700000012340);place(w,h2,'x2',1700000022340);
    await wait(()=>h1.trend.exactMarkerRows?.get(ids[0])?.x1&&h2.trend.exactMarkerRows?.get(ids[0])?.x1,'exact marker samples in both files');
    assert.equal(h1.trend.exactMarkerRows.get(ids[0]).x1.value,12.34);assert.equal(h2.trend.exactMarkerRows.get(ids[0]).x1.value,123.4);
    assert.equal(h1.trend.exactMarkerRows.get(ids[0]).x2.value,22.34);assert.equal(h2.trend.exactMarkerRows.get(ids[0]).x2.value,223.4);
    assert.equal(h1.trend.markers.x1,h2.trend.markers.x1);assert.equal(h1.trend.markers.x2,h2.trend.markers.x2);
    const exactTable=control(h1,'markerDataBody').textContent;
    for(const width of [1400,900,480]){w.__canvasWidth=width;hist.draw();assert.equal(h1.trend.canvas.width,width);assert.equal(control(h1,'markerDataBody').textContent,exactTable);assert.equal(h1.trend.markers.x1,h2.trend.markers.x1);}
    w.__canvasWidth=950;hist.draw();checks.push('independent CSV/Parquet files; exact synchronized markers preserved on resize');
    clickRole(h1,'activate');assert.equal($('history-selected-name').textContent,'fixture_A.csv');clickRole(h2,'activate');assert.equal($('history-selected-name').textContent,'fixture_B.parquet');
    // Import a file outside Recordings into the active chart through the same file-change handler.
    const external='timestamp_ms,node_id,signal_name,value_numeric,good,data_type\n1700000000000,ns=2;s=External,Esterno,42,True,Double\n1700000000010,ns=2;s=External,Esterno,44,True,Double\n';
    const externalFile=new globalThis.File([external],'external.csv',{type:'text/csv'});
    Object.defineProperty($('history-local-file'),'files',{configurable:true,value:[externalFile]});$('history-local-file').dispatchEvent(new w.Event('change',{bubbles:true}));
    await wait(()=>h2.context.loaded&&h2.context.file?.source==='local','external local file');assert.equal(h1.context.file.name,'fixture_A.csv');assert.equal(h2.nodes.size,1);checks.push('local file import isolated to active chart');
    clickRole(h1,'activate');clickRole(h1,'resetMarkers');assert.equal(h2.trend.markers.x1,null);assert.equal(h2.trend.markers.x2,null);
    $('history-add-chart').click();const h3=hist.active;assert.equal(h3.context.file,null);assert.equal(h3.nodes.size,0);place(w,h1,'x1',1700000012340);assert.equal(h3.trend.markers.x1,h1.trend.markers.x1);
    clickRole(h2,'remove');assert.equal(hist.charts.length,2);
    while(hist.charts.length<hist.constructor.MAX_CHARTS)$('history-add-chart').click();assert.equal($('history-add-chart').disabled,true);
    const allIds=[...w.document.querySelectorAll('[id]')].map(e=>e.id);assert.equal(new Set(allIds).size,allIds.length);
    for(const graph of [...hist.charts].slice(1))clickRole(graph,'remove');assert.equal(hist.charts.length,1);assert.equal(control(hist.charts[0],'remove').disabled,true);checks.push('add/remove up to eight charts without duplicate controls');
    $('nav-live').click();assert.equal(JSON.stringify(live.markers),shared);assert.ok(t.connected);
    assert.equal(errors.length,0,errors.join('\n'));console.log(JSON.stringify({result:'PASS',checks,files:[]}));
  }finally{for(const opened of pages)opened.window.close();}
})().then(()=>process.exit(0)).catch(e=>{console.error(e.stack);process.exit(1);});
