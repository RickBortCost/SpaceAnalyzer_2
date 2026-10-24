# Space Analyzer V1.29.0 — Fase 1: acquisizione e registrazione

L’analisi eseguita prima delle modifiche è in [ANALISI_FASE1_PRELIMINARE.md](ANALISI_FASE1_PRELIMINARE.md). Questa versione rafforza il backend; JavaScript, CSS, template, grafici e workspace mantengono le funzionalità precedenti.

## Pipeline implementata

```text
ADS / OPC UA / MQTT / S7
    ↓ callback: copia, timestamp, coda limitata
    ↓ buffer indipendente per connessione
Acquisition Engine: sequenze, connessione, discontinuità
    ├─ PRIMA: recorder primario → IPC anonimo → processo writer → blocchi SAC + fsync
    ├─ formato scelto → coda indipendente → writer/convertitore esistente
    ├─ formule → processo calcoli esistente
    └─ distribuzione Live limitata → FIFO, storico Live, UI/API
```

Il consumer MQTT e il passaggio dal consumer di acquisizione al recorder primario applicano una backpressure massima di 500 ms quando il buffer successivo è pieno; la callback di rete continua a copiare nella propria coda limitata. Superato il tempo limite, il sovraccarico è esplicito e la sessione viene arrestata. Non vengono aumentate le capacità per assorbire una raffica.

Il recorder viene alimentato prima dei calcoli e della visualizzazione. Il consumer degli originali non acquisisce il mutex della UI. Il consumer Live può attendere quel mutex senza trattenere il recorder. La chiusura del browser e il pulsante Pausa restano operazioni della vista.

Il processo di scrittura usa pipe anonime locali, senza porte aggiuntive o servizi esterni. Encoding dei batch e driver restano nel processo dell’applicazione: la separazione dal mutex della UI è implementata e verificata, ma CPU/GIL, sistema operativo, storage e rete rimangono risorse condivise. **Non è una garanzia real time né una qualificazione del target ADS industriale.** È stata valutata la separazione completa dei driver: il processo writer è introdotto ora; isolamento dei driver e trasporto a memoria condivisa sono raccomandati per la Fase 2, alla luce del benchmark ADS distinto sotto.

## Moduli

| Modulo | Responsabilità |
|---|---|
| `source/acquisition_engine.py` | Handoff per connessione, dispatch equo, sequenze, recorder prioritario, distribuzione Live separata |
| `source/acquisition_buffers.py` | Code limitate per campioni, byte stimati e batch; overflow terminale esplicito |
| `source/acquisition_health.py` | Contatori, latenze, normalizzazione temporale per diagnostica, eventi SQLite persistenti |
| `source/primary_format.py` | SAC v1, codec tipizzato sicuro, CRC, lettura e recovery |
| `source/primary_recording.py` | Producer limitato, aggregazione batch, IPC e ricevute di durabilità |
| `source/primary_worker.py` | Processo append-only, flush/fsync, finalizzazione e stato su disco |
| `source/primary_recovery.py` | Recovery automatico in background; analisi dei blocchi in processo separato |
| `source/primary_tools.py` | Ispezione, recupero ed esportazione offline |
| `source/durable_recording.py` | Primario prioritario e formato compatibile su coda separata |
| `source/app.py`, `recording_manager.py` | Integrazione, limite Stop basato sulla ricezione, API e sovrapposizione delle sessioni |
| `source/ads_notifications.py`, `ads_client.py` | Sequenze callback, conteggi/range persi, orologi arretrati, byte originali, SumRead configurabile |
| `source/mqtt_source.py`, `s7_client.py`, `s7_catalog.py` | Ricezione MQTT in nanosecondi e valori originali tipizzati prima delle conversioni per la UI |
| `source/source_sessions.py`, `calculation_service.py` | Epoche di riconnessione, eventi sorgente/calcoli e timestamp delle osservazioni |
| Build, launcher e strumenti Windows | V1.29.0 e inclusione dei nuovi worker nel runtime distribuito |

## Buffer e comportamento in sovraccarico

