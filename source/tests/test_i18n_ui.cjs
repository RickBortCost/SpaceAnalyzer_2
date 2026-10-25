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

async function page(blockStorage=false){
  const html=await (await fetch(base)).text();
  const vc=new VirtualConsole();vc.on('jsdomError',e=>errors.push(e.message));
  const dom=new JSDOM(html,{url:base,runScripts:'outside-only',pretendToBeVisual:true,virtualConsole:vc});
  pages.push(dom);const w=dom.window;if(blockStorage)Object.defineProperty(w,'localStorage',{get(){throw new Error('storage disabled')}});w.FormData=globalThis.FormData;
  w.AbortController=globalThis.AbortController;
  w.fetch=async(url,options)=>{try{return await fetch(new URL(url,base),options);}catch(e){errors.push('fetch '+url+': '+e.message);throw e;}};
  w.ResizeObserver=class{observe(){} disconnect(){}};
  w.HTMLCanvasElement.prototype.getBoundingClientRect=()=>({x:0,y:0,left:0,top:0,width:950,height:560,right:950,bottom:560});
  const ctx=new Proxy({measureText:text=>({width:String(text).length*7})},{get:(obj,key)=>key in obj?obj[key]:(()=>{})});
  w.HTMLCanvasElement.prototype.getContext=()=>ctx;
  w.addEventListener('error',e=>errors.push(e.message));
  w.eval(['i18n-catalog.js','i18n.js','trend.js','workspace.js','app.js','history.js','advanced.js','fft-ui.js','source.js','s7.js','calculations.js','studio.js','ssh.js','live-extras.js'].map(file=>fs.readFileSync(path.join(__dirname,'../static',file),'utf8')).join('\n')+'\nwindow.uiTest={get trend(){return liveCharts.charts[0].trend},get selected(){return selected},get record(){return recordState},get connected(){return connected},get liveCharts(){return liveCharts},get historyCharts(){return historyCharts},selectHistoryFile,loadHistory,importHistoryLocalFile,openSignalProperties,openAxisProperties,duplicateStudioChart,setNavigatorRange,dropHistorySignal,switchSource,saveWorkspace,chartSnapshot,config,stopPolling(){polling=false;clearTimeout(pollTimer);clearTimeout(workspaceTimer);}};');
  const $=id=>w.document.getElementById(id);
  try{await wait(()=>$('endpoint').value,'configuration loaded');}catch(e){console.error('UI notice:', $('msg').textContent);throw e;}
  await w.opcuaConfigReady;return {dom,w,$,t:w.uiTest};
}

