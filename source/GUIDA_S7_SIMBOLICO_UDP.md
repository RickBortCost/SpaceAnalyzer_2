# Space Analyzer 1.13.0 — S7 simbolico e UDP Direct

Estrarre tutto lo ZIP, chiudere la versione precedente e avviare `Space_Analyzer_V1.14.0.exe`. Python e TIA Portal non sono necessari sul PC che esegue Space Analyzer. TIA serve per configurare il programma Siemens che invia i dati UDP.

Nel popup **Connessione → Fonte dati: S7** scegliere la modalità:

| Modalità | Origine delle variabili | Acquisizione |
|---|---|---|
| S7 classico | Catalogo di indirizzi assoluti DB/M/I/Q | Polling ReadVar su TCP 102 |
| S7 simbolico | Catalogo letto dalla CPU: DB, strutture e foglie degli array | S7CommPlus su TCP 102, letture aggregate |
| S7 UDP Direct Generic | Campi del payload definiti dall'utente | Il PLC invia ciclicamente i telegrammi al PC |

Tutte le modalità usano grafici, zoom e marker sincronizzati, formule, FFT Live/storica, Record CSV/Parquet, rotazione dei file, qualità e workspace esistenti. Le tre modalità mantengono selezioni e grafici separati. Una sola fonte dati è attiva alla volta. Fermare Record e disconnettere prima di cambiare modalità, parametri o catalogo.

## Accesso simbolico ai DB

1. Selezionare **S7 simbolico · DB/strutture/array** e inserire l'IP della CPU e la porta TCP, normalmente 102.
2. Impostare timeout, eventuale password CPU e TLS secondo il modello, firmware e autorizzazioni del progetto. Rack/slot e TSAP del catalogo classico non controllano questo trasporto.
3. Premere **Test connessione S7**: apre una sessione nativa S7CommPlus, esplora il catalogo e legge il primo simbolo disponibile. Il numero dei simboli viene mostrato nel risultato.
4. Premere **Connetti**. Nell'albero a sinistra usare **Esplora rami** oppure la scansione dei simboli e selezionare i dati da acquisire.
5. Espandere il DB, le strutture e le foglie degli array. La radice è `s7:sym:/`. Una sottostruttura può essere impostata come `s7:sym:/DB_Produzione.Data`; un segnale ha un ID come `s7:sym:DB_Produzione.Data.Carico`.

L'albero mostra l'ultimo nome, ad esempio `Carico`, con percorso completo al passaggio del mouse. Gli identificativi mantengono il percorso completo, inclusi maiuscole e indici, per distinguere nomi uguali in DB differenti. I rami con molti elementi hanno il pulsante per caricare la pagina successiva. Il catalogo viene caricato su richiesta e riutilizzato; **Aggiorna simboli CPU** lo rilegge dopo modifiche al programma, con Record fermo.

I nomi provengono dalla CPU, non da una ricostruzione degli offset. Il provider gestisce EXPLORE, informazioni di tipo, LID e SymbolCRC; i DB ottimizzati non richiedono la conversione automatica in DB standard. Non viene effettuato alcun ripiego silenzioso su PUT/GET: se la sessione simbolica è rifiutata, l'errore viene mostrato. Il catalogo espone le foglie che la CPU rende accessibili e il provider riesce a risolvere, fino a 50000 simboli. Restano al massimo 64 segnali acquisiti/calcolati per Record e la selezione Live esistente.

Sono decodificati BOOL, interi, bit string, REAL/LREAL, STRING/WSTRING e alcuni tipi temporali numerici. Altri tipi vengono conservati come testo esadecimale: non diventano falsi valori numerici. Gli interi a 64 bit oltre la precisione Float64 rimangono esatti nel campo testuale; grafici e formule usano Float64.

### Compatibilità, protezione e tempo

