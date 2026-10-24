import asyncio
import atexit
import json
import itertools
import logging
import math
import mimetypes
import os
import secrets
import shutil
import socket
import sys
import threading
import time
import webbrowser
from collections import deque
from live_overview import LiveBuffer
from live_archive import LiveArchive, Reference as ArchiveReference, point_rows
from concurrent.futures import TimeoutError as FutureTimeout
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

from flask import Flask, jsonify, render_template, request, send_file
from werkzeug.exceptions import HTTPException
from werkzeug.serving import make_server
from asyncua import Client, ua
from asyncua.crypto.security_policies import (
    SecurityPolicyAes128Sha256RsaOaep,
    SecurityPolicyAes256Sha256RsaPss,
    SecurityPolicyBasic256Sha256,
)
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from ads_native import create_ads_client
from s7_protocols import S7_TYPES, S7Client, S7SymbolicClient, S7UDPClient, create_s7_client, DEFAULTS as S7_DEFAULTS, parse_tag, normalize_tags, protocol_of, validate_config as validate_s7_config, root as s7_root, endpoint as s7_endpoint, selection_guard as s7_selection_guard
from s7_catalog import normalize_udp_tags
from ads_client import ADSClient, ADSError, validate_config as validate_ads_config, symbol_name, symbol_item
from mqtt_source import MQTTClient, DEFAULTS as MQTT_DEFAULTS, parse_address as parse_mqtt_address, validate_config as validate_mqtt_config, endpoint as mqtt_endpoint, value_row as mqtt_value_row
from recordings import Recorder, prepare_format
from recording_manager import RecordingManager
from durable_recording import DurableRecorder
from acquisition_engine import AcquisitionEngine
from acquisition_health import EventStore
from primary_recovery import RecoveryService
from primary_format import typed as primary_typed
from recording_reader import RecordingLibrary
from recording_compare import ComparisonService
from analysis_tools import interval, spectrum, export_rows, selected_rows
from logging.handlers import RotatingFileHandler
from calculation_store import FormulaStore
from calculation_service import CalculationService
from formula_engine import Expression, Plan, number
from ssh_service import SSHService
from ssh_routes import blueprint as ssh_blueprint

# Force deterministic JavaScript MIME types on Windows. This also protects future .mjs use.
mimetypes.add_type('application/javascript', '.js')
mimetypes.add_type('application/javascript', '.mjs')

APP_NAME = "Space Analyzer"
APP_VERSION = "1.29.0"
MAX_SELECTED = 64
MAX_SAMPLES = 360000
MAX_TOTAL_SAMPLES = 2000000

BASE_DIR = (
    Path(getattr(sys, "_MEIPASS"))
    if getattr(sys, "frozen", False)
    else Path(__file__).resolve().parent
)
if os.environ.get("OPCUA_DATA_DIR"):
    DATA_DIR = Path(os.environ["OPCUA_DATA_DIR"]).expanduser().resolve()
elif getattr(sys, "frozen", False) or (BASE_DIR / "bundled-runtime.flag").exists():
    DATA_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "OPCUA_Web_Client"
else:
    DATA_DIR = BASE_DIR
CERT_DIR = DATA_DIR / "certificates"
CONFIG_FILE = DATA_DIR / "config.json"
CERT_FILE = CERT_DIR / "client.der"
KEY_FILE = CERT_DIR / "client-key.pem"
SERVER_CERT_FILE = CERT_DIR / "server.der"
SETTINGS_DIR = DATA_DIR / "settings"
SETTINGS_FILE = SETTINGS_DIR / "saved_settings.json"
SETTINGS_CERT_DIR = SETTINGS_DIR / "certificates"
APPLICATION_DIR = Path(os.environ.get("OPCUA_APP_DIR") or (str(Path(sys.executable).resolve().parent) if getattr(sys, "frozen", False) else str(BASE_DIR))).resolve()
RECORDINGS_DIR = APPLICATION_DIR / "Recordings"
IMPORTED_RECORDINGS_DIR = DATA_DIR / "recording_imports"
acquisition_events = EventStore(DATA_DIR / 'acquisition_events.sqlite')
DurableRecorder.events = acquisition_events
recording = RecordingManager(RECORDINGS_DIR, APP_VERSION, factory=DurableRecorder)
primary_recovery = RecoveryService(RECORDINGS_DIR / '.primary', acquisition_events)
recording_library = RecordingLibrary(RECORDINGS_DIR)
imported_recording_library = RecordingLibrary(IMPORTED_RECORDINGS_DIR)
comparison_service = ComparisonService(IMPORTED_RECORDINGS_DIR)
live_archive = LiveArchive(DATA_DIR / 'live_analysis_cache')
atexit.register(live_archive.close)


def live_buffer(nid, iterable=(), maxlen=None):
    generation=live_archive.generation
    return LiveBuffer(iterable,maxlen=maxlen if maxlen is not None else sample_limit(),
        archive_generation=generation,on_append=lambda point:live_archive.append(generation,nid,point))


app = Flask(__name__, template_folder=str(BASE_DIR / "templates"), static_folder=str(BASE_DIR / "static"))
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024 * 1024  # Local CSV/Parquet import; streamed to disk.
SESSION_TOKEN = secrets.token_urlsafe(32)
ssh_service = SSHService(SETTINGS_DIR / "ssh")
app.register_blueprint(ssh_blueprint(ssh_service, SESSION_TOKEN))
file_lock = threading.RLock()
http_server = None

DEFAULT_CONFIG = {
    "mqtt_parallel": False,
    "enabled_sources": [],
    "source_intervals": {},
    "ui_language": "it",
    **MQTT_DEFAULTS,
    **S7_DEFAULTS,
    "source": "opcua", "ads_transport": "tcp", "ads_dll_path": "", "ads_host": "127.0.0.1", "ads_target_netid": "127.0.0.1.1.1",
    "ads_port": 851, "ads_tcp_port": 48898, "ads_source_netid": "", "ads_source_port": 32905,
    "ads_bind_ip": "", "ads_timeout_ms": 3000, "ads_mode": "sum", "ads_encoding": "cp1252", "ads_root": "ads:/",
    "endpoint": "opc.tcp://127.0.0.1:4840",
    "security": "Basic256Sha256",
    "security_mode": "SignAndEncrypt",
    "username": "",
    "password": "",
    "client_cert": "certificates/client.der",
    "client_key": "certificates/client-key.pem",
    "server_cert": "certificates/server.der",
    "application_uri": "",
    "auto_reconnect": True,
    "sample_ms": 500,
    "root_nodeid": "i=85",
    "window_seconds": 60,
    "max_samples": 360000,
    "buffer_mode": "time",
    "scan_limit": 50000,
}

# Compatibilita con config create dalla V1.1.0.
POLICY_ALIASES = {
    "Aes128_Sha256_RsaOaep": "Aes128Sha256RsaOaep",
    "Aes256_Sha256_RsaPss": "Aes256Sha256RsaPss",
    "Basic256Sha256": "Basic256Sha256",
    "None": "None",
}

# Client.set_security() di asyncua richiede le CLASSI SecurityPolicy,
# non gli enum ua.SecurityPolicyType usati lato server.
POLICY_CLASSES = {
    "Basic256Sha256": SecurityPolicyBasic256Sha256,
    "Aes128Sha256RsaOaep": SecurityPolicyAes128Sha256RsaOaep,
    "Aes256Sha256RsaPss": SecurityPolicyAes256Sha256RsaPss,
}

MODE_MAP = {
    "Sign": ua.MessageSecurityMode.Sign,
    "SignAndEncrypt": ua.MessageSecurityMode.SignAndEncrypt,
}


class Runtime:
    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self._run_loop, daemon=True)
        self.thread.start()
        self.client = None
        self.connected = False
        self.last_error = ""
        self.selected = []
        self.samples = {}
        self.latest_values = []
        self.sampler_task = None
        self.scan_task = None
        self.scan = {"id": "", "state": "idle", "nodes": [], "visited": 0, "pending": 0, "errors": [], "error_count": 0, "root": ""}
        self.active_config = None
        self.want_connected = False
        self.state = "disconnected"
        self.selection_revision = 0
        self.buffer_epoch = 0
        self.window_seconds = 60
        self.max_samples = MAX_SAMPLES
        self.buffer_mode = "time"
        self.periods = deque(maxlen=100)
        self.last_sample_at = None
        self.command_lock = asyncio.Lock()
        self.sample_ms = 500
        self.last_cycle_ms = 0.0
        self.actual_period_ms = 0.0
        self.read_count = 0
        # V1.1.5: acquisizione primaria tramite OPC UA Subscription/MonitoredItem.
        # Il polling Read resta come fallback diagnostico e per server che non supportano le subscription.
        self.subscription = None
        self.subscription_revision = -1
        self.subscription_sample_ms = None
        self.subscription_rows = {}
        self.subscription_bad = {}
        self.acquisition_mode = "Read polling"
        self.notification_count = 0
        self.last_notification_at = None
        self.read_fallback_count = 0
        self.individual_nodes = set()
        self.lock = threading.Lock()

    def _run_loop(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def run(self, coro, timeout=20):
        fut = asyncio.run_coroutine_threadsafe(coro, self.loop)
        try:
            return fut.result(timeout=timeout)
        except FutureTimeout:
            fut.cancel()
            raise RuntimeError("Operazione scaduta: controllare endpoint, rete e server.") from None


runtime = Runtime()
formula_store = FormulaStore(DATA_DIR / 'formulas.json')
runtime.calculated_latest = {}
runtime.calculation_generation = 0
runtime.recording_cutoff = None
runtime.recording_begin = None
runtime.recording_accept = True


def enqueue_recording(t, rows, sample_ms):
    if getattr(runtime,'record_receive_end_ns',None) is not None:
        last=getattr(runtime,'record_raw_last_t',None)
        if last is None or t>last:return False
    if source_sessions.enabled:
        begin=getattr(runtime,'multi_recording_begin',None);end=getattr(runtime,'multi_recording_end',None)
        if begin is None or t<begin or end is not None and t>end:return False
    if not runtime.recording_accept or (runtime.recording_begin is not None and t <= runtime.recording_begin): return False
    if runtime.recording_cutoff is None or t <= runtime.recording_cutoff:
        return recording.enqueue(t, rows, sample_ms)


def finish_recording(reason='user', timeout=5):
    # Fence source arrival, then drain copied callbacks and recorder handoffs.
    # Never use the displayed FIFO to decide which originals belong to a session.
    runtime.record_receive_end_mono_ns=time.monotonic_ns();runtime.record_receive_end_ns=time.time_ns()
    runtime.multi_recording_end=runtime.record_receive_end_ns/1e6
    runtime.mqtt_recording_end_ms=runtime.multi_recording_end
    if source_sessions.enabled:
        source_sessions.flush(timeout)
        recording.source_diagnostics(source_sessions.status())
    else:
        clients=[runtime.client,parallel_mqtt.client if parallel_mqtt.enabled else None]
        for client in clients:
            if client and hasattr(client,'flush'):
                drained=client.flush(min(timeout,5))
                if not drained or getattr(client,'error',None):recording.fail(getattr(client,'error',None) or 'Coda sorgente non completamente drenata alla chiusura')
                if hasattr(client,'diagnostics'):
                    diag=client.diagnostics();diag['reconnections']=getattr(runtime,'mqtt_reconnections',0)
                    recording.source_diagnostics(diag)
        worker=getattr(runtime,'ads_notification_worker',None)
        if worker:
            target=getattr(worker,'accepted',worker.received);deadline=time.monotonic()+timeout
            while worker.processed<target and worker.thread.is_alive() and time.monotonic()<deadline:time.sleep(.001)
            if worker.processed<target:recording.fail('ADS: coda non drenata alla chiusura')
    if not acquisition.flush(timeout):recording.fail('Coda Acquisition Engine non drenata alla chiusura')
    calculations.flush()
    acquisition_events.emit('recording_stop_fence',reason=reason,received_end_ns=str(runtime.record_receive_end_ns),acquisition=acquisition.status(),calculations=calculations.status())
    return recording.stop(reason,.15 if reason=='user' else 0)


def calculated_result(rows, generation, sample_ms):
    if generation != runtime.calculation_generation:return
    recorded={}
    for row in rows:
        value=dict(nodeid=row['node_id'],value=row.get('value_numeric'),plot_value=row.get('value_numeric'),
            quality=row['quality'],good=row['quality']=='Good',numeric=row.get('value_numeric') is not None,
            status=row['status'],data_type='Double',source_ts=row.get('source_timestamp'),server_ts=None,
            local_ts=row.get('local_timestamp'),calculated=True,error='' if row['quality']=='Good' else row['status'])
        recorded.setdefault(row['timestamp_ms'],[]).append(value)
    for t,batch in recorded.items():
        enqueue_recording(t,batch,sample_ms)
        acquisition.live.put((t,batch,sample_ms,('calculated',generation)),len(batch),len(batch)*1024)

calculations = CalculationService(DATA_DIR / 'calculation_cache', calculated_result)
calculations.fault_callback=lambda error,dropped:acquisition_events.emit('calculation_overflow',protocol='calculated',error=error,lost_batches=dropped)


def reset_calculations(protocol=None):
    """Called under runtime.lock: replace only derived buffers and invalidate old callbacks."""
    protocol = protocol or (runtime.active_config or load_config()).get('source', 'opcua')
    if len(allowed_sources(runtime.active_config or load_config()))>1:protocol=None
    snapshot = formula_store.snapshot()
    runtime.calculation_generation = calculations.configure(snapshot['formulas'], protocol)
    runtime.samples = {n: p for n, p in runtime.samples.items() if not n.startswith('calc:')}
    for nid in calculations.ids: runtime.samples[nid] = live_buffer(nid)
    runtime.calculated_latest = {}


def normalize_policy(value):
    return POLICY_ALIASES.get(value, value)


def load_config():
    cfg = DEFAULT_CONFIG.copy()
    loaded = False
    if CONFIG_FILE.exists():
        try:
            cfg.update(json.loads(CONFIG_FILE.read_text(encoding="utf-8")))
            loaded = True
        except Exception:
            pass
    # Fallback al profilo completo salvato se config.json e' assente/non leggibile.
    if not loaded and SETTINGS_FILE.exists():
        try:
            saved = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            if isinstance(saved.get("config"), dict):
                cfg.update(saved["config"])
        except Exception:
            pass
    cfg["security"] = normalize_policy(cfg.get("security", "Basic256Sha256"))
    if not cfg.get("application_uri"):
        cfg["application_uri"] = f"urn:{socket.gethostname()}:DSiNC:OPCUAWebClient"
    return cfg


def save_config(cfg, *, ui_language_update=False):
    with file_lock:
        cfg = dict(cfg)
        cfg['s7_symbolic_password']=''
        cfg['mqtt_password']=''
        if not ui_language_update:cfg['ui_language']=load_config()['ui_language']
        cfg["security"] = normalize_policy(cfg.get("security", "None"))
        atomic_json(CONFIG_FILE, cfg)


def atomic_json(path, data):
    with file_lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8")
        os.replace(temporary, path)


def nodeid_text(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 4096:
        raise ValueError("NodeId non valido: usare ad esempio ns=2;s=PLC1.Temperatura oppure ns=2;i=1001.")
    if value.strip().startswith('mqtt:'):
        return parse_mqtt_address(value.strip(),True)['nodeid']
    if value.strip().startswith("s7:"):
        return parse_tag(value, allow_folder=True)['nodeid']
    if value.strip().startswith("ads:"):
        symbol_name(value.strip())
        return value.strip()
    try:
        return ua.NodeId.from_string(value.strip()).to_string()
    except Exception:
        raise ValueError(f"NodeId non valido: {value}") from None


def validated_config(data):
    if not isinstance(data, dict):
        raise ValueError("Configurazione non valida")
    cfg = load_config()
    cfg.update({k: v for k, v in data.items() if k in DEFAULT_CONFIG})
    if cfg['ui_language'] not in ('en','it','de','fr','es'):
        raise ValueError('Lingua interfaccia non supportata')
    for key in ("endpoint", "security", "security_mode", "username", "password", "application_uri"):
        if not isinstance(cfg[key], str):
            raise ValueError(f"Parametro non valido: {key}")
    validate_ads_config(cfg)
    validate_s7_config(cfg)
    validate_mqtt_config(cfg)
    if not isinstance(cfg['mqtt_parallel'], bool):
        raise ValueError('mqtt_parallel deve essere booleano')
    sources = cfg['enabled_sources']
    if not isinstance(sources, list) or len(sources)>4 or any(p not in PROTOCOLS for p in sources) or len(set(sources))!=len(sources):
        raise ValueError('enabled_sources: scegliere protocolli distinti tra OPC UA, ADS, MQTT e S7')
    if not isinstance(cfg['source_intervals'], dict) or any(p not in PROTOCOLS for p in cfg['source_intervals']):
        raise ValueError('source_intervals: configurazione non valida')
    for protocol in configured_sources(cfg):
        local=dict(cfg,source=protocol)
        validate_ads_config(local);validate_s7_config(local);validate_mqtt_config(local)
    for protocol, period in cfg['source_intervals'].items():
        if type(period) is not int or not (1 if protocol=='ads' else 10)<=period<=60000:
            raise ValueError('Periodo sorgente non valido: '+protocol)
    cfg["endpoint"] = cfg["endpoint"].strip()
    url = urlsplit(cfg["endpoint"])
    if url.scheme != "opc.tcp" or not url.hostname or url.username is not None or url.password is not None:
        raise ValueError("Specificare un endpoint opc.tcp://host:porta e le credenziali nei campi dedicati.")
    _ = url.port  # Reject malformed ports.
    cfg["security"] = normalize_policy(cfg["security"])
    if cfg["security"] not in {"None", *POLICY_CLASSES}:
        raise ValueError("Security Policy non supportata")
    if cfg["security"] == "None":
        cfg["security_mode"] = "None"
    elif cfg["security_mode"] not in MODE_MAP:
        raise ValueError("Selezionare Sign oppure SignAndEncrypt")
    minimum_sample_ms = 1 if cfg.get('source') == 'ads' else 10
    for key, low, high in (("sample_ms", minimum_sample_ms, 60000), ("window_seconds", 10, 3600), ("max_samples", 1000, 360000), ("scan_limit", 100, 500000)):
        try:
            value = float(cfg[key])
            if isinstance(cfg[key], bool) or not math.isfinite(value) or not value.is_integer() or not low <= value <= high:
                raise ValueError()
            cfg[key] = int(value)
        except (ValueError, TypeError, OverflowError):
            raise ValueError(f"{key}: inserire un intero tra {low} e {high}") from None
    if cfg["buffer_mode"] not in ("time", "samples"):
        raise ValueError("Modalità buffer non valida")
    if not isinstance(cfg["auto_reconnect"], bool):
        raise ValueError("auto_reconnect deve essere un valore booleano")
    cfg["root_nodeid"] = nodeid_text(cfg.get("root_nodeid") or "i=85")
    cfg["application_uri"] = cfg["application_uri"].strip() or load_config()["application_uri"]
    return cfg


def validated_nodes(nodes):
    if not isinstance(nodes, list) or len(nodes) > MAX_SELECTED:
        raise ValueError(f"Selezionare al massimo {MAX_SELECTED} variabili")
    result = {}
    for row in nodes:
        if isinstance(row, str):
            row = {"nodeid": row}
        if not isinstance(row, dict):
            raise ValueError("Selezione nodi non valida")
        nid = nodeid_text(row.get("nodeid"))
        if nid.startswith('s7:'): parse_tag(nid)  # Folders belong to browsing, not acquisition.
        if nid.startswith('mqtt:'):parse_mqtt_address(nid)
        result[nid] = {"nodeid": nid, "display_name": str(row.get("display_name") or nid), "browse_name": str(row.get("browse_name") or ""), "node_class": "Variable"}
    return list(result.values())




def load_saved_settings():
    if not SETTINGS_FILE.exists():
        return {}
    try:
        data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_full_settings(cfg, selected_nodes=None, server_data=None):
    """Persist connection parameters, selected nodes and certificate files.

    The profile is local to the application directory and is restored at startup.
    Passwords are intentionally preserved because the user requested all connection
    parameters to be saved; therefore the profile must be treated as sensitive.
    """
    SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
    SETTINGS_CERT_DIR.mkdir(parents=True, exist_ok=True)

    cfg = dict(cfg)
    cfg['ui_language']=load_config()['ui_language']
    cfg["security"] = normalize_policy(cfg.get("security", "None"))
    cfg['s7_symbolic_password']=''
    cfg['mqtt_password']=''
    cfg["sample_ms"] = max(1 if cfg.get('source') == 'ads' else 10, int(cfg.get("sample_ms", 500) or 500))
    save_config(cfg)
    ensure_client_certificate(cfg.get("application_uri"))

    copied = {}
    for label, src, name in (
        ("client_cert", CERT_FILE, "client.der"),
        ("client_key", KEY_FILE, "client-key.pem"),
        ("server_cert", SERVER_CERT_FILE, "server.der"),
    ):
        if src.exists():
            dst = SETTINGS_CERT_DIR / name
            shutil.copy2(src, dst)
            copied[label] = str(dst)

    selected_nodes = selected_nodes or []
    selected_nodes = validated_nodes(selected_nodes)
    payload = {
        "version": APP_VERSION,
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "config": cfg,
        "selected_nodes": selected_nodes,
        "server_data": server_data if isinstance(server_data, dict) else {},
        "calculations": formula_store.snapshot(),
        "certificates": {
            "copies": copied,
            "client": certificate_info(CERT_FILE),
            "server": certificate_info(SERVER_CERT_FILE),
        },
    }
    atomic_json(SETTINGS_FILE, payload)
    return payload


def restore_saved_certificate_files():
    """Restore saved certificate copies only when the active files are missing."""
    mapping = (
        (SETTINGS_CERT_DIR / "client.der", CERT_FILE),
        (SETTINGS_CERT_DIR / "client-key.pem", KEY_FILE),
        (SETTINGS_CERT_DIR / "server.der", SERVER_CERT_FILE),
    )
    CERT_DIR.mkdir(parents=True, exist_ok=True)
    for src, dst in mapping:
        if src.exists() and not dst.exists():
            shutil.copy2(src, dst)


def ensure_client_certificate(application_uri=None, force=False):
    CERT_DIR.mkdir(parents=True, exist_ok=True)
    cfg = load_config()
    app_uri = application_uri or cfg.get("application_uri") or f"urn:{socket.gethostname()}:DSiNC:OPCUAWebClient"

    if CERT_FILE.exists() and KEY_FILE.exists() and not force:
        try:
            cert = x509.load_der_x509_certificate(CERT_FILE.read_bytes())
            uris = cert.extensions.get_extension_for_class(
                x509.SubjectAlternativeName
            ).value.get_values_for_type(x509.UniformResourceIdentifier)
            if app_uri in uris:
                return app_uri, False
        except Exception:
            pass

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.now(timezone.utc)
    subject = issuer = x509.Name(
        [
            x509.NameAttribute(NameOID.COMMON_NAME, APP_NAME),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "DSiNC"),
        ]
    )
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=True,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=None,
                decipher_only=None,
            ),
            critical=True,
        )
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CLIENT_AUTH]), critical=False)
        .add_extension(
            x509.SubjectAlternativeName(
                [
                    x509.UniformResourceIdentifier(app_uri),
                    x509.DNSName(socket.gethostname()),
                ]
            ),
            critical=False,
        )
        .sign(private_key=key, algorithm=hashes.SHA256())
    )

    CERT_FILE.write_bytes(cert.public_bytes(serialization.Encoding.DER))
    KEY_FILE.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )

    cfg["application_uri"] = app_uri
    cfg["client_cert"] = "certificates/client.der"
    cfg["client_key"] = "certificates/client-key.pem"
    save_config(cfg)
    return app_uri, True


