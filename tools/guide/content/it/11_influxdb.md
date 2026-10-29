# 11. Database: pagina InfluxDB

La pagina InfluxDB (menu **Database ▾ → InfluxDB**) legge e disegna dati da un server InfluxDB 2.x (sviluppata per la 2.7), con le stesse funzioni degli altri grafici: marker, FFT / Export, scale Y condivise e più grafici indipendenti. L'accesso è in **sola lettura**: l'applicazione non scrive mai nel database.

## Collegamento

Aprire **Connessione al server**, inserire l'indirizzo (ad esempio `http://localhost:8086`) e un token API con permesso di lettura sui bucket (InfluxDB: Load Data → API Tokens), poi **Connetti**. L'organizzazione si ricava dai bucket del token; si indica solo se il token ne vede più di una. Il token è salvato in `influx.json` nella cartella dati dell'applicazione e non viene mai rimandato all'interfaccia (si vedono solo le ultime quattro cifre): alla riapertura la connessione si ristabilisce da sola. «Dimentica il token» lo cancella. Per server HTTPS con certificato non verificato esiste un'opzione dedicata.

![influx_page](Pagina InfluxDB con tre serie aperte nel grafico (server di prova).)

| N. | Controllo |
|---|---|
| 1 | **Connessione al server**: indirizzo, token, organizzazione, TLS, Connetti / Dimentica il token. |
| 2 | **Bucket**; (3) **Mostra i bucket di sistema** (`_monitoring`, `_tasks`). |
| 4 | **Cerca le serie con dati negli ultimi**: 24 ore, 7 giorni, 30 giorni, 1 anno; (5) **Aggiorna** l'elenco. |
| 6 | **Filtro** su misure, campi e tag. L'elenco raggruppa per misura, con il tipo di ogni campo (Double, Int64, Boolean, String). |
| 7 | **Periodo**: 5 min, 15 min, 1 h, 6 h, 24 h, 7 g, 30 g; (8) **Intervallo** con inizio e fine a scelta. |
| 9 | **Riduzione dei dati**: Automatica (media), Media, Minimo, Massimo, Ultimo valore, Mediana, Tutti i punti (fino a 500.000 per serie). |
| 10 | **Aggiornamento automatico**: No, 5, 10, 30 o 60 s. |
| 11 | **Apri nel grafico** e (12) **Deseleziona**. |
| 13 | **Grafico di destinazione**, (14) **Disposizione** e (15) **+ Grafico vuoto**. |

FFT / Export, marker e analisi funzionano come nelle altre pagine. L'analisi X1 ↔ X2 mostra i valori del grafico selezionato; la larghezza del grafico si adatta alla pagina.

## Periodo e riduzione dei dati

I periodi relativi finiscono ad adesso; se il server non riceve dati da più di 5 minuti finiscono all'ultimo dato. Se nel periodo ci sono meno punti di quanti il grafico ne mostra, arrivano tutti, esatti. Altrimenti i dati sono ridotti dal server InfluxDB a finestre di durata tonda (1 s, 2 s, 5 s … 1 g) con la funzione scelta, e i punti sono posti al centro della finestra: la riga sopra il grafico indica il numero di punti, quelli originali e la riduzione («5.400 punti (da 10.800, mean ogni 2 s)»). Con «Tutti i punti» si leggono i dati alla frequenza con cui sono salvati (ad esempio 10 ms), fino a 100.000 punti per serie nel grafico. Dopo uno zoom i dati dell'intervallo visibile sono riletti con più dettaglio; Ripristina zoom torna al periodo scelto.

Appena collegati, la lista delle serie si costruisce da sola e il primo grafico si apre con la prima misura numerica. Si possono aprire fino a 64 serie per grafico. Le serie booleane sono disegnate come 0/1 (con la riduzione la media è la frazione di tempo a 1); le serie di testo sono elencate ma non selezionabili.

## Esporta dati

Il pulsante **Esporta…** (sotto l'elenco delle serie) apre una finestra per salvare in locale i dati delle serie del grafico attivo.

![influx_export](Finestra «Esporta dati InfluxDB».)

| N. | Campo |
|---|---|
| 1 | **Periodo da esportare**: «Periodo caricato», «Intervallo visibile nel grafico» (dopo uno zoom) oppure «Intervallo a scelta» con Dal/Al. |
| 2 | **Formato**: CSV, Apache Parquet, FDA DAS4 o RBF. |
| 3 | **Nome del file**. |
| 4 | **Cartella di destinazione**; (5) **Sfoglia…** apre la finestra di Windows per sceglierla, «Predefinita» ripristina la cartella standard. |
| 6 | **Esporta**: avvia l'esportazione, con avanzamento e annullamento. |

Vengono scritti tutti i punti originali del periodo, senza riduzione: con dati molto fitti il file può essere grande e la lettura richiedere tempo. FDA e RBF accettano al massimo 64 segnali (limiti come nel capitolo 10).

## Sicurezza e limiti

Nomi di misure, campi e tag sono inseriti nelle query Flux sempre tra virgolette con escape (anche `\`, `"` e `${`): non possono alterare la query. Se una serie ha più tag di quelli scelti, la query si limita ai tag mostrati. Il grafico non ricorda la selezione dopo la chiusura dell'applicazione. Le schermate di questo capitolo sono state ottenute con il server InfluxDB di prova fornito con i test del progetto, che riproduce le risposte dell'API 2.x con dati simulati; il collegamento a una vera istanza va provato dall'utente.