È incluso `s7commplus 0.1.0`, un provider Python non ufficiale pubblicato come **beta**. La presenza del catalogo e la possibilità di aprire una sessione dipendono da CPU, firmware e autorizzazioni. Questa versione offre password CPU, TLS opzionale, CA PEM e coppia certificato/chiave client; non implementa una schermata di login con utenti/ruoli UMAC. CPU che richiedono altri metodi di autenticazione possono rifiutare il collegamento. Il messaggio del test deve essere verificato sul PLC reale.

La password è usata solo nella sessione e non salvata nelle impostazioni o nel workspace. Se TLS è attivo senza CA, il canale è cifrato ma l'identità del certificato server **non è verificata**; il risultato lo indica. Inserire una CA attendibile per la verifica. I percorsi PEM si riferiscono al PC dell'applicazione e restano configurabili.

Le letture TCP sono eseguite in un worker dedicato e serializzate; esplorazione e lettura non bloccano il loop applicativo. Una scansione iniziale di un programma grande può richiedere tempo e ritardare le letture della stessa sessione. GetMultiVariables non fornisce il timestamp del ciclo PLC: SourceTimestamp/ServerTimestamp restano vuoti, LocalTimestamp indica il completamento della lettura. Più richieste non garantiscono uno snapshot dello stesso ciclo. Good indica una lettura riuscita, non lo stato RUN della CPU. Il polling richiesto a 10 ms rimane un obiettivo da misurare.

## UDP Direct Generic

Questa modalità implementa il formato **Direct Generic** descritto nel manuale pubblico iba S7-TCP/UDP. Richiede un programma PLC che costruisca e invii il telegramma. Non è il protocollo proprietario **iba Request-S7-UDP**: non ne include blocchi, negoziazione, licenze, ResolveSymbols o selezione remota dei tag.

Il ricevitore non apre TCP 102, non esplora i DB e non cambia il programma Siemens. Il catalogo UDP descrive i dati presenti nel payload. Selezionare nell'app un campo non modifica ciò che il PLC invia.

### Parametri nel popup Connessione

- **IP PLC**: filtra l'indirizzo del mittente; inserire quello della CPU che invia, non l'IP del PC.
- **IP locale PC**: `0.0.0.0` ascolta sulle interfacce IPv4; oppure scegliere l'indirizzo della scheda PC collegata al PLC.
- **Porta UDP locale**: 4170 predefinita. Configurare lo stesso valore come destinazione nel programma PLC. Un'altra applicazione non può occupare la stessa porta.
- **Porta mittente**: 0 accetta qualsiasi porta della CPU selezionata; un valore diverso limita anche la porta sorgente.
- **Module index**: un modulo Generic, ad esempio 200. Sono accettati 200–263, 1200–1263, 2200–2263 e 3200–3263.
- **Lunghezza**: totale del telegramma, inclusi i 6 byte di header, pari e compresa tra 8 e 4096 byte.
- **Byte order payload**: big-endian predefinito per Siemens; little-endian solo se il proprio programma lo usa. L'header rimane sempre big-endian.
- **Watchdog**: dopo l'assenza di telegrammi validi produce un'interruzione Bad. La ripresa dopo un timeout viene segnalata Uncertain.
- **Coda acquisizione**: capacità limitata per assorbire burst. Overflow e perdite sono visibili, non nascosti.
- **Offset timestamp**: `-1` usa la ricezione locale; un offset non negativo legge un LINT UTC in microsecondi dal 1970 nel payload. Questo è un campo opzionale del nostro layout, non un campo timestamp dell'header iba.

Il ritmo viene stabilito dal PLC. Un thread riceve i datagrammi e un altro li decodifica e li inoltra al flusso comune: un telegramma valido genera un campione di ciascun campo selezionato, anche se il periodo client è 500 ms. Il grafico può aggiornarsi meno spesso e decimare la vista; Record conserva i campioni ricevuti senza questa decimazione.

Il buffer Record viene dimensionato usando il periodo mediano osservato, preferendo i timestamp PLC; in loro assenza usa gli arrivi locali. È una stima iniziale: configurare il periodo client vicino al ritmo atteso se Record viene avviato prima di ricevere i primi campioni. Il campo `sample_ms` è un'indicazione intera; i timestamp mantengono la precisione originale. La FFT usa il tempo dei campioni, non la frequenza di aggiornamento del browser.