def load_certificate_any(data: bytes):
    try:
        return x509.load_der_x509_certificate(data)
    except Exception:
        return x509.load_pem_x509_certificate(data)


def certificate_info(path: Path):
    if not path.exists():
        return {"present": False, "path": str(path)}
    try:
        cert = load_certificate_any(path.read_bytes())
    except Exception as exc:
        return {"present": False, "path": str(path), "error": f"Certificato non valido: {exc}"}
    uris = []
    dns_names = []
    try:
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        uris = san.get_values_for_type(x509.UniformResourceIdentifier)
        dns_names = san.get_values_for_type(x509.DNSName)
    except Exception:
        pass
    return {
        "present": True,
        "path": str(path),
        "application_uri": uris[0] if uris else "",
        "uris": uris,
        "dns_names": dns_names,
        "subject": cert.subject.rfc4514_string(),
        "issuer": cert.issuer.rfc4514_string(),
        "valid_from": cert.not_valid_before_utc.isoformat(),
        "valid_to": cert.not_valid_after_utc.isoformat(),
        "thumbprint_sha256": cert.fingerprint(hashes.SHA256()).hex(":").upper(),
    }


def cert_info():
    ensure_client_certificate()
    info = certificate_info(CERT_FILE)
    info["key_path"] = str(KEY_FILE)
    return info


def policy_from_uri(uri):
    if not uri:
        return "None"
    if "#" in uri:
        return uri.rsplit("#", 1)[-1]
    return uri.rsplit("/", 1)[-1]


def mode_name(mode):
    try:
        return mode.name
    except Exception:
        return str(mode).split(".")[-1]


async def discover_endpoints_async(endpoint):
    c = Client(url=endpoint, timeout=8)
    eps = await c.connect_and_get_server_endpoints()
    result = []
    for ep in eps:
        policy = policy_from_uri(ep.SecurityPolicyUri)
        mode = mode_name(ep.SecurityMode)
        server_uri = ""
        server_name = ""
        try:
            server_uri = ep.Server.ApplicationUri or ""
            if ep.Server.ApplicationName:
                server_name = ep.Server.ApplicationName.Text or ""
        except Exception:
            pass
        cert_thumb = ""
        if ep.ServerCertificate:
            try:
                cert = x509.load_der_x509_certificate(bytes(ep.ServerCertificate))
                cert_thumb = cert.fingerprint(hashes.SHA256()).hex(":").upper()
            except Exception:
                pass
        result.append(
            {
                "endpoint_url": ep.EndpointUrl,
                "security": policy,
                "security_mode": mode,
                "security_level": int(ep.SecurityLevel),
                "server_application_uri": server_uri,
                "server_name": server_name,
                "has_server_certificate": bool(ep.ServerCertificate),
                "server_certificate_sha256": cert_thumb,
            }
        )
    return result, eps


async def fetch_server_certificate_async(endpoint, security, security_mode):
    _, eps = await discover_endpoints_async(endpoint)
    security = normalize_policy(security)
    matches = []
    for ep in eps:
        if policy_from_uri(ep.SecurityPolicyUri) == security and mode_name(ep.SecurityMode) == security_mode:
            matches.append(ep)
    if not matches:
        available = sorted(
            {f"{policy_from_uri(ep.SecurityPolicyUri)} / {mode_name(ep.SecurityMode)}" for ep in eps}
        )
        raise RuntimeError(
            "Endpoint richiesto non esposto dal server. Disponibili: " + ", ".join(available)
        )
    ep = sorted(matches, key=lambda x: int(x.SecurityLevel), reverse=True)[0]
    if not ep.ServerCertificate:
        raise RuntimeError("L'endpoint selezionato non espone un certificato server.")
    CERT_DIR.mkdir(parents=True, exist_ok=True)
    raw = bytes(ep.ServerCertificate)
    # Normalizza a un singolo certificato DER quando possibile.
    cert = x509.load_der_x509_certificate(raw)
    SERVER_CERT_FILE.write_bytes(cert.public_bytes(serialization.Encoding.DER))
    return certificate_info(SERVER_CERT_FILE)


async def stop_scan():
    task = runtime.scan_task
    if task and not task.done():
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
    runtime.scan_task = None


async def stop_subscription():
    sub, runtime.subscription = runtime.subscription, None
    with runtime.lock:
        runtime.subscription_revision = -1
        runtime.subscription_sample_ms = None
        runtime.subscription_rows = {}
        runtime.subscription_bad = {}
    if sub:
        with suppress(Exception):
            await asyncio.wait_for(sub.delete(), 3)


async def close_client():
    c = runtime.client
    runtime.connected = False
    # La subscription deve essere eliminata prima della sessione OPC UA.
    await stop_subscription()
    runtime.client = None
    if c:
        with suppress(Exception):
            await asyncio.wait_for(c.disconnect(), 3)


async def disconnect_async():
    runtime.want_connected = False
    await asyncio.to_thread(finish_recording, "disconnect", 5)
    await source_sessions.stop()
    await parallel_mqtt.stop()
    await stop_scan()
    task, runtime.sampler_task = runtime.sampler_task, None
    if task and task is not asyncio.current_task():
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
    await close_client()
    runtime.state = "disconnected"
    logging.info("Data source disconnected")
    runtime.last_error = ""


async def make_protocol_client(cfg):
    if cfg.get('source')=='mqtt':
        c=MQTTClient(cfg)
        try:
            await c.connect()
        except BaseException:
            with suppress(Exception): await c.disconnect()
            raise
        logging.info('MQTT connected: host=%s port=%s version=%s',cfg['mqtt_host'],cfg['mqtt_port'],cfg['mqtt_version'])
        return c
    if cfg.get('source') == 's7':
        c = create_s7_client(cfg)
        try:
            await c.connect()
        except BaseException:
            with suppress(Exception): await c.disconnect()
            raise
        logging.info('S7 connected: mode=%s host=%s', cfg['s7_mode'], cfg['s7_host'])
        return c
    if cfg.get("source") == "ads":
        c = create_ads_client(cfg)
        try:
            await c.connect()
        except BaseException:
            with suppress(Exception): await c.disconnect()
            raise
        logging.info("ADS connected: device=%s state=%s", c.info.get("device"), c.info.get("ads_state_name"))
        return c
    c = Client(url=cfg["endpoint"], timeout=8, auto_reconnect=False)
    c.application_uri, c.name = cfg["application_uri"], APP_NAME
    if cfg.get("username"):
        c.set_user(cfg["username"])
        c.set_password(cfg.get("password", ""))
    try:
        sec = cfg["security"]
        if sec != "None":
            if not SERVER_CERT_FILE.exists():
                raise ValueError("Certificato server mancante: acquisirlo dal server oppure importarlo.")
            await c.set_security(POLICY_CLASSES[sec], certificate=str(CERT_FILE),
                                 private_key=str(KEY_FILE), server_certificate=str(SERVER_CERT_FILE),
                                 mode=MODE_MAP[cfg["security_mode"]])
        await c.connect()
    except BaseException:
        with suppress(Exception):
            await asyncio.wait_for(c.disconnect(), 2)
        raise
    logging.info("OPC UA connected: policy=%s mode=%s", cfg["security"], cfg["security_mode"])
    return c


async def open_client(cfg):
    reconnecting=runtime.state=='reconnecting'
    c = await make_protocol_client(cfg)
    runtime.client, runtime.connected = c, True
    runtime.state, runtime.last_error = 'connected', ''
    if cfg['source']=='mqtt': runtime.mqtt_last_client=c
    counters=getattr(runtime,'source_reconnections',{}).copy();counters[cfg['source']]=counters.get(cfg['source'],0)+int(reconnecting);runtime.source_reconnections=counters
    acquisition_events.emit('source_connected',acquisition.connection(cfg['source'],cfg['source'])[1].connection_id,protocol=cfg['source'],reconnections=counters[cfg['source']])


async def connect_sources(cfg):
    if 'opcua' in configured_sources(cfg): ensure_client_certificate(cfg['application_uri'])
    runtime.active_config = dict(cfg)
    apply_live_config(cfg)
    runtime.want_connected = True
    live_archive.new_session()
    with runtime.lock:
        runtime.samples = {nid: live_buffer(nid) for nid in runtime.selected}
        reset_calculations(cfg['source'])
        runtime.latest_values = []
        runtime.read_count = runtime.notification_count = 0
        runtime.last_sample_at = None
        runtime.periods.clear()
        runtime.buffer_epoch += 1
        runtime.acquisition_mode = 'OPC UA / ADS / MQTT / S7 · PC UTC arrival'
    await source_sessions.start(cfg)
    refresh_sources()


def refresh_sources():
    if not source_sessions.enabled: return
    sessions = list(source_sessions.sessions.values())
    runtime.connected = any(s.state=='connected' for s in sessions)
    runtime.state = 'connected' if runtime.connected else 'reconnecting' if any(s.state in ('connecting','reconnecting') for s in sessions) else 'disconnected'
    runtime.last_error = ' · '.join(s.protocol.upper()+': '+s.error for s in sessions if s.error)
    runtime.client = next((s.client for s in sessions if s.client), None)


