# Fase 1 — analisi preliminare della V1.28.0

Analisi eseguita prima delle modifiche, 24 ottobre 2026.

La UI usa Flask; un loop asyncio nell'app gestisce i collegamenti e il polling. `source_sessions.py` mantiene quattro sessioni indipendenti. ADS ciclico, MQTT e S7 UDP hanno già callback/ricevitori e code dedicate. `recording_manager.py` separa Record dai salvataggi precedenti; `recordings.py` scrive CSV/Parquet su thread, mentre FDA/RBF/iba/PDA3 hanno conversioni separate. `live_archive.py` conserva lo storico temporaneo per l'analisi. Le API e i grafici consultano i buffer condivisi in `app.runtime`.

Criticità rilevate:

1. I sink ADS/MQTT/misti e il polling raggiungono il recorder dentro `runtime.lock`. Le API delle viste e il buffer Live usano lo stesso lock. La separazione dal browser è quindi incompleta.
2. In ADS singolo la verifica dell'epoca del buffer precede la registrazione: un azzeramento Live può scartare notifiche già accodate durante Record. Il sink misto distingue già revisione della selezione ed epoca della vista.
3. Le callback ADS copiano soltanto dati e orologi, ma il contatore ricevuti non include i tentativi in overflow. Mancano sequenze per campione e dettagli persistenti della perdita; una regressione dell'orologio durante il decode può interrompere un batch già estratto.
4. Il recorder applica limiti per cicli e rileva gli overflow, ma non ha un archivio primario versionato recuperabile dopo un crash. Un Parquet senza footer non è un recovery generale. Solo PDA3 dispone già di un proprio journal di conversione.
5. L'esattezza ADS FILETIME e dei timestamp MQTT è conservata nelle colonne originali; l'asse del grafico usa float in millisecondi. Mancano un identificativo stabile della connessione, sequenze e una distinzione sistematica tra ricezione, origine e clock discontinuo.
6. Le diagnostiche dei driver sono utili ma frammentate. Mancano un evento persistente comune, latenza fino al flush durevole, P95/P99 e confronto ricevuti/registrati per connessione.
7. I limiti dei formati esterni e della UI non sono un benchmark di 1.000 canali. Il riferimento 100.000 valori/s deve essere verificato separatamente e non attribuito a PLC/DLL reali senza prova.

Interventi previsti, senza modifiche grafiche:

- Code limitate per connessione e distribuzione Live separata; percorso recorder prima delle operazioni secondarie, fence espliciti durante Stop e cambio sessione.
- Archivio primario append-only a blocchi con schema/versione, tipi, sequenze, CRC, flush configurabile, writer separato e recovery automatico non distruttivo.
- Compatibilità dei sei formati: gli errori dei writer esterni non devono distruggere l'originale primario.
- Sequenze, timestamp di ricezione ad alta precisione, contatori di overflow/backlog e gestione esplicita delle discontinuità ADS.
- API diagnostiche e storico eventi persistente; nessuna nuova pagina UI.
- Profilo di acquisizione isolato senza dipendenze esterne, con controllo backend; migrazione predefinita dei driver valutata in base alle regressioni e ai benchmark.
- Test di saturazione, crash/recovery, disco pieno, multi-protocollo, UI sotto carico; benchmark verificabile e runner separato di 24 ore.
