/* V1.2.0: durable workspace, synchronized time tools and original-sample analysis. */
let workspaceReady=false,workspaceTimer=null,workspaceSaving=false,workspaceDirty=false,lastWorkspaceJSON='';
let analysisTarget=null,fftResult=null;
const panelIds=['selected','scan-list','tree','history-file-list','history-signal-list'];
const recordFields={session_name:'record-session',machine:'record-machine',operator:'record-operator',description:'record-description',max_duration_s:'record-duration',max_file_mb:'record-max-mb',buffer_seconds:'record-buffer',parquet_profile:'record-parquet-profile'};
function recordOptions(){return {...Object.fromEntries(Object.entries(recordFields).map(([key,id])=>[key,['max_duration_s','max_file_mb','buffer_seconds'].includes(key)?Number($(id).value):$(id).value]))};}
function chartSnapshot(workspace){
  return {...workspace.serialize(),syncZoom:workspace.syncZoom,syncHover:workspace.syncHover,liveZoom:workspace.liveZoom,timeRange:workspace.timeRange,markers:{...workspace.markers},colors:[...workspace.colors],charts:workspace.charts.map(g=>({name:g.name,ids:[...g.ids],height:g.element.querySelector('.chartwrap').style.height,expanded:g.element.classList.contains('expanded-chart'),cleared:g.cleared,cutoff:g.cutoff,
    markerFractions:{...g.trend.markerFractions},types:g.trend.types,zoom:g.trend.zoom,linkedRange:g.trend.linkedRange,paused:g.trend.frozen,manualPaused:g.trend.manualPaused,
    snapshot:!workspace.staticMode&&g.trend.frozen?g.trend.series:null,
    signalSettings:g.signalSettings||{},axisSettings:g.axisSettings||{},scaleState:{mode:g.scaleState?.mode==='groups'?'groups':'legacy'},markerOpen:!!workspace.dock?.open,collapsed:!!g.collapsed,
    context:workspace.staticMode&&g.context?{file:g.context.file,chosen:[...g.context.chosen],from:g.context.from,to:g.context.to,resolution:g.context.resolution,loaded:g.context.loaded,
      compareFiles:(g.context.compareFiles||[]).map(f=>({...f})),compareMode:g.context.compareMode||'absolute',compareCoverage:g.context.compareCoverage||'union',compareStatus:g.context.comparing?'':g.context.compareStatus||''}:null}))};
}
function workspaceSnapshot(){
  if(historyCharts.active)storeHistoryInputs();
  return {schema:1,source_profiles:window.sourceProfiles||{},s7_mode_profiles:window.s7ModeProfiles||{},config:config(),selected_nodes:[...selected.values()],live:{...chartSnapshot(liveCharts),viewMode:liveViewMode},history:chartSnapshot(historyCharts),
    historyFilters:Object.fromEntries(['history-file-search','history-filter-format','history-duration-min','history-duration-max'].map(id=>[id,$(id).value])),
    fft_options:window.FFTAnalyzer?.settings()||{},studio:window.captureStudio?.()||{},page:window.currentAppPage||($('live-page').hidden?'recordings':'live'),autosave:$('workspace-autosave').checked,recordOptions:recordOptions(),record_format:$('record-form').querySelector('input[name="record-format"]:checked').value,
    sidebar:{live:livePageLayout.saved||livePageLayout.width(),history:historyPageLayout.saved||historyPageLayout.width()},
    panels:Object.fromEntries(panelIds.map(id=>[id,$(id).style.height])),addressOpen:document.querySelector('.address-drawer').open};
}
function scheduleWorkspaceSave(){
  if(!workspaceReady||applicationClosed)return;
  workspaceDirty=true;clearTimeout(workspaceTimer);
  if($('workspace-autosave').checked)workspaceTimer=setTimeout(()=>saveWorkspace().catch(e=>{I18N.bind($('workspace-status'),"textContent",()=>(I18N.join([{text:"Workspace non salvato: "},{value:(e.message)}])),true);}),900);
}
window.scheduleWorkspaceSave=scheduleWorkspaceSave;
async function saveWorkspace(force=false){
  if(!workspaceReady)return;
  if(workspaceSaving){workspaceDirty=true;if(force){await new Promise(resolve=>setTimeout(resolve,100));return saveWorkspace(true);}return;}
  if(historyCharts.charts.some(g=>g.context?.loading)||connectionBusy){if(force)throw new Error(I18N.t("Attendere il completamento delle operazioni prima di salvare"));scheduleWorkspaceSave();return;}
  const snapshot=workspaceSnapshot(),serialized=JSON.stringify(snapshot);
  if(!force&&serialized===lastWorkspaceJSON){workspaceDirty=false;return;}
  workspaceSaving=true;workspaceDirty=false;
  try{const result=await request('/api/workspace',snapshot);lastWorkspaceJSON=serialized;I18N.bind($('workspace-status'),"textContent",()=>(I18N.join([{text:"Workspace salvato · "},{value:(new Date(result.saved_at).toLocaleTimeString(I18N.locale()))}])),true);}
  finally{workspaceSaving=false;if(workspaceDirty)scheduleWorkspaceSave();}
}
function restoreView(graph,item,workspace){
  if(!item)return;const t=graph.trend;if(item.markerFractions)t.markerFractions={...item.markerFractions};graph.signalSettings=item.signalSettings||{};graph.axisSettings=item.axisSettings||{};graph.scaleState={mode:item.scaleState?.mode==='groups'?'groups':'legacy'};t.presentation=graph.signalSettings;t.axisSettings=graph.axisSettings;t.scaleState=graph.scaleState;workspace.updateGraph(graph);for(const [id,n] of t.nodes){if(graph.signalSettings[id]?.alias)t.nodes.set(id,{...n,display_name:graph.signalSettings[id].alias});}if(item.markerOpen&&workspace.dock)workspace.dock.open=true;if(item.collapsed)workspace.setCollapsed(graph,true);
  if(/^\d+(\.\d+)?px$/.test(item.height||''))graph.element.querySelector('.chartwrap').style.height=item.height;
  graph.element.classList.toggle('expanded-chart',!!item.expanded);
  graph.element.querySelector('[data-chart-role="expand"]').setAttribute('aria-pressed',String(!!item.expanded));
  graph.cleared=!!item.cleared;graph.cutoff=Number.isFinite(item.cutoff)?item.cutoff:null;
  if(!workspace.staticMode&&item.snapshot&&typeof item.snapshot==='object'){
    t.series=item.snapshot;t.latestSeries=item.snapshot;t.frozen=true;t.manualPaused=!!item.manualPaused;
    t.nodeSignature=[...t.nodes.keys()].join('\u001f');
  }
  if(item.types)t.types=item.types;
  t.axisSignature="";t.zoom=item.zoom||null;t.linkedRange=item.linkedRange||null;
  if(!workspace.staticMode&&item.paused){t.frozen=true;t.manualPaused=!!item.manualPaused;}
  t.draw();
}
function restoreWorkspaceOptions(workspace,saved){
  workspace.syncZoom=saved.syncZoom!==false;workspace.syncHover=saved.syncHover!==false;
  workspace.liveZoom=!workspace.staticMode&&saved.liveZoom===true;
  for(const graph of workspace.charts)graph.trend.liveZoom=workspace.liveZoom;
  $(workspace.scope+'-sync-zoom').checked=workspace.syncZoom;$(workspace.scope+'-sync-hover').checked=workspace.syncHover;
  workspace.timeRange=saved.timeRange||null;
  if(Array.isArray(saved.colors)){workspace.colors.clear();for(const [id,color] of saved.colors)if(typeof color==='string')workspace.colors.set(id,color);}
  workspace.setMarkers(saved.markers||{x1:null,x2:null});
}
async function restoreWorkspace(){
  const saved=window.initialWorkspace||{};
  if(saved.schema!==1){workspaceReady=true;I18N.bind($('workspace-status'),"textContent",()=>(I18N.t("Workspace pronto. Salvataggio automatico attivo.")),true);return;}
  $('workspace-autosave').checked=saved.autosave!==false;
  for(const id of ['history-file-search','history-filter-format','history-duration-min','history-duration-max'])if(typeof saved.historyFilters?.[id]==='string')$(id).value=saved.historyFilters[id];
  for(const [scope,layout] of [['live',livePageLayout],['history',historyPageLayout]])if(saved.sidebar?.[scope]){layout.saved=saved.sidebar[scope];layout.apply(saved.sidebar[scope],false);}
  for(const id of panelIds)if(/^\d+(\.\d+)?px$/.test(saved.panels?.[id]||''))$(id).style.height=saved.panels[id];
  if(typeof saved.addressOpen==='boolean')document.querySelector('.address-drawer').open=saved.addressOpen;
  for(const [key,id] of Object.entries(recordFields))if(saved.recordOptions?.[key]!==undefined){const el=$(id),v=String(saved.recordOptions[key]);if(el.tagName==='SELECT'&&![...el.options].some(o=>o.value===v)){if(key==='parquet_profile')continue;el.add(I18N.option(()=>(v+' s'),v,false,false,false));}el.value=v;}
  // Workspaces saved with the removed DAT formats keep the default Parquet.
  if(['csv','parquet','das','rbf'].includes(saved.record_format))$('record-form').querySelector('input[name="record-format"][value="'+saved.record_format+'"]').checked=true;
  updateRecordFormatControls();
  if(saved.live){setLiveViewMode(saved.live.viewMode);restoreWorkspaceOptions(liveCharts,saved.live);liveCharts.charts.forEach((g,i)=>restoreView(g,saved.live.charts[i],liveCharts));}
  if(saved.history?.charts?.length){
    for(const g of historyCharts.charts){g.trend.destroy();g.element.remove();}
    historyCharts.charts=[];historyCharts.counter=0;historyCharts.active=null;
    for(const item of saved.history.charts.slice(0,ChartWorkspace.MAX_CHARTS)){
      const graph=historyCharts.create(historyCharts.template.cloneNode(true),String(item.name||I18N.t("Grafico")),[],false),ctx=item.context;
      graph.context=ctx?{...emptyHistory(),...ctx,chosen:new Set(ctx.chosen||[]),loaded:false}:emptyHistory();
    }
    historyCharts.setLayout(saved.history.layout);restoreWorkspaceOptions(historyCharts,saved.history);
    const activeIndex=Math.max(0,Math.min(historyCharts.charts.length-1,Math.trunc(Number(saved.history.active)||0)));
    historyCharts.active=historyCharts.charts[activeIndex];historyCharts.renderSelectors();renderHistoryControls();
    for(let i=0;i<historyCharts.charts.length;i++){
      const graph=historyCharts.charts[i],item=saved.history.charts[i];
      if(item.context?.loaded&&graph.context.file){try{await loadHistory(graph);}catch(e){graph.context.status=I18N.t("Ripristino: ")+e.message;graph.context.error=true;}}
      restoreView(graph,item,historyCharts);
    }
    renderHistoryControls();scheduleHistoryMarkers();
  }
  window.FFTAnalyzer?.restore(saved.fft_options||{});window.restoreStudio?.(saved.studio||{});showAppPage(saved.page||'live');workspaceReady=true;
  I18N.bind($('workspace-status'),"textContent",()=>(I18N.join([{text:"Workspace ripristinato · "},{value:(new Date(saved.saved_at).toLocaleString(I18N.locale()))}])),true);
  lastWorkspaceJSON=JSON.stringify(workspaceSnapshot());
}
for(const workspace of [liveCharts,historyCharts]){
  $(workspace.scope+'-sync-zoom').addEventListener('change',e=>{
    workspace.syncZoom=e.target.checked;
    if(workspace.syncZoom){const range=workspace.active.trend.linkedRange||workspace.active.trend.zoom;workspace.syncRange(workspace.active,range?{xmin:range.xmin,xmax:range.xmax}:null);}
    else{workspace.timeRange=null;for(const g of workspace.charts){const t=g.trend;if(t.linkedRange&&t.fullDomain)t.zoom={...t.linkedRange,axes:t.zoom?.axes||t.fullDomain.axes};t.linkedRange=null;t.draw();}}
    scheduleWorkspaceSave();
  });
  $(workspace.scope+'-sync-hover').addEventListener('change',e=>{workspace.syncHover=e.target.checked;workspace.hoverAt(workspace.active,null);scheduleWorkspaceSave();});
}
document.addEventListener('change',scheduleWorkspaceSave);
document.addEventListener('click',scheduleWorkspaceSave);
document.addEventListener('pointerup',scheduleWorkspaceSave);
document.addEventListener('mouseup',scheduleWorkspaceSave);
document.querySelector('.address-drawer').addEventListener('toggle',scheduleWorkspaceSave);
function openDialog(id){const d=$(id);if(!d.open){if(d.showModal)d.showModal();else d.setAttribute('open','');}}
function closeDialog(id){const d=$(id);if(d.close)d.close();else d.removeAttribute('open');}
function releaseFrozenAnalysis(graph){
  graph.analysisSnapshotRevision=(graph.analysisSnapshotRevision||0)+1;
  const id=graph.analysisSnapshotId;graph.analysisSnapshotId=null;graph.analysisSnapshotError=null;graph.analysisSnapshotPromise=null;
  if(id)request('/api/analysis/live-snapshot/release',{snapshot_id:id}).catch(()=>{});
}
window.releaseFrozenAnalysis=releaseFrozenAnalysis;
window.captureFrozenAnalysis=function(graph){
  releaseFrozenAnalysis(graph);const revision=graph.analysisSnapshotRevision;
  let x1=Infinity,x2=-Infinity;for(const points of Object.values(graph.trend.series)){if(isSampleSeries(points)){if(points.length){x1=Math.min(x1,points.stats.xmin);x2=Math.max(x2,points.stats.xmax);}continue;}for(const p of points){x1=Math.min(x1,p[0]);x2=Math.max(x2,p[0]);}}
  if(!Number.isFinite(x1)||!Number.isFinite(x2)){graph.analysisSnapshotError=I18N.t("Nessun campione nella vista congelata");return;}
  graph.analysisSnapshotPromise=request('/api/analysis/live-snapshot',{signal_ids:[...graph.ids],x1,x2}).then(result=>{
    if(revision!==graph.analysisSnapshotRevision){request('/api/analysis/live-snapshot/release',{snapshot_id:result.snapshot_id}).catch(()=>{});return;}
    graph.analysisSnapshotId=result.snapshot_id;
  }).catch(error=>{if(revision===graph.analysisSnapshotRevision)graph.analysisSnapshotError=error.message;});
};
async function preparedAnalysisRequest(){
  const target=analysisTarget;if(!target)throw Error(I18N.t("Apri prima un grafico"));
  if(!target.workspace.staticMode&&target.graph.trend.frozen){
    await target.graph.analysisSnapshotPromise;
    if(target!==analysisTarget)throw Error(I18N.t("Grafico di analisi cambiato"));
    if(!target.graph.analysisSnapshotId)throw Error(target.graph.analysisSnapshotError||I18N.t("Questa vista in pausa non conserva i campioni originali (workspace ripristinato). Riprendere Play e congelare nuovamente oppure aprire la registrazione"));
    return {...analysisRequest(),snapshot_id:target.graph.analysisSnapshotId};
  }
  return analysisRequest();
}
function openAnalysis(workspace,graph){
  const markers=graph.trend.markers,liveDefault=false;
  if((!workspace.staticMode&&!graph.trend.frozen)||!Number.isFinite(markers.x1)||!Number.isFinite(markers.x2)||markers.x1===markers.x2){notice(I18N.t('FFT disponibile solo in Pausa con marker X1 e X2'),true);return;}

  if(!graph.ids.size){notice(I18N.t("Aggiungi almeno un segnale al grafico."),true);return;}
  analysisTarget={workspace,graph,markers:{...markers},file:graph.context?.file?{...graph.context.file}:null,ids:[...graph.ids]};fftResult=null;
  I18N.bind($('analysis-title'),"textContent",()=>(I18N.join([{value:(graph.name)},{text:" · Analisi X1–X2"}])),true);I18N.bind($('analysis-range'),"textContent",()=>(new Date(Math.min(markers.x1,markers.x2)).toLocaleString(I18N.locale())+' → '+new Date(Math.max(markers.x1,markers.x2)).toLocaleString(I18N.locale())),true);
  $('fft-signal').replaceChildren();for(const id of graph.ids){const option=I18N.option(()=>(graph.signalSettings?.[id]?.alias||treeLabel(graph.trend.nodes.get(id)||{nodeid:id})),id,false,false,false);I18N.bind(option,"title",()=>(id),false);$('fft-signal').add(option);}
  $('fft-result').hidden=true;$('export-download').hidden=true;I18N.bind($('export-status'),"textContent",()=>(''),false);I18N.bind($('analysis-status'),"textContent",()=>(I18N.t("Campioni originali. In Live, FFT ed export usano il buffer recente, anche dalla panoramica completa. Per lo storico precedente apri il file Record. FFT richiede un tratto Good continuo.")),true);openDialog('analysis-dialog');window.FFTAnalyzer.open(liveDefault);
}
function analysisRequest(){
  if(!analysisTarget)throw new Error(I18N.t("Apri prima un grafico"));const {workspace,markers,file,ids}=analysisTarget;
  if(workspace.staticMode&&!file)throw new Error(I18N.t("Apri una registrazione prima dell’analisi"));
  return {source:workspace.staticMode?(file.source||'repository'):'live',name:file?.name,signal_ids:ids,x1:markers.x1,x2:markers.x2};
}
async function calculateFFT(){return window.FFTAnalyzer.calculate();}
function drawFFT(){window.FFTAnalyzer?.draw();}
async function exportInterval(){
  const target=analysisTarget;const body={...await preparedAnalysisRequest(),format:$('export-format').value};$('export-run').disabled=true;I18N.bind($('export-status'),"textContent",()=>(I18N.t("Esportazione dei campioni originali…")),true);
  try{const r=await request('/api/analysis/export',body);if(target!==analysisTarget)return;I18N.bind($('export-status'),"textContent",()=>(I18N.join([{value:(r.rows)},{text:" righe esportate · "},{value:(r.path)},{value:(r.note?' · '+r.note:'')}])),true);$('export-download').href=r.url;$('export-download').download=r.name;$('export-download').hidden=false;}
  catch(e){I18N.bind($('export-status'),"textContent",()=>(e.message),true);}finally{$('export-run').disabled=false;}
}
$('analysis-close').onclick=()=>closeDialog('analysis-dialog');$('fft-run').onclick=()=>calculateFFT().catch(e=>notice(e.message,true));$('export-run').onclick=()=>exportInterval().catch(e=>notice(e.message,true));
$('fft-max-hz').oninput=drawFFT;$('fft-log').onchange=drawFFT;
if(window.ResizeObserver)new ResizeObserver(drawFFT).observe($('fft-canvas').parentElement);
async function refreshLogs(){try{const r=await request('/api/logs');I18N.bind($('logs-path'),"textContent",()=>(r.path),false);I18N.bind($('logs-content'),"textContent",()=>(r.tail||I18N.t("Nessun evento.")),true);}catch(e){I18N.bind($('logs-content'),"textContent",()=>(e.message),true);}}
$('open-logs').onclick=()=>{openDialog('logs-dialog');refreshLogs();};$('logs-refresh').onclick=refreshLogs;$('logs-close').onclick=()=>closeDialog('logs-dialog');
function logFrontend(message){if(applicationClosed)return;fetch('/api/log/frontend',{method:'POST',headers:{'X-OPCUA-Token':token,'Content-Type':'application/json'},body:JSON.stringify({message:String(message).slice(0,1200)})}).catch(()=>{});}
window.addEventListener('error',e=>logFrontend(e.message||I18N.t("Errore JavaScript")));
window.addEventListener('unhandledrejection',e=>logFrontend(e.reason?.message||String(e.reason)));
// Startup is coordinated by studio.js after UI enhancement hooks are installed.
