/* InfluxDB 2.x (sola lettura): connessione, elenco automatico di bucket, misure e serie, periodo a scelta e grafici con riduzione automatica dei dati. */
const IFX={connected:false,info:null,bucket:'',series:[],byKey:new Map(),preset:3600,settingsLoaded:false,refreshTimer:null,refineTimer:null,catalogBusy:false,truncated:false,filter:''};
const ifx=id=>document.getElementById(id);
const IFX_MAX_SERIES=64;
function ifxStore(patch){try{const saved=JSON.parse(localStorage.getItem('space-analyzer.influx')||'{}');localStorage.setItem('space-analyzer.influx',JSON.stringify({...saved,...patch}));}catch{}}
function ifxStored(){try{return JSON.parse(localStorage.getItem('space-analyzer.influx')||'{}');}catch{return {};}}
// I grafici di questa pagina usano lo stesso elemento dei grafici di REGISTRAZIONI, con identificatori propri.
function influxChartElement(){
  const element=historyCharts.template.cloneNode(true),map=new Map();
  for(const node of [element,...element.querySelectorAll('[id]')])if(node.id){const next='influx-'+node.id.replace(/^history-/,'');map.set(node.id,next);node.id=next;}
  for(const node of element.querySelectorAll('[for],[aria-labelledby],[aria-describedby]'))for(const attr of ['for','aria-labelledby','aria-describedby'])if(node.hasAttribute(attr))node.setAttribute(attr,node.getAttribute(attr).split(' ').map(v=>map.get(v)||v).join(' '));
  const close=element.querySelector('[data-chart-role="closeFile"]');
  if(close){close.disabled=false;close.textContent=I18N.t('Svuota grafico');close.setAttribute('data-i18n','Svuota grafico');}
  return element;
}
const ifxCtx=(graph=influxCharts.active)=>graph.influx||(graph.influx={selected:new Set(),loaded:false,base:null,loading:false,revision:0});
const influxCharts=(()=>{
  ifx('influx-charts').append(influxChartElement());
  const workspace=new ChartWorkspace({scope:'influx',dock:'influx-analysis-dock',root:'influx-charts',selector:'influx-active-chart',add:'influx-add-chart',layout:'influx-layout',staticMode:true,exactMarkers:false,stateLabel:'INFLUX',closeLabel:'Svuota grafico',copySignalsOnAdd:false,
    onNew:graph=>{graph.influx=null;ifxCtx(graph);hookInfluxGraph(graph);},
    onActive:graph=>{ifxCtx(graph);renderInfluxSelection();renderInfluxStatus();graph.trend.draw();},
    onMarkers:()=>{},onClear:graph=>{const ctx=ifxCtx(graph);ctx.selected.clear();ctx.loaded=false;renderInfluxSelection();},
    onClose:graph=>closeInfluxGraph(graph)});
  return workspace;
})();
function closeInfluxGraph(graph=influxCharts.active){
  const ctx=ifxCtx(graph);ctx.selected.clear();ctx.loaded=false;ctx.base=null;ctx.revision++;
  graph.signalSettings={};graph.axisSettings={};influxCharts.setGraphData(graph,{},new Map(),{});renderInfluxSelection();renderInfluxStatus();
}
influxCharts.syncZoom=ifx('influx-sync-zoom').checked;influxCharts.syncHover=ifx('influx-sync-hover').checked;
ifx('influx-sync-zoom').addEventListener('change',e=>{influxCharts.syncZoom=e.target.checked;});
ifx('influx-sync-hover').addEventListener('change',e=>{influxCharts.syncHover=e.target.checked;});
function hookInfluxGraph(graph){
  const trend=graph.trend,original=trend.onViewChanged;
  trend.onViewChanged=range=>{original?.(range);scheduleInfluxRefine(graph,range);};
}
hookInfluxGraph(influxCharts.active);

