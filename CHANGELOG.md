# Changelog

Il numero di versione e la data di ogni release sono assegnati dalla pipeline (vedi `VERSIONI.md`); per le versioni 1.x vedi `source/CHANGELOG.txt`.

## 3.9.1

- **Analisi X1 ↔ X2 generale: dock corretto in Registrazioni e InfluxDB**: nel modello dei grafici di Registrazioni il dock era finito dentro la scheda del grafico; la pagina InfluxDB ne copiava il modello e otteneva due dock con lo stesso identificatore, uno dentro il grafico (la sezione per grafico che doveva sparire) e uno in fondo mai collegato, che non si apriva. Ora il dock è unico, fuori dai grafici e apribile in tutte e tre le pagine. Motivo: segnalazione dell'utente sulla 3.8.1; i test controllavano solo l'esistenza del dock, non la sua posizione (ora verificata).
- **InfluxDB: valori di Analisi X1 ↔ X2 calcolati**: restava il messaggio "Lettura dei campioni originali…" perché per le registrazioni i valori si leggono dal file, che qui non esiste. I valori ora sono calcolati sui dati mostrati nel grafico (per periodi ridotti sono medie di finestra: lo zoom rilegge più dettaglio). Motivo: segnalazione dell'utente.
- **InfluxDB: più punti e frequenza dei dati originali indicata**: il budget di punti per serie sale da 20.000 a 100.000 (diviso per il numero di serie, minimo 2.000, come il limite delle registrazioni) e la barra di stato mostra "dati originali ogni ~10 ms (zoom per il dettaglio)" quando i dati sono ridotti. Motivo: con dati a 10 ms su 6 ore il grafico mostrava medie ogni 10 s senza dire che i dati originali sono molto più fitti. Con 2,1 milioni di punti per serie nel periodo il dettaglio a 10 ms resta visibile con lo zoom (o scegliendo un periodo più breve).
- **InfluxDB: larghezza regolabile**: aggiunta la barra di separazione tra pannello serie e grafici (trascinabile, doppio clic per ripristinare, larghezza memorizzata) e tolto il limite di larghezza della pagina; i grafici si ridisegnano al ridimensionamento. Motivo: segnalazione dell'utente, come per Live e Registrazioni.
- Test: `test_influx_ui.cjs` (dock unico e fuori dal grafico, valori X1/X2 calcolati, frequenza originale nella barra di stato), `test_dock_ui.cjs` e `test_multi_ui.cjs` adattati.

## 3.9.0

- **Raggruppamento delle notifiche ADS cicliche**: il router TwinCAT campiona ancora ogni ciclo, ma può inviare insieme i campioni di un intervallo (parametro nMaxDelay delle notifiche), ognuno con il proprio timestamp PLC. Prima ogni ciclo era un messaggio separato (a 1 ms, mille messaggi al secondo per ogni notifica); ora, in automatico, i campioni vengono raccolti fino a 50 ms (mai meno di un periodo). Il valore si sceglie nella connessione ADS ("Raggruppamento notifiche cicliche", 0 = automatico, fino a 1000 ms). Motivo: con molti meno messaggi il flusso è molto meno esposto ai ritardi di Windows quando l'acquisizione è spinta; la precisione temporale resta quella del PLC. Il Live si aggiorna con lo stesso ritardo.
- **Priorità di Windows durante l'acquisizione**: mentre le sorgenti acquisiscono, il processo passa a priorità "superiore al normale" e torna normale alla disconnessione (`SPACE_PROCESS_PRIORITY=normal|abovenormal|high` per cambiarla). Il thread che riceve i campioni da TcAdsDll viene registrato nel servizio multimediale di Windows (MMCSS, profilo "Pro Audio", priorità alta), oppure portato a priorità massima se MMCSS non è disponibile; il thread del motore di acquisizione ha priorità superiore al normale. Motivo: ridurre i ritardi di Windows sui thread che ricevono e smistano i campioni. Non si usa la priorità "alta" per tutto il processo come impostazione predefinita, per non sottrarre tempo ai servizi TwinCAT sui core condivisi.
- **Garbage collector a bassa latenza** durante l'acquisizione (modalità .NET SustainedLowLatency, ripristinata alla disconnessione). Motivo: evitare pause lunghe di raccolta della memoria mentre arrivano i campioni.
- **Diagnostica**: il riquadro delle notifiche cicliche ADS mostra il raggruppamento in uso, la priorità del processo, lo stato del thread di ricezione, la modalità del GC e il piano energetico di Windows, con il consiglio di usare "Prestazioni elevate" quando non è attivo. Motivo: verificare sull'IPC che le impostazioni siano applicate; il piano energetico non viene cambiato dall'applicazione.
- Nessun componente in modalità kernel e nessuna modifica permanente alle impostazioni di sistema.
- Test: `AcquisitionTuningTests` (ritardo massimo delle notifiche, modalità del GC applicata e ripristinata).

## 3.8.1

