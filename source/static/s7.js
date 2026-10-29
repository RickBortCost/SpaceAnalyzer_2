/* S7 catalog editor. Data remains typed and names are always rendered as text. */
window.s7Tags=[];
let s7Editing=null,s7CatalogBusy=false;
window.renderS7Tags=()=>{
  const root=$('s7-tag-list');root.replaceChildren();
  if(!window.s7Tags.length){const p=document.createElement('p');p.className='small';I18N.bind(p,"textContent",()=>(I18N.t("Nessuna variabile configurata.")),true);root.append(p);}
  window.s7Tags.forEach((tag,index)=>{
    const row=document.createElement('div');row.className='s7-tag-row';
    const name=document.createElement('span');I18N.bind(name,"textContent",()=>(tag.display_name),false);I18N.bind(name,"title",()=>(tag.nodeid),false);
    const address=document.createElement('small');I18N.bind(address,"textContent",()=>(tag.nodeid.slice(3)),false);address.className='mono';
    const edit=document.createElement('button');edit.type='button';edit.className='ghost';I18N.bind(edit,"textContent",()=>(I18N.t("Modifica")),true);
    edit.onclick=()=>{s7Editing=index;const parts=tag.nodeid.slice(3).split(':');$('s7-tag-name').value=tag.display_name;$('s7-tag-address').value=parts[0];$('s7-tag-type').value=parts[1].split('[')[0];$('s7-tag-length').value=Number(parts[1].match(/\[(\d+)\]/)?.[1]||40);$('s7-tag-cancel').hidden=false;};
    const remove=document.createElement('button');remove.type='button';remove.className='ghost';I18N.bind(remove,"textContent",()=>(I18N.t("Rimuovi")),true);remove.onclick=()=>commitS7Tags(window.s7Tags.filter((_,i)=>i!==index)).catch(e=>notice(e.message,true));
    row.append(name,address,edit,remove);root.append(row);
  });
  window.refreshSourceState?.();
};
async function commitS7Tags(tags){
  if(s7CatalogBusy||state!=='disconnected'||connectionBusy||recordState.busy)throw new Error(I18N.t("Disconnetti e ferma Record prima di modificare il catalogo S7."));
  s7CatalogBusy=true;const old=window.s7Tags;
  try{
    const result=await request('/api/s7/tags/validate',{tags});window.s7Tags=result.tags;
    try{await request('/api/config',config());}catch(e){window.s7Tags=old;throw e;}
    s7Editing=null;$('s7-tag-cancel').hidden=true;window.renderS7Tags();scheduleWorkspaceSave();
    notice(I18N.t("Catalogo S7 salvato. Le selezioni già presenti nei grafici restano indipendenti."));
  }finally{s7CatalogBusy=false;}
}
bind('s7-tag-add',async()=>{
  const type=$('s7-tag-type').value+($('s7-tag-type').value==='STRING'?'['+$('s7-tag-length').value+']':'');
  const tag={address:$('s7-tag-address').value.trim(),data_type:type,display_name:$('s7-tag-name').value.trim()};
  const candidate=[...window.s7Tags];if(s7Editing===null)candidate.push(tag);else candidate[s7Editing]=tag;
  await commitS7Tags(candidate);$('s7-tag-name').value='';$('s7-tag-address').value='';
});
bind('s7-tag-cancel',async()=>{s7Editing=null;$('s7-tag-cancel').hidden=true;$('s7-tag-name').value='';$('s7-tag-address').value='';});
bind('s7-tags-export',async()=>{
  const url=URL.createObjectURL(new Blob([JSON.stringify({schema:1,tags:window.s7Tags},null,2)],{type:'application/json'}));
  const a=document.createElement('a');a.href=url;a.download='Space_Fieldbus_Analyzer_S7_Tags.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
});
$('s7-tags-import').addEventListener('change',async event=>{
  try{const file=event.target.files[0];if(!file)return;if(file.size>2*1024*1024)throw new Error(I18N.t("Catalogo JSON: massimo 2 MiB."));const data=JSON.parse(await file.text());const tags=Array.isArray(data)?data:data.tags;if(!Array.isArray(tags))throw new Error(I18N.t("JSON: serve un elenco tags."));await commitS7Tags([...window.s7Tags,...tags]);}
  catch(e){notice(e.message,true);}finally{event.target.value='';}
});
window.renderS7Info=info=>{
  if(!info?.host)return;
  if(info.mode==='symbolic'){
    I18N.bind($('s7-info'),"textContent",()=>(I18N.join([{text:I18N.t("S7 simbolico · ")},{value:(info.host)},{text:":"},{value:(info.port)},{text:I18N.t(" · protocollo V")},{value:(info.version)},{text:"\nSimboli CPU: "},{value:(info.symbols||0)},{text:" · "},{value:(info.tls?(info.certificate_verified?I18N.t("TLS, certificato verificato"):I18N.t("TLS, certificato non verificato")):I18N.t("Sessione senza TLS"))},{text:"\n"},{value:(info.timestamps)},{text:""}])),true);return;
  }
  if(info.mode==='udp'){
    I18N.bind($('s7-info'),"textContent",()=>(I18N.join([{text:"UDP Direct Generic · "},{value:(info.host)},{text:" → "},{value:(info.bind_ip)},{text:":"},{value:(info.port)},{text:I18N.t("\nModulo ")},{value:(info.module_index)},{text:" · "},{value:(info.length)},{text:" byte · filtro IP mittente attivo\n"},{value:(info.timestamps)},{text:""}])),true);return;
  }
  I18N.bind($('s7-info'),"textContent",()=>(I18N.join([{text:"S7 · "},{value:(info.host)},{text:":"},{value:(info.port)},{text:" · rack "},{value:(info.rack)},{text:", slot "},{value:(info.slot)},{text:"\nTSAP "},{value:(info.local_tsap)},{text:" → "},{value:(info.remote_tsap)},{text:" · PDU "},{value:(info.pdu)},{text:" byte\n"},{value:(info.timestamps)},{text:""}])),true);
};
bind('s7-test',async()=>{
  I18N.bind($('s7-info'),"textContent",()=>(I18N.t("Connessione S7 in corso…")),true);
  try{const result=await request('/api/s7/test',config());window.renderS7Info(result.s7);
    if(result.probe?.length){const p=result.probe[0];$('s7-info').textContent+=I18N.join([{text:"\nProva "},{value:(p.nodeid)},{text:": "},{value:(p.good?String(p.value):p.error)}]);notice(p.good?I18N.t("Connessione e prima variabile S7 verificate."):I18N.join([{text:"Connessione S7 attiva, ma lettura negata/non valida: "},{value:(p.error)}]),!p.good);}
    else notice(window.currentS7Mode()==='symbolic'?I18N.join([{text:"Sessione simbolica verificata. Simboli disponibili: "},{value:(result.s7.symbols)}]):window.currentS7Mode()==='udp'?I18N.t("Telegramma UDP valido ricevuto. Configura i campi da graficare."):I18N.t("Connessione S7 e negoziazione PDU riuscite. Aggiungi una variabile per verificare l’accesso ai dati."));
  }catch(e){I18N.bind($('s7-info'),"textContent",()=>(e.message),true);throw e;}
});

