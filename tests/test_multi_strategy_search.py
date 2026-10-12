"""Execute production query structure offline, not a mock SQL evaluator.

SQLite checks joins, guards, aggregation and pagination using the same query.
Only parameter markers and ILIKE are adapted; PostgreSQL plans/types still need
separate integration proof against an explicitly authorized local database.
"""

import re
import sqlite3
from xml.etree import ElementTree

import pytest

import app as backend
from settings import SettingsStore


class LocalSQL:
    def __init__(self):
        self.db = sqlite3.connect(":memory:")
        self.calls = []
        self.db.executescript("""
            CREATE TABLE torrents (
                title TEXT, size BIGINT, seeders SMALLINT, provider TEXT,
                upload_date TEXT, info_hash TEXT PRIMARY KEY, type TEXT,
                imdb_id TEXT, tmdb_id INTEGER, imdb_season INTEGER, imdb_episode INTEGER
            );
            CREATE TABLE files (
                id INTEGER PRIMARY KEY, info_hash TEXT NOT NULL, file_index INTEGER,
                title TEXT NOT NULL, size BIGINT, imdb_id TEXT,
                imdb_season INTEGER, imdb_episode INTEGER,
                UNIQUE(info_hash, file_index)
            );
            CREATE TABLE pack_files (
                id INTEGER PRIMARY KEY, pack_hash TEXT, imdb_id TEXT,
                file_index INTEGER, file_path TEXT, file_size INTEGER,
                created_at TEXT, rd_link_index INTEGER);
        """)
        self.db.create_function(
            "regexp_like",
            3,
            lambda text, pattern, flags: bool(re.search(pattern, text or "", re.I)),
        )
        self.db.create_function(
            "regexp_replace",
            4,
            lambda text, pattern, replacement, flags: re.sub(
                pattern, replacement, text or "", flags=re.I
            ),
        )

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def cursor(self):
        return self

    def execute(self, sql, values):
        self.calls.append((sql, values))
        self.result = self.db.execute(sql.replace("%s", "?").replace("ILIKE", "LIKE"), values)

    def fetchall(self):
        return self.result.fetchall()

    def torrent(
        self,
        hash_value,
        *,
        imdb=None,
        tmdb=None,
        season=None,
        episode=None,
        kind="movie",
        seeders=7,
        title=None,
    ):
        self.db.execute(
            "INSERT INTO torrents VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                title or hash_value,
                1234,
                seeders,
                "provider",
                None,
                hash_value,
                kind,
                imdb,
                tmdb,
                season,
                episode,
            ),
        )

    def file(self, hash_value, *, imdb=None, season=1, episode=1, index=None):
        self.db.execute(
            """INSERT INTO files
            (info_hash, file_index, title, imdb_id, imdb_season, imdb_episode)
            VALUES (?,?,?,?,?,?)""",
            (hash_value, index, "episode", imdb, season, episode),
        )


@pytest.fixture
def sql(tmp_path, monkeypatch):
    database = LocalSQL()
    monkeypatch.setattr(backend, "get_conn", lambda: database)
    monkeypatch.setattr(
        backend, "SETTINGS_STORE", SettingsStore(path=tmp_path / "settings.json", environ={})
    )
    yield database
    database.db.close()


def hashes(xml):
    return [item.findtext("guid") for item in ElementTree.fromstring(xml).findall("channel/item")]


@pytest.mark.parametrize(
    "kind,t", [("movie", "movie"), ("series", "tvsearch"), ("anime", "tvsearch")]
)
@pytest.mark.parametrize(
    "ids,expected",
    [
        ({"imdbid": "123"}, ["both", "imdb", "wrong_tmdb"]),
        ({"tmdbid": 10}, ["both", "tmdb", "wrong_imdb"]),
        ({"imdbid": "123", "tmdbid": 10}, ["both", "imdb", "tmdb"]),
    ],
)
def test_all_parent_ids_missing_conflicting_and_shared(sql, kind, t, ids, expected):
    sql.torrent("both", imdb="tt123", tmdb=10, kind=kind)
    sql.torrent("imdb", imdb="tt123", kind=kind)
    sql.torrent("tmdb", tmdb=10, kind=kind)
    sql.torrent("wrong_imdb", imdb="tt999", tmdb=10, kind=kind)
    sql.torrent("wrong_tmdb", imdb="tt123", tmdb=99, kind=kind)
    sql.torrent("title_only", kind=kind, title="fallback")
    # Exact-only compatibility; ID+title coverage has its own contract tests.
    assert hashes(backend.execute_search(t=t, **ids)) == expected
    assert len(sql.calls) == 1
    assert "UNION ALL" in sql.calls[0][0] if len(ids) == 2 else True


