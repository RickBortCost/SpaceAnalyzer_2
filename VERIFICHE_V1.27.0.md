# Marker e formule di intervallo — verifiche V1.27.0

## Risultato e origine dei dati

La panoramica **Intera acquisizione** conserva un riassunto grafico dei dati. La V1.26.0 tentava di risolvere i marker e le statistiche sul solo FIFO recente, causando risultati vuoti quando l'intervallo era precedente. Ora la V1.27.0 conserva anche gli originali della sessione in una cache SQLite temporanea, separata dal processo Record, con scrittura su thread dedicato, coda limitata per numero/byte e limite disco di 8 GiB.

Marker, statistiche, FFT ed export Live leggono gli originali. Gli snapshot dei grafici in pausa fissano sessione e sequenza: nuovi campioni e revisioni successive non cambiano il risultato congelato. Le revisioni di una serie calcolata allo stesso timestamp vengono contate una volta. Una nuova acquisizione o cancellazione dati invalida i riferimenti precedenti. Errori di cache sono espliciti e non causano un calcolo sulla panoramica aggregata. Il processo elimina la propria cache alla chiusura.

Il pannello sotto i grafici consente più variabili, riferimenti A/B, formula libera controllata, allineamento e tolleranza del motore già utilizzato da Dati calcolati. La statistica viene applicata alla serie risultante dalla formula. Sono disponibili definizioni salvate, passaggio all'editor completo e salvataggio/aggiunta al grafico. Le formule Live possono combinare protocolli diversi in acquisizione simultanea. Le modifiche salvate restano bloccate durante Record.

## Verifiche locali superate

**59 test backend in dieci suite**, [risultati](evidence_v1.27.0/backend_results.json):

- Otto nuove prove: 2.400 originali per segnale e FIFO di soli cinque campioni; valori marker, media e differenza su un vecchio intervallo; formule OPC UA/ADS prima dell'aggregazione; snapshot e invalidazione; revisioni di serie calcolate; allineamento causale/esatto, qualità e divisione per zero; sintassi arbitraria rifiutata; limiti disco/coda espliciti e pulizia.
- Motore formule e API, FFT su originali, panoramica, estensioni Live, quindici combinazioni non vuote dei quattro protocolli e cataloghi, registrazioni miste nei sei formati, ricampionamento PDA3, lingue e calcolo ADS. Le prove DAT/iba di queste suite usano il sostituto dichiarato della DLL, senza validare una licenza reale.

**Cinque flussi DOM**, con canvas simulati:

- Nuovo `test_live_interval_ui.cjs`: quattro sorgenti locali, segnali OPC UA/ADS, buffer di 10 secondi, attesa della reale evizione del vecchio intervallo; marker in Intera acquisizione, formula a due variabili, confronto numerico con il backend, editor completo, salvataggio, acquisizione del nuovo segnale, richiamo delle formule, cinque lingue e riapertura della pagina. Carica gli script dichiarati dall'HTML nell'ordine reale.
- Cataloghi delle quattro sorgenti e Record misto, Zoom live, Autoscan MQTT/marker e builder generale delle formule con preview Live/storico, Record, materializzazione Parquet e persistenza.

**Chromium reale**, mantenendo la CSP dell'applicazione: quattro driver contro peer locali, attesa dell'evizione dal FIFO, quattro righe marker valide su un vecchio intervallo, comandi reali per aggiungere variabili, assegnare OPC UA/ADS, calcolare e salvare il segnale. [Rapporto](evidence_v1.27.0/browser_interval.json), [schermata del pannello](evidence_v1.27.0/interval_formula.png).

**Smoke del processo sorgente**: avvio HTTP, quattro sessioni con errori isolati, registrazione/rilettura CSV, Parquet, FDA/DAS4, RBF e PDA3, marker e formula dopo l'evizione dal FIFO, istanza singola, arresto e pulizia dello storico. [Rapporto](evidence_v1.27.0/source_smoke.json). Questo rapporto Linux dichiara `packaged_exe_executed: false`. È superato anche il test separato di avvio, porta occupata e riapertura con workspace/importazioni conservati.

Riproduzione con Python + `source/requirements.txt`, `python-snap7==2.0.2`, Node 24 e `jsdom@30.0.1`:

```sh
python -m unittest discover -s source/tests -p test_live_interval_calculations.py
JSDOM_PATH=/path/to/node_modules/jsdom UI_SCRIPT=test_live_interval_ui.cjs python source/tests/test_source_sessions_ui_harness.py
```

Per il browser installare Playwright nello stesso ambiente Python e indicare Chromium:

```sh
python tools/verify_live_interval_browser.py --chromium /usr/bin/chromium --report /tmp/browser_interval.json --screenshot /tmp/interval_formula.png
```

## Windows e limiti

La pubblicazione V1.27.0 deve completare le otto prove backend e tre flussi DOM su Windows, compilare l'EXE con `live_archive.py`, verificare il payload, avviare il vero eseguibile senza Python installato nel PATH e provare cinque formati. Lo smoke Windows verifica anche i controlli incorporati, i marker e una formula oltre il FIFO recente, arresto e cancellazione della cache. Il pacchetto include la nuova guida operativa. **Build e prove Windows superati; EXE e ZIP pubblicati il 22 ottobre 2026.** [Workflow](https://github.com/RickBortCost/SpaceAnalizer/actions/runs/39797894663), [test di intervallo su Windows](evidence_v1.27.0/windows_interval_ui.json), [cataloghi](evidence_v1.27.0/windows_catalog_ui.json), [Zoom live](evidence_v1.27.0/windows_live_zoom_ui.json), [rapporto del vero EXE](releases/v1.27.0/WINDOWS_EXE_VERIFICATION.json) e [asset/SHA-256](releases/v1.27.0/WINDOWS_RELEASE.json). Il vero EXE ha verificato SQLite e il motore formule su un intervallo già uscito dal FIFO, oltre alla rimozione della cache alla chiusura. Commit compilato: `66315370de5d8f3ff0b8fc83a0fda5e6ce6c9a63`.

Lo storico temporaneo serve alla sessione corrente; non recupera originali già eliminati dalla vecchia versione e non sostituisce Record. PLC fisici, ADS nativo reale, scrittura DAT/iba con licenza e compatibilità con applicazioni native richiedono le prove dell'ambiente destinatario. Il browser reale di questa verifica è Chromium su Linux.
