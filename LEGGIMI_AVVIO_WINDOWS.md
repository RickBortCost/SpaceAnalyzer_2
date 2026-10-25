# Avvio di Space Analyzer 3 su Windows

1. Apri la sezione **Releases** del repository e scarica dall’ultima release il file `Space_Analyzer_V<versione>_WINDOWS.zip` (ad esempio `Space_Analyzer_V3.0.0_WINDOWS.zip`). In alternativa puoi scaricare il solo EXE.
2. Clic destro sullo ZIP → **Estrai tutto**. Scegli una cartella scrivibile, ad esempio `Documenti\Space Analyzer`.
3. Fai doppio clic su **Space_Analyzer_V<versione>.exe**. Al centro dello schermo compare il logo con la barra di avvio; al termine si apre la finestra dell’applicazione (Microsoft Edge in modalità applicazione, senza schede né barra degli indirizzi).
4. Se la finestra non si apre, visita `http://127.0.0.1:5000`; se la porta è occupata il software usa una delle successive (fino a 5019).
5. Nel popup **Connessione** configura i collegamenti. Attiva **Acquisizione simultanea** per usare insieme OPC UA, ADS, MQTT e S7, quindi premi **Connetti e leggi**.

## Caratteristiche

- Software interamente in C# (.NET 8 incluso nell’EXE): non serve installare Python, .NET o altri componenti.
- Registrazione in **CSV**, **Parquet**, **FDA (.das)** e **RBF**; i file `.dat` esistenti si aprono e si visualizzano.
- Grafici: doppio clic su un segnale per aprirlo in un nuovo grafico; trascina un canale della legenda su un altro per condividere la scala Y (tasto destro sulla legenda per separarla).
- Al primo avvio di una nuova versione il servizio viene estratto nella cartella dati (qualche secondo); gli avvii successivi riutilizzano i file già estratti.

## Novità

Le modifiche di ogni versione sono in testa a questa pagina della release, nel file `README.txt` dello ZIP e in `CHANGELOG.md` nel repository.

Strumenti da riga di comando (catture primarie e conversioni):
- `Space_Analyzer_V<versione>.exe --tool inspect <file.sac>`: verifica una cattura primaria;
- `Space_Analyzer_V<versione>.exe --tool recover <file.sac.part>`: recupera una cattura interrotta;
- `Space_Analyzer_V<versione>.exe --tool export-fda|export-rbf <originale.parquet> <nodi.json>`: converte un Parquet originale.

Il numero di versione è assegnato automaticamente a ogni rilascio (vedi `VERSIONI.md`).

## Dati, registrazioni e chiusura

**Cartella dati.** Dalla 3.0.3 tutto ciò che l’applicazione scrive sta sul disco dell’EXE: registrazioni in `Recordings`, e impostazioni, log, servizio estratto, storico Live, cache, file aperti dal PC e file temporanei in `SpaceAnalyzer_Data` accanto all’EXE. Se l’EXE è su D:, l’applicazione non scrive su C: (resta l’uso in sola lettura di Windows e di Microsoft Edge installati su C:, che salva il profilo della finestra applicazione in `SpaceAnalyzer_Data\webapp`). Al primo avvio impostazioni e file importati vengono copiati dalla cartella precedente `%LOCALAPPDATA%\OPCUA_Web_Client` (che resta per le versioni precedenti) e le sue cache vengono eliminate, liberando spazio su C:. Se la cartella dell’EXE non è scrivibile si usa `%LOCALAPPDATA%\OPCUA_Web_Client`; la variabile d’ambiente `OPCUA_DATA_DIR` indica una cartella a scelta. Lo storico Live lasciato da una chiusura anomala viene eliminato all’avvio successivo.

Le registrazioni vengono salvate nella cartella `Recordings` accanto all’EXE, oppure nella cartella scelta nella finestra di Record (**Sfoglia…**, o percorso scritto a mano; **Predefinita** torna a `Recordings`). La stessa cartella è usata per gli export e mostrata nella pagina Registrazioni (**Cambia…**), e resta memorizzata ai riavvii. Le catture primarie recuperabili sono nella sottocartella `.primary`.

Per chiudere il software chiudi la finestra dell’applicazione oppure usa **Chiudi applicazione**: in entrambi i casi le registrazioni in corso vengono finalizzate e i servizi si arrestano. Se la finestra è stata aperta nel browser predefinito (Edge e Chrome assenti, oppure `SPACE_WINDOW=browser`), chiudere la scheda lascia attivi i servizi: usa **Chiudi applicazione**. Prima di avviare la nuova versione chiudi l’eventuale versione precedente; se è ancora attiva, il programma lo segnala.

Se non si avvia, consulta `launcher-v<versione>.log`, `opcua-client.log` ed eventuali `crash-<data>.txt` nella cartella dati.

## Verifiche e componenti esterni

La Release viene pubblicata soltanto dopo: compilazione e test unitari C#, 23 workflow dell’interfaccia reale contro il servizio C# con peer locali OPC UA, ADS, MQTT, S7 e SSH, build dell’EXE su Windows, avvio del vero EXE con quattro sessioni indipendenti, registrazione e rilettura CSV, Parquet, FDA/DAS4 e RBF, verifica della cattura primaria, registrazioni sovrapposte, seconda apertura e arresto. Il rapporto è `WINDOWS_EXE_VERIFICATION.json`; gli hash sono in `SHA256SUMS.txt`.

I test non sostituiscono prove con PLC e reti industriali. ADS nativo richiede TwinCAT/TcAdsDll e la route appropriata. **S7 simbolico** (S7CommPlus) è implementato con la libreria S7CommPlusDriver ma va verificato su una CPU S7-1200/1500 reale: l’emulatore di test non supporta l’autenticazione. Per connessioni OPC UA protette occorre autorizzare il certificato client sul server e acquisire o importare il certificato del server. Le licenze dei componenti di terze parti sono in `THIRD_PARTY_NOTICES.txt` e nella cartella `licenses` dello ZIP.
