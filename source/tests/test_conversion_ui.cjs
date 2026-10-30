// Conversione page against the real C# service: fixture_A.csv / fixture_B.parquet (6000 cycles x 2 signals) from ui_harness_cs.py.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {JSDOM,VirtualConsole}=require(process.env.JSDOM_PATH||'jsdom');
const [base]=process.argv.slice(2),errors=[];
const wait=async(fn,label,timeout=30000)=>{const end=Date.now()+timeout;while(Date.now()<end){if(await fn()){console.error('PASS '+label);return;}await new Promise(r=>setTimeout(r,40));}throw Error('Timeout '+label+' '+errors.join(';'));};
(async()=>{
const vc=new VirtualConsole();vc.on('jsdomError',e=>errors.push(e.message));const html=await(await fetch(base)).text(),dom=new JSDOM(html,{url:base,runScripts:'outside-only',pretendToBeVisual:true,virtualConsole:vc}),w=dom.window,$=id=>w.document.getElementById(id);
w.fetch=(url,options)=>fetch(new URL(url,base),options);w.AbortController=globalThis.AbortController;w.FormData=globalThis.FormData;w.ResizeObserver=class{observe(){}disconnect(){}};
const ctx=new Proxy({measureText:t=>({width:String(t).length*7})},{get:(o,k)=>k in o?o[k]:(()=>{})});w.HTMLCanvasElement.prototype.getContext=()=>ctx;w.HTMLCanvasElement.prototype.getBoundingClientRect=()=>({x:0,y:0,left:0,top:0,width:950,height:560,right:950,bottom:560});
w.addEventListener('error',e=>errors.push(e.message));w.eval([...w.document.querySelectorAll('script[src]')].map(s=>fs.readFileSync(path.join(__dirname,'../static',path.basename(new URL(s.src).pathname)),'utf8')).join('\n')+'\nwindow.convTest={get conv(){return CONV},get history(){return historyCharts},importConversionFiles,stop(){polling=false;clearTimeout(pollTimer);clearTimeout(workspaceTimer);}};');
const t=w.convTest,checks=[];
const item=name=>[...$('conv-file-list').querySelectorAll('.conversion-file')].find(b=>b.title===name);
const recordings=async()=>(await(await fetch(base+'/api/recordings')).json()).files;
async function convert(label){const previous=t.conv.job?.id;$('conv-start').click();await wait(()=>t.conv.job&&t.conv.job.id!==previous&&t.conv.job.state!=='running',label);assert.equal(t.conv.job.state,'ready',t.conv.job.error);await wait(()=>!$('conv-result').hidden,label+' result');return t.conv.job;}
try{
 await wait(()=>$('endpoint').value,'config loaded');await w.opcuaConfigReady;
 $('nav-conversion').click();await wait(()=>!$('conversion-page').hidden&&item('fixture_A.csv')&&item('fixture_B.parquet'),'conversion page lists recordings');
 assert($('live-page').hidden);assert($('recordings-page').hidden);assert.equal($('nav-conversion').getAttribute('aria-pressed'),'true');assert($('conv-form').hidden);checks.push('navigation');
 // A primary capture is never uploaded from the browser: both pages say where to open it, and the picker is server side (Windows only).
 await assert.rejects(()=>t.importConversionFiles([new File(['x'],'copia.sac')]),/archivi primari/);
 await assert.rejects(()=>t.importConversionFiles([new File(['x'],'copia.sac.part')]),/archivi primari/);
 assert($('conv-open-primary')&&!$('conv-open-primary').disabled);if(process.platform!=='win32'){$('conv-open-primary').click();
 await wait(()=>!$('conv-error').hidden&&$('conv-error').textContent.includes('Windows'),'primary picker refused outside Windows');$('conv-error').hidden=true;}checks.push('primary capture opened by path, never uploaded');
 // CSV -> Parquet: every sample, default target, destination folder.
 item('fixture_A.csv').click();assert(!$('conv-form').hidden);assert.equal($('conv-format').value,'parquet');assert.equal($('conv-signal-count').textContent,'2 / 2');
 assert($('conv-destination').textContent.includes('Conversioni'));
 let job=await convert('CSV to Parquet');assert.equal(job.rows_read,12000);assert.equal(job.files.length,1);assert(/^Conversioni\/fixture_A_\d{8}_\d{6}\.parquet$/.test(job.files[0].name),job.files[0].name);
 let listed=(await recordings()).find(f=>f.name===job.files[0].name);assert(listed,'converted file listed');assert.equal(listed.format,'parquet');assert.equal(listed.signals.length,2);assert.equal(Number(listed.rows_written),12000);
 assert.deepEqual(listed.signals.map(s=>s.display_name),['Temperatura','Potenza']);checks.push('CSV to Parquet, all samples');
 // Parquet -> FDA with one signal and the interval of the first 10 s.
 item('fixture_B.parquet').click();assert($('conv-split-box').hidden,'split choice only for primary captures');assert.equal($('conv-format').value,'csv');$('conv-format').value='das';$('conv-format').dispatchEvent(new w.Event('change'));
 assert($('conv-format-note').textContent.includes('Int16'));
 const boxes=[...$('conv-signal-list').querySelectorAll('input[type=checkbox]')];assert.equal(boxes.length,2);boxes[1].checked=false;boxes[1].dispatchEvent(new w.Event('change'));
 assert.equal($('conv-signal-count').textContent,'1 / 2 · max 64');assert($('conv-all').indeterminate);
 const start=1700000000000;$('conv-to').value=w.localInputFromMs(start+9990);
 job=await convert('Parquet to FDA');assert.equal(job.rows_read,1000);assert(job.files[0].name.endsWith('.das'));
 listed=(await recordings()).find(f=>f.name===job.files[0].name);assert(listed&&listed.format==='das');assert.equal(listed.signals.length,1);assert.equal(listed.signals[0].nodeid,'ns=2;s=Temperature');checks.push('Parquet to FDA, signal and interval selection');
 // Parquet -> RBF, all signals, full range restored.
 $('conv-full-range').click();$('conv-all').checked=true;$('conv-all').dispatchEvent(new w.Event('change'));$('conv-format').value='rbf';$('conv-format').dispatchEvent(new w.Event('change'));
 job=await convert('Parquet to RBF');assert.equal(job.rows_read,12000);assert(job.files.every(f=>f.name.endsWith('.rbf')));checks.push('Parquet to RBF');
 // TwinCAT Scope project (.svdx): listed with its channels, converted to CSV with every sample of both channels.
 await wait(()=>item('fixture_C.svdx'),'scope project listed');item('fixture_C.svdx').click();assert.equal($('conv-signal-count').textContent,'2 / 2');
 $('conv-format').value='csv';$('conv-format').dispatchEvent(new w.Event('change'));
 job=await convert('Scope svdx to CSV');assert.equal(job.rows_read,2000);assert(job.files[0].name.endsWith('.csv'));
 listed=(await recordings()).find(f=>f.name===job.files[0].name);assert.deepEqual(listed.signals.map(s=>s.display_name),['Posizione','Velocita']);checks.push('Scope .svdx to CSV');
 // Same project, one channel and the first 100 ms only: the file shrinks accordingly.
 {const scopeStart=(134238281553100000-116444736000000000)/10000;const bigger=listed.size_bytes;assert(bigger>0);
  const checks2=[...$('conv-signal-list').querySelectorAll('input[type=checkbox]')];assert.equal(checks2.length,2);checks2[1].checked=false;checks2[1].dispatchEvent(new w.Event('change'));
  $('conv-from').value=w.localInputFromMs(scopeStart);$('conv-to').value=w.localInputFromMs(scopeStart+99);
  job=await convert('Scope svdx one channel, first 100 ms');assert.equal(job.rows_read,100);assert.equal(job.signal_count,1);
  listed=(await recordings()).find(f=>f.name===job.files[0].name);assert.deepEqual(listed.signals.map(s=>s.display_name),['Posizione']);assert(listed.size_bytes<bigger/10);
  $('conv-full-range').click();$('conv-all').checked=true;$('conv-all').dispatchEvent(new w.Event('change'));checks.push('Scope .svdx: channel and period selection');}
 // Progress: overall percentage in every phase of a running job, with the estimate marked by "~".
 {const saved=t.conv.job,bar=$('conv-progress'),text=()=>$('conv-status').textContent;
  const show=job=>{t.conv.job={id:'x',source:'repository',name:'fixture_A.csv',format:'rbf',format_label:'RBF v3',...job};w.renderConversionJob();};
  show({state:'running',phase:'read',percent:20,rows_read:5000,rows_total:20000});assert.equal(bar.value,.2);assert(text().startsWith('20% · '));assert(/5[.,]?000/.test(text()));
  show({state:'running',phase:'read',percent:10,rows_read:100,rows_total:1000,rows_total_estimated:true});assert(text().includes('~'));
  show({state:'running',phase:'stage',percent:55,rows_read:20000,rows_total:20000});assert.equal(bar.value,.55);assert(text().startsWith('55% · '));assert(text().includes('Preparazione'));
  show({state:'running',phase:'write',percent:85,rows_read:20000,rows_total:20000});assert.equal(bar.value,.85);assert(text().startsWith('85% · '));assert(text().includes('RBF v3'));
  show({state:'running',phase:'read',percent:null,rows_read:100,rows_total:null});assert(!bar.hasAttribute('value'));assert(!/%/.test(text()));
  show({state:'ready',phase:'done',percent:100,rows_read:20000,rows_total:20000,files:[]});assert.equal(bar.value,1);
  t.conv.job=saved;w.renderConversionJob();checks.push('conversion percentage in read, staging and writing phases');}
 // Upload of a large file from the PC reports its percentage, then the reading on the server.
 {const seen=[];w.XMLHttpRequest=class{constructor(){this.upload={};this.headers={};}open(m,u){this.url=u;}setRequestHeader(k,v){this.headers[k]=v;}
   send(form){(async()=>{const total=8*1048576;this.upload.onprogress({lengthComputable:true,loaded:total/2,total});seen.push([$('conv-import-progress').hidden,$('conv-import-progress').value,$('conv-list-status').textContent]);
     this.upload.onprogress({lengthComputable:true,loaded:total,total});seen.push([$('conv-import-progress').hidden,$('conv-import-progress').hasAttribute('value'),$('conv-list-status').textContent]);
     const r=await fetch(new URL(this.url,base),{method:'POST',headers:this.headers,body:form});this.status=r.status;this.statusText=r.statusText;this.responseText=await r.text();this.onload();})().catch(e=>this.onerror(e));}};
  const big=new File([new Uint8Array(8*1048576)],'grande.csv',{type:'text/csv'});
  await assert.rejects(()=>t.importConversionFiles([big]));
  assert.equal(seen[0][0],false);assert.equal(seen[0][1],.5);assert(seen[0][2].includes('50%'),seen[0][2]);assert.equal(seen[1][1],false);assert(seen[1][2].includes('Lettura'),seen[1][2]);
  assert($('conv-import-progress').hidden);delete w.XMLHttpRequest;checks.push('upload percentage and server reading');}
 // A file from the PC: imported, selected, converted to CSV.
 const lines=['timestamp_utc,timestamp_ms,cycle,node_id,signal_name,value_numeric,value_text,data_type,status,good,sample_ms,quality'];
 for(let i=0;i<50;i++)for(const id of ['pc:A','pc:B','pc:C'])lines.push([new Date(start+i*20).toISOString(),start+i*20,i,id,id,i,i,'Double','Good','True',20,'Good'].join(','));
 const csv=lines.join('\r\n')+'\r\n';
 await t.importConversionFiles([new File([csv],'dal_pc.csv',{type:'text/csv'})]);await wait(()=>t.conv.file?.source==='local','imported file selected');
 assert.equal($('conv-format').value,'parquet');$('conv-format').value='csv';$('conv-format').dispatchEvent(new w.Event('change'));assert($('conv-format-note').textContent.includes('Stesso formato'));
 job=await convert('PC file to CSV');assert.equal(job.rows_read,150);assert(job.files[0].name.endsWith('.csv'));checks.push('file from PC');
 // An interval without samples reports the error and writes no file.
 $('conv-format').value='parquet';$('conv-format').dispatchEvent(new w.Event('change'));$('conv-from').value=w.localInputFromMs(start+10000000);$('conv-to').value=w.localInputFromMs(start+20000000);
 const before=t.conv.job?.id;$('conv-start').click();await wait(()=>t.conv.job&&t.conv.job.id!==before&&t.conv.job.state==='error','empty interval error');assert($('conv-status').textContent.includes('Nessun campione'));checks.push('empty selection error');
 // Open the result in Recordings.
 $('conv-full-range').click();job=await convert('second conversion');$('conv-result-list').querySelector('button').click();
 await wait(()=>!$('recordings-page').hidden&&t.history.active.context.file?.name===job.files[0].name,'opened in Recordings');assert($('conversion-page').hidden);checks.push('open in Recordings');
 // Language.
 $('ui-language').value='en';$('ui-language').dispatchEvent(new w.Event('change',{bubbles:true}));await wait(()=>$('nav-conversion').textContent==='Conversion','English label');
 $('nav-conversion').click();await wait(()=>$('conv-start').textContent==='Convert'&&$('conv-files-title').textContent==='Conversion','English page');checks.push('i18n');
 assert.deepEqual(errors,[]);console.log(JSON.stringify({result:'PASS',checks,files:[]},null,2));
}finally{t.stop();await new Promise(r=>setTimeout(r,300));w.close();}
})().then(()=>process.exit(0)).catch(e=>{console.error(e.stack);process.exit(1);});
