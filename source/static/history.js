/* V1.1.15: independent recording views with shared absolute marker timestamps. */
const H=id=>document.getElementById(id);
let historyFiles=[],historyMarkerTimer=null,historyCompareBusy=false;
const emptyHistory=()=>({file:null,chosen:new Set(),from:'',to:'',loaded:false,loading:false,stats:null,status:I18N.t("Seleziona una registrazione oppure spunta più file da confrontare."),error:false,markerSeq:0,compareFiles:[],compareMode:'absolute',compareCoverage:'union',compareStatus:'',comparing:false,resetViewOnOpen:false});
const historyCharts=new ChartWorkspace({scope:'history',root:'history-charts',selector:'history-active-chart',add:'history-add-chart',layout:'history-layout',staticMode:true,copySignalsOnAdd:false,
  onNew:(graph)=>{graph.context=emptyHistory();resetHistoryView(graph,false);},
  onActive:(graph,previous)=>{if(previous)storeHistoryInputs(previous);if(!graph.context)graph.context=emptyHistory();renderHistoryControls();renderHistoryFileList();graph.trend.draw();},
  onMarkers:()=>scheduleHistoryMarkers(),onClear:()=>renderHistoryControls(),onClose:graph=>closeHistoryFile(graph)});
historyCharts.active.context=emptyHistory();
const historyPageLayout=new ResizablePageLayout('recordings-page','history-page-splitter','opcua.history.sidebar.width',()=>historyCharts.draw());
const historyTrend=historyCharts.charts[0].trend;
function historyContext(graph=historyCharts.active){return graph.context||(graph.context=emptyHistory());}
function resetHistoryView(graph,shared=true){
  const trend=graph.trend;graph.navigatorCache=null;trend.drag=null;trend.crosshair=null;trend.tooltip.hidden=true;
  if(shared)trend.resetZoom();
  else{trend.zoom=null;trend.linkedRange=null;trend.frozen=false;trend.manualPaused=false;trend.draw();}
}
function closeHistoryAnalysis(graph){
  if(typeof analysisTarget!=='undefined'&&analysisTarget?.workspace===historyCharts&&analysisTarget.graph===graph){
    H('analysis-close').click();analysisTarget=null;fftResult=null;
  }
}
function closeHistoryFile(graph=historyCharts.active,render=true){
  if(!graph||!historyCharts.charts.includes(graph))return;
  graph.loadRevision++;const old=historyContext(graph);old.markerSeq++;closeHistoryAnalysis(graph);
  graph.context=emptyHistory();graph.context.status=I18N.t("Grafico vuoto. Seleziona un’altra registrazione oppure apri un file dal PC.");
  if(typeof studioFullscreen!=='undefined'&&studioFullscreen===graph)window.toggleStudioFullscreen?.(graph);
  graph.signalSettings={};graph.axisSettings={};resetHistoryView(graph,false);
  historyCharts.setGraphData(graph,{},new Map(),{});
  resetHistoryView(graph,false);
  if(!historyCharts.charts.some(g=>g.context?.file)){historyCharts.timeRange=null;historyCharts.resetMarkers();}
  if(render){renderHistoryControls();renderHistoryFileList();window.scheduleWorkspaceSave?.();}
}
function closeAllHistoryFiles(){
  const keep=historyCharts.active||historyCharts.charts[0];
  for(const graph of [...historyCharts.charts])closeHistoryFile(graph,false);
  for(const graph of [...historyCharts.charts])if(graph!==keep)historyCharts.remove(graph);
  keep.name='Grafico 1';keep.element.querySelector('[data-chart-role="title"]').value=keep.name;
  historyCharts.timeRange=null;historyCharts.resetMarkers();historyCharts.setLayout('single');historyCharts.renderSelectors();
  H('history-local-file').value='';H('history-signal-search').value='';
  renderHistoryControls();renderHistoryFileList();window.scheduleWorkspaceSave?.();
}
function historyBytes(bytes){const n=Number(bytes||0);if(n<1024)return n+' B';if(n<1048576)return (n/1024).toLocaleString(I18N.locale(),{maximumFractionDigits:1})+' kB';if(n<1073741824)return (n/1048576).toLocaleString(I18N.locale(),{maximumFractionDigits:1})+' MB';return (n/1073741824).toLocaleString(I18N.locale(),{maximumFractionDigits:2})+' GB';}
function historyDuration(seconds){const s=Number(seconds);if(seconds===null||seconds===undefined||!Number.isFinite(s)||s<0)return '—';if(s<1)return (s*1000).toLocaleString(I18N.locale(),{maximumFractionDigits:1})+' ms';if(s<60)return s.toLocaleString(I18N.locale(),{maximumFractionDigits:2})+' s';if(s<3600)return (s/60).toLocaleString(I18N.locale(),{maximumFractionDigits:2})+' min';return (s/3600).toLocaleString(I18N.locale(),{maximumFractionDigits:2})+' h';}
function localInputFromMs(ms){const d=new Date(Number(ms));if(!Number.isFinite(d.getTime()))return '';const pad=(v,n=2)=>String(v).padStart(n,'0');return `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}.${pad(d.getMilliseconds(),3)}`;}
function localInputFromIso(value){const t=Date.parse(value||'');return Number.isFinite(t)?localInputFromMs(t):'';}
function msFromLocalInput(value){if(!value)return null;const t=new Date(value).getTime();return Number.isFinite(t)?t:null;}
function historyFormatStamp(value){const t=Date.parse(value||'');return Number.isFinite(t)?new Date(t).toLocaleString(I18N.locale()):'—';}
function showAppPage(page){
  window.currentAppPage=page;const history=page==='recordings',calculated=page==='calculations',ssh=page==='ssh',conversion=page==='conversion';H('live-page').hidden=page!=='live';H('recordings-page').hidden=!history;if(H('calculations-page'))H('calculations-page').hidden=!calculated;if(H('ssh-page'))H('ssh-page').hidden=!ssh;if(H('conversion-page'))H('conversion-page').hidden=!conversion;
  for(const [id,active] of [['nav-live',page==='live'],['nav-recordings',history],...(H('nav-calculations')?[['nav-calculations',calculated]]:[]),...(H('nav-ssh')?[['nav-ssh',ssh]]:[]),...(H('nav-conversion')?[['nav-conversion',conversion]]:[])]){H(id).classList.toggle('active',active);H(id).setAttribute('aria-pressed',String(active));}
  if(ssh)window.openSSHPage?.();if(conversion)window.openConversionPage?.();if(calculated)window.openCalculationsPage?.();if(history)refreshHistoryFiles().catch(e=>notice(e.message,true));
  setTimeout(()=>{(history?historyCharts:liveCharts).draw();},0);
}
function storeHistoryInputs(graph=historyCharts.active){
  const ctx=historyContext(graph);if(!ctx.file)return;
  ctx.from=H('history-from').value;ctx.to=H('history-to').value;
}
function renderHistoryFileList(){
  const box=H('history-file-list'),ctx=historyContext(),current=ctx.file;box.replaceChildren();
  const query=H('history-file-search').value.trim().toLocaleLowerCase(I18N.locale()),format=H('history-filter-format').value;
  const minimum=H('history-duration-min').value===''?null:Number(H('history-duration-min').value),maximum=H('history-duration-max').value===''?null:Number(H('history-duration-max').value);
  const invalid=minimum!==null&&(!Number.isFinite(minimum)||minimum<0)||maximum!==null&&(!Number.isFinite(maximum)||maximum<0)||minimum!==null&&maximum!==null&&maximum<minimum;
  const visible=invalid?[]:historyFiles.filter(file=>{
    if(format&&file.format!==format)return false;
    if(query&&![file.display_name,file.name,file.session?.session_name,file.session?.machine,file.session?.description].some(v=>String(v||'').toLocaleLowerCase(I18N.locale()).includes(query)))return false;
    const duration=file.duration_seconds;
    if(minimum!==null&&(duration===null||duration===undefined||duration<minimum))return false;
    if(maximum!==null&&(duration===null||duration===undefined||duration>maximum))return false;
    return true;
  });
  I18N.bind(H('history-file-summary'),"textContent",()=>(invalid?I18N.t("Durate non valide: la massima deve essere almeno pari alla minima."):I18N.join([{value:(visible.length)},{text:I18N.t(" di ")},{value:(historyFiles.length)},{text:" file · "},{value:((ctx.compareFiles||[]).length)},{text:" selezionati"}])),true);
  for(const file of visible){
    const row=document.createElement('div');row.className='history-file-item';
    const check=document.createElement('input');check.type='checkbox';check.className='history-file-check';check.checked=(ctx.compareFiles||[]).some(f=>historyFileKey(f)===historyFileKey(file));I18N.attr(check,'aria-label',()=>(I18N.join([{text:"Confronta "},{value:(file.display_name||file.name)}])));check.dataset.fileKey=historyFileKey(file);check.disabled=ctx.comparing||!!file.read_error;
    check.addEventListener('change',()=>{if(check.checked){if((ctx.compareFiles||[]).length>=32){check.checked=false;notice(I18N.t("Seleziona al massimo 32 file."),true);return;}ctx.compareFiles=[...(ctx.compareFiles||[]),historyCompareEntry(file)];}else ctx.compareFiles=(ctx.compareFiles||[]).filter(f=>historyFileKey(f)!==historyFileKey(file));renderHistoryComparison();renderHistoryFileList();window.scheduleWorkspaceSave?.();});
    const button=document.createElement('button');button.type='button';button.className='history-file'+(current?.source===(file.source||'repository')&&current?.name===file.name?' active':'');
    const top=document.createElement('span');top.className='history-file-top';const name=document.createElement('strong'),badge=document.createElement('b');I18N.bind(name,"textContent",()=>(file.display_name||file.name),false);I18N.bind(badge,"textContent",()=>(file.storage?.codec==='zstd'?'PARQUET ZSTD':String(file.format||'').toUpperCase()),false);top.append(name,badge);
    const meta=document.createElement('span');meta.className='history-file-meta';I18N.bind(meta,"textContent",()=>([file.session?.session_name,file.session?.machine,file.session?.operator,file.session?.part?I18N.t("Parte ")+file.session.part:'',historyBytes(file.size_bytes),historyDuration(file.duration_seconds),historyFormatStamp(file.started_at),file.rows_written!==undefined&&file.rows_written!==null?I18N.join([{value:(file.rows_written)},{text:" valori"}]):'',file.read_error].filter(Boolean).join(' · ')),true);I18N.bind(button,"title",()=>(file.read_error||file.session?.description||''),false);
    button.append(top,meta);button.addEventListener('click',()=>selectHistoryFile(file));row.append(check,button);box.append(row);
  }
  if(!visible.length){const p=document.createElement('p');p.className='empty';I18N.bind(p,"textContent",()=>(historyFiles.length?I18N.t("Nessun file corrisponde ai filtri. Le selezioni già fatte rimangono disponibili."):I18N.t("Nessuna registrazione disponibile. Puoi aprire più file dal PC.")),true);box.append(p);}
  renderHistoryComparison();
}

