# Space Fieldbus Analyzer 1.13.0 — Siemens S7 classico

Questa guida riguarda la modalità **S7 classico**. Per la lettura simbolica dei DB e il ricevitore UDP Direct vedere [GUIDA_S7_SIMBOLICO_UDP.md](GUIDA_S7_SIMBOLICO_UDP.md), con catalogo JSON ed esempi TIA inclusi.

## Collegamento

1. Estrarre tutto lo ZIP in una cartella locale scrivibile e avviare `Space_Analyzer_V1.14.0.exe`. Chiudere la precedente istanza prima di avviare quella nuova.
2. Aprire **Connessione**, disconnettere l'eventuale sorgente attiva e scegliere **S7 · Siemens ISO-on-TCP**.
3. Impostare IP del PLC, porta TCP (normalmente **102**), rack e slot. I preset sono punti di partenza: S7-1200/1500 rack 0/slot 1, S7-300 rack 0/slot 2, S7-400 rack 0/slot 3; verificare sempre la configurazione hardware, soprattutto usando CP di comunicazione.
4. Nel catalogo aggiungere nome, indirizzo assoluto e tipo; oppure importare un JSON come `ESEMPIO_VARIABILI_S7.json`. Gli indirizzi dell'esempio sono dimostrativi, non vengono scritti nel PLC.
5. Premere **Test connessione S7**: verifica TCP/COTP, negozia la PDU e prova la prima variabile del catalogo. Con catalogo vuoto verifica solo il collegamento. Il risultato non certifica la validità degli altri indirizzi.
6. Premere **Connetti**, poi **Elenca variabili configurate** o **Esplora rami** nel pannello di scelta segnali. Selezionare le variabili per grafici e registrazioni. È disponibile anche l'aggiunta diretta, ad esempio `DB1.DBD0:REAL`.

La connessione e il catalogo vengono salvati nelle impostazioni esistenti. Grafici e selezioni hanno profili separati per OPC UA, ADS e S7; una sola sorgente è attiva alla volta. Le modifiche al catalogo non cancellano o rinominano automaticamente segnali già selezionati: rimuoverli/riaggiungerli se necessario. È necessario disconnettersi e fermare Record prima di modificare il catalogo o i parametri di connessione.

## Protocollo e requisiti CPU

È **S7comm classico ReadVar su ISO-on-TCP**, non un controller PROFINET IO e non PROFIBUS. Il client invia soltanto richieste di apertura/negoziazione e lettura; non scrive valori, non scarica programmi e non comanda RUN/STOP. Nessuna DLL Snap7 o installazione TIA Portal è richiesta sul PC di utilizzo.

Per CPU S7-1200/1500 configurare l'autorizzazione all'accesso PUT/GET da partner remoti e utilizzare DB standard **non ottimizzati**, nei limiti previsti dal modello/firmware e dalla politica di sicurezza dell'impianto. Le protezioni e la disponibilità del servizio dipendono dalla CPU: non disabilitare indiscriminatamente le protezioni. Le modifiche al progetto PLC devono essere valutate ed eseguite dal responsabile autorizzato, non dal client.

La modalità classica non supporta S7CommPlus, autenticazione CPU, accesso simbolico a DB ottimizzati, scoperta dei nomi del progetto, timer/contatori S5, strutture automatiche, array automatici, WSTRING o date/ora Siemens. Per le nuove funzioni simboliche scegliere l'altra modalità nel popup. Nel catalogo classico ogni elemento numerico/booleano deve essere definito per indirizzo e tipo. S7 classico non cifra il traffico: utilizzarlo soltanto su rete industriale autorizzata e segmentata, non esporre TCP 102 a Internet.

Documentazione Siemens di riferimento:

