/* One persisted registry; calculation series reuse the existing chart workspace. */
let calculationRegistry={formulas:[],revision:0},calculationDraft=null,calculationBindings={},calculationSources=[],calculationFile=null,calculationBusy=false,calculationEditSequence=0;
window.calculationContextMode='live';
window.calculationProtocolAvailable=protocol=>protocol==='any'||(window.simultaneousEnabled?.()?window.enabledProtocols?.().includes(protocol):protocol===(window.currentSource?.()||'opcua'));
const C=id=>document.getElementById(id);
function calculationNode(f){return {nodeid:f.nodeid,display_name:f.name,unit:f.unit||f.unit_hint||'',calculated:true,data_type:'Double',node_class:'Variable'};}
window.liveDataNodes=nodes=>{
  const merged=new Map(nodes),protocol=window.currentSource?.()||'opcua';
  for(const f of calculationRegistry.formulas)if(f.active&&f.modes.live&&window.calculationProtocolAvailable(f.protocol))merged.set(f.nodeid,calculationNode(f));
  return merged;
};
window.loadCalculations=payload=>{calculationRegistry={formulas:payload.formulas||[],revision:payload.revision||0,error:payload.error||''};renderCalculationList();if(payload.error)calcError(payload.error);};
function calcError(message){I18N.bind(C('calc-error'),"textContent",()=>(message||''),true);C('calc-error').hidden=!message;}
function calcControls(){
  for(const button of C('calculations-page').querySelectorAll('button'))button.disabled=calculationBusy||applicationClosed;
  for(const id of ['calc-save','calc-new'])C(id).disabled=calculationBusy||applicationClosed||recordingLocked();
  for(const button of C('calc-list').querySelectorAll('[data-edit]'))button.disabled=calculationBusy||recordingLocked()||applicationClosed;
  for(const id of ['calc-add-live','calc-add-recorded','calc-materialize'])C(id).disabled=calculationBusy||!calculationDraft?.id||applicationClosed||(id==='calc-add-live'&&recordingLocked());
}
async function calcRun(action){
  if(calculationBusy)return;
  calculationBusy=true;calcError('');calcControls();
  try{return await action();}catch(e){calcError(e.message);notice(e.message,true);}finally{calculationBusy=false;calcControls();}
}
function renderCalculationList(){
  const host=C('calc-list');if(!host)return;host.replaceChildren();const query=C('calc-list-search').value.trim().toLocaleLowerCase(I18N.locale());
  for(const f of calculationRegistry.formulas){
    if(query&&!JSON.stringify([f.name,f.expression,f.bindings]).toLocaleLowerCase(I18N.locale()).includes(query))continue;
    const box=document.createElement('article');box.className='calculation-item'+(!f.active?' inactive':'')+(calculationDraft?.id===f.id?' current':'');
    const name=document.createElement('strong');I18N.bind(name,"textContent",()=>(f.name+(f.unit?' · '+f.unit:'')),false);I18N.bind(name,"title",()=>(f.nodeid),false);
    const expr=document.createElement('p');I18N.bind(expr,"textContent",()=>(f.expression),false);box.append(name,expr);
    for(const label of [f.modes.live?I18N.t("Live"):null,f.modes.recorded?'Recorded':null,f.active?I18N.t("Attiva"):I18N.t("Disattivata"),'Rev. '+f.revision])if(label){const badge=document.createElement('span');badge.className='calculation-badge';I18N.bind(badge,"textContent",()=>(label),true);box.append(badge);}
    const status=document.createElement('small');status.className='calculation-row-status';status.dataset.calculation=f.nodeid;box.append(status);
    const bar=document.createElement('div');bar.className='toolbar';
    const button=(label,fn,editing=false)=>{const b=document.createElement('button');b.type='button';I18N.bind(b,"textContent",()=>(label),true);if(editing)b.dataset.edit='true';b.onclick=()=>calcRun(fn);bar.append(b);};
    button(I18N.t("Modifica"),()=>editCalculation(f));
    button(f.active?I18N.t("Disattiva"):I18N.t("Attiva"),()=>saveCalculationDefinition({...f,active:!f.active,expected_revision:f.revision}),true);
    button(I18N.t("Duplica"),async()=>{const result=await request('/api/calculations/duplicate',{id:f.id});await applyCalculationRegistry(result);editCalculation(result.formula);},true);
    button(I18N.t("Elimina"),async()=>{if(!window.confirm(I18N.join([{text:"Eliminare la formula “"},{value:(f.name)},{text:"”? I file già registrati vengono conservati."}])))return;const result=await request('/api/calculations/delete',{id:f.id});await applyCalculationRegistry(result);if(calculationDraft?.id===f.id)newCalculation();},true);
    box.append(bar);host.append(box);
  }
  if(!host.children.length){const p=document.createElement('p');p.className='small';I18N.bind(p,"textContent",()=>(calculationRegistry.formulas.length?I18N.t("Nessuna formula corrisponde alla ricerca."):I18N.t("Crea una formula usando i segnali Live oppure una registrazione.")),true);host.append(p);}
  calcControls();
}
async function applyCalculationRegistry(payload){
  window.loadCalculations(payload);renderSelected();
  for(const graph of historyCharts.charts){
    const ctx=historyContext(graph);if(!ctx.file)continue;
    ctx.file.signals=(ctx.file.signals||[]).filter(s=>!s.virtual||calculationRegistry.formulas.some(f=>f.nodeid===s.nodeid&&f.active&&f.modes.recorded));
    for(const id of [...ctx.chosen])if(id.startsWith('calc:')&&!ctx.file.signals.some(s=>s.nodeid===id))ctx.chosen.delete(id);
    if(ctx.loaded&&[...graph.ids].some(n=>n.startsWith('calc:'))){
      if(!ctx.chosen.size){historyCharts.setGraphData(graph,{},new Map(),{});ctx.loaded=false;ctx.status=I18N.t("Variabile calcolata eliminata o disattivata.");}
      else await loadHistory(graph).catch(e=>notice(I18N.join([{text:"Aggiornamento registrazione: "},{value:(e.message)}]),true));
    }
  }
  renderHistorySignals();scheduleWorkspaceSave();
}
window.disableCalculation=async nid=>{const f=calculationRegistry.formulas.find(f=>f.nodeid===nid);if(!f)return;await saveCalculationDefinition({...f,active:false,expected_revision:f.revision});};
window.openIntervalFormula=(definition,existing)=>{
  if(existing)editCalculation(existing);else newCalculation();
  calculationBindings=JSON.parse(JSON.stringify(definition.bindings));
  C('calc-name').value=definition.name;C('calc-unit').value=definition.unit;
  C('calc-expression').value=definition.expression;C('calc-alignment').value=definition.alignment;C('calc-age').value=definition.max_age_ms;
  calculationEditSequence++;refreshCalculationSources();C('calc-clock').value=definition.clock_nodeid||'';syntaxCalculation();showAppPage('calculations');
};
async function saveCalculationDefinition(definition){const result=await request('/api/calculations/save',definition);await applyCalculationRegistry(result);return result.formula;}
function newCalculation(){
  calculationDraft=null;calculationBindings={};calculationEditSequence++;
  C('calc-form').reset();C('calc-live').checked=true;C('calc-recorded').checked=true;C('calc-active').checked=true;C('calc-context').value=window.calculationContextMode;
  I18N.bind(C('calc-builder-title'),"textContent",()=>(I18N.t("Nuova variabile calcolata")),true);I18N.bind(C('calc-revision'),"textContent",()=>(''),false);I18N.bind(C('calc-validation'),"textContent",()=>(''),false);I18N.bind(C('calc-materialize-status'),"textContent",()=>(''),false);calcError('');clearCalculationPreview();refreshCalculationContext(false);renderCalculationList();syntaxCalculation();
}
function editCalculation(f){
  calculationDraft=JSON.parse(JSON.stringify(f));calculationBindings=JSON.parse(JSON.stringify(f.bindings));calculationEditSequence++;
  C('calc-name').value=f.name;C('calc-unit').value=f.unit;C('calc-expression').value=f.expression;C('calc-live').checked=f.modes.live;C('calc-recorded').checked=f.modes.recorded;C('calc-active').checked=f.active;C('calc-alignment').value=f.alignment;C('calc-age').value=f.max_age_ms;
  I18N.bind(C('calc-builder-title'),"textContent",()=>(I18N.join([{text:"Modifica · "},{value:(f.name)}])),true);I18N.bind(C('calc-revision'),"textContent",()=>('Rev. '+f.revision+' · creata '+new Date(f.created_at).toLocaleString(I18N.locale())),true);I18N.bind(C('calc-validation'),"textContent",()=>(''),false);calcError('');clearCalculationPreview();refreshCalculationSources();syntaxCalculation();C('calc-clock').value=f.clock_nodeid||'';renderCalculationList();
}
function formulaDefinition(){
  const expression=C('calc-expression').value,bindings={...calculationBindings};
  // Names typed by hand are resolved only when the visible source name is unique.
  for(const match of expression.matchAll(/\[([^\[\]]+)\]/g)){const alias=match[1].trim();if(bindings[alias])continue;const found=calculationSources.filter(s=>sourceAlias(s)===alias);if(found.length===1)bindings[alias]=sourceBinding(found[0]);}
  return {id:calculationDraft?.id,name:C('calc-name').value.trim(),unit:C('calc-unit').value.trim(),expression,bindings,
    active:C('calc-active').checked,modes:{live:C('calc-live').checked,recorded:C('calc-recorded').checked},alignment:C('calc-alignment').value,
    max_age_ms:Number(C('calc-age').value),clock_nodeid:C('calc-clock').value||null,expected_revision:calculationDraft?.revision};
}
function sourceAlias(source){return String(source.display_name||source.name||treeLabel(source)||source.nodeid).replace(/[\[\]]/g,'').trim().slice(0,120)||I18N.t("Variabile");}
function sourceBinding(source){return {nodeid:source.nodeid,name:source.display_name||source.name||source.nodeid,unit:source.unit||'',kind:source.kind||(source.nodeid.startsWith('calc:')?'formula':'signal')};}
function refreshCalculationSources(){
  const map=new Map();
  const physical=window.calculationContextMode==='recorded'?(calculationFile?.signals||[]):[...selected.values(),...scanNodes.filter(n=>n.node_class==='Variable')];
  for(const source of physical){const settings=liveCharts.active.signalSettings?.[source.nodeid];map.set(source.nodeid,{...source,display_name:source.nodeid.startsWith('ads:')?treeLabel(source):source.display_name||source.nodeid,unit:source.unit||(window.calculationContextMode==='live'?settings?.unit:'')||'',kind:source.nodeid.startsWith('calc:')?'stored':source.kind||'signal'});}
  if(window.calculationContextMode==='live')for(const f of calculationRegistry.formulas)for(const source of f.required_sources||[])if(!map.has(source.nodeid)&&source.kind!=='stored')map.set(source.nodeid,{...source,display_name:source.name});
  for(const f of calculationRegistry.formulas)if(f.id!==calculationDraft?.id&&f.modes[window.calculationContextMode]){
    // Prefer stored columns in replay; they preserve the original formula revision.
    if(!map.has(f.nodeid))map.set(f.nodeid,{...calculationNode(f),kind:'formula'});
  }
  calculationSources=[...map.values()];renderCalculationSources();
}
function renderCalculationSources(){
  const host=C('calc-source-list');host.replaceChildren();const q=C('calc-source-search').value.toLocaleLowerCase(I18N.locale());
  const found=calculationSources.filter(s=>!q||(sourceAlias(s)+' '+s.nodeid).toLocaleLowerCase(I18N.locale()).includes(q));
  for(const source of found.slice(0,150)){const b=document.createElement('button');b.type='button';I18N.bind(b,"textContent",()=>(sourceAlias(source)+(source.unit?' · '+source.unit:'')),false);I18N.bind(b,"title",()=>(source.nodeid),false);b.className=source.nodeid.startsWith('calc:')?'source-calculated':'';b.onclick=()=>insertCalculationSource(source);host.append(b);}
  if(!found.length){const p=document.createElement('p');p.className='small';I18N.bind(p,"textContent",()=>(I18N.t("Seleziona o scansiona le variabili Live, oppure scegli un file registrato.")),true);host.append(p);}
  if(found.length>150){const p=document.createElement('p');p.className='small';I18N.bind(p,"textContent",()=>(I18N.t("Prime 150 variabili: usa il filtro per trovare le altre.")),true);host.append(p);}
}
function insertCalculationSource(source,replaceFrom=null){
  let alias=sourceAlias(source),i=1;const base=alias;
  while(calculationBindings[alias]&&calculationBindings[alias].nodeid!==source.nodeid)alias=base+' ('+(++i)+')';
  calculationBindings[alias]=sourceBinding(source);
  if(source.kind==='stored'){C('calc-live').checked=false;C('calc-recorded').checked=true;}
  insertCalculationText('['+alias+']',replaceFrom);
}
function insertCalculationText(text,replaceFrom=null){
  const area=C('calc-expression'),start=replaceFrom??area.selectionStart,end=area.selectionEnd;
  area.value=area.value.slice(0,start)+text+area.value.slice(end);area.focus();area.selectionStart=area.selectionEnd=start+text.length;
  calculationEditSequence++;syntaxCalculation();
}
function syntaxCalculation(){
  const expression=C('calc-expression').value,host=C('calc-syntax');host.replaceChildren();
  for(const part of expression.split(/(\[[^\[\]]+\]|[+*/()<>-]|\b(?:abs|sqrt|mean|min|max|pow|ifelse)\b)/g)){const span=document.createElement('span');I18N.bind(span,"textContent",()=>(part),false);span.className=part.startsWith('[')?'reference':/^[+*/()<>-]$/.test(part)?'operation':'';host.append(span);}
  const bindings=C('calc-bindings');bindings.replaceChildren();const clock=C('calc-clock'),previous=clock.value;clock.replaceChildren(I18N.option(()=>(I18N.t("Qualsiasi sorgente")),'',false,false,true));
  const used=new Set([...expression.matchAll(/\[([^\[\]]+)\]/g)].map(m=>m[1].trim()));
  for(const alias of used){const source=calculationBindings[alias]||calculationSources.filter(s=>sourceAlias(s)===alias).map(sourceBinding)[0];const p=document.createElement('p');I18N.bind(p,"textContent",()=>('['+alias+'] → '+(source?.nodeid||'da associare')),false);bindings.append(p);if(source&&!Array.from(clock.options).some(o=>o.value===source.nodeid))clock.add(I18N.option(()=>(alias),source.nodeid,false,false,false));}
  clock.value=previous;autocompleteCalculation();
}
function autocompleteCalculation(){
  const area=C('calc-expression'),before=area.value.slice(0,area.selectionStart),match=before.match(/\[([^\[\]]*)$/),host=C('calc-autocomplete');host.replaceChildren();host.hidden=!match;if(!match)return;
  const q=match[1].toLocaleLowerCase(I18N.locale()),start=area.selectionStart-match[0].length;
  for(const source of calculationSources.filter(s=>sourceAlias(s).toLocaleLowerCase(I18N.locale()).includes(q)).slice(0,8)){const b=document.createElement('button');b.type='button';I18N.bind(b,"textContent",()=>(sourceAlias(source)),false);I18N.bind(b,"title",()=>(source.nodeid),false);b.onclick=()=>insertCalculationSource(source,start);host.append(b);}
  if(!host.children.length)host.hidden=true;
}
async function refreshCalculationContext(load=true){
  window.calculationContextMode=C('calc-context').value;const recorded=window.calculationContextMode==='recorded';C('calc-file-field').hidden=!recorded;C('calc-interval-field').hidden=!recorded;
  if(recorded&&load)await calculationRecordingChanged();else refreshCalculationSources();
}
async function calculationRecordingChanged(){
  const option=C('calc-recording').selectedOptions[0];calculationFile=option?.dataset.name?{source:option.dataset.source,name:option.dataset.name}:null;
  if(calculationFile){const result=await request('/api/recordings/data',{...calculationFile,max_points:1000});calculationFile.signals=result.recording.signals.map(s=>({...s,kind:s.nodeid.startsWith('ads:')||/^(?:ns=\d+;)?[isgb]=/.test(s.nodeid)?'signal':'stored'}));}
  refreshCalculationSources();syntaxCalculation();
}
window.openCalculationsPage=()=>window.calculationsPageReady=calcRun(async()=>{
  const current=historyContext().file,files=[...historyFiles];if(current&&!files.some(f=>f.name===current.name&&f.source===current.source))files.unshift(current);
  const result=await request('/api/recordings');for(const f of result.files||[])if(!files.some(x=>x.name===f.name))files.push({source:'repository',...f});
  const picker=C('calc-recording'),old=picker.value;picker.replaceChildren(I18N.option(()=>(I18N.t("Seleziona una registrazione")),'',false,false,true));
  for(const file of files){const option=I18N.option(()=>(file.display_name||file.name),(file.source||'repository')+'|'+file.name,false,false,false);option.dataset.source=file.source||'repository';option.dataset.name=file.name;picker.add(option);}
  picker.value=old||((current?.source||'repository')+'|'+current?.name);if(!picker.value)picker.value='';
  if(!calculationDraft&&!C('calc-name').value)newCalculation();
  await refreshCalculationContext();renderCalculationList();
});
function calculationRange(){
  if(!C('calc-use-interval').checked)return {};
  const graph=historyCharts.active,ctx=historyContext(graph);if(!calculationFile||ctx.file?.name!==calculationFile.name||ctx.file?.source!==calculationFile.source)throw new Error(I18N.t("Per usare l’intervallo seleziona lo stesso file del grafico Registrazioni."));
  const m=historyCharts.markers;if(Number.isFinite(m.x1)&&Number.isFinite(m.x2))return {start_ms:Math.min(m.x1,m.x2),end_ms:Math.max(m.x1,m.x2)};
  return {start_ms:msFromLocalInput(C('history-from').value),end_ms:msFromLocalInput(C('history-to').value)};
}
function clearCalculationPreview(){C('calc-preview-chart').replaceChildren();I18N.bind(C('calc-preview-value'),"textContent",()=>('—'),false);C('calc-preview-table').replaceChildren();I18N.bind(C('calc-preview-status'),"textContent",()=>(I18N.t("Premi Test e anteprima.")),true);}
function showCalculationPreview(result){
  I18N.bind(C('calc-preview-value'),"textContent",()=>((result.value===null?'—':valueText(result.value))+(result.unit?' '+result.unit:'')),false);I18N.bind(C('calc-preview-status'),"textContent",()=>(I18N.join([{value:(result.quality)},{text:" · "},{value:(result.status)},{text:" · "},{value:(result.sample_count)},{text:" campioni in anteprima"}])),true);
  const host=C('calc-preview-chart');host.replaceChildren();const valid=result.series.filter(p=>Number.isFinite(p[1]));
  if(valid.length){const xmin=result.series[0][0],xmax=result.series.at(-1)[0],values=valid.map(p=>p[1]),min=Math.min(...values),max=Math.max(...values);let segment=[];const flush=()=>{if(!segment.length)return;const line=document.createElementNS('http://www.w3.org/2000/svg','polyline');line.setAttribute('fill','none');line.setAttribute('stroke','#009FE3');line.setAttribute('stroke-width','2');line.setAttribute('points',segment.join(' '));host.append(line);segment=[];};for(const p of result.series){if(!Number.isFinite(p[1])){flush();continue;}segment.push((12+(p[0]-xmin)/Math.max(1,xmax-xmin)*576)+','+(128-(p[1]-min)/Math.max(1e-12,max-min)*112));}flush();}
  const table=document.createElement('table'),header=document.createElement('tr');for(const text of ['Timestamp',I18N.t("Valore"),I18N.t("Qualità")]){const th=document.createElement('th');I18N.bind(th,"textContent",()=>(text),true);header.append(th);}table.append(header);
  for(const p of result.series.slice(-6)){const tr=document.createElement('tr');for(const text of [new Date(p[0]).toLocaleTimeString(I18N.locale())+'.'+String(Math.round(p[0])%1000).padStart(3,'0'),p[1]===null?'—':valueText(p[1]),p[2]]){const td=document.createElement('td');I18N.bind(td,"textContent",()=>(text),true);tr.append(td);}if(p[2]!=='Good')tr.className='bad';table.append(tr);}C('calc-preview-table').replaceChildren(table);
}
async function validateCalculation(){const result=await request('/api/calculations/validate',{formula:formulaDefinition()});I18N.bind(C('calc-validation'),"textContent",()=>(I18N.join([{text:"Formula valida · "},{value:(result.required_sources.length)},{text:" sorgenti"},{value:(result.unit_hint?I18N.join([{text:" · unità stimata: "},{value:(result.unit_hint)}]):I18N.t(" · specificare l’unità se necessaria"))}])),true);return result;}
async function testCalculation(){const sequence=calculationEditSequence,mode=window.calculationContextMode;if(mode==='recorded'&&!calculationFile)throw new Error(I18N.t("Seleziona una registrazione."));const result=await request('/api/calculations/preview',{formula:formulaDefinition(),mode,...(mode==='recorded'?{...calculationFile,...calculationRange()}:{})});if(sequence===calculationEditSequence){showCalculationPreview(result);I18N.bind(C('calc-validation'),"textContent",()=>(I18N.join([{text:"Formula validata e testata"},{value:(result.formula.unit_hint?I18N.join([{text:" · unità stimata: "},{value:(result.formula.unit_hint)}]):'')}])),true);}}
async function ensureCalculationSources(f){
  const source=window.currentSource?.()||'opcua';if(!window.calculationProtocolAvailable(f.protocol))throw new Error(I18N.join([{text:"Seleziona la fonte "},{value:(f.protocol.toUpperCase())},{text:" nella finestra Connessione."}]));
  const needed=(f.required_sources||[]).filter(s=>!selected.has(s.nodeid));if(selected.size+needed.length>maxSelected)throw new Error(I18N.join([{text:"La formula supera il limite di "},{value:(maxSelected)},{text:" variabili acquisite."}]));
  for(const s of needed)selected.set(s.nodeid,{nodeid:s.nodeid,display_name:s.name,node_class:'Variable',unit:s.unit});
  if(needed.length){await syncSelection();syncCheckboxes();}renderSelected();
}
async function saveCalculation(){
  const validation=await validateCalculation(),f=await saveCalculationDefinition(formulaDefinition());editCalculation(f);
  if(f.active&&f.modes.live&&window.calculationProtocolAvailable(f.protocol))await ensureCalculationSources(f);
  I18N.bind(C('calc-validation'),"textContent",()=>(I18N.join([{text:"Formula salvata · Rev. "},{value:(f.revision)},{value:(validation.unit_hint?' · '+validation.unit_hint:'')}])),true);notice(I18N.join([{text:"Variabile calcolata “"},{value:(f.name)},{text:"” salvata."}]));scheduleWorkspaceSave();
}
function savedCalculation(mode){const f=calculationRegistry.formulas.find(x=>x.id===calculationDraft?.id);if(!f)throw new Error(I18N.t("Salva prima la formula."));if(!f.active||!f.modes[mode])throw new Error(I18N.join([{text:"Abilita la formula nella modalità "},{value:(mode)},{text:"."}]));if(JSON.stringify(formulaDefinition().bindings)!==JSON.stringify(f.bindings)||C('calc-expression').value.trim()!==f.expression||C('calc-name').value.trim()!==f.name)throw new Error(I18N.t("Salva le modifiche prima di applicare la formula."));return f;}
async function addCalculationLive(){const f=savedCalculation('live');await ensureCalculationSources(f);liveCharts.assign(f.nodeid,true);showAppPage('live');renderSelected();scheduleWorkspaceSave();}
async function applyCalculationRecorded(){
  const f=savedCalculation('recorded');if(!calculationFile)throw new Error(I18N.t("Scegli una registrazione nelle sorgenti del Formula Builder."));
  const graph=historyCharts.active,ctx=historyContext(graph),range=calculationRange();
  if(ctx.file?.name!==calculationFile.name||ctx.file?.source!==calculationFile.source)selectHistoryFile({...calculationFile,format:calculationFile.name.endsWith('.csv')?'csv':'parquet'},graph);
  const current=historyContext(graph),signals=new Map((current.file.signals||[]).map(s=>[s.nodeid,s]));if(!signals.has(f.nodeid)||signals.get(f.nodeid).virtual)signals.set(f.nodeid,{...calculationNode(f),virtual:true});current.file.signals=[...signals.values()];current.chosen.add(f.nodeid);
  current.from=range.start_ms!=null?localInputFromMs(range.start_ms):'';current.to=range.end_ms!=null?localInputFromMs(range.end_ms):'';
  renderHistoryControls();await loadHistory(graph);showAppPage('recordings');scheduleWorkspaceSave();
}
async function materializeCalculation(){
  const f=savedCalculation('recorded');if(!calculationFile)throw new Error(I18N.t("Seleziona una registrazione."));
  const ids=[...new Set([...(calculationFile.signals||[]).map(s=>s.nodeid),f.nodeid])],format=C('calc-output-format').value;
  I18N.bind(C('calc-materialize-status'),"textContent",()=>(I18N.t("Calcolo e salvataggio dei campioni originali…")),true);
  const result=await request('/api/calculations/materialize',{...calculationFile,signal_ids:ids,format,...calculationRange()});
  I18N.bind(C('calc-materialize-status'),"textContent",()=>(I18N.join([{text:"Salvato: "},{value:(result.path)},{text:" · "},{value:(result.recording.rows_written)},{text:" righe. Il file originale è conservato."}])),true);await refreshHistoryFiles();notice(I18N.join([{text:"Nuovo dataset "},{value:(format.toUpperCase())},{text:" salvato in Recordings."}]));
}
window.renderCalculationStatus=(diagnostics,values)=>{
  if(!diagnostics)return;const missing=calculationRegistry.formulas.filter(f=>diagnostics.active_ids.includes(f.nodeid)&&f.required_sources.some(s=>!selected.has(s.nodeid))).length;
  I18N.bind(C('calc-service-status'),"textContent",()=>(I18N.join([{text:"Coda "},{value:(diagnostics.queue)},{text:"/"},{value:(diagnostics.capacity)},{text:" · calcolo "},{value:(Number(diagnostics.processing_ms||0).toFixed(1))},{text:" ms · batch persi "},{value:(diagnostics.dropped_batches)},{text:" · campioni tardivi "},{value:(diagnostics.late_samples)},{value:(missing?I18N.join([{text:" · "},{value:(missing)},{text:" formule con sorgenti non selezionate"}]):'')},{value:(diagnostics.error?' · '+diagnostics.error:'')},{value:(calculationRegistry.error?' · '+calculationRegistry.error:'')}])),true);
  const map=new Map(values.map(v=>[v.nodeid,v]));for(const row of C('calc-list').querySelectorAll('[data-calculation]')){const v=map.get(row.dataset.calculation);I18N.bind(row,"textContent",()=>(v?' · '+(v.good?valueText(v.value):v.status):''),false);}
  calcControls();
};
C('nav-calculations').onclick=()=>showAppPage('calculations');C('calc-new').onclick=()=>newCalculation();C('calc-cancel').onclick=()=>newCalculation();C('calc-list-search').oninput=renderCalculationList;
C('calc-source-search').oninput=()=>{refreshCalculationSources();};C('calc-context').onchange=()=>calcRun(()=>refreshCalculationContext());C('calc-recording').onchange=()=>calcRun(calculationRecordingChanged);
C('calc-expression').oninput=()=>{calculationEditSequence++;I18N.bind(C('calc-validation'),"textContent",()=>(I18N.t("Formula modificata: validare o testare prima del salvataggio.")),true);syntaxCalculation();};C('calc-expression').onclick=autocompleteCalculation;
for(const input of C('calc-form').querySelectorAll('input,select'))input.addEventListener('change',()=>calculationEditSequence++);
for(const button of C('calc-operators').querySelectorAll('[data-insert]'))button.onclick=()=>insertCalculationText(button.dataset.insert);
C('calc-validate').onclick=()=>calcRun(validateCalculation);C('calc-test').onclick=()=>calcRun(testCalculation);C('calc-form').onsubmit=e=>{e.preventDefault();calcRun(saveCalculation);};
C('calc-add-live').onclick=()=>calcRun(addCalculationLive);C('calc-add-recorded').onclick=()=>calcRun(applyCalculationRecorded);C('calc-materialize').onclick=()=>calcRun(materializeCalculation);
newCalculation();
