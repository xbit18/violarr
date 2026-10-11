# Automatic database updates

The application periodically checks the latest release from
[`xbit18/icvdb-snapshots`](https://github.com/xbit18/icvdb-snapshots), downloads
the first `.dump` asset, and requires the release asset's `sha256:` digest.

The active database remains available during download, dump inspection, restore,
and candidate validation. Only the final database rename happens in maintenance
mode.

You can disable automatic checks, choose a numeric interval in
minutes/hours/days, or request a manual check in the WebUI. Manual and automatic
checks share one pipeline: duplicates do not start another update. See
[database update controls](../configuration/database-updates) for progress and
scheduling. The initial bootstrap still needs a valid snapshot before FastAPI
can serve a fresh installation.

See [snapshot system](../how-it-works/snapshots) and
[safe updates](../how-it-works/safe-updates) for the full flow.
