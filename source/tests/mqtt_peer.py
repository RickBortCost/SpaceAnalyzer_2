"""Deterministic local MQTT 3.1.1/5 wire peer; not a production broker.

Exercises actual TCP/TLS, CONNECT/auth, SUBACK ACL failures, QoS 0/1/2,
retained delivery and connection loss against the unmodified Paho client.
No public brokers or physical devices are contacted.
"""
import json
import socket
import ssl
import struct
import threading
import time


def varint(n):
    out=bytearray()
    while True:
        digit=n%128;n//=128;out.append(digit|(128 if n else 0))
        if not n:return bytes(out)


def text(value):
    raw=value.encode('utf-8');return struct.pack('!H',len(raw))+raw


def read_exact(sock,n):
    out=bytearray()
    while len(out)<n:
        value=sock.recv(n-len(out))
        if not value:raise EOFError()
        out.extend(value)
    return bytes(out)


def packet(sock):
    first=read_exact(sock,1)[0];length=0;scale=1
    for _ in range(4):
        digit=read_exact(sock,1)[0];length+=(digit&127)*scale
        if not digit&128:return first,read_exact(sock,length)
        scale*=128
    raise ValueError('Bad packet length')


def skip_varint(data,pos):
    value=0;scale=1
    for _ in range(4):
        digit=data[pos];pos+=1;value+=(digit&127)*scale
        if not digit&128:return value,pos
        scale*=128
    raise ValueError('Bad varint')


def get_text(data,pos):
    size=struct.unpack_from('!H',data,pos)[0];pos+=2
    return data[pos:pos+size].decode('utf-8'),pos+size


def matches(pattern,topic):
    if topic.startswith('$') and not pattern.startswith('$'):return False
    p=pattern.split('/');v=topic.split('/')
    for i,level in enumerate(p):
        if level=='#':return True
        if i>=len(v) or level not in ('+',v[i]):return False
    return len(p)==len(v)


class Connection:
    def __init__(self,sock):
        self.sock=sock;self.lock=threading.Lock();self.filters=[];self.version=4
        self.alive=True;self.mid=0;self.auth=None;self.client_id=''

    def send(self,header,body=b''):
        with self.lock:self.sock.sendall(bytes([header])+varint(len(body))+body)

    def publish(self,topic,payload,qos=0,retain=False,dup=False,mid=None):
        if not self.alive:return
        matching=[min(qos,q) for p,q in self.filters if matches(p,topic)]
        if not matching:return
        qos=max(matching)
        with self.lock:
            self.mid=self.mid%65535+1;identifier=mid or self.mid
            body=text(topic)+(struct.pack('!H',identifier) if qos else b'')+(b'\0' if self.version==5 else b'')+payload
            self.sock.sendall(bytes([0x30|(qos<<1)|(int(retain))|(int(dup)<<3)])+varint(len(body))+body)

    def close(self):
        self.alive=False
        try:self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:pass
        self.sock.close()


class Peer:
    def __init__(self,auth=None,reject_subscribe=False,tls=None,retained=None):
        self.auth=auth;self.reject_subscribe=reject_subscribe;self.tls=tls;self.retained=retained or {}
        self.sock=socket.socket();self.sock.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
        self.sock.bind(('127.0.0.1',0));self.sock.listen();self.sock.settimeout(.1);self.port=self.sock.getsockname()[1]
        self.connections=[];self.workers=[];self.guard=threading.RLock();self.stop=threading.Event()
        self.opcodes=[];self.acks=[];self.errors=[];self.connects=0
        self.thread=threading.Thread(target=self._accept,name='MQTT-test-peer',daemon=True);self.thread.start()

    def _accept(self):
        while not self.stop.is_set():
            try:sock,_=self.sock.accept()
            except socket.timeout:continue
            except OSError:break
            sock.settimeout(2);sock.setsockopt(socket.IPPROTO_TCP,socket.TCP_NODELAY,1)
            try:
                if self.tls:sock=self.tls.wrap_socket(sock,server_side=True)
            except (ssl.SSLError,OSError):sock.close();continue
            sock.settimeout(None);connection=Connection(sock)
            with self.guard:self.connections.append(connection)
            t=threading.Thread(target=self._run,args=(connection,),daemon=True);self.workers.append(t);t.start()

    def _run(self,c):
        try:
            header,data=packet(c.sock);assert header==0x10
            proto,pos=get_text(data,0);assert proto=='MQTT'
            c.version=data[pos];flags=data[pos+1];pos+=4
            if c.version==5:
                length,pos=skip_varint(data,pos);pos+=length
            c.client_id,pos=get_text(data,pos)
            assert not flags&4,'Test peer expects no client Will'
            username=password=''
            if flags&128:username,pos=get_text(data,pos)
            if flags&64:password,pos=get_text(data,pos)
            c.auth=(username,password)
            with self.guard:self.connects+=1;self.opcodes.append(1)
            denied=self.auth is not None and c.auth!=self.auth
            c.send(0x20,bytes([0,0x86 if c.version==5 and denied else 4 if denied else 0])+(b'\0' if c.version==5 else b''))
            if denied:return
            while c.alive and not self.stop.is_set():
                header,data=packet(c.sock);kind=header>>4
                with self.guard:self.opcodes.append(kind)
                if kind==8:
                    mid=data[:2];pos=2
                    if c.version==5:
                        length,pos=skip_varint(data,pos);pos+=length
                    filters=[]
                    while pos<len(data):
                        topic,pos=get_text(data,pos);qos=data[pos]&3;pos+=1;filters.append((topic,qos))
                    c.filters=[] if self.reject_subscribe else filters
                    granted=bytes([0x87 if c.version==5 else 0x80]*len(filters)) if self.reject_subscribe else bytes(q for _,q in filters)
                    c.send(0x90,mid+(b'\0' if c.version==5 else b'')+granted)
                    if not self.reject_subscribe:
                        for topic,payload in self.retained.items():c.publish(topic,payload,2,True)
                elif kind==4:
                    with self.guard:self.acks.append((1,struct.unpack_from('!H',data)[0]))
                elif kind==5:c.send(0x62,data[:2])
                elif kind==7:
                    with self.guard:self.acks.append((2,struct.unpack_from('!H',data)[0]))
                elif kind==12:c.send(0xd0)
                elif kind==14:break
                else:raise AssertionError('Unexpected client opcode '+str(kind))
        except (EOFError,OSError):pass
        except Exception as exc:self.errors.append(repr(exc))
        finally:c.close()

    def publish(self,topic,value,qos=0,retain=False,dup=False,mid=None):
        payload=value if isinstance(value,bytes) else value.encode('utf-8') if isinstance(value,str) else json.dumps(value,separators=(',',':')).encode('utf-8')
        with self.guard:connections=list(self.connections)
        for c in connections:
            if c.alive:
                try:c.publish(topic,payload,qos,retain,dup,mid)
                except OSError:pass

    def drop_all(self):
        with self.guard:connections=list(self.connections)
        for c in connections:c.close()

    def close(self):
        self.stop.set();self.sock.close();self.drop_all();self.thread.join(2)
        for worker in self.workers:worker.join(2)


def until(condition,timeout=5):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        if condition():return
        time.sleep(.01)
    raise AssertionError('MQTT peer timeout')
