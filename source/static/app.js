/* OPC UA Web Client V1.1.15 — acquisizione live */
const $=id=>document.getElementById(id),token=document.querySelector('meta[name="opcua-token"]').content;
let recordState={state:'idle',busy:false},recordDirectory='',recordStarting=false,applicationClosed=false;
let selected=new Map(),series={},types={},discovered=[],savedServerData={},maxSelected=64,connected=false,state='disconnected';
let polling=false,pollTimer=null,selectionQueue=Promise.resolve(),scanJob='',scanNodes=[],scanOffset=0,scanTimer=null,page=0,view='list';
let scanRunning=false,filtered=[],lastValues=[],noticeTimer=null,clearRevision=0,connectionBusy=false;
function recordingLocked(){return !!(recordState.selection_locked??recordState.busy);}
let liveViewMode='all';
function liveValuesURL(){return '/api/values?view='+liveViewMode;}
function setLiveViewMode(mode){liveViewMode=mode==='window'?'window':'all';$('live-time-view').value=liveViewMode;}
$('live-time-view').addEventListener('change',event=>{setLiveViewMode(event.target.value);clearRevision++;liveCharts.timeRange=null;liveCharts.resetViews();scheduleWorkspaceSave();if(!polling)updateValues();});
const pageSize=250;
const liveCharts=new ChartWorkspace({scope:'live',root:'live-charts',selector:'live-active-chart',add:'live-add-chart',layout:'live-layout',allowSignalDrop:true,copySignalsOnAdd:false,
  onActive:()=>renderSelected(),onAssignment:()=>renderSelected(),onSignalDrop:(graph,id)=>notice(I18N.join([{text:"Segnale aggiunto a "},{value:(graph.name)},{text:"."}])),onChange:()=>{I18N.bind($('savestatus'),"textContent",()=>(I18N.t("Layout modificato. Usa “Salva impostazione” per conservarlo.")),true);}});
const livePageLayout=new ResizablePageLayout('live-page','live-page-splitter','opcua.live.sidebar.width',()=>liveCharts.draw());
const trend=liveCharts.charts[0].trend; // First-chart compatibility for existing diagnostics.

async function request(url,body,method){
  const options={headers:{'X-OPCUA-Token':token}};
  let timer=null,controller=null;const timeoutMs=(url.startsWith('/api/s7/')||url.startsWith('/api/browse')||url.startsWith('/api/node'))?190000:url==='/api/ads/test'?40000:url==='/api/record/start'?45000:url==='/api/recordings/location/browse'?1800000:url.startsWith('/api/analysis/')?600000:(url.startsWith('/api/recordings/')||url.startsWith('/api/calculations/'))?240000:30000;
  if(typeof AbortController!=='undefined'){
    controller=new AbortController();options.signal=controller.signal;
    timer=setTimeout(()=>controller.abort(),timeoutMs);
  }
  if(body!==undefined||method){
    options.method=method||'POST';
    if(typeof FormData!=='undefined'&&body instanceof FormData)options.body=body;
    else if(body!==undefined){options.headers['Content-Type']='application/json';options.body=JSON.stringify(body);}
  }
  try{
    const response=await fetch(url,options);let data;
    if(response.ok&&String(response.headers.get('Content-Type')||'').startsWith('application/x-space-analyzer-samples'))return decodeSampleBuffer(await response.arrayBuffer());
    try{data=await response.json();}catch{throw new Error(I18N.t("Risposta del servizio locale non valida"));}
    if(!response.ok)throw new Error(I18N.message(data.error||response.statusText));return data;
  }catch(e){
    if(e&&e.name==='AbortError')throw new Error(I18N.join([{text:"Timeout del servizio locale ("},{value:(timeoutMs/1000)},{text:" s)."}]));
    throw e;
  }finally{if(timer!==null)clearTimeout(timer);}
}
function notice(text,error=false){clearTimeout(noticeTimer);I18N.bind($('msg'),"textContent",()=>(text),true);$('msg').className='message'+(error?' error':'');$('msg').hidden=false;const feedback=$('connection-feedback');if($('connection-dialog').open&&feedback){feedback.hidden=false;I18N.bind(feedback,"textContent",()=>(text),true);feedback.className='connection-feedback'+(error?' error':'');}if(!error)noticeTimer=setTimeout(()=>$('msg').hidden=true,7500);}
function config(){return{ui_language:I18N.language,endpoint:$('endpoint').value.trim(),security:$('security').value,security_mode:$('security_mode').value,username:$('username').value,password:$('password').value,application_uri:$('application_uri').value.trim(),sample_ms:Number($('sample_ms').value),window_seconds:Number($('window_seconds').value),max_samples:Number($('max_samples').value),buffer_mode:$('buffer_mode').value,root_nodeid:$('root_nodeid').value.trim()||'i=85',scan_limit:Number($('scan_limit').value),auto_reconnect:$('auto_reconnect').checked,...(window.sourceConfig?.()||{})};}
function securityChanged(){if($('security').value==='None')$('security_mode').value='None';else if($('security_mode').value==='None')$('security_mode').value='SignAndEncrypt';}
function serverCert(info){$('server_cert_abs').value=info.path||'';I18N.bind($('servercertstatus'),"textContent",()=>(info.present?I18N.join([{text:"Presente · SHA-256: "},{value:(info.thumbprint_sha256)}]):I18N.t("Nessun certificato server: acquisiscilo per usare Sign / SignAndEncrypt.")),true);I18N.bind($('cert-badge'),"textContent",()=>(info.present?I18N.t("Server presente"):I18N.t("Server assente")),true);}
async function loadConfig(){
  const c=await request('/api/config');window.initialWorkspace=c.workspace||{};if(c.workspace?.config){Object.assign(c,c.workspace.config);if(!c.recording_busy)c.selected_nodes=c.workspace.selected_nodes||c.selected_nodes;if(c.workspace.live)c.saved_server_data={...(c.saved_server_data||{}),chart_layout:c.workspace.live};}recordDirectory=c.recordings_dir||'';I18N.restore(c.ui_language);
  for(const k of ['endpoint','security','security_mode','username','password','application_uri','sample_ms','window_seconds','max_samples','buffer_mode','root_nodeid','scan_limit','client_cert_abs','client_key_abs']){
    if($(k).tagName==='SELECT'&&![...$(k).options].some(o=>o.value===String(c[k])))$(k).add(I18N.option(()=>(String(c[k])),String(c[k]),false,false,false));$(k).value=c[k]??'';
  }
  $('auto_reconnect').checked=c.auto_reconnect;maxSelected=c.max_selected||64;
  serverCert({path:c.server_cert_abs,present:c.server_cert_present,thumbprint_sha256:c.server_cert_sha256});securityChanged();
  window.loadSourceConfig?.(c);
  selected=new Map((c.selected_nodes||[]).map(n=>[n.nodeid,n]));window.loadCalculations?.(c.calculations||{});savedServerData=c.saved_server_data||{};
  liveCharts.restore(savedServerData.chart_layout,(window.liveDataNodes?.(selected)||selected));renderSelected();
  discovered=savedServerData.discovered_endpoints||[];renderEndpoints();
  if(c.saved_at)I18N.bind($('savestatus'),"textContent",()=>(I18N.join([{text:"Ultimo salvataggio: "},{value:(new Date(c.saved_at).toLocaleString(I18N.locale()))}])),true);
  I18N.bind($('data-dir'),"textContent",()=>(I18N.join([{text:"Cartella impostazioni: "},{value:(c.data_dir)}])),true);await syncSelection();
}
function setState(next,error=''){state=next;connected=next==='connected';const labels={connected:I18N.t("Connesso"),disconnected:I18N.t("Non connesso"),connecting:I18N.t("Connessione…"),reconnecting:I18N.t("Riconnessione…")};I18N.bind($('status'),"textContent",()=>(labels[next]||next),true);$('status').className='status'+(connected?' ok':error?' bad':'');I18N.bind($('status'),"title",()=>(error||''),false);
  $('connect').disabled=connectionBusy||next==='connecting';$('disconnect').disabled=next==='disconnected'||connectionBusy;
  for(const id of ['regenerate','fetch-cert','upload-cert'])$(id).disabled=next!=='disconnected';window.refreshSourceState?.();}