| Stadio | Limite predefinito | Politica |
|---|---:|---|
| Callback ADS | 32.768 campioni / 16 MiB di payload | Conteggio ricevuti/accettati/persi, ultimi 128 range persi; errore e chiusura controllata |
| Ogni connessione Acquisition Engine | 32.768 campioni / 32 MiB stimati / 4.096 batch | Rifiuto esplicito e arresto della registrazione; le altre connessioni restano diagnosticabili |
| Coda primaria | 65.536 campioni / 32 MiB stimati / 4.096 batch | Sessione incompleta, contatori ed evento persistente |
| Pump formato scelto | 65.536 campioni / 32 MiB stimati | Esportazione differita; primario continua; ricostruzione automatica del formato allo Stop |
| Distribuzione Live | 8.192 campioni / 16 MiB stimati | Salta soltanto la vista; contatori e storico Live marcato incompleto |
| Calcoli | 512 batch | Segnalazione della perdita dei derivati; originali continuano |

Le code usano deque limitate e batch; la callback ADS copia i byte del payload prima di restituire il controllo alla DLL. Non vengono preallocate grandi matrici di oggetti Python per tutti i segnali. Il limite byte è conservativo e stimato, non una misura esatta della RAM dell’interprete.

Configurazione backend, senza nuove opzioni grafiche:

- `SPACE_ACQ_QUEUE_SAMPLES`, `SPACE_ACQ_QUEUE_MB`: capacità per connessione all’avvio.
- `SPACE_ADS_QUEUE_SAMPLES`, `SPACE_ADS_QUEUE_MB`: callback ADS.
- `SPACE_ADS_SUM_BATCH`: 1–500 letture per richiesta SumRead; predefinito 32, compatibile con l’implementazione precedente. La capacità del PLC va verificata sul posto.
- `SPACE_UA_QUEUE`: dimensione richiesta per MonitoredItem OPC UA singolo; predefinito 256, minimo 2. Il server può applicare propri limiti.
- `options.primary_flush_ms`: 10–10.000 ms, predefinito 250; `primary_queue_samples` e `primary_queue_mb` nella richiesta Record.

Una coda di connessione in overflow viene riarmata alla registrazione successiva dopo il drenaggio, con nuovo identificativo ed evento storico. I limiti non sono stati aumentati per far passare il benchmark: l’ottimizzazione riusa la stima del batch lungo la pipeline.

## Archivio primario e precisione

Ogni Record conserva anche un originale in `Recordings/.primary/`. È aggiuntivo ai formati selezionati e occupa spazio: non viene eliminato automaticamente. La directory segue quella dell’EXE, mentre eventi e configurazione seguono la directory dati esistente.

SAC v1 è un contenitore binario append-only con magic `SACAP001`, versione, metadata di sessione/canali/connessioni e CRC32 dell’header. I blocchi hanno tipo, progressivo, primo ordinale globale, numero di campioni, lunghezze, CRC32 di header e payload. Il payload è JSON tipizzato compatto, eventualmente compresso con zlib livello 1. Non è un formato numerico colonnare né usa pickle. Il limite di payload decodificato è 32 MiB per blocco.

Sono conservati NodeId e indice del canale, ID della connessione, progressivo del canale, eventuali progressivo/epoca della sorgente, valore originale, valore compatibile con la UI, tipo, qualità, stato, timestamp sorgente/server/ricezione, FILETIME ADS, picosecondi OPC UA e origine dell’osservazione. La ricezione UTC `received_ns` è un intero decimale esatto. Il riferimento monotono interno serve ai limiti Start/Stop; un arretramento UTC è conservato nei campioni e segnalato separatamente negli eventi. La precisione del timestamp OPC UA è quella fornita da asyncua/Python e dagli eventuali picosecondi: non vengono ricostruite cifre già perse dal driver. FILETIME resta una stringa intera a 100 ns; i timestamp MQTT conservano la frazione originaria. `timestamp_ms` mantiene l’asse preesistente per compatibilità e non sostituisce questi campi precisi.

Interi grandi, float non finiti e relativi bit, byte, Decimal, date, UUID, tuple e strutture supportate hanno un codec esplicito. ADS conserva anche i byte originali. Per oggetti OPC UA non rappresentabili dal codec generico viene conservato il Variant binario UA. I tipi non supportati determinano un errore esplicito, senza conversione silenziosa in testo nel primario.

Non viene introdotta interpolazione nel primario. Le notifiche OPC UA e le osservazioni periodiche già usate dall’applicazione sono distinte da `acquisition_origin`; le osservazioni periodiche conservano il timestamp originario del valore. Il resampling esplicito dell’esportazione PDA3 rimane una funzione del formato esterno. Le discontinuità temporali ADS vengono conservate e marcate, senza perdere il resto del batch già estratto dalla coda.

