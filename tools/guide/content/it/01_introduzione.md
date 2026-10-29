# 1. Cos'è Space Fieldbus Analyzer

Space Fieldbus Analyzer è un software Windows per acquisire, visualizzare e registrare dati da PLC e sistemi industriali, per leggere dati da database di serie temporali e per analizzarli dopo l'acquisizione. Le sorgenti live supportate sono **OPC UA**, **ADS** (Beckhoff TwinCAT), **MQTT** e **S7** (Siemens): possono essere usate anche insieme (acquisizione simultanea, fino a 64 segnali complessivi), ciascuna con sessione, errori e riconnessione indipendenti. I dati storici si leggono da **InfluxDB 2.x** e da **PostgreSQL** (anche TimescaleDB), sempre in sola lettura.

Dalla versione 3.0 il prodotto non contiene più Python. L'EXE Windows è composto da un launcher con splash (Go), da un servizio ASP.NET Core (.NET 8, incorporato) e dal motore di acquisizione e registrazione (`SpaceAnalyzer.Engine.dll`). L'interfaccia è una pagina web servita in locale: l'accesso è consentito solo da `127.0.0.1`/localhost e ogni richiesta di modifica richiede un token.

| Sezione | A cosa serve |
|---|---|
| **LIVE** | Trend in tempo reale, selezione dei segnali, marker X1/X2, formule sull'intervallo, FFT, Record. |
| **Registrazioni** | Apertura e analisi dei file registrati (CSV, Parquet, FDA/DAS4, RBF, DAT), confronto di più file. |
| **Database ▾** | Menu con **InfluxDB** (server InfluxDB 2.x) e **PostgreSQL**: grafici, vista tabellare, query SQL salvate ed esportazione dei dati. |
| **Strumenti ▾** | Menu con **Conversione** (da un formato di file all'altro), **Dati calcolati** (formule condivise tra Live e registrazioni) e **Debug SSH** (console verso un target remoto, indipendente dall'acquisizione). |
| **Connessione** | Fonte dati live, parametri di connessione, intervallo di lettura e buffer. |

## Novità dalla 3.0.8 alla {{version}}

| Versione | Cosa cambia |
|---|---|
| **3.1.0** | Doppio clic su un segnale: crea un grafico con quel solo segnale. Scale Y indipendenti per segnale con assi colorati (fino a 4 colonne per lato). Scale condivise trascinando i nomi nella legenda; menu contestuale. Fino a 24 grafici per pagina. |
| **3.2.x** | Data e ora del PC nell'intestazione. X1/X2 non più nella barra: i marker si posizionano dall'analisi X1 ↔ X2. Zoom e spostamento per singola scala Y, «+N Y», legenda da tastiera, pallino di stato della connessione. |
| **3.3.0** | Rotella sul grafico LIVE = periodo visualizzato (asse X). |
| **3.4.x** | Nuovo nome **Space Fieldbus Analyzer** in interfaccia, launcher, log ed esportazioni; il nome compare nell'intestazione da 1500 px; menu Impostazioni in posizione fissa; chiusura senza l'errore «Failed to fetch». |
| **3.5.x** | Nuova sezione **Conversione** tra formati (CSV, Parquet, FDA DAS4, RBF v3, DAT); apertura dei DAT scritti da ibaPDA; cursore e griglia allineati tra i grafici dello stesso file in Registrazioni; i file FDA si dividono prima di superare 255 MB di RAM nel programma FDA. |
| **3.6.0** | Tipo di dato scelto automaticamente (RBF con canali Boolean/Int16 nativi, FDA a 16 bit esatti con offset, meno memoria nei grafici LIVE). |
| **3.7.0** | Pagina **InfluxDB** (lettura da InfluxDB 2.x) e menu **Strumenti** (Conversione, Dati calcolati, Debug SSH). |
| **3.8.x** | **Analisi X1 ↔ X2 generale**: una sola sezione a fondo pagina che mostra i valori del grafico selezionato (non più una per grafico). Tasto **Riduci a barra** accanto al pieno schermo. **Riordino dei grafici** con drag&drop. |
| **3.9.x** | ADS: notifiche cicliche raggruppate (fino a 50 ms) e priorità di Windows durante l'acquisizione. InfluxDB: valori X1/X2 calcolati, fino a 100.000 punti per serie, larghezza regolabile. |
| **3.10.0** | **Esportazione dei dati InfluxDB** in CSV, Parquet, FDA o RBF, con periodo e cartella a scelta. |
| **3.11.0** | Menu **Database** con InfluxDB e **PostgreSQL**. |
| **3.12.x** | PostgreSQL: **vista tabellare**, **query SQL** di sola lettura e **query salvate**. Il nome di un segnale vale per tutti i grafici della pagina. |
| **3.13.0** | Questa guida illustrata è inclusa nel pacchetto di ogni versione. |
| **3.14.0** | S7: **timestamp PLC** e controllo della coerenza di ciclo (classico e simbolico); **Seleziona ramo** nell'albero dei simboli S7. |

## Avvio su Windows

Scaricare dall'ultima release il file `Space_Fieldbus_Analyzer_V<versione>_WINDOWS.zip`, estrarlo in una cartella scrivibile e avviare `Space_Fieldbus_Analyzer_V<versione>.exe`. Compare lo splash con la barra di avvio, poi si apre l'interfaccia nel browser (porta 5000 o la prima libera successiva). L'avvio è a schermo intero (F11 per uscire) e la pagina non scorre: barra superiore e barra di stato restano fisse e i pannelli scorrono al loro interno. Il pulsante **Spegni** (simbolo di accensione, in alto a destra) chiude l'applicazione. Chiudere solo la scheda del browser lascia attivo il servizio.

## Flusso di lavoro tipico

1) Connessione alla sorgente → 2) scansione e scelta dei segnali → 3) trend live e analisi con marker → 4) Record → 5) analisi della registrazione (FFT, formule, confronto, export). In alternativa: Database → InfluxDB o PostgreSQL → serie, periodo e grafico → esportazione.
