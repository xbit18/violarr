# API reference

The service exposes three HTTP surfaces on port `8000`.

| Prefix    | Purpose                            | Authentication                               |
| --------- | ---------------------------------- | -------------------------------------------- |
| `/`       | WebUI static application           | None                                         |
| `/api`    | Torznab XML endpoint               | None; `apikey` is accepted but not validated |
| `/webapi` | Same-origin JSON configuration API | None                                         |

## WebAPI endpoints

| Method | Path                        | `200` response                                | Handled error statuses |
| ------ | --------------------------- | --------------------------------------------- | ---------------------- |
| `GET`  | `/webapi/status`            | Aggregate service status                      | —                      |
| `GET`  | `/webapi/settings`          | Public schema-v1 settings                     | —                      |
| `PUT`  | `/webapi/settings`          | Validated effective public settings           | `422` invalid body     |
| `GET`  | `/webapi/result-processing` | Effective preset and custom rules             | —                      |
| `PUT`  | `/webapi/result-processing` | Validated effective preset and rules          | `422` invalid body     |
| `GET`  | `/webapi/prowlarr/status`   | Live status, including remote failure details | Always `200`           |
| `POST` | `/webapi/prowlarr/test`     | `{ "connected": true, "error": null }`        | `400`, `502`           |
| `POST` | `/webapi/prowlarr/indexer`  | Creation or existing-indexer result           | `400`, `502`           |

During the brief snapshot database switch, middleware can return HTTP `503` with
`Retry-After: 5` before normal handlers run, except the DB-independent cache
read `GET /webapi/application-update`.

## `GET /webapi/application-update`

Returns only the in-memory cache, without contacting GitHub or PostgreSQL:

```json
{
  "update_available": true,
  "latest_version": "1.10.0",
  "release_url": "https://github.com/xbit18/violarr/releases/tag/v1.10.0"
}
```

Hidden/unavailable state is `update_available: false` with both other fields
null. Only a newer stable release and an exact HTTPS repository release URL
matching its tag produce a link. Checks run in the background at startup and
hourly; failures preserve the last valid cache, which may be stale. This does
not update the application or snapshots. Like the rest of the WebAPI, it has no
authentication: use a trusted network or an authenticated reverse proxy.

## Diagnostics

| Method           | Path                             | Contract                                                         |
| ---------------- | -------------------------------- | ---------------------------------------------------------------- |
| `POST`           | `/webapi/diagnostics/search`     | Report v2; stages may fail despite HTTP `200`                    |
| `GET` / `PUT`    | `/webapi/diagnostics/monitoring` | `{enabled,capacity,count}`; PUT accepts only `{enabled:boolean}` |
| `GET` / `DELETE` | `/webapi/diagnostics/requests`   | `{requests:[...]}`; DELETE clears history                        |

Search accepts only `t` (`search`, `movie`, `tvsearch`), `q`, `imdbid`,
`tmdbid`, `season`, `ep`, `cat`, `limit` and `offset`. Strings: at most 512
characters; TMDb/season/episode: integers ±1000000000 or null; limit: 1–200
(default 100); offset: 0–1000000 (default 0). Unknown fields, wrong types and
out-of-range values return safe `422` detail
`{code:invalid_parameters,message:Invalid diagnostic parameters}`.

The report includes versions, timestamp, original/normalized parameters,
strategy, input/database/processing/serialization stage status/timing,
candidate, excluded, retained, outside-page, selected and returned counts,
windows, settings, releases, errors, duration, `truncated`, `replayable` and
interpretation limitations. Monitored requests are arrival-newest-first metadata
without releases or settings. Replay is a new POST with original parameters only
when `replayable`; there is no separate replay/export endpoint. There is no
authentication: see [diagnostics and privacy](../features/diagnostics).

