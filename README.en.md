<h1 align="center">
  <a href="https://github.com/henriquesebastiao/downtify" target="_blank" rel="noopener noreferrer">
    <picture>
      <img width="150"src="docs/public/logo.png" alt="Logo di Violarr" width="180">
    </picture>
  </a>
  <br>
  Violarr
</h1>

<p align="center">
  <strong>Self-hosted wrapper to use ICVDB as a Prowlarr indexer</strong>
</p>

<div align="center">

[![CI](https://img.shields.io/github/actions/workflow/status/xbit18/violarr/ci.yml?label=test)](https://github.com/xbit18/violarr/actions/workflows/ci.yml)
[![GitHub Release](https://img.shields.io/github/v/release/xbit18/violarr)](https://github.com/xbit18/violarr/releases)
[![GitHub License](https://img.shields.io/github/license/xbit18/violarr)](/LICENSE)
[![Visitors](https://api.visitorbadge.io/api/visitors?path=xbit18%2Fviolarr&label=repository%20visits&countColor=%231182c3&style=flat)](https://github.com/xbit18/violarr)
![Dynamic JSON Badge](https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fghcr-badge.elias.eu.org%2Fapi%2Fxbit18%2Fviolarr%2Fviolarr&query=downloadCount&label=Image%20pulls&color=red)

[Documentation](https://xbit18.github.io/violarr/en) ·
[Installation](https://xbit18.github.io/violarr/en/getting-started/installation) ·
[How to use](https://xbit18.github.io/violarr/en/configuration/webui) ·
[Changelog](https://github.com/xbit18/violarr/blob/main/CHANGELOG.md)

</div>

## Features

- Search movies, TV, and anime from Prowlarr.
- Configure everything from the WebUI in Italian or English.
- Prefer Italian results or build custom rules.
- Keep the database updated automatically.
- Inspect searches, filters and Torznab requests in **Diagnostics**, with replay and local JSON reports.

## Quick start

```bash
docker run -d \
  --name icvdb-torznab \
  -p 8000:8000 \
  -v icvdb_torznab_data:/data \
  --restart unless-stopped \
  ghcr.io/xbit18/violarr:latest
```

When the service is ready, open `http://localhost:8000/` and complete the
[first setup](https://xbit18.github.io/violarr/en/getting-started/first-setup).

## Documentation

For a missing release, open **Diagnostics → Search**; use **Requests** and enable
monitoring to inspect actual client parameters (at most 100 in-memory requests,
cleared on restart). Replay uses the current snapshot and settings. Counts cover
only inspected windows, not the entire database or live Stremio results; Violarr
cannot determine why Sonarr or Radarr rejects a release.
See the [diagnostics guide](https://xbit18.github.io/violarr/en/features/diagnostics).

WebUI and APIs have no authentication: use a trusted network or an authenticated
reverse proxy. Before sharing JSON reports, review the preview: search terms and
titles can remain sensitive despite structural redaction.

- [Installation, setup, and usage](https://xbit18.github.io/violarr/en/)
- [Architecture](ARCHITECTURE.md)
- [Changelog](CHANGELOG.md)

## Contributing

See the [contributing guide](CONTRIBUTING.md) for the project workflow.

## License

MIT
