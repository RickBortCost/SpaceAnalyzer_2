"""Bounded immutable references to original samples of paused live charts."""
from collections import OrderedDict
import secrets
import threading

class Snapshots:
    def __init__(self,max_items=8,max_points=2000000):
        self.items=OrderedDict();self.lock=threading.Lock();self.max_items=max_items;self.max_points=max_points
    def create(self,points):
        captured={key:tuple(tuple(p) for p in values) for key,values in points.items()}
        count=sum(map(len,captured.values()))
        if not count:raise ValueError('Nessun campione originale disponibile per congelare questo grafico; riprendere Play e attendere dati')
        if count>self.max_points:raise ValueError('Vista congelata troppo grande; ridurre segnali o buffer')
        with self.lock:
            while self.items and (len(self.items)>=self.max_items or sum(item[1] for item in self.items.values())+count>self.max_points):self.items.popitem(last=False)
            key=secrets.token_hex(16);self.items[key]=(captured,count)
        return key,count
    def get(self,key):
        with self.lock:
            if key not in self.items:raise ValueError('Campioni della vista congelata non piu disponibili (riavvio o limite memoria). Riprendere Play e congelare nuovamente, oppure aprire la registrazione')
            return self.items[key][0]
    def create_reference(self,reference):
        with self.lock:
            while len(self.items)>=self.max_items:self.items.popitem(last=False)
            key=secrets.token_hex(16);self.items[key]=(reference,0)
        return key,reference.count
    def delete(self,key):
        with self.lock:self.items.pop(key,None)

snapshots=Snapshots()
