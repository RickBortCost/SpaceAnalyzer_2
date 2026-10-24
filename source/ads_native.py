"""Windows ADS transport through the installed TwinCAT router (TcAdsDll x64).
Only read operations and symbol handle acquisition/release are exposed.
No Beckhoff binaries are bundled. The installed router owns AMS identity/routes.
"""
import asyncio
import ctypes as C
import os
from pathlib import Path
import struct
from contextlib import suppress
from ads_client import ADSClient, ADSError, MAX_PACKET, netid

I32=C.c_int32;U32=C.c_uint32;U16=C.c_uint16;PTR=C.c_void_p
class AmsAddr(C.Structure):
    _pack_=1
    _fields_=[('netid',C.c_ubyte*6),('port',U16)]
class Version(C.Structure):
    _pack_=1
    _fields_=[('major',C.c_ubyte),('minor',C.c_ubyte),('build',U16)]


def load_library(explicit=''):
    if os.name!='nt':raise ValueError('TwinCAT locale richiede Windows e TcAdsDll a 64 bit installata sul PC')
    candidates=[]
    if explicit:
        p=Path(explicit)
        if not p.is_absolute():raise ValueError('Percorso TcAdsDll: inserire un percorso assoluto')
        candidates=[p]
    else:
        bases=[Path(os.environ.get('SystemDrive','C:')+'\\TwinCAT')]
        for key in ('TWINCAT3DIR','TWINCAT2DIR'):
            if os.environ.get(key):bases.append(Path(os.environ[key]))
        with suppress(ImportError,OSError):
            import winreg
            for key in (r'SOFTWARE\Beckhoff\TwinCAT3',r'SOFTWARE\WOW6432Node\Beckhoff\TwinCAT3'):
                with suppress(OSError):
                    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,key) as h:
                        for field in ('InstallDir','TwinCATDir'):
                            with suppress(OSError):bases.append(Path(winreg.QueryValueEx(h,field)[0]))
        for base in bases:
            for directory in (base,base.parent):
                candidates.extend(directory/p for p in ('Common64/TcAdsDll.dll','3.1/Common64/TcAdsDll.dll','AdsApi/TcAdsDll/x64/TcAdsDll.dll'))
        candidates.append(Path(os.environ.get('SystemRoot',r'C:\Windows'))/'System32/TcAdsDll.dll')
    errors=[]
    for p in dict.fromkeys(candidates):
        if not p.is_file():continue
        try:
            # WinDLL uses stdcall; never search the working directory for a DLL.
            dll=C.WinDLL(str(p.resolve()),winmode=0x1100)
            configure_library(dll)
            return dll,str(p)
        except (OSError,AttributeError) as e:errors.append(f'{p}: {e}')
    raise ValueError('TcAdsDll x64 non disponibile. Installare i componenti ADS/TwinCAT sul PC o indicare il percorso della DLL a 64 bit. '+('; '.join(errors) if errors else 'Ricerca eseguita nelle cartelle TwinCAT Common64 e System32.'))


def configure_library(dll):
    signatures={
        'AdsPortOpenEx':[], 'AdsPortCloseEx':[I32],
        'AdsGetLocalAddressEx':[I32,C.POINTER(AmsAddr)],
        'AdsSyncSetTimeoutEx':[I32,U32],
        'AdsSyncReadDeviceInfoReqEx':[I32,C.POINTER(AmsAddr),PTR,C.POINTER(Version)],
        'AdsSyncReadStateReqEx':[I32,C.POINTER(AmsAddr),C.POINTER(U16),C.POINTER(U16)],
        'AdsSyncReadReqEx2':[I32,C.POINTER(AmsAddr),U32,U32,U32,PTR,C.POINTER(U32)],
        'AdsSyncReadWriteReqEx2':[I32,C.POINTER(AmsAddr),U32,U32,U32,PTR,U32,PTR,C.POINTER(U32)],
        'AdsSyncWriteReqEx':[I32,C.POINTER(AmsAddr),U32,U32,U32,PTR],
    }
    for name,args in signatures.items():
        function=getattr(dll,name);function.argtypes=args;function.restype=I32


