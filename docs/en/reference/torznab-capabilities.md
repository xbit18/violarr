# Torznab capabilities

Request capabilities with:

```bash
curl 'http://localhost:8000/api?t=caps'
```

## Advertised searches

| Operation  | Advertised parameters                   |
| ---------- | --------------------------------------- |
| `search`   | `q`                                     |
| `movie`    | `q`, `imdbid`, `tmdbid`                 |
| `tvsearch` | `q`, `season`, `ep`, `imdbid`, `tmdbid` |

All requests also accept `t`, `cat`, `limit`, `offset`, `apikey`, and `extended`
at the HTTP layer. `cat`, `apikey`, and `extended` are accepted for
compatibility but do not currently change search behavior.

`limit` defaults to 100 and must be 1–200. `offset` defaults to 0 and must be
non-negative. Numeric IMDb IDs are normalized by adding the `tt` prefix.

## Complementary exact-ID searches

Movie and TV searches run all supplied IMDb/TMDB strategies, not just the first
identifier. Missing (`NULL`) metadata is not a conflict; a populated conflicting
identifier excludes a release. Without IDs, the existing title fallback applies;
an empty exact-ID search does not retry by title. TVDB is not supported.

TV season/episode searches also inspect files from the same torrent. IMDb,
season and episode evidence must come from one file row, or a matching parent
TMDB can identify the torrent while one file supplies season/episode. Missing
file IMDb is allowed with parent TMDB, but cannot identify an IMDb episode
alone. Known request/parent/file identity conflicts, including mixed-series
files, exclude the torrent. Valid child evidence can recover season packs
despite parent episode mismatches. Series are not equated and seasons are not
remapped. The returned title, size, category and magnet always belong to the
parent torrent.

SQL deduplicates hashes before ordering by seeders descending (NULL last), then
hash ascending, and applying limit/offset. Processed presets retain fixed
1000-candidate windows and window-local ranking without backfilling later
windows. These bounds limit fetched rows, not PostgreSQL's aggregation/sort
work: broad matches, file consistency checks and large offsets can be expensive.
Historical indexes support exact lookups, but production plans and large-dataset
latency still require measurement on the installed snapshot.

## Categories

| ID     | Name     | Mapping                          |
| ------ | -------- | -------------------------------- |
| `2000` | Movies   | Database type `movie`            |
| `5000` | TV       | Other non-movie, non-anime types |
| `5070` | TV/Anime | Database type `anime`            |

## XML response

Searches return RSS 2.0 XML. Each result can contain:

- title, non-permalink info-hash GUID, magnet link, and optional UTC `pubDate`;
- a BitTorrent enclosure with magnet URL and optional byte length;
- Torznab attributes `category`, `infohash`, `magneturl`, `seeders`, `peers`,
  optional `size`, `downloadvolumefactor=0`, `uploadvolumefactor=1`, and
  optional provider `description`.

Unknown `t` values return a valid empty RSS feed. Database and input values are
escaped by XML element serialization.

When result processing is enabled, pagination uses
[fixed 1000-row windows](../how-it-works/result-processing).
