# Acquisizione simultanea — V1.25.1

La V1.25 estende la V1.24: OPC UA, ADS, MQTT e S7 possono funzionare insieme, in qualsiasi sottoinsieme. Nessun protocollo è obbligatorio. Un protocollo può continuare a funzionare quando gli altri non sono raggiungibili.

## Uso

Nella V1.25.1 è corretto il cambio di Catalogo durante l'acquisizione simultanea. Chiudere la versione precedente con **Chiudi applicazione** prima di avviare il nuovo EXE.

Esempio OPC UA + ADS:

1. Aprire **Connessione**, scorrere sotto i campi OPC UA e attivare **Acquisizione simultanea**.
2. In **Protocolli e periodi (ms)** spuntare **OPC UA** e **ADS**.
3. Usare **Fonte dati live** per compilare prima i parametri OPC UA e poi quelli ADS; premere **Connetti e leggi** una volta.
4. In **LIVE → Address Space · scansione → Catalogo** scegliere **OPC UA**: il campo deve mostrare la radice OPC UA salvata, normalmente `i=85`, e l'etichetta **Nodo di partenza**. Premere **Esplora rami** o **Scansiona tutto**, quindi selezionare le variabili.
5. Passare a **Catalogo → ADS**, esplorare o scansionare e selezionare altre variabili: i segnali OPC UA restano acquisiti. La radice ADS, normalmente `ads:/`, è separata da quella OPC UA.
6. Premere **● Record**, scegliere uno dei sei formati e **Avvia registrazione**. **■ Stop** finalizza la registrazione comune. È possibile cambiare Catalogo durante Record; per modificare i segnali bisogna prima fermare Record.

Nel popup Connessione attivare **Acquisizione simultanea**, selezionare i protocolli e impostare i rispettivi periodi. Il menu Fonte dati permette di configurare ciascun protocollo con i campi già presenti. I parametri, i protocolli abilitati e i periodi vengono salvati nelle impostazioni e nel workspace; le password già contrassegnate «solo sessione» continuano a non essere salvate.

Premere Connetti. Una sorgente non raggiungibile segnala il proprio errore e segue la propria politica di riconnessione; le altre restano attive. I comandi Riconnetti sorgente e Disconnetti sorgente agiscono soltanto sulla riga indicata. Il comando Disconnetti generale chiude tutte le sessioni e finalizza la registrazione. La selezione dei protocolli e i parametri di trasporto si modificano dopo la disconnessione generale.

Cambiare Catalogo o Fonte dati durante una sessione mista cambia la navigazione: non cancella la selezione comune, i buffer o i grafici. È possibile selezionare fino a 64 segnali complessivi. Durante Record la selezione resta bloccata. MQTT registra ogni pubblicazione ammessa dalle impostazioni, indipendentemente dal periodo indicativo; Autoscan continua a interrogare soltanto il catalogo osservato ogni secondo.

La configurazione persistente aggiunge `enabled_sources` e `source_intervals`. I vecchi profili `mqtt_parallel` vengono interpretati dal nuovo gestore e non richiedono una connessione OPC UA funzionante per mantenere MQTT attivo. La modalità con una sola sorgente conserva i precedenti client e modalità.

## Tempi, identità e qualità

Le sessioni miste usano `timestamp_ms` come asse comune **PC UTC di ricezione/completamento lettura**. Questo rende confrontabili le sorgenti senza presupporre PLC sincronizzati. Non è una promessa di campionamento hardware simultaneo né di prestazioni real time.

Gli identificativi conservano il protocollo: NodeId OPC UA, `ads:…`, `mqtt:topic#pointer`, `s7:…`. Le nuove colonne `source_protocol`, `acquisition_timestamp_ms` e `acquisition_origin` rendono esplicita la provenienza. Restano presenti `source_timestamp`, `server_timestamp`, `local_timestamp`, `ads_filetime`, qualità, status e tipo, quando disponibili. I timestamp PLC non vengono ricostruiti se il protocollo non li fornisce.

OPC UA conserva le notifiche ricevute e le distingue dagli snapshot periodici (`notification` / `subscription_snapshot`). Gli snapshot mantengono il comportamento precedente sui valori costanti e forniscono un gruppo temporale comune alle formule OPC UA con allineamento esatto. Le notifiche restano nell'archivio e non alimentano una seconda volta tali formule. ADS notifiche e S7 UDP mantengono i rispettivi timestamp originali, pur usando la ricezione PC sull'asse comune della sessione mista.