### Telegramma e catalogo di esempio

L'header consiste in tre campi di 16 bit big-endian: lunghezza totale, module index, contatore di sequenza. Il contatore avanza a ogni invio e ritorna a zero dopo 65535. Il layout di esempio seguente è definito da Space Analyzer:

| Byte nel telegramma | Offset nel payload | Campo | Formato |
|---|---|---|---|
| 0–1 | — | Lunghezza 24 | 16 bit big-endian |
| 2–3 | — | Modulo 200 | 16 bit big-endian |
| 4–5 | — | Sequenza | 16 bit big-endian |
| 6–13 | 0 | PLC UTC µs | LINT, 8 byte |
| 14–17 | 8 | CaricoA | REAL, IEEE-754, 4 byte |
| 18–21 | 12 | CaricoB | REAL, IEEE-754, 4 byte |
| 22 | 16.0 | Enabled | BOOL, bit 0 |
| 23 | 17 | Padding | 0 |

Impostare nell'app **lunghezza 24, modulo 200, payload big-endian, timestamp offset 0**. Nel catalogo UDP importare `ESEMPIO_VARIABILI_S7_UDP.json` oppure aggiungere `s7:udp:8:REAL`, `s7:udp:12:REAL` e `s7:udp:16.0:BOOL`. Gli offset sono dal payload, non dall'inizio del telegramma. L'importazione del catalogo aggiunge i tag; non cambia automaticamente lunghezza, modulo o timestamp e rifiuta duplicati.

### Preparazione TIA / PLC

`ESEMPI_TIA/SA_UDP_Direct_S71500.scl` contiene un FC originale di packing e un FB che mantiene stabili payload e indirizzo durante TUSEND. Il file non è un blocco iba e **non è stato compilato con TIA né provato su CPU fisica**. Generare i blocchi da sorgente esterno, verificare la versione delle istruzioni e adattare collegamento, nomi dei segnali e temporizzazione al progetto S7-1500.

1. Creare una connessione UDP OUC in TIA, oppure aprirla tramite TCON con i parametri del proprio progetto. Passare al FB il relativo ID `CONN_OUC` e `ConnectionReady` solo quando è disponibile. Il FB non crea la connessione.
2. Creare un `TADDR_Param` con `rem_ip_addr[1..4]` uguale all'IP del PC, `rem_port_nr = 4170` e `reserved = 0`. Il buffer del FB e il FC sono standard/non ottimizzati per la sovrapposizione AT del REAL; i segnali sorgente possono provenire da DB ottimizzati.
3. Chiamare il FB regolarmente, per esempio in un task a 1 ms, e fornire un impulso `SamplePulse` della durata di una chiamata ogni 10 ms. REQ deve poter tornare falso tra richieste. Se il task stesso dura 10 ms e l'ingresso rimane sempre vero non si ottiene un invio a ogni chiamata.
4. Collegare CaricoA/CaricoB/Enabled ai simboli effettivi. Per un timestamp UTC S7-1500, leggere `RD_SYS_T` in un LDT e, con esito zero, calcolare `LDT_TO_LINT(UtcLdt) / LINT#1000`. La precisione del clock dipende dalla CPU e dalla sua sincronizzazione. Se non disponibile, impostare nell'app offset `-1`: un semplice contatore relativo non è un timestamp UTC.
5. Verificare DONE/ERROR/STATUS e `SkippedSamples`. Se TUSEND è ancora occupato, l'esempio salta l'impulso e mantiene il telegramma immutato; non promette la consegna di quel campione. La sequenza conta le richieste di invio, non le risposte del PC.
6. Consentire l'ingresso UDP verso la porta del PC dalla CPU sulla rete autorizzata. Disconnettere Space Analyzer prima di usare altri ricevitori sulla stessa porta.

