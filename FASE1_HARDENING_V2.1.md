# Space Analyzer 2.1 — Fase 1: hardening acquisizione e registrazione (seconda iterazione)

Questa iterazione parte dalla 2.0.0. La V1.29.0 aveva già introdotto la base della Fase 1, descritta in [FASE1_ACQUISIZIONE_REGISTRAZIONE.md](FASE1_ACQUISIZIONE_REGISTRAZIONE.md):

- buffer per connessione;
- recorder primario SAC con checksum, flush e recovery;
- eventi SQLite;
- API `/api/acquisition/*`.

Qui non si riscrive quella base: si correggono le criticità ancora aperte, misurate prima di intervenire. Interfaccia, grafici, strumenti di analisi, workspace e formati esterni non cambiano.

## 1. Analisi preliminare

Analisi eseguita sulla 2.0.0 in questo ambiente: Linux, 4 core, Python 3.13 (la stessa versione dell'EXE Windows).

| # | Criticità rilevata | Evidenza |
|---|---|---|
| 1 | **ADS: una callback Python per ogni campione.** Il thread del router TcAdsDll deve acquisire il GIL per ogni notifica, in concorrenza con i thread consumer. Con 1.000 simboli a 10 ms le callback sono 100.000 al secondo e il thread del router accumula ritardo. | Benchmark ctypes, 1.000 canali: **9.409 valori/s** (target 100.000), CPU 56,6 s per 500.000 campioni |
| 2 | **Perdite ADS a monte della callback non rilevabili.** Il progressivo ADS viene assegnato alla ricezione, quindi le notifiche perse dal router, dalla DLL o dal PLC non lasciano buchi nella sequenza: la perdita è silenziosa. | Analisi di `ads_notifications._callback` |
| 3 | Percorso critico: costo per campione elevato in `drain` ADS, `size_hint` e `primary.enqueue`. | Profilo: 27 µs/campione a thread singolo (`drain` 12,4; `submit` 4,5; `enqueue` 4,5; sender 3,4) |
| 4 | Ricevute del writer lette **un byte per system call** (stdout del writer senza buffer). | 2 s di CPU per poche centinaia di righe di ricevuta |
| 5 | Ciclo consumer ADS: watchdog e `selection()` O(canali) eseguiti a ogni piccolo batch. | 1.000 canali: decine di µs per iterazione, ripetuti centinaia di volte al secondo |
| 6 | Serializzazione JSON dei blocchi primari nel processo di acquisizione, cioè sotto lo stesso GIL dei driver. | 2,5 µs/campione |
| 7 | Gli eventi critici (overflow, buchi di sequenza, perdite sorgente) sono solo nello storico SQLite globale, non nel file di registrazione a cui si riferiscono. | Il formato SAC prevede blocchi `EVENT`, ma nessuno li scriveva |
| 8 | Le metriche di rate sono medie dall'avvio: uno stallo recente non si vede. | `received_per_second` = totale / durata |
| 9 | Cinque suite di regressione fallivano già sulla 2.0.0 e sulla V1.29.0 in questo ambiente. | Asserzioni dipendenti dai tempi sullo Stop asincrono (4 suite) e certificati TLS di test non conformi a RFC 5280, rifiutati da Python 3.13 (1 suite) |

Dall'analisi della 2.0.0 risulta anche quanto segue:

- **Buffering e backpressure:** già limitati e attribuiti per connessione.
- **Registrazione:** è indipendente dal mutex della UI.
- **Stop:** usa un fence monotono.
- **Acquisizione da più protocolli:** passa da code separate.

Questi punti non richiedevano una riprogettazione.

## 2. Moduli modificati

| Modulo | Modifica |
|---|---|
| `source/ads_notifications.py` | Callback più leggera (indirizzi grezzi, un solo `string_at` per l'intestazione e uno per i dati). **Notifiche a blocco** opzionali. Rilevamento dei **cicli PLC mancanti** per canale o per blocco. `drain` con contabilità per notifica e cache dei timestamp. Ciclo consumer con time slice e watchdog ogni 100 ms. |
| `source/ads_client.py` | `decode_value` con `struct.Struct` in cache: stessi risultati. |
| `source/acquisition_engine.py` | Controlli dell'orologio di ricezione e conversione del timestamp sorgente eseguiti una volta per valore distinto nel batch, con contatori identici. Rate su finestra mobile. |
| `source/acquisition_buffers.py` | `size_hint`: percorso rapido per i tipi scalari, risultato identico. |
| `source/acquisition_health.py` | `RateWindow` (rate istantanei), listener degli eventi, import fuori dal percorso per campione. |
| `source/primary_recording.py` | `annotate()` (eventi nel file SAC), costruzione diretta delle righe, IPC `marshal`, lettura delle ricevute con buffer, rate recenti. |
| `source/primary_worker.py` | Decodifica `marshal` → JSON: bytes del blocco identici a prima. |
| `source/primary_format.py` | `inspect()` riporta gli eventi; nuovo `iter_events()`. |
| `source/durable_recording.py`, `source/recording_manager.py` | `annotate()` verso la sola sessione in acquisizione. |
| `source/app.py`, `source/source_sessions.py` | Tee degli eventi verso la registrazione attiva; evento persistente `ads_source_gap`. |
| `source/tests/test_phase1_integrity.py` | Nuova suite di 12 test. |
| `source/tests/benchmark_ads_primary.py` | Opzione `--grouping`; backpressure allineata all'applicazione (0,5 s). |
| `source/tests/test_ads.py`, `test_calculation_ads.py`, `test_calculation_api.py`, `test_s7.py`, `test_mqtt.py` | Attesa della finalizzazione asincrona dopo Stop; certificati di test conformi RFC 5280. Nessun test saltato o indebolito: le verifiche sui dati restano invariate. |
| `tools/run_hardening_checks.py` | Include la nuova suite. |

Nessuna nuova dipendenza: `marshal`, `struct`, `ctypes`, `sqlite3` e `zlib` sono nella libreria standard già inclusa nell'EXE. Non servono servizi esterni, Docker o database.

## 3. Pipeline di acquisizione

```text
PLC ADS ─ router/TcAdsDll ─► callback (copia bytes + orologi; 1 per simbolo, oppure 1 per blocco)
                               │ coda limitata per connessione, perdite contate e attribuite
                               ▼
                         consumer ADS: decode, FILETIME, cicli mancanti per canale/blocco
OPC UA / MQTT / S7 ──────────► │
                               ▼
              Acquisition Engine: buffer per connessione, sequenze, orologi, rate recenti
                ├─ PRIMA il recorder primario ─► marshal su pipe anonima ─► processo writer
                │                                   (JSON + CRC + zlib + fsync, file SAC)
                ├─ formato scelto (CSV/Parquet/FDA/RBF/PDA3/DAT) su coda separata
                ├─ calcoli
                └─ distribuzione Live con perdita ammessa (solo vista)
EventStore (SQLite) ──tee──► blocchi EVENT nel SAC della sessione in acquisizione
```

**Notifiche a blocco ADS (opzionali).** Le variabili selezionate nello stesso index group e in un intervallo di al massimo 64 KiB vengono sottoscritte con **una sola notifica ciclica sull'area di memoria**. A ogni ciclo PLC arriva una callback, da cui il consumer estrae i singoli canali. Tutti i valori di un blocco provengono dallo stesso ciclo e hanno lo stesso timestamp PLC. Regole di sicurezza:

- sono esclusi i simboli a bit, i riferimenti, i puntatori a interfaccia e i metodi;
- un blocco richiede almeno due membri;
- l'area viene **letta una volta** prima della sottoscrizione; in caso di errore quei canali ricadono sulle notifiche per handle attuali (`fallback_members` nella diagnostica);
- dopo un cambio di simboli (online change) la sottoscrizione viene rifatta come prima.

**Attivazione:** variabile d'ambiente `SPACE_ADS_NOTIFY_GROUPING=1`, oppure chiave `ads_notify_grouping: true` nella configurazione. **Predefinito: disattivo**, perché il comportamento su un PLC reale non è stato verificato. La diagnostica `notify_layout` riporta blocchi, membri, singoli e fallback.

**Perdite a monte della callback.** Per ogni canale, oppure per ogni blocco, il periodo viene appreso come il minimo delta FILETIME positivo, mai inferiore al ciclo richiesto. Così il ciclo arrotondato al task PLC (per esempio 12 ms invece di 10) non genera falsi allarmi. Un delta oltre 1,5 periodi conta i campioni mancanti. Il risultato:

- compare in `source_gap_samples` / `source_gaps` della diagnostica;
- genera l'evento persistente `ads_source_gap`;
- viene scritto nel file SAC.

La registrazione **continua**, perché la perdita è a monte del sistema, ma è documentata. Limiti:

- i primi 4 delta di ogni flusso servono ad apprendere il periodo e non vengono valutati;
- i silenzi con PLC fuori da RUN sono marcati `plc_not_run`.

## 4. Formato di registrazione e recovery

Il formato SAC resta alla **versione 1**, invariato su disco: i file 2.0 sono leggibili come prima. Cambia quanto segue.

- **Blocchi `EVENT` effettivamente scritti.** Ogni evento critico emesso durante una registrazione viene copiato nel file della sessione in acquisizione, ordinato rispetto ai campioni (`after_sample` = campioni già accettati). Gli eventi coperti sono: overflow, buchi di sequenza, salti dell'orologio, perdite sorgente, connessioni e disconnessioni, cicli ADS mancanti, guasti del formato esterno. L'annotazione non blocca e non fa fallire la sessione. Se la coda è piena, l'evento resta in SQLite e viene contato in `annotations_lost`.
- `inspect` riporta `events_count` ed `events` (massimo 1.000); `iter_events(path)` li legge in ordine. Gli eventi sopravvivono al recovery dopo un crash: la copia del prefisso verificato li conserva.
- **IPC verso il writer.** Il processo di acquisizione invia i blocchi in `marshal` sulla pipe anonima privata (`ipc_encoding` nella configurazione del writer). Il writer produce il JSON del blocco con **bytes identici** al formato precedente (verificato) su un altro core. CRC, compressione, fsync, finalizzazione e recovery non cambiano.

## 5. Benchmark e test effettivamente eseguiti

Ambiente: Linux, 4 core, Python 3.13.17, DLL e PLC simulati. **Nessun PLC fisico, router TwinCAT o hardware Windows qualificato.** Rapporti JSON in [`evidence_v2.1.0/`](evidence_v2.1.0/).

### ADS: ABI ctypes simulata, decode reale, engine e primario fino alla rilettura

Ogni prova confronta tutti i valori riletti dal file SAC con quelli prodotti.

| Configurazione | Prodotti → registrati | Differenze | Valori/s sostenuti | Esito |
|---|---|---:|---:|---|
| 2.0.0, 1.000 simboli, notifica per simbolo | 500.000 → 500.000 | 0 | 9.409 | Target non raggiunto |
| 2.1, 1.000 simboli, notifica per simbolo | 500.000 → 500.000 | 0 | **15.236** (+62%) | Target non raggiunto; CPU da 56,6 a 35,5 s |
| 2.1, 200 o 300 simboli, notifica per simbolo | tutti | 0 | ~16.000 | Limite strutturale della callback per campione: 20.000 valori/s non raggiunti |
| 2.1, 500 simboli a blocco, 10 s | 500.000 → 500.000 | 0 | 50.047 | **PASS** |
| 2.1, 600 simboli a blocco, 20 s | 1.200.000 → 1.200.000 | 0 | 60.028 | **PASS**; latenza primaria P95/P99 108,9 / 121,3 ms; RSS 123 MiB |
| 2.1, 800 simboli a blocco, 20 s | 335.200 → 279.200 | 0 | 80.128 | Overflow **esplicito** del buffer di acquisizione: sessione arrestata, perdita contata ed evento registrato |
| 2.1, 1.000 simboli a blocco, 10 s | 1 callback per ciclo, zero perdite ADS | 0 | 100.000 generati | Overflow **esplicito** dopo alcuni secondi: l'elaborazione Python a valle non sostiene 100.000 valori/s |

Il **benchmark di riferimento (1.000 variabili a 10 ms) non è raggiunto** nel percorso ADS completo. Con le notifiche a blocco la callback non è più il collo di bottiglia, mentre resta il limite di un solo interprete Python per decode, engine e serializzazione: circa 60.000 valori/s sostenuti in questa macchina. In ogni prova fallita la perdita è stata esplicita, attribuita alla connessione e documentata, mai silenziosa.

### Pipeline multi-protocollo simulata (`benchmark_acquisition.py`, quattro protocolli, 1.000 canali a 10 ms, 20 s)

| Misura | Live normale | Live rallentata (UI sotto carico) |
|---|---:|---:|
| Prodotti → registrati | 2.000.000 → 2.000.000 | 2.000.000 → 2.000.000 |
| Differenze nei progressivi | 0 | 0 |
| Valori/s | 100.039 | 100.034 |
| CPU del processo di acquisizione, su un core | 65,0% (V1.29: 76,1%) | 65,5% (V1.29: 77,2%) |
| CPU del writer nella prova | 5,9 s (ora include la codifica JSON) | 6,0 s |
| Latenza recorder P95 / P99 | 108,9 / 115,4 ms | 111,8 / 116,4 ms |
| Rate durabile su finestra mobile | 99.802/s | 99.740/s |

### Altre prove

- **Regressione completa** (`tools/run_hardening_checks.py`): **241 test in 29 suite**, tutti superati in esecuzioni ripetute (salvo l'intermittenza preesistente descritta sotto), comprese le 5 suite che fallivano già sulla 2.0.0 e la nuova `phase1_integrity`. Log in `evidence_v2.1.0/*.log`.
- **`test_phase1_integrity.py` (12 test):**
  - pianificazione dei blocchi ed esclusioni;
  - 50 canali più un bit, con una callback per ciclo e valori esatti;
  - fallback se l'area non è leggibile;
  - raggruppamento disattivo per impostazione predefinita;
  - ciclo PLC mancante contato per canale;
  - periodo arrotondato dal task senza falsi positivi;
  - evento persistente `ads_source_gap`;
  - eventi nel SAC ordinati e conservati dopo un **crash forzato del writer** e il recovery;
  - annotazioni che non bloccano la sessione;
  - tee degli eventi verso la sola sessione attiva;
  - finestra mobile dei rate;
  - formattazione UTC esatta.
- **Equivalenza:** 4.168 righe casuali producono righe primarie identiche campo per campo tra la vecchia e la nuova `enqueue`. `size_hint` dà risultati identici. I blocchi JSON prodotti dal writer via `marshal` sono identici byte per byte.
- **Soak breve** (`soak_acquisition.py`, 30 s, 1.000 canali, rotazione ogni 10 s): 3.000.000 prodotti e verificati in 3 file, zero mancanti.
- **Già coperti dalle suite esistenti, rieseguite:** saturazione controllata, crash del recorder e recovery, `ENOSPC` iniettato, interruzione e ripristino delle connessioni, UI sotto carico, registrazione simultanea dei quattro protocolli.
- **Test di 24 ore:** il runner è pronto (sezione 8) ma **non è stato eseguito** in questa consegna.
- **Intermittenza preesistente.** `test_source_sessions.test_queue_overflow_and_late_originals_are_explicit` verifica che un errore MQTT impostato subito prima di Stop porti la registrazione allo stato `error`. È fallito 2 volte su 12 sul codice 2.0.0 invariato e 1 volta su circa 35 esecuzioni sul codice 2.1. In quei casi la registrazione chiude come `stopped`. Non si riproduce eseguendo il singolo test (40 esecuzioni) né con tracciamento sulla suite completa (14 esecuzioni). La causa non è ancora individuata: il caso riguarda l'attribuzione di un errore sorgente alla registrazione, quindi va chiarito. Il test non è stato modificato né disattivato. L'esecuzione di regressione finale lo riporta come fallito nel log `evidence_v2.1.0/source_sessions.log`; le 12 esecuzioni successive della suite sono passate.

## 6. Limiti prestazionali ancora presenti

1. **1.000 variabili ADS a 10 ms non sono sostenute** nel percorso ADS completo in un solo processo Python (circa 60.000 valori/s con notifiche a blocco, circa 16.000 per simbolo). Servono più processi o codice nativo: vedi la Fase 2.
2. Le notifiche per simbolo, che restano il comportamento predefinito, hanno un tetto di circa 16.000 valori/s per costo del GIL per callback, indipendente dal numero di canali.
3. Le notifiche a blocco richiedono variabili contigue in memoria PLC, per esempio nella stessa GVL o struttura. Non sono state provate su TwinCAT reale.
4. Il rilevamento dei cicli mancanti presuppone la modalità ciclica (SERVERCYCLE, l'unica usata), salta i primi 4 delta di ogni flusso e non distingue la causa: router, rete o PLC.
5. I formati esterni (CSV, Parquet, FDA, RBF, PDA3, DAT) e la Live non sono qualificati a 100.000 valori/s. Il primario fa da spool e la ricostruzione dopo Stop resta il meccanismo previsto (V1.29).
6. La latenza riportata è dall'ingresso nel recorder alla conferma `fsync`, non dal PLC. La cache del disco e l'hardware possono limitare la persistenza effettiva.

## 7. Compatibilità e migrazioni

- Nessuna migrazione: workspace, impostazioni, file SAC e formati esterni restano compatibili. Nessun file esistente viene riscritto.
- `local_ts` ADS (ricezione in testo, µs) è ora arrotondato in modo esatto dagli interi nanosecondi. Nell'8% circa dei campioni differisce di 1 µs dalla versione precedente, che usava un float. `received_ns`, il valore esatto, non cambia.
- Python 3.13 (lo stesso dell'EXE) rifiuta i certificati TLS senza Authority Key Identifier o senza KeyUsage sulla CA. **I broker MQTT con certificati non conformi a RFC 5280 vanno corretti lato certificato.** La verifica non è stata indebolita nel prodotto.
- I tre processi coinvolti restano gli stessi (applicazione, writer primario, convertitori), con lo stesso launcher.

## 8. Avvio e verifica

L'utilizzo da EXE non cambia: Record e Stop funzionano come prima. Per provare le notifiche a blocco su un impianto di prova, prima di avviare l'EXE:

```powershell
set SPACE_ADS_NOTIFY_GROUPING=1
Space_Analyzer_V2.1.0.exe
```

Verificare poi `GET /api/acquisition/health`. In `protocols.ads.ads_notifications` (oppure `source_sessions`) controllare questi campi:

- `notify_layout`;
- `source_gap_samples` / `source_gaps`;
- `dropped`.

Nelle connessioni, `recent.received_per_second` e `recent.dispatched_per_second` danno i rate degli ultimi 10 s; nel primario, `recent.durable_samples_per_second`, `annotations` e `annotations_lost`.

Da sorgente:

```powershell
python tools/run_hardening_checks.py --output evidenze
python -m unittest discover -s source/tests -p test_phase1_integrity.py
cd source/tests
python benchmark_ads_primary.py --channels 1000 --duration 10 --output ads_simboli.json
python benchmark_ads_primary.py --channels 1000 --duration 10 --grouping --output ads_blocchi.json
python benchmark_acquisition.py --duration 20 --ui-stress --output pipeline.json
python soak_acquisition.py --duration 86400 --channels 1000 --rotate-seconds 60 --directory D:/SpaceAnalyzerSoak --report soak24h.json --delete-verified
python ../primary_tools.py inspect "Recordings/.primary/ID.sac"     # ora con events_count/events
```

## 9. Interventi raccomandati per la Fase 2

1. **Qualificare le notifiche a blocco su TwinCAT reale**: layout GVL, online change, reboot, limite di notifiche per porta, misura end-to-end. Se positive, abilitarle per impostazione predefinita sopra una soglia di canali.
2. **Driver ADS in un processo dedicato** che invii al processo principale blocchi binari, in memoria condivisa o su pipe, invece di righe Python. È il passo necessario per 100.000 valori/s: la misura indica circa 60.000 valori/s in un solo interprete.
3. Valutare una callback nativa (C# o C) che accumuli i campioni in un ring buffer. Eliminerebbe il costo GIL per notifica anche senza blocchi, ed è da confrontare con il punto 2 sui numeri reali.
4. Rappresentazione colonnare per batch lungo engine e primario: oggi resta un dizionario Python per campione.
5. Eseguire il soak di 24 ore sull'hardware di produzione, con UI, FFT e import attivi.
6. Pagina "Acquisition Health" basata sulle metriche già esposte: rate recenti, eventi, `source_gaps`, `notify_layout`.
