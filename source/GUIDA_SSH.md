# Space Fieldbus Analyzer 1.12.0 — Debug SSH

## Avvio e collegamento

Estrarre tutto lo ZIP in una cartella scrivibile. Chiudere l'istanza precedente usando **Chiudi applicazione**, quindi avviare `Space_Analyzer_V1.14.0.exe`. Aprire la pagina **Debug SSH** dalla barra superiore.

Per il PAC indicato, il profilo iniziale propone:

| Parametro | Valore iniziale |
|---|---|
| Protocollo | SSH2 |
| IP | `192.168.5.220` |
| Porta | `22` |
| Utente | `Administrator`, modificabile |
| Autenticazione | Password oppure chiave privata |
| Timeout connessione | 10 secondi |
| Keepalive | 20 secondi |
| Output | UTF-8, modificabile |

Inserire le **credenziali effettive** del target: nessuna password è preimpostata. Il servizio SSH deve essere attivo e raggiungibile dal PC che esegue Space Fieldbus Analyzer. Username e password sono sensibili alle maiuscole. Per autenticazione a chiave la chiave pubblica deve essere autorizzata dal target; selezionare il file privato OpenSSH/PEM e la passphrase, se cifrato. RSA, Ed25519 ed ECDSA sono supportati. Convertire i file PuTTY PPK in OpenSSH tramite PuTTYgen.

Premere **Connetti SSH**. Al primo collegamento appare l'impronta SHA256 della chiave host: confrontarla con un'impronta ottenuta da un canale affidabile e premere **Impronta verificata · Accetta**. Prima dell'accettazione il client non invia l'autenticazione al target. Una chiave diversa da quella salvata blocca il collegamento. Dopo aver verificato una sostituzione prevista del target/chiave, disconnettere e utilizzare **Rimuovi chiave salvata** per ripetere la verifica. La richiesta di verifica scade dopo 120 secondi.

La funzione è distinta dalle sorgenti ADS/OPC UA/S7: è possibile acquisire e registrare dati mentre si eseguono comandi SSH. Il client usa thread dedicati per rete e console; non esegue i comandi sul PC Windows locale. I comandi hanno i permessi dell'utente remoto e possono modificare il sistema se il contenuto lo prevede. Nessun comando viene eseguito automaticamente dopo la connessione o la riconnessione.

## Tre modalità

**Comando**: incollare, per esempio, `uname -a` e premere **Esegui sul target**. La console mostra progressivamente stdout, stderr in rosso e il codice di uscita quando fornito dal target. Ogni esecuzione apre un processo remoto nuovo: `cd` o variabili impostate in un comando non rimangono nel successivo. Abilitare PTY se il programma richiede un terminale/input interattivo; PTY può unire stdout e stderr e cambia il buffering del programma remoto.

**Script multilinea**: incollare il testo oppure usare **Apri script locale**. Il contenuto viene inviato allo stdin dell'interprete sul target, senza salvare un file remoto. Il default `/bin/sh` è adatto agli script POSIX per FreeBSD; per altro interprete indicare il percorso assoluto disponibile sul target, senza opzioni. Gli script Bash richiedono Bash installato e il suo percorso effettivo. La selezione di un file `.py` non installa Python e non cambia automaticamente l'interprete. Il contenuto è UTF-8; CRLF Windows viene normalizzato a LF. Massimo 128 KiB. Lo stdin è riservato allo script, quindi non è disponibile input manuale durante questa modalità. Per programmi che richiedono input usare Comando con PTY o Shell interattiva.

**Shell interattiva**: premere **Apri shell interattiva**, poi scrivere le righe nel campo sotto la console e premere **Invia input** o Invio. `cd` e variabili di ambiente rimangono nella stessa shell. Sono disponibili **Ctrl+C** (PTY) ed **EOF**. `exit` chiude normalmente la shell. La console è testuale, non un emulatore VT100 completo: programmi come editor, `top` a schermo intero e applicazioni con sequenze terminale complesse richiedono un terminale dedicato. Le sequenze ANSI vengono filtrate; output remoto mai interpretato come HTML. Le righe inviate a una shell possono essere conservate nella cronologia remota secondo la configurazione del target.