Report v2 adds `strategies` execution records and `provenance` per result
identity, with optional `search` and `merge` stages. These describe observed
execution; unavailable deduplication, relevance and inclusion facts remain null.
Exact-ID searches report observed window memberships, exclusive contributions,
SQL deduplication and validated XML inclusion. Shared-query strategy
`duration_ms` is nullable; the shared duration is measured only at the database
stage. Match metadata may include allowlisted `source` (`torrent`, `file` or
`pack_file`) and `match_type=fuzzy`. These are categorical observations, not
child paths/identifiers or numeric confidence. Movie/TV title strategies run
alongside IDs; counts remain limited to inspected windows and bounded fuzzy
pools. See
[diagnostic interpretation](../features/diagnostics#strategy-executions-and-provenance).

## Database update progress

`updater.phase` is additive: `idle`, `checking`, `downloading`, `validating`,
`restoring`, `switching`, `up_to_date`, `updated`, or `failed`. Terminal phases
remain visible until the next operation; errors contain no raw exception
details. `updating` includes discovery. `next_check` is null during active work
or when disabled.

### Manual database update

`POST /webapi/database-update` (no body) returns `202` with
`{ "accepted": true }` after reserving the shared updater lock, before
background work finishes. It works with automatic updates disabled. A duplicate
returns `409` with
`{ "detail": { "code": "update_busy", "message": "A database update is already in progress" } }`.
Poll `GET /webapi/status` for the outcome; maintenance may temporarily return
`503`. This route has the same trusted-network requirement as the rest of the
unauthenticated WebAPI.

## `GET /webapi/status`

This endpoint probes PostgreSQL but does not contact Prowlarr. Its Prowlarr
section contains the latest in-process summary from a focused Prowlarr
operation, or null state before one has run.

| Field                                 | Type            | Meaning                                              |
| ------------------------------------- | --------------- | ---------------------------------------------------- |
| `api_version`                         | integer         | WebAPI schema version; currently `1`                 |
| `application_version`                 | string          | FastAPI application version                          |
| `database.connected`                  | boolean         | Result of the current database probe                 |
| `updater.installed_version`           | string or null  | Installed snapshot tag                               |
| `updater.latest_version`              | string or null  | Last discovered remote tag                           |
| `updater.enabled`                     | boolean         | Effective automatic-update state                     |
| `updater.updating`                    | boolean         | Whether an update is running                         |
| `updater.maintenance`                 | boolean         | Whether the database switch is in progress           |
| `updater.last_check`                  | string or null  | ISO-8601 timestamp                                   |
| `updater.next_check`                  | string or null  | ISO-8601 timestamp; null when disabled               |
| `updater.last_error`                  | string or null  | Last updater error                                   |
| `result_processing.preset`            | string          | Effective preset                                     |
| `result_processing.custom_rule_count` | integer         | Number of configured rules, including disabled rules |
| `prowlarr.configured`                 | boolean         | URL and API key are both effective                   |
| `prowlarr.connected`                  | boolean or null | Cached connection result                             |
| `prowlarr.indexer_installed`          | boolean or null | Cached installation result                           |
| `prowlarr.error`                      | string or null  | Cached sanitized error                               |

## Settings

`GET /webapi/settings` and a successful `PUT /webapi/settings` return:

| Field                              | Type                                                           |
| ---------------------------------- | -------------------------------------------------------------- |
| `schema_version`                   | integer `1`                                                    |
| `database_update.enabled`          | boolean                                                        |
| `database_update.interval_seconds` | integer, 60–604800                                             |
| `result_processing.preset`         | `unfiltered`, `italian_preferred`, `italian_only`, or `custom` |
| `result_processing.custom_rules`   | array                                                          |
| `prowlarr.url`                     | string                                                         |
| `prowlarr.indexer_url`             | string                                                         |
| `prowlarr.api_key_configured`      | boolean                                                        |

`PUT` accepts an optional write-only `prowlarr.api_key`: omission preserves the
persisted key, a non-empty string replaces it, and an empty string clears it.
Validation or unknown fields return HTTP `422` with `{ "detail": "..." }`. A
successful full-settings update also reconfigures the updater and clears cached
Prowlarr status.

## Result processing

Both result-processing routes use this body:

```json
{
  "preset": "italian_preferred",
  "custom_rules": []
}
```

`GET` returns the effective object. `PUT` returns the validated effective
object; an invalid preset, rule, field, operator, action, value, or unknown
field returns HTTP `422` with a detail string.

## Prowlarr status

`GET /webapi/prowlarr/status` returns HTTP `200` with all four fields:

| Field               | Type            | Behavior                                                                               |
| ------------------- | --------------- | -------------------------------------------------------------------------------------- |
| `configured`        | boolean         | False unless URL and API key are effective                                             |
| `connected`         | boolean or null | Null when unconfigured; otherwise the live connection result                           |
| `indexer_installed` | boolean or null | Null when unconfigured or status setup fails; otherwise the live endpoint match result |
| `error`             | string or null  | Sanitized specific failure or null                                                     |

Normal remote failures remain status data rather than HTTP errors. For example:

```json
{
  "configured": true,
  "connected": false,
  "indexer_installed": false,
  "error": "Unable to connect to Prowlarr"
}
```

Specific safe errors include connection, remote HTTP status, response-size,
invalid-JSON, and invalid-schema failures. If an error contains the configured
secret or traceback text, it is replaced with `Prowlarr request failed`.

## Prowlarr commands

| Route                           | Success body                                                                          | `400`                                         | `502`                                                                            |
| ------------------------------- | ------------------------------------------------------------------------------------- | --------------------------------------------- | -------------------------------------------------------------------------------- |
| `POST /webapi/prowlarr/test`    | `{ "connected": true, "error": null }`                                                | Prowlarr URL or API key missing               | Remote/client failure; `{ "detail": "Prowlarr request failed" }`                 |
| `POST /webapi/prowlarr/indexer` | `{ "created": boolean, "already_installed": boolean, "indexer_id": integer or null }` | Prowlarr URL, API key, or Indexer URL missing | Schema, test, list, or create failure; `{ "detail": "Prowlarr request failed" }` |

An already-installed indexer returns `created: false` and
`already_installed: true`. A newly created indexer returns the inverse. The ID
is null when Prowlarr does not provide an integer ID.

## Secret behavior

Normal JSON reads never include the saved or environment-provided API key.
`GET /webapi/settings` exposes only `api_key_configured`; status responses
expose no key field. Prowlarr command errors always use the stable generic
detail. Prowlarr status errors may retain a sanitized specific reason, but any
reason containing the configured key or traceback text is replaced. See the
[configuration schema](./configuration-schema) for replacement and clear rules.

## Torznab

See [Torznab capabilities](./torznab-capabilities) for query and XML details.
