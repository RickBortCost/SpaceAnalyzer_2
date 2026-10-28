/* Observed MQTT catalogue, original markers and interval formula editor. */
(()=>{
  let jobsKey='';
  window.renderRecordingJobs=info=>{
    const all=info.finalizations||[],jobs=[...all.filter(j=>j.busy),...all.filter(j=>!j.busy).slice(-8)].sort((a,b)=>String(b.started_at||'').localeCompare(String(a.started_at||''))),host=$('recording-jobs-list');
    if(!host)return;$('recording-jobs').hidden=!jobs.length;
    $('recording-jobs-count').textContent=String(info.finalizing_count||0);
    const key=JSON.stringify(jobs.map(j=>[j.recording_id,j.state,j.rows_written,j.conversion_phase,j.files,j.error]));if(key===jobsKey)return;jobsKey=key;host.replaceChildren();
    for(const job of jobs){
      const row=document.createElement('div');row.className='recording-job'+(job.error?' error':'');row.dataset.recordingId=job.recording_id;
      const name=document.createElement('strong'),status=document.createElement('span'),detail=document.createElement('div');
      name.textContent=(job.session?.session_name||job.filename||job.recording_id)+' · '+(job.format_label||job.format.toUpperCase());
      status.textContent=I18N.t(job.error?'Errore':job.busy?(job.conversion_phase==='converting'?'Conversione in corso':job.conversion_phase==='queued'?'Conversione in attesa':'Salvataggio originali'):'Pronto');
      detail.className='small';detail.textContent=job.error||(job.files||[]).map(f=>f.name).join(' · ')||job.original_path||job.path;row.append(name,status,detail);
      if(!job.busy&&!job.error&&job.files?.length){const button=document.createElement('button');button.type='button';button.className='ghost';button.textContent=I18N.t('Apri registrazione');button.onclick=async()=>{try{const result=await request('/api/recordings'),f=result.files.find(f=>f.source==='repository'&&f.name===job.files[0].name);if(!f)throw Error(I18N.t('Registrazione non trovata'));showAppPage('recordings');selectHistoryFile(f);await loadHistory();}catch(e){notice(e.message,true);}};row.append(button);}
      host.append(row);
    }
  };
  let autoscan=false,catalogBusy=false,catalogRevision='',catalogTicket=0,lastCatalog=null;
  let quickBusy=false,lastResult=null;
  const folders=new Map();
  window.mqttBrowseActive=()=>currentSource()==='mqtt';
  const mqttReady=()=>window.sourceSessionsStatus?.mqtt?window.sourceSessionsStatus.mqtt.state==='connected':currentSource()==='mqtt'&&connected;
  const write=(el,text)=>{if(el.textContent!==String(text))el.textContent=String(text);};
  function treeSync(data){
    const host=$('tree');
    if(!host.dataset.mqttTree){host.replaceChildren();host.dataset.mqttTree='true';folders.clear();}
    const byTopic=new Map();
    for(const node of data.nodes){const topic=node.nodeid.slice(5).split('#')[0];if(!byTopic.has(topic))byTopic.set(topic,[]);byTopic.get(topic).push(node);}
    for(const root of data.roots){
      let f=folders.get(root.nodeid);
      if(!f){
        const wrap=document.createElement('div'),row=document.createElement('div'),exp=document.createElement('button'),name=document.createElement('span'),children=document.createElement('div');
        row.className='node';exp.className='expander';exp.textContent='›';exp.setAttribute('aria-expanded','false');name.className='name';name.textContent=root.display_name.split('/').filter(Boolean).at(-1)||root.display_name;name.title=root.display_name+' · '+root.nodeid;exp.setAttribute('aria-label',root.display_name);children.className='indent';children.hidden=true;
        row.append(exp,name);wrap.append(row,children);host.append(wrap);f={wrap,children,exp,leaves:new Map()};folders.set(root.nodeid,f);
        exp.onclick=()=>{children.hidden=!children.hidden;exp.textContent=children.hidden?'›':'⌄';exp.setAttribute('aria-expanded',String(!children.hidden));};
      }
      for(const node of byTopic.get(root.display_name)||[]){
        let row=f.leaves.get(node.nodeid);
        if(!row){row=document.createElement('div');row.className='node';row.dataset.nodeid=node.nodeid;const name=document.createElement('span');name.className='name';row.append(selectionBox(node),name);f.children.append(row);f.leaves.set(node.nodeid,row);}
        write(row.querySelector('.name'),treeLabel(node));row.title=node.nodeid;const cb=row.querySelector('input');cb.checked=selected.has(node.nodeid);cb.disabled=!!recordingLocked();
      }
    }
  }
  window.refreshMqttCatalog=async function(tree=null){
    if(catalogBusy)return;
    const ticket=catalogTicket;catalogBusy=true;
    try{
      const data=await request('/api/mqtt/catalog?revision='+encodeURIComponent(catalogRevision));
      if(ticket!==catalogTicket||!mqttBrowseActive())return;
      if(!data.unchanged){catalogRevision=data.revision;lastCatalog=data;scanNodes=data.nodes;renderScan();treeSync(data);}
      else if(lastCatalog&&!$('tree').dataset.mqttTree){treeSync(lastCatalog);}
      if(tree!==null)setView(tree?'tree':'list');
      $('scan-progress').value=1;write($('result-count'),String(scanNodes.length));
      I18N.bind($('scan-status'),'textContent',()=>I18N.t('Catalogo MQTT aggiornato'),true);
    }catch(e){if(tree!==null)throw e;/* Keep the last visible tree during reconnect. */}
    finally{catalogBusy=false;}
  };
  window.refreshLiveExtras=()=>{
    const active=mqttBrowseActive();$('mqtt-autoscan').hidden=!active;$('mqtt-autoscan').disabled=!mqttReady();
    $('mqtt-autoscan').setAttribute('aria-pressed',String(autoscan));$('mqtt-autoscan').classList.toggle('active',autoscan);
    const sessions=window.sourceSessionsStatus||{};
    $('parallel-status').hidden=!Object.keys(sessions).length;
    write($('parallel-status'),Object.entries(sessions).map(([p,s])=>p.toUpperCase()+': '+I18N.t(s.state==='connected'?'Connesso':s.state==='reconnecting'?'Riconnessione…':'Non connesso')+(s.error?' · '+s.error:'')).join(' · '));

  };
  $('mqtt-autoscan').onclick=()=>{autoscan=!autoscan;refreshLiveExtras();if(autoscan)refreshMqttCatalog().catch(()=>{});};
  $('mqtt-parallel').onchange=()=>{
    if($('mqtt-parallel').checked)$('mqtt_time_basis').value='arrival';
    sourcePresentation();scheduleWorkspaceSave();
  };
  $('browse-source').onchange=()=>{
    catalogTicket++;catalogRevision='';lastCatalog=null;folders.clear();
    switchSource($('browse-source').value).then(()=>{refreshLiveExtras();if(mqttBrowseActive()&&mqttReady())refreshMqttCatalog(true).catch(e=>notice(e.message,true));}).catch(e=>notice(e.message,true));
  };
  // Explicit disconnect/source changes invalidate in-flight catalogue responses.
  $('disconnect').addEventListener('click',()=>{autoscan=false;catalogTicket++;catalogRevision='';refreshLiveExtras();});
  $('data-source').addEventListener('change',()=>{autoscan=false;catalogTicket++;catalogRevision='';lastCatalog=null;delete $('tree').dataset.mqttTree;});
  $('connect').addEventListener('click',()=>{catalogTicket++;catalogRevision='';lastCatalog=null;});
  let bindings=[{alias:'A',nodeid:''}],sources=new Map(),sourceKey='',registryKey='',formulaVersion=0,markerBusy=false,lastRequestKey='',quickClock='',activeGraph=null;
  const role=(g,name)=>g.element.querySelector('[data-chart-role="'+name+'"]');
  function renderQuick(){
    const raw=lastResult?.statistics?.[$('quick-operation').value];
    let unit=$('quick-unit').value;const metric=$('quick-operation').value;
    if(metric==='integral')unit=unit?unit+'·s':'s';else if(metric==='slope')unit=unit?unit+'/s':'1/s';
    write($('quick-value'),Number.isFinite(raw)?valueText(raw)+(unit?' '+unit:''):'—');
  }
  function invalidate(){formulaVersion++;lastResult=null;lastRequestKey='';renderQuick();write($('quick-status'),I18N.t('Premi Calcola per valutare la formula sull’intervallo selezionato.'));scheduleWorkspaceSave();}
  function insert(text){
    const area=$('quick-expression'),start=area.selectionStart,end=area.selectionEnd;
    area.value=area.value.slice(0,start)+text+area.value.slice(end);area.focus();
    area.selectionStart=area.selectionEnd=start+text.length-(text.endsWith('()')?1:0);invalidate();
  }
  for(const [label,text] of [['+',' + '],['−',' - '],['×',' * '],['÷',' / '],['(', '('],[')',')'],['abs','abs()'],['sqrt','sqrt()'],['mean','mean()'],['min','min()'],['max','max()'],['ifelse','ifelse()']]){
    const b=document.createElement('button');b.type='button';b.className='ghost';b.textContent=label;b.onclick=()=>insert(text);$('quick-tools').append(b);
  }
  function renderBindings(){
    const host=$('quick-bindings');host.replaceChildren();
    for(const binding of bindings){
      const row=document.createElement('div');row.className='quick-binding';
      const ref=document.createElement('button');ref.type='button';ref.className='ghost';ref.textContent='['+binding.alias+']';ref.onclick=()=>insert('['+binding.alias+']');
      const label=document.createElement('label'),select=document.createElement('select');label.append(document.createTextNode(I18N.t('Variabile')+' '+binding.alias),select);
      select.dataset.quickBinding=binding.alias;select.setAttribute('aria-label',I18N.t('Variabile')+' '+binding.alias);
      select.append(new Option(I18N.t('Seleziona una variabile'),''));
      for(const [id,node] of sources){const protocol=id.startsWith('calc:')?I18N.t('Calcolato'):id.startsWith('ads:')?'ADS':id.startsWith('mqtt:')?'MQTT':id.startsWith('s7:')?'S7':'OPC UA';const option=new Option('['+protocol+'] '+(node.display_name||id),id);option.title=id;select.append(option);}
      if(binding.nodeid&&!sources.has(binding.nodeid))select.append(new Option(binding.name||binding.nodeid,binding.nodeid));
      select.value=binding.nodeid;select.onchange=()=>{binding.nodeid=select.value;binding.name=sources.get(select.value)?.display_name||select.value;if(quickClock&&!bindings.some(b=>b.nodeid===quickClock))quickClock='';invalidate();};
      const remove=document.createElement('button');remove.type='button';remove.className='ghost';remove.textContent='×';remove.setAttribute('aria-label',I18N.t('Rimuovi')+' '+binding.alias);remove.disabled=bindings.length===1;
      remove.onclick=()=>{bindings=bindings.filter(b=>b!==binding);if(quickClock&&!bindings.some(b=>b.nodeid===quickClock))quickClock='';renderBindings();invalidate();};
      row.append(ref,label,remove);host.append(row);
    }
    $('quick-add-variable').disabled=bindings.length>=32;
  }
  function refreshQuickSources(){
    if(activeGraph!==liveCharts.active){activeGraph=liveCharts.active;lastResult=null;lastRequestKey='';formulaVersion++;renderQuick();write($('quick-range'),'');}
    sources=new Map([...(window.liveDataNodes?.(selected)||selected)].map(([id,node])=>[id,{...node,display_name:liveCharts.active?.signalSettings?.[id]?.alias||treeLabel(node)}]));
    const key=JSON.stringify([...sources].map(([id,n])=>[id,n.display_name]));
    if(!bindings[0]?.nodeid&&sources.size){bindings[0].nodeid=sources.keys().next().value;sourceKey='';}
    if(key!==sourceKey){sourceKey=key;renderBindings();}
    const list=calculationRegistry.formulas,key2=JSON.stringify(list.map(f=>[f.id,f.name,f.revision]));
    if(key2!==registryKey){
      registryKey=key2;const old=$('quick-saved').value;$('quick-saved').replaceChildren(new Option(I18N.t('Nuova formula'),''));
      for(const f of list)$('quick-saved').append(new Option(f.name,f.id));$('quick-saved').value=list.some(f=>f.id===old)?old:'';
    }
    $('quick-save').disabled=recordingLocked()||quickBusy||applicationClosed;
  }
  function definition(){
    const map={};
    for(const b of bindings)if(b.nodeid){const source=sources.get(b.nodeid);map[b.alias]={nodeid:b.nodeid,name:source?.display_name||b.name||b.alias,unit:source?.unit||'',kind:b.nodeid.startsWith('calc:')?'formula':'signal'};}
    return {name:$('quick-name').value.trim()||I18N.t('Calcolo intervallo'),unit:$('quick-unit').value.trim(),expression:$('quick-expression').value,
      bindings:map,clock_nodeid:quickClock||null,alignment:$('quick-alignment').value,max_age_ms:Number($('quick-age').value),active:true,modes:{live:true,recorded:true}};
  }
  window.intervalFormulaDefinition=definition;
  $('quick-add-variable').onclick=()=>{
    let index=0,alias;do{alias=index<26?String.fromCharCode(65+index):'V'+(index+1);index++;}while(bindings.some(b=>b.alias===alias));
    bindings.push({alias,nodeid:[...sources.keys()].find(id=>!bindings.some(b=>b.nodeid===id))||''});renderBindings();invalidate();
  };
  for(const id of ['quick-name','quick-unit','quick-expression','quick-alignment','quick-age'])$(id).addEventListener('input',invalidate);
  $('quick-operation').addEventListener('change',()=>{renderQuick();scheduleWorkspaceSave();});
  $('quick-saved').onchange=()=>{
    const f=calculationRegistry.formulas.find(f=>f.id===$('quick-saved').value);
    if(!f){quickClock='';bindings=[{alias:'A',nodeid:sources.keys().next().value||''}];$('quick-name').value=I18N.t('Calcolo intervallo');$('quick-unit').value='';$('quick-expression').value='[A]';$('quick-alignment').value='hold';$('quick-age').value='1000';renderBindings();invalidate();return;}
    quickClock=f.clock_nodeid||'';bindings=Object.entries(f.bindings).map(([alias,b])=>({alias,...b}));
    for(const [id,value] of Object.entries({'quick-name':f.name,'quick-unit':f.unit,'quick-expression':f.expression,'quick-alignment':f.alignment,'quick-age':f.max_age_ms}))$(id).value=value;
    renderBindings();invalidate();
  };
  async function snapshotBody(graph,ids,range){
    const body={source:'live',signal_ids:ids,...range},t=graph.trend;
    if(t.frozen){await graph.analysisSnapshotPromise;if(!graph.analysisSnapshotId)throw Error(I18N.t('Riprendi Play e metti nuovamente in Pausa'));body.snapshot_id=graph.analysisSnapshotId;}
    return body;
  }
  async function refreshMarkers(){
    if(markerBusy||applicationClosed||$('live-page').hidden)return;markerBusy=true;
    try{
      for(const graph of liveCharts.charts){
        const t=graph.trend,range={...t.markers},marked=Number.isFinite(range.x1)&&Number.isFinite(range.x2)&&range.x1!==range.x2;
        if(!marked){t.exactMarkerMode=false;t.exactMarkerRows=null;t.markerError='';continue;}
        if(!liveCharts.analysisOpen(graph))continue;
        const frozen=t.frozen,revision=graph.analysisSnapshotRevision,ids=[...t.nodes.keys()].slice(0,64);if(!ids.length)continue;
        try{
          t.exactMarkerMode=true;t.markerError='';
          const body=await snapshotBody(graph,ids,range),result=await request('/api/analysis/markers',body);
          if(!liveCharts.charts.includes(graph)||frozen!==t.frozen||revision!==graph.analysisSnapshotRevision)continue;
          if(frozen&&(range.x1!==t.markers.x1||range.x2!==t.markers.x2))continue;
          t.exactMarkerTimes=range;t.exactMarkerRows=new Map(result.results.map(r=>[r.nodeid,r]));t.updateMarkerTable();
        }catch(e){
          if(revision!==graph.analysisSnapshotRevision)continue;
          t.exactMarkerTimes=range;t.exactMarkerRows=new Map();t.markerError=e.message;t.updateMarkerTable();
        }
      }
    }finally{markerBusy=false;}
  }
  async function runQuick(force=true){
    if(quickBusy||applicationClosed)return;refreshQuickSources();
    const graph=liveCharts.active,t=graph?.trend;if(!t)return;
    const marked=Number.isFinite(t.markers.x1)&&Number.isFinite(t.markers.x2)&&t.markers.x1!==t.markers.x2;
    const range=marked?{...t.markers}:{x1:t.domain?.xmin,x2:t.domain?.xmax},draft=definition(),version=formulaVersion;
    if(!Number.isFinite(range.x1)||!Number.isFinite(range.x2)||range.x1===range.x2){write($('quick-status'),I18N.t('Nessun campione nella vista congelata'));return;}
    const key=JSON.stringify([draft,range,graph.id,graph.analysisSnapshotRevision]);if(!force&&key===lastRequestKey)return;
    quickBusy=true;$('quick-run').disabled=true;$('quick-save').disabled=true;write($('quick-status'),I18N.t('Calcolo in corso…'));
    try{
      const body=await snapshotBody(graph,Object.values(draft.bindings).map(b=>b.nodeid),range);
      const result=await request('/api/analysis/quick',{...body,formula:draft});
      if(version!==formulaVersion||graph!==liveCharts.active)return;
      lastResult=result.results[0];lastRequestKey=key;renderQuick();
      write($('quick-range'),I18N.t(marked?'Intervallo marker':'Finestra visibile')+' · '+new Date(range.x1).toLocaleTimeString(I18N.locale())+' → '+new Date(range.x2).toLocaleTimeString(I18N.locale()));
      write($('quick-status'),lastResult.count+' '+I18N.t('campioni validi')+' · '+lastResult.excluded+' '+I18N.t('esclusi')+(lastResult.count?'':' · '+(lastResult.last_status||I18N.t('Nessun campione valido'))));
    }catch(e){if(version===formulaVersion){lastResult=null;renderQuick();write($('quick-status'),e.message);}}
    finally{quickBusy=false;$('quick-run').disabled=false;$('quick-save').disabled=recordingLocked()||applicationClosed;}
  }
  $('quick-run').onclick=()=>runQuick();
  $('quick-save').onclick=async()=>{
    try{
      if(recordingLocked())throw Error(I18N.t('Premere Stop prima di modificare le formule: la registrazione conserva la definizione iniziale'));
      const f=calculationRegistry.formulas.find(f=>f.id===$('quick-saved').value),draft=definition();
      if(f){draft.id=f.id;draft.expected_revision=f.revision;}
      const result=await request('/api/calculations/save',draft);await applyCalculationRegistry(result);await ensureCalculationSources(result.formula);
      liveCharts.assignTo(liveCharts.active,result.formula.nodeid,true);refreshQuickSources();$('quick-saved').value=result.formula.id;
      write($('quick-status'),I18N.t('Formula salvata e aggiunta al grafico.'));
    }catch(e){write($('quick-status'),e.message);}
  };
  $('quick-editor').onclick=()=>window.openIntervalFormula(definition(),calculationRegistry.formulas.find(f=>f.id===$('quick-saved').value));
  async function quickTick(){
    if(applicationClosed||$('live-page').hidden)return;
    refreshQuickSources();await refreshMarkers();
    if($('quick-calculations').open&&$('quick-auto').checked)await runQuick(false);
  }
  const timer=setInterval(()=>{if(applicationClosed){clearInterval(timer);return;}refreshLiveExtras();if(autoscan&&mqttBrowseActive()&&mqttReady()&&!document.hidden)refreshMqttCatalog().catch(()=>{});quickTick();},1000);
  window.quickCalculationsTick=quickTick;
  const capture=window.captureStudio,restore=window.restoreStudio;
  window.captureStudio=()=>({...capture(),quick:{clock:quickClock,bindings:bindings.map(b=>({...b})),name:$('quick-name').value,unit:$('quick-unit').value,expression:$('quick-expression').value,alignment:$('quick-alignment').value,age:$('quick-age').value,operation:$('quick-operation').value,automatic:$('quick-auto').checked,saved:$('quick-saved').value,open:$('quick-calculations').open}});
  window.restoreStudio=saved=>{
    restore(saved);const q=saved.quick;if(!q)return;quickClock=q.clock||'';
    if(Array.isArray(q.bindings)&&q.bindings.length)bindings=q.bindings.slice(0,32).map(b=>({...b}));
    else if(q.signal)bindings=[{alias:'A',nodeid:q.signal}];
    for(const [key,id] of Object.entries({name:'quick-name',unit:'quick-unit',expression:'quick-expression',alignment:'quick-alignment',age:'quick-age',operation:'quick-operation'}))if(q[key]!==undefined)$(id).value=q[key];
    $('quick-auto').checked=!!q.automatic;$('quick-calculations').open=q.open!==false;
    refreshQuickSources();$('quick-saved').value=q.saved||'';renderBindings();
  };
  window.addEventListener('space-analyzer-languagechange',()=>{jobsKey='';window.renderRecordingJobs(recordState);sourceKey='';registryKey='';refreshQuickSources();renderQuick();});
  $('quick-auto').onchange=()=>{scheduleWorkspaceSave();if($('quick-auto').checked)runQuick();};
  write($('quick-status'),I18N.t('Premi Calcola per valutare la formula sull’intervallo selezionato.'));
  refreshLiveExtras();
})();
