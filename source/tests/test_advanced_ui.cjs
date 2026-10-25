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
  w.eval(['i18n-catalog.js','i18n.js','trend.js','workspace.js','app.js','history.js','advanced.js','fft-ui.js','source.js','s7.js','calculations.js','studio.js','ssh.js','live-extras.js'].map(file=>fs.readFileSync(path.join(__dirname,'../static',file),'utf8')).join('\n')+'\nwindow.uiTest={get trend(){return liveCharts.charts[0].trend},get selected(){return selected},get record(){return recordState},get connected(){return connected},get liveCharts(){return liveCharts},get historyCharts(){return historyCharts},selectHistoryFile,loadHistory,importHistoryLocalFile};');
  const $=id=>w.document.getElementById(id);
  try{await wait(()=>$('endpoint').value,'configuration loaded');}catch(e){console.error('UI notice:', $('msg').textContent);throw e;}
  await w.opcuaConfigReady;return {dom,w,$,t:w.uiTest};
}

const role=(g,r)=>g.element.querySelector('[data-chart-role="'+r+'"]');
function pointer(w,g,type,x,y){const target=type==='up'&&!w.PointerEvent?w:g.trend.canvas;target.dispatchEvent(new (w.PointerEvent||w.MouseEvent)((w.PointerEvent?'pointer':'mouse')+type,{bubbles:true,clientX:x,clientY:y,button:0,pointerId:1}));}
(async()=>{
 const {w,$,t}=await page(),checks=[];
 try{
  $('nav-connection').click();$('endpoint').value=endpoint;$('security').value='None';$('sample_ms').value='10';$('root_nodeid').value=rootNode;
  $('window_seconds').value='3600';$('max_samples').value='10000';$('buffer_mode').value='samples';
  $('connect').click();await wait(()=>t.connected,'connection');
  for(const id of ids){$('manual-node').value=id;$('add-node').click();await wait(()=>t.selected.has(id)&&!$('add-node').disabled,'selected');}
  await wait(()=>t.liveCharts.charts[0].trend.domain,'live acquisition');
  assert.match($('buffer-diag').textContent,/10.000|10000/);checks.push('sample buffer configuration reaches acquisition');
  const live=t.liveCharts,g1=live.charts[0];$('live-add-chart').click();const g2=live.active;assert.equal(g2.ids.size,0);
  const drop=new w.Event('drop',{bubbles:true,cancelable:true});Object.defineProperty(drop,'dataTransfer',{value:{getData:()=>ids[0],types:['application/x-opcua-signal']}});g2.element.dispatchEvent(drop);assert.equal(g2.ids.size,1);assert.equal(g1.ids.size,2);
  await wait(()=>g1.trend.series[ids[0]].length>25,'buffer has data');
  pointer(w,g1,'down',250,120);pointer(w,g1,'move',700,450);pointer(w,g1,'up',700,450);
  assert.ok(g1.trend.zoom);assert.equal(g1.trend.domain.xmin,g2.trend.domain.xmin);assert.equal(g1.trend.domain.xmax,g2.trend.domain.xmax);assert.ok(!g2.trend.domain.axes.right);assert.ok(g2.trend.frozen);
  pointer(w,g1,'move',400,200);assert.equal(g1.trend.crosshair,g2.trend.crosshair);assert.notEqual(g1.trend.crosshair,null);assert.equal(g1.trend.crosshairEl.hidden,false);assert.equal(g1.trend.crosshairEl.style.left,g2.trend.crosshairEl.style.left);
  role(g1,'timeScroll').value='800';role(g1,'timeScroll').dispatchEvent(new w.Event('input',{bubbles:true}));assert.equal(g1.trend.domain.xmin,g2.trend.domain.xmin);
  live.setMarkers({x1:g1.trend.domain.xmin,x2:g1.trend.domain.xmax});
  await wait(()=>g1.analysisSnapshotId&&g2.analysisSnapshotId,'original live snapshots on synchronized zoom');
  role(g1,'analysis').click();const frozenRequest=await w.preparedAnalysisRequest();assert.equal(frozenRequest.snapshot_id,g1.analysisSnapshotId);assert.equal(frozenRequest.source,'live');assert.equal($('fft-mode').value,'interval');assert($('fft-mode').querySelector('[value=live]').disabled);$('analysis-close').click();
  checks.push('paused/zoomed live FFT uses retained original backend samples, including synchronized charts');
  g1.element.querySelector('.chartwrap').style.height='410px';$('selected').style.height='270px';role(g2,'title').value='Solo temperatura';role(g2,'title').dispatchEvent(new w.Event('change',{bubbles:true}));
  assert.equal(w.document.querySelector('input[name="record-format"]:checked').value,'parquet');
  const csvChoice=w.document.querySelector('input[name="record-format"][value="csv"]'),binaryChoice=w.document.querySelector('input[name="record-format"][value="parquet"]');
  csvChoice.checked=true;csvChoice.dispatchEvent(new w.Event('change'));assert($('record-binary-settings').hidden);assert($('record-parquet-profile').disabled);
  binaryChoice.checked=true;binaryChoice.dispatchEvent(new w.Event('change'));assert(!$('record-binary-settings').hidden);assert(!$('record-parquet-profile').disabled);
  $('record-parquet-profile').value='fast';$('record-parquet-profile').dispatchEvent(new w.Event('change'));assert.match($('record-storage-hint').textContent,/livello 3/);
  const firstRange={...g1.trend.domain},firstSeries=JSON.stringify(g1.trend.series);
  await w.saveWorkspace(true);
  const restored=await page();assert.equal(restored.t.liveCharts.charts.length,2);const rg=restored.t.liveCharts.charts[0];assert.ok(rg.trend.frozen);assert.equal(rg.trend.domain.xmin,firstRange.xmin);assert.deepEqual(JSON.parse(JSON.stringify(rg.trend.series)),JSON.parse(firstSeries));assert.equal(rg.element.querySelector('.chartwrap').style.height,'410px');assert.equal(restored.$('selected').style.height,'270px');assert.equal(restored.t.liveCharts.charts[1].name,'Solo temperatura');assert.equal(restored.$('record-parquet-profile').value,'fast');assert.equal(restored.w.document.querySelector('input[name="record-format"]:checked').value,'parquet');checks.push('DND, synchronized X zoom/pan/crosshair, persisted paused view and dimensions; binary format/profile workspace restore');
  // A reset propagates; independent mode remains available.
  role(g2,'resetZoom').click();assert.equal(g1.trend.linkedRange,null);assert.equal(g1.trend.frozen,false);
  $('live-sync-zoom').checked=false;$('live-sync-zoom').dispatchEvent(new w.Event('change',{bubbles:true}));pointer(w,g1,'down',250,120);pointer(w,g1,'move',650,420);pointer(w,g1,'up',650,420);assert.equal(g2.trend.zoom,null);assert.equal(g2.trend.linkedRange,null);checks.push('time synchronization toggle');
  // Record dialog options, duration rotation and descriptive metadata through real HTTP API.
  $('record-session').value='Commissioning';$('record-machine').value='M1';$('record-operator').value='Test';$('record-description').value='Transient test';$('record-duration').add(new w.Option('1 s','1'));$('record-duration').value='1';$('record-max-mb').value='1';
  $('record-parquet-profile').value='maximum';$('record-parquet-profile').dispatchEvent(new w.Event('change'));assert.match($('record-storage-hint').textContent,/livello 19/);
  const customDir=fs.realpathSync.native(fs.mkdtempSync(path.join(require('node:os').tmpdir(),'sa-rec-')));  // long path (no 8.3 short names on Windows)
  $('record').click();await wait(()=>!$('record-modal').hidden,'record dialog');assert($('record-directory').value.length>0,'current folder shown');$('record-directory').value=customDir;$('record-form').dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));await wait(()=>t.record.active,'record');await wait(()=>w.document.body.classList.contains('is-recording')&&/^● REC \d\d:\d\d$/.test($('record').textContent),'record button shows REC and elapsed time');assert.match(w.document.title,/^● REC \d\d:\d\d · /);assert($('record-status').querySelector('.rec-live [data-rec=clock]'),'compact recording bar');assert.match($('record-status').title,/Registrazione in corso/);assert.equal(t.record.storage.codec,'zstd');assert.equal(t.record.storage.level,19);await wait(()=>t.record.part>=2,'automatic time rotation',8000);$('record-stop').click();await wait(()=>t.record.state==='stopped','stop rotated record');assert(!w.document.body.classList.contains('is-recording'));assert.equal($('record').textContent,'● Record');assert(!w.document.title.startsWith('● REC'));assert.equal(t.record.dropped_cycles,0);assert.equal(t.record.pending_rows,0);assert.equal(t.record.session.machine,'M1');const binaryRecording=t.record.filename;checks.push('ZSTD 19 Record/Stop, metadata and automatic rotation without drops');
  // Chosen folder: Record writes there, the Recordings page follows it, a relative path is refused, Predefinita restores the default.
  assert(fs.readdirSync(customDir).some(f=>f.endsWith('.parquet')),'recording written in the chosen folder');
  let listing=await api('/api/recordings');assert.equal(listing.directory,customDir);assert(listing.files.some(f=>f.name.endsWith('.parquet')));
  await assert.rejects(w.eval('setRecordingsLocation')('relativo'),/percorso completo/);assert.equal((await api('/api/recordings')).directory,customDir);
  $('record-directory-default').click();await wait(async()=>(await api('/api/recordings')).directory===listing.default_directory,'default folder restored');
  await wait(()=>$('record-directory').value===listing.default_directory&&$('history-location').textContent===listing.default_directory,'folder fields updated');
  // The history steps below use this recording from the default folder.
  for(const f of fs.readdirSync(customDir,{withFileTypes:true}))if(f.isFile())fs.copyFileSync(path.join(customDir,f.name),path.join(listing.default_directory,f.name));
  fs.rmSync(customDir,{recursive:true,force:true});await w.eval('refreshHistoryFiles')();checks.push('recordings folder chosen in the Record dialog, validated and restored');
  $('nav-recordings').click();await wait(()=>$('history-file-list').textContent.includes('fixture_A.csv'),'history files');
  const choose=name=>[...$('history-file-list').querySelectorAll('button')].find(e=>e.textContent.includes(name)).click();
  choose(binaryRecording);assert.match($('history-selected-meta').textContent,/ZSTD/);$('history-load').click();await wait(()=>t.historyCharts.active.context.loaded,'new binary recording loaded');assert.equal($('history-format').textContent,'PARQUET ZSTD');checks.push('binary recording in the existing history chart tools');
  choose('fixture_A.csv');$('history-load').click();await wait(()=>t.historyCharts.active.context.loaded,'CSV loaded');const hist=t.historyCharts,h1=hist.active;
  $('history-add-chart').click();const h2=hist.active;assert.equal(h2.ids.size,0);choose('fixture_B.parquet');$('history-load').click();await wait(()=>h2.context.loaded,'Parquet loaded');
  hist.setMarkers({x1:1700000005000,x2:1700000015000});await wait(()=>h1.trend.exactMarkerRows?.size&&h2.trend.exactMarkerRows?.size,'exact markers');
  pointer(w,h1,'down',250,120);pointer(w,h1,'move',700,450);pointer(w,h1,'up',700,450);assert.equal(h1.trend.domain.xmin,h2.trend.domain.xmin);
  role(h1,'analysis').click();assert.equal($('analysis-dialog').open,true);$('fft-signal').value=ids[0];$('fft-run').click();await wait(()=>!$('fft-result').hidden,'FFT from original CSV');assert.match($('fft-summary').textContent,/campioni/);
  assert.equal($('fft-settings').open,false);assert.equal($('fft-spectrum-title').textContent,'Spettro in frequenza');assert($('fft-spectrum-panel').contains($('fft-canvas')));assert($('fft-spectrum-signal').textContent.includes('Hz'));
  $('fft-render-mode').value='sticks';$('fft-render-mode').dispatchEvent(new w.Event('change'));
  $('fft-spectrum-expand').click();assert($('analysis-dialog').classList.contains('spectrum-expanded'));assert.equal($('fft-spectrum-expand').getAttribute('aria-pressed'),'true');$('fft-spectrum-expand').click();assert(!$('analysis-dialog').classList.contains('spectrum-expanded'));
  $('fft-output').value='psd';$('fft-output').dispatchEvent(new w.Event('change'));$('fft-resolution-value').value='256';$('fft-run').click();await wait(()=>!$('fft-run').disabled&&$('analysis-status').textContent==='FFT completata.','PSD block calculation');assert($('fft-band-result').textContent.includes('RMS banda'));assert($('fft-summary').textContent.includes('256 campioni'));
  $('fft-fundamental-mode').value='manual';$('fft-fundamental-mode').dispatchEvent(new w.Event('change'));assert(!$('fft-fundamental-hz').disabled);$('fft-fundamental-hz').value='2';$('fft-run').click();await wait(()=>!$('fft-run').disabled&&$('analysis-status').textContent==='FFT completata.','manual harmonic calculation from recording');
  assert.equal($('fft-harmonic-body').children.length,5);assert.equal($('fft-harmonic-body').lastElementChild.children[1].textContent,'10');assert($('fft-harmonic-summary').textContent.includes('F1 impostata manualmente'));
  $('fft-harmonic-focus').click();assert.equal(Number($('fft-max-hz').value),11);assert.equal(Number($('fft-min-hz').value),0);
  let pngDownload=null;w.HTMLCanvasElement.prototype.toDataURL=()=> 'data:image/png;base64,test';const pngClick=w.HTMLAnchorElement.prototype.click;w.HTMLAnchorElement.prototype.click=function(){pngDownload={filename:this.download,url:this.href};};$('fft-spectrum-png').click();w.HTMLAnchorElement.prototype.click=pngClick;assert.equal(pngDownload.filename,'Space_Analyzer_FFT_spectrum.png');assert(pngDownload.url.startsWith('data:image/png'));
  checks.push('prominent frequency spectrum, curve/sticks, expanded view, F1-H5 focus, PNG export and spectrum preferences');
  $('fft-show-harmonics').checked=false;$('fft-show-harmonics').dispatchEvent(new w.Event('change'));$('fft-show-harmonics').checked=true;$('fft-show-harmonics').dispatchEvent(new w.Event('change'));
  $('fft-show-current').checked=true;$('fft-show-current').dispatchEvent(new w.Event('change'));$('fft-show-max').checked=true;$('fft-show-max').dispatchEvent(new w.Event('change'));$('fft-log').checked=true;$('fft-log').dispatchEvent(new w.Event('change'));$('fft-x-log').checked=true;$('fft-x-log').dispatchEvent(new w.Event('change'));
  $('fft-min-hz').value='1';$('fft-max-hz').value='10';$('fft-max-hz').dispatchEvent(new w.Event('input'));$('fft-view-reset').click();assert(Number($('fft-max-hz').value)>10);
  let exportedBlob=null;w.URL.createObjectURL=b=>{exportedBlob=b;return 'blob:fft-test';};w.URL.revokeObjectURL=()=>{};const oldClick=w.HTMLAnchorElement.prototype.click;w.HTMLAnchorElement.prototype.click=function(){};$('fft-download').click();assert(exportedBlob&&exportedBlob.size>100);exportedBlob=null;$('fft-harmonic-download').click();w.HTMLAnchorElement.prototype.click=oldClick;assert(exportedBlob&&exportedBlob.size>100);
  await w.saveWorkspace(true);const fftSettings=(await api('/api/config')).workspace.fft_options;assert.equal(fftSettings['fft-render-mode'],'sticks');assert.equal(fftSettings['fft-fundamental-mode'],'manual');assert.equal(fftSettings['fft-fundamental-hz'],'2');assert.equal(fftSettings['fft-output'],'psd');assert.equal(fftSettings['fft-resolution-value'],'256');checks.push('F1–H5 table from recorded data, manual fundamental, harmonic overlays/export/workspace; PSD, configurable block size, RMS band, overlays, log axes, autoscale, spectral CSV and FFT workspace options');
  for(const fmt of ['csv','parquet']){
   $('export-format').value=fmt;$('export-download').hidden=true;$('export-run').click();await wait(()=>!$('export-download').hidden,'interval export '+fmt);const response=await fetch($('export-download').href);assert.equal(response.status,200);const bytes=new Uint8Array(await response.arrayBuffer());
   if(fmt==='csv'){const text=new TextDecoder().decode(bytes);assert.ok(text.includes('source_timestamp'));assert.equal(text.trim().split('\n').length,2003);}else assert.equal(Buffer.from(bytes.slice(0,4)).toString(),'PAR1');
  }
  $('analysis-close').click();checks.push('history synchronized zoom, FFT and exact CSV/Parquet interval export');
  // Import containing all three quality classes remains restorable after reload.
  const data='timestamp_ms,node_id,signal_name,value_numeric,good,quality,status\n1700000000000,ns=2;s=Q,Quality,1,True,Good,Good\n1700000000010,ns=2;s=Q,Quality,,False,Uncertain,Uncertain\n1700000000020,ns=2;s=Q,Quality,,False,Bad,BadOutOfService\n';
  hist.activate(h2);await w.importHistoryLocalFile(new globalThis.File([data],'Quality.csv',{type:'text/csv'}));assert.equal(h2.context.file.source,'local');assert.equal(h2.trend.series['ns=2;s=Q'].at(1)[1],null);assert.match(role(h2,'qualitySummary').textContent,/Good 1 · Uncertain 1 · Bad 1/);
  await w.saveWorkspace(true);const third=await page();await wait(()=>third.t.historyCharts.charts[1].context.loaded,'restore local original and history');assert.equal(third.t.historyCharts.charts.length,2);assert.equal(third.t.historyCharts.charts[1].context.file.source,'local');assert.equal(third.t.historyCharts.charts[0].trend.markers.x1,1700000005000);checks.push('quality gaps and complete history workspace restore');
  w.logFrontend('RegressionFrontend password=should_not_be_logged');await wait(async()=>((await api('/api/logs')).tail||'').includes('RegressionFrontend'),'frontend log');const logs=(await api('/api/logs')).tail;assert.ok(!logs.includes('should_not_be_logged'));assert.ok(logs.includes('Record part finalized'));$('open-logs').click();await wait(()=>$('logs-content').textContent.includes('RegressionFrontend'),'log viewer');checks.push('rotating backend/frontend logging and redaction');
  assert.equal(errors.length,0,errors.join('\n'));const allIds=[...w.document.querySelectorAll('[id]')].map(e=>e.id);assert.equal(new Set(allIds).size,allIds.length);
  console.log(JSON.stringify({result:'PASS',checks,files:[]}));
 }finally{for(const p of pages)p.window.close();}
})().then(()=>process.exit(0)).catch(e=>{console.error(e.stack);process.exit(1);});
