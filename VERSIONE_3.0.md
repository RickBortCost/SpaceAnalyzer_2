# Space Fieldbus Analyzer 3.0 — applicazione interamente in C#

## Cosa cambia

Dalla 3.0 il prodotto non contiene più Python. L'EXE Windows è composto da:

| Componente | Tecnologia | Ruolo |
|---|---|---|
| `Space_Analyzer_V3.0.0.exe` | launcher Go (GUI) | splash con logo e barra di avvio, estrazione del servizio, avvio, strumenti `--tool` |
| `SpaceAnalyzer.exe` (incorporato) | C#, ASP.NET Core, .NET 8 self-contained | interfaccia web locale e tutte le API |
| `SpaceAnalyzer.Engine.dll` | C# | driver, buffer di acquisizione, registrazione, cattura primaria |

L'interfaccia (HTML/JS in `source/static` e `source/templates`) e le API HTTP sono le stesse della 2.x: stesse route, stessi formati JSON, stessi controlli di sicurezza (accesso solo da `127.0.0.1`/`localhost`, token per ogni POST e per l'intera API SSH, intestazioni `Cache-Control: no-store`, `nosniff`, `no-referrer`, CSP), stessi messaggi.

## Corrispondenza moduli 2.x → 3.0

| 2.x (Python) | 3.0 (C#) |
|---|---|
| `app.py` (Flask) | `source/app/SpaceAnalyzer/Program.cs`, `Web/Api.cs`, `Web/Http.cs`, `Runtime/AppRuntime.cs` |
| `source_sessions.py`, `acquisition_engine.py`, `acquisition_buffers.py` | `Engine/Sources/*`, `Engine/Acquisition/*` (in-process) |
| `mqtt_source.py`, `ads_*.py`, `s7_*.py`, asyncua | `Engine/Drivers/{Mqtt,Ads,S7,OpcUa}` |
| `recordings.py`, `durable_recording.py`, `primary_*.py`, `fda_recording.py`, `rbf_recording.py`, `recording_manager.py` | `Engine/Recording/*`, `Runtime/RecordingManager.cs` |
| `recording_reader.py`, `recording_preview.py`, `fda_das.py`, `rbf.py`, `pda3/`, `iba_dat.py` | `Recordings/RecordingLibrary.cs`, `FdaReader.cs`, `RbfReader.cs`, `Pda3.cs`, `Rows.cs` |
| `recording_compare.py` | `Recordings/Comparisons.cs` (thread dedicato invece del sottoprocesso) |
| `analysis_tools.py`, `quick_analysis.py`, `fft_engine.py`, `fft_service.py` | `Recordings/Export.cs`, `Analysis/QuickAnalysis.cs`, `Analysis/Fft.cs` |
| `formula_engine.py`, `calculation_*.py` | `Calculations/*` (thread dedicato con coda limitata e watermark di 20 ms) |
| `live_overview.py`, `live_archive.py`, `live_analysis.py` | `Live/LiveBuffer.cs`, `Live/LiveArchive.cs` |
| `acquisition_health.py` (EventStore) | `Infrastructure/EventStore.cs` (SQLite) |
| `primary_recovery.py`, `primary_tools.py` | `Recordings/PrimaryRecovery.cs`, `--tool inspect/recover/export-*` |
| `ssh_service.py` (Paramiko) | `Ssh/SshService.cs` (SSH.NET) |
| `startup_progress.py` | marcatori `@@SPACE_ANALYZER_STARTUP` in `Program.cs` |

## Verifiche eseguite

| Verifica | Esito |
|---|---|
| Test xUnit applicazione (`source/app/SpaceAnalyzer.Tests`): FFT, lettore CSV/Parquet, export, marker, decimazione picchi, formule, statistiche d'intervallo, parser, template | 20/20 |
| Test xUnit motore (`source/engine/SpaceAnalyzer.Engine.Tests`) | 3/3 |
| Workflow UI jsdom contro il servizio C# (`source/tests/ui_harness_cs.py`): Live, zoom, intervalli, calcoli, FFT, FDA, RBF, MQTT, ADS, S7, SSH, sessioni multi-sorgente, registrazioni sovrapposte/sincronizzate/chiusura, i18n, studio, strumenti grafico | 21/21 |
| Smoke test end-to-end (`tools/windows/smoke_exe.py --app`): 4 sessioni indipendenti, record/rilettura CSV, Parquet, FDA, RBF, cattura primaria verificata con il lettore C#, DAT rifiutato, marker e formule su originali oltre il FIFO, FDA da 128.000 righe sovrapposta a un nuovo CSV, istanza singola, arresto | PASS (Linux, build di sviluppo) |
| `test_launcher.py`: porta occupata, seconda apertura, workspace e import conservati al riavvio, arresto | PASS |
| Build Windows (`tools/windows/build_windows.py`): publish win-x64 self-contained, payload verificato, launcher Go con icona | PASS (cross-build da Linux, EXE 64 MB) |

L'esecuzione del vero EXE su Windows avviene nella pipeline (`.github/workflows/windows-exe.yml`) prima di ogni pubblicazione.

## Limiti noti

- **S7 simbolico (S7CommPlus)**: implementato con S7CommPlusDriver; l'emulatore di test non supporta l'autenticazione, quindi la verifica richiede una CPU S7-1200/1500 reale. Per lo stesso motivo `test_s7_modes_ui.cjs` è escluso dalla CI.
- **ADS nativo (TcAdsDll)**: richiede Windows con TwinCAT; la logica condivisa con ADS/TCP è verificata con il simulatore.
- **SSH**: in modalità comando l'opzione PTY non è disponibile con SSH.NET (stdout e stderr restano separati); Ctrl+C e input interattivo sono disponibili nella modalità shell.
- **FFT**: algoritmo identico alla 2.x; i valori possono differire dalla 2.x solo nell'ultima cifra decimale (arrotondamenti della libreria numerica).
- Python resta nel repository solo come strumento di test e build (simulatori dei protocolli, harness dei test UI, versionamento, packaging): non è incluso nell'EXE.
