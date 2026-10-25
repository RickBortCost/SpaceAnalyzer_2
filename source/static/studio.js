/* V1.3.0 UI only. Acquisition, original values and recording remain server-owned. */
/* Keep the actual controls (and their listeners/disabled state) when overflowing. */
class ChartActionToolbar {
  constructor(graph,workspace){
    this.graph=graph;this.workspace=workspace;this.frame=0;this.destroyed=false;
    this.heading=graph.element.querySelector('.chart-heading');
    this.controls=this.heading.querySelector('.toolbar');
    this.menu=this.controls.querySelector('.chart-menu');this.items=this.menu.querySelector('.chart-menu-items');
    this.rail=document.createElement('div');this.rail.className='chart-action-tools';this.rail.setAttribute('role','toolbar');I18N.attr(this.rail,'aria-label',()=>(I18N.t("Strumenti del grafico")));
    this.heading.insertBefore(this.rail,this.controls);this.heading.classList.add('has-chart-tools');
    this.measure=document.createElement('div');this.measure.className='chart-tool-measure';this.measure.setAttribute('aria-hidden','true');this.measure.inert=true;this.heading.append(this.measure);
    const definitions=[['axes',I18N.t("Assi Y1/Y2")],['signals',I18N.t("Segnali")],['analysis',I18N.t("FFT / Export")],['resetZoom',I18N.t("Ripristina zoom")],['sync',I18N.t("Sincronizza")],['image','PNG'],['rename',I18N.t("Rinomina")],['duplicate',I18N.t("Duplica")],['closeFile',I18N.t("Chiudi file")],['clear',I18N.t("Pulisci")],['remove',I18N.t("Rimuovi")]];
    this.actions=[];
    for(const [key,label] of definitions){
      const button=this.items.querySelector('[data-studio-action="'+key+'"],[data-chart-role="'+key+'"]');if(!button)continue;
      const full=button.textContent;button.classList.add('chart-tool-button');I18N.bind(button,"title",()=>(full),true);I18N.attr(button,'aria-label',()=>(full));
      button.addEventListener('click',()=>{this.menu.open=false;});
      const measure=button.cloneNode(true);for(const attr of ['id','data-studio-action','data-chart-role','aria-label'])measure.removeAttribute(attr);measure.tabIndex=-1;I18N.bind(measure,"textContent",()=>(label),true);this.measure.append(measure);
      this.actions.push({key,button,label,full,measure});
    }
    this.onResize=()=>this.schedule();window.addEventListener('resize',this.onResize);
    if(window.ResizeObserver){this.observer=new ResizeObserver(this.onResize);this.observer.observe(this.heading);}
    document.fonts?.ready.then(()=>this.schedule());this.schedule();
  }
  schedule(){if(this.destroyed||this.frame)return;this.frame=requestAnimationFrame(()=>{this.frame=0;this.layout();});}
  layout(){
    if(this.destroyed||!this.heading.isConnected||this.rail.clientWidth<1)return;
    const gap=parseFloat(getComputedStyle(this.rail).columnGap)||4;
    const menuWidth=this.menu.hidden?this.lastMenuWidth||34:this.menu.getBoundingClientRect().width||34;
    this.lastMenuWidth=menuWidth;
    const controlGap=parseFloat(getComputedStyle(this.controls).columnGap)||5;
    const room=this.rail.clientWidth+(this.menu.hidden?0:menuWidth+controlGap);
    const widths=this.actions.map(a=>Math.ceil(a.measure.getBoundingClientRect().width));
    if(widths.some(w=>w<=0))return;
    const total=widths.reduce((a,b)=>a+b,0)+gap*Math.max(0,widths.length-1);
    const overflow=total>room-1,available=room-(overflow?menuWidth+controlGap:0);
    let used=0,count=0;
    for(const width of widths){const next=used+(count?gap:0)+width;if(next>available-1)break;used=next;count++;}
    const focused=document.activeElement;
    const shown=this.actions.slice(0,count),hidden=this.actions.slice(count);
    const same=(parent,entries)=>parent.children.length===entries.length&&entries.every((a,i)=>parent.children[i]===a.button);
    for(const a of shown)if(a.button.textContent!==a.label)I18N.bind(a.button,"textContent",()=>(a.label),true);
    for(const a of hidden)if(a.button.textContent!==a.full)I18N.bind(a.button,"textContent",()=>(a.full),true);
    if(!same(this.rail,shown))this.rail.replaceChildren(...shown.map(a=>a.button));
    if(!same(this.items,hidden))this.items.replaceChildren(...hidden.map(a=>a.button));
    this.menu.hidden=!hidden.length;if(this.menu.hidden)this.menu.open=false;
    if(focused&&this.actions.some(a=>a.button===focused)&&focused!==document.activeElement){
      if(this.items.contains(focused)&&!this.menu.open)this.menu.querySelector('summary').focus();else focused.focus();
    }
    this.syncState();
  }
  syncState(){const sync=this.actions.find(a=>a.key==='sync');sync?.button.setAttribute('aria-pressed',String(this.workspace.syncZoom));}
  destroy(){this.destroyed=true;if(this.frame)cancelAnimationFrame(this.frame);this.observer?.disconnect();window.removeEventListener('resize',this.onResize);}
}
let studioThemeMode='light',studioFullscreen=null,propertiesTarget=null,studioToolbarFrame=0;
document.addEventListener('space-analyzer-languagechange',()=>{
  for(const ws of [liveCharts,historyCharts])for(const g of ws.charts){
    const bar=g.actionToolbar;
    if(bar){for(const action of bar.actions){action.label=I18N.t(action.label);action.full=I18N.t(action.full);I18N.bind(action.measure,'textContent',()=>action.label,true);}bar.schedule();}
    g.navigatorCache=null;g.trend.draw();
  }
  if(typeof drawFFT==='function')drawFFT();scheduleStudioToolbar();
});
const studioMedia=window.matchMedia?.('(prefers-color-scheme: dark)');
function activeWorkspace(){return (window.currentAppPage==='calculations'?window.calculationContextMode==='recorded':$('live-page').hidden)?historyCharts:liveCharts;}
function applyStudioTheme(mode,persist=true){
  studioThemeMode=['light','dark','system'].includes(mode)?mode:'light';
  const dark=studioThemeMode==='dark'||studioThemeMode==='system'&&!!studioMedia?.matches;
  document.documentElement.dataset.theme=dark?'dark':'light';$('theme-mode').value=studioThemeMode;
  window.studioTheme=dark?{text:'#CCC3BE',grid:'#404040',surface:'#1A1A1A'}:{text:'#707E86',grid:'#E5E5E5',surface:'#FFFFFF'};
  if(persist){try{localStorage.setItem('opcua.studio.theme',studioThemeMode);localStorage.setItem('space-analyzer.palette.version','standard-v1');}catch{}scheduleWorkspaceSave();}
  for(const ws of [liveCharts,historyCharts]){for(const g of ws.charts)g.navigatorCache=null;ws.draw();}drawFFT();
}
$('theme-mode').onchange=e=>applyStudioTheme(e.target.value);
studioMedia?.addEventListener?.('change',()=>{if(studioThemeMode==='system')applyStudioTheme('system',false);});
function updateStudioToolbar(){
  const ws=activeWorkspace(),g=ws.active,t=g?.trend;if(!t)return;
  $('global-pause').disabled=ws.staticMode||applicationClosed;I18N.bind($('global-pause'),"textContent",()=>(t.frozen?I18N.t("▶ Play"):I18N.t("Ⅱ Pausa")),true);$('global-pause').setAttribute('aria-pressed',String(t.frozen));
  $('global-live-zoom').disabled=ws.staticMode||applicationClosed;$('global-live-zoom').setAttribute('aria-pressed',String(liveCharts.liveZoom));
  $('global-reset').disabled=!(t.zoom||t.linkedRange)||applicationClosed;
  for(const k of ['x1','x2']){$('global-'+k).disabled=!t.domain||applicationClosed;$('global-'+k).setAttribute('aria-pressed',String(t.markerMode===k));}
  I18N.bind($('toolbar-endpoint'),"textContent",()=>((window.sourceEndpoint?.()||$('endpoint').value)||I18N.t("Nessun server")),true);I18N.bind($('toolbar-endpoint'),"title",()=>((window.sourceEndpoint?.()||$('endpoint').value)||I18N.t("Fonte dati")),true);
  I18N.bind($('active-graph-label'),"textContent",()=>(I18N.join([{value:(ws.staticMode?I18N.t("Registrazioni"):I18N.t("Live"))},{text:" · "},{value:(g.name)}])),true);
  document.body.classList.toggle('is-recording',!!recordingLocked());
  I18N.bind($('backend-state'),"textContent",()=>(applicationClosed?I18N.t("Servizio arrestato"):recordingLocked()?I18N.join([{text:"● Registrazione attiva · "},{value:((recordState.duration_seconds||0).toFixed(1))},{text:" s"}]):connected?I18N.join([{text:"● Servizio online · "},{value:(window.sourceLabel?.()||'OPC UA')},{text:" connesso"}]):I18N.join([{text:"Servizio locale · "},{value:(window.sourceLabel?.()||'OPC UA')},{text:" "},{value:(state==='reconnecting'?I18N.t("in riconnessione"):I18N.t("non connesso"))}])),true);
  $('status').classList.toggle('pending',['connecting','reconnecting'].includes(state));
}
function scheduleStudioToolbar(){if(studioToolbarFrame)return;studioToolbarFrame=requestAnimationFrame(()=>{studioToolbarFrame=0;updateStudioToolbar();});}
$('global-pause').onclick=()=>{activeWorkspace().active?.trend.togglePause();scheduleWorkspaceSave();};
$('global-live-zoom').onclick=()=>{liveCharts.setLiveZoom(!liveCharts.liveZoom);scheduleStudioToolbar();};
$('global-reset').onclick=()=>activeWorkspace().active?.trend.resetZoom();
for(const key of ['x1','x2'])$('global-'+key).onclick=()=>{activeWorkspace().active?.trend.armMarker(key);scheduleStudioToolbar();};
for(const [name,fn] of [['setState',setState],['renderRecording',renderRecording]]){
  window[name]=function(...args){const result=fn(...args);scheduleStudioToolbar();return result;};
}
function toggleStudioFullscreen(graph){
  const previous=studioFullscreen;
  if(previous){previous.element.classList.remove('studio-fullscreen','expanded-chart');previous.element.querySelector('[data-chart-role=expand]').setAttribute('aria-pressed','false');}
  studioFullscreen=previous===graph?null:graph;
  if(studioFullscreen){studioFullscreen.element.classList.add('studio-fullscreen');studioFullscreen.element.querySelector('[data-chart-role=expand]').setAttribute('aria-pressed','true');}
  document.body.classList.toggle('analysis-fullscreen',!!studioFullscreen);
  requestAnimationFrame(()=>{liveCharts.draw();historyCharts.draw();});scheduleWorkspaceSave();
}
window.toggleStudioFullscreen=toggleStudioFullscreen;
document.addEventListener('keydown',e=>{if(e.key==='Escape'&&!document.querySelector('dialog[open]')&&$('record-modal').hidden&&studioFullscreen){e.preventDefault();toggleStudioFullscreen(studioFullscreen);}});
document.addEventListener('click',e=>{for(const menu of document.querySelectorAll('.chart-menu[open],.sync-menu[open],.app-menu[open]'))if(!menu.contains(e.target))menu.open=false;});
function createResizer(target,min,max,onChange,className='panel-resizer'){
  const handle=document.createElement('div');handle.className=className;handle.role='separator';handle.tabIndex=0;I18N.attr(handle,'aria-label',()=>(I18N.t("Ridimensiona altezza. Doppio clic per ripristinare")));handle.setAttribute('aria-orientation','horizontal');
  const height=()=>target.getBoundingClientRect().height||parseFloat(target.style.height)||240;
  const set=value=>{const v=Math.max(min,Math.min(typeof max==='function'?max():max,value));target.style.height=v+'px';handle.setAttribute('aria-valuenow',Math.round(v));onChange?.();};
  handle.setAttribute('aria-valuemin',min);handle.setAttribute('aria-valuemax',typeof max==='function'?max():max);
  handle.addEventListener('pointerdown',e=>{if(e.button!==0)return;e.preventDefault();const start=e.clientY,h=height();handle.setPointerCapture?.(e.pointerId);const move=ev=>set(h+ev.clientY-start);const end=()=>{window.removeEventListener('pointermove',move);window.removeEventListener('pointerup',end);window.removeEventListener('pointercancel',end);scheduleWorkspaceSave();};window.addEventListener('pointermove',move);window.addEventListener('pointerup',end);window.addEventListener('pointercancel',end);});
  handle.ondblclick=()=>{target.style.height='';onChange?.();scheduleWorkspaceSave();};
  handle.onkeydown=e=>{if(e.key==='ArrowDown'||e.key==='ArrowUp'){e.preventDefault();set(height()+(e.key==='ArrowDown'?24:-24));scheduleWorkspaceSave();}else if(e.key==='Home'){target.style.height='';onChange?.();scheduleWorkspaceSave();}};
  return handle;
}
function applyGridRatio(ws,ratio){ratio=Math.max(.25,Math.min(.75,Number(ratio)||.5));ws.gridRatio=ratio;ws.root.querySelector('.grid-resizer')?.setAttribute('aria-valuenow',String(Math.round(ratio*100)));ws.root.style.setProperty('--grid-ratio',(ratio/(1-ratio))+'fr');ws.root.style.setProperty('--grid-divider',(ratio*100)+'%');ws.draw();}
function refreshPresetVisibility(ws){
  const count={single:1,vertical:2,horizontal:2,quad:4}[ws.layoutSelect.value];
  let shown=count?ws.charts.slice(0,count):ws.charts;
  if(count&&ws.active&&!shown.includes(ws.active))shown=[...shown.slice(0,count-1),ws.active];
  for(const g of ws.charts)g.element.hidden=!shown.includes(g);
}
function setupGridResizer(ws){
  const handle=document.createElement('div');handle.className='grid-resizer';handle.role='separator';handle.tabIndex=0;handle.setAttribute('aria-valuemin','25');handle.setAttribute('aria-valuemax','75');handle.setAttribute('aria-valuenow','50');I18N.attr(handle,'aria-label',()=>(I18N.t("Ridimensiona colonne grafici")));handle.setAttribute('aria-orientation','vertical');ws.root.append(handle);
  handle.onpointerdown=e=>{if(e.button!==0)return;e.preventDefault();handle.setPointerCapture?.(e.pointerId);const move=ev=>{const r=ws.root.getBoundingClientRect();if(r.width)applyGridRatio(ws,(ev.clientX-r.left)/r.width);};const end=()=>{window.removeEventListener('pointermove',move);window.removeEventListener('pointerup',end);window.removeEventListener('pointercancel',end);scheduleWorkspaceSave();};window.addEventListener('pointermove',move);window.addEventListener('pointerup',end);window.addEventListener('pointercancel',end);};
  handle.ondblclick=()=>{applyGridRatio(ws,.5);scheduleWorkspaceSave();};handle.onkeydown=e=>{if(['ArrowLeft','ArrowRight','Home'].includes(e.key)){e.preventDefault();applyGridRatio(ws,e.key==='Home'?.5:(ws.gridRatio||.5)+(e.key==='ArrowLeft'?-.05:.05));scheduleWorkspaceSave();}};
}
function setNavigatorRange(g,lo,hi){
  const t=g.trend,f=t.fullDomain;if(!f||!Number.isFinite(lo)||!Number.isFinite(hi))return;
  const min=Math.max(1,(f.xmax-f.xmin)/10000);lo=Math.max(f.xmin,Math.min(f.xmax-min,lo));hi=Math.max(lo+min,Math.min(f.xmax,hi));
  t.pauseForZoom();
  t.zoom={xmin:lo,xmax:hi,axes:t.zoom?.axes||f.axes};t.linkedRange=null;t.draw();t.onViewChanged?.({xmin:lo,xmax:hi});
}
function setupNavigator(g){
  const nav=g.element.querySelector('.navigator'),win=nav.querySelector('.navigator-window');let drag=null;
  const ratio=x=>{const r=nav.getBoundingClientRect();return Math.max(0,Math.min(1,(x-r.left)/(r.width||1)));};
  nav.addEventListener('pointerdown',e=>{const t=g.trend,f=t.fullDomain;if(!f||e.button!==0)return;e.preventDefault();const view=t.linkedRange||t.zoom||f,r=ratio(e.clientX),span=f.xmax-f.xmin;
    const edge=e.target.dataset.edge;if(edge)drag={mode:edge,start:r,lo:view.xmin,hi:view.xmax,full:{...f}};
    else if(e.target===win)drag={mode:'move',start:r,lo:view.xmin,hi:view.xmax,full:{...f}};
    else {const width=view.xmax-view.xmin<.99*span?view.xmax-view.xmin:span*.3;const lo=Math.max(f.xmin,Math.min(f.xmax-width,f.xmin+r*span-width/2));setNavigatorRange(g,lo,lo+width);drag={mode:'move',start:r,lo,hi:lo+width,full:{...f}};}
    nav.setPointerCapture?.(e.pointerId);
  });
  nav.addEventListener('pointermove',e=>{if(!drag)return;const f=drag.full,span=f.xmax-f.xmin,delta=(ratio(e.clientX)-drag.start)*span;let lo=drag.lo,hi=drag.hi;
    if(drag.mode==='start')lo=Math.min(hi-Math.max(1,span/10000),lo+delta);else if(drag.mode==='end')hi=Math.max(lo+Math.max(1,span/10000),hi+delta);else{const width=hi-lo;lo=Math.max(f.xmin,Math.min(f.xmax-width,lo+delta));hi=lo+width;}setNavigatorRange(g,lo,hi);
  });
  for(const event of ['pointerup','pointercancel'])nav.addEventListener(event,e=>{drag=null;try{nav.releasePointerCapture?.(e.pointerId);}catch{}scheduleWorkspaceSave();});
  nav.addEventListener('keydown',e=>{if(!['ArrowLeft','ArrowRight','Home'].includes(e.key))return;e.preventDefault();const t=g.trend,f=t.fullDomain;if(!f)return;if(e.key==='Home'){t.resetZoom();return;}const v=t.linkedRange||t.zoom||f,delta=(f.xmax-f.xmin)*.02*(e.key==='ArrowLeft'?-1:1),edge=e.target.dataset.edge;
    if(edge==='start')setNavigatorRange(g,v.xmin+delta,v.xmax);else if(edge==='end')setNavigatorRange(g,v.xmin,v.xmax+delta);else{const span=v.xmax-v.xmin,lo=Math.max(f.xmin,Math.min(f.xmax-span,v.xmin+delta));setNavigatorRange(g,lo,lo+span);}
  });
}
function drawNavigator(g){
  const t=g.trend,nav=g.element.querySelector('.navigator'),c=nav?.querySelector('canvas'),win=nav?.querySelector('.navigator-window'),f=t.fullDomain;if(!c)return;
  nav.classList.toggle('empty',!f);win.hidden=!f;if(!f){const ctx=c.getContext('2d');ctx?.clearRect(0,0,c.width,c.height);return;}
  const v=t.linkedRange||t.zoom||f,span=f.xmax-f.xmin;
  win.style.left=(Math.max(0,Math.min(1,(v.xmin-f.xmin)/span))*100)+'%';win.style.right=(100-Math.max(0,Math.min(1,(v.xmax-f.xmin)/span))*100)+'%';
  win.setAttribute('aria-valuemin',f.xmin);win.setAttribute('aria-valuemax',f.xmax);win.setAttribute('aria-valuenow',v.xmin);win.setAttribute('aria-valuetext',t.formatMarkerTime(v.xmin)+' – '+t.formatMarkerTime(v.xmax));
  const r=c.getBoundingClientRect(),w=r.width,h=r.height;if(w<1||h<1)return;
  const key=[w,h,document.documentElement.dataset.theme,f.xmin,f.xmax,...Object.entries(t.series).map(([id,a])=>id+':'+a.length+':'+a.at(-1)?.[1]+':'+t.color(id))].join('|');if(g.navigatorCache===key)return;g.navigatorCache=key;
  const dpr=window.devicePixelRatio||1;c.width=Math.round(w*dpr);c.height=Math.round(h*dpr);const ctx=c.getContext('2d');ctx.scale(dpr,dpr);ctx.lineWidth=1;
  // Overview: live data with a bounded sample budget; file data (SampleSeries) reads every sample for each column's min/max.
  for(const [id,points] of Object.entries(t.series)){if(!points.length)continue;const axis=t.axisPlan.axisById.get(id)||'left',yb=f.axes[axis]||f.axes.left;if(!yb)continue;const ymin=yb.ymin,yspan=yb.ymax-ymin;ctx.strokeStyle=t.color(id);ctx.beginPath();const buckets=new Map();
    if(isSampleSeries(points)){const cols=Math.max(1,Math.floor(w)),lo=new Float64Array(cols).fill(Infinity),hi=new Float64Array(cols).fill(-Infinity),tt=points.t,vv=points.v;for(let i=0;i<tt.length;i++){const value=vv[i];if(!Number.isFinite(value))continue;const x=Math.floor((tt[i]-f.xmin)/span*(w-1));if(x<0||x>=cols)continue;if(value<lo[x])lo[x]=value;if(value>hi[x])hi[x]=value;}for(let x=0;x<cols;x++)if(lo[x]<=hi[x])buckets.set(x,[lo[x],hi[x]]);}
    else for(let i=0,step=Math.max(1,Math.floor(points.length/Math.max(256,w*4)));i<points.length;i+=step){const time=points[i][0],value=points[i][1];if(!Number.isFinite(value))continue;const x=Math.floor((time-f.xmin)/span*(w-1));const old=buckets.get(x);if(old){old[0]=Math.min(old[0],value);old[1]=Math.max(old[1],value);}else buckets.set(x,[value,value]);}
    let first=true;for(const [x,[lo,hi]] of buckets){const y=h-3-(lo-ymin)/yspan*(h-6),yy=h-3-(hi-ymin)/yspan*(h-6);if(first){ctx.moveTo(x,y);first=false;}else ctx.lineTo(x,y);ctx.lineTo(x,yy);}ctx.stroke();
  }
}
function duplicateStudioChart(ws,source){
  if(ws.charts.length>=8){notice(I18N.t("Massimo 8 grafici per pagina."),true);return;}
  const graph=ws.create(ws.template.cloneNode(true),source.name+' · copia',[...source.ids],false);
  graph.series=source.series;graph.nodes=new Map(source.nodes);graph.types={...source.types};graph.signalSettings=JSON.parse(JSON.stringify(source.signalSettings||{}));graph.axisSettings=JSON.parse(JSON.stringify(source.axisSettings||{}));
  if(ws.staticMode&&source.context)graph.context={...source.context,chosen:new Set(source.context.chosen),compareFiles:(source.context.compareFiles||[]).map(f=>({...f})),markerSeq:0,loading:false,comparing:false};
  ws.updateGraph(graph);graph.trend.zoom=source.trend.zoom?JSON.parse(JSON.stringify(source.trend.zoom)):null;graph.trend.linkedRange=source.trend.linkedRange?{...source.trend.linkedRange}:null;
  if(source.trend.frozen){graph.trend.series=source.trend.cloneSeries(source.trend.series);graph.trend.frozen=true;graph.trend.manualPaused=source.trend.manualPaused;}
  graph.element.querySelector('.chartwrap').style.height=source.element.querySelector('.chartwrap').style.height;
  ws.active=graph;ws.renderSelectors();ws.options.onActive?.(graph,source);graph.trend.draw();ws.options.onMarkers?.(ws.markers);ws.changed();return graph;
}
function exportChartImage(graph){
  const t=graph.trend,c=document.createElement('canvas'),dpr=window.devicePixelRatio||1;
  c.width=t.canvas.width;c.height=t.canvas.height+Math.round(70*dpr);const ctx=c.getContext('2d');ctx.fillStyle=window.studioTheme.surface;ctx.fillRect(0,0,c.width,c.height);ctx.drawImage(t.canvas,0,70*dpr);ctx.scale(dpr,dpr);ctx.fillStyle=window.studioTheme.text;ctx.font='600 15px Segoe UI';ctx.fillText(graph.name,20,24);ctx.font='11px Segoe UI';ctx.fillText([...t.nodes].map(([id,n])=>n.display_name+(t.presentation[id]?.unit?' ['+t.presentation[id].unit+']':'')).join(' · ').slice(0,150),20,45);
  const save=blob=>{if(!blob){notice(I18N.t("Export immagine non disponibile."),true);return;}const a=document.createElement('a'),url=URL.createObjectURL(blob);a.href=url;a.download=graph.name.replace(/[^a-zA-Z0-9_-]/g,'_')+'.png';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};c.toBlob(save,'image/png');
}
const decorateStudioChart=(ws,g)=>{
  if(g.studioDecorated)return;g.studioDecorated=true;g.signalSettings=g.signalSettings||{};g.axisSettings=g.axisSettings||{};
  const wrap=g.element.querySelector('.chartwrap');wrap.after(createResizer(wrap,180,()=>Math.max(600,window.innerHeight*1.5),()=>g.trend.draw(),'chart-resizer'));
  const panel=g.element.querySelector('.marker-panel');panel.after(createResizer(panel,130,()=>window.innerHeight*.7,()=>{},'panel-resizer'));
  setupNavigator(g);
  g.element.addEventListener('pointerdown',e=>{if(!e.target.closest('button,summary,input,select'))ws.activate(g);});
  g.element.querySelector('.marker-analysis').addEventListener('toggle',()=>{g.trend.draw();scheduleWorkspaceSave();});
  for(const button of g.element.querySelectorAll('[data-studio-action]'))button.onclick=()=>{
    const action=button.dataset.studioAction;const menu=button.closest('details');if(menu)menu.open=false;
    if(action==='rename'){const input=g.element.querySelector('[data-chart-role=title]');input.focus();input.select();}
    if(action==='duplicate')duplicateStudioChart(ws,g);
    if(action==='axes')openAxisProperties(ws,g);
    if(action==='signals'){const id=[...g.ids][0];if(id)openSignalProperties(ws,g,id);else notice(I18N.t("Aggiungi un segnale al grafico."),true);}
    if(action==='image')exportChartImage(g);
    if(action==='sync'){const cb=$(ws.scope+'-sync-zoom');cb.checked=!cb.checked;cb.dispatchEvent(new Event('change',{bubbles:true}));}
  };
  g.actionToolbar=new ChartActionToolbar(g,ws);
  const draw=g.trend.draw.bind(g.trend);g.trend.draw=function(){draw();drawNavigator(g);const delta=g.element.querySelector('.marker-inline-delta');I18N.bind(delta,"textContent",()=>('Δt '+this.formatDuration(Number.isFinite(this.markers.x1)&&Number.isFinite(this.markers.x2)?this.markers.x2-this.markers.x1:NaN)),false);const badge=g.element.querySelector('[data-studio-role=state]');I18N.bind(badge,"textContent",()=>(ws.staticMode?I18N.t("FILE"):this.frozen?I18N.t("PAUSA"):connected?'LIVE ●':'OFFLINE'),true);badge.className='graph-state'+(this.frozen?' paused':!connected&&!ws.staticMode?' disconnected':'');g.element.dataset.dropLabel=I18N.join([{text:"Rilascia per aggiungere a "},{value:(g.name)}]);g.actionToolbar?.syncState();scheduleStudioToolbar();};
  if(ws.staticMode){g.element.classList.add('signal-drop-target');const over=e=>{if(Array.from(e.dataTransfer?.types||[]).includes('application/x-opcua-history')){e.preventDefault();g.element.classList.add('signal-drag-over');}};g.element.addEventListener('dragover',over);g.element.addEventListener('dragleave',e=>{if(!g.element.contains(e.relatedTarget))g.element.classList.remove('signal-drag-over');});g.element.addEventListener('drop',e=>{g.element.classList.remove('signal-drag-over');const raw=e.dataTransfer?.getData('application/x-opcua-history');if(!raw)return;e.preventDefault();try{const payload=JSON.parse(raw);dropHistorySignal(ws,g,payload).catch(err=>notice(err.message,true));}catch{notice(I18N.t("Segnale trascinato non valido."),true);}});}
}
window.decorateStudioChart=decorateStudioChart;
async function dropHistorySignal(ws,g,payload){
  const source=ws.charts.find(c=>c.id===payload.graph);if(!source?.context?.file)return;const file=source.context.file;if(!(file.signals||[]).some(s=>s.nodeid===payload.id))return;
  if(g.context?.file&&(g.context.file.name!==file.name||g.context.file.source!==file.source)){notice(I18N.t("Questo grafico usa un altro file. Usa un grafico vuoto per questa registrazione."),true);return;}
  const chosen=new Set(g.context?.chosen||[]);chosen.add(payload.id);
  g.context={...emptyHistory(),...source.context,compareFiles:(source.context.compareFiles||[]).map(f=>({...f})),chosen,loading:false,comparing:false,loaded:false,markerSeq:0};ws.activate(g);renderHistoryControls();await loadHistory(g);scheduleWorkspaceSave();
}
const originalRenderSelected=renderSelected;
renderSelected=function(){originalRenderSelected();const graph=liveCharts.active;if(!graph)return;
  for(const row of $('selected').querySelectorAll('.selected-row')){
    const id=row.dataset.id,prefs=graph.signalSettings?.[id]||{},node=selected.get(id)||liveCharts.nodes.get(id)||{nodeid:id},name=row.querySelector('.signal-name');name.parentElement.classList.add('signal-label');I18N.bind(name,"textContent",()=>(prefs.alias||treeLabel(node)),false);I18N.bind(name,"title",()=>(id),false);I18N.bind(row,"title",()=>(I18N.join([{value:(prefs.alias||node.display_name||id)},{text:"\n"},{value:(id)},{text:"\nTrascina per aggiungere a un grafico"}])),true);row.querySelector('.selected-color').style.background=graph.trend.color(id);
    const axis=document.createElement('span');axis.className='signal-axis';I18N.bind(axis,"textContent",()=>(graph.trend.axisPlan.axisById.get(id)==='right'?'Y2':'Y1'),false);
    const menu=document.createElement('button');menu.className='signal-properties';I18N.bind(menu,"textContent",()=>('⋮'),false);I18N.bind(menu,"title",()=>(I18N.t("Proprietà segnale")),true);I18N.attr(menu,'aria-label',()=>(I18N.join([{text:"Proprietà "},{value:(name.textContent)}])));menu.onclick=()=>openSignalProperties(liveCharts,graph,id);row.append(axis,menu);
  }
  filterLiveSignals();renderValues(lastValues);
};
const originalRenderValues=renderValues;
renderValues=function(values){originalRenderValues(values);const map=new Map(values.map(v=>[v.nodeid,v]));for(const row of $('selected').querySelectorAll('.selected-row')){const id=row.dataset.id,v=map.get(id),prefs=liveCharts.active?.signalSettings?.[id]||{},q=row.querySelector('.quality'),value=row.querySelector('.value');
  const quality=v?.quality||(v?.good?'Good':v?'Bad':I18N.t("In attesa"));I18N.bind(q,"title",()=>(I18N.join([{value:(quality+(v?.status?' · '+v.status:'')+(v?.source_ts?I18N.t("\nSource: ")+v.source_ts:'')+(v?.server_ts?I18N.join([{text:"\nServer: "},{value:(v.server_ts)}]):''))}])),true);q.className=I18N.join([{text:"quality"},{value:(quality==='Uncertain'?' uncertain':quality==='Bad'?' bad':quality==='In attesa'?' waiting':'')}]);
  if(v){I18N.bind(value,"textContent",()=>((v.error?'—':valueText(v.value))+(prefs.unit?' '+prefs.unit:'')),false);I18N.bind(value,"title",()=>(I18N.join([{value:(value.textContent+(v.precision_warning?I18N.t(" · Grafico approssimato, valore testuale esatto"):''))}])),true);}
  const axis=row.querySelector('.signal-axis');if(axis)I18N.bind(axis,"textContent",()=>(liveCharts.active?.trend.axisPlan.axisById.get(id)==='right'?'Y2':'Y1'),false);
}};
function filterLiveSignals(){const q=$('live-signal-search').value.toLocaleLowerCase(I18N.locale());for(const row of $('selected').querySelectorAll('.selected-row'))row.hidden=!!q&&!((row.querySelector('.signal-name')?.textContent||'')+' '+row.dataset.id).toLocaleLowerCase(I18N.locale()).includes(q);}
$('live-signal-search').oninput=filterLiveSignals;
const originalHistorySignals=renderHistorySignals;
renderHistorySignals=function(){originalHistorySignals();const graph=historyCharts.active;for(const row of $('history-signal-list').querySelectorAll('.history-signal-row')){const id=row.querySelector('input').value;I18N.bind(row,"title",()=>(I18N.join([{value:(id)},{text:"\nTrascina su un grafico della stessa registrazione"}])),true);row.draggable=true;row.addEventListener('dragstart',e=>{e.dataTransfer?.setData('application/x-opcua-history',JSON.stringify({graph:graph.id,id}));if(e.dataTransfer)e.dataTransfer.effectAllowed='copy';});const button=document.createElement('button');button.type='button';button.className='signal-properties';I18N.bind(button,"textContent",()=>('⋮'),false);I18N.attr(button,'aria-label',()=>(I18N.t("Proprietà segnale")));button.onclick=e=>{e.preventDefault();if(graph.ids.has(id))openSignalProperties(historyCharts,graph,id);else notice(I18N.t("Apri il segnale nel grafico prima di configurarlo."),true);};row.append(button);}};
function propertyField(label,id,type='text',value='',options=null){const box=document.createElement('div'),lab=document.createElement('label');lab.htmlFor=id;I18N.bind(lab,"textContent",()=>(label),true);const input=document.createElement(options?'select':'input');input.id=id;if(options)for(const [v,name] of options)input.add(I18N.option(()=>(name),v,false,false,false));else input.type=type;input.value=String(value);box.append(lab,input);return box;}
function propertiesBase(title){I18N.bind($('properties-title'),"textContent",()=>(title),true);$('properties-error').hidden=true;const fields=$('properties-fields');fields.replaceChildren();const grid=document.createElement('div');grid.className='properties-grid';fields.append(grid);return grid;}
function openSignalProperties(ws,g,id){
  const node=g.nodes.get(id)||selected.get(id);if(!node)return;const p=g.signalSettings?.[id]||{};propertiesTarget={kind:'signal',ws,g,id};
  const grid=propertiesBase(I18N.join([{text:"Segnale · "},{value:(g.name)}])),choices=[...new Set([...g.ids,id])].map(key=>[key,g.signalSettings?.[key]?.alias||g.nodes.get(key)?.display_name||selected.get(key)?.display_name||key]);
  grid.append(propertyField(I18N.t("Segnale"),'prop-signal','text',id,choices));$('prop-signal').onchange=e=>openSignalProperties(ws,g,e.target.value);
  grid.append(propertyField(I18N.t("Nome / alias"),'prop-alias','text',p.alias||node.display_name||id),propertyField(I18N.t("Unità visualizzata"),'prop-unit','text',p.unit||''),propertyField(I18N.t("Asse"),'prop-axis','text',p.axis||'auto',[['auto',I18N.t("Automatico")],['left',I18N.t("Y1 · sinistra")],['right',I18N.t("Y2 · destra")]]),propertyField(I18N.t("Colore"),'prop-color','color',g.trend.color(id)),propertyField(I18N.t("Spessore linea (px)"),'prop-width','number',p.width||1.8),propertyField(I18N.t("Visibile nel grafico"),'prop-visible','text',p.visible===false?'no':'yes',[['yes',I18N.t("Sì")],['no',I18N.t("No (continua acquisizione)")]]));
  $('prop-width').min='.5';$('prop-width').max='6';$('prop-width').step='.1';$('prop-alias').maxLength=100;$('prop-unit').maxLength=24;
  const note=document.createElement('p');note.className='wide properties-note';I18N.bind(note,"textContent",()=>(I18N.join([{text:"NodeId: "},{value:(id)},{text:"\nLe proprietà sono specifiche di questo grafico. Scala manuale: menu grafico → Configura assi. Record, FFT ed export conservano i valori originali."}])),true);grid.append(note);
  const future=document.createElement('fieldset');future.className='wide';const legend=document.createElement('legend');I18N.bind(legend,"textContent",()=>(I18N.t("Trasformazioni · predisposizione UI")),true);future.append(legend,propertyField(I18N.t("Gain (non applicato)"),'prop-gain','number',1),propertyField(I18N.t("Offset (non applicato)"),'prop-offset','number',0));future.disabled=true;grid.append(future);
  if(!ws.staticMode){const remove=document.createElement('button');remove.type='button';remove.className='ghost';I18N.bind(remove,"textContent",()=>(I18N.t("Rimuovi dall’acquisizione")),true);remove.disabled=!!recordingLocked();remove.onclick=()=>(node.calculated?window.disableCalculation(id):toggle(node,false)).then(()=>closeDialog('properties-dialog')).catch(e=>notice(e.message,true));grid.append(remove);}
  openDialog('properties-dialog');
}
function openAxisProperties(ws,g){
  propertiesTarget={kind:'axes',ws,g};const grid=propertiesBase(I18N.join([{text:"Assi · "},{value:(g.name)}]));
  for(const [key,label] of [['left',I18N.t("Y1 · sinistra")],['right',I18N.t("Y2 · destra")]]){const p=g.axisSettings?.[key]||{},domain=g.trend.fullDomain?.axes[key]||{ymin:0,ymax:100};const fieldset=document.createElement('fieldset'),legend=document.createElement('legend');I18N.bind(legend,"textContent",()=>(label),true);fieldset.append(legend,propertyField(I18N.t("Scala"),'axis-'+key+'-mode','text',p.mode||'auto',[['auto','Auto'],['manual',I18N.t("Manuale")]]),propertyField(I18N.t("Unità asse (opzionale)"),'axis-'+key+'-unit','text',p.unit||''),propertyField('Min','axis-'+key+'-min','number',p.min??domain.ymin),propertyField('Max','axis-'+key+'-max','number',p.max??domain.ymax));grid.append(fieldset);for(const n of ['min','max'])$('axis-'+key+'-'+n).step='any';}
  const note=document.createElement('p');note.className='wide properties-note';I18N.bind(note,"textContent",()=>(I18N.t("Assegna Y1/Y2 nelle proprietà dei segnali. Automatico mantiene la separazione per ordine di grandezza. Le scale manuali non modificano i valori acquisiti.")),true);grid.append(note);openDialog('properties-dialog');
}
$('properties-close').onclick=()=>closeDialog('properties-dialog');
$('properties-form').onsubmit=e=>{e.preventDefault();if(!propertiesTarget)return;const {kind,ws,g,id}=propertiesTarget;
  if(!ws.charts.includes(g)){closeDialog('properties-dialog');return;}
  try{if(kind==='signal'){const width=Number($('prop-width').value);if(!Number.isFinite(width)||width<.5||width>6)throw new Error(I18N.t("Spessore linea tra 0,5 e 6 px."));g.signalSettings[id]={alias:$('prop-alias').value.trim().slice(0,100),unit:$('prop-unit').value.trim().slice(0,24),axis:$('prop-axis').value,color:$('prop-color').value,width,visible:$('prop-visible').value==='yes'};}
    else{const settings={};for(const key of ['left','right']){const min=Number($('axis-'+key+'-min').value),max=Number($('axis-'+key+'-max').value),mode=$('axis-'+key+'-mode').value;if(mode==='manual'&&(!Number.isFinite(min)||!Number.isFinite(max)||max<=min))throw new Error(I18N.join([{text:"Scala "},{value:(key)},{text:": Max deve essere maggiore di Min."}]));settings[key]={mode,min,max,unit:$('axis-'+key+'-unit').value.trim().slice(0,24)};}g.axisSettings=settings;g.trend.zoom=null;g.trend.axisSignature='';}
    ws.updateGraph(g);g.navigatorCache=null;g.trend.draw();if(!ws.staticMode)renderSelected();else renderHistorySignals();scheduleWorkspaceSave();closeDialog('properties-dialog');
  }catch(err){$('properties-error').hidden=false;I18N.bind($('properties-error'),"textContent",()=>(err.message),true);}
};
window.captureStudio=()=>({palette:'standard-v1',theme:studioThemeMode,liveCollapsed:$('live-page').classList.contains('sidebar-collapsed'),historyCollapsed:$('recordings-page').classList.contains('sidebar-collapsed'),liveRatio:liveCharts.gridRatio||.5,historyRatio:historyCharts.gridRatio||.5,details:[...document.querySelectorAll('.secondary-controls,.diagnostics-drawer')].map(d=>d.open),fullscreen:studioFullscreen?{scope:activeWorkspace().scope,index:activeWorkspace().charts.indexOf(studioFullscreen)}:null});
window.restoreStudio=saved=>{applyStudioTheme(saved.palette?(saved.theme||studioThemeMode):'light',false);for(const [ws,page,key] of [[liveCharts,'live-page','live'],[historyCharts,'recordings-page','history']]){$(page).classList.toggle('sidebar-collapsed',!!saved[key+'Collapsed']);$(ws.scope+'-sidebar-toggle').setAttribute('aria-pressed',String(!!saved[key+'Collapsed']));applyGridRatio(ws,saved[key+'Ratio']);for(const g of ws.charts){g.element.classList.remove('expanded-chart');if(g.signalSettings)ws.updateGraph(g);}}
  document.querySelectorAll('.secondary-controls,.diagnostics-drawer').forEach((d,i)=>{if(typeof saved.details?.[i]==='boolean')d.open=saved.details[i];});if(saved.fullscreen){const ws=saved.fullscreen.scope==='history'?historyCharts:liveCharts,g=ws.charts[saved.fullscreen.index];if(g)toggleStudioFullscreen(g);}renderSelected();renderHistorySignals();};