const role=(g,r)=>g.element.querySelector('[data-chart-role="'+r+'"]');
function pointer(w,g,type,x,y){const target=type==='up'&&!w.PointerEvent?w:g.trend.canvas;target.dispatchEvent(new (w.PointerEvent||w.MouseEvent)((w.PointerEvent?'pointer':'mouse')+type,{bubbles:true,clientX:x,clientY:y,button:0,pointerId:1}));}
(async()=>{
 const checks=[];let {w,$,t}=await page();
 try{
  const selector=$('ui-language');assert(selector);assert(selector.closest('.app-bar'));
  assert(selector.closest('.language-control').previousElementSibling.matches('.app-menu'));
  assert.deepEqual(Array.from(selector.options,o=>o.value),['en','it','de','fr','es']);
  const names={
   en:['Recordings','Connection','New recording','Apply','Name / alias'],
   it:['Registrazioni','Connessione','Nuova registrazione','Applica','Nome / alias'],
   de:['Aufzeichnungen','Verbindung','Neue Aufzeichnung','Anwenden','Name / Alias'],
   fr:['Enregistrements','Connexion','Nouvel enregistrement','Appliquer','Nom / alias'],
   es:['Grabaciones','Conexión','Nueva grabación','Aplicar','Nombre / alias']
  };
  async function language(code){
   const current=$('ui-language');current.value=code;current.dispatchEvent(new w.Event('change',{bubbles:true}));
   await w.I18N.whenSaved();await new Promise(resolve=>w.requestAnimationFrame(resolve));
   assert.equal(w.document.documentElement.lang,code);assert.equal(current.value,code);
   assert.equal($('nav-recordings').textContent,names[code][0]);assert.equal($('nav-connection').textContent,names[code][1]);
   assert.equal($('record-title').textContent,names[code][2]);assert.equal($('properties-form').querySelector('button[type="submit"]').textContent,names[code][3]);
   assert.equal((await api('/api/config')).ui_language,code);
  }
  for(const code of ['en','de','fr','es','it'])await language(code);
  checks.push('Five-language selector next to Settings; immediate navigation, modal, action and document-lang updates; language persisted through API');
  t.openAxisProperties(t.liveCharts,t.liveCharts.active);
  const min=$('axis-left-min'),unit=$('axis-left-unit');min.value='-123.4';unit.value='Impostazioni';
  await language('de');assert.equal($('axis-left-min'),min);assert.equal(min.value,'-123.4');assert.equal(unit.value,'Impostazioni');
  assert.equal($('properties-title').textContent,'Achsen · '+t.liveCharts.active.name);
  for(let i=0;i<2100;i++)w.I18N.join([{text:'Segnale · '},{value:i}]);
  assert.equal(w.document.querySelector('label[for="axis-left-mode"]').textContent,'Skalierung');
  await language('fr');assert.equal(min.value,'-123.4');assert.equal(w.document.querySelector('label[for="axis-left-mode"]').textContent,'Échelle');assert.equal($('properties-title').textContent,'Axes · '+t.liveCharts.active.name);
  w.sshAppend('Nome');await language('es');assert.equal($('ssh-output').textContent,'Nome');
  $('properties-close').click();
  checks.push('Open property dialogs translate without replacing inputs, losing unsaved edits or changing operator data');
  $('nav-connection').click();$('endpoint').value=endpoint;$('security').value='None';$('sample_ms').value='10';$('root_nodeid').value=rootNode;
  $('connect').click();await wait(()=>t.connected,'OPC UA connected');
  for(const id of ids){$('manual-node').value=id;$('add-node').click();await wait(()=>t.selected.has(id)&&!$('add-node').disabled,'signal selected');}
  t.selected.get(ids[0]).display_name='Nome';t.liveCharts.active.name='Impostazioni';w.renderSelected();
  await wait(()=>t.trend.series[ids[0]]?.length>20,'live samples');
  const graph=t.liveCharts.active,toolbar=graph.actionToolbar,originals=Array.from(toolbar.actions,a=>a.button);
  let budget=370;Object.defineProperty(toolbar.rail,'clientWidth',{configurable:true,get:()=>budget-(toolbar.menu.hidden?0:39)});
  toolbar.menu.getBoundingClientRect=()=>({width:34});for(const action of toolbar.actions)action.measure.getBoundingClientRect=()=>({width:action.label.length*7+14});
  for(const code of ['en','de','fr','es','it']){
   await language(code);toolbar.layout();assert(toolbar.rail.children.length<toolbar.actions.length);assert(!toolbar.menu.hidden);
   assert.deepEqual([...toolbar.rail.children,...toolbar.items.children],originals);
   assert.equal(t.selected.get(ids[0]).display_name,'Nome');assert.equal(graph.name,'Impostazioni');
  }
  budget=3000;toolbar.layout();assert.equal(toolbar.rail.children.length,toolbar.actions.filter(a=>toolbar.primary.has(a.key)).length);assert.deepEqual([...toolbar.rail.children,...toolbar.items.children],originals);
  checks.push('Live data and raw signal/chart names survive all languages; translated toolbar measures overflow and preserves the same controls/listeners');
  await language('en');$('record').click();await wait(()=>!$('record-modal').hidden,'record popup');
  const radio=$('record-form').querySelector('input[name="record-format"][value="parquet"]');radio.checked=true;
  $('record-form').dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));
  await wait(()=>t.record.active,'record started');await wait(()=>t.record.cycles_received>25,'samples recorded');
  const before=t.record.cycles_received;await language('de');await wait(()=>t.record.cycles_received>before+20,'record continues through language change');
  await language('es');$('record-stop').click();await wait(()=>t.record.state==='stopped','record finalized');
  assert.equal(t.record.error,'');const file=(await api('/api/recordings')).files.find(f=>f.name===t.record.files[0].name);
  assert(file);const data=await w.request('/api/recordings/data',{name:file.name});assert(data.recording.numeric_rows>=90);
  t.selectHistoryFile(file);await t.loadHistory();assert(t.historyCharts.active.trend.domain);
  checks.push('Acquisition and Parquet Record continue through language changes; finalized file reopens with original signal IDs and values');
  $('disconnect').click();await wait(()=>!t.connected&&!$('data-source').disabled,'disconnected');
  await t.switchSource('mqtt');await language('fr');assert.equal(w.document.querySelector('label[for="mqtt_host"]').textContent,'Courtier · IP ou nom d’hôte');
  await t.switchSource('s7');assert.equal(w.document.querySelector('label[for="s7_mode"]').textContent,'Mode d’acquisition');
  assert.equal($('ssh-run').textContent,'Exécuter sur la cible');
  await t.saveWorkspace(true);const saved=await api('/api/config');assert.equal(saved.workspace.config.ui_language,'fr');
  t.stopPolling();w.close();({w,$,t}=await page(true));
  assert.equal(w.I18N.language,'fr');assert.equal($('nav-recordings').textContent,'Enregistrements');assert.equal($('ui-language').value,'fr');
  assert.deepEqual(errors,[]);checks.push('MQTT, S7 and SSH labels translate; workspace persists language and restores it even when localStorage is disabled');
  console.log(JSON.stringify({result:'PASS',checks,files:[]},null,2));
 }finally{for(const dom of pages){dom.window.uiTest?.stopPolling();dom.window.close();}}
})().then(()=>process.exit(0)).catch(e=>{console.error(e.stack);process.exit(1);});
