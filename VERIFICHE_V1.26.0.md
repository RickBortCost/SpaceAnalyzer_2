# Zoom live — verifiche V1.26.0

## Comportamento

Il nuovo pulsante **Zoom live**, nella barra superiore accanto a Pausa, controlla la pausa automatica durante lo zoom sui grafici LIVE. È disattivato per impostazione predefinita e viene salvato nel workspace.

Quando è attivo, il grafico mantiene la durata X e i limiti Y1/Y2 scelti; la finestra temporale segue gli ultimi dati. L'attivazione su uno zoom in pausa lo rimette in Play conservando quelle scale. Pausa manuale continua a fermare il grafico e Play conserva lo zoom. Sono gestiti anche navigatore, barra temporale, nuovi grafici e sincronizzazione X. Disattivare la modalità ripristina la pausa sullo zoom; le registrazioni storiche restano indipendenti.

I driver e i codec di registrazione non sono modificati. La correzione del menu Catalogo della V1.25.1 è inclusa.

## Prove eseguite

Sei flussi DOM superati:

- `test_live_zoom_ui.cjs`: script caricati dalla pagina reale; zoom rettangolare, durata X costante, due scale Y fisse, dati oltre il buffer precedente, variazioni di grandezza, zoom ripetuti, Pausa/Play, grafici sincronizzati e indipendenti, navigatore, reset, cinque lingue e riapertura della pagina con modalità persistita.
- `test_source_sessions_ui.cjs`: quattro driver contro peer locali, cataloghi ADS/OPC UA/S7/MQTT, zoom inizialmente in pausa e successiva attivazione Zoom live durante Record CSV; snapshot di analisi rilasciato alla ripresa, finestra in avanzamento, scale mantenute e quattro segnali presenti nella rilettura.
- `test_chart_tools_ui.cjs`: comandi dei grafici, overflow e indipendenza delle azioni.
- `test_studio_ui.cjs`: grafici, assi, marker, pannelli, persistenza e registrazioni storiche.
- `test_live_full_ui.cjs`: intera sessione oltre l'evizione del buffer, zoom, pausa e sincronizzazione.
- `test_i18n_ui.cjs`: lingue, dati operatore, dialoghi e registrazione durante il cambio lingua.

I canvas DOM sono simulati. È stata eseguita anche una prova separata in **Chromium con canvas e rendering reali**, mantenendo la Content Security Policy dell'applicazione: zoom tramite mouse, pulsante Zoom live, Pausa/Play, quattro sorgenti, Record CSV e rilettura dei quattro segnali. Il pulsante è visibile anche a 1366 px di larghezza. [Rapporto browser](evidence_v1.26.0/browser_live_zoom.json) e [schermata](evidence_v1.26.0/zoom_live.png).

Log e rapporti sono in `evidence_v1.26.0/`. Per riprodurre le prove DOM, con Node 24, `jsdom@30.0.1`, `source/requirements.txt` e `python-snap7==2.0.2`:

```sh
JSDOM_PATH=/path/to/node_modules/jsdom UI_SCRIPT=test_live_zoom_ui.cjs python source/tests/test_source_sessions_ui_harness.py
JSDOM_PATH=/path/to/node_modules/jsdom python source/tests/test_source_sessions_ui_harness.py
```

Per Chromium installare anche Playwright nel medesimo ambiente Python e indicare un eseguibile Chromium locale:

```sh
python tools/verify_live_zoom_browser.py --chromium /usr/bin/chromium --report /tmp/browser_zoom.json --screenshot /tmp/zoom_live.png
```

## Windows e limiti

**Build e verifiche Windows superati; EXE e ZIP pubblicati il 22 ottobre 2026.** [Workflow](https://github.com/RickBortCost/SpaceAnalizer/actions/runs/39792672754), [Zoom live su Windows](evidence_v1.26.0/windows_live_zoom_ui.json), [cataloghi e Record misto su Windows](evidence_v1.26.0/windows_catalog_ui.json), [rapporto del vero EXE](releases/v1.26.0/WINDOWS_EXE_VERIFICATION.json) e [asset/hash](releases/v1.26.0/WINDOWS_RELEASE.json). Commit compilato: `8ffc9954cb5acc6deeb9f14801503028c13e6f2d`.

Il workflow Windows rende obbligatorie le due prove DOM su Zoom live e protocolli prima di compilare. Verifica poi il payload, avvia il vero EXE e controlla CSV, Parquet, FDA/DAS4, RBF e PDA3, istanza singola e arresto. Controlla inoltre che il nuovo comando sia presente nell'interfaccia incorporata. Il pacchetto include `GUIDA_ZOOM_LIVE.md`.

La prova Chromium usa Linux e sorgenti, non il browser nativo del PC Windows destinatario. I peer sono locali: PLC fisici, ADS nativo reale, licenza DAT/iba e compatibilità nelle applicazioni native restano da verificare nell'ambiente destinatario.
