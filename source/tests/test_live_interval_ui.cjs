// Exercise the actual HTML/script loading and production chart controls.
// Canvas rendering is stubbed; incoming data is deterministic in this workflow.
const assert = require('node:assert/strict');
const {JSDOM, VirtualConsole} = require(process.env.JSDOM_PATH || 'jsdom');
const [base] = process.argv.slice(2);
const errors=[],pages=[];
const wait=async(fn,label,timeout=12000)=>{const end=Date.now()+timeout;while(Date.now()<end){if(await fn()){console.error('PASS '+label);return;}await new Promise(r=>setTimeout(r,30));}throw Error('Timeout '+label+'; '+errors.join('; '));};
const api=async route=>(await fetch(base+route)).json();
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
  w.eval('window.uiTest={get trend(){return liveCharts.charts[0].trend},get selected(){return selected},get record(){return recordState},get connected(){return connected},get liveCharts(){return liveCharts},get historyCharts(){return historyCharts},showAppPage,saveWorkspace,chartSnapshot,config,feed(data,nodes,newTypes){series=data;selected=new Map(nodes);types=newTypes;liveCharts.update(series,selected,types);},stopPolling(){polling=false;clearTimeout(pollTimer);clearTimeout(workspaceTimer);}};');
  const $=id=>w.document.getElementById(id);
  try{await wait(()=>$('endpoint').value,'configuration loaded');}catch(e){console.error('UI notice:', $('msg').textContent);throw e;}
  await w.opcuaConfigReady;return {dom,w,$,t:w.uiTest};
}

