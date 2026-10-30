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
| 8 | **Converti**: avvia la conversione. Durante l'operazione una barra mostra la **percentuale complessiva** (lettura dei campioni, preparazione dei dati e scrittura del file, con il totale stimato — «~» — se si sono scelti pochi canali o un periodo) e il pulsante Annulla, che interrompe la lettura ed elimina i file parziali. Con «Apri file dal PC…» la barra a sinistra indica anche il caricamento (percentuale e MB) e poi la lettura del file. |

![conversion_done](Conversione completata: numero di campioni, file creato e pulsante «Apri in Registrazioni».)

| N. | Elemento |
|---|---|
| 1 | **Stato**: esito, campioni scritti e formato. |
| 2 | **File prodotti**, con «Apri in Registrazioni». |

| Voce | Dettagli |
|---|---|
| Sorgenti | CSV, Parquet, FDA DAS4 (.das), RBF v3, progetti TwinCAT Scope (.svdx), DAT (iba PDA3) e archivi primari (.sac). |
| Destinazioni | CSV, Apache Parquet, FDA DAS4 e RBF v3. |
| Campioni | Ogni campione della selezione viene scritto con valore, qualità e timestamp, senza riduzione. Gli istanti in cui un canale FDA/RBF non ha valore («mancante») non sono campioni e non vengono riportati. Nomi, unità, periodo e dati della sessione passano nei metadati del nuovo file. |
| Limiti dei formati | CSV e Parquet sono senza perdita. FDA DAS4 salva valori Int16 scalati (l'errore di arrotondamento è nei metadati) e tempi al millisecondo. FDA e RBF accettano al massimo 64 segnali. Un file RBF contiene al massimo 57.600 istanti: le registrazioni più lunghe sono divise in più parti. Ogni file FDA viene chiuso prima di superare circa 255 MB di RAM nel programma FDA e la conversione prosegue in `_FDA0002`, `_FDA0003`…, senza perdere campioni. |
| Destinazione | Sottocartella «Conversioni» della cartella delle registrazioni. Un nome già esistente non viene sovrascritto. |
| Esecuzione | In background, una conversione alla volta; l'interfaccia resta utilizzabile. |

## Archivi primari (.sac)

Durante ogni registrazione l'applicazione scrive anche un **archivio primario** con tutti i campioni originali, nella cartella `Recordings\.primary` (estensioni `.sac`, `.sac.part`, `.incomplete.sac`, `.recovered.sac`). Di norma serve solo come copia di sicurezza, perché il file Parquet, FDA o RBF è già in `Recordings`. Se quel file manca o è incompleto (arresto anomalo, errore del disco, formato che non ha retto), l'archivio primario si può **esportare** in un formato leggibile dalla stessa pagina Conversione: gli archivi compaiono nell'elenco con il formato «Archivio primario».

![conversion_sac](Archivio primario selezionato nella pagina Conversione.)

| N. | Elemento |
|---|---|
| 1 | **Archivio primario** nell'elenco: tipo (completo, incompleto, interrotto `.part`, recuperato), dimensione e inizio. L'elenco legge solo l'intestazione, quindi compare subito anche per file da decine di GB. |
| 2 | **Informazioni**: formato, dimensione, numero di segnali e inizio. Durata e numero di campioni non sono mostrati perché richiederebbero di leggere tutto il file. |
| 3 | **Nota del formato**: ricorda che l'archivio contiene tutti i campioni originali. |
| 4 | **Dividi in file da**: ogni file prodotto copre 15 minuti di tempo registrato (5, 30, 60 minuti o «Un solo file» a scelta); l'ultimo può essere più breve. |
| 5 | **Converti**: segnali e intervallo si scelgono come per le altre sorgenti. |

L'esportazione scrive tutti i campioni originali dei segnali e dell'intervallo scelti, **divisi in file da 15 minuti** di tempo registrato (contati dal primo campione esportato; il taglio cade tra due istanti, senza spezzare un ciclo): un archivio di ore diventa una serie di file piccoli, facili da aprire. Con «Un solo file» si ottiene un unico file; l'ultimo file può essere più breve. Un archivio **interrotto o danneggiato in coda** non viene rifiutato: si esportano tutti i blocchi validi e il messaggio finale indica la causa (ad esempio «Archivio non chiuso correttamente» oppure «Archivio chiuso con errore» con il numero di campioni non arrivati all'archivio). Con FDA DAS4 e RBF le notifiche ADS non periodiche restano solo nell'archivio e il massimo è di 64 segnali; con CSV e Parquet si esporta tutto. Il file originale non viene modificato.