function historyFileKey(file){return (file.source||'repository')+'\u001f'+file.name;}
function historyCompareEntry(file){return Object.fromEntries(['source','name','display_name','format','started_at','ended_at','duration_seconds','offset_ms'].map(k=>[k,k==='source'?(file[k]||'repository'):k==='offset_ms'?(file[k]||0):file[k]]));}
function renderHistoryComparison(){
  const ctx=historyContext(),files=ctx.compareFiles||[],box=H('history-compare-files');box.replaceChildren();
  I18N.bind(H('history-compare-count'),"textContent",()=>(I18N.join([{value:(files.length)},{text:" selezionati"}])),true);H('history-compare-mode').value=ctx.compareMode||'absolute';H('history-compare-coverage').value=ctx.compareCoverage||'union';
  H('history-compare').disabled=historyCompareBusy||files.length<2;H('history-compare-clear').disabled=ctx.comparing;
  H('history-compare-mode').disabled=ctx.comparing;H('history-compare-coverage').disabled=ctx.comparing;
  I18N.bind(H('history-compare-status'),"textContent",()=>(ctx.compareStatus||''),false);H('history-compare-status').className='record-status'+(ctx.comparing?' active':'');
  const origin=Date.parse(files[0]?.started_at||'');
  const bounds=files.map(f=>{const a=Date.parse(f.started_at||''),b=Date.parse(f.ended_at||''),offset=Number(f.offset_ms||0)+(ctx.compareMode==='starts'&&Number.isFinite(origin)&&Number.isFinite(a)?origin-a:0);return {a:a+offset,b:b+offset};});
  const start=Math.min(...bounds.filter(b=>Number.isFinite(b.a)).map(b=>b.a)),end=Math.max(...bounds.filter(b=>Number.isFinite(b.b)).map(b=>b.b));
  files.forEach((file,index)=>{
    const row=document.createElement('div');row.className='history-compare-file';
    const name=document.createElement('strong');I18N.bind(name,"textContent",()=>(file.display_name||file.name),false);I18N.bind(name,"title",()=>(name.textContent),false);
    const remove=document.createElement('button');remove.type='button';remove.className='ghost compact';I18N.bind(remove,"textContent",()=>('×'),false);I18N.attr(remove,'aria-label',()=>(I18N.join([{text:"Rimuovi "},{value:(name.textContent)},{text:" dal confronto"}])));remove.disabled=ctx.comparing;remove.onclick=()=>{ctx.compareFiles=files.filter((_,i)=>i!==index);renderHistoryFileList();window.scheduleWorkspaceSave?.();};
    const label=document.createElement('label'),input=document.createElement('input');I18N.bind(label,"textContent",()=>(I18N.t("Correzione orario (ms)")),true);input.type='number';input.step='any';input.value=String(file.offset_ms||0);input.dataset.compareOffset=String(index);I18N.attr(input,'aria-label',()=>(I18N.join([{text:"Correzione orario in millisecondi per "},{value:(name.textContent)}])));input.disabled=ctx.comparing;
    input.addEventListener('change',()=>{const offset=Number(input.value);if(!Number.isFinite(offset)||Math.abs(offset)>315576000000){notice(I18N.t("Correzione orario non valida (massimo 10 anni)."),true);input.value=String(file.offset_ms||0);return;}file.offset_ms=offset;renderHistoryComparison();window.scheduleWorkspaceSave?.();});label.append(input);
    const meta=document.createElement('small');I18N.bind(meta,"textContent",()=>(String(file.format||'').toUpperCase()+' · '+historyDuration(file.duration_seconds)+' · '+historyFormatStamp(file.started_at)),false);
    const track=document.createElement('div'),bar=document.createElement('span');track.className='history-time-track';bar.className='history-time-bar';const range=bounds[index];
    if(Number.isFinite(start)&&Number.isFinite(end)&&Number.isFinite(range.a)&&Number.isFinite(range.b)){bar.style.left=((range.a-start)/Math.max(1,end-start)*100)+'%';bar.style.width=Math.max(.7,(range.b-range.a)/Math.max(1,end-start)*100)+'%';I18N.bind(track,"title",()=>(new Date(range.a).toLocaleString(I18N.locale())+' → '+new Date(range.b).toLocaleString(I18N.locale())),true);}else track.hidden=true;
    track.append(bar);row.append(name,remove,meta,track,label);box.append(row);
  });
}

