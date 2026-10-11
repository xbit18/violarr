"""Candidate SQL; pagination follows one globally deduplicated, tiered stream."""

import re

from search_diagnostics import ACTIVE_COLLECTOR


class CandidateRows(list):
    """Seven-field public rows with private, observed SQL strategy memberships."""

    def __init__(self, raw_rows, strategies):
        width = 8 + len(strategies)
        fuzzy_names = [name for name in strategies if name.endswith("_fuzzy")]
        self.fuzzy_pools = {}
        if raw_rows and len(raw_rows[0]) > width:
            # One private statistics envelope survives even an empty SQL page.
            stats = raw_rows[0][width + 1 :]
            for index, name in enumerate(fuzzy_names):
                found, retained = stats[index * 2 : index * 2 + 2]
                self.fuzzy_pools[name] = {
                    "found": found,
                    "retained": retained,
                    "omitted": found - retained,
                    "truncated": found > retained,
                }
            raw_rows = [row[:width] for row in raw_rows if row[width]]
        super().__init__(tuple(row[:7]) for row in raw_rows)
        self.strategies = strategies
        self.memberships = [
            tuple(name for name, matched in zip(strategies, row[7:-1], strict=True) if matched)
            for row in raw_rows
        ]
        self.deduplicated = [row[-1] > 1 for row in raw_rows]


def _exact_parts(kind, imdb_id, tmdb_id, season, episode):
    """Only internal constants become SQL text; every request value is bound."""
    branches = []
    parameters = []
    strategies = []
    type_guard = "t.type = 'movie'" if kind == "movie" else "t.type IN ('series', 'anime')"

    def parent_guards(exact_field=None):
        guards = [type_guard]
        values = []
        for field, value in (("imdb", imdb_id), ("tmdb", tmdb_id)):
            if value is not None and field != exact_field:
                guards.append(f"(t.{field}_id IS NULL OR t.{field}_id = %s)")
                values.append(value)
        return " AND ".join(guards), values

    consistency_guard = ""
    consistency_params = []
    if kind == "tv":
        # Retained parent guards make the requested IMDb the only allowed
        # populated identity. Otherwise use the parent, or (only if it is NULL)
        # count equality classes among this candidate's files. COUNT(DISTINCT)
        # avoids assuming MIN/MAX ordering agrees with varchar equality.
        if imdb_id is not None:
            consistency_guard = """WHERE NOT EXISTS (
                SELECT 1 FROM files bad WHERE bad.info_hash = t.info_hash
                AND bad.imdb_id <> %s)"""
            consistency_params = [imdb_id]
        else:
            consistency_guard = """WHERE CASE WHEN t.imdb_id IS NOT NULL THEN
                NOT EXISTS (SELECT 1 FROM files bad
                    WHERE bad.info_hash = t.info_hash AND bad.imdb_id <> t.imdb_id)
                ELSE (SELECT COUNT(DISTINCT bad.imdb_id) FROM files bad
                    WHERE bad.info_hash = t.info_hash) <= 1 END"""

    season_guard = ""
    season_params = []
    suffix = ""
    if kind == "tv" and season is not None:
        if episode is not None:
            suffix = "_season_episode"
            season_guard = " AND t.imdb_season = %s AND t.imdb_episode = %s"
            season_params = [season, episode]
        else:
            suffix = "_season"
            # Preserve existing season title hints only when parent season is missing.
            season_guard = """ AND (t.imdb_season = %s OR
                (t.imdb_season IS NULL AND (t.title ILIKE %s OR t.title ILIKE %s)))"""
            season_params = [season, f"%S{season:02d}%", f"%Season {season}%"]

    for field, value in (("imdb", imdb_id), ("tmdb", tmdb_id)):
        if value is None:
            continue
        identifier = f"{kind}_{field}{suffix}"
        strategies.append(identifier)
        guards, guard_params = parent_guards(field)
        branches.append(
            f"SELECT t.info_hash, '{identifier}' AS strategy FROM torrents t "
            f"WHERE t.{field}_id = %s AND {guards}{season_guard}"
        )
        parameters.extend([value, *guard_params, *season_params])

    if kind == "tv" and season is not None:
        file_episode_guard = " AND f.imdb_episode = %s" if episode is not None else ""
        file_params = [season, episode] if episode is not None else [season]
        if imdb_id is not None:
            identifier = f"tv_file_imdb{suffix}"
            strategies.append(identifier)
            # The identity, season and episode must all belong to this same row.
            guards, guard_params = parent_guards()
            branches.append(f"""SELECT t.info_hash, '{identifier}' AS strategy
                FROM files f JOIN torrents t ON t.info_hash = f.info_hash
                WHERE f.imdb_id = %s AND f.imdb_season = %s {file_episode_guard}
                AND {guards}""")
            parameters.extend([imdb_id, *file_params, *guard_params])
        if tmdb_id is not None:
            identifier = f"tv_file_tmdb{suffix}"
            strategies.append(identifier)
            # Files have no TMDB field: only an exact parent TMDB match supplies
            # this identity. EXISTS avoids fan-out from repeated qualifying files.
            guards, guard_params = parent_guards("tmdb")
            branches.append(f"""SELECT t.info_hash, '{identifier}' AS strategy
                FROM torrents t WHERE t.tmdb_id = %s AND {guards}
                AND EXISTS (SELECT 1 FROM files f WHERE f.info_hash = t.info_hash
                    AND f.imdb_season = %s {file_episode_guard})""")
            parameters.extend([tmdb_id, *guard_params, *file_params])

    return branches, parameters, strategies, consistency_guard, consistency_params


