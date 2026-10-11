# Aggiornamenti del database

Apri **Aggiornamenti database** nella WebUI per mantenere aggiornati i dati
usati nelle ricerche.

1. Lascia **Aggiornamenti automatici** attivi per la maggior parte delle
   installazioni.
2. Inserisci un intervallo numerico e scegli minuti, ore o giorni. Il valore
   predefinito è `1` giorno; i limiti inclusivi sono 1 minuto e 7 giorni. I
   decimali devono produrre secondi interi (ad esempio `1.5` minuti); i valori
   non validi non vengono arrotondati.
3. Premi **Salva impostazioni aggiornamenti** e attendi la conferma.

Premi **Controlla e aggiorna ora** per un controllo asincrono, anche con gli
aggiornamenti automatici disattivati. L'accettazione indica l'avvio del
controllo, non il completamento dell'aggiornamento. Lo stato distingue
controllo, download, verifica, ripristino, attivazione, database già aggiornato,
aggiornamento completato ed errore. Durante un controllo il pulsante è
disabilitato; altre richieste ricevono una risposta di operazione in corso. Il
polling riprova dopo risposte temporanee di manutenzione senza modificare le
impostazioni non salvate.

Modificare l'intervallo a riposo pianifica il prossimo controllo dal
salvataggio; abilitare i controlli permette un controllo immediato,
disabilitarli elimina la scadenza. Le modifiche durante un'operazione non la
annullano e non ne accodano un'altra. Al termine, anche dei controlli manuali,
il prossimo controllo viene pianificato dal completamento con l'intervallo
effettivo più recente, oppure non viene pianificato se disabilitato. Le
variabili d'ambiente mantengono la precedenza.

La scheda **Snapshot database** in **Panoramica** mostra versione installata,
ultima versione rilevata, ultimo e prossimo controllo. **Aggiornamento** o
**Manutenzione attiva** sono stati temporanei; **Errore** richiede di leggere il
messaggio e i log.

Per i dettagli tecnici consulta gli
[aggiornamenti sicuri](../how-it-works/safe-updates).
