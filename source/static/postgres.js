/* PostgreSQL (sola lettura): connessione, tabelle con colonna di tempo e colonne numeriche, serie (una colonna per segnale o colonna × etichetta), periodo a scelta e grafici con riduzione dei dati sul server. */
const PGX={connected:false,info:null,table:'',series:[],byKey:new Map(),preset:3600,settingsLoaded:false,refreshTimer:null,refineTimer:null,catalogBusy:false,truncated:false,filter:''};
const pgx=id=>document.getElementById(id);
const PGX_MAX_SERIES=64;
function pgxStore(patch){try{const saved=JSON.parse(localStorage.getItem('space-analyzer.postgres')||'{}');localStorage.setItem('space-analyzer.postgres',JSON.stringify({...saved,...patch}));}catch{}}
function pgxStored(){try{return JSON.parse(localStorage.getItem('space-analyzer.postgres')||'{}');}catch{return {};}}
// I grafici di questa pagina usano lo stesso elemento dei grafici di REGISTRAZIONI, con identificatori propri.
function pgChartElement(){
  const element=historyCharts.template.cloneNode(true),map=new Map();
  for(const node of [element,...element.querySelectorAll('[id]')])if(node.id){const next='pg-'+node.id.replace(/^history-/,'');map.set(node.id,next);node.id=next;}
  for(const node of element.querySelectorAll('[for],[aria-labelledby],[aria-describedby]'))for(const attr of ['for','aria-labelledby','aria-describedby'])if(node.hasAttribute(attr))node.setAttribute(attr,node.getAttribute(attr).split(' ').map(v=>map.get(v)||v).join(' '));
  const close=element.querySelector('[data-chart-role="closeFile"]');
  if(close){close.disabled=false;close.textContent=I18N.t('Svuota grafico');close.setAttribute('data-i18n','Svuota grafico');}
  return element;
}
const pgxCtx=(graph=pgCharts.active)=>graph.pg||(graph.pg={selected:new Set(),loaded:false,base:null,loading:false,revision:0});
const pgCharts=(()=>{
  pgx('pg-charts').append(pgChartElement());
  const workspace=new ChartWorkspace({scope:'pg',dock:'pg-analysis-dock',root:'pg-charts',selector:'pg-active-chart',add:'pg-add-chart',layout:'pg-layout',staticMode:true,exactMarkers:false,stateLabel:'POSTGRESQL',closeLabel:'Svuota grafico',copySignalsOnAdd:false,
    onNew:graph=>{graph.pg=null;pgxCtx(graph);hookPgGraph(graph);},
    onActive:graph=>{pgxCtx(graph);renderPgSelection();renderPgStatus();graph.trend.draw();},
    onMarkers:()=>{},onClear:graph=>{const ctx=pgxCtx(graph);ctx.selected.clear();ctx.loaded=false;renderPgSelection();},
    onClose:graph=>closePgGraph(graph)});
  return workspace;
})();
function closePgGraph(graph=pgCharts.active){
  const ctx=pgxCtx(graph);ctx.selected.clear();ctx.loaded=false;ctx.base=null;ctx.revision++;
  graph.signalSettings={};graph.axisSettings={};pgCharts.setGraphData(graph,{},new Map(),{});renderPgSelection();renderPgStatus();
}
pgCharts.syncZoom=pgx('pg-sync-zoom').checked;pgCharts.syncHover=pgx('pg-sync-hover').checked;
pgx('pg-sync-zoom').addEventListener('change',e=>{pgCharts.syncZoom=e.target.checked;});
pgx('pg-sync-hover').addEventListener('change',e=>{pgCharts.syncHover=e.target.checked;});
function hookPgGraph(graph){
  const trend=graph.trend,original=trend.onViewChanged;
  trend.onViewChanged=range=>{original?.(range);schedulePgRefine(graph,range);};
}
hookPgGraph(pgCharts.active);