## Flush, Stop e recovery

Una ricevuta di durabilità viene inviata solo dopo `flush` e `fsync`. Le latenze P95/P99 sono pesate per campione sulle ultime 4.096 ricevute: limite superiore del batch, dall’ingresso nel recorder alla conferma di fsync. Non sono una misura end-to-end del PLC; la coda di acquisizione ha metriche separate. Cache del disco, alimentazione e hardware possono limitare la persistenza effettiva.

Start e Stop fissano un limite monotono di ricezione, indipendente dalle correzioni dell’orologio UTC, drena le code già accettate e i calcoli, quindi separa la sessione dal prossimo Record. Non usa più l’ultimo punto del grafico come limite finale. Il pump secondario viene drenato prima di arrestare il writer compatibile. Il file precedente può continuare a finalizzarsi mentre una nuova sessione acquisisce.

Durante la scrittura il file termina con `.sac.part`. Un footer viene scritto e sincronizzato prima del rename a `.sac`; una sessione incompleta viene marcata `.incomplete.sac`. I sidecar di stato riportano ultimo flush, campioni durabili ed errore. Su POSIX viene sincronizzata anche la directory; Python non espone lo stesso fsync della directory su Windows.

All’avvio, il recovery automatico esamina solo i nuovi `.sac.part`. Un lock del sistema operativo esclude i writer attivi e si libera dopo un crash. Il recovery verifica i blocchi in ordine, si ferma al primo blocco non valido e copia il prefisso verificato in **un nuovo file**. Il rapporto distingue byte scartati, campioni recuperati, header non recuperabile e coda RAM non quantificabile. Non inventa i campioni persi dopo l’ultimo blocco verificabile. Il file danneggiato resta disponibile e un marker evita copie ripetute dello stesso contenuto. Non vengono riscritti automaticamente CSV, Parquet, FDA o altre registrazioni preesistenti.

SQLite embedded contiene lo storico degli eventi critici, con coda limitata e conteggio/coalescenza degli eventuali eventi persi. Non contiene tutti i campioni della registrazione primaria. Un errore della persistenza eventi resta visibile nella diagnostica.

## Recupero del formato esterno dopo una raffica o un errore

Il primario fa anche da spool su disco: se la coda del formato scelto si satura, quel writer viene chiuso e il primario continua. Dopo Stop, quando il primario è completo, un processo con priorità inferiore rilegge i blocchi e ricostruisce automaticamente il formato scelto con i writer esistenti. I dati non vengono trattenuti in una coda RAM crescente. Anche un errore scoperto durante la finalizzazione attiva questo recupero.

Durante la ricostruzione è possibile avviare un nuovo Record. La selezione e i file di ogni sessione restano indipendenti. L’API espone `deferred_export_pending`, `external_rebuilt_from_primary` e gli eventi di ricostruzione; il pannello esistente dei salvataggi resta operativo. I vecchi file parziali vengono conservati, insieme al primario. Un errore permanente del disco, del convertitore o della licenza resta esplicito e non trasforma una conversione incompleta in un file dichiarato pronto. Il primario conserva comunque gli originali recuperabili.

Questa procedura è verificata con errori iniettati per CSV, Parquet, FDA/DAS, RBF e PDA3, inclusi errore tardivo e fallimento del processo di esportazione. DAT/iba usa lo stesso percorso, ma richiede la licenza reale: non è stato dichiarato provato su DLL/licenza fisiche. Restano validi i limiti dei formati e il consenso sperimentale PDA3 già previsto; il job riprende le opzioni effettivamente scelte dall’operatore.

## API di diagnostica

- `GET /api/acquisition/health`: connessioni, contatori ricevuti/distribuiti/persi, campioni esclusi dalla registrazione, sequenze mancanti, backlog/limiti, latenze, primario e formati secondari, calcoli, recovery e persistenza eventi.
- `GET /api/acquisition/events?limit=100`: storico persistente degli eventi critici.
- `GET /api/acquisition/captures`: catalogo leggero degli archivi primari.
- `POST /api/acquisition/recover`: avvio di un controllo in background; usa il token locale come gli altri POST.

I rate esposti sono medie dalla creazione della connessione/sessione. `recording_rejected_samples` comprende anche campioni fuori dall’intervallo Record: non va interpretato da solo come perdita. Per una sessione confrontare ricevuti/durabili/inflight del primario e i relativi eventi di errore. Gli overflow delle sorgenti e le porzioni non durabili vengono segnalati separatamente. Non è stata aggiunta una pagina grafica “Acquisition Health”.