function renderEndpoints(){const host=$('endpointlist');host.replaceChildren();host.hidden=!discovered.length;for(const ep of discovered){const b=document.createElement('button');b.className='ep';const title=document.createElement('b'),url=document.createElement('span');I18N.bind(title,"textContent",()=>(ep.security+' / '+ep.security_mode),false);url.className='mono';I18N.bind(url,"textContent",()=>(ep.endpoint_url),false);b.append(title,url);const supported=[...$('security').options].some(o=>o.value===ep.security);b.disabled=!supported;I18N.bind(b,"title",()=>(supported?I18N.t("Usa questa policy mantenendo l’indirizzo server inserito"):I18N.t("Policy non supportata")),true);b.onclick=()=>{$('security').value=ep.security;$('security_mode').value=ep.security_mode;securityChanged();notice(I18N.t("Selezionata ")+title.textContent+'. Indirizzo server: '+$('endpoint').value);};host.append(b);}}
async function discover(){notice(I18N.t("Rilevamento endpoint in corso…"));const j=await request('/api/endpoints',{endpoint:$('endpoint').value.trim()});discovered=j.endpoints;renderEndpoints();const cfg=config();if(!discovered.some(e=>e.security===cfg.security&&e.security_mode===cfg.security_mode)){const ep=discovered.find(e=>e.security==='Basic256Sha256'&&e.security_mode==='SignAndEncrypt')||discovered.find(e=>e.security==='Basic256Sha256'&&e.security_mode==='Sign');if(ep){$('security').value=ep.security;$('security_mode').value=ep.security_mode;}}notice(I18N.join([{value:(discovered.length)},{text:" endpoint rilevati."}]));}
async function connect(){connectionBusy=true;setState('connecting');try{securityChanged();await selectionQueue;await request('/api/connect',config());liveCharts.resetViews();liveCharts.resetMarkers();series={};liveCharts.update(series,(window.liveDataNodes?.(selected)||selected));setState('connected');closeConnection();notice(I18N.join([{text:"Sessione "},{value:(window.sourceLabel?.()||'OPC UA')},{text:" attiva. Esplora i rami o avvia la scansione completa."}]));await browseRoot().catch(e=>notice(I18N.join([{text:"Connessione attiva; esplorazione: "},{value:(e.message)}]),true));}catch(e){setState('disconnected',e.message);throw e;}finally{connectionBusy=false;setState(state);}}
async function disconnect(){await request('/api/disconnect',{},'POST');setState('disconnected');notice(I18N.t("Sessione disconnessa."));}
async function save(){await selectionQueue;const c=config(),serverData={...savedServerData,active_endpoint:c.endpoint,security:c.security,security_mode:c.security_mode,discovered_endpoints:discovered,chart_layout:liveCharts.serialize()};const j=await request('/api/settings/save',{config:c,selected_nodes:[...selected.values()],server_data:serverData});savedServerData=serverData;I18N.bind($('savestatus'),"textContent",()=>(I18N.join([{text:"Ultimo salvataggio: "},{value:(new Date(j.saved_at).toLocaleString(I18N.locale()))}])),true);await saveWorkspace(true);notice(I18N.t("Salvati parametri, NodeId, dati del server, certificati e layout. Le password marcate “solo sessione” non vengono salvate."));}
async function liveConfig(){await request('/api/config',{sample_ms:Number($('sample_ms').value),window_seconds:Number($('window_seconds').value),max_samples:Number($('max_samples').value),buffer_mode:$('buffer_mode').value,auto_reconnect:$('auto_reconnect').checked});notice(I18N.t("Parametri di acquisizione aggiornati."));}
async function regenerate(){const j=await request('/api/certificate/regenerate',{application_uri:$('application_uri').value.trim()});$('application_uri').value=j.application_uri;$('client_cert_abs').value=j.info.path;$('client_key_abs').value=j.info.key_path;notice(I18N.t("Certificato client rigenerato. Esporta client.der e autorizzalo sul server."));}
async function fetchCert(){const j=await request('/api/server-certificate/fetch',config());serverCert(j.info);notice(I18N.t("Certificato server acquisito. Verifica l’impronta SHA-256 nella sezione Certificati."));}
async function uploadCert(){const file=$('serverfile').files[0];if(!file)throw new Error(I18N.t("Seleziona prima un certificato server."));const data=new FormData();data.append('file',file);serverCert((await request('/api/server-certificate',data)).info);notice(I18N.t("Certificato server importato."));}
function syncSelection(){const ids=[...selected.keys()];selectionQueue=selectionQueue.catch(()=>{}).then(()=>request('/api/select',{nodeids:ids}));return selectionQueue;}
async function toggle(node,on){if(recordingLocked())throw new Error(I18N.t("Premere Stop prima di cambiare i segnali."));if(on&&!selected.has(node.nodeid)&&selected.size>=maxSelected){syncCheckboxes();throw new Error(I18N.join([{text:"Massimo "},{value:(maxSelected)},{text:" variabili nel grafico."}]));}if(on)selected.set(node.nodeid,node);else{selected.delete(node.nodeid);delete series[node.nodeid];}renderSelected();syncCheckboxes();await syncSelection();}
function syncCheckboxes(){document.querySelectorAll('#live-page input[data-nodeid]').forEach(cb=>cb.checked=selected.has(cb.dataset.nodeid));}
function selectionBox(node){const cb=document.createElement('input');cb.type='checkbox';cb.disabled=!!recordingLocked();cb.dataset.nodeid=node.nodeid;cb.checked=selected.has(node.nodeid);I18N.attr(cb,'aria-label',()=>(I18N.join([{text:"Seleziona "},{value:(node.display_name)}])));cb.addEventListener('change',()=>toggle(node,cb.checked).catch(e=>notice(e.message,true)));return cb;}
function renderSelected(){
  liveCharts.update(series,(window.liveDataNodes?.(selected)||selected),types);
  const host=$('selected');host.replaceChildren();const display=window.liveDataNodes?.(selected)||selected;I18N.bind($('selection-count'),"textContent",()=>(I18N.join([{value:(selected.size)},{text:" acquisiti · "},{value:(display.size-selected.size)},{text:" calcolati"}])),true);
  if(!display.size){const p=document.createElement('p');p.className='empty';I18N.bind(p,"textContent",()=>(I18N.t("Connetti il client e seleziona le variabili dall’albero oppure inserisci un NodeId.")),true);host.append(p);}
  for(const [id,node] of display){
    const row=document.createElement('div');row.className='selected-row';row.dataset.id=id;row.draggable=true;I18N.bind(row,"title",()=>(I18N.t("Trascina questo segnale su un grafico")),true);
    row.addEventListener('dragstart',event=>{if(!event.dataTransfer)return;event.dataTransfer.effectAllowed='copy';event.dataTransfer.setData('application/x-opcua-signal',id);event.dataTransfer.setData('text/plain',id);row.classList.add('dragging-signal');});
    row.addEventListener('dragend',()=>row.classList.remove('dragging-signal'));
    const grip=document.createElement('span');grip.className='signal-drag-handle';I18N.bind(grip,"textContent",()=>('⋮⋮'),false);grip.setAttribute('aria-hidden','true');
    const visible=document.createElement('input');visible.type='checkbox';visible.className='chart-assignment';visible.dataset.signalId=id;
    visible.checked=liveCharts.active.ids.has(id);I18N.attr(visible,'aria-label',()=>(I18N.join([{text:"Mostra "},{value:(node.display_name||id)},{text:" in "},{value:(liveCharts.active.name)}])));
    visible.addEventListener('change',()=>liveCharts.assign(id,visible.checked));
    const color=document.createElement('span');color.className='selected-color';color.style.background=liveCharts.color(id);
    const name=document.createElement('div'),title=document.createElement('strong'),sub=document.createElement('div');title.className='signal-name';I18N.bind(title,"textContent",()=>(treeLabel(node)),false);I18N.bind(title,"title",()=>(id),false);sub.className='signal-id mono';I18N.bind(sub,"textContent",()=>(id),false);name.append(title,sub);
    const box=document.createElement('div'),value=document.createElement('div'),quality=document.createElement('div');box.className='valuebox';value.className='value';I18N.bind(value,"textContent",()=>('—'),false);quality.className='quality';I18N.bind(quality,"textContent",()=>(I18N.t("In attesa")),true);box.append(value,quality);
    const remove=document.createElement('button');remove.className='remove';I18N.bind(remove,"textContent",()=>('×'),false);I18N.attr(remove,'aria-label',()=>(I18N.join([{text:"Rimuovi "},{value:(node.display_name||id)},{text:" dall’acquisizione"}])));remove.onclick=()=>(node.calculated?window.disableCalculation(id):toggle(node,false)).catch(e=>notice(e.message,true));
    row.append(grip,visible,color,name,box,remove);host.append(row);
  }
  renderValues(lastValues);applyRecordingControls();
}
function renderValues(values){const map=new Map(values.map(v=>[v.nodeid,v]));for(const row of $('selected').children){const v=map.get(row.dataset.id);if(!v)continue;const quality=row.querySelector('.quality');I18N.bind(row.querySelector('.value'),"textContent",()=>(v.error?'—':valueText(v.value)),false);I18N.bind(quality,"textContent",()=>(I18N.join([{value:((v.status||'')+(v.data_type?' · '+v.data_type:'')+(v.precision_warning?I18N.t(" · grafico approssimato"):''))}])),true);quality.className='quality'+(!v.good?' bad':'');I18N.bind(quality,"title",()=>([v.error||'',v.source_ts?I18N.t("Source: ")+v.source_ts:'',v.server_ts?I18N.join([{text:"Server: "},{value:(v.server_ts)}]):''].filter(Boolean).join('\n')),true);types[v.nodeid]=v.data_type;}}
async function addNode(){if(!connected)throw new Error(I18N.t("Connetti il client per verificare il NodeId."));const j=await request('/api/node?nodeid='+encodeURIComponent(window.sourceNode?.($('manual-node').value.trim())||$('manual-node').value.trim()));if(j.node.node_class!=='Variable')throw new Error(I18N.join([{text:"Il nodo è "},{value:(j.node.node_class)},{text:": usalo come nodo di partenza per esplorare le sue variabili."}]));await toggle(j.node,true);$('manual-node').value='';}
async function removeAll(){if(recordingLocked())throw new Error(I18N.t("Premere Stop prima di cambiare i segnali."));selected.clear();series={};lastValues=[];renderSelected();syncCheckboxes();await syncSelection();}
function startPolling(){if(polling)return;polling=true;updateValues();}
async function updateValues(){const revision=clearRevision;try{const j=await request(liveValuesURL());if(revision!==clearRevision)return;series=j.samples||{};lastValues=j.values||[];if(!connectionBusy)setState(j.state,j.error);if(j.recording)renderRecording(j.recording);window.renderAdsInfo?.(j.ads);window.renderS7Info?.(j.s7);window.renderMqttInfo?.(j.mqtt);window.parallelMqttStatus=j.parallel_mqtt;window.sourceSessionsStatus=j.sources||{};window.refreshSourceState?.();window.refreshLiveExtras?.();window.renderCalculationStatus?.(j.calculations,lastValues);renderValues(lastValues);const d=j.diagnostics||{},ms=v=>v===null||v===undefined?'—':valueText(v)+' ms';renderLiveSession(d);window.renderAdsCyclicDiagnostics?.(d);window.renderS7UDPDiagnostics?.(d);window.renderMqttDiagnostics?.(d.mqtt);I18N.bind($('requested-ms'),"textContent",()=>(ms(d.requested_ms)),false);I18N.bind($('mean-ms'),"textContent",()=>(ms(d.mean_period_ms)),false);I18N.bind($('read-ms'),"textContent",()=>(ms(d.read_cycle_ms)),false);I18N.bind($('read-count'),"textContent",()=>(valueText(d.read_count||0)),false);const age=d.last_sample_at?Math.max(0,Date.now()-d.last_sample_at):null;const bad=lastValues.filter(v=>!v.good),valid=lastValues.filter(v=>v.good&&v.numeric);if(selected.size&&valid.length===0&&bad.length){const counts={};for(const v of bad){const k=v.status||v.error||'Bad';counts[k]=(counts[k]||0)+1;}liveCharts.setEmptyMessage(I18N.join([{text:"Nessun valore numerico valido · "},{value:(Object.entries(counts).map(([k,n])=>k+' '+n+'/'+lastValues.length).join(' · '))}]));}else if(selected.size&&valid.length===0){liveCharts.setEmptyMessage(I18N.t("In attesa del primo campione valido…"));}else liveCharts.setEmptyMessage(I18N.t("Seleziona una variabile numerica per avviare il trend"));const mode=d.acquisition_mode?I18N.join([{text:"Modalità: "},{value:(d.acquisition_mode)},{text:" · "}]):'';const quality=bad.length?I18N.join([{text:" · Qualità non valida: "},{value:(bad.length)},{text:"/"},{value:(lastValues.length)}]):'';I18N.bind($('acqdiag'),"textContent",()=>(j.error?I18N.join([{text:"Connessione: "},{value:(j.error)}]):I18N.join([{value:(mode)},{text:"Ultimo periodo: "},{value:(ms(d.actual_period_ms))},{text:" · "},{value:(age===null?I18N.t("In attesa dei primi campioni."):I18N.join([{text:"Ultimo campione ricevuto "},{value:((age/1000).toFixed(1))},{text:" s fa."}]))},{value:(quality)},{text:" · Periodo richiesto "},{value:(d.requested_ms)},{text:" ms subordinato a server, rete e PC."}])),true);if(j.source==='mqtt')I18N.bind($('acqdiag'),"textContent",()=>(I18N.join([{value:(j.error?I18N.join([{text:"Connessione: "},{value:(j.error)},{text:" · "}]):'')},{text:"MQTT · ogni pubblicazione · "},{value:(d.mqtt?.timestamp_origin||'ricezione')},{text:" · Il periodo indicativo non limita i campioni; il broker e i publisher determinano frequenza e QoS."}])),true);liveCharts.update(series,(window.liveDataNodes?.(selected)||selected),types);I18N.bind($('buffer-diag'),"textContent",()=>(I18N.join([{text:"Buffer "},{value:(d.buffer_mode==='samples'?I18N.t("a campioni"):d.window_seconds+' s')},{text:" · limite effettivo "},{value:((d.buffer_limit_per_signal||0).toLocaleString(I18N.locale()))},{text:" campioni/segnale · Good/Uncertain/Bad conservati con i timestamp."}])),true);}catch(e){setState('disconnected',e.message);I18N.bind($('acqdiag'),"textContent",()=>(I18N.join([{text:"Servizio locale non raggiungibile: "},{value:(e.message)}])),true);}finally{if(polling)pollTimer=setTimeout(updateValues,250);}}
async function clear(){clearRevision++;await request('/api/samples/clear',{});liveCharts.resetViews();liveCharts.resetMarkers();series={};liveCharts.update(series,(window.liveDataNodes?.(selected)||selected),types);}
function setView(next){view=next;$('scan-list').hidden=next!=='list';$('tree').hidden=next!=='tree';$('view-list').classList.toggle('active',next==='list');$('view-tree').classList.toggle('active',next==='tree');$('search').disabled=next==='tree';$('select-filtered').disabled=next==='tree';$('variables-only').disabled=next==='tree';renderScan();applyRecordingControls();}
async function browseRoot(){if(window.mqttBrowseActive?.())return window.refreshMqttCatalog(true);if(!connected)throw new Error(I18N.t("Connetti prima il client."));const root=window.sourceNode?.($('root_nodeid').value.trim())||$('root_nodeid').value.trim()||'i=85';$('tree').replaceChildren();setView('tree');const j=await request('/api/node?nodeid='+encodeURIComponent(root));const element=treeNode(j.node,new Set());$('tree').append(element);await element.expand();}
// Short ADS tree labels are presentation only; canonical NodeIds remain intact.
function treeLabel(node){
  const id=String(node.nodeid||'');
  if(id.startsWith('ads:')&&id!=='ads:/'){
    const path=id.slice(4);return path.slice(path.lastIndexOf('.')+1)||path;
  }
  return node.display_name||node.browse_name||id;
}
function treeNode(node,ancestors){const wrap=document.createElement('div'),row=document.createElement('div'),exp=document.createElement('button');row.className='node';exp.className='expander';I18N.bind(exp,"textContent",()=>('›'),false);I18N.attr(exp,'aria-label',()=>(I18N.join([{text:"Espandi "},{value:(treeLabel(node))}])));exp.setAttribute('aria-expanded','false');row.append(exp);if(node.node_class==='Variable'&&!node.remote)row.append(selectionBox(node));const name=document.createElement('span'),cls=document.createElement('span'),nid=document.createElement('span');name.className='name';I18N.bind(name,"textContent",()=>(treeLabel(node)),false);I18N.bind(name,"title",()=>(node.nodeid),false);cls.className='cls';I18N.bind(cls,"textContent",()=>(node.node_class+(node.data_type?' · '+node.data_type:'')),false);I18N.bind(row,"title",()=>([node.nodeid,node.comment].filter(Boolean).join('\n')),false);nid.className='mono';I18N.bind(nid,"textContent",()=>(node.nodeid),false);row.append(name,cls);if(!String(node.nodeid||'').startsWith('ads:')&&!String(node.nodeid||'').startsWith('s7:sym:')&&!String(node.nodeid||'').startsWith('mqtt:'))row.append(nid);const children=document.createElement('div');children.className='indent';children.hidden=true;wrap.append(row,children);let loaded=false,loading=false;const cycle=ancestors.has(node.nodeid);exp.disabled=cycle||node.remote||node.has_children===false;if(cycle)I18N.bind(exp,"title",()=>(I18N.t("Riferimento a un antenato: ciclo già esplorato")),true);wrap.expand=async()=>{if(exp.disabled||loading)return;if(!loaded){loading=true;I18N.bind(exp,"textContent",()=>('…'),false);try{const j=await request('/api/browse?nodeid='+encodeURIComponent(node.nodeid));const next=new Set(ancestors);next.add(node.nodeid);children.replaceChildren(...j.nodes.map(n=>treeNode(n,next)));if(j.warning)notice(j.warning,true);let cursor=j.next_offset;const more=document.createElement('button');I18N.bind(more,"textContent",()=>(I18N.t("Carica altri…")),true);more.onclick=async()=>{more.disabled=true;try{const page=await request('/api/browse?nodeid='+encodeURIComponent(node.nodeid)+'&offset='+cursor);more.before(...page.nodes.map(n=>treeNode(n,next)));cursor=page.next_offset;if(cursor==null)more.remove();}catch(e){notice(e.message,true);}finally{more.disabled=false;}};if(cursor!=null)children.append(more);loaded=true;if(!j.nodes.length){exp.disabled=true;I18N.bind(exp,"textContent",()=>('·'),false);return;}}finally{loading=false;if(!loaded)I18N.bind(exp,"textContent",()=>('›'),false);}}children.hidden=!children.hidden;I18N.bind(exp,"textContent",()=>(children.hidden?'›':'⌄'),false);exp.setAttribute('aria-expanded',String(!children.hidden));};exp.onclick=()=>wrap.expand().catch(e=>notice(e.message,true));return wrap;}
async function startScan(){if(window.mqttBrowseActive?.())return window.refreshMqttCatalog(false);if(!connected)throw new Error(I18N.t("Connetti prima il client."));clearTimeout(scanTimer);const j=await request('/api/scan',config());scanJob=j.id;scanRunning=true;scanNodes=[];scanOffset=0;page=0;setView('list');$('cancel-scan').disabled=false;$('scan').disabled=true;I18N.bind($('scan-status'),"textContent",()=>(I18N.t("Scansione in corso…")),true);$('scan-progress').removeAttribute('value');pollScan(scanJob);}
async function pollScan(id){try{const j=await request('/api/scan?id='+encodeURIComponent(id)+'&offset='+scanOffset+'&count=1000');if(id!==scanJob)return;scanNodes.push(...j.nodes);scanOffset=j.next_offset;const running=j.state==='running',more=scanOffset<j.total;scanRunning=running;const labels={running:I18N.t("Scansione in corso"),completed:I18N.t("Scansione completata"),completed_with_errors:I18N.t("Completata con nodi non accessibili"),cancelled:I18N.t("Scansione interrotta"),limited:I18N.t("Limite raggiunto: risultati parziali. Aumenta il limite per continuare con una nuova scansione."),failed:I18N.t("Scansione fallita")};I18N.bind($('scan-status'),"textContent",()=>(I18N.join([{value:(labels[j.state]||j.state)},{text:" · "},{value:(j.total)},{text:" nodi · "},{value:(j.visited)},{text:" esplorati · "},{value:(j.error_count)},{text:" errori"},{value:(j.error?' · '+j.error:'')}])),true);I18N.bind($('result-count'),"textContent",()=>(I18N.join([{value:(j.total)},{text:" nodi rilevati"}])),true);$('cancel-scan').disabled=!running;$('scan').disabled=running;if(!running)$('scan-progress').value=1;else $('scan-progress').removeAttribute('value');$('scan-errors').hidden=!j.error_count;if(j.error_count){$('scan-error-list').replaceChildren(...j.errors.map(e=>{const p=document.createElement('p');I18N.bind(p,"textContent",()=>(e.nodeid+': '+e.error),false);return p;}));}renderScan();if(running||more)scanTimer=setTimeout(()=>pollScan(id),more?20:350);}catch(e){I18N.bind($('scan-status'),"textContent",()=>(I18N.join([{text:"Scansione: "},{value:(e.message)}])),true);$('scan').disabled=false;$('cancel-scan').disabled=false;notice(e.message,true);}}
async function cancelScan(){await request('/api/scan/cancel',{});if(scanJob){clearTimeout(scanTimer);await pollScan(scanJob);}}
function renderScan(){
  const query=$('search').value.trim().toLocaleLowerCase(I18N.locale()),only=$('variables-only').checked;
  filtered=scanNodes.filter(n=>(!only||n.node_class==='Variable')&&(!query||[n.display_name,n.nodeid,n.path].some(v=>String(v||'').toLocaleLowerCase(I18N.locale()).includes(query))));
  page=Math.max(0,Math.min(page,Math.ceil(filtered.length/pageSize)-1));
  const visible=filtered.slice(page*pageSize,(page+1)*pageSize),body=$('scan-body');
  const existing=new Map([...body.children].map(r=>[r.dataset.nodeid,r])),keep=new Set(visible.map(n=>n.nodeid));
  for(const row of [...body.children])if(!keep.has(row.dataset.nodeid))row.remove();
  visible.forEach((node,index)=>{
    let tr=existing.get(node.nodeid);
    if(!tr){tr=document.createElement('tr');tr.dataset.nodeid=node.nodeid;const pick=document.createElement('td');if(node.node_class==='Variable'&&!node.remote)pick.append(selectionBox(node));else pick.textContent='—';tr.append(pick);for(let i=0;i<4;i++)tr.append(document.createElement('td'));}
    tr.title=node.comment||'';
    [node.display_name,node.nodeid,node.node_class+(node.data_type?' · '+node.data_type:''),node.path].forEach((v,i)=>{const text=v||'';if(tr.children[i+1].textContent!==text)tr.children[i+1].textContent=text;});
    tr.children[2].className='mono';const cb=tr.querySelector('input');if(cb){cb.checked=selected.has(node.nodeid);cb.disabled=!!recordingLocked();}
    if(body.children[index]!==tr)body.insertBefore(tr,body.children[index]||null);
  });
  if(!visible.length&&!body.children.length){const tr=document.createElement('tr'),td=document.createElement('td');td.colSpan=5;td.className='empty';I18N.bind(td,'textContent',()=>scanNodes.length?I18N.t('Nessun nodo corrisponde al filtro.'):I18N.t('Avvia “Scansiona tutto” per trovare i dati del nodo scelto.'),true);tr.append(td);body.append(tr);}
  $('page-info').textContent=filtered.length?`${page*pageSize+1}–${Math.min((page+1)*pageSize,filtered.length)} / ${filtered.length}`:'0';
  $('page-prev').disabled=view==='tree'||page===0;$('page-next').disabled=view==='tree'||(page+1)*pageSize>=filtered.length;
}
async function selectFiltered(){if(recordingLocked())throw new Error(I18N.t("Premere Stop prima di cambiare i segnali."));const candidates=filtered.filter(n=>n.node_class==='Variable'&&!n.remote&&!selected.has(n.nodeid));if(selected.size+candidates.length>maxSelected)throw new Error(I18N.join([{text:"Il filtro contiene "},{value:(candidates.length)},{text:" nuove variabili. Affina la ricerca: massimo "},{value:(maxSelected)},{text:" selezionate."}]));for(const n of candidates)selected.set(n.nodeid,n);renderSelected();syncCheckboxes();await syncSelection();}
async function shutdown(){if(recordingLocked())await stopRecording();while(recordState.busy){notice(I18N.t('Attendo il completamento dei salvataggi prima di chiudere…'));await new Promise(r=>setTimeout(r,300));renderRecording((await request('/api/record/status')).recording);}try{await saveWorkspace(true);}catch(e){logFrontend(I18N.join([{text:"Workspace non aggiornato prima della chiusura: "},{value:(e.message)}]));}await request('/api/shutdown',{});applicationClosed=true;polling=false;clearTimeout(pollTimer);clearTimeout(scanTimer);setState('disconnected');notice(I18N.t("Applicazione chiusa. Puoi chiudere questa pagina."));document.querySelectorAll('button,input,select').forEach(e=>e.disabled=true);setTimeout(()=>{try{window.close();}catch{}},800);}
// Closing the application window stops the service: ask for confirmation while a recording is running.
window.addEventListener('beforeunload',event=>{if(!applicationClosed&&recordingLocked()){event.preventDefault();event.returnValue='';}});
function bind(id,fn,event='click'){
  const element=$(id);if(!element){console.error(I18N.t("Elemento UI mancante:"),id);return;}
  element.addEventListener(event,async()=>{
    const lock=event==='click';if(lock)element.disabled=true;
    try{await fn();}
    catch(e){notice((e&&e.message)||String(e),true);}
    finally{
      if(lock&&!applicationClosed){
        if(id==='scan')element.disabled=scanRunning;
        else if(id==='cancel-scan')element.disabled=!scanRunning;
        
        else element.disabled=false;
        if(['connect','disconnect','regenerate','fetch-cert','upload-cert'].includes(id))setState(state);applyRecordingControls();
      }
    }
  });
}
function applyRecordingControls(){
  const busy=!!recordingLocked();
  $('record').disabled=busy||recordStarting||!connected||applicationClosed;
  $('record-stop').disabled=!busy||recordState.state==='stopping'||applicationClosed;
  for(const id of ['remove-all','add-node','manual-node'])$(id).disabled=busy||applicationClosed;
  const acquiring=!!recordState.selection_locked||recordStarting;for(const id of ['record-directory','record-directory-browse','record-directory-default','history-location-change'])if($(id))$(id).disabled=acquiring||applicationClosed;
  $('select-filtered').disabled=busy||applicationClosed||view==='tree';
  document.querySelectorAll('#live-page input[data-nodeid],#selected .remove').forEach(e=>e.disabled=busy||applicationClosed);
}
function renderRecording(info){
  if(info.revision!==undefined&&recordState.revision!==undefined&&info.revision<recordState.revision)return;
  recordState=info;recordDirectory=info.directory||recordDirectory;syncRecordingsLocation();
  const box=$('record-status'),seconds=Number(info.duration_seconds||0).toFixed(1),rows=Number(info.rows_written||0).toLocaleString(I18N.locale());
  box.className='record-status'+(info.busy?' active':'')+(info.error?' error':'')+(info.state==='recording'&&!info.error?' recording':'');box.title='';
  if(info.error)I18N.bind(box,"textContent",()=>(I18N.join([{text:"Errore registrazione: "},{value:(info.error)},{text:" · "},{value:(info.path||'')}])),true);
  else if(info.state==='recording')renderRecordingBar(box,info);
  else if(info.state==='stopping')I18N.bind(box,"textContent",()=>(I18N.join([{value:(((info.conversion_phase||info.fda_phase)==='converting'?I18N.join([{text:"Generazione dei file "},{value:(info.format==='rbf'?'RBF':'FDA')},{text:" in corso… "}]):I18N.t("Chiusura del file in corso… "))+info.path)}])),true);
  else if(info.state==='stopped')I18N.bind(box,"textContent",()=>(I18N.join([{text:"Registrazione salvata · "},{value:(seconds)},{text:" s · "},{value:(rows)},{text:" righe · Parte "},{value:(info.part||1)},{text:" · Coda "},{value:(info.queue_cycles||0)},{text:"/"},{value:(info.queue_capacity_cycles||0)},{text:" cicli · "},{value:(info.path)}])),true);
  else I18N.bind(box,"textContent",()=>(I18N.join([{text:"Registrazione non avviata. Cartella: "},{value:(recordDirectory)}])),true);
  if(['das','rbf'].includes(info.format)&&info.status_note&&info.state!=='recording')box.textContent+=' · '+info.status_note;
  syncRecordingClock(info);window.renderRecordingJobs?.(info);applyRecordingControls();
}
// Recordings folder: one choice for Record, exports and the Recordings page, kept across restarts.
function syncRecordingsLocation(force=false){const input=$('record-directory');if(input&&(force||document.activeElement!==input))input.value=recordDirectory||'';const shown=$('history-location');if(shown){shown.textContent=recordDirectory||'—';shown.title=recordDirectory||'';}}
async function setRecordingsLocation(directory){
  const j=await request('/api/recordings/location',{directory});recordDirectory=j.directory;syncRecordingsLocation(true);
  window.refreshHistoryFiles?.().catch(()=>{});notice(I18N.join([{text:"Cartella registrazioni: "},{value:(j.directory)}]));return j.directory;
}
async function browseRecordingsLocation(){const j=await request('/api/recordings/location/browse',{title:I18N.t("Cartella delle registrazioni")});return j.directory?setRecordingsLocation(j.directory):null;}
// Recording in evidence: blinking Record button with the elapsed time, compact live figures, time in the window title.
let recordClock=null,recordClockTimer=null;const baseTitle=document.title;
function recordElapsed(){return recordClock?recordClock.seconds+(Date.now()-recordClock.at)/1000:0;}
function formatElapsed(seconds){const s=Math.max(0,Math.floor(seconds)),h=Math.floor(s/3600),m=Math.floor(s%3600/60),pad=v=>String(v).padStart(2,'0');return (h?h+':'+pad(m):pad(m))+':'+pad(s%60);}
function syncRecordingClock(info){
  const recording=info.state==='recording'&&!info.error;document.body.classList.toggle('is-recording',recording);
  if(!recording){recordClock=null;clearInterval(recordClockTimer);recordClockTimer=null;I18N.bind($('record'),"textContent",()=>(I18N.t("● Record")),true);document.title=baseTitle;return;}
  recordClock={seconds:Number(info.duration_seconds||0),at:Date.now()};
  if(!recordClockTimer)recordClockTimer=setInterval(tickRecordingClock,1000);tickRecordingClock();
}
function tickRecordingClock(){
  if(!recordClock)return;const elapsed=formatElapsed(recordElapsed());
  $('record').textContent='● REC '+elapsed;document.title='● REC '+elapsed+' · '+baseTitle;
  const clock=$('record-status').querySelector('[data-rec=clock]');if(clock)clock.textContent=elapsed;
}
function renderRecordingBar(box,info){
  const locale=I18N.locale(),seconds=Number(info.duration_seconds||0),rows=Number(info.rows_written||0);
  const capacity=Number(info.queue_capacity_cycles||0),queued=Number(info.queue_cycles||0),load=capacity?queued/capacity:0;
  const name=String(info.path||'').split(/[\\/]/).pop();
  const chip=(label,value,extra='')=>{const span=document.createElement('span'),b=document.createElement('b');span.className='rec-chip'+(extra?' '+extra:'');if(label){span.append(document.createTextNode(label+' '));}b.textContent=value;span.append(b);return span;};
  const live=document.createElement('span');live.className='rec-chip rec-live';const dot=document.createElement('i');dot.className='rec-dot';const time=document.createElement('b');time.dataset.rec='clock';time.textContent=formatElapsed(recordElapsed()||seconds);live.append(dot,document.createTextNode('REC '),time);
  const parts=[live,chip('',info.format_label||String(info.format||'').toUpperCase()),
    chip(I18N.t("Righe"),rows.toLocaleString(locale)),
    chip(I18N.t("Velocità"),(seconds>0?Math.round(rows/seconds):0).toLocaleString(locale)+' '+I18N.t("righe/s")),
    chip(I18N.t("Parte"),String(info.part||1)),
    chip(I18N.t("Coda"),Math.round(load*100)+'%',load>=.8?'rec-bad':load>=.5?'rec-warn':'rec-ok')];
  if(name)parts.push(chip('',name,'rec-file'));
  box.replaceChildren(...parts);
  box.title=I18N.join([{text:"Registrazione in corso · "},{value:(seconds.toFixed(1))},{text:" s · "},{value:(rows.toLocaleString(locale))},{text:" righe scritte · "},{value:(info.pending_rows||0)},{text:" righe in memoria · Coda "},{value:(queued)},{text:"/"},{value:(capacity)},{text:" cicli · "},{value:(info.path||'')}])+(info.status_note?' · '+info.status_note:'');
}
async function openRecordDialog(){
  if(!connected)throw new Error(I18N.t("Connetti il client prima di avviare Record."));
  if(!selected.size)throw new Error(I18N.t("Seleziona almeno un segnale da registrare."));
  await selectionQueue;
  const j=await request('/api/record/status');renderRecording(j.recording);
  if(recordingLocked())throw new Error(I18N.t("Una registrazione è già in corso."));
  I18N.bind($('record-signal-count'),"textContent",()=>(String((window.liveDataNodes?.(selected)||selected).size)),false);syncRecordingsLocation(true);
  updateRecordFormatControls();$('record-dialog-error').hidden=true;$('record-modal').hidden=false;
  $('record-form').querySelector('input:checked').focus();
}
function closeRecordDialog(){if(recordStarting)return;$('record-modal').hidden=true;$('record').focus();}
async function beginRecording(event){
  event.preventDefault();if(recordStarting)return;recordStarting=true;$('record-start').disabled=true;$('record-cancel').disabled=true;$('record-dialog-error').hidden=true;
  try{
    const wantedDirectory=$('record-directory').value.trim();if(wantedDirectory&&wantedDirectory!==recordDirectory)await setRecordingsLocation(wantedDirectory);
    await selectionQueue;
    const format=$('record-form').querySelector('input[name="record-format"]:checked').value;
    const result=await request('/api/record/start',{format,selected_nodes:[...(window.liveDataNodes?.(selected)||selected).values()],options:recordOptions()});renderRecording(result.recording);$('record-modal').hidden=true;$('record-stop').focus();notice(I18N.join([{text:"Registrazione "},{value:(result.recording.format_label||format.toUpperCase())},{text:" avviata. Pausa e zoom non interrompono Record."}]));
  }catch(e){I18N.bind($('record-dialog-error'),"textContent",()=>(e.message),true);$('record-dialog-error').hidden=false;}
  finally{recordStarting=false;$('record-start').disabled=false;$('record-cancel').disabled=false;applyRecordingControls();}
}
async function stopRecording(){const result=await request('/api/record/stop',{recording_id:recordState.recording_id});renderRecording(result.recording);if(result.recording.state==='stopped')notice(I18N.join([{text:"Registrazione salvata in "},{value:(result.recording.path)}]));}
function updateRecordFormatControls(){
  const selectedFormat=$('record-form').querySelector('input[name="record-format"]:checked')?.value;
  const binary=selectedFormat==='parquet';$('record-fda-note').hidden=selectedFormat!=='das';
  $('record-rbf-note').hidden=selectedFormat!=='rbf';
  $('record-binary-settings').hidden=!binary;$('record-parquet-profile').disabled=!binary;
  const profile=$('record-parquet-profile').value;
  I18N.bind($('record-storage-hint'),"textContent",()=>(profile==='maximum'?I18N.t("ZSTD livello 19: cerca una compressione maggiore, con un costo CPU più alto. Nessuna riduzione di precisione o campioni."):profile==='fast'?I18N.t("ZSTD livello 3: minore costo CPU, utile con molti segnali. Nessuna riduzione di precisione o campioni."):profile==='compatible'?I18N.t("Snappy: stesso codec delle versioni precedenti, generalmente file più grandi. Nessuna riduzione di precisione o campioni."):I18N.t("ZSTD livello 9: profilo consigliato per ridurre lo spazio con un costo CPU contenuto. Nessuna riduzione di precisione o campioni.")),true);
}
document.querySelectorAll('input[name="record-format"]').forEach(input=>input.addEventListener('change',updateRecordFormatControls));
$('record-parquet-profile').addEventListener('change',updateRecordFormatControls);
updateRecordFormatControls();
$('record-form').addEventListener('submit',beginRecording);
$('record-cancel').addEventListener('click',closeRecordDialog);
$('record-modal').addEventListener('click',event=>{if(event.target===$('record-modal'))closeRecordDialog();});
$('record-modal').addEventListener('keydown',event=>{if(event.key==='Escape'){event.preventDefault();closeRecordDialog();}if(event.key==='Tab'){const list=[...$('record-modal').querySelectorAll('button:not(:disabled),input:not(:disabled),select:not(:disabled),textarea:not(:disabled)')];const first=list[0],last=list[list.length-1];if(event.shiftKey&&document.activeElement===first){event.preventDefault();last.focus();}else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first.focus();}}});
bind('record',openRecordDialog);bind('record-stop',stopRecording);bind('discover',discover);bind('connect',connect);bind('disconnect',disconnect);bind('save',save);bind('regenerate',regenerate);bind('fetch-cert',fetchCert);bind('upload-cert',uploadCert);bind('add-node',addNode);bind('remove-all',removeAll);bind('browse-root',browseRoot);bind('scan',startScan);bind('cancel-scan',cancelScan);bind('select-filtered',selectFiltered);bind('shutdown',shutdown);bind('record-directory-browse',browseRecordingsLocation);bind('record-directory-default',()=>setRecordingsLocation(''));bind('history-location-change',browseRecordingsLocation);bind('record-directory',()=>{const v=$('record-directory').value.trim();return v===recordDirectory?null:setRecordingsLocation(v);},'change');
for(const id of ['sample_ms','window_seconds','max_samples','buffer_mode','auto_reconnect'])bind(id,liveConfig,'change');for(const id of ['security','security_mode'])bind(id,securityChanged,'change');
$('view-list').onclick=()=>setView('list');$('view-tree').onclick=()=>setView('tree');$('search').oninput=()=>{page=0;renderScan();};$('variables-only').onchange=()=>{page=0;renderScan();};$('page-prev').onclick=()=>{page--;renderScan();};$('page-next').onclick=()=>{page++;renderScan();};$('manual-node').onkeydown=e=>{if(e.key==='Enter')addNode().catch(e=>notice(e.message,true));};
window.addEventListener('error',e=>notice(I18N.join([{text:"Errore interfaccia: "},{value:(e.message||I18N.t("errore JavaScript"))}]),true));
window.addEventListener('unhandledrejection',e=>notice(I18N.join([{text:"Errore interfaccia: "},{value:((e.reason&&e.reason.message)||e.reason||I18N.t("promessa non gestita"))}]),true));
// Startup is coordinated by advanced.js after all controllers are available.

