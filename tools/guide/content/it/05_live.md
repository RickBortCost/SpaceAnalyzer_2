# 5. La pagina LIVE: segnali e grafici

![live](Pagina LIVE con due grafici verticali: ogni segnale ha la propria scala Y colorata (Y1…Y4).)

| N. | Elemento |
|---|---|
| 1 | **Grafico attivo**: i segnali spuntati vengono assegnati al grafico selezionato in questo menu. |
| 2 | **Segnali live**: casella per assegnare il segnale al grafico, barra colorata, valore, qualità, scala (Y1, Y2…) e menu ⋮. Doppio clic sul nome: crea un grafico con quel solo segnale. |
| 3 | **Vista tempo**: «Intera acquisizione» (tutta la sessione) oppure «Ultima finestra» (periodo recente). |
| 4 | **Disposizione**: 1 grafico, 2 verticali, 2 orizzontali, 4 grafici, libero a colonna o a griglia. |
| 5 | **+ Grafico**: aggiunge un grafico (fino a 24 per pagina). |
| 6 | **FFT / Export**: analisi spettrale ed esportazione dell'intervallo X1–X2 (capitolo 13). |
| 7 | **Area del grafico**: trascinare per lo zoom, rotella = periodo visualizzato, rotella sugli assi = zoom di quella scala, doppio clic per il reset; sotto, il navigatore della finestra temporale. |
| 8 | **Analisi X1 ↔ X2**: sezione unica a fondo pagina con marker e tabella dei valori (capitolo 6). |

## Scale Y per segnale, legenda e rotella

Ogni segnale ha la propria scala Y: gli assi sono colorati come il segnale, numerati Y1, Y2… e disposti fino a 4 colonne per lato. Oltre il limite la scala resta calcolata ma senza colonna e compare l'indicazione «+N Y».

**Scale condivise.** Trascinando il nome di un canale sopra un altro nella legenda i due condividono una sola scala (ripetibile senza limiti di canali); l'autoscale usa minimo e massimo complessivi dei campioni validi del gruppo. Unire canali con unità fisiche diverse richiede una conferma esplicita («scala numerica senza equivalenza fisica»): i valori non vengono mai modificati. Da tastiera: Spazio o frecce prendono un canale, Invio lo rilascia su un altro, Canc lo separa, Esc annulla.

![legend_menu](Menu contestuale della legenda (tasto destro): Separa scala, Unisci tutte le scale, Separa tutte le scale.)

![legend_shared](Dopo aver trascinato «Forza» su «Pressione» i due canali condividono la scala Y1.)

**Zoom per singola scala.** La rotella sopra una colonna asse ingrandisce o riduce solo quella scala attorno al cursore (con Maiusc la sposta del 10 %); il trascinamento verticale sull'asse la sposta; il doppio clic sull'asse ripristina la scala.

**Rotella sull'area del grafico = periodo visualizzato.** Verso l'alto riduce il periodo, verso il basso lo aumenta (passo ×1,25), ancorato all'ultimo campione. La riga di stato indica «Periodo visualizzato» e i secondi mostrati, per ricordare che il grafico non mostra l'intera sessione.

![wheel](Dopo alcuni scatti di rotella il grafico mostra gli ultimi secondi: «Periodo visualizzato».)

## Nome, colore e proprietà di un segnale

Dal menu ⋮ del segnale (o dalla legenda) si apre «Segnale · Grafico»:

![signal_properties](Finestra delle proprietà del segnale.)

| N. | Campo |
|---|---|
| 1 | **Segnale**: permette di passare alle proprietà di un altro segnale del grafico. |
| 2 | **Nome / alias**: il nome mostrato in legenda, tabelle e registrazioni. **Vale per tutti i grafici** della pagina: se si sposta il segnale in un altro grafico mantiene il nome assegnato. |
| 3 | **Unità visualizzata**. |
| 4 | **Asse**: scala Y a sinistra o a destra, a cui il segnale è collegato. |
| 5 | **Colore** della curva e dell'asse. |
| 6 | **Spessore linea** in pixel. |
| 7 | **Visibile nel grafico**: nasconde la curva senza toglierla dall'acquisizione. |

Colore, spessore, asse e visibilità sono specifici del singolo grafico; il nome è globale. Record, FFT ed export conservano sempre i valori originali. «Rimuovi dall'acquisizione» toglie il segnale dalla selezione.

## Menu del grafico e opzioni

![chart_menu](Menu ⋮ del grafico.)

Il menu ⋮ raccoglie: **Configura assi** (autoscale o limiti manuali), **Configura segnali**, **Sincronizza tempo**, **Esporta immagine PNG**, **Rinomina**, **Duplica**, **Pulisci** (azzera la vista di quel grafico senza toccare gli altri né la registrazione) e **Rimuovi grafico**. «Sincronizzazione» (in alto) allinea zoom e crosshair tra i grafici.

## Riduci a barra e riordino dei grafici

![collapse_reorder](Grafico 1 espanso, Grafico 2 ridotto a barra.)

| N. | Elemento |
|---|---|
| 1 | **Maniglia di trascinamento** (⠿): si trascina il grafico per cambiarne la posizione nella pagina. |
| 2 | **Riduci a barra** (simbolo ▁): il grafico si riduce alla sola intestazione (titolo, segnali e stato) e libera spazio; nella barra ridotta lo stesso pulsante (▣) lo ripristina. L'acquisizione continua. |
| 3 | **Espandi** (pieno schermo): porta il grafico a tutta pagina; lo stesso pulsante riporta alla vista normale. |

## Pausa, Play e Zoom live

Pausa congela soltanto la vista: i dati continuano a essere acquisiti e registrati. Play riporta la vista ai dati aggiornati. **Zoom live**: attivarlo, trascinare un rettangolo sul grafico per scegliere durata X e scala Y; la finestra continua a seguire i nuovi campioni con la durata scelta e i limiti verticali fissi. Ripristina zoom (o doppio clic) torna alla vista completa. Zoom live si salva nel workspace.

Il Live usa la stessa precisione delle registrazioni: il grafico contiene ogni campione acquisito. Quando i campioni visibili superano i pixel, ogni colonna di pixel mostra l'intervallo minimo–massimo di tutti i suoi campioni (i picchi di un solo campione restano visibili) e con lo zoom compaiono i singoli punti. Marker, formule, FFT ed export usano sempre i campioni originali.
