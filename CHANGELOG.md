# Changelog

All notable changes to this project are documented here.

## [1.2.0](https://github.com/xbit18/violarr/compare/v1.1.5...v1.2.0) (2026-10-12)


### Features

* add copy action for diagnostic history JSON ([33b0aa8](https://github.com/xbit18/violarr/commit/33b0aa8105604d1c648221d5d4d6d4d4b96334ad))
* add search diagnostics and request monitoring ([f2077b4](https://github.com/xbit18/violarr/commit/f2077b4b76661c8fea6b34924c6d061906af44b2))
* expand search coverage with fuzzy matching and movie packs ([#56](https://github.com/xbit18/violarr/issues/56)) ([da986e1](https://github.com/xbit18/violarr/commit/da986e1438eae67cd7dd4d48a5474c91a99400fb))
* improve database snapshot update controls ([#57](https://github.com/xbit18/violarr/issues/57)) ([8521825](https://github.com/xbit18/violarr/commit/85218259fa7bd4bdf572625b3c4ae17d34a9452f))
* improve search coverage with multi-strategy matching ([#52](https://github.com/xbit18/violarr/issues/52)) ([d3b3b59](https://github.com/xbit18/violarr/commit/d3b3b5949047f8f90e01c1d03307c3fa4a9cfec3))
* notify users when a new version is available ([#60](https://github.com/xbit18/violarr/issues/60)) ([a4e3d34](https://github.com/xbit18/violarr/commit/a4e3d3437eb84fa2590a0f9d5ed3a1f7fd0f9859))

## [1.1.5](https://github.com/xbit18/violarr/compare/v1.1.4...v1.1.5) (2026-10-09)


### Features

* add dynamic JSON badge for download count in README ([#25](https://github.com/xbit18/violarr/issues/25)) ([ab1783c](https://github.com/xbit18/violarr/commit/ab1783c2d70057d0287b26da788b5a2837754086))


### Bug Fixes

* correct Italian subtitle language detection ([#14](https://github.com/xbit18/violarr/issues/14)) ([b9c094f](https://github.com/xbit18/violarr/commit/b9c094fabd4e078e02d6d89af0132a27f082f9ad))

## [1.1.4](https://github.com/xbit18/violarr/compare/v1.1.3...v1.1.4) (2026-10-07)


### Bug Fixes

* improve Prowlarr connection diagnostics ([3dbfeea](https://github.com/xbit18/violarr/commit/3dbfeea27b94af4a78d023f0644addbaa15062b2))

## [1.1.3](https://github.com/xbit18/violarr/compare/v1.1.2...v1.1.3) (2026-10-06)


### Bug Fixes

* apply database update settings from WebUI ([d2403a0](https://github.com/xbit18/violarr/commit/d2403a0505f7539c0ee25e1d6a73ea31ea0496d6))

## v1.1.2 - 2026-10-05
### Added
- support for arm64 image

## v1.1.1 — 2026-10-05

### Solved
 - too small payload limiter for Prowlarr
 - bug in flow for adding Violarr as Prowlarr indexer

## v1.1.0 — 2026-10-05

### Added

- Rebranded the public product, repository metadata, package metadata, and
  primary container image as Violarr.
- Persistent schema-v1 settings at `/data/state/settings.json`, including atomic
  writes, environment precedence, validation, and masked Prowlarr secrets.
- Result-processing presets for unfiltered, Italian-preferred, Italian-only, and
  bounded custom score/exclusion rules.
- Same-origin `/webapi` endpoints for status, settings, result processing, and
  schema-derived Prowlarr test/install operations.
- Vue WebUI served from `/` with responsive light/dark styling.
- Multi-stage frontend build for the existing single-container runtime.
- Markdown-first VitePress documentation and GitHub Pages deployment workflow.

### Compatibility

- Publishes the Violarr image at `ghcr.io/xbit18/violarr`.
- Keeps the Compose service/container alias `icvdb-torznab`, named volume
  `icvdb_torznab_data`, `/data` paths, schema version 1, `ICVDB_*` and `DB_*`
  environment variables, `/api` and `/webapi`, and snapshot repository
  `xbit18/icvdb-snapshots` unchanged.
- Preserves the v1.0 `/api` Torznab operations, parameters, categories,
  pagination defaults, and XML mapping.
- Preserves PostgreSQL 16, port `8000`, the single `/data` volume, snapshot
  source, state file, candidate validation, maintenance-only switch, and rollback.
- Existing v1.0 volumes are expected to be reused; v1.1 adds the settings file
  without replacing PostgreSQL or snapshot state.

### Security

- The WebUI, WebAPI, and Torznab endpoint remain unauthenticated and should be
  restricted to a trusted LAN or protected by an authenticated reverse proxy.
- Prowlarr API keys are omitted from normal read responses and sanitized from
  high-level errors.

## v1.0

- Introduced the single-container PostgreSQL 16 and FastAPI Torznab service.
- Added automatic GitHub snapshot bootstrap and periodic safe updates.
- Established `/api` support for capabilities, generic, movie, and TV searches.