def _merge(
    branches,
    parameters,
    strategies,
    guard,
    guard_params,
    limit,
    offset,
    tiers=None,
    ctes=(),
    fuzzy_pools=(),
):
    if not branches:
        return None, (), strategies
    # MAX flags avoid duplicate-file/branch fan-out while retaining actual matches.
    memberships = ", ".join(
        f"MAX(CASE WHEN strategy = '{name}' THEN 1 ELSE 0 END) AS matched_{index}"
        for index, name in enumerate(strategies)
    )
    columns = ", ".join(f"m.matched_{index}" for index in range(len(strategies)))
    tier = ""
    order = ""
    if tiers:
        cases = " ".join(f"WHEN '{name}' THEN {rank}" for name, rank in tiers.items())
        tier = f", MIN(CASE strategy {cases} END) AS tier"
        order = "m.tier ASC, "
    prefix = ", ".join(ctes)
    match_count = "SUM(evidence_count)" if fuzzy_pools else "COUNT(*)"
    private_tier = ", m.tier AS tier" if fuzzy_pools else ""
    sql = f"""
        WITH {prefix + ", " if prefix and not fuzzy_pools else ""}matches AS ({" UNION ALL ".join(branches)}),
        merged AS (SELECT info_hash, {memberships}, {match_count} AS match_count
                   {tier}
                   FROM matches GROUP BY info_hash)
        SELECT t.title, t.size, t.seeders, t.provider, t.upload_date, t.info_hash, t.type,
               {columns}, m.match_count{private_tier}
        FROM merged m JOIN torrents t ON t.info_hash = m.info_hash
        {guard}
        ORDER BY {order}t.seeders DESC NULLS LAST, t.info_hash ASC
        LIMIT %s OFFSET %s
    """
    if fuzzy_pools:
        # Statistics are numeric, per execution, and independent of pagination.
        # The singleton left join returns a private sentinel, never a fake release.
        stats = ", ".join(
            f"(SELECT COUNT(*) FROM {pool}_parents) AS found_{i}, "
            f"(SELECT COUNT(*) FROM {pool}) AS retained_{i}"
            for i, pool in enumerate(fuzzy_pools)
        )
        public = ", ".join(
            f"page.{column}"
            for column in (
                "title",
                "size",
                "seeders",
                "provider",
                "upload_date",
                "info_hash",
                "type",
            )
        )
        flags = ", ".join(f"page.matched_{i}" for i in range(len(strategies)))
        counts = ", ".join(f"stats.found_{i}, stats.retained_{i}" for i in range(len(fuzzy_pools)))
        sql = f"""
            WITH {prefix}, page AS ({sql}), stats AS (SELECT {stats})
            SELECT {public}, {flags}, page.match_count,
                   page.info_hash IS NOT NULL AS present, {counts}
            FROM stats LEFT JOIN page ON TRUE
            ORDER BY page.tier ASC, page.seeders DESC NULLS LAST, page.info_hash ASC
        """
    return sql, (*parameters, *guard_params, limit, offset), strategies