async def connect_async(cfg):
    async with runtime.command_lock:
        if any(protocol_of(n) not in allowed_sources(cfg) for n in runtime.selected):
            raise ValueError("La selezione contiene segnali di una fonte diversa: cambiare fonte e selezione prima di connettere")
        s7_selection_guard(cfg,runtime.selected)
        await disconnect_async()
        if len(allowed_sources(cfg))>1 or allowed_sources(cfg)!={cfg['source']}:
            await connect_sources(cfg)
            return
        runtime.state = "connecting"
        try:
            ensure_client_certificate(cfg["application_uri"])
            await open_client(cfg)
            if cfg.get('mqtt_parallel'): await parallel_mqtt.start(cfg)
        except BaseException as exc:
            await parallel_mqtt.stop()
            await close_client()
            runtime.state = "disconnected"
            runtime.last_error = str(exc) or type(exc).__name__
            raise
        runtime.active_config = dict(cfg)
        apply_live_config(cfg)
        runtime.want_connected = True
        live_archive.new_session()
        with runtime.lock:
            runtime.samples = {nid: live_buffer(nid) for nid in runtime.selected}
            reset_calculations(cfg['source'])
            runtime.latest_values = []
            runtime.read_count = 0
            runtime.last_sample_at = None
            runtime.periods.clear()
            runtime.buffer_epoch += 1
            runtime.ads_notification_diag = {}
            runtime.notification_count = 0
            runtime.last_notification_at = None
            runtime.read_fallback_count = 0
            runtime.individual_nodes = set()
            runtime.acquisition_mode = "Inizializzazione subscription"
            runtime.subscription_revision = -1
            runtime.subscription_sample_ms = None
            runtime.subscription_rows = {}
            runtime.subscription_bad = {}
            runtime.mqtt_reconnections=0;runtime.mqtt_late_samples=0;runtime.mqtt_recording_begin_ms=None;runtime.mqtt_recording_end_ms=None
        runtime.sampler_task = asyncio.create_task(mqtt_sampler_loop() if cfg['source']=='mqtt' else ads_sampler_loop() if cfg['source']=='ads' else s7_sampler_loop() if cfg['source']=='s7' else sampler_loop())


async def disconnect_command():
    async with runtime.command_lock:
        await disconnect_async()


def require_connection(protocol=None):
    if source_sessions.enabled:
        if protocol: return source_sessions.client(protocol)
        refresh_sources()
    if not runtime.connected or not runtime.client:
        raise ValueError("Client non connesso")
    return runtime.client


async def reference_pages(nodeid):
    """Browse hierarchical references; follow and release server continuation points."""
    c = require_connection("opcua")
    desc = ua.BrowseDescription(NodeId=ua.NodeId.from_string(nodeid),
        BrowseDirection=ua.BrowseDirection.Forward,
        ReferenceTypeId=ua.NodeId(ua.ObjectIds.HierarchicalReferences),
        IncludeSubtypes=True, NodeClassMask=0, ResultMask=ua.BrowseResultMask.All)
    params = ua.BrowseParameters(NodesToBrowse=[desc], RequestedMaxReferencesPerNode=250)
    params.View.Timestamp = ua.get_win_epoch()
    cp = None
    try:
        result = (await c.uaclient.browse(params))[0]
        while True:
            cp = result.ContinuationPoint
            result.StatusCode.check()
            yield result.References
            if not cp:
                break
            params_next = ua.BrowseNextParameters(ContinuationPoints=[cp], ReleaseContinuationPoints=False)
            result = (await c.uaclient.browse_next(params_next))[0]
    finally:
        if cp:
            with suppress(Exception):
                await asyncio.wait_for(c.uaclient.browse_next(ua.BrowseNextParameters(
                    ContinuationPoints=[cp], ReleaseContinuationPoints=True)), 2)


def reference_item(ref, parent_path=""):
    nid = ua.NodeId(ref.NodeId.Identifier, ref.NodeId.NamespaceIndex, ref.NodeId.NodeIdType).to_string()
    name = ref.DisplayName.Text or ref.BrowseName.Name or nid
    remote = bool(getattr(ref.NodeId, "ServerIndex", 0) or getattr(ref.NodeId, "NamespaceUri", None))
    return {"nodeid": nid, "display_name": name,
            "browse_name": f"{ref.BrowseName.NamespaceIndex}:{ref.BrowseName.Name}",
            "node_class": ref.NodeClass.name, "has_children": not remote,
            "remote": remote, "path": f"{parent_path}/{name}" if parent_path else name}


async def mixed_catalog_operation(protocol, operation, nodeid):
    session=source_sessions.sessions[protocol]
    # Symbol upload has its own transport and never serializes acquisition reads.
    c=await make_protocol_client(session.cfg)
    try: return await getattr(c,operation)(nodeid)
    finally: await c.disconnect()


async def inspect_node(nodeid):
    if source_sessions.enabled and protocol_of(nodeid)!='opcua':
        protocol=protocol_of(nodeid)
        if protocol=='ads' or protocol=='s7' and runtime.active_config['s7_mode']=='symbolic':
            return await mixed_catalog_operation(protocol,'inspect',nodeid)
        c = require_connection(protocol)
        return await c.inspect(nodeid)

    if nodeid.startswith('mqtt:'): return await mqtt_catalog_client().inspect(nodeid)
    if not source_sessions.enabled and isinstance(require_connection(),MQTTClient):return await runtime.client.inspect(nodeid)
    if not source_sessions.enabled and isinstance(require_connection(), S7_TYPES):
        return await runtime.client.inspect(nodeid)
    if not source_sessions.enabled and isinstance(require_connection(), ADSClient):
        return await ads_catalog_operation("inspect", nodeid)
    node = require_connection("opcua").get_node(nodeid)
    attrs = await node.read_attributes([ua.AttributeIds.NodeClass, ua.AttributeIds.DisplayName, ua.AttributeIds.BrowseName])
    for a in attrs:
        a.StatusCode.check()
    cls, display, browse = [a.Value.Value for a in attrs]
    return {"nodeid": nodeid, "node_class": ua.NodeClass(cls).name,
            "display_name": display.Text or browse.Name or nodeid,
            "browse_name": f"{browse.NamespaceIndex}:{browse.Name}", "has_children": True}


async def browse_node(nodeid="i=85"):
    if source_sessions.enabled and protocol_of(nodeid)!='opcua':
        protocol=protocol_of(nodeid)
        if protocol=='ads' or protocol=='s7' and runtime.active_config['s7_mode']=='symbolic':
            return await mixed_catalog_operation(protocol,'browse',nodeid)
        c = require_connection(protocol)
        return await c.browse(nodeid)

    if nodeid.startswith("mqtt:"): return await mqtt_catalog_client().browse(nodeid)
    if not source_sessions.enabled and isinstance(require_connection(),MQTTClient):return await runtime.client.browse(nodeid)
    if not source_sessions.enabled and isinstance(require_connection(), S7_TYPES):
        return await runtime.client.browse(nodeid)
    if not source_sessions.enabled and isinstance(require_connection(), ADSClient):
        return await ads_catalog_operation("browse", nodeid)
    result, seen = [], set()
    async for refs in reference_pages(nodeid):
        for ref in refs:
            row = reference_item(ref)
            if row["nodeid"] not in seen:
                seen.add(row["nodeid"])
                result.append(row)
    return result


async def scan_tree(job, root, limit):
    queue, seen = deque(), {root}
    try:
        first = await inspect_node(root)
        first["path"] = first["display_name"]
        with runtime.lock:
            job["nodes"].append(first)
        queue.append((root, first["path"]))
        while queue:
            nid, path = queue.popleft()
            try:
                pages = reference_pages(nid)
                try:
                    async for refs in pages:
                        for ref in refs:
                            row = reference_item(ref, path)
                            if row["nodeid"] in seen:
                                continue
                            if len(seen) >= limit:
                                job["state"] = "limited"
                                return
                            seen.add(row["nodeid"])
                            with runtime.lock:
                                job["nodes"].append(row)
                            if not row["remote"]:
                                queue.append((row["nodeid"], row["path"]))
                finally:
                    await pages.aclose()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                with runtime.lock:
                    job["error_count"] += 1
                    if len(job["errors"]) < 100:
                        job["errors"].append({"nodeid": nid, "error": str(exc) or type(exc).__name__})
                if not runtime.connected:
                    raise
            with runtime.lock:
                job["visited"] += 1
                job["pending"] = len(queue)
            await asyncio.sleep(0)  # Acquisition remains responsive during discovery.
        job["state"] = "completed_with_errors" if job["error_count"] else "completed"
    except asyncio.CancelledError:
        job["state"] = "cancelled"
    except Exception as exc:
        job["state"], job["error"] = "failed", str(exc) or type(exc).__name__
    finally:
        job["finished_at"] = datetime.now(timezone.utc).isoformat()


async def start_scan(root, limit):
    require_connection()
    await stop_scan()
    job = {"id": secrets.token_hex(8), "state": "running", "root": root, "limit": limit,
           "nodes": [], "visited": 0, "pending": 1, "errors": [], "error_count": 0}
    with runtime.lock:
        runtime.scan = job
    if source_sessions.enabled:
        runtime.scan_task = asyncio.create_task(scan_sources(job, root, limit))
        return job["id"]
    runtime.scan_task = asyncio.create_task(mqtt_scan_tree(job,root,limit) if isinstance(runtime.client,MQTTClient) else s7_scan_tree(job, root, limit) if isinstance(runtime.client, S7_TYPES) else ads_scan_tree(job, root, limit) if isinstance(runtime.client, ADSClient) else scan_tree(job, root, limit))
    return job["id"]


async def scan_sources(job, root, limit):
    try:
        protocol = protocol_of(root)
        c = require_connection(protocol)
        if protocol=='mqtt' or protocol=='s7' and isinstance(c,(S7SymbolicClient,S7UDPClient)):
            items = await c.scan_items(root)
        elif protocol=='s7':
            group=parse_tag(root,True)
            items=[await c.inspect(n) for n in c.catalog if group.get('group','') in ('',parse_tag(n)['group'])]
        else:
            pending, seen, items = deque([root]), set(), []
            while pending and len(items)<limit:
                nid=pending.popleft()
                if nid in seen: continue
                seen.add(nid)
                item=await inspect_node(nid);items.append(item)
                if item.get('has_children') or item.get('node_class')!='Variable':
                    pending.extend(n['nodeid'] for n in await browse_node(nid))
                await asyncio.sleep(0)
        job.update(nodes=items[:limit],visited=min(len(items),limit),state='limited' if len(items)>=limit else 'completed')
    except asyncio.CancelledError: job['state']='cancelled'
    except Exception as exc: job.update(state='failed',error=str(exc))
    finally: job.update(pending=0,finished_at=datetime.now(timezone.utc).isoformat())


async def mqtt_scan_tree(job,root,limit):
    try:
        items=await require_connection().scan_items(root)
        with runtime.lock:
            job['nodes']=items[:limit];job['visited']=len(items[:limit])
            job['state']='limited' if len(items)>limit else 'completed'
            job['note']='Topic/campi osservati nelle pubblicazioni ricevute; MQTT non offre un catalogo completo del broker.'
    except asyncio.CancelledError:job['state']='cancelled'
    except Exception as exc:job['state'],job['error']='failed',str(exc)
    finally:job['pending']=0;job['finished_at']=datetime.now(timezone.utc).isoformat()


async def s7_scan_tree(job, root, limit):
    try:
        c = require_connection()
        if isinstance(c,(S7SymbolicClient,S7UDPClient)):
            if isinstance(c,S7SymbolicClient): job['note']='Esplorazione dei simboli della CPU in corso…'
            items=await c.scan_items(root)
            nids=[n['nodeid'] for n in items]
        else:
            group = parse_tag(root, True)
            nids = [n for n in c.catalog if group.get('group','') in ('',parse_tag(n)['group'])]
            if not group.get('folder'): nids=[group['nodeid']]
        for nid in nids[:limit]:
            item = await c.inspect(nid)
            with runtime.lock:
                job['nodes'].append(item); job['visited'] += 1
            await asyncio.sleep(0)
        job['state']='limited' if len(nids)>limit else 'completed'
        job['note']='Simboli risolti direttamente dalla CPU S7CommPlus.' if isinstance(c,S7SymbolicClient) else 'Campi del telegramma UDP configurati dall’utente.' if isinstance(c,S7UDPClient) else 'Catalogo S7 configurato dall’utente; nessuna scansione simbolica del PLC.'
    except asyncio.CancelledError: job['state']='cancelled'
    except Exception as exc: job['state'],job['error']='failed',str(exc)
    finally:
        job['pending']=0; job['finished_at']=datetime.now(timezone.utc).isoformat()


def json_value(value):
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple)):
        return [json_value(v) for v in value]
    if isinstance(value, dict):
        return {str(k): json_value(v) for k, v in value.items()}
    return str(value)


def value_row(nid, dv):
    value = dv.Value.Value if dv.Value else None
    good = dv.StatusCode.is_good()
    quality = "Good" if good else "Uncertain" if dv.StatusCode.is_uncertain() else "Bad"
    numeric = isinstance(value, (int, float)) and math.isfinite(value)
    safe_number = not isinstance(value, int) or abs(value) <= 9007199254740991
    row = {"nodeid": nid, "value": json_value(value),
           "source_picoseconds": dv.SourcePicoseconds, "server_picoseconds": dv.ServerPicoseconds, "status": dv.StatusCode.name,
           "good": good, "quality": quality, "status_code": dv.StatusCode.value, "numeric": numeric, "precision_warning": numeric and not safe_number,
           "plot_value": float(value) if good and numeric else None,
           "data_type": dv.Value.VariantType.name if dv.Value else "Null",
           "source_ts": dv.SourceTimestamp.isoformat() if dv.SourceTimestamp else None,
           "server_ts": dv.ServerTimestamp.isoformat() if dv.ServerTimestamp else None}
    try:row['_source_typed']=primary_typed(value)
    except TypeError:
        row['_source_binary_typed']=primary_typed(ua.ua_binary.variant_to_binary(dv.Value))
    if not safe_number:
        row["value"] = str(value)  # Retain exact Int64 text; the graph uses float64.
    if not good:
        row["error"] = dv.StatusCode.name
    return row


async def read_selected_once(nodeids, retry_individual=True):
    """Read Values with automatic per-node fallback for servers that dislike batch Read.

    Once a node succeeds individually after failing/nulling in a batch Read, that NodeId is
    promoted to individual mode for the rest of the connection. This avoids alternating Good/Bad
    samples and is especially useful with PLC OPC UA servers exposing complex symbolic paths.
    """
    c = require_connection()
    forced = set(runtime.individual_nodes)
    rows_by_id = {}
    bulk_ids = [nid for nid in nodeids if nid not in forced]
    if bulk_ids:
        dvs = await c.uaclient.read_attributes([ua.NodeId.from_string(n) for n in bulk_ids], ua.AttributeIds.Value)
        if len(dvs) != len(bulk_ids):
            raise RuntimeError("Il server ha restituito un numero di valori inatteso")
        rows_by_id.update((nid, value_row(nid, dv)) for nid, dv in zip(bulk_ids, dvs))

    # Nodes already known to require single-Value reads are always read individually.
    for nid in [n for n in nodeids if n in forced]:
        dv = await c.get_node(nid).read_data_value(raise_on_bad_status=False)
        rows_by_id[nid] = value_row(nid, dv)

    if retry_individual:
        retry = [nid for nid in bulk_ids
                 if (not rows_by_id[nid].get("good")) or rows_by_id[nid].get("data_type") == "Null"]
        for nid in retry:
            try:
                dv = await c.get_node(nid).read_data_value(raise_on_bad_status=False)
                candidate = value_row(nid, dv)
                current = rows_by_id[nid]
                if candidate.get("good") or (current.get("value") is None and candidate.get("value") is not None):
                    rows_by_id[nid] = candidate
                if candidate.get("good") and not current.get("good"):
                    runtime.individual_nodes.add(nid)
            except Exception:
                # The batch response remains available as diagnostics; a transport failure is
                # still caught by the regular heartbeat/reconnect path.
                pass
    return [rows_by_id.get(nid, missing_row(nid, "Nessun DataValue restituito")) for nid in nodeids]


class DataChangeHandler:
    """Fast callback used by asyncua Subscription/MonitoredItems."""
    def datachange_notification(self, node, val, data):
        nid = node.nodeid.to_string()
        try:
            dv = data.monitored_item.Value
            row = value_row(nid, dv)
        except Exception as exc:
            row = {"nodeid": nid, "value": json_value(val), "status": "CallbackError",
                   "good": False, "numeric": False, "plot_value": None,
                   "data_type": type(val).__name__, "error": str(exc) or type(exc).__name__}
        now = time.time() * 1000
        row['_received_mono_ns']=time.monotonic_ns();row['received_ns']=time.time_ns()
        row['acquisition_origin']='notification'
        submit_acquisition(now,[row],runtime.sample_ms,(runtime.selection_revision,runtime.buffer_epoch))
        if row.get('status_code',0) & 0x80:recording.fail('OPC UA: overflow MonitoredItem')
        runtime.subscription_rows[nid] = row
        runtime.notification_count += 1
        runtime.last_notification_at = now

    def status_change_notification(self, status):
        with runtime.lock:
            runtime.last_error = f"Subscription status: {status.Status.name if status and status.Status else status}"


