# Registrazioni e salvataggi in background — V1.28.0

**Stop termina l’intervallo registrato e libera Record senza aspettare la conversione dell’intero file.** Puoi avviare una nuova registrazione mentre quella precedente viene salvata. Funziona con CSV, Parquet, FDA/DAS4, RBF, DAT/iba e DAT/PDA3.

1. Avvia Record come prima, scegliendo formato e dati della sessione.
2. Premi Stop. Completato lo scarico dei campioni già acquisiti, Record torna disponibile. Per i file piccoli il salvataggio può essere già concluso; per quelli grandi continua in background.
3. Premi nuovamente Record. Puoi cambiare formato, nome della sessione e selezione dei segnali: il file precedente conserva la propria configurazione e i propri campioni.
4. Apri **Salvataggi in background**, sopra i grafici. Ogni sessione mostra nome, formato, fase, file prodotti ed eventuale errore. **Pronto** indica un file disponibile; **Apri registrazione** lo carica nella sezione Registrazioni, anche mentre un nuovo Record è attivo.

I salvataggi precedenti hanno identità, code e file distinti. Lo Stop della nuova sessione agisce sulla nuova acquisizione. Le conversioni vengono eseguite in modo controllato, evitando di avviarne molte contemporaneamente sullo stesso disco. Il limite di protezione è di 32 sessioni ancora da completare: se il disco non riesce a smaltirle, l’app segnala il limite prima di iniziare un’altra registrazione. I campioni già accettati non vengono scartati per liberare il pulsante.

## Salvataggio più efficiente

CSV scrive gruppi di righe insieme. Parquet evita una seconda compressione di prova quando è lontano dal limite massimo del file; vicino alla rotazione mantiene il controllo delle dimensioni.

FDA, RBF e DAT/iba conservano il Parquet originale usando il profilo **Rapido**, senza modificare precisione, valori, qualità o timestamp dell’archivio originale. Le parti già chiuse vengono convertite durante l’acquisizione. FDA e RBF usano processi separati; DAT/iba e PDA3 mantengono i propri processi di conversione. Il profilo Rapido può occupare più spazio rispetto al precedente profilo Compatto. I profili della registrazione Parquet restano selezionabili.

Ogni errore rimane associato al proprio salvataggio. I file parziali e gli originali disponibili vengono conservati e un errore di conversione del vecchio file non viene attribuito alla nuova sessione.

## Apertura e gestione dei file

La lettura FDA lavora su blocchi binari; i nuovi FDA creati dall’app supportano anche l’accesso diretto all’intervallo richiesto. Parquet legge le colonne necessarie e salta i gruppi di righe che non intersecano l’intervallo. I grafici usano panoramiche limitate che mantengono estremi e qualità, senza costruire l’intero storico in memoria prima di ridurlo.

Le descrizioni, le viste e i marker già letti vengono riutilizzati da una cache limitata. Modificare il file, i metadati o l’archivio originale associato invalida i risultati relativi. La cache migliora le riaperture durante l’esecuzione dell’app; non altera i file.

FFT, formule ed esportazioni continuano a usare i campioni originali, indipendentemente dai punti ridotti per il grafico. Il confronto multi-file conserva la propria gestione dei vuoti temporali.

Per terminare usa **Chiudi applicazione**. L’interfaccia attende i salvataggi ancora in corso prima di chiudere; chiudere soltanto la scheda del browser lascia il servizio attivo. Evita di terminare forzatamente il processo mentre salva.
