# Marker e formule sotto il grafico — V1.27.0

In **LIVE**, la vista **Intera acquisizione** permette di analizzare anche i tratti già usciti dal buffer recente. I valori ai marker e i calcoli usano i campioni originali della sessione; la panoramica del grafico resta aggregata solo per la visualizzazione.

1. Aggiungi i segnali dai cataloghi OPC UA, ADS, MQTT o S7. Puoi combinarli nella stessa formula durante l'acquisizione simultanea.
2. Apri **Analisi X1 ↔ X2** sotto il grafico. Premi **Posiziona X1** e fai clic sul grafico, poi ripeti per **Posiziona X2**. La tabella mostra i valori originali ai due marker e la differenza. In Play i marker seguono la finestra; usa **Pausa** per fissare l'intervallo.
3. Nel pannello **Dati calcolati**, scegli la **Variabile A**. Con **Aggiungi variabile** puoi assegnare B, C e altre variabili. Le etichette indicano anche il protocollo. Sono disponibili i segnali acquisiti e le variabili calcolate attive.
4. Scrivi una formula usando i riferimenti: `[A]-[B]`, `([A]+[B])/2`, `mean([A],[B])`, `abs([A])` oppure `ifelse([B]>0,[A]/[B],0)`. I pulsanti inseriscono riferimenti, operatori e funzioni nel punto del cursore.
5. Scegli il **Risultato** e premi **Calcola**. Il risultato è calcolato sulla serie prodotta dalla formula, tra i marker se entrambi sono presenti, altrimenti sulla finestra visibile. Puoi scegliere ultimo valore, media, minimo, massimo, RMS, picco-picco, deviazione standard, differenza X2−X1, pendenza o integrale. Il pannello riporta intervallo, campioni validi ed esclusi.

**Ultimo valore valido** allinea i segnali in modo causale: riusa solo campioni precedenti, entro la **Tolleranza (ms)**. **Timestamp identici** richiede campioni allo stesso istante ed è adatto a sorgenti sincronizzate. Le qualità non valide, i valori scaduti e gli errori matematici sono esclusi; non vengono sostituiti con zero. L'integrale non attraversa campioni non validi o intervalli oltre tre periodi di campionamento.

**Aggiornamento automatico** ricalcola ogni secondo quando il pannello è aperto; nelle acquisizioni lunghe è preferibile il comando esplicito **Calcola**. Cambiare il tipo di risultato usa le statistiche già calcolate.

Per riutilizzare una definizione, selezionala in **Formula salvata**. **Salva e aggiungi al grafico** la registra nella sezione generale **Dati calcolati**, attiva gli ingressi necessari e aggiunge il segnale al grafico corrente. Il segnale salvato viene acquisito da quel momento. **Apri editor completo** trasferisce la formula e le variabili all'editor generale. Durante Record termina la registrazione con **Stop** prima di salvare modifiche alle formule.

## Disponibilità dello storico

Gli originali Live sono mantenuti su disco in una cache temporanea separata dalle registrazioni, con scrittura su un thread dedicato e lettura progressiva. La cache ha un limite di 8 GiB e una coda limitata: in caso di spazio esaurito o cache incompleta, l'analisi mostra un errore esplicito anziché usare la panoramica aggregata. Visualizzazione e Record restano indipendenti dalla cache.

Una nuova acquisizione o **Azzera dati** invalida lo storico e gli snapshot precedenti; **Chiudi applicazione** elimina la cache temporanea. Disconnettere lascia consultabili i dati dell'ultima sessione fino alla successiva acquisizione. Per conservare lo storico dopo la chiusura usa **Record** nei formati disponibili.

L'aggiornamento non può recuperare campioni che la vecchia versione aveva già eliminato. Dopo aver avviato la V1.27.0, avvia una nuova acquisizione oppure apri una registrazione originale per analizzare dati precedenti.