async def rebuild_subscription(nodeids, revision):
    """Create UA MonitoredItems at the requested sampling interval (UAExpert-like live acquisition)."""
    await stop_subscription()
    if not nodeids:
        return False
    c = require_connection()
    sub = await c.create_subscription(float(runtime.sample_ms), DataChangeHandler())
    try:
        nodes = [c.get_node(nid) for nid in nodeids]
        results = await sub.subscribe_data_change(
            nodes, queuesize=max(2,min(65536,int(os.environ.get('SPACE_UA_QUEUE',256)))), sampling_interval=float(runtime.sample_ms)
        )
        if not isinstance(results, list):
            results = [results]
        bad = {}
        good_count = 0
        for nid, result in zip(nodeids, results):
            if isinstance(result, ua.StatusCode):
                if result.is_good():
                    good_count += 1
                else:
                    bad[nid] = result.name
            else:
                good_count += 1
        if good_count == 0:
            await sub.delete()
            runtime.acquisition_mode = "Read polling (subscription rifiutata)"
            runtime.subscription_bad = bad
            return False
        runtime.subscription = sub
        runtime.subscription_revision = revision
        runtime.subscription_sample_ms = runtime.sample_ms
        runtime.subscription_bad = bad
        runtime.acquisition_mode = "OPC UA Subscription" if not bad else "Subscription + Read fallback"
        return True
    except Exception:
        with suppress(Exception):
            await sub.delete()
        raise


def missing_row(nid, message="In attesa della prima notifica OPC UA"):
    return {"nodeid": nid, "value": None, "status": "Waiting", "good": False,
            "numeric": False, "plot_value": None, "data_type": "Null", "error": message}


# Separate catalog socket avoids holding the acquisition connection during symbol upload.
ads_catalog_lock = asyncio.Lock()

async def ads_catalog_operation(operation, nodeid, *args):
    active = require_connection()
    if not isinstance(active, ADSClient):
        raise ValueError("Selezionare la fonte ADS")
    async with ads_catalog_lock:
        c = create_ads_client(runtime.active_config, catalog=True)
        try:
            await c.connect()
            return await getattr(c, operation)(nodeid, *args)
        finally:
            await c.disconnect()


async def ads_scan_tree(job, root, limit):
    c = create_ads_client(runtime.active_config, catalog=True)
    try:
        async with ads_catalog_lock:
            await c.connect()
            symbols = await c.upload_symbols()
            prefix = symbol_name(root)
            prefix = '' if prefix == '/' else prefix
            queue=[(root,0,0)];seen=set();errors=[]
            if prefix:
                item=await c.inspect(root)
                if item['node_class']=='Variable':
                    job['nodes'].append(item);seen.add(root)
            while queue:
                nid,offset,depth=queue.pop(0)
                if depth>64:
                    errors.append(nid+': profondita massima 64');continue
                try:page=await c.browse_page(nid,offset,min(250,max(1,limit-len(job['nodes']))))
                except (ValueError,ADSError) as exc:
                    errors.append(nid+': '+str(exc));continue
                if page.get('warning'):errors.append(nid+': '+page['warning'])
                for item in page['nodes']:
                    if item['nodeid'] in seen:continue
                    seen.add(item['nodeid'])
                    if item['node_class']=='Variable':
                        if len(job['nodes'])>=limit:break
                        with runtime.lock:
                            job['nodes'].append(item);job['visited']+=1
                    if item.get('has_children'):queue.append((item['nodeid'],0,depth+1))
                if page.get('next_offset') is not None:queue.append((nid,page['next_offset'],depth))
                if len(job['nodes'])>=limit:
                    job['state']='limited';break
                job['pending']=len(queue)
                await asyncio.sleep(0)
            else:job['state']='completed'
            job['pending']=0;job['catalog_count']=len(symbols)
            job['note']='Scansione simboli e membri datatype ADS.'
            if errors:
                job['note']+=' Alcuni rami non sono espandibili: '+'; '.join(errors[:20])
                job['error']='; '.join(errors[:20])
                logging.warning('Scansione ADS parziale: %s',job['error'])
    except asyncio.CancelledError:
        job['state'] = 'cancelled'
    except Exception as exc:
        job['state'], job['error'] = 'failed', str(exc) or type(exc).__name__
    finally:
        await c.disconnect()
        job['finished_at'] = datetime.now(timezone.utc).isoformat()


def ads_notification_selection():
    return list(runtime.selected),runtime.sample_ms,(runtime.selection_revision,runtime.buffer_epoch)


def acquisition_record(t,rows,period,generation):
    if generation is not None and generation[0]!=runtime.selection_revision:return False
    if not runtime.recording_accept:return False
    begin=getattr(runtime,'record_receive_begin_mono_ns',None);end=getattr(runtime,'record_receive_end_mono_ns',None)
    if begin is not None:
        rows=[r for r in rows if int(r['_received_mono_ns'])>=begin and (end is None or int(r['_received_mono_ns'])<=end)]
    if not rows:return False
    if rows[0].get('acquisition_origin')=='notification' and not source_sessions.enabled:
        accepted=recording.enqueue_primary(t,rows,period)
    else:accepted=recording.enqueue(t,rows,period)
    if accepted:
        previous=getattr(runtime,'record_raw_last_t',None)
        runtime.record_raw_last_t=max(t,previous) if previous is not None else t
    return accepted


def acquisition_calculate(t,rows,period,generation):
    if generation is not None and generation[0]!=runtime.selection_revision:return
    batch=[r for r in rows if r.get('acquisition_origin')!='notification']
    if batch:calculations.submit(t,batch,period)


def acquisition_display(t,rows,period,generation):
    # Only this consumer touches the live FIFO/history or waits for the UI lock.
    with runtime.lock:
        calculated=generation is not None and generation[0]=='calculated'
        if calculated:
            if generation[1]!=runtime.calculation_generation:return
        elif generation is not None and generation!=(runtime.selection_revision,runtime.buffer_epoch):return
        if not source_sessions.enabled and rows and rows[0].get('acquisition_origin')=='notification':return
        current={r['nodeid']:r for r in runtime.latest_values}
        for original in rows:
            # Raw Python values/bytes belong to the primary archive, not JSON UI.
            row={k:v for k,v in original.items() if not k.startswith('_') and k not in ('source_value','source_binary')}
            nid=row['nodeid'];points=runtime.samples.get(nid)
            if points is None:continue
            if points and t<points[-1][0] and not (protocol_of(nid)=='ads' and not source_sessions.enabled):
                if protocol_of(nid)=='mqtt':runtime.mqtt_late_samples+=1
                continue
            current[nid]=row
            point=[t,row.get('plot_value'),row.get('quality'),row.get('status_code'),row.get('source_ts'),row.get('server_ts'),
                str(row.get('value')),row.get('data_type'),row.get('status'),period,row.get('local_ts'),row.get('source_filetime')]
            if calculated and points and points[-1][0]==t:points[-1]=point
            else:points.append(point)
            if calculated:runtime.calculated_latest[nid]=row
            while runtime.buffer_mode=='time' and points and points[0][0]<t-runtime.window_seconds*1000:points.popleft()
        runtime.latest_values=[current[n] for n in runtime.selected if n in current]
        if not calculated:
            runtime.read_count+=1;runtime.notification_count+=len(rows)
            runtime.last_sample_at=max(t,runtime.last_sample_at or t);runtime.last_notification_at=runtime.last_sample_at


acquisition=AcquisitionEngine(acquisition_record,acquisition_display,recording.fail,acquisition_events,calculate=acquisition_calculate)
def live_delivery_loss(item,count):
    generation=item[3]
    current=generation==(runtime.selection_revision,runtime.buffer_epoch) or generation==('calculated',runtime.calculation_generation)
    if current and not live_archive.error:
        live_archive.error='Storico Live incompleto: visualizzazione in ritardo; usare la registrazione primaria per gli originali'
        acquisition_events.emit('live_distribution_skipped',samples=count,generation=generation)

acquisition.live.on_drop=live_delivery_loss
atexit.register(acquisition_events.close)
atexit.register(acquisition.close)


def submit_acquisition(ts,rows,period,generation):
    if generation is not None and generation[0]!=runtime.selection_revision:return False
    groups={}
    for row in rows:groups.setdefault(protocol_of(row['nodeid']),[]).append(row)
    for protocol,batch in groups.items():acquisition.submit(protocol,protocol,ts,batch,period,generation,backpressure_timeout=.5 if protocol=='mqtt' else 0)
    return True


def ads_notification_sink(ts,rows,sample_ms,generation):
    return submit_acquisition(ts,rows,sample_ms,generation)


def ads_notification_report(diag):
    previous=getattr(runtime,'ads_notification_diag',{})
    if diag.get('error') or diag.get('dropped',0)>previous.get('dropped',0):
        acquisition_events.emit('ads_acquisition_fault',acquisition.connection('ads','ads')[1].connection_id,diagnostics=diag)
        recording.fail(diag.get('error') or 'ADS: perdita notifiche')
    runtime.ads_notification_diag=diag
    runtime.acquisition_mode='ADS notifiche cicliche · timestamp PLC'
    runtime.last_cycle_ms=0
    period=diag.get('source_period_ms')
    runtime.periods.clear()
    if period is not None:runtime.periods.append(period);runtime.actual_period_ms=period
    if isinstance(runtime.client,ADSClient):runtime.client.info.update(diag)


async def ads_notification_loop():
    from ads_notifications import NotificationAcquisition
    while runtime.want_connected:
        worker=None
        try:
            if not runtime.connected:await open_client(runtime.active_config)
            runtime.acquisition_mode='Avvio notifiche ADS cicliche'
            worker=NotificationAcquisition(runtime.active_config,ads_notification_selection,ads_notification_sink,ads_notification_report)
            runtime.ads_notification_worker=worker
            worker.start()
            while runtime.want_connected and worker.thread.is_alive():await asyncio.sleep(.05)
            if worker.error:raise RuntimeError(worker.error)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            runtime.last_error=str(exc);runtime.connected=False
            logging.exception('Acquisizione ADS ciclica interrotta')
            with runtime.lock:
                now=time.time()*1000
                bad=[]
                for nid in runtime.selected:
                    bad.append({'nodeid':nid,'good':False,'quality':'Bad','status':'ADS notification interrupted',
                                'error':str(exc),'value':None,'plot_value':None,'data_type':'Null'})
                    points=runtime.samples.get(nid)
                    if points is not None:
                        gap=points[-1][0]+runtime.sample_ms if points else now
                        points.append([gap,None,'Bad',None,None,None,None,'Null','NotificationInterrupted',runtime.sample_ms])
                runtime.latest_values=bad
                enqueue_recording(now,bad,runtime.sample_ms)
                calculations.submit(max((p[-1][0] for n,p in runtime.samples.items() if p and not n.startswith('calc:')), default=now), bad, runtime.sample_ms)
            await stop_scan();await close_client()
            if not runtime.active_config.get('auto_reconnect',True):
                runtime.want_connected=False;runtime.state='disconnected'
                await asyncio.to_thread(recording.stop,'connection_lost',5)
            else:runtime.state='reconnecting'
        finally:
            if worker:
                worker.stop()
                # No native callback may outlive its owning port or worker storage.
                await asyncio.to_thread(worker.thread.join)
                runtime.ads_notification_worker=None
        if runtime.want_connected and not runtime.connected:await asyncio.sleep(3)


async def ads_sampler_loop():
    if runtime.active_config.get('ads_mode')=='notify':
        await ads_notification_loop();return
    await ads_polling_loop()


def mqtt_notification_sink(ts,rows,sample_ms,generation):
    return submit_acquisition(ts,rows,sample_ms,generation)


async def mqtt_sampler_loop():
    try:
        while runtime.want_connected:
            if not runtime.connected:
                await asyncio.sleep(1)
                if not runtime.want_connected:break
                try:
                    await open_client(runtime.active_config)
                    runtime.mqtt_reconnections+=1
                except Exception as exc:
                    runtime.last_error=str(exc);continue
            c=runtime.client
            if c.consumer is None:c.configure_acquisition(ads_notification_selection,mqtt_notification_sink)
            try:
                await c.read_state();diag=c.diagnostics()
                runtime.acquisition_mode='MQTT · ogni pubblicazione · '+c.info['timestamps']
                runtime.last_cycle_ms=0;runtime.actual_period_ms=diag['arrival_period_ms'] or 0
                runtime.periods.clear()
                if diag['arrival_period_ms'] is not None:runtime.periods.append(diag['arrival_period_ms'])
            except Exception as exc:
                runtime.last_error=str(exc)
                if c.error:
                    recording.source_diagnostics(c.diagnostics());recording.fail(c.error)
                await stop_scan();await close_client()
                if c.error:
                    recording.source_diagnostics(c.diagnostics());recording.fail(c.error)
                received=time.time()*1000
                with runtime.lock:
                    generation=(runtime.selection_revision,runtime.buffer_epoch)
                    gaps=[(n,points[-1][0] if runtime.active_config['mqtt_time_basis']=='payload' and points else received) for n in runtime.selected if (points:=runtime.samples.get(n)) is not None]
                for nid,stamp in gaps:
                    row=mqtt_value_row(nid,None,'Bad','MQTT collegamento interrotto',None,received)
                    mqtt_notification_sink(stamp,[row],runtime.sample_ms,generation)
                if not runtime.active_config.get('auto_reconnect',True):
                    runtime.want_connected=False;runtime.state='disconnected'
                    await asyncio.to_thread(finish_recording,'connection_lost',5);break
                runtime.state='reconnecting';continue
            await asyncio.sleep(.1)
    except asyncio.CancelledError:pass


async def s7_sampler_loop():
    if not isinstance(runtime.client,S7UDPClient):
        await ads_polling_loop(); return
    last_loss=None
    try:
        while runtime.want_connected:
            if not runtime.connected:
                await asyncio.sleep(3)
                if not runtime.want_connected: break
                try: await open_client(runtime.active_config)
                except Exception as exc:
                    runtime.last_error=str(exc); continue
            c=runtime.client
            if not c.consumer: c.configure_acquisition(ads_notification_selection,ads_notification_sink)
            try:
                await c.read_state(); diag=c.diagnostics()
                runtime.acquisition_mode='S7 UDP ciclico · '+('ricezione attiva' if diag['receiving'] else 'in attesa di telegrammi PLC')
                runtime.last_cycle_ms=0
                period=diag['arrival_period_ms']
                runtime.periods.clear()
                if period is not None: runtime.periods.append(period); runtime.actual_period_ms=period
                runtime.last_error=diag.get('error','')
                loss=(diag['lost'],diag['queue_dropped'],diag['timeouts'])
                if last_loss is not None and loss!=last_loss:
                    logging.warning('S7 UDP diagnostics: lost=%s queue_dropped=%s timeouts=%s',*loss)
                    acquisition_events.emit('s7_udp_diagnostic_change',acquisition.connection('s7','s7')[1].connection_id,diagnostics=diag)
                if diag.get('lost') or diag.get('queue_dropped') or diag.get('timestamp_errors'):
                    recording.fail('S7 UDP: perdita sequenza/coda o timestamp non valido; vedere diagnostica')
                last_loss=loss
            except Exception as exc:
                runtime.last_error=str(exc); await close_client()
                if not runtime.active_config.get('auto_reconnect',True):
                    runtime.want_connected=False; runtime.state='disconnected'
                    await asyncio.to_thread(finish_recording,'connection_lost',5); break
                runtime.state='reconnecting'; continue
            await asyncio.sleep(.2)
    except asyncio.CancelledError: pass


async def ads_polling_loop():
    """Shared asynchronous polling for ADS Read and S7 ReadVar."""
    last_start, deadline, heartbeat = None, time.perf_counter(), 0
    try:
        while runtime.want_connected:
            if not runtime.connected:
                await asyncio.sleep(3)
                if not runtime.want_connected: break
                try:
                    await open_client(runtime.active_config)
                    deadline, last_start, heartbeat = time.perf_counter(), None, 0
                except Exception as exc:
                    runtime.last_error = str(exc) or type(exc).__name__
                    logging.warning("%s reconnect: %s", runtime.active_config['source'], runtime.last_error)
                    continue
            start = time.perf_counter()
            nids = list(runtime.selected)
            revision, epoch = runtime.selection_revision, runtime.buffer_epoch
            interval = runtime.sample_ms / 1000 if nids else .5
            try:
                c = runtime.client
                if start - heartbeat >= 1:
                    await c.read_state()
                    await c.check_symbols()
                    heartbeat = start
                if nids:
                    rows = await c.read_values(nids)
                    runtime.acquisition_mode = c.info.get('read_mode', 'ADS Read')
                    ts = time.time() * 1000
                    for row in rows:
                        if row.get('acquisition_origin')=='notification':
                            row['acquisition_origin']='subscription_snapshot';row['_received_mono_ns']=time.monotonic_ns();row['received_ns']=time.time_ns()
                    submit_acquisition(ts,rows,runtime.sample_ms,(revision,epoch))
                    runtime.last_cycle_ms=(time.perf_counter()-start)*1000
                    runtime.actual_period_ms=(start-last_start)*1000 if last_start is not None else 0
                    if last_start is not None:runtime.periods.append(runtime.actual_period_ms)
                    last_start = start
            except Exception as exc:
                logging.warning("%s connection lost: %s", runtime.active_config['source'], type(exc).__name__)
                runtime.last_error = str(exc) or type(exc).__name__
                runtime.connected = False
                await stop_scan()
                with runtime.lock:
                    ts = time.time() * 1000
                    runtime.latest_values = [{"nodeid": n, "good": False, "error": runtime.last_error,
                                              "status": "Connessione persa", "value": None,
                                              "plot_value": None, "data_type": "Null"} for n in runtime.selected]
                    for points in runtime.samples.values():
                        points.append([ts, None, "Bad", None, None, None, None, "Null", "ConnectionLost", runtime.sample_ms])
                    enqueue_recording(ts, runtime.latest_values, runtime.sample_ms)
                    calculations.submit(ts, runtime.latest_values, runtime.sample_ms)
                await close_client()
                if not runtime.active_config.get("auto_reconnect", True):
                    runtime.want_connected, runtime.state = False, "disconnected"
                    await asyncio.to_thread(recording.stop, "connection_lost", 5)
                    break
                runtime.state = "reconnecting"
                continue
            deadline += interval
            if deadline < time.perf_counter() - interval:
                deadline = time.perf_counter() + interval
            await asyncio.sleep(max(0, deadline - time.perf_counter()))
    except asyncio.CancelledError:
        pass