Per pacchetti più grandi verificare i limiti TUSEND del modello/firmware: il limite applicativo di 4096 byte non garantisce che una CPU possa inviare un pacchetto così grande. Iniziare con telegrammi piccoli; la documentazione Siemens indica comunemente 1472 byte, con varianti specifiche. CPU, task e rete devono essere misurati sul campo prima di dichiarare 10 ms.

### Test, qualità e timestamp

**Test connessione S7** in UDP attende un telegramma realmente valido; non dà successo soltanto perché la porta è aperta. Durante il test la porta non è disponibile a un'altra sessione. Attivare prima l'invio PLC; poi Test e Connetti. La diagnostica Live mostra periodo campioni, tempi di arrivo/P95, età, validi, persi, duplicati, fuori ordine, invalidi, filtrati, coda e timeout.

Duplicati e pacchetti fuori ordine sono scartati. Le lacune di sequenza e gli overflow rendono Uncertain i campioni interessati; non vengono riempite con zeri. Pacchetti con mittente/modulo diverso sono filtrati; lunghezza incoerente e timestamp invalidi sono rifiutati. I timestamp PLC devono essere crescenti, nell'intervallo UTC 2000–2199; un orologio che torna indietro richiede la verifica del clock e una nuova sessione. Il file conserva SourceTimestamp del payload, LocalTimestamp di ricezione e ServerTimestamp vuoto. Good descrive la validità del telegramma, non una qualità interna Siemens non presente nel payload.

Le registrazioni sono in `Recordings` accanto all'EXE, con metadati del trasporto, origine del tempo e catalogo. Parquet include queste informazioni anche nel footer. La qualità, il valore originale e i timestamp restano disponibili nel replay, nelle formule, nella FFT e negli export. Leggere `VERIFICA.txt` per l'ambito dei test: simulatori di rete e UI DOM, nessun collaudo Windows/Siemens fisico e nessuna garanzia deterministica a 10 ms.

## Fonti primarie consultate

- [Provider S7CommPlus — progetto e API](https://github.com/gijzelaerr/s7commplus), [release 0.1.0](https://pypi.org/project/s7commplus/0.1.0/).
- [ibaPDA Interface S7-TCP/UDP, issue 3.1 — Direct/Generic](https://www.iba-ag.com/fileadmin/webdownloads/software/ibaPDA/manuals/ibapda-interfaces/ibaPDA-Interface-S7-TCP-UDP/ibaPDA-Interface-S7-TCP-UDP_en_v3.1.pdf).
- [ibaPDA Request-S7-UDP, issue 2.2 — protocollo distinto](https://www.iba-ag.com/fileadmin/webdownloads/software/ibaPDA/manuals/ibapda-request-options/ibaPDA-Request-S7-UDP/ibaPDA-Request-S7-UDP_en_v2.2.pdf).
- [Siemens TUSEND e consistenza del buffer](https://docs.tia.siemens.cloud/r/en-us/v20/open-user-communication-s7-1200-s7-1500/other-s7-1200-s7-1500/sending-and-receiving-data-via-ethernet-udp-or-fdl-s7-1200-s7-1500/tusend-sending-data-s7-1200-s7-1500).
- [Siemens TADDR_Param](https://docs.tia.siemens.cloud/r/en-us/v20/open-user-communication-s7-1200-s7-1500/other-s7-1200-s7-1500/sending-and-receiving-data-via-ethernet-udp-or-fdl-s7-1200-s7-1500/addressing-the-remote-partner-via-taddr_param-s7-1200-s7-1500).
- [Siemens RD_SYS_T](https://docs.tia.siemens.cloud/r/en-us/v20/extended-instructions-s7-1200-s7-1500/date-and-time-of-day-s7-1200-s7-1500/time-of-day-functions-s7-1200-s7-1500/rd_sys_t-read-time-of-day-s7-1200-s7-1500), [conversione LDT](https://docs.tia.siemens.cloud/r/en-us/v20/data-types/data-type-conversion-for-s7-1500-s7-1500/explicit-conversions-s7-1500/date-and-time-of-day-s7-1500/explicit-conversion-of-ldt-s7-1500).