def exact_query(kind, imdb_id, tmdb_id, season, episode, limit, offset):
    branches, parameters, strategies, guard, guard_params = _exact_parts(
        kind, imdb_id, tmdb_id, season, episode
    )
    return _merge(branches, parameters, tuple(strategies), guard, guard_params, limit, offset)


RELEASE_WORDS = {
    "1080p",
    "720p",
    "2160p",
    "web",
    "dl",
    "webrip",
    "bluray",
    "ita",
    "eng",
    "multi",
    "h264",
    "h265",
    "x264",
    "x265",
}
FUZZY_CANDIDATES = 2000


def title_terms(query):
    """Bounded literal tokens; short titles remain exact-ID searchable."""
    if not query or len(query) > 128:
        return ()
    terms = tuple(re.findall(r"[^\W_]+", query.lower()))
    if not 1 <= len(terms) <= 12 or not any(
        len(term) >= 3 and term not in RELEASE_WORDS for term in terms
    ):
        return ()
    return terms


def coverage_query(kind, imdb_id, tmdb_id, q, season, episode, limit, offset, *, fuzzy=True):
    """Exact IDs plus qualified text; no similarity ordering within a tier.

    Fuzzy pools retain the best eligible distinct parents, independent of page.
    Confidence orders retention only; the final tier/seeders/hash order is unchanged.
    """
    branches, parameters, names, guard, guard_params = _exact_parts(
        kind, imdb_id, tmdb_id, season, episode
    )
    descriptors = {}
    # Episode qualification through files is child evidence even when the
    # identity itself comes from the parent's TMDB. Metadata source stays truthful.
    tiers = {name: 1 if name.startswith("tv_file_") else 0 for name in names}
    for name in names:
        descriptors[name] = {
            "field": "imdb" if "imdb" in name else "tmdb",
            "match_type": "exact",
            "source": "file" if name.startswith("tv_file_imdb") else "torrent",
        }
    ctes, cte_values, fuzzy_pools = [], [], []

    def add(name, sql, values, metadata, tier):
        branches.append(sql)
        parameters.extend(values)
        descriptors[name] = metadata
        tiers[name] = tier

    if kind == "movie" and imdb_id is not None:
        name = "movie_pack_imdb"
        add(
            name,
            f"""SELECT t.info_hash, '{name}' AS strategy
            FROM pack_files p JOIN torrents t ON t.info_hash = p.pack_hash
            WHERE t.type = 'movie' AND p.imdb_id = %s""",
            [imdb_id],
            {"field": "imdb", "match_type": "exact", "source": "pack_file"},
            1,
        )

    terms = title_terms(q)
    if not terms:
        return _merge(branches, parameters, descriptors, guard, guard_params, limit, offset, tiers)
    text = " ".join(terms)
    pattern = "%" + "%".join(terms) + "%"
    # Query tokens are escaped and bound, never executable regex supplied by HTTP.
    boundary = r"[^\w]|_"
    literal = r"^[\W_]*" if kind == "tv" else rf"(^|{boundary})"
    literal += r"[\W_]+".join(re.escape(term) for term in terms) + rf"($|{boundary})"
    fuzzy = (
        fuzzy
        and sum(map(len, terms)) >= 8
        and any(len(term) >= 4 and not term.isdigit() for term in terms)
    )
    parent_guard = "t.type = 'movie'" if kind == "movie" else "t.type IN ('series', 'anime')"
    parent_values = []
    for field, value in (("imdb", imdb_id), ("tmdb", tmdb_id)):
        if value is not None:
            parent_guard += f" AND (t.{field}_id IS NULL OR t.{field}_id = %s)"
            parent_values.append(value)

    qualification, qualification_values = "", []
    if kind == "tv" and (season is not None or episode is not None):
        fields = [("imdb_season", season), ("imdb_episode", episode)]
        fields = [(field, value) for field, value in fields if value is not None]
        parent = " AND ".join(f"t.{field} = %s" for field, _ in fields)
        child = " AND ".join(f"f.{field} = %s" for field, _ in fields)
        qualification = f""" AND (({parent}) OR EXISTS (
            SELECT 1 FROM files f WHERE f.info_hash = t.info_hash AND {child}))"""
        qualification_values = [v for _, v in fields] * 2

    sources = [
        (
            f"{kind}_title",
            "torrents t",
            "t.title",
            "t.title",
            parent_guard + qualification,
            parent_values + qualification_values,
            "torrent",
            "t.info_hash",
        )
    ]
    if kind == "tv":
        suffix = "_season" if season is not None else ""
        if episode is not None:
            suffix += "_episode"
        file_guard, file_values = parent_guard, list(parent_values)
        if imdb_id is not None:
            file_guard += " AND (f.imdb_id IS NULL OR f.imdb_id = %s)"
            file_values.append(imdb_id)
        for column, value in (("imdb_season", season), ("imdb_episode", episode)):
            if value is not None:
                file_guard += f" AND f.{column} = %s"
                file_values.append(value)
        sources.append(
            (
                f"tv_file_title{suffix}",
                "files f JOIN torrents t ON t.info_hash = f.info_hash",
                "f.title",
                "f.title",
                file_guard,
                file_values,
                "file",
                "t.info_hash, f.id",
            )
        )
    else:
        pack_guard, pack_values = "t.type = 'movie'", []
        if imdb_id is not None:
            pack_guard += " AND (p.imdb_id IS NULL OR p.imdb_id = %s)"
            pack_values.append(imdb_id)
        # Parent movie IDs describe the collection, not its individual children.
        basename = r"regexp_replace(p.file_path, '^.*[/\\]', '', 'g')"
        sources.append(
            (
                "movie_pack_title",
                "pack_files p JOIN torrents t ON t.info_hash = p.pack_hash",
                "p.file_path",
                basename,
                pack_guard,
                pack_values,
                "pack_file",
                "t.info_hash, p.id",
            )
        )

    for origin, relation, column, identity, conditions, values, source, ordering in sources:
        if source == "pack_file":
            conditions += " AND NOT regexp_like(p.file_path, %s, 'i')"
            values = [
                *values,
                r"(^|[\W_])(extras?|trailers?|sample|featurette|deleted|subtitles?)([\W_]|$)",
            ]
            conditions += " AND regexp_like(p.file_path, %s, 'i')"
            values.append(r"\.(mkv|mp4|avi|m4v|mov|ts)$")
        name = origin + "_text"
        add(
            name,
            f"""SELECT t.info_hash, '{name}' AS strategy FROM {relation}
            WHERE {conditions} AND {column} ILIKE %s
            AND regexp_like({identity}, %s, 'i')""",
            [*values, pattern, literal],
            {"field": "title", "match_type": "token", "source": source},
            2,
        )
        if not fuzzy:
            continue
        pool = f"fuzzy_{len(fuzzy_pools)}"
        # Extract only the leading series identity, never an episode's name.
        series = identity
        if kind == "tv":
            series = (
                f"regexp_replace({identity}, '[ ._-]+(s[0-9]+|season[ ._-]+[0-9]+).*$', '', 'i')"
            )
            leading = rf"^([\W_]*([^\W_]+[\W_]+){{{len(terms) - 1}}}[^\W_]+).*$"
            series = f"regexp_replace({series}, '{leading}', '\\1', 'i')"
        child_key = ordering.split(", ")[-1] if source != "torrent" else "0"
        consistency = " AND " + guard.removeprefix("WHERE ") if guard else ""
        ctes.append(f"""{pool}_discovered AS (
            SELECT t.info_hash, t.seeders, {child_key} AS child_key,
                   {series} AS identity_title FROM {relation}
            WHERE {conditions} AND {column} %%>> %s{consistency})""")
        cte_values.extend([*values, text, *guard_params])
        prefix_score = "similarity(%s, identity_title)" if kind == "tv" else "1.0"
        ctes.append(f"""{pool}_scored AS (
            SELECT *, strict_word_similarity(%s, identity_title) AS title_confidence,
                   {prefix_score} AS prefix_confidence FROM {pool}_discovered),
            {pool}_eligible AS (
                SELECT *, LEAST(title_confidence, prefix_confidence) AS confidence
                FROM {pool}_scored
                WHERE title_confidence >= 0.75 AND prefix_confidence >= 0.75),
            {pool}_parents AS MATERIALIZED (
                SELECT info_hash, seeders, child_key, confidence, evidence_count FROM (
                    SELECT *, ROW_NUMBER() OVER (
                        PARTITION BY info_hash ORDER BY confidence DESC, child_key ASC) AS position,
                        COUNT(*) OVER (PARTITION BY info_hash) AS evidence_count
                    FROM {pool}_eligible) evidence WHERE position = 1),
            {pool} AS MATERIALIZED (
                SELECT * FROM {pool}_parents
                ORDER BY confidence DESC, seeders DESC NULLS LAST, info_hash ASC, child_key ASC
                LIMIT {FUZZY_CANDIDATES})""")
        cte_values.extend([text, text] if kind == "tv" else [text])
        fuzzy_pools.append(pool)
        name = origin + "_fuzzy"
        add(
            name,
            f"SELECT info_hash, '{name}' AS strategy, evidence_count FROM {pool}",
            [],
            {"field": "title", "match_type": "fuzzy", "source": source},
            3,
        )
    if fuzzy_pools:
        branches = [
            branch
            if name.endswith("_fuzzy")
            else f"SELECT info_hash, strategy, 1 AS evidence_count FROM ({branch}) evidence"
            for name, branch in zip(descriptors, branches, strict=True)
        ]
    return _merge(
        branches,
        [*cte_values, *parameters],
        descriptors,
        guard,
        guard_params,
        limit,
        offset,
        tiers,
        ctes,
        fuzzy_pools,
    )


