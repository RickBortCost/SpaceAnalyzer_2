# Istruzioni per il repository

- Rispondere e scrivere documentazione, commit e PR in italiano.
- Ad ogni pubblicazione (nuova versione): aggiungere in `CHANGELOG.md` la sezione `## X.Y.Z` con **tutte** le modifiche della versione e aggiornare la sezione "Novità della versione" di `README.md`. La pipeline usa quella sezione per la pagina della release e per il `README.txt` dello ZIP (`tools/release_notes.py`); `source/tests/test_release_notes.py` fallisce se manca la sezione della versione in `VERSION`.
- Versione: `VERSION` + `python tools/versioning.py apply X.Y.Z`; rigenerare `MANIFEST_SHA256.json` dopo le modifiche.
