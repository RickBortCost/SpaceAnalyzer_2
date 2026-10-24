"""Build a genuine standalone Windows x64 EXE with the Go launcher.

Requires Python 3.10+ and Go 1.24+ on the build PC, not on the destination PC.
The optional --runtime and --pyarrow-wheel reuse an already staged Windows runtime.
"""
import argparse
import ast
import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parent
VERSION = '2.0.0'
PYTHON_URL = 'https://www.python.org/ftp/python/3.13.15/python-3.13.15-embed-amd64.zip'
PYTHON_SHA256 = 'd1f04d990aee1253d8569e8e5104e30fa9f5fa830899f14843448872d936a2cf'


def validate_versions():
    """Refuse an EXE whose launcher or application advertises an older version."""
    module = ast.parse((ROOT/'app.py').read_text(encoding='utf-8'))
    version = next(ast.literal_eval(node.value) for node in module.body
                   if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id=='APP_VERSION' for t in node.targets))
    launcher = (ROOT/'launcher/launcher_windows.go').read_text(encoding='utf-8')
    match = re.search(r'const version = "([^"]+)"', launcher)
    if version != VERSION or not match or match.group(1) != VERSION:
        raise RuntimeError('Versioni applicazione, launcher e build non allineate')


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest() if sys.version_info >= (3,11) else hashlib.sha256(stream.read()).hexdigest()


