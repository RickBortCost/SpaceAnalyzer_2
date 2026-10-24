# Stato della consegna 2.1.0

Seconda iterazione della Fase 1, senza modifiche a UI, grafici o formati:

- rilevamento delle perdite ADS a monte della callback;
- notifiche ADS a blocco opzionali;
- eventi di integrità nel file primario;
- IPC del writer più leggera;
- rate recenti nella diagnostica.

Superati 243 test in 29 suite (inclusa la correzione dell'errore sorgente perso durante la chiusura di una sessione), la pipeline multi-protocollo (2.000.000 di campioni senza perdite, anche con UI sotto carico) e uno smoke soak di 30 s con 3.000.000 di campioni.

Il benchmark ADS di riferimento (1.000 variabili a 10 ms) **non è raggiunto**: in simulazione si sostengono circa 60.000 valori/s con notifiche a blocco e circa 16.000 per simbolo. Ogni overflow è esplicito e attribuito. PLC reali e soak di 24 ore restano da eseguire. Dettagli in `FASE1_HARDENING_V2.1.md`.

## Consegna precedente V1.29.0

Implementata la Fase 1 del backend: buffer per connessione, recorder prioritario, processo di scrittura SAC v1 con checksum/flush/recovery, limiti Start/Stop monotoni, timestamp UTC originali, valori tipizzati, eventi SQLite e API di diagnostica. Un formato sovraccarico viene ricostruito dal primario dopo Stop senza impedire un nuovo Record. Le funzionalità e i formati precedenti rimangono disponibili nei relativi limiti; JavaScript, CSS e template non sono stati modificati.

Superati 229 test in 28 suite, quattro flussi DOM e Chromium reale; due scenari di pipeline da 2.000.000 di campioni senza perdite. La simulazione ctypes ADS con 1.000 canali conserva i dati ma non raggiunge 100.000 valori/s. Il soak di 24 ore è disponibile separatamente; eseguito soltanto uno smoke continuo di 3 secondi con 300.000 campioni e tre file. PLC fisici e licenza iba reale restano da qualificare. Dettagli, moduli, metriche e Fase 2 in `FASE1_ACQUISIZIONE_REGISTRAZIONE.md` e `VERIFICHE_V1.29.0.md`.

Build e prova del vero EXE Windows V1.29.0 superate: 46 test backend, quattro flussi DOM, cinque formati, sequenza primaria, FDA da 128.000 campioni e CSV da 640 durante il salvataggio, istanza singola e arresto. EXE e ZIP pubblicati nella Release V1.29.0; metadata e SHA-256 in `releases/v1.29.0/`.

## Consegna precedente V1.28.0

Registrazione separata dai salvataggi precedenti per tutti i sei formati; conversioni isolate e parti ruotate elaborate durante l’acquisizione. Pannello Salvataggi in background, identità indipendenti e Stop protetto da richieste obsolete. Letture FDA/Parquet e scritture CSV/Parquet ottimizzate; viste limitate e cache invalidate dalle modifiche ai file. Guida: `source/GUIDA_REGISTRAZIONI_RAPIDE.md`.

Superati 157 test backend in 22 suite, cinque flussi DOM, Chromium reale, benchmark e smoke del processo sorgente con FDA da 128.000 righe e successivo CSV da 640 righe durante la conversione. Rapporti in `evidence_v1.28.0/` e `VERIFICHE_V1.28.0.md`. Build e prove del vero EXE Windows V1.28.0 superati: 16 test backend, quattro flussi DOM, payload, cinque formati, marker/formule, FDA da 128.000 campioni e nuovo CSV da 640 campioni durante la conversione, istanza singola e arresto. EXE e ZIP sono pubblicati nella Release V1.28.0; rapporti e SHA-256 in `releases/v1.28.0/`.

## Consegna precedente V1.27.0

Corrette le analisi con marker in Intera acquisizione attraverso uno storico temporaneo degli originali. Nuovo editor sotto il grafico con più variabili, formule, statistiche, richiamo dei salvataggi e aggiunta al grafico, anche con protocolli diversi. Guida in `source/GUIDA_CALCOLI_INTERVALLO.md`, prove in `VERIFICHE_V1.27.0.md` ed `evidence_v1.27.0/`.

Verifiche locali superate: 59 test backend, cinque flussi DOM, browser Chromium reale e smoke del processo sorgente. Build e prove del vero EXE V1.27.0 superati: otto prove backend, tre flussi DOM, payload, cinque formati, marker e formula oltre il FIFO, istanza singola, arresto e pulizia della cache. EXE e ZIP sono pubblicati nella Release V1.27.0; rapporti e SHA-256 in `releases/v1.27.0/`.

## Consegna precedente V1.26.0

Aggiunto Zoom live: i grafici seguono i dati mantenendo la durata X e i limiti Y dello zoom. Guida in `source/GUIDA_ZOOM_LIVE.md` e prove in `VERIFICHE_V1.26.0.md`.

La V1.26.0 è pubblicata su GitHub con EXE e ZIP verificati su Windows: due prove DOM, compilazione, payload, avvio, cinque formati e arresto sono superati. Rapporti e SHA-256 in `releases/v1.26.0/`.

Corretto il caricamento del menu Catalogo: la scansione OPC UA è selezionabile mentre ADS è attivo. La regressione usa la pagina HTML reale e il menu visibile, con prove durante Record. Vedere [verifiche V1.25.1](VERIFICHE_V1.25.1.md) ed `evidence_v1.25.1/`.

La richiesta multi-protocollo è implementata nei sorgenti: gestione indipendente delle quattro sorgenti e registrazione mista nei sei formati esistenti. La vecchia limitazione CSV/Parquet è rimossa insieme alle modifiche di codec, metadati, finalizzazione e rilettura necessarie. PDA3 usa una politica esplicita di proiezione a 10 ms e conserva gli originali.

Risultati e limiti della correzione sono in VERIFICHE_V1.25.1.md; le prove precedenti restano in VERIFICHE_V1.25.0.md. Regressione DOM, build ed esecuzione del vero EXE Windows V1.25.1 sono superati e gli asset sono pubblicati nella Release V1.25.1; vedere releases/v1.25.1/WINDOWS_EXE_VERIFICATION.json. Restano da eseguire nell’ambiente destinatario: PLC reali, DLL ADS reale, ibaFiles.dll con licenza reale, validazione nelle applicazioni native e controllo visivo browser. Non trattare i test simulati come certificazione di tali componenti.
