# Avvio di Space Analyzer 3 su Windows

1. Apri la sezione **Releases** del repository e scarica dall’ultima release il file `Space_Analyzer_V<versione>_WINDOWS.zip` (ad esempio `Space_Analyzer_V3.0.0_WINDOWS.zip`). In alternativa puoi scaricare il solo EXE.
2. Clic destro sullo ZIP → **Estrai tutto**. Scegli una cartella scrivibile, ad esempio `Documenti\Space Analyzer`.
3. Fai doppio clic su **Space_Analyzer_V<versione>.exe**. Al centro dello schermo compare il logo con la barra di avvio; al termine si apre il browser con l’interfaccia.
4. Se il browser non si apre, visita `http://127.0.0.1:5000`; se la porta è occupata il software usa una delle successive (fino a 5019).
5. Nel popup **Connessione** configura i collegamenti. Attiva **Acquisizione simultanea** per usare insieme OPC UA, ADS, MQTT e S7, quindi premi **Connetti e leggi**.

## Novità della 3.0

- Il software è interamente in C# (.NET 8): acquisizione, registrazione, analisi e interfaccia web girano nel servizio `SpaceAnalyzer.exe` incorporato nell’EXE. Non serve installare Python, .NET o altri componenti sul PC di utilizzo.
- Formati di registrazione: **CSV**, **Parquet**, **FDA (.das)** e **RBF**. La registrazione `.dat` non è più disponibile; i file `.dat` esistenti si aprono e si visualizzano come prima.
- Al primo avvio di una nuova versione il servizio viene estratto in `%LOCALAPPDATA%\OPCUA_Web_Client\runtime` (qualche secondo); gli avvii successivi riutilizzano i file già estratti.
- Strumenti da riga di comando (catture primarie e conversioni):
  - `Space_Analyzer_V<versione>.exe --tool inspect <file.sac>`: verifica una cattura primaria;
  - `Space_Analyzer_V<versione>.exe --tool recover <file.sac.part>`: recupera una cattura interrotta;
  - `Space_Analyzer_V<versione>.exe --tool export-fda|export-rbf <originale.parquet> <nodi.json>`: converte un Parquet originale.

Dettagli in `CHANGELOG.md` e `VERSIONE_3.0.md` nel repository. Il numero di versione è assegnato automaticamente a ogni rilascio (vedi `VERSIONI.md`).

## Dati, registrazioni e chiusura

Le registrazioni vengono salvate nella cartella `Recordings` accanto all’EXE; le catture primarie recuperabili in `Recordings\.primary`. Le impostazioni sono in `%LOCALAPPDATA%\OPCUA_Web_Client` e restano compatibili con la 2.x.

Per chiudere il software usa **Chiudi applicazione** nell’interfaccia: chiudere soltanto la scheda del browser lascia attivi i servizi. Prima di avviare la nuova versione chiudi l’eventuale versione precedente; se è ancora attiva, il programma lo segnala.

Se non si avvia, consulta `%LOCALAPPDATA%\OPCUA_Web_Client\launcher-v<versione>.log` e `opcua-client.log` nella stessa cartella.

## Verifiche e componenti esterni

La Release viene pubblicata soltanto dopo: compilazione e test unitari C#, 21 workflow dell’interfaccia reale contro il servizio C# con peer locali OPC UA, ADS, MQTT, S7 e SSH, build dell’EXE su Windows, avvio del vero EXE con quattro sessioni indipendenti, registrazione e rilettura CSV, Parquet, FDA/DAS4 e RBF, verifica della cattura primaria, registrazioni sovrapposte, seconda apertura e arresto. Il rapporto è `WINDOWS_EXE_VERIFICATION.json`; gli hash sono in `SHA256SUMS.txt`.

I test non sostituiscono prove con PLC e reti industriali. ADS nativo richiede TwinCAT/TcAdsDll e la route appropriata. **S7 simbolico** (S7CommPlus) è implementato con la libreria S7CommPlusDriver ma va verificato su una CPU S7-1200/1500 reale: l’emulatore di test non supporta l’autenticazione. Per connessioni OPC UA protette occorre autorizzare il certificato client sul server e acquisire o importare il certificato del server. Le licenze dei componenti di terze parti sono in `THIRD_PARTY_NOTICES.txt` e nella cartella `licenses` dello ZIP.
