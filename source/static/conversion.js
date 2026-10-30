/* Conversione: una registrazione (CSV, Parquet, FDA DAS4, RBF, DAT) o un archivio primario (.sac) in CSV, Parquet, FDA DAS4 o RBF. Il servizio converte in background. */
const CONV={files:[],file:null,chosen:new Set(),job:null,timer:null,directory:'',range:{from:'',to:''},loaded:false};
const CONV_LIMIT={das:64,rbf:64};
const CONV_NOTES={
  csv:"Testo leggibile anche con Excel, file più grande: tutti i campioni e i valori originali.",
  parquet:"Compresso e senza perdita: tutti i campioni e i valori originali.",
  das:"FDA DAS4: valori Int16 scalati (errore di arrotondamento nei metadati), tempi al millisecondo, massimo 64 segnali.",
  rbf:"RBF v3: valori Float32, massimo 64 segnali; ogni file contiene al massimo 57.600 istanti, le registrazioni più lunghe vengono divise in più parti."};
const convEl=id=>document.getElementById(id);
const convKey=f=>(f.source||'repository')+'|'+f.name;
const convRunning=()=>CONV.job?.state==='running';
function convSignals(){return CONV.file?.signals||[];}
function convError(text){I18N.bind(convEl('conv-error'),'textContent',()=>(text||''),true);convEl('conv-error').hidden=!text;}
function convFormatLabel(f){return f.source==='primary'?I18N.t(f.format_label):(f.format_label||String(f.format||'').toUpperCase());}
async function refreshConversionFiles(){
  const result=await request('/api/recordings');CONV.directory=result.directory||'';
  const primary=await request('/api/convert/primary').catch(()=>({files:[]}));
  CONV.files=[...(result.files||[]).map(f=>({source:'repository',...f})),...(primary.files||[])];CONV.loaded=true;
  if(CONV.file){const current=CONV.files.find(f=>convKey(f)===convKey(CONV.file));if(current)CONV.file=current;}
  renderConversionFiles();renderConversion();
}
function renderConversionFiles(){
  const box=convEl('conv-file-list'),query=convEl('conv-search').value.trim().toLowerCase();box.replaceChildren();
  const files=CONV.files.filter(f=>!query||[f.display_name,f.name,f.format,f.format_label].some(v=>String(v||'').toLowerCase().includes(query)));
  for(const f of files){
    const item=document.createElement('button');item.type='button';item.className='conversion-file'+(CONV.file&&convKey(CONV.file)===convKey(f)?' active':'');
    item.setAttribute('role','option');item.setAttribute('aria-selected',String(CONV.file&&convKey(CONV.file)===convKey(f)));item.disabled=convRunning()||applicationClosed;
    const name=document.createElement('strong');name.textContent=f.display_name||f.name;
    const badge=document.createElement('span');badge.className='conversion-badge';badge.textContent=String(f.format||'').toUpperCase();
    const meta=document.createElement('span');meta.className='small';
    meta.textContent=f.read_error?f.read_error:f.source==='primary'?[I18N.t(f.format_label),historyBytes(f.size_bytes),(f.signals||[]).length+' '+I18N.t('segnali'),historyFormatStamp(f.started_at)].join(' · ')
      :[f.source==='local'?I18N.t('Dal PC'):f.name,historyBytes(f.size_bytes),(f.signals||[]).length+' '+I18N.t('segnali'),historyDuration(f.duration_seconds)].join(' · ');
    item.title=f.name;item.append(badge,name,meta);item.onclick=()=>selectConversionFile(f);box.append(item);
  }
  I18N.bind(convEl('conv-list-status'),'textContent',()=>(CONV.files.length?(files.length?'':I18N.t('Nessun file corrisponde al filtro')):I18N.t('Nessuna registrazione nella cartella')),true);
}
function selectConversionFile(f){
  if(convRunning())return;
  CONV.file=f;CONV.chosen=new Set(convSignals().map(s=>s.nodeid));CONV.job=null;convError('');
  CONV.range={from:Number.isFinite(f.start_ms)?localInputFromMs(Math.floor(f.start_ms)):localInputFromIso(f.started_at),to:Number.isFinite(f.end_ms)?localInputFromMs(Math.ceil(f.end_ms)):localInputFromIso(f.ended_at)};
  convEl('conv-from').value=CONV.range.from;convEl('conv-to').value=CONV.range.to;convEl('conv-signal-search').value='';
  convEl('conv-format').value=f.format==='parquet'?'csv':'parquet';
  convEl('conv-result').hidden=true;convEl('conv-progress-box').hidden=true;
  renderConversionFiles();renderConversion();
}
function convInfo(label,value){const item=document.createElement('div'),dt=document.createElement('dt'),dd=document.createElement('dd');dt.textContent=I18N.t(label);dd.textContent=value;item.append(dt,dd);return [item];}
function renderConversion(){
  const f=CONV.file,running=convRunning();convEl('conv-form').hidden=!f;
  convEl('conv-title').textContent=f?(f.display_name||f.name):I18N.t('Nessun file selezionato');
  const info=convEl('conv-info');info.replaceChildren();
  if(f)info.append(...convInfo('Formato',convFormatLabel(f)),...convInfo('Dimensione',historyBytes(f.size_bytes)),...convInfo('Segnali',String(convSignals().length)),
    ...convInfo('Inizio',historyFormatStamp(f.started_at)),...(f.source==='primary'?[]:[...convInfo('Durata',historyDuration(f.duration_seconds)),...convInfo('Campioni',f.rows_written==null?'—':Number(f.rows_written).toLocaleString(I18N.locale()))]));
  if(!f)return;
  const format=convEl('conv-format').value,limit=CONV_LIMIT[format],count=CONV.chosen.size,total=convSignals().length;
  convEl('conv-split-box').hidden=f.source!=='primary';convEl('conv-split').disabled=running||applicationClosed;
  convEl('conv-format-note').textContent=I18N.t(CONV_NOTES[format])+(format===f.format?' '+I18N.t('Stesso formato del file di origine: viene creata una copia con la selezione indicata.'):'')+(f.source==='primary'?' '+I18N.t('Archivio primario: contiene tutti i campioni originali della registrazione; se è interrotto o danneggiato si esporta fino all’ultimo blocco valido.'):'');
  const query=convEl('conv-signal-search').value.trim().toLowerCase(),list=convEl('conv-signal-list');list.replaceChildren();
  for(const s of convSignals()){
    if(query&&![s.display_name,s.nodeid].some(v=>String(v||'').toLowerCase().includes(query)))continue;
    const label=document.createElement('label');label.className='checkline';label.title=s.nodeid;
    const box=document.createElement('input');box.type='checkbox';box.checked=CONV.chosen.has(s.nodeid);box.disabled=running||applicationClosed;
    box.onchange=()=>{if(box.checked)CONV.chosen.add(s.nodeid);else CONV.chosen.delete(s.nodeid);renderConversion();};
    const name=document.createElement('span');name.textContent=(s.display_name||s.nodeid)+(s.unit?' ['+s.unit+']':'');label.append(box,name);list.append(label);
  }
  const all=convEl('conv-all');all.checked=count===total&&total>0;all.indeterminate=count>0&&count<total;all.disabled=running||applicationClosed;
  convEl('conv-signal-count').textContent=count+' / '+total+(limit?' · max '+limit:'');
  const tooMany=limit&&count>limit;convEl('conv-signal-count').classList.toggle('warning',!!tooMany);
  const separator=CONV.directory.includes('\\')?'\\':'/';
  convEl('conv-destination').textContent=I18N.t('Destinazione:')+' '+(CONV.directory?CONV.directory.replace(/[\\/]+$/,'')+separator+'Conversioni':'Conversioni');
  for(const id of ['conv-format','conv-signal-search','conv-from','conv-to','conv-full-range'])convEl(id).disabled=running||applicationClosed;
  convEl('conv-start').disabled=running||applicationClosed||!count||!!tooMany||!!f.read_error;convEl('conv-start').title=tooMany?I18N.t('Selezionare al massimo 64 segnali per FDA DAS4 e RBF'):'';
  convEl('conv-cancel').hidden=!running;convEl('conv-open-local').disabled=running||applicationClosed;convEl('conv-open-primary').disabled=running||applicationClosed;
  renderConversionJob();
}
function renderConversionJob(){
  const job=CONV.job,box=convEl('conv-progress-box'),bar=convEl('conv-progress');box.hidden=!job;if(!job)return;
  const rows=Number(job.rows_read||0),total=Number(job.rows_total||0),loc=I18N.locale();
  const percent=job.state==='ready'?100:Number.isFinite(Number(job.percent))&&job.percent!==null?Math.min(100,Math.max(0,Number(job.percent))):null;
  if(job.state==='running'&&percent!==null)bar.value=percent/100;else if(job.state==='running')bar.removeAttribute('value');else bar.value=job.state==='ready'?1:0;
  const n=rows.toLocaleString(loc),pct=percent!==null&&job.state==='running'?Math.floor(percent)+'% · ':'';
  const count=total?' '+n+' / '+(job.rows_total_estimated?'~':'')+total.toLocaleString(loc):' '+n;
  const phase=job.phase==='write'?I18N.t('Scrittura del file')+' '+job.format_label+'…':job.phase==='stage'?I18N.t('Preparazione dei dati…'):I18N.t('Lettura dei campioni…')+count+' '+I18N.t('campioni letti');
  const text=job.state==='ready'?I18N.t('Conversione completata')+': '+n+' '+I18N.t('campioni'):job.state==='error'||job.state==='cancelled'?I18N.t(job.error||'Conversione non riuscita'):pct+phase;
  convEl('conv-status').textContent=text+(job.state==='ready'&&job.warning?' — '+job.warning:'');convEl('conv-status').classList.toggle('error',job.state==='error');
  const result=convEl('conv-result'),list=convEl('conv-result-list');list.replaceChildren();result.hidden=job.state!=='ready';
  for(const file of job.files||[]){
    const li=document.createElement('li'),name=document.createElement('span');name.className='break';
    name.textContent=file.file+' · '+historyBytes(file.bytes)+' · '+Number(file.rows||0).toLocaleString(I18N.locale())+' '+I18N.t('campioni');name.title=file.path;
    const open=document.createElement('button');open.type='button';open.className='ghost';open.textContent=I18N.t('Apri in Registrazioni');open.onclick=()=>openConvertedFile(file).catch(e=>notice(e.message,true));
    li.append(name,open);list.append(li);
  }
}
async function startConversion(){
  const f=CONV.file;if(!f||convRunning())return;convError('');
  const body={source:f.source||'repository',name:f.name,format:convEl('conv-format').value};
  if(f.source==='primary')body.split_minutes=Number(convEl('conv-split').value);
  if(CONV.chosen.size!==convSignals().length)body.signal_ids=[...CONV.chosen];
  const from=convEl('conv-from').value,to=convEl('conv-to').value;
  if(from&&from!==CONV.range.from)body.start_ms=msFromLocalInput(from);
  if(to&&to!==CONV.range.to)body.end_ms=msFromLocalInput(to);
  try{CONV.job=(await request('/api/convert',body)).job;renderConversion();renderConversionFiles();pollConversion();}
  catch(e){convError(e.message);}
}
async function pollConversion(){
  clearTimeout(CONV.timer);if(!convRunning())return;
  CONV.timer=setTimeout(async()=>{
    try{CONV.job=(await request('/api/convert/'+encodeURIComponent(CONV.job.id))).job;}
    catch(e){CONV.job={...CONV.job,state:'error',error:e.message};}
    renderConversion();
    if(convRunning())pollConversion();else{renderConversionFiles();if(CONV.job.state==='ready')refreshConversionFiles().catch(()=>{});}
  },400);
}
async function cancelConversion(){if(!convRunning())return;try{CONV.job=(await request('/api/convert/'+encodeURIComponent(CONV.job.id)+'/cancel',{})).job;renderConversion();}catch(e){convError(e.message);}}
async function openConvertedFile(file){
  showAppPage('recordings');await refreshHistoryFiles();
  const target=historyFiles.find(f=>(f.source||'repository')==='repository'&&f.name===file.name);
  if(!target)throw new Error(I18N.t('File convertito non trovato nella cartella delle registrazioni'));
  selectHistoryFile(target);await loadHistory();
}
async function importConversionFiles(list){
  const files=[...list||[]];if(!files.length)return;
  if(files.some(f=>/\.sac(\.part)?$/i.test(String(f.name||''))))throw new Error(I18N.t("Gli archivi primari (.sac) non si caricano dal browser: usa Strumenti → Conversione e «Apri archivio primario…»."));
  if(files.some(f=>!['.csv','.parquet','.das','.rbf','.svdx','.dat','.json'].some(ext=>String(f.name||'').toLowerCase().endsWith(ext))))throw new Error(I18N.t("Seleziona CSV, Parquet, FDA DAS4, RBF, Scope .svdx, DAT PDA3 e relativi JSON."));
  const form=new FormData();for(const f of files)form.append('files',f,f.name);
  form.append('pda3_timezone_offset_minutes',document.getElementById('history-dat-offset')?.value||'120');form.append('join_pda3',String(!!document.getElementById('history-dat-join')?.checked));
  const bar=convEl('conv-import-progress'),status=convEl('conv-list-status');
  const total=files.reduce((n,f)=>n+(f.size||0),0);bar.hidden=false;bar.removeAttribute('value');status.textContent=I18N.t('Caricamento del file…');
  let data;
  try{
    data=await uploadForm('/api/recordings/import',form,(sent,all)=>{
      if(sent>=all){bar.removeAttribute('value');status.textContent=I18N.t('Lettura del file caricato…');return;}
      bar.value=sent/all;status.textContent=I18N.t('Caricamento del file…')+' '+uploadPercentText(sent,all);
    },total);
  }finally{bar.hidden=true;status.textContent='';}
  await refreshConversionFiles();
  const first=CONV.files.find(f=>f.source==='local'&&f.name===data.recording?.name);if(first)selectConversionFile(first);
  const messages=[...(data.warnings||[]),...(data.errors||[])];if(messages.length)notice(messages.join(' · '),true);
}
window.openConversionPage=()=>refreshConversionFiles().catch(e=>notice(e.message,true));
convEl('nav-conversion').onclick=()=>showAppPage('conversion');
convEl('conv-refresh').onclick=()=>refreshConversionFiles().catch(e=>notice(e.message,true));
convEl('conv-search').oninput=renderConversionFiles;
convEl('conv-signal-search').oninput=renderConversion;
convEl('conv-format').onchange=renderConversion;
convEl('conv-all').onchange=()=>{CONV.chosen=convEl('conv-all').checked?new Set(convSignals().map(s=>s.nodeid)):new Set();renderConversion();};
convEl('conv-full-range').onclick=()=>{convEl('conv-from').value=CONV.range.from;convEl('conv-to').value=CONV.range.to;};
convEl('conv-start').onclick=startConversion;
convEl('conv-cancel').onclick=cancelConversion;
convEl('conv-open-local').onclick=()=>convEl('conv-local-file').click();
convEl('conv-open-primary').onclick=async()=>{
  try{
    convError('');const picked=await request('/api/convert/primary/browse',{});if(!picked.path)return;
    const added=(await request('/api/convert/primary/add',{path:picked.path})).file;
    await refreshConversionFiles();const item=CONV.files.find(f=>f.source==='primary'&&f.name===added.name);if(item)selectConversionFile(item);
  }catch(e){convError(e.message);notice(e.message,true);}
};
convEl('conv-local-file').onchange=async()=>{const input=convEl('conv-local-file');try{await importConversionFiles(input.files);}catch(e){convError(e.message);notice(e.message,true);}finally{input.value='';}};
document.addEventListener('space-analyzer-languagechange',()=>{if(CONV.loaded){renderConversionFiles();renderConversion();}});
