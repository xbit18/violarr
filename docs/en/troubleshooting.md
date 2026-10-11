# Troubleshooting

Start with container logs:

```bash
docker logs -f icvdb-torznab
```

## WebUI does not open

- Confirm port `8000` is published.
- Check `curl 'http://localhost:8000/api?t=caps'`.
- Confirm the container uses `ghcr.io/xbit18/violarr:latest` and recreate it
  after pulling the updated image.
- A source checkout must build frontend assets before FastAPI can serve them.

## First start takes a long time

Fresh installations download, verify, inspect, and restore a PostgreSQL dump
before FastAPI starts. Follow the logs and preserve the `/data` volume between
retries.

## Prowlarr Test fails

1. Confirm the Prowlarr URL is reachable from the Violarr container.
2. If Violarr runs in Docker and Prowlarr is installed as a Windows service on
   the host machine, Docker Desktop can normally reach it at
   `http://host.docker.internal:9696`.
3. If both applications run as containers on the same Docker network, use the
   service name, for example `http://prowlarr:9696`.
4. Confirm the API key in Prowlarr under **Settings → General → Security**.

## Prowlarr cannot add Violarr

If **Test connection** succeeds but **Add Violarr to Prowlarr** fails, the
Violarr → Prowlarr connection is working. The problem is usually in the opposite
direction: Prowlarr → Violarr.

- If Prowlarr runs on the same Windows host and Violarr publishes port `8000`,
  normally use `http://localhost:8000/api`.
- If both applications run as containers on the same Docker network, use
  `http://icvdb-torznab:8000/api`.
- The URL must end with `/api`.
- Check the detail shown in the WebUI, the browser console, and the Prowlarr
  logs.

## Expected releases are missing

Open **[Diagnostics](./features/diagnostics) → Search** to distinguish lookup,
filtering and validated XML output without changing settings. If the problem
only appears in clients, enable monitoring in **Requests**, inspect actual
parameters and replay them. Replay uses current state, not historical
conditions.

The local snapshot may differ from live Stremio results. No candidates in
inspected windows does not prove absence from the whole database. A valid
Torznab response alone cannot explain Sonarr/Radarr rejection. Before sharing a
report, review titles and search terms in its preview.

- `italian_only` and custom exclusion rules are hard filters.
- Ranking is local to 1000-row database windows.
- Result processing cannot guarantee Radarr/Sonarr selection.
- Switch back to `unfiltered` to compare the original database order.

## Update is temporarily unavailable

HTTP `503` with `Retry-After: 5` is expected during the short database switch.
Persistent updater errors appear on the WebUI dashboard and in logs. The active
database remains unchanged for failures before the switch.

## Settings do not match the saved file

Check [environment variables](./configuration/environment). Runtime overrides
win over persisted values without rewriting `settings.json`.
