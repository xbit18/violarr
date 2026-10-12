# Panoramica WebUI

Apri `http://HOST:8000/` per controllare Violarr e modificarne le impostazioni.

## Cosa trovi

- **Panoramica** mostra stato ed errori. Tutte le schede devono essere sane.
- **Generali** mostra identità, sicurezza e indirizzi. Non richiede azioni.
- **Aggiornamenti database** gestisce i controlli. Di norma: ogni 24 ore.
- **Elaborazione risultati** gestisce preset e regole. Inizia senza filtri.
- **Prowlarr** salva la connessione. Lo stato atteso è **Connesso**.
- **[Diagnostica](../features/diagnostics)** esamina ricerche e richieste
  Torznab, le riesegue con lo stato attuale ed esporta report locali con
  anteprima privacy.
- **Avanzate** è una vista in sola lettura e non richiede azioni.

## Nuove versioni di Violarr

Quando è confermata una versione stabile più recente, la barra laterale mostra
**Versione {version} disponibile** con un link alla release GitHub esatta,
aperta in una nuova scheda. Non scarica, installa o riavvia nulla; è
indipendente dagli aggiornamenti del database ICVDB.

Il server controlla GitHub in background all'avvio e poi ogni ora. La WebUI
legge solo la cache locale all'apertura e ogni ora, con un solo tentativo
anticipato dopo 15 secondi se il primo controllo non mostra aggiornamenti.
Errori di rete e limiti GitHub restano silenziosi: l'ultimo risultato valido può
essere obsoleto. Prima di un risultato valido il link è nascosto.

Le versioni installate non valide, prerelease o esplicitamente di sviluppo non
mostrano notifiche. La versione del file `VERSION` identifica le immagini
ufficiali costruite dal tag di release, ma una build personalizzata con la
stessa versione stabile non è distinguibile automaticamente.

## Salvare e verificare

- Le impostazioni persistenti vengono salvate solo con il relativo pulsante
  **Salva**. Il monitoraggio diagnostico cambia immediatamente e resta solo in
  memoria.
- Attendi il messaggio di conferma prima di cambiare pagina.
- La chiave API salvata non viene mostrata: usa **Sostituisci chiave** solo per
  cambiarla.

## Se lo stato non è operativo

1. Premi **Aggiorna** in **Panoramica**.
2. Leggi l'errore nella scheda rossa e apri l'area indicata dal problema.
3. Se l'errore resta, consulta la [risoluzione dei problemi](../troubleshooting)
   e i log del container.

La WebUI non ha autenticazione: esponila solo su una rete affidabile o dietro un
reverse proxy autenticato.
