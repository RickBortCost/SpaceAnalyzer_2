# Variabili calcolate — Space Analyzer 1.9.0

L'acquisizione e le serie acquisite restano in `app.Runtime`, le scritture in
`Recorder`, la lettura/decimazione/marker in `RecordingLibrary`, l'analisi in
`analysis_tools` e nei worker FFT. La nuova funzione riusa questi componenti.

| Componente | Responsabilità |
| --- | --- |
| `formula_engine.py` | AST numerico controllato, whitelist, unità suggerite, grafo topologico, allineamento e qualità |
| `calculation_store.py` | Normalizzazione, revisioni, validazione completa, CRUD, RLock e salvataggio JSON atomico |
| `calculation_service.py` | Controller Live, coda limitata, worker, anteprima isolata, cache storica e materializzazione |
| `calculation_worker.py` | Processo senza import di `app.py`, protocollo JSON, riordino temporale Live, replay/anteprima |
| `calculation_history.py` | Ordinamento dei campioni originali, warm-up delle dipendenze, stesso Engine, export standard |
| `app.py` | Hook sui flussi esistenti, buffer derivati, endpoint, Record, snapshot, FFT/export e workspace |
| `static/calculations.js` | Formula Builder, riferimenti dall'elenco, autocomplete, anteprima, gestione e assegnazione |
| `templates/index.html`, `static/calculations.css` | Nuova pagina responsive con palette esistente |
| `app.js`, `history.js`, `workspace.js`, `studio.js`, `source.js`, `advanced.js` | Integrazione minima di nodi/metadata derivati nei grafici e nella persistenza |

## Modello persistito

```json
{
  "id": "UUID esadecimale a 32 caratteri",
  "nodeid": "calc:UUID",
  "name": "Delta Carico",
  "expression": "[Carico Live 1] - [Carico Live 2]",
  "bindings": {
    "Carico Live 1": {"nodeid":"ns=2;s=Load1","name":"Carico Live 1","unit":"kN","kind":"signal"},
    "Carico Live 2": {"nodeid":"ns=2;s=Load2","name":"Carico Live 2","unit":"kN","kind":"signal"}
  },
  "unit": "kN",
  "modes": {"live":true,"recorded":true},
  "active": true,
  "alignment": "hold",
  "max_age_ms": 1000,
  "clock_nodeid": null,
  "created_at": "UTC ISO 8601",
  "updated_at": "UTC ISO 8601",
  "revision": 1
}
```

La validazione aggiunge `protocol`, `required_sources`, `dependencies` e
`unit_hint`. Il registro ha `schema:1`, revisione globale e lista `formulas`.
`kind:formula` crea una dipendenza, `kind:stored` identifica una colonna
storica immutabile. Le etichette non sono identificatori: gli ID restano stabili
quando vengono rinominate formule o proprietà del grafico.

## Flusso Live e concorrenza

I tre hook sono il sink delle notifiche ADS, il ciclo ADS Read e il campionatore
OPC UA. Il callback OPC UA di subscription continua a riempire solo la cache
esistente: non viene calcolato un secondo flusso di campioni.
Gli hook accodano il batch senza formule o I/O disco. Un controller serializza
fuori dal thread PLC e comunica con un processo dedicato. `Engine` compila una
volta, riusa gli ultimi valori e valuta solo i percorsi dipendenti aggiornati.
Il riordino di 20 ms coalesca timestamp uguali; il flush in idle chiude la coda.

Il worker ha watchdog di risposta, coda 512 batch, heap limitato, profondità e
complessità limitate. Saturazione o crash producono gap e diagnostica, senza
fermare l'acquisizione sorgente. Il controller riavvia il worker. Ogni
riconfigurazione incrementa una generazione: i callback vecchi vengono scartati.
I risultati entrano nelle deques esistenti e nel Recorder con il loro timestamp,
mai con l'orario di completamento del worker. L'aggiornamento con lock è diviso
in blocchi di massimo 256 risultati e le scritture sono raggruppate per tempo.

`runtime.selected` e la Map `selected` del browser contengono solo segnali
fisici: nessun ID `calc:` è inviato al PLC. Le serie disponibili per i grafici
sono unite tramite `liveDataNodes`. Il budget di 2 milioni di punti considera
anche le serie derivate. Configurazione/reset invalida solo i risultati derivati
oltre ai reset già esistenti di connessione e buffer.

Le mutazioni delle formule e l'avvio Record sono serializzati sul command lock.
Il registro conserva revisioni per evitare sovrascritture da altre finestre.
Record impedisce modifiche alle definizioni. Avvio e Stop delimitano i timestamp
accettati e drenano la coda dei calcoli. Questo evita risultati anteriori all'avvio
o perdita della coda finale della registrazione.

## Replay e materializzazione

La libreria originale autorizza il percorso. Un processo legge le righe originali,
seleziona le sorgenti necessarie, ordina per tempo e applica lo stesso `Plan/Engine`.
Il warm-up antecedente l'intervallo tiene conto dell'intera catena di tolleranze.
Il risultato è un file Parquet temporaneo standard, letto dalla normale libreria:
la decimazione arriva dopo il calcolo; marker, FFT ed export vedono campioni originali.
Il cache key include percorso, stat file, definizioni complete, segnali e intervallo.
Generazione cache serializzata, file temporaneo e rename atomico impediscono letture
di risultati incompleti. I processi di replay hanno timeout e limiti di righe.

Materializzare esegue lo stesso percorso verso un nuovo CSV/Parquet in Recordings,
senza alterare l'originale. La sidecar conserva formule e versioni effettivamente
usate. Gli ID già fisicamente presenti nel file vengono trattati come colonne
salvate; non vengono ricalcolati implicitamente con una revisione diversa.
Per un nuovo calcolo su un archivio contenente una serie omonima si duplica la
formula, ottenendo un nuovo ID. Nessuna migrazione dei vecchi file è richiesta.

## Estensioni e sicurezza

`ast.parse` viene usato esclusivamente per tradurre la sintassi in un albero
numerico interno. Non si eseguono AST/Python e non si usano eval/compile. Sono
ammessi soltanto tipi di nodo, operatori e funzioni elencati nel motore; accessi
ad attributi, import, subscript, chiamate generiche, stringhe e comprensioni sono
rifiutati. Le potenze, costanti e risultati hanno limiti numerici espliciti.

Le funzioni pure si aggiungono al registro `FUNCTIONS` con arità e callable
controllato. Operatori temporali futuri richiederanno stato per formula/operatore
nell'istanza Engine e un contesto di timestamp, mantenendo lo stesso percorso
Live/replay e reset per generazione. Non sono implementati filtri/derivate/integrali
in questa versione. `mean` è la media istantanea degli argomenti.

Gli endpoint ereditano autenticazione locale/token, controllo origine e protezione
dei file esistenti. Errori vengono riportati in italiano e registrati nei log.
I test sono in `tests/test_calculations.py`, `test_calculation_api.py`,
`test_calculation_ads.py` e `test_calculations_ui.cjs`; i report e i limiti di
collaudo sono in `VERIFICA.txt` e `Verifiche/V1.9.0` del pacchetto.
