"""Original-sample block FFT with separate coherent and energy normalization."""
import cmath
import math
import statistics


def fft(values):
    a=[complex(v) for v in values];n=len(a);j=0
    for i in range(1,n):
        bit=n>>1
        while j&bit:j^=bit;bit>>=1
        j^=bit
        if i<j:a[i],a[j]=a[j],a[i]
    size=2
    while size<=n:
        root=cmath.exp(-2j*math.pi/size)
        for base in range(0,n,size):
            twiddle=1+0j
            for k in range(size//2):
                u=a[base+k];v=twiddle*a[base+k+size//2]
                a[base+k],a[base+k+size//2]=u+v,u-v;twiddle*=root
        size*=2
    return a[:n//2+1]


def number(o,key,default,low,high):
    try:v=float(o.get(key,default))
    except (TypeError,ValueError):raise ValueError('Parametro FFT non valido: '+key) from None
    if not math.isfinite(v) or not low<=v<=high:raise ValueError(f'{key}: valore tra {low} e {high}')
    return v


def harmonic_analysis(power, values, df, fs, options):
    """Find candidate peaks around F1..5F1; never infer missing fundamentals."""
    mode=options.get('fundamental_mode','auto')
    if mode not in ('auto','manual'):raise ValueError('Modalita fondamentale non valida')
    tolerance=number(options,'harmonic_tolerance_percent',3,0,20)/100
    strongest=max(range(1,len(power)),key=power.__getitem__)
    f1=number(options,'fundamental_hz',df,df,fs/2) if mode=='manual' else strongest*df
    note=('Automatica: F1 e il picco AC dominante, che puo essere un armonico. '
          'Impostare F1 manualmente quando si conosce la frequenza fondamentale.' if mode=='auto' else 'F1 impostata manualmente.')
    note+=' Frequenze misurate sulle righe FFT; incertezza di risoluzione delta-f. I picchi in banda sono candidati, non prova di una relazione armonica.'
    if power[strongest]<=0:
        return dict(fundamental_hz=None,mode=mode,rows=[],note='Nessuna componente AC misurabile.')
    local=[k for k in range(1,len(power)) if power[k]>power[strongest]*1e-12 and power[k]>power[k-1] and (k==len(power)-1 or power[k]>=power[k+1])]
    results=[]
    for order in range(1,6):
        target=order*f1;radius=min(max(2*df,target*tolerance),.4*f1)
        row=dict(order=order,target_hz=target,measured_hz=None,amplitude=None,bin_index=None,deviation_hz=None,search_half_width_hz=radius)
        if target>fs/2+df*1e-9:row['status']='Oltre Nyquist'
        elif f1<2*df:row['status']='Risoluzione insufficiente'
        else:
            candidates=[k for k in local if abs(k*df-target)<=radius+df*1e-9]
            if candidates:
                k=max(candidates,key=power.__getitem__);row.update(measured_hz=k*df,amplitude=values[k],bin_index=k,deviation_hz=k*df-target,status='Picco in banda')
            else:row['status']='Nessun picco in banda'
        results.append(row)
    return dict(fundamental_hz=f1,mode=mode,rows=results,note=note,
                tolerance_percent=tolerance*100,numerical_power_floor_relative=1e-12)


def analyze(rows,nodeid,start,end,window='hann',detrend=True,options=None):
    o=dict(options or {});points=[];bad=0
    policy=o.get('gap_policy','reject')
    if policy not in ('reject','skip'):raise ValueError('Politica qualita FFT non valida')
    for r in rows:
        if r.get('node_id')!=nodeid:continue
        try:t=float(r['timestamp_ms'])
        except (KeyError,TypeError,ValueError):continue
        if not math.isfinite(t) or not start<=t<=end:continue
        try:v=float(r.get('value_numeric'))
        except (TypeError,ValueError):v=math.nan
        q=r.get('quality') or ('Good' if str(r.get('good',True)).lower() in ('1','true') else 'Bad')
        good=q=='Good' and math.isfinite(v);bad+=not good
        ft=r.get('ads_filetime')
        if ft:
            try:ft=int(ft)
            except (TypeError,ValueError):raise ValueError('Timestamp ADS non valido') from None
        points.append((t,v,good,ft))
        if len(points)>262144:raise ValueError('FFT: massimo 262.144 campioni; ridurre intervallo')
    if len(points)<16:raise ValueError('FFT: servono almeno 16 campioni')
    if bad and policy=='reject':raise ValueError('FFT: campioni Bad/Uncertain o non numerici')
    deltas=[(b[3]-a[3])/10000 if a[3] and b[3] else b[0]-a[0] for a,b in zip(points,points[1:])]
    if min(deltas)<=0:raise ValueError('FFT: timestamp non strettamente crescenti')
    dt=statistics.median(deltas);fs=1000/dt
    tolerance=number(o,'timing_tolerance_percent',5,.01,25)/100
    gaps={i+1 for i,d in enumerate(deltas) if abs(d-dt)>max(dt*tolerance,.001)}
    if gaps and policy=='reject':raise ValueError('FFT: buco temporale o campionamento irregolare; nessun ricampionamento automatico')
    mode=o.get('resolution_mode','samples')
    if mode=='samples':desired=number(o,'block_size',0,0,65536)
    elif mode=='hz':desired=fs/number(o,'resolution_value',.1,.000001,fs/2)
    elif mode=='seconds':desired=fs*number(o,'resolution_value',10,.000001,86400)
    else:raise ValueError('Modalita risoluzione non valida')
    n=min(2048,1<<(len(points).bit_length()-1)) if desired==0 else max(16,1<<math.ceil(math.log2(max(1,desired))))
    if n>65536:raise ValueError('FFT: massimo 65536 campioni per blocco')
    if len(points)<n:raise ValueError(f'FFT: servono {n} campioni ({n/fs:.3f} s); disponibili {len(points)}')
    coefficients={'rectangular':[1.], 'hann':[.5,-.5], 'blackmanharris':[.35875,-.48829,.14128,-.01168],
                  'flattop':[.21557895,-.41663158,.277263158,-.083578947,.006947368]}
    if window not in coefficients:raise ValueError('Finestra FFT non valida')
    weights=[sum(c*math.cos(2*math.pi*j*i/n) for j,c in enumerate(coefficients[window])) for i in range(n)]
    sw=sum(weights);sw2=sum(w*w for w in weights);df=fs/n;enbw=fs*sw2/sw**2
    hop=max(1,round(n*(1-number(o,'overlap_percent',50,0,90)/100)))
    average=o.get('averaging','energy');output=o.get('output','peak')
    if average not in ('none','energy','exponential','maxhold'):raise ValueError('Media FFT non valida')
    if output not in ('peak','rms','power','psd','asd'):raise ValueError('Formato FFT non valido')
    count=number(o,'average_count',0,0,512)
    if not count.is_integer():raise ValueError('Numero medie intero richiesto')
    tau=number(o,'tau_seconds',5,.01,3600)
    invalid=[0];discontinuities=[0]
    for i,p in enumerate(points):
        invalid.append(invalid[-1]+(not p[2]));discontinuities.append(discontinuities[-1]+(i in gaps))
    starts=list(range(0,len(points)-n+1,hop));valid=[];excluded=0
    for first in starts:
        if invalid[first+n]!=invalid[first] or discontinuities[first+n]!=discontinuities[first+1]:excluded+=1
        else:valid.append(first)
    if not valid:raise ValueError(f'FFT: nessun blocco continuo Good valido; {excluded} blocchi esclusi')
    if count:valid=valid[-int(count):]
    if len(valid)>512 or len(valid)*n*math.log2(n)>16000000:raise ValueError('FFT: limitare numero medie, intervallo o overlap')
    result=None;hold=None;last=None;previous=None;used=set()
    for ordinal,first in enumerate(valid):
        values=[p[1] for p in points[first:first+n]];mean=statistics.fmean(values)
        transformed=fft([(v-mean)*w for v,w in zip(values,weights)])
        transformed[0]=complex(0 if detrend else abs(mean)*sw)
        power=[];psd=[]
        for k,z in enumerate(transformed):
            factor=1 if k in (0,n//2) else 2;magnitude=abs(z)**2
            power.append(factor*magnitude/sw**2);psd.append(factor*magnitude/(fs*sw2))
        psd[0]=power[0]/df;last=(power,psd)
        if result is None:result=([*power],[*psd]);hold=([*power],[*psd])
        else:
            alpha=1-math.exp(-(points[first][0]-previous)/1000/tau)
            for field in range(2):
                for k,v in enumerate(last[field]):
                    hold[field][k]=max(hold[field][k],v)
                    if average=='energy':result[field][k]+=(v-result[field][k])/(ordinal+1)
                    elif average=='exponential':result[field][k]+=alpha*(v-result[field][k])
                    else:result[field][k]=v
        previous=points[first][0];used.update(range(first,first+n))
    if average=='maxhold':result=hold
    if average=='none':result=last
    def convert(s):
        if output=='psd':return s[1]
        if output=='asd':return [math.sqrt(max(0,v)) for v in s[1]]
        if output=='power':return s[0]
        return [math.sqrt(max(0,v))*(math.sqrt(2) if output=='peak' and k not in (0,n//2) else 1) for k,v in enumerate(s[0])]
    y=convert(result);current=convert(last);maximum=convert(hold)
    frequencies=[k*df for k in range(len(y))]
    low=number(o,'band_min_hz',0,0,fs/2);high=min(fs/2,number(o,'band_max_hz',fs/2,0,1e9))
    band=[k for k,f in enumerate(frequencies) if low<=f<=high]
    if not band:raise ValueError('Nessuna riga FFT nella banda RMS richiesta')
    peak=max(range(1,len(y)),key=y.__getitem__)
    peaks=sorted([i for i in range(1,len(y)-1) if y[i]>y[i-1] and y[i]>=y[i+1]],key=y.__getitem__,reverse=True)[:10]
    unit=str(o.get('unit','u'))[:40].strip() or 'u'
    units={'peak':unit+' peak','rms':unit+' RMS','power':unit+'²','psd':unit+'²/Hz','asd':unit+'/√Hz'}
    harmonics=harmonic_analysis(result[0],y,df,fs,o)
    return dict(harmonics=harmonics,bins=list(map(list,zip(frequencies,y))),current_bins=list(map(list,zip(frequencies,current))),maxhold_bins=list(map(list,zip(frequencies,maximum))),
                samples_original=len(points),samples_fft=n,samples_used=len(used),samples_unused=len(points)-len(used),sample_hz=fs,nyquist_hz=fs/2,
                resolution_hz=df,block_seconds=n/fs,hop_seconds=hop/fs,overlap_percent=100*(1-hop/n),window=window,detrend=bool(detrend),averaging=average,output=output,
                blocks_total=len(starts),blocks_used=len(valid),blocks_excluded=excluded,bad_samples=bad,timing_breaks=len(gaps),jitter_percent=statistics.pstdev(deltas)/dt*100,
                timing_tolerance_percent=tolerance*100,noise_bandwidth_hz=enbw,coherent_gain=sw/n,peak_hz=frequencies[peak],peak_amplitude=y[peak],amplitude_unit=units[output],signal_unit=unit,
                band_min_hz=frequencies[band[0]],band_max_hz=frequencies[band[-1]],band_rms=math.sqrt(sum(result[1][k] for k in band)*df),overall_rms=math.sqrt(sum(result[1])*df),
                rms_kind='inviluppo Max Hold (non RMS temporale)' if average=='maxhold' else 'da PSD selezionata',peaks=[dict(hz=frequencies[k],value=y[k]) for k in peaks],resampled=False,
                time_basis='ADS FILETIME originale' if any(p[3] for p in points) else 'timestamp registrati',first_timestamp_ms=points[valid[0]][0],last_timestamp_ms=points[valid[-1]+n-1][0],
                note='Campioni originali, senza interpolazione o decimazione temporale. Blocchi completi validi; Max Hold e medie riferiti al solo intervallo analizzato.')
