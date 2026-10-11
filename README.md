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
  <strong>Wrapper self-hosted per usare ICVDB come indexer Prowlarr</strong>
</p>

<div align="center">

[![CI](https://img.shields.io/github/actions/workflow/status/xbit18/violarr/ci.yml?label=test)](https://github.com/xbit18/violarr/actions/workflows/ci.yml)
[![GitHub Release](https://img.shields.io/github/v/release/xbit18/violarr)](https://github.com/xbit18/violarr/releases)
[![GitHub License](https://img.shields.io/github/license/xbit18/violarr)](/LICENSE)
[![Visitors](https://api.visitorbadge.io/api/visitors?path=xbit18%2Fviolarr&label=repository%20visits&countColor=%231182c3&style=flat)](https://github.com/xbit18/violarr)
![Dynamic JSON Badge](https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fghcr-badge.elias.eu.org%2Fapi%2Fxbit18%2Fviolarr%2Fviolarr&query=downloadCount&label=Image%20pulls&color=red)

[Documentazione](xbit18.github.io/violarr) ·
[Installazione](https://xbit18.github.io/violarr/getting-started/installation) ·
[Utilizzo](https://xbit18.github.io/violarr/configuration/webui) ·
[Changelog](https://github.com/xbit18/violarr/blob/main/CHANGELOG.md)

</div>

## Funzionalità

- Cerca film, serie TV e anime da Prowlarr.
- Configura tutto dalla WebUI in italiano o inglese.
- Preferisci i risultati italiani o crea regole personalizzate.
- Mantieni il database aggiornato automaticamente.
- Esamina ricerche, filtri e richieste Torznab in **Diagnostica**, con riesecuzione e report JSON locali.

## Avvio rapido

```bash
docker run -d \
  --name icvdb-torznab \
  -p 8000:8000 \
  -v icvdb_torznab_data:/data \
  --restart unless-stopped \
  ghcr.io/xbit18/violarr:latest
```

Quando il servizio è pronto, apri `http://localhost:8000/` e completa la
[prima configurazione](https://xbit18.github.io/violarr/getting-started/first-setup).

## Documentazione

Per una release mancante, apri **Diagnostica → Ricerca**; per i parametri effettivi
dei client usa **Richieste** e abilita il monitoraggio (massimo 100 richieste in memoria,
azzerate al riavvio). La riesecuzione usa snapshot e impostazioni attuali. I conteggi
riguardano solo le finestre esaminate, non tutto il database né i risultati live di
Stremio; Violarr non determina perché Sonarr o Radarr rifiutano una release.
Consulta la [guida diagnostica](https://xbit18.github.io/violarr/features/diagnostics).

WebUI e API non hanno autenticazione: usa una rete affidabile o un reverse proxy
autenticato. Prima di condividere un report JSON, controlla l'anteprima: termini di
ricerca e titoli possono essere sensibili anche dopo l'oscuramento strutturale.

- [Installazione, configurazione e utilizzo](https://xbit18.github.io/violarr/)
- [Architecture](ARCHITECTURE.md)
- [Changelog](CHANGELOG.md)

## Contribuire

Consulta la [guida per contribuire](CONTRIBUTING.md), disponibile in inglese.

## Licenza

MIT
