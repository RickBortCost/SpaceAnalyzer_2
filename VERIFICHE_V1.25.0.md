# Verifiche Space Analyzer V1.25.0

**Aggiornamento Windows del 22 ottobre 2026:** il nuovo EXE è stato compilato, avviato e verificato su Windows ed è pubblicato nella [Release V1.25.0](https://github.com/RickBortCost/SpaceAnalizer/releases/tag/v1.25.0). [Rapporto EXE](releases/v1.25.0/WINDOWS_EXE_VERIFICATION.json) e [hash degli asset](releases/v1.25.0/WINDOWS_RELEASE.json). La verifica include cinque formati reali; DAT/iba con licenza reale resta escluso. Il rapporto seguente documenta la precedente consegna dei sorgenti Linux: la sua limitazione relativa alla mancata build Windows è quindi superata.

**180 test backend superati in 23 suite e 7 flussi DOM superati.** Esecuzioni nuove sui sorgenti aggiornati, il 22 ottobre 2026. I report storici V1.24 non sono stati usati come prova.

Le 15 combinazioni non vuote dei quattro protocolli sono coperte da sottocasi della suite `test_source_sessions`. La stessa suite prova i sei formati, quattro trasporti insieme, perdita/ripristino di ogni collegamento, guasto iniziale di una sola sorgente, selezione/cataloghi, segnali costanti, formule OPC UA, metadati, campioni tardivi, overflow, Stop, Start duplicato, riavvio e disconnessione con pubblicazioni ancora in coda.

Le prove ADS native usano sostituti della DLL e callback ABI simulate. Le prove S7 classiche usano un server Snap7 locale, quelle MQTT un peer locale, OPC UA un server asyncua locale; sono prove dei client reali, senza hardware industriale. S7 UDP usa socket reali locali. I fixture S7CommPlus non equivalgono a una CPU Siemens reale.

## Formati

| Formato | Verifica |
| --- | --- |
| CSV / Parquet | Scrittura e rilettura reali; campioni asincroni e provenienza dei quattro protocolli. |
| FDA/DAS4 / RBF | Conversione reale dei Parquet misti e rilettura con i codec del progetto; qualità e timestamp. |
| DAT/PDA3 | Codec e modello reali: proiezione causale a 10 ms, NaN per vuoti/qualità non valida, contesto tra finestre, segmentazione e conteggi dei campioni. Round trip dei bit Float32. |
| DAT/iba | Preparazione canali e conversione provate con FakeNative, esplicitamente non un DAT nativo. DLL e licenza Windows reali non verificate. |

## Limiti dichiarati

Non è stato prodotto o eseguito un nuovo EXE Windows. Il runtime Windows completo non è incluso nell’handoff; il programma `go` disponibile nell’ambiente non risponde come toolchain Go (`go version` fallisce). Il build script è aggiornato con versione, moduli e lista dei formati. Il bridge EXE/DLL già contenuto nei sorgenti è un componente originale del pacchetto, non un nuovo eseguibile Space Analyzer.

Non sono state eseguite prove con PLC fisici, TcAdsDll reale, ibaFiles.dll con licenza reale, FDA/RBF nelle rispettive applicazioni esterne, o ibaAnalyzer. La compatibilità sperimentale del modello PDA3 non viene promossa a compatibilità certificata. Le prove DOM usano JSDOM e canvas simulati; non sostituiscono una verifica visiva in un browser reale.

## Riproduzione

Preparazione da `source/` (virtualenv consigliato):

```sh
python -m pip install -r requirements.txt
python -m pip install python-snap7==2.0.2
cd tests
npm ci
cd ..
```

Le variabili d’ambiente dei comandi DOM sono scritte in sintassi shell Unix; in Windows impostarle prima con PowerShell o cmd. La dipendenza python-snap7 è esclusivamente di test. I log riportano anche gli errori attesi dei test di overflow/disco pieno: il risultato della suite è quello finale.

| Suite backend | Test | Esito |
| --- | ---: | --- |
| test_ads | 12 | PASS |
| test_ads_notifications | 12 | PASS |
| test_s7 | 8 | PASS |
| test_s7_extended | 16 | PASS |
| test_binary_recordings | 6 | PASS |
| test_recordings | 6 | PASS |
| test_recording_reader | 1 | PASS |
| test_recording_compare_api | 4 | PASS |
| test_fda | 12 | PASS |
| test_rbf | 11 | PASS |
| test_pda3_codec | 3 | PASS |
| test_pda3 | 14 | PASS |
| test_pda3_lifecycle | 3 | PASS |
| test_pda3_capacity | 8 | PASS |
| test_iba_dat | 13 | PASS |
| test_calculation_api | 3 | PASS |
| test_fft_api | 5 | PASS |
| test_fft_engine | 10 | PASS |
| test_i18n | 4 | PASS |
| test_mqtt_api | 10 | PASS |
| test_live_extensions | 7 | PASS |
| test_source_sessions | 9 | PASS |
| test_mixed_resampling | 3 | PASS |

Comandi eseguiti (equivalenti, con il Python del virtualenv):

```sh
python -m unittest discover -s tests -p test_ads.py -v
python -m unittest discover -s tests -p test_ads_notifications.py -v
python -m unittest discover -s tests -p test_s7.py -v
python -m unittest discover -s tests -p test_s7_extended.py -v
python -m unittest discover -s tests -p test_binary_recordings.py -v
python -m unittest discover -s tests -p test_recordings.py -v
python -m unittest discover -s tests -p test_recording_reader.py -v
python -m unittest discover -s tests -p test_recording_compare_api.py -v
python -m unittest discover -s tests -p test_fda.py -v
python -m unittest discover -s tests -p test_rbf.py -v
python -m unittest discover -s tests -p test_pda3_codec.py -v
python -m unittest discover -s tests -p test_pda3.py -v
python -m unittest discover -s tests -p test_pda3_lifecycle.py -v
python -m unittest discover -s tests -p test_pda3_capacity.py -v
python -m unittest discover -s tests -p test_iba_dat.py -v
python -m unittest discover -s tests -p test_calculation_api.py -v
python -m unittest discover -s tests -p test_fft_api.py -v
python -m unittest discover -s tests -p test_fft_engine.py -v
python -m unittest discover -s tests -p test_i18n.py -v
python -m unittest discover -s tests -p test_mqtt_api.py -v
python -m unittest discover -s tests -p test_live_extensions.py -v
python -m unittest discover -s tests -p test_source_sessions.py -v
python -m unittest discover -s tests -p test_mixed_resampling.py -v
```

Flussi DOM:

```sh
JSDOM_PATH=tests/node_modules/jsdom UI_SCRIPT=test_i18n_ui.cjs python tests/test_ui_harness.py
JSDOM_PATH=tests/node_modules/jsdom MULTICHART_TEST=1 UI_SCRIPT=test_recording_close_ui.cjs python tests/test_ui_harness.py
JSDOM_PATH=tests/node_modules/jsdom MULTICHART_TEST=1 UI_SCRIPT=test_recording_sync_ui.cjs python tests/test_ui_harness.py
JSDOM_PATH=tests/node_modules/jsdom MULTICHART_TEST=1 UI_SCRIPT=test_chart_tools_ui.cjs python tests/test_ui_harness.py
JSDOM_PATH=tests/node_modules/jsdom MULTICHART_TEST=1 UI_SCRIPT=test_calculations_ui.cjs python tests/test_ui_harness.py
JSDOM_PATH=tests/node_modules/jsdom UI_SCRIPT=test_live_extensions_ui.cjs python tests/test_ui_harness.py
JSDOM_PATH=tests/node_modules/jsdom python tests/test_source_sessions_ui_harness.py
```

Verifica sintattica: `python -m compileall -q` sui moduli modificati e `node --check` sugli script applicativi modificati, superate. I log completi e il riepilogo JSON sono in `evidence_v1.25/`.

## File aggiunti o modificati

La guida `source/GUIDA_ACQUISIZIONE_SIMULTANEA.md` descrive configurazione, tempi, qualità e politiche dei formati. Diff rispetto al manifest originale dell’handoff:

- `source/CHANGELOG.txt`
- `source/GUIDA_ACQUISIZIONE_SIMULTANEA.md`
- `source/app.py`
- `source/build_windows.py`
- `source/localization/it.json`
- `source/localization/translations.tsv`
- `source/pda3/recording.py`
- `source/pda3/resampling.py`
- `source/pda3/segmented_export.py`
- `source/pda3/window_export.py`
- `source/recording_compare.py`
- `source/recordings.py`
- `source/s7_protocols.py`
- `source/s7_udp.py`
- `source/source_sessions.py`
- `source/static/app.js`
- `source/static/i18n-catalog.js`
- `source/static/live-extras.css`
- `source/static/live-extras.js`
- `source/static/source.js`
- `source/templates/index.html`
- `source/tests/test_calculations_ui.cjs`
- `source/tests/test_live_extensions.py`
- `source/tests/test_live_extensions_ui.cjs`
- `source/tests/test_mixed_resampling.py`
- `source/tests/test_s7_extended.py`
- `source/tests/test_source_sessions.py`
- `source/tests/test_source_sessions_ui.cjs`
- `source/tests/test_source_sessions_ui_harness.py`

Il pacchetto non contiene nuove credenziali o certificati privati. Il bootstrap genera un certificato client locale quando i seed sono assenti; la fiducia OPC UA va configurata sul server destinatario. Non sono stati effettuati push, pubblicazioni o invii a terzi.