I campioni ricevuti fuori ordine vengono conservati nella registrazione. Il Live evita di inserire punti arretrati nel buffer ordinato e il contatore Tardivi ne rende esplicita l'esclusione dalla vista. La rilettura/confronto ammette l'ordine di arrivo dell'archivio misto. La perdita di connessione aggiunge un campione Bad per i soli segnali della sorgente coinvolta.

Le code restano limitate. Overflow, perdite rilevate dai contatori UDP, errori del worker o mancato drenaggio rendono la registrazione incompleta/errore, conservando gli originali e i file parziali; non vengono pubblicati come file completi. Il pannello delle sorgenti mostra stato, campioni, campioni tardivi e perdite rilevate. Nessun protocollo può recuperare pubblicazioni o telegrammi che non sono arrivati al PC: i gap sono segnalati, non ricostruiti.

## Formati

| Formato | Sessione mista e limiti |
| --- | --- |
| CSV | Righe asincrone con tutti i campi originali; nessun ricampionamento. |
| Parquet | Righe asincrone, profili ZSTD/Snappy esistenti, metadati dei protocolli e colonne di provenienza. |
| FDA/DAS4 | Conversione dopo Stop con ordinamento su disco. Timestamp espliciti in ms, canali Int16 scalati e canali qualità Q. Aggiornamenti disgiunti condividono un frame; segnali assenti hanno Q=0, senza riempimento. Il Parquet associato conserva precisione e tempi originali. |
| RBF v3 | Frame asincroni con secondi/nanosecondi e canali Q. L'intervallo nell'header è indicativo. Float32/Boolean/stringhe secondo i limiti del codec esistente; valori/stringhe non rappresentabili producono un errore e conservano il Parquet. |
| DAT/PDA3 | Proiezione causale su griglia a 10 ms: ultimo campione Good numerico non successivo al punto di griglia, mantenuto per massimo tre periodi configurati della relativa sorgente. Prima del primo campione, dopo scadenza, con Bad/Uncertain/null, non numerici o overflow Float32 si scrive NaN. A parità di timestamp prevale l'ultimo aggiornamento nell'archivio; gli originali restano tutti nel Parquet. Il contesto della finestra precedente mantiene i valori ancora validi. Le statistiche dei vuoti rimangono corrette anche con suddivisione automatica. |
| DAT/iba | Conversione originale con la DLL/bridge esistenti: canali valore, qualità Q e offset temporale T per segnale, senza eliminare gli aggiornamenti asincroni. Il calendario/griglia nativi e T restano soggetti alla precisione Float32 e ai limiti reali della DLL. Richiede Windows e licenza di scrittura verificata; nessuna licenza viene simulata nel programma. |

Tutti i formati derivati conservano anche il Parquet originale. Non eliminare i metadati o gli originali dopo la conversione. Per PDA3 i nomi/calendario interni del modello restano sperimentali: Space Fieldbus Analyzer usa la mappatura e i tempi reali dei manifest JSON. La compatibilità con ibaAnalyzer deve essere verificata nell'ambiente destinatario; il round trip interno non la dimostra. La qualità PLC completa rimane nell'archivio originale; il DAT PDA3 distingue numeri e NaN, senza inventare un canale qualità nativo.

## Arresto e riavvio

Stop acquisisce un limite temporale, drena le code di ricezione ammesse e le formule, quindi finalizza l'archivio. Eventuali conversioni lunghe restano in stato «stopping» e continuano nei worker esistenti. Non avviare un'altra registrazione prima che la precedente abbia concluso la finalizzazione. Una richiesta Start duplicata viene rifiutata senza cambiare i limiti della registrazione attiva. Svuotare la vista Live invalida i callback di visualizzazione, non i campioni già ricevuti per Record.

La chiusura generale arresta anche le sessioni indipendenti; i job di recupero PDA3 conservano la politica di proiezione e gli archivi di contesto. Un'interruzione forzata del processo o del sistema operativo può lasciare file parziali: non vengono rinominati come completati.
