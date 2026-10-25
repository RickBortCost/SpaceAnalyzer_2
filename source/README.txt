SPACE ANALYZER V3.0.2 WINDOWS x64

Estrarre lo ZIP e avviare Space_Analyzer_V3.0.2.exe.
Chiudere la versione precedente con Chiudi applicazione prima dell'avvio.
L'interfaccia si apre in una finestra applicazione dedicata (Microsoft Edge
in modalità app). Chiudere la finestra chiude l'applicazione e finalizza le
registrazioni in corso.
I file aperti in Registrazioni sono mostrati a piena risoluzione: tutti i
campioni del file, fino a 80 milioni per grafico.
Durante Record il pulsante lampeggia con il tempo trascorso e una barra
compatta mostra righe scritte, velocità, parte e occupazione della coda.
La cartella delle registrazioni si sceglie nella finestra di Record
(Sfoglia…) e resta memorizzata.
Applicazione interamente in C# (.NET 8 incluso nell'EXE): non serve
installare Python, .NET o altri componenti.

Registrazione: CSV, Parquet, FDA (.das), RBF. I file .dat si aprono e si
visualizzano; la registrazione .dat non è più disponibile.
Strumenti: Space_Analyzer_V3.0.2.exe --tool inspect|recover|export-fda|export-rbf.
Dettagli: CHANGELOG.md, VERSIONE_3.0.md e LEGGIMI_AVVIO_WINDOWS.md.

--- Note delle versioni precedenti ---

SPACE ANALYZER V1.29.0 WINDOWS x64

Estrarre lo ZIP e avviare Space_Analyzer_V1.29.0.exe.
Chiudere la versione precedente con Chiudi applicazione prima dell'avvio.
Runtime e dipendenze sono inclusi; non serve installare Python.

Record può ripartire mentre il precedente file viene salvato.
Stati e file pronti sono nel pannello Salvataggi in background.
FDA/Parquet più veloci da aprire; originali preservati.
Guida: GUIDA_REGISTRAZIONI_RAPIDE.md.

Marker e formule: GUIDA_CALCOLI_INTERVALLO.md. In Intera acquisizione
i calcoli leggono gli originali della sessione oltre il buffer recente.
Sotto il grafico aggiungi variabili A/B, scrivi [A]-[B] e premi Calcola.
Lo storico Live è temporaneo: usa Record per conservarlo.

Zoom live: pulsante accanto a Pausa. Conserva la durata X e i limiti Y
mentre il grafico segue gli ultimi dati, anche durante Record.
Pausa/Play manuale conserva lo zoom con Zoom live abilitato.
Guida: GUIDA_ZOOM_LIVE.md.

Acquisizione simultanea OPC UA, ADS, MQTT e S7, con cataloghi separati.
Formati: CSV, Parquet, FDA/DAS4, RBF, DAT/PDA3 e DAT/iba.
Guida: GUIDA_ACQUISIZIONE_SIMULTANEA.md.

Le verifiche usano peer locali e browser Chromium. La release Windows
viene pubblicata dopo build e avvio del vero EXE e prova di cinque formati.
PLC fisici, ADS nativo reale e DAT/iba con licenza restano da verificare.

Backend V1.29.0: originale recuperabile aggiuntivo in Recordings/.primary/, code per sorgente e diagnostica /api/acquisition/health.
Leggere FASE1_ACQUISIZIONE_REGISTRAZIONE.md per benchmark, recovery e limiti. Il target ADS 1000 canali a 10 ms richiede qualificazione su hardware reale.