def stage(args, work):
    runtime = work / 'runtime'
    if runtime.exists():
        shutil.rmtree(runtime)
    if args.runtime:
        shutil.copytree(Path(args.runtime), runtime, ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    else:
        runtime.mkdir(parents=True)
        embed = work / 'python-3.13.15-embed-amd64.zip'
        if not embed.exists():
            urllib.request.urlretrieve(PYTHON_URL, embed)
        if digest(embed) != PYTHON_SHA256:
            raise RuntimeError('Checksum del runtime Python non valido')
        with zipfile.ZipFile(embed) as archive:
            archive.extractall(runtime)
        wheels = work / 'wheels'
        wheels.mkdir(exist_ok=True)
        subprocess.run([sys.executable,'-m','pip','download','--only-binary=:all:',
                        '--platform','win_amd64','--python-version','313','--implementation','cp','--abi','cp313',
                        '-r',str(ROOT/'requirements.txt'),'-d',str(wheels)],check=True)
        for wheel in sorted(wheels.glob('*.whl')):
            with zipfile.ZipFile(wheel) as archive:
                archive.extractall(runtime/'Lib/site-packages')
    if args.pyarrow_wheel:
        with zipfile.ZipFile(args.pyarrow_wheel) as archive:
            archive.extractall(runtime/'Lib/site-packages')
    if args.mqtt_wheel:
        with zipfile.ZipFile(args.mqtt_wheel) as archive:
            archive.extractall(runtime/'Lib/site-packages')
    if not (runtime/'Lib/site-packages/paho/mqtt/client.py').is_file():
        raise RuntimeError('Il runtime deve includere paho-mqtt 2.1.0 per l’acquisizione MQTT')
    if not (runtime/'Lib/site-packages/pyarrow').is_dir():
        raise RuntimeError('Il runtime deve includere PyArrow per la registrazione Parquet')
    for name in ('acquisition_engine.py','acquisition_buffers.py','acquisition_health.py','primary_format.py','primary_recording.py','primary_worker.py','primary_recovery.py','primary_tools.py','durable_recording.py','source_sessions.py','parallel_mqtt.py','quick_analysis.py','app.py','ssh_service.py','ssh_routes.py','s7_client.py','s7_catalog.py','s7_protocols.py','s7_symbolic.py','s7_udp.py','recordings.py','recording_manager.py','recording_preview.py','conversion_worker.py','recording_storage.py','fda_das.py','fda_recording.py','recording_reader.py','analysis_tools.py','ads_client.py','ads_native.py', 'ads_types.py','ads_notifications.py','fft_engine.py','fft_service.py','fft_worker.py','live_analysis.py','live_archive.py','live_overview.py','formula_engine.py','calculation_store.py','calculation_service.py','calculation_history.py','calculation_worker.py'):
        shutil.copy2(ROOT/name, runtime/name)
    shutil.copy2(ROOT/'THIRD_PARTY_NOTICES.txt', runtime/'THIRD_PARTY_NOTICES.txt')
    shutil.copy2(ROOT/'mqtt_source.py',runtime/'mqtt_source.py')
    for name in ('converted_recording.py','iba_dat.py','recording_compare.py','comparison_worker.py','rbf.py','rbf_recording.py'):
        shutil.copy2(ROOT/name,runtime/name)
    if (runtime/'pda3').exists():shutil.rmtree(runtime/'pda3')
    shutil.copytree(ROOT/'pda3',runtime/'pda3',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    for name in ('static','templates','seed_certificates'):
        if (runtime/name).exists():
            shutil.rmtree(runtime/name)
        shutil.copytree(ROOT/name, runtime/name)
        for asset in (ROOT/name).rglob('*'):
            if asset.is_file() and asset.suffix != '.pyc' and '__pycache__' not in asset.parts:
                staged=runtime/asset.relative_to(ROOT)
                if not staged.is_file() or digest(staged)!=digest(asset):
                    raise RuntimeError('Asset del runtime non aggiornato: '+str(asset.relative_to(ROOT)))
    (runtime/'python313._pth').write_text('python313.zip\n.\nLib/site-packages\nimport site\n')
    (runtime/'bundled-runtime.flag').write_text('Standalone OPC UA Web Client '+VERSION+'\n')
    # Do not copy test caches or obsolete alternate frontend assets into the executable.
    (runtime/'static/trend.mjs').unlink(missing_ok=True)
    return runtime


def main():
    validate_versions()
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime',help='Existing Windows CPython 3.13 x64 embedded runtime')
    parser.add_argument('--pyarrow-wheel',help='Windows CPython 3.13 x64 PyArrow wheel')
    parser.add_argument('--mqtt-wheel',help='Pure Python paho-mqtt 2.1.0 wheel when reusing an older runtime')
    parser.add_argument('--go',default='go')
    parser.add_argument('--work',default=str(ROOT/'build'))
    parser.add_argument('--output',default=str(ROOT/'dist'/('Space_Analyzer_V'+VERSION+'.exe')))
    args=parser.parse_args()
    work=Path(args.work).resolve();work.mkdir(parents=True,exist_ok=True)
    runtime=stage(args,work)
    launcher=work/'launcher';launcher.mkdir(exist_ok=True)
    shutil.copy2(ROOT/'launcher/launcher_windows.go',launcher/'launcher_windows.go')
    payload=launcher/'payload.zip'
    with zipfile.ZipFile(payload,'w',zipfile.ZIP_DEFLATED,compresslevel=9) as archive:
        for file in sorted(runtime.rglob('*')):
            if file.is_file() and '__pycache__' not in file.parts and file.suffix!='.pyc':
                name=file.relative_to(runtime).as_posix()
                source=ROOT/name
                application_file=source.is_file() and (name.endswith('.py') or name.startswith(('static/','templates/','seed_certificates/','pda3/')))
                if application_file:
                    # Use the tested source bytes as the authority, even when
                    # reusing a runtime containing an older application.
                    archive.writestr(name,source.read_bytes())
                else:
                    archive.write(file,name)
    with zipfile.ZipFile(payload) as archive:
        for name in archive.namelist():
            source=ROOT/name
            if source.is_file() and (name.endswith('.py') or name.startswith(('static/','templates/','seed_certificates/','pda3/'))):
                if archive.read(name)!=source.read_bytes():
                    raise RuntimeError('Payload diverso dai sorgenti: '+name)
    from windows_resources import build_icon_resource
    build_icon_resource(ROOT/'static/space-analyzer.ico',launcher/'icon_windows_amd64.syso')
    output=Path(args.output).resolve();output.parent.mkdir(parents=True,exist_ok=True)
    env={**os.environ,'GOOS':'windows','GOARCH':'amd64','CGO_ENABLED':'0','GOTOOLCHAIN':'local','GO111MODULE':'off'}
    subprocess.run([args.go,'build','-trimpath','-ldflags=-s -w -H windowsgui','-o',str(output),'.'],cwd=launcher,env=env,check=True)
    if output.read_bytes().find(payload.read_bytes()) < 0:
        raise RuntimeError('EXE privo del payload aggiornato: build non pubblicabile')
    try:
        commit=subprocess.run(['git','rev-parse','HEAD'],cwd=ROOT,capture_output=True,text=True)
        source_commit=commit.stdout.strip() if commit.returncode==0 else None
    except OSError:
        source_commit=None
    manifest={'application_name':'Space Analyzer','icon':'Space Analyzer logo (RT_ICON / RT_GROUP_ICON)','version':VERSION,'platform':'Windows x64','exe':output.name,'size_bytes':output.stat().st_size,
              'source_commit':source_commit,
              'embedded_payload_verified':True,
              'source_hashes':{str(p.relative_to(ROOT)).replace('\\','/'):digest(p) for p in sorted(ROOT.rglob('*'))
                  if p.is_file() and (p.parent==ROOT and p.suffix=='.py' or p.parts[len(ROOT.parts)] in ('static','templates','pda3','launcher'))
                  and '__pycache__' not in p.parts and p.suffix!='.pyc'},
              'ads_sampling_min_ms':1,'ads_fast_presets_ms':[1,2,5],
              'ads_period_persistence':'settings and per-source workspace profile',
              'ads_1ms_notification_units_100ns':10000,
              's7_modes':['classic','symbolic','udp'],'s7_symbolic_provider':'s7commplus 0.1.0 beta',
              's7_symbolic_access':'CPU EXPLORE + type-info + symbolic LIDs and SymbolCRC; structured/array leaves',
              's7_symbolic_worker':'single dedicated executor; no blocking acquisition loop',
              's7_symbolic_security':['SessionKey compatibility','optional TLS','optional CA/client PEM files','session-only CPU password'],
              's7_udp_protocol':'iba documented Direct Generic; six-byte big-endian length/module/sequence header',
              's7_udp_port_default':4170,'s7_udp_proprietary_request_protocol':False,
              's7_udp_worker':'dedicated receive and acquisition threads; bounded queue; per-datagram samples',
              's7_udp_diagnostics':['sequence loss','duplicates','out-of-order','sender/module filtering','invalid packets','queue overflow','watchdog','timestamp monotonicity'],
              's7_udp_timestamps':'local receive; optional payload int64 UTC microseconds preserved as SourceTimestamp',
              's7_mode_workspace_profiles':True,
              'sha256':digest(output),'packaging':'Go GUI launcher + embedded CPython 3.13 Windows runtime',
              'payload_sha256':digest(payload),'python_required_at_runtime':False,'pip_required_at_runtime':False,
              'recordings_directory':'directory of the running EXE / Recordings',
              'external_import_cache':'%LOCALAPPDATA%/OPCUA_Web_Client/recording_imports (persistent workspace originals)',
              'calculated_variables':True,'formula_engine':'controlled AST interpreter; no eval', 'live_calculation_process':'isolated worker with bounded queue', 'historical_calculation':'original samples, dynamic cache or new materialized dataset',
              'formats':['CSV UTF-8 BOM','Apache Parquet / ZSTD 3, 9, 19 / Snappy','FDA DAS4 Int16 + Float32 reading','RBF v3 read/write + original archive','PDA3 DAT reading','iba DAT 2.x parts reading via companion Parquet'],
              'chart_action_toolbar':'per-chart dedicated buttons with measured-width overflow menu',
              'mqtt_autoscan_seconds':1,'simultaneous_protocols':['opcua','ads','mqtt','s7'],'simultaneous_clock':'PC UTC arrival','simultaneous_recording_formats':['csv','parquet','das','rbf','pda3','dat'],'live_markers':'viewport-relative in Play; fixed timestamps in Pause','fft_requires':'paused view and two markers; recorded views are static','quick_calculations':'original samples; scalar measurements without derived signals','ui_languages':['en','it','de','fr','es'],'ui_language_default':'it',
              'ui_language_change':'immediate without reload; persisted in settings/workspace; acquisition unaffected',
              'mqtt':{'library':'Eclipse Paho MQTT Python 2.1.0','versions':['3.1.1','5'],
                  'transport':['TCP','verified TLS 1.2+ with optional client certificate'],
                  'qos':[0,1,2],'read_only':True,'payloads':['UTF-8 scalar','JSON Pointer fields'],
                  'acquisition':'one sample group per received publication; dedicated bounded decoding worker',
                  'timestamps':['local receipt','optional source ISO8601 or Unix s/ms/us/ns'],
                  'retained_default':'catalogue only; optional Uncertain sample',
                  'reconnect':'new clean session and resubscribe; no missed-history recovery',
                  'credentials_persisted':False,'queue_default_messages':2048,'queue_max_bytes':33554432,
                  'on_overflow':'Record error and original partial archive preserved'},
              'rbf':{'profile':'v3 derived from supplied TEST_RBF.rbf','requires_proprietary_libraries':False,'maximum_selected_variables':64,'maximum_frames_per_file':57600,'timestamps':'UInt32 seconds + UInt32 nanoseconds','numeric_values':'Float32; original precision in Parquet','quality':'per-signal _Q; no interpolation or carry-forward','conversion':'after Stop','external_application_validation':False},
              'pda3':{'recording':False,'reader':'known-profile native PDA3 including all eleven index pages; no DLL',
                  'template_sha256':digest(ROOT/'pda3/assets/template_64_10ms.dat'),'profile_sha256':digest(ROOT/'pda3/assets/profile_64_10ms.json')},
              'iba_dat':{'recording':False,'viewer':'DAT parts written by 2.x through their lossless companion Parquet; not a universal external DAT reader'},
              'lossless_binary_recording':True,'default_recording_format':'Parquet / ZSTD 9',
              'binary_profiles':['compact','maximum','fast','compatible'],
              'binary_encoding':['IEEE-754 float64 / byte-stream split','dictionary for repeated labels','data page CRC'],
              'fft_spectrum_view':['curve','spectral lines','expanded view','F1-H5 focus','PNG export'],'recording_viewer':True,
              'ssh_debug':True,'ssh_protocol':'SSH2 / Paramiko 4.0.0','ssh_modes':['command','script stdin','interactive shell PTY'],'ssh_host_key_verification':'explicit first-use trust; changed-key rejection','ssh_output':'bounded streaming stdout/stderr','ssh_credentials_persisted':False,'connection_dialog':True,'editable_ads_manual_profile':True,'effective_ads_client_identity':True,'data_sources':['opcua','ads','s7','mqtt'],'s7_transport':['classic S7comm / ISO-on-TCP','S7CommPlus symbolic / TCP','S7 UDP Direct Generic'],'s7_address_areas':['DB','M','I','Q'],'s7_catalog':'classic absolute catalog, symbolic CPU browse, UDP payload catalog','s7_timestamps':'TCP local read completion; UDP local receive plus optional payload PLC UTC microseconds','ads_transport':['ADS/TCP direct','TwinCAT local TcAdsDll x64, read-only'],
              'ui_palette':'Space Analyzer colour standard','studio_ui':True,'themes':['Standard','Dark','System'],'navigator':True,'draggable_markers':True,
              'chart_presets':['single','vertical','horizontal','quad','stack','grid'],'signal_presentation':True,
              'manual_y_axes':True,'chart_fullscreen':True,'png_export':True,
              'responsive_layout':True,
              'resizable_page_splitters':True,
              'independent_new_charts':True,
              'live_signal_drag_drop':True,
              'charts_per_page':8,
              'marker_sync':'absolute X1/X2 timestamps, X zoom and crosshair shared within each page',
              'live_chart_layout_persistence':True,
              'complete_workspace':True,'synchronized_time_zoom':True,'synchronized_crosshair':True,
              'recording_rotation':['duration','maximum complete file size including footer'],'recording_metadata':True,
              'fft':'original blocks without resampling; four windows; Peak/RMS/Power/PSD/ASD; energy/exponential/maxhold; isolated worker; F1-H5 harmonic candidates',
              'interval_export':['CSV','Parquet','PDA3 single or lossless multipart'],'quality':['Good','Uncertain','Bad'],
              'timestamps':['source','server','local'],'logging':'rotating 5 MiB x 6 files',
              'multi_file_recordings':{'max_files':32,'max_signals_per_view':64,'formats':['csv','parquet','das','known-profile PDA3 DAT'],
                  'alignment':['absolute timestamps','align starts to first file','per-file manual millisecond offsets'],
                  'coverage':['union','intersection'],'interpolation':False,'signal_identity':'per-file namespace',
                  'worker':'isolated subprocess, lossless Float64 Parquet comparison with provenance',
                  'filters':['name','format','minimum duration','maximum duration'],'persistent_workspace':True},
              'viewer_features':['repository files','external local CSV/Parquet picker','signal selection','time range','min/max decimation','automatic dual Y axes','drag zoom','X-axis scroll/pan','X1/X2 exact markers','mixed-format multi-file time comparison'],
              'certificate_hashes':{f.name:digest(f) for f in sorted((ROOT/'seed_certificates').iterdir()) if f.is_file()}}
    output.with_name('BUILD_MANIFEST.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    print(json.dumps(manifest,indent=2))


if __name__=='__main__':
    main()
