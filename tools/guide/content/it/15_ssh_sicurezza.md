# 15. Debug SSH, strumenti da riga di comando, sicurezza

## Debug SSH

![ssh](Pagina Debug SSH (menu Strumenti).)

Console verso un target remoto, indipendente dall'acquisizione. L'intera API SSH richiede il token dell'applicazione.

| N. | Controllo |
|---|---|
| 1 | **Host** (IP e porta). |
| 2 | **Utente**. |
| 3 | **Autenticazione**: password o chiave. |
| 4 | **Connetti**. |
| 5 | **Modalità**: Comando o shell. In modalità comando l'opzione PTY non è disponibile; Ctrl+C e input interattivo sono disponibili nella modalità shell. |
| 6 | **Editor** del comando o dello script (anche aprendo script locali), con durata massima. |
| 7 | **Esegui** (e Stop). |
| 8 | **Esporta** l'output in TXT. |

## Strumenti offline (--tool)

Il launcher/EXE accetta comandi non interattivi per la gestione dell'archivio primario e per l'esportazione:

```
--tool inspect <file.sac>
--tool recover <file.sac.part> [output]
--tool export-sac <file.sac> csv|parquet|das|rbf [cartella]
--tool export-fda|export-rbf <originale.parquet> <nodi.json> [max_bytes]
```

`inspect` verifica un archivio primario; `recover` ricostruisce un file dopo un arresto anomalo; `export-sac` esporta un archivio primario in CSV, Parquet, FDA/DAS4 o RBF (capitolo 10); `export-fda` e `export-rbf` convertono un Parquet originale in FDA/DAS4 o RBF.

## Sicurezza e limiti noti

| Tema | Nota |
|---|---|
| Accesso | Solo da 127.0.0.1/localhost; token per ogni richiesta di modifica e per l'API SSH; intestazioni di sicurezza (no-store, nosniff, CSP). |
| Sola lettura | Space Fieldbus Analyzer non scrive sul PLC (ADS senza scrittura né RUN/STOP, MQTT senza PUBLISH) né sui database: InfluxDB e PostgreSQL sono letti con query di sola lettura. |
| Password e token | Password di PostgreSQL e token InfluxDB restano nel servizio e non vengono rimandati all'interfaccia né scritti nei file esportati. |
| S7 simbolico | Richiede una CPU S7-1200/1500 reale per la verifica. |
| ADS nativo | Richiede Windows con TwinCAT. |
| Periodo di lettura | Il valore impostato è un obiettivo; il periodo reale è mostrato nella diagnostica del Live. |
| FFT | Richiede campionamento regolare; su sorgenti irregolari analizzare un file registrato. |
| InfluxDB e PostgreSQL | Verificati con server di prova e un PostgreSQL temporaneo; provare il collegamento alla propria istanza. |
| DAT ibaPDA | Lettura sperimentale: canali senza nome, ricostruiti dalla struttura del file. |
| FDA | Da verificare nel programma FDA proprietario l'offset di canale dei segnali a 16 bit esatti. |

## Risoluzione dei problemi

| Sintomo | Azione |
|---|---|
| Non si connette | Controllare endpoint, Security Policy e Message Security coerenti con il server e, per OPC UA, i certificati («Certificati e Application URI»). |
| Il selettore della fonte è grigio | Premere Disconnetti: la fonte si cambia solo a connessione chiusa. |
| Marker senza valori | Riprendere Play e rimettere in Pausa: i campioni originali devono essere ancora nel buffer. Per intervalli lunghi usare Record. |
| FFT rifiutata | Verificare marker X1/X2 distinti, grafico in pausa, regolarità del periodo (tolleranza) e almeno 16 campioni. |
| Query PostgreSQL rifiutata | Sono ammesse solo istruzioni singole SELECT, WITH, VALUES o TABLE; il messaggio indica il carattere dell'errore. |
| Chiusura | Usare Spegni e attendere i salvataggi in background. |

Documentazione di riferimento nel repository: `source/GUIDA_*.md`, `CHANGELOG.md`.