- **Correzione del test `test_multi_ui.cjs`**: leggeva i campi marker dentro l'elemento del grafico, ma con l'analisi X1 ↔ X2 generale (3.8.0) il pannello del grafico attivo sta nel dock a fondo pagina. Ora il test cerca i campi anche nel pannello del grafico. Motivo: la pipeline della 3.8.0 si era fermata su questo test e la release non era stata pubblicata; il comportamento dell'applicazione non cambia. Le modifiche della 3.8.0 (analisi generale, riduzione a barra, riordino dei grafici) sono incluse in questa release.

## 3.8.0

- **Analisi X1 ↔ X2 generale**: la sezione per grafico è stata tolta e sostituita da un'unica sezione "Analisi X1 ↔ X2" in fondo alla schermata (Live, Registrazioni e InfluxDB). All'apertura mostra tutti i valori di calcolo (X1, X2, Δt e tabella dei segnali) del grafico selezionato in quel momento; cliccando un altro grafico l'analisi passa a quel grafico. Motivo: con più grafici le sezioni ripetute occupavano molto spazio e andavano aperte una per una.
- **Riduci a barra**: nuovo tasto accanto al tasto a schermo intero (▁ / ▣) che riduce il grafico alla sola barra del titolo; la riga della griglia si abbassa e gli altri grafici recuperano spazio. Un grafico ridotto non viene ridisegnato (meno carico) e lo stato è salvato con il workspace. Motivo: poter tenere sotto mano più grafici mettendone in evidenza solo alcuni.
- **Riordino dei grafici**: maniglia ⠿ nella barra del titolo per trascinare i grafici e cambiarne l'ordine (indicatore del punto di rilascio), oppure Alt + frecce con la maniglia a fuoco. L'ordine è salvato con il workspace. Motivo: l'ordine dipendeva solo da quello di creazione.
- Le dimensioni del pannello marker per grafico non sono più salvate (il pannello è unico, a fondo pagina, con altezza massima e scorrimento). Motivo: conseguenza del pannello unico.
- Test: nuovo `test_dock_ui.cjs` (dock, attivazione, riduzione, ripristino, riordino, lingue); aggiornati `test_studio_ui.cjs` e `test_live_interval_ui.cjs`. Nuove stringhe in italiano, inglese, tedesco, francese e spagnolo.

## 3.7.0