function pgError(text){const box=pgx('pg-error');box.textContent=text||'';box.hidden=!text;}
function renderPgState(){
  const box=pgx('pg-state');
  if(!PGX.connected){box.textContent=I18N.t('Non connesso');box.className='pg-state';return;}
  box.textContent=I18N.join([{text:'PostgreSQL '},{value:(PGX.info?.version||'').split(' ')[0]},{text:' · '},{value:PGX.info?.database||''}]);box.className='pg-state ok';
}
const pgTableKey=t=>t.schema+'.'+t.name;
function pgTable(){return (PGX.info?.tables||[]).find(t=>pgTableKey(t)===pgx('pg-table').value)||null;}
function renderPgTables(){
  const select=pgx('pg-table'),list=PGX.info?.tables||[];select.replaceChildren();
  for(const t of list){const option=document.createElement('option');option.value=pgTableKey(t);option.textContent=pgTableKey(t)+(t.kind!=='table'?' · '+I18N.t(t.kind==='view'?'vista':t.kind==='materialized view'?'vista materializzata':'tabella esterna'):'');select.append(option);}
  select.disabled=!list.length;
  const wanted=PGX.table||pgxStored().table;
  select.value=list.some(t=>pgTableKey(t)===wanted)?wanted:(list[0]?pgTableKey(list[0]):'');PGX.table=select.value;
  renderPgColumns();
}
// Colonna del tempo e colonna delle etichette (facoltativa: una riga per segnale e istante).
function renderPgColumns(){
  const table=pgTable(),time=pgx('pg-time-column'),label=pgx('pg-label-column'),saved=(pgxStored().columns||{})[PGX.table]||{};
  time.replaceChildren();label.replaceChildren();
  for(const c of table?.time_columns||[]){const option=document.createElement('option');option.value=c.name;option.textContent=c.name+' · '+c.type;time.append(option);}
  label.append(I18N.option(()=>I18N.t('Nessuna: una colonna per segnale'),'',true,true,true));
  for(const name of table?.label_columns||[]){const option=document.createElement('option');option.value=name;option.textContent=name;label.append(option);}
  if(saved.time&&[...time.options].some(o=>o.value===saved.time))time.value=saved.time;
  label.value=saved.label&&[...label.options].some(o=>o.value===saved.label)?saved.label:'';
  time.disabled=!table;label.disabled=!table||!(table.label_columns||[]).length;
  pgx('pg-lookback-field').hidden=!label.value;
}
async function connectPg(){
  pgError('');pgx('pg-connect').disabled=true;
  try{
    const result=await request('/api/postgres/connect',{host:pgx('pg-host').value.trim(),port:Number(pgx('pg-port').value)||5432,database:pgx('pg-database').value.trim(),
      user:pgx('pg-user').value.trim(),password:pgx('pg-password').value,ssl:pgx('pg-ssl').value,timezone:pgx('pg-timezone').value});
    PGX.connected=true;PGX.info=result.connection;pgx('pg-password').value='';
    pgx('pg-password').placeholder=I18N.t('Password salvata (lascia vuoto per usarla)');
    renderPgState();renderPgTables();pgx('pg-connection').open=false;
    setPgStatus(I18N.join([{text:'Connesso a PostgreSQL '},{value:(PGX.info.version||'').split(' ')[0]},{text:' · '},{value:(PGX.info.tables||[]).length},{text:' tabelle con serie temporali'}]));
    await loadPgCatalog(true);
  }catch(error){PGX.connected=false;renderPgState();pgError(error.message);pgx('pg-connection').open=true;}
  finally{pgx('pg-connect').disabled=false;}
}
async function forgetPg(){
  try{await request('/api/postgres/forget',{});}catch(error){pgError(error.message);return;}
  PGX.connected=false;PGX.info=null;PGX.series=[];PGX.byKey=new Map();pgx('pg-password').placeholder=I18N.t('Password');pgx('pg-table').replaceChildren();pgx('pg-table').disabled=true;renderPgColumns();
  renderPgState();renderPgSeries();pgx('pg-connection').open=true;setPgStatus(I18N.t('Password dimenticata.'));
}
function setPgStatus(text){pgx('pg-status').textContent=text;}
async function loadPgCatalog(autoSelect=false){
  const table=pgTable();if(!PGX.connected||!table)return;
  const time=pgx('pg-time-column').value,label=pgx('pg-label-column').value,revision=PGX.catalogRevision=(PGX.catalogRevision||0)+1;
  PGX.catalogBusy=true;PGX.table=pgTableKey(table);
  const columns={...(pgxStored().columns||{}),[PGX.table]:{time,label}};
  pgxStore({table:PGX.table,lookback:pgx('pg-lookback').value,columns});pgx('pg-list-status').textContent=I18N.t('Ricerca delle serie…');
  try{
    const result=await request('/api/postgres/catalog',{schema:table.schema,table:table.name,time_column:time,label_column:label,lookback_s:Number(pgx('pg-lookback').value)});
    // Only the last request counts: the operator may change table or columns while an earlier list is still loading.
    if(revision!==PGX.catalogRevision)return;
    PGX.series=result.catalog.series||[];PGX.truncated=!!result.catalog.truncated;PGX.byKey=new Map(PGX.series.map(s=>[s.key,s]));
    renderPgSeries();
  }catch(error){if(revision===PGX.catalogRevision){pgx('pg-list-status').textContent=error.message;pgError(error.message);}return;}
  finally{if(revision===PGX.catalogRevision)PGX.catalogBusy=false;}
  if(autoSelect&&!pgxCtx().selected.size)await pgAutoSelect();
}
// Prima vista automatica: fino a 4 serie della tabella.
async function pgAutoSelect(){
  const pick=PGX.series.slice(0,4);if(!pick.length)return;
  const ctx=pgxCtx();for(const s of pick)ctx.selected.add(s.key);renderPgSelection();
  try{await loadPg();}catch(error){pgError(error.message);}
}
const pgGroupOf=s=>s.label_column?s.label_column+' = '+s.label:s.table;
function pgMatches(series,query){
  if(!query)return true;const text=(series.table+' '+series.column+' '+(series.label??'')+' '+series.data_type).toLowerCase();
  return query.toLowerCase().split(/\s+/).every(part=>text.includes(part));
}
function renderPgSeries(){
  const box=pgx('pg-series');box.replaceChildren();const query=pgx('pg-search').value.trim(),groups=new Map();
  for(const s of PGX.series){if(!pgMatches(s,query))continue;const group=pgGroupOf(s);if(!groups.has(group))groups.set(group,[]);groups.get(group).push(s);}
  const selected=pgxCtx().selected;
  for(const [name,items] of groups){
    const group=document.createElement('details');group.className='pg-group';group.open=!!query||groups.size<=3;group.dataset.group=name;
    const summary=document.createElement('summary'),title=document.createElement('strong');title.textContent=name;
    const count=document.createElement('span');count.className='small';count.textContent=' · '+items.length;summary.append(title,count);group.append(summary);
    for(const s of items){
      const row=document.createElement('label');row.className='pg-series-row';row.dataset.key=s.key;
      const input=document.createElement('input');input.type='checkbox';input.value=s.key;input.checked=selected.has(s.key);
      const field=document.createElement('span');field.className='pg-field';field.textContent=s.column;
      const tags=document.createElement('span');tags.className='pg-tags small';tags.textContent=s.time_column;
      const type=document.createElement('span');type.className='pg-type';type.textContent=s.data_type;
      row.append(input,field,tags,type);group.append(row);
    }
    box.append(group);
  }
  const total=PGX.series.length,shown=[...groups.values()].reduce((n,g)=>n+g.length,0);
  pgx('pg-list-status').textContent=total?I18N.join([{value:shown},{text:' / '},{value:total},{text:' serie'},{text:PGX.truncated?' · '+I18N.t('elenco limitato: restringi la ricerca'):''}]):(PGX.connected?I18N.t('Nessuna serie con dati nel periodo di ricerca.'):'');
}
function renderPgSelection(){
  const selected=pgxCtx().selected;
  for(const input of pgx('pg-series').querySelectorAll('input[type=checkbox]'))input.checked=selected.has(input.value);
}
pgx('pg-series').addEventListener('change',event=>{
  const input=event.target;if(!(input instanceof HTMLInputElement))return;const ctx=pgxCtx();
  if(input.checked){if(ctx.selected.size>=PGX_MAX_SERIES){input.checked=false;notice(I18N.join([{text:'Si possono aprire fino a '},{value:PGX_MAX_SERIES},{text:' serie per grafico.'}]),true);return;}ctx.selected.add(input.value);}
  else ctx.selected.delete(input.value);
});
pgx('pg-search').addEventListener('input',()=>renderPgSeries());
pgx('pg-clear-selection').addEventListener('click',()=>{pgxCtx().selected.clear();renderPgSelection();});

