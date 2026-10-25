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
  t.showAppPage('recordings');await wait(()=>$('history-file-list').textContent.includes('fixture_A.csv'),'recording list');
  const file=(await t.request('/api/recordings')).files.find(f=>f.name==='fixture_A.csv');
  t.selectHistoryFile(file);await t.loadHistory();const g=hist.active,bar=g.actionToolbar;
  const originals=Array.from(bar.actions,a=>a.button);
  function width(b,budget){
   b.testBudget=budget;
   if(!b.sized){
    Object.defineProperty(b.rail,'clientWidth',{configurable:true,get:()=>b.testBudget-(b.menu.hidden?0:39)});
    b.menu.getBoundingClientRect=()=>({width:34});
    for(const a of b.actions)a.measure.getBoundingClientRect=()=>({width:a.label.length*6+14});
    b.sized=true;
   }
   b.layout();
  }
  // Wide chart: only the frequent actions are buttons in the header; the others stay in the ⋮ menu.
  const primary=bar.actions.filter(a=>bar.primary.has(a.key)).length;assert(primary>0&&primary<bar.actions.length);
  width(bar,2000);assert.equal(bar.rail.children.length,primary);assert.equal(bar.menu.hidden,false);
  assert.deepEqual([...bar.rail.children,...bar.items.children],originals);assert.equal(bar.items.children.length,bar.actions.length-primary);
  checks.push('Wide chart shows the frequent actions in the header and keeps the others in the menu');
  width(bar,330);const count=bar.rail.children.length;
  assert(count>0&&count<bar.actions.length);assert.equal(bar.menu.hidden,false);
  assert.equal(bar.items.children.length,bar.actions.length-count);
  assert.deepEqual([...bar.rail.children,...bar.items.children],originals);
  const reset=role(g,'resetZoom');assert.equal(reset.disabled,true);
  sharedZoom(hist,g,1700000010000,1700000015000);assert.equal(reset.disabled,false);
  reset.click();assert.equal(g.trend.zoom,null);assert.equal(reset.disabled,true);
  checks.push('Shrink moves remaining controls into the overflow menu, preserving identity, listeners and disabled states');
  width(bar,70);assert.equal(bar.rail.children.length,0);assert.equal(bar.items.children.length,bar.actions.length);
  bar.menu.open=true;bar.actions.find(a=>a.key==='axes').button.click();assert.equal(bar.menu.open,false);
  assert.equal($('properties-title').textContent,'Assi · '+g.name);$('properties-close').click();
  width(bar,2000);assert.equal(bar.rail.children.length,primary);
  assert.deepEqual([...bar.rail.children,...bar.items.children],originals);
  checks.push('All controls remain accessible in narrow charts and return to the toolbar when enlarged');
  const other=w.duplicateStudioChart(hist,g);other.name='Grafico indipendente';role(other,'title').value=other.name;
  width(other.actionToolbar,270);assert(other.actionToolbar.items.children.length>0);
  hist.activate(g);bar.actions.find(a=>a.key==='axes').button.click();assert.equal($('properties-title').textContent,'Assi · '+g.name);$('properties-close').click();
  const o=other.actionToolbar;o.actions.find(a=>a.key==='axes').button.click();assert.equal($('properties-title').textContent,'Assi · '+other.name);$('properties-close').click();
  width(o,2000);o.actions.find(a=>a.key==='rename').button.click();assert.equal(w.document.activeElement,role(other,'title'));
  const before=hist.charts.length;o.actions.find(a=>a.key==='duplicate').button.click();assert.equal(hist.charts.length,before+1);
  const copy=hist.active;assert.equal(copy.name,other.name+' · copia');assert(copy.actionToolbar);
  o.actions.find(a=>a.key==='closeFile').button.click();assert.equal(other.context.file,null);assert(g.context.file);
  const removed=copy.actionToolbar;hist.remove(copy);assert(removed.destroyed);
  checks.push('Each chart owns its commands; duplicate, rename, close and observer cleanup work independently');
  const live=t.liveCharts.active;width(live.actionToolbar,2000);assert(live.actionToolbar.rail.contains(role(live,'resetZoom')));assert(live.actionToolbar.items.contains(role(live,'clear')));
  assert(live.element.querySelector('[data-chart-role=pausePlay]'));
  const allIds=[...w.document.querySelectorAll('[id]')].map(e=>e.id);assert.equal(new Set(allIds).size,allIds.length);
  assert.equal(errors.length,0,errors.join('; '));console.log(JSON.stringify({result:'PASS',checks,files:[]}));
 }finally{for(const p of pages)p.window.close();}
})().then(()=>process.exit(0)).catch(e=>{console.error(e.stack);process.exit(1);});
