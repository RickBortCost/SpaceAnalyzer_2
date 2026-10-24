"""Bounded per-connection handoff; overflow is a terminal, attributable event."""
from collections import deque
import threading
import time


_SCALARS=frozenset((int,float,bool,type(None)))

def size_hint(rows):
    # Conservative UTF-8/objects budget, bounded without serializing in callbacks.
    size=128
    for row in rows:
        size+=512
        for value in row.values():
            kind=type(value)
            if kind is str:size+=4*len(value)
            elif kind in _SCALARS:continue
            elif isinstance(value,str):size+=4*len(value)
            elif isinstance(value,(bytes,bytearray,memoryview)):size+=len(value)
            elif isinstance(value,(list,tuple,dict)):size+=len(repr(value).encode('utf-8'))*2
    return size

class ConnectionBuffer:
    def __init__(self,max_samples=32768,max_bytes=32*1024**2,max_batches=4096):
        self.max_samples=max_samples;self.max_bytes=max_bytes;self.max_batches=max_batches
        self.items=deque();self.lock=threading.Lock();self.samples=self.bytes=0
        self.accepted=self.completed=self.lost=0;self.failed=False;self.high_water=0
    def has_room(self,count,size):
        with self.lock:return not self.failed and self.samples+count<=self.max_samples and self.bytes+size<=self.max_bytes and len(self.items)<self.max_batches
    def put(self,item,count,size):
        with self.lock:
            if self.failed:self.lost+=count;return False
            if self.samples+count>self.max_samples or self.bytes+size>self.max_bytes or len(self.items)>=self.max_batches:
                self.failed=True;self.lost+=count;return False
            self.accepted+=1;self.items.append((self.accepted,item,count,size,time.monotonic_ns()))
            self.samples+=count;self.bytes+=size;self.high_water=max(self.high_water,self.samples);return True
    def get(self):
        with self.lock:
            if not self.items:return None
            serial,item,count,size,queued=self.items.popleft();self.samples-=count;self.bytes-=size
            return serial,item,count,queued
    def done(self,serial):
        with self.lock:self.completed=max(self.completed,serial)
    def snapshot(self):
        with self.lock:return dict(samples=self.samples,bytes=self.bytes,capacity_samples=self.max_samples,
            capacity_bytes=self.max_bytes,capacity_batches=self.max_batches,accepted_batches=self.accepted,
            completed_batches=self.completed,lost_samples=self.lost,failed=self.failed,high_water=self.high_water)
