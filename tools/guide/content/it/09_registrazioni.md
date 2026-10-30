# 9. La pagina Registrazioni

![recordings](Pagina Registrazioni: elenco della cartella, filtri e grafico di analisi.)

| N. | Controllo |
|---|---|
| 1 | **Cambia…**: imposta la cartella delle registrazioni; «Aggiorna» rilegge l'elenco. |
| 2 | **Apri file dal PC…**: importa file da qualsiasi posizione (selezione multipla anche di formati diversi). |
| 3 | **Cerca** per nome, macchina o sessione. |
| 4 | **Formato**: CSV, Parquet, FDA DAS4, RBF, Scope (.svdx), DAT/PDA3, confronti; sono disponibili anche i filtri di durata minima e massima in secondi. |
| 5 | **Elenco dei file**: selezionando un file compaiono i suoi segnali. |
| 6 | **Apri nel grafico** (oppure «Intero file»): i segnali compaiono nel grafico scelto. |
| 7 | **Grafico di destinazione**. |
| 8 | **Disposizione** dei grafici. |

Per i DAT senza manifest si può indicare l'offset UTC del calendario interno e unire DAT consecutivi solo con continuità verificata. Si possono aprire più file, usare più grafici e le stesse funzioni della pagina LIVE: marker, zoom, FFT/Export, formule, riduci a barra e riordino. «Chiudi file» e «Chiudi tutti» liberano i grafici senza cancellare nulla.

![recording_markers](Registrazione aperta nel grafico con marker X1 e X2 e tabella dei valori.)

Il file viene letto a blocchi: Parquet salta i gruppi di righe fuori intervallo e i nuovi FDA creati dall'app permettono l'accesso diretto all'intervallo richiesto. Il grafico usa panoramiche limitate che mantengono estremi e qualità; FFT, formule ed export usano sempre i campioni originali. Il pannello «Dettagli registrazione» riporta metadati, intervallo Da/A e segnali.

**DAT scritti da ibaPDA.** Si aprono in Registrazioni e si convertono (lettura sperimentale): i valori sono letti senza arrotondamenti, ma nomi e unità non sono disponibili e i canali si chiamano «Canale 01», «Canale 02»… ricostruiti dalla struttura del file; se l'attribuzione è ambigua il file viene rifiutato invece di mescolare i canali. L'orario viene dal calendario interno del file (offset UTC indicato con «Apri file dal PC…», altrimenti fuso del PC).

Con la sincronizzazione attiva i grafici dello stesso file condividono l'intervallo X, quindi cursore e griglia cadono nello stesso punto. I messaggi di errore in basso si chiudono con un clic e comunque dopo 20 secondi; il messaggio di timeout riporta l'indirizzo della richiesta e l'elenco registrazioni ha un limite di 240 s.
