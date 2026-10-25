/* OPC UA Web Client V1.1.15
 * Script classico: nessuna dipendenza ES-module/.mjs o CDN.
 * Trend con doppia scala Y automatica quando i segnali hanno ordini di grandezza molto diversi.
 */
/* Serie a piena risoluzione dei file: colonne tipizzate (tempo, valore, qualità) con tutti i campioni.
 * Quando i campioni visibili superano i pixel, ogni colonna di pixel mostra l'intervallo minimo–massimo di tutti
 * i suoi campioni (la stessa copertura della linea completa); con lo zoom compaiono i singoli campioni.
 * Nessun campione viene scartato. */
const SAMPLE_QUALITIES=['Good','Uncertain','Bad'];
class SampleSeries{
  constructor(t,v,q){
    this.t=t;this.v=v;this.q=q;
    let xmin=Infinity,xmax=-Infinity,ymin=Infinity,ymax=-Infinity,count=0;const counts=[0,0,0];
    for(let i=0;i<t.length;i++){const time=t[i],value=v[i];counts[q[i]]=(counts[q[i]]||0)+1;if(!Number.isFinite(time))continue;if(time<xmin)xmin=time;if(time>xmax)xmax=time;if(value===value&&value!==Infinity&&value!==-Infinity){if(value<ymin)ymin=value;if(value>ymax)ymax=value;count++;}}
    this.stats={xmin,xmax,ymin,ymax,count,good:counts[0],uncertain:counts[1],bad:counts[2]};
  }
  get length(){return this.t.length;}
  value(i){const v=this.v[i];return Number.isFinite(v)?v:null;}
  quality(i){return SAMPLE_QUALITIES[this.q[i]]||'Bad';}
  at(i){const n=this.t.length;if(i<0)i+=n;if(i<0||i>=n)return undefined;return [this.t[i],this.value(i),this.quality(i)];}
  *[Symbol.iterator](){for(let i=0;i<this.t.length;i++)yield this.at(i);}
  filter(fn){const out=[];for(let i=0;i<this.t.length;i++){const p=this.at(i);if(fn(p,i))out.push(p);}return out;}
  map(fn){const out=new Array(this.t.length);for(let i=0;i<this.t.length;i++)out[i]=fn(this.at(i),i);return out;}
  toJSON(){return this.map(p=>p);}
  /** Primo indice con tempo >= t. */
  lowerBound(t){let l=0,r=this.t.length;while(l<r){const m=(l+r)>>1;if(this.t[m]<t)l=m+1;else r=m;}return l;}
  /** Primo indice con tempo > t. */
  upperBound(t){let l=0,r=this.t.length;while(l<r){const m=(l+r)>>1;if(this.t[m]<=t)l=m+1;else r=m;}return l;}
}
function isSampleSeries(points){return points instanceof SampleSeries;}
/** Risposta binaria di /api/recordings/samples: intestazione JSON e colonne Float64/Float64/Uint8 per segnale. */
function decodeSampleBuffer(buffer){
  const view=new DataView(buffer),magic=String.fromCharCode(view.getUint8(0),view.getUint8(1),view.getUint8(2),view.getUint8(3));
  if(magic!=='SAS1')throw new Error(I18N.t("Risposta del servizio locale non valida"));
  const length=view.getUint32(4,true),header=JSON.parse(new TextDecoder().decode(new Uint8Array(buffer,8,length)));
  const align=n=>n+((8-n%8)%8);let offset=align(8+length);const series={};
  for(const signal of header.signals||[]){
    const n=Number(signal.points_returned||0);
    const t=new Float64Array(buffer,offset,n);offset=align(offset+n*8);
    const v=new Float64Array(buffer,offset,n);offset=align(offset+n*8);
    const q=new Uint8Array(buffer,offset,n);offset=align(offset+n);
    series[signal.nodeid]=new SampleSeries(t,v,q);
  }
  header.series=series;return header;
}
function bounds(series, ids=null) {
  let xmin=Infinity,xmax=-Infinity,ymin=Infinity,ymax=-Infinity,count=0;
  const filter=ids?new Set(ids):null;
  for(const [id,points] of Object.entries(series)){
    if(filter&&!filter.has(id))continue;
    if(isSampleSeries(points)){const st=points.stats;if(Number.isFinite(st.xmin)){xmin=Math.min(xmin,st.xmin);xmax=Math.max(xmax,st.xmax);}if(st.count){ymin=Math.min(ymin,st.ymin);ymax=Math.max(ymax,st.ymax);count+=st.count;}continue;}
    for(const [t,v] of points){
      if(!Number.isFinite(t)) continue;
      xmin=Math.min(xmin,t);xmax=Math.max(xmax,t);
      if(typeof v==='number'&&Number.isFinite(v)){ymin=Math.min(ymin,v);ymax=Math.max(ymax,v);count++;}
    }
  }
  if(!count) return null;
  const unit=Math.max(Math.abs(ymin),Math.abs(ymax),1),low=ymin/unit,high=ymax/unit;
  const span=high-low||Math.max(Math.abs(high)*.1,1/unit);
  return {xmin,xmax:xmax===xmin?xmin+1000:xmax,low:Math.max(-Number.MAX_VALUE/unit,low-span*.1),
    high:Math.min(Number.MAX_VALUE/unit,high+span*.1),unit,count};
}
function valueText(value){
  if(value===null||value===undefined)return '—';
  if(typeof value==='number')return value.toLocaleString(I18N.locale(),{maximumFractionDigits:6});
  if(typeof value==='boolean')return value?'TRUE · 1':'FALSE · 0';
  return typeof value==='object'?JSON.stringify(value):String(value);
}
function nearest(points,t){
  if(!points.length)return null;
  if(isSampleSeries(points)){let l=Math.min(points.lowerBound(t),points.length-1);if(l>0&&Math.abs(points.t[l-1]-t)<Math.abs(points.t[l]-t))l--;return points.at(l);}
  let l=0,r=points.length-1;
  while(l<r){const m=(l+r)>>1;if(points[m][0]<t)l=m+1;else r=m;}
  return l>0&&Math.abs(points[l-1][0]-t)<Math.abs(points[l][0]-t)?points[l-1]:points[l];
}
function nearestValid(points,t){
  if(!points.length)return null;
  if(isSampleSeries(points)){
    const start=points.lowerBound(t);let left=start-1,right=start;
    while(left>=0||right<points.length){
      const dl=left>=0?Math.abs(points.t[left]-t):Infinity,dr=right<points.length?Math.abs(points.t[right]-t):Infinity,idx=dl<=dr?left--:right++;
      if(Number.isFinite(points.v[idx]))return points.at(idx);
    }
    return null;
  }
  let l=0,r=points.length-1;while(l<r){const m=(l+r)>>1;if(points[m][0]<t)l=m+1;else r=m;}
  let left=l-1,right=l,best=null,bestDistance=Infinity;
  while(left>=0||right<points.length){
    const dl=left>=0?Math.abs(points[left][0]-t):Infinity,dr=right<points.length?Math.abs(points[right][0]-t):Infinity;
    const idx=dl<=dr?left--:right++,point=points[idx];
    if(!point)break;const distance=Math.abs(point[0]-t);if(distance>bestDistance)break;
    if(typeof point[1]==='number'&&Number.isFinite(point[1])){best=point;bestDistance=distance;break;}
  }
  return best;
}
function signalScale(points){
  let min=Infinity,max=-Infinity,count=0;
  if(isSampleSeries(points)){const st=points.stats;if(!st.count)return null;return Math.max(Math.max(Math.abs(st.ymin),Math.abs(st.ymax)),st.ymax-st.ymin,1e-12);}
  for(const [,v] of points){if(typeof v==='number'&&Number.isFinite(v)){min=Math.min(min,v);max=Math.max(max,v);count++;}}
  if(!count)return null;
  const span=max-min,absmax=Math.max(Math.abs(min),Math.abs(max));
  return Math.max(absmax,span,1e-12);
}
function axisAssignment(series,nodes,threshold=20){
  const stats=[];
  for(const id of nodes.keys()){
    const scale=signalScale(series[id]||[]);
    if(scale!==null&&Number.isFinite(scale)&&scale>0)stats.push({id,scale,log:Math.log10(scale)});
  }
  const axisById=new Map([...nodes.keys()].map(id=>[id,'left']));
  if(stats.length<2)return {dual:false,axisById,left:[...nodes.keys()],right:[],ratio:1};
  stats.sort((a,b)=>a.log-b.log);
  const overallRatio=stats[stats.length-1].scale/stats[0].scale;
  if(!Number.isFinite(overallRatio)||overallRatio<threshold)return {dual:false,axisById,left:[...nodes.keys()],right:[],ratio:overallRatio};
  let split=0,bestGap=-Infinity;
  for(let i=0;i<stats.length-1;i++){
    const gap=stats[i+1].log-stats[i].log;
    if(gap>bestGap){bestGap=gap;split=i;}
  }
  // Evita due assi per differenze distribuite gradualmente senza un vero salto di scala.
  if(bestGap<Math.log10(Math.sqrt(threshold)))return {dual:false,axisById,left:[...nodes.keys()],right:[],ratio:overallRatio};
  const low=new Set(stats.slice(0,split+1).map(s=>s.id)),high=new Set(stats.slice(split+1).map(s=>s.id));
  if(!low.size||!high.size)return {dual:false,axisById,left:[...nodes.keys()],right:[],ratio:overallRatio};
  for(const id of high)axisById.set(id,'right');
  // I nodi senza ancora campioni numerici restano sull'asse sinistro finché non sono classificabili.
  return {dual:true,axisById,left:[...nodes.keys()].filter(id=>axisById.get(id)==='left'),right:[...nodes.keys()].filter(id=>axisById.get(id)==='right'),ratio:overallRatio};
}
class Trend {
  constructor(canvas,legend,tooltip,options={}){
    this.ui={zoomState:'zoom-state',resetZoom:'reset-zoom',pausePlay:'pause-play',timeScroll:'time-scroll',timeScrollStatus:'time-scroll-status',markerX1:'marker-x1',markerX2:'marker-x2',resetMarkers:'reset-markers',markerHint:'marker-hint',markerX1Time:'marker-x1-time',markerX2Time:'marker-x2-time',markerDeltaTime:'marker-delta-time',markerDataBody:'marker-data-body',...(options.ui||{})};
    this.staticMode=!!options.staticMode;this.liveZoom=!this.staticMode&&!!options.liveZoom;this.onLiveRange=options.onLiveRange;this.onViewChanged=options.onViewChanged;this.onHover=options.onHover;this.linkedRange=null;this.crosshair=null;this.onMarkersChanged=typeof options.onMarkersChanged==='function'?options.onMarkersChanged:null;
    this.preserveMarkers=!!options.preserveMarkers;this.exactMarkerMode=!!options.exactMarkerMode;this.exactMarkerRows=null;this.listeners=[];
    const listen=(target,type,handler)=>{target.addEventListener(type,handler);this.listeners.push(()=>target.removeEventListener(type,handler));};
    this.canvas=canvas;this.crosshairEl=document.createElement('div');this.crosshairEl.className='synchronized-crosshair';this.crosshairEl.hidden=true;canvas.parentElement.append(this.crosshairEl);this.legend=legend;this.tooltip=tooltip;this.colors=new Map();this.series={};this.latestSeries={};this.nodes=new Map();this.types={};
    this.presentation={};this.axisSettings={};this.emptyMessage=options.emptyMessage||I18N.t("Seleziona una variabile numerica per avviare il trend");this.zoom=null;this.drag=null;this.fullDomain=null;this.nodeSignature='';this.frozen=false;this.frozenAt=null;this.manualPaused=false;
    this.markers={x1:null,x2:null};this.markerFractions={x1:null,x2:null};this.markerMode=null;
    this.axisPlan={dual:false,axisById:new Map(),left:[],right:[],ratio:1};this.axisSignature='';
    if(typeof ResizeObserver!=='undefined'){this._resizeObserver=new ResizeObserver(()=>this.draw());this._resizeObserver.observe(canvas.parentElement);}
    else listen(window,'resize',()=>this.draw());
    if('PointerEvent' in window){
      listen(canvas,'pointerdown',e=>this.zoomStart(e));
      listen(canvas,'pointermove',e=>this.pointerMove(e));
      listen(canvas,'pointerup',e=>this.zoomEnd(e));
      listen(canvas,'pointercancel',e=>this.zoomCancel(e));
    }else{
      listen(canvas,'mousedown',e=>this.zoomStart(e));
      listen(canvas,'mousemove',e=>this.pointerMove(e));
      listen(window,'mouseup',e=>this.zoomEnd(e));
    }
    listen(canvas,'mouseleave',()=>{if(!this.drag){tooltip.hidden=true;this.onHover?.(null);}});
    listen(canvas,'dblclick',()=>this.resetZoom());
    canvas.classList.add('zoomable');
    for(const [which,id] of [['x1','markerX1'],['x2','markerX2']]){
      const button=this.el(id);if(button&&typeof button.addEventListener==='function')listen(button,'click',()=>this.armMarker(which));
    }
    const resetMarkers=this.el('resetMarkers');if(resetMarkers&&typeof resetMarkers.addEventListener==='function')listen(resetMarkers,'click',()=>this.resetMarkers());
    const timeScroll=this.el('timeScroll');if(timeScroll&&typeof timeScroll.addEventListener==='function')listen(timeScroll,'input',e=>this.scrollTime(e));
    this.syncZoomControls();this.syncMarkerControls();this.syncTimeScroll();this.updateMarkerTable();
  }
  el(name){const id=this.ui[name];return id?document.getElementById(id):null;}
  destroy(){this.onResume?.();for(const remove of this.listeners)remove();this.listeners=[];this._resizeObserver?.disconnect();this.crosshairEl?.remove();}
  color(id){if(this.presentation?.[id]?.color)return this.presentation[id].color;if(!this.colors.has(id)){const palette=['#006EFF','#F58C3A','#0AAF96','#707E86','#004299','#A14C12','#006B5C','#505A60'];const i=this.colors.size;this.colors.set(id,palette[i]||`hsl(${(i*137.5)%360} 65% 38%)`);}return this.colors.get(id);}
  cloneSeries(series){return Object.fromEntries(Object.entries(series).map(([id,points])=>[id,isSampleSeries(points)?points:(points||[]).map(p=>[...p])]));}
  update(series,nodes,types={}){
    const signature=[...nodes.keys()].join('\u001f');
    if(this.nodeSignature&&signature!==this.nodeSignature){this.zoom=null;this.manualPaused=false;this.frozen=false;this.frozenAt=null;if(!this.preserveMarkers)this.resetMarkers(false);}
    this.nodeSignature=signature;
    const incoming=Object.fromEntries([...nodes.keys()].map(id=>[id,series[id]||[]]));
    this.latestSeries=incoming;
    if(!this.frozen)this.series=incoming;
    this.nodes=nodes;this.types=types;this.draw();
  }
  syncZoomControls(){
    const badge=this.el('zoomState'),reset=this.el('resetZoom');
    const dual=this.axisPlan?.dual;
    const pause=this.el('pausePlay');if(pause){I18N.bind(pause,"textContent",()=>(this.frozen?I18N.t("▶ Play"):I18N.t("⏸ Pausa")),true);pause.setAttribute('aria-pressed',String(this.frozen));pause.classList.toggle('paused',this.frozen);}
    if(badge){
      const mode=(this.zoom||this.linkedRange)?(this.staticMode?I18N.t("Zoom attivo"):this.liveZoom&&!this.frozen?I18N.t("Zoom live · Y fisse"):I18N.t("Zoom attivo · Grafico fermo")):this.manualPaused?I18N.t("Grafico in pausa"):I18N.t("Autoscale attivo");
      I18N.bind(badge,"textContent",()=>(mode+(dual?' · 2 scale Y':'')),false);badge.classList.toggle('zoomed',!!this.zoom);
      badge.classList.toggle('live-zoom',this.liveZoom&&!this.frozen&&!!(this.zoom||this.linkedRange));
    }
    if(reset)reset.disabled=!(this.zoom||this.linkedRange);
  }
  syncTimeScroll(){
    const slider=this.el('timeScroll'),status=this.el('timeScrollStatus');if(!slider)return;
    const full=this.fullDomain,view=this.linkedRange||this.zoom;
    const fullSpan=full&&Number.isFinite(full.xmin)&&Number.isFinite(full.xmax)?full.xmax-full.xmin:0;
    const viewSpan=view&&Number.isFinite(view.xmin)&&Number.isFinite(view.xmax)?view.xmax-view.xmin:0;
    const movable=!!(view&&fullSpan>0&&viewSpan>0&&viewSpan<fullSpan-1e-9);
    slider.disabled=!movable;slider.min='0';slider.max='1000';slider.step='1';
    if(movable){
      const maxOffset=fullSpan-viewSpan;const ratio=maxOffset>0?(view.xmin-full.xmin)/maxOffset:0;
      slider.value=String(Math.max(0,Math.min(1000,Math.round(ratio*1000))));
      if(status)I18N.bind(status,"textContent",()=>(I18N.join([{text:"Finestra "},{value:((viewSpan/1000).toLocaleString(I18N.locale(),{maximumFractionDigits:3}))},{text:" s · trascina per scorrere"}])),true);
    }else{slider.value='0';if(status)I18N.bind(status,"textContent",()=>(view?I18N.t("Zoom sull’intero intervallo"):I18N.t("Vista completa")),true);}
  }
  scrollTime(event){
    if(!(this.zoom||this.linkedRange)||!this.fullDomain)return;const full=this.fullDomain,view=this.linkedRange||this.zoom,span=view.xmax-view.xmin,fullSpan=full.xmax-full.xmin,maxOffset=fullSpan-span;if(!(maxOffset>0))return;
    const ratio=Math.max(0,Math.min(1,Number(event?.target?.value||0)/1000));const xmin=full.xmin+ratio*maxOffset;this.zoom={axes:this.fullDomain.axes,...this.zoom,xmin,xmax:xmin+span};this.linkedRange=null;this.tooltip.hidden=true;this.syncTimeScroll();this.draw();this.onViewChanged?.({xmin,xmax:xmin+span});
  }
  resetZoom(){
    this.onResume?.();this.linkedRange=null;this.zoom=null;this.drag=null;this.manualPaused=false;this.frozen=false;this.frozenAt=null;this.tooltip.hidden=true;
    this.series=this.latestSeries;this.syncZoomControls();this.syncTimeScroll();this.draw();this.onViewChanged?.(null);
  }
  pauseForZoom(){
    if(this.staticMode||this.liveZoom||this.frozen)return;
    this.series=this.cloneSeries(this.series);this.frozen=true;this.frozenAt=Date.now();this.onFreeze?.();
  }
  resumeZoom(){
    this.onResume?.();this.manualPaused=false;this.frozen=false;this.frozenAt=null;
    this.series=this.latestSeries;this.tooltip.hidden=true;this.draw();
    const range=this.linkedRange||this.zoom;
    if(range)this.onViewChanged?.({xmin:range.xmin,xmax:range.xmax});
  }
  setLiveZoom(enabled){
    this.liveZoom=!this.staticMode&&!!enabled;
    if(this.liveZoom&&!this.zoom&&this.linkedRange&&this.domain?.axes)this.zoom={...this.linkedRange,axes:this.domain.axes};
    if(this.liveZoom&&this.frozen&&(this.zoom||this.linkedRange)){this.resumeZoom();return;}
    if(!this.liveZoom&&(this.zoom||this.linkedRange))this.pauseForZoom();
    this.draw();
  }
  followLiveZoom(fullDomain){
    if(!this.liveZoom||this.frozen||this.drag||this.markerDrag)return;
    const range=this.linkedRange||this.zoom;if(!range)return;
    const span=range.xmax-range.xmin,end=Number.isFinite(this.liveZoomEnd)?this.liveZoomEnd:fullDomain.xmax;
    if(!(span>0)||!Number.isFinite(end))return;
    const next={xmin:end-span,xmax:end};
    if(this.zoom)Object.assign(this.zoom,next);else this.zoom={...next,axes:fullDomain.axes};
    if(this.linkedRange)this.linkedRange={...next};
    this.onLiveRange?.(next);
  }
  togglePause(){
    if(this.frozen){if(this.liveZoom){this.resumeZoom();return;}this.manualPaused=false;this.resetZoom();return;}
    this.series=this.cloneSeries(this.series);this.manualPaused=true;this.frozen=true;this.frozenAt=Date.now();this.onFreeze?.();this.tooltip.hidden=true;this.syncZoomControls();this.draw();
  }
  armMarker(which){
    if(which!=='x1'&&which!=='x2')return;
    this.markerMode=this.markerMode===which?null:which;this.drag=null;this.tooltip.hidden=true;this.syncMarkerControls();
  }
  syncMarkerControls(){
    for(const [which,id] of [['x1','markerX1'],['x2','markerX2']]){
      const button=this.el(id);if(!button)continue;
      if(button.classList&&typeof button.classList.toggle==='function')button.classList.toggle('active',this.markerMode===which);
      if(typeof button.setAttribute==='function')button.setAttribute('aria-pressed',String(this.markerMode===which));
    }
    const hint=this.el('markerHint');
    if(hint)I18N.bind(hint,"textContent",()=>(this.markerMode?I18N.join([{text:"Clicca nel grafico per posizionare "},{value:(this.markerMode.toUpperCase())},{text:"."}]):I18N.t("Seleziona X1 o X2, poi clicca nel grafico per posizionare il marker.")),true);
  }
  resetMarkers(redraw=true){this.markerFractions={x1:null,x2:null};this.markers={x1:null,x2:null};this.markerMode=null;this.syncMarkerControls();this.updateMarkerTable();if(redraw)this.draw();if(this.onMarkersChanged)this.onMarkersChanged(this.markers);}
  setMarker(which,t){
    if(!Number.isFinite(t))return;
    if(this.domain)this.markerFractions[which]=Math.max(0,Math.min(1,(t-this.domain.xmin)/(this.domain.xmax-this.domain.xmin)));
    this.markers[which]=t;this.markerMode=null;this.syncMarkerControls();this.updateMarkerTable();this.draw();if(this.onMarkersChanged)this.onMarkersChanged(this.markers);
  }
  formatMarkerTime(t){return Number.isFinite(t)?new Date(t).toLocaleTimeString(I18N.locale(),{hour:'2-digit',minute:'2-digit',second:'2-digit',fractionalSecondDigits:3}):'—';}
  formatDuration(ms){
    if(!Number.isFinite(ms))return '—';const sign=ms<0?'−':'';const a=Math.abs(ms);
    if(a<1000)return sign+a.toLocaleString(I18N.locale(),{maximumFractionDigits:3})+' ms';
    if(a<60000)return sign+(a/1000).toLocaleString(I18N.locale(),{maximumFractionDigits:6})+' s';
    return sign+(a/60000).toLocaleString(I18N.locale(),{maximumFractionDigits:6})+' min';
  }
  markerCell(point,markerT){
    if(!point||point[1]===null||point[1]===undefined||!Number.isFinite(point[0]))return {value:'—',detail:I18N.t("Nessun campione valido")};
    const dt=point[0]-markerT;const sign=dt>0?'+':'';
    return {value:valueText(point[1]),detail:`${this.formatMarkerTime(point[0])} · ${sign}${this.formatDuration(dt)}`};
  }
  updateMarkerTable(){
    const markerTimes=this.exactMarkerMode&&this.exactMarkerTimes&&Number.isFinite(this.markers.x1)&&Number.isFinite(this.markers.x2)?this.exactMarkerTimes:this.markers;
    const x1=this.el('markerX1Time'),x2=this.el('markerX2Time'),dt=this.el('markerDeltaTime'),body=this.el('markerDataBody');
    if(x1)I18N.bind(x1,"textContent",()=>(this.formatMarkerTime(markerTimes.x1)),false);if(x2)I18N.bind(x2,"textContent",()=>(this.formatMarkerTime(markerTimes.x2)),false);
    if(dt)I18N.bind(dt,"textContent",()=>(Number.isFinite(markerTimes.x1)&&Number.isFinite(markerTimes.x2)?this.formatDuration(markerTimes.x2-markerTimes.x1):'—'),false);
    if(!body||typeof body.replaceChildren!=='function')return;body.replaceChildren();
    if(!Number.isFinite(markerTimes.x1)||!Number.isFinite(markerTimes.x2)){
      const tr=document.createElement('tr'),td=document.createElement('td');td.colSpan=5;td.className='empty marker-empty';I18N.bind(td,"textContent",()=>(I18N.t("Posiziona entrambi i marker X1 e X2 per calcolare i valori e le differenze.")),true);tr.append(td);body.append(tr);return;
    }
    if(!this.nodes.size){const tr=document.createElement('tr'),td=document.createElement('td');td.colSpan=5;td.className='empty marker-empty';I18N.bind(td,"textContent",()=>(I18N.t("Nessun segnale selezionato.")),true);tr.append(td);body.append(tr);return;}
    for(const [id,node] of this.nodes){
      const exact=this.exactMarkerRows?.get(id),point=p=>p?[p.timestamp_ms,p.value]:null;
      const p1=this.exactMarkerMode?point(exact?.x1):nearestValid(this.series[id]||[],markerTimes.x1),p2=this.exactMarkerMode?point(exact?.x2):nearestValid(this.series[id]||[],markerTimes.x2),c1=this.markerCell(p1,markerTimes.x1),c2=this.markerCell(p2,markerTimes.x2);
      if(this.exactMarkerMode&&this.exactMarkerRows===null){c1.detail=c2.detail=I18N.t("Lettura dei campioni originali…");}
      if(this.markerError)c1.detail=c2.detail=I18N.message(this.markerError);
      const v1=p1?.[1],v2=p2?.[1],delta=typeof v1==='number'&&Number.isFinite(v1)&&typeof v2==='number'&&Number.isFinite(v2)?v2-v1:null;
      const tr=document.createElement('tr');
      const name=document.createElement('td'),nameWrap=document.createElement('span'),dot=document.createElement('i');dot.className='dot';dot.style.color=this.color(id);nameWrap.className='marker-signal';nameWrap.append(dot,document.createTextNode(node.display_name||id));name.append(nameWrap);
      const axis=document.createElement('td');I18N.bind(axis,"textContent",()=>(this.axisPlan.axisById.get(id)==='right'?'Y2':'Y1'),false);axis.className='marker-axis';
      const makeValueCell=data=>{const td=document.createElement('td'),strong=document.createElement('strong'),small=document.createElement('small');I18N.bind(strong,"textContent",()=>(data.value),false);I18N.bind(small,"textContent",()=>(data.detail),false);td.className='marker-reading';td.append(strong,small);return td;};
      const deltaCell=document.createElement('td');deltaCell.className='marker-reading marker-delta';const deltaStrong=document.createElement('strong');I18N.bind(deltaStrong,"textContent",()=>(delta===null?'—':valueText(delta)),false);deltaCell.append(deltaStrong);
      tr.append(name,axis,makeValueCell(c1),makeValueCell(c2),deltaCell);body.append(tr);
    }
  }
  plotPoint(event,clamp=true){
    if(!this.layout)return null;const r=this.canvas.getBoundingClientRect(),{p,w,h}=this.layout,raw={x:event.clientX-r.left,y:event.clientY-r.top};
    if(!clamp)return raw;return {x:Math.max(p.l,Math.min(w-p.r,raw.x)),y:Math.max(p.t,Math.min(h-p.b,raw.y))};
  }
  inPlot(pt){if(!pt||!this.layout)return false;const {p,w,h}=this.layout;return pt.x>=p.l&&pt.x<=w-p.r&&pt.y>=p.t&&pt.y<=h-p.b;}
  xTime(x){const b=this.domain,{p,w}=this.layout;return b.xmin+(x-p.l)/(w-p.l-p.r)*(b.xmax-b.xmin);}
  zoomStart(event){
    if(!this.domain||event.button!==undefined&&event.button!==0)return;
    const pt=this.plotPoint(event,false);if(!this.inPlot(pt))return;
    if(this.markerMode){this.setMarker(this.markerMode,this.xTime(pt.x));event.preventDefault();return;}
    const hit=['x1','x2'].find(k=>Number.isFinite(this.markers[k])&&Math.abs(pt.x-(this.layout.p.l+(this.markers[k]-this.domain.xmin)/(this.domain.xmax-this.domain.xmin)*(this.layout.w-this.layout.p.l-this.layout.p.r)))<=8);
    if(hit){this.markerDrag=hit;try{this.canvas.setPointerCapture?.(event.pointerId);}catch{}event.preventDefault();return;}
    this.drag={x0:pt.x,y0:pt.y,x1:pt.x,y1:pt.y,domain:this.domain};this.tooltip.hidden=true;
    if(event.pointerId!==undefined&&this.canvas.setPointerCapture){try{this.canvas.setPointerCapture(event.pointerId);}catch{}}
    event.preventDefault();this.draw();
  }
  pointerMove(event){
    if(this.markerDrag){const point=this.plotPoint(event);this.setMarker(this.markerDrag,this.xTime(point.x));event.preventDefault();return;}
    if(this.drag){const pt=this.plotPoint(event);this.drag.x1=pt.x;this.drag.y1=pt.y;this.tooltip.hidden=true;event.preventDefault();this.draw();return;}
    this.hover(event);
  }
  zoomEnd(event){
    if(this.markerDrag){const point=this.plotPoint(event);this.setMarker(this.markerDrag,this.xTime(point.x));this.markerDrag=null;try{this.canvas.releasePointerCapture?.(event.pointerId);}catch{}return;}
    if(!this.drag||!this.domain||!this.layout)return;
    const pt=this.plotPoint(event);this.drag.x1=pt.x;this.drag.y1=pt.y;const d=this.drag;this.drag=null;
    if(event.pointerId!==undefined&&this.canvas.releasePointerCapture){try{this.canvas.releasePointerCapture(event.pointerId);}catch{}}
    const dx=Math.abs(d.x1-d.x0),dy=Math.abs(d.y1-d.y0);if(dx<12||dy<12){this.draw();return;}
    const {p,w,h}=this.layout,b=d.domain;const xToT=x=>b.xmin+(x-p.l)/(w-p.l-p.r)*(b.xmax-b.xmin);
    const xa=xToT(Math.min(d.x0,d.x1)),xb=xToT(Math.max(d.x0,d.x1));const axes={};
    for(const [name,dom] of Object.entries(b.axes)){if(!dom)continue;const yToV=y=>dom.ymax-(y-p.t)/(h-p.t-p.b)*(dom.ymax-dom.ymin);axes[name]={ymin:yToV(Math.max(d.y0,d.y1)),ymax:yToV(Math.min(d.y0,d.y1))};}
    if(Number.isFinite(xa)&&Number.isFinite(xb)&&xb>xa&&Object.values(axes).every(a=>Number.isFinite(a.ymin)&&Number.isFinite(a.ymax)&&a.ymax>a.ymin)){
      this.zoom={xmin:xa,xmax:xb,axes};this.linkedRange=null;this.pauseForZoom();this.syncZoomControls();
    }
    this.draw();if(this.zoom)this.onViewChanged?.({xmin:this.zoom.xmin,xmax:this.zoom.xmax});
  }
  zoomCancel(event){this.markerDrag=null;if(!this.drag)return;this.drag=null;if(event.pointerId!==undefined&&this.canvas.releasePointerCapture){try{this.canvas.releasePointerCapture(event.pointerId);}catch{}}this.draw();}
  formatAxis(v){return Math.abs(v)>=1e6||(Math.abs(v)>0&&Math.abs(v)<.001)?v.toExponential(2):Number(v.toPrecision(5)).toLocaleString(I18N.locale());}
  planAxes(){
    // Keep each signal on its chosen Y scale while a live zoom fixes those limits.
    const plan=(this.liveZoom||this.frozen)&&this.zoom&&this.axisSignature?{...this.axisPlan,axisById:new Map(this.axisPlan.axisById)}:axisAssignment(this.series,this.nodes);
    for(const id of this.nodes.keys()){const axis=this.presentation[id]?.axis;if(axis==='left'||axis==='right')plan.axisById.set(id,axis);}
    plan.left=[...this.nodes.keys()].filter(id=>plan.axisById.get(id)!=='right');plan.right=[...this.nodes.keys()].filter(id=>plan.axisById.get(id)==='right');plan.dual=plan.right.length>0;return plan;
  }
  axisLabel(axis){const units=[...new Set([...this.nodes.keys()].filter(id=>(this.axisPlan.axisById.get(id)||'left')===axis).map(id=>this.presentation[id]?.unit).filter(Boolean))];const a=this.axisSettings[axis]||{};return I18N.join([{value:(axis==='left'?'Y1':'Y2')},{text:" "},{value:((a.unit||units.join(' / ')).slice(0,26))},{text:" · "},{value:(a.mode==='manual'?I18N.t("Manuale"):'Auto')}]);}
  draw(){
    const c=this.canvas,r=c.getBoundingClientRect(),dpr=window.devicePixelRatio||1;if(r.width<1||r.height<1)return;
    this.theme=window.studioTheme||{text:'#707E86',grid:'#E5E5E5',surface:'#FFFFFF'};c.width=Math.round(r.width*dpr);c.height=Math.round(r.height*dpr);const ctx=c.getContext('2d');if(!ctx)return;ctx.scale(dpr,dpr);
    const plan=this.planAxes(),axisSignature=[plan.dual?'2':'1',...[...plan.axisById.entries()].map(([id,a])=>id+':'+a)].join('|');
    if(this.axisSignature&&axisSignature!==this.axisSignature&&this.zoom){this.zoom=null;this.frozen=false;this.series=this.latestSeries;}
    this.axisSignature=axisSignature;this.axisPlan=plan;
    const w=r.width,h=r.height,p={l:78,r:82,t:22,b:42};this.layout={p,w,h};
    const scrollBar=this.el('timeScroll')?.closest?.('.time-scrollbar');if(scrollBar){scrollBar.style.marginLeft=p.l+'px';scrollBar.style.marginRight=p.r+'px';}
    const legendKey=JSON.stringify([...this.nodes].map(([id,n])=>[id,n.display_name,this.color(id),plan.axisById.get(id),this.presentation[id]?.unit]));
    if(this.legendKey!==legendKey){this.legendKey=legendKey;this.legend.replaceChildren();for(const [id,n] of this.nodes){const s=document.createElement('span'),dot=document.createElement('i'),tag=document.createElement('b');s.style.color=this.color(id);dot.className='dot';tag.className='axis-tag';I18N.bind(tag,"textContent",()=>(plan.axisById.get(id)==='right'?'Y2':'Y1'),false);s.append(dot,document.createTextNode((n.display_name||id)+(this.presentation[id]?.unit?' ['+this.presentation[id].unit+']':'')),tag);this.legend.append(s);}}
    ctx.fillStyle=this.theme.text;ctx.font='12px Segoe UI';const all=bounds(this.series);
    if(!all){this.crosshairEl.hidden=true;this.domain=null;this.fullDomain=null;this.syncZoomControls();this.syncTimeScroll();ctx.textAlign='center';ctx.fillText(I18N.message(this.emptyMessage),w/2,h/2);this.drawSelection(ctx);this.updateMarkerTable();return;}
    const leftB=bounds(this.series,plan.left)||all,rightB=plan.dual?(bounds(this.series,plan.right)||all):null;const asDomain=b=>b?{ymin:b.low*b.unit,ymax:b.high*b.unit}:null;
    const fullDomain={xmin:all.xmin,xmax:all.xmax,axes:{left:asDomain(leftB),right:asDomain(rightB)}};for(const axis of ['left','right']){const a=this.axisSettings[axis];if(a?.mode==='manual'&&Number.isFinite(a.min)&&Number.isFinite(a.max)&&a.max>a.min&&fullDomain.axes[axis])fullDomain.axes[axis]={ymin:a.min,ymax:a.max};}if(!this.frozen)this.lastDisplayRange=this.displayRange?{...this.displayRange}:null;const extent=this.frozen?this.lastDisplayRange:this.displayRange;if(extent&&extent.xmax>extent.xmin){fullDomain.xmin=extent.xmin;fullDomain.xmax=extent.xmax;}this.fullDomain=fullDomain;
    this.followLiveZoom(fullDomain);
    if(this.zoom&&!this.linkedRange&&(this.zoom.xmax<fullDomain.xmin||this.zoom.xmin>fullDomain.xmax)){this.zoom=null;this.frozen=false;this.series=this.latestSeries;}
    if(this.zoom&&plan.dual&&!this.zoom.axes?.right){this.zoom=null;this.frozen=false;this.series=this.latestSeries;}
    const b=this.drag?.domain?{...this.drag.domain}:this.zoom?{xmin:this.zoom.xmin,xmax:this.zoom.xmax,axes:this.zoom.axes}:{...fullDomain};if(this.linkedRange&&!this.drag){b.xmin=this.linkedRange.xmin;b.xmax=this.linkedRange.xmax;}this.domain=b;
    if(!this.staticMode&&!this.frozen){
      for(const key of ['x1','x2']){
        if(!Number.isFinite(this.markers[key])){this.markerFractions[key]=null;continue;}
        if(!Number.isFinite(this.markerFractions[key]))this.markerFractions[key]=Math.max(0,Math.min(1,(this.markers[key]-b.xmin)/(b.xmax-b.xmin)));
        this.markers[key]=b.xmin+this.markerFractions[key]*(b.xmax-b.xmin);
      }
      this.onMarkerFrame?.(this.markers);
    }
    this.syncZoomControls();this.syncTimeScroll();
    const X=t=>p.l+(t-b.xmin)/(b.xmax-b.xmin)*(w-p.l-p.r);const Y=(v,axis)=>{const d=b.axes[axis]||b.axes.left;return p.t+(d.ymax-v)/(d.ymax-d.ymin)*(h-p.t-p.b);};
    ctx.lineWidth=1;ctx.strokeStyle=this.theme.grid;
    for(let i=0;i<=5;i++){const y=p.t+i*(h-p.t-p.b)/5;ctx.beginPath();ctx.moveTo(p.l,y);ctx.lineTo(w-p.r,y);ctx.stroke();const lv=b.axes.left.ymax-i*(b.axes.left.ymax-b.axes.left.ymin)/5;ctx.fillStyle=this.theme.text;ctx.textAlign='right';ctx.fillText(this.formatAxis(lv),p.l-9,y+4);if(plan.dual&&b.axes.right){const rv=b.axes.right.ymax-i*(b.axes.right.ymax-b.axes.right.ymin)/5;ctx.textAlign='left';ctx.fillText(this.formatAxis(rv),w-p.r+9,y+4);}}
    ctx.font='600 10px Segoe UI';ctx.fillStyle=this.theme.text;ctx.textAlign='left';ctx.fillText(this.axisLabel('left'),p.l,p.t-8);if(plan.dual){ctx.textAlign='right';ctx.fillText(this.axisLabel('right'),w-p.r,p.t-8);}
    ctx.save();ctx.beginPath();ctx.rect(p.l,p.t,w-p.l-p.r,h-p.t-p.b);ctx.clip();
    this.drawSeriesLayer(ctx,plan,X,Y,b,p,w,h,dpr);
    this.drawMarkers(ctx,X,p,w,h);ctx.restore();ctx.fillStyle=this.theme.text;ctx.font='11px Segoe UI';
    for(let i=0;i<=4;i++){const t=b.xmin+i*(b.xmax-b.xmin)/4;ctx.textAlign=i===0?'left':i===4?'right':'center';const dt=new Date(t);ctx.fillText(dt.toLocaleTimeString(I18N.locale(),{hour:'2-digit',minute:'2-digit',second:'2-digit'}),X(t),h-14);}
    this.drawSelection(ctx);this.updateMarkerTable();this.renderCrosshair();
  }
  /** Segnali e fascia qualità. Con dati di file lo strato resta in cache finché vista, dimensioni e dati non cambiano
   * (selezione zoom, marker e cursore non ridisegnano milioni di campioni). */
  drawSeriesLayer(ctx,plan,X,Y,b,p,w,h,dpr){
    const packed=this.staticMode&&Object.values(this.series).some(isSampleSeries);
    if(!packed){this.seriesLayer=null;this.paintSeries(ctx,plan,X,Y,b,p,w,h,dpr);return;}
    Trend.layerIds=Trend.layerIds||new WeakMap();Trend.layerCounter=Trend.layerCounter||0;
    const identity=points=>{if(!points||typeof points!=='object')return '0';if(!Trend.layerIds.has(points))Trend.layerIds.set(points,++Trend.layerCounter);return Trend.layerIds.get(points)+':'+points.length;};
    const key=JSON.stringify([w,h,dpr,b.xmin,b.xmax,b.axes,Object.entries(this.series).map(([id,points])=>[id,identity(points),this.color(id),this.presentation[id]?.width,this.types[id],plan.axisById.get(id)])]);
    if(this.seriesLayer?.key!==key){
      const layer=document.createElement('canvas');layer.width=Math.round(w*dpr);layer.height=Math.round(h*dpr);const lctx=layer.getContext('2d');
      if(!lctx){this.paintSeries(ctx,plan,X,Y,b,p,w,h,dpr);return;}
      lctx.scale(dpr,dpr);lctx.beginPath();lctx.rect(p.l,p.t,w-p.l-p.r,h-p.t-p.b);lctx.clip();this.paintSeries(lctx,plan,X,Y,b,p,w,h,dpr);
      this.seriesLayer={key,layer};
    }
    ctx.drawImage(this.seriesLayer.layer,0,0,w,h);
  }
  paintSeries(ctx,plan,X,Y,b,p,w,h,dpr){
    for(const [id,points] of Object.entries(this.series)){const axis=plan.axisById.get(id)||'left';ctx.strokeStyle=this.color(id);ctx.fillStyle=this.color(id);ctx.lineWidth=this.presentation[id]?.width||1.8;
      if(isSampleSeries(points)){this.drawSamples(ctx,points,X,v=>Y(v,axis),this.types[id]==='Boolean',b,Math.max(1,Math.round((w-p.l-p.r)*dpr)));continue;}
      ctx.beginPath();let last=null,finite=0,first=null;
      for(const point of points){const [t,v]=point;if(v===null||!Number.isFinite(v)||!Number.isFinite(t)){last=null;continue;}const x=X(t),y=Y(v,axis);if(last){if(this.types[id]==='Boolean')ctx.lineTo(x,last[1]);ctx.lineTo(x,y);}else ctx.moveTo(x,y);last=[x,y];first=first||last;finite++;}
      ctx.stroke();if(finite===1&&first){ctx.beginPath();ctx.arc(first[0],first[1],3,0,Math.PI*2);ctx.fill();}
    }
    // Quality stripe: bad/uncertain intervals are never joined by a numerical line.
    for(const points of Object.values(this.series))if(isSampleSeries(points))this.drawSampleQuality(ctx,points,X,b,h-p.b-7);else for(let i=0;i<points.length;i++){
      const q=points[i][2];if(q!=='Bad'&&q!=='Uncertain')continue;
      const x=X(points[i][0]),next=X(points[i+1]?.[0]??points[i][0]);ctx.fillStyle=q==='Bad'?'#E53C1470':'#F58C3A90';ctx.fillRect(x,h-p.b-7,Math.max(2,next-x),7);
    }
  }
  /** Disegno a piena risoluzione: segmenti reali se i campioni visibili sono pochi, altrimenti min–max per colonna di pixel. */
  drawSamples(ctx,s,X,Y,step,b,columns){
    const t=s.t,v=s.v,n=t.length;if(!n)return;
    const i0=Math.max(0,s.lowerBound(b.xmin)-1),i1=Math.min(n,s.upperBound(b.xmax)+1),span=b.xmax-b.xmin;
    ctx.beginPath();let pen=false,lastY=0,finite=0,fx=0,fy=0;
    const to=(x,y)=>{if(pen){if(step)ctx.lineTo(x,lastY);ctx.lineTo(x,y);}else{ctx.moveTo(x,y);pen=true;}lastY=y;};
    if(i1-i0<=columns*4){
      for(let i=i0;i<i1;i++){const value=v[i];if(!Number.isFinite(value)){pen=false;continue;}const x=X(t[i]),y=Y(value);to(x,y);if(!finite){fx=x;fy=y;}finite++;}
    }else{
      // Più campioni che pixel: per ogni colonna di pixel il tratto verticale dal minimo al massimo dei campioni,
      // esteso all'ultimo valore della colonna precedente. È ciò che la linea completa copre in quella colonna.
      const lw=ctx.lineWidth||1;let col=NaN,lo=0,hi=0,last=0,prev=NaN;
      const flush=()=>{
        if(col!==col)return;let a=lo,z=hi;if(prev===prev){if(prev<a)a=prev;if(prev>z)z=prev;}
        const x=X(b.xmin+(col+.5)/columns*span),y1=Y(z),y2=Y(a),top=Math.min(y1,y2);
        ctx.fillRect(x-lw/2,top-lw/2,lw,Math.abs(y2-y1)+lw);prev=last;col=NaN;finite+=2;
      };
      for(let i=i0;i<i1;i++){
        const value=v[i];if(!Number.isFinite(value)){flush();prev=NaN;continue;}
        const c=Math.floor((t[i]-b.xmin)/span*columns);
        if(c!==col){flush();col=c;lo=hi=last=value;continue;}
        if(value<lo)lo=value;if(value>hi)hi=value;last=value;
      }
      flush();
    }
    ctx.stroke();if(finite===1){ctx.beginPath();ctx.arc(fx,fy,3,0,Math.PI*2);ctx.fill();}
  }
  /** Fascia qualità: intervalli contigui Bad/Uncertain uniti in un solo rettangolo. */
  drawSampleQuality(ctx,s,X,b,y){
    const t=s.t,q=s.q,n=t.length;if(!n||!(s.stats.bad||s.stats.uncertain))return;
    const i0=Math.max(0,s.lowerBound(b.xmin)-1),i1=Math.min(n,s.upperBound(b.xmax)+1);
    for(let i=i0;i<i1;i++){
      const code=q[i];if(code!==1&&code!==2)continue;let j=i;while(j+1<i1&&q[j+1]===code)j++;
      const x=X(t[i]),next=X(j+1<n?t[j+1]:t[j]);ctx.fillStyle=code===2?'#E53C1470':'#F58C3A90';ctx.fillRect(x,y,Math.max(2,next-x),7);i=j;
    }
  }
  drawMarkers(ctx,X,p,w,h){
    for(const [which,label,color] of [['x1','X1','#006EFF'],['x2','X2','#F58C3A']]){
      const t=this.markers[which];if(!Number.isFinite(t)||!this.domain||t<this.domain.xmin||t>this.domain.xmax)continue;const x=X(t);
      ctx.save();ctx.strokeStyle=color;ctx.fillStyle=color;ctx.lineWidth=1.6;ctx.setLineDash([7,4]);ctx.beginPath();ctx.moveTo(x,p.t);ctx.lineTo(x,h-p.b);ctx.stroke();ctx.setLineDash([]);
      ctx.font='700 11px Segoe UI';const tw=ctx.measureText?ctx.measureText(label).width:16;const boxX=Math.max(p.l,Math.min(w-p.r-tw-12,x-tw/2-6));ctx.fillRect(boxX,p.t+4,tw+12,20);ctx.fillStyle='white';ctx.textAlign='left';ctx.fillText(label,boxX+6,p.t+18);ctx.restore();
    }
  }
  drawSelection(ctx){if(!this.drag)return;const x=Math.min(this.drag.x0,this.drag.x1),y=Math.min(this.drag.y0,this.drag.y1),w=Math.abs(this.drag.x1-this.drag.x0),h=Math.abs(this.drag.y1-this.drag.y0);ctx.save();ctx.fillStyle='rgba(20,103,213,.12)';ctx.strokeStyle='rgba(20,103,213,.85)';ctx.lineWidth=1.5;ctx.setLineDash([6,4]);ctx.fillRect(x,y,w,h);ctx.strokeRect(x+.5,y+.5,Math.max(0,w-1),Math.max(0,h-1));ctx.restore();}
  hover(event){
    const b=this.domain;if(!b||this.drag||this.markerMode)return;const r=this.canvas.getBoundingClientRect(),{p,w,h}=this.layout,x=event.clientX-r.left,y=event.clientY-r.top;
    if(x<p.l||x>w-p.r||y<p.t||y>h-p.b){this.tooltip.hidden=true;this.onHover?.(null);return;}const t=b.xmin+(x-p.l)/(w-p.l-p.r)*(b.xmax-b.xmin);
    this.onHover?.(t);this.showHover(t);
  }
  renderCrosshair(){
    const b=this.domain,layout=this.layout,t=this.crosshair;
    if(!Number.isFinite(t)||!b||!layout||t<b.xmin||t>b.xmax){this.crosshairEl.hidden=true;return;}
    const {p,w,h}=layout;this.crosshairEl.style.left=(p.l+(t-b.xmin)/(b.xmax-b.xmin)*(w-p.l-p.r))+'px';this.crosshairEl.style.top=p.t+'px';this.crosshairEl.style.height=(h-p.t-p.b)+'px';this.crosshairEl.hidden=false;
  }
  showHover(t){
    if(!Number.isFinite(t)){this.tooltip.hidden=true;return;}
    this.tooltip.replaceChildren();const title=document.createElement('strong');I18N.bind(title,"textContent",()=>(new Date(t).toLocaleTimeString(I18N.locale(),{fractionalSecondDigits:3})),true);this.tooltip.append(title);
    let count=0;for(const [id,points] of Object.entries(this.series)){if(count++>=12)break;const point=nearest(points,t),pp=document.createElement('p'),axis=this.axisPlan.axisById.get(id)==='right'?'Y2':'Y1';I18N.bind(pp,"textContent",()=>(I18N.join([{value:(this.nodes.get(id)?.display_name||id)},{text:" ["},{value:(axis)},{text:"]: "},{value:(valueText(point?.[1]))},{value:(this.presentation[id]?.unit?' '+this.presentation[id].unit:'')},{text:" · "},{value:(point?.[2]||'Good')},{value:(point&&Math.abs(point[0]-t)>1000?I18N.t(" (campione distante dal cursore)"):'')}])),true);this.tooltip.append(pp);}this.tooltip.hidden=false;
  }
}
