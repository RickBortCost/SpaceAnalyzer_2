/* OPC UA Web Client V1.1.15
 * Script classico: nessuna dipendenza ES-module/.mjs o CDN.
 * Trend con doppia scala Y automatica quando i segnali hanno ordini di grandezza molto diversi.
 */
/* Serie a piena risoluzione (file e Live): colonne tipizzate (tempo, valore, qualità) con tutti i campioni.
 * Quando i campioni visibili superano i pixel, ogni colonna di pixel mostra l'intervallo minimo–massimo di tutti
 * i suoi campioni (la stessa copertura della linea completa); con lo zoom compaiono i singoli campioni.
 * Nessun campione viene scartato. */
const SAMPLE_QUALITIES=['Good','Uncertain','Bad'];
function sampleStats(t,v,q){
  let xmin=Infinity,xmax=-Infinity,ymin=Infinity,ymax=-Infinity,count=0;const counts=[0,0,0];
  for(let i=0;i<t.length;i++){const time=t[i],value=v[i];counts[q[i]]=(counts[q[i]]||0)+1;if(!Number.isFinite(time))continue;if(time<xmin)xmin=time;if(time>xmax)xmax=time;if(value===value&&value!==Infinity&&value!==-Infinity){if(value<ymin)ymin=value;if(value>ymax)ymax=value;count++;}}
  return {xmin,xmax,ymin,ymax,count,good:counts[0],uncertain:counts[1],bad:counts[2]};
}
class SampleSeries{
  constructor(t,v,q){this._t=t;this._v=v;this._q=q;this._stats=null;this.version=0;}
  get t(){return this._t;}
  get v(){return this._v;}
  get q(){return this._q;}
  get stats(){if(!this._stats)this._stats=sampleStats(this.t,this.v,this.q);return this._stats;}
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
  /** Vista immutabile dei campioni con tempo > t (grafico Live "Pulisci"). */
  after(t){const i=this.upperBound(t);return new SampleSeries(this.t.subarray(i),this.v.subarray(i),this.q.subarray(i));}
  /** Copia di sola lettura per la pausa: i campioni successivi non la modificano. */
  snapshot(){return this;}
}
/* Serie Live che cresce a ogni aggiornamento: ogni campione porta la sequenza del flusso del suo segnale. */
class LiveSeries extends SampleSeries{
  constructor(){super(null,null,null);this.cap=0;this.start=0;this.end=0;this.buffer=null;this.views=null;this.bt=this.bv=this.bs=new Float64Array(0);this.bq=new Uint8Array(0);}
  view(){if(!this.views)this.views={t:this.bt.subarray(this.start,this.end),v:this.bv.subarray(this.start,this.end),q:this.bq.subarray(this.start,this.end),s:this.bs.subarray(this.start,this.end)};return this.views;}
  get t(){return this.view().t;}
  get v(){return this.view().v;}
  get q(){return this.view().q;}
  get seq(){return this.view().s;}
  get lastSeq(){return this.end>this.start?this.bs[this.end-1]:0;}
  get firstSeq(){return this.end>this.start?this.bs[this.start]:0;}
  touch(){this.views=null;this._stats=null;this.version++;}
  clear(){this.start=this.end=0;this.touch();}
  // Fresh arrays when growing or compacting: snapshots (pause) keep their own view untouched.
  reserve(extra){
    const used=this.end-this.start,need=used+extra;if(this.end+extra<=this.cap)return;
    const cap=Math.max(1024,need*2),copy=(Type,from)=>{const to=new Type(cap);to.set(from.subarray(this.start,this.end));return to;};
    this.bt=copy(Float64Array,this.bt);this.bv=copy(Float64Array,this.bv);this.bq=copy(Uint8Array,this.bq);this.bs=copy(Float64Array,this.bs);this.cap=cap;this.start=0;this.end=used;
  }
  append(s,t,v,q){const n=t.length;if(!n)return;if(this.revised){this.cap=0;this.revised=false;}this.reserve(n);this.bs.set(s,this.end);this.bt.set(t,this.end);this.bv.set(v,this.end);this.bq.set(q,this.end);this.end+=n;this.touch();}
  prepend(s,t,v,q){
    const n=t.length;if(!n)return;const used=this.end-this.start,cap=Math.max(1024,(n+used)*2),join=(Type,head,tail)=>{const to=new Type(cap);to.set(head);to.set(tail.subarray(this.start,this.end),n);return to;};
    this.bs=join(Float64Array,s,this.bs);this.bt=join(Float64Array,t,this.bt);this.bv=join(Float64Array,v,this.bv);this.bq=join(Uint8Array,q,this.bq);this.cap=cap;this.start=0;this.end=n+used;this.touch();
  }
  // The revision is written into fresh arrays, so a paused chart keeps the value it showed.
  dropLast(){if(this.end>this.start){this.end--;this.revised=true;this.touch();}}
  /** Rimuove i campioni con sequenza < seq (finestra Live: rispecchia il FIFO del servizio). */
  dropBefore(seq){let l=this.start,r=this.end;while(l<r){const m=(l+r)>>1;if(this.bs[m]<seq)l=m+1;else r=m;}if(l!==this.start){this.start=l;this.touch();}}
  keepLast(count){if(this.end-this.start>count){this.start=this.end-count;this.touch();}}
  snapshot(){const v=this.view();return new SampleSeries(v.t,v.v,v.q);}
}
function isSampleSeries(points){return points instanceof SampleSeries;}
window.SampleSeries=SampleSeries;window.LiveSeries=LiveSeries;
/** Risposta binaria di /api/recordings/samples e /api/live/*: intestazione JSON e, per segnale, colonne
 * Float64 sequenza (solo Live), Float64 tempo, Float64 valore, Uint8 qualità. */
