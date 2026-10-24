# Space Analyzer V1.29.0

**Fase 1: acquisizione e registrazione rafforzate.** Code limitate per connessione, priorità agli originali, archivio primario a blocchi con checksum e recovery dopo crash, backpressure e diagnostica persistente. Se il formato scelto si satura, viene ricostruito dal primario in un processo separato mentre può partire un nuovo Record. Nessuna nuova funzione grafica. [Rapporto completo, avvio, benchmark e Fase 2](FASE1_ACQUISIZIONE_REGISTRAZIONE.md) · [Verifiche V1.29.0](VERIFICHE_V1.29.0.md).

Verificati **229 test backend** e due scenari da **2.000.000 di campioni** a 1.000 canali/10 ms senza differenze nei progressivi. Il benchmark separato delle callback ADS ctypes non raggiunge il target: circa 25.682 valori/s; non è una qualificazione di PLC, DLL o rete reali. Test di 24 ore predisposto, non eseguito per 24 ore.

**Dalla V1.28.0: nuovo Record durante il salvataggio del file precedente**, per CSV, Parquet, FDA/DAS4, RBF, DAT/iba e DAT/PDA3. Sessioni indipendenti, conversioni separate, stato ed errori per ogni file in **Salvataggi in background**. Migliorate lettura FDA/Parquet, scrittura CSV/Parquet e riapertura delle viste; originali preservati per analisi ed export. [Guida](source/GUIDA_REGISTRAZIONI_RAPIDE.md) e [verifiche V1.28.0](VERIFICHE_V1.28.0.md).

Corrette le analisi con marker nella vista **Intera acquisizione**: uno storico temporaneo degli originali permette di analizzare anche gli intervalli usciti dal buffer recente. Il pannello **Dati calcolati** sotto i grafici ora accetta più variabili, anche di protocolli diversi, e formule come `[A]-[B]`; permette di scegliere la statistica, richiamare definizioni salvate, aprire l'editor completo e salvare il risultato come segnale. [Guida operativa](source/GUIDA_CALCOLI_INTERVALLO.md) e [verifiche V1.27.0](VERIFICHE_V1.27.0.md).

Nuovo pulsante **Zoom live** accanto a Pausa: mantiene la durata X e i limiti Y scelti mentre la finestra segue i nuovi dati. Agisce sui grafici LIVE, si salva nel workspace e mantiene lo zoom anche dopo Pausa/Play. [Guida operativa](source/GUIDA_ZOOM_LIVE.md) e [verifiche V1.26.0](VERIFICHE_V1.26.0.md).

Corretto il cambio di **Catalogo** durante l'acquisizione simultanea: scegliendo OPC UA con ADS attivo, il campo radice e la scansione passano effettivamente a OPC UA. La selezione comune e la registrazione restano attive. Vedere [correzione e verifiche V1.25.1](VERIFICHE_V1.25.1.md).

Sorgenti aggiornati dalla base V1.24.0 per l’acquisizione simultanea con **OPC UA, ADS, MQTT e S7**, in qualsiasi combinazione. Ogni sorgente ha sessione, errori e riconnessione indipendenti.

La registrazione mista supporta i sei formati esistenti: **CSV, Parquet, FDA/DAS4, RBF, DAT/PDA3 e DAT/iba**. I formati derivati conservano anche gli archivi Parquet originali; le dipendenze native e i limiti dei formati restano applicabili.

## Download e documentazione

- [ZIP completo dei sorgenti V1.25.0](releases/v1.25.0/Space_Analyzer_V1.25.0_SORGENTI.zip)
- [Guida all’acquisizione simultanea](source/GUIDA_ACQUISIZIONE_SIMULTANEA.md)
- [Changelog](source/CHANGELOG.txt)
- [Rapporto delle verifiche](VERIFICHE_V1.25.0.md)
- [Risultati e log](evidence_v1.25/)
- [Istruzioni di handoff e build Windows](README_HANDOFF.md)

**Verifiche della precedente V1.25.0:** 180 test backend in 23 suite e 7 flussi DOM superati, comprese le 15 combinazioni non vuote dei protocolli. Il test DOM della V1.25.1 carica gli script nell'ordine effettivo dell'HTML e usa il menu Catalogo; corregge la lacuna dei precedenti test che concatenavano i file JavaScript. I canvas restano simulati. DAT/iba è verificato con un sostituto dichiarato della DLL; DLL/licenza reali, PLC fisici, compatibilità nelle applicazioni native e controlli visivi restano da verificare nell’ambiente destinatario.

## Avvio su Windows

Apri la [Release V1.29.0](https://github.com/RickBortCost/SpaceAnalizer/releases/tag/v1.29.0), scarica **Space_Analyzer_V1.29.0_WINDOWS.zip**, estrai tutto in una cartella scrivibile e avvia **Space_Analyzer_V1.29.0.exe**. Non serve installare Python. Chiudi prima la vecchia versione con **Chiudi applicazione**. Per conservare la stessa cartella Recordings puoi mettere il nuovo EXE accanto al precedente; workspace e impostazioni mantengono la directory dati esistente.

[Istruzioni complete](LEGGIMI_AVVIO_WINDOWS.md). **Build e prove Windows V1.29.0 superate**: [workflow](https://github.com/RickBortCost/SpaceAnalizer/actions/runs/40075096236), 46 test backend e quattro flussi DOM, payload, esecuzione del vero EXE, cinque formati, sequenze primarie, marker/formule, FDA da 128.000 campioni e nuovo CSV da 640 durante il salvataggio, istanza singola e arresto. [Rapporto EXE](releases/v1.29.0/WINDOWS_EXE_VERIFICATION.json) e [asset con SHA-256](releases/v1.29.0/WINDOWS_RELEASE.json).

## Avvio dai sorgenti

Creare e attivare un virtualenv, quindi:

```sh
cd source
python -m pip install -r requirements.txt
python app.py
```

Il build locale è disponibile con `source/build_windows.cmd` su un PC di sviluppo con Python e Go. Il build automatico pubblica EXE, ZIP Windows, manifest e risultati delle prove nella Release; i componenti nativi originali inclusi nell’handoff mantengono le rispettive licenze e limitazioni. I certificati client vengono generati localmente e non sono distribuiti.

I test e le dipendenze di test sono descritti nel rapporto. Il manifest SHA-256 a radice descrive i file della consegna sorgenti; l’hash dello ZIP è in [PACKAGE_VALIDATION.json](releases/v1.25.0/PACKAGE_VALIDATION.json).
