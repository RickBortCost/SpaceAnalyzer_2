# Pagina InfluxDB (versione 3.7.0)

Lettura dei dati da un server **InfluxDB 2.x** (sviluppata per la 2.7) e visualizzazione nei grafici di Space Fieldbus Analyzer. L'accesso è in sola lettura: l'applicazione non scrive mai nel database.

## Collegamento

1. Apri la pagina **InfluxDB**.
2. Indirizzo del server (ad esempio `http://localhost:8086`) e **token** di accesso con permesso di lettura sui bucket (InfluxDB: *Load Data → API Tokens*).
3. **Connetti**. L'organizzazione si ricava da sola dai bucket del token; si indica solo se il token vede più organizzazioni.

Il token è salvato in `influx.json` nella cartella dati dell'applicazione (come le altre impostazioni locali) e non viene mai rimandato all'interfaccia: la pagina mostra solo le ultime quattro cifre. Alla riapertura la connessione si ristabilisce da sola. **Dimentica il token** lo cancella. Per server HTTPS con certificato non verificato c'è l'opzione dedicata.

## Scelta dei dati

- Scegli il **bucket**; i bucket di sistema (`_monitoring`, `_tasks`) compaiono solo se richiesti.
- L'elenco delle **serie** (misura + campo + tag) è costruito in automatico dai dati degli ultimi 24 ore, 7 giorni, 30 giorni o 1 anno, con il tipo di ogni campo (Double, Int64, Boolean, String). Il filtro cerca in misure, campi e tag. Le serie di testo non si possono disegnare.
- Appena collegato, il primo grafico si apre da solo con la prima misura numerica.
- Si possono aprire fino a 64 serie per grafico e più grafici indipendenti, come nelle altre pagine.

## Periodo e riduzione dei dati

- Periodo: 5 min, 15 min, 1 h, 6 h, 24 h, 7 g, 30 g oppure un intervallo a scelta con inizio e fine. I periodi relativi finiscono ad adesso; se i dati sono fermi da più di 5 minuti finiscono all'ultimo dato.
- **Riduzione automatica**: se in periodo ci sono meno punti di quanti ne mostra il grafico (circa il doppio dei pixel), arrivano tutti, esatti. Altrimenti i dati sono ridotti a finestre di durata tonda (1 s, 2 s, 5 s, … 1 g) con la **media**, il **minimo**, il **massimo**, l'**ultimo valore** o la **mediana** a scelta; i punti sono posti al centro della finestra. L'opzione "Tutti i punti" legge fino a 500.000 punti per serie.
- **Zoom**: dopo uno zoom i dati dell'intervallo visibile sono riletti con più dettaglio; il ripristino torna al periodo scelto.
- **Aggiornamento automatico** (5, 10, 30, 60 s) per seguire dati recenti, finché il grafico non è in zoom.
- Le serie booleane si disegnano come 0/1 (con la riduzione, la media è la frazione di tempo a 1).

## Limiti

- Serie definite dall'insieme completo dei tag: se una serie ha più tag di quelli scelti, la query si limita ai tag mostrati.
- Una serie di testo non si apre; un grafico non ricorda la selezione dopo la chiusura dell'applicazione.

## Esportare i dati in un file

Il pulsante **Esporta…** (sotto l'elenco delle serie) apre una finestra per salvare sul computer i dati delle serie del grafico attivo.

- **Periodo**: *periodo caricato* (quello scelto con i preset o l'intervallo), *intervallo visibile nel grafico* (quello dello zoom) oppure *intervallo a scelta* (dal / al).
- **Formato**: CSV, Apache Parquet, FDA DAS4 (`.das`) o RBF v3 (FDA e RBF fino a 64 serie).
- **Nome del file** e **cartella di destinazione** (*Sfoglia…* apre la finestra di Windows; *Predefinita* usa la sottocartella `Conversioni` delle registrazioni, così il file compare anche nella pagina Registrazioni). Formato e cartella sono ricordati.
- Vengono scritti **tutti i punti originali** del periodo, non le medie mostrate nel grafico. Con dati molto fitti (ad esempio 10 ms su molte ore) il file può essere grande e l'esportazione richiedere tempo: la finestra mostra l'avanzamento e permette di annullare. Un'esportazione alla volta.
- Accanto al file viene scritto un file di metadati con l'indirizzo del server (mai il token), le serie e il periodo.
