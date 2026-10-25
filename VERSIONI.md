# Versionamento automatico

Le versioni seguono il formato **MAJOR.MINOR.PATCH** (Semantic Versioning) e coincidono con i tag Git `vX.Y.Z`. Non si modificano più a mano i numeri in `Config/Defaults.cs`, nei file `.csproj`, in `tools/windows/build_windows.py`, nel launcher e nello smoke test: lo fa la pipeline.

## Come viene scelto il numero

A ogni push su `main` che tocca `source/`, `tools/windows/`, `LEGGIMI_AVVIO_WINDOWS.md`, `VERSION` o il workflow, GitHub Actions:

1. calcola la versione con `python tools/versioning.py next`;
2. la scrive nei file di build (`tools/versioning.py apply`);
3. esegue i test, compila l’EXE, lo avvia e verifica le registrazioni;
4. solo se tutto passa, crea il tag `vX.Y.Z` e pubblica la release con EXE, ZIP, manifest, verifica e hash.

Il numero dipende dai messaggi dei commit arrivati dopo l’ultimo tag:

| Messaggio di commit | Effetto | Esempio |
|---|---|---|
| contiene `BREAKING CHANGE`, oppure `tipo!:` o `major:` | MAJOR +1 | 2.4.1 → 3.0.0 |
| inizia con `feat:` o `minor:` | MINOR +1 | 2.4.1 → 2.5.0 |
| qualsiasi altro (`fix:`, `docs:`, testo libero) | PATCH +1 | 2.4.1 → 2.4.2 |

Il primo rilascio usa il valore del file `VERSION` (2.0.0). Per un salto deciso a mano, ad esempio verso 3.0.0, scrivi `3.0.0` in `VERSION` e fai push.

## Comportamenti utili

- Le **pull request** verso `main` vengono compilate e provate, ma non pubblicano nulla.
- Se un’esecuzione fallisce non viene creato nessun tag: il push successivo riprova con lo stesso numero.
- Rieseguire il workflow sullo stesso commit ripubblica la stessa versione, senza crearne una nuova.
- I rilasci su `main` sono eseguiti uno alla volta, quindi due push ravvicinati non ottengono lo stesso numero.
- Le modifiche solo alla documentazione (fuori dai percorsi sopra) non generano una release.

## Comandi locali

    python tools/versioning.py next       # quale versione uscirebbe ora
    python tools/versioning.py apply X.Y.Z
    python tools/versioning.py check      # i file di build coincidono con VERSION?
    python -m unittest discover -s source/tests -p test_versioning.py