def query_coverage(connect, kind, imdb_id, tmdb_id, q, season, episode, limit, offset):
    sql, values, descriptors = coverage_query(
        kind, imdb_id, tmdb_id, q, season, episode, limit, offset
    )
    collector = ACTIVE_COLLECTOR.get()
    if collector is not None:
        collector.merged_begin(descriptors)
    if sql is None:
        return CandidateRows([], descriptors)
    with connect() as connection, connection.cursor() as cursor:
        if "fuzzy" in " ".join(descriptors):
            cursor.execute("SET LOCAL pg_trgm.strict_word_similarity_threshold = 0.6")
        cursor.execute(sql, values)
        rows = CandidateRows(cursor.fetchall(), descriptors)
        if collector is not None and rows.fuzzy_pools:
            collector.fuzzy_pools(rows.fuzzy_pools)
        return rows


def query_exact(connect, kind, imdb_id, tmdb_id, season, episode, limit, offset):
    sql, parameters, strategies = exact_query(
        kind, imdb_id, tmdb_id, season, episode, limit, offset
    )
    collector = ACTIVE_COLLECTOR.get()
    if collector is not None:
        collector.merged_begin(strategies)
    with connect() as connection, connection.cursor() as cursor:
        cursor.execute(sql, parameters)
        return CandidateRows(cursor.fetchall(), strategies)
