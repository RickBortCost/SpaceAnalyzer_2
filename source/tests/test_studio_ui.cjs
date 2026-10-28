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
  w.eval(['i18n-catalog.js','i18n.js','trend.js','workspace.js','app.js','history.js','advanced.js','fft-ui.js','source.js','s7.js','calculations.js','studio.js','ssh.js','live-extras.js'].map(file=>fs.readFileSync(path.join(__dirname,'../static',file),'utf8')).join('\n')+'\nwindow.uiTest={get trend(){return liveCharts.charts[0].trend},get selected(){return selected},get record(){return recordState},get connected(){return connected},get liveCharts(){return liveCharts},get historyCharts(){return historyCharts},selectHistoryFile,loadHistory,importHistoryLocalFile,openSignalProperties,openAxisProperties,duplicateStudioChart,setNavigatorRange,dropHistorySignal};');
  const $=id=>w.document.getElementById(id);
  try{await wait(()=>$('endpoint').value,'configuration loaded');}catch(e){console.error('UI notice:', $('msg').textContent);throw e;}
  await w.opcuaConfigReady;return {dom,w,$,t:w.uiTest};
}

const role=(g,r)=>g.element.querySelector('[data-chart-role="'+r+'"]');
function pointer(w,g,type,x,y){const target=type==='up'&&!w.PointerEvent?w:g.trend.canvas;target.dispatchEvent(new (w.PointerEvent||w.MouseEvent)((w.PointerEvent?'pointer':'mouse')+type,{bubbles:true,clientX:x,clientY:y,button:0,pointerId:1}));}
(async()=>{
 const {w,$,t}=await page(),checks=[];
 const change=el=>el.dispatchEvent(new w.Event('change',{bubbles:true}));
 const submit=()=>$('properties-form').dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));
 try{
  $('endpoint').value=endpoint;$('security').value='None';$('sample_ms').value='10';$('root_nodeid').value=rootNode;
  $('connect').click();await wait(()=>t.connected,'connection');
  for(const id of ids){$('manual-node').value=id;$('add-node').click();await wait(()=>t.selected.has(id)&&!$('add-node').disabled,'selected');}
  await wait(()=>t.trend.domain&&t.trend.series[ids[0]].length>30,'numeric data');
  const live=t.liveCharts,g1=live.active;
  assert.ok($('record').closest('header'));assert.ok($('nav-connection').closest('.page-tabs'));assert.equal($('nav-connection').closest('details'),null);
  assert.equal($('selected').querySelectorAll('.signal-properties').length,2);
  for(const [mode,color] of [['dark','#CCC3BE'],['light','#707E86'],['system','#707E86']]){$('theme-mode').value=mode;change($('theme-mode'));assert.equal(g1.trend.theme.text,color);}
  checks.push('corporate standard palette, Light/Dark/System, canvas colours and compact signal properties');
  t.openSignalProperties(live,g1,ids[0]);$('prop-alias').value='Pressione Top';$('prop-unit').value='bar';$('prop-axis').value='left';$('prop-color').value='#08aa99';$('prop-width').value='3';submit();
  assert.equal(g1.trend.nodes.get(ids[0]).display_name,'Pressione Top');assert.equal(g1.trend.color(ids[0]),'#08aa99');assert.equal(g1.trend.presentation[ids[0]].width,3);assert.ok($('selected').textContent.includes('Pressione Top'));
  const k0=g1.trend.axisPlan.axisById.get(ids[0]);t.openAxisProperties(live,g1);$('axis-'+k0+'-mode').value='manual';$('axis-'+k0+'-min').value='0';$('axis-'+k0+'-max').value='200';$('axis-'+k0+'-unit').value='bar';submit();assert.equal(g1.trend.domain.axes[k0].ymin,0);assert.equal(g1.trend.domain.axes[k0].ymax,200);assert.match(g1.trend.axisLabel(k0),/bar.*Manuale/);
  t.openAxisProperties(live,g1);$('axis-'+k0+'-min').value='500';submit();assert.equal($('properties-error').hidden,false);assert.equal(g1.axisSettings[k0].min,0);$('properties-close').click();
  checks.push('alias, units, color, line width, Y assignment and validated manual axes');
  const g2=t.duplicateStudioChart(live,g1);assert.equal(g2.ids.size,2);g2.signalSettings[ids[0]].alias='Copia';assert.equal(g1.signalSettings[ids[0]].alias,'Pressione Top');
  $('live-layout').value='quad';change($('live-layout'));assert.equal(live.charts.length,4);assert.equal(live.charts[2].ids.size,0);assert.equal(live.charts[3].ids.size,0);
  $('live-layout').value='single';change($('live-layout'));assert.equal(live.charts.length,4);live.activate(g1);assert.ok(live.root.classList.contains('layout-single'));
  $('live-layout').value='horizontal';change($('live-layout'));checks.push('independent duplicate and nondestructive layout presets');
  const nav=g1.element.querySelector('.navigator'),win=nav.querySelector('.navigator-window');nav.getBoundingClientRect=()=>({left:0,width:800});
  const navEvent=(target,type,x)=>target.dispatchEvent(new w.MouseEvent(type,{bubbles:true,cancelable:true,clientX:x,button:0}));
  const full={...g1.trend.fullDomain};navEvent(nav,'pointerdown',400);navEvent(nav,'pointerup',400);assert.ok(g1.trend.frozen);assert.ok(g1.trend.domain.xmax-g1.trend.domain.xmin<full.xmax-full.xmin);assert.equal(g1.trend.domain.xmin,g2.trend.domain.xmin);
  const before=g1.trend.domain.xmin;navEvent(win.querySelector('.start'),'pointerdown',300);navEvent(nav,'pointermove',340);navEvent(nav,'pointerup',340);assert.ok(g1.trend.domain.xmin>before);
  const width=g1.trend.domain.xmax-g1.trend.domain.xmin;win.dispatchEvent(new w.KeyboardEvent('keydown',{key:'ArrowRight',bubbles:true}));assert.ok(Math.abs(g1.trend.domain.xmax-g1.trend.domain.xmin-width)<.1);assert.equal(g2.trend.domain.xmin,g1.trend.domain.xmin);
  checks.push('navigator jump, edge resize, keyboard pan and synchronized freeze');
  const d=g1.trend.domain,old1=d.xmin+(d.xmax-d.xmin)*.25;live.setMarkers({x1:old1,x2:d.xmin+(d.xmax-d.xmin)*.75});const l=g1.trend.layout,x=l.p.l+.25*(l.w-l.p.l-l.p.r);
  pointer(w,g1,'down',x,80);pointer(w,g1,'move',x+40,80);pointer(w,g1,'up',x+40,80);assert.ok(live.markers.x1>old1);assert.equal(g2.trend.markers.x1,live.markers.x1);assert.equal(g1.trend.drag,null);
  const analysis=live.dock;analysis.open=true;analysis.dispatchEvent(new w.Event('toggle'));assert.match(analysis.textContent,/Δt/);assert.ok(analysis.contains(g1.markerPanel),'il pannello del grafico attivo è nel dock');assert.equal(g1.element.querySelector('.marker-analysis'),null);checks.push('draggable synchronized markers and general analysis dock');
  const resize=g1.element.querySelector('.chart-resizer');resize.dispatchEvent(new w.KeyboardEvent('keydown',{key:'ArrowDown',bubbles:true}));assert.ok(parseFloat(g1.element.querySelector('.chartwrap').style.height)>240);
  resize.dispatchEvent(new w.MouseEvent('dblclick',{bubbles:true}));assert.equal(g1.element.querySelector('.chartwrap').style.height,'');
  role(g1,'expand').click();assert.ok(w.document.body.classList.contains('analysis-fullscreen'));w.document.dispatchEvent(new w.KeyboardEvent('keydown',{key:'Escape'}));assert.ok(!w.document.body.classList.contains('analysis-fullscreen'));
  $('live-sidebar-toggle').click();assert.ok($('live-page').classList.contains('sidebar-collapsed'));const divider=live.root.querySelector('.grid-resizer');divider.dispatchEvent(new w.KeyboardEvent('keydown',{key:'ArrowLeft',bubbles:true}));assert.ok(live.gridRatio<.5);checks.push('resize/reset, grid divider, sidebar and fullscreen Escape');
  $('theme-mode').value='dark';change($('theme-mode'));g1.element.querySelector('.chartwrap').style.height='410px';
  t.openSignalProperties(live,g1,ids[1]);$('prop-visible').value='no';submit();if(!g1.trend.frozen)g1.trend.togglePause();const view=JSON.stringify(g1.trend.series);
  await w.saveWorkspace(true);const next=await page(),n=next.t.liveCharts.charts[0];assert.equal(next.w.document.documentElement.dataset.theme,'dark');assert.ok(next.$('live-page').classList.contains('sidebar-collapsed'));assert.equal(n.signalSettings[ids[0]].alias,'Pressione Top');assert.equal(n.axisSettings[k0].max,200);assert.ok(n.trend.frozen);assert.equal(n.trend.nodes.has(ids[1]),false);assert.equal(JSON.stringify(n.trend.series),view);assert.equal(next.t.liveCharts.dock.open,true);assert.equal(next.t.liveCharts.gridRatio,live.gridRatio);checks.push('durable theme, axes, aliases, hidden signals, paused snapshot and panels');
  {const menu=w.document.querySelector('.app-bar .app-menu'),panel=menu.querySelector('.detail-content');menu.open=true;menu.dispatchEvent(new w.Event('toggle'));assert.ok(panel.style.top&&panel.style.right,'il menu Impostazioni ha coordinate proprie (non ritagliato dalla barra)');
   w.document.body.dispatchEvent(new w.MouseEvent('click',{bubbles:true}));assert.equal(menu.open,false,'clic fuori chiude il menu');menu.open=true;w.document.dispatchEvent(new w.KeyboardEvent('keydown',{key:'Escape',bubbles:true}));assert.equal(menu.open,false,'Esc chiude il menu');
   checks.push('menu Impostazioni posizionato fuori dal ritaglio della barra, chiuso da clic esterno ed Esc');}
  {w.eval('notice')('Errore di prova',true);assert.equal($('msg').hidden,false);$('msg').click();assert.equal($('msg').hidden,true,'un messaggio di errore si chiude con un clic');checks.push('i messaggi di errore si chiudono con un clic');}
  $('nav-recordings').click();await wait(()=>$('history-file-list').textContent.includes('fixture_A.csv'),'history repository');[...$('history-file-list').querySelectorAll('button')].find(b=>b.textContent.includes('fixture_A.csv')).click();$('history-load').click();await wait(()=>t.historyCharts.active.context.loaded,'history loaded');const hist=t.historyCharts,h1=hist.active;
  $('history-add-chart').click();const h2=hist.active;hist.activate(h1);assert.equal($('history-signal-list').querySelector('.history-signal-row').draggable,true);
  const drop=new w.Event('drop',{bubbles:true,cancelable:true});Object.defineProperty(drop,'dataTransfer',{value:{getData:type=>type==='application/x-opcua-history'?JSON.stringify({graph:h1.id,id:ids[0]}):'',types:['application/x-opcua-history']}});h2.element.dispatchEvent(drop);await wait(()=>h2.context?.loaded,'history DND loaded');assert.equal(h2.ids.size,1);assert.equal(h1.ids.size,2);checks.push('recording viewer independent signal DND');
  const allIds=[...w.document.querySelectorAll('[id]')].map(e=>e.id);assert.equal(new Set(allIds).size,allIds.length);assert.equal(errors.length,0,errors.join('\n'));
  console.log(JSON.stringify({result:'PASS',checks,files:[]}));
 }finally{for(const p of pages)p.window.close();}
})().then(()=>process.exit(0)).catch(e=>{console.error(e.stack);process.exit(1);});