for(const ws of [liveCharts,historyCharts]){
  for(const g of ws.charts)decorateStudioChart(ws,g);setupGridResizer(ws);
  $(ws.scope+'-sidebar-toggle').onclick=e=>{const page=$(ws.staticMode?'recordings-page':'live-page');page.classList.toggle('sidebar-collapsed');e.currentTarget.setAttribute('aria-pressed',String(page.classList.contains('sidebar-collapsed')));requestAnimationFrame(()=>ws.draw());scheduleWorkspaceSave();};
  const render=ws.renderSelectors.bind(ws);ws.renderSelectors=function(){render();refreshPresetVisibility(ws);};const layout=ws.setLayout.bind(ws);ws.setLayout=function(mode){layout(mode);refreshPresetVisibility(ws);requestAnimationFrame(()=>ws.draw());};
  ws.layoutSelect.addEventListener('change',()=>{const count={single:1,vertical:2,horizontal:2,quad:4}[ws.layoutSelect.value];if(count)while(ws.charts.length<count)ws.add();refreshPresetVisibility(ws);ws.draw();});
  const activate=ws.activate.bind(ws);ws.activate=function(...args){activate(...args);scheduleStudioToolbar();if(ws.layoutSelect.value==='single')requestAnimationFrame(()=>ws.draw());};
  const remove=ws.remove.bind(ws);ws.remove=function(g){if(studioFullscreen===g)toggleStudioFullscreen(g);remove(g);};
}
for(const id of ['selected','scan-list','tree','history-file-list','history-signal-list']){const target=$(id);target.after(createResizer(target,100,()=>window.innerHeight*.8,()=>{},'panel-resizer'));}
const originalShowAppPage=showAppPage;showAppPage=function(page){if(studioFullscreen&&!((page==='recordings'?historyCharts:liveCharts).charts.includes(studioFullscreen)))toggleStudioFullscreen(studioFullscreen);originalShowAppPage(page);scheduleStudioToolbar();};
try{applyStudioTheme(localStorage.getItem('space-analyzer.palette.version')?(localStorage.getItem('opcua.studio.theme')||'light'):'light',false);}catch{applyStudioTheme('light',false);}
window.opcuaConfigReady=loadConfig().then(async()=>{startPolling();await restoreWorkspace();renderSelected();scheduleStudioToolbar();}).catch(e=>{workspaceReady=true;notice(I18N.t("Avvio interfaccia: ")+e.message,true);}).finally(()=>startPolling());
