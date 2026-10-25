// DOM/component test of the real HTML and JS against a running local test service.
// Canvas calls are stubbed: this test does not claim pixel rendering or native Windows QA.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {JSDOM, VirtualConsole} = require(process.env.JSDOM_PATH || 'jsdom');
const [base, endpoint, rootNode, ...ids] = process.argv.slice(2);
const errors = [];const pages=[];
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
  pages.push(dom);const w=dom.window;w.FormData=globalThis.FormData;
  w.AbortController=globalThis.AbortController;
  w.fetch=async(url,options)=>{try{return await fetch(new URL(url,base),options);}catch(e){errors.push('fetch '+url+': '+e.message);throw e;}};
  w.ResizeObserver=class{observe(){} disconnect(){}};
  w.HTMLCanvasElement.prototype.getBoundingClientRect=()=>({x:0,y:0,left:0,top:0,width:950,height:560,right:950,bottom:560});
  const ctx=new Proxy({measureText:text=>({width:String(text).length*7})},{get:(obj,key)=>key in obj?obj[key]:(()=>{})});
  w.HTMLCanvasElement.prototype.getContext=()=>ctx;
  w.addEventListener('error',e=>errors.push(e.message));
  w.eval(['i18n-catalog.js','i18n.js','trend.js','workspace.js','app.js','history.js','advanced.js','fft-ui.js','source.js','s7.js','calculations.js','studio.js','ssh.js','live-extras.js'].map(file=>fs.readFileSync(path.join(__dirname,'../static',file),'utf8')).join('\n')+'\nwindow.uiTest={get trend(){return liveCharts.charts[0].trend},get selected(){return selected},get record(){return recordState},get connected(){return connected},get liveCharts(){return liveCharts},get historyCharts(){return historyCharts},selectHistoryFile,loadHistory,importHistoryLocalFile,stopPolling(){applicationClosed=true;polling=false;clearTimeout(pollTimer);clearTimeout(workspaceTimer);},get formulas(){return calculationRegistry.formulas},get draft(){return calculationDraft},get sources(){return calculationSources},formulaDefinition,insertCalculationSource,savedCalculation};');
  const $=id=>w.document.getElementById(id);
  try{await wait(()=>$('endpoint').value,'configuration loaded');}catch(e){console.error('UI notice:', $('msg').textContent);throw e;}
  await w.opcuaConfigReady;await w.calculationsPageReady;return {dom,w,$,t:w.uiTest};
}