window.s7UDPTags=[];window.s7ModeProfiles={};window.s7ActiveMode='classic';
let s7UDPEditing=null,s7UDPBusy=false;
window.renderS7Mode=()=>{
  const mode=window.currentS7Mode();
  document.querySelectorAll('[data-s7-classic]').forEach(el=>el.hidden=mode!=='classic');
  $('s7-tcp-fields').hidden=mode==='udp';$('s7-time-fields').hidden=mode==='udp';$('s7-time-symbolic').hidden=mode!=='symbolic';$('s7-symbolic-fields').hidden=mode!=='symbolic';$('s7-udp-fields').hidden=mode!=='udp';
  I18N.bind($('s7-mode-note'),"textContent",()=>(mode==='symbolic'?I18N.t("S7-1200/1500 · accesso per nome e scansione dei simboli su TCP 102. Non richiede offset assoluti o software Siemens sul PC."):mode==='udp'?I18N.t("Invio ciclico PLC → PC. Il ciclo e la composizione del telegramma si configurano nel programma PLC."):I18N.t("Lettura S7 classica su TCP 102, con indirizzi DB/M/I/Q configurati.")),true);
};
window.switchS7Mode=async next=>{
  const old=window.s7ActiveMode;if(next===old)return;
  if(state!=='disconnected'||connectionBusy||recordState.busy||sourceSwitchBusy){$('s7_mode').value=old;throw new Error(I18N.t("Disconnetti e ferma Record prima di cambiare modalità S7."));}
  if(window.currentSource()!=='s7'){window.s7ActiveMode=next;sourcePresentation();return;}
  sourceSwitchBusy=true;window.refreshSourceState();clearTimeout(workspaceTimer);
  try{
    await selectionQueue;
    const oldRoot=$('root_nodeid').value;
    window.s7Roots[old]=oldRoot;
    window.s7ModeProfiles[old]={selected_nodes:[...selected.values()],live:chartSnapshot(liveCharts),root:oldRoot};
    const profile=window.s7ModeProfiles[next]||{};
    $('root_nodeid').value=profile.root||({classic:'s7:/',symbolic:'s7:sym:/',udp:'s7:udp:/'}[next]);
    try{await request('/api/config',config());}catch(e){$('root_nodeid').value=oldRoot;throw e;}
    await request('/api/select',{nodeids:(profile.selected_nodes||[]).map(n=>n.nodeid)});
    window.s7ActiveMode=next;
    selected=new Map((profile.selected_nodes||[]).map(n=>[n.nodeid,n]));series={};types={};lastValues=[];
    clearTimeout(scanTimer);scanNodes=[];scanJob='';scanRunning=false;page=0;$('tree').replaceChildren();renderScan();
    liveCharts.restore(profile.live||{charts:[{name:'Grafico 1',ids:[]}],layout:'single',active:0},(window.liveDataNodes?.(selected)||selected));
    if(profile.live){restoreWorkspaceOptions(liveCharts,profile.live);liveCharts.charts.forEach((g,i)=>restoreView(g,profile.live.charts[i],liveCharts));}
    else{liveCharts.colors.clear();restoreWorkspaceOptions(liveCharts,{syncZoom:true,syncHover:true});liveCharts.resetViews();}
    sourcePresentation();renderSelected();renderValues([]);liveCharts.update(series,(window.liveDataNodes?.(selected)||selected),types);
    scheduleWorkspaceSave();notice(I18N.join([{text:"Modalità S7 "},{value:({classic:'classica',symbolic:'simbolica',udp:I18N.t("UDP ciclica")}[next])},{text:" selezionata. Configura e premi Connetti."}]));
  }catch(e){$('s7_mode').value=old;window.s7ActiveMode=old;sourcePresentation();throw e;}
  finally{sourceSwitchBusy=false;window.refreshSourceState();}
};
$('s7_mode').addEventListener('change',()=>window.switchS7Mode($('s7_mode').value).catch(e=>notice(e.message,true)));
bind('s7-symbols-refresh',async()=>{
  $('s7-symbols-refresh').disabled=true;
  try{const result=await request('/api/s7/symbols/refresh',{});$('tree').replaceChildren();scanNodes=[];renderScan();notice(I18N.join([{text:"Catalogo CPU aggiornato: "},{value:(result.symbols)},{text:" simboli. Esplora o scansiona l’albero."}]));}
  finally{window.refreshSourceState();}
});
window.renderS7UDPTags=()=>{
  const root=$('s7-udp-tag-list');root.replaceChildren();
  if(!window.s7UDPTags.length){const p=document.createElement('p');p.className='small';I18N.bind(p,"textContent",()=>(I18N.t("Nessun campo configurato.")),true);root.append(p);}
  window.s7UDPTags.forEach((tag,i)=>{
    const row=document.createElement('div');row.className='s7-tag-row';
    const name=document.createElement('span');I18N.bind(name,"textContent",()=>(tag.display_name.split('.').at(-1)),false);I18N.bind(name,"title",()=>(tag.display_name+' · '+tag.nodeid),false);
    const address=document.createElement('small');address.className='mono';I18N.bind(address,"textContent",()=>(tag.nodeid),false);
    const edit=document.createElement('button');edit.type='button';edit.className='ghost';I18N.bind(edit,"textContent",()=>(I18N.t("Modifica")),true);edit.onclick=()=>{
      s7UDPEditing=i;const m=tag.nodeid.match(/^s7:udp:(\d+)(?:\.([0-7]))?:(.+)$/);$('s7-udp-tag-name').value=tag.display_name;$('s7-udp-tag-offset').value=m[1];$('s7-udp-tag-bit').value=m[2]||0;
      const select=$('s7-udp-tag-type');if(![...select.options].some(o=>o.value===m[3]))select.add(I18N.option(()=>(m[3]),m[3],false,false,false));select.value=m[3];$('s7-udp-tag-cancel').hidden=false;
    };
    const remove=document.createElement('button');remove.type='button';remove.className='ghost';I18N.bind(remove,"textContent",()=>(I18N.t("Rimuovi")),true);remove.onclick=()=>commitS7UDP(window.s7UDPTags.filter((_,j)=>j!==i)).catch(e=>notice(e.message,true));
    row.append(name,address,edit,remove);root.append(row);
  });window.refreshSourceState?.();
};
async function commitS7UDP(tags){
  if(s7UDPBusy||state!=='disconnected'||connectionBusy||recordState.busy)throw new Error(I18N.t("Disconnetti e ferma Record prima di cambiare il catalogo UDP."));
  s7UDPBusy=true;const old=window.s7UDPTags;
  try{
    const result=await request('/api/s7/udp/tags/validate',{tags,config:{...config(),s7_udp_tags:tags}});window.s7UDPTags=result.tags;
    try{await request('/api/config',config());}catch(e){window.s7UDPTags=old;throw e;}
    s7UDPEditing=null;$('s7-udp-tag-cancel').hidden=true;window.renderS7UDPTags();scheduleWorkspaceSave();notice(I18N.t("Catalogo UDP salvato. Gli offset sono relativi al payload, dopo i 6 byte di header."));
  }finally{s7UDPBusy=false;}
}
bind('s7-udp-tag-add',async()=>{
  const tag={offset:Number($('s7-udp-tag-offset').value),bit:Number($('s7-udp-tag-bit').value),data_type:$('s7-udp-tag-type').value,display_name:$('s7-udp-tag-name').value.trim()};
  const tags=[...window.s7UDPTags];if(s7UDPEditing===null)tags.push(tag);else tags[s7UDPEditing]=tag;await commitS7UDP(tags);$('s7-udp-tag-name').value='';
});
bind('s7-udp-tag-cancel',async()=>{s7UDPEditing=null;$('s7-udp-tag-cancel').hidden=true;});
bind('s7-udp-tags-export',async()=>{
  const blob=new Blob([JSON.stringify({schema:1,mode:'udp',length:Number($('s7_udp_length').value),module_index:Number($('s7_udp_module').value),tags:window.s7UDPTags},null,2)],{type:'application/json'});
  const url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download='Space_Fieldbus_Analyzer_S7_UDP_Tags.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
});
$('s7-udp-tags-import').addEventListener('change',async e=>{
  try{const file=e.target.files[0];if(!file)return;if(file.size>2*1024*1024)throw new Error(I18N.t("JSON UDP: massimo 2 MiB."));const data=JSON.parse(await file.text());const tags=Array.isArray(data)?data:data.tags;if(!Array.isArray(tags))throw new Error(I18N.t("JSON UDP: serve un elenco tags."));await commitS7UDP([...window.s7UDPTags,...tags]);}
  catch(err){notice(err.message,true);}finally{e.target.value='';}
});
window.renderS7UDPDiagnostics=d=>{
  const el=$('s7-udp-diag'),n=d.s7_udp||{};el.hidden=!n.mode;
  const ms=v=>v==null?'—':Number(v).toFixed(3)+' ms';
  I18N.bind(el,"textContent",()=>(n.mode?I18N.join([{text:"S7 UDP · "},{value:(n.receiving?I18N.t("ricezione attiva"):I18N.t("in attesa / timeout"))},{text:" · periodo campioni "},{value:(ms(n.sample_period_ms))},{text:I18N.t(" · arrivo medio ")},{value:(ms(n.arrival_period_ms))},{text:" · P95 "},{value:(ms(n.p95_ms))},{text:" · ultimo telegramma "},{value:(ms(n.age_ms))},{text:" fa | Validi "},{value:(n.accepted)},{text:I18N.t(" · persi ")},{value:(n.lost)},{text:" · duplicati "},{value:(n.duplicates)},{text:" · fuori ordine "},{value:(n.out_of_order)},{text:I18N.t(" · invalidi ")},{value:(n.invalid)},{text:I18N.t(" · filtrati ")},{value:(n.filtered)},{text:" | Coda "},{value:(n.queue_samples)},{text:"/"},{value:(n.queue_capacity)},{text:" · overflow "},{value:(n.queue_dropped)},{text:" · timeout "},{value:(n.timeouts)},{text:" · errori timestamp "},{value:(n.timestamp_errors)},{text:" | Tempo: "},{value:(n.timestamp_origin)},{text:""}]):''),true);
  if(n.mode)I18N.bind($('mean-ms'),"title",()=>(I18N.t("Intervallo di ricezione dei telegrammi UDP, misurato dal PC")),true);
};
for(const id of s7Fields)$(id).addEventListener('change',()=>{
  if(id==='s7_family'&&$('s7_family').value!=='custom'){$('s7_rack').value=0;$('s7_slot').value=({'300':2,'400':3,'1200/1500':1})[$('s7_family').value];$('s7_remote_tsap').value='';}
  scheduleWorkspaceSave();
});
