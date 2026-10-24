# Avvio di Space Analyzer 2 su Windows

1. Apri la sezione **Releases** del repository e scarica dall’ultima release il file `Space_Analyzer_V<versione>_WINDOWS.zip` (ad esempio `Space_Analyzer_V2.0.0_WINDOWS.zip`).
2. Clic destro sullo ZIP → **Estrai tutto**. Scegli una cartella scrivibile, ad esempio `Documenti\Space Analyzer`.
3. Apri la cartella estratta e fai doppio clic su **Space_Analyzer_V<versione>.exe**.
4. Attendi l’apertura del browser: quella pagina è l’interfaccia del software. Se il browser non si apre, visita `http://127.0.0.1:5000`; in caso di porta occupata il software sceglie una delle successive.
5. Nel popup **Connessione** configura i collegamenti. Attiva **Acquisizione simultanea** per usare insieme OPC UA, ADS, MQTT e S7, quindi premi **Connetti e leggi**.

**Novità della 2.0 (rispetto alla V1.29.0).** Registrazione FDA/.das: i campioni dello stesso istante finiscono nello stesso frame (file molto più piccoli), la scala Int16 usa passi 1-2-5 (risoluzione fino a 5 volte migliore), i valori Uncertain non vengono più azzerati, le registrazioni oltre 49,7 giorni proseguono in una nuova parte, i salti dell'orologio sono riportati nei metadati e lo spazio disco viene controllato. Acquisizione ADS: la coda delle notifiche cresce con canali e frequenza, un PLC in STOP non interrompe più l'acquisizione, su Windows il polling usa timer da 1 ms e i cicli saltati vengono contati. Dettagli in `CHANGELOG.md` nel repository. Il numero di versione è assegnato automaticamente a ogni rilascio (vedi `VERSIONI.md`).

La V1.29.0 aggiunge un archivio primario recuperabile in `Recordings/.primary/`, code separate per sorgente, priorità alla registrazione e diagnostica backend. Il formato scelto continua a essere salvato separatamente. Consultare `FASE1_ACQUISIZIONE_REGISTRAZIONE.md` per risultati misurati, recovery, API e limiti: il target ADS 1.000 canali a 10 ms non è qualificato su PLC reali.

In V1.29.0 puoi premere nuovamente **Record** mentre il file precedente viene salvato. **Stop** conclude l’intervallo e separa il salvataggio dall’acquisizione successiva. Nel pannello **Salvataggi in background** controlli stato, file pronti ed errori di ogni sessione; **Apri registrazione** apre un file completato anche durante un altro Record. FDA e RBF vengono convertiti in processi separati e le parti già chiuse vengono elaborate durante l’acquisizione. Tutti e sei i formati usano sessioni indipendenti. Vedere `GUIDA_REGISTRAZIONI_RAPIDE.md`.

Apertura FDA e Parquet ottimizzata, cache delle viste e uso di memoria limitato per le panoramiche. La riduzione dei punti riguarda il grafico: i campioni originali e il loro uso per FFT, formule ed export restano preservati. Per FDA/RBF/iba il Parquet originale usa ora il profilo Rapido, che può occupare più spazio del precedente Compatto.

È inclusa la correzione del menu **Catalogo** durante l'acquisizione simultanea. In **LIVE → Address Space · scansione**, scegliendo OPC UA devono comparire **Nodo di partenza** e la sua radice (normalmente `i=85`); scegliendo ADS devono comparire **Prefisso simboli ADS** e la radice ADS (normalmente `ads:/`). Premi **Esplora rami** o **Scansiona tutto** per trovare e selezionare le variabili di ciascuna sorgente. **● Record** registra la selezione comune; **■ Stop** finalizza il file. I dettagli sono in `GUIDA_ACQUISIZIONE_SIMULTANEA.md`.

Il nuovo pulsante **Zoom live**, accanto a **Pausa**, mantiene il grafico in Play durante lo zoom: la finestra X segue i dati con la durata scelta e le scale Y restano fisse. Funziona anche se il grafico è già fermo per uno zoom. **Pausa** ferma volontariamente la vista; **Play** riprende conservando lo zoom quando Zoom live è abilitato. La scelta è salvata nel workspace. Vedere `GUIDA_ZOOM_LIVE.md`.

In **Intera acquisizione** i marker e i calcoli leggono gli originali anche oltre il buffer recente. Sotto il grafico, in **Dati calcolati**, assegna le variabili A, B… con **Aggiungi variabile**, scrivi ad esempio `[A]-[B]`, scegli il risultato e premi **Calcola**. Con entrambi i marker il calcolo usa X1–X2; altrimenti usa la finestra visibile. **Salva e aggiungi al grafico** crea o aggiorna un segnale nella sezione generale Dati calcolati. Vedere `GUIDA_CALCOLI_INTERVALLO.md`.

Lo storico originale Live è temporaneo (massimo 8 GiB), viene azzerato a ogni nuova acquisizione o cancellazione dati ed eliminato alla chiusura. Per conservare i dati usa Record. I campioni già eliminati dalle vecchie versioni non sono recuperabili: avvia una nuova acquisizione con la V1.29.0 o successiva oppure apri la registrazione originale.

Non occorre installare Python, pip o Go sul PC di utilizzo. L’EXE contiene il runtime e i moduli aggiornati. Puoi anche scaricare il solo EXE dalla stessa Release.

Le registrazioni vengono salvate nella cartella `Recordings` accanto all’EXE. Le impostazioni sono in `%LOCALAPPDATA%\OPCUA_Web_Client`. Per chiudere il software usa **Chiudi applicazione** nell’interfaccia; chiudere soltanto la scheda del browser lascia attivi i servizi. Prima di avviare la nuova versione chiudi l’eventuale versione precedente.

Se non si avvia, consulta `%LOCALAPPDATA%\OPCUA_Web_Client\launcher-v<versione>.log` e `opcua-client.log` nella stessa cartella.

## Verifiche e componenti esterni

La Release viene pubblicata soltanto dopo regressione DOM della pagina reale con quattro peer locali, compilazione su Windows, verifica del payload incorporato, avvio del vero EXE, prova delle sessioni indipendenti con peer MQTT locale, registrazione/rilettura CSV, Parquet, FDA/DAS4, RBF e PDA3, seconda apertura e arresto. Il relativo rapporto è `WINDOWS_EXE_VERIFICATION.json`; gli hash sono in `SHA256SUMS.txt`. La regressione DOM usa i sorgenti e canvas simulati; il test del vero EXE è separato.

I test di avvio non sostituiscono prove con PLC e reti industriali. ADS nativo richiede TwinCAT/TcAdsDll e la route appropriata. DAT/iba richiede la licenza di scrittura reale: non viene fornita o simulata. PDA3 mantiene i limiti sperimentali documentati del modello e gli originali Parquet. Per connessioni OPC UA protette occorre autorizzare il certificato client sul server e configurare la fiducia del certificato server.
