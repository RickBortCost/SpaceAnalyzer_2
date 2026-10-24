# Zoom live — V1.26.0

Il pulsante **Zoom live** si trova nella barra superiore, accanto a **Pausa**. Agisce su tutti i grafici della pagina LIVE e viene evidenziato quando è attivo.

1. Avvia l'acquisizione e seleziona i segnali da visualizzare.
2. Attiva **Zoom live** e trascina un rettangolo nel grafico per scegliere lo zoom XY.
3. La finestra temporale segue i nuovi campioni mantenendo la durata scelta. I limiti verticali selezionati restano fissi, anche con due assi Y. Il rettangolo seleziona la durata e le scale: in questa modalità il bordo destro torna agli ultimi dati disponibili.
4. Puoi ripetere lo zoom e usare il navigatore per cambiare l'ampiezza della finestra senza mettere la vista in pausa.

Se il grafico è già fermo per uno zoom, attivare **Zoom live** lo rimette in Play conservando la durata X e le scale Y. La modalità vale anche per i nuovi grafici e viene salvata nel workspace.

**Pausa** continua a fermare volontariamente il grafico attivo. Con Zoom live abilitato, **Play** riprende gli aggiornamenti mantenendo lo zoom XY. Se lo zoom è sincronizzato, i grafici in Play condividono la finestra X; un grafico messo manualmente in Pausa resta fermo finché non viene ripreso.

Disattivare **Zoom live** ripristina la pausa sullo zoom. **Ripristina zoom** o il doppio clic nel grafico tornano alla vista completa e all'autoscale, mantenendo la preferenza Zoom live per gli zoom successivi. Con sincronizzazione disattivata ogni grafico conserva la propria durata X e le proprie scale Y.

Il comando è disabilitato nella pagina Registrazioni. Non modifica i valori acquisiti, i formati, i campioni salvati o la registrazione: **Record** continua anche durante zoom e pause della vista. Le analisi FFT mantengono il requisito di una pausa esplicita e dei marker X1/X2.
