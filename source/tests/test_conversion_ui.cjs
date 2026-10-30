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
 item('fixture_B.parquet').click();assert.equal($('conv-format').value,'csv');$('conv-format').value='das';$('conv-format').dispatchEvent(new w.Event('change'));
 assert($('conv-format-note').textContent.includes('Int16'));
 const boxes=[...$('conv-signal-list').querySelectorAll('input[type=checkbox]')];assert.equal(boxes.length,2);boxes[1].checked=false;boxes[1].dispatchEvent(new w.Event('change'));
 assert.equal($('conv-signal-count').textContent,'1 / 2 · max 64');assert($('conv-all').indeterminate);
 const start=1700000000000;$('conv-to').value=w.localInputFromMs(start+9990);
 job=await convert('Parquet to FDA');assert.equal(job.rows_read,1000);assert(job.files[0].name.endsWith('.das'));
 listed=(await recordings()).find(f=>f.name===job.files[0].name);assert(listed&&listed.format==='das');assert.equal(listed.signals.length,1);assert.equal(listed.signals[0].nodeid,'ns=2;s=Temperature');checks.push('Parquet to FDA, signal and interval selection');
 // Parquet -> RBF, all signals, full range restored.
 $('conv-full-range').click();$('conv-all').checked=true;$('conv-all').dispatchEvent(new w.Event('change'));$('conv-format').value='rbf';$('conv-format').dispatchEvent(new w.Event('change'));
 job=await convert('Parquet to RBF');assert.equal(job.rows_read,12000);assert(job.files.every(f=>f.name.endsWith('.rbf')));checks.push('Parquet to RBF');
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