def test_dedup_before_tied_order_limit_offset_and_null_seeders(sql):
    for hash_value in reversed(["a", "b", "c", "d"]):
        sql.torrent(hash_value, imdb="tt123", tmdb=10)
    sql.torrent("null", imdb="tt123", seeders=None)
    assert hashes(
        backend.execute_search(t="movie", imdbid="123", tmdbid=10, limit=2, offset=1)
    ) == ["b", "c"]
    assert hashes(
        backend.execute_search(t="movie", imdbid="123", tmdbid=10, limit=2, offset=3)
    ) == ["d", "null"]


@pytest.mark.parametrize(
    "ids", [{"imdbid": "123"}, {"tmdbid": 10}, {"imdbid": "123", "tmdbid": 10}]
)
def test_tv_parent_season_episode_constraints(sql, ids):
    for name, season, episode in [
        ("episode", 1, 2),
        ("other_episode", 1, 3),
        ("other_season", 2, 2),
        ("missing", None, None),
    ]:
        sql.torrent(name, imdb="tt123", tmdb=10, kind="series", season=season, episode=episode)
    assert hashes(backend.execute_search(t="tvsearch", season=1, ep=2, **ids)) == ["episode"]
    assert hashes(backend.execute_search(t="tvsearch", season=1, **ids)) == [
        "episode",
        "other_episode",
    ]


def test_title_season_now_qualifies_episode_and_unknown_type(sql):
    sql.torrent("legacy", kind="series", title="Show S01E09", episode=9)
    assert hashes(backend.execute_search(t="tvsearch", q="Show", season=1, ep=1)) == []
    assert hashes(backend.execute_search(t="music", imdbid="123", tmdbid=10)) == []


@pytest.mark.parametrize(
    "ids", [{"imdbid": "26545971"}, {"tmdbid": 273419}, {"imdbid": "26545971", "tmdbid": 273419}]
)
@pytest.mark.parametrize("parent_episode", [None, 1, 8])
def test_file_episode_recovers_packs_and_parent_mismatch(sql, ids, parent_episode):
    sql.torrent(
        "pack",
        imdb="tt26545971",
        tmdb=273419,
        kind="series",
        season=1,
        episode=parent_episode,
        title="Hanno ucciso l'Uomo Ragno S01 pack",
    )
    for episode in (1, 2, 2, 3):
        sql.file("pack", imdb="tt26545971", episode=episode)
    xml = backend.execute_search(t="tvsearch", season=1, ep=2, **ids)
    assert hashes(xml) == ["pack"]
    item = ElementTree.fromstring(xml).find("channel/item")
    assert item.findtext("title") == "Hanno ucciso l'Uomo Ragno S01 pack"
    assert item.findtext("link") == "magnet:?xt=urn:btih:pack"
    assert len(sql.calls) == 1


def test_imdb_file_can_recover_missing_parent_identity_and_season(sql):
    sql.torrent("recovered", kind="anime")
    sql.file("recovered", imdb="tt26545971", episode=2)
    assert hashes(backend.execute_search(t="tvsearch", imdbid="26545971", season=1, ep=2)) == [
        "recovered"
    ]
    assert hashes(backend.execute_search(t="tvsearch", imdbid="26545971", season=1)) == [
        "recovered"
    ]


@pytest.mark.parametrize(
    "ids", [{"imdbid": "26545971"}, {"tmdbid": 273419}, {"imdbid": "26545971", "tmdbid": 273419}]
)
def test_cross_parent_file_and_mixed_series_conflicts(sql, ids):
    sql.torrent("correct", imdb="tt26545971", tmdb=273419, kind="series", season=1, episode=2)
    sql.file("correct", imdb="tt26545971", episode=2)
    sql.torrent("mixed", imdb="tt26545971", tmdb=273419, kind="series", season=1, episode=2)
    sql.file("mixed", imdb="tt26545971", episode=2)
    sql.file("mixed", imdb="tt42026819", episode=2)
    sql.torrent("parent_conflict", imdb="tt42026819", tmdb=273419, kind="series")
    sql.file("parent_conflict", imdb="tt26545971", episode=2)
    sql.torrent("file_conflict", imdb="tt26545971", tmdb=273419, kind="series", season=1, episode=2)
    sql.file("file_conflict", imdb="tt42026819", episode=2)
    sql.torrent("other_series", imdb="tt42026819", tmdb=337905, kind="series")
    sql.file("other_series", imdb="tt42026819", episode=2)
    assert hashes(backend.execute_search(t="tvsearch", season=1, ep=2, **ids)) == ["correct"]