Il pulsante **Inserisci diagnostica FreeBSD** prepara uno script con `uname -a`, `uptime`, `ifconfig`, `df -h` e una lista dei processi. Non lo esegue: revisionare il testo e premere Esegui. Non contiene arresto TwinCAT, reboot o modifiche alla configurazione.

## Stop, timeout e output

Una sessione permette un comando/script/shell alla volta. **Stop canale** chiude il canale attivo e consente una nuova esecuzione mantenendo il collegamento SSH. **Disconnetti** chiude anche la sessione. La durata massima di un'esecuzione è configurabile tra 0 e 86400 secondi (default 300; 0 senza limite), con chiusura del canale al timeout. Stop/timeout non garantiscono la terminazione di processi remoti in background, `nohup`, servizi o figli che ignorano la chiusura: verificarli sul target. La perdita di rete non riesegue automaticamente i comandi; riconnettere esplicitamente.

Buffer backend 2 MiB / 8192 eventi, visualizzazione limitata agli ultimi 2 MiB circa. Il client continua a leggere entrambi i flussi anche quando il buffer è pieno, per evitare blocchi sul controllo di flusso SSH; avvisa quando righe precedenti sono rimosse o non più disponibili. Per grandi acquisizioni di log usare un file sul target o un altro strumento adatto. La comparsa immediata dell'output dipende anche dal buffering del programma remoto.

**Scorri automaticamente** controlla lo scorrimento della console, che può essere ridimensionata verticalmente. **Pulisci console** è disponibile a esecuzione ferma. **Esporta output TXT** scarica il contenuto attualmente visibile, con target e stato del comando; non recupera parti già eliminate dal buffer.

IP, porta, username e parametri sono in `%LOCALAPPDATA%/OPCUA_Web_Client/settings/ssh/ssh_profile.json` nell'EXE; chiavi host pubbliche in `ssh_known_hosts.json`. Password, passphrase, chiavi private, comandi e output non vengono salvati automaticamente dall'applicazione. Le credenziali sono mantenute solo durante il tentativo di connessione; i campi sensibili vengono svuotati dopo l'invio. Log applicativi limitati a stato, tipo di errore, byte e codice di uscita. Esportazioni TXT e cronologia eventualmente salvata dal target dipendono dall'utente/configurazione remota. I parametri SSH sono un profilo locale separato dal workspace dei grafici.

## Verifiche e limiti

Test automatici con server SSH2 indipendente AsyncSSH su loopback: password, chiave Ed25519 cifrata, verifica prima dell'autenticazione, rifiuto/cambio chiave, Unicode a byte separati, output stdout/stderr superiore a 11 MiB, buffer, script multilinea, shell/input, concorrenza, Stop/timeout e riuso della sessione. Test HTML/JS con JSDOM, HTTP locale e acquisizione OPC UA simultanea; canvas simulato.

L'EXE include Paramiko e le dipendenze Windows: Python, pip e PuTTY non servono sul PC di utilizzo. Non sono stati eseguiti un collaudo Windows né un collegamento al PAC fisico `192.168.5.220`. Non sono inclusi SFTP, forwarding, MFA, SSH agent, salvataggio password o terminale grafico completo. La chiusura della sola scheda browser non ferma il servizio: utilizzare Stop/Disconnetti o Chiudi applicazione.

Riferimenti primari:

- [Beckhoff — Accesso remoto SSH a TwinCAT/BSD](https://infosys.beckhoff.com/content/1033/twincat_bsd/5619943435.html)
- [Paramiko — Transport SSH2](https://docs.paramiko.org/en/4.0/api/transport.html)
- [Paramiko — Channel e controllo di flusso](https://docs.paramiko.org/en/4.0/api/channel.html)
