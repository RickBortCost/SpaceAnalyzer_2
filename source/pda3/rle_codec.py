"""Bit-exact Float32 RLE encoding into an unchanged, fixed-capacity PDA3 block."""
from functools import lru_cache
import struct
from .errors import PDA3CapacityError

def _literal(data,n,k):
    out=bytearray();offset=0
    for left in range(k,0,-1):
        count=min(127,n-left+1);out.append(128+count);out.extend(data[offset:offset+4*count]);offset+=4*count;n-=count
    return bytes(out)

@lru_cache(maxsize=8192)
def _constant_plan(n,budget):
    if 4*n+(n+126)//127<=budget<=5*n:return (0,0,n,budget-4*n)
    # These repeat/literal combinations cover the observed <=65535-sample spans.
    for a,b in ((1,0),(0,1),(2,0),(1,1),(0,2),(3,0),(2,1),(3,1)):
        base=5*a+7*b;low=a+b;high=126*a+65535*b
        if budget==base and low<=n<=high:return (a,b,0,0)
        diff=budget-base
        if diff<5:continue
        lo=max(1,diff-4*(n-low),(diff+508)//509)
        hi=min(n-low,diff//5,diff-4*max(0,n-high))
        k=lo+(diff-lo)%4
        if k<=hi:
            literals=(diff-k)//4
            if k<=literals<=127*k and low<=n-literals<=high:return (a,b,literals,k)
    return None

def _constant(word,n,budget):
    plan=_constant_plan(n,budget)
    if plan is None:raise PDA3CapacityError('PDA3: capacita RLE costante non rappresentabile esattamente')
    a,b,l,k=plan;out=bytearray();remaining=n-l
    for maximum in [126]*a+[65535]*b:
        after=a+b-1;count=min(maximum,remaining-after)
        out.extend(bytes((count,))+word if maximum==126 else b'\x7f'+word+struct.pack('<H',count))
        remaining-=count
        if maximum==126:a-=1
        else:b-=1
    if l:out.extend(_literal(word*l,l,k))
    return bytes(out)

@lru_cache(maxsize=4096)
def _choices(n,repeat,cap):
    if repeat:return tuple(b for b in range(5 if n<=126 else 7,min(5*n,cap)+1) if _constant_plan(n,b) is not None)
    return tuple(range(4*n+(n+126)//127,min(5*n,cap)+1))

def _sample_dp(data,words,capacity):
    """Exact fallback also permits literals crossing maximal-run boundaries."""
    n=len(words)
    if n>5000:raise ValueError('PDA3: blocco fuori dal limite del ricodificatore esatto')
    runs=[1]*n
    for i in range(n-2,-1,-1):
        if words[i]==words[i+1]:runs[i]=runs[i+1]+1
    suffix=[0]*(n+1);suffix[n]=1;mask=(1<<(capacity+1))-1
    for i in range(n-1,-1,-1):
        bits=0
        for count in range(1,min(127,n-i)+1):bits|=suffix[i+count]<<(4*count+1)
        for count in range(1,min(runs[i],65535)+1):
            if count<=126:bits|=suffix[i+count]<<5
            bits|=suffix[i+count]<<7
        suffix[i]=bits&mask
    if not (suffix[0]>>capacity)&1:raise PDA3CapacityError('PDA3: nessuna codifica lossless alla capacita esatta del blocco')
    out=bytearray();i=0;remaining=capacity
    while i<n:
        found=False
        for count in range(1,min(127,n-i)+1):
            cost=4*count+1
            if cost<=remaining and (suffix[i+count]>>(remaining-cost))&1:
                out.append(128+count);out.extend(data[i*4:(i+count)*4]);found=True;break
        if not found:
            for count in range(1,min(runs[i],65535)+1):
                for cost in (5,7) if count<=126 else (7,):
                    if cost<=remaining and (suffix[i+count]>>(remaining-cost))&1:
                        out.extend(bytes((count,))+words[i] if cost==5 else b'\x7f'+words[i]+struct.pack('<H',count));found=True;break
                if found:break
        if not found:raise ValueError('PDA3: ricostruzione budget non valida')
        i+=count;remaining-=cost
    return bytes(out)

def encode_exact(data,capacity):
    """Never average values, change Float32 bits or append padding samples."""
    n=len(data)//4
    if not n or len(data)!=n*4 or capacity<1:raise ValueError('PDA3: blocco Float32 non valido')
    words=[data[i:i+4] for i in range(0,len(data),4)]
    if all(w==words[0] for w in words):
        result=_constant(words[0],n,capacity)
        if len(result)!=capacity:raise ValueError('PDA3: budget costante non rispettato')
        return result
    if 4*n+(n+126)//127<=capacity<=5*n:return _literal(data,n,capacity-4*n)
    units=[];start=0;unique=0
    while start<n:
        end=start+1
        while end<n and words[end]==words[start]:end+=1
        if end-start>=2:
            if unique<start:units.append((unique,start,False))
            units.append((start,end,True));unique=end
        start=end
    if unique<n:units.append((unique,n,False))
    choices=[_choices(end-start,repeat,capacity) for start,end,repeat in units]
    # Report the real minimum for each span, even when choices was clipped at
    # capacity; capacity+1 is only a rejection sentinel, never a byte estimate.
    def min_span(start,end,repeat):
        length=end-start
        if not repeat:return 4*length+(length+126)//127
        full,rest=divmod(length,65535)
        return full*7+(0 if not rest else 5 if rest<=126 else 7)
    minimum=sum(min_span(*unit) for unit in units)
    if minimum>capacity:raise PDA3CapacityError(f'PDA3: dati non comprimibili nel blocco ({minimum} byte minimi, capacita {capacity}); Parquet conservato',required_bytes=minimum,capacity=capacity)
    # Exact byte-budget allocation across independent bit-preserving spans.
    suffix=[0]*(len(units)+1);suffix[-1]=1;mask=(1<<(capacity+1))-1
    for i in range(len(units)-1,-1,-1):
        bits=0
        for cost in choices[i]:bits|=suffix[i+1]<<cost
        suffix[i]=bits&mask
    if not (suffix[0]>>capacity)&1:return _sample_dp(data,words,capacity)
    out=bytearray();remaining=capacity
    for i,(start,end,repeat) in enumerate(units):
        cost=next(c for c in choices[i] if c<=remaining and (suffix[i+1]>>(remaining-c))&1)
        section=data[start*4:end*4]
        out.extend(_constant(words[start],end-start,cost) if repeat else _literal(section,end-start,cost-4*(end-start)))
        remaining-=cost
    if remaining or len(out)!=capacity:raise ValueError('PDA3: budget finale non rispettato')
    return bytes(out)
