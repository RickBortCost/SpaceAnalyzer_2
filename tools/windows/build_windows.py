"""Build the standalone Windows x64 EXE: Go GUI launcher + self-contained .NET 8 service.

Build-time tooling only (requires the .NET 8 SDK and Go on the build PC); the
destination PC needs neither .NET nor Python. The service is published
self-contained for win-x64 and embedded as payload.zip in the launcher.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'source'
APP = SOURCE / 'app' / 'SpaceAnalyzer'
VERSION = '3.12.0'


def validate_versions():
    """Refuse an EXE whose launcher or service advertises another version."""
    defaults = (APP / 'Config' / 'Defaults.cs').read_text(encoding='utf-8')
    project = (APP / 'SpaceAnalyzer.csproj').read_text(encoding='utf-8')
    launcher = (SOURCE / 'launcher' / 'launcher_windows.go').read_text(encoding='utf-8')
    found = {
        'Defaults.cs': re.search(r'public const string Version = "([^"]+)"', defaults),
        'SpaceAnalyzer.csproj': re.search(r'<Version>([^<]+)</Version>', project),
        'launcher_windows.go': re.search(r'const version = "([^"]+)"', launcher),
    }
    wrong = {name: (m.group(1) if m else None) for name, m in found.items() if not m or m.group(1) != VERSION}
    if wrong:
        raise RuntimeError('Versioni servizio, launcher e build non allineate: ' + json.dumps(wrong))


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def publish(work, dotnet):
    runtime = work / 'runtime'
    if runtime.exists():
        shutil.rmtree(runtime)
    subprocess.run([dotnet, 'publish', str(APP / 'SpaceAnalyzer.csproj'), '-c', 'Release', '-r', 'win-x64', '--self-contained', 'true',
                    '-p:DebugType=None', '-p:DebugSymbols=false', '-p:SatelliteResourceLanguages=it%3Ben', '-o', str(runtime)], check=True)
    if not (runtime / 'SpaceAnalyzer.exe').is_file():
        raise RuntimeError('Pubblicazione .NET priva di SpaceAnalyzer.exe')
    for name in ('static', 'templates', 'seed_certificates'):
        if (SOURCE / name).is_dir():
            shutil.copytree(SOURCE / name, runtime / name, dirs_exist_ok=True)
    (runtime / 'static' / 'trend.mjs').unlink(missing_ok=True)
    shutil.copy2(SOURCE / 'THIRD_PARTY_NOTICES.txt', runtime / 'THIRD_PARTY_NOTICES.txt')
    (runtime / 'bundled-runtime.flag').write_text('Space Fieldbus Analyzer ' + VERSION + ' self-contained .NET service\n', encoding='utf-8')
    for name in ('static', 'templates'):
        for asset in (SOURCE / name).rglob('*'):
            if asset.is_file() and asset.name != 'trend.mjs':
                staged = runtime / asset.relative_to(SOURCE)
                if not staged.is_file() or digest(staged) != digest(asset):
                    raise RuntimeError('Asset del runtime non aggiornato: ' + str(asset.relative_to(SOURCE)))
    return runtime


def main():
    validate_versions()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--go', default='go')
    parser.add_argument('--dotnet', default=os.environ.get('DOTNET', 'dotnet'))
    parser.add_argument('--work', default=str(SOURCE / 'build'))
    parser.add_argument('--output', default=str(SOURCE / 'dist' / ('Space_Fieldbus_Analyzer_V' + VERSION + '.exe')))
    args = parser.parse_args()
    work = Path(args.work).resolve()
    work.mkdir(parents=True, exist_ok=True)
    runtime = publish(work, args.dotnet)
    launcher = work / 'launcher'
    if launcher.exists():
        shutil.rmtree(launcher)
    launcher.mkdir()
    for name in ('launcher_windows.go', 'splash_windows.go', 'splash_logo.png'):
        shutil.copy2(SOURCE / 'launcher' / name, launcher / name)
    payload = launcher / 'payload.zip'
    with zipfile.ZipFile(payload, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for file in sorted(runtime.rglob('*')):
            if file.is_file():
                archive.write(file, file.relative_to(runtime).as_posix())
    with zipfile.ZipFile(payload) as archive:
        if archive.testzip() is not None:
            raise RuntimeError('Payload ZIP corrotto')
        for name in archive.namelist():
            source = SOURCE / name
            if name.startswith(('static/', 'templates/')) and source.is_file() and archive.read(name) != source.read_bytes():
                raise RuntimeError('Payload diverso dai sorgenti: ' + name)
    (launcher / 'payload.sha256').write_text(digest(payload) + '\n', encoding='ascii')
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from windows_resources import build_icon_resource
    build_icon_resource(SOURCE / 'static' / 'space-analyzer.ico', launcher / 'icon_windows_amd64.syso')
    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, 'GOOS': 'windows', 'GOARCH': 'amd64', 'CGO_ENABLED': '0', 'GOTOOLCHAIN': 'local', 'GO111MODULE': 'off'}
    subprocess.run([args.go, 'build', '-trimpath', '-ldflags=-s -w -H windowsgui', '-o', str(output), '.'], cwd=launcher, env=env, check=True)
    if output.read_bytes().find(payload.read_bytes()) < 0:
        raise RuntimeError('EXE privo del payload aggiornato: build non pubblicabile')
    try:
        commit = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT, capture_output=True, text=True)
        source_commit = commit.stdout.strip() if commit.returncode == 0 else None
    except OSError:
        source_commit = None
    service = runtime / 'SpaceAnalyzer.exe'
    manifest = {
        'application_name': 'Space Fieldbus Analyzer', 'version': VERSION, 'platform': 'Windows x64', 'exe': output.name, 'size_bytes': output.stat().st_size,
        'icon': 'Space Fieldbus Analyzer logo (RT_ICON / RT_GROUP_ICON) in launcher and service',
        'source_commit': source_commit, 'embedded_payload_verified': True,
        'packaging': 'Go GUI launcher + self-contained .NET 8 service (win-x64), runtime cached per payload in %LOCALAPPDATA%/OPCUA_Web_Client/runtime',
        'service': {'exe': 'SpaceAnalyzer.exe', 'sha256': digest(service), 'framework': '.NET 8 self-contained', 'web': 'ASP.NET Core Kestrel on 127.0.0.1'},
        'python_required_at_runtime': False, 'python_in_product': False, 'dotnet_required_at_runtime': False,
        'payload_sha256': digest(payload), 'sha256': digest(output),
        'source_hashes': {p.relative_to(ROOT).as_posix(): digest(p) for p in sorted(SOURCE.rglob('*'))
                          if p.is_file() and p.parts[len(SOURCE.parts)] in ('app', 'engine', 'static', 'templates', 'launcher')
                          and not any(part in ('bin', 'obj', 'TestResults') for part in p.parts)},
        'engines': {'acquisition': 'C#', 'recording': 'C#', 'analysis': 'C#', 'web_api': 'C# (ASP.NET Core)'},
        'data_sources': ['opcua', 'ads', 's7', 'mqtt'],
        'opcua_library': 'OPC Foundation .NET Standard 1.5.378',
        'mqtt_library': 'MQTTnet 5', 's7_symbolic_library': 'S7CommPlusDriver (LGPL)', 'ssh_library': 'SSH.NET',
        'recording_formats': ['csv', 'parquet', 'das', 'rbf'], 'dat_recording': False, 'dat_viewing': ['PDA3 known profile', 'iba DAT 2.x parts via companion Parquet'],
        'primary_capture': 'SAC CRC-checked blocks with startup recovery',
        'ui_languages': ['en', 'it', 'de', 'fr', 'es'],
        'certificate_hashes': {f.name: digest(f) for f in sorted((SOURCE / 'seed_certificates').iterdir()) if f.is_file()} if (SOURCE / 'seed_certificates').is_dir() else {},
    }
    output.with_name('BUILD_MANIFEST.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    print(json.dumps(manifest, indent=2))


if __name__ == '__main__':
    main()
