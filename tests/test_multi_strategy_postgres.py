"""Opt-in PostgreSQL proof; never discover credentials or connect by default.

Set VIOLARR_TEST_LOCAL_PG_DSN explicitly with host, port, dbname, user and password
for an authorized loopback-only test instance. All tables are temporary and the
transaction is rolled back. This is not a production dataset benchmark.
"""

import os

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict

from search_strategies import CandidateRows, exact_query


def test_exact_strategies_on_explicit_local_postgres():
    dsn = os.environ.get("VIOLARR_TEST_LOCAL_PG_DSN")
    if not dsn:
        pytest.skip("No explicitly authorized local PostgreSQL DSN")
    options = conninfo_to_dict(dsn)
    required = {"host", "port", "dbname", "user", "password"}
    assert set(options) == required, "Specify exactly host, port, dbname, user, password"
    assert options["host"] in {"localhost", "127.0.0.1", "::1"}, "Local loopback only"
    options["hostaddr"] = "::1" if options["host"] == "::1" else "127.0.0.1"
    options.update(connect_timeout="3", passfile="/dev/null")
    with psycopg.connect(**options) as connection:
        try:
            with connection.cursor() as cursor:
                cursor.execute("""CREATE TEMP TABLE torrents (
                    title varchar(256), size bigint, seeders smallint, provider text,
                    upload_date timestamp, info_hash varchar(64) PRIMARY KEY,
                    type varchar(16), imdb_id varchar(20), tmdb_id integer,
                    imdb_season integer, imdb_episode integer)
                """)
                cursor.execute("""CREATE TEMP TABLE files (
                    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                    info_hash varchar(64) NOT NULL, file_index integer,
                    title varchar(256) NOT NULL, size bigint, imdb_id varchar(32),
                    imdb_season integer, imdb_episode integer,
                    UNIQUE(info_hash, file_index))
                """)
                for hash_value, imdb, tmdb in [
                    ("a", "tt123", 10),
                    ("b", "tt123", None),
                    ("c", None, 10),
                    ("conflict", "tt999", 10),
                ]:
                    cursor.execute(
                        """INSERT INTO torrents
                        (title, size, seeders, info_hash, type, imdb_id, tmdb_id)
                        VALUES (%s, 1234, 7, %s, 'movie', %s, %s)""",
                        (hash_value, hash_value, imdb, tmdb),
                    )
                sql, values, strategies = exact_query("movie", "tt123", 10, None, None, 2, 1)
                cursor.execute(sql, values)
                rows = CandidateRows(cursor.fetchall(), strategies)
                assert [row[5] for row in rows] == ["b", "c"]
                assert rows.memberships == [("movie_imdb",), ("movie_tmdb",)]
                cursor.execute("""UPDATE torrents SET type = 'series', imdb_season = 1,
                    imdb_episode = 1 WHERE info_hash = 'a'""")
                for _ in range(2):
                    cursor.execute("""INSERT INTO files
                        (info_hash, title, imdb_id, imdb_season, imdb_episode)
                        VALUES ('a', 'child', 'tt123', 1, 2)""")
                sql, values, strategies = exact_query("tv", "tt123", 10, 1, 2, 100, 0)
                cursor.execute(sql, values)
                rows = CandidateRows(cursor.fetchall(), strategies)
                assert [row[5] for row in rows] == ["a"]
                assert rows.memberships == [
                    ("tv_file_imdb_season_episode", "tv_file_tmdb_season_episode")
                ]
                cursor.execute("EXPLAIN " + sql, values)
                assert cursor.fetchall()
        finally:
            connection.rollback()
