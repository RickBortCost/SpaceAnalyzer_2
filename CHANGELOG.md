# Changelog

Il numero di versione e la data di ogni release sono assegnati dalla pipeline (vedi `VERSIONI.md`); per le versioni 1.x vedi `source/CHANGELOG.txt`.

## 2.1.0

Seconda iterazione della Fase 1 (hardening acquisizione e registrazione). Report completo, benchmark e limiti in [FASE1_HARDENING_V2.1.md](FASE1_HARDENING_V2.1.md); evidenze in `evidence_v2.1.0/`.

- ADS: rilevamento per canale dei cicli PLC mancanti a monte della callback (`source_gaps`, evento `ads_source_gap`). Prima queste perdite erano silenziose.
- ADS: notifiche a blocco opzionali (`SPACE_ADS_NOTIFY_GROUPING=1`): una callback per ciclo per le variabili contigue, con fallback automatico. Predefinito disattivo.
- ADS: callback e decode più leggeri. In simulazione, 1.000 simboli passano da 9.409 a 15.236 valori/s; con i blocchi, 60.000 valori/s sostenuti senza perdite. Il target di 100.000 valori/s **non** è raggiunto.
- Recorder primario: gli eventi critici sono scritti nel file SAC (blocchi `EVENT`) e sopravvivono al recovery. L'IPC `marshal` sposta la codifica JSON nel processo writer, con file identici.
- Diagnostica: rate su finestra mobile (`recent`) per connessioni e primario.
- Test: nuova suite `test_phase1_integrity` (12). Rese robuste 4 suite dipendenti dai tempi dello Stop asincrono; certificati MQTT di test conformi a RFC 5280. 241 test superati (un test di `source_sessions` resta intermittente, già sulla 2.0.0: vedi report).

## 2.0.0

Prima versione di Space Analyzer 2, basata su V1.29.0.

### Versionamento e rilascio

- Versione calcolata automaticamente da tag Git e messaggi di commit (`tools/versioning.py`), scritta nei file di build dalla pipeline.
- Release `vX.Y.Z` pubblicata solo dopo test, build ed esecuzione dell'EXE reale su Windows; le pull request vengono provate senza pubblicare.
- Nuove prove `test_versioning.py` (11) e `test_patch_v1291.py` (14) nella pipeline.

### Correzioni al software

Modificati `fda_das.py`, `ads_notifications.py` e `app.py`; il resto del programma è invariato rispetto a V1.29.0.

### Registrazione FDA/DAS4 (`fda_das.py`)

| Problema in V1.29.0 | Correzione 2.0.0 |
|---|---|
| Campioni dello stesso ciclo con timestamp diversi di pochi µs finivano in frame separati (file fino a 64× più grande, canali a Q=0) | Raggruppamento per millisecondo arrotondato, cioè lo stesso istante che finisce nel file. Misura: 64 segnali × 100 cicli, da 6.400 a 100 frame e da 1,67 MB a 35 kB |
| Scala Int16 a sole potenze di 10: con picco 32.768 si usava il 10 % del range | Passi 1-2-5: si usa sempre almeno il 40 % del range (picco 40.000: gain 10 → 2) |
| I campioni Uncertain perdevano il valore (scritto 0) | Il valore resta, con Q=2. Bad, mancanti e non numerici restano 0 con Q=1 |
| Oltre 49,7 giorni l'esportazione falliva | Si continua in una nuova parte con una nuova epoca |
| Un `.part` residuo bloccava ogni nuova esportazione (`FileExistsError`) | Il `.part` viene sovrascritto |
| Salti dell'orologio sorgente (NTP/PLC) mescolati in silenzio | Contati e riportati in `.metadata.json` → `time_discontinuities` |
| Disco pieno durante l'ordinamento | Controllo (minimo 256 MB, `SPACE_FDA_MIN_FREE_MB`) all'avvio e ogni 500.000 campioni; errore chiaro, originale conservato |
| Sidecar scritto dopo il `.das` | Prima il sidecar, poi il `.das` |
| nodeid troncato a 200 caratteri nel canale | 250 caratteri |

### Acquisizione ADS (`ads_notifications.py`, `app.py`)

| Problema in V1.29.0 | Correzione 2.0.0 |
|---|---|
| Coda fissa da 32.768 campioni (0,33 s a 1.000 canali × 10 ms): ogni stallo breve fermava tutto | La coda segue canali × frequenza (`SPACE_ADS_QUEUE_SECONDS`, predefinito 2 s, tetto 1.000.000 campioni / 256 MiB). I limiti configurati restano il minimo |
| PLC in STOP/CONFIG oltre 5 s: watchdog, errore, registrazione fallita | Con stato ADS diverso da RUN il watchdog è sospeso e `plc_not_run` compare nella diagnostica. In RUN senza dati il watchdog scatta come prima |
| Timer asyncio a ~15,6 ms su Windows nel polling a 10 ms | `timeBeginPeriod(1)` durante la sessione ADS, poi ripristino |
| Cicli di polling saltati senza traccia | Contatore `polling_skipped_ticks` (info del client) |

### Non modificato (limiti rimasti)

- Il polling ADS/TCP (senza TwinCAT sul PC) resta senza timestamp PLC e senza istantanea coerente.
- Il target 1.000 canali a 10 ms non è raggiungibile in Python: la coda più grande ritarda l'overflow ma non aumenta il throughput (circa 26.000 valori/s misurati dall'autore).
- Lo stato RUN/STOP è letto una volta al secondo.
- FDA: sempre Int16 senza offset; i valori non numerici (stringhe, array) restano Bad; compatibilità con il software FDA reale **non verificata**.
