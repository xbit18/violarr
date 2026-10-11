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
identifier excludes a release for the entity being matched. Eligible title and
conservative typo strategies run alongside IDs, even when exact matches exist.
TVDB is not supported. Generic search/browse remains unchanged.

TV season/episode searches also inspect files from the same torrent. IMDb,
season and episode evidence must come from one file row, or a matching parent
TMDB can identify the torrent while one file supplies season/episode. Missing
file IMDb is allowed with parent TMDB, but cannot identify an IMDb episode
alone. Known request/parent/file identity conflicts, including mixed-series
files, exclude the torrent. Valid child evidence can recover season packs
despite parent episode mismatches. Series are not equated and seasons are not
remapped. The returned title, size, category and magnet always belong to the
parent torrent.

Movie packs also use child IMDb from `pack_files`; it may differ from the
collection's parent IMDb. There is no child TMDB: the parent's TMDB never proves
that a child film matches it. Child path searches use the filename, not folder
names, and exclude extras, trailers, subtitles and non-video files. Returned
metadata and magnets still belong to the parent torrent.

SQL deduplicates hashes before ordering by four tiers: exact parent torrent
IMDb/TMDB, exact file/pack-child evidence, high-confidence validated title
tokens, then conservative fuzzy matches. A TV branch qualified through files
stays in the second tier even when TMDB identity comes from the parent. All
matching memberships are retained, but the strongest tier wins. Within each
tier, seeders descend (NULL last), then hash ascends, before limit/offset. This
also applies without a title. Processed presets retain fixed 1000-candidate
windows and window-local ranking without backfilling later windows. These bounds
limit fetched rows, not PostgreSQL's aggregation/sort work: broad matches, file
consistency checks and large offsets can be expensive. Historical indexes
support exact lookups, but production plans and large-dataset latency still
require measurement on the installed snapshot.

### Conservative title coverage and limits

Movie/TV title tokens accept punctuation separators, for example
`Hanno ucciso l'Uomo Ragno - 883`. TV title recovery identifies the series and
requires the requested season/episode on the parent or one qualifying file;
episode names alone cannot identify the series. Title hints alone do not prove
an episode. Mixed-series or cross-file identity/episode evidence is rejected.

Queries over 128 characters or 12 tokens, release-word-only queries, and titles
without a discriminating token of three characters do not use title recovery.
Use exact IDs for very short titles. Fuzzy lookup additionally requires eight
alphanumeric characters and a nonnumeric token of four characters; `883` retains
literal matching without typo expansion. Similarity is an eligibility check, not
ordering within a tier. Existing presets may still reorder each 1000-row window.

Each fuzzy source uses the existing GIN index shape and a page-independent pool.
Pools over 2000 rows are omitted; exact/literal matches remain available. Broad
titles may therefore miss fuzzy results, and the pool does not bound all
database work. No snapshot/schema/index mutation occurs. Historical snapshots
may lack the required `pack_files` or pg_trgm/index coverage; a sanitized
database failure does not imply the release is absent. Real plans and latency
require separately authorized PostgreSQL measurements, not SQLite tests or
capabilities XML alone.

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
