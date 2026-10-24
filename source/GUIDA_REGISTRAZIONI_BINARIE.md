# Space Analyzer V1.10.0 — Registrazioni binarie

Nel popup **Record**, scegliere **Binario Parquet** e il livello di compressione.
Il formato predefinito è **Compatto (ZSTD 9)**. I file mantengono l'estensione
`.parquet`, così si riaprono nella pagina Registrazioni e negli strumenti esterni
compatibili con Apache Parquet, ZSTD e byte-stream split.

| Profilo | Codec | Uso |
| --- | --- | --- |
| Compatto | ZSTD 9 | Scelta consigliata per spazio e costo CPU. |
| Massima compressione | ZSTD 19 | Cerca un file più piccolo; maggiore lavoro di compressione. |
| Rapido | ZSTD 3 | Minore costo CPU con molti segnali. |
| Compatibilità | Snappy | Codec delle versioni precedenti. |
| CSV | Nessuno | Testo leggibile, generalmente più voluminoso. |

Non esiste un codec che dia sempre il file più piccolo: il risultato dipende dai
segnali, dal rumore, dalle ripetizioni e dalla durata. “Massima compressione” è il
profilo più intenso tra quelli disponibili, senza promettere un rapporto fisso.

## Precisione e campionamento

La compressione è reversibile. I campioni numerici restano Float64; byte-stream
split riordina i byte IEEE-754 prima della compressione, senza arrotondarli.
Il campo `value_text` continua a conservare il valore originale: questo mantiene
anche gli interi Int64/UInt64 che il valore numerico del grafico non può rappresentare
esattamente. Non vengono eliminati valori ripetuti, buchi o campioni Bad/Uncertain.
Il grafico continua a mostrare un gap per la qualità non Good.

Restano conservati timestamp della registrazione, timestamp locale, SourceTimestamp,
ServerTimestamp se disponibili e ADS FILETIME originale a 100 ns. Non si aggiungono
conversioni o riduzioni alla precisione temporale dei campioni già acquisiti.
Il formato non modifica il periodo impostato né ricampiona i dati: registra ogni
campione ricevuto dal servizio di acquisizione, prima della decimazione grafica.

## Scrittura e rotazione

I dati vengono accodati senza attendere la scrittura su disco. Il thread del recorder
crea blocchi fino a 32.768 righe (Compatto/Rapido) o 65.536 (Massima compressione),
con un intervallo di flush di 5 secondi. Snappy e CSV mantengono il comportamento
precedente: 4.096 righe / 1 secondo. Lo stato mostra righe scritte, righe ancora in
memoria e coda di acquisizione. Stop drena la coda e completa tutti i dati accettati.

La cartella rimane **Recordings**, accanto all'EXE. Il limite di dimensione usa i byte
compressi e include intestazione/footer del file dati; il file laterale dei metadati
è separato. Al limite si genera una nuova parte, senza spezzare un ciclo. Se un
ciclo non entra nella dimensione scelta oppure il disco non tiene il passo, il
recorder segnala esplicitamente l'errore e conserva le parti già scritte.
Le pagine ZSTD hanno checksum CRC verificati durante la lettura.

Le opzioni si salvano nel workspace. Il profilo è descritto nel `.metadata.json`
e nel footer Parquet: viene riconosciuto anche copiando il solo file `.parquet`.
Le registrazioni CSV/Parquet esistenti rimangono leggibili. Replay, grafici, marker,
FFT e formule usano il medesimo lettore. Export Parquet X1–X2 e dataset calcolati
materializzati usano il profilo Compatto, preservando i campioni richiesti.

## Confronto riproducibile

Dataset sintetico ADS: 30 s, 100 Hz, 8 segnali, 24.000 righe. Include valori analogici,
rumore, booleani, interi a 64 bit, qualità e tutti i timestamp.

| Formato | File dati (byte) | Risparmio rispetto a CSV | Risparmio rispetto a Snappy |
| --- | ---: | ---: | ---: |
| CSV | 8.689.438 | — | — |
| Parquet Snappy | 522.076 | 93,99% | — |
| Parquet ZSTD 3 | 248.426 | 97,14% | 52,42% |
| Parquet ZSTD 9 | 237.042 | 97,27% | 54,60% |
| Parquet ZSTD 19 | 224.870 | 97,41% | 56,93% |

Nessun ciclo perso; stessa sequenza di valori originali e timestamp, verificata con
SHA-256. Nel runtime Linux di prova, la scrittura completa ha richiesto circa 0,40 s
per ZSTD 9 e 1,80 s per ZSTD 19. I campioni sono stati inviati più rapidamente del
tempo reale: queste misure non sono un collaudo di un PAC o del PC Windows.
I risultati dipendono dai dati e dall'hardware. ZSTD 19 ha risparmiato circa il 5%
aggiuntivo rispetto a ZSTD 9 su questo dataset, con maggior costo di compressione.

Script: `source/tests/benchmark_binary.py`. Risultati completi e metadati in
`Verifiche/V1.10.0/benchmark_binary.json`.

## Implementazione

`recording_storage.py` definisce i profili e le opzioni comuni a recorder ed export.
Non introduce un formato proprietario. `recordings.py` applica gli stessi parametri
alla stima preventiva e alla scrittura effettiva per rispettare la rotazione.
`recording_reader.py` riconosce il profilo e verifica i checksum. L'API mantiene
`format: "parquet"`; il campo `options.parquet_profile` ammette `compact`, `maximum`,
`fast`, `compatible`. I codec mancanti e i profili non validi producono un errore
chiaro, senza sostituzioni silenziose del formato.

Documentazione di riferimento:
- https://arrow.apache.org/docs/python/generated/pyarrow.parquet.ParquetWriter.html
- https://parquet.apache.org/docs/file-format/data-pages/compression/
