# Test SSH — dipendenze solo di test

Dalla cartella `source`, in un ambiente Python isolato:

```sh
python -m pip install -r requirements.txt
python -m pip install asyncssh==2.24.1 python-snap7==2.0.2
python tests/test_ssh.py
python tests/test_ssh_api.py
npm ci --prefix tests
```

Workflow UI su Linux/macOS:

```sh
UI_SCRIPT=test_ssh_ui.cjs python tests/test_ui_harness.py
```

In PowerShell: `$env:UI_SCRIPT='test_ssh_ui.cjs'`, poi `python tests/test_ui_harness.py`.
Il server AsyncSSH di prova è indipendente dal client Paramiko applicativo; usa solo loopback, dati di prova e directory temporanee. Non si connette al PAC reale. Il peer restituisce output/script di prova: non emula l'intero sistema FreeBSD. AsyncSSH è esclusivamente una dipendenza di test e non è incluso nell'EXE.

Ogni suite Python applicativa deve essere eseguita in un processo distinto per isolare le directory dei fixture. I test JSDOM usano i reali file HTML/JS e servizio HTTP, con canvas simulato; non sono verifica visuale pixel né collaudo Windows. I test API verificano token/origine locale, validazione, limiti richiesta e assenza di credenziali nel profilo.
