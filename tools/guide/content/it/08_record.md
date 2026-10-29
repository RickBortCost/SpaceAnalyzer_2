# 8. Registrazione (Record)

![record_dialog](Finestra «Nuova registrazione».)

| N. | Campo |
|---|---|
| 1 | **Formato**: Parquet (consigliato, senza perdita), FDA · DAS4 + archivio originale, RBF (fino a 64 segnali) e CSV. FDA e RBF vengono generati dopo Stop e conservano il Parquet originale con tutti i campioni; sono disponibili anche DAT/iba e DAT/PDA3. |
| 2 | **Compressione binaria** (Parquet): Compatto ZSTD 9 (consigliato) o profili più veloci; nessuna perdita di precisione. |
| 3 | **Nome sessione, Macchina, Operatore, Descrizione**: metadati salvati col file e usati dai filtri. |
| 4 | **Nuovo file ogni**: rotazione temporale, senza fermare la sessione. |
| 5 | **Dimensione massima file** (MiB; 0 = disabilitata): rotazione al primo limite raggiunto. |
| 6 | **Buffer scrittura** (secondi). |
| 7 | **Cartella di destinazione**: modificabile; Sfoglia… apre la finestra di Windows, Predefinita ripristina la cartella standard. |
| 8 | **Avvia registrazione** (o Annulla). |

Pausa e zoom del grafico non interrompono il salvataggio. Record salva tutti i segnali acquisiti della colonna sinistra, indipendentemente da come sono disposti nei grafici.

**Formati e memoria.** Ogni file FDA viene chiuso prima di superare circa 255 MB di RAM nel programma FDA e la registrazione prosegue in un nuovo file `_FDA0002`, `_FDA0003`… senza perdere campioni; vale anche per la conversione e per `--tool export-fda`. Il tipo di dato di ogni segnale è scelto automaticamente (capitolo 10); i grafici LIVE usano 32 bit per booleani, interi fino a 16 bit e Float32 e passano da soli a 64 bit se serve, senza arrotondare nulla.

## Durante e dopo la registrazione

![record_bar](Barra «REC»: tempo trascorso, formato, righe scritte, velocità, campioni persi, parte, coda e nome del file.)

![record_saved](Dopo Stop: «Registrazione salvata» con durata, righe e percorso; sopra i grafici compare «Salvataggi in background».)

Stop termina l'intervallo registrato e libera subito Record, senza attendere la conversione dell'intero file: si può avviare una nuova registrazione (anche con formato e segnali diversi) mentre la precedente viene salvata. Il pannello «Salvataggi in background» mostra, per ogni sessione, nome, formato, fase, file prodotti ed eventuale errore; «Pronto» indica un file disponibile e «Apri registrazione» lo carica nella sezione Registrazioni. Il limite di protezione è di 32 sessioni ancora da completare. L'archivio primario è a blocchi con checksum e recovery dopo crash (strumenti `--tool`, capitolo 15).