class NativeADSClient(ADSClient):
    def __init__(self,cfg,catalog=False):
        super().__init__(cfg,catalog);self.dll=None;self.port=0
        self.address=AmsAddr((C.c_ubyte*6)(*netid(cfg['ads_target_netid'])),cfg['ads_port'])

    @staticmethod
    def check(code):
        if code:raise ADSError(C.c_uint32(code).value,'TwinCAT locale')

    async def threaded(self,function,*args):
        # A cancelled coroutine must not close/reuse an ADS port while a native
        # request still runs. Wait for its configured timeout before propagating.
        task=asyncio.create_task(asyncio.to_thread(function,*args))
        try:return await asyncio.shield(task)
        except asyncio.CancelledError:
            with suppress(Exception):await task
            raise

    def open_native(self):
        self.dll,path=load_library(self.cfg.get('ads_dll_path',''))
        self.port=int(self.dll.AdsPortOpenEx())
        if self.port<=0:raise ConnectionError('Router TwinCAT locale non disponibile: verificare che il servizio TwinCAT System sia avviato')
        try:
            self.check(self.dll.AdsSyncSetTimeoutEx(self.port,self.cfg['ads_timeout_ms']))
            local=AmsAddr();self.check(self.dll.AdsGetLocalAddressEx(self.port,C.byref(local)))
            source_id='.'.join(map(str,local.netid))
            self.info={'transport':'TwinCAT locale / TcAdsDll','source_netid':source_id,'source_port':int(local.port),
                'local_ip':'gestito dal router TwinCAT','target_netid':self.cfg['ads_target_netid'],
                'target_port':self.cfg['ads_port'],'dll_path':path,'timestamps':'Local only (ADS Read)'}
            self.writer=True  # Base class handle lifecycle checks for an open transport.
        except BaseException:
            self.dll.AdsPortCloseEx(self.port);self.port=0;raise

    async def connect(self):
        try:
            await self.threaded(self.open_native)
            data=await self.command(1,b'');self.check_result(data)
            self.info.update(device=data[8:24].split(b'\0')[0].decode('utf-8',errors='replace'),version=f'{data[4]}.{data[5]}.{struct.unpack_from("<H",data,6)[0]}')
            await self.read_state()
        except BaseException:
            await self.abort();raise

    def native_command(self,cmd,data):
        if not self.port:raise ConnectionError('Porta ADS locale chiusa')
        port,addr=self.port,C.byref(self.address)
        if cmd==1:
            name=C.create_string_buffer(16);version=Version()
            self.check(self.dll.AdsSyncReadDeviceInfoReqEx(port,addr,name,C.byref(version)))
            return struct.pack('<IBBH',0,version.major,version.minor,version.build)+name.raw
        if cmd==4:
            state,device=U16(),U16();self.check(self.dll.AdsSyncReadStateReqEx(port,addr,C.byref(state),C.byref(device)))
            return struct.pack('<IHH',0,state.value,device.value)
        if cmd==3:
            g,o,n=struct.unpack_from('<III',data)
            if g!=0xF006 or o!=0 or n!=4 or len(data)!=16:raise ValueError('Scrittura PLC non consentita')
            buf=C.create_string_buffer(data[12:]);self.check(self.dll.AdsSyncWriteReqEx(port,addr,g,o,n,buf))
            return struct.pack('<I',0)
        if cmd not in (2,9):raise ValueError('Comando ADS non consentito')
        g,o,n=struct.unpack_from('<III',data)
        if n>MAX_PACKET:raise ValueError('Lettura ADS troppo grande')
        buffer=C.create_string_buffer(n);count=U32()
        if cmd==2:
            self.check(self.dll.AdsSyncReadReqEx2(port,addr,g,o,n,buffer,C.byref(count)))
        else:
            if g not in (0xF003,0xF009,0xF011,0xF080):raise ValueError('ADS ReadWrite non consentita')
            length=struct.unpack_from('<I',data,12)[0]
            if len(data)!=16+length:raise ValueError('Payload ADS non valido')
            written=C.create_string_buffer(data[16:])
            self.check(self.dll.AdsSyncReadWriteReqEx2(port,addr,g,o,n,buffer,length,written,C.byref(count)))
        if count.value>n:raise ValueError('Lunghezza ADS DLL non valida')
        return struct.pack('<II',0,count.value)+buffer.raw[:count.value]

    async def command(self,command,data):
        async with self.lock:
            return await self.threaded(self.native_command,command,data)

    async def abort(self):
        async with self.lock:
            if self.port:
                port,self.port=self.port,0;self.writer=None
                await self.threaded(self.dll.AdsPortCloseEx,port)

    async def disconnect(self):
        try:
            if self.writer:await self.release_handles()
        finally:await self.abort()


def create_ads_client(cfg,catalog=False):
    if cfg.get('ads_transport','tcp')=='native':return NativeADSClient(cfg,catalog)
    return ADSClient(cfg,catalog)
