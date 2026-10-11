# Riferimento API

Il servizio espone tre superfici HTTP sulla porta `8000`.

| Prefisso  | Scopo                                  | Autenticazione                                   |
| --------- | -------------------------------------- | ------------------------------------------------ |
| `/`       | Applicazione statica WebUI             | Nessuna                                          |
| `/api`    | Endpoint XML Torznab                   | Nessuna; `apikey` è accettato ma non convalidato |
| `/webapi` | API JSON same-origin di configurazione | Nessuna                                          |

## Endpoint WebAPI

| Metodo | Percorso                    | Risposta `200`                             | Errori gestiti        |
| ------ | --------------------------- | ------------------------------------------ | --------------------- |
| `GET`  | `/webapi/status`            | Stato aggregato del servizio               | —                     |
| `GET`  | `/webapi/settings`          | Impostazioni pubbliche schema v1           | —                     |
| `PUT`  | `/webapi/settings`          | Impostazioni effettive convalidate         | `422` body non valido |
| `GET`  | `/webapi/result-processing` | Preset e regole effettivi                  | —                     |
| `PUT`  | `/webapi/result-processing` | Preset e regole convalidati                | `422` body non valido |
| `GET`  | `/webapi/prowlarr/status`   | Stato live, inclusi errori remoti          | Sempre `200`          |
| `POST` | `/webapi/prowlarr/test`     | `{ "connected": true, "error": null }`     | `400`, `502`          |
| `POST` | `/webapi/prowlarr/indexer`  | Risultato di creazione o indexer esistente | `400`, `502`          |

Durante la breve sostituzione del database, il middleware può restituire HTTP
`503` con `Retry-After: 5` prima dell'handler normale di qualsiasi route.

## Diagnostica

| Metodo           | Percorso                         | Contratto                                                        |
| ---------------- | -------------------------------- | ---------------------------------------------------------------- |
| `POST`           | `/webapi/diagnostics/search`     | Report v2; le fasi possono fallire anche con HTTP `200`          |
| `GET` / `PUT`    | `/webapi/diagnostics/monitoring` | `{enabled,capacity,count}`; PUT accetta solo `{enabled:boolean}` |
| `GET` / `DELETE` | `/webapi/diagnostics/requests`   | `{requests:[...]}`; DELETE svuota la cronologia                  |

La ricerca accetta solo `t` (`search`, `movie`, `tvsearch`), `q`, `imdbid`,
`tmdbid`, `season`, `ep`, `cat`, `limit` e `offset`. Stringhe: massimo 512
caratteri; TMDb/stagione/episodio: interi ±1000000000 o null; limite: 1–200
(predefinito 100); offset: 0–1000000 (predefinito 0). Campi sconosciuti, tipi
errati o valori fuori limite restituiscono `422` con dettaglio sicuro
`{code:invalid_parameters,message:Invalid diagnostic parameters}`.

Il report include versioni, timestamp, parametri originali/normalizzati,
strategia, fasi input/database/processing/serialization con stato/durata,
conteggi candidati, esclusi, mantenuti, fuori pagina, selezionati e restituiti,
finestre, impostazioni, release, errori, durata, `truncated`, `replayable` e
limiti interpretativi. Le richieste monitorate sono metadati in ordine inverso
di arrivo, senza release o impostazioni. Per rieseguire usa un nuovo POST con i
parametri originali solo se `replayable`; non esiste un endpoint replay/export
separato. Non c'è autenticazione: consulta
[diagnostica e privacy](../features/diagnostics).

Il report v2 aggiunge le esecuzioni `strategies` e la `provenance` per identità
del risultato, con fasi opzionali `search` e `merge`. Descrivono l'esecuzione
osservata; i dati non disponibili su deduplicazione, rilevanza e inclusione
rimangono null.

## `GET /webapi/status`

L'endpoint verifica PostgreSQL ma non contatta Prowlarr. La sezione Prowlarr
contiene l'ultimo riepilogo in memoria, oppure valori nulli prima di
un'operazione.