## Risultati misurati

Ambiente Linux con Python 3.12; simulazioni e server locali. I JSON in `evidence_v1.29.0/` contengono conteggi, latenze, CPU, RAM osservata, code e limiti della misura. Nessun PLC fisico è stato qualificato.

| Prova | Risultato effettivo |
|---|---|
| Pipeline, 1.000 canali ogni 10 ms, 20 s, quattro protocolli simulati | **2.000.000 prodotti e riletti, zero differenze nei progressivi, circa 100.000 valori/s** |
| Stessa pipeline con consumer Live rallentato di 200 ms | **2.000.000 prodotti e riletti, zero perdite primarie**; la vista salta campioni ed espone il conteggio |
| ABI callback ADS ctypes, 1.000 canali, 1.000 cicli | **1.000.000 prodotti e riletti, zero differenze**, ma 38,94 s: circa **25.682 valori/s**, target 10 ms **non raggiunto** |
| ABI callback ADS ctypes, 64 canali ogni 10 ms, 10 s | **64.000 prodotti e riletti**, circa **6.406 valori/s**, target raggiunto nella simulazione |
| Soak breve continuo, 1.000 canali, 3 s, rotazione ogni secondo | **300.000 prodotti e verificati, 3 file, zero perdite**, acquisizione continua durante rotazione/verifica |

Nel test ADS il generatore, ctypes e il decoder condividono l’interprete: il risultato misura l’intera simulazione e non stabilisce il massimo di TwinCAT reale. Il passaggio della pipeline sintetica non dimostra il target ADS su hardware.

Misure della ripetizione finale della pipeline (flush a 100 ms, primario senza convertitori o client di rete reali):

| Misura | Live normale | Consumer Live rallentato |
|---|---:|---:|
| Campioni verificati | 2.000.000 | 2.000.000 |
| Perdita primaria / differenze nei progressivi | 0 / 0 | 0 / 0 |
| Valori prodotti al secondo | 100.040 | 100.039 |
| CPU processo generatore/engine, su un core | 76,07% | 77,18% |
| CPU writer, totale nella prova | 0,77 s | 0,77 s |
| RSS massima osservata del processo generatore/engine | 18,17 MiB | 23,12 MiB |
| RSS writer osservata all’ultimo flush | 11,01 MiB | 11,52 MiB |
| Latenza recorder P95 / P99 | 105,42 / 106,62 ms | 105,57 / 106,88 ms |
| Byte del file primario | 7.635.239 | 7.640.799 |
| Campioni saltati dalla sola vista | 0 | 1.967.000 |

La CPU comprende generazione e handoff, esclude la successiva rilettura di verifica; la RAM è campionata e non rappresenta l’intera applicazione Flask/PyArrow. La ripetitività dei vettori sintetici favorisce la compressione: questi numeri non prevedono il rapporto di compressione dei dati di produzione. L’intervallo di generazione può avere jitter e recuperi; il rate medio misurato non garantisce una scadenza real time per ogni ciclo.

Il primo benchmark prima del riuso dei budget aveva saturato una coda: il file era stato marcato incompleto e la perdita segnalata. Il risultato è conservato come evidenza dell’iterazione, non presentato come prova riuscita.

Le prove automatiche comprendono: valori tipizzati e timestamp esatti; quattro connessioni; mutex UI occupato mentre MQTT registra; saturazione primaria/acquisizione/Live; writer terminato forzatamente; recupero automatico e CRC troncati; esclusione dei writer attivi dal recovery; epoche e sequenze mancanti; `ENOSPC` iniettato nel writer senza riempire il disco; guasto del formato secondario mentre il primario continua; nuovo Record mentre FDA sta salvando. Le suite con i quattro client reali usano peer locali e verificano anche disconnessioni/riconnessioni e tutti i formati nelle possibilità dei test esistenti.

Verifiche DOM e Chromium esercitano catalogo OPC UA con ADS attivo, zoom Live, marker sull’intera acquisizione, formule e sovrapposizione FDA/CSV. Il test del pacchetto Windows esegue realmente l’EXE e controlla anche la sequenza primaria: consultare il rapporto della release. La scrittura DAT con **licenza iba reale non è stata provata**; fixture e simulazioni non sostituiscono una licenza.

