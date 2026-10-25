// Real service/recordings, with canvas stubbed; checks the stale shared-range bug.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {JSDOM,VirtualConsole}=require(process.env.JSDOM_PATH||'jsdom');
const [base]=process.argv.slice(2),pages=[],errors=[],checks=[];
const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));
const wait=async(fn,label,timeout=15000)=>{const end=Date.now()+timeout;while(Date.now()<end){if(await fn()){console.error('PASS '+label);return;}await pause(30);}throw Error('Timeout '+label+'; '+errors.join('; '));};
async function page(){
  const html=await(await fetch(base)).text(),vc=new VirtualConsole();vc.on('jsdomError',e=>errors.push(e.message));
  const dom=new JSDOM(html,{url:base,runScripts:'outside-only',pretendToBeVisual:true,virtualConsole:vc});pages.push(dom);
  const w=dom.window;w.AbortController=globalThis.AbortController;w.FormData=globalThis.FormData;
  w.fetch=(url,options)=>fetch(new URL(url,base),options);w.ResizeObserver=class{observe(){}disconnect(){}};
  w.HTMLCanvasElement.prototype.getBoundingClientRect=()=>({x:0,y:0,left:0,top:0,width:1050,height:560,right:1050,bottom:560});
  const canvas=new Proxy({measureText:text=>({width:String(text).length*7})},{get:(o,k)=>k in o?o[k]:(()=>{})});
  w.HTMLCanvasElement.prototype.getContext=()=>canvas;w.addEventListener('error',e=>errors.push(e.message));
  w.eval(['i18n-catalog.js','i18n.js','trend.js','workspace.js','app.js','history.js','advanced.js','fft-ui.js','source.js','s7.js','calculations.js','studio.js','ssh.js','live-extras.js'].map(name=>fs.readFileSync(path.join(__dirname,'../static',name),'utf8')).join('\n')+
    '\nwindow.__close={historyCharts,liveCharts,historyContext,showAppPage,selectHistoryFile,loadHistory,closeHistoryFile,closeAllHistoryFiles,saveWorkspace,workspaceSnapshot,request,get ready(){return workspaceReady;},source:s=>{activeSource=s;sourcePresentation();}};');
  await wait(()=>w.__close.ready,'workspace initialized');return {w,t:w.__close,$:id=>w.document.getElementById(id)};
}
const role=(g,name)=>g.element.querySelector('[data-chart-role="'+name+'"]');
const sharedZoom=(ws,g,a,b)=>{g.trend.zoom={xmin:a,xmax:b,axes:g.trend.fullDomain.axes};g.trend.draw();ws.syncRange(g,{xmin:a,xmax:b});};
(async()=>{
 try{
  const {w,t,$}=await page(),hist=t.historyCharts;
  for(const source of ['ads','s7','opcua']){t.source(source);assert.equal($('nav-live').textContent,'LIVE');}
  checks.push('navigation stays LIVE for ADS, Siemens S7 and OPC UA');
  t.showAppPage('recordings');await wait(()=>$('history-file-list').textContent.includes('fixture_A.csv'),'recording list');
  const listBefore=(await t.request('/api/recordings')).files;
  const a=listBefore.find(f=>f.name==='fixture_A.csv'),b=listBefore.find(f=>f.name==='fixture_B.parquet');
  assert.ok(a&&b);
  t.selectHistoryFile(a);await t.loadHistory();const first=hist.active;
  sharedZoom(hist,first,1700000010000,1700000015000);assert.ok(hist.timeRange);
  const laterStart=1700086400000;
  const body='timestamp_ms,node_id,signal_name,value_numeric,good,data_type,sample_ms\n'+Array.from({length:51},(_,i)=>`${laterStart+i*10},ns=2;s=Temperature,Nuova registrazione,${i},True,Double,10`).join('\n');
  const f=new globalThis.File([body],'different_day.csv',{type:'text/csv'});
  Object.defineProperty($('history-local-file'),'files',{configurable:true,value:[f]});$('history-local-file').dispatchEvent(new w.Event('change',{bubbles:true}));
  await wait(()=>first.context.loaded&&!first.context.loading&&first.context.file?.source==='local','new recording opened after zoom');
  assert.equal(hist.timeRange,null);assert.equal(first.trend.zoom,null);assert.equal(first.trend.linkedRange,null);
  assert.equal(first.trend.domain.xmin,laterStart);assert.equal(first.trend.domain.xmax,laterStart+500);assert.equal(role(first,'resetZoom').disabled,true);assert.equal(first.ids.size,1);
  checks.push('opening a recording on another day clears both manual and synchronized zoom without pressing Reset');
  // A late shared range while reading a selected file must also be discarded.
  t.selectHistoryFile(a);let release;const normalFetch=w.fetch;
  w.fetch=(url,options)=>String(url)==='/api/recordings/samples'?new Promise(resolve=>{release=()=>normalFetch(url,options).then(resolve);}):normalFetch(url,options);
  const loading=t.loadHistory(first);await wait(()=>release,'delayed data request captured');
  hist.timeRange={xmin:laterStart,xmax:laterStart+20};first.trend.linkedRange={...hist.timeRange};release();await loading;w.fetch=normalFetch;
  assert.equal(hist.timeRange,null);assert.equal(first.trend.domain.xmin,1700000000000);assert.equal(first.trend.domain.xmax,1700000059990);
  checks.push('a range arriving during file loading cannot hide the new data');
  sharedZoom(hist,first,1700000010000,1700000015000);
  $('history-add-chart').click();const second=hist.active;
  assert.equal(second.context.file,null);assert.equal(second.ids.size,0);assert.equal(second.trend.zoom,null);assert.equal(second.trend.linkedRange,null);assert.equal(second.trend.domain,null);
  assert.ok(first.context.loaded);assert.ok(first.trend.zoom);t.selectHistoryFile(b,second);await t.loadHistory(second);
  sharedZoom(hist,first,1700000010000,1700000015000);const firstSeries=JSON.stringify(first.trend.series),firstDomain=JSON.stringify(first.trend.domain);
  hist.setMarkers({x1:1700000012000,x2:1700000013000});$('history-close-file').click();
  assert.equal(second.context.file,null);assert.equal(second.nodes.size,0);assert.equal(second.ids.size,0);assert.deepEqual(Object.keys(second.series),[]);assert.equal(second.trend.fullDomain,null);assert.equal(second.trend.linkedRange,null);assert.equal($('history-file-controls').hidden,true);
  assert.equal(JSON.stringify(first.trend.series),firstSeries);assert.equal(JSON.stringify(first.trend.domain),firstDomain);assert.equal(first.context.file.name,a.name);assert.equal(hist.markers.x1,1700000012000);
  checks.push('close active file clears all of its traces and settings; another recording and its zoom stay intact');
  // Closing while data is being fetched must invalidate the outstanding result.
  t.selectHistoryFile(a,second);release=null;
  w.fetch=(url,options)=>String(url)==='/api/recordings/samples'?new Promise(resolve=>{release=()=>normalFetch(url,options).then(resolve);}):normalFetch(url,options);
  const pending=t.loadHistory(second);await wait(()=>release,'pending read captured');$('history-close-file').click();release();await pending;w.fetch=normalFetch;
  assert.equal(second.context.file,null);assert.equal(second.context.loaded,false);assert.equal(second.ids.size,0);
  checks.push('closing during a pending read prevents the late response from reopening the file');
  // Reopen in the emptied graph, including when X synchronization is disabled.
  $('history-sync-zoom').checked=false;$('history-sync-zoom').dispatchEvent(new w.Event('change',{bubbles:true}));
  t.selectHistoryFile(b,second);await t.loadHistory(second);second.trend.zoom={xmin:1700000000100,xmax:1700000000200,axes:second.trend.fullDomain.axes};second.trend.draw();
  t.selectHistoryFile(a,second);await t.loadHistory(second);assert.equal(second.trend.zoom,null);assert.equal(second.trend.linkedRange,null);
  const liveBefore=JSON.stringify(t.liveCharts.serialize());$('history-close-all').click();
  assert.equal(hist.charts.length,1);assert.equal(hist.active.context.file,null);assert.equal(hist.active.ids.size,0);assert.equal(hist.timeRange,null);assert.equal(hist.markers.x1,null);assert.equal(hist.markers.x2,null);assert.equal(hist.layoutSelect.value,'single');assert.equal(JSON.stringify(t.liveCharts.serialize()),liveBefore);
  const remaining=(await t.request('/api/recordings')).files;
  for(const file of [a,b])assert.ok(remaining.some(f=>f.name===file.name&&f.source===file.source));
  const snapshot=t.workspaceSnapshot();assert.equal(snapshot.history.charts.length,1);assert.equal(snapshot.history.charts[0].context.file,null);
  await t.saveWorkspace(true);const restored=await page();assert.equal(restored.t.historyCharts.charts.length,1);assert.equal(restored.t.historyContext().file,null);assert.equal(restored.$('nav-live').textContent,'LIVE');
  checks.push('close all returns one blank chart, preserves repository/live charts, and restores blank after workspace reload');
  // Close is also available in each chart's action menu, including fullscreen.
  const active=hist.active;t.selectHistoryFile(a,active);await t.loadHistory(active);role(active,'expand').click();assert.ok(active.element.classList.contains('studio-fullscreen'));role(active,'closeFile').click();
  assert.equal(active.context.file,null);assert.ok(!active.element.classList.contains('studio-fullscreen'));assert.equal(active.ids.size,0);
  assert.equal(errors.length,0,errors.join('\n'));console.log(JSON.stringify({result:'PASS',checks,files:[]}));
 }finally{for(const p of pages)p.window.close();}
})().then(()=>process.exit(0)).catch(e=>{console.error(e.stack);process.exit(1);});