(async()=>{
 const {w,$,t}=await page(),checks=[],cfg=JSON.parse(process.env.TEST_MULTI_CONFIG),signals=JSON.parse(process.env.TEST_MULTI_SIGNALS);
 try{
  for(const p of ['opcua','ads','mqtt','s7']){$('enable-'+p).checked=true;$('period-'+p).value=cfg.source_intervals[p];}
  $('mqtt-parallel').checked=true;$('mqtt-parallel').dispatchEvent(new w.Event('change'));
  for(const key of ['endpoint','ads_tcp_port','s7_host','s7_tcp_port','mqtt_port'])$(key).value=cfg[key];
  $('security').value='None';$('sample_ms').value='20';$('buffer_mode').value='time';$('window_seconds').value='10';
  $('mqtt_host').value='127.0.0.1';$('mqtt_topics').value='plant/#';w.s7Tags=cfg.s7_tags;
  $('connect').click();await wait(async()=>t.connected&&!$('browse-source').disabled&&Object.values((await api('/api/values')).sources||{}).filter(s=>s.state==='connected').length===4,'four sources connected');
  for(const p of ['opcua','ads']){
   $('browse-source').value=p;$('browse-source').dispatchEvent(new w.Event('change'));
   await wait(()=>t.config().source===p,'catalog '+p);
   $('manual-node').value=signals[p];$('add-node').click();await wait(()=>t.selected.has(signals[p]),'selected '+p);
  }
  const graph=t.liveCharts.active,trend=graph.trend;
  await wait(()=>trend.domain&&Object.values(trend.series).every(a=>a.length>5),'two protocols graphed');
  const v=await api('/api/values'),a=v.samples[signals.opcua];
  const x1=a[0][0],x2=a.at(-1)[0];assert(x2>x1);
  await wait(async()=>{const v=await api('/api/values?view=window');return v.samples[signals.opcua][0][0]>x2+100;},'old marker interval evicted from recent raw FIFO',16000);
  assert.equal($('live-time-view').value,'all');
  $('global-pause').click();await wait(()=>graph.analysisSnapshotId,'full-session original snapshot');
  trend.setMarker('x1',x1);trend.setMarker('x2',x2);graph.element.querySelector('.marker-analysis').open=true;
  await w.quickCalculationsTick();
  await wait(()=>trend.exactMarkerRows?.get(signals.opcua)?.x1,'old marker resolves from original history');
  assert(Number.isFinite(trend.exactMarkerRows.get(signals.opcua).x2.value));
  assert.equal(trend.markerError,'');assert.equal(t.selected.size,2);
  checks.push('Intera acquisizione: old markers work after raw FIFO eviction, using the actual paused chart and original samples');
  await w.quickCalculationsTick();$('quick-add-variable').click();
  const bind=(alias,id)=>{const el=$('quick-bindings').querySelector('[data-quick-binding="'+alias+'"]');el.value=id;el.dispatchEvent(new w.Event('change'));};
  bind('A',signals.opcua);bind('B',signals.ads);
  assert($('quick-bindings').textContent.includes('[ADS]'));assert($('quick-bindings').textContent.includes('[OPC UA]'));
  $('quick-name').value='Differenza intervallo';$('quick-unit').value='kN';$('quick-expression').value='([A]-[B])*2';$('quick-expression').dispatchEvent(new w.Event('input'));
  $('quick-operation').value='mean';$('quick-run').click();await wait(()=>$('quick-value').textContent!=='—'&&!$('quick-run').disabled,'multi-variable formula calculated');
  const draft=w.intervalFormulaDefinition(),expected=await w.eval('request')( '/api/analysis/quick',{source:'live',signal_ids:[signals.opcua,signals.ads],snapshot_id:graph.analysisSnapshotId,x1,x2,formula:draft});
  const mean=expected.results[0].statistics.mean;
  assert(Number.isFinite(mean));assert.equal($('quick-value').textContent,w.eval('valueText') (mean)+' kN');
  assert($('quick-range').textContent.includes('Intervallo marker'));assert.equal(t.selected.size,2);
  $('quick-operation').value='last';$('quick-operation').dispatchEvent(new w.Event('change'));
  assert.equal($('quick-value').textContent,w.eval('valueText')(expected.results[0].statistics.last)+' kN');
  checks.push('Two original OPC UA/ADS variables, custom formula before aggregation, selected metric/unit and marker interval produce the backend result without altering acquisition');
  $('quick-editor').click();await wait(()=>!$('calculations-page').hidden,'full editor opens');
  assert.equal($('calc-expression').value,'([A]-[B])*2');assert.equal($('calc-name').value,'Differenza intervallo');
  t.showAppPage('live');$('quick-save').click();
  await wait(()=>$('quick-status').textContent.includes('Formula salvata'),'interval formula saved and graphed');
  const saved=(await api('/api/calculations')).formulas.find(f=>f.name==='Differenza intervallo');
  assert(saved);assert.equal(saved.protocol,'any');assert(graph.ids.has(saved.nodeid));
  await wait(async()=>{const v=await api('/api/values');return v.samples[saved.nodeid]?.some(p=>p[2]==='Good'&&Number.isFinite(p[1]));},'mixed-protocol formula continues acquiring after save');
  $('quick-saved').value='';$('quick-saved').dispatchEvent(new w.Event('change'));assert.equal($('quick-expression').value,'[A]');
  $('quick-saved').value=saved.id;$('quick-saved').dispatchEvent(new w.Event('change'));assert.equal($('quick-expression').value,saved.expression);
  checks.push('Shared full editor preserves the draft; save registers a mixed-protocol calculated signal, adds it to the chart and reloads it from saved formulas');
  for(const [lang,label] of [['en','Add variable'],['de','Variable hinzufügen'],['fr','Ajouter une variable'],['es','Añadir variable'],['it','Aggiungi variabile']]){
   await w.I18N.setLanguage(lang,{persist:true});assert.equal($('quick-add-variable').textContent,label);
  }
  await t.saveWorkspace(true);const restored=await page();restored.t.stopPolling();await restored.w.quickCalculationsTick();
  assert.equal(restored.$('quick-expression').value,saved.expression);assert.equal(restored.$('quick-bindings').querySelectorAll('select').length,2);
  assert.equal(restored.$('quick-saved').value,saved.id);assert.equal(restored.$('quick-auto').checked,false);
  checks.push('Five languages, bindings, formula, metric, unit and explicit-calculation preference persist across a real page reload');
  assert.deepEqual(errors,[]);console.log(JSON.stringify({result:'PASS',checks,files:[]}));
 }finally{t.stopPolling();for(const d of pages)d.window.close();}
})().then(()=>process.exit(0)).catch(error=>{console.error(error);process.exit(1);});