def connection_guard(cfg):
    if runtime.want_connected and configured_sources(cfg)!=configured_sources(runtime.active_config):
        raise ValueError('Disconnettere prima di cambiare i protocolli attivi')
    if source_sessions.enabled:
        active=runtime.active_config
        mutable={'source','ui_language','window_seconds','max_samples','buffer_mode','root_nodeid','ads_root','mqtt_root','s7_root','s7_symbolic_root','s7_udp_root','scan_limit','mqtt_password','s7_symbolic_password','auto_reconnect'}
        if any(cfg[k]!=active.get(k) for k in DEFAULT_CONFIG if k not in mutable):
            raise ValueError('Disconnettere prima di modificare i parametri delle sorgenti')
        return
    if (runtime.want_connected or parallel_mqtt.enabled) and cfg['source'] != runtime.active_config.get('source', 'opcua'):
        raise ValueError('Disconnettere prima di cambiare fonte dati')
    if (runtime.want_connected or parallel_mqtt.enabled) and cfg['source']=='s7':
        if any(cfg.get(k)!=runtime.active_config.get(k) for k in S7_DEFAULTS
               if k not in ('s7_root','s7_symbolic_root','s7_udp_root','s7_symbolic_password')):
            raise ValueError('Disconnettere prima di modificare modalità, catalogo o connessione S7')
    if (runtime.want_connected or parallel_mqtt.enabled) and cfg.get('mqtt_parallel') != runtime.active_config.get('mqtt_parallel',False):
        raise ValueError('Disconnettere prima di cambiare la modalità simultanea')
    if (runtime.want_connected or parallel_mqtt.enabled) and (cfg['source']=='mqtt' or cfg.get('mqtt_parallel')):
        if any(cfg.get(k)!=runtime.active_config.get(k) for k in MQTT_DEFAULTS if k not in ('mqtt_root','mqtt_password')):
            raise ValueError('Disconnettere prima di modificare broker, topic o decodifica MQTT')


async def sampler_loop():
    """Store a uniform local trend buffer while sourcing values primarily from MonitoredItems."""
    last_start, deadline = None, time.perf_counter()
    last_bad_probe = 0.0
    last_heartbeat = 0.0
    last_sub_attempt = 0.0
    last_sub_signature = None
    try:
        while runtime.want_connected:
            if not runtime.connected:
                await asyncio.sleep(3)
                if not runtime.want_connected:
                    break
                try:
                    await open_client(runtime.active_config)
                    deadline, last_start = time.perf_counter(), None
                    runtime.subscription_revision = -1
                except Exception as exc:
                    logging.warning("OPC UA connection lost: %s", type(exc).__name__)
                    runtime.last_error = str(exc) or type(exc).__name__
                    continue
            start = time.perf_counter()
            nids = [n for n in runtime.selected if protocol_of(n)=='opcua']
            revision, epoch = runtime.selection_revision, runtime.buffer_epoch
            sub_revision = runtime.subscription_revision
            sub_sample_ms = runtime.subscription_sample_ms
            interval = runtime.sample_ms / 1000 if nids else .5
            try:
                if nids:
                    # Recreate monitored items after selection/sampling changes. Failed servers are
                    # retried with a 2 s backoff instead of hammering CreateSubscription at 10 ms.
                    now_perf = time.perf_counter()
                    sub_signature = (tuple(nids), revision, runtime.sample_ms)
                    need_sub = runtime.subscription is None or sub_revision != revision or sub_sample_ms != runtime.sample_ms
                    should_attempt = need_sub and (sub_signature != last_sub_signature or now_perf - last_sub_attempt >= 2.0)
                    if should_attempt:
                        last_sub_signature, last_sub_attempt = sub_signature, now_perf
                        try:
                            await rebuild_subscription(nids, revision)
                        except Exception as exc:
                            runtime.acquisition_mode = "Read polling (fallback)"
                            runtime.last_error = "Subscription non disponibile: " + (str(exc) or type(exc).__name__)

                    cache = {nid: dict(row) for nid, row in list(runtime.subscription_rows.items()) if nid in nids}
                    sub_bad = dict(runtime.subscription_bad)
                    has_subscription = runtime.subscription is not None

                    direct_rows = {}
                    if has_subscription:
                        missing = [nid for nid in nids if nid not in cache]
                        bad_cached = [nid for nid in nids if nid in cache and not cache[nid].get("good")]
                        # Immediately seed missing items; afterwards retry bad-quality nodes only at 4 Hz.
                        retry_bad_due = now_perf - last_bad_probe >= .25
                        extra_bad = (bad_cached + list(sub_bad)) if retry_bad_due else []
                        probe = list(dict.fromkeys(missing + extra_bad))
                        if probe:
                            direct = await read_selected_once(probe, retry_individual=True)
                            direct_rows = {row["nodeid"]: row for row in direct}
                            last_bad_probe = now_perf
                            runtime.read_fallback_count += 1
                            for nid, row in direct_rows.items():
                                current = runtime.subscription_rows.get(nid)
                                if current is None or row.get("good") or not current.get("good"):
                                    runtime.subscription_rows[nid] = row
                            cache.update(direct_rows)
                        rows = [cache.get(nid, missing_row(nid)) for nid in nids]
                    else:
                        retry_now = now_perf - last_bad_probe >= .25
                        rows = await read_selected_once(nids, retry_individual=retry_now)
                        if retry_now:
                            last_bad_probe = now_perf
                        runtime.read_fallback_count += 1
                        if runtime.individual_nodes:
                            runtime.acquisition_mode = "Read polling + individual fallback"
                        else:
                            runtime.acquisition_mode = "Read polling (fallback)"

                    # Explicit heartbeat catches a lost transport even when values are constant.
                    if now_perf - last_heartbeat >= 1.0:
                        await runtime.client.nodes.server_state.read_data_value()
                        last_heartbeat = now_perf

                    ts = time.time() * 1000
                    for row in rows:
                        if row.get('acquisition_origin')=='notification':
                            row['acquisition_origin']='subscription_snapshot';row['_received_mono_ns']=time.monotonic_ns();row['received_ns']=time.time_ns()
                    submit_acquisition(ts,rows,runtime.sample_ms,(revision,epoch))
                    runtime.last_cycle_ms=(time.perf_counter()-start)*1000
                    runtime.actual_period_ms=(start-last_start)*1000 if last_start is not None else 0
                    if last_start is not None:runtime.periods.append(runtime.actual_period_ms)
                    last_start = start
                else:
                    if runtime.subscription is not None:
                        await stop_subscription()
                    await runtime.client.nodes.server_state.read_value()
                    last_start = None
            except Exception as exc:
                logging.warning("OPC UA connection lost: %s", type(exc).__name__)
                runtime.last_error = str(exc) or type(exc).__name__
                runtime.connected = False
                await stop_scan()
                with runtime.lock:
                    ts = time.time() * 1000
                    lost_rows = [{"nodeid": n, "good": False, "error": runtime.last_error,
                                              "status": "Connessione persa", "value": None,
                                              "plot_value": None, "data_type": "Null"} for n in runtime.selected if protocol_of(n)=='opcua']
                    runtime.latest_values = [r for r in runtime.latest_values if r['nodeid'].startswith('mqtt:')] + lost_rows
                    for nid,points in runtime.samples.items():
                        if protocol_of(nid)!='opcua':continue
                        points.append([ts, None, "Bad", None, None, None, None, "Null", "ConnectionLost", runtime.sample_ms])
                    enqueue_recording(ts, lost_rows, runtime.sample_ms)
                    calculations.submit(ts, lost_rows, runtime.sample_ms)
                await close_client()
                if not runtime.active_config.get("auto_reconnect", True):
                    runtime.want_connected, runtime.state = False, "disconnected"
                    if not parallel_mqtt.enabled: await asyncio.to_thread(recording.stop, 'connection_lost', 5)
                    break
                runtime.state = "reconnecting"
                continue
            deadline += interval
            if deadline < time.perf_counter() - interval:
                deadline = time.perf_counter() + interval
            await asyncio.sleep(max(0, deadline - time.perf_counter()))
    except asyncio.CancelledError:
        pass


async def apply_selection(nodes):
    nids = [n["nodeid"] for n in nodes]
    if (runtime.want_connected or parallel_mqtt.enabled) and any(protocol_of(n) not in allowed_sources(runtime.active_config) for n in nids):
        raise ValueError('I segnali selezionati non appartengono alla fonte connessa')
    if (runtime.want_connected or parallel_mqtt.enabled): s7_selection_guard(runtime.active_config,nids)
    with runtime.lock:
        if recording.acquiring and set(nids) != set(runtime.selected):
            raise ValueError("Premere Stop prima di cambiare i segnali della registrazione")
        if nids == runtime.selected:
            return
        runtime.selected = nids
        runtime.selection_revision += 1
        runtime.samples = {n: live_buffer(n,runtime.samples.get(n, [])) for n in nids}
        reset_calculations((runtime.active_config or load_config()).get('source','opcua'))
        runtime.latest_values = [v for v in runtime.latest_values if v["nodeid"] in nids]


def allowed_sources(cfg):
    return configured_sources(cfg)


def parallel_selection():
    return [n for n in runtime.selected if n.startswith('mqtt:')],runtime.sample_ms,(runtime.selection_revision,runtime.buffer_epoch)


def parallel_sink(ts,rows,period,generation):
    return submit_acquisition(ts,rows,period,generation)


from parallel_mqtt import ParallelMQTT
parallel_mqtt=ParallelMQTT(runtime,parallel_sink,parallel_selection,recording.fail)


from source_sessions import SourceSessions, PROTOCOLS, configured_sources


def mixed_sink(ts,rows,period,generation):
    return submit_acquisition(ts,rows,period,generation)


def mixed_selection():
    return ads_notification_selection()


source_sessions=SourceSessions(make_protocol_client,mixed_selection,mixed_sink,recording.fail,protocol_of,value_row,refresh_sources)
source_sessions.events=acquisition_events
source_sessions.connection_id=lambda p:acquisition.connection(p,p)[1].connection_id


def mqtt_catalog_client():
    if source_sessions.enabled: return source_sessions.client("mqtt")
    c=runtime.client if isinstance(runtime.client,MQTTClient) else parallel_mqtt.client
    if c is None: raise ValueError('MQTT non connesso')
    return c


