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
  w.eval(['i18n-catalog.js','i18n.js','trend.js','workspace.js','app.js','history.js','advanced.js','fft-ui.js','source.js','s7.js','calculations.js','studio.js','ssh.js','live-extras.js'].map(file=>fs.readFileSync(path.join(__dirname,'../static',file),'utf8')).join('\n')+'\nwindow.uiTest={get trend(){return liveCharts.charts[0].trend},get selected(){return selected},get record(){return recordState},get connected(){return connected},get liveCharts(){return liveCharts},get historyCharts(){return historyCharts},selectHistoryFile,loadHistory,importHistoryLocalFile,openSignalProperties,openAxisProperties,duplicateStudioChart,setNavigatorRange,dropHistorySignal,switchSource,saveWorkspace,chartSnapshot,config,stopPolling(){polling=false;clearTimeout(pollTimer);clearTimeout(workspaceTimer);}};');
  const $=id=>w.document.getElementById(id);
  try{await wait(()=>$('endpoint').value,'configuration loaded');}catch(e){console.error('UI notice:', $('msg').textContent);throw e;}
  await w.opcuaConfigReady;return {dom,w,$,t:w.uiTest};
}

const role=(g,r)=>g.element.querySelector('[data-chart-role="'+r+'"]');
function pointer(w,g,type,x,y){const target=type==='up'&&!w.PointerEvent?w:g.trend.canvas;target.dispatchEvent(new (w.PointerEvent||w.MouseEvent)((w.PointerEvent?'pointer':'mouse')+type,{bubbles:true,clientX:x,clientY:y,button:0,pointerId:1}));}
(async()=>{
 const checks=[];let {w,$,t}=await page();
 try{
  $('nav-connection').click();$('endpoint').value=endpoint;$('security').value='None';$('sample_ms').value='10';$('root_nodeid').value=rootNode;
  $('connect').click();await wait(()=>t.connected,'OPC UA connected');
  for(const id of ids){$('manual-node').value=id;$('add-node').click();await wait(()=>t.selected.has(id)&&!$('add-node').disabled,'signal selected');}
  $('record').click();await wait(()=>!$('record-modal').hidden,'record popup');
  const radio=$('record-form').querySelector('input[name="record-format"][value="rbf"]');assert(radio);radio.checked=true;radio.dispatchEvent(new w.Event('change'));
  assert.equal($('record-rbf-note').hidden,false);assert($('record-rbf-note').textContent.includes('Float32'));assert($('record-binary-settings').hidden);
  $('record-form').dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));
  await wait(()=>t.record.state==='recording'&&t.record.format==='rbf','RBF recording started');
  await wait(()=>t.record.cycles_received>=90,'original acquisition recorded');
  const before=t.record.cycles_received;t.trend.togglePause();await wait(()=>t.record.cycles_received>before+10,'record continues while chart paused');t.trend.togglePause();
  $('record-stop').click();await wait(()=>t.record.state==='stopped','RBF finalized',20000);
  assert.equal(t.record.rbf_phase,'ready');assert(t.record.files.length);assert(t.record.files[0].name.endsWith('.rbf'));assert(t.record.original_files[0].name.endsWith('.parquet'));
  checks.push('RBF popup choice, numeric-conversion disclosure, Record/Stop and companion original archive');
  await t.saveWorkspace(true);const saved=await api('/api/config');assert.equal(saved.workspace.record_format,'rbf');
  w.showAppPage('recordings');const list=await api('/api/recordings');const file=list.files.find(f=>f.name===t.record.files[0].name);assert(file&&file.format==='rbf');
  t.selectHistoryFile(file);await t.loadHistory();const graph=t.historyCharts.active;assert.equal(graph.ids.size,2);assert(graph.trend.domain);
  const data=await w.request('/api/recordings/data',{name:file.name});assert(data.recording.numeric_rows>128);
  const lo=data.recording.start_ms,hi=data.recording.end_ms;
  const fixture=process.env.UI_FFT_FIXTURE;assert(fixture);
  const fft=await w.request('/api/analysis/fft',{source:'repository',name:fixture,signal_ids:[ids[0]],x1:1700000000000,x2:1700000000256,options:{block_size:256}});assert.equal(fft.spectrum.sample_hz,1000);assert.equal(fft.spectrum.peak_hz,125);
  const exported=await w.request('/api/analysis/export',{source:'repository',name:file.name,signal_ids:[ids[0]],x1:lo,x2:hi,format:'csv'});assert(exported.rows>=64);
  checks.push('Generated RBF listed/reopened in charts and CSV interval export; deterministic RBF FFT at 125 Hz; record-format workspace persistence');
  for(const [name,count] of [['TEST_RBF.rbf',64],['TEST_DAS_2.das',17]]){
    const file=(await api('/api/recordings')).files.find(f=>f.name===name);
    if(!file)continue;
    assert(!file.read_error,file.read_error);t.selectHistoryFile(file);await t.loadHistory();
    assert(t.historyCharts.active.context.loaded);assert.equal(t.historyCharts.active.ids.size,count);
    assert(t.historyCharts.active.trend.domain);if(name==='TEST_RBF.rbf')assert.equal(t.historyCharts.active.context.stats.signals[0].points_original,26304);checks.push('Complete supplied '+name+' opens through the normal chart controls');
  }
  assert.deepEqual(errors,[]);console.log(JSON.stringify({result:'PASS',checks,files:[]},null,2));
 }finally{for(const dom of pages){dom.window.uiTest?.stopPolling();dom.window.close();}}
})().then(()=>process.exit(0)).catch(e=>{console.error(e.stack);process.exit(1);});
