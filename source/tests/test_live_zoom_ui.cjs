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

function pointer(w,g,type,x,y){
 const target=type==='up'&&!w.PointerEvent?w:g.trend.canvas;
 target.dispatchEvent(new (w.PointerEvent||w.MouseEvent)((w.PointerEvent?'pointer':'mouse')+type,{bubbles:true,clientX:x,clientY:y,button:0,pointerId:1}));
}
function zoom(w,g){
 const {p,w:width,h}=g.trend.layout;
 pointer(w,g,'down',p.l+.15*(width-p.l-p.r),p.t+.15*(h-p.t-p.b));
 pointer(w,g,'move',p.l+.65*(width-p.l-p.r),p.t+.75*(h-p.t-p.b));
 pointer(w,g,'up',p.l+.65*(width-p.l-p.r),p.t+.75*(h-p.t-p.b));
}
const close=(a,b)=>assert(Math.abs(a-b)<1e-6,`${a} != ${b}`);

(async()=>{
 const {w,$,t}=await page(),checks=[];
 try{
  t.stopPolling();
  // Drain the initial HTTP poll before supplying deterministic data frames.
  await new Promise(resolve=>setTimeout(resolve,100));
  const ws=t.liveCharts,g=ws.active,a=g.trend;
  const ids=['ns=2;s=Temperature','ads:MAIN.Wave'];
  const nodes=new w.Map(ids.map((id,i)=>[id,{nodeid:id,display_name:i?'Power':'Temperature'}]));
  let end=1700000010000;
  function frame(next,flipped=false){
   end=next;
   const data=Object.fromEntries(ids.map((id,k)=>[id,Array.from({length:41},(_,i)=>[end-10000+i*250,flipped?(k?2:1000000):(k?1000000+i*100:20+i/10),'Good'])]));
   ws.displayRange={xmin:end-10000,xmax:end};
   t.feed(data,nodes,{[ids[0]]:'Double',[ids[1]]:'Double'});
  }
  frame(end);
  assert.equal(ws.liveZoom,false);assert.equal(a.axisPlan.dual,true);

  // Rotella sull'area del grafico: periodo visualizzato da 10 s fino a tutta la sessione (120 s qui).
  {
   const sessionEnd=end,longData=Object.fromEntries(ids.map((id,k)=>[id,Array.from({length:121},(_,i)=>[sessionEnd-120000+i*1000,(k?100:10)+i,'Good'])]));
   const feedLong=()=>{ws.displayRange={xmin:sessionEnd-120000,xmax:sessionEnd};t.feed(longData,nodes,{[ids[0]]:'Double',[ids[1]]:'Double'});};
   feedLong();
   const spanOf=()=>a.domain.xmax-a.domain.xmin,lay=a.layout,x=lay.p.l+100,y=lay.p.t+40;
   const wheel=(dy,px=x,py=y)=>{const e=new w.WheelEvent('wheel',{bubbles:true,cancelable:true,clientX:px,clientY:py,deltaY:dy});a.canvas.dispatchEvent(e);return e;};
   close(spanOf(),120000);assert.equal(ws.liveSpan||null,null);
   assert(wheel(-100).defaultPrevented);close(spanOf(),120000/1.25);close(a.domain.xmax,sessionEnd);
   for(let i=0;i<30;i++)wheel(-100);
   close(spanOf(),10000);close(a.domain.xmax,sessionEnd);
   wheel(100);close(spanOf(),12500);
   for(let i=0;i<40;i++)wheel(100);
   close(spanOf(),120000);assert.equal(ws.liveSpan,null,'oltre l’intera sessione torna alla vista completa');
   wheel(-100);wheel(-100);const narrow=spanOf();
   assert(narrow<120000);
   a.canvas.dispatchEvent(new w.MouseEvent('dblclick',{bubbles:true,clientX:x,clientY:y}));
   close(spanOf(),120000);assert.equal(ws.liveSpan,null);
   a.pauseForZoom();assert(!wheel(-100).defaultPrevented,'a grafico fermo la rotella non cambia il periodo');a.resetZoom();
   const axisHit=lay.p.l-29;assert(!ws.liveSpan);wheel(-100,axisHit,y);close(spanOf(),120000,'la rotella sull’asse Y non cambia il periodo');
   frame(end);
   checks.push('Rotella sul grafico LIVE: periodo X da 10 s a tutta la sessione, ancorato all’ultimo campione; doppio clic ripristina; ferma o sugli assi non agisce');
  }
  zoom(w,g);assert(a.zoom);assert.equal(a.frozen,true);
  const width=a.domain.xmax-a.domain.xmin,axes=JSON.stringify(a.domain.axes);
  $('global-live-zoom').click();
  await wait(()=>$('global-live-zoom').getAttribute('aria-pressed')==='true','dedicated Zoom live button enabled');
  assert.equal(a.frozen,false);close(a.domain.xmax-a.domain.xmin,width);assert.equal(JSON.stringify(a.domain.axes),axes);
  close(a.domain.xmax,end);
  frame(end+2000);
  assert.equal(a.frozen,false);close(a.domain.xmax,end);close(a.domain.xmax-a.domain.xmin,width);
  assert.equal(JSON.stringify(a.domain.axes),axes);close(a.series[ids[0]].at(-1)[0],end);
  checks.push('Default zoom pauses; enabling Zoom live resumes the existing XY zoom and advances the time window without changing either Y scale');

  frame(end+120000,true);
  assert(a.zoom);assert.equal(a.frozen,false);close(a.domain.xmax,end);
  close(a.domain.xmax-a.domain.xmin,width);assert.equal(JSON.stringify(a.domain.axes),axes);
  assert.equal(a.axisPlan.side.get(a.axisPlan.axisById.get(ids[1])),'right');
  zoom(w,g);
  assert.equal(a.frozen,false);const narrower=a.domain.xmax-a.domain.xmin,newAxes=JSON.stringify(a.domain.axes);
  close(narrower,width/2);frame(end+2000,true);
  close(a.domain.xmax-a.domain.xmin,narrower);assert.equal(JSON.stringify(a.domain.axes),newAxes);
  checks.push('Repeated rectangle zoom stays live and retains XY limits after buffer eviction and large changes in signal magnitude');

  $('global-pause').click();assert.equal(a.frozen,true);assert.equal(a.manualPaused,true);
  const held={xmin:a.domain.xmin,xmax:a.domain.xmax,last:a.series[ids[0]].at(-1)[0]};
  frame(end+3000,true);
  close(a.domain.xmin,held.xmin);close(a.domain.xmax,held.xmax);close(a.series[ids[0]].at(-1)[0],held.last);
  $('global-pause').click();assert.equal(a.frozen,false);assert(a.zoom);
  close(a.domain.xmax-a.domain.xmin,narrower);close(a.domain.xmax,end);assert.equal(JSON.stringify(a.domain.axes),newAxes);
  checks.push('Manual Pausa freezes timestamps and data; Play resumes live updates while preserving the XY zoom');
  {
   const lay=a.layout,e=new w.WheelEvent('wheel',{bubbles:true,cancelable:true,clientX:lay.p.l+100,clientY:lay.p.t+40,deltaY:100});
   a.canvas.dispatchEvent(e);assert(e.defaultPrevented);
   const grown=a.domain.xmax-a.domain.xmin;close(grown,10000);
   frame(end+1000);close(a.domain.xmax-a.domain.xmin,grown);close(a.domain.xmax,end);
   checks.push('Con Zoom live la rotella cambia il periodo seguito dalla finestra (minimo 10 s, massimo la durata disponibile)');
  }

  ws.add();const other=ws.active;ws.assignTo(other,ids[0],true);ws.assignTo(other,ids[1],true);
  assert.equal(other.trend.liveZoom,true);
  ws.activate(g);zoom(w,g);frame(end+2000,true);
  assert.equal(a.frozen,false);assert.equal(other.trend.frozen,false);
  close(other.trend.domain.xmin,a.domain.xmin);close(other.trend.domain.xmax,a.domain.xmax);
  const syncedSpan=a.domain.xmax-a.domain.xmin;
  ws.activate(other);$('global-pause').click();const heldOther=other.trend.domain.xmax;
  ws.activate(g);zoom(w,g);frame(end+2000,true);
  close(other.trend.domain.xmax,heldOther);close(a.domain.xmax,end);
  ws.activate(other);
  $('global-pause').click();close(other.trend.domain.xmax,end);
  close(other.trend.domain.xmax-other.trend.domain.xmin,syncedSpan);
  checks.push('New charts inherit Zoom live; synchronized charts follow the same window while an explicitly paused chart stays frozen');

  $('live-sync-zoom').checked=false;$('live-sync-zoom').dispatchEvent(new w.Event('change'));
  const firstSpan=a.domain.xmax-a.domain.xmin;
  zoom(w,other);const otherSpan=other.trend.domain.xmax-other.trend.domain.xmin;
  frame(end+2000,true);close(a.domain.xmax-a.domain.xmin,firstSpan);
  close(other.trend.domain.xmax-other.trend.domain.xmin,otherSpan);assert(otherSpan<firstSpan);
  const nav=other.element.querySelector('.navigator');
  nav.querySelector('[data-edge="start"]').dispatchEvent(new w.KeyboardEvent('keydown',{key:'ArrowRight',bubbles:true}));
  assert.equal(other.trend.frozen,false);assert(other.trend.domain.xmax-other.trend.domain.xmin<otherSpan);
  const slider=other.element.querySelector('[data-chart-role="timeScroll"]');slider.value='250';slider.dispatchEvent(new w.Event('input'));
  assert.equal(other.trend.frozen,false);close(other.trend.domain.xmax,end);
  checks.push('Independent charts retain their own zoom widths; navigator and time scrollbar also respect Zoom live');

  $('global-live-zoom').click();assert.equal(ws.liveZoom,false);assert(a.frozen);assert(other.trend.frozen);
  $('global-live-zoom').click();assert.equal(ws.liveZoom,true);assert.equal(a.frozen,false);assert.equal(other.trend.frozen,false);
  const beforeReset=other.trend.zoom;assert(beforeReset);
  $('global-reset').click();assert.equal(other.trend.zoom,null);assert.equal(other.trend.linkedRange,null);
  assert.equal(ws.liveZoom,true);assert.equal(other.trend.frozen,false);
  checks.push('Disabling Zoom live restores zoom pause; reset returns to autoscale while retaining the selected live policy');

  for(const [lang,label] of [['en','Live zoom'],['de','Live-Zoom'],['fr','Zoom en direct'],['es','Zoom en vivo'],['it','Zoom live']]){
   await w.I18N.setLanguage(lang,{persist:true});assert.equal($('global-live-zoom').textContent,label);
  }
  t.showAppPage('recordings');await wait(()=>$('global-live-zoom').disabled,'Zoom live disabled for historical recordings');
  assert.equal(t.historyCharts.liveZoom,false);
  t.showAppPage('live');await wait(()=>!$('global-live-zoom').disabled,'Zoom live available in LIVE');
  await t.saveWorkspace(true);assert.equal((await api('/api/config')).workspace.live.liveZoom,true);
  const restored=await page();restored.t.stopPolling();
  assert.equal(restored.t.liveCharts.liveZoom,true);assert.equal(restored.t.historyCharts.liveZoom,false);
  await wait(()=>restored.$('global-live-zoom').getAttribute('aria-pressed')==='true','Zoom live restored after page reload');
  checks.push('Five languages, historical-view isolation and persisted Zoom live preference survive a real page reload');
  assert.deepEqual(errors,[]);console.log(JSON.stringify({result:'PASS',checks,files:[]}));
 }finally{t.stopPolling();for(const page of pages)page.window.close();}
})().then(()=>process.exit(0)).catch(error=>{console.error(error);process.exit(1);});

