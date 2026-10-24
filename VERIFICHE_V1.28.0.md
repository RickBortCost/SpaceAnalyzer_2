# Registrazioni, salvataggi e letture — V1.28.0

## Comportamento

L’applicazione gestisce una registrazione attiva e sessioni distinte in finalizzazione. Stop conclude il proprio intervallo, scarica i campioni già acquisiti e separa la sessione; una breve attesa massima di 150 ms può completare i file piccoli, ma la conversione completa non blocca il successivo Record. Il limite di protezione è di 32 salvataggi ancora in corso. I sei formati continuano a usare i propri writer e codec.

Ogni sessione ha ID, selezione, metadati, code, file e stato indipendenti. Uno Stop riferito a un ID precedente viene rifiutato prima di modificare l’acquisizione corrente. Il client ignora le risposte di generazioni precedenti. È possibile cambiare selezione/formato/formule dopo Stop senza modificare i salvataggi precedenti. Errori e file parziali rimangono associati alla propria sessione.

FDA, RBF e DAT/iba elaborano le parti già chiuse mentre l’originale continua a essere acquisito. Le conversioni sono serializzate per contenere la contesa; FDA/RBF usano processi separati, come già avveniva per iba e PDA3. Gli originali dei formati convertiti usano Parquet Rapido, senza cambiamenti a valori, precisione, qualità o timestamp. Il costo può essere un archivio originale più grande rispetto al precedente Compatto.

CSV scrive blocchi; Parquet evita la compressione di prova lontano dal limite di rotazione, conservando il controllo esatto vicino al limite e quello finale. Il conteggio dei cicli usa contatori invece di un insieme crescente. FDA/RBF leggono solo le colonne necessarie nella preparazione e usano database temporanei di ordinamento senza sincronizzazioni superflue; gli originali e gli output finali mantengono le proprie garanzie di chiusura.

La lettura FDA usa blocchi binari senza costruire testi/data-ora per ogni campione del grafico; i nuovi FDA cronologici supportano la ricerca diretta dell’intervallo. Parquet seleziona colonne e gruppi temporali necessari. Le panoramiche mantengono estremi, confini temporali e qualità con memoria limitata per segnale; i timestamp duplicati restano distinti prima della riduzione per il grafico. Il confronto multi-file conserva la gestione preesistente dei vuoti temporali.

Cache limitate riutilizzano descrizioni, viste e marker. Dimensione e data di modifica di file, metadati e originale associato invalidano i dati pertinenti. La cache restituisce copie: le modifiche alla risposta non alterano le risposte successive. FFT, formule ed export restano separati dalle panoramiche ridotte.

## Verifiche eseguite

**157 test backend in 22 suite**, [conteggi e risultati](evidence_v1.28.0/backend_results.json):

- Nuovi test del manager: writer precedente bloccato, avvio successivo, selezioni separate, conteggi esatti, errore della vecchia sessione isolato, chiusura di tutte le sessioni e conversione delle parti FDA prima di Stop.
- Nuovi test delle viste: memoria/punti limitati, estremi, Bad/Uncertain, timestamp duplicati e fuori ordine, cache senza nuova scansione, copie indipendenti, invalidazione di file e metadati, limite della cache e lettura dei soli gruppi Parquet interessati.
- CSV/Parquet e tutti i profili, FDA, RBF, DAT/iba con DLL sostitutiva dichiarata, DAT/PDA3, rotazione, originali numerici e qualità, errori disco/overflow, protocollo MQTT con tempi sorgente, S7 classico/simbolico/UDP, ADS e dati calcolati, confronto multi-file, FFT, marker dell’intera acquisizione e lingue. Le prove di round trip attendono esplicitamente i file finalizzati quando il nuovo Stop restituisce ancora `stopping`.

**Cinque flussi DOM**, con canvas simulati: nuovo flusso di sovrapposizione; cataloghi e Record con quattro protocolli; formule/marker oltre il FIFO; Zoom live; chiusura/riapertura delle registrazioni e workspace. Il test nuovo blocca deliberatamente il writer della prima sessione FDA, avvia la seconda Parquet, cambia la selezione tra le sessioni, rifiuta uno Stop obsoleto e verifica entrambi i replay. Le nuove etichette sono provate nelle cinque lingue.

**Chromium con canvas e CSP reali**, quattro peer locali: pulsanti Stop/Record e pannello salvataggi, FDA precedente bloccato, nuova registrazione CSV attiva, finalizzazione del FDA durante il nuovo Record e conteggi originali uguali a quelli riletti per ciascuna sessione. [Rapporto](evidence_v1.28.0/browser_overlap.json) e [schermata](evidence_v1.28.0/overlap_recording.png).

