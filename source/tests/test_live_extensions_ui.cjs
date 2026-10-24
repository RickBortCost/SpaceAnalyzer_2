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
 const {w,$,t}=await page(),checks=[],mqtt='mqtt:plant/motor#/pressure';
 try{
  $('endpoint').value=endpoint;$('security').value='None';$('sample_ms').value='20';
  $('enable-opcua').checked=true;$('enable-mqtt').checked=true;
  $('mqtt-parallel').checked=true;$('mqtt-parallel').dispatchEvent(new w.Event('change'));
  $('mqtt_host').value='127.0.0.1';$('mqtt_port').value=process.env.TEST_MQTT_PORT;$('mqtt_username').value='operator';$('mqtt_password').value=' test-session ';$('mqtt_topics').value='plant/#';
  assert.equal($('multi-source-settings').hidden,false);$('connect').click();
  await wait(async()=>t.connected&&(await api('/api/values')).parallel_mqtt?.state==='connected','two protocols connected');
  $('manual-node').value=ids[0];$('add-node').click();await wait(()=>t.selected.has(ids[0]),'OPC signal selected');
  await wait(()=>!$('browse-source').disabled,'connection command completed');
  $('browse-source').value='mqtt';$('browse-source').dispatchEvent(new w.Event('change'));
  await wait(()=>$('tree').querySelector('input[data-nodeid="'+mqtt+'"]'),'observed MQTT catalogue');
  const exp=$('tree').querySelector('.expander');exp.click();const box=$('tree').querySelector('input[data-nodeid="'+mqtt+'"]');box.click();
  await wait(()=>t.selected.has(mqtt),'MQTT selected alongside OPC');
  await wait(async()=>{const j=await api('/api/values');return j.samples[mqtt]?.length>10&&j.samples[ids[0]]?.length>10;},'simultaneous numeric data');
  $('mqtt-autoscan').click();box.focus();$('tree').scrollTop=15;
  const count=$('tree').querySelectorAll('input').length;
  await wait(()=>$('tree').querySelectorAll('input').length>count,'Autoscan adds a new observed field',7000);
  assert.equal($('tree').querySelector('input[data-nodeid="'+mqtt+'"]'),box);assert.equal($('tree').querySelector('.expander'),exp);
  assert.equal(exp.getAttribute('aria-expanded'),'true');assert.equal(box.checked,true);assert.equal(w.document.activeElement,box);assert.equal($('tree').scrollTop,15);
  assert.equal(t.selected.size,2);checks.push('Autoscan every second preserves open branches, DOM identity, focus, scroll and selected signals while discovering new fields');
  const graph=t.liveCharts.active,trend=graph.trend;await wait(()=>trend.domain&&graph.ids.size===2,'combined chart');
  trend.setMarker('x1',trend.domain.xmin+.25*(trend.domain.xmax-trend.domain.xmin));trend.setMarker('x2',trend.domain.xmin+.75*(trend.domain.xmax-trend.domain.xmin));
  const first=trend.markers.x1;assert.equal(trend.frozen,false);
  await wait(()=>trend.markers.x1>first+20,'markers follow the live viewport');
  assert(Math.abs((trend.markers.x1-trend.domain.xmin)/(trend.domain.xmax-trend.domain.xmin)-.25)<1e-5);
  assert.equal(trend.frozen,false);role(graph,'analysis').click();assert.equal($('analysis-dialog').open,false);
  await w.quickCalculationsTick();
  const select=$('quick-bindings').querySelector('select');select.value=mqtt;select.dispatchEvent(new w.Event('change'));$('quick-run').click();
  await wait(()=>$('quick-value').textContent!=='—','formula analysis from original samples');
  $('quick-expression').value='[A]*0+123';$('quick-expression').dispatchEvent(new w.Event('input'));$('quick-run').click();
  await wait(()=>$('quick-value').textContent==='123','edited expression calculated');assert.equal(t.selected.size,2);
  checks.push('Markers stay in Play at fixed screen fractions; interval results update from raw samples without new variables');
  trend.togglePause();const paused={...trend.markers};await wait(()=>graph.analysisSnapshotId,'frozen original-sample snapshot');
  await new Promise(r=>setTimeout(r,1200));assert.deepEqual({...trend.markers},paused);role(graph,'analysis').click();assert.equal($('analysis-dialog').open,true);assert.equal($('fft-mode').value,'interval');assert.equal($('fft-mode').disabled,true);
  $('analysis-close').click();trend.togglePause();await wait(()=>trend.markers.x1>paused.x1,'resume restores relative markers');
  checks.push('FFT requires paused graph and two markers; paused timestamps remain fixed and resume follows the viewport');
  await w.I18N.setLanguage('en',{persist:true});assert.equal($('quick-calculations').querySelector('summary').textContent,'Calculated data');await w.I18N.setLanguage('it',{persist:true});
  $('disconnect').click();await wait(async()=>!(await api('/api/values')).parallel_mqtt.enabled&&!t.connected,'both sessions closed');
  await t.switchSource('mqtt');await t.switchSource('opcua');assert.equal($('mqtt-parallel').checked,true);assert(t.selected.has(mqtt)&&t.selected.has(ids[0]));
  checks.push('Simultaneous-source profile survives switching away and back');
  assert.equal(errors.length,0,errors.join('; '));console.log(JSON.stringify({result:'PASS',checks,files:[]}));
 }finally{t.stopPolling();for(const d of pages)d.window.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