function openConnection(){
  const dialog=$('connection-dialog');if(dialog.open)return;
  $('connection-feedback').hidden=true;$('nav-connection').setAttribute('aria-expanded','true');
  if(typeof dialog.showModal==='function')dialog.showModal();else dialog.setAttribute('open','');
  $(({ads:'ads_host',s7:'s7_host',mqtt:'mqtt_host'})[window.currentSource?.()]||'endpoint').focus();
}
function closeConnection(){
  const dialog=$('connection-dialog');if(!dialog.open)return;
  if(typeof dialog.close==='function')dialog.close();else dialog.removeAttribute('open');
  $('nav-connection').setAttribute('aria-expanded','false');$('nav-connection').focus();
}
$('nav-connection').addEventListener('click',openConnection);
$('connection-close').addEventListener('click',closeConnection);
$('connection-dialog').addEventListener('click',event=>{if(event.target===$('connection-dialog')){const r=event.target.getBoundingClientRect();if(event.clientX<r.left||event.clientX>r.right||event.clientY<r.top||event.clientY>r.bottom)closeConnection();}});
$('connection-dialog').addEventListener('keydown',event=>{if(event.key==='Escape'){event.preventDefault();closeConnection();}});
bind('connection-save',save);

function renderLiveSession(d){
 const range=d.display_range;liveCharts.displayRange=liveViewMode==='all'?range:null;
 const label=$('live-session-status');
 if(!range){I18N.bind(label,"textContent",()=>(I18N.join([{value:(liveViewMode==='all'?I18N.t("Intera acquisizione"):I18N.t("Ultima finestra"))},{text:" · in attesa del primo campione"}])),true);return;}
 const stamp=t=>new Date(t).toLocaleString(I18N.locale(),{day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit'});
 const span=Math.max(0,(range.xmax-range.xmin)/1000);
 I18N.bind(label,"textContent",()=>(I18N.join([{value:(liveViewMode==='all'?I18N.t("Intera acquisizione"):I18N.t("Ultima finestra"))},{text:" · "},{value:(stamp(range.xmin))},{text:" → "},{value:(stamp(range.xmax))},{text:" · "},{value:(span.toLocaleString(I18N.locale(),{maximumFractionDigits:1}))},{text:" s"},{value:(liveViewMode==='all'?I18N.t(" · Panoramica con picchi e qualità; dettaglio recente nel buffer. Per conservare tutti i campioni usa Record."):I18N.t(" · Dati originali del buffer recente."))}])),true);
}