def test_same_row_same_hash_no_cross_episode_identity_or_season_remapping(sql):
    sql.torrent("split", kind="series")
    sql.file("split", imdb="tt26545971", season=1, episode=1)
    sql.file("split", imdb=None, season=1, episode=2)
    sql.torrent("different_season", kind="series")
    sql.file("different_season", imdb="tt26545971", season=2, episode=2)
    sql.torrent("orphan_parent", kind="series", tmdb=273419)
    sql.file("missing_hash", imdb="tt26545971", episode=2)
    assert hashes(backend.execute_search(t="tvsearch", imdbid="26545971", season=1, ep=2)) == []
    assert hashes(backend.execute_search(t="tvsearch", tmdbid=273419, season=1, ep=2)) == []


def test_tmdb_file_requires_parent_match_and_guards_known_imdb(sql):
    sql.torrent("null_file_id", kind="series", tmdb=273419)
    sql.file("null_file_id", imdb=None, episode=2)
    sql.torrent("null_parent_id", kind="series")
    sql.file("null_parent_id", imdb="tt26545971", episode=2)
    sql.torrent("wrong_tmdb", kind="series", tmdb=337905)
    sql.file("wrong_tmdb", imdb="tt26545971", episode=2)
    assert hashes(backend.execute_search(t="tvsearch", tmdbid=273419, season=1, ep=2)) == [
        "null_file_id"
    ]
    assert hashes(
        backend.execute_search(t="tvsearch", imdbid="26545971", tmdbid=273419, season=1, ep=2)
    ) == ["null_file_id", "null_parent_id"]


def test_duplicate_files_and_parent_strategies_are_one_paginated_candidate(sql):
    for name in ("c", "b", "a"):
        sql.torrent(name, imdb="tt26545971", tmdb=273419, kind="series", season=1, episode=2)
        for _ in range(3):
            sql.file(name, imdb="tt26545971", episode=2)
    assert hashes(
        backend.execute_search(
            t="tvsearch", imdbid="26545971", tmdbid=273419, season=1, ep=2, limit=1, offset=1
        )
    ) == ["b"]


def test_diagnostics_observed_memberships_counts_and_serialized_facts(sql):
    sql.torrent("both", imdb="tt123", tmdb=10)
    sql.torrent("imdb", imdb="tt123")
    sql.torrent("tmdb", tmdb=10)
    sql.torrent("uninspected", imdb="tt123", tmdb=10, seeders=1)
    observed = backend.SearchCollector()
    assert hashes(
        backend.execute_search(t="movie", imdbid="123", tmdbid=10, limit=3, collector=observed)
    ) == ["both", "imdb", "tmdb"]
    report = observed.report
    assert report["strategy"] == "multi_strategy"
    assert [
        (s["identifier"], s["candidates"], s["unique_contribution"], s["duration_ms"])
        for s in report["strategies"]
    ] == [("movie_imdb", 2, 1, None), ("movie_tmdb", 2, 1, None), ("movie_pack_imdb", 0, 0, None)]
    assert [p["strategies"] for p in report["provenance"]] == [
        ["movie_imdb", "movie_tmdb"],
        ["movie_imdb"],
        ["movie_tmdb"],
    ]
    assert [p["deduplicated"] for p in report["provenance"]] == [True, False, False]
    assert all(p["included"] is True and p["relevance"] is None for p in report["provenance"])
    assert report["provenance"][0]["match_evidence"] == [
        {"strategy": "movie_imdb", "field": "imdb", "match_type": "exact", "source": "torrent"},
        {"strategy": "movie_tmdb", "field": "tmdb", "match_type": "exact", "source": "torrent"},
    ]
    assert report["counts"]["candidates"] == 3
    observed.versions("test", None)
    assert observed.snapshot()["strategies"][0]["duration_ms"] is None
    assert len(sql.calls) == 1


