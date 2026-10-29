# 10. Conversione di file (menu Strumenti)

La sezione Conversione (menu Strumenti → Conversione) trasforma un file registrato in un altro formato, senza aprirlo nel grafico e senza esportare a mano un intervallo. Il file originale non viene mai modificato.

![conversion](Pagina Conversione con un file selezionato.)

| N. | Controllo |
|---|---|
| 1 | **Filtra file…**: cerca nell'elenco. |
| 2 | **Apri file dal PC…**: aggiunge file di qualsiasi posizione (anche DAT iba PDA3). |
| 3 | **Elenco file** della cartella delle registrazioni: formato, nome, dimensione e durata. |
| 4 | **Formato di destinazione**: CSV, Apache Parquet, FDA DAS4 (.das), RBF v3 (.rbf). Sotto il menu compaiono i limiti del formato scelto. |
| 5 | **Segnali**: Tutti oppure una selezione (massimo 64 per FDA e RBF). |
| 6 | **Filtra segnali…**. |
| 7 | **Intervallo** (facoltativo): Da/A, con pulsante per ripristinare l'intero file. Senza intervallo si converte tutto. |
| 8 | **Converti**: avvia la conversione. Durante l'operazione compaiono i campioni letti (con percentuale quando il totale è noto), la fase di scrittura e il pulsante Annulla, che interrompe la lettura ed elimina i file parziali. |

![conversion_done](Conversione completata: numero di campioni, file creato e pulsante «Apri in Registrazioni».)

| N. | Elemento |
|---|---|
| 1 | **Stato**: esito, campioni scritti e formato. |
| 2 | **File prodotti**, con «Apri in Registrazioni». |

| Voce | Dettagli |
|---|---|
| Sorgenti | CSV, Parquet, FDA DAS4 (.das), RBF v3 e DAT (iba PDA3). |
| Destinazioni | CSV, Apache Parquet, FDA DAS4 e RBF v3. |
| Campioni | Ogni campione della selezione viene scritto con valore, qualità e timestamp, senza riduzione. Gli istanti in cui un canale FDA/RBF non ha valore («mancante») non sono campioni e non vengono riportati. Nomi, unità, periodo e dati della sessione passano nei metadati del nuovo file. |
| Limiti dei formati | CSV e Parquet sono senza perdita. FDA DAS4 salva valori Int16 scalati (l'errore di arrotondamento è nei metadati) e tempi al millisecondo. FDA e RBF accettano al massimo 64 segnali. Un file RBF contiene al massimo 57.600 istanti: le registrazioni più lunghe sono divise in più parti. Ogni file FDA viene chiuso prima di superare circa 255 MB di RAM nel programma FDA e la conversione prosegue in `_FDA0002`, `_FDA0003`…, senza perdere campioni. |
| Destinazione | Sottocartella «Conversioni» della cartella delle registrazioni. Un nome già esistente non viene sovrascritto. |
| Esecuzione | In background, una conversione alla volta; l'interfaccia resta utilizzabile. |

## Tipo di dato scelto automaticamente

L'applicazione ricava da sola, per ogni segnale, il tipo più stretto che resta esatto in base al tipo acquisito e ai valori registrati: nessuna impostazione da fare. Nei file RBF i segnali booleani restano canali Boolean (1 byte) e gli interi a 16 bit o meno con valori entro Int16 diventano canali Int16 nativi (2 byte invece dei 4 del Float32). Un UINT con valori oltre 32767 resta Float32 (esatto fino a 16 milioni). Per i segnali Double o a 32/64 bit l'RBF ha solo il Float32: quel file resta approssimato e l'archivio Parquet originale resta esatto. Nei file FDA un intero con escursione entro 65.536 valori (ad esempio un UINT a 16 bit) è salvato a 16 bit esatti con guadagno 1 e offset 32768 (valore = grezzo × guadagno + offset). La pagina Registrazioni mostra lo storage reale dei file RBF e l'indicazione «senza perdita» ricavata dai metadati.

> Da verificare nel programma FDA proprietario: se non applicasse l'offset del canale, i valori dei segnali a 16 bit con offset risulterebbero traslati.