function influxError(text){const box=ifx('influx-error');box.textContent=text||'';box.hidden=!text;}
function renderInfluxState(){
  const box=ifx('influx-state');
  if(!IFX.connected){box.textContent=I18N.t('Non connesso');box.className='influx-state';return;}
  box.textContent=I18N.join([{text:'InfluxDB '},{value:IFX.info?.version||''},{text:IFX.info?.org?' · '+IFX.info.org:''}]);box.className='influx-state ok';
}
function influxBucketList(){
  const show=ifx('influx-system').checked;
  return (IFX.info?.buckets||[]).filter(b=>show||b.type!=='system');
}
function renderInfluxBuckets(){
  const select=ifx('influx-bucket'),list=influxBucketList();select.replaceChildren();
  for(const b of list){const option=document.createElement('option');option.value=b.name;option.textContent=b.name+(b.type==='system'?' · '+I18N.t('sistema'):'');select.append(option);}
  select.disabled=!list.length;
  const wanted=IFX.bucket||ifxStored().bucket;
  select.value=list.some(b=>b.name===wanted)?wanted:(list[0]?.name||'');IFX.bucket=select.value;
}
async function connectInflux(){
  influxError('');ifx('influx-connect').disabled=true;
  try{
    const result=await request('/api/influx/connect',{url:ifx('influx-url').value.trim(),token:ifx('influx-token').value.trim(),org:ifx('influx-org').value.trim(),tls_insecure:ifx('influx-tls').checked});
    IFX.connected=true;IFX.info=result.connection;ifx('influx-token').value='';ifx('influx-url').value=IFX.info.url;ifx('influx-org').value=IFX.info.org||'';
    ifx('influx-token').placeholder=I18N.t('Token salvato (lascia vuoto per usarlo)');
    renderInfluxState();renderInfluxBuckets();ifx('influx-connection').open=false;
    setInfluxStatus(I18N.join([{text:'Connesso a InfluxDB '},{value:IFX.info.version||''},{text:' · '},{value:influxBucketList().length},{text:' bucket'}]));
    await loadInfluxCatalog(true);
  }catch(error){IFX.connected=false;renderInfluxState();influxError(error.message);ifx('influx-connection').open=true;}
  finally{ifx('influx-connect').disabled=false;}
}
async function forgetInflux(){
  try{await request('/api/influx/forget',{});}catch(error){influxError(error.message);return;}
  IFX.connected=false;IFX.info=null;IFX.series=[];IFX.byKey=new Map();ifx('influx-token').placeholder='API token (sola lettura)';ifx('influx-bucket').replaceChildren();ifx('influx-bucket').disabled=true;
  renderInfluxState();renderInfluxSeries();ifx('influx-connection').open=true;setInfluxStatus(I18N.t('Token dimenticato.'));
}
function setInfluxStatus(text){ifx('influx-status').textContent=text;}
async function loadInfluxCatalog(autoSelect=false){
  const bucket=ifx('influx-bucket').value;if(!IFX.connected||!bucket||IFX.catalogBusy)return;
  IFX.catalogBusy=true;IFX.bucket=bucket;ifxStore({bucket,lookback:ifx('influx-lookback').value});ifx('influx-list-status').textContent=I18N.t('Ricerca delle serie…');
  try{
    const result=await request('/api/influx/catalog',{bucket,lookback_s:Number(ifx('influx-lookback').value)});
    IFX.series=result.catalog.series||[];IFX.truncated=!!result.catalog.truncated;IFX.byKey=new Map(IFX.series.map(s=>[s.key,s]));
    renderInfluxSeries();
    if(autoSelect&&!ifxCtx().selected.size)await influxAutoSelect();
  }catch(error){ifx('influx-list-status').textContent=error.message;influxError(error.message);}
  finally{IFX.catalogBusy=false;}
}
// Prima vista automatica: fino a 4 serie numeriche della prima misura.
async function influxAutoSelect(){
  const numeric=IFX.series.filter(s=>s.data_type!=='String');if(!numeric.length)return;
  const first=numeric[0].measurement,pick=numeric.filter(s=>s.measurement===first).slice(0,4);
  const ctx=ifxCtx();for(const s of pick)ctx.selected.add(s.key);renderInfluxSelection();
  try{await loadInflux();}catch(error){influxError(error.message);}
}
function influxTagText(tags){return Object.entries(tags||{}).map(([k,v])=>k+'='+v).join(', ');}
function influxMatches(series,query){
  if(!query)return true;const text=(series.measurement+' '+series.field+' '+influxTagText(series.tags)+' '+series.data_type).toLowerCase();
  return query.toLowerCase().split(/\s+/).every(part=>text.includes(part));
}
function renderInfluxSeries(){
  const box=ifx('influx-series');box.replaceChildren();const query=ifx('influx-search').value.trim(),groups=new Map();
  for(const s of IFX.series){if(!influxMatches(s,query))continue;if(!groups.has(s.measurement))groups.set(s.measurement,[]);groups.get(s.measurement).push(s);}
  const selected=ifxCtx().selected;
  for(const [measurement,items] of groups){
    const group=document.createElement('details');group.className='influx-group';group.open=!!query||groups.size<=3;group.dataset.measurement=measurement;
    const summary=document.createElement('summary'),name=document.createElement('strong');name.textContent=measurement;
    const count=document.createElement('span');count.className='small';count.textContent=' · '+items.length;summary.append(name,count);group.append(summary);
    for(const s of items){
      const row=document.createElement('label');row.className='influx-series-row'+(s.data_type==='String'?' disabled':'');row.dataset.key=s.key;
      const input=document.createElement('input');input.type='checkbox';input.value=s.key;input.checked=selected.has(s.key);input.disabled=s.data_type==='String';
      const field=document.createElement('span');field.className='influx-field';field.textContent=s.field;
      const tags=document.createElement('span');tags.className='influx-tags small';tags.textContent=influxTagText(s.tags);
      const type=document.createElement('span');type.className='influx-type';type.textContent=s.data_type;
      if(s.data_type==='String')row.title=I18N.t('Le serie di testo non si possono disegnare.');
      row.append(input,field,tags,type);group.append(row);
    }
    box.append(group);
  }
  const total=IFX.series.length,shown=[...groups.values()].reduce((n,g)=>n+g.length,0);
  ifx('influx-list-status').textContent=total?I18N.join([{value:shown},{text:' / '},{value:total},{text:' serie'},{text:IFX.truncated?' · '+I18N.t('elenco limitato: restringi la ricerca'):''}]):(IFX.connected?I18N.t('Nessuna serie con dati nel periodo di ricerca.'):'');
}
function renderInfluxSelection(){
  const selected=ifxCtx().selected;
  for(const input of ifx('influx-series').querySelectorAll('input[type=checkbox]'))input.checked=selected.has(input.value);
}
ifx('influx-series').addEventListener('change',event=>{
  const input=event.target;if(!(input instanceof HTMLInputElement))return;const ctx=ifxCtx();
  if(input.checked){if(ctx.selected.size>=IFX_MAX_SERIES){input.checked=false;notice(I18N.join([{text:'Si possono aprire fino a '},{value:IFX_MAX_SERIES},{text:' serie per grafico.'}]),true);return;}ctx.selected.add(input.value);}
  else ctx.selected.delete(input.value);
});
ifx('influx-search').addEventListener('input',()=>renderInfluxSeries());
ifx('influx-clear-selection').addEventListener('click',()=>{ifxCtx().selected.clear();renderInfluxSelection();});

