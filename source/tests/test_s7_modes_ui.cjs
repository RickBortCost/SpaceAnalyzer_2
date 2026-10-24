/* Actual page/controllers + HTTP and simulated S7/UDP wire peers. */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {JSDOM,VirtualConsole}=require(process.env.JSDOM_PATH||'jsdom');
const [base,plusPort,udpPort]=process.argv.slice(2),errors=[],pages=[];
const wait=async(fn,label,timeout=16000)=>{const end=Date.now()+timeout;while(Date.now()<end){if(await fn()){console.error('PASS '+label);return;}await new Promise(r=>setTimeout(r,35));}throw Error('Timeout '+label+' '+errors.join(';'));};
async function page(){
 const html=await(await fetch(base)).text(),vc=new VirtualConsole();vc.on('jsdomError',e=>errors.push(e.message));
 const dom=new JSDOM(html,{url:base,runScripts:'outside-only',pretendToBeVisual:true,virtualConsole:vc});pages.push(dom);
 const w=dom.window,$=id=>w.document.getElementById(id);w.fetch=(u,o)=>fetch(new URL(u,base),o);w.AbortController=globalThis.AbortController;
 w.ResizeObserver=class{observe(){}disconnect(){}};
 const ctx=new Proxy({measureText:t=>({width:String(t).length*7})},{get:(o,k)=>k in o?o[k]:(()=>{})});
 w.HTMLCanvasElement.prototype.getContext=()=>ctx;w.HTMLCanvasElement.prototype.getBoundingClientRect=()=>({x:0,y:0,left:0,top:0,width:950,height:560,right:950,bottom:560});
 w.addEventListener('error',e=>errors.push(e.message));
 const scripts=[...w.document.querySelectorAll('script[src]')].map(s=>path.basename(new URL(s.src).pathname));
 w.eval(scripts.map(f=>fs.readFileSync(path.join(__dirname,'../static',f),'utf8')).join('\n')+'\nwindow.modeTest={switchSource,saveWorkspace,config,get selected(){return selected},get connected(){return connected},get charts(){return liveCharts},get history(){return historyCharts},get record(){return recordState},stop(){polling=false;clearTimeout(pollTimer);clearTimeout(workspaceTimer);}};');
 await wait(()=>$('endpoint').value,'configuration loaded');await w.opcuaConfigReady;return {w,$,t:w.modeTest};
}
(async()=>{
 let {w,$,t}=await page();
 try{
  $('nav-connection').click();await t.switchSource('s7');$('s7_mode').value='symbolic';await w.switchS7Mode('symbolic');
  assert(!$('s7-symbolic-fields').hidden);assert($('s7-catalog-editor').hidden);assert($('s7-udp-fields').hidden);
  $('s7_host').value='127.0.0.1';$('s7_tcp_port').value=plusPort;$('sample_ms').value=10;
  $('s7-test').click();await wait(()=>$('s7-info').textContent.includes('Simboli CPU: 8'),'actual symbolic EXPLORE test');
  $('connect').click();await wait(()=>t.connected,'symbolic connected');$('scan').click();
  await wait(()=>$('scan-body').textContent.includes('Array[3]')&&$('scan-body').textContent.includes('Status'),'symbolic DB/structure/array scan');
  for(const symbol of ['DB1.CaricoA','DB1.Data.Status']){$('manual-node').value=symbol;$('add-node').click();await wait(()=>t.selected.has('s7:sym:'+symbol)&&!$('add-node').disabled,'symbol selected '+symbol);}
  await wait(()=>t.charts.charts[0].trend.series['s7:sym:DB1.CaricoA']?.length>20,'symbolic live chart');
  const row=$('selected').querySelector('[data-id="s7:sym:DB1.Data.Status"]');assert.equal(row.querySelector('.signal-name').textContent,'Status');assert(row.title.includes('DB1.Data.Status'));
  $('live-add-chart').click();assert.equal(t.charts.charts.length,2);const g=t.charts.charts[1];
  const drop=new w.Event('drop',{bubbles:true,cancelable:true});Object.defineProperty(drop,'dataTransfer',{value:{types:['application/x-opcua-signal'],getData:()=>'s7:sym:DB1.Data.Status'}});g.element.dispatchEvent(drop);assert(g.ids.has('s7:sym:DB1.Data.Status'));
  t.charts.charts[0].name='Simboli CPU';$('disconnect').click();await wait(()=>!t.connected&&!$('data-source').disabled,'symbolic disconnect');
  $('nav-connection').click();$('s7_mode').value='udp';await w.switchS7Mode('udp');assert.equal(t.selected.size,0);assert(!$('s7-udp-fields').hidden);assert($('s7-tcp-fields').hidden);
  $('s7_udp_bind').value='127.0.0.1';$('s7_udp_port').value=udpPort;$('s7_udp_length').value=24;$('s7_udp_timestamp_offset').value=0;$('sample_ms').value=500;
  $('s7-udp-tag-name').value='Macchina.CaricoA';$('s7-udp-tag-offset').value=8;$('s7-udp-tag-type').value='REAL';$('s7-udp-tag-add').click();
  await wait(()=>w.s7UDPTags.length===1&&!$('s7-udp-tag-add').disabled,'UDP field saved');assert($('s7-udp-tag-list').textContent.includes('CaricoA'));
  $('s7-test').click();await wait(()=>$('s7-info').textContent.includes('Prova s7:udp:8:REAL'),'UDP test receives actual packet');
  $('connect').click();await wait(()=>t.connected,'UDP listener connected');$('scan').click();await wait(()=>$('scan-body').textContent.includes('CaricoA'),'UDP configured scan');
  $('manual-node').value='s7:udp:8:REAL';$('add-node').click();await wait(()=>t.selected.has('s7:udp:8:REAL')&&!$('add-node').disabled,'UDP variable selected');
  await wait(()=>t.charts.charts[0].trend.series['s7:udp:8:REAL']?.length>40,'UDP samples independent of 500ms polling');
  await wait(()=>!$('s7-udp-diag').hidden&&$('s7-udp-diag').textContent.includes('ricezione attiva'),'UDP live diagnostics');assert($('s7-udp-diag').textContent.includes('PLC UTC'));
  $('record').click();await wait(()=>!$('record-modal').hidden,'UDP Record format popup');w.document.querySelector('input[name="record-format"][value="parquet"]').checked=true;
  $('record-form').dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));await wait(()=>t.record.active&&t.record.cycles_received>50,'UDP Parquet recording');
  const graph=t.charts.charts[0],pause=graph.element.querySelector('[data-chart-role="pausePlay"]');pause.click();assert(graph.trend.frozen);
  const frozen=JSON.stringify(graph.trend.series),before=t.record.cycles_received;await wait(()=>t.record.cycles_received>before+30,'UDP Record continues while graph paused');assert.equal(JSON.stringify(graph.trend.series),frozen);pause.click();
  $('record-stop').click();await wait(()=>t.record.state==='stopped','UDP Record finalized');const filename=t.record.filename;assert.equal(t.record.error,'');
  $('disconnect').click();await wait(()=>!t.connected&&!$('data-source').disabled,'UDP disconnected');
  $('nav-recordings').click();await wait(()=>$('history-file-list').textContent.includes(filename),'recording repository');
  [...$('history-file-list').querySelectorAll('button')].find(b=>b.textContent.includes(filename)).click();$('history-load').click();await wait(()=>t.history.active.context.loaded,'UDP Parquet replay graph');assert(t.history.active.trend.series['s7:udp:8:REAL'].length>30);
  $('nav-live').click();$('nav-connection').click();$('s7_mode').value='symbolic';await w.switchS7Mode('symbolic');assert(t.selected.has('s7:sym:DB1.CaricoA'));assert.equal(t.charts.charts.length,2);assert.equal(t.charts.charts[0].name,'Simboli CPU');
  $('s7_mode').value='udp';await w.switchS7Mode('udp');assert(t.selected.has('s7:udp:8:REAL'));await t.saveWorkspace(true);
  t.stop();await new Promise(r=>setTimeout(r,600));const old=w;({w,$,t}=await page());old.close();assert.equal(t.config().s7_mode,'udp');assert.equal(t.config().s7_udp_port,Number(udpPort));assert.equal(w.s7UDPTags.length,1);assert(t.selected.has('s7:udp:8:REAL'));
  $('nav-connection').click();$('s7_mode').value='symbolic';await w.switchS7Mode('symbolic');assert.equal(t.charts.charts.length,2);assert(t.selected.has('s7:sym:DB1.Data.Status'));
  assert.deepEqual(errors,[]);console.log(JSON.stringify({result:'PASS',checks:['real EXPLORE/LID browse, structs and arrays','short signal names/full-path hover','multi-chart symbolic selection','mode-specific workspace persistence','UDP field editor and actual packet test','UDP per-packet trend and PLC timestamp diagnostics','Record/Stop/Parquet replay','pause preserves continuous recording','no frontend exceptions']},null,2));
 }finally{t.stop();await new Promise(r=>setTimeout(r,600));for(const d of pages)d.window.close();}
})().catch(e=>{console.error(e.stack);process.exit(1);});
