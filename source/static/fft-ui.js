/* Standard DSP block analyzer; calculation isolated from acquisition. */
window.FFTAnalyzer=(()=>{
 const fields=['fft-window','fft-detrend','fft-mode','fft-live-seconds','fft-resolution-mode','fft-resolution-value','fft-overlap','fft-average','fft-count','fft-tau','fft-output','fft-unit','fft-gap-policy','fft-tolerance','fft-band-min','fft-band-max','fft-log','fft-x-log','fft-show-current','fft-show-max','fft-fundamental-mode','fft-fundamental-hz','fft-harmonic-tolerance','fft-show-harmonics','fft-render-mode'];
 let busy=false,running=false,timer=null,generation=0,plot=null,drag=null,hover=null;
 const num=id=>{const v=Number($(id).value);if(!Number.isFinite(v))throw Error(I18N.join([{text:"Parametro non valido: "},{value:(id)}]));return v;};
 const settings=()=>Object.fromEntries(fields.map(id=>[id,$(id).type==='checkbox'?$(id).checked:$(id).value]));
 function restore(values){for(const id of fields)if(Object.hasOwn(values,id)){if($(id).type==='checkbox')$(id).checked=!!values[id];else $(id).value=String(values[id]);}state();}
 function state(){$('fft-mode').value='interval';$('fft-mode').disabled=true;$('fft-mode').querySelector('[value=live]').disabled=true;$('fft-live-seconds').closest('div').hidden=true;$('fft-stop').hidden=true;$('fft-spectrum-recalculate').disabled=busy;$('fft-spectrum-stop').hidden=!running;const live=$('fft-mode').value==='live';$('fft-live-seconds').disabled=!live;I18N.bind($('fft-run'),"textContent",()=>(live?I18N.t("Avvia FFT live"):I18N.t("Calcola FFT")),true);$('fft-run').disabled=busy;$('fft-stop').disabled=!running;$('fft-fundamental-hz').disabled=$('fft-fundamental-mode').value!=='manual';$('fft-tau').disabled=$('fft-average').value!=='exponential';}
 function stop(){running=false;clearTimeout(timer);timer=null;generation++;state();}
 function open(liveDefault){stop();expanded(false);$('fft-settings').open=true;plot=null;hover=null;$('fft-mode').querySelector('[value=live]').disabled=!!analysisTarget.workspace.staticMode||analysisTarget.graph.trend.frozen;if(analysisTarget.workspace.staticMode||analysisTarget.graph.trend.frozen)$('fft-mode').value='interval';else if(liveDefault)$('fft-mode').value='live';I18N.bind($('analysis-status'),"textContent",()=>(I18N.t("Campioni originali · blocchi completi · nessun ricampionamento automatico. Le unità etichettano i valori originali.")),true);state();}
 function options(){const mode=$('fft-resolution-mode').value,v=num('fft-resolution-value');const o={resolution_mode:mode,resolution_value:v,block_size:mode==='samples'?v:0,overlap_percent:num('fft-overlap'),averaging:$('fft-average').value,average_count:num('fft-count'),tau_seconds:num('fft-tau'),output:$('fft-output').value,unit:$('fft-unit').value,gap_policy:$('fft-gap-policy').value,timing_tolerance_percent:num('fft-tolerance'),band_min_hz:num('fft-band-min')};o.fundamental_mode=$('fft-fundamental-mode').value;o.harmonic_tolerance_percent=num('fft-harmonic-tolerance');if(o.fundamental_mode==='manual')o.fundamental_hz=num('fft-fundamental-hz');if($('fft-band-max').value!=='')o.band_max_hz=num('fft-band-max');return o;}
 async function calculate(automatic=false){
  if(busy||!analysisTarget)return;
  if(!analysisTarget.workspace.staticMode&&!analysisTarget.graph.trend.frozen){stop();notice(I18N.t('FFT disponibile solo in Pausa con marker X1 e X2'),true);return;}
  $('fft-mode').value='interval';
  if(!automatic){running=$('fft-mode').value==='live';generation++;}
  const ticket=generation,target=analysisTarget;busy=true;state();I18N.bind($('analysis-status'),"textContent",()=>(I18N.t("Calcolo in processo separato…")),true);
  try{
   const body={...await preparedAnalysisRequest(),signal_ids:[$('fft-signal').value],window:$('fft-window').value,detrend:$('fft-detrend').checked,options:options()};
   if($('fft-mode').value==='live')body.live_seconds=num('fft-live-seconds');
   const response=await request('/api/analysis/fft',body);
   if(ticket!==generation||target!==analysisTarget||!$('analysis-dialog').open)return;
   const first=!fftResult;fftResult=response.spectrum;const r=fftResult;$('fft-result').hidden=false;
   if(first){$('fft-min-hz').value=0;$('fft-max-hz').value=r.nyquist_hz;}
   I18N.bind($('fft-summary'),"textContent",()=>(I18N.join([{text:"Fs "},{value:(valueText(r.sample_hz))},{text:" Hz · Δf "},{value:(valueText(r.resolution_hz))},{text:" Hz · blocco "},{value:(r.samples_fft)},{text:" campioni / "},{value:(valueText(r.block_seconds))},{text:" s · NBW "},{value:(valueText(r.noise_bandwidth_hz))},{text:" Hz · "},{value:(r.blocks_used)},{text:" blocchi usati / "},{value:(r.blocks_excluded)},{text:" esclusi · "},{value:(r.samples_original)},{text:" campioni originali, "},{value:(r.samples_unused)},{text:" non usati"}])),true);
   I18N.bind($('fft-band-result'),"textContent",()=>(I18N.join([{text:"RMS banda "},{value:(valueText(r.band_min_hz))},{text:"–"},{value:(valueText(r.band_max_hz))},{text:" Hz: "},{value:(valueText(r.band_rms))},{text:" "},{value:(r.signal_unit)},{text:" · RMS complessivo: "},{value:(valueText(r.overall_rms))},{text:" "},{value:(r.signal_unit)},{text:" ("},{value:(r.rms_kind)},{text:"). Picco AC: "},{value:(valueText(r.peak_hz))},{text:" Hz / "},{value:(valueText(r.peak_amplitude))},{text:" "},{value:(r.amplitude_unit)},{text:""}])),true);
   I18N.bind($('fft-note'),"textContent",()=>(I18N.join([{text:""},{value:(r.note)},{text:" Origine: "},{value:(r.data_origin)},{text:I18N.t(". Base tempi: ")},{value:(r.time_basis)},{text:". "},{value:(r.bad_samples)},{text:" campioni non validi; "},{value:(r.timing_breaks)},{text:" discontinuità temporali. Tolleranza "},{value:(r.timing_tolerance_percent)},{text:"%. Blu: risultato · verde: ultimo blocco · arancio: Max Hold."}])),true);
   I18N.bind($('fft-peaks'),"textContent",()=>(I18N.join([{text:"Picchi sulle righe FFT: "},{value:(r.peaks.map(p=>`${valueText(p.hz)} Hz → ${valueText(p.value)} ${r.amplitude_unit}`).join(' | ')||I18N.t("nessun picco locale"))}])),true);
   const signal=$('fft-signal').selectedOptions[0];I18N.bind($('fft-spectrum-signal'),"textContent",()=>(I18N.join([{value:(signal?.textContent||I18N.t("Segnale"))},{text:" · "},{value:(r.amplitude_unit)},{text:" · 0–"},{value:(valueText(r.nyquist_hz))},{text:" Hz"}])),true);I18N.bind($('fft-spectrum-signal'),"title",()=>($('fft-signal').value),false);
   renderHarmonics();I18N.bind($('analysis-status'),"textContent",()=>(running?I18N.t("FFT live attiva · finestra scorrevole, ricalcolo ogni secondo dopo il completamento."):I18N.t("FFT completata.")),true);if(!automatic){$('fft-settings').open=false;$('fft-spectrum-panel').scrollIntoView?.({block:'start',behavior:'smooth'});}draw();
  }catch(e){if(ticket===generation)I18N.bind($('analysis-status'),"textContent",()=>(e.message),true);}
  finally{busy=false;state();if(running&&$('analysis-dialog').open)timer=setTimeout(()=>calculate(true),1000);}
 }
 function renderHarmonics(){
  const r=fftResult,h=r.harmonics;const body=$('fft-harmonic-body');body.replaceChildren();
  I18N.bind($('fft-harmonic-summary'),"textContent",()=>((h.fundamental_hz!=null?`F1 ${valueText(h.fundamental_hz)} Hz · Δf ${valueText(r.resolution_hz)} Hz. `:'')+h.note),false);
  for(const row of h.rows){const tr=document.createElement('tr');for(const v of [row.order===1?'F1':'H'+row.order,valueText(row.target_hz),row.measured_hz==null?'—':valueText(row.measured_hz),row.amplitude==null?'—':valueText(row.amplitude)+' '+r.amplitude_unit,row.deviation_hz==null?'—':valueText(row.deviation_hz),row.status]){const td=document.createElement('td');I18N.bind(td,"textContent",()=>(v),false);tr.append(td);}body.append(tr);}
  $('fft-harmonic-download').disabled=!h.rows.length;
 }
 function draw(){
  if(!fftResult)return;const r=fftResult,c=$('fft-canvas'),rect=c.getBoundingClientRect(),w=rect.width||800,h=rect.height||330,dpr=window.devicePixelRatio||1;
  c.width=Math.round(w*dpr);c.height=Math.round(h*dpr);const ctx=c.getContext('2d');if(!ctx)return;ctx.scale(dpr,dpr);ctx.fillStyle=(window.studioTheme||{}).surface||'#fff';ctx.fillRect(0,0,w,h);
  let lo=Math.max(0,Number($('fft-min-hz').value)||0),hi=Math.min(r.nyquist_hz,Number($('fft-max-hz').value)||r.nyquist_hz);const logX=$('fft-x-log').checked,logY=$('fft-log').checked;
  if(logX)lo=Math.max(lo,r.resolution_hz);if(hi<=lo)hi=Math.min(r.nyquist_hz,lo+r.resolution_hz);if(hi<=lo)return;
  const series=[{bins:r.bins,color:'#006EFF'}];if($('fft-show-current').checked)series.push({bins:r.current_bins,color:'#0AAF96'});if($('fft-show-max').checked)series.push({bins:r.maxhold_bins,color:'#F58C3A'});
  let peak=1e-15;for(const s of series)for(const [f,v] of s.bins)if(f>=lo&&f<=hi)peak=Math.max(peak,v);
  const p={l:85,r:24,t:34,b:65},low=logY?Math.log10(peak)-6:0,high=logY?Math.log10(peak)+.05:peak*1.1;
  const x0=logX?Math.log10(lo):lo,x1=logX?Math.log10(hi):hi;
  const X=f=>p.l+((logX?Math.log10(Math.max(f,lo)):f)-x0)/(x1-x0)*(w-p.l-p.r),F=x=>{const v=x0+(x-p.l)/(w-p.l-p.r)*(x1-x0);return logX?10**v:v;};
  const Y=v=>h-p.b-((logY?Math.log10(Math.max(v,peak*1e-6)):v)-low)/(high-low)*(h-p.t-p.b);
  plot={lo,hi,X,F,w,h,p};ctx.font='11px Segoe UI';ctx.fillStyle=(window.studioTheme||{}).text||'#707E86';ctx.strokeStyle=(window.studioTheme||{}).grid||'#E5E5E5';
  for(let i=0;i<=5;i++){const y=p.t+i*(h-p.t-p.b)/5;ctx.beginPath();ctx.moveTo(p.l,y);ctx.lineTo(w-p.r,y);ctx.stroke();ctx.textAlign='right';ctx.fillText((logY?10**(high-i*(high-low)/5):high-i*(high-low)/5).toPrecision(3),p.l-7,y+4);ctx.textAlign='center';const f=F(p.l+i*(w-p.l-p.r)/5);ctx.fillText(f.toPrecision(4)+' Hz',X(f),h-35);}
  ctx.textAlign='left';ctx.fillText(I18N.join([{text:"Ampiezza / livello ["},{value:(r.amplitude_unit)},{text:"]"}]),p.l,17);ctx.textAlign='center';ctx.fillText(I18N.t("Frequenza [Hz]"),p.l+(w-p.l-p.r)/2,h-10);ctx.save();ctx.beginPath();ctx.rect(p.l,p.t,w-p.l-p.r,h-p.t-p.b);ctx.clip();
  for(const s of series){ctx.strokeStyle=s.color;ctx.lineWidth=1.4;ctx.beginPath();let active=false;for(const [f,v] of s.bins){if(f<lo||f>hi)continue;if($('fft-render-mode').value==='sticks'){ctx.moveTo(X(f),h-p.b);ctx.lineTo(X(f),Y(v));}else if(active)ctx.lineTo(X(f),Y(v));else{ctx.moveTo(X(f),Y(v));active=true;}}ctx.stroke();}
  if($('fft-show-harmonics').checked&&r.harmonics){ctx.save();ctx.setLineDash([4,4]);for(const row of r.harmonics.rows){const f=row.target_hz;if(f<lo||f>hi)continue;ctx.strokeStyle='#707E86';ctx.beginPath();ctx.moveTo(X(f),p.t);ctx.lineTo(X(f),h-p.b);ctx.stroke();ctx.fillStyle='#707E86';ctx.textAlign='center';ctx.fillText(row.order===1?'F1':'H'+row.order,X(f),p.t+12+(row.order%2)*13);if(row.measured_hz!=null){ctx.setLineDash([]);ctx.beginPath();ctx.arc(X(row.measured_hz),Y(row.amplitude),3,0,Math.PI*2);ctx.fill();ctx.setLineDash([4,4]);}}ctx.restore();}
  if(hover!=null&&hover>=lo&&hover<=hi){ctx.strokeStyle='#707E86';ctx.beginPath();ctx.moveTo(X(hover),p.t);ctx.lineTo(X(hover),h-p.b);ctx.stroke();}ctx.restore();
 }
 function expanded(on){
  $('analysis-dialog').classList.toggle('spectrum-expanded',on);$('fft-spectrum-expand').setAttribute('aria-pressed',String(on));I18N.bind($('fft-spectrum-expand'),"textContent",()=>(on?I18N.t("Ripristina dimensione"):I18N.t("Ingrandisci spettro")),true);requestAnimationFrame(draw);
 }
 $('fft-spectrum-expand').onclick=()=>expanded(!$('analysis-dialog').classList.contains('spectrum-expanded'));
 $('fft-spectrum-recalculate').onclick=()=>calculate();
 $('fft-spectrum-stop').onclick=()=>{$('fft-stop').click();};
 $('fft-harmonic-focus').onclick=()=>{const f=fftResult?.harmonics?.fundamental_hz;if(!f){I18N.bind($('analysis-status'),"textContent",()=>(I18N.t("Imposta o calcola una fondamentale per la vista F1–H5.")),true);return;}$('fft-min-hz').value=0;$('fft-max-hz').value=Math.min(fftResult.nyquist_hz,5.5*f);draw();};
 $('fft-spectrum-png').onclick=()=>{if(!fftResult)return;draw();const a=document.createElement('a');a.href=$('fft-canvas').toDataURL('image/png');a.download='Space_Fieldbus_Analyzer_FFT_spectrum.png';a.click();};
 function autoscale(){if(!fftResult)return;$('fft-min-hz').value=0;$('fft-max-hz').value=fftResult.nyquist_hz;draw();}
 for(const id of fields)$(id).addEventListener('change',()=>{if(!['fft-log','fft-x-log','fft-show-current','fft-show-max','fft-show-harmonics','fft-render-mode'].includes(id))stop();state();window.scheduleWorkspaceSave?.();draw();});
 $('fft-signal').addEventListener('change',stop);
 $('fft-stop').onclick=()=>{stop();I18N.bind($('analysis-status'),"textContent",()=>(I18N.t("Aggiornamento FFT fermato. Acquisizione e Record continuano.")),true);};
 $('fft-reset').onclick=()=>{stop();expanded(false);fftResult=null;plot=null;$('fft-result').hidden=true;I18N.bind($('analysis-status'),"textContent",()=>(I18N.t("Risultato azzerato; il prossimo calcolo usa l’intervallo selezionato.")),true);};
 $('analysis-dialog').addEventListener('close',stop);$('analysis-dialog').addEventListener('cancel',stop);$('analysis-close').addEventListener('click',stop);
 $('fft-view-reset').onclick=autoscale;$('fft-min-hz').oninput=draw;
 const c=$('fft-canvas');
 c.addEventListener('pointerdown',e=>{if(plot){drag=e.clientX-c.getBoundingClientRect().left;c.setPointerCapture?.(e.pointerId);}});
 c.addEventListener('pointermove',e=>{if(!plot)return;const x=e.clientX-c.getBoundingClientRect().left;hover=Math.max(plot.lo,Math.min(plot.hi,plot.F(x)));const k=Math.max(0,Math.min(fftResult.bins.length-1,Math.round(hover/fftResult.resolution_hz))),point=fftResult.bins[k];I18N.bind($('fft-cursor'),"textContent",()=>(`${valueText(point[0])} Hz · ${valueText(point[1])} ${fftResult.amplitude_unit}`),false);draw();});
 c.addEventListener('pointerup',e=>{if(drag==null||!plot)return;const end=e.clientX-c.getBoundingClientRect().left;if(Math.abs(end-drag)>6){$('fft-min-hz').value=Math.max(plot.lo,plot.F(Math.min(drag,end)));$('fft-max-hz').value=Math.min(plot.hi,plot.F(Math.max(drag,end)));draw();}drag=null;});
 c.addEventListener('dblclick',autoscale);
 c.addEventListener('wheel',e=>{if(!plot)return;e.preventDefault();const factor=e.deltaY>0?1.4:1/1.4,center=(plot.lo+plot.hi)/2,half=(plot.hi-plot.lo)*factor/2;$('fft-min-hz').value=Math.max(0,center-half);$('fft-max-hz').value=Math.min(fftResult.nyquist_hz,center+half);draw();},{passive:false});
 $('fft-download').onclick=()=>{if(!fftResult)return;const r=fftResult,quote=x=>'"'+String(x).replaceAll('"','""')+'"';let csv='frequency_hz,value,current,max_hold,unit,window,averaging,sample_hz,block_size,blocks_used\r\n';csv+=r.bins.map((p,i)=>[p[0],p[1],r.current_bins[i][1],r.maxhold_bins[i][1],quote(r.amplitude_unit),r.window,r.averaging,r.sample_hz,r.samples_fft,r.blocks_used].join(',')).join('\r\n');const url=URL.createObjectURL(new Blob(['\uFEFF'+csv],{type:'text/csv;charset=utf-8'}));const a=document.createElement('a');a.href=url;a.download='FFT_spectrum.csv';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
 $('fft-harmonic-download').onclick=()=>{if(!fftResult?.harmonics)return;const r=fftResult,h=r.harmonics,quote=x=>'"'+String(x??'').replaceAll('"','""')+'"';let csv='order,target_hz,measured_hz,amplitude,unit,deviation_hz,status,fundamental_mode,resolution_hz,search_half_width_hz\r\n';csv+=h.rows.map(row=>[row.order,row.target_hz,row.measured_hz??'',row.amplitude??'',quote(r.amplitude_unit),row.deviation_hz??'',quote(row.status),h.mode,r.resolution_hz,row.search_half_width_hz].join(',')).join('\r\n');const url=URL.createObjectURL(new Blob(['\uFEFF'+csv],{type:'text/csv;charset=utf-8'}));const a=document.createElement('a');a.href=url;a.download='FFT_harmonics.csv';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
 return {calculate,draw,settings,restore,stop,open};
})();
