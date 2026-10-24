# Ripetere le verifiche S7

Usare un ambiente Python separato (Python 3.11 o successivo). Dalla cartella `source`:

```sh
python -m pip install -r requirements.txt
python -m pip install python-snap7==2.0.2
python -m unittest discover -s tests -p test_s7.py -v
npm ci --prefix tests
python tests/test_s7_ui_harness.py
```

Il server **nativo Snap7** indipendente è una dipendenza solo di test; non viene distribuito nell'EXE. Il test apre una porta TCP effimera su loopback, usa memoria di prova e cartelle temporanee, non collega un PLC di produzione. Sono verificate anche letture multiple e un DB assente nello stesso pacchetto. La release python-snap7 3.0.0 del server Python non gestisce tutte le letture multiple di questa prova: usare la versione nativa fissata sopra.

Il workflow UI usa i file HTML/JS reali e il backend HTTP, con canvas simulato in JSDOM: non verifica rendering pixel per pixel né l'esecuzione Windows. Per regressioni eseguire ciascun `test_*.py` unitario in un processo separato; alcuni fixture importano l'applicazione con directory temporanee proprie. `test_ui_harness.py` seleziona un workflow tramite `UI_SCRIPT`; per `test_calculations_ui.cjs` e `test_studio_ui.cjs` impostare anche `MULTICHART_TEST=1` per generare i file storici di prova. `test_s7_ui_harness.py` è un workflow separato.

Il driver applicativo utilizza solo libreria standard Python, non il client Snap7. Sono ammesse esclusivamente apertura trasporto, Setup Communication e ReadVar. Le prove non certificano prestazioni a 10 ms, coerenza di ciclo CPU o compatibilità con ogni firmware Siemens.