def test_file_provenance_is_not_attributed_to_parent_episode_strategy(sql):
    sql.torrent("pack", imdb="tt123", tmdb=10, kind="series", season=1, episode=1)
    for _ in range(2):
        sql.file("pack", imdb="tt123", episode=2)
    observed = backend.SearchCollector()
    backend.execute_search(
        t="tvsearch", imdbid="123", tmdbid=10, season=1, ep=2, collector=observed
    )
    assert observed.report["provenance"][0]["strategies"] == [
        "tv_file_imdb_season_episode",
        "tv_file_tmdb_season_episode",
    ]
    assert [s["candidates"] for s in observed.report["strategies"]] == [0, 0, 1, 1]
    evidence = observed.report["provenance"][0]["match_evidence"]
    assert [(e["field"], e["source"]) for e in evidence] == [
        ("imdb", "file"),
        ("season", "file"),
        ("episode", "file"),
        ("tmdb", "torrent"),
        ("season", "file"),
        ("episode", "file"),
    ]


@pytest.mark.parametrize("episode", [None, 2])
def test_tmdb_file_branch_separates_parent_identity_from_same_file_episode_evidence(sql, episode):
    # Neither parent nor child has IMDb; parent has no season/episode metadata.
    sql.torrent("pack", tmdb=10, kind="series")
    sql.file("pack", imdb=None, season=1, episode=2)
    observed = backend.SearchCollector()
    assert hashes(
        backend.execute_search(t="tvsearch", tmdbid=10, season=1, ep=episode, collector=observed)
    ) == ["pack"]
    suffix = "_season_episode" if episode is not None else "_season"
    identifier = f"tv_file_tmdb{suffix}"
    provenance = observed.report["provenance"][0]
    assert provenance["strategies"] == [identifier]
    expected = [
        {"strategy": identifier, "field": "tmdb", "match_type": "exact", "source": "torrent"},
        {"strategy": identifier, "field": "season", "match_type": "exact", "source": "file"},
    ]
    if episode is not None:
        expected.append(
            {"strategy": identifier, "field": "episode", "match_type": "exact", "source": "file"}
        )
    assert provenance["match_evidence"] == expected
    assert observed.report["strategies"][1]["metadata"] == {
        "field": "tmdb",
        "match_type": "exact",
        "source": "torrent",
    }
    # Membership proves categorical predicates, not retrieved/request-only values.
    assert all(
        set(item) == {"strategy", "field", "match_type", "source"}
        for item in provenance["match_evidence"]
    )
    assert len(sql.calls) == 1
    observed.versions("test", None)
    assert observed.snapshot()["provenance"][0]["match_evidence"] == expected


@pytest.mark.parametrize("preset", ["unfiltered", "italian_only", "italian_preferred", "custom"])
def test_real_merged_sql_presets_subtitle_and_xml(sql, preset):
    settings = backend.SETTINGS_STORE.load()
    settings["result_processing"].update(
        preset=preset,
        subtitle_language_correction=True,
        custom_rules=[
            {
                "enabled": True,
                "field": "title",
                "operator": "contains",
                "value": "Fallback",
                "action": "exclude",
            }
        ],
    )
    backend.SETTINGS_STORE.save(settings)
    sql.torrent("a", imdb="tt123", tmdb=10, title="Fallback")
    sql.torrent("b", imdb="tt123", title="Show [SUB ITA]")
    sql.torrent("c", tmdb=10, title="Movie.ITA")
    observed = backend.SearchCollector()
    xml = backend.execute_search(t="movie", imdbid="123", tmdbid=10, collector=observed)
    expected = {
        "unfiltered": ["a", "b", "c"],
        "italian_only": ["c"],
        "italian_preferred": ["c", "a", "b"],
        "custom": ["b", "c"],
    }[preset]
    assert hashes(xml) == expected
    items = ElementTree.fromstring(xml).findall("channel/item")
    for item in items:
        attrs = {a.get("name"): a.get("value") for a in item if a.tag.endswith("attr")}
        assert attrs["category"] == "2000"
        if item.findtext("guid") == "b":
            assert attrs["language"] == "English"
    assert [p["included"] for p in observed.report["provenance"]] == [
        name in expected for name in ("a", "b", "c")
    ]


def test_merged_windows_cross_1000_boundary_without_duplicates_or_backfill(sql):
    settings = backend.SETTINGS_STORE.load()
    settings["result_processing"]["preset"] = "italian_only"
    backend.SETTINGS_STORE.save(settings)
    for index in range(1002):
        sql.torrent(
            f"{index:04d}",
            imdb="tt123",
            tmdb=10,
            title="Fallback" if index == 1000 else "Movie.ITA",
        )
    observed = backend.SearchCollector()
    assert hashes(
        backend.execute_search(
            t="movie", imdbid="123", tmdbid=10, limit=2, offset=999, collector=observed
        )
    ) == ["0999", "1001"]
    assert [values[-2:] for _, values in sql.calls] == [(1000, 0), (1000, 1000)]
    assert observed.report["counts"]["candidates"] == 1002
    assert sum(s["candidates"] for s in observed.report["strategies"]) == 2004


