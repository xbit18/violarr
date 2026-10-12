# Risoluzione dei problemi

Inizia dai log del container:

```bash
docker logs -f icvdb-torznab
```

## La WebUI non si apre

- Verifica che la porta `8000` sia pubblicata.
- Esegui `curl 'http://localhost:8000/api?t=caps'`.
- Verifica che il container usi `ghcr.io/xbit18/violarr:latest` e ricrealo dopo
  aver scaricato l'immagine aggiornata.
- Un checkout dei sorgenti deve compilare gli asset frontend prima che FastAPI
  possa servirli.

## Il primo avvio richiede molto tempo

Una nuova installazione scarica, verifica, ispeziona e ripristina un dump
PostgreSQL prima di avviare FastAPI. Segui i log e conserva il volume `/data`.

## Il test di Prowlarr fallisce

1. Verifica che l'URL di Prowlarr sia raggiungibile dal container Violarr.
2. Se Violarr gira in Docker e Prowlarr è installato come servizio sul PC host,
   con Docker Desktop usa normalmente `http://host.docker.internal:9696`.
3. Se entrambi sono container sulla stessa rete Docker, usa il nome del
   servizio, ad esempio `http://prowlarr:9696`.
4. Verifica la API key in **Settings → General → Security** di Prowlarr.

## Prowlarr non riesce ad aggiungere Violarr

Se **Verifica connessione** funziona ma **Aggiungi Violarr a Prowlarr**
fallisce, la connessione Violarr → Prowlarr funziona: il problema è normalmente
nella direzione opposta, Prowlarr → Violarr.

- Se Prowlarr gira sullo stesso host Windows e Violarr pubblica la porta `8000`,
  usa normalmente `http://localhost:8000/api`.
- Se entrambi sono container sulla stessa rete Docker, usa
  `http://icvdb-torznab:8000/api`.
- L'URL deve terminare con `/api`.
- Controlla il dettaglio mostrato dalla WebUI, la console del browser e i log di
  Prowlarr.

## Mancano risultati attesi

Apri **[Diagnostica](./features/diagnostics) → Ricerca** per distinguere
ricerca, filtri e output XML validato senza cambiare le impostazioni. Se il
problema appare solo nei client, abilita il monitoraggio in **Richieste**,
esamina i parametri reali e rieseguili. La riesecuzione usa lo stato attuale,
non quello storico.

Lo snapshot locale può differire dai risultati live di Stremio. Nessun candidato
nelle finestre esaminate non prova l'assenza dall'intero database. Una risposta
Torznab valida non spiega da sola il rifiuto di Sonarr/Radarr. Prima di
condividere un report, controlla titoli e termini di ricerca nell'anteprima.

- `italian_only` e le regole di esclusione sono filtri rigidi.
- L'ordinamento è locale a finestre di 1000 righe.
- L'elaborazione non garantisce la selezione di Radarr o Sonarr.
- Torna a `unfiltered` per confrontare l'ordine originale.

## L'aggiornamento è temporaneamente indisponibile

HTTP `503` con `Retry-After: 5` è previsto durante la breve sostituzione del
database. Gli errori persistenti compaiono nella dashboard e nei log. Prima
della sostituzione, un errore lascia invariato il database attivo.

## Le impostazioni non corrispondono al file salvato

Controlla le [variabili d'ambiente](./configuration/environment): gli override
di runtime prevalgono sui valori persistenti senza riscrivere `settings.json`.