// ---- periodo ---------------------------------------------------------------------------------------------------------
function pgLatest(keys){let latest=0;for(const key of keys){const s=PGX.byKey.get(key);if(s&&Number.isFinite(s.last_ms))latest=Math.max(latest,s.last_ms);}return latest;}
/** Preset relativi: fino ad adesso se i dati sono recenti, altrimenti fino all'ultimo dato (serie ferme o storiche). */
function pgRange(keys){
  if(PGX.preset>0){const now=Date.now(),latest=pgLatest(keys),end=latest&&now-latest>300000?latest+1000:now;return {start:end-PGX.preset*1000,end};}
  const start=msFromLocalInput(pgx('pg-from').value),end=msFromLocalInput(pgx('pg-to').value);
  if(start===null||end===null)throw new Error(I18N.t('Indica inizio e fine del periodo.'));
  if(end<=start)throw new Error(I18N.t('La fine deve seguire l’inizio.'));
  return {start,end};
}
function setPgPreset(seconds,persist=true){
  PGX.preset=seconds;for(const b of pgx('pg-presets').querySelectorAll('button'))b.classList.toggle('active',Number(b.dataset.preset)===seconds);
  pgx('pg-custom').hidden=seconds!==0;
  if(seconds===0&&!pgx('pg-from').value){const end=Date.now();pgx('pg-to').value=localInputFromMs(end);pgx('pg-from').value=localInputFromMs(end-3600000);}
  if(persist)pgxStore({preset:seconds});
}
pgx('pg-presets').addEventListener('click',event=>{const b=event.target.closest('button[data-preset]');if(!b)return;setPgPreset(Number(b.dataset.preset));if(Number(b.dataset.preset)>0&&pgxCtx().selected.size)loadPg().catch(e=>pgError(e.message));});