def compress_points(points, budget=1200):
    """Keep extrema and gap markers in chronological order for canvas rendering."""
    if len(points) <= budget:
        return points
    step = math.ceil(len(points) / (budget // 5))
    out = []
    for start in range(0, len(points), step):
        bucket = points[start:start + step]
        indices = {0, len(bucket) - 1}
        numeric = [i for i, p in enumerate(bucket) if p[1] is not None]
        if numeric:
            indices.add(min(numeric, key=lambda i: bucket[i][1]))
            indices.add(max(numeric, key=lambda i: bucket[i][1]))
        gap = next((i for i, p in enumerate(bucket) if p[1] is None), None)
        if gap is not None:
            indices.add(gap)
        for quality in ("Bad", "Uncertain"):
            match=next((i for i,p in enumerate(bucket) if len(p)>2 and p[2]==quality),None)
            if match is not None:indices.add(match)
        previous = None
        for i in sorted(indices):
            if previous is not None and bucket[previous][1] is not None and bucket[i][1] is not None:
                hidden_gap = next((j for j in range(previous + 1, i) if bucket[j][1] is None), None)
                if hidden_gap is not None:
                    out.append(bucket[hidden_gap])
            out.append(bucket[i])
            previous = i
    return out


@app.before_request
def local_access():
    if request.host.split(":")[0] not in {"127.0.0.1", "localhost"}:
        return jsonify(error="Accesso consentito solo da localhost"), 403
    if request.method == "POST" and request.headers.get("X-OPCUA-Token") != SESSION_TOKEN:
        return jsonify(error="Pagina scaduta: ricaricare l'interfaccia"), 403


@app.after_request
def response_headers(response):
    if request.path.endswith((".js", ".mjs")):
        response.headers["Content-Type"] = "application/javascript; charset=utf-8"
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    return response


@app.errorhandler(Exception)
def api_error(exc):
    code = exc.code if isinstance(exc, HTTPException) else 400 if isinstance(exc, ValueError) else 500
    message = str(exc) or type(exc).__name__
    if code == 500:
        logging.exception("Operazione fallita")
    return jsonify(ok=False, error=message), code


@app.get("/")
def index():
    code=load_config().get('ui_language','it')
    return render_template("index.html", version=APP_VERSION, token=SESSION_TOKEN, ui_language=code if code in ('en','it','de','fr','es') else 'it')


@app.post('/api/ui/language')
def api_ui_language():
    data=request.get_json() or {}
    if not isinstance(data,dict):raise ValueError('Lingua interfaccia non supportata')
    code=data.get('ui_language')
    if not isinstance(code,str) or code not in ('en','it','de','fr','es'):
        raise ValueError('Lingua interfaccia non supportata')
    with file_lock:
        cfg=load_config();cfg['ui_language']=code;save_config(cfg,ui_language_update=True)
        for path in (WORKSPACE_FILE,SETTINGS_FILE):
            if not path.is_file():continue
            try:profile=json.loads(path.read_text(encoding='utf-8'))
            except (OSError,ValueError):continue
            if isinstance(profile,dict) and isinstance(profile.get('config'),dict):
                profile['config']['ui_language']=code;atomic_json(path,profile)
    return jsonify(ok=True,ui_language=code)


@app.get("/api/config")
def api_get_config():
    cfg = load_config()
    info, server_info, saved = cert_info(), certificate_info(SERVER_CERT_FILE), load_saved_settings()
    cfg.update({"client_cert_abs": info["path"], "client_key_abs": info["key_path"],
        "server_cert_abs": server_info.get("path", str(SERVER_CERT_FILE)),
        "server_cert_present": server_info.get("present", False),
        "server_cert_sha256": server_info.get("thumbprint_sha256", ""),
        "version": APP_VERSION, "max_selected": MAX_SELECTED,
        "saved_settings_present": bool(saved), "saved_at": saved.get("saved_at", ""),
        "selected_nodes": saved.get("selected_nodes", []), "saved_server_data": saved.get("server_data", {}),
        "data_dir": str(DATA_DIR)})
    cfg["recordings_dir"] = str(RECORDINGS_DIR)
    cfg["workspace"] = load_workspace()
    cfg['calculations'] = formula_store.snapshot()
    rec = recording.status()
    cfg["recording_busy"] = rec.get("selection_locked",rec["busy"])
    if rec.get("selection_locked",rec["busy"]):
        cfg["selected_nodes"] = [n for n in rec["nodes"] if not n['nodeid'].startswith('calc:')]
    return jsonify(cfg)


def sample_limit():
    return max(1000, min(runtime.max_samples, MAX_TOTAL_SAMPLES // max(1,len(runtime.selected)+len(calculations.ids))))


def apply_live_config(cfg):
    runtime.sample_ms, runtime.window_seconds = cfg["sample_ms"], cfg["window_seconds"]
    runtime.max_samples, runtime.buffer_mode = cfg["max_samples"], cfg["buffer_mode"]
    with runtime.lock:
        runtime.samples = {nid: live_buffer(nid,points) for nid, points in runtime.samples.items()}
    if source_sessions.enabled:
        for session in source_sessions.sessions.values(): session.cfg["auto_reconnect"]=cfg["auto_reconnect"]
    if runtime.active_config:
        for k in ("sample_ms", "window_seconds", "auto_reconnect", "max_samples", "buffer_mode"):
            runtime.active_config[k] = cfg[k]


@app.post("/api/config")
def api_save_config():
    cfg = validated_config(request.get_json())
    connection_guard(cfg)
    if runtime.want_connected and cfg["application_uri"] != runtime.active_config["application_uri"]:
        raise ValueError("Disconnettere prima di cambiare Application URI")
    save_config(cfg)
    apply_live_config(cfg)
    return jsonify(ok=True)


@app.post("/api/settings/save")
def api_save_full_settings():
    profile = runtime.run(save_settings_command(request.get_json()))
    return jsonify(ok=True, saved_at=profile["saved_at"], settings_file=str(SETTINGS_FILE))


async def save_settings_command(data):
    cfg = validated_config(data.get("config", data))
    connection_guard(cfg)
    nodes = validated_nodes(data.get("selected_nodes", []))
    if recording.acquiring and set(n["nodeid"] for n in nodes) != set(runtime.selected):
        raise ValueError("Premere Stop prima di cambiare i segnali della registrazione")
    with file_lock:
        if runtime.want_connected and cfg["application_uri"] != runtime.active_config["application_uri"]:
            raise ValueError("Disconnettere prima di cambiare Application URI e certificato client")
        profile = save_full_settings(cfg, nodes, data.get("server_data", {}))
    apply_live_config(cfg)
    await apply_selection(nodes)
    return profile


@app.get("/api/certificate")
def api_certificate():
    return jsonify(cert_info())


@app.post("/api/certificate/regenerate")
def api_regenerate_certificate():
    if runtime.want_connected:
        raise ValueError("Disconnettere il client prima di rigenerare il certificato")
    data = request.get_json() or {}
    with file_lock:
        uri, created = ensure_client_certificate(data.get("application_uri"), force=True)
    return jsonify(ok=True, created=created, application_uri=uri, info=cert_info())


@app.get("/api/certificate/download")
def api_download_certificate():
    return send_file(CERT_FILE, as_attachment=True, download_name="client.der")


@app.get("/api/server-certificate")
def api_server_certificate_info():
    return jsonify(certificate_info(SERVER_CERT_FILE))


@app.post("/api/server-certificate")
def api_server_certificate():
    if runtime.want_connected:
        raise ValueError("Disconnettere prima di sostituire il certificato server")
    if "file" not in request.files:
        raise ValueError("Selezionare un certificato server")
    cert = load_certificate_any(request.files["file"].read())
    CERT_DIR.mkdir(parents=True, exist_ok=True)
    with file_lock:
        SERVER_CERT_FILE.write_bytes(cert.public_bytes(serialization.Encoding.DER))
    return jsonify(ok=True, info=certificate_info(SERVER_CERT_FILE))


@app.post("/api/endpoints")
def api_endpoints():
    cfg = validated_config(request.get_json())
    endpoints, _ = runtime.run(discover_endpoints_async(cfg["endpoint"]), timeout=18)
    return jsonify(ok=True, endpoints=endpoints)


@app.post("/api/server-certificate/fetch")
def api_fetch_server_certificate():
    if runtime.want_connected:
        raise ValueError("Disconnettere prima di sostituire il certificato server")
    cfg = validated_config(request.get_json())
    if cfg["security"] == "None":
        raise ValueError("Selezionare una policy sicura per acquisire il certificato server")
    info = runtime.run(fetch_server_certificate_async(cfg["endpoint"], cfg["security"], cfg["security_mode"]), timeout=18)
    return jsonify(ok=True, info=info)


async def test_mqtt_connection(cfg):
    async with runtime.command_lock:
        if runtime.want_connected or recording.acquiring:raise ValueError('Disconnettere e fermare Record prima del test MQTT')
        c=MQTTClient(cfg)
        c.configure_acquisition(lambda:([],cfg['sample_ms'],None),None)
        try:
            await c.connect()
            return dict(c.info)
        finally:await c.disconnect()


@app.post('/api/mqtt/test')
def api_mqtt_test():
    cfg=validated_config({**(request.get_json() or {}),'source':'mqtt'})
    return jsonify(ok=True,mqtt=runtime.run(test_mqtt_connection(cfg),timeout=35))


async def test_ads_connection(cfg):
    async with runtime.command_lock:
        if runtime.want_connected:
            raise ValueError("Disconnettere prima di eseguire il test ADS")
        c = create_ads_client(cfg, catalog=True)
        try:
            await c.connect()
            return dict(c.info)
        finally:
            await c.disconnect()


@app.post("/api/ads/test")
def api_ads_test():
    cfg = validated_config({**(request.get_json() or {}), "source": "ads"})
    return jsonify(ok=True, ads=runtime.run(test_ads_connection(cfg), timeout=35))


async def test_s7_connection(cfg):
    async with runtime.command_lock:
        if runtime.want_connected: raise ValueError('Disconnettere prima del test S7')
        c = create_s7_client(cfg)
        try:
            await c.connect()
            if isinstance(c,S7SymbolicClient):
                await c.ensure_catalog()
                probe=await c.read_values(list(c.catalog)[:1])
            elif isinstance(c,S7UDPClient):
                c.configure_acquisition(lambda:(list(c.catalog)[:1],cfg['sample_ms'],None),None)
                received=await asyncio.to_thread(c.first_packet.wait,min(10,cfg['s7_timeout_ms']/1000))
                if not received: raise ValueError('Socket UDP aperto, ma nessun telegramma valido dal PLC. Verificare TUSEND, IP PC, firewall, modulo e lunghezza.')
                await asyncio.to_thread(c.queue.join)
                probe=await c.read_values(list(c.catalog)[:1]); c.info['diagnostics']=c.diagnostics()
            else: probe = await c.read_values([cfg['s7_tags'][0]['nodeid']]) if cfg['s7_tags'] else []
            return dict(c.info), probe
        finally: await c.disconnect()


@app.post('/api/s7/test')
def api_s7_test():
    cfg = validated_config({**(request.get_json() or {}), 'source':'s7'})
    info, probe = runtime.run(test_s7_connection(cfg), timeout=180 if cfg['s7_mode']=='symbolic' else 35)
    return jsonify(ok=True, s7=info, probe=probe)


@app.post('/api/s7/tags/validate')
def api_s7_tags_validate():
    return jsonify(ok=True, tags=normalize_tags((request.get_json() or {}).get('tags')))


@app.post('/api/s7/udp/tags/validate')
def api_s7_udp_tags_validate():
    data=request.get_json() or {}
    cfg=validated_config({**(data.get('config') or {}),'s7_udp_tags':data.get('tags')})
    return jsonify(ok=True,tags=normalize_udp_tags(data.get('tags'),cfg['s7_udp_length']-6))


@app.post('/api/s7/symbols/refresh')
def api_s7_symbols_refresh():
    async def command():
        async with runtime.command_lock:
            c=require_connection('s7' if source_sessions.enabled else None)
            if not isinstance(c,S7SymbolicClient): raise ValueError('Connettere la modalità S7 simbolica')
            if recording.acquiring and not source_sessions.enabled: raise ValueError('Fermare Record prima di aggiornare il catalogo della CPU')
            await c.ensure_catalog(refresh=True)
            return len(c.catalog)
    return jsonify(ok=True,symbols=runtime.run(command(),timeout=180))


@app.post("/api/connect")
def api_connect():
    cfg = validated_config(request.get_json())
    runtime.run(connect_async(cfg), timeout=max(25,cfg['mqtt_timeout_ms']/1000+5) if cfg['source']=='mqtt' else 25)
    save_config(cfg)
    return jsonify(ok=True, connected=runtime.connected, sources=source_sessions.status())


@app.post("/api/disconnect")
def api_disconnect():
    runtime.run(disconnect_command(), timeout=12)
    return jsonify(ok=True, connected=False)


@app.post('/api/sources/<protocol>/<action>')
def api_source_control(protocol, action):
    if protocol not in source_sessions.sessions or action not in ('connect','disconnect'):
        raise ValueError('Sorgente o comando non valido')
    async def command():
        async with runtime.command_lock:
            session=source_sessions.sessions[protocol]
            await session.stop()
            ids,period,generation=session.selection()
            mixed_sink(time.time()*1000,[dict(nodeid=n,value=None,plot_value=None,good=False,quality='Bad',status='Disconnected',data_type='Null') for n in ids],period,generation)
            if action=='connect':
                session.enabled=True
                session.task=asyncio.create_task(session.run())
    runtime.run(command())
    refresh_sources()
    return jsonify(ok=True,sources=source_sessions.status())


@app.get("/api/status")
def api_status():
    refresh_sources()
    return jsonify(app=APP_NAME, version=APP_VERSION, state=runtime.state,
                   connected=runtime.connected, error=runtime.last_error, sources=source_sessions.status())


@app.get("/api/browse")
def api_browse():
    nid = nodeid_text(request.args.get("nodeid") or "i=85")
    if source_sessions.enabled:
        nodes=runtime.run(browse_node(nid),timeout=180)
        offset=max(0,int(request.args.get('offset',0)));count=max(1,min(1000,int(request.args.get('count',250))))
        end=min(len(nodes),offset+count)
        return jsonify(ok=True,nodes=nodes[offset:end],total=len(nodes),next_offset=end if end<len(nodes) else None)
    if nid.startswith("mqtt:"):
        nodes=runtime.run(mqtt_catalog_client().browse(nid))
        return jsonify(ok=True,nodes=nodes,next_offset=None)
    if isinstance(require_connection(), ADSClient):
        page=runtime.run(ads_catalog_operation('browse_page',nid,int(request.args.get('offset',0)),int(request.args.get('count',250))),timeout=25)
        return jsonify(ok=True,**page)
    if isinstance(runtime.client,(S7SymbolicClient,MQTTClient)):
        nodes=runtime.run(browse_node(nid),timeout=180)
        offset=max(0,int(request.args.get('offset',0)));count=max(1,min(1000,int(request.args.get('count',250))))
        end=min(len(nodes),offset+count)
        return jsonify(ok=True,nodes=nodes[offset:end],total=len(nodes),next_offset=end if end<len(nodes) else None)
    return jsonify(ok=True, nodes=runtime.run(browse_node(nid), timeout=180 if isinstance(runtime.client,S7SymbolicClient) else 25))


@app.get("/api/node")
def api_node():
    nid = nodeid_text(request.args.get("nodeid"))
    return jsonify(ok=True, node=runtime.run(inspect_node(nid),timeout=180 if isinstance(runtime.client,S7SymbolicClient) else 20))


@app.get('/api/mqtt/catalog')
def api_mqtt_catalog():
    # Observed metadata only: never changes selection, subscriptions or samples.
    c=mqtt_catalog_client()
    with c.guard:
        revision=c.catalog_session+':'+str(c.catalog_revision)
        if request.args.get('revision')==revision: return jsonify(ok=True,revision=revision,unchanged=True)
        nodes=[dict(n) for n in c.catalog.values()]
    return jsonify(ok=True,revision=revision,nodes=nodes,roots=runtime.run(c.browse('mqtt:/')))


@app.post("/api/scan")
def api_scan_start():
    cfg = validated_config(request.get_json() or {})
    source = require_connection(cfg['source'] if source_sessions.enabled else None)
    root = cfg['mqtt_root'] if isinstance(source,MQTTClient) else s7_root(cfg) if isinstance(source,S7_TYPES) else cfg['ads_root'] if isinstance(source,ADSClient) else cfg['root_nodeid']
    jobid = runtime.run(start_scan(root, cfg['scan_limit']))
    return jsonify(ok=True, id=jobid)


@app.get("/api/scan")
def api_scan_status():
    offset = max(0, int(request.args.get("offset", 0)))
    count = min(1000, max(1, int(request.args.get("count", 500))))
    with runtime.lock:
        job = runtime.scan
        expected = request.args.get("id")
        if expected and expected != job["id"]:
            return jsonify(error="La scansione è stata sostituita da una nuova richiesta"), 409
        out = {k: v for k, v in job.items() if k != "nodes"}
        out.update(nodes=job["nodes"][offset:offset + count], total=len(job["nodes"]), next_offset=min(offset + count, len(job["nodes"])))
    return jsonify(ok=True, **out)


@app.post("/api/scan/cancel")
def api_scan_cancel():
    runtime.run(stop_scan())
    return jsonify(ok=True)


@app.post("/api/select")
def api_select():
    data = request.get_json()
    nodes = validated_nodes(data.get("nodeids", []))
    runtime.run(apply_selection(nodes))
    return jsonify(ok=True, selected=runtime.selected)


@app.post("/api/samples/clear")
def api_clear_samples():
    live_archive.new_session()
    with runtime.lock:
        runtime.buffer_epoch += 1
        runtime.samples = {nid: live_buffer(nid) for nid in runtime.selected}
        reset_calculations()
    return jsonify(ok=True)


@app.get("/api/values")
def api_values():
    refresh_sources()
    sessions=source_sessions.status()
    current_client=runtime.client
    view=request.args.get('view','all')
    if view not in ('all','window'):raise ValueError('Vista Live non valida')
    with runtime.lock:
        samples = {k: v.overview.points() if view=='all' and isinstance(v,LiveBuffer) else list(v) for k,v in runtime.samples.items()}
        extent=[p[0] for values in samples.values() if values for p in (values[0],values[-1])]
        display_range={'xmin':min(extent),'xmax':max(extent)} if extent else None
        vals = [dict(v) for v in runtime.latest_values] + list(runtime.calculated_latest.values())
        diag = {"requested_ms": runtime.sample_ms, "read_cycle_ms": round(runtime.last_cycle_ms, 3),
                "actual_period_ms": round(runtime.actual_period_ms, 3), "read_count": runtime.read_count,
                "mean_period_ms": round(sum(runtime.periods) / len(runtime.periods), 3) if runtime.periods else None,
                "last_sample_at": runtime.last_sample_at, "window_seconds": runtime.window_seconds,
                "buffer_mode": runtime.buffer_mode, "buffer_limit_per_signal": sample_limit(),
                "buffer_samples": {k: len(v) for k,v in runtime.samples.items()},
                "acquisition_mode": runtime.acquisition_mode, "notification_count": runtime.notification_count,
                "live_view":view,"display_range":display_range,
                "display_summary":view=='all',
                "analysis_history_error":live_archive.error,
                "session_samples":{k:v.overview.count for k,v in runtime.samples.items() if isinstance(v,LiveBuffer)},
                "last_notification_at": runtime.last_notification_at, "read_fallback_count": runtime.read_fallback_count,
                "subscription_bad": dict(runtime.subscription_bad),
                "individual_nodes": sorted(runtime.individual_nodes),
                "ads_notifications": getattr(runtime,"ads_notification_diag",{}),
                "s7_udp": current_client.diagnostics() if isinstance(current_client,S7UDPClient) else {},
                "mqtt": {**current_client.diagnostics(),'late_samples':getattr(runtime,'mqtt_late_samples',0),'reconnections':getattr(runtime,'mqtt_reconnections',0)} if isinstance(current_client,MQTTClient) else {}}
    if sessions:
        diag['mqtt']={**sessions.get('mqtt',{}).get('diagnostics',{}),**{k:sessions.get('mqtt',{}).get(k,0) for k in ('late_samples','reconnections')}}
        diag['s7_udp']=sessions.get('s7',{}).get('diagnostics',{}) if (runtime.active_config or {}).get('s7_mode')=='udp' else {}
        diag['ads_notifications']=sessions.get('ads',{}).get('diagnostics',{}) if (runtime.active_config or {}).get('ads_mode')=='notify' else {}
    return jsonify(ok=True, connected=runtime.connected, state=runtime.state, error=runtime.last_error,
                   values=vals, samples={k: [p[:3] for p in compress_points(v)] for k, v in samples.items()}, diagnostics=diag,
                   source=(runtime.active_config or load_config()).get("source", "opcua"),
                   ads=sessions.get('ads',{}).get('info',{}) if sessions else dict(current_client.info) if isinstance(current_client, ADSClient) else {},
                   s7=sessions.get('s7',{}).get('info',{}) if sessions else dict(current_client.info) if isinstance(current_client, S7_TYPES) else {},
                   mqtt=sessions.get('mqtt',{}).get('info',{}) if sessions else dict(current_client.info) if isinstance(current_client,MQTTClient) else {},
                   sources=sessions, parallel_mqtt=sessions.get('mqtt',parallel_mqtt.status()), recording=recording.status(), calculations=calculations.status())


async def start_recording(data):
    require_connection()
    if recording.acquiring: raise ValueError("Una registrazione è già in corso o in fase di chiusura")
    supplied = data.get('selected_nodes', [])
    if not isinstance(supplied, list) or len(supplied)>64: raise ValueError('Record: massimo 64 segnali acquisiti e calcolati complessivi')
    derived = [n for n in supplied if isinstance(n, dict) and str(n.get('nodeid', '')).startswith('calc:')]
    nodes = validated_nodes([n for n in supplied if n not in derived])
    if not nodes or set(n["nodeid"] for n in nodes) != set(runtime.selected):
        raise ValueError("La selezione è cambiata: chiudere e riaprire la finestra Record")
    definitions = {f['nodeid']: f for f in formula_store.snapshot()['formulas']}
    for node in derived:
        f = definitions.get(node['nodeid'])
        if f is None or node['nodeid'] not in calculations.ids: raise ValueError('Serie calcolata non attiva in Live')
        nodes.append({'nodeid': f['nodeid'], 'display_name': f['name'], 'unit': f['unit'] or f['unit_hint'], 'calculated': True})
    await asyncio.to_thread(calculations.flush)
    await asyncio.to_thread(acquisition.flush)
    await asyncio.to_thread(acquisition.reset_failed)
    options = dict(data.get('options') or {});options['conversion_isolated']=True
    for node in nodes:acquisition.connection(protocol_of(node['nodeid']),protocol_of(node['nodeid']))
    options['primary_connections']=acquisition.catalog()
    if derived: options['calculation_formulas'] = formula_store.snapshot()['formulas']
    if isinstance(runtime.client,S7_TYPES): options['acquisition_info'] = dict(runtime.client.info)
    if isinstance(runtime.client,MQTTClient):
        if runtime.client.error:raise ValueError(runtime.client.error)
        options['acquisition_info']={**runtime.client.info,'time_basis':runtime.active_config['mqtt_time_basis']}
    if parallel_mqtt.enabled: options['acquisition_info']={'protocols':['opcua','mqtt'],'time_basis':'PC UTC arrival; asynchronous original samples'}
    if source_sessions.enabled:
        for session in source_sessions.sessions.values(): session.check_loss()
        periods={p:s.period for p,s in source_sessions.sessions.items()}
        options['acquisition_info']={'protocols':list(source_sessions.sessions),'time_basis':'PC UTC arrival',
            'source_intervals':periods,'original_clocks':'source_timestamp, server_timestamp, ads_filetime, acquisition_timestamp_ms',
            'signal_protocols':{n['nodeid']:'calculated' if n['nodeid'].startswith('calc:') else protocol_of(n['nodeid']) for n in nodes}}
        if data.get('format')=='pda3':
            options['pda3_resampling']={'method':'previous','grid_ms':10,
                'max_age_ms':{n['nodeid']:max(10,3*periods.get(protocol_of(n['nodeid']),runtime.sample_ms)) for n in nodes},
                'invalid':'NaN; original quality retained in companion Parquet'}
    period=runtime.sample_ms
    if source_sessions.enabled: period=min(s.period for s in source_sessions.sessions.values())
    if source_sessions.enabled and data.get('format')=='pda3': period=10
    if not source_sessions.enabled and isinstance(runtime.client,MQTTClient):period=runtime.client.sample_period_ms(period)
    if not source_sessions.enabled and isinstance(runtime.client,S7UDPClient):
        period=runtime.client.sample_period_ms(period)
        options['acquisition_info']['configured_client_interval_ms']=runtime.sample_ms
        options['acquisition_info']['recording_period_ms']=period
        options['acquisition_info']['period_origin']='Median PLC timestamp interval, or local arrival interval when absent; timestamps retain full precision'
    with runtime.lock: runtime.recording_accept = False; runtime.recording_begin = None
    if isinstance(runtime.client,MQTTClient):
        runtime.mqtt_recording_begin_ms=time.time()*1000;runtime.mqtt_recording_end_ms=None;runtime.mqtt_recording_max_ts=None
    try:
        state = await asyncio.to_thread(recording.start, data.get("format"), nodes,
            endpoint=('mixed://'+'/'.join(source_sessions.sessions)) if source_sessions.enabled else mqtt_endpoint(runtime.active_config) if runtime.active_config.get('source')=='mqtt' else s7_endpoint(runtime.active_config) if runtime.active_config.get('source')=='s7' else ("ads://%s/%s:%s" % (runtime.active_config["ads_host"], runtime.active_config["ads_target_netid"], runtime.active_config["ads_port"])) if runtime.active_config.get("source")=="ads" else runtime.active_config.get("endpoint", ""), sample_ms=period, options=options)
    finally:
        with runtime.lock:
            runtime.record_receive_begin_mono_ns=time.monotonic_ns();runtime.record_receive_end_mono_ns=None
            runtime.record_receive_begin_ns=time.time_ns();runtime.record_receive_end_ns=None
            runtime.record_raw_last_t=None
            if derived:runtime.recording_begin=acquisition.last_timestamp
            runtime.recording_accept = True
            if source_sessions.enabled:
                runtime.multi_recording_begin=time.time()*1000
                runtime.multi_recording_end=None
    return state


@app.get('/api/acquisition/health')
def api_acquisition_health():
    return jsonify(ok=True,acquisition=acquisition.status(),recording=recording.status(),
        calculations=calculations.status(),recovery=primary_recovery.status(),
        protocols=source_sessions.status() if source_sessions.enabled else {(runtime.active_config or {}).get('source','opcua'):dict(state=runtime.state,error=runtime.last_error,reconnections=getattr(runtime,'source_reconnections',{}),ads_notifications=getattr(runtime,'ads_notification_diag',{}),source_diagnostics=runtime.client.diagnostics() if runtime.client and hasattr(runtime.client,'diagnostics') else {})},
        events=dict(persistence_error=acquisition_events.error,overflow=acquisition_events.overflow))


@app.get('/api/acquisition/events')
def api_acquisition_events():
    return jsonify(ok=True,events=acquisition_events.history(request.args.get('limit',100)))


@app.get('/api/acquisition/captures')
def api_acquisition_captures():
    return jsonify(ok=True,captures=primary_recovery.catalog())


@app.post('/api/acquisition/recover')
def api_acquisition_recover():
    return jsonify(ok=True,started=primary_recovery.start(),recovery=primary_recovery.status())


@app.get("/api/record/status")
def api_record_status():
    return jsonify(ok=True, recording=recording.status())


@app.post("/api/record/start")
def api_record_start():
    data = request.get_json()
    prepare_format(data.get("format"), data.get("options"))
    async def command():
        async with runtime.command_lock: return await start_recording(data)
    return jsonify(ok=True, recording=runtime.run(command(), timeout=25))


@app.post('/api/record/iba/check')
def api_record_iba_check():
    from iba_dat import bridge
    bridge.report_path=DATA_DIR/'iba_last_check.json'
    result=bridge.check(force=True)
    return jsonify(ok=True,check=result)


@app.get('/api/record/iba/status')
def api_record_iba_status():
    from iba_dat import bridge
    return jsonify(ok=True,check=bridge.status())


@app.get('/api/pda3/status')
def api_pda3_status():
    from pda3.template_loader import status
    from pda3.recording import RECOVERIES
    return jsonify(ok=True,template=status(),recovery=RECOVERIES.get(str(RECORDINGS_DIR.resolve()),{}))


@app.post("/api/record/stop")
def api_record_stop():
    ident=(request.get_json(silent=True) or {}).get('recording_id')
    async def command():
        async with runtime.command_lock:
            if ident and recording.status().get('recording_id')!=ident:raise ValueError('Registrazione cambiata: aggiornare lo stato prima di premere Stop')
            return await asyncio.to_thread(finish_recording)
    return jsonify(ok=True, recording=runtime.run(command(),timeout=25))


def _recording_source(source):
    source = str(source or "repository").lower()
    if source == "repository":
        return recording_library
    if source == "local":
        return imported_recording_library
    raise ValueError("Origine registrazione non valida")


def _clear_imported_recordings():
    # Imported originals remain available for a saved workspace across restarts.
    IMPORTED_RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)


@app.get("/api/recordings")
def api_recordings_list():
    return jsonify(ok=True, directory=str(RECORDINGS_DIR), files=recording_library.list()+imported_recording_library.list(source='local'))


@app.post("/api/recordings/import")
def api_recordings_import():
    from pda3.import_service import import_files
    return jsonify(import_files(request,IMPORTED_RECORDINGS_DIR,imported_recording_library))


@app.post("/api/recordings/data")
def api_recordings_data():
    data = request.get_json(silent=True) or {}
    library, name, descriptor = historical_reader(data)
    result = library.load(
        name,
        signal_ids=data.get("signal_ids"),
        start_ms=data.get("start_ms"),
        end_ms=data.get("end_ms"),
        max_points=data.get("max_points", 25000),
    )
    result['name'] = data.get('name')
    result['format'] = descriptor['format']
    result['available_calculations'] = available_recorded(data, descriptor)
    definitions = {f['nodeid']: f for f in formula_store.snapshot()['formulas']}
    metadata = library._metadata(library._path(name))
    signal_meta = {s['nodeid']: s for s in (metadata.get('signals') or descriptor.get('signals') or [])}
    for signal in result['signals']:
        info = signal_meta.get(signal['nodeid'], {})
        signal['unit'] = info.get('unit', '')
        signal['calculated'] = signal['nodeid'].startswith('calc:')
        if signal['calculated'] and not signal['unit']:
            signal['unit'] = definitions.get(signal['nodeid'], {}).get('unit', '')
    return jsonify(ok=True, recording=result)


@app.post('/api/recordings/compare')
def api_recordings_compare():
    return jsonify(ok=True, job=comparison_service.start(request.get_json(silent=True) or {}, _recording_source))


@app.get('/api/recordings/compare/<ident>')
def api_recordings_compare_status(ident):
    return jsonify(ok=True, job=comparison_service.status(ident))


@app.post("/api/recordings/markers")
def api_recordings_markers():
    data = request.get_json(silent=True) or {}
    library, name, _ = historical_reader(data, full=True)
    result = library.markers(
        name, data.get("signal_ids") or [], data.get("x1"), data.get("x2")
    )
    return jsonify(ok=True, markers=result)


WORKSPACE_FILE = DATA_DIR / "workspace.json"
EXPORT_DIR = RECORDINGS_DIR / "Exports"


def available_recorded(data, descriptor):
    physical = [s['nodeid'] for s in descriptor.get('signals', [])]
    if not physical:
        library = _recording_source(data.get('source'))
        physical = [s['nodeid'] for s in library.load(data.get('name'), max_points=1000)['signals']]
    return calculations.available(formula_store.snapshot()['formulas'], physical)


def historical_reader(data, full=False):
    library = _recording_source(data.get('source'))
    name = data.get('name', '')
    descriptor = library.describe(name)
    ids = data.get('signal_ids') or []
    physical = {s['nodeid'] for s in descriptor['signals']}
    if any(str(n).startswith('calc:') and n not in physical for n in ids):
        start, end = (None, None) if full else (data.get('start_ms', data.get('x1')), data.get('end_ms', data.get('x2')))
        if start is not None or end is not None:
            start, end = calculation_interval(start, end)
        return calculations.history(library, name, ids, formula_store.snapshot()['formulas'], start, end)
    return library, name, descriptor


def calculation_interval(start, end):
    for value in (start, end):
        if value is not None and number(value) is None: raise ValueError('Intervallo di calcolo non valido')
    start = number(start); end = number(end)
    if start is not None and end is not None and end <= start: raise ValueError('Fine intervallo successiva all’inizio')
    return start, end


@app.get('/api/calculations')
def api_calculations():
    return jsonify(ok=True, **formula_store.snapshot(), diagnostics=calculations.status())


def calculations_edit_guard():
    if recording.acquiring: raise ValueError('Premere Stop prima di modificare le formule: la registrazione conserva la definizione iniziale')


@app.post('/api/calculations/validate')
def api_calculations_validate():
    data = request.get_json() or {}
    f, _ = formula_store.candidate(data.get('formula', data))
    return jsonify(ok=True, formula=f, unit_hint=f['unit_hint'], required_sources=f['required_sources'])


@app.post('/api/calculations/save')
def api_calculations_save():
    # Serialize editing with Record start and acquisition configuration changes.
    async def command():
        async with runtime.command_lock:
            calculations_edit_guard()
            f = formula_store.save(request_data)
            with runtime.lock: reset_calculations()
            logging.info('Formula salvata id=%s revision=%s', f['id'], f['revision'])
            return f
    request_data = request.get_json() or {}
    f = runtime.run(command())
    return jsonify(ok=True, formula=f, **formula_store.snapshot())


@app.post('/api/calculations/delete')
def api_calculations_delete():
    ident = (request.get_json() or {}).get('id')
    async def command():
        async with runtime.command_lock:
            calculations_edit_guard(); formula_store.delete(ident)
            with runtime.lock: reset_calculations()
    runtime.run(command())
    logging.info('Formula eliminata id=%s', ident)
    return jsonify(ok=True, **formula_store.snapshot())


@app.post('/api/calculations/duplicate')
def api_calculations_duplicate():
    ident = (request.get_json() or {}).get('id')
    async def command():
        async with runtime.command_lock:
            calculations_edit_guard(); f = formula_store.duplicate(ident)
            with runtime.lock: reset_calculations()
            return f
    f = runtime.run(command())
    return jsonify(ok=True, formula=f, **formula_store.snapshot())


@app.post('/api/calculations/preview')
def api_calculations_preview():
    data = request.get_json() or {}
    f, formulas = formula_store.candidate(data.get('formula', {}))
    mode = data.get('mode', 'live')
    if mode not in ('live', 'recorded'): raise ValueError('Modalità anteprima non valida')
    if not f['modes'][mode]: raise ValueError('Abilitare la modalità '+mode+' nella formula')
    # Preview tests a disabled draft without enabling it in the live registry.
    preview_formulas = [dict(g, active=True) if g['nodeid']==f['nodeid'] else g for g in formulas]
    start, end = calculation_interval(data.get('start_ms'), data.get('end_ms'))
    if mode == 'live':
        plan = Plan(preview_formulas, 'live', [f['nodeid']])
        with runtime.lock:
            captured = {n: [p[:3] for p in itertools.islice(reversed(runtime.samples.get(n, [])),5000)][::-1] for n in (plan.raw_ids or runtime.selected[:1])}
        rows = calculations.preview(preview_formulas, f['nodeid'], captured, start, end)
        points = [[r['timestamp_ms'], r['value_numeric'], r['quality']] for r in rows]
        last = rows[-1] if rows else {}
        status = last.get('status', 'Nessun campione sorgente: selezionare le variabili e avviare l’acquisizione')
        quality = last.get('quality', 'Bad')
    else:
        library, name, _ = calculations.history(_recording_source(data.get('source')), data.get('name'),
            [f['nodeid']], preview_formulas, start, end)
        result = library.load(name, [f['nodeid']], max_points=1000)
        points = result['series'].get(f['nodeid'], [])[-200:]
        last = {}
        for last in library._iter_rows(library._path(name)): pass
        status = last.get('status', 'Nessun campione'); quality = last.get('quality', 'Bad')
    return jsonify(ok=True, formula=f, series=points, value=points[-1][1] if points else None,
        quality=quality, status=status, unit=f['unit'] or f['unit_hint'], sample_count=len(points))


@app.post('/api/calculations/materialize')
def api_calculations_materialize():
    data = request.get_json() or {}
    fmt = data.get('format', 'parquet')
    if fmt not in ('csv','parquet'):raise ValueError('Dataset calcolati: scegliere CSV o Parquet; FDA e disponibile da Record')
    prepare_format(fmt)
    start, end = calculation_interval(data.get('start_ms'), data.get('end_ms'))
    ids = data.get('signal_ids')
    if not isinstance(ids, list) or not ids or not any(str(n).startswith('calc:') for n in ids):
        raise ValueError('Selezionare almeno una variabile calcolata')
    RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
    destination = RECORDINGS_DIR / ('Calculated_'+datetime.now().strftime('%Y%m%d_%H%M%S')+'_'+secrets.token_hex(4)+'.'+fmt)
    library, name, _ = calculations.history(_recording_source(data.get('source')), data.get('name'), ids,
        formula_store.snapshot()['formulas'], start, end, destination, fmt)
    logging.info('Dataset calcolato materializzato file=%s', destination.name)
    return jsonify(ok=True, recording=library.describe(name), path=str(destination))


def load_workspace():
    try:
        value=json.loads(WORKSPACE_FILE.read_text(encoding="utf-8"))
        return value if isinstance(value,dict) else {}
    except (OSError,ValueError):return {}


@app.post("/api/workspace")
def api_workspace_save():
    if request.content_length and request.content_length > 32*1024*1024:
        raise ValueError("Workspace troppo grande (massimo 32 MB)")
    data=request.get_json()
    if not isinstance(data,dict) or data.get("schema")!=1:raise ValueError("Workspace non valido")
    data["config"]=validated_config(data.get("config",{}))
    connection_guard(data["config"])
    data['config']['s7_symbolic_password']=''
    data['config']['mqtt_password']=''
    data["selected_nodes"]=validated_nodes(data.get("selected_nodes",[]))
    for scope in ("live","history"):
        charts=data.get(scope,{}).get("charts",[])
        if not isinstance(charts,list) or len(charts)>8:raise ValueError("Massimo 8 grafici per pagina")
    data["saved_at"]=datetime.now(timezone.utc).isoformat()
    data['calculations'] = formula_store.snapshot()
    with file_lock:
        data['config']['ui_language']=load_config()['ui_language']
        atomic_json(WORKSPACE_FILE,data)
    return jsonify(ok=True,saved_at=data["saved_at"])


def live_reference(ids,start=-math.inf,end=math.inf):
    if live_archive.active and all(getattr(runtime.samples.get(n),'archive_generation',None)==live_archive.generation or n not in runtime.samples and live_archive.counts[n]>0 for n in ids):
        return live_archive.reference(ids,start,end)
    return None


@app.post('/api/analysis/live-snapshot')
def api_live_snapshot():
    from live_analysis import snapshots
    data=request.get_json() or {};ids=data.get('signal_ids')
    if not isinstance(ids,list) or not 1<=len(ids)<=64 or any(not isinstance(n,str) for n in ids):raise ValueError('Segnali snapshot non validi')
    start,end=interval(data)
    with runtime.lock:
        reference=live_reference(ids,start,end)
        if reference:reference=live_archive.reference(tuple(live_archive.counts.keys()|runtime.samples.keys()),start,end)
        captured=None if reference else {nid:[p for p in runtime.samples.get(nid,[]) if start<=p[0]<=end] for nid in dict.fromkeys(ids)}
    if reference and reference.count:
        key,count=snapshots.create_reference(reference)
    else:key,count=snapshots.create(captured or {})
    return jsonify(ok=True,snapshot_id=key,samples=count)


@app.post('/api/analysis/live-snapshot/release')
def api_live_snapshot_release():
    from live_analysis import snapshots
    snapshots.delete((request.get_json() or {}).get('snapshot_id'))
    return jsonify(ok=True)


def analysis_rows(data,ordered=False):
    ids=data.get('signal_ids') or []
    if not isinstance(ids,list) or not 1<=len(ids)<=64 or any(not isinstance(n,str) for n in ids):raise ValueError('Selezionare da 1 a 64 segnali')
    if data.get('source')=='live':
        if data.get('snapshot_id'):
            from live_analysis import snapshots
            frozen=snapshots.get(data['snapshot_id'])
            if isinstance(frozen,ArchiveReference):reference=frozen;captured=None
            else:reference=None;captured={nid:frozen.get(nid,[]) for nid in ids}
        else:
            with runtime.lock:
                reference=live_reference(ids)
                captured=None if reference else {nid:list(runtime.samples.get(nid,[])) for nid in ids}
        names={f['nodeid']:f['name'] for f in formula_store.snapshot()['formulas']}
        if reference:
            start,end=interval(data)
            return point_rows(reference.archive.points(reference,ids,start,end),runtime.sample_ms,names)
        if data.get('x1') is not None and data.get('x2') is not None and not data.get('live_seconds'):
            requested_start=min(float(data['x1']),float(data['x2']))
            for nid,points in captured.items():
                if points:
                    tolerance=max(1,float(points[0][9] or runtime.sample_ms))*1.5 if len(points[0])>9 else max(1,runtime.sample_ms)*1.5
                    if requested_start<points[0][0]-tolerance:
                        raise ValueError('Campioni originali di questo intervallo non disponibili: avviare una nuova acquisizione o aprire la registrazione')
        points=((nid,p) for nid,values in captured.items() for p in values)
        if ordered:points=sorted(points,key=lambda pair:pair[1][0])
        return point_rows(points,runtime.sample_ms,names)
    library,name,_=historical_reader(data)
    start,end=interval(data)
    return library._iter_rows(library._path(name),ids,start,end)


@app.post('/api/analysis/markers')
def api_live_markers():
    from live_analysis import snapshots
    data=request.get_json() or {};ids=data.get('signal_ids')
    if not isinstance(ids,list) or not 1<=len(ids)<=64 or any(not isinstance(n,str) for n in ids):raise ValueError('Selezionare da 1 a 64 segnali')
    interval(data);x1=float(data['x1']);x2=float(data['x2'])
    if data.get('snapshot_id'):
        frozen=snapshots.get(data['snapshot_id'])
        reference=frozen if isinstance(frozen,ArchiveReference) else None
        captured=None if reference else frozen
    else:
        with runtime.lock:
            reference=live_reference(ids)
            captured=None if reference else {nid:list(runtime.samples.get(nid,[])) for nid in ids}
    if reference:results=reference.archive.markers(reference,ids,x1,x2)
    else:
        results=[]
        for nid in ids:
            row={'nodeid':nid,'x1':None,'x2':None};points=captured.get(nid,[])
            for key,t in [('x1',x1),('x2',x2)]:
                if not points:continue
                p=min(points,key=lambda p:abs(p[0]-t));period=p[9] if len(p)>9 and p[9] else runtime.sample_ms
                if abs(p[0]-t)<=3*max(1,period) and p[2]=='Good' and p[1] is not None:row[key]={'timestamp_ms':p[0],'value':p[1]}
            results.append(row)
    return jsonify(ok=True,x1=x1,x2=x2,results=results,data_origin='originals')


@app.post('/api/analysis/quick')
def api_quick_analysis():
    from quick_analysis import summarize, formula_rows
    data=request.get_json() or {}
    start,end=interval(data)
    if data.get('formula'):
        from calculation_store import normalize
        definition={**data['formula'],'name':data['formula'].get('name') or 'Calcolo intervallo','active':True,'modes':{'live':True,'recorded':True}}
        f=normalize(definition)
        available={g['nodeid']:g for g in formula_store.snapshot()['formulas']};available[f['nodeid']]=f
        # Already acquired calculated signals preserve their historical values;
        # other saved dependencies are evaluated from their original raw inputs.
        with runtime.lock:stored={n for n in runtime.samples.keys()|live_archive.counts.keys() if n.startswith('calc:')}
        needed={}
        def include(nid):
            if nid in needed:return
            if nid not in available:raise ValueError('Una variabile calcolata referenziata non esiste più')
            g=available[nid];needed[nid]=g
            for alias in Expression(g['expression'],g['bindings']).used_bindings:
                binding=g['bindings'][alias];source=binding['nodeid']
                if source.startswith('calc:') and source not in stored and binding.get('kind')!='stored':include(source)
        include(f['nodeid'])
        plan=Plan(list(needed.values()),'live',[f['nodeid']],external_ids=stored)
        ids=list(plan.raw_ids)
        if not ids:raise ValueError('Associare almeno una variabile alla formula')
        lookback=sum(plan.formulas[n]['max_age_ms'] for n in plan.order)
        body={**data,'signal_ids':ids,'x1':start-lookback,'x2':end}
        if data.get('source')!='live':raise ValueError('Calcolo intervallo disponibile sui segnali Live')
        rows=formula_rows(analysis_rows(body,ordered=True),plan,f['nodeid'],start,end,runtime.sample_ms)
        result=summarize(rows,[f['nodeid']],start,end,data['x1'],data['x2'])
        return jsonify(ok=True,x1=data['x1'],x2=data['x2'],formula=f,results=result,data_origin='originals')
    return jsonify(ok=True,x1=data['x1'],x2=data['x2'],results=summarize(analysis_rows(data),data['signal_ids'],start,end,data['x1'],data['x2']))


@app.post("/api/analysis/fft")
def api_fft():
    from fft_service import calculate
    data=request.get_json() or {};ids=data.get('signal_ids') or []
    if not isinstance(ids,list) or len(ids)!=1:raise ValueError('Selezionare un solo segnale per FFT')
    if data.get('live_seconds') is not None:
        raise ValueError('FFT disponibile solo in Pausa con marker X1 e X2')
    if data.get('source')=='live' and not data.get('snapshot_id'): raise ValueError('FFT live: mettere il grafico in Pausa e posizionare X1 e X2')
    start,end=interval(data);options=data.get('options') or {}
    if not isinstance(options,dict):raise ValueError('Opzioni FFT non valide')
    result=calculate(analysis_rows(data),ids[0],start,end,data.get('window','hann'),bool(data.get('detrend',True)),options)
    result['data_origin']='vista live congelata: campioni originali' if data.get('snapshot_id') else 'buffer live corrente' if data.get('source')=='live' else 'file registrazione originale'
    logging.info('FFT: original=%d blocks=%d excluded=%d',result['samples_original'],result['blocks_used'],result['blocks_excluded'])
    return jsonify(ok=True,spectrum=result)


@app.post("/api/analysis/export")
def api_export():
    data=request.get_json();start,end=interval(data);fmt=data.get("format")
    if fmt=='pda3':
        if data.get('source')=='live':raise ValueError('PDA3 Live: usare Record; per export manuale scegliere una registrazione storica')
        from pda3.historical_export import export_interval
        library,name,_=historical_reader(data)
        return jsonify(ok=True,**export_interval(library,name,data.get('signal_ids',[]),start,end,RECORDINGS_DIR,EXPORT_DIR,data))
    if fmt not in ("csv","parquet"):raise ValueError("Scegliere CSV, Parquet o DAT PDA3")
    EXPORT_DIR.mkdir(parents=True,exist_ok=True)
    name="Export_"+datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")+"_"+secrets.token_hex(5)+"."+fmt
    final=EXPORT_DIR/name;partial=EXPORT_DIR/(name+".part")
    try:
        count=export_rows(selected_rows(analysis_rows(data),data.get("signal_ids",[]),start,end),partial,fmt)
        os.replace(partial,final)
        atomic_json(final.with_suffix(".metadata.json"),dict(source=data.get("source"),original=data.get("name"),x1=start,x2=end,rows_written=count,signal_ids=data.get("signal_ids")))
    except Exception:
        partial.unlink(missing_ok=True);raise
    logging.info("Interval exported: %s rows=%d",name,count)
    return jsonify(ok=True,name=name,path=str(final),rows=count,bytes=final.stat().st_size,url="/api/exports/"+name)


@app.get("/api/exports/<name>")
def api_export_download(name):
    path=(EXPORT_DIR/name).resolve()
    if path.parent!=EXPORT_DIR.resolve() or path.suffix not in (".csv",".parquet",".zip") or not path.is_file():
        raise ValueError("Export non trovato")
    return send_file(path,as_attachment=True,download_name=path.name)


_frontend_log_times=deque(maxlen=100)
@app.post("/api/log/frontend")
def api_frontend_log():
    now=time.monotonic()
    if len(_frontend_log_times)==100 and now-_frontend_log_times[0]<60:return jsonify(ok=True,throttled=True)
    _frontend_log_times.append(now)
    data=request.get_json(silent=True) or {}
    # Never log credentials, request bodies or arbitrary stack contents.
    text=str(data.get("message", "Frontend error"))[:1200].replace("\n"," ").replace("\r"," ")
    import re
    text=re.sub(r"(?i)(password|token|authorization|secret)([\s:=]+)\S+",r"\1=[redacted]",text)
    logging.error("Frontend: %s",text)
    return jsonify(ok=True)


@app.get("/api/logs")
def api_logs():
    path=DATA_DIR/"opcua-client.log"
    with path.open("rb") as f:
        f.seek(max(0,path.stat().st_size-65536));text=f.read().decode("utf-8",errors="replace")
    return jsonify(ok=True,path=str(path),tail=text)


@app.post("/api/shutdown")
def api_shutdown():
    if http_server:
        threading.Thread(target=http_server.shutdown, daemon=True).start()
    return jsonify(ok=True)


def configure_logging():
    root=logging.getLogger()
    if not any(getattr(h,"opcua_rotating",False) for h in root.handlers):
        handler=RotatingFileHandler(DATA_DIR/"opcua-client.log",maxBytes=5*1024*1024,backupCount=5,encoding="utf-8")
        handler.opcua_rotating=True
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        root.addHandler(handler);root.setLevel(logging.INFO)
        for name in ("asyncua","werkzeug"):logging.getLogger(name).setLevel(logging.WARNING)


def initialize():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    configure_logging()
    logging.info("Application start V%s", APP_VERSION)
    _clear_imported_recordings()
    formula_store.load(load_workspace().get('calculations') or load_saved_settings().get('calculations'))
    restore_saved_certificate_files()
    bootstrap_certificate_bundle()
    cfg = validated_config({})
    ensure_client_certificate(cfg["application_uri"])
    save_config(cfg)
    apply_live_config(cfg)
    nodes = validated_nodes(load_saved_settings().get("selected_nodes", []))
    runtime.run(apply_selection(nodes))
    with runtime.lock: reset_calculations(cfg['source'])
    from pda3.recording import recover_pending
    recover_pending(RECORDINGS_DIR)
    primary_recovery.start()


def bootstrap_certificate_bundle():
    """Preserve active user certificates; restore missing files from the supplied bundle."""
    seeds = BASE_DIR / "seed_certificates"
    if not seeds.is_dir() or not any((seeds/name).is_file() for name in ("client.der","client-key.pem","server.der")):
        return
    CERT_DIR.mkdir(parents=True, exist_ok=True)
    bundle = CERT_DIR / "embedded-v1.1.10"
    bundle.mkdir(exist_ok=True)
    for name, destination in (("client.der", CERT_FILE), ("client-key.pem", KEY_FILE), ("server.der", SERVER_CERT_FILE)):
        source = seeds / name
        if not source.is_file():
            raise RuntimeError("Bundle certificati incompleto: " + name)
        if not (bundle / name).exists():
            shutil.copy2(source, bundle / name)
        if not destination.exists():
            shutil.copy2(source, destination)
    trusted = CERT_DIR / "trusted"
    trusted.mkdir(exist_ok=True)
    shutil.copy2(SERVER_CERT_FILE, trusted / "server.der")
    info = certificate_info(CERT_FILE)
    if info.get("application_uri"):
        cfg = load_config()
        cfg["application_uri"] = info["application_uri"]
        save_config(cfg)


def claim_instance():
    """One service per data folder; subsequent launches reopen its browser page."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    handle = (DATA_DIR / "instance.lock").open("a+b")
    handle.seek(0, 2)
    if handle.tell() == 0:
        handle.write(b"0")
        handle.flush()
    handle.seek(0)
    try:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (OSError, BlockingIOError):
        handle.close()
        from urllib.request import ProxyHandler, build_opener
        opener = build_opener(ProxyHandler({}))
        for _ in range(20):
            try:
                port = int(json.loads((DATA_DIR / "instance.json").read_text())["port"])
                if not 1024 <= port <= 65535:
                    raise ValueError("Porta non valida")
                url = f"http://127.0.0.1:{port}"
                with opener.open(url + "/api/status", timeout=1) as response:
                    info = json.load(response)
                if info.get("app") == APP_NAME:
                    running_version = str(info.get("version") or "sconosciuta")
                    if running_version != APP_VERSION:
                        raise RuntimeError(
                            f"È già attiva Space Analyzer V{running_version}. "
                            f"Chiudi la versione precedente con 'Chiudi applicazione' e poi avvia V{APP_VERSION}."
                        )
                    if not os.environ.get("OPCUA_NO_BROWSER"):
                        webbrowser.open(url)
                    print(f"Applicazione già avviata: {url}", flush=True)
                    return None
            except RuntimeError:
                raise
            except Exception:
                time.sleep(.25)
        raise RuntimeError("L'applicazione è già in avvio. Attendere alcuni secondi e riprovare.")
    return handle


def main():
    global http_server
    instance = claim_instance()
    if instance is None:
        return
    initialize()
    configure_logging()
    port = int(os.environ.get("OPCUA_WEB_PORT", "5000"))
    # Bind before opening the browser. A busy port never points to an unrelated app.
    for candidate in range(port, port + 20):
        try:
            http_server = make_server("127.0.0.1", candidate, app, threaded=True)
            port = candidate
            break
        except (OSError, SystemExit):
            continue
    if http_server is None:
        raise RuntimeError("Nessuna porta locale disponibile tra 5000 e 5019")
    url = f"http://127.0.0.1:{port}"
    atomic_json(DATA_DIR / "instance.json", {"port": port, "pid": os.getpid(), "version": APP_VERSION})
    print(f"{APP_NAME} V{APP_VERSION}\nPagina: {url}\nImpostazioni: {DATA_DIR}\nUsa 'Chiudi applicazione' nella pagina per terminare i servizi.", flush=True)
    if not os.environ.get("OPCUA_NO_BROWSER"):
        webbrowser.open(url)
    timer_started = False
    if os.name == "nt":
        import ctypes
        timer_started = ctypes.windll.winmm.timeBeginPeriod(1) == 0
    try:
        http_server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        ssh_service.close()
        comparison_service.close()
        runtime.run(disconnect_command(), timeout=15)
        acquisition.close()
        calculations.close()
        live_archive.close()
        recording.close()
        acquisition_events.close()
        http_server.server_close()
        runtime.loop.call_soon_threadsafe(runtime.loop.stop)
        if timer_started:
            ctypes.windll.winmm.timeEndPeriod(1)
        _clear_imported_recordings()
        instance.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        if os.name == "nt":
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, str(exc), APP_NAME + " - Errore di avvio", 16)
        raise
