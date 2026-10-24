// Real HTTP + OPC UA workflow. Canvas is stubbed, no Windows/ibaAnalyzer claims.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {File}=require('node:buffer');const {JSDOM,VirtualConsole}=require(process.env.JSDOM_PATH||'jsdom');
const [base,endpoint,rootNode,...ids]=process.argv.slice(2);const errors=[];
const wait=async(fn,label,timeout=18000)=>{const until=Date.now()+timeout;while(Date.now()<until){if(await fn()){console.error('PASS '+label);return;}await new Promise(r=>setTimeout(r,40));}throw Error(label+' timeout; '+errors.join(';'));};
(async()=>{
 const vc=new VirtualConsole();vc.on('jsdomError',e=>errors.push(e.message));
 const dom=new JSDOM(await(await fetch(base)).text(),{url:base,runScripts:'outside-only',pretendToBeVisual:true,virtualConsole:vc});const w=dom.window,$=id=>w.document.getElementById(id);
 w.FormData=globalThis.FormData;w.AbortController=globalThis.AbortController;w.fetch=(url,o)=>fetch(new URL(url,base),o);w.ResizeObserver=class{observe(){}disconnect(){}};
 w.HTMLCanvasElement.prototype.getBoundingClientRect=()=>({left:0,top:0,width:950,height:560,right:950,bottom:560});
 const ctx=new Proxy({measureText:s=>({width:String(s).length*7})},{get:(o,k)=>k in o?o[k]:(()=>{})});w.HTMLCanvasElement.prototype.getContext=()=>ctx;w.addEventListener('error',e=>errors.push(e.message));
 w.eval(['i18n-catalog.js','i18n.js','trend.js','workspace.js','app.js','history.js','advanced.js','fft-ui.js','source.js','s7.js','calculations.js','studio.js','ssh.js','live-extras.js'].map(f=>fs.readFileSync(path.join(__dirname,'../static',f),'utf8')).join('\n')+`\nwindow.t={get connected(){return connected},get selected(){return selected},get record(){return recordState},get live(){return liveCharts},get history(){return historyCharts},selectHistoryFile,loadHistory,importHistoryLocalFile,duplicateStudioChart,saveWorkspace,stop(){polling=false;clearTimeout(pollTimer);clearTimeout(workspaceTimer);clearTimeout(historyMarkerTimer);for(const g of historyCharts.charts){if(g.context)g.context.markerSeq++;}}}`);
 const t=w.t;try{
  await wait(()=>$('endpoint').value,'configuration');await w.opcuaConfigReady;
  $('nav-connection').click();$('endpoint').value=endpoint;$('security').value='None';$('sample_ms').value='10';$('root_nodeid').value=rootNode;$('connect').click();await wait(()=>t.connected,'OPC UA connection');
  for(const id of ids){$('manual-node').value=id;$('add-node').click();await wait(()=>t.selected.has(id)&&!$('add-node').disabled,'select signal');}
  $('record').click();await wait(()=>!$('record-modal').hidden,'record dialog');const radio=$('record-form').querySelector('[value="pda3"]');assert(radio);radio.checked=true;radio.dispatchEvent(new w.Event('change'));
  await wait(()=>$('record-pda3-template').textContent.includes('64'),'template 64 channels');assert.equal($('record-pda3-settings').hidden,false);assert($('record-duration').disabled);
  $('record-pda3-accept').checked=true;$('record-form').dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));await wait(()=>t.record.state==='recording'&&t.record.format==='pda3','native PDA3 recording');
  await wait(()=>t.record.cycles_received>35,'live samples');const before=t.record.cycles_received;t.live.active.trend.togglePause();await wait(()=>t.record.cycles_received>before+10,'recording while graph paused');t.live.active.trend.togglePause();
  $('record-stop').click();await wait(()=>!t.record.busy&&t.record.state==='stopped','short Stop saves DAT normally');assert(t.record.original_files.length);assert.equal(t.record.files.length,1);assert.equal(t.record.error,'');assert(t.record.files[0].duration_seconds<600);assert(t.record.files[0].rows<60000*2);
  await t.saveWorkspace(true);const config=await(await fetch(base+'/api/config')).json();assert.equal(config.workspace.record_format,'pda3');assert(config.workspace.recordOptions.pda3_accept_template_time);assert(config.workspace.recordOptions.pda3_auto_split);
  w.showAppPage('recordings');const listed=await(await fetch(base+'/api/recordings')).json();const file=listed.files.find(f=>f.name===process.env.UI_PDA3_NAME);assert(file);assert(file.name.endsWith('.pda3seq'));t.selectHistoryFile(file);await t.loadHistory();const first=t.history.active;assert(first.trend.domain);assert.equal(first.ids.size,2);
  const second=t.duplicateStudioChart(t.history,first);assert.equal(t.history.charts.length,2);t.history.setMarkers({x1:1792584000100,x2:1792584001100});assert.equal(first.trend.markers.x1,second.trend.markers.x1);t.history.hoverAt(first,1792584000500);assert.equal(second.trend.crosshair,1792584000500);
  const fft=await w.request('/api/analysis/fft',{source:'repository',name:file.name,signal_ids:['ads:MAIN.Noise1'],x1:1792584000000,x2:1792584020990,options:{block_size:1000}});assert(Math.abs(fft.spectrum.peak_hz-1)<.03);
  await t.importHistoryLocalFile([new File([fs.readFileSync(process.env.UI_PDA3_DAT)],'import.dat'),new File([fs.readFileSync(process.env.UI_PDA3_DAT.replace(/\.dat$/,'.json'))],'import.json')]);assert.equal(t.history.active.context.file.source,'local');assert(t.history.active.trend.domain);
  assert.deepEqual(errors,[]);console.log(JSON.stringify({result:'PASS',checks:['native PDA3 Record and original Parquet while paused; deterministic test-only 10 ms acquisition grid','short Stop saves actual sample count without duration error','workspace PDA3 automatic-split options','real noisy-data capacity fallback: multipart timeline, charts, shared markers and crosshair','FFT 1 Hz across DAT part boundaries','DAT + hash-linked JSON imported from PC'],files:[]},null,2));
 }finally{t.stop();dom.window.close();}
})().catch(e=>{console.error(e.stack);process.exitCode=1;});
