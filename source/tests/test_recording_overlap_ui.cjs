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
  $('security').value='None';$('sample_ms').value='20';$('mqtt_host').value='127.0.0.1';$('mqtt_topics').value='plant/#';w.s7Tags=cfg.s7_tags;
  $('connect').click();await wait(async()=>t.connected&&!$('browse-source').disabled&&Object.values((await api('/api/values')).sources||{}).filter(s=>s.state==='connected').length===4,'four sources connected');
  const select=async p=>{
   $('browse-source').value=p;$('browse-source').dispatchEvent(new w.Event('change'));
   await wait(()=>t.config().source===p,'catalog '+p);$('manual-node').value=signals[p];$('add-node').click();await wait(()=>t.selected.has(signals[p])&&!$('add-node').disabled,'selected '+p);
  };
  await select('opcua');await select('ads');
  const start=async(fmt,name)=>{
   $('record').click();await wait(()=>!$('record-modal').hidden,'Record dialog available');
   $('record-session').value=name;$('record-form').querySelector('input[name="record-format"][value="'+fmt+'"]').checked=true;$('record-start').click();
   await wait(()=>t.record.active&&t.record.session.session_name===name,'recording '+name);
  };
  await start('das','Slow previous');await wait(()=>t.record.cycles_received>10,'FDA original samples accepted');
  const first=t.record.recording_id;assert(t.record.selection_locked);$('record-stop').click();
  await wait(()=>t.record.finalizing_count===1&&!t.record.selection_locked&&!$('record').disabled,'Record re-enabled while previous disk writer remains blocked');
  assert(t.record.busy);assert.equal($('recording-jobs').hidden,false);
  assert($('recording-jobs-list').textContent.includes('Slow previous'));
  checks.push('Stop detaches the FDA save and re-enables Record even while the original disk writer is deliberately blocked');
  $('selected').querySelector('[data-id="'+signals.ads+'"] .remove').click();await wait(()=>!t.selected.has(signals.ads),'selection editable during save');await select('mqtt');
  await start('parquet','Second recording');const second=t.record.recording_id;assert.notEqual(first,second);
  await wait(()=>t.record.cycles_received>10&&t.record.finalizing_count===1,'next session acquires independently while FDA save is blocked');
  assert(t.record.selection_locked);assert($('record-stop').disabled===false);
  let stale=false;try{await w.eval('request')('/api/record/stop',{recording_id:first});}catch(e){stale=e.message.includes('Registrazione cambiata');}assert(stale);
  assert.equal((await api('/api/record/status')).recording.recording_id,second);assert((await api('/api/record/status')).recording.active);
  checks.push('New session has a distinct identity and selection; a delayed Stop for the old identity cannot stop or truncate the new recording');
  await w.eval('request')('/test/finalizers/release',{});
  await wait(()=>t.record.finalizations.some(j=>j.recording_id===first&&!j.busy&&!j.error),'FDA finalized while next session stays active');
  assert(t.record.active);assert($('recording-jobs-list').textContent.includes('Pronto'));
  $('record-stop').click();await wait(()=>!t.record.busy,'both saves complete');
  const jobs=t.record.finalizations;assert.equal(jobs.length,2);
  for(const [id,wanted] of [[first,[signals.opcua,signals.ads]],[second,[signals.opcua,signals.mqtt]]]){
   const job=jobs.find(j=>j.recording_id===id);assert.equal(job.state,'stopped');assert(job.rows_written>10);
   const replay=await w.eval('request')('/api/recordings/data',{source:'repository',name:job.files[0].name,max_points:1000});
   assert.deepEqual(Object.keys(replay.recording.series).sort(),wanted.sort());
  }
  checks.push('FDA and Parquet files replay their own exact signal sets and remain individually accessible after both saves');
  for(const [language,label] of [['en','Background saves'],['de','Speichern im Hintergrund'],['fr','Enregistrements en arrière-plan'],['es','Guardados en segundo plano'],['it','Salvataggi in background']]){
   await w.I18N.setLanguage(language,{persist:true});assert.equal($('recording-jobs').querySelector('summary span').textContent,label);
  }
  assert.deepEqual(errors,[]);console.log(JSON.stringify({result:'PASS',checks,files:[]}));
 }finally{await w.eval('request')('/test/finalizers/release',{}).catch(()=>{});t.stopPolling();for(const d of pages)d.window.close();}
})().then(()=>process.exit(0)).catch(error=>{console.error(error);process.exit(1);});