// ---- periodo ---------------------------------------------------------------------------------------------------------
function influxLatest(keys){let latest=0;for(const key of keys){const s=IFX.byKey.get(key);if(s&&Number.isFinite(s.last_ms))latest=Math.max(latest,s.last_ms);}return latest;}
/** Preset relativi: fino ad adesso se i dati sono recenti, altrimenti fino all'ultimo dato (serie ferme o storiche). */
function influxRange(keys){
  if(IFX.preset>0){const now=Date.now(),latest=influxLatest(keys),end=latest&&now-latest>300000?latest+1000:now;return {start:end-IFX.preset*1000,end};}
  const start=msFromLocalInput(ifx('influx-from').value),end=msFromLocalInput(ifx('influx-to').value);
  if(start===null||end===null)throw new Error(I18N.t('Indica inizio e fine del periodo.'));
  if(end<=start)throw new Error(I18N.t('La fine deve seguire l’inizio.'));
  return {start,end};
}
function setInfluxPreset(seconds,persist=true){
  IFX.preset=seconds;for(const b of ifx('influx-presets').querySelectorAll('button'))b.classList.toggle('active',Number(b.dataset.preset)===seconds);
  ifx('influx-custom').hidden=seconds!==0;
  if(seconds===0&&!ifx('influx-from').value){const end=Date.now();ifx('influx-to').value=localInputFromMs(end);ifx('influx-from').value=localInputFromMs(end-3600000);}
  if(persist)ifxStore({preset:seconds});
}
ifx('influx-presets').addEventListener('click',event=>{const b=event.target.closest('button[data-preset]');if(!b)return;setInfluxPreset(Number(b.dataset.preset));if(Number(b.dataset.preset)>0&&ifxCtx().selected.size)loadInflux().catch(e=>influxError(e.message));});