function decodeSampleBuffer(buffer){
  const view=new DataView(buffer),magic=String.fromCharCode(view.getUint8(0),view.getUint8(1),view.getUint8(2),view.getUint8(3));
  if(magic!=='SAS1')throw new Error(I18N.t("Risposta del servizio locale non valida"));
  const length=view.getUint32(4,true),header=JSON.parse(new TextDecoder().decode(new Uint8Array(buffer,8,length)));
  const align=n=>n+((8-n%8)%8);let offset=align(8+length);const series={},columns={};
  for(const signal of header.signals||[]){
    const n=Number(signal.points_returned||0);let s=null;
    if(header.with_seq){s=new Float64Array(buffer,offset,n);offset=align(offset+n*8);}
    const t=new Float64Array(buffer,offset,n);offset=align(offset+n*8);
    const v=new Float64Array(buffer,offset,n);offset=align(offset+n*8);
    const q=new Uint8Array(buffer,offset,n);offset=align(offset+n);
    series[signal.nodeid]=new SampleSeries(t,v,q);columns[signal.nodeid]={s,t,v,q};
  }
  header.series=series;header.columns=columns;return header;
}
/* Palette dei segnali: tinte ben distinte; nel tema scuro versioni più luminose per il contrasto con lo sfondo. */
const TREND_PALETTE_LIGHT=['#006EFF','#F58C3A','#0AAF96','#8A4FD8','#D6336C','#4E9A06','#B8860B','#505A60'];
const TREND_PALETTE_DARK=['#5AA9FF','#FFA552','#2ED3B7','#B48CFF','#FF6B8B','#9BE15D','#FFD43B','#C9D1D9'];
const TREND_PALETTE_LEGACY=['#006EFF','#F58C3A','#0AAF96','#707E86','#004299','#A14C12','#006B5C','#505A60'];
function trendDark(){return typeof document!=='undefined'&&document.documentElement?.dataset?.theme==='dark';}
function trendColor(stored){
  let index=-1;
  if(typeof stored==='string'&&stored.startsWith('palette:'))index=Number(stored.slice(8));
  else{const legacy=TREND_PALETTE_LEGACY.indexOf(String(stored).toUpperCase());if(legacy<0)return stored;index=legacy;}
  const palette=trendDark()?TREND_PALETTE_DARK:TREND_PALETTE_LIGHT;
  return palette[index]||`hsl(${(index*137.5)%360} 70% ${trendDark()?'64%':'40%'})`;
}
/** Nome breve di un segnale per legenda e tooltip: alias, oppure l'ultimo elemento del percorso dei simboli PLC. */
function signalShortName(id,node,presentation){
  if(presentation?.alias)return presentation.alias;
  const name=String(node?.display_name||id||'');
  if(/^(ads|s7sym|s7symbolic):/.test(String(id))||(name.split('.').length>2&&!/\s/.test(name))){const tail=name.slice(name.lastIndexOf('.')+1);return tail||name;}
  return name;
}
/** Passo "tondo" (1, 2, 2,5, 5 × 10^n) per circa target intervalli. */
function niceStep(span,target){
  if(!(span>0)||!Number.isFinite(span))return 1;
  const raw=span/Math.max(1,target),power=Math.pow(10,Math.floor(Math.log10(raw))),unit=raw/power;
  return (unit<=1?1:unit<=2?2:unit<=2.5?2.5:unit<=5?5:10)*power;
}
function niceTicks(min,max,target=5){
  const step=niceStep(max-min,target),first=Math.ceil(min/step-1e-9)*step,ticks=[];
  for(let v=first;v<=max+step*1e-9&&ticks.length<50;v+=step)ticks.push(Math.abs(v)<step*1e-9?0:v);
  return {ticks,step};
}
/** Tacche dell'asse del tempo allineate all'ora locale; i millisecondi compaiono quando il passo è sotto il secondo. */
const TIME_STEPS=[1,2,5,10,20,50,100,200,500,1e3,2e3,5e3,1e4,15e3,3e4,6e4,12e4,3e5,6e5,9e5,18e5,36e5,72e5,108e5,216e5,432e5,864e5];
function timeTicks(xmin,xmax,width){
  const span=xmax-xmin,target=Math.max(2,Math.floor(width/120));
  const step=TIME_STEPS.find(s=>span/s<=target)||TIME_STEPS[TIME_STEPS.length-1];
  const offset=-new Date(xmin).getTimezoneOffset()*60000,ticks=[];
  for(let t=Math.ceil((xmin+offset)/step)*step-offset;t<=xmax&&ticks.length<200;t+=step)ticks.push(t);
  return {ticks,step};
}
function timeLabel(t,step){
  const d=new Date(t),pad=(v,n=2)=>String(v).padStart(n,'0'),hm=pad(d.getHours())+':'+pad(d.getMinutes());
  if(step>=6e4)return step>=864e5?d.toLocaleDateString(I18N.locale(),{day:'2-digit',month:'2-digit'}):hm;
  const hms=hm+':'+pad(d.getSeconds());
  return step>=1e3?hms:hms+'.'+pad(d.getMilliseconds(),3);
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
    this.axisPlan={groups:false,dual:false,axisById:new Map(),left:[],right:[],keys:['left'],members:new Map(),side:new Map([['left','left']]),ratio:1};this.axisSignature='';this.scaleState={mode:'legacy'};
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
    listen(canvas,'dblclick',e=>{const hit=this.axisAt(e);if(hit)this.resetScale(hit.key);else this.resetZoom();});
    listen(canvas,'wheel',e=>this.axisWheel(e));
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
  // Colors are stored as palette positions, so each theme draws its own readable, well separated hues.
  color(id){
    if(this.presentation?.[id]?.color)return this.presentation[id].color;
    if(!this.colors.has(id))this.colors.set(id,'palette:'+this.colors.size);
    return trendColor(this.colors.get(id));
  }
  cloneSeries(series){return Object.fromEntries(Object.entries(series).map(([id,points])=>[id,isSampleSeries(points)?points.snapshot():(points||[]).map(p=>[...p])]));}
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
      const name=document.createElement('td'),nameWrap=document.createElement('span'),dot=document.createElement('i');dot.className='dot';dot.style.color=this.color(id);nameWrap.className='marker-signal';nameWrap.title=node.display_name||id;nameWrap.append(dot,document.createTextNode(signalShortName(id,node,this.presentation[id])));name.append(nameWrap);
      const axis=document.createElement('td');I18N.bind(axis,"textContent",()=>(this.axisTag(id)),false);axis.className='marker-axis';
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
  // ---- Zoom e spostamento di una sola scala: rotella sull'asse (Maiusc = sposta), trascinamento sull'asse, doppio clic = automatica ----
  axisAt(event){
    if(!this.layout||!this.domain||!this.axisPlan?.keys)return null;
    const pt=this.plotPoint(event,false),{p,w,h}=this.layout;if(!pt||pt.y<p.t||pt.y>h-p.b)return null;
    const COL=Trend.AXIS_COLUMN,plan=this.axisPlan,list=side=>plan.keys.filter(k=>(plan.side.get(k)==='right'?'right':'left')===side).slice(0,Trend.AXIS_COLUMNS);
    if(pt.x<p.l){const key=list('left')[Math.floor((p.l-pt.x)/COL)];return key?{key,side:'left'}:null;}
    if(pt.x>w-p.r){const key=list('right')[Math.floor((pt.x-(w-p.r))/COL)];return key?{key,side:'right'}:null;}
    return null;
  }
  scaleRange(key){const d=this.zoom?.axes?.[key]||this.domain?.axes?.[key];return d?{min:d.ymin,max:d.ymax}:null;}
  setScaleRange(key,min,max){
    if(!(Number.isFinite(min)&&Number.isFinite(max)&&max>min))return false;
    if(this.zoom?.axes)this.zoom={...this.zoom,axes:{...this.zoom.axes,[key]:{ymin:min,ymax:max}}};
    else this.axisSettings[key]={...(this.axisSettings[key]||{}),mode:'manual',min,max};
    this.tooltip.hidden=true;this.draw();clearTimeout(this.scaleTimer);this.scaleTimer=setTimeout(()=>this.onScaleChanged?.(),250);return true;
  }
  resetScale(key){
    if(this.zoom?.axes&&this.fullDomain?.axes?.[key])this.zoom={...this.zoom,axes:{...this.zoom.axes,[key]:{...this.fullDomain.axes[key]}}};
    else if(this.axisSettings[key])this.axisSettings[key]={...this.axisSettings[key],mode:'auto'};
    this.draw();clearTimeout(this.scaleTimer);this.scaleTimer=setTimeout(()=>this.onScaleChanged?.(),250);
  }
  axisWheel(event){
    const hit=this.axisAt(event),r=hit&&this.scaleRange(hit.key);if(!r)return;
    event.preventDefault();const {p,h}=this.layout,pt=this.plotPoint(event,false),span=r.max-r.min,frac=Math.max(0,Math.min(1,(pt.y-p.t)/(h-p.t-p.b)));
    if(event.shiftKey){const shift=span*.1*(event.deltaY>0?-1:1);this.setScaleRange(hit.key,r.min+shift,r.max+shift);return;}
    const at=r.max-frac*span,next=span*(event.deltaY<0?1/1.2:1.2);this.setScaleRange(hit.key,at-(1-frac)*next,at+frac*next);
  }
  zoomStart(event){
    if(!this.domain||event.button!==undefined&&event.button!==0)return;
    const hitAxis=this.axisAt(event);if(hitAxis&&this.scaleRange(hitAxis.key)){this.axisDrag={key:hitAxis.key,y0:event.clientY,range:this.scaleRange(hitAxis.key)};this.tooltip.hidden=true;if(event.pointerId!==undefined&&this.canvas.setPointerCapture){try{this.canvas.setPointerCapture(event.pointerId);}catch{}}event.preventDefault();return;}
    const pt=this.plotPoint(event,false);if(!this.inPlot(pt))return;
    if(this.markerMode){this.setMarker(this.markerMode,this.xTime(pt.x));event.preventDefault();return;}
    const hit=['x1','x2'].find(k=>Number.isFinite(this.markers[k])&&Math.abs(pt.x-(this.layout.p.l+(this.markers[k]-this.domain.xmin)/(this.domain.xmax-this.domain.xmin)*(this.layout.w-this.layout.p.l-this.layout.p.r)))<=8);
    if(hit){this.markerDrag=hit;try{this.canvas.setPointerCapture?.(event.pointerId);}catch{}event.preventDefault();return;}
    this.drag={x0:pt.x,y0:pt.y,x1:pt.x,y1:pt.y,domain:this.domain};this.tooltip.hidden=true;
    if(event.pointerId!==undefined&&this.canvas.setPointerCapture){try{this.canvas.setPointerCapture(event.pointerId);}catch{}}
    event.preventDefault();this.draw();
  }
  pointerMove(event){
    if(this.axisDrag){const d=this.axisDrag,{p,h}=this.layout,shift=(event.clientY-d.y0)/(h-p.t-p.b)*(d.range.max-d.range.min);event.preventDefault();this.setScaleRange(d.key,d.range.min+shift,d.range.max+shift);return;}
    if(this.markerDrag){const point=this.plotPoint(event);this.setMarker(this.markerDrag,this.xTime(point.x));event.preventDefault();return;}
    if(this.drag){const pt=this.plotPoint(event);this.drag.x1=pt.x;this.drag.y1=pt.y;this.tooltip.hidden=true;event.preventDefault();this.draw();return;}
    this.canvas.style.cursor=this.axisAt(event)?'ns-resize':'';this.hover(event);
  }
  zoomEnd(event){
    if(this.axisDrag){this.axisDrag=null;if(event.pointerId!==undefined&&this.canvas.releasePointerCapture){try{this.canvas.releasePointerCapture(event.pointerId);}catch{}}return;}
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
  zoomCancel(event){this.markerDrag=null;this.axisDrag=null;if(!this.drag)return;this.drag=null;if(event.pointerId!==undefined&&this.canvas.releasePointerCapture){try{this.canvas.releasePointerCapture(event.pointerId);}catch{}}this.draw();}
  formatAxis(v,step){
    if(Math.abs(v)>=1e7||(Math.abs(v)>0&&Math.abs(v)<1e-4))return v.toExponential(2);
    if(!(step>0))return Number(v.toPrecision(5)).toLocaleString(I18N.locale());
    let decimals=0;for(let s=step;decimals<6&&Math.abs(s-Math.round(s))>1e-9*Math.max(1,Math.abs(s));s*=10)decimals++;
    return v.toLocaleString(I18N.locale(),{minimumFractionDigits:decimals,maximumFractionDigits:decimals});
  }
  // ---- Gruppi di scale Y: solo livello di visualizzazione, i campioni e i buffer non vengono mai toccati ----
  groupsMode(){return this.scaleState?.mode==='groups';}
  unitOf(id){return String(this.presentation[id]?.unit??this.nodes.get(id)?.unit??'').trim();}
  axisTag(id){const plan=this.axisPlan,key=plan.axisById.get(id);if(!plan.groups)return key==='right'?'Y2':'Y1';return 'Y'+(Math.max(0,plan.keys.indexOf(key))+1);}
  groupColor(plan,key){return 'hsl('+((Math.max(0,plan.keys.indexOf(key))*67+205)%360)+',68%,'+(trendDark()?'62%':'42%')+')';}
  planAxes(){
    const ids=[...this.nodes.keys()];
    if(this.groupsMode()){
      const axisById=new Map(),members=new Map(),side=new Map();
      for(const id of ids){const sc=this.presentation[id]?.scale,key=typeof sc==='string'&&sc?sc:'s:'+id;axisById.set(id,key);if(!members.has(key))members.set(key,[]);members.get(key).push(id);}
      let auto=0;
      for(const [key,list] of members){const explicit=list.map(id=>this.presentation[id]?.axis).find(a=>a==='left'||a==='right');side.set(key,explicit||(key==='right'?'right':key==='left'?'left':(auto++%2?'right':'left')));}
      const leftIds=ids.filter(id=>side.get(axisById.get(id))!=='right'),rightIds=ids.filter(id=>side.get(axisById.get(id))==='right');
      return {groups:true,dual:rightIds.length>0,axisById,members,side,keys:[...members.keys()],left:leftIds,right:rightIds,ratio:1};
    }
    // Keep each signal on its chosen Y scale while a live zoom fixes those limits.
    const plan=(this.liveZoom||this.frozen)&&this.zoom&&this.axisSignature?{...this.axisPlan,axisById:new Map(this.axisPlan.axisById)}:axisAssignment(this.series,this.nodes);
    for(const id of ids){const axis=this.presentation[id]?.axis;if(axis==='left'||axis==='right')plan.axisById.set(id,axis);}
    plan.left=ids.filter(id=>plan.axisById.get(id)!=='right');plan.right=ids.filter(id=>plan.axisById.get(id)==='right');plan.dual=plan.right.length>0;
    plan.groups=false;plan.keys=plan.dual?['left','right']:['left'];plan.members=new Map([['left',plan.left],['right',plan.right]]);plan.side=new Map([['left','left'],['right','right']]);
    return plan;
  }
  axisLabel(key){const plan=this.axisPlan,list=plan.members.get(key)||[],units=[...new Set(list.map(id=>this.presentation[id]?.unit).filter(Boolean))];const a=this.axisSettings[key]||{};return I18N.join([{value:(plan.groups?'Y'+(plan.keys.indexOf(key)+1):key==='left'?'Y1':'Y2')},{text:" "},{value:((a.unit||units.join(' / ')).slice(0,26))},{text:" · "},{value:(a.mode==='manual'?I18N.t("Manuale"):'Auto')}]);}
  // Passa dalla modalità storica (Y1/Y2 automatici) ai gruppi di scala congelando l'assegnazione corrente.
  ensureGroupMode(){
    if(this.groupsMode())return;
    const plan=this.axisPlan;this.scaleState=this.scaleState||{};this.scaleState.mode='groups';
    for(const id of this.nodes.keys()){const cur=this.presentation[id]||{},key=plan.axisById.get(id)||'left';this.presentation[id]={...cur,scale:key,axis:cur.axis==='left'||cur.axis==='right'?cur.axis:key};}
  }
  nextScaleId(){let n=0;for(const v of Object.values(this.presentation)){const m=/^g(\d+)$/.exec(v?.scale||'');if(m)n=Math.max(n,+m[1]);}return 'g'+(n+1);}
  freezeSides(plan){for(const id of this.nodes.keys())this.presentation[id]={...(this.presentation[id]||{}),axis:plan.side.get(plan.axisById.get(id))||'left'};}
  pruneScaleSettings(){const used=new Set(Object.values(this.presentation).map(v=>v?.scale).filter(Boolean));for(const key of Object.keys(this.axisSettings))if(/^g\d+$/.test(key)&&!used.has(key))delete this.axisSettings[key];}
  scaleChanged(){this.tooltip.hidden=true;this.draw();this.onScaleChanged?.();}
  // Il segnale trascinato passa al gruppo del bersaglio; gli altri membri del gruppo d'origine restano invariati.
  async joinScale(dragId,targetId,options={}){
    if(!dragId||!targetId||dragId===targetId||!this.nodes.has(dragId)||!this.nodes.has(targetId))return false;
    const before=this.groupsMode()?this.planAxes():this.axisPlan;
    if(before.axisById.get(dragId)===before.axisById.get(targetId))return false;
    const unitsOf=key=>new Set((before.members.get(key)||[]).map(id=>this.unitOf(id).toLowerCase()).filter(Boolean)),dragUnits=unitsOf(before.axisById.get(dragId)),targetUnits=unitsOf(before.axisById.get(targetId));
    // Si chiede conferma solo quando il gruppo di destinazione riceve un'unità che non ha ancora.
    const added=[...dragUnits].filter(u=>!targetUnits.has(u));
    if(targetUnits.size>0&&added.length>0&&!options.force&&!(await Trend.askMixedUnits([...new Set([...targetUnits,...dragUnits])])))return false;
    if(!this.nodes.has(dragId)||!this.nodes.has(targetId))return false;
    this.ensureGroupMode();const plan=this.planAxes();
    if(plan.axisById.get(dragId)===plan.axisById.get(targetId))return false;
    this.freezeSides(plan);
    const targetKey=plan.axisById.get(targetId),side=plan.side.get(targetKey)||'left',gid=targetKey.startsWith('s:')?this.nextScaleId():targetKey;
    const set=(id,patch)=>{this.presentation[id]={...(this.presentation[id]||{}),...patch};};
    for(const id of plan.members.get(targetKey)||[])set(id,{scale:gid,axis:side});
    set(dragId,{scale:gid,axis:side});
    this.pruneScaleSettings();this.scaleChanged();return true;
  }
  separateScale(id){
    if(!this.nodes.has(id))return false;
    const before=this.groupsMode()?this.planAxes():this.axisPlan;
    if((before.members.get(before.axisById.get(id))||[]).length<2)return false;
    this.ensureGroupMode();const plan=this.planAxes();this.freezeSides(plan);
    const {scale,...rest}=this.presentation[id]||{};this.presentation[id]=rest;
    this.pruneScaleSettings();this.scaleChanged();return true;
  }
  async mergeAllScales(options={}){
    const ids=[...this.nodes.keys()];if(ids.length<2)return false;
    const units=new Set(ids.map(id=>this.unitOf(id).toLowerCase()).filter(Boolean));
    if(units.size>1&&!options.force&&!(await Trend.askMixedUnits([...units])))return false;
    this.ensureGroupMode();const plan=this.planAxes();this.freezeSides(plan);const gid=this.nextScaleId(),side=plan.side.get(plan.axisById.get(ids[0]))||'left';
    for(const id of ids)this.presentation[id]={...(this.presentation[id]||{}),scale:gid,axis:side};
    this.pruneScaleSettings();this.scaleChanged();return true;
  }
  separateAllScales(){
    if(this.groupsMode()?this.planAxes().keys.length===this.nodes.size:false)return false;
    this.ensureGroupMode();const plan=this.planAxes();this.freezeSides(plan);
    for(const id of this.nodes.keys()){const {scale,...rest}=this.presentation[id]||{};this.presentation[id]=rest;}
    this.pruneScaleSettings();this.scaleChanged();return true;
  }
  renderLegend(plan){
    const legendKey=JSON.stringify([trendDark(),plan.groups,...[...this.nodes].map(([id,n])=>[id,n.display_name,this.color(id),plan.axisById.get(id),plan.keys.indexOf(plan.axisById.get(id)),(plan.members.get(plan.axisById.get(id))||[]).length,this.presentation[id]?.unit,this.presentation[id]?.alias])]);
    if(this.legendKey===legendKey)return;this.legendKey=legendKey;this.legend.replaceChildren();
    for(const [id,n] of this.nodes){
      const key=plan.axisById.get(id),shared=plan.groups&&(plan.members.get(key)||[]).length>1;
      const s=document.createElement('span'),dot=document.createElement('i'),tag=document.createElement('b');
      s.style.color=this.color(id);dot.className='dot';tag.className='axis-tag'+(shared?' shared':'');
      if(shared){const gc=this.groupColor(plan,key);tag.style.color=gc;tag.style.borderColor=gc;}
      I18N.bind(tag,"textContent",()=>this.axisTag(id),false);
      s.title=(n.display_name||id)+(shared?'\n'+I18N.t("Scala condivisa: trascina su un altro canale per unire, tasto destro per separare"):'\n'+I18N.t("Trascina su un altro canale per condividere la scala"))+'\n'+I18N.t("Tastiera: Spazio prende il canale, Invio lo rilascia su un altro, Canc separa");
      s.draggable=true;s.tabIndex=0;s.dataset.signalId=id;s.dataset.scaleKey=key;s.dataset.scaleShared=shared?'1':'0';
      s.append(dot,document.createTextNode(signalShortName(id,n,this.presentation[id])+(this.presentation[id]?.unit?' ['+this.presentation[id].unit+']':'')),tag);
      this.bindLegendEntry(s,id);this.legend.append(s);
    }
  }
  clearLegendPick(){Trend.legendPicked=null;for(const e of this.legendEntries()){e.classList.remove('legend-picked');e.removeAttribute('aria-grabbed');}}
  legendEntries(){return [...this.legend.querySelectorAll('span[data-signal-id]')];}
  clearLegendDrag(){Trend.legendDrag=null;for(const e of this.legendEntries())e.classList.remove('legend-dragging','legend-drop-target','legend-drop-group');}
  bindLegendEntry(s,id){
    s.addEventListener('dragstart',e=>{Trend.legendDrag={trend:this,id};try{e.dataTransfer.effectAllowed='link';e.dataTransfer.setData('application/x-sa-scale',id);}catch{}s.classList.add('legend-dragging');this.tooltip.hidden=true;});
    s.addEventListener('dragend',()=>this.clearLegendDrag());
    const over=e=>{const d=Trend.legendDrag;if(!d||d.trend!==this||d.id===id)return;e.preventDefault();try{e.dataTransfer.dropEffect='link';}catch{}
      s.classList.add('legend-drop-target');for(const o of this.legendEntries())if(o!==s)o.classList.toggle('legend-drop-group',o.dataset.scaleKey===s.dataset.scaleKey&&o.dataset.signalId!==d.id);};
    s.addEventListener('dragenter',over);s.addEventListener('dragover',over);
    s.addEventListener('dragleave',e=>{if(!s.contains(e.relatedTarget)){s.classList.remove('legend-drop-target');for(const o of this.legendEntries())o.classList.remove('legend-drop-group');}});
    s.addEventListener('drop',e=>{const d=Trend.legendDrag;this.clearLegendDrag();if(!d||d.trend!==this||d.id===id)return;e.preventDefault();e.stopPropagation();this.joinScale(d.id,id).catch(()=>{});});
    s.addEventListener('contextmenu',e=>this.showScaleMenu(e,id,s));
    // Tastiera: Spazio prende il canale, Invio lo rilascia su quello selezionato, Esc annulla, frecce spostano il fuoco, Canc separa.
    s.addEventListener('keydown',e=>{
      const entries=this.legendEntries(),index=entries.indexOf(s),picked=Trend.legendPicked?.trend===this?Trend.legendPicked.id:null;
      if(e.key==='ArrowRight'||e.key==='ArrowDown'){e.preventDefault();entries[Math.min(entries.length-1,index+1)]?.focus();}
      else if(e.key==='ArrowLeft'||e.key==='ArrowUp'){e.preventDefault();entries[Math.max(0,index-1)]?.focus();}
      else if(e.key===' '){e.preventDefault();this.clearLegendPick();if(picked!==id){Trend.legendPicked={trend:this,id};s.classList.add('legend-picked');s.setAttribute('aria-grabbed','true');}}
      else if(e.key==='Enter'&&picked&&picked!==id){e.preventDefault();this.clearLegendPick();this.joinScale(picked,id).catch(()=>{});}
      else if(e.key==='Escape'&&picked){e.preventDefault();this.clearLegendPick();}
      else if(e.key==='Delete'||e.key==='Backspace'){e.preventDefault();this.separateScale(id);}
    });
  }
  showScaleMenu(event,id,anchor){
    event.preventDefault();Trend.closeScaleMenu();
    const plan=this.axisPlan,key=plan.axisById.get(id),shared=(plan.members.get(key)||[]).length>1;
    const menu=document.createElement('div');menu.className='scale-menu';menu.setAttribute('role','menu');
    const add=(label,enabled,run)=>{const b=document.createElement('button');b.type='button';b.setAttribute('role','menuitem');b.dataset.scaleAction=label;I18N.bind(b,"textContent",()=>I18N.t(label),true);b.disabled=!enabled;b.onclick=()=>{Trend.closeScaleMenu();Promise.resolve(run()).catch(()=>{});};menu.append(b);};
    add("Separa scala",shared,()=>this.separateScale(id));
    add("Unisci tutte le scale",plan.keys.length>1,()=>this.mergeAllScales());
    add("Separa tutte le scale",plan.keys.some(k=>(plan.members.get(k)||[]).length>1),()=>this.separateAllScales());
    const r=anchor?.getBoundingClientRect?.()||{left:0,bottom:0};const x=Number.isFinite(event.clientX)&&event.clientX>0?event.clientX:r.left,y=Number.isFinite(event.clientY)&&event.clientY>0?event.clientY:r.bottom;
    menu.style.left=Math.max(4,Math.min(x,(window.innerWidth||1200)-190))+'px';menu.style.top=Math.max(4,Math.min(y,(window.innerHeight||800)-130))+'px';
    document.body.append(menu);Trend.scaleMenu=menu;
    const away=e=>{if(!menu.contains(e.target))Trend.closeScaleMenu();},key2=e=>{if(e.key==='Escape')Trend.closeScaleMenu();};
    menu.cleanup=()=>{document.removeEventListener('pointerdown',away,true);document.removeEventListener('keydown',key2,true);};
    setTimeout(()=>{if(Trend.scaleMenu===menu){document.addEventListener('pointerdown',away,true);document.addEventListener('keydown',key2,true);}},0);
    menu.querySelector('button:not(:disabled)')?.focus?.();
  }
  static closeScaleMenu(){const m=Trend.scaleMenu;Trend.scaleMenu=null;if(m){m.cleanup?.();m.remove();}}
  // Unità fisiche diverse si uniscono solo con una scelta esplicita: la scala numerica sarà condivisa senza equivalenza fisica.
  static askMixedUnits(units){
    return new Promise(resolve=>{
      Trend.closeScaleMenu();
      const overlay=document.createElement('div'),box=document.createElement('div'),text=document.createElement('p'),row=document.createElement('div'),ok=document.createElement('button'),cancel=document.createElement('button');
      overlay.className='scale-confirm';overlay.setAttribute('role','alertdialog');overlay.setAttribute('aria-modal','true');ok.type=cancel.type='button';ok.className='primary';cancel.className='ghost';ok.dataset.scaleConfirm='ok';cancel.dataset.scaleConfirm='cancel';
      I18N.bind(text,"textContent",()=>(I18N.join([{text:"I segnali hanno unità diverse ("},{value:(units.join(', '))},{text:"). Condividere una scala numerica senza equivalenza fisica? I valori non vengono modificati."}])),true);
      I18N.bind(ok,"textContent",()=>(I18N.t("Unisci comunque")),true);I18N.bind(cancel,"textContent",()=>(I18N.t("Annulla")),true);
      const key=e=>{if(e.key==='Escape'){e.preventDefault();close(false);}};
      const close=v=>{document.removeEventListener('keydown',key,true);overlay.remove();resolve(v);};
      ok.onclick=()=>close(true);cancel.onclick=()=>close(false);overlay.addEventListener('pointerdown',e=>{if(e.target===overlay)close(false);});document.addEventListener('keydown',key,true);
      row.append(cancel,ok);box.append(text,row);overlay.append(box);document.body.append(overlay);cancel.focus?.();
    });
  }
  draw(){
    const c=this.canvas,r=c.getBoundingClientRect(),dpr=window.devicePixelRatio||1;if(r.width<1||r.height<1)return;
    this.theme=window.studioTheme||{text:'#707E86',grid:'#E5E5E5',surface:'#FFFFFF'};c.width=Math.round(r.width*dpr);c.height=Math.round(r.height*dpr);const ctx=c.getContext('2d');if(!ctx)return;ctx.scale(dpr,dpr);
    const plan=this.planAxes(),axisSignature=[plan.dual?'2':'1',...[...plan.axisById.entries()].map(([id,a])=>id+':'+a),...plan.keys.map(k=>k+'@'+plan.side.get(k))].join('|');
    if(this.axisSignature&&axisSignature!==this.axisSignature&&this.zoom){this.zoom=null;this.frozen=false;this.series=this.latestSeries;}
    this.axisSignature=axisSignature;this.axisPlan=plan;
    const w=r.width,h=r.height,leftKeys=plan.keys.filter(k=>plan.side.get(k)!=='right'),rightKeys=plan.keys.filter(k=>plan.side.get(k)==='right'),p={l:78+Trend.AXIS_COLUMN*(Math.min(Trend.AXIS_COLUMNS,Math.max(1,leftKeys.length))-1),r:82+Trend.AXIS_COLUMN*(Math.min(Trend.AXIS_COLUMNS,Math.max(1,rightKeys.length))-1),t:22,b:42};this.layout={p,w,h};
    const scrollBar=this.el('timeScroll')?.closest?.('.time-scrollbar');if(scrollBar){scrollBar.style.marginLeft=p.l+'px';scrollBar.style.marginRight=p.r+'px';}
    this.renderLegend(plan);
    ctx.fillStyle=this.theme.text;ctx.font='12px Segoe UI';const all=bounds(this.series);
    if(!all){this.crosshairEl.hidden=true;this.domain=null;this.fullDomain=null;this.syncZoomControls();this.syncTimeScroll();ctx.textAlign='center';ctx.fillText(I18N.message(this.emptyMessage),w/2,h/2);this.drawSelection(ctx);this.updateMarkerTable();return;}
    const asDomain=b=>b?{ymin:b.low*b.unit,ymax:b.high*b.unit}:null,axesFull={};for(const key of plan.keys)axesFull[key]=asDomain(bounds(this.series,plan.members.get(key)||[])||all);
    const fullDomain={xmin:all.xmin,xmax:all.xmax,axes:axesFull};for(const axis of plan.keys){const a=this.axisSettings[axis];if(a?.mode==='manual'&&Number.isFinite(a.min)&&Number.isFinite(a.max)&&a.max>a.min&&fullDomain.axes[axis])fullDomain.axes[axis]={ymin:a.min,ymax:a.max};}if(!this.frozen)this.lastDisplayRange=this.displayRange?{...this.displayRange}:null;const extent=this.frozen?this.lastDisplayRange:this.displayRange;if(extent&&extent.xmax>extent.xmin){fullDomain.xmin=extent.xmin;fullDomain.xmax=extent.xmax;}this.fullDomain=fullDomain;
    this.followLiveZoom(fullDomain);
    if(this.zoom&&!this.linkedRange&&(this.zoom.xmax<fullDomain.xmin||this.zoom.xmin>fullDomain.xmax)){this.zoom=null;this.frozen=false;this.series=this.latestSeries;}
    if(this.zoom&&plan.keys.some(k=>!this.zoom.axes?.[k])){this.zoom=null;this.frozen=false;this.series=this.latestSeries;}
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
    const X=t=>p.l+(t-b.xmin)/(b.xmax-b.xmin)*(w-p.l-p.r);const Y=(v,axis)=>{const d=b.axes[axis]||b.axes[plan.keys[0]];return p.t+(d.ymax-v)/(d.ymax-d.ymin)*(h-p.t-p.b);};
    ctx.lineWidth=1;ctx.strokeStyle=this.theme.grid;
    // Tacche Y su valori tondi (1, 2, 2,5, 5 × 10^n); l'asse Y2 usa le proprie.
    const plotH=h-p.t-p.b,yAt=(v,d)=>p.t+(d.ymax-v)/(d.ymax-d.ymin)*plotH;
    {const COL=Trend.AXIS_COLUMN,MAXC=Trend.AXIS_COLUMNS,primary=plan.keys[0],steps=Math.max(3,Math.round(plotH/60)),sideKeys=side=>plan.keys.filter(k=>(plan.side.get(k)==='right'?'right':'left')===side).slice(0,MAXC);
     ctx.font='12px Segoe UI';
     for(const side of ['left','right']){const right=side==='right';
       sideKeys(side).forEach((key,i)=>{const d=b.axes[key];if(!d)return;const {ticks,step}=niceTicks(d.ymin,d.ymax,steps),members=plan.members.get(key)||[],x=right?w-p.r+9+COL*i:p.l-9-COL*i;
         for(const v of ticks){const y=yAt(v,d);if(key===primary){ctx.strokeStyle=this.theme.grid;ctx.beginPath();ctx.moveTo(p.l,y);ctx.lineTo(w-p.r,y);ctx.stroke();}ctx.fillStyle=plan.groups&&members.length===1?this.color(members[0]):this.theme.text;ctx.textAlign=right?'left':'right';ctx.fillText(this.formatAxis(v,step),x,y+4);}});}
     ctx.font='600 10px Segoe UI';
     if(!plan.groups){ctx.fillStyle=this.theme.text;ctx.textAlign='left';ctx.fillText(this.axisLabel('left'),p.l,p.t-8);if(plan.dual){ctx.textAlign='right';ctx.fillText(this.axisLabel('right'),w-p.r,p.t-8);}}
     else for(const side of ['left','right']){const right=side==='right';
       sideKeys(side).forEach((key,i)=>{const members=plan.members.get(key)||[],a=this.axisSettings[key]||{},unit=(a.unit||[...new Set(members.map(id=>this.presentation[id]?.unit).filter(Boolean))].join('/')).slice(0,5);
         ctx.fillStyle=members.length===1?this.color(members[0]):this.theme.text;ctx.textAlign=right?'left':'right';
         ctx.fillText('Y'+(plan.keys.indexOf(key)+1)+(members.length>1?'*':'')+(a.mode==='manual'?'M':'')+(unit?' '+unit:''),right?w-p.r+9+COL*i:p.l-9-COL*i,p.t-8);});}
     const hidden=plan.keys.length-sideKeys('left').length-sideKeys('right').length;
     this.hiddenScales=Math.max(0,hidden);
     if(hidden>0){ctx.fillStyle=this.theme.text;ctx.textAlign='right';ctx.fillText('+'+hidden+' Y',w-4,10);}
    }
    ctx.save();ctx.beginPath();ctx.rect(p.l,p.t,w-p.l-p.r,h-p.t-p.b);ctx.clip();
    this.drawSeriesLayer(ctx,plan,X,Y,b,p,w,h,dpr);
    this.drawMarkers(ctx,X,p,w,h);ctx.restore();ctx.fillStyle=this.theme.text;ctx.font='11px Segoe UI';
    // Asse del tempo: passo adattivo allineato all'ora; con zoom sotto il secondo compaiono i millisecondi.
    {const {ticks,step}=timeTicks(b.xmin,b.xmax,w-p.l-p.r);ctx.strokeStyle=this.theme.grid;ctx.textAlign='center';
     for(const t of ticks){const x=X(t);ctx.beginPath();ctx.moveTo(x,h-p.b);ctx.lineTo(x,h-p.b+4);ctx.stroke();const label=timeLabel(t,step),half=(ctx.measureText?ctx.measureText(label).width:60)/2;ctx.fillText(label,Math.max(p.l+half,Math.min(w-p.r-half,x)),h-14);}}
    this.drawSelection(ctx);this.updateMarkerTable();this.renderCrosshair();
  }
  /** Segnali e fascia qualità. Con dati di file lo strato resta in cache finché vista, dimensioni e dati non cambiano
   * (selezione zoom, marker e cursore non ridisegnano milioni di campioni). */
  drawSeriesLayer(ctx,plan,X,Y,b,p,w,h,dpr){
    const packed=Object.values(this.series).some(isSampleSeries);
    if(!packed){this.seriesLayer=null;this.paintSeries(ctx,plan,X,Y,b,p,w,h,dpr);return;}
    Trend.layerIds=Trend.layerIds||new WeakMap();Trend.layerCounter=Trend.layerCounter||0;
    const identity=points=>{if(!points||typeof points!=='object')return '0';if(!Trend.layerIds.has(points))Trend.layerIds.set(points,++Trend.layerCounter);return Trend.layerIds.get(points)+':'+points.length+':'+(points.version||0);};
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
    let count=0;for(const [id,points] of Object.entries(this.series)){if(count++>=12)break;const point=nearest(points,t),pp=document.createElement('p'),axis=this.axisTag(id);I18N.bind(pp,"textContent",()=>(I18N.join([{value:(signalShortName(id,this.nodes.get(id),this.presentation[id]))},{text:" ["},{value:(axis)},{text:"]: "},{value:(valueText(point?.[1]))},{value:(this.presentation[id]?.unit?' '+this.presentation[id].unit:'')},{text:" · "},{value:(point?.[2]||'Good')},{value:(point&&Math.abs(point[0]-t)>1000?I18N.t(" (campione distante dal cursore)"):'')}])),true);this.tooltip.append(pp);}this.tooltip.hidden=false;
  }
}
Trend.AXIS_COLUMN=58;Trend.AXIS_COLUMNS=4;Trend.legendDrag=null;Trend.legendPicked=null;Trend.scaleMenu=null;