async function compareHistory(graph=historyCharts.active){
  const ctx=historyContext(graph);if(historyCompareBusy)throw new Error(I18N.t("Un confronto è già in preparazione."));
  if((ctx.compareFiles||[]).length<2)throw new Error(I18N.t("Spunta almeno due file da confrontare."));
  const revision=++graph.loadRevision,files=ctx.compareFiles.map(f=>({...f}));
  ctx.comparing=true;ctx.loading=true;ctx.error=false;ctx.compareStatus=I18N.t("Preparazione del confronto…");historyCompareBusy=true;renderHistoryControls();renderHistoryFileList();
  try{
    const started=await request('/api/recordings/compare',{files:files.map(f=>({source:f.source,name:f.name,offset_ms:Number(f.offset_ms||0)})),mode:ctx.compareMode||'absolute',coverage:ctx.compareCoverage||'union'});
    let job=started.job;
    while(job.state==='running'){
      await new Promise(resolve=>setTimeout(resolve,350));job=(await request('/api/recordings/compare/'+job.id)).job;
      ctx.compareStatus=I18N.join([{value:(job.phase==='write'?I18N.t("Allineamento dei campioni"):I18N.t("Lettura degli orari"))},{text:" · file "},{value:(job.file_index||1)},{text:"/"},{value:(job.file_count||files.length)},{value:(job.rows_written?I18N.join([{text:" · "},{value:(Number(job.rows_written).toLocaleString(I18N.locale()))},{text:" valori"}]):'')}]);
      if(graph===historyCharts.active&&ctx===graph.context)renderHistoryComparison();
    }
    if(job.state!=='ready')throw new Error(job.error||I18N.t("Confronto non riuscito"));
    if(!historyCharts.charts.includes(graph)||revision!==graph.loadRevision||ctx!==graph.context){await refreshHistoryFiles();return;}
    selectHistoryFile({source:'local',...job.recording},graph);
    graph.context.compareFiles=files;graph.context.compareMode=ctx.compareMode;graph.context.compareCoverage=ctx.compareCoverage;graph.context.compareStatus=I18N.join([{text:"Confronto pronto · "},{value:(files.length)},{text:" file · campioni originali, senza interpolazione"}]);
    await loadHistory(graph);await refreshHistoryFiles();window.scheduleWorkspaceSave?.();
  }catch(error){if(ctx===graph.context){ctx.error=true;ctx.status=error.message;ctx.compareStatus=error.message;}throw error;}
  finally{ctx.loading=false;ctx.comparing=false;historyCompareBusy=false;renderHistoryControls();renderHistoryFileList();}
}
function renderHistorySignals(){
  const ctx=historyContext(),list=H('history-signal-list');list.replaceChildren();const query=H('history-signal-search').value.trim().toLocaleLowerCase(I18N.locale());
  for(const signal of ctx.file?.signals||[]){
    if(query&&![signal.display_name,signal.nodeid].some(v=>String(v||'').toLocaleLowerCase(I18N.locale()).includes(query)))continue;
    const label=document.createElement('label');label.className='history-signal-row';const check=document.createElement('input');check.type='checkbox';check.value=signal.nodeid;check.checked=ctx.chosen.has(signal.nodeid);check.dataset.historySignal=signal.nodeid;
    check.addEventListener('change',()=>{if(check.checked){if(ctx.chosen.size>=64){check.checked=false;notice(I18N.t("Massimo 64 segnali per grafico. Aggiungi un altro grafico per gli altri segnali."),true);return;}ctx.chosen.add(signal.nodeid);}else ctx.chosen.delete(signal.nodeid);});
    const text=document.createElement('span'),strong=document.createElement('strong'),small=document.createElement('small');I18N.bind(strong,"textContent",()=>(signal.display_name||signal.nodeid),false);I18N.bind(small,"textContent",()=>(signal.nodeid),false);small.className='mono';text.append(strong,small);label.append(check,text);list.append(label);
  }
  if(!list.children.length){const p=document.createElement('p');p.className='empty';I18N.bind(p,"textContent",()=>(ctx.file?.signals?.length?I18N.t("Nessun segnale corrisponde al filtro."):I18N.t("I segnali saranno rilevati alla prima apertura del file.")),true);list.append(p);}
}
function renderHistoryControls(){
  const graph=historyCharts.active,ctx=historyContext(),file=ctx.file,data=ctx.stats;
  H('history-file-controls').hidden=!file;
  I18N.bind(H('history-selected-name'),"textContent",()=>(file?(file.display_name||file.name):I18N.t("Nessun file selezionato")),true);
  I18N.bind(H('history-selected-meta'),"textContent",()=>(file?I18N.join([{text:""},{value:(file.source==='local'?I18N.t("File dal PC"):'Recordings')},{text:" · "},{value:(file.format_label||String(file.format).toUpperCase())},{text:" · "},{value:(historyBytes(file.size_bytes))},{text:" · "},{value:(historyFormatStamp(file.started_at||file.modified_at))},{text:""},{value:(file.view_source_note?' · '+file.view_source_note:'')},{text:""}]):''),true);
  H('history-from').value=ctx.from;H('history-to').value=ctx.to;
  H('history-load').disabled=ctx.loading;H('history-full-range').disabled=ctx.loading;
  H('history-close-file').disabled=!(file||ctx.loading||ctx.comparing||graph.ids.size||(ctx.compareFiles||[]).length);
  H('history-close-all').disabled=!historyCharts.charts.some(g=>g.context?.file||g.context?.loading||g.context?.comparing||g.ids.size||(g.context?.compareFiles||[]).length)||applicationClosed;
  for(const chart of historyCharts.charts){const button=chart.element.querySelector('[data-chart-role="closeFile"]');if(button)button.disabled=!(chart.context?.file||chart.context?.loading||chart.context?.comparing||chart.ids.size||(chart.context?.compareFiles||[]).length);}
  H('history-load-status').className='record-status'+(ctx.loading?' active':'')+(ctx.error?' error':'');I18N.bind(H('history-load-status'),"textContent",()=>(graph.name+' · '+ctx.status),false);
  I18N.bind(H('history-format'),"textContent",()=>(file?.storage?.codec==='zstd'?'PARQUET ZSTD':String(data?.format||file?.format||'—').toUpperCase()),false);
  const seconds=data&&Number.isFinite(data.start_ms)&&Number.isFinite(data.end_ms)?(data.end_ms-data.start_ms)/1000:file?.duration_seconds;
  I18N.bind(H('history-duration'),"textContent",()=>(seconds===undefined?'—':historyDuration(seconds)),false);
  const points=graph.cleared?0:(data?.signals||[]).reduce((n,s)=>n+Number(s.points_returned||0),0);
  I18N.bind(H('history-points'),"textContent",()=>(points.toLocaleString(I18N.locale())),true);I18N.bind(H('history-signal-count'),"textContent",()=>(String(graph.nodes.size)),false);
  I18N.bind(H('history-diag'),"textContent",()=>(data?.full_resolution?I18N.t("Piena risoluzione: il grafico contiene tutti i campioni del file; con lo zoom compaiono i singoli campioni."):I18N.t("Marker comuni a tutti i grafici della pagina, con timestamp assoluti. Ogni grafico conserva file, segnali e intervallo propri.")),true);
  renderHistorySignals();
  renderHistoryComparison();
  if(file?.session)H('history-selected-meta').textContent+=' · '+[file.session.session_name,file.session.machine,file.session.operator,file.session.description].filter(Boolean).join(' · ');
}
function selectHistoryFile(file,graph=historyCharts.active){
  const previous=historyContext(graph);graph.loadRevision++;const current={source:'repository',...file};
  closeHistoryAnalysis(graph);resetHistoryView(graph);
  graph.context={...emptyHistory(),file:current,chosen:new Set((current.signals||[]).slice(0,current.format==='dat'?8:64).map(s=>s.nodeid)),from:Number.isFinite(current.start_ms)?localInputFromMs(Math.floor(current.start_ms)):localInputFromIso(current.started_at),to:Number.isFinite(current.end_ms)?localInputFromMs(Math.ceil(current.end_ms)):localInputFromIso(current.ended_at),status:current.read_error||I18N.t("File selezionato. Premi “Apri nel grafico”. Per DAT inizialmente selezionati fino a 8 canali; gli altri sono disponibili nell’elenco.")};
  graph.context.resetViewOnOpen=true;
  for(const key of ['compareFiles','compareMode','compareCoverage','compareStatus'])graph.context[key]=previous[key]??graph.context[key];
  if(current.comparison){const groups=new Map();for(const s of current.signals||[]){const key=s.nodeid.split(':').slice(0,2).join(':');if(!groups.has(key))groups.set(key,[]);groups.get(key).push(s.nodeid);}const selected=[];while(selected.length<64&&[...groups.values()].some(g=>g.length))for(const group of groups.values())if(group.length&&selected.length<64)selected.push(group.shift());graph.context.chosen=new Set(selected);graph.context.status=I18N.t("Confronto pronto: seleziona fino a 64 segnali per grafico.");}
  historyCharts.setGraphData(graph,{},new Map(),{});
  if(graph===historyCharts.active){renderHistoryControls();renderHistoryFileList();}
}
async function refreshHistoryFiles(){const result=await request('/api/recordings');historyFiles=(result.files||[]).map(f=>({source:'repository',...f}));I18N.bind(H('history-directory'),"textContent",()=>(result.directory||'—'),false);if(result.directory&&!recordingLocked()){recordDirectory=result.directory;syncRecordingsLocation();}if(result.location_warning)notice(result.location_warning,true);renderHistoryFileList();}
async function importHistoryLocalFile(file){
  const files=Array.isArray(file)?file:file?[file]:[];if(!files.length)return;
  if(files.some(f=>!['.csv','.parquet','.das','.rbf','.dat','.json'].some(ext=>String(f.name||'').toLowerCase().endsWith(ext))))throw new Error(I18N.t("Seleziona CSV, Parquet, FDA DAS4, RBF, DAT PDA3 e relativi JSON."));
  const graph=historyCharts.active,ctx=historyContext(graph),revision=++graph.loadRevision;ctx.status=I18N.join([{text:I18N.t("Importazione di ")},{value:(files.length)},{text:" file…"}]);ctx.loading=true;ctx.error=false;renderHistoryControls();H('history-open-local').disabled=true;
  try{
    const form=new FormData();for(const f of files)form.append('files',f,f.name);
    form.append('pda3_timezone_offset_minutes',H('history-dat-offset').value);form.append('join_pda3',String(H('history-dat-join').checked));
    const response=await fetch('/api/recordings/import',{method:'POST',headers:{'X-OPCUA-Token':token},body:form});const data=await response.json();if(!response.ok)throw new Error(data.error||response.statusText);
    if(!historyCharts.charts.includes(graph)||revision!==graph.loadRevision)return;
    selectHistoryFile({source:'local',...data.recording},graph);
    if((data.recordings||[]).length>1&&!data.sequence){graph.context.compareFiles=data.recordings.slice(0,32).map(f=>historyCompareEntry({source:'local',...f}));if(data.recordings.length>32)notice(I18N.t("Importati tutti i file; selezionati i primi 32 per il confronto."));renderHistoryFileList();await compareHistory(graph);}else await loadHistory(graph);
    await refreshHistoryFiles();const messages=[...(data.warnings||[]),...(data.errors||[])];if(messages.length){notice(messages.join(' · '),true);graph.context.status+=' · '+messages.join(' · ');renderHistoryControls();}
  }catch(error){ctx.status=error.message;ctx.error=true;throw error;}
  finally{ctx.loading=false;H('history-open-local').disabled=applicationClosed;H('history-local-file').value='';if(graph===historyCharts.active)renderHistoryControls();}
}
async function loadHistory(graph=historyCharts.active){
  if(graph===historyCharts.active)storeHistoryInputs(graph);
  const ctx=historyContext(graph),file=ctx.file;if(!file)throw new Error(I18N.t("Seleziona prima una registrazione."));
  let ids=[...ctx.chosen];if(!file.signals?.length)ids=null;else if(!ids.length)throw new Error(I18N.t("Seleziona almeno un segnale per il grafico."));
  const start=msFromLocalInput(ctx.from),end=msFromLocalInput(ctx.to),revision=++graph.loadRevision;
  ctx.loading=true;ctx.error=false;ctx.status=I18N.join([{text:"Lettura di "},{value:(file.display_name||file.name)},{text:"…"}]);if(graph===historyCharts.active)renderHistoryControls();
  try{
    const data=await request('/api/recordings/samples',{source:file.source||'repository',name:file.name,signal_ids:ids,start_ms:start,end_ms:end});
    if(!historyCharts.charts.includes(graph)||revision!==graph.loadRevision||ctx!==graph.context)return;
    const nodes=new Map((data.signals||[]).map(s=>[s.nodeid,{nodeid:s.nodeid,display_name:s.display_name||s.nodeid,unit:s.unit||'',calculated:!!s.calculated}])),types=Object.fromEntries((data.signals||[]).map(s=>[s.nodeid,s.data_type||'Double']));
    if(!file.signals?.length){file.signals=(data.signals||[]).map(s=>({nodeid:s.nodeid,display_name:s.display_name||s.nodeid,unit:s.unit||'',calculated:!!s.calculated}));ctx.chosen=new Set(file.signals.map(s=>s.nodeid));}
    if(data.available_calculations){const byId=new Map((file.signals||[]).map(s=>[s.nodeid,s]));for(const s of data.available_calculations)byId.set(s.nodeid,{...s,virtual:true});file.signals=[...byId.values()];}ctx.loaded=true;ctx.stats=data;ctx.status=I18N.join([{value:(file.display_name||file.name)},{text:" · "},{value:(Number(data.numeric_rows||0).toLocaleString(I18N.locale()))},{text:" campioni numerici"},{value:(I18N.t(" · piena risoluzione"))}]);
    if(start===null&&Number.isFinite(data.start_ms))ctx.from=localInputFromMs(data.start_ms);if(end===null&&Number.isFinite(data.end_ms))ctx.to=localInputFromMs(data.end_ms);
    if(ctx.resetViewOnOpen){resetHistoryView(graph);ctx.resetViewOnOpen=false;}
    historyCharts.setGraphData(graph,data.series||{},nodes,types);
  }catch(error){if(!historyCharts.charts.includes(graph)||ctx!==graph.context)return;ctx.status=error.message;ctx.error=true;throw error;}
  finally{ctx.loading=false;if(graph===historyCharts.active)renderHistoryControls();}
}
function scheduleHistoryMarkers(){clearTimeout(historyMarkerTimer);historyMarkerTimer=setTimeout(resolveHistoryMarkers,80);}
async function resolveHistoryMarkers(){
  const markers={...historyCharts.markers};if(!Number.isFinite(markers.x1)||!Number.isFinite(markers.x2))return;
  const cache=new Map();
  await Promise.all(historyCharts.charts.map(async graph=>{
    const ctx=historyContext(graph),file=ctx.file,ids=[...graph.ids],seq=++ctx.markerSeq,revision=graph.loadRevision;
    if(!ctx.loaded||!file||!ids.length)return;
    const body={source:file.source||'repository',name:file.name,signal_ids:ids,x1:markers.x1,x2:markers.x2},key=JSON.stringify(body);
    try{
      if(!cache.has(key))cache.set(key,request('/api/recordings/markers',body));const result=await cache.get(key);
      if(!historyCharts.charts.includes(graph)||seq!==ctx.markerSeq||revision!==graph.loadRevision||ctx!==graph.context||markers.x1!==historyCharts.markers.x1||markers.x2!==historyCharts.markers.x2)return;
      graph.trend.exactMarkerRows=new Map((result.markers.signals||[]).map(row=>[row.nodeid,row]));graph.trend.updateMarkerTable();
    }catch(error){if(ctx===graph.context&&seq===ctx.markerSeq){graph.trend.exactMarkerRows=new Map();graph.trend.updateMarkerTable();notice('Marker '+graph.name+': '+error.message,true);}}
  }));
}
function setHistorySignals(checked){const ctx=historyContext();ctx.chosen=checked?new Set((ctx.file?.signals||[]).slice(0,64).map(s=>s.nodeid)):new Set();renderHistorySignals();if(checked&&(ctx.file?.signals||[]).length>64)notice(I18N.t("Selezionati i primi 64 segnali. Puoi distribuire gli altri su ulteriori grafici."));}
H('nav-live').addEventListener('click',()=>showAppPage('live'));H('nav-recordings').addEventListener('click',()=>showAppPage('recordings'));
H('history-refresh').addEventListener('click',()=>refreshHistoryFiles().catch(e=>notice(e.message,true)));
H('history-open-local').addEventListener('click',()=>H('history-local-file').click());
H('history-local-file').addEventListener('change',()=>importHistoryLocalFile([...H('history-local-file').files]).catch(e=>notice(e.message,true)));
H('history-dat-offset').value=String(-new Date().getTimezoneOffset());
H('history-load').addEventListener('click',()=>loadHistory().catch(e=>notice(e.message,true)));
H('history-close-file').addEventListener('click',()=>closeHistoryFile());
H('history-close-all').addEventListener('click',closeAllHistoryFiles);
H('history-full-range').addEventListener('click',()=>{H('history-from').value='';H('history-to').value='';loadHistory().catch(e=>notice(e.message,true));});
H('history-select-all').addEventListener('click',()=>setHistorySignals(true));H('history-select-none').addEventListener('click',()=>setHistorySignals(false));
H('history-signal-search').addEventListener('input',renderHistorySignals);
for(const id of ['history-file-search','history-filter-format','history-duration-min','history-duration-max'])H(id).addEventListener('input',renderHistoryFileList);
H('history-compare').addEventListener('click',()=>compareHistory().catch(e=>notice(e.message,true)));
H('history-compare-clear').addEventListener('click',()=>{historyContext().compareFiles=[];historyContext().compareStatus='';renderHistoryFileList();});
H('history-compare-mode').addEventListener('change',()=>{historyContext().compareMode=H('history-compare-mode').value;renderHistoryComparison();});
H('history-compare-coverage').addEventListener('change',()=>{historyContext().compareCoverage=H('history-compare-coverage').value;renderHistoryComparison();});
