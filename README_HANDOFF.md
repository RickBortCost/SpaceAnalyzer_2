# Space Analyzer V1.28.0 — sorgenti aggiornati

Implementazione basata sui sorgenti V1.24.0 dell’handoff, senza ricreare l’applicazione.

- Registrazioni indipendenti durante i salvataggi, conversioni isolate e letture accelerate con cache limitate.
- Marker sull’intera acquisizione e formule sotto i grafici con più ingressi e storico originale temporaneo.
- Zoom live con durata X costante, limiti Y fissi, pausa manuale e persistenza nel workspace.
- Acquisizione simultanea OPC UA, ADS, MQTT e S7 in ogni sottoinsieme.
- Registrazioni miste CSV, Parquet, FDA/DAS4, RBF, DAT/PDA3 e DAT/iba, con i limiti reali dei codec e delle DLL.
- Sessioni indipendenti, configurazione persistente, cataloghi, controlli e cinque lingue.

Leggere `source/GUIDA_REGISTRAZIONI_RAPIDE.md`, `source/GUIDA_CALCOLI_INTERVALLO.md`, `source/GUIDA_ZOOM_LIVE.md`, `source/GUIDA_ACQUISIZIONE_SIMULTANEA.md`, `source/CHANGELOG.txt` e `VERIFICHE_V1.25.0.md` prima dell’uso.

## Esecuzione dai sorgenti

Creare un virtualenv, installare `source/requirements.txt`, quindi eseguire `python app.py` dalla directory `source/`. Le dipendenze di test sono descritte nel rapporto.

## Windows

Il nuovo EXE Windows V1.28.0 e lo ZIP pronto da estrarre sono disponibili nella [Release](https://github.com/RickBortCost/SpaceAnalizer/releases/tag/v1.28.0). Leggere [LEGGIMI_AVVIO_WINDOWS.md](LEGGIMI_AVVIO_WINDOWS.md). Il workflow pubblica la Release dopo la regressione DOM e la prova del vero EXE su Windows; vedere [verifiche V1.28.0](VERIFICHE_V1.28.0.md). Su un PC con Python e Go compatibili eseguire `source/build_windows.cmd`; la procedura può richiedere accesso ai runtime/dipendenze. Il build verifica che il payload corrisponda ai sorgenti. Validare l’eseguibile e i collegamenti reali prima dell’uso operativo.

I componenti nativi e il modello PDA3 già presenti nell’handoff mantengono le rispettive licenze e limitazioni. Non viene fornita una licenza iba. I certificati client privati non sono distribuiti; vengono generati localmente. I certificati server e la fiducia vanno configurati per l’installazione destinataria.

`MANIFEST_SHA256.json` descrive tutti i file di questa consegna, escluso il manifest stesso. `examples/` contiene esempi originali dell’handoff, non acquisizioni nuove della V1.25. Le prove della V1.25.0 restano in `evidence_v1.25/`; quelle della correzione V1.25.1 sono in `evidence_v1.25.1/`; Zoom live V1.26.0 è verificato in `evidence_v1.26.0/`. Marker e formule V1.27.0 hanno rapporti in `evidence_v1.27.0/`.
