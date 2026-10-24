// Real HTTP service and real comparison worker; canvas is stubbed in this DOM test.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {JSDOM,VirtualConsole}=require(process.env.JSDOM_PATH||'jsdom');
const [base]=process.argv.slice(2),errors=[],pages=[],checks=[];
const wait=async(fn,label,timeout=20000)=>{const until=Date.now()+timeout;while(Date.now()<until){if(await fn()){console.error('PASS '+label);return;}await new Promise(r=>setTimeout(r,35));}throw Error('Timeout '+label+'; '+errors.join('; '));};
async function page(){
  const html=await(await fetch(base)).text(),vc=new VirtualConsole();vc.on('jsdomError',e=>errors.push(e.message));
  const dom=new JSDOM(html,{url:base,runScripts:'outside-only',pretendToBeVisual:true,virtualConsole:vc});pages.push(dom);
  const w=dom.window;w.AbortController=globalThis.AbortController;w.FormData=globalThis.FormData;
  w.fetch=(url,options)=>fetch(new URL(url,base),options);w.ResizeObserver=class{observe(){}disconnect(){}};
  w.HTMLCanvasElement.prototype.getBoundingClientRect=()=>({x:0,y:0,left:0,top:0,width:1000,height:560,right:1000,bottom:560});
  const ctx=new Proxy({measureText:text=>({width:String(text).length*7})},{get:(o,k)=>k in o?o[k]:(()=>{})});
  w.HTMLCanvasElement.prototype.getContext=()=>ctx;w.addEventListener('error',e=>errors.push(e.message));
  w.eval(['i18n-catalog.js','i18n.js','trend.js','workspace.js','app.js','history.js','advanced.js','fft-ui.js','source.js','s7.js','calculations.js','studio.js','ssh.js','live-extras.js'].map(f=>fs.readFileSync(path.join(__dirname,'../static',f),'utf8')).join('\n')+
    '\nwindow.__test={historyCharts,historyContext,saveWorkspace,workspaceSnapshot,request,showAppPage,selectHistoryFile,loadHistory,get ready(){return workspaceReady;}};');
  await wait(()=>w.__test.ready,'workspace ready');return {w,t:w.__test,$:id=>w.document.getElementById(id)};
}
const change=(w,node,value)=>{if(node.type==='checkbox')node.checked=value;else node.value=value;node.dispatchEvent(new w.Event(node.type==='search'||node.type==='number'||node.tagName==='SELECT'?'input':'change',{bubbles:true}));};
const clickCheck=(w,$,name)=>{const row=[...$('history-file-list').querySelectorAll('.history-file-item')].find(e=>e.textContent.includes(name));assert.ok(row,name);const check=row.querySelector('input');check.checked=true;check.dispatchEvent(new w.Event('change',{bubbles:true}));};
(async()=>{
  try{
    const {w,t,$}=await page();t.showAppPage('recordings');await wait(()=>$('history-file-list').textContent.includes('fixture_A.csv'),'file inventory');
    change(w,$('history-filter-format'),'csv');assert.ok($('history-file-list').textContent.includes('fixture_A.csv'));assert.ok(!$('history-file-list').textContent.includes('fixture_B.parquet'),JSON.stringify({filter:$('history-filter-format').value,html:$('history-file-list').innerHTML,errors}));
    clickCheck(w,$,'fixture_A.csv');change(w,$('history-duration-min'),100);assert.equal($('history-file-list').querySelectorAll('button').length,0);assert.equal(t.historyContext().compareFiles.length,1);
    change(w,$('history-duration-min'),'');change(w,$('history-filter-format'),'');clickCheck(w,$,'fixture_B.parquet');assert.equal(t.historyContext().compareFiles.length,2);assert.equal($('history-compare').disabled,false);checks.push('format/name/duration filters preserve the selected files');
    const offset=$('history-compare-files').querySelector('[data-compare-offset="1"]');offset.value='1000';offset.dispatchEvent(new w.Event('change',{bubbles:true}));
    $('history-compare-coverage').value='intersection';$('history-compare-coverage').dispatchEvent(new w.Event('change',{bubbles:true}));$('history-compare').click();
    await wait(()=>t.historyContext().loaded&&!t.historyContext().loading&&t.historyContext().file?.comparison,'mixed comparison load');
    const h1=t.historyCharts.active;assert.equal(h1.nodes.size,4);assert.equal(h1.context.file.comparison.coverage,'intersection');assert.equal(h1.context.compareFiles[1].offset_ms,1000);
    const a=[...h1.nodes].find(([id,n])=>n.display_name.includes('Temperatura')&&n.display_name.includes('fixture_A.csv'))[0],b=[...h1.nodes].find(([id,n])=>n.display_name.includes('Temperatura')&&n.display_name.includes('fixture_B.parquet'))[0];
    assert.notEqual(a,b);t.historyCharts.setMarkers({x1:1700000012340,x2:1700000022340});
    await wait(()=>h1.trend.exactMarkerRows?.get(a)?.x1&&h1.trend.exactMarkerRows?.get(b)?.x1,'original exact markers across files');
    assert.equal(h1.trend.exactMarkerRows.get(a).x1.value,12.34);assert.equal(h1.trend.exactMarkerRows.get(b).x1.value,113.4);checks.push('CSV plus Parquet; independent identities, manual clock offset, common time interval and exact markers');
    const loadedName=h1.context.file.name,original=JSON.stringify(h1.trend.series);
    change(w,$('history-file-search'),'fixture');await t.saveWorkspace(true);
    const restored=await page();await wait(()=>restored.t.historyContext().loaded,'restore mixed-file workspace');assert.equal(restored.t.historyContext().file.name,loadedName);assert.equal(restored.t.historyContext().compareFiles[1].offset_ms,1000);assert.equal(restored.$('history-file-search').value,'fixture');assert.equal(restored.t.historyCharts.active.nodes.size,4);checks.push('comparison and filters survive workspace reload');
    $('history-add-chart').click();const h2=t.historyCharts.active;
    const csv=(period,count,offset)=>'timestamp_ms,node_id,signal_name,value_numeric,good,data_type,sample_ms\n'+Array.from({length:count},(_,i)=>`${1700000000000+offset+i*period},same,Segnale,${Math.sin(i)},True,Double,${period}`).join('\n');
    const files=[new globalThis.File([csv(1,100,0)],'import_1ms.csv',{type:'text/csv'}),new globalThis.File([csv(10,30,50)],'import_10ms.csv',{type:'text/csv'})];
    Object.defineProperty($('history-local-file'),'files',{configurable:true,value:files});$('history-local-file').dispatchEvent(new w.Event('change',{bubbles:true}));
    await wait(()=>h2.context.loaded&&h2.context.file?.comparison&&!h2.context.loading,'automatic multiple local-file comparison');
    assert.equal(h2.nodes.size,2);assert.equal(h2.context.stats.numeric_rows,130);assert.equal(h1.context.file.name,loadedName);assert.equal(JSON.stringify(h1.trend.series),original);checks.push('multiple external imports with unequal sample rates and durations; other chart remains independent');
    // Synchronization and tools still target the comparison dataset, not a source file.
    const exported=await t.request('/api/analysis/export',{source:'local',name:h2.context.file.name,signal_ids:[...h2.ids],x1:1700000000000,x2:1700000000099,format:'csv'});assert.equal(exported.rows,105);
    assert.equal(errors.length,0,errors.join('\n'));console.log(JSON.stringify({result:'PASS',checks,files:[]}));
  }finally{for(const p of pages)p.window.close();}
})().then(()=>process.exit(0)).catch(e=>{console.error(e.stack);process.exit(1);});
