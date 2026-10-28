/* Protocol configuration and independent live sessions. */
let activeSource='opcua',sourceSwitchBusy=false;
const adsFields=['ads_transport','ads_dll_path','ads_host','ads_target_netid','ads_port','ads_tcp_port','ads_source_netid','ads_source_port','ads_bind_ip','ads_timeout_ms','ads_notify_batch_ms','ads_mode','ads_encoding'];
const adsNumbers=new Set(['ads_port','ads_tcp_port','ads_source_port','ads_timeout_ms','ads_notify_batch_ms']);
const s7Fields=['s7_host','s7_tcp_port','s7_rack','s7_slot','s7_family','s7_connection_type','s7_local_tsap','s7_remote_tsap','s7_timeout_ms','s7_pdu','s7_mode','s7_symbolic_password','s7_symbolic_tls','s7_tls_ca','s7_tls_cert','s7_tls_key','s7_legacy_key_fallback','s7_udp_bind','s7_udp_port','s7_udp_sender_port','s7_udp_module','s7_udp_length','s7_udp_endian','s7_udp_timestamp_offset','s7_udp_watchdog_ms','s7_udp_queue'];
const s7Numbers=new Set(['s7_tcp_port','s7_rack','s7_slot','s7_connection_type','s7_timeout_ms','s7_pdu','s7_udp_port','s7_udp_sender_port','s7_udp_module','s7_udp_length','s7_udp_timestamp_offset','s7_udp_watchdog_ms','s7_udp_queue']);
const s7Booleans=new Set(['s7_symbolic_tls','s7_legacy_key_fallback']);
const mqttFields=['mqtt_host','mqtt_port','mqtt_version','mqtt_client_id','mqtt_username','mqtt_password','mqtt_tls','mqtt_ca_file','mqtt_cert_file','mqtt_key_file','mqtt_keepalive','mqtt_timeout_ms','mqtt_topics','mqtt_qos','mqtt_timestamp_pointer','mqtt_timestamp_unit','mqtt_time_basis','mqtt_quality_pointer','mqtt_include_retained','mqtt_queue','mqtt_max_payload_kb'];
const mqttNumbers=new Set(['mqtt_port','mqtt_qos','mqtt_keepalive','mqtt_timeout_ms','mqtt_queue','mqtt_max_payload_kb']);
const mqttBooleans=new Set(['mqtt_tls','mqtt_include_retained']);
window.currentS7Mode=()=>$('s7_mode').value||'classic';
window.s7Roots={classic:'s7:/',symbolic:'s7:sym:/',udp:'s7:udp:/'};
window.sourceProfiles={};
const liveProtocols=['opcua','ads','mqtt','s7'];
window.simultaneousEnabled=()=>$('mqtt-parallel').checked;
window.enabledProtocols=()=>liveProtocols.filter(p=>$('enable-'+p).checked);