// ---- dati --------------------------------------------------------------------------------------------------------------
// Budget di punti per serie: più serie selezionate, meno punti ciascuna (massimo 100.000, come le registrazioni). Con pochi punti nel periodo arrivano tutti i dati originali.
function pgMaxPoints(graph){if(PGX.maxPoints)return PGX.maxPoints;const series=Math.max(1,pgxCtx(graph).selected.size);return Math.max(2000,Math.min(100000,Math.floor(300000/series)));}
async function loadPg(graph=pgCharts.active,options={}){
  const ctx=pgxCtx(graph),keys=[...ctx.selected];if(!keys.length)throw new Error(I18N.t('Seleziona almeno una serie.'));
  const range=options.range||pgRange(keys),revision=++ctx.revision;
  ctx.loading=true;if(!options.quiet)setPgStatus(I18N.t('Lettura da PostgreSQL…'));
  pgError('');
  try{
    const data=await request('/api/postgres/samples',{series:keys.map(k=>PGX.byKey.get(k)).filter(Boolean),start_ms:range.start,end_ms:range.end,max_points:pgMaxPoints(graph),aggregate:pgx('pg-aggregate').value});
    if(!pgCharts.charts.includes(graph)||revision!==ctx.revision)return;
    const nodes=new Map((data.signals||[]).map(s=>[s.nodeid,{nodeid:s.nodeid,display_name:s.display_name||s.nodeid,unit:''}])),types=Object.fromEntries((data.signals||[]).map(s=>[s.nodeid,s.data_type||'Double']));
    if(options.quiet){graph.series=data.series||{};graph.nodes=nodes;graph.types=types;graph.ids=new Set(nodes.keys());pgCharts.updateGraph(graph);}
    else{if(!options.range)ctx.base=range;pgCharts.setGraphData(graph,data.series||{},nodes,types);}
    ctx.loaded=true;ctx.info=data;
    if(graph===pgCharts.active)renderPgStatus();
  }finally{ctx.loading=false;}
}
function renderPgStatus(){
  const ctx=pgxCtx(),data=ctx.info;if(!ctx.loaded||!data){return;}
  const returned=(data.signals||[]).reduce((n,s)=>n+Number(s.points_returned||0),0),original=(data.signals||[]).reduce((n,s)=>n+Number(s.points_original||0),0);
  const fmt=ms=>new Date(ms).toLocaleString(I18N.locale());
  const reduced=(data.signals||[]).find(s=>s.window_s>0),perSeries=Math.max(0,...(data.signals||[]).map(s=>Number(s.points_original||0))),spanMs=Number(data.end_ms)-Number(data.start_ms),nativeMs=perSeries>1&&spanMs>0?spanMs/(perSeries-1):0;
  setPgStatus(I18N.join([{value:(data.signals||[]).length},{text:' serie · '},{value:returned.toLocaleString(I18N.locale())},{text:' punti'},
    {text:reduced?' (da '+original.toLocaleString(I18N.locale())+', '+reduced.aggregate+' ogni '+pgWindowText(reduced.window_s)+')':' · '+I18N.t('tutti i punti')},{text:reduced&&nativeMs>0?' · dati originali ogni ~'+pgNativeText(nativeMs)+' (zoom per il dettaglio)':''},{text:' · '},{value:fmt(data.start_ms)},{text:' → '},{value:fmt(data.end_ms)}]));
}
function pgNativeText(ms){if(ms<1)return ms.toFixed(2)+' ms';if(ms<1000)return Math.round(ms)+' ms';return (ms/1000).toFixed(ms<10000?1:0)+' s';}
function pgWindowText(seconds){if(seconds<60)return seconds+' s';if(seconds<3600)return seconds/60+' min';if(seconds<86400)return seconds/3600+' h';return seconds/86400+' g';}
// Zoom sul grafico: i dati dell'intervallo visibile vengono riletti con più dettaglio; il ripristino torna al periodo scelto.
function schedulePgRefine(graph,range){
  clearTimeout(PGX.refineTimer);
  PGX.refineTimer=setTimeout(()=>{
    const graphs=pgCharts.syncZoom?pgCharts.charts:[graph];
    for(const g of graphs){
      const ctx=pgxCtx(g);if(!ctx.loaded||!ctx.base||ctx.loading)continue;
      const target=range&&Number.isFinite(range.xmin)&&Number.isFinite(range.xmax)&&range.xmax>range.xmin?{start:Math.max(ctx.base.start,range.xmin),end:Math.min(ctx.base.end,range.xmax)}:ctx.base;
      if(!(target.end>target.start))continue;
      loadPg(g,{range:target,quiet:true}).catch(e=>pgError(e.message));
    }
  },400);
}
function schedulePgAutoRefresh(){
  clearInterval(PGX.refreshTimer);const seconds=Number(pgx('pg-refresh-every').value);pgxStore({refresh:seconds});if(!(seconds>0))return;
  PGX.refreshTimer=setInterval(()=>{
    if(window.currentAppPage!=='pg'||PGX.preset<=0)return;
    for(const g of pgCharts.charts){const ctx=pgxCtx(g);if(!ctx.loaded||ctx.loading||g.trend.zoom||g.trend.linkedRange||!ctx.selected.size)continue;
      const range=pgRange([...ctx.selected]);ctx.base=range;loadPg(g,{range,quiet:true}).catch(e=>pgError(e.message));}
  },seconds*1000);
}
pgx('pg-refresh-every').addEventListener('change',schedulePgAutoRefresh);
pgx('pg-aggregate').addEventListener('change',()=>{pgxStore({aggregate:pgx('pg-aggregate').value});if(pgxCtx().loaded)loadPg().catch(e=>pgError(e.message));});
pgx('pg-table').addEventListener('change',()=>{pgxCtx().selected.clear();PGX.table=pgx('pg-table').value;renderPgColumns();loadPgCatalog(true);});
for(const id of ['pg-time-column','pg-label-column'])pgx(id).addEventListener('change',()=>{pgxCtx().selected.clear();pgx('pg-lookback-field').hidden=!pgx('pg-label-column').value;loadPgCatalog(true);});
pgx('pg-lookback').addEventListener('change',()=>loadPgCatalog());
pgx('pg-refresh').addEventListener('click',()=>loadPgCatalog());
pgx('pg-connect').addEventListener('click',connectPg);
pgx('pg-forget').addEventListener('click',forgetPg);
pgx('pg-load').addEventListener('click',()=>loadPg().catch(e=>{pgError(e.message);setPgStatus(e.message);}));
for(const id of ['pg-host','pg-port','pg-database','pg-user','pg-password'])pgx(id).addEventListener('keydown',event=>{if(event.key==='Enter'){event.preventDefault();connectPg();}});

