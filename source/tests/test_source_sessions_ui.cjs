// DOM/component test of the real HTML and JS against a running local test service.
// Canvas calls are stubbed: this test does not claim pixel rendering or native Windows QA.
const assert = require('node:assert/strict');
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
  // Load the script tags from the served page in browser order. Concatenating
  // files here concealed a blocking script running before its deferred imports.
  const dom=new JSDOM(html,{url:base,runScripts:'dangerously',resources:'usable',pretendToBeVisual:true,virtualConsole:vc,beforeParse(w){
  w.FormData=globalThis.FormData;
  w.AbortController=globalThis.AbortController;
  w.fetch=async(url,options)=>{try{return await fetch(new URL(url,base),options);}catch(e){errors.push('fetch '+url+': '+e.message);throw e;}};
  w.ResizeObserver=class{observe(){} disconnect(){}};
  w.HTMLCanvasElement.prototype.getBoundingClientRect=()=>({x:0,y:0,left:0,top:0,width:950,height:560,right:950,bottom:560});
  const ctx=new Proxy({measureText:text=>({width:String(text).length*7})},{get:(obj,key)=>key in obj?obj[key]:(()=>{})});
  w.HTMLCanvasElement.prototype.getContext=()=>ctx;
  w.addEventListener('error',e=>errors.push(e.message));
  }});
  pages.push(dom);const w=dom.window;
  await new Promise(resolve=>w.addEventListener('load',resolve,{once:true}));
  if(errors.length)w.close();
  assert.deepEqual(errors,[],'The real page must load without JavaScript errors');
  w.eval('window.uiTest={get trend(){return liveCharts.charts[0].trend},get selected(){return selected},get record(){return recordState},get connected(){return connected},get liveCharts(){return liveCharts},get historyCharts(){return historyCharts},selectHistoryFile,loadHistory,importHistoryLocalFile,openSignalProperties,openAxisProperties,duplicateStudioChart,setNavigatorRange,dropHistorySignal,saveWorkspace,chartSnapshot,config,stopPolling(){polling=false;clearTimeout(pollTimer);clearTimeout(workspaceTimer);}};');
  const $=id=>w.document.getElementById(id);
  try{await wait(()=>$('endpoint').value,'configuration loaded');}catch(e){console.error('UI notice:', $('msg').textContent);throw e;}
  await w.opcuaConfigReady;return {dom,w,$,t:w.uiTest};
}

