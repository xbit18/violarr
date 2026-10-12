"""Independent SQL oracle frozen before optimization.

Source: search_strategies.py at 2154136477fe9a773aac4d1cfe87bcc90955bded.
Do not refactor with the production generator: this preserves inherited behavior.
"""


def exact_query(kind, imdb_id, tmdb_id, season, episode, limit, offset):
    """Only internal constants become SQL text; every request value is bound."""
    branches = []
    parameters = []
    strategies = []
    type_guard = "t.type = 'movie'" if kind == "movie" else "t.type IN ('series', 'anime')"
    guards = [type_guard]
    guard_params = []
    for column, value in (("imdb_id", imdb_id), ("tmdb_id", tmdb_id)):
        if value is not None:
            guards.append(f"(t.{column} IS NULL OR t.{column} = %s)")
            guard_params.append(value)

    if kind == "tv":
        # A missing identity is not negative evidence. Any populated disagreement
        # with the request, parent or another file is, even in a different episode.
        request_conflict = " OR bad.imdb_id <> %s" if imdb_id is not None else ""
        guards.append(f"""NOT EXISTS (
            SELECT 1 FROM files bad WHERE bad.info_hash = t.info_hash
            AND bad.imdb_id IS NOT NULL AND (
                bad.imdb_id <> t.imdb_id {request_conflict}
                OR EXISTS (SELECT 1 FROM files other
                    WHERE other.info_hash = bad.info_hash
                    AND other.imdb_id <> bad.imdb_id)
            ))""")
        if imdb_id is not None:
            guard_params.append(imdb_id)

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
        branches.append(
            f"SELECT t.info_hash, '{identifier}' AS strategy FROM torrents t "
            f"WHERE t.{field}_id = %s AND {' AND '.join(guards)}{season_guard}"
        )
        parameters.extend([value, *guard_params, *season_params])

    if kind == "tv" and season is not None:
        file_episode_guard = " AND f.imdb_episode = %s" if episode is not None else ""
        file_params = [season, episode] if episode is not None else [season]
        if imdb_id is not None:
            identifier = f"tv_file_imdb{suffix}"
            strategies.append(identifier)
            # The identity, season and episode must all belong to this same row.
            branches.append(f"""SELECT t.info_hash, '{identifier}' AS strategy
                FROM files f JOIN torrents t ON t.info_hash = f.info_hash
                WHERE f.imdb_id = %s AND f.imdb_season = %s {file_episode_guard}
                AND {" AND ".join(guards)}""")
            parameters.extend([imdb_id, *file_params, *guard_params])
        if tmdb_id is not None:
            identifier = f"tv_file_tmdb{suffix}"
            strategies.append(identifier)
            # Files have no TMDB field: only an exact parent TMDB match supplies
            # this identity. EXISTS avoids fan-out from repeated qualifying files.
            branches.append(f"""SELECT t.info_hash, '{identifier}' AS strategy
                FROM torrents t WHERE t.tmdb_id = %s AND {" AND ".join(guards)}
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
        ORDER BY t.seeders DESC NULLS LAST, t.info_hash ASC
        LIMIT %s OFFSET %s
    """
    return sql, (*parameters, limit, offset), tuple(strategies)
