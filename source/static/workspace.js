/* V1.1.15: responsive split panels and independent graph signal assignment. */
class ResizablePageLayout {
  constructor(pageId,splitterId,storageKey,onResize){
    this.page=document.getElementById(pageId);this.splitter=document.getElementById(splitterId);this.storageKey=storageKey;this.onResize=onResize;
    this.pointer=null;this.startX=0;this.startWidth=0;this.saved=null;
    if(!this.page||!this.splitter)return;
    try{const v=Number(localStorage.getItem(storageKey));if(Number.isFinite(v)&&v>0)this.saved=v;}catch{}
    if(this.saved)this.apply(this.saved,false);else this.syncAria();
    this.splitter.addEventListener('pointerdown',e=>this.begin(e));
    this.splitter.addEventListener('dblclick',()=>this.reset());
    this.splitter.addEventListener('keydown',e=>this.key(e));
    window.addEventListener('resize',()=>this.clamp());
  }
  desktop(){return window.matchMedia?window.matchMedia('(min-width: 761px)').matches:window.innerWidth>760;}
  bounds(){const total=Math.max(0,this.page?.clientWidth||window.innerWidth||1200),min=250,minMain=430,max=Math.max(min,Math.min(720,total-minMain-34));return {min,max};}
  width(){const first=this.page.firstElementChild,w=first?.getBoundingClientRect().width;if(Number.isFinite(w)&&w>0)return w;const inline=parseFloat(this.page.style.getPropertyValue('--sidebar-width'));return Number.isFinite(inline)?inline:360;}
  apply(value,persist=true){if(!this.page||!this.desktop())return;const {min,max}=this.bounds(),width=Math.max(min,Math.min(max,Number(value)||360));this.page.style.setProperty('--sidebar-width',width+'px');if(persist){this.saved=width;try{localStorage.setItem(this.storageKey,String(Math.round(width)));}catch{}}this.syncAria(width);this.onResize?.();}
  syncAria(width=this.width()){if(!this.splitter)return;const {min,max}=this.bounds();this.splitter.setAttribute('aria-valuemin',String(Math.round(min)));this.splitter.setAttribute('aria-valuemax',String(Math.round(max)));this.splitter.setAttribute('aria-valuenow',String(Math.round(width)));}
  begin(event){if(!this.desktop()||event.button!==0)return;event.preventDefault();this.pointer=event.pointerId;this.startX=event.clientX;this.startWidth=this.width();this.splitter.classList.add('dragging');try{this.splitter.setPointerCapture(event.pointerId);}catch{}
    const move=e=>{if(this.pointer!==null&&(!('pointerId'in e)||e.pointerId===this.pointer))this.apply(this.startWidth+(e.clientX-this.startX));};
    const up=e=>{if(this.pointer===null||('pointerId'in e&&e.pointerId!==this.pointer))return;this.pointer=null;this.splitter.classList.remove('dragging');try{this.splitter.releasePointerCapture(e.pointerId);}catch{}window.removeEventListener('pointermove',move);window.removeEventListener('pointerup',up);};
    window.addEventListener('pointermove',move);window.addEventListener('pointerup',up);
  }
  key(event){if(!this.desktop())return;let next=null;if(event.key==='ArrowLeft')next=this.width()-(event.shiftKey?80:24);if(event.key==='ArrowRight')next=this.width()+(event.shiftKey?80:24);if(event.key==='Home'){this.reset();event.preventDefault();return;}if(next!==null){event.preventDefault();this.apply(next);}}
  reset(){if(!this.page)return;this.page.style.removeProperty('--sidebar-width');try{localStorage.removeItem(this.storageKey);}catch{}this.syncAria();this.onResize?.();}
  clamp(){if(!this.desktop()){this.page?.style.removeProperty('--sidebar-width');this.onResize?.();return;}const current=this.saved||this.width();this.apply(current,false);}
}

