# 13. FFT ed export

La FFT si apre da **FFT / Export**. Su un grafico live occorre metterlo in Pausa e posizionare due marker X1/X2 distinti; sulle registrazioni servono i due marker. L'analisi usa i campioni originali, non il disegno ridotto.

![fft_params](Finestra di analisi: parametri FFT ed export.)

| N. | Parametro | Significato |
|---|---|---|
| 1 | Segnale / Intervallo | Segnale da analizzare; modalità X1–X2 oppure «Live · finestra scorrevole». |
| 2 | Finestra | Hann (generale), Flat-top (ampiezza), Blackman-Harris, Rettangolare. |
| 3 | Risoluzione | Campioni per blocco, Δf desiderata (Hz) o durata minima del blocco (0 = automatico). |
| 4 | Overlap | 0, 50, 75 o 90 %. |
| 5 | Media | Energetica, Nessuna (ultimo blocco), Energetica esponenziale (τ), Max Hold. |
| 6 | Grandezza | Ampiezza Peak, Ampiezza RMS, Power, PSD, ASD; unità del segnale originale. |
| 7 | Qualità e buchi | Escludi blocchi non validi oppure Rifiuta intervallo non valido; tolleranza del periodo 0,01–25 %. |
| 8 | Fondamentale F1 | Auto (picco AC dominante) o Manuale; ricerca armoniche ± %; banda RMS da/a. |
| 9 | Rimuovi media DC | Sottrae la componente continua. |
| 10 | Esegui FFT | Calcola e mostra lo spettro. |

![fft_result](Risultato FFT sul segnale «Vibrazione» (registrazione sintetica a 1 kHz con componenti a 50, 150 e 250 Hz).)

Lo spettro mostra la frequenza in Hz e il livello nella grandezza scelta, con le righe tratteggiate di F1–H5 (fondamentale e armoniche fino alla quinta). Nell'esempio il picco dominante è a circa 50 Hz, con armoniche a circa 150 e 250 Hz. Comandi: vista Curva o Righe spettrali, Vista F1–H5, intervallo Da/A Hz, Y log / X log, Ultimo blocco, Max Hold, Autoscale; Salva spettro PNG, Esporta spettro CSV ed Esporta armoniche CSV. La tabella delle armoniche confronta frequenze attese e misurate con lo scarto e l'esito.

**Regolarità del campionamento.** La FFT controlla la regolarità dei timestamp. Con sorgenti irregolari (ad esempio una lettura OPC UA a 100 ms con periodo non perfettamente regolare) l'analisi di un intervallo live può rispondere «nessun blocco continuo Good valido»: conviene registrare e analizzare il file, o usare sorgenti con campionamento regolare (ad esempio ADS ciclico). La tolleranza massima impostabile è 25 %.

## Export dell'intervallo

Sotto i parametri, **Esporta intervallo** salva i segnali del grafico tra X1 e X2 in CSV o Apache Parquet e rende disponibile il link «Scarica file». Se la parte iniziale richiesta è uscita dal buffer originale, il servizio segnala che l'intervallo non è disponibile invece di esportarne solo la coda.
