# Correzione Catalogo V1.25.1

## Problema e causa

Con ADS attivo, scegliere OPC UA nel menu Catalogo lasciava etichetta e radice su ADS. Il file `live-extras.js`, che inizializza quel menu, era caricato senza `defer` alla fine della pagina: veniva eseguito prima degli script differiti da cui dipende e si interrompeva con `$ is not defined`. Il menu cambiava visivamente senza cambiare il protocollo usato per esplorazione e scansione.

Ora anche quel file è differito, rispettando l'ordine dei componenti. Nessuna modifica ai driver o ai codec di registrazione.

## Regressione

Il test `source/tests/test_source_sessions_ui.cjs` carica i tag script della pagina servita tramite HTTP nell'ordine effettivo dell'HTML. Non concatena più gli script e non chiama direttamente la funzione di cambio sorgente. La prova sulla pagina precedente fallisce con `$ is not defined`; la pagina corretta supera il flusso.

Verifiche eseguite con peer locali:

- ADS come catalogo iniziale e quattro sorgenti connesse.
- Cambio tramite il vero menu Catalogo, radici separate, scansione e selezione dei segnali ADS, OPC UA, S7 e MQTT.
- Disconnessione e riconnessione ADS senza cancellare la selezione comune.
- Esplorazione OPC UA con ADS attivo durante Record CSV, conservazione dei buffer e rilettura dei quattro segnali registrati.
- Autoscan MQTT con conservazione di rami, focus, scroll e selezione.
- Cinque lingue, sei formati disponibili e persistenza dei protocolli abilitati.
- 9 test backend multi-sorgente superati, comprese le 15 combinazioni di protocolli e le registrazioni nei sei formati (DAT/iba tramite sostituto dichiarato della DLL).
- Avvio, porta occupata, istanza singola, arresto e persistenza tramite `test_launcher.py`.

Log in `evidence_v1.25.1/`. Comandi:

```sh
JSDOM_PATH=/path/to/node_modules/jsdom python source/tests/test_source_sessions_ui_harness.py
python -m unittest discover -s source/tests -p test_source_sessions.py
python source/tests/test_launcher.py
```

Dipendenze: `source/requirements.txt`, `python-snap7==2.0.2`, Node 24 e `jsdom@30.0.1`.

## Windows e limiti

**Build e verifiche Windows superati, asset pubblicati il 22 ottobre 2026.** [Workflow](https://github.com/RickBortCost/SpaceAnalizer/actions/runs/39788164608), [regressione DOM su Windows](evidence_v1.25.1/windows_catalog_ui.json), [rapporto del vero EXE](releases/v1.25.1/WINDOWS_EXE_VERIFICATION.json) e [download/hash](releases/v1.25.1/WINDOWS_RELEASE.json). Il codice compilato è il commit `02dfbaf4fbebe1322efddfad1c5f76673a2b0632`.

Il workflow Windows esegue la regressione DOM prima della compilazione, poi avvia il vero EXE e verifica cinque formati reali, istanza singola e arresto. Pubblica gli asset soltanto dopo il superamento delle verifiche; log e risultati sono negli artifact del workflow e negli asset della Release V1.25.1.

I canvas DOM sono simulati. I peer sono locali: non sono prove sui PLC dell'utente o sulla DLL ADS nativa reale. DAT/iba con licenza reale e compatibilità nelle applicazioni native restano da verificare nell'ambiente destinatario. Il test del vero EXE è distinto dalla regressione DOM eseguita sui sorgenti.
