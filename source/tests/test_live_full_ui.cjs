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
  w.eval(['i18n-catalog.js','i18n.js','trend.js','workspace.js','app.js','history.js','advanced.js','fft-ui.js','source.js','s7.js','calculations.js','studio.js','ssh.js','live-extras.js'].map(file=>fs.readFileSync(path.join(__dirname,'../static',file),'utf8')).join('\n')+'\nwindow.uiTest={get liveViewMode(){return liveViewMode},get trend(){return liveCharts.charts[0].trend},get selected(){return selected},get record(){return recordState},get connected(){return connected},get liveCharts(){return liveCharts},get historyCharts(){return historyCharts},selectHistoryFile,loadHistory,importHistoryLocalFile,openSignalProperties,openAxisProperties,duplicateStudioChart,setNavigatorRange,dropHistorySignal,switchSource,saveWorkspace,chartSnapshot,config,stopPolling(){polling=false;clearTimeout(pollTimer);clearTimeout(workspaceTimer);}};');
  const $=id=>w.document.getElementById(id);
  try{await wait(()=>$('endpoint').value,'configuration loaded');}catch(e){console.error('UI notice:', $('msg').textContent);throw e;}
  await w.opcuaConfigReady;return {dom,w,$,t:w.uiTest};
}

const role=(g,r)=>g.element.querySelector('[data-chart-role="'+r+'"]');
function pointer(w,g,type,x,y){const target=type==='up'&&!w.PointerEvent?w:g.trend.canvas;target.dispatchEvent(new (w.PointerEvent||w.MouseEvent)((w.PointerEvent?'pointer':'mouse')+type,{bubbles:true,clientX:x,clientY:y,button:0,pointerId:1}));}
(async()=>{
 const checks=[];let {w,$,t}=await page();
 try{
  assert.equal($('live-time-view').value,'all');
  $('nav-connection').click();$('endpoint').value=endpoint;$('security').value='None';$('sample_ms').value='10';$('window_seconds').value='10';$('buffer_mode').value='time';$('root_nodeid').value=rootNode;
  $('connect').click();await wait(()=>t.connected,'connected');
  for(const id of ids){$('manual-node').value=id;$('add-node').click();await wait(()=>t.selected.has(id)&&!$('add-node').disabled,'selected');}
  await wait(()=>t.trend.domain,'first domain');
  const ws=t.liveCharts,g1=ws.charts[0],first=g1.trend.domain.xmin;
  $('live-add-chart').click();const g2=ws.active;ws.assignTo(g2,ids[1],true);
  await wait(()=>g2.trend.domain,'second chart');
  assert.equal(g2.trend.domain.xmin,g1.trend.domain.xmin);
  await wait(()=>g1.trend.domain.xmax-first>10800,'history exceeds 10 s raw window',16000);
  const raw=await api('/api/values?view=window');
  assert(raw.samples[ids[0]][0][0]>first+300);
  assert.equal(g1.trend.domain.xmin,first);assert.equal(g2.trend.domain.xmin,first);
  checks.push('Full-session range and original start retained on two live charts beyond 10-second raw FIFO eviction');
  const end=g1.trend.domain.xmax;g1.trend.togglePause();
  await wait(()=>g2.trend.domain.xmax>end+400,'second chart continues while first paused');
  assert.equal(g1.trend.domain.xmax,end);
  g1.trend.togglePause();await wait(()=>g1.trend.domain.xmax>end+400,'play catches up');
  assert.equal(g1.trend.domain.xmin,first);
  ws.setMarkers({x1:first+100,x2:first+400});assert.equal(g2.trend.markers.x1,first+100);
  ws.hoverAt(g1,first+200);assert.equal(g2.trend.crosshair,first+200);
  ws.syncRange(g1,{xmin:first+100,xmax:first+500});assert.equal(g2.trend.domain.xmin,first+100);
  g1.trend.resetZoom();await wait(()=>!g2.trend.frozen&&g2.trend.domain.xmin===first,'linked zoom reset returns to growing full range');
  checks.push('Pause/play, shared zoom, X1/X2 and crosshair preserved');
  $('live-time-view').value='window';$('live-time-view').dispatchEvent(new w.Event('change'));
  await wait(()=>g1.trend.domain.xmin>first+300,'recent mode');
  $('live-time-view').value='all';$('live-time-view').dispatchEvent(new w.Event('change'));
  await wait(()=>g1.trend.domain.xmin===first,'full mode restored');
  assert($('live-session-status').textContent.includes('Panoramica'));
  await t.saveWorkspace(true);const saved=(await api('/api/config')).workspace;assert.equal(saved.live.viewMode,'all');
  t.stopPolling();({w,$,t}=await page());
  assert.equal(t.liveViewMode,'all');await wait(()=>t.trend.domain?.xmin===first,'workspace reload preserves display preference and current session');
  checks.push('Recent/full selector, visible summary disclosure and workspace reload verified');
  assert.deepEqual(errors,[]);console.log(JSON.stringify({result:'PASS',checks,files:[]},null,2));
 }finally{for(const dom of pages){dom.window.uiTest?.stopPolling();dom.window.close();}}
})().then(()=>process.exit(0)).catch(e=>{console.error(e.stack);process.exit(1);});