- [GET e requisiti S7-1200/1500](https://docs.tia.siemens.cloud/r/en-us/v20/s7-communication-s7-1200-s7-1500/get-read-data-from-a-remote-cpu-s7-1200-s7-1500)
- [Limitazione dei servizi di comunicazione S7-1500](https://docs.tia.siemens.cloud/r/en-us/v20/functional-description-of-s7-1500-cpus-s7-1500/setting-the-operating-behavior-s7-1500/protection-security-s7-1500/restriction-of-communication-services-s7-1500)

## Indirizzi e tipi

| Area | Esempio | Tipo |
|---|---|---|
| Data block | DB1.DBX4.0 | BOOL |
| Data block | DB1.DBW6 | INT / UINT / WORD |
| Data block | DB1.DBD8 | REAL / DINT / UDINT / DWORD / TIME |
| Data block, 8 byte | DB1.DBD16 | LREAL / LINT / ULINT / LWORD |
| Merker | M0.0, MB2, MW4, MD8 | BOOL, BYTE, INT, REAL |
| Ingressi | I0.0, IB2, IW4, ID8 | BOOL, BYTE, INT, REAL |
| Uscite, solo lettura | Q0.0, QB2, QW4, QD8 | BOOL, BYTE, INT, REAL |
| Stringa Siemens | DB1.DBB32 | STRING, lunghezza dichiarata 1–254 |

E/A sono accettati come alias di I/Q. B è largo 1 byte (BYTE/USINT/SINT), W 2 byte, D 4 byte o 8 byte se il tipo esplicito è a 64 bit. Non esiste deduzione del tipo da parte del PLC. Per le STRING configurare la capacità dichiarata nel DB, non la lunghezza del testo attuale; la lettura include i due byte di intestazione Siemens. TIME è un intero signed a 32 bit in millisecondi. La codifica STRING è Windows-1252.

Il catalogo supporta fino a 5000 definizioni. Rimane il limite applicativo esistente di **64 segnali acquisiti contemporaneamente**, indipendente dalla dimensione del catalogo. Alias visualizzati nell'elenco; indirizzo completo disponibile nel tooltip. Importazione JSON additiva: indirizzi/tipi duplicati sono rifiutati.

## Parametri avanzati

- Risorsa S7 Basic / PG / OP: dipende dalle connessioni disponibili e dalla configurazione CPU.
- TSAP locale: quattro cifre esadecimali, default `0100`.
- TSAP remoto vuoto: calcolato da risorsa, rack e slot. Se esplicito, prevale su tali campi.
- Timeout 100–10000 ms; default 3000 ms.
- PDU richiesta 240/480/960 byte; la dimensione effettiva è negoziata. Le richieste ReadVar sono raggruppate entro i limiti PDU e 20 elementi per richiesta. Stringhe lunghe possono richiedere più letture.

## Dati, qualità, tempo e prestazioni

I segnali S7 usano gli stessi grafici Live, pause, marker, FFT, variabili calcolate e Record CSV/Parquet degli altri protocolli. I file sono nella cartella `Recordings` accanto all'EXE, con prefisso `S7_` e metadati `source_protocol=s7`. Restano disponibili compressione binaria, rotazione, riproduzione storica ed export dell'intervallo.

ReadVar **non fornisce timestamp originali PLC**: SourceTimestamp e ServerTimestamp restano vuoti; LocalTimestamp indica il completamento locale dell'intero gruppo di letture. Non viene garantita la simultaneità dei valori o uno snapshot coerente del ciclo PLC, soprattutto fra più PDU o stringhe suddivise. Per coerenza di ciclo occorre un buffer/snapshot progettato nel PLC.

Good significa lettura S7 riuscita, Bad significa errore del dato/lettura. Lo stato RUN/STOP della CPU non viene interrogato: Good non dimostra che il programma PLC stia aggiornando il valore. Un indirizzo errato non interrompe le letture valide nello stesso gruppo. Una perdita di connessione produce discontinuità e segue la riconnessione automatica già disponibile; non vengono inventati campioni mancanti.

Il polling è asincrono e separato dall'aggiornamento grafico. Il periodo richiesto, anche **10 ms**, non è una garanzia: dipende da CPU, carico, rete, PDU e numero/dimensione degli indirizzi. Non sono notifiche ADS né acquisizione a ciclo deterministico. Int64/UInt64 oltre la precisione JavaScript sono conservati esatti nel campo testuale; il grafico e i calcoli numerici usano Float64 e possono approssimarli.

## Diagnostica e verifiche

- Timeout: controllare rete, IP, TCP 102, firewall, rack/slot e TSAP; poi carico/risorse CPU.
- Accesso negato: verificare autorizzazioni del servizio e accesso PUT/GET con il responsabile PLC.
- DB/area assente o fuori intervallo: verificare numero DB, accesso standard, offset, lunghezza e tipo.
- Catalogo vuoto: S7 non importa automaticamente i nomi del progetto. Aggiungere le definizioni o importare il JSON.
- Valori fermi ma Good: verificare programma/stato CPU e indirizzo corretto; ReadVar da solo non certifica RUN.

Test funzionali eseguiti su Linux contro il server nativo Snap7 indipendente, tramite `python-snap7==2.0.2` **solo nei test**. Nessuna dipendenza Snap7 aggiunta al prodotto. Test dell'interfaccia con DOM/HTTP reali e canvas simulato; non costituiscono collaudo visuale né esecuzione dell'EXE su Windows. Prima dell'uso operativo verificare sul PLC Siemens reale e misurare il periodo ottenuto.