/* Multiple chart views share acquisition and marker timestamps, not their view state. */
class ChartWorkspace {
  constructor(options){
    this.options=options;this.scope=options.scope;this.root=document.getElementById(options.root);
    this.selector=document.getElementById(options.selector);this.addButton=document.getElementById(options.add);
    this.layoutSelect=document.getElementById(options.layout);this.staticMode=!!options.staticMode;
    this.syncZoom=true;this.syncHover=true;this.liveZoom=false;this.timeRange=null;this.charts=[];this.counter=0;this.active=null;this.markers={x1:null,x2:null};this.colors=new Map();
    this.series={};this.nodes=new Map();this.types={};this.knownIds=new Set();this.pendingLayout=null;
    const base=this.root.firstElementChild;this.template=base.cloneNode(true);
    this.create(base,'Grafico 1',[],false);
    this.selector.addEventListener('change',()=>this.activate(this.charts.find(c=>c.id===this.selector.value)));
    this.addButton.addEventListener('click',()=>this.add());
    this.layoutSelect.addEventListener('change',()=>this.setLayout(this.layoutSelect.value));
  }
  create(element,name,ids,notify=true){
    const index=++this.counter,id=this.scope+'-g'+index;
    if(index!==1&&element.id)element.id=id+'-'+element.id;
    const remap=new Map();
    for(const child of element.querySelectorAll('[id]')){
      const old=child.id,next=index===1?old:id+'-'+old;remap.set(old,next);child.id=next;
    }
    for(const child of element.querySelectorAll('[for],[aria-labelledby],[aria-describedby]')){
      for(const attr of ['for','aria-labelledby','aria-describedby'])if(child.hasAttribute(attr)){
        child.setAttribute(attr,child.getAttribute(attr).split(' ').map(v=>remap.get(v)||v).join(' '));
      }
    }
    element.dataset.chartId=id;element.classList.add('workspace-chart');
    if(!element.parentElement)this.root.append(element);
    const graph={id,name,element,ids:new Set(ids),series:{},nodes:new Map(),types:{},cutoff:null,cleared:false,context:null,loadRevision:0};
    const get=role=>element.querySelector('[data-chart-role="'+role+'"]');
    const ui={};for(const role of ['zoomState','resetZoom','pausePlay','timeScroll','timeScrollStatus','markerX1','markerX2','resetMarkers','markerHint','markerX1Time','markerX2Time','markerDeltaTime','markerDataBody']){
      const control=get(role);ui[role]=control?.id||null;
    }
    graph.trend=new Trend(get('canvas'),get('legend'),get('tooltip'),{ui,staticMode:this.staticMode,liveZoom:this.liveZoom,preserveMarkers:true,
      onLiveRange:range=>{if(this.syncZoom)this.timeRange={...range};},
      exactMarkerMode:this.staticMode,onMarkersChanged:markers=>this.setMarkers(markers,graph),onViewChanged:range=>this.syncRange(graph,range),onHover:t=>this.hoverAt(graph,t),
      emptyMessage:this.staticMode?I18N.t("Apri una registrazione"):I18N.t("Trascina qui i segnali oppure selezionali dalla lista")});
    if(!this.staticMode){graph.trend.onFreeze=()=>window.captureFrozenAnalysis?.(graph);graph.trend.onResume=()=>window.releaseFrozenAnalysis?.(graph);}
    graph.trend.onMarkerFrame=markers=>{if(this.active===graph)this.markers={...markers};};
    graph.trend.colors=this.colors;graph.trend.markers={...this.markers};
    get('title').value=name;I18N.attr(get('title'),'aria-label',()=>(I18N.join([{text:"Nome del grafico "},{value:(index)}])));
    get('title').addEventListener('change',()=>{graph.name=get('title').value.trim().slice(0,80)||I18N.join([{text:"Grafico "},{value:(index)}]);get('title').value=graph.name;this.renderSelectors();this.changed();});
    get('activate').addEventListener('click',()=>this.activate(graph));
    get('remove').addEventListener('click',()=>this.remove(graph));
    get('pausePlay')?.addEventListener('click',()=>graph.trend.togglePause());
    get('resetZoom').addEventListener('click',()=>graph.trend.resetZoom());
    get('clear').addEventListener('click',()=>this.clearGraph(graph));
    get('closeFile')?.addEventListener('click',()=>this.options.onClose?.(graph));
    get('expand').addEventListener('click',()=>window.toggleStudioFullscreen?.(graph));
    get('analysis')?.addEventListener('click',()=>openAnalysis(this,graph));
    if(this.options.allowSignalDrop)this.enableSignalDrop(graph);
    this.charts.push(graph);window.decorateStudioChart?.(this,graph);const previous=this.active;this.active=graph;this.renderSelectors();
    if(notify)this.options.onActive?.(graph,previous);
    return graph;
  }
  add(){
    if(this.charts.length>=8){notice(I18N.t("Sono disponibili fino a 8 grafici per pagina."),true);return;}
    const previous=this.active,copy=!!this.options.copySignalsOnAdd;
    const graph=this.create(this.template.cloneNode(true),I18N.join([{text:"Grafico "},{value:(this.counter+1)}]),copy&&previous?[...previous.ids]:[],false);
    if(this.staticMode&&copy&&previous){
      graph.series=previous.series;graph.nodes=new Map(previous.nodes);graph.types={...previous.types};
      this.options.onClone?.(graph,previous);this.updateGraph(graph);
    }else if(this.staticMode){
      graph.context=null;this.updateGraph(graph);this.options.onNew?.(graph,previous);
    }else this.update(this.series,this.nodes,this.types);
    this.options.onActive?.(graph,previous);this.changed();this.options.onMarkers?.(this.markers);
  }
  remove(graph){
    if(this.charts.length===1){notice(I18N.t("Mantieni almeno un grafico nella pagina."),true);return;}
    graph.loadRevision++;graph.actionToolbar?.destroy();graph.trend.destroy();graph.element.remove();this.charts=this.charts.filter(c=>c!==graph);
    if(this.active===graph){const previous=graph;this.active=this.charts[0];this.options.onActive?.(this.active,previous);}
    this.renderSelectors();this.changed();
  }
  activate(graph){
    if(!graph||graph===this.active)return;const previous=this.active;this.active=graph;
    this.renderSelectors();this.options.onActive?.(graph,previous);
  }
  renderSelectors(){
    this.selector.replaceChildren();
    for(const graph of this.charts){
      const option=document.createElement('option');option.value=graph.id;I18N.bind(option,"textContent",()=>(graph.name),false);this.selector.append(option);
      graph.element.classList.toggle('active-chart',graph===this.active);
      const active=graph.element.querySelector('[data-chart-role="activate"]');I18N.bind(active,"textContent",()=>(graph===this.active?I18N.t("Grafico attivo"):I18N.t("Seleziona")),true);active.setAttribute('aria-pressed',String(graph===this.active));
      graph.element.querySelector('[data-chart-role="remove"]').disabled=this.charts.length===1;
    }
    this.selector.value=this.active?.id||'';this.addButton.disabled=this.charts.length>=8;
    this.root.classList.toggle('multiple-charts',this.charts.length>1);
  }
  setLayout(mode){const allowed=['single','vertical','horizontal','quad','stack','grid'];mode=allowed.includes(mode)?mode:'stack';this.layoutSelect.value=mode;this.root.classList.toggle('chart-grid',mode==='grid');for(const m of allowed)this.root.classList.toggle('layout-'+m,m===mode);this.draw();this.changed();}
  draw(){for(const graph of this.charts)graph.trend.draw();}
  color(id){return this.charts[0].trend.color(id);}
  update(series,nodes,types={}){
    this.series=series;this.nodes=nodes;this.types=types;
    const ends=Object.values(series).map(points=>points.at(-1)?.[0]).filter(Number.isFinite);
    this.liveZoomEnd=ends.length?Math.max(...ends):null;
    const current=new Set(nodes.keys());
    for(const id of current)if(!this.knownIds.has(id))this.active?.ids.add(id);
    for(const graph of this.charts){
      graph.ids=new Set([...graph.ids].filter(id=>current.has(id)));
      graph.series=series;graph.nodes=nodes;graph.types=types;this.updateGraph(graph);
    }
    this.knownIds=current;
  }
  updateGraph(graph){
    const nodes=new Map([...graph.nodes].filter(([id])=>graph.ids.has(id))),data={};
    for(const id of nodes.keys()){const points=graph.series[id]||[];data[id]=graph.cleared?[]:graph.cutoff===null?points:isSampleSeries(points)?points.after(graph.cutoff):points.filter(p=>p[0]>graph.cutoff);}
    graph.signalSettings=graph.signalSettings||{};graph.axisSettings=graph.axisSettings||{};graph.trend.presentation=graph.signalSettings;graph.trend.axisSettings=graph.axisSettings;
    for(const [id,node] of nodes)if(node.calculated&&node.unit&&graph.signalSettings[id]?.unit===undefined)graph.signalSettings[id]={...(graph.signalSettings[id]||{}),unit:node.unit};
    for(const [id,node] of nodes){const prefs=graph.signalSettings[id];if(prefs?.visible===false){nodes.delete(id);delete data[id];}else if(prefs?.alias)nodes.set(id,{...node,display_name:prefs.alias});}
    graph.trend.displayRange=!this.staticMode&&this.displayRange?{...this.displayRange,xmin:Math.max(this.displayRange.xmin,graph.cutoff??-Infinity)}:null;
    graph.trend.liveZoomEnd=this.syncZoom?(this.displayRange?.xmax??this.liveZoomEnd):null;
    graph.trend.update(data,nodes,graph.types);
    const counts={Good:0,Uncertain:0,Bad:0};for(const points of Object.values(graph.trend.series)){if(isSampleSeries(points)){counts.Good+=points.stats.good;counts.Uncertain+=points.stats.uncertain;counts.Bad+=points.stats.bad;continue;}for(const p of points)counts[p[2]||(p[1]===null?'Bad':'Good')]++;}
    const summary=graph.element.querySelector('[data-chart-role="qualitySummary"]');if(summary){I18N.bind(summary,"textContent",()=>(Object.entries(counts).map(([k,v])=>k+' '+v).join(' · ')),false);I18N.bind(summary,"title",()=>(I18N.t("Qualità dei campioni mostrati nel grafico (visibile solo con campioni Uncertain o Bad). Rosso: Bad; ambra: Uncertain.")),true);summary.classList.toggle('has-bad',counts.Bad>0);summary.hidden=counts.Bad+counts.Uncertain===0;}
    if(this.syncZoom&&this.timeRange&&!(this.liveZoom&&graph.trend.frozen)){graph.trend.linkedRange={...this.timeRange};graph.trend.draw();}
    const caption=graph.element.querySelector('[data-chart-role="caption"]');
    I18N.bind(caption,"textContent",()=>(I18N.join([{value:(nodes.size)},{text:" segnali"},{value:(this.staticMode&&graph.context?.file?' · '+(graph.context.file.display_name||graph.context.file.name):'')}])),true);
  }
  setGraphData(graph,series,nodes,types={}){
    graph.loadRevision++;graph.series=series;graph.nodes=nodes;graph.types=types;graph.ids=new Set(nodes.keys());graph.cutoff=null;graph.cleared=false;
    graph.trend.zoom=null;graph.trend.frozen=false;graph.trend.manualPaused=false;graph.trend.exactMarkerRows=null;
    this.updateGraph(graph);this.options.onMarkers?.(this.markers);
  }
  assign(id,enabled){return this.assignTo(this.active,id,enabled);}
  assignTo(graph,id,enabled){
    if(!graph||!this.nodes.has(id))return false;
    if(enabled)graph.ids.add(id);else graph.ids.delete(id);
    graph.cleared=false;this.updateGraph(graph);this.changed();this.options.onAssignment?.(graph,id,enabled);return true;
  }
  enableSignalDrop(graph){
    const element=graph.element;
    const readId=event=>{const dt=event.dataTransfer;if(!dt)return '';return dt.getData('application/x-opcua-signal')||dt.getData('text/plain')||'';};
    const hasSignal=event=>{const dt=event.dataTransfer;if(!dt)return false;const types=Array.from(dt.types||[]);return types.includes('application/x-opcua-signal')||types.includes('text/plain');};
    element.classList.add('signal-drop-target');element.dataset.dropLabel=I18N.join([{text:"Rilascia per aggiungere a "},{value:(graph.name)}]);
    const enter=event=>{if(!hasSignal(event))return;event.preventDefault();if(event.dataTransfer)event.dataTransfer.dropEffect='copy';element.classList.add('signal-drag-over');};
    element.addEventListener('dragenter',enter);element.addEventListener('dragover',enter);
    element.addEventListener('dragleave',event=>{if(!element.contains(event.relatedTarget))element.classList.remove('signal-drag-over');});
    element.addEventListener('drop',event=>{const id=readId(event);element.classList.remove('signal-drag-over');if(!id||!this.nodes.has(id))return;event.preventDefault();this.assignTo(graph,id,true);this.activate(graph);this.options.onSignalDrop?.(graph,id);});
  }
  clearGraph(graph){
    graph.cutoff=this.staticMode?null:Date.now();graph.cleared=this.staticMode;
    graph.trend.zoom=null;graph.trend.frozen=false;graph.trend.manualPaused=false;
    this.updateGraph(graph);
    this.options.onClear?.(graph);
  }
  resetViews(){for(const graph of this.charts){graph.cutoff=null;graph.cleared=false;graph.trend.resetZoom();}}
  setLiveZoom(enabled){
    this.liveZoom=!this.staticMode&&!!enabled;
    // Set every chart's policy before resuming any synchronized view.
    for(const graph of this.charts)graph.trend.liveZoom=this.liveZoom;
    for(const graph of this.charts)graph.trend.setLiveZoom(this.liveZoom);
    this.changed();
  }
  setMarkers(markers,source=this.active){
    this.markers={x1:Number.isFinite(markers.x1)?markers.x1:null,x2:Number.isFinite(markers.x2)?markers.x2:null};
    for(const graph of this.charts){
      graph.trend.markers={...this.markers};
      for(const key of ['x1','x2']){
        const d=source?.trend.domain;
        graph.trend.markerFractions[key]=Number.isFinite(markers[key])&&d?Math.max(0,Math.min(1,(markers[key]-d.xmin)/(d.xmax-d.xmin))):null;
      }
      graph.trend.markerMode=null;graph.trend.exactMarkerRows=null;graph.trend.exactMarkerTimes=null;
      graph.trend.syncMarkerControls();graph.trend.updateMarkerTable();graph.trend.draw();
    }
    this.options.onMarkers?.(this.markers);window.scheduleWorkspaceSave?.();
  }
  resetMarkers(){this.setMarkers({x1:null,x2:null});}
  setEmptyMessage(message){for(const graph of this.charts)graph.trend.emptyMessage=message;}
  serialize(){return {layout:this.layoutSelect.value,active:this.charts.indexOf(this.active),charts:this.charts.map(c=>({name:c.name,ids:[...c.ids]}))};}
  restore(config,nodes){
    if(!config||!Array.isArray(config.charts)||!config.charts.length)return;
    for(const graph of this.charts){graph.actionToolbar?.destroy();graph.trend.destroy();graph.element.remove();}
    this.charts=[];this.counter=0;this.active=null;this.knownIds=new Set(nodes.keys());
    for(const item of config.charts.slice(0,8))this.create(this.template.cloneNode(true),String(item.name||I18N.t("Grafico")).slice(0,80),Array.isArray(item.ids)?item.ids.filter(id=>nodes.has(id)):[],false);
    this.active=this.charts[Math.max(0,Math.min(this.charts.length-1,Number(config.active)||0))];
    this.setLayout(config.layout);this.renderSelectors();
  }
  syncRange(source,range){
    if(this.syncZoom){
      this.timeRange=range?{...range}:null;
      for(const graph of this.charts){const t=graph.trend;
        if(range&&this.liveZoom&&t.frozen&&t.manualPaused&&graph!==source)continue;
        t.linkedRange=range?{...range}:null;
        if(range)t.pauseForZoom();
        if(!range){t.onResume?.();t.zoom=null;t.frozen=false;t.manualPaused=false;t.series=t.latestSeries;}
        t.draw();
      }
    }
    window.scheduleWorkspaceSave?.();
  }
  hoverAt(source,time){
    for(const graph of this.charts){const t=graph.trend;t.crosshair=this.syncHover||graph===source?time:null;t.showHover(t.crosshair);t.renderCrosshair();}

  }
  changed(){this.options.onChange?.();window.scheduleWorkspaceSave?.();}
}