// ---- dati --------------------------------------------------------------------------------------------------------------
// Budget di punti per serie: più serie selezionate, meno punti ciascuna (massimo 100.000, come le registrazioni). Con pochi punti nel periodo arrivano tutti i dati originali.
function influxMaxPoints(graph){if(IFX.maxPoints)return IFX.maxPoints;const series=Math.max(1,ifxCtx(graph).selected.size);return Math.max(2000,Math.min(100000,Math.floor(300000/series)));}
async function loadInflux(graph=influxCharts.active,options={}){
  const ctx=ifxCtx(graph),keys=[...ctx.selected];if(!keys.length)throw new Error(I18N.t('Seleziona almeno una serie.'));
  const range=options.range||influxRange(keys),revision=++ctx.revision;
  ctx.loading=true;if(!options.quiet)setInfluxStatus(I18N.t('Lettura da InfluxDB…'));
  influxError('');
  try{
    const data=await request('/api/influx/samples',{series:keys.map(k=>IFX.byKey.get(k)).filter(Boolean),start_ms:range.start,end_ms:range.end,max_points:influxMaxPoints(graph),aggregate:ifx('influx-aggregate').value});
    if(!influxCharts.charts.includes(graph)||revision!==ctx.revision)return;
    const nodes=new Map((data.signals||[]).map(s=>[s.nodeid,{nodeid:s.nodeid,display_name:s.display_name||s.nodeid,unit:''}])),types=Object.fromEntries((data.signals||[]).map(s=>[s.nodeid,s.data_type||'Double']));
    if(options.quiet){graph.series=data.series||{};graph.nodes=nodes;graph.types=types;graph.ids=new Set(nodes.keys());influxCharts.updateGraph(graph);}
    else{if(!options.range)ctx.base=range;influxCharts.setGraphData(graph,data.series||{},nodes,types);}
    ctx.loaded=true;ctx.info=data;
    if(graph===influxCharts.active)renderInfluxStatus();
  }finally{ctx.loading=false;}
}
function renderInfluxStatus(){
  const ctx=ifxCtx(),data=ctx.info;if(!ctx.loaded||!data){return;}
  const returned=(data.signals||[]).reduce((n,s)=>n+Number(s.points_returned||0),0),original=(data.signals||[]).reduce((n,s)=>n+Number(s.points_original||0),0);
  const fmt=ms=>new Date(ms).toLocaleString(I18N.locale());
  const reduced=(data.signals||[]).find(s=>s.window_s>0),perSeries=Math.max(0,...(data.signals||[]).map(s=>Number(s.points_original||0))),spanMs=Number(data.end_ms)-Number(data.start_ms),nativeMs=perSeries>1&&spanMs>0?spanMs/(perSeries-1):0;
  setInfluxStatus(I18N.join([{value:(data.signals||[]).length},{text:' serie · '},{value:returned.toLocaleString(I18N.locale())},{text:' punti'},
    {text:reduced?' (da '+original.toLocaleString(I18N.locale())+', '+reduced.aggregate+' ogni '+influxWindowText(reduced.window_s)+')':' · '+I18N.t('tutti i punti')},{text:reduced&&nativeMs>0?' · dati originali ogni ~'+influxNativeText(nativeMs)+' (zoom per il dettaglio)':''},{text:' · '},{value:fmt(data.start_ms)},{text:' → '},{value:fmt(data.end_ms)}]));
}
function influxNativeText(ms){if(ms<1)return ms.toFixed(2)+' ms';if(ms<1000)return Math.round(ms)+' ms';return (ms/1000).toFixed(ms<10000?1:0)+' s';}
function influxWindowText(seconds){if(seconds<60)return seconds+' s';if(seconds<3600)return seconds/60+' min';if(seconds<86400)return seconds/3600+' h';return seconds/86400+' g';}
// Zoom sul grafico: i dati dell'intervallo visibile vengono riletti con più dettaglio; il ripristino torna al periodo scelto.
function scheduleInfluxRefine(graph,range){
  clearTimeout(IFX.refineTimer);
  IFX.refineTimer=setTimeout(()=>{
    const graphs=influxCharts.syncZoom?influxCharts.charts:[graph];
    for(const g of graphs){
      const ctx=ifxCtx(g);if(!ctx.loaded||!ctx.base||ctx.loading)continue;
      const target=range&&Number.isFinite(range.xmin)&&Number.isFinite(range.xmax)&&range.xmax>range.xmin?{start:Math.max(ctx.base.start,range.xmin),end:Math.min(ctx.base.end,range.xmax)}:ctx.base;
      if(!(target.end>target.start))continue;
      loadInflux(g,{range:target,quiet:true}).catch(e=>influxError(e.message));
    }
  },400);
}
function scheduleInfluxAutoRefresh(){
  clearInterval(IFX.refreshTimer);const seconds=Number(ifx('influx-refresh-every').value);ifxStore({refresh:seconds});if(!(seconds>0))return;
  IFX.refreshTimer=setInterval(()=>{
    if(window.currentAppPage!=='influx'||IFX.preset<=0)return;
    for(const g of influxCharts.charts){const ctx=ifxCtx(g);if(!ctx.loaded||ctx.loading||g.trend.zoom||g.trend.linkedRange||!ctx.selected.size)continue;
      const range=influxRange([...ctx.selected]);ctx.base=range;loadInflux(g,{range,quiet:true}).catch(e=>influxError(e.message));}
  },seconds*1000);
}
ifx('influx-refresh-every').addEventListener('change',scheduleInfluxAutoRefresh);
ifx('influx-aggregate').addEventListener('change',()=>{ifxStore({aggregate:ifx('influx-aggregate').value});if(ifxCtx().loaded)loadInflux().catch(e=>influxError(e.message));});
ifx('influx-system').addEventListener('change',()=>{renderInfluxBuckets();loadInfluxCatalog();});
ifx('influx-bucket').addEventListener('change',()=>{ifxCtx().selected.clear();loadInfluxCatalog(true);});
ifx('influx-lookback').addEventListener('change',()=>loadInfluxCatalog());
ifx('influx-refresh').addEventListener('click',()=>loadInfluxCatalog());
ifx('influx-connect').addEventListener('click',connectInflux);
ifx('influx-forget').addEventListener('click',forgetInflux);
ifx('influx-load').addEventListener('click',()=>loadInflux().catch(e=>{influxError(e.message);setInfluxStatus(e.message);}));
for(const id of ['influx-url','influx-token','influx-org'])ifx(id).addEventListener('keydown',event=>{if(event.key==='Enter'){event.preventDefault();connectInflux();}});