(async()=>{
 const {w,$,t}=await page(),checks=[],cfg=JSON.parse(process.env.TEST_MULTI_CONFIG),signals=JSON.parse(process.env.TEST_MULTI_SIGNALS);
 try{
  for(const p of ['opcua','ads','mqtt','s7']){$('enable-'+p).checked=true;$('period-'+p).value=cfg.source_intervals[p];}
  $('mqtt-parallel').checked=true;$('mqtt-parallel').dispatchEvent(new w.Event('change'));
  $('endpoint').value=cfg.endpoint;$('security').value='None';$('sample_ms').value='20';
  $('ads_tcp_port').value=cfg.ads_tcp_port;$('s7_host').value=cfg.s7_host;$('s7_tcp_port').value=cfg.s7_tcp_port;
  $('mqtt_host').value='127.0.0.1';$('mqtt_port').value=cfg.mqtt_port;$('mqtt_topics').value='plant/#';
  w.s7Tags=cfg.s7_tags;
  $('root_nodeid').value=rootNode;
  $('data-source').value='ads';$('data-source').dispatchEvent(new w.Event('change'));
  await wait(()=>t.config().source==='ads','configure ADS as the initial catalog');
  $('connect').click();
  await wait(async()=>{const j=await api('/api/values');return Object.values(j.sources||{}).length===4&&Object.values(j.sources).every(s=>s.state==='connected');},'four independent UI sessions');
  const chooseCatalog=async p=>{
   $('browse-source').value=p;$('browse-source').dispatchEvent(new w.Event('change'));
   await wait(()=>t.config().source===p&&$('data-source').value===p,'select '+p+' through the actual Catalogo dropdown');
  };
  for(const p of ['ads','opcua','s7','mqtt']){
   await chooseCatalog(p);
   assert.equal($('root_nodeid').value,p==='opcua'?rootNode:p+':/');
   if(p==='opcua')assert($('root_nodeid').labels[0].textContent.includes('Nodo di partenza'));
   $('scan').click();
   await wait(()=>$('scan-body').querySelector('input[data-nodeid="'+signals[p]+'"]')&&!$('scan').disabled,'discover '+p+' signal with Scansiona tutto');
   const box=$('scan-body').querySelector('input[data-nodeid="'+signals[p]+'"]');
   box.checked=true;box.dispatchEvent(new w.Event('change'));
   await wait(()=>t.selected.has(signals[p]),'select discovered '+p+' signal without clearing previous selections');
  }
  await wait(async()=>{const j=await api('/api/values');return Object.values(signals).every(n=>j.samples[n]?.length>3);},'four displayed sources');
  const selection=[...t.selected.keys()];
  $('disconnect-ads').click();
  await wait(async()=>(await api('/api/values')).sources.ads.state==='disconnected','individual ADS disconnect');
  assert.deepEqual([...t.selected.keys()],selection);
  $('reconnect-ads').click();
  await wait(async()=>(await api('/api/values')).sources.ads.state==='connected','individual ADS reconnect');
  checks.push('Real HTML script loading, ADS initial catalog, actual Catalogo dropdown and scans discover/select all four protocols while retaining the common chart');
  const countsBeforeRecord=(await api('/api/values')).diagnostics.session_samples;
  $('record').click();
  await wait(()=>!$('record-modal').hidden,'recording dialog');
  $('record-form').querySelector('input[name="record-format"][value="csv"]').checked=true;
  $('record-start').click();
  await wait(async()=>t.record.busy&&(await api('/api/record/status')).recording.busy,'mixed recording started');
  const graph=t.liveCharts.active,trend=graph.trend,{p,w:width,h}=trend.layout;
  const point=(type,x,y)=>{
   const target=type==='up'&&!w.PointerEvent?w:trend.canvas;
   target.dispatchEvent(new (w.PointerEvent||w.MouseEvent)((w.PointerEvent?'pointer':'mouse')+type,{bubbles:true,clientX:x,clientY:y,button:0,pointerId:1}));
  };
  point('down',p.l+.15*(width-p.l-p.r),p.t+.15*(h-p.t-p.b));
  point('move',p.l+.65*(width-p.l-p.r),p.t+.75*(h-p.t-p.b));
  point('up',p.l+.65*(width-p.l-p.r),p.t+.75*(h-p.t-p.b));
  assert.equal(trend.frozen,true);
  await wait(()=>graph.analysisSnapshotId,'paused zoom retains original-sample analysis snapshot');
  const zoomSpan=trend.domain.xmax-trend.domain.xmin,zoomAxes=JSON.stringify(trend.domain.axes);
  $('global-live-zoom').click();
  assert.equal(trend.frozen,false);assert.equal(graph.analysisSnapshotId,null);
  const zoomEnd=trend.domain.xmax;
  await wait(()=>trend.domain.xmax>zoomEnd+80,'Zoom live advances with four real client stacks while CSV records');
  assert(Math.abs(trend.domain.xmax-trend.domain.xmin-zoomSpan)<.001);
  assert.equal(JSON.stringify(trend.domain.axes),zoomAxes);
  assert.equal(t.record.busy,true);
  checks.push('Zoom live resumes a paused XY zoom, releases its analysis snapshot, preserves X span/Y limits and continues four-source acquisition during CSV recording');
  await chooseCatalog('ads');
  await chooseCatalog('opcua');
  await wait(()=>$('tree').querySelector('input[data-nodeid="'+signals.opcua+'"]')||$('tree').textContent.includes('Dati annidati'),'OPC UA browse remains available while ADS and Record are active');
  assert.deepEqual([...t.selected.keys()],selection);
  const countsAfterBrowse=(await api('/api/values')).diagnostics.session_samples;
  for(const id of selection)assert(countsAfterBrowse[id]>=countsBeforeRecord[id],'source buffer retained: '+id);
  await wait(async()=>(await api('/api/record/status')).recording.cycles_received>10,'mixed samples recorded while browsing');
  $('record-stop').click();
  await wait(async()=>!(await api('/api/record/status')).recording.busy,'mixed recording finalized');
  const rec=(await api('/api/record/status')).recording;
  assert.equal(rec.state,'stopped');
  const replay=await w.fetch('/api/recordings/data',{method:'POST',headers:{'Content-Type':'application/json','X-OPCUA-Token':w.document.querySelector('meta[name="opcua-token"]').content},body:JSON.stringify({source:'repository',name:rec.files[0].name,max_points:200})});
  assert.deepEqual(Object.keys((await replay.json()).recording.series).sort(),Object.values(signals).sort());
  await wait(()=>!t.record.busy,'UI recording state finalized');
  checks.push('Browse OPC UA while ADS and CSV recording remain active; replay contains all four selected sources');
  await chooseCatalog('mqtt');
  await w.refreshMqttCatalog(true);
  await wait(()=>$('tree').querySelector('.expander'),'MQTT tree loaded after catalog switch');
  const exp=$('tree').querySelector('.expander');exp.click();
  const box=$('tree').querySelector('input[data-nodeid="'+signals.mqtt+'"]');box.focus();$('tree').scrollTop=15;
  $('mqtt-autoscan').click();const count=$('tree').querySelectorAll('input').length;
  await wait(()=>$('tree').querySelectorAll('input').length>count,'incremental MQTT discovery',7000);
  assert.equal($('tree').querySelector('input[data-nodeid="'+signals.mqtt+'"]'),box);
  assert.equal(w.document.activeElement,box);assert.equal($('tree').scrollTop,15);assert.equal(exp.getAttribute('aria-expanded'),'true');
  checks.push('MQTT Autoscan preserves expanded branches, focus, scroll and selection during four-source acquisition');
  for(const [language,label] of [['en','Reconnect source'],['de','Quelle neu verbinden'],['fr','Reconnecter la source'],['es','Reconectar fuente'],['it','Riconnetti sorgente']]){
   await w.I18N.setLanguage(language,{persist:true});assert.equal($('reconnect-ads').textContent,label);
  }
  checks.push('New controls translated in five languages');
  assert.deepEqual([...$('record-form').querySelectorAll('input[name="record-format"]')].map(el=>el.value).sort(),['csv','das','parquet','rbf']);
  assert([...$('record-form').querySelectorAll('input[name="record-format"]')].every(el=>!el.disabled));
  await t.saveWorkspace(true);
  const saved=await api('/api/config');assert.equal(saved.enabled_sources.length,4);
  checks.push('The four 3.0 recording formats (CSV, Parquet, FDA, RBF) remain available and multi-source configuration persists');
  $('disconnect').click();await wait(async()=>!Object.keys((await api('/api/values')).sources||{}).length,'all sessions closed');
  assert.equal(errors.length,0,errors.join('; '));console.log(JSON.stringify({result:'PASS',checks,files:[]}));
 }finally{t.stopPolling();for(const d of pages)d.window.close();}
})().then(()=>process.exit(0)).catch(e=>{console.error(e);process.exit(1);});
