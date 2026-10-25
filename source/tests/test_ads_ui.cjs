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
 let {w,$,t}=await page();const checks=[];
 const manualFields=['ads_host','ads_tcp_port','ads_source_netid','ads_source_port','ads_bind_ip'];
 const savedManual={ads_host:'192.168.5.221',ads_tcp_port:48899,ads_source_netid:'192.168.92.36.1.1',ads_source_port:33011,ads_bind_ip:'192.168.92.36'};
 try{
  assert(w.document.title.startsWith('Space Analyzer'));assert(w.document.querySelector('.brand .company-logo').src.endsWith('/static/space-analyzer-logo.png'));assert(w.document.querySelector('link[rel="icon"]').href.endsWith('/static/space-analyzer.ico'));
  assert($('nav-connection').closest('.page-tabs'));assert(!$('nav-connection').closest('details'));
  $('nav-connection').click();assert($('connection-dialog').open);assert(!$('opcua-connection-fields').hidden);assert($('ads-connection-fields').hidden);
  $('endpoint').value=endpoint;$('security').value='None';$('sample_ms').value='10';$('root_nodeid').value=rootNode;
  $('connect').click();await wait(()=>t.connected,'OPC UA connected');
  for(const id of ids){$('manual-node').value=id;$('add-node').click();await wait(()=>t.selected.has(id)&&!$('add-node').disabled,'OPC signal selected');}
  const opcChart=t.liveCharts.active;opcChart.name='OPC saved chart';opcChart.signalSettings={[ids[0]]:{alias:'Temperature saved',color:'#f97316',unit:'C'}};
  $('live-add-chart').click();assert.equal(t.liveCharts.charts.length,2);
  // The initial branch browse keeps Disconnect disabled; a click before it ends is ignored.
  await wait(()=>!$('disconnect').disabled,'disconnect available');$('disconnect').click();await wait(()=>!t.connected&&!$('data-source').disabled,'disconnected');
  await t.switchSource('ads');assert.equal(t.config().source,'ads');assert.equal(t.selected.size,0);assert.equal(t.liveCharts.charts.length,1);
  assert([...$('sample_ms').options].find(o=>o.value==='1'&&!o.disabled&&!o.hidden));
  $('sample_ms').value='1';$('sample_ms').dispatchEvent(new w.Event('change'));
  assert.equal(t.config().sample_ms,1);assert(!$('ads-fast-sampling-note').hidden);
  assert(!$('ads-connection-fields').hidden);assert($('opcua-connection-fields').hidden);assert.equal($('root_nodeid').value,'ads:/');assert($('scan-body'));
  $('ads-enable-cyclic').click();await wait(()=>t.config().ads_mode==='notify','native notification profile');
  assert.equal(t.config().ads_transport,'native');assert(!$('ads-native-options').hidden);
  for(const id of manualFields){assert.equal($(id).disabled,false,id);assert.equal($(id).readOnly,false,id);}
  assert($('ads-transport-note').textContent.includes('vengono salvati'));
  assert.equal($('ads-connection-edit-note').hidden,true);
  assert([...w.document.querySelectorAll('[data-ads-manual-label]')].every(el=>!el.hidden));
  $('ads_transport').value='tcp';$('ads_transport').dispatchEvent(new w.Event('change'));assert.equal(t.config().ads_mode,'sum');assert($('msg').textContent.includes('SumRead'));
  assert([...w.document.querySelectorAll('[data-ads-manual-label]')].every(el=>el.hidden));
  $('ads_tcp_port').value=process.env.TEST_ADS_PORT;$('ads_host').value='127.0.0.1';$('ads_target_netid').value='127.0.0.1.1.1';
  $('ads_source_netid').value='192.0.2.99.1.1';$('ads_source_port').value='33511';$('ads_bind_ip').value='127.0.0.1';
  $('ads-test').click();await wait(()=>$('ads-info').textContent.includes('ADS test peer'),'ADS route test');
  assert($('ads-info').textContent.includes('Client 192.0.2.99.1.1 · porta 33512'));
  $('connect').click();await wait(()=>t.connected,'ADS connected');assert.equal($('data-source').disabled,true);
  await wait(()=>$('ads-info').textContent.includes('porta 33511'),'configured live client port');
  for(const id of manualFields)assert.equal($(id).disabled,true,id);
  assert.equal($('ads-connection-edit-note').hidden,false);
  const longPath='PlcSlow.fbEntryPoint.fbAdsProductionDataRepository._aDtoWsgProdFieldData[1]._stWsgProdFieldData.stSmgWsgDetRollerHolderWsgDataFD.eLeftDriveStatus';
  const fixture={nodeid:'ads:'+longPath,display_name:longPath,browse_name:longPath,node_class:'Variable',comment:'Stato drive'};
  const tree=w.treeNode(fixture,new Set());
  assert.equal(tree.querySelector('.name').textContent,'eLeftDriveStatus');
  assert.equal(tree.querySelector('.mono'),null);
  assert(tree.querySelector('.node').title.includes(longPath));
  assert.equal(tree.querySelector('input').dataset.nodeid,'ads:'+longPath);
  assert.equal(fixture.display_name,longPath);
  t.selected.set(fixture.nodeid,fixture);w.renderSelected();
  const selectedRow=[...$('selected').children].find(row=>row.dataset.id===fixture.nodeid);
  assert.equal(selectedRow.querySelector('.signal-name').textContent,'eLeftDriveStatus');
  assert(selectedRow.title.includes(longPath));assert.equal(selectedRow.querySelector('.signal-name').title,fixture.nodeid);
  assert.equal(selectedRow.dataset.id,fixture.nodeid);
  t.liveCharts.active.signalSettings={[fixture.nodeid]:{alias:'Drive alias'}};w.renderSelected();
  assert.equal([...$('selected').children].find(row=>row.dataset.id===fixture.nodeid).querySelector('.signal-name').textContent,'Drive alias');
  t.selected.delete(fixture.nodeid);delete t.liveCharts.active.signalSettings[fixture.nodeid];w.renderSelected();
  checks.push('Space Analyzer identity/logo/favicon; selected ADS signals show leaf names, full-path tooltips and preserved custom aliases');
  const folder=w.treeNode({nodeid:'ads:PlcSlow.fbEntryPoint._aData[1]',display_name:'PlcSlow.fbEntryPoint._aData[1]',node_class:'Object'},new Set());
  assert.equal(folder.querySelector('.name').textContent,'_aData[1]');
  assert.equal(w.treeLabel({nodeid:'ads:Other.eLeftDriveStatus'}),'eLeftDriveStatus');
  assert.equal(w.treeLabel({nodeid:'ns=2;s=Temperature',display_name:'Temp. ingresso'}),'Temp. ingresso');
  checks.push('short ADS branch/variable labels, array indices, full-path tooltip, canonical selection IDs and unchanged OPC UA labels');
  $('scan').click();await wait(()=>$('scan-body').textContent.includes('MAIN.Wave'),'ADS symbols scanned');
  for(const id of ['MAIN.Wave','MAIN.Running']){$('manual-node').value=id;$('add-node').click();await wait(()=>t.selected.has('ads:'+id)&&!$('add-node').disabled,'ADS symbol selected');}
  await wait(()=>t.trend.series['ads:MAIN.Wave']?.length>20,'ADS graph receives data');
  assert($('toolbar-endpoint').textContent.includes('AMS'));assert($('ads-info').textContent.includes('RUN'));
  assert.equal(t.liveCharts.charts.length,1);t.liveCharts.active.name='ADS saved chart';
  for(const format of ['csv','parquet']){
   $('record').click();await wait(()=>!$('record-modal').hidden,'ADS format popup');
   w.document.querySelector('input[name="record-format"][value="'+format+'"]').checked=true;
   $('record-form').dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));
   await wait(()=>t.record.active,'ADS '+format+' recording');
   await wait(()=>t.record.cycles_received>15,'ADS '+format+' samples');
   $('record-stop').click();await wait(()=>t.record.state==='stopped','ADS '+format+' finalized');
   assert.equal(t.record.error,'');
  }
  checks.push('visible connection shortcut; OPC UA/ADS fields, route test, symbol scan, live trends, CSV and Parquet Record/Stop');
  $('disconnect').click();await wait(()=>!t.connected&&!$('data-source').disabled,'ADS disconnected');
  $('ads-enable-cyclic').click();await wait(()=>t.config().ads_mode==='notify','cyclic shortcut');assert.equal(t.config().ads_transport,'native');
  for(const [id,value] of Object.entries(savedManual)){$(id).value=value;$(id).dispatchEvent(new w.Event('change'));assert.equal($(id).disabled,false,id);}
  $('connection-save').click();await wait(async()=>{const c=await api('/api/config');return c.saved_settings_present&&manualFields.every(id=>c[id]===savedManual[id]);},'edited native parameters explicitly saved');
  await wait(()=>!$('connection-save').disabled,'native settings save completed');
  w.renderAdsCyclicDiagnostics({ads_notifications:{mode:'ADS notifiche cicliche',source_period_ms:10,arrival_period_ms:40,queue_samples:0,queue_capacity:32768,dropped:0,reference_signal:'ads:MAIN.Wave'}});assert(!$('ads-cyclic-diag').hidden);assert($('ads-cyclic-diag').textContent.includes('40.000 ms'));
  await t.saveWorkspace(true);
  t.stopPolling();await new Promise(r=>setTimeout(r,600));const reloaded=await page();pages[0].window.close();w=reloaded.w;$=reloaded.$;t=reloaded.t;
  assert.equal(t.config().ads_mode,'notify');assert.equal(t.config().ads_transport,'native');
  assert.equal(t.config().sample_ms,1);
  for(const id of manualFields){assert.equal(t.config()[id],savedManual[id],id);assert.equal($(id).disabled,false,id);}
  assert.equal(t.config().source,'ads');assert(t.selected.has('ads:MAIN.Wave'));assert.equal(t.liveCharts.active.name,'ADS saved chart');
  await t.switchSource('opcua');assert.equal(t.liveCharts.charts.length,2);assert(t.selected.has(ids[0]));assert.equal(t.liveCharts.charts[0].signalSettings[ids[0]].alias,'Temperature saved');assert.equal($('root_nodeid').value,rootNode);
  assert.equal(t.config().sample_ms,10);assert([...$('sample_ms').options].find(o=>o.value==='1').disabled);
  await t.switchSource('ads');assert.equal(t.liveCharts.charts.length,1);assert(t.selected.has('ads:MAIN.Wave'));assert.equal(t.liveCharts.active.name,'ADS saved chart');
  assert.equal(t.config().sample_ms,1);
  checks.push('ADS 1 ms selectable, explicit save and reload preserved, protocol switching restores per-source period');
  checks.push('cyclic mode shortcut, source/arrival diagnostics, persisted native notification settings');
  checks.push('workspace reload and separate signal/layout/properties preservation across source switches');
  checks.push('native manual fields editable and saved/reloaded; explicit TCP SumRead selection and actual manual client identity/port/bind applied; connected fields remain locked');
  assert.deepEqual(errors,[]);console.log(JSON.stringify({result:'PASS',checks,files:[]},null,2));
 }finally{t.stopPolling();await new Promise(r=>setTimeout(r,600));for(const dom of pages)dom.window.close();}
})().catch(e=>{console.error(e.stack);process.exit(1);});
