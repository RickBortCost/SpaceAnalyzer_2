# 7. Dati calcolati: formule

Le formule creano segnali derivati. Esistono due modi: il pannello rapido sotto i grafici e la pagina dedicata «Dati calcolati» (menu Strumenti).

## Pannello rapido sotto i grafici

![calc_quick](Formula [A]-[B] con A = Temperatura e B = Setpoint, risultato Max tra i marker X1/X2.)

| N. | Elemento |
|---|---|
| 1 | **Variabili**: scegliere la Variabile A; con «Aggiungi variabile» si assegnano B, C… (anche di protocolli diversi). |
| 2 | **Formula** con i riferimenti tra parentesi quadre: `[A]-[B]`, `([A]+[B])/2`, `mean([A],[B])`, `abs([A])`, `ifelse([B]>0,[A]/[B],0)`. Operatori e funzioni: + − × ÷ ( ) abs sqrt mean min max pow ifelse; confronti < <= > >= == !=. |
| 3 | **Allineamento**: «Ultimo valore valido» usa solo campioni precedenti entro la tolleranza (ms); «Timestamp identici» richiede campioni allo stesso istante. |
| 4 | **Risultato**: Ultimo valore, Media, Min, Max, RMS, Peak-to-Peak, Deviazione standard, X2−X1, Δ/Δt, Integrale. |
| 5 | **Valore** calcolato, con intervallo, campioni validi ed esclusi. L'intervallo è quello tra i marker se entrambi presenti, altrimenti la finestra visibile. |
| 6 | **Salva e aggiungi**: registra la formula tra i Dati calcolati e la aggiunge al grafico; il segnale viene acquisito da quel momento. |

Le qualità non valide, i valori scaduti e gli errori matematici producono un punto vuoto con spiegazione: non vengono sostituiti con zero. Durante Record occorre fermare la registrazione prima di salvare modifiche alle formule.

## Pagina «Dati calcolati»

![calc_page](Editor completo delle formule (menu Strumenti → Dati calcolati).)

| N. | Elemento |
|---|---|
| 1 | **Nome** del segnale calcolato. |
| 2 | **Uso**: in Live e/o nelle registrazioni. |
| 3 | **Sorgenti**: elenco dei segnali disponibili; un clic inserisce il riferimento nella formula. |
| 4 | **Formula**. |
| 5 | **Valida**: controlla sintassi e riferimenti. |
| 6 | **Test e anteprima**: mostra valori, qualità e una curva senza salvare. |
| 7 | **Salva formula**: la rende attiva. |

Una formula può valere per Live, per le registrazioni o per entrambe; sulle registrazioni si può usare l'intervallo X1/X2 e salvare un nuovo CSV o Parquet con i segnali originali e il risultato. In Live le formule attive usano una sola fonte per volta; le notifiche vengono riordinate in una finestra di 20 ms.