## Avvio e verifiche riproducibili

Per l’operatore Windows: estrarre lo ZIP V1.29.0 in una cartella scrivibile, avviare `Space_Analyzer_V1.29.0.exe`, collegare i protocolli e usare Record/Stop come nelle versioni precedenti. Non servono nuove installazioni Python, Node, Docker o servizi. Restano gli eventuali requisiti del trasporto ADS e della licenza iba già previsti. Vedere [LEGGIMI_AVVIO_WINDOWS.md](LEGGIMI_AVVIO_WINDOWS.md).

Da sorgente, con l’ambiente Python e le dipendenze già previste:

```powershell
python -m pip install -r source/requirements.txt
python source/app.py
python -m unittest discover -s source/tests -p test_acquisition_hardening.py
python tools/run_hardening_checks.py --output hardening-evidence
python source/tests/benchmark_acquisition.py --duration 20 --output idle.json
python source/tests/benchmark_acquisition.py --duration 20 --ui-stress --output stressed.json
python source/tests/benchmark_ads_primary.py --channels 1000 --duration 10 --output ads.json
```

Le suite S7 richiedono `python-snap7==2.0.2` nell’ambiente di sviluppo; DOM usa jsdom e le prove browser usano Playwright/Chromium. Queste sono dipendenze di test, non nuovi servizi o prerequisiti del pacchetto EXE.

Recovery ed esportazione manuale su una copia di lavoro:

```powershell
python source/primary_tools.py inspect "Recordings/.primary/ID.sac"
python source/primary_tools.py recover "Recordings/.primary/ID.sac.part"
python source/primary_tools.py export "Recordings/.primary/ID.sac" --format parquet --output esportazioni
```

`export` usa i writer esistenti e i loro limiti: CSV, Parquet, FDA/DAS, RBF, PDA3 e DAT. Un archivio primario da 1.000 canali non rende automaticamente questi formati o l’attuale UI compatibili con 1.000 canali: selezione e limiti storici restano validi. DAT richiede la licenza effettiva. Non eliminare il primario se l’esportazione segnala un errore.

## Test separato di 24 ore

Preparato ma **non eseguito per 24 ore** in questa consegna. Lo smoke di 3 secondi verifica il medesimo runner, inclusa la rotazione sovrapposta. Il generatore continua mentre un thread prepara il prossimo processo recorder e un altro verifica i file chiusi; non si ferma per analizzare ogni segmento.

```powershell
python source/tests/soak_acquisition.py --duration 86400 --channels 1000 --rotate-seconds 60 --directory D:/SpaceAnalyzerSoak --report soak24h.json --delete-verified
```

Usare una directory di prova dedicata. `--delete-verified` elimina solo i file sintetici verificati di questo test per limitare l’uso disco; ometterlo per conservarli, dimensionando lo spazio. Il rapporto distingue durata richiesta, tempo reale, jitter del generatore, campioni prodotti/verificati e errori. L’audit per segmento è in `soak24h.segments.jsonl`.

## Limiti e Fase 2 raccomandata

1. Qualificare ADS su Windows con PLC, router, rete e disco reali: 1.000 variabili a 10 ms, perdita end-to-end, comportamento dopo reboot e cambio simboli. Il target non è garantito da questa consegna.
2. Isolare i driver in processi dedicati e confrontare IPC binario/memoria condivisa con il costo attuale di Python, callback e codec. Valutare C#/.NET solo sulla base di queste misure.
3. Eseguire il soak di 24 ore sull’hardware destinato alla produzione, anche con carichi reali UI/FFT/import e storage lento. I test brevi non dimostrano stabilità di lungo periodo.
4. Aggiungere rotazione/retention del primario configurabile, indici temporali per ricerche e accesso/esportazione progressiva, senza legare l’acquisizione a conversioni o letture storiche.
5. Qualificare e ottimizzare ciascun formato esterno con i file e le licenze effettive. I writer storici possono essere il collo di bottiglia; il primario rimane l’originale recuperabile.
6. Integrare in una futura pagina Acquisition Health le metriche già esposte, con storico dei rate e allarmi operatore. Nessun redesign è incluso nella Fase 1.

Nessuna migrazione obbligatoria dei workspace o dei file esistenti. Conservare una copia dei dati operativi quando si cambia versione, come per qualsiasi aggiornamento dell’applicazione.