**Processo sorgente avviato realmente**: cinque formati registrati/riletti, marker e formula oltre il FIFO, poi un FDA da **128.000 campioni** e un nuovo CSV da **640 campioni** avviato prima della sua finalizzazione. ID, insiemi di 64 segnali e conteggi sono distinti e corretti. Istanza singola, arresto e rimozione della cache temporanea superati. Il rapporto dichiara `packaged_exe_executed: false`: [smoke sorgenti](evidence_v1.28.0/source_smoke.json). È superata anche la prova separata di porta occupata, doppia apertura e ripristino del workspace.

## Misure locali

Fixture deterministica: 60.000 istanti, quattro segnali, 240.000 campioni. Confronto tra sorgenti V1.27.0 e V1.28.0 sullo stesso ambiente Linux. Tempi della libreria, esclusi HTTP, browser e PLC; una singola esecuzione per versione. La seconda apertura riusa la cache della vista. Le misure dipendono da disco e macchina e non costituiscono una garanzia di throughput.

| Operazione | V1.27.0 | V1.28.0 |
| --- | ---: | ---: |
| Conversione FDA | 1,590 s | 1,435 s |
| Prima lettura FDA completa | 1,010 s | 0,368 s |
| Riapertura stessa vista FDA | 1,047 s | 0,002 s |
| Lettura intervallo FDA | 0,028 s | 0,002 s |
| Prima lettura Parquet completa | 1,099 s | 0,697 s |
| Riapertura stessa vista Parquet | 0,919 s | 0,003 s |
| Lettura intervallo Parquet | 0,363 s | 0,004 s |

[Prima](evidence_v1.28.0/benchmark_before.json), [dopo](evidence_v1.28.0/benchmark_after.json), [confronto](evidence_v1.28.0/benchmark_comparison.json). Il principale cambiamento di Stop è indipendente da questi tempi: il successivo Record viene abilitato anche se il file precedente non è pronto.

Riproduzione, con `source/requirements.txt`, `python-snap7==2.0.2`, Node 24 e `jsdom@30.0.1`:

```sh
python -m unittest discover -s source/tests -p test_recording_manager.py
python -m unittest discover -s source/tests -p test_recording_preview.py
JSDOM_PATH=/path/to/jsdom UI_SCRIPT=test_recording_overlap_ui.cjs python source/tests/test_source_sessions_ui_harness.py
python tools/benchmark_recording_io.py --source source --directory /tmp/io-benchmark --report /tmp/io-benchmark.json
```

Il browser richiede anche Playwright nello stesso ambiente Python:

```sh
python tools/verify_recording_overlap_browser.py --chromium /usr/bin/chromium --report /tmp/browser_overlap.json --screenshot /tmp/overlap.png
```

## Windows

Il workflow richiede 16 test backend e quattro flussi DOM prima della compilazione. Include tutti i nuovi moduli nel payload, avvia il vero EXE senza Python nel PATH e verifica cinque formati, marker/formule, FDA da 128.000 campioni e nuovo CSV durante la sua finalizzazione, istanza singola e chiusura. Il pacchetto include la nuova guida. **Build e prove Windows superati; EXE e ZIP pubblicati il 23 ottobre 2026.** [Workflow](https://github.com/RickBortCost/SpaceAnalizer/actions/runs/39992407560), [sovrapposizione su Windows](evidence_v1.28.0/windows_overlap_ui.json), [cataloghi](evidence_v1.28.0/windows_catalog_ui.json), [Zoom live](evidence_v1.28.0/windows_live_zoom_ui.json), [intervalli](evidence_v1.28.0/windows_interval_ui.json), [rapporto del vero EXE](releases/v1.28.0/WINDOWS_EXE_VERIFICATION.json) e [asset/hash](releases/v1.28.0/WINDOWS_RELEASE.json). Il vero EXE ha acquisito il CSV mentre il precedente FDA da 128.000 campioni era ancora in finalizzazione; i replay contano rispettivamente 640 e 128.000 campioni con tutti i 64 segnali. Commit compilato: `df2b785678a5b56166d4b1e15d5852a9d7d299ad`.

PLC fisici, ADS nativo reale, licenza di scrittura DAT/iba e compatibilità con applicazioni native richiedono le prove dell’ambiente destinatario. Non sono state sostituite licenze reali. Gli originali Parquet restano necessari per la precisione e le informazioni non rappresentabili nei formati derivati.
