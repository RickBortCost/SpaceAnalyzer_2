# Changelog

Il numero di versione e la data di ogni release sono assegnati dalla pipeline (vedi `VERSIONI.md`); per le versioni 1.x vedi `source/CHANGELOG.txt`.

## 3.0.4

- **Modifiche in ogni pubblicazione**: la pagina della release e il nuovo `README.txt` nello ZIP iniziano con l'elenco delle modifiche della versione, preso dalla sezione corrispondente di `CHANGELOG.md` (`tools/release_notes.py`). Se la sezione manca, l'elenco viene ricavato dai commit dall'ultima release. Per una release già esistente la pipeline aggiorna anche il testo.
- `LEGGIMI_AVVIO_WINDOWS.md`: rimosse le sezioni "Novità" di versioni precedenti e l'indicazione superata sulla posizione delle impostazioni; aggiunte le caratteristiche generali.
- Test: `test_release_notes.py` (sezione della versione, testo della release, ripiego sui commit, README in testo semplice, presenza della sezione per la versione corrente) eseguito dalla pipeline.

## 3.0.3

- **Cartella delle registrazioni a scelta**: nella finestra di Record il campo "Cartella di destinazione" si può modificare. **Sfoglia…** apre la finestra di Windows "Seleziona cartella"; si può anche scrivere un percorso completo; **Predefinita** torna a `Recordings` accanto all'EXE. La pagina Registrazioni mostra la cartella attiva e ha il pulsante **Cambia…**. La scelta vale per nuove registrazioni, export e pagina Registrazioni ed è memorizzata in `%LOCALAPPDATA%\OPCUA_Web_Client\recordings_location.json`. Il servizio verifica che la cartella sia scrivibile; il cambio è rifiutato durante Record. Se all'avvio la cartella salvata non è disponibile (ad esempio un disco di rete scollegato), si usa quella predefinita e viene mostrato un avviso.
- **Live a piena risoluzione**: il Live usa ora la stessa precisione delle registrazioni. Prima "Intera acquisizione" mostrava un riassunto di 256 intervalli min/max e "Ultima finestra" 1.200 punti per segnale; ora il grafico contiene ogni campione acquisito, disegnato con il min–max per colonna di pixel e i singoli campioni con lo zoom. A ogni aggiornamento il servizio invia solo i campioni nuovi, in formato binario (`/api/live/samples`), identificati da una sequenza per segnale (le revisioni dei valori calcolati sostituiscono l'ultimo campione). "Ultima finestra" rispecchia il buffer del servizio. "Intera acquisizione" conserva tutta la sessione: la parte precedente all'apertura della finestra viene letta dallo storico originale Live su disco (`/api/live/history`). Limite di 40 milioni di campioni nel grafico (20 milioni per una lettura dallo storico); oltre, si mostra la parte più recente con un avviso. Se il disegno supera 150 ms, il grafico viene ridisegnato meno spesso senza perdere campioni.
- **Robustezza del servizio**: le notifiche del router TwinCAT (ADS nativo, TcAdsDll) arrivano in un callback chiamato dal router; un errore al suo interno poteva chiudere l'intero servizio (`exit status 0xe0434352`, interfaccia bloccata). Ora l'errore ferma solo l'acquisizione ADS con un messaggio. Un errore fatale di qualunque thread viene scritto in `opcua-client.log` e in `crash-<data>.txt` nella cartella dei dati; il messaggio del launcher indica quel file.
- **Tutto sul disco dell'EXE**: impostazioni, log, servizio estratto, storico Live, cache dei calcoli, file aperti dal PC e file temporanei (upload, .NET, SQLite, certificati temporanei OPC UA: TMP/TEMP del servizio puntano a `SpaceAnalyzer_Data\tmp`) stanno in `SpaceAnalyzer_Data` accanto all'EXE (prima: `%LOCALAPPDATA%\OPCUA_Web_Client` e la cartella temporanea di Windows, su C:). Al primo avvio il launcher copia impostazioni e file importati dalla cartella precedente ed elimina le sue cache (storico Live, calcoli, servizi estratti). Se la cartella dell'EXE non è scrivibile si usa quella precedente; `OPCUA_DATA_DIR` resta prioritaria.
- **Storico Live dopo una chiusura anomala**: ogni sessione può occupare fino a 8 GB e prima veniva eliminata solo alla chiusura regolare; dopo un crash restava su disco. Ora le sessioni rimaste vengono eliminate all'avvio.
- **Disco pieno**: le operazioni che falliscono per mancanza di spazio mostrano "Disco pieno" con il percorso, invece di un errore generico.

## 3.0.2

- **Registrazione in evidenza**: durante Record il pulsante diventa rosso e lampeggia con il tempo trascorso (`● REC 04:47`, aggiornato ogni secondo); lo stesso tempo compare nel titolo della finestra. La lunga riga di stato è sostituita da una barra compatta: formato, righe scritte, velocità (righe/s), parte, occupazione della coda (verde sotto 50%, ambra fino a 80%, rossa oltre) e nome del file. Percorso completo, righe in memoria, cicli in coda e note restano nel tooltip. Animazioni disattivate se il sistema chiede movimento ridotto.

## 3.0.1

- **Finestra applicazione**: l'interfaccia si apre in una finestra dedicata (Microsoft Edge o Chrome in modalità applicazione, con un profilo proprio), senza schede né barra degli indirizzi. Chiudere la finestra arresta l'applicazione come **Chiudi applicazione**: le registrazioni in corso vengono finalizzate. Durante una registrazione la chiusura chiede conferma. Se Edge e Chrome non sono disponibili si usa il browser predefinito; `SPACE_WINDOW=browser` mantiene la pagina nel browser.
- **Grafici dei file a piena risoluzione**: aprendo un file il grafico riceve tutti i campioni dell'intervallo selezionato, con i valori originali, senza riduzione min/max né limite di punti per segnale. Il selettore "Risoluzione grafico per segnale" è rimosso. I dati arrivano dal servizio in formato binario (colonne Float64) e restano in memoria come array tipizzati.
- **Disegno**: quando i campioni visibili superano i pixel, ogni colonna di pixel mostra l'intervallo minimo–massimo di tutti i suoi campioni (stessa copertura della linea completa, picchi di un solo campione compresi); con lo zoom compaiono i singoli campioni. Navigatore e conteggio qualità leggono tutti i campioni. Selezione zoom, marker e cursore riusano lo strato già disegnato.
- **Limite di memoria**: una vista a piena risoluzione accetta fino a 80 milioni di campioni (circa 1,4 GB). Oltre, il servizio chiede di ridurre intervallo o segnali invece di mostrare una vista ridotta.
- La parte Live è invariata.
- Test: nuovo workflow UI `test_full_resolution_ui.cjs` (300.000 righe, valori esatti, picco singolo, zoom, marker, endpoint binario) e due test xUnit (lettura completa e formato binario).

## 3.0.0

Space Analyzer 3.0 è interamente in C#. Rapporto completo, verifiche e limiti in [VERSIONE_3.0.md](VERSIONE_3.0.md).

- **Applicazione in C#**: il servizio Flask/Python è sostituito da `SpaceAnalyzer.exe` (ASP.NET Core su .NET 8, self-contained). Acquisizione, registrazione, lettura delle registrazioni, anteprime, confronti, calcoli, FFT, analisi d'intervallo, recupero delle catture primarie, eventi (SQLite) e SSH girano in un unico processo C#. Nessun runtime Python è incluso nel prodotto.
- **Motori di acquisizione e registrazione in C#**: driver OPC UA (OPC Foundation .NET), ADS/TCP e TwinCAT locale, MQTT (MQTTnet), S7 classico, UDP e simbolico (S7CommPlusDriver); writer CSV, Parquet (ParquetSharp), FDA DAS4, RBF v3 e cattura primaria SAC.
- **Formati di registrazione**: CSV, Parquet, FDA (.das) e RBF. La registrazione `.dat` (iba/PDA3) è rimossa; i file `.dat` restano apribili e visualizzabili (PDA3 del modello noto e parti DAT 2.x tramite il Parquet associato).
- **Marchio**: logo Space Analyzer come icona dell'EXE e nell'interfaccia; rimossi i riferimenti al marchio precedente.
- **Avvio**: splash centrale con sfondo trasparente, logo e barra di avanzamento guidata dalle fasi reali di avvio del servizio. Il runtime viene estratto una sola volta per versione (`%LOCALAPPDATA%\OPCUA_Web_Client\runtime`), gli avvii successivi sono più rapidi.
- **Strumenti offline**: `Space_Analyzer_V3.0.0.exe --tool inspect|recover|export-fda|export-rbf` (sostituiscono `primary_tools.py`).
- **API HTTP invariate**: stesse route, stessi controlli di sicurezza (solo localhost, token per le richieste POST e per SSH, intestazioni CSP), stessi messaggi in italiano; l'interfaccia web non è cambiata.
- **Test**: 21 workflow UI jsdom eseguiti contro il servizio C# con peer locali OPC UA, ADS, MQTT, S7 e SSH; smoke test end-to-end dell'eseguibile; test xUnit. I test Python che verificavano l'implementazione Python sono stati rimossi con essa; Python resta solo come strumento di test e build (simulatori, harness, versionamento), non nel prodotto.

## 2.1.0

Seconda iterazione della Fase 1 (hardening acquisizione e registrazione). Report completo, benchmark e limiti in [FASE1_HARDENING_V2.1.md](FASE1_HARDENING_V2.1.md); evidenze in `evidence_v2.1.0/`.

- ADS: rilevamento per canale dei cicli PLC mancanti a monte della callback (`source_gaps`, evento `ads_source_gap`). Prima queste perdite erano silenziose.
- ADS: notifiche a blocco opzionali (`SPACE_ADS_NOTIFY_GROUPING=1`): una callback per ciclo per le variabili contigue, con fallback automatico. Predefinito disattivo.
- ADS: callback e decode più leggeri. In simulazione, 1.000 simboli passano da 9.409 a 15.236 valori/s; con i blocchi, 60.000 valori/s sostenuti senza perdite. Il target di 100.000 valori/s **non** è raggiunto.
- Recorder primario: gli eventi critici sono scritti nel file SAC (blocchi `EVENT`) e sopravvivono al recovery. L'IPC `marshal` sposta la codifica JSON nel processo writer, con file identici.
- Diagnostica: rate su finestra mobile (`recent`) per connessioni e primario.
- Sessioni sorgente: un errore rilevato mentre una sessione si chiudeva poteva andare perso se Stop arrivava in quella finestra, e la registrazione risultava completa. Ora l'errore viene attribuito prima di staccare il client e Stop attende la chiusura in corso (difetto già presente nella 2.0.0).
- Test: nuova suite `test_phase1_integrity` (14). Rese robuste 4 suite dipendenti dai tempi dello Stop asincrono; certificati MQTT di test conformi a RFC 5280. 243 test superati.

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
