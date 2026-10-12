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
    assert [r[5] for r in fetch(pg, "movie", None, None, "Interstellarr", None, None)] == ["pack"]


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


def test_pg_fuzzy_overflow_retains_pool_without_affecting_literal(pg):
    pg.execute("""INSERT INTO torrents(info_hash,title,type,seeders)
        SELECT n::text, 'Interstellarr.2014', 'movie', 1 FROM generate_series(1,2001) n;
        INSERT INTO torrents(info_hash,title,type,seeders)
        VALUES ('literal','Interstellar.2014','movie',1)""")
    rows = fetch(pg, "movie", None, None, "Interstellar", None, None)
    assert rows[0][5] == "literal"
    assert len(rows) == 100
    assert rows.fuzzy_pools["movie_title_fuzzy"] == {
        "found": 2002,
        "retained": 2000,
        "omitted": 2,
        "truncated": True,
    }


def test_pg_confidence_retention_is_not_release_metadata_or_final_order(pg, monkeypatch):
    import search_strategies as search

    monkeypatch.setattr(search, "FUZZY_CANDIDATES", 2)
    pg.execute("""INSERT INTO torrents(info_hash,title,type,seeders) VALUES
        ('a','Interstellar.2014.1080p.ITA','movie',1),
        ('b','Interstellar.2025.2160p.ENG','movie',2),
        ('c','Interstellarr.2014','movie',100),
        ('exact','Unrelated','movie',0);
        UPDATE torrents SET imdb_id='tt1' WHERE info_hash='exact'""")
    pg.execute("""SELECT strict_word_similarity('Interstellar','Interstellar.2014.1080p.ITA'),
        strict_word_similarity('Interstellar','Interstellar.2025.2160p.ENG')""")
    first, second = pg.fetchone()
    assert first == second == 1
    rows = fetch(pg, "movie", "tt1", None, "Interstellar", None, None)
    assert [r[5] for r in rows] == ["exact", "b", "a"]
    assert rows.fuzzy_pools["movie_title_fuzzy"]["retained"] == 2
    # c remains eligible but loses to confidence, regardless of its seeders.
    assert rows.fuzzy_pools["movie_title_fuzzy"]["found"] == 3


def test_pg_pack_duplicate_children_do_not_crowd_best_parents(pg, monkeypatch):
    import search_strategies as search

    monkeypatch.setattr(search, "FUZZY_CANDIDATES", 2)
    pg.execute("""INSERT INTO torrents(info_hash,title,type,seeders) VALUES
        ('a','Collection','movie',2), ('b','Collection','movie',2),
        ('c','Collection','movie',1), ('bad','Collection','movie',100);
        INSERT INTO pack_files(pack_hash,file_path)
        SELECT 'a','Collection/Interstellar.2014.mkv' FROM generate_series(1,10);
        INSERT INTO pack_files(pack_hash,file_path) VALUES
        ('b','Collection/Interstellar.2014.mkv'), ('c','Collection/Interstellar.2014.mkv'),
        ('bad','Interstellar/Other.mkv'), ('bad','Extras/Interstellar.trailer.mkv'),
        ('bad','Interstellar.srt')""")
    rows = fetch(pg, "movie", None, None, "Interstellarr", None, None)
    assert [r[5] for r in rows] == ["a", "b"]
    assert rows.deduplicated == [True, False]
    assert rows.fuzzy_pools["movie_pack_title_fuzzy"] == {
        "found": 3,
        "retained": 2,
        "omitted": 1,
        "truncated": True,
    }


def test_pg_tv_invalid_prefix_episode_and_ids_cannot_consume_cap(pg, monkeypatch):
    import search_strategies as search

    monkeypatch.setattr(search, "FUZZY_CANDIDATES", 1)
    pg.execute("""INSERT INTO torrents
        (info_hash,title,type,seeders,imdb_id,imdb_season,imdb_episode) VALUES
        ('valid','Breaking.Bad.S01E02.2160p.ITA','series',1,NULL,1,2),
        ('bait','Other.Show.S01E02.Breaking.Bad','series',100,NULL,1,2),
        ('episode','Breaking.Bad.S01E09','series',100,NULL,1,9),
        ('season','Breaking.Bad.S09E02','series',100,NULL,9,2),
        ('conflict','Breaking.Bad.S01E02','series',100,'tt2',1,2),
        ('mixed','Breaking.Bad.S01E02','series',100,NULL,1,2);
        INSERT INTO files(info_hash,title,imdb_id,imdb_season,imdb_episode) VALUES
        ('mixed','Breaking.Bad.S01E02','tt1',1,2),
        ('mixed','Breaking.Bad.S01E02','tt2',1,2)""")
    rows = fetch(pg, "tv", "tt1", None, "Breaking Badd", 1, 2)
    assert [r[5] for r in rows] == ["valid"]
    assert rows.fuzzy_pools["tv_title_fuzzy"]["found"] == 1


@pytest.mark.parametrize("total", [1, 2, 3])
def test_pg_fuzzy_counts_and_pagination_survive_empty_page(pg, monkeypatch, total):
    import search_strategies as search

    monkeypatch.setattr(search, "FUZZY_CANDIDATES", 2)
    pg.execute(
        """INSERT INTO torrents(info_hash,title,type,seeders)
        SELECT n::text, 'Interstellar.2014','movie',n FROM generate_series(1,%s) n""",
        (total,),
    )
    query, values, descriptors = coverage_query(
        "movie", None, None, "Interstellarr", None, None, 1, 99
    )
    pg.execute(query, values)
    empty = CandidateRows(pg.fetchall(), descriptors)
    assert empty == []
    assert empty.fuzzy_pools["movie_title_fuzzy"] == {
        "found": total,
        "retained": min(total, 2),
        "omitted": max(total - 2, 0),
        "truncated": total > 2,
    }
    complete = fetch(pg, "movie", None, None, "Interstellarr", None, None)
    pages = []
    for offset in range(min(total, 2)):
        query, values, descriptors = coverage_query(
            "movie", None, None, "Interstellarr", None, None, 1, offset
        )
        pg.execute(query, values)
        pages.extend(CandidateRows(pg.fetchall(), descriptors))
    assert pages == complete