- **Nuova pagina InfluxDB**: collegamento a un server InfluxDB 2.x (indirizzo e token di sola lettura), elenco automatico di bucket, misure, campi e tag con il tipo di ogni campo, scelta delle serie e del periodo (5 min … 30 g oppure inizio e fine a scelta) e visualizzazione nei grafici con marker, FFT/Export, scale Y condivise e più grafici indipendenti. Motivo: leggere e graficare i dati di un database InfluxDB senza passare da file.
- **Il più automatico possibile**: l'organizzazione si ricava dai bucket del token; al collegamento l'elenco delle serie si costruisce da solo e il primo grafico si apre con la prima misura numerica; la connessione si ristabilisce da sola con il token salvato (mai rimandato all'interfaccia, solo le ultime quattro cifre); i periodi relativi finiscono all'ultimo dato se il server non riceve dati da più di 5 minuti.
- **Riduzione automatica dei dati**: se il periodo contiene meno punti di quanti il grafico ne mostra, arrivano tutti esatti; altrimenti i dati sono ridotti dal server InfluxDB a finestre di durata tonda con media (o minimo, massimo, ultimo valore, mediana a scelta), con i punti al centro della finestra; "Tutti i punti" legge fino a 500.000 punti per serie. Con lo zoom i dati dell'intervallo visibile sono riletti con più dettaglio e il ripristino torna al periodo scelto. Motivo: grafici fluidi anche con milioni di punti nel periodo.
- Serie booleane disegnate come 0/1; serie di testo elencate ma non selezionabili; aggiornamento automatico opzionale (5–60 s).
- Nomi di misure, campi e tag sono inseriti nelle query Flux sempre tra virgolette con escape (anche `\`, `"` e `${`), quindi non possono alterare la query.
- **Menu Strumenti**: Conversione, Dati calcolati e Debug SSH sono raggruppati in un menu "Strumenti" della barra superiore. Motivo: con la nuova pagina le voci non entravano più su una riga; la barra resta su una riga da 1500 a 1920 px in tutte le lingue. Il menu si apre in posizione fissa e si chiude con clic fuori, Esc o scelta di una voce.
- Test: `InfluxTests` (analisi del CSV annotato di InfluxDB, quoting Flux, finestre, indirizzi), `test_influx_ui.cjs` (connessione, token errato, elenco, valori e tempi esatti, riduzione, booleani, intervallo, zoom, più grafici, riconnessione) contro un server InfluxDB di prova (`influx_peer.py`); guida in `source/GUIDA_INFLUXDB.md`.
- **Non verificato con un server InfluxDB reale**: i test usano un server di prova che riproduce le risposte dell'API 2.x; il collegamento a una vera istanza 2.7.12 va provato dall'utente.

## 3.6.0

- **Tipo di dato scelto automaticamente in base ai dati acquisiti**: l'app ora ricava da sola il tipo più stretto che resta esatto per ciascun segnale, in base al tipo acquisito (ad esempio UINT a 16 bit) e ai valori realmente registrati. Nessuna impostazione da fare. I segnali a 32 bit, a 64 bit (Double, Int64, …) e Float non vengono mai ristretti: restano a piena precisione dove il formato lo permette. Motivo: risparmiare memoria e spazio senza perdere nulla.
- **RBF**: i segnali booleani restano canali Boolean (1 byte) e i segnali interi a 16 bit o meno (UINT, INT, WORD, BYTE, SINT, …) i cui valori sono interi entro Int16 diventano canali Int16 nativi (2 byte invece di 4 del Float32), esatti. Un UINT con valori oltre 32767 resta Float32 (esatto fino a 16 milioni). Le informazioni di formato nei metadati dicono se il file è senza perdita: lo è quando tutti i canali sono Boolean, Int16, testo o Float32 da dati a singola precisione. Per i segnali Double o a 32/64 bit l'RBF ha solo il Float32, quindi quel file resta approssimato e l'archivio Parquet originale resta esatto (come prima).
- **FDA**: un segnale intero (UINT, DINT, …) con valori oltre 32767 veniva scalato con passi 1-2-5 (ad esempio ÷5 per un UINT fino a 65535), perdendo precisione. Ora, se l'escursione registrata sta in 65.536 valori (UINT a 16 bit, contatori con piccole variazioni), il segnale è salvato a 16 bit esatti con guadagno 1 e un offset di 32768 nel canale (`valore = grezzo × guadagno + offset`). I segnali che già entravano in Int16 e quelli non interi non cambiano. Da verificare nel programma FDA proprietario: se non applicasse l'offset del canale i valori di questi segnali risulterebbero traslati.
- **Memoria dei grafici LIVE**: per i segnali booleani, interi fino a 16 bit e Float32 i valori sono conservati a 32 bit invece di 64 (−4 byte su 25 per campione); se arriva un valore non esatto a 32 bit la serie passa da sola a 64 bit senza arrotondare nulla. I segnali Double e a 32/64 bit restano a 64 bit. In più la serie cresce del 50% per volta invece di raddoppiare, riducendo la memoria inutilizzata. Motivo: l'acquisizione completa della sessione può occupare centinaia di MB.
- **Parquet originale: non modificato, con misura**: un confronto su un milione di campioni UInt16 (andamento casuale) dà 1.132 KiB con la colonna Double attuale (ByteStreamSplit + zstd) contro 745 KiB con una colonna Int32 delta + zstd, ma il file ha una sola colonna del valore per tutti i segnali (anche Double) e molte altre colonne, quindi il guadagno sull'intero file sarebbe di pochi punti percentuali e richiederebbe un cambio di formato dell'archivio. Motivo: costo e rischio di compatibilità superiori al beneficio.
- La pagina Registrazioni mostra lo storage reale dei file RBF (etichetta e indicazione "senza perdita" dai metadati del file). Motivo: prima mostrava sempre "RBF v3" e "con perdita" anche per file esatti.
- Test: `RbfUsesTheNarrowestExactTypePerSignal`, `RbfWithOnlyExactTypesIsDeclaredLossless`, `FdaStoresSixteenBitIntegersExactlyEvenAbove32767` (compresi i casi Double e Int64 che non devono restringersi) e il controllo di `LiveSeries` (32 bit esatti, passaggio automatico a 64 bit).

## 3.5.3

- **File FDA apribili nel programma FDA senza selezione del sottoinsieme**: un file FDA da 900.000 campioni e 46 canali (15 minuti a 1 kHz) faceva comparire nel programma FDA l'avviso "requires storing data in more than 256 MB" e la finestra "Data Subset Selection", perché il programma carica in RAM campioni × canali (tempo incluso) × 8 byte e rifiuta oltre 256 MB (qui 322,7 MB). Ora ogni file FDA viene chiuso prima di superare 255 MB di RAM così calcolati (circa 711.000 campioni con 46 canali; con meno canali, di più) e la registrazione prosegue in un nuovo file `_FDA0002`, `_FDA0003`, … senza perdere campioni. Vale per Record, per la conversione dalla pagina Conversione e per `--tool export-fda`. Motivo: i file generati devono aprirsi direttamente nel programma FDA.
- Limite invariato per le dimensioni su disco (`max_file_mb`): il nuovo limite si somma e vince quello che scatta per primo.
- Test: `FdaFilesStayWithinTheViewerRamLimit` (suddivisione dei file, nessun campione perso, formula del limite).

## 3.5.2

- **REGISTRAZIONI: cursore e griglia allineati tra grafici dello stesso file**: con la sincronizzazione attiva e senza zoom, ogni grafico usava l'intervallo X dei propri segnali; segnali con inizio e fine leggermente diversi (qui circa 0,4 s) davano assi X sfasati, quindi la linea di posizione cadeva in punti diversi dei due grafici pur indicando lo stesso istante. Ora i grafici dello stesso file con la sincronizzazione attiva condividono l'intervallo X (unione dei loro dati). Senza sincronizzazione ogni grafico continua a usare i propri dati; file diversi non si influenzano. Motivo: cursore, marker e griglia devono corrispondere allo stesso punto in tutti i grafici.
- **I messaggi di errore non restano più per sempre**: il messaggio in basso (ad esempio "Timeout del servizio locale") restava finché non compariva un altro messaggio. Ora si chiude con un clic (suggerimento tradotto nelle cinque lingue) e comunque dopo 20 secondi. Motivo: un errore già superato non deve restare sullo schermo.
- **Timeout più comprensibile e meno frequente**: il messaggio di timeout riporta anche l'indirizzo della richiesta (ad esempio "Timeout del servizio locale (30 s) · /api/…") e l'elenco delle registrazioni ha ora lo stesso limite di 240 s delle altre operazioni sulle registrazioni (prima 30 s). Motivo: con molti file o file grandi l'elenco può richiedere più di 30 s, e senza l'indirizzo non si capiva quale richiesta fosse scaduta.
- Test UI: `test_scale_groups_ui.cjs` (intervallo X condiviso con e senza sincronizzazione, fallisce sul codice precedente) e `test_studio_ui.cjs` (chiusura del messaggio con un clic).

## 3.5.1

- **Apertura e conversione dei DAT scritti da ibaPDA (lettura sperimentale)**: finora si aprivano solo i DAT PDA3 del modello a 64 canali scritto da Space Analyzer 1.x; un DAT prodotto da ibaPDA veniva rifiutato con "record RLE 0x00 non supportato". Ora anche questi file si aprono in Registrazioni e si convertono nella pagina Conversione (ad esempio in Parquet). Motivo: richiesta di convertire i DAT acquisiti con ibaPDA.
- **Valori Float32 o Double**: i blocchi dati usano la stessa codifica RLE del modello, ma con valori a 8 byte (Double); il lettore riconosce da solo la dimensione dei valori. I valori sono letti senza arrotondamenti. Motivo: era la differenza che bloccava la lettura.
- **Canali senza nome, ricostruiti dalla struttura del file**: intestazione e indice dei file ibaPDA sono in formato proprietario non leggibile, quindi nomi e unità non sono disponibili; i canali si chiamano "Canale 01", "Canale 02"… nell'ordine dei blocchi finali. I blocchi di ogni canale vengono riconosciuti da: un blocco finale corto per canale (dà il numero di canali), stesso numero di campioni per tutti i canali, periodo ricavato dalla durata, blocchi scritti in ordine di tempo, salti tra blocchi consecutivi compatibili con la variazione del segnale. Fra le attribuzioni valide si sceglie quella con i salti minori, che deve essere nettamente migliore della seconda; se l'attribuzione è ambigua il file viene rifiutato invece di mescolare i canali. L'esito della verifica è riportato nella nota del file. Motivo: i dati devono restare quelli del canale giusto anche senza leggere l'indice.
- **Orario**: dal calendario interno del file. Con **Apri file dal PC…** vale l'offset UTC indicato all'importazione; per un DAT copiato nella cartella delle registrazioni si usa il fuso orario del PC. Motivo: il file non indica il fuso orario.
- Verifica sul DAT di ibaPDA ricevuto (25 canali, 60.000 campioni per canale a 10 ms, 600 s): attribuzione unica e completa, conversione in Parquet identica, valore per valore, a una decodifica indipendente.
- Test: `Pda3GenericTests` (DAT sintetico con valori Double e blocchi interlacciati: canali, ordine, valori, tempi, intervallo e conversione in Parquet).

## 3.5.0

- **Nuova sezione Conversione** (tra Registrazioni e Dati calcolati): si sceglie un file e lo si converte in un altro formato. Sorgenti: CSV, Parquet, FDA DAS4 (.das), RBF v3 e DAT (iba PDA3, anche aperti dal PC con **Apri file dal PC…**). Destinazioni: CSV, Apache Parquet, FDA DAS4 e RBF v3. Si possono scegliere i segnali (tutti o una parte) e un intervallo Da/A facoltativo; senza intervallo si converte tutto il file. Motivo: richiesta di convertire i file acquisiti e registrati (ad esempio un DAT in Parquet) senza aprirli nel grafico né esportare a mano un intervallo.
- **Tutti i campioni originali**: la conversione scrive ogni campione della selezione con valore, qualità e timestamp, senza riduzione. Gli istanti in cui un canale FDA/RBF non ha un valore ("mancante") non sono campioni e non vengono riportati. Nomi, unità di misura, periodo di campionamento e dati della sessione passano nei metadati del nuovo file. Motivo: il file convertito deve contenere gli stessi dati dell'originale.
- **Limiti dei formati indicati prima di convertire**: FDA DAS4 salva valori Int16 scalati (l'errore di arrotondamento è nei metadati) e tempi al millisecondo; FDA e RBF accettano al massimo 64 segnali; un file RBF contiene al massimo 57.600 istanti, quindi le registrazioni più lunghe vengono divise in più parti. CSV e Parquet sono senza perdita. Motivo: chiarire quando il formato di destinazione perde precisione.
- **Conversione in background con avanzamento e annullamento**: la pagina mostra i campioni letti (con percentuale quando il totale è noto) e la fase di scrittura; **Annulla** interrompe la lettura ed elimina i file parziali. Una conversione alla volta. Motivo: i file grandi richiedono tempo e l'interfaccia deve restare utilizzabile.
- **Destinazione**: i file convertiti vanno nella sottocartella `Conversioni` della cartella delle registrazioni (quella scelta con **Cambia…**), compaiono nella pagina Registrazioni e si aprono subito con **Apri in Registrazioni**. Il file originale non viene modificato; un nome già esistente non viene sovrascritto. Motivo: ritrovare i file convertiti senza cercarli e senza rischiare di perdere l'originale.
- Servizio: `POST /api/convert`, `GET /api/convert/{id}`, `POST /api/convert/{id}/cancel`.
- Test: `ConversionTests` (CSV→Parquet, Parquet→FDA/RBF, FDA→CSV con selezione di segnali e intervallo, richieste non valide, nessun file intermedio residuo) e `test_conversion_ui.cjs` (navigazione, conversioni, file dal PC, errore su selezione vuota, apertura in Registrazioni, lingua).

## 3.4.2

- **Chiusura dell'applicazione senza errore "Failed to fetch"**: premendo il pulsante Spegni la pagina mostrava l'errore "Failed to fetch" anche se l'applicazione si chiudeva. Il servizio ora attende circa 400 ms prima di fermarsi, così la risposta arriva alla pagina, e la pagina considera concluso lo spegnimento anche se la connessione cade subito dopo la richiesta. Motivo: il servizio si fermava prima di rispondere e la pagina interpretava la chiusura come un errore. Un errore vero restituito dal servizio continua a essere mostrato.
- **Menu Impostazioni visibile durante la registrazione e in ogni larghezza**: il menu a tendina si apriva dentro la barra superiore, che ritaglia i contenuti oltre la sua altezza, quindi risultava tagliato o coperto dalla barra di registrazione. Ora si apre in posizione fissa sotto il pulsante, sopra a tutti gli altri elementi, e si chiude con clic fuori dal menu, con Esc o ridimensionando la finestra. Motivo: il menu doveva essere sempre leggibile e utilizzabile.
- Test UI: `test_studio_ui.cjs` verifica posizione e chiusura del menu Impostazioni.

## 3.4.1

- **Nome dell'applicazione sempre visibile nella barra superiore da 1500 px**: con la scala dello schermo al 125% (circa 1530 px di larghezza utile) il nome "Space Fieldbus Analyzer" spariva e restava solo il logo, perché compariva solo sopra 1760 px. Ora compare da 1500 px in su: in quella fascia la versione e REQ/REALE non sono nell'intestazione (la versione resta nella barra di stato, REQ/REALE nella diagnostica del Live), l'icona della lingua è nascosta e spaziature, tasti, selettori e orologio sono più compatti. Motivo: far stare il nome senza spostare o tagliare i comandi.
- **Lingue con testi più lunghi**: tedesco e francese mostrano il nome da 1640 px, spagnolo da 1560 px (italiano e inglese da 1500 px). Motivo: con quei testi i comandi non entrerebbero sulla stessa riga; verificato da 1500 a 1920 px in tutte le lingue senza barre che vadano a capo o taglino i controlli.
- Sotto 1500 px il nome resta nascosto (resta il logo), come prima.

## 3.4.0

- **Nuovo nome: SPACE FIELDBUS ANALYZER**: l'applicazione si chiama ora "Space Fieldbus Analyzer" nell'interfaccia (titolo della finestra, intestazione, testi, tooltip e messaggi, nelle cinque lingue), nel launcher (finestra e splash), nei messaggi e nei file generati (log, esportazioni, nome dell'applicazione OPC UA, intestazione Parquet/RBF) e nella documentazione. Motivo: nome richiesto per l'applicazione.
- **File della release**: EXE e ZIP si chiamano `Space_Fieldbus_Analyzer_V<versione>.exe` e `Space_Fieldbus_Analyzer_V<versione>_WINDOWS.zip`; titolo della release e artefatti della pipeline usano il nuovo nome. Le versioni precedenti restano con il nome originale. Motivo: il nome del file deve corrispondere al nome dell'applicazione.
- **Intestazione**: il nome più lungo compare solo sopra 1760 px (prima 1640 px) e la barra resta su una riga senza tagliare i controlli, verificato da 1920 a 761 px. Motivo: evitare che il nome più lungo sposti o tagli i comandi.
- **Identificatori tecnici invariati**: namespace e progetti C# (`SpaceAnalyzer`), `SpaceAnalyzer.exe` del servizio, cartella dati `SpaceAnalyzer_Data`, variabili d'ambiente, chiavi di salvataggio del browser (lingua e tema), tipo di contenuto interno e identificatore del formato di cattura primaria. Motivo: rinominarli farebbe perdere impostazioni, workspace e dati già presenti e renderebbe illeggibili file già registrati, senza alcun beneficio per l'utente.
- Test aggiornati al nuovo nome (titolo, risposta di `/api/status`, titolo delle note di release).

## 3.3.0

- **Rotella sul grafico LIVE = periodo visualizzato (asse X)**: con il cursore sull'area di un grafico LIVE, la rotella verso l'alto riduce il periodo visualizzato e verso il basso lo aumenta (passo ×1,25), ancorato all'ultimo campione. Il periodo va da un minimo di 10 secondi di dati fino a tutti i dati dall'inizio della connessione (oltre si torna alla vista completa). Vale per tutti i grafici della pagina e anche con Zoom live attivo; il doppio clic nell'area del grafico ripristina l'intera sessione. Non agisce a grafico fermo (Pausa o zoom a rettangolo) né sulle colonne degli assi Y, dove la rotella continua a zoomare la singola scala. Motivo: poter passare dal dettaglio degli ultimi secondi all'intera acquisizione senza aprire menu o impostazioni.
- La riga di stato del Live indica "Periodo visualizzato" e i secondi mostrati quando il periodo è ridotto (nuova voce tradotta in tutte le lingue). Motivo: rendere evidente che il grafico non mostra l'intera sessione.
- Test UI: `test_live_zoom_ui.cjs` (limiti 10 s e sessione completa, ripristino, grafico fermo, assi, Zoom live) e `test_scale_groups_ui.cjs` aggiornato.

## 3.2.2

- **Zoom e spostamento per singola scala Y**: la rotella sopra una colonna asse ingrandisce o riduce solo quella scala attorno al cursore (con Maiusc la sposta del 10%), il trascinamento verticale sull'asse la sposta, il doppio clic sull'asse ripristina la scala automatica (fuori dagli assi riporta tutto alla vista iniziale). Con lo zoom rettangolare attivo la modifica riguarda lo zoom corrente. Motivo: con scale indipendenti serviva poter regolare un canale senza toccare gli altri.
- **Scale oltre il limite di 4 colonne**: nel grafico compare l'indicazione "+N Y" con le scale calcolate ma senza colonna. Motivo: prima non era visibile che alcune scale non avevano un asse disegnato.
- **Legenda utilizzabile da tastiera**: Spazio o frecce prendono un canale, Invio lo rilascia su un altro per condividere la scala, Canc lo separa, Esc annulla; il canale preso è evidenziato e il suggerimento compare nel tooltip (tradotto in tutte le lingue). Motivo: il solo trascinamento escludeva chi non usa il mouse.
- **Indicatore di connessione**: un pallino accanto a Connessione (verde connesso, giallo in corso, rosso errore, grigio non connesso) con testo accessibile. Motivo: dopo aver tolto lo stato dall'intestazione (3.2.1) serviva un segnale minimo, senza occupare spazio.
- **Storico Live: diagnosi del sovraccarico e scrittura più veloce**: l'errore "coda di scrittura piena" riporta ora le righe/s in arrivo e quelle scritte, così si capisce quanto ridurre segnali o frequenza; la serializzazione dei punti avviene senza alberi JSON intermedi e SQLite usa `synchronous=OFF` (l'archivio è temporaneo e ricostruibile), portando lo scrittore da circa 77.000 a oltre 90.000 righe/s nelle misure. Limite invariato e dichiarato: un carico stabile oltre la capacità (indicativamente centinaia di segnali a 1 kHz) rende lo storico incompleto e viene segnalato, mai nascosto. Nuovi test di carico (`LiveArchiveLoadTests`, parametri `SA_LOAD_*`) e di equivalenza della serializzazione (`LivePointJsonTests`).
- **Smoke test dell'EXE deterministico**: la prova di sovrapposizione delle registrazioni usa il gancio di test `SPACE_TEST_HOOKS` per trattenere il salvataggio precedente e rilasciarlo esplicitamente. Motivo: su runner veloci il controllo "Overlap fixture finalized too early" falliva a caso e bloccava la pubblicazione.
- **Controlli statici dell'interfaccia** (`test_static_assets.py`, eseguito dalla pipeline): sintassi di tutti i JavaScript, file referenziati dal template, id duplicati, stesse chiavi in tutte le lingue del catalogo. Motivo: errori di questo tipo emergevano solo a runtime.
- Test UI estesi (`test_scale_groups_ui.cjs`) a rotella, trascinamento, doppio clic, tastiera, scale nascoste e pallino.

Non incluso, da valutare: trigger e allarmi, firma dell'EXE e aggiornamento automatico, prove con hardware e Windows reali.

## 3.2.1

- **Intestazione su un solo livello**: la barra non va più a capo a nessuna larghezza (dal 3.1.0 andava su due righe tra circa 1280 e 1730 px). Lo stato della connessione e l'indirizzo del server (ad esempio "Non connesso" e "TwinCAT locale · AMS …") non compaiono più nell'intestazione: restano nella barra di stato in basso e nella finestra Connessione. Motivo: la barra deve restare una sola riga senza tagliare i controlli.
- **Compattazione progressiva**: sotto circa 1640 px si nascondono nome e versione accanto al logo, sotto 1380 px REQ/REALE, sotto 1240 px il logo, e sotto 1200 px caratteri e margini si riducono; sotto circa 1170 px la barra scorre in orizzontale invece di andare a capo, così nessun controllo è irraggiungibile.
- Il selettore del tema (Standard/Dark/System) è più largo e il testo non viene più tagliato.
- **Salvataggio di impostazioni e workspace più robusto su Windows**: il salvataggio atomico dei file JSON usa un file temporaneo diverso a ogni scrittura, riprova per pochi istanti se Windows rifiuta la sostituzione e le letture non bloccano più la sostituzione del file. Motivo: con scritture ravvicinate (cambio lingua, salvataggio automatico del workspace) il servizio poteva rispondere 500 "Access to the path is denied" e il salvataggio andava perso; era anche la causa di un test UI che a volte bloccava la pubblicazione. Nuovo test `AtomicJsonTests` con scrittori e lettori concorrenti.

## 3.2.0

- **Data e ora del PC nell'intestazione**: accanto a Impostazioni compare la data e l'ora, fino al secondo, del PC su cui gira l'applicazione, aggiornate ogni secondo e nel formato della lingua scelta. Motivo: poter confrontare a colpo d'occhio l'orario del PC con i timestamp di grafici, registrazioni e log.
- **Ordine dei controlli dell'intestazione**: ora, da sinistra a destra, lingua, tema (Standard/Dark/System), data e ora, Impostazioni e pulsante Spegni (prima Impostazioni e Spegni stavano a sinistra di lingua e tema). Motivo: i comandi usati raramente e quello di chiusura restano all'estremità destra della finestra.
- **Pulsanti X1 e X2 tolti dalla barra strumenti dell'intestazione**: i marker si posizionano dal pannello "Analisi X1↔X2" di ciascun grafico ("Posiziona X1/X2"), invariato. Motivo: la barra dell'intestazione resta essenziale (Pausa, Zoom live, Record, Stop, Zoom).
- Test: `test_i18n_ui.cjs` verifica l'ordine dei controlli e il formato dell'orologio.

## 3.1.0

- **Doppio clic su un segnale crea un grafico**: nella lista dei segnali LIVE e della pagina Registrazioni il doppio clic apre un nuovo grafico indipendente con il solo segnale (stessa registrazione in Registrazioni); i doppioni da doppio clic ripetuto sono ignorati. Motivo: prima servivano "+ Grafico" e l'assegnazione manuale del segnale.
- **Drag & drop su un grafico**: il grafico di destinazione resta evidenziato durante il trascinamento, il segnale viene aggiunto una sola volta e non nascono nuovi grafici (invariato, ora coperto da test).
- **Scale Y indipendenti per segnale**: nei nuovi grafici ogni segnale ha la propria scala Y; gli assi sono colorati come il segnale, fino a 4 colonne per lato, numerati Y1, Y2, … (oltre il limite la scala resta calcolata ma senza colonna). Motivo: confrontare canali di ordini di grandezza diversi senza che uno schiacci gli altri.
- **Scale condivise dalla legenda**: si trascina il nome di un canale sopra un altro nello stesso grafico per condividere una sola scala, ripetibile senza limiti di canali; il segnale trascinato passa al gruppo di destinazione e gli altri membri del gruppo d'origine restano invariati. Evidenziati il canale trascinato, il canale e il gruppo di destinazione e i canali che condividono la scala (etichetta Yn colorata); il rilascio fuori da una destinazione valida non cambia nulla. Il trascinamento della legenda non sposta il grafico né attiva lo zoom.
- **Menu contestuale della legenda** (tasto destro): "Separa scala" (il canale torna indipendente), "Unisci tutte le scale", "Separa tutte le scale".
- **Unità diverse**: l'unione di canali con unità fisiche diverse chiede una conferma esplicita ("scala numerica senza equivalenza fisica"); senza conferma nulla cambia. I valori non vengono mai modificati.
- **Comportamento del gruppo**: autoscale sul minimo e massimo complessivi dei campioni validi di tutti i canali del gruppo (NaN, infiniti e campioni non validi ignorati; valori costanti o senza campioni validi danno comunque una scala finita), zoom verticale e limiti manuali (Impostazioni assi, ora uno per gruppo) condivisi, Y1/Y2 delle proprietà del segnale applicato a tutto il gruppo.
- **Workspace**: i gruppi di scala, l'asse di ciascun canale e i limiti manuali sono salvati e ripristinati per grafico. I workspace salvati prima della 3.1.0 si caricano senza errori in modalità Y1/Y2 automatica e passano ai gruppi alla prima unione o separazione.
- **Fino a 24 grafici per pagina** (erano 8; limite anche nel salvataggio del workspace). Motivo: gestione dei grafici senza limite pratico per l'operatore.
- **Intestazione su due righe invece di controlli tagliati**: tra circa 1280 e 1730 px di larghezza i controlli andavano oltre il bordo della finestra; ora la barra va a capo. Motivo: tutti gli elementi sempre visibili.
- Test: nuovo `test_scale_groups_ui.cjs` (creazione grafico, drop, unione/separazione, 12 canali in un gruppo, autoscale, grafici indipendenti, persistenza e compatibilità, 60 modifiche rapide durante nuovi frame LIVE senza richieste di rete verso acquisizione o registrazione, pagina Registrazioni, cinque lingue); aggiornati i test sul numero massimo di grafici e sugli assi.

## 3.0.8

- **Pulsante «Spegni»**: "Chiudi applicazione" esce dal menu Impostazioni e diventa un pulsante dedicato con il simbolo di accensione/spegnimento, accanto a Impostazioni e sempre visibile a ogni dimensione della finestra; l'azione è invariata. Motivo: con l'avvio a schermo intero la chiusura va trovata subito, senza aprire un menu.

## 3.0.7

- **Schermata sempre a tutta altezza**: la pagina non scorre più. Barra superiore e barra di stato restano fisse e l'area centrale occupa esattamente lo spazio della finestra, perché prima con più grafici la pagina cresceva oltre lo schermo e parti dell'interfaccia (Dati calcolati, messaggi, barra di stato) finivano fuori vista.
- **Pannelli con scroll interno**: l'elenco dei segnali, le registrazioni e "Dati calcolati" (massimo 28% dell'altezza) scorrono dentro il proprio pannello; i grafici si dividono l'altezza disponibile e, solo sotto l'altezza minima utile, scorre l'area dei grafici.
- Stesso comportamento nelle pagine Registrazioni, Dati calcolati e Debug SSH (nuovo `static/fit.css`).
- **Avvio a schermo intero**: la finestra dell'applicazione si apre sempre a schermo intero (F11 per uscire; `SPACE_FULLSCREEN=0` la apre solo massimizzata), perché la schermata massimizzata lasciava barra del titolo e barra di Windows a ridurre lo spazio utile.
- **Adattamento alla dimensione della finestra**: la pagina non scorre a nessuna larghezza o altezza. Sotto i 761 px di larghezza il contenuto passa su una colonna che scorre dentro l'area centrale; la barra superiore ha un'altezza massima e i grafici hanno un'altezza minima, così nessun contenuto viene tagliato quando la finestra si riduce.
- Altezza dei grafici adattata automaticamente: la maniglia di ridimensionamento manuale è nascosta, perché l'altezza dipende dallo spazio disponibile.

## 3.0.6

- **Storico Live non più bloccato**: la coda di scrittura dello storico originale passa da 32.768 elementi / 32 MB a 131.072 / 128 MB, lo scrittore SQLite scrive fino a 8.192 righe per transazione (prima 1.024) e usa cache da 64 MB, file temporanei in memoria e checkpoint meno frequenti. Con campionamento a 1 ms e molti segnali la coda si riempiva ("Storico Live incompleto: coda di scrittura piena") e lo storico restava non consultabile fino al Play successivo, anche con il disco libero.

## 3.0.5

- **Asse Y su valori tondi**: le tacche dell'asse verticale cadono su passi 1-2-5 (ad esempio 0, 20, 40…) invece che su frazioni arbitrarie dell'intervallo; i decimali delle etichette dipendono dal passo.
- **Asse dei tempi adattivo**: le tacche sono allineate all'ora locale (ms, s, min, h, giorni) con segni di riferimento; con zoom sotto il secondo l'etichetta mostra i millisecondi (`hh:mm:ss.mmm`).
- **Palette per tema**: otto colori distinti per il tema chiaro e otto per il tema scuro, scelti in base al tema attivo; i colori salvati da versioni precedenti vengono convertiti per posizione.
- **Legenda compatta**: nella legenda, nel tooltip e nella tabella dei marker compare il nome breve del segnale (alias, oppure l'ultimo elemento del percorso ADS/simbolico); il percorso completo resta nel tooltip della legenda.
- **Barra strumenti del grafico essenziale**: restano visibili solo Analisi X1↔X2, Vista completa e Chiudi file; le altre azioni sono nel menu ⋮.
- **Indicatore di qualità**: il riepilogo della qualità compare solo se ci sono campioni Uncertain o Bad.
- **Stato Live compatto**: la riga mostra "modalità · N s · piena risoluzione"; l'intervallo completo è nel tooltip.
- **Sovrapposizioni risolte**: la barra di scorrimento sotto il grafico è nascosta (resta utilizzabile da tastiera) e il navigatore è più basso; "Vista completa" non si sovrappone più ad "Analisi X1↔X2".
- **Unità di misura** accanto ai valori nell'elenco dei segnali.
- **Cicli persi in registrazione**: la barra di Record mostra il chip "Persi" con i cicli scartati e i buchi di campionamento ADS dall'inizio della registrazione.

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
