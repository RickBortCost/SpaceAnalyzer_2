# PostgreSQL: vista tabellare e query salvate

Nella pagina **Database → PostgreSQL** la scheda **Tabella e query** mostra i dati come tabella, accanto alla scheda **Grafico**.

## Tabella selezionata

- Mostra tutte le colonne della tabella o vista scelta nel pannello a sinistra, con il tipo sotto il nome della colonna.
- **Solo il periodo scelto** limita le righe al periodo del grafico (colonna del tempo scelta a sinistra); **Ordine** sceglie le righe più recenti o più vecchie per prime.
- **Righe per pagina**: 100, 500, 1.000 o 5.000; i tasti ‹ › scorrono le pagine.
- Un clic sull'intestazione ordina la pagina mostrata (un secondo clic inverte l'ordine).

## Query SQL

1. Scegli **Query SQL**, scrivi la query e premi **Esegui** (o Ctrl+Invio).
2. Il risultato appare nella stessa tabella, con tipi, pagine e tempo di esecuzione. Un errore indica il carattere della query a cui si riferisce.

**Sola lettura**: è ammessa una sola istruzione che inizia con `SELECT`, `WITH`, `VALUES` o `TABLE`. La query gira in una transazione di sola lettura, dietro un cursore sul server e con un tempo massimo di 60 secondi; `INSERT`, `UPDATE`, `DELETE`, istruzioni di definizione e `WITH … DELETE` vengono rifiutati. Per sicurezza usa comunque un utente PostgreSQL con soli permessi di lettura.

## Query salvate

- Scrivi un **nome** e premi **Salva**: la query (testo e righe per pagina) è salvata nella cartella dati dell'applicazione (`postgres_queries.json`, fino a 200 query).
- L'elenco **Query salvate** ricarica nome e testo nell'editor. Se il nome esiste già, premi **Salva** una seconda volta per sostituirla.
- **Nuova** svuota l'editor; **Elimina** cancella la query selezionata (con conferma).
- Il testo scritto resta anche dopo aver chiuso la pagina, anche se non salvato.

## Esportare

- **Esporta pagina in CSV** salva le righe mostrate; **Copia** le copia negli appunti (separate da tabulazioni).
- Per esportare tutti i punti delle serie usa **Esporta…** nel pannello a sinistra (CSV, Parquet, FDA, RBF).
