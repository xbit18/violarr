"""Complete raw-row differential proof using independently frozen original SQL."""

import hashlib
import inspect
import itertools

import pytest
from original_exact_query import exact_query as original_query
from test_multi_strategy_search import LocalSQL

from search_strategies import exact_query


def test_oracle_is_frozen():
    source = inspect.getsource(original_query)
    digest = hashlib.sha256(source.encode()).hexdigest()

    assert digest == "f87806e0c7548400aff6b32487577f63a0cf3fa194e8102311d1678874f2b9da"


def requests():
    return itertools.product(
        ("movie", "tv"),
        (("tt123", None), (None, 10), ("tt123", 10)),
        ((None, None), (1, None), (1, 1), (None, 1)),
    )


def raw(database, generator, args):
    sql, values, strategies = generator(*args)
    database.execute(sql, values)
    return database.fetchall(), strategies


@pytest.fixture
def corpus():
    database = LocalSQL()
    # Includes absent/all-NULL/homogeneous/mixed identities, repeated files,
    # outside-episode conflicts and split same-file evidence. Cross these with
    # parent conflicts, packs/title fallback, tied and NULL seeders, and all types.
    files = (
        (),
        (None,),
        ("tt123",),
        ("tt999",),
        ("tt123", "tt123", None),
        ("tt123", "tt999"),
        ("tt999", "tt888"),
        (None, None),
    )
    cases = itertools.product(
        (None, "tt123", "tt999"),
        (None, 10, 99),
        files,
        ("movie", "series", "anime", "other"),
        ((None, None), (1, 1), (1, 2), (2, 1)),
    )
    for index, (imdb, tmdb, identities, kind, mode) in enumerate(cases):
        hash_value = f"h{index:05d}"
        database.torrent(
            hash_value,
            imdb=imdb,
            tmdb=tmdb,
            kind=kind,
            season=mode[0],
            episode=mode[1],
            seeders=(None, 7, 7, 0)[index % 4],
            title=f"{hash_value} S01 Season 1",
        )
        for number, identity in enumerate(identities):
            database.file(hash_value, imdb=identity, season=1, episode=1)
            # A duplicate qualifying row must survive until COUNT, not DISTINCT.
            database.file(hash_value, imdb=identity, season=1, episode=1)
            database.file(hash_value, imdb=identity, season=2, episode=9)
        if identities:
            database.file(hash_value, imdb=None, season=1, episode=2)
    database.file("orphan", imdb="tt123")
    for hash_value, tmdb in (("split_evidence", 10), ("split_without_tmdb", None)):
        database.torrent(hash_value, kind="series", tmdb=tmdb)
        database.file(hash_value, imdb="tt123", season=1, episode=9)
        database.file(hash_value, imdb=None, season=2, episode=1)
        database.file(hash_value, imdb=None, season=1, episode=1)
    yield database
    database.db.close()


@pytest.mark.parametrize("kind,ids,mode", list(requests()))
def test_complete_raw_rows_and_pages(corpus, kind, ids, mode):
    args = (kind, *ids, *mode)
    expected, strategies = raw(corpus, original_query, (*args, 10000, 0))
    assert raw(corpus, exact_query, (*args, 10000, 0)) == (expected, strategies)
    for limit in (1, 7, 100):
        for offset in (0, 1, 7, 100, len(expected), len(expected) + 1):
            old = raw(corpus, original_query, (*args, limit, offset))
            new = raw(corpus, exact_query, (*args, limit, offset))
            assert new == old == (expected[offset : offset + limit], strategies)


def test_consistency_is_candidate_bound_not_nested_or_repeated():
    for imdb in (None, "tt123"):
        sql, _, _ = exact_query("tv", imdb, 10, 1, 1, 100, 0)
        assert "files other" not in sql
        assert "bad.info_hash = t.info_hash" in sql
        assert sql.count("files bad") == (1 if imdb else 2)
        assert "files bad" not in sql.split("FROM merged m")[0]
        assert "MATERIALIZED" not in sql


def test_duplicate_occurrences_and_exact_membership_survive():
    database = LocalSQL()
    try:
        database.torrent("pack", imdb="tt123", tmdb=10, kind="series", season=1, episode=1)
        for _ in range(3):
            database.file("pack", imdb="tt123", season=1, episode=1)
        args = ("tv", "tt123", 10, 1, 1, 100, 0)
        rows, strategies = raw(database, exact_query, args)
        assert (rows, strategies) == raw(database, original_query, args)
        assert rows[0][7:] == (1, 1, 1, 1, 6)
        assert len(strategies) == 4
    finally:
        database.db.close()