@pytest.mark.parametrize("detailed", [False, True])
def test_merged_observation_faults_cannot_change_sql_or_xml(sql, monkeypatch, detailed):
    from search_diagnostics import DiagnosticResult

    sql.torrent("both", imdb="tt123", tmdb=10)
    expected = backend.execute_search(t="movie", imdbid="123", tmdbid=10)
    calls = list(sql.calls)
    if not detailed:
        monkeypatch.setattr(
            DiagnosticResult, "from_row", lambda *args: pytest.fail("metadata-only adapter")
        )
    observed = backend.SearchCollector(detailed=detailed)
    if detailed:

        def fail(*args, **kwargs):
            raise RuntimeError("collector-only fault")

        monkeypatch.setattr(observed, "merged_window", fail, raising=False)
    assert (
        backend.execute_search(t="movie", imdbid="123", tmdbid=10, collector=observed) == expected
    )
    assert sql.calls == calls * 2
    if not detailed:
        assert observed.report["provenance"] == []
        assert observed.report["strategies"][0]["candidates"] == 1


def test_tv_caps_advertise_implemented_ids_not_tvdb(sql):
    caps = ElementTree.fromstring(backend.make_caps())
    assert caps.find("searching/tv-search").get("supportedParams") == "q,season,ep,imdbid,tmdbid"
    assert "tvdbid" not in str(caps.find("searching/tv-search").attrib)


def test_shared_sql_failure_keeps_attempts_without_inventing_branch_metrics(sql, monkeypatch):
    def fail(*args):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(sql, "execute", fail)
    observed = backend.SearchCollector()
    with pytest.raises(RuntimeError, match="database unavailable"):
        backend.execute_search(t="movie", imdbid="123", tmdbid=10, collector=observed)
    assert [
        (s["identifier"], s["status"], s["candidates"], s["duration_ms"])
        for s in observed.report["strategies"]
    ] == [
        ("movie_imdb", "failed", None, None),
        ("movie_tmdb", "failed", None, None),
        ("movie_pack_imdb", "failed", None, None),
    ]
    assert observed.report["provenance"] == []


def test_exact_sql_no_cross_window_backfill_and_empty_observed_strategies(sql):
    settings = backend.SETTINGS_STORE.load()
    settings["result_processing"]["preset"] = "italian_only"
    backend.SETTINGS_STORE.save(settings)
    for index in range(1001):
        sql.torrent(
            f"{index:04d}",
            imdb="tt123",
            tmdb=10,
            title="Movie.ITA" if index == 1000 else "Fallback",
        )
    observed = backend.SearchCollector()
    assert (
        hashes(
            backend.execute_search(t="movie", imdbid="123", tmdbid=10, limit=1, collector=observed)
        )
        == []
    )
    assert len(sql.calls) == 1
    assert observed.report["counts"]["excluded"] == 1000
    empty = backend.SearchCollector()
    assert (
        hashes(backend.execute_search(t="movie", imdbid="other", tmdbid=99, collector=empty)) == []
    )
    assert [s["candidates"] for s in empty.report["strategies"]] == [0, 0, 0]


@pytest.mark.parametrize("event", ["merged_begin", "merged_window"])
def test_mutating_or_failing_strategy_events_leave_search_unchanged(sql, monkeypatch, event):
    sql.torrent("a", imdb="tt123", tmdb=10)
    sql.torrent("b", imdb="tt123")
    expected = backend.execute_search(t="movie", imdbid="123", tmdbid=10)
    observed = backend.SearchCollector()

    def mutate(*args):
        for value in args:
            if isinstance(value, list):
                value.clear()
        raise RuntimeError("observation mutation")

    monkeypatch.setattr(observed, event, mutate)
    assert (
        backend.execute_search(t="movie", imdbid="123", tmdbid=10, collector=observed) == expected
    )
    assert observed.report["truncated"] is True


def test_merged_inclusion_not_claimed_after_invalid_serialization(sql, monkeypatch):
    sql.torrent("both", imdb="tt123", tmdb=10)
    monkeypatch.setattr(backend, "make_rss", lambda *args, **kwargs: b"<rss><channel/></rss>")
    observed = backend.SearchCollector()
    backend.execute_search(t="movie", imdbid="123", tmdbid=10, collector=observed)
    assert observed.report["provenance"][0]["included"] is None
    assert observed.report["counts"]["returned"] == 0