async function openInfluxPage(){
  const saved=ifxStored();
  if(!IFX.settingsLoaded){
    IFX.settingsLoaded=true;
    if(saved.lookback)ifx('influx-lookback').value=saved.lookback;if(saved.aggregate)ifx('influx-aggregate').value=saved.aggregate;
    if(Number.isFinite(saved.preset))setInfluxPreset(saved.preset,false);if(Number.isFinite(saved.refresh)){ifx('influx-refresh-every').value=String(saved.refresh);scheduleInfluxAutoRefresh();}
    try{
      const result=await request('/api/influx/settings');const s=result.settings||{};
      if(s.url)ifx('influx-url').value=s.url;ifx('influx-org').value=s.org||'';ifx('influx-tls').checked=!!s.tls_insecure;
      if(s.token_saved){ifx('influx-token').placeholder=I18N.t('Token salvato (lascia vuoto per usarlo)')+' '+s.token_hint;if(s.url)await connectInflux();}
    }catch(error){influxError(error.message);}
  }
  setTimeout(()=>influxCharts.draw(),0);
}
window.openInfluxPage=openInfluxPage;
ifx('nav-influx').onclick=()=>showAppPage('influx');
document.addEventListener('space-analyzer-languagechange',()=>{renderInfluxState();renderInfluxSeries();renderInfluxStatus();});
// ---- esportazione ----------------------------------------------------------------------------------------------------
// Finestra per esportare i dati originali delle serie del grafico attivo (periodo e formato a scelta, cartella locale); il lavoro gira nel servizio.
const IFX_EXPORT={job:null,timer:null};
const ifxExportMode=()=>document.querySelector('input[name="influx-export-period"]:checked')?.value||'loaded';
function influxExportBase(graph){const ctx=ifxCtx(graph);if(ctx.base)return ctx.base;return influxRange([...ctx.selected]);}
function influxExportVisible(graph){const d=graph?.trend?.domain;return d&&Number.isFinite(d.xmin)&&Number.isFinite(d.xmax)&&d.xmax>d.xmin?{start:d.xmin,end:d.xmax}:null;}
function influxExportRange(){
  const graph=influxCharts.active,mode=ifxExportMode();
  if(mode==='custom'){
    const start=msFromLocalInput(ifx('influx-export-from').value),end=msFromLocalInput(ifx('influx-export-to').value);
    if(start===null||end===null)throw new Error(I18N.t('Indica inizio e fine del periodo.'));
    if(end<=start)throw new Error(I18N.t('La fine deve seguire l’inizio.'));
    return {start,end};
  }
  if(mode==='visible'){const visible=influxExportVisible(graph);if(visible)return visible;}
  return influxExportBase(graph);
}
function influxExportRangeText(range){if(!range)return '';const fmt=ms=>new Date(ms).toLocaleString(I18N.locale());return '('+fmt(range.start)+' → '+fmt(range.end)+')';}
function renderInfluxExportInfo(){
  const graph=influxCharts.active,ctx=ifxCtx(graph);
  I18N.bind(ifx('influx-export-summary'),"textContent",()=>I18N.join([{value:ctx.selected.size},{text:' serie del grafico '},{value:graph.name}]),false);
  let base=null;try{base=influxExportBase(graph);}catch{}
  const visible=influxExportVisible(graph)||base;
  ifx('influx-export-loaded-range').textContent=influxExportRangeText(base);
  ifx('influx-export-visible-range').textContent=influxExportRangeText(visible);
  const dir=ifx('influx-export-directory');dir.placeholder=I18N.t('Cartella Conversioni delle registrazioni');
  I18N.bind(ifx('influx-export-hint'),"textContent",()=>I18N.t('Vengono scritti tutti i punti originali del periodo: con dati molto fitti il file può essere grande e la lettura richiedere tempo.'),true);
}
function openInfluxExport(){
  const graph=influxCharts.active,ctx=ifxCtx(graph);
  if(!ctx.selected.size){influxError(I18N.t('Seleziona almeno una serie.'));return;}
  influxError('');
  const saved=ifxStored().export||{};
  if(saved.format&&[...ifx('influx-export-format').options].some(o=>o.value===saved.format))ifx('influx-export-format').value=saved.format;
  ifx('influx-export-directory').value=saved.directory||'';
  if(!ifx('influx-export-name').value)ifx('influx-export-name').value='InfluxDB_'+(IFX.bucket||'').replace(/[^\w.-]+/g,'_');
  const end=Date.now();if(!ifx('influx-export-to').value){ifx('influx-export-to').value=localInputFromMs(end);ifx('influx-export-from').value=localInputFromMs(end-3600000);}
  renderInfluxExportInfo();
  const dialog=ifx('influx-export-dialog');
  if(!dialog.open){if(typeof dialog.showModal==='function')dialog.showModal();else dialog.setAttribute('open','');}
  if(IFX_EXPORT.job&&!IFX_EXPORT.timer)pollInfluxExport();
}
function setInfluxExportBusy(busy){
  ifx('influx-export-start').disabled=busy;ifx('influx-export-cancel').hidden=!busy;
  for(const id of ['influx-export-format','influx-export-name','influx-export-browse','influx-export-default','influx-export-from','influx-export-to'])ifx(id).disabled=busy;
  for(const r of document.querySelectorAll('input[name="influx-export-period"]'))r.disabled=busy;
}
function showInfluxExportJob(job){
  const status=ifx('influx-export-status'),progress=ifx('influx-export-progress'),list=ifx('influx-export-files');
  status.hidden=false;list.replaceChildren();
  const fmt=n=>Number(n||0).toLocaleString(I18N.locale());
  if(job.state==='running'){
    const total=Number(job.rows_total)||0,done=Number(job.rows_read)||0;
    progress.hidden=false;progress.value=total>0?Math.min(100,Math.round(done*100/total)):0;
    I18N.bind(status,"textContent",()=>job.phase==='count'?I18N.t('Conteggio dei punti…'):job.phase==='write'?I18N.join([{text:'Scrittura del file '},{value:job.format_label},{text:'…'}]):I18N.join([{text:'Lettura da InfluxDB e scrittura: '},{value:fmt(done)},{text:' di '},{value:fmt(total)},{text:' punti'}]),false);
    status.className='record-status';
  }else if(job.state==='ready'){
    progress.hidden=true;
    I18N.bind(status,"textContent",()=>I18N.join([{text:'Esportazione completata: '},{value:fmt(job.rows_read)},{text:' punti in '},{value:(job.files||[]).length},{text:' file'}]),false);
    status.className='record-status ok';
    for(const f of job.files||[]){const li=document.createElement('li');li.textContent=f.path+' · '+(Number(f.bytes)/1048576).toFixed(1)+' MiB';list.append(li);}
  }else{
    progress.hidden=true;status.className='record-status error';
    status.textContent=job.state==='cancelled'?I18N.t('Esportazione annullata'):(job.error||I18N.t('Esportazione non riuscita'));
  }
}
async function pollInfluxExport(){
  clearTimeout(IFX_EXPORT.timer);IFX_EXPORT.timer=null;
  const id=IFX_EXPORT.job;if(!id)return;
  try{
    const job=(await request('/api/influx/export/'+id)).job;
    showInfluxExportJob(job);
    if(job.state==='running'){setInfluxExportBusy(true);IFX_EXPORT.timer=setTimeout(pollInfluxExport,600);return;}
    IFX_EXPORT.job=null;setInfluxExportBusy(false);
  }catch(e){IFX_EXPORT.job=null;setInfluxExportBusy(false);const status=ifx('influx-export-status');status.hidden=false;status.className='record-status error';status.textContent=e.message;}
}
async function startInfluxExport(){
  const ctx=ifxCtx(),keys=[...ctx.selected],status=ifx('influx-export-status');
  try{
    const range=influxExportRange();
    const body={series:keys.map(k=>IFX.byKey.get(k)).filter(Boolean),start_ms:range.start,end_ms:range.end,format:ifx('influx-export-format').value,
      directory:ifx('influx-export-directory').value.trim(),name:ifx('influx-export-name').value.trim()};
    if(!body.series.length)throw new Error(I18N.t('Seleziona almeno una serie.'));
    ifxStore({export:{format:body.format,directory:body.directory}});
    setInfluxExportBusy(true);ifx('influx-export-files').replaceChildren();
    IFX_EXPORT.job=(await request('/api/influx/export',body)).job.id;
    pollInfluxExport();
  }catch(e){setInfluxExportBusy(false);IFX_EXPORT.job=null;status.hidden=false;status.className='record-status error';status.textContent=e.message;}
}
ifx('influx-export-open').addEventListener('click',openInfluxExport);
ifx('influx-export-close').addEventListener('click',()=>{const dialog=ifx('influx-export-dialog');if(typeof dialog.close==='function')dialog.close();else dialog.removeAttribute('open');});
ifx('influx-export-form').addEventListener('submit',event=>{event.preventDefault();startInfluxExport();});
ifx('influx-export-cancel').addEventListener('click',()=>{if(IFX_EXPORT.job)request('/api/influx/export/'+IFX_EXPORT.job+'/cancel',{}).catch(e=>influxError(e.message));});
ifx('influx-export-browse').addEventListener('click',async()=>{
  try{const j=await request('/api/recordings/location/browse',{title:I18N.t('Cartella di esportazione')});if(j.directory)ifx('influx-export-directory').value=j.directory;}
  catch(e){const status=ifx('influx-export-status');status.hidden=false;status.className='record-status error';status.textContent=e.message;}
});
ifx('influx-export-default').addEventListener('click',()=>{ifx('influx-export-directory').value='';});
for(const input of document.querySelectorAll('#influx-export-from,#influx-export-to'))input.addEventListener('focus',()=>{const custom=document.querySelector('input[name="influx-export-period"][value="custom"]');if(custom)custom.checked=true;});

const influxPageLayout=new ResizablePageLayout('influx-page','influx-page-splitter','opcua.influx.sidebar.width',()=>influxCharts.draw());
window.uiInflux={IFX,openInfluxExport,influxExportRange,startInfluxExport,influxCharts,loadInflux,connectInflux,loadInfluxCatalog,setInfluxPreset};
