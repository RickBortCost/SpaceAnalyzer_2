# 12. Database: pagina PostgreSQL

La pagina PostgreSQL (menu **Database ▾ → PostgreSQL**) legge serie temporali da un database PostgreSQL (anche TimescaleDB), in **sola lettura**, con gli stessi grafici, periodi, riduzione dei dati, zoom con più dettaglio, aggiornamento automatico, analisi X1 ↔ X2 ed esportazione della pagina InfluxDB. Due schede: **Grafico** e **Tabella e query**.

## Collegamento

In **Connessione al server** si indicano indirizzo, porta, database, utente e password, la modalità SSL (se disponibile, disattivata, obbligatoria, obbligatoria con verifica del certificato) e il fuso orario degli orari senza fuso (UTC oppure ora del PC). La password resta nel servizio (cartella dati) e non viene mai inviata alla pagina né scritta nei file esportati; «Dimentica la password» la cancella. Si consiglia un utente con soli permessi di lettura.

## Scheda Grafico

![pg_chart](Pagina PostgreSQL, scheda Grafico: quattro colonne della tabella aperte come serie.)

| N. | Controllo |
|---|---|
| 1 | **Connessione al server**. |
| 2 | **Tabella o vista**: elenco automatico di tabelle, viste e viste materializzate leggibili con una colonna di tempo e colonne numeriche o booleane. |
| 3 | **Colonna del tempo** (timestamp, timestamp con fuso, date): proposta in automatico e modificabile. |
| 4 | **Colonna con il nome del segnale**: «Nessuna: una colonna per segnale» (ogni colonna numerica è una serie) oppure una colonna con i nomi (una riga per segnale e istante; ogni nome è una serie). |
| 5 | **Aggiorna l'elenco**. |
| 6 | **Filtro** su tabelle, colonne e segnali. |
| 7 | **Serie** della tabella, con il tipo di ogni colonna. |
| 8 | **Periodo** (5 min … 30 g) o «Intervallo» a scelta. |
| 9 | **Riduzione dei dati**: Automatica (media), media, minimo, massimo, ultimo, primo, mediana, somma, tutti i punti. |
| 10 | **Apri nel grafico**. |
| 11 | Scheda **Grafico**. |
| 12 | Scheda **Tabella e query**. |

Con pochi punti nel periodo arrivano tutti i valori originali; con molti punti il server calcola la funzione scelta su finestre di durata tonda, con il punto al centro della finestra; i booleani diventano 0/1. I nomi di tabelle e colonne sono sempre racchiusi tra virgolette e i valori passati come parametri.

## Scheda Tabella e query: vista tabellare

![pg_table](Vista tabellare della tabella selezionata.)

| N. | Controllo |
|---|---|
| 1 | **Tabella selezionata**: nome della tabella o vista letta. |
| 2 | **Solo il periodo scelto**: casella che limita le righe al periodo scelto per il grafico (altrimenti si legge tutta la tabella). |
| 3 | **Ordine**: «Più recenti prima» (o più vecchie prima); si può ordinare anche con un clic sull'intestazione di una colonna. |
| 4 | **Righe per pagina**: 100, 500, 1.000 o 5.000. |
| 5 | **Esegui** (aggiorna la pagina). |
| 6 | **Pagina successiva / precedente**. |
| 7 | **Esporta pagina in CSV** (e **Copia**): salvano o copiano le righe mostrate, con i valori come visualizzati. |
| 8 | **Tabella**: tutte le colonne, con il tipo sotto il nome, numeri allineati a destra, valori nulli evidenziati e numero di riga. |

Per esportare tutti i dati delle serie si usa **Esporta…** nella scheda Grafico (CSV, Parquet, FDA, RBF).

## Query SQL e query salvate

![pg_query](Query SQL scritta nell'editor, eseguita e salvata.)

| N. | Controllo |
|---|---|
| 1 | **Query salvate**: elenco; un clic ricarica nome e testo nell'editor. |
| 2 | **Nome** della query. |
| 3 | **Salva**: salva nome, testo e righe per pagina; se il nome esiste già, una seconda pressione di Salva sostituisce la query. |
| 4 | **Nuova**: svuota l'editor. |
| 5 | **Elimina** la query salvata selezionata. |
| 6 | **Editor SQL**: Ctrl+Invio esegue. |
| 7 | **Esegui**. |
| 8 | **Risultato**: stessa tabella della vista tabellare, con tipi, pagine e tempo di esecuzione. Gli errori indicano il carattere della query a cui si riferiscono. |

Le query salvate stanno nella cartella dati del servizio (`postgres_queries.json`, fino a 200); l'ultimo testo scritto è ricordato anche senza salvarlo.

> **Sola lettura garantita.** Sono ammesse solo istruzioni singole che iniziano con SELECT, WITH, VALUES o TABLE. La query gira in una transazione di sola lettura dietro un cursore sul server (si trasferisce solo la pagina richiesta) con un tempo massimo di 60 s: una DELETE, un UPDATE o un WITH … DELETE vengono rifiutati dal server anche se aggirassero il controllo del testo.
