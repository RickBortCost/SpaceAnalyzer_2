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
 let {w,$,t}=await page();const checks=[];const a='mqtt:plant/motor#/pressure',b='mqtt:plant/motor#/load';
 try{
  $('nav-connection').click();assert($('connection-dialog').open);
  await t.switchSource('mqtt');assert(!$('mqtt-connection-fields').hidden);assert($('opcua-connection-fields').hidden);
  assert.equal($('root_nodeid').value,'mqtt:/');assert.equal($('nav-live').textContent,'LIVE');
  $('mqtt_tls').checked=true;$('mqtt_tls').dispatchEvent(new w.Event('change'));assert.equal($('mqtt_port').value,'8883');assert(!$('mqtt-tls-fields').hidden);
  $('mqtt_tls').checked=false;$('mqtt_tls').dispatchEvent(new w.Event('change'));assert.equal($('mqtt_port').value,'1883');
  $('mqtt_host').value='127.0.0.1';$('mqtt_port').value=process.env.TEST_MQTT_PORT;
  $('mqtt_version').value='5';$('mqtt_qos').value='2';$('mqtt_topics').value='plant/#';
  $('mqtt_username').value='operator';$('mqtt_password').value=' test-session ';
  $('mqtt_timestamp_pointer').value='/ts';$('mqtt_timestamp_unit').value='ns';$('mqtt_time_basis').value='payload';
  $('sample_ms').value='500';
  $('mqtt-test').click();await wait(()=>$('mqtt-info').textContent.includes('MQTT 5 TCP'),'CONNACK/SUBACK test');
  assert($('mqtt-info').textContent.includes('sola lettura'));
  $('connect').click();await wait(()=>t.connected,'MQTT connected');
  assert($('mqtt_host').disabled);assert($('mqtt_topics').disabled);assert($('mqtt-test').disabled);
  for(const id of [a,b]){$('manual-node').value=id;$('add-node').click();await wait(()=>t.selected.has(id)&&!$('add-node').disabled,'JSON field selected');}
  await wait(()=>t.trend.series[a]?.length>=30,'message-driven trend exceeds configured polling cadence');
  $('scan').click();await wait(()=>$('scan-body').textContent.includes('mqtt:plant/motor#/counter'),'catalogue includes observed JSON fields');
  await wait(()=>$('mqtt-diag').textContent.includes('messaggi'),'MQTT diagnostics');
  assert($('acqdiag').textContent.includes('non limita i campioni'));
  checks.push('MQTT 5 source, independent profile, TLS controls/default ports, authentication, real broker/subscription test, manual JSON selection, observed catalogue and event-driven live chart');
  t.liveCharts.active.name='MQTT saved chart';
  for(const fmt of ['parquet','rbf']){
   $('record').click();await wait(()=>!$('record-modal').hidden,'record popup');
   const radio=$('record-form').querySelector('input[name="record-format"][value="'+fmt+'"]');radio.checked=true;radio.dispatchEvent(new w.Event('change'));
   $('record-form').dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));
   await wait(()=>t.record.active,'MQTT '+fmt+' recording');await wait(()=>t.record.cycles_received>=30,'all received messages recorded');
   const before=t.record.cycles_received;t.trend.togglePause();await wait(()=>t.record.cycles_received>before+10,'recording continues with chart paused');t.trend.togglePause();
   $('record-stop').click();await wait(()=>t.record.state==='stopped','MQTT '+fmt+' finalized',20000);
   assert.equal(t.record.error,'');if(fmt==='rbf'){assert.equal(t.record.rbf_phase,'ready');assert(t.record.original_files.length);}
   const file=(await api('/api/recordings')).files.find(f=>f.name===t.record.files[0].name);assert(file);
   t.selectHistoryFile(file);await t.loadHistory();assert.equal(t.historyCharts.active.ids.size,2);assert(t.historyCharts.active.trend.domain);
  }
  checks.push('Parquet/RBF Record and Stop, chart pause independent of acquisition, original archive, reopen in historical charts');
  await t.saveWorkspace(true);let saved=await api('/api/config');assert.equal(saved.workspace.config.source,'mqtt');assert.equal(saved.workspace.config.mqtt_password,'');assert.equal(saved.workspace.config.mqtt_timestamp_unit,'ns');
  $('disconnect').click();await wait(()=>!t.connected&&!$('data-source').disabled,'MQTT disconnected');
  await t.switchSource('opcua');assert.equal(t.selected.size,0);assert($('mqtt-connection-fields').hidden);
  await t.switchSource('mqtt');assert.equal(t.selected.size,2);assert.equal(t.liveCharts.active.name,'MQTT saved chart');
  await t.saveWorkspace(true);t.stopPolling();w.close();
  ({w,$,t}=await page());assert.equal(t.config().source,'mqtt');assert.equal($('mqtt_password').value,'');assert(t.selected.has(a));assert.equal(t.liveCharts.active.name,'MQTT saved chart');
  checks.push('per-source chart and selection profiles, configuration/workspace persistence, session-only password blank after reload');
  assert.deepEqual(errors,[]);console.log(JSON.stringify({result:'PASS',checks,files:[]},null,2));
 }finally{for(const dom of pages){dom.window.uiTest?.stopPolling();dom.window.close();}}
})().catch(e=>{console.error(e.stack);process.exitCode=1;});