window.currentSource=()=>activeSource;
window.sourceLabel=()=>({ads:'ADS',s7:I18N.t("S7 Siemens"),mqtt:'MQTT',opcua:'OPC UA'}[activeSource]);
window.sourceEndpoint=()=>activeSource==='mqtt'?`${$('mqtt_tls').checked?'mqtts':'mqtt'}://${$('mqtt_host').value}:${$('mqtt_port').value}`:activeSource==='s7'?(window.currentS7Mode()==='udp'?`${$('s7_host').value} → UDP ${$('s7_udp_bind').value}:${$('s7_udp_port').value}${I18N.t(" · Modulo ")}${$('s7_udp_module').value}`:window.currentS7Mode()==='symbolic'?`${$('s7_host').value}:${$('s7_tcp_port').value}${I18N.t(" · S7 simbolico")}`:`${$('s7_host').value}:${$('s7_tcp_port').value} · Rack ${$('s7_rack').value} / Slot ${$('s7_slot').value}`):activeSource==='ads'?I18N.join([{text:""},{value:($('ads_transport').value==='native'?I18N.t("TwinCAT locale"):$('ads_host').value)},{text:" · AMS "},{value:($('ads_target_netid').value)},{text:":"},{value:($('ads_port').value)},{text:""}]):$('endpoint').value;
window.sourceNode=value=>window.mqttBrowseActive?.()?(value?(value.startsWith('mqtt:')?value:'mqtt:'+value+(value.includes('#')?'':'#')):'mqtt:/'):activeSource==='mqtt'?(value?(value.startsWith('mqtt:')?value:'mqtt:'+value+(value.includes('#')?'':'#')):'mqtt:/'):activeSource==='s7'?(value?(value.startsWith('s7:')?value:({classic:'s7:',symbolic:'s7:sym:',udp:'s7:udp:'}[window.currentS7Mode()]+value)):window.s7Roots[window.currentS7Mode()]):activeSource==='ads'?(value?(value.startsWith('ads:')?value:'ads:'+value):'ads:/'):(value||'i=85');
window.sourceConfig=()=>({source:activeSource,mqtt_parallel:false,enabled_sources:simultaneousEnabled()?enabledProtocols():[],source_intervals:simultaneousEnabled()?Object.fromEntries(liveProtocols.map(p=>[p,Number($('period-'+p).value)])):{},...Object.fromEntries(adsFields.map(k=>[k,adsNumbers.has(k)?Number($(k).value):$(k).value.trim()])),
  ...Object.fromEntries(mqttFields.map(k=>[k,mqttBooleans.has(k)?$(k).checked:mqttNumbers.has(k)?Number($(k).value):['mqtt_username','mqtt_password'].includes(k)?$(k).value:$(k).value.trim()])),mqtt_root:activeSource==='mqtt'?($('root_nodeid').value.trim()||'mqtt:/'):(window.sourceProfiles.mqtt?.root||'mqtt:/'),
  ...Object.fromEntries(s7Fields.map(k=>[k,s7Booleans.has(k)?$(k).checked:s7Numbers.has(k)?Number($(k).value):$(k).value.trim()])),s7_tags:window.s7Tags||[],s7_udp_tags:window.s7UDPTags||[],
  root_nodeid:activeSource!=='opcua'||window.mqttBrowseActive?.()?(window.sourceProfiles.opcua?.root||'i=85'):($('root_nodeid').value.trim()||'i=85'),
  ...Object.fromEntries(Object.entries({classic:'s7_root',symbolic:'s7_symbolic_root',udp:'s7_udp_root'}).map(([mode,key])=>[key,activeSource==='s7'&&window.currentS7Mode()===mode?window.sourceNode($('root_nodeid').value.trim()):window.s7Roots[mode]])),
  ads_root:activeSource==='ads'?window.sourceNode($('root_nodeid').value.trim()):(window.sourceProfiles.ads?.root||'ads:/')});