| Campo                                 | Tipo            | Significato                                    |
| ------------------------------------- | --------------- | ---------------------------------------------- |
| `api_version`                         | integer         | Versione schema WebAPI, attualmente `1`        |
| `application_version`                 | string          | Versione dell'applicazione FastAPI             |
| `database.connected`                  | boolean         | Esito della verifica corrente                  |
| `updater.installed_version`           | string or null  | Tag snapshot installato                        |
| `updater.latest_version`              | string or null  | Ultimo tag remoto individuato                  |
| `updater.enabled`                     | boolean         | Stato effettivo degli aggiornamenti automatici |
| `updater.updating`                    | boolean         | Aggiornamento in corso                         |
| `updater.maintenance`                 | boolean         | Sostituzione del database in corso             |
| `updater.last_check`                  | string or null  | Timestamp ISO-8601 dell'ultimo controllo       |
| `updater.next_check`                  | string or null  | Prossimo controllo; null se disabilitato       |
| `updater.last_error`                  | string or null  | Ultimo errore dell'updater                     |
| `result_processing.preset`            | string          | Preset effettivo                               |
| `result_processing.custom_rule_count` | integer         | Numero di regole, incluse quelle disabilitate  |
| `prowlarr.configured`                 | boolean         | URL e API key entrambe effettive               |
| `prowlarr.connected`                  | boolean or null | Esito della connessione in cache               |
| `prowlarr.indexer_installed`          | boolean or null | Esito dell'installazione in cache              |
| `prowlarr.error`                      | string or null  | Errore sanificato in cache                     |

## Impostazioni

`GET /webapi/settings` e un `PUT /webapi/settings` riuscito restituiscono:

| Campo                              | Tipo                                                              |
| ---------------------------------- | ----------------------------------------------------------------- |
| `schema_version`                   | integer `1`                                                       |
| `database_update.enabled`          | boolean                                                           |
| `database_update.interval_seconds` | integer, 60–604800                                                |
| `result_processing.preset`         | `unfiltered`, `italian_preferred`, `italian_only` oppure `custom` |
| `result_processing.custom_rules`   | array                                                             |
| `prowlarr.url`                     | string                                                            |
| `prowlarr.indexer_url`             | string                                                            |
| `prowlarr.api_key_configured`      | boolean                                                           |

`PUT` accetta `prowlarr.api_key` facoltativo e di sola scrittura: ometterlo
conserva la chiave, una stringa non vuota la sostituisce e una stringa vuota la
cancella. Campi non validi o sconosciuti restituiscono HTTP `422` con
`{ "detail": "..." }`. Un aggiornamento completo riuscito riconfigura anche
l'updater e cancella lo stato Prowlarr in cache.

## Elaborazione dei risultati

Entrambe le route usano questo body:

```json
{
  "preset": "italian_preferred",
  "custom_rules": []
}
```

`GET` restituisce l'oggetto effettivo. `PUT` restituisce quello convalidato;
preset, regole, campi, operatori, azioni, valori o campi sconosciuti non validi
producono HTTP `422` con un dettaglio.

## Stato Prowlarr

`GET /webapi/prowlarr/status` restituisce HTTP `200` con quattro campi:

| Campo               | Tipo            | Comportamento                                                                 |
| ------------------- | --------------- | ----------------------------------------------------------------------------- |
| `configured`        | boolean         | False se URL e API key non sono entrambe effettive                            |
| `connected`         | boolean or null | Null senza configurazione; altrimenti esito live                              |
| `indexer_installed` | boolean or null | Null senza configurazione o in caso di errore iniziale; altrimenti esito live |
| `error`             | string or null  | Motivo sanificato oppure null                                                 |

Gli errori remoti normali restano dati di stato invece di errori HTTP:

```json
{
  "configured": true,
  "connected": false,
  "indexer_installed": false,
  "error": "Unable to connect to Prowlarr"
}
```

Gli errori sicuri specifici includono connessione, stato HTTP remoto, risposta
troppo grande, JSON non valido e schema non valido. Se il testo contiene il
segreto configurato o `traceback`, viene sostituito con
`Prowlarr request failed`.

## Comandi Prowlarr

| Route                           | Risposta in caso di successo                                                          | `400`                               | `502`                                                    |
| ------------------------------- | ------------------------------------------------------------------------------------- | ----------------------------------- | -------------------------------------------------------- |
| `POST /webapi/prowlarr/test`    | `{ "connected": true, "error": null }`                                                | URL o API key mancanti              | Errore remoto; `{ "detail": "Prowlarr request failed" }` |
| `POST /webapi/prowlarr/indexer` | `{ "created": boolean, "already_installed": boolean, "indexer_id": integer or null }` | URL, API key o Indexer URL mancanti | Errore remoto; stesso dettaglio stabile                  |

Un indexer già installato restituisce `created: false` e
`already_installed: true`; uno appena creato restituisce l'inverso. L'ID è null
se Prowlarr non fornisce un integer ID.

## Gestione dei segreti

Le letture JSON non includono mai la API key salvata o fornita dall'ambiente.
`GET /webapi/settings` espone solo `api_key_configured` e le risposte di stato
non hanno un campo chiave. Gli errori dei comandi usano sempre il dettaglio
generico stabile; gli errori di stato possono mantenere un motivo specifico
sanificato. Consulta lo [schema di configurazione](./configuration-schema).

## Torznab

Consulta le [funzionalità Torznab](./torznab-capabilities) per query e XML.
