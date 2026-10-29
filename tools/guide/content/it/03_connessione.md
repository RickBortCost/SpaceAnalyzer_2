# 3. Connessione alle sorgenti dati

Il pulsante **Connessione** apre il popup «Fonte dati e connessione». La fonte (e gli altri parametri di trasporto) si cambia solo a connessione chiusa: il selettore è disabilitato mentre si è connessi.

![connection_opcua](Popup Connessione per OPC UA.)

| N. | Campo |
|---|---|
| 1 | **Fonte dati live**: OPC UA, ADS · Beckhoff TwinCAT, S7 · Siemens ISO-on-TCP, MQTT · Broker TCP/TLS. |
| 2 | **Endpoint server** (ad esempio `opc.tcp://127.0.0.1:4840`). |
| 3 | **Rileva endpoint e policy**: interroga il server e propone le policy disponibili. |
| 4 | **Security Policy** e (5) **Message Security**: None, Sign, SignAndEncrypt. In produzione usare una policy firmata o cifrata; i certificati si gestiscono in «Certificati e Application URI». |
| 6 | **Username / Password**. La password è «solo sessione»: non viene salvata. |
| 7 | **Acquisizione simultanea**: abilita più protocolli insieme, ciascuno con il proprio periodo. |
| 8 | **Intervallo lettura**: da 1 ms (ADS) a 2 s. È un obiettivo: il periodo effettivo dipende da server, rete e PC ed è mostrato nella diagnostica del Live. |
| 9 | **Buffer recente / FFT**: 10 s, 30 s, 1 min, 2 min. |
| 10 | **Riconnetti automaticamente** se la connessione cade. |
| 11 | **Connetti e leggi** e (12) **Disconnetti**. |
| 13 | **Salva impostazione e layout**. |

Limite buffer live: «Tempo + limite campioni» oppure «Solo numero campioni» (massimo 360.000 per segnale; 2 milioni di campioni complessivi in memoria). Record salva anche i campioni usciti dal buffer.

## ADS · Beckhoff TwinCAT

![connection_ads](Popup Connessione per ADS.)

Due modalità: ADS/TCP diretto (client indipendente) oppure TwinCAT locale con notifiche cicliche. Si indicano IP e AMS Net ID del PLC, porte, AMS Net ID del client e metodo di acquisizione (SumRead o altri). In modalità diretta serve una route sul PLC verso il PC e il PLC deve pubblicare i simboli. Con TwinCAT locale si usano le notifiche cicliche con il timestamp del PLC (1, 2 e 5 ms sono disponibili solo per ADS). Non è prevista scrittura di variabili né comandi RUN/STOP.

## S7 · Siemens

![connection_s7](Popup Connessione per S7.)

Il menu «Modalità di acquisizione» offre tre modi, con selezioni e grafici separati (una sola modalità attiva alla volta; per cambiare occorre disconnettersi e fermare Record):

| Modalità | Origine delle variabili | Acquisizione |
|---|---|---|
| **S7 classico** | Catalogo di indirizzi assoluti DB/M/I/Q definito dall'utente (fino a 5000) | Polling ReadVar su TCP 102 |
| **S7 simbolico** | Catalogo letto dalla CPU: DB, strutture e array (S7-1200/1500, S7CommPlus) | Letture aggregate su TCP 102 |
| **S7 UDP Direct Generic** | Campi del payload definiti dall'utente | Il PLC invia ciclicamente i telegrammi al PC |

**Classico.** Si indicano IP, rack e slot della CPU. Per S7-1200/1500 occorre abilitare PUT/GET e usare DB non ottimizzati. Le variabili non vengono scoperte automaticamente: si definiscono nome, indirizzo (ad esempio `DB1.DBD8`, `MW10`, `I0.0`) e tipo, poi compaiono nel catalogo (importabile ed esportabile in JSON).

**Simbolico.** Il catalogo viene letto dalla CPU e mostra DB, strutture e array anche ottimizzati; non servono offset né software Siemens sul PC. Il driver S7CommPlus è in beta e non è stato provato su una CPU reale: la disponibilità dipende da modello, firmware e autorizzazioni. Nell'albero a sinistra, accanto a ogni ramo (DB, struttura, array) compare **Seleziona ramo**: con un clic si aggiungono ai grafici tutte le variabili del ramo che non lo sono già. Se superano il massimo di 64 segnali la selezione viene rifiutata con un messaggio che indica quante sono: si sceglie allora un sotto-ramo.

**UDP Direct Generic.** Il PLC invia ciclicamente un telegramma (formato Direct Generic documentato da iba); il ritmo lo stabilisce il programma PLC. Un telegramma valido produce un campione per ogni campo selezionato. L'applicazione segnala perdite di sequenza, timeout (watchdog) e overflow della coda marcando i campioni «Uncertain»; duplicati e telegrammi fuori sequenza sono contati nella diagnostica. Con un timestamp UTC nel payload il tempo dei campioni è quello del PLC.

### Timestamp PLC e coerenza di ciclo (classico e simbolico)

![connection_s7_time](Sezione «Timestamp PLC e coerenza di ciclo» del popup Connessione.)

ReadVar e S7CommPlus non forniscono il tempo del PLC. Per averlo, il programma PLC scrive a ogni ciclo un intero a 64 bit con l'ora UTC (ad esempio da `RD_SYS_T`) e lo si indica qui:

| N. | Campo |
|---|---|
| 1 | **Variabile / simbolo timestamp PLC**: indirizzo `DB1.DBD300:LINT` in modalità classica, simbolo (ad esempio `DB_Produzione.Orologio.Utc`) in modalità simbolica. Il tipo deve essere LINT, ULINT o LWORD. |
| 2 | **Unità del valore**: microsecondi dal 1970 UTC (LINT) oppure nanosecondi (LINT, LDT). |

Il valore viene letto insieme a ogni gruppo di variabili e salvato come timestamp sorgente (`source_timestamp`) di tutti i campioni del gruppo. Se il gruppo richiede più richieste ReadVar, il timestamp viene letto prima e dopo le altre variabili: se cambia, i valori non appartengono allo stesso ciclo PLC e sono marcati «Uncertain» con il motivo. Un timestamp non letto, di tipo errato o fuori dall'intervallo 2000–2199 (ad esempio nanosecondi indicati come microsecondi) marca i campioni «Uncertain» ma non li scarta. L'asse dei grafici resta l'ora di arrivo sul PC; il tempo del PLC è nei file registrati.

> Nelle prove la coerenza di ciclo è stata verificata con un server S7 simulato (modalità classica). La modalità simbolica non è stata provata con una CPU reale.

## MQTT

![connection_mqtt](Popup Connessione per MQTT.)

Si indicano broker, porta, versione, QoS, topic da sottoscrivere, base tempi e puntatori JSON. L'acquisizione è a eventi: ogni messaggio ricevuto viene elaborato. Space Fieldbus Analyzer si sottoscrive ai topic ma non pubblica né invia comandi. Il catalogo mostra solo topic e campi effettivamente ricevuti. Si possono indicare i puntatori JSON per timestamp e qualità e la base tempi (ricezione locale oppure timestamp del messaggio).

## Acquisizione simultanea

Dopo aver compilato i parametri di ogni protocollo, premere **Connetti** una sola volta. In LIVE, nel pannello Address Space, il menu Catalogo permette di passare da una sorgente all'altra senza perdere selezione, buffer e grafici. Una sorgente non raggiungibile segnala il proprio errore senza fermare le altre.
