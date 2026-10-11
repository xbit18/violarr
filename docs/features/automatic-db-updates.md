# Aggiornamenti automatici del database

L'applicazione controlla periodicamente l'ultima release di
[`xbit18/icvdb-snapshots`](https://github.com/xbit18/icvdb-snapshots), scarica
il primo asset `.dump` e richiede il digest `sha256:` dell'asset.

Il database attivo resta disponibile durante download, ispezione, ripristino e
convalida del candidato. Solo la sostituzione finale avviene in manutenzione.

Puoi disabilitare i controlli automatici, scegliere un intervallo numerico in
minuti/ore/giorni o richiedere un controllo manuale nella WebUI. Le operazioni
manuali e automatiche condividono la stessa pipeline: le richieste duplicate non
avviano altri aggiornamenti. Consulta i
[controlli aggiornamenti](../configuration/database-updates) per stato e
pianificazione. Una nuova installazione richiede comunque uno snapshot valido
prima di avviare FastAPI.

Consulta [sistema snapshot](../how-it-works/snapshots) e
[aggiornamenti sicuri](../how-it-works/safe-updates) per il flusso completo.
