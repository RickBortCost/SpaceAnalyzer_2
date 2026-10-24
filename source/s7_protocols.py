"""Mode selection/configuration for the three independent S7 transports."""
from s7_client import S7Client, DEFAULTS as CLASSIC_DEFAULTS, validate_config as validate_classic, normalize_tags, protocol_of
from s7_catalog import parse_node as parse_tag, normalize_udp_tags, integer
from s7_symbolic import S7SymbolicClient
from s7_udp import S7UDPClient

S7_TYPES=(S7Client,S7SymbolicClient,S7UDPClient)
DEFAULTS={**CLASSIC_DEFAULTS,'s7_mode':'classic','s7_symbolic_password':'','s7_symbolic_tls':False,
    's7_tls_ca':'','s7_tls_cert':'','s7_tls_key':'','s7_legacy_key_fallback':True,
    's7_symbolic_root':'s7:sym:/','s7_udp_root':'s7:udp:/',
    's7_udp_bind':'0.0.0.0','s7_udp_port':4170,'s7_udp_sender_port':0,
    's7_udp_module':200,'s7_udp_length':16,'s7_udp_endian':'big',
    's7_udp_timestamp_offset':-1,'s7_udp_watchdog_ms':1000,'s7_udp_queue':2048,'s7_udp_tags':[]}


def validate_config(cfg):
    for k,v in DEFAULTS.items(): cfg.setdefault(k,v)
    validate_classic(cfg)
    if cfg['s7_mode'] not in ('classic','symbolic','udp'): raise ValueError('Modalità S7 non valida')
    for k in ('s7_symbolic_tls','s7_legacy_key_fallback'):
        if not isinstance(cfg[k],bool): raise ValueError(k+': valore booleano richiesto')
    for k in ('s7_symbolic_password','s7_tls_ca','s7_tls_cert','s7_tls_key'):
        if not isinstance(cfg[k],str) or len(cfg[k])>4096 or '\0' in cfg[k]: raise ValueError('Parametro S7 simbolico non valido: '+k)
    if bool(cfg['s7_tls_cert'])!=bool(cfg['s7_tls_key']): raise ValueError('TLS client richiede sia certificato sia chiave privata')
    cfg['s7_symbolic_root']=parse_tag(cfg['s7_symbolic_root'] or 's7:sym:/',True)['nodeid']
    if not cfg['s7_symbolic_root'].startswith('s7:sym:'): raise ValueError('Radice simbolica S7 non valida')
    if cfg['s7_udp_root']!='s7:udp:/': raise ValueError('Radice UDP: usare s7:udp:/')
    if not isinstance(cfg['s7_udp_bind'],str) or not cfg['s7_udp_bind'].strip() or any(c in cfg['s7_udp_bind'] for c in '/\\\0 \r\n'):
        raise ValueError('Indirizzo IPv4 locale UDP non valido')
    cfg['s7_udp_bind']=cfg['s7_udp_bind'].strip()
    for k,low,high in [('s7_udp_port',1,65535),('s7_udp_sender_port',0,65535),('s7_udp_module',200,3263),
        ('s7_udp_length',8,4096),('s7_udp_timestamp_offset',-1,4082),('s7_udp_watchdog_ms',20,60000),('s7_udp_queue',32,16384)]:
        cfg[k]=integer(cfg[k],k,low,high)
    # Generic module index bands documented by iba; exclude Real/Integer/Request.
    if not any(start<=cfg['s7_udp_module']<=start+63 for start in (200,1200,2200,3200)):
        raise ValueError('Modulo UDP Generic: 200–263, 1200–1263, 2200–2263 o 3200–3263')
    if cfg['s7_udp_length']%2: raise ValueError('Lunghezza telegramma UDP deve essere pari (header incluso)')
    if cfg['s7_udp_endian'] not in ('big','little'): raise ValueError('Ordine byte UDP non valido')
    if cfg['s7_udp_timestamp_offset']>=0 and cfg['s7_udp_timestamp_offset']+8>cfg['s7_udp_length']-6:
        raise ValueError('Timestamp PLC di 8 byte oltre il payload UDP')
    cfg['s7_udp_tags']=normalize_udp_tags(cfg['s7_udp_tags'],cfg['s7_udp_length']-6)
    return cfg


def create_s7_client(cfg):
    validate_config(cfg)
    return {'classic':S7Client,'symbolic':S7SymbolicClient,'udp':S7UDPClient}[cfg['s7_mode']](cfg)


def root(cfg):
    return cfg[{'classic':'s7_root','symbolic':'s7_symbolic_root','udp':'s7_udp_root'}[cfg.get('s7_mode','classic')]]


def endpoint(cfg):
    if cfg.get('s7_mode')=='udp':
        return f"s7://{cfg['s7_host']}/udp/{cfg['s7_udp_port']}/{cfg['s7_udp_module']}"
    return f"s7://{cfg['s7_host']}:{cfg['s7_tcp_port']}/{cfg['s7_rack']}/{cfg['s7_slot']}?mode={cfg.get('s7_mode','classic')}"


def selection_guard(cfg,nids):
    if any(parse_tag(n)['mode']!=cfg.get('s7_mode','classic') for n in nids if protocol_of(n)=='s7'):
        raise ValueError('La selezione contiene segnali di un’altra modalità S7. Cambiare modalità nel popup Connessioni.')
