"""Opt-in PostgreSQL 16 correctness/index proof; never connect by default.

Requires explicit authorization and VIOLARR_TEST_LOCAL_PG_DSN. Uses only temporary
tables/indexes and rollback; pg_trgm must already be installed. Small fixture plans
prove operator exploitability, not installed-dataset latency or planner choices.
"""

import os

import psycopg
import pytest
from benchmark_multi_strategy_postgres import local_options

from search_strategies import CandidateRows, coverage_query


@pytest.fixture
def pg():
    dsn = os.environ.get("VIOLARR_TEST_LOCAL_PG_DSN")
    if not dsn:
        pytest.skip("No explicitly authorized local PostgreSQL DSN")
    with psycopg.connect(**local_options(dsn)) as connection:
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT extversion FROM pg_extension WHERE extname = 'pg_trgm'")
                assert cursor.fetchone() is not None, (
                    "Existing pg_trgm required; no extension install"
                )
                cursor.execute("""CREATE TEMP TABLE torrents (
                    info_hash varchar(64) PRIMARY KEY, title text, size bigint,
                    seeders smallint, provider text, upload_date timestamp, type text,
                    imdb_id text, tmdb_id integer, imdb_season integer, imdb_episode integer);
                    CREATE TEMP TABLE files (id integer GENERATED ALWAYS AS IDENTITY,
                    info_hash text, title text, imdb_id text, imdb_season integer, imdb_episode integer);
                    CREATE TEMP TABLE pack_files (id integer GENERATED ALWAYS AS IDENTITY,
                    pack_hash text, imdb_id text, file_path text);
                    CREATE INDEX coverage_torrents_trgm ON torrents USING gin(title gin_trgm_ops);
                    CREATE INDEX coverage_files_trgm ON files USING gin(title gin_trgm_ops);
                    CREATE INDEX coverage_pack_trgm ON pack_files USING gin(file_path gin_trgm_ops);
                    SET LOCAL pg_trgm.strict_word_similarity_threshold = 0.6;
                    SET LOCAL statement_timeout = '5s';""")
                yield cursor
        finally:
            connection.rollback()


def fetch(pg, *request):
    query, values, descriptors = coverage_query(*request, 100, 0)
    pg.execute(query, values)
    return CandidateRows(pg.fetchall(), descriptors)


def test_pg_typos_tiers_conflicts_and_same_series_episode(pg):
    pg.execute("""INSERT INTO torrents
        (info_hash,title,type,seeders,imdb_id,imdb_season,imdb_episode) VALUES
        ('exact','Different','series',1,'tt1',1,2),
        ('literal','Breaking.Bad.S01E02','series',9,NULL,1,2),
        ('typo','Breaking.Badd.S01E02','series',100,NULL,1,2),
        ('wrong','Breaking.Bad.S01E09','series',9,NULL,1,9),
        ('bait','Other.Show.S01E02.Breaking.Bad','series',9,NULL,1,2),
        ('conflict','Breaking.Bad.S01E02','series',9,'tt2',1,2)""")
    rows = fetch(pg, "tv", "tt1", None, "Breaking Bad", 1, 2)
    assert [r[5] for r in rows] == ["exact", "literal", "typo"]
    assert "tv_title_fuzzy" in rows.memberships[-1]


def test_pg_child_pack_identity_and_basename(pg):
    pg.execute("""INSERT INTO torrents(info_hash,title,type,seeders,imdb_id,tmdb_id) VALUES
        ('pack','Collection','movie',1,'ttParent',99),
        ('folder','Collection','movie',1,NULL,NULL);
        INSERT INTO pack_files(pack_hash,imdb_id,file_path) VALUES
        ('pack','tt1','Collection/Interstellar.2014.mkv'),
        ('folder',NULL,'Interstellar/Other.2014.mkv')""")
    assert [r[5] for r in fetch(pg, "movie", "tt1", 10, None, None, None)] == ["pack"]
    assert [r[5] for r in fetch(pg, "movie", None, None, "Interstelar", None, None)] == ["pack"]


@pytest.mark.parametrize(
    "table,column,index",
    [
        ("torrents", "title", "coverage_torrents_trgm"),
        ("files", "title", "coverage_files_trgm"),
        ("pack_files", "file_path", "coverage_pack_trgm"),
    ],
)
def test_pg_operator_can_use_existing_gin_shape(pg, table, column, index):
    # Disable seqscan only inside this synthetic transaction to prove eligibility,
    # not to claim the installed dataset planner would choose this index.
    pg.execute("SET LOCAL enable_seqscan = off")
    pg.execute(
        f"EXPLAIN (FORMAT JSON) SELECT * FROM {table} WHERE {column} %%>> %s", ("Breaking Bad",)
    )
    assert index in str(pg.fetchone()[0])


def test_pg_generated_tv_pools_can_exploit_gin(pg):
    pg.execute("SET LOCAL enable_seqscan = off")
    query, values, _ = coverage_query("tv", None, None, "Breaking Badd", 1, 2, 100, 0)
    pg.execute("EXPLAIN (FORMAT JSON) " + query, values)
    plan = str(pg.fetchone()[0])
    assert "coverage_torrents_trgm" in plan
    assert "coverage_files_trgm" in plan


def test_pg_fuzzy_overflow_omits_pool_without_affecting_literal(pg):
    pg.execute("""INSERT INTO torrents(info_hash,title,type,seeders)
        SELECT n::text, 'Interstelar.2014', 'movie', 1 FROM generate_series(1,2001) n;
        INSERT INTO torrents(info_hash,title,type,seeders)
        VALUES ('literal','Interstellar.2014','movie',1)""")
    rows = fetch(pg, "movie", None, None, "Interstellar", None, None)
    assert [r[5] for r in rows] == ["literal"]