Da riga di comando, senza avviare l'interfaccia: `--tool export-sac <file.sac> csv|parquet|das|rbf [cartella] [minuti]`; i minuti per file sono 15 se omessi (0 = un solo file). Senza cartella i file vanno nella sottocartella `Conversioni` delle registrazioni; il risultato (campioni, file, avvisi) è stampato in JSON.

## Progetti TwinCAT Scope (.svdx)

I progetti di **Beckhoff TwinCAT Scope** salvati con i dati inclusi («Include data in SVDX») si aprono come le altre registrazioni e si convertono in CSV, Parquet, FDA DAS4 o RBF. Beckhoff non pubblica la specifica del formato: il lettore è stato ricavato da un file di riferimento (94 canali REAL32 a 1 ms) e controlla la struttura a ogni lettura; un file che non la rispetta viene rifiutato con un messaggio invece di produrre dati errati.

![conversion_svdx](Progetto Scope (.svdx) con due canali su tre selezionati.)

| N. | Elemento |
|---|---|
| 1 | **Progetto Scope** nell'elenco (formato «TwinCAT Scope (.svdx)»). Un file di un altro PC si aggiunge con «Apri file dal PC…». |
| 2 | **Canali**: ogni canale del progetto è un segnale, con il nome dato in Scope e l'unità se presente. Si convertono solo quelli selezionati. |
| 3 | **Intervallo**: da/a della registrazione. Con canali e periodo ridotti il file prodotto è proporzionalmente più piccolo. |
| 4 | **Converti**. |

I campioni hanno l'istante assoluto (UTC) del registratore di Scope con risoluzione di 100 ns; i canali con periodi diversi restano distinti, senza interpolazione. FDA e RBF accettano al massimo 64 segnali: con più canali si scelgono quelli da esportare. I valori sono quelli grezzi del file (nessuna scala o offset del progetto viene applicata). Verificato su tipi REAL32; gli altri tipi numerici (interi, REAL64, BOOL) seguono la stessa struttura ma non sono stati provati con file reali.

## Tipo di dato scelto automaticamente

L'applicazione ricava da sola, per ogni segnale, il tipo più stretto che resta esatto in base al tipo acquisito e ai valori registrati: nessuna impostazione da fare. Nei file RBF i segnali booleani restano canali Boolean (1 byte) e gli interi a 16 bit o meno con valori entro Int16 diventano canali Int16 nativi (2 byte invece dei 4 del Float32). Un UINT con valori oltre 32767 resta Float32 (esatto fino a 16 milioni). Per i segnali Double o a 32/64 bit l'RBF ha solo il Float32: quel file resta approssimato e l'archivio Parquet originale resta esatto. Nei file FDA un intero con escursione entro 65.536 valori (ad esempio un UINT a 16 bit) è salvato a 16 bit esatti con guadagno 1 e offset 32768 (valore = grezzo × guadagno + offset). La pagina Registrazioni mostra lo storage reale dei file RBF e l'indicazione «senza perdita» ricavata dai metadati.

> Da verificare nel programma FDA proprietario: se non applicasse l'offset del canale, i valori dei segnali a 16 bit con offset risulterebbero traslati.