const role=(g,r)=>g.element.querySelector('[data-chart-role="'+r+'"]');
function pointer(w,g,type,x,y){const target=type==='up'&&!w.PointerEvent?w:g.trend.canvas;target.dispatchEvent(new (w.PointerEvent||w.MouseEvent)((w.PointerEvent?'pointer':'mouse')+type,{bubbles:true,clientX:x,clientY:y,button:0,pointerId:1}));}
(async()=>{
 const {dom,w,$,t}=await page();
 try {
  $('nav-connection').click();$('endpoint').value=endpoint;$('security').value='None';$('sample_ms').value='10';$('root_nodeid').value=rootNode;
  $('connect').click();await wait(()=>t.connected,'OPC UA connection');
  for(const id of ids){$('manual-node').value=id;$('add-node').click();await wait(()=>t.selected.has(id)&&!$('add-node').disabled,'raw signal '+id);}
  $('nav-calculations').click();await wait(()=>!$('calculations-page').hidden&&$('calc-source-list').children.length>=2&&!$('calc-save').disabled,'Formula Builder page');
  assert.equal($('live-page').hidden,true);assert.equal($('recordings-page').hidden,true);
  $('calc-name').value='Delta Carico';$('calc-unit').value='kN';
  const source1=t.sources.find(s=>s.nodeid===ids[0]),source2=t.sources.find(s=>s.nodeid===ids[1]);
  t.insertCalculationSource(source1);$('calc-expression').value+=' - ';$('calc-expression').selectionStart=$('calc-expression').selectionEnd=$('calc-expression').value.length;t.insertCalculationSource(source2);
  assert.equal($('calc-expression').value,'[Temperatura] - [Potenza]');
  $('calc-validate').click();await wait(()=>$('calc-validation').textContent.includes('Formula valida'),'validate expression');
  await wait(()=>t.liveCharts.charts[0].trend.latestSeries[ids[0]]?.length>5,'original live samples before preview');await wait(()=>!$('calc-test').disabled,'preview ready');$('calc-test').click();await wait(()=>$('calc-preview-table').querySelectorAll('tr').length>1&&!$('calc-test').disabled,'Live preview').catch(e=>{console.error('PREVIEW DEBUG', $('calc-error').textContent,$('calc-preview-status').textContent,$('calc-preview-table').innerHTML,JSON.stringify(t.formulaDefinition()));throw e;});
  assert.ok($('calc-preview-chart').querySelector('polyline'));assert.notEqual($('calc-preview-value').textContent,'—');assert.match($('calc-preview-value').textContent,/kN/);
  $('calc-form').dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));await wait(()=>t.formulas.length===1&&!$('calc-save').disabled,'save formula');
  const f=t.formulas[0],nid=f.nodeid;assert.equal(t.selected.size,2);assert.equal(t.formulas[0].unit,'kN');
  $('calc-add-live').click();await wait(()=>!$('live-page').hidden&&t.liveCharts.active.ids.has(nid),'calculated series in live graph').catch(e=>{console.error('GRAPH DEBUG',$('calc-error').textContent,$('msg').textContent,$('calc-add-live').disabled,JSON.stringify(t.formulaDefinition()),JSON.stringify(t.formulas),[...t.liveCharts.nodes.keys()],[...t.liveCharts.active.ids]);throw e;});
  await wait(()=>t.liveCharts.active.trend.series[nid]?.length>5,'continuous calculated samples');
  assert.equal(t.liveCharts.active.nodes.get(nid).display_name,'Delta Carico');assert.ok($('selected').querySelector('[data-id="'+nid+'"]'));
  $('live-add-chart').click();assert.equal(t.liveCharts.charts.length,2);t.liveCharts.assign(nid,true);
  assert.ok(t.liveCharts.active.ids.has(nid));t.liveCharts.setMarkers({x1:t.liveCharts.charts[0].trend.series[nid][0][0],x2:t.liveCharts.charts[0].trend.series[nid].at(-1)[0]});
  assert.equal(t.liveCharts.charts[0].trend.markers.x1,t.liveCharts.charts[1].trend.markers.x1);
  $('record').click();await wait(()=>!$('record-modal').hidden,'Record including calculation');assert.equal($('record-signal-count').textContent,'3');
  $('record-form').querySelector('input[value="csv"]').checked=true;$('record-form').dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));
  await wait(()=>t.record.active,'Record active');await wait(()=>t.record.cycles_received>20,'Record samples');
  $('record-stop').click();await wait(()=>t.record.state==='stopped','Record drain and Stop');
  const recording=t.record;
  $('nav-calculations').click();await wait(()=>!$('calculations-page').hidden&&!$('calc-test').disabled,'reopen calculation page');
  $('calc-expression').value='1 / 0';$('calc-expression').dispatchEvent(new w.Event('input'));$('calc-test').click();await wait(()=>$('calc-preview-status').textContent.includes('Divisione per zero'),'clear math error');
  // Restore saved expression; replay it over the original CSV fixture.
  const edit=[...$('calc-list').querySelectorAll('button')].find(b=>b.textContent==='Modifica');edit.click();await wait(()=>$('calc-expression').value===f.expression&&!$('calc-test').disabled,'edit saved formula');
  $('calc-context').value='recorded';$('calc-context').dispatchEvent(new w.Event('change'));await wait(()=>!$('calc-file-field').hidden&&!$('calc-test').disabled,'Recorded context');
  const opt=[...$('calc-recording').options].find(o=>o.dataset.name==='fixture_A.csv');assert.ok(opt);$('calc-recording').value=opt.value;$('calc-recording').dispatchEvent(new w.Event('change'));await wait(()=>t.sources.some(s=>s.nodeid===ids[1])&&!$('calc-test').disabled,'Recorded source discovery');
  $('calc-test').click();await wait(()=>$('calc-preview-status').textContent.includes('Good')&&!$('calc-test').disabled,'Recorded preview');
  $('calc-add-recorded').click();await wait(()=>!$('recordings-page').hidden&&t.historyCharts.active.ids.has(nid),'dynamic calculated replay');
  assert.equal(t.historyCharts.active.trend.series[nid].length,6000);
  $('nav-calculations').click();await wait(()=>!$('calc-materialize').disabled,'materialize controls');$('calc-output-format').value='parquet';$('calc-materialize').click();await wait(()=>$('calc-materialize-status').textContent.includes('Salvato:')&&!$('calc-materialize').disabled,'Parquet materialization');
  // Malformed input is rejected by the controlled parser.
  $('calc-expression').value='__import__("os")';$('calc-validate').click();await wait(()=>!$('calc-error').hidden&&!$('calc-validate').disabled,'unsafe expression rejected');
  assert.match($('calc-error').textContent,/consentiti/);
  $('calc-list').querySelector('button').click();await wait(()=>$('calc-expression').value===f.expression&&!$('calc-save').disabled,'restore draft');
  const duplicate=[...$('calc-list').querySelectorAll('button')].find(b=>b.textContent==='Duplica');duplicate.click();await wait(()=>t.formulas.length===2&&!$('calc-save').disabled,'duplicate formula');assert.equal(t.draft.active,false);
  $('save').click();await wait(()=>$('workspace-status').textContent.includes('salvato'),'save workspace');
  const reloaded=await page();assert.equal(reloaded.t.formulas.length,2);assert.ok(reloaded.t.liveCharts.charts.some(g=>g.ids.has(nid)));reloaded.t.stopPolling();await new Promise(r=>setTimeout(r,600));reloaded.dom.window.close();
  assert.equal(errors.length,0,errors.join('\n'));
  console.log(JSON.stringify({result:'PASS',checks:['builder references','validation','preview live/replay','CRUD duplicate','record calculated timestamps','two live charts shared markers','historical original 6000 samples','Parquet materialization','safe parser rejection','workspace restoration'],files:[recording],formula_id:nid,errors},null,2));
 }finally{t.stopPolling();await new Promise(r=>setTimeout(r,600));dom.window.close();}
})().then(()=>process.exit(0)).catch(e=>{console.error(e.stack);process.exit(1);});