function sourcePresentation(){
  const ads=activeSource==='ads',s7=activeSource==='s7',mqtt=activeSource==='mqtt';
  $('mqtt-parallel-option').hidden=false;
  $('multi-source-settings').hidden=!simultaneousEnabled();
  $('browse-source-wrap').hidden=!simultaneousEnabled();
  $('browse-source').value=activeSource;
  window.refreshLiveExtras?.();$('data-source').value=activeSource;
  for(const option of $('sample_ms').options){if(Number(option.value)<10){option.disabled=!ads;option.hidden=!ads;}}
  if(!simultaneousEnabled()&&!ads&&Number($('sample_ms').value)<10)$('sample_ms').value='10';
  I18N.bind(document.querySelector('label[for="sample_ms"]'),"textContent",()=>(mqtt?I18N.t("Periodo indicativo MQTT (ms)"):ads?I18N.t("Periodo ADS richiesto"):I18N.t("Intervallo lettura")),true);
  $('ads-fast-sampling-note').hidden=!ads;
  $('opcua-connection-fields').hidden=ads||s7||mqtt;$('ads-connection-fields').hidden=!ads;$('s7-connection-fields').hidden=!s7;$('mqtt-connection-fields').hidden=!mqtt;
  I18N.bind(document.querySelector('label[for="root_nodeid"]'),"textContent",()=>(ads?I18N.t("Prefisso simboli ADS (ads:/ = tutti)"):I18N.t("Nodo di partenza (i=85 = Objects)")),true);
  I18N.bind($('root_nodeid'),"placeholder",()=>(ads?I18N.t("ads:/ oppure ads:MAIN"):'i=85'),true);
  I18N.bind($('manual-node'),"placeholder",()=>(ads?I18N.t("Simbolo: MAIN.Temperatura oppure MAIN.Array[0]"):I18N.t("NodeId diretto: ns=2;s=PLC1.Temperatura")),true);
  I18N.attr($('manual-node'),'aria-label',()=>(ads?I18N.t("Simbolo ADS da aggiungere"):I18N.t("NodeId da aggiungere")));
  I18N.bind($('add-node'),"textContent",()=>(ads?I18N.t("Aggiungi simbolo"):I18N.t("Aggiungi NodeId")),true);
  I18N.bind($('nav-live'),"textContent",()=>('LIVE'),false);
  if(s7){
    I18N.bind(document.querySelector('label[for="root_nodeid"]'),"textContent",()=>(I18N.t("Catalogo S7 (s7:/ = tutte le definizioni)")),true);
    I18N.bind($('root_nodeid'),"placeholder",()=>(I18N.t("s7:/ oppure s7:/DB1")),true);
    I18N.bind($('manual-node'),"placeholder",()=>(I18N.t("DB1.DBD0:REAL oppure M0.0:BOOL")),true);
    I18N.attr($('manual-node'),'aria-label',()=>('Indirizzo S7 e tipo'));I18N.bind($('add-node'),"textContent",()=>(I18N.t("Aggiungi indirizzo")),true);
    const mode=window.currentS7Mode();
    if(mode==='symbolic'){
      I18N.bind(document.querySelector('label[for="root_nodeid"]'),"textContent",()=>(I18N.t("Simboli CPU (s7:sym:/ = tutti i DB)")),true);
      I18N.bind($('root_nodeid'),"placeholder",()=>(I18N.t("s7:sym:/ oppure s7:sym:/DB_Produzione")),true);
      I18N.bind($('manual-node'),"placeholder",()=>(I18N.t("DB_Produzione.Carico oppure s7:sym:DB_Produzione.Carico")),true);I18N.bind($('add-node'),"textContent",()=>(I18N.t("Aggiungi simbolo")),true);
    }else if(mode==='udp'){
      I18N.bind(document.querySelector('label[for="root_nodeid"]'),"textContent",()=>(I18N.t("Campi del telegramma UDP")),true);I18N.bind($('root_nodeid'),"placeholder",()=>('s7:udp:/'),false);
      I18N.bind($('manual-node'),"placeholder",()=>(I18N.t("s7:udp:0:REAL (campo configurato)")),true);I18N.bind($('add-node'),"textContent",()=>(I18N.t("Aggiungi campo")),true);
    }
  }
  if(mqtt){
    I18N.bind(document.querySelector('label[for="root_nodeid"]'),"textContent",()=>(I18N.t("Catalogo MQTT osservato")),true);I18N.bind($('root_nodeid'),"placeholder",()=>('mqtt:/'),false);
    I18N.bind($('manual-node'),"placeholder",()=>(I18N.t("plant/press#/value oppure plant/temperature#")),true);I18N.attr($('manual-node'),'aria-label',()=>(I18N.t("Topic MQTT e JSON Pointer")));I18N.bind($('add-node'),"textContent",()=>(I18N.t("Aggiungi campo MQTT")),true);
  }
  I18N.bind($('scan'),"textContent",()=>(mqtt?I18N.t("Elenca topic/campi ricevuti"):s7?(window.currentS7Mode()==='symbolic'?I18N.t("Scansiona simboli CPU"):window.currentS7Mode()==='udp'?I18N.t("Elenca campi UDP"):I18N.t("Elenca variabili configurate")):I18N.t("Scansiona tutto")),true);
  window.renderS7Mode?.();
  window.refreshSourceState();window.scheduleStudioToolbar?.();
}
window.refreshSourceState=()=>{
  const locked=state!=='disconnected'||connectionBusy||sourceSwitchBusy||recordingLocked()||!!window.parallelMqttStatus?.enabled||!!Object.keys(window.sourceSessionsStatus||{}).length;
  $('ads-enable-cyclic').disabled=locked;
  $('mqtt-parallel').disabled=locked;
  $('browse-source').disabled=connectionBusy||sourceSwitchBusy;
  if(Object.keys(window.sourceSessionsStatus||{}).length)$('disconnect').disabled=connectionBusy;
  $('sample_ms').disabled=simultaneousEnabled()&&locked;
  for(const protocol of liveProtocols){
    $('enable-'+protocol).disabled=locked;$('period-'+protocol).disabled=locked;
    const info=window.sourceSessionsStatus?.[protocol];
    $('reconnect-'+protocol).disabled=!info||info.state==='connecting';
    $('disconnect-'+protocol).disabled=!info||!info.enabled;
    const loss=info?['queue_dropped','dropped','lost','timestamp_errors'].reduce((sum,k)=>sum+Number(info.diagnostics?.[k]||0),0):0;
    const value=info?I18N.t(info.state==='connected'?'Connesso':info.state==='reconnecting'?'Riconnessione…':'Non connesso')+' · '+I18N.t('Campioni')+' '+info.received_rows+(info.late_samples?' · '+I18N.t('Tardivi')+' '+info.late_samples:'')+(loss?' · '+I18N.t('Persi')+' '+loss:'')+(info.error?' · '+info.error:''):'';
    if($('state-'+protocol).textContent!==value)$('state-'+protocol).textContent=value;
  }
  $('data-source').disabled=locked&&!simultaneousEnabled();$('ads-test').disabled=locked;
  for(const id of adsFields)$(id).disabled=locked;
  for(const id of s7Fields)$(id).disabled=locked;
  for(const id of mqttFields)$(id).disabled=locked;
  $('mqtt-test').disabled=locked;$('mqtt-tls-fields').hidden=!$('mqtt_tls').checked;
  $('s7-test').disabled=locked;
  $('s7-symbols-refresh').disabled=state!=='connected'||window.currentS7Mode()!=='symbolic'||(recordingLocked()&&!simultaneousEnabled());
  document.querySelectorAll('#s7-catalog-editor input,#s7-catalog-editor select,#s7-catalog-editor button').forEach(el=>el.disabled=locked);
  document.querySelectorAll('#s7-udp-catalog-editor input,#s7-udp-catalog-editor select,#s7-udp-catalog-editor button').forEach(el=>el.disabled=locked);
  const native=$('ads_transport').value==='native';
  $('ads-native-options').hidden=!native;
  $('ads-connection-edit-note').hidden=!locked;
  document.querySelectorAll('[data-ads-manual-label]').forEach(el=>el.hidden=!native);
  I18N.bind($('ads-transport-note'),"textContent",()=>(native?I18N.t("Tutti i campi sono modificabili e vengono salvati. TwinCAT locale usa la route del PC verso AMS Net ID PLC e porta runtime; Net ID e porta client effettivi sono assegnati dal router. I campi indicati ADS/TCP sono usati scegliendo ADS/TCP diretto."):I18N.t("Connessione diretta al PAC: IP, porta TCP, Net ID client, porta AMS client e scheda di rete sono applicati alla connessione. Usa un’identità AMS client dedicata e la relativa route; non riutilizzare quella di un router TwinCAT attivo.")),true);
};
window.loadSourceConfig=c=>{
  activeSource=['ads','s7','mqtt'].includes(c.source)?c.source:'opcua';$('mqtt-parallel').checked=!!c.mqtt_parallel||!!c.enabled_sources?.length;
  for(const p of liveProtocols){$('enable-'+p).checked=(c.enabled_sources?.length?c.enabled_sources:c.mqtt_parallel?['opcua','mqtt']:[c.source]).includes(p);$('period-'+p).value=c.source_intervals?.[p]||Math.max(p==='ads'?1:10,c.sample_ms||500);}
  window.sourceProfiles=c.workspace?.source_profiles||{};
  for(const k of adsFields)$(k).value=c[k]??(k==='ads_transport'?'tcp':'');
  for(const k of s7Fields){if(s7Booleans.has(k))$(k).checked=!!c[k];else $(k).value=c[k]??'';}
  for(const k of mqttFields){if(mqttBooleans.has(k))$(k).checked=!!c[k];else $(k).value=c[k]??'';}
  window.s7Roots={classic:c.s7_root||'s7:/',symbolic:c.s7_symbolic_root||'s7:sym:/',udp:'s7:udp:/'};
  window.s7ModeProfiles=c.workspace?.s7_mode_profiles||{};
  window.s7ActiveMode=window.currentS7Mode();
  window.s7Tags=JSON.parse(JSON.stringify(c.s7_tags||[]));window.renderS7Tags?.();
  window.s7UDPTags=JSON.parse(JSON.stringify(c.s7_udp_tags||[]));window.renderS7UDPTags?.();
  $('root_nodeid').value=activeSource==='mqtt'?(c.mqtt_root||'mqtt:/'):activeSource==='s7'?window.s7Roots[window.currentS7Mode()]:activeSource==='ads'?(c.ads_root||'ads:/'):(c.root_nodeid||'i=85');
  sourcePresentation();
};
window.renderAdsInfo=info=>{
  if(!info||!info.device)return;
  I18N.bind($('ads-info'),"textContent",()=>(I18N.join([{text:"Parametri effettivi · "},{value:(info.transport||'ADS')},{text:"\n"},{value:(info.device)},{text:" · v"},{value:(info.version)},{text:" · "},{value:(info.ads_state_name)},{text:" (device "},{value:(info.device_state)},{text:")\nClient "},{value:(info.source_netid)},{text:" · porta "},{value:(info.source_port||'configurata')},{text:" · IP "},{value:(info.local_ip)},{text:I18N.t("\nTarget ")},{value:(info.target_netid)},{text:":"},{value:(info.target_port)},{text:" · "},{value:(info.read_mode||'ADS/TCP')},{text:"\nTimestamp: "},{value:(info.timestamps||I18N.t("locale (polling)"))},{text:"."},{value:(info.sum_fallback?I18N.join([{text:"\nFallback: "},{value:(info.sum_fallback)}]):'')},{text:""}])),true);
};
async function switchSource(next){
  if(next===activeSource)return;
  if(simultaneousEnabled()){
    if(sourceSwitchBusy||connectionBusy){$('data-source').value=activeSource;return;}
    sourceProfiles[activeSource]={...(sourceProfiles[activeSource]||{}),root:$('root_nodeid').value};
    activeSource=next;
    $('root_nodeid').value=sourceProfiles[next]?.root||(next==='mqtt'?'mqtt:/':next==='ads'?'ads:/':next==='s7'?s7Roots[currentS7Mode()]:'i=85');
    $('tree').replaceChildren();delete $('tree').dataset.mqttTree;scanNodes=[];renderScan();
    sourcePresentation();scheduleWorkspaceSave();
    if(connected)await browseRoot().catch(e=>notice(e.message,true));
    return;
  }
  if(state!=='disconnected'||connectionBusy||recordingLocked()||sourceSwitchBusy){$('data-source').value=activeSource;throw new Error(I18N.t("Disconnettere e fermare Record prima di cambiare fonte dati."));}
  sourceSwitchBusy=true;window.refreshSourceState();
  const previous=activeSource;
  clearTimeout(workspaceTimer);
  window.sourceProfiles[previous]={mqtt_parallel:previous==='opcua'&&$('mqtt-parallel').checked,selected_nodes:[...selected.values()],live:chartSnapshot(liveCharts),root:window.mqttBrowseActive?.()&&previous==='opcua'?(window.sourceProfiles.opcua?.root||'i=85'):$('root_nodeid').value,sample_ms:Number($('sample_ms').value)};
  const profile=window.sourceProfiles[next]||{};
  try{
    await selectionQueue;
    // Backend selection changes first; a rejection leaves the current workspace intact.
    await request('/api/select',{nodeids:(profile.selected_nodes||[]).map(n=>n.nodeid)});
    activeSource=next;if(next==='opcua')$('mqtt-parallel').checked=!!profile.mqtt_parallel;selected=new Map((profile.selected_nodes||[]).map(n=>[n.nodeid,n]));
    const period=Math.max(next==='ads'?1:10,Number(profile.sample_ms)||Number($('sample_ms').value)||500);
    if(![...$('sample_ms').options].some(o=>o.value===String(period)))$('sample_ms').add(I18N.option(()=>(period+' ms'),String(period),false,false,false));
    $('sample_ms').value=String(period);
    series={};types={};lastValues=[];clearTimeout(scanTimer);scanNodes=[];scanJob='';scanRunning=false;page=0;
    $('tree').replaceChildren();renderScan();I18N.bind($('scan-status'),"textContent",()=>(I18N.t("Scegli Connetti, poi Esplora rami o Scansiona tutto.")),true);
    $('root_nodeid').value=profile.root||(next==='mqtt'?'mqtt:/':next==='s7'?window.s7Roots[window.currentS7Mode()]:next==='ads'?'ads:/':'i=85');
    liveCharts.restore(profile.live||{charts:[{name:'Grafico 1',ids:[]}],layout:'single',active:0},(window.liveDataNodes?.(selected)||selected));
    if(profile.live){restoreWorkspaceOptions(liveCharts,profile.live);liveCharts.charts.forEach((g,i)=>restoreView(g,profile.live.charts[i],liveCharts));}
    else{liveCharts.colors.clear();restoreWorkspaceOptions(liveCharts,{syncZoom:true,syncHover:true});liveCharts.resetViews();}
    sourcePresentation();renderSelected();renderValues([]);liveCharts.update(series,(window.liveDataNodes?.(selected)||selected),types);
    await request('/api/config',config());
    scheduleWorkspaceSave();notice(I18N.join([{text:"Fonte "},{value:(window.sourceLabel())},{text:" selezionata. Configura la connessione nel popup."}]));
  }finally{sourceSwitchBusy=false;window.refreshSourceState();}
}
$('data-source').addEventListener('change',()=>switchSource($('data-source').value).catch(e=>notice(e.message,true)));
bind('ads-test',async()=>{
  I18N.bind($('ads-info'),"textContent",()=>(I18N.t("Test ADS in corso…")),true);
  try{const result=await request('/api/ads/test',config());window.renderAdsInfo(result.ads);notice(I18N.join([{text:"Route AMS e dispositivo raggiungibili. Stato PLC: "},{value:(result.ads.ads_state_name)}]));}
  catch(e){I18N.bind($('ads-info'),"textContent",()=>(e.message),true);throw e;}
});
for(const id of adsFields)$(id).addEventListener('change',()=>{
  if(id==='ads_transport'&&$('ads_transport').value==='tcp'&&$('ads_mode').value==='notify'){
    $('ads_mode').value='sum';notice(I18N.t("ADS/TCP diretto selezionato: acquisizione SumRead. Le notifiche cicliche richiedono TwinCAT locale."));
  }
  window.refreshSourceState();scheduleWorkspaceSave();
});

