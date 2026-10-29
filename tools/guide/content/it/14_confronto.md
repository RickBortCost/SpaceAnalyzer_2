# 14. Confronto di più registrazioni

![compare](Confronto di due registrazioni (1 ms e 10 ms) nello stesso grafico, con scale Y indipendenti.)

Nell'elenco della pagina Registrazioni si spuntano da 2 a 32 file e si preme **Confronta nel grafico**. Ogni segnale conserva nome, file di origine e campionamento; nomi uguali in file diversi restano segnali distinti. Il grafico apre fino a 64 segnali (fino a 8 grafici; 2048 segnali complessivi).

| N. | Impostazione | Significato |
|---|---|---|
| 1 | **Riferimento temporale** | «Data e ora effettive (UTC)» (predefinito): la posizione sull'asse X deriva dai timestamp. «Allinea gli inizi al primo file»: confronta eventi avvenuti in momenti diversi; non indica simultaneità. |
| 2 | **Intervallo del confronto** | «Intera estensione» (conserva durate diverse e buchi) oppure «Solo intervallo comune» (errore esplicito se non c'è sovrapposizione). |
| 3 | **Correzione orario (ms)** | Per ciascun file: positivo sposta in avanti, negativo anticipa (ad esempio clock avanti di 1 s → −1000 ms). Il file originale non viene modificato. |
| 4 | **Confronta nel grafico** | Apre i file spuntati nel grafico di destinazione. |

Non c'è correzione della deriva dei clock né interpolazione o ricampionamento: una base a 1 ms può essere confrontata con una a 10 ms mantenendo ciascuna serie originale. I marker non riportano un valore fuori dalla copertura del segnale. FFT, formule ed export usano i campioni originali. Il confronto genera un file di appoggio nella cartella delle importazioni dell'applicazione.
