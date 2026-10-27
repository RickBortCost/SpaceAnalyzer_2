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
  w.eval(['i18n-catalog.js','i18n.js','trend.js','workspace.js','app.js','history.js','advanced.js','fft-ui.js','source.js','s7.js','calculations.js','studio.js','ssh.js','live-extras.js'].map(file=>fs.readFileSync(path.join(__dirname,'../static',file),'utf8')).join('\n')+'\nwindow.uiTest={get trend(){return liveCharts.charts[0].trend},get selected(){return selected},get record(){return recordState},get connected(){return connected},get liveCharts(){return liveCharts},get historyCharts(){return historyCharts},selectHistoryFile,loadHistory,importHistoryLocalFile,loadHistory};');
  const $=id=>w.document.getElementById(id);
  try{await wait(()=>$('endpoint').value,'configuration loaded');}catch(e){console.error('UI notice:', $('msg').textContent);throw e;}
  await w.opcuaConfigReady;return {dom,w,$};
}

// Opening a file keeps every recorded sample: no display reduction, exact values, single-sample spikes, M4 drawing.
(async()=>{
  const {w,$}=await page();const checks=[];const hist=w.uiTest.historyCharts,g=hist.active;
  const N=150000,base=1700000000000,spikeAt=77777;
  const value=(k,i)=>k===0?(i===spikeAt?1000:Math.sin(i/50)):(i%2===0?1:-1)*(i/N);
  const lines=['timestamp_ms,node_id,signal_name,value_numeric,good,quality,status,sample_ms'];
  for(let i=0;i<N;i++){lines.push(`${base+i},ns=2;s=Wave,Wave,${value(0,i)},True,Good,Good,1`);lines.push(`${base+i},ns=2;s=Saw,Saw,${value(1,i)},True,Good,Good,1`);}
  await w.uiTest.importHistoryLocalFile(new globalThis.File([lines.join('\n')+'\n'],'FullResolution.csv',{type:'text/csv'}));
  await wait(()=>g.context?.loaded&&!g.context.loading,'full-resolution file opened',60000);
  const wave=g.trend.series['ns=2;s=Wave'],saw=g.trend.series['ns=2;s=Saw'];
  assert(w.isSampleSeries(wave)&&w.isSampleSeries(saw),'file data kept in typed columns');
  assert.equal(wave.length,N);assert.equal(saw.length,N);
  for(const i of [0,1,spikeAt-1,spikeAt,spikeAt+1,N-1]){assert.equal(wave.t[i],base+i);assert.equal(wave.v[i],value(0,i));assert.equal(saw.v[i],value(1,i));}
  assert.equal(wave.stats.ymax,1000);assert.equal(g.context.stats.decimated,false);assert.equal(g.context.stats.full_resolution,true);
  assert.equal($('history-points').textContent.replace(/\D/g,''),String(2*N));
  assert.match(g.context.status,/piena risoluzione/);assert($('history-max-points')===null,'no reduced-resolution option');
  checks.push('every sample of a 300.000-row CSV loaded with exact values');
  // Drawing: whole range fills each pixel column from the min to the max of all its samples and still reaches the
  // single-sample spike; zoom draws real samples.
  const record=()=>{const ops=[],rects=[];return {ops,rects,ctx:new Proxy({ops,lineWidth:1,measureText:t=>({width:String(t).length*7}),moveTo:(x,y)=>ops.push(['M',x,y]),lineTo:(x,y)=>ops.push(['L',x,y]),fillRect:(x,y,w,h)=>rects.push([x,y,w,h])},{get:(o,k)=>k in o?o[k]:(()=>{})})};};
  const b={xmin:base,xmax:base+N-1},cols=872,X=t=>(t-b.xmin)/(b.xmax-b.xmin)*cols,Y=v=>-v;
  let r=record();g.trend.drawSamples(r.ctx,wave,X,Y,false,b,cols);
  assert.equal(r.ops.length,0);assert(r.rects.length<=cols+1,'one column per pixel: '+r.rects.length);
  const spikeColumn=Math.floor((spikeAt)/(N-1)*cols);
  assert(r.rects.some(([x,y,,h])=>Math.floor(x+.5)===spikeColumn&&y<=-1000&&y+h>=-1000),'single-sample spike covered in its pixel column');
  const perColumn=new Map();for(let i=0;i<N;i++){const c=Math.floor((wave.t[i]-b.xmin)/(b.xmax-b.xmin)*cols),e=perColumn.get(c)||[Infinity,-Infinity];e[0]=Math.min(e[0],wave.v[i]);e[1]=Math.max(e[1],wave.v[i]);perColumn.set(c,e);}
  const drawn=new Map();for(const [x,y,,h] of r.rects)drawn.set(Math.floor(x+.5),[-(y+h),-y]);
  for(const [c,[lo,hi]] of perColumn){const d=drawn.get(c);assert(d&&d[0]<=lo&&d[1]>=hi,'column '+c+' covers the true min/max');}
  const zoom={xmin:base+spikeAt-100,xmax:base+spikeAt+100};r=record();g.trend.drawSamples(r.ctx,wave,t=>t-zoom.xmin,Y,false,zoom,cols);
  assert.equal(r.ops.length,203,'zoomed view draws each real sample (plus one neighbour per side)');
  checks.push('per-pixel min/max columns keep every extreme and spikes; zoom shows individual samples');
  // Markers on the spike read the exact original sample.
  hist.setMarkers({x1:base+spikeAt,x2:base+N-1});
  await wait(()=>g.trend.exactMarkerRows?.get('ns=2;s=Wave')?.x1,'exact markers',30000);
  assert.equal(g.trend.exactMarkerRows.get('ns=2;s=Wave').x1.value,1000);
  checks.push('markers read the exact spike sample');
  // Binary endpoint: same counts as the file, and a clear error instead of a reduced view for invalid selections.
  const name=g.context.file.name;
  const response=await w.fetch('/api/recordings/samples',{method:'POST',headers:{'Content-Type':'application/json','X-OPCUA-Token':w.document.querySelector('meta[name="opcua-token"]').content},body:JSON.stringify({source:'local',name,signal_ids:['ns=2;s=Saw'],start_ms:base+10,end_ms:base+19})});
  assert.equal(response.headers.get('Content-Type'),'application/x-space-analyzer-samples');
  const decoded=w.decodeSampleBuffer(await response.arrayBuffer());assert.equal(decoded.series['ns=2;s=Saw'].length,10);assert.equal(decoded.series['ns=2;s=Saw'].v[0],value(1,10));
  checks.push('binary samples endpoint returns the exact interval');
  // Live stream: appended deltas, revised last sample, FIFO mirror, archive prepend and frozen snapshots.
  {const L=new w.LiveSeries(),F=a=>new w.Float64Array(a),U=a=>new w.Uint8Array(a);
   L.append(F([1,2,3]),F([10,20,30]),F([1,2,3]),U([0,0,0]));assert.equal(L.length,3);assert.equal(L.lastSeq,3);
   const frozen=L.snapshot();L.dropLast();L.append(F([5,6]),F([30,40]),F([3.5,4]),U([0,2]));
   assert.deepEqual([...L.v].map(v=>Number.isNaN(v)?null:v),[1,2,3.5,4]);assert.deepEqual([...frozen.v],[1,2,3]);
   L.dropBefore(2);assert.deepEqual([...L.seq],[2,5,6]);L.prepend(F([1]),F([10]),F([1]),U([0]));assert.deepEqual([...L.t],[10,20,30,40]);
   for(let k=0;k<3000;k++)L.append(F([7+k]),F([50+k]),F([k]),U([0]));assert.equal(L.length,3004);assert.equal(L.stats.xmax,3049);
   L.keepLast(10);assert.equal(L.length,10);assert.equal(L.firstSeq,2997);assert(w.isSampleSeries(L.after(3045)));assert.equal(L.after(3045).length,4);}
  checks.push('LiveSeries keeps every streamed sample: revisions, FIFO window, archive prepend, snapshots');
  {const F=a=>new w.Float64Array(a),U=a=>new w.Uint8Array(a),is=w.eval('isCompactValueType');
   for(const t of ['UInt16','UINT','Boolean','BOOL','Int16','Byte','REAL','Float32'])assert.equal(is(t),true,t);
   for(const t of ['Double','LREAL','UInt32','DINT','Int64','UInt64','','Variant',undefined])assert.equal(is(t),false,String(t));
   // 16 bit: 4 byte per valore, esatto; 64 bit: 8 byte.
   const c=new w.LiveSeries(true),d=new w.LiveSeries(false);
   c.append(F([1,2,3]),F([1000,1010,1020]),F([0,40000,65535]),U([0,0,0]));d.append(F([1,2,3]),F([1000,1010,1020]),F([0,40000,65535.5]),U([0,0,0]));
   assert.equal(c.bv.BYTES_PER_ELEMENT,4);assert.equal(d.bv.BYTES_PER_ELEMENT,8);assert.deepEqual([...c.v],[0,40000,65535]);assert.equal(c.compact,true);
   // un valore non esatto in 32 bit porta la serie a 64 bit senza arrotondare nulla
   c.append(F([4,5]),F([1030,1040]),F([0.1,3000000001]),U([0,0]));
   assert.equal(c.compact,false);assert.equal(c.bv.BYTES_PER_ELEMENT,8);assert.deepEqual([...c.v],[0,40000,65535,0.1,3000000001]);
   // anche la parte storica anteposta non viene arrotondata
   const h=new w.LiveSeries(true);h.append(F([10]),F([2000]),F([7]),U([0]));h.prepend(F([9]),F([1990]),F([1e-7]),U([0]));
   assert.equal(h.compact,false);assert.deepEqual([...h.v],[1e-7,7]);
   // NaN non obbliga a 64 bit
   const n=new w.LiveSeries(true);n.append(F([1]),F([1]),F([NaN]),U([0]));assert.equal(n.compact,true);
   checks.push('LiveSeries stores 16 bit, boolean and Float32 signals as 32 bit values and widens to 64 bit when a value is not exact');}
  assert.deepEqual(errors,[]);console.log(JSON.stringify({ok:true,checks},null,2));
  for(const dom of pages)dom.window.close();
})().then(()=>process.exit(0)).catch(e=>{console.error(e);console.error(errors.join("\n"));process.exit(1);});