bind('ads-enable-cyclic',async()=>{$('ads_transport').value='native';$('ads_mode').value='notify';window.refreshSourceState();scheduleWorkspaceSave();notice(I18N.t("ADS cicliche selezionate: premi Connetti."));});

window.renderAdsCyclicDiagnostics=d=>{
 const el=$('ads-cyclic-diag');if(!el)return;
 const n=d.ads_notifications||{};el.hidden=!n.mode;
 const ms=v=>v==null?'—':Number(v).toFixed(3)+' ms';
 I18N.bind(el,"textContent",()=>(n.mode?I18N.join([{text:"Campioni PLC: media "},{value:(ms(n.source_period_ms))},{text:" · min "},{value:(ms(n.source_min_ms))},{text:" · max "},{value:(ms(n.source_max_ms))},{text:" · P95 "},{value:(ms(n.source_p95_ms))},{text:I18N.t(" | Arrivo PC: ")},{value:(ms(n.arrival_period_ms))},{text:" | Coda "},{value:(n.queue_samples)},{text:"/"},{value:(n.queue_capacity)},{text:" · picco "},{value:(n.queue_high_water||0)},{text:I18N.t(" · elaborati ")},{value:(n.processed||0)},{text:" · persi per overflow "},{value:(n.dropped)},{text:" · attesa coda max "},{value:(ms(n.queue_delay_ms))},{text:" | PC − PLC "},{value:(ms(n.clock_delta_ms))},{text:" (include disallineamento orologi). Riavvii subscription: "},{value:(n.subscription_restarts||0)},{text:I18N.t(". Riferimento: ")},{value:(n.reference_signal||'nessuno')},{text:I18N.t(". Controllo ADS: ")},{value:(n.control_error?'DEGRADATO — '+n.control_error:(n.control_last_ok?'OK':I18N.t("in attesa")))},{text:" · timeout "},{value:(n.control_timeouts||0)},{text:" · durata "},{value:(ms(n.control_ms))},{text:" | Raggruppamento notifiche: "},{value:(n.notify_max_delay_ms!=null?n.notify_max_delay_ms+' ms':'—')},{text:" | Windows: priorità "},{value:(n.system?.process_priority||'—')},{text:" · ricezione "},{value:(n.system?.receive_thread||'—')},{text:" · GC "},{value:(n.system?.gc_latency||'—')},{text:" · piano energetico "},{value:(n.system?.power_plan||'—')},{text:(n.system?.power_plan_performance===false?" (consigliato: Prestazioni elevate)":"")}]):''),true);
 I18N.bind($('mean-ms'),"title",()=>(n.mode?I18N.t("Periodo dei timestamp PLC sul segnale di riferimento"):I18N.t("Periodo medio del polling PC")),true);
};

