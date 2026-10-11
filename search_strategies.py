"""Exact-ID candidate SQL; pagination follows one globally deduplicated stream."""

from search_diagnostics import ACTIVE_COLLECTOR


class CandidateRows(list):
    """Seven-field public rows with private, observed SQL strategy memberships."""

    def __init__(self, raw_rows, strategies):
        super().__init__(tuple(row[:7]) for row in raw_rows)
        self.strategies = strategies
        self.memberships = [
            tuple(name for name, matched in zip(strategies, row[7:-1], strict=True) if matched)
            for row in raw_rows
        ]
        self.deduplicated = [row[-1] > 1 for row in raw_rows]


def exact_query(kind, imdb_id, tmdb_id, season, episode, limit, offset):
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

    # MAX flags avoid duplicate-file/branch fan-out while retaining actual matches.
    memberships = ", ".join(
        f"MAX(CASE WHEN strategy = '{name}' THEN 1 ELSE 0 END) AS matched_{index}"
        for index, name in enumerate(strategies)
    )
    columns = ", ".join(f"m.matched_{index}" for index in range(len(strategies)))
    sql = f"""
        WITH matches AS ({" UNION ALL ".join(branches)}),
        merged AS (SELECT info_hash, {memberships}, COUNT(*) AS match_count
                   FROM matches GROUP BY info_hash)
        SELECT t.title, t.size, t.seeders, t.provider, t.upload_date, t.info_hash, t.type,
               {columns}, m.match_count
        FROM merged m JOIN torrents t ON t.info_hash = m.info_hash
        {consistency_guard}
        ORDER BY t.seeders DESC NULLS LAST, t.info_hash ASC
        LIMIT %s OFFSET %s
    """
    return sql, (*parameters, *consistency_params, limit, offset), tuple(strategies)


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