async function openPgPage(){
  const saved=pgxStored();
  if(!PGX.settingsLoaded){
    PGX.settingsLoaded=true;
    if(saved.lookback)pgx('pg-lookback').value=saved.lookback;if(saved.aggregate)pgx('pg-aggregate').value=saved.aggregate;
    if(Number.isFinite(saved.preset))setPgPreset(saved.preset,false);if(Number.isFinite(saved.refresh)){pgx('pg-refresh-every').value=String(saved.refresh);schedulePgAutoRefresh();}
    try{
      const result=await request('/api/postgres/settings');const s=result.settings||{};
      // Only a server saved by a previous connection replaces the fields (the defaults never overwrite what the operator is typing).
      if(s.host){pgx('pg-host').value=s.host;pgx('pg-port').value=s.port||5432;pgx('pg-database').value=s.database||'';pgx('pg-user').value=s.user||'';pgx('pg-ssl').value=s.ssl||'prefer';pgx('pg-timezone').value=s.timezone||'utc';}
      if(s.password_saved){pgx('pg-password').placeholder=I18N.t('Password salvata (lascia vuoto per usarla)');if(s.host&&s.database)await connectPg();}
    }catch(error){pgError(error.message);}
  }
  if(!PGD.restored){PGD.restored=true;restorePgData();}
  setTimeout(()=>pgCharts.draw(),0);
}
window.openPgPage=openPgPage;
pgx('nav-pg').onclick=()=>showAppPage('pg');
document.addEventListener('space-analyzer-languagechange',()=>{renderPgState();renderPgSeries();renderPgStatus();});
// ---- esportazione ----------------------------------------------------------------------------------------------------
// Finestra per esportare i dati originali delle serie del grafico attivo (periodo e formato a scelta, cartella locale); il lavoro gira nel servizio.
const PGX_EXPORT={job:null,timer:null};
const pgxExportMode=()=>document.querySelector('input[name="pg-export-period"]:checked')?.value||'loaded';
function pgExportBase(graph){const ctx=pgxCtx(graph);if(ctx.base)return ctx.base;return pgRange([...ctx.selected]);}
function pgExportVisible(graph){const d=graph?.trend?.domain;return d&&Number.isFinite(d.xmin)&&Number.isFinite(d.xmax)&&d.xmax>d.xmin?{start:d.xmin,end:d.xmax}:null;}
function pgExportRange(){
  const graph=pgCharts.active,mode=pgxExportMode();
  if(mode==='custom'){
    const start=msFromLocalInput(pgx('pg-export-from').value),end=msFromLocalInput(pgx('pg-export-to').value);
    if(start===null||end===null)throw new Error(I18N.t('Indica inizio e fine del periodo.'));
    if(end<=start)throw new Error(I18N.t('La fine deve seguire l’inizio.'));
    return {start,end};
  }
  if(mode==='visible'){const visible=pgExportVisible(graph);if(visible)return visible;}
  return pgExportBase(graph);
}
function pgExportRangeText(range){if(!range)return '';const fmt=ms=>new Date(ms).toLocaleString(I18N.locale());return '('+fmt(range.start)+' → '+fmt(range.end)+')';}
function renderPgExportInfo(){
  const graph=pgCharts.active,ctx=pgxCtx(graph);
  I18N.bind(pgx('pg-export-summary'),"textContent",()=>I18N.join([{value:ctx.selected.size},{text:' serie del grafico '},{value:graph.name}]),false);
  let base=null;try{base=pgExportBase(graph);}catch{}
  const visible=pgExportVisible(graph)||base;
  pgx('pg-export-loaded-range').textContent=pgExportRangeText(base);
  pgx('pg-export-visible-range').textContent=pgExportRangeText(visible);
  const dir=pgx('pg-export-directory');dir.placeholder=I18N.t('Cartella Conversioni delle registrazioni');
  I18N.bind(pgx('pg-export-hint'),"textContent",()=>I18N.t('Vengono scritti tutti i punti originali del periodo: con dati molto fitti il file può essere grande e la lettura richiedere tempo.'),true);
}
function openPgExport(){
  const graph=pgCharts.active,ctx=pgxCtx(graph);
  if(!ctx.selected.size){pgError(I18N.t('Seleziona almeno una serie.'));return;}
  pgError('');
  const saved=pgxStored().export||{};
  if(saved.format&&[...pgx('pg-export-format').options].some(o=>o.value===saved.format))pgx('pg-export-format').value=saved.format;
  pgx('pg-export-directory').value=saved.directory||'';
  if(!pgx('pg-export-name').value)pgx('pg-export-name').value='PostgreSQL_'+(PGX.table||'').replace(/[^\w.-]+/g,'_');
  const end=Date.now();if(!pgx('pg-export-to').value){pgx('pg-export-to').value=localInputFromMs(end);pgx('pg-export-from').value=localInputFromMs(end-3600000);}
  renderPgExportInfo();
  const dialog=pgx('pg-export-dialog');
  if(!dialog.open){if(typeof dialog.showModal==='function')dialog.showModal();else dialog.setAttribute('open','');}
  if(PGX_EXPORT.job&&!PGX_EXPORT.timer)pollPgExport();
}
function setPgExportBusy(busy){
  pgx('pg-export-start').disabled=busy;pgx('pg-export-cancel').hidden=!busy;
  for(const id of ['pg-export-format','pg-export-name','pg-export-browse','pg-export-default','pg-export-from','pg-export-to'])pgx(id).disabled=busy;
  for(const r of document.querySelectorAll('input[name="pg-export-period"]'))r.disabled=busy;
}
function showPgExportJob(job){
  const status=pgx('pg-export-status'),progress=pgx('pg-export-progress'),list=pgx('pg-export-files');
  status.hidden=false;list.replaceChildren();
  const fmt=n=>Number(n||0).toLocaleString(I18N.locale());
  if(job.state==='running'){
    const total=Number(job.rows_total)||0,done=Number(job.rows_read)||0;
    progress.hidden=false;progress.value=total>0?Math.min(100,Math.round(done*100/total)):0;
    I18N.bind(status,"textContent",()=>job.phase==='count'?I18N.t('Conteggio dei punti…'):job.phase==='write'?I18N.join([{text:'Scrittura del file '},{value:job.format_label},{text:'…'}]):I18N.join([{text:'Lettura da PostgreSQL e scrittura: '},{value:fmt(done)},{text:' di '},{value:fmt(total)},{text:' punti'}]),false);
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
async function pollPgExport(){
  clearTimeout(PGX_EXPORT.timer);PGX_EXPORT.timer=null;
  const id=PGX_EXPORT.job;if(!id)return;
  try{
    const job=(await request('/api/postgres/export/'+id)).job;
    showPgExportJob(job);
    if(job.state==='running'){setPgExportBusy(true);PGX_EXPORT.timer=setTimeout(pollPgExport,600);return;}
    PGX_EXPORT.job=null;setPgExportBusy(false);
  }catch(e){PGX_EXPORT.job=null;setPgExportBusy(false);const status=pgx('pg-export-status');status.hidden=false;status.className='record-status error';status.textContent=e.message;}
}
async function startPgExport(){
  const ctx=pgxCtx(),keys=[...ctx.selected],status=pgx('pg-export-status');
  try{
    const range=pgExportRange();
    const body={series:keys.map(k=>PGX.byKey.get(k)).filter(Boolean),start_ms:range.start,end_ms:range.end,format:pgx('pg-export-format').value,
      directory:pgx('pg-export-directory').value.trim(),name:pgx('pg-export-name').value.trim()};
    if(!body.series.length)throw new Error(I18N.t('Seleziona almeno una serie.'));
    pgxStore({export:{format:body.format,directory:body.directory}});
    setPgExportBusy(true);pgx('pg-export-files').replaceChildren();
    PGX_EXPORT.job=(await request('/api/postgres/export',body)).job.id;
    pollPgExport();
  }catch(e){setPgExportBusy(false);PGX_EXPORT.job=null;status.hidden=false;status.className='record-status error';status.textContent=e.message;}
}
pgx('pg-export-open').addEventListener('click',openPgExport);
pgx('pg-export-close').addEventListener('click',()=>{const dialog=pgx('pg-export-dialog');if(typeof dialog.close==='function')dialog.close();else dialog.removeAttribute('open');});
pgx('pg-export-form').addEventListener('submit',event=>{event.preventDefault();startPgExport();});
pgx('pg-export-cancel').addEventListener('click',()=>{if(PGX_EXPORT.job)request('/api/postgres/export/'+PGX_EXPORT.job+'/cancel',{}).catch(e=>pgError(e.message));});
pgx('pg-export-browse').addEventListener('click',async()=>{
  try{const j=await request('/api/recordings/location/browse',{title:I18N.t('Cartella di esportazione')});if(j.directory)pgx('pg-export-directory').value=j.directory;}
  catch(e){const status=pgx('pg-export-status');status.hidden=false;status.className='record-status error';status.textContent=e.message;}
});
pgx('pg-export-default').addEventListener('click',()=>{pgx('pg-export-directory').value='';});
for(const input of document.querySelectorAll('#pg-export-from,#pg-export-to'))input.addEventListener('focus',()=>{const custom=document.querySelector('input[name="pg-export-period"][value="custom"]');if(custom)custom.checked=true;});

// ---- vista tabellare e query salvate -----------------------------------------------------------------------------------
// Tabella o vista (tutte le colonne, a pagine) oppure query SQL scritta dall'operatore: sola lettura, una pagina per volta; le query si salvano nel servizio.
const PGD={view:'chart',page:1,result:null,saved:[],selectedId:'',busy:false,sort:{col:-1,dir:1},overwrite:null,request:0};
const pgdSource=()=>document.querySelector('input[name="pg-data-source"]:checked')?.value||'table';
const PGD_NUMERIC=new Set(['smallint','integer','bigint','numeric','real','double precision','money','int2','int4','int8','float4','float8','oid']);
function setPgdStatus(text,error=false){const box=pgx('pg-data-status');box.hidden=!text;box.textContent=text||'';box.className='record-status'+(error?' error':'');}
function setPgdQueryStatus(text,error=false){const box=pgx('pg-query-status');box.textContent=text||'';box.classList.toggle('error',!!error);}
function setPgView(view){
  PGD.view=view==='table'?'table':'chart';
  pgx('pg-graph-main').classList.toggle('pg-view-table',PGD.view==='table');pgx('pg-data').hidden=PGD.view!=='table';
  for(const [id,name] of [['pg-view-chart','chart'],['pg-view-table','table']]){const b=pgx(id);b.classList.toggle('active',PGD.view===name);b.setAttribute('aria-pressed',String(PGD.view===name));}
  pgxStore({view:PGD.view});
  if(PGD.view==='chart')setTimeout(()=>pgCharts.draw(),0);
  else{renderPgdSource();if(!PGD.result&&pgdSource()==='table'&&PGX.connected&&pgTable())runPgData(1).catch(()=>{});}
}
function renderPgdSource(){
  const query=pgdSource()==='query';
  pgx('pg-query').hidden=!query;pgx('pg-data-table-options').hidden=query;
  const table=pgTable();pgx('pg-data-table-name').textContent=table?pgTableKey(table):'';
}
function pgdBody(page){
  const body={page_size:Number(pgx('pg-data-size').value)||500,page,timeout_s:60};
  if(pgdSource()==='query'){body.mode='query';body.sql=pgx('pg-query-sql').value;return body;}
  const table=pgTable();if(!table)throw new Error(I18N.t('Collega un server PostgreSQL e scegli una tabella.'));
  body.mode='table';body.schema=table.schema;body.table=table.name;
  const time=pgx('pg-time-column').value,type=(table.time_columns||[]).find(c=>c.name===time)?.type;
  if(time&&type){
    body.time_column=time;body.time_type=type;body.order=pgx('pg-data-order').value;
    if(pgx('pg-data-period').checked){const ctx=pgxCtx();let range=ctx.base;if(!range&&ctx.selected.size){try{range=pgRange([...ctx.selected]);}catch{}}if(range){body.start_ms=range.start;body.end_ms=range.end;}}
  }
  return body;
}
async function runPgData(page=1){
  if(PGD.busy)return;
  const query=pgdSource()==='query';
  if(!PGX.connected){setPgdStatus(I18N.t('PostgreSQL non connesso: premi Connetti.'),true);return;}
  let body;try{body=pgdBody(page);}catch(error){setPgdStatus(error.message,true);return;}
  PGD.busy=true;pgx('pg-data-run').disabled=true;setPgdStatus(I18N.t('Esecuzione…'));setPgdQueryStatus('');
  const token=++PGD.request;
  try{
    const result=(await request('/api/postgres/query',body)).result;
    if(token!==PGD.request)return;
    if(page>1&&!result.row_count){setPgdStatus(I18N.t('Nessuna altra riga.'));return;}
    PGD.result=result;PGD.page=page;PGD.sort={col:-1,dir:1};renderPgGrid();setPgdStatus('');
  }catch(error){setPgdStatus(error.message,true);if(query)setPgdQueryStatus(error.message,true);}
  finally{PGD.busy=false;pgx('pg-data-run').disabled=false;}
}
const pgdFormat=v=>v===null||v===undefined?null:typeof v==='boolean'?(v?'true':'false'):String(v);
function renderPgGrid(){
  const table=pgx('pg-grid'),result=PGD.result;table.replaceChildren();
  if(!result){pgx('pg-data-page').textContent='';return;}
  const numeric=result.columns.map(c=>PGD_NUMERIC.has(c.type));
  let order=result.rows.map((_,i)=>i);
  if(PGD.sort.col>=0){
    const col=PGD.sort.col,dir=PGD.sort.dir;
    order.sort((a,b)=>{const x=result.rows[a][col],y=result.rows[b][col];if(x===null&&y===null)return 0;if(x===null)return 1;if(y===null)return -1;
      const nx=Number(x),ny=Number(y);const cmp=numeric[col]&&Number.isFinite(nx)&&Number.isFinite(ny)?nx-ny:String(x).localeCompare(String(y),undefined,{numeric:true});return cmp*dir||a-b;});
  }
  const head=document.createElement('thead'),hr=document.createElement('tr'),corner=document.createElement('th');corner.className='rownum';corner.textContent='#';hr.append(corner);
  result.columns.forEach((c,i)=>{
    const th=document.createElement('th');th.title=c.name+' · '+c.type;if(numeric[i])th.className='num';th.dataset.col=String(i);
    th.append(document.createTextNode(c.name));
    if(PGD.sort.col===i){const mark=document.createElement('span');mark.className='sort';mark.textContent=PGD.sort.dir>0?'▲':'▼';th.append(mark);}
    const type=document.createElement('small');type.textContent=c.type;th.append(type);hr.append(th);
  });
  head.append(hr);
  const body=document.createElement('tbody'),fragment=document.createDocumentFragment();
  order.forEach((index,position)=>{
    const tr=document.createElement('tr'),n=document.createElement('td');n.className='rownum';n.textContent=String(result.first_row+position);tr.append(n);
    result.rows[index].forEach((v,i)=>{
      const td=document.createElement('td');if(numeric[i])td.className='num';
      const text=pgdFormat(v);if(text===null){const span=document.createElement('span');span.className='pg-null';span.textContent='NULL';td.append(span);}else{td.textContent=text;td.title=text.length>60?text.slice(0,500):'';}
      tr.append(td);
    });
    fragment.append(tr);
  });
  body.append(fragment);table.append(head,body);
  const last=result.first_row+result.row_count-1;
  pgx('pg-data-page').textContent=result.row_count?I18N.join([{text:'Righe '},{value:result.first_row.toLocaleString(I18N.locale())},{text:'–'},{value:last.toLocaleString(I18N.locale())},{text:' · pagina '},{value:result.page},{text:' · '},{value:result.elapsed_ms},{text:' ms'}]):I18N.t('Nessuna riga.');
  pgx('pg-data-prev').disabled=result.page<=1;pgx('pg-data-next').disabled=!result.has_more;
}
pgx('pg-grid').addEventListener('click',event=>{
  const th=event.target.closest('th[data-col]');if(!th||!PGD.result)return;
  const col=Number(th.dataset.col);PGD.sort=PGD.sort.col===col?{col,dir:-PGD.sort.dir}:{col,dir:1};renderPgGrid();
});
function pgdRowsText(separator,quote){
  const result=PGD.result;if(!result)return '';
  const cell=v=>{const t=pgdFormat(v);if(t===null)return '';return quote&&/[",\r\n;]/.test(t)?'"'+t.replace(/"/g,'""')+'"':t.replace(/[\t\r\n]+/g,' ');};
  return [result.columns.map(c=>cell(c.name)).join(separator),...result.rows.map(r=>r.map(cell).join(separator))].join('\r\n')+'\r\n';
}
pgx('pg-data-csv').addEventListener('click',()=>{
  if(!PGD.result||!PGD.result.rows.length){setPgdStatus(I18N.t('Nessuna riga da esportare.'),true);return;}
  const blob=new Blob(['﻿'+pgdRowsText(',',true)],{type:'text/csv;charset=utf-8'}),name=(PGD.result.mode==='table'?(pgTable()?.name||'tabella'):(pgx('pg-query-name').value.trim()||'query')).replace(/[^\w.-]+/g,'_');
  const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='postgres_'+name+'_pagina'+PGD.result.page+'.csv';document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(a.href),2000);
});
pgx('pg-data-copy').addEventListener('click',async()=>{
  if(!PGD.result)return;const text=pgdRowsText('\t',false);
  try{await navigator.clipboard.writeText(text);setPgdStatus(I18N.t('Righe copiate negli appunti.'));}catch{setPgdStatus(I18N.t('Copia non disponibile in questo browser.'),true);}
});
pgx('pg-data-run').addEventListener('click',()=>runPgData(1));
pgx('pg-data-prev').addEventListener('click',()=>{if(PGD.result&&PGD.result.page>1)runPgData(PGD.result.page-1);});
pgx('pg-data-next').addEventListener('click',()=>{if(PGD.result?.has_more)runPgData(PGD.result.page+1);});
pgx('pg-data-size').addEventListener('change',()=>{pgxStore({data:{...(pgxStored().data||{}),size:pgx('pg-data-size').value}});if(PGD.result)runPgData(1);});
pgx('pg-data-order').addEventListener('change',()=>{pgxStore({data:{...(pgxStored().data||{}),order:pgx('pg-data-order').value}});if(PGD.result)runPgData(1);});
pgx('pg-data-period').addEventListener('change',()=>{if(PGD.result)runPgData(1);});
for(const radio of document.querySelectorAll('input[name="pg-data-source"]'))radio.addEventListener('change',()=>{
  pgxStore({data:{...(pgxStored().data||{}),source:pgdSource()}});PGD.result=null;renderPgGrid();setPgdStatus('');renderPgdSource();if(pgdSource()==='table')runPgData(1);
});
pgx('pg-view-chart').addEventListener('click',()=>setPgView('chart'));
pgx('pg-view-table').addEventListener('click',()=>setPgView('table'));
pgx('pg-table').addEventListener('change',()=>{PGD.result=null;renderPgGrid();renderPgdSource();if(PGD.view==='table'&&pgdSource()==='table')runPgData(1);});
pgx('pg-query-sql').addEventListener('keydown',event=>{if(event.key==='Enter'&&(event.ctrlKey||event.metaKey)){event.preventDefault();runPgData(1);}});
pgx('pg-query-sql').addEventListener('input',()=>{pgxStore({sql:pgx('pg-query-sql').value});});
// Query salvate (nella cartella dati del servizio): elenco, caricamento nell'editor, salvataggio con nome, eliminazione.
function renderPgSaved(){
  const select=pgx('pg-query-saved');select.replaceChildren();
  const none=document.createElement('option');none.value='';none.textContent=I18N.t('— query salvate —');select.append(none);
  for(const q of PGD.saved){const option=document.createElement('option');option.value=q.id;option.textContent=q.name;select.append(option);}
  select.value=PGD.saved.some(q=>q.id===PGD.selectedId)?PGD.selectedId:'';PGD.selectedId=select.value;
  pgx('pg-query-delete').disabled=!PGD.selectedId;
}
async function loadPgSaved(){
  try{PGD.saved=(await request('/api/postgres/queries')).saved.queries||[];}catch(error){setPgdQueryStatus(error.message,true);}
  renderPgSaved();
}
pgx('pg-query-saved').addEventListener('change',()=>{
  const q=PGD.saved.find(x=>x.id===pgx('pg-query-saved').value);PGD.selectedId=q?q.id:'';PGD.overwrite=null;
  if(q){pgx('pg-query-sql').value=q.sql;pgx('pg-query-name').value=q.name;if(q.page_size&&[...pgx('pg-data-size').options].some(o=>o.value===String(q.page_size)))pgx('pg-data-size').value=String(q.page_size);pgxStore({sql:q.sql});}
  pgx('pg-query-delete').disabled=!PGD.selectedId;setPgdQueryStatus('');
});
pgx('pg-query-new').addEventListener('click',()=>{PGD.selectedId='';PGD.overwrite=null;pgx('pg-query-saved').value='';pgx('pg-query-name').value='';pgx('pg-query-sql').value='';pgxStore({sql:''});pgx('pg-query-delete').disabled=true;setPgdQueryStatus('');pgx('pg-query-sql').focus();});
pgx('pg-query-save').addEventListener('click',async()=>{
  const name=pgx('pg-query-name').value.trim();
  try{
    const result=(await request('/api/postgres/queries',{id:PGD.selectedId||undefined,name,sql:pgx('pg-query-sql').value,page_size:Number(pgx('pg-data-size').value),overwrite:PGD.overwrite===name.toLowerCase()})).saved;
    PGD.saved=result.queries||[];PGD.selectedId=result.saved.id;PGD.overwrite=null;renderPgSaved();setPgdQueryStatus(I18N.join([{text:'Query salvata: '},{value:result.saved.name}]));
  }catch(error){
    if(/conferma per sostituirla/.test(error.message)){PGD.overwrite=name.toLowerCase();setPgdQueryStatus(I18N.t('Esiste già una query con questo nome: premi di nuovo Salva per sostituirla.'),true);}
    else setPgdQueryStatus(error.message,true);
  }
});
pgx('pg-query-delete').addEventListener('click',async()=>{
  const q=PGD.saved.find(x=>x.id===PGD.selectedId);if(!q)return;
  if(typeof confirm==='function'&&!confirm(I18N.join([{text:'Eliminare la query «'},{value:q.name},{text:'»?'}])))return;
  try{PGD.saved=(await request('/api/postgres/queries/delete',{id:q.id})).saved.queries||[];PGD.selectedId='';renderPgSaved();pgx('pg-query-name').value='';setPgdQueryStatus(I18N.t('Query eliminata.'));}
  catch(error){setPgdQueryStatus(error.message,true);}
});
function restorePgData(){
  const saved=pgxStored(),data=saved.data||{};
  if(data.size&&[...pgx('pg-data-size').options].some(o=>o.value===String(data.size)))pgx('pg-data-size').value=String(data.size);
  if(data.order)pgx('pg-data-order').value=data.order;
  if(data.source==='query'){const r=document.querySelector('input[name="pg-data-source"][value="query"]');if(r)r.checked=true;}
  if(typeof saved.sql==='string'&&!pgx('pg-query-sql').value)pgx('pg-query-sql').value=saved.sql;
  renderPgdSource();loadPgSaved();
  if(saved.view==='table')setPgView('table');
}
document.addEventListener('space-analyzer-languagechange',()=>{renderPgSaved();renderPgGrid();});

const pgPageLayout=new ResizablePageLayout('pg-page','pg-page-splitter','opcua.pg.sidebar.width',()=>pgCharts.draw());
window.uiPg={PGX,PGD,runPgData,setPgView,loadPgSaved,openPgExport,pgExportRange,startPgExport,pgCharts,loadPg,connectPg,loadPgCatalog,setPgPreset};