window.renderMqttInfo=info=>{
  if(!info?.endpoint)return;
  I18N.bind($('mqtt-info'),"textContent",()=>(I18N.join([{value:(info.transport)},{text:" · "},{value:(info.endpoint)},{text:"\nTopic: "},{value:(info.topics.join(', '))},{text:"\nQoS richiesto "},{value:(info.qos)},{value:(info.granted_qos?' · concessi '+info.granted_qos.join(', '):'')},{text:"\nBase tempi: "},{value:(info.timestamps)},{text:" · sola lettura"}])),true);
};
window.renderMqttDiagnostics=d=>{
  const el=$('mqtt-diag');el.hidden=activeSource!=='mqtt';if(el.hidden)return;
  if(!d||d.received===undefined){I18N.bind(el,"textContent",()=>(I18N.t("MQTT · in attesa della connessione e delle prime pubblicazioni.")),true);return;}
  I18N.bind(el,"textContent",()=>(I18N.join([{text:"MQTT · messaggi "},{value:(d.received)},{text:I18N.t(" · elaborati ")},{value:(d.processed)},{text:" · valori selezionati "},{value:(d.selected_values)},{text:" · catalogo "},{value:(d.catalog_signals)},{text:" campi · coda "},{value:(d.queue_samples)},{text:"/"},{value:(d.queue_capacity)},{text:" ("},{value:(Math.round(d.queue_bytes/1024))},{text:" KiB) · persi "},{value:(d.queue_dropped)},{text:" · payload non validi "},{value:(d.invalid_payloads)},{text:" · retained esclusi "},{value:(d.retained_excluded)},{text:" · flag DUP "},{value:(d.dup_flag)},{text:" · campioni tardivi "},{value:(d.late_samples)},{text:" · riconnessioni "},{value:(d.reconnections)},{value:(d.error?' · '+d.error:'')}])),true);
};
bind('mqtt-test',async()=>{
  I18N.bind($('mqtt-info'),"textContent",()=>(I18N.t("Verifica broker e autorizzazioni di sottoscrizione…")),true);
  try{const result=await request('/api/mqtt/test',config());window.renderMqttInfo(result.mqtt);notice(I18N.t("Broker MQTT connesso e sottoscrizioni accettate."));}
  catch(e){I18N.bind($('mqtt-info'),"textContent",()=>(e.message),true);throw e;}
});
for(const id of mqttFields)$(id).addEventListener('change',()=>{
  if(id==='mqtt_tls'){
    if($('mqtt_tls').checked&&$('mqtt_port').value==='1883')$('mqtt_port').value='8883';
    else if(!$('mqtt_tls').checked&&$('mqtt_port').value==='8883')$('mqtt_port').value='1883';
  }
  window.refreshSourceState();scheduleWorkspaceSave();
});

for(const protocol of liveProtocols){
  for(const id of ['enable-','period-'])$(id+protocol).addEventListener('change',()=>{sourcePresentation();scheduleWorkspaceSave();});
  for(const action of ['reconnect','disconnect'])$(''+action+'-'+protocol).onclick=async()=>{
    try{const result=await request('/api/sources/'+protocol+'/'+(action==='reconnect'?'connect':'disconnect'),{});window.sourceSessionsStatus=result.sources;refreshSourceState();}
    catch(e){notice(e.message,true);}
  };
}
