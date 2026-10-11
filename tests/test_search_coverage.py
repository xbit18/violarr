"""Offline policy/relational proof; no SQLite emulation of pg_trgm."""

import re

import pytest
from test_multi_strategy_search import LocalSQL

import app as backend
import search_strategies as search
from settings import SettingsStore


@pytest.mark.parametrize(
    "query,expected",
    [
        ("Hanno ucciso l'Uomo Ragno - 883", ("hanno", "ucciso", "l", "uomo", "ragno", "883")),
        ("883", ("883",)),
        ("It", ()),
        ("1080p WEB-DL", ()),
        ("%_", ()),
        ("x" * 129, ()),
    ],
)
def test_title_policy(query, expected):
    assert search.title_terms(query) == expected


class TextSQL(LocalSQL):
    def __init__(self):
        super().__init__()
        # PostgreSQL regexp predicates adapted for literal relational tests only.
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


def candidates(
    db, kind="movie", imdb=None, tmdb=None, q=None, season=None, ep=None, limit=100, offset=0
):
    sql, values, strategies = search.coverage_query(
        kind, imdb, tmdb, q, season, ep, limit, offset, fuzzy=False
    )
    db.execute(sql, values)
    return search.CandidateRows(db.fetchall(), strategies)


def test_literal_merge_ids_conflicts_tiers_and_public_metadata():
    db = TextSQL()
    try:
        db.torrent("exact", imdb="tt1", title="Other", seeders=1)
        db.torrent("text", title="Hanno.ucciso.l'Uomo.Ragno.883.ITA", seeders=100)
        db.torrent("both", imdb="tt1", title="Hanno ucciso l Uomo Ragno 883", seeders=2)
        db.torrent("conflict", imdb="tt2", title="Hanno ucciso l Uomo Ragno 883")
        rows = candidates(db, imdb="tt1", q="Hanno ucciso l'Uomo Ragno")
        assert [r[5] for r in rows] == ["both", "exact", "text"]
        assert len(rows[0]) == 7
        assert len(rows.memberships[0]) == 2
        assert [
            r[5]
            for r in candidates(db, imdb="tt1", q="Hanno ucciso l'Uomo Ragno", limit=1, offset=1)
        ] == ["exact"]
    finally:
        db.db.close()


def test_tv_series_title_and_episode_are_one_qualified_row():
    db = TextSQL()
    try:
        db.torrent("correct", kind="series", title="Show.S01E02", season=1, episode=2)
        db.torrent("wrong", kind="series", title="Show.S01E09", season=1, episode=9)
        db.torrent("bait", kind="series", title="Other.S01E02.Show", season=1, episode=2)
        db.torrent("pack", kind="series", title="Season pack")
        db.file("pack", imdb="tt1", season=1, episode=2)
        db.db.execute("UPDATE files SET title='Show.S01E02' WHERE info_hash='pack'")
        db.torrent("split", kind="series", title="Other")
        db.file("split", imdb="tt1", season=1, episode=9)
        db.file("split", imdb="tt1", season=1, episode=2)
        db.db.execute(
            "UPDATE files SET title='Show.S01E09' WHERE info_hash='split' AND imdb_episode=9"
        )
        assert [r[5] for r in candidates(db, "tv", q="Show", season=1, ep=2)] == ["correct", "pack"]
    finally:
        db.db.close()


def test_pack_child_identity_is_not_parent_identity_or_folder_title():
    db = TextSQL()
    try:
        for name in ("child", "folder", "extras"):
            db.torrent(name, imdb="ttParent", tmdb=99, title="Collection")
        for name, identity, path in [
            ("child", "tt1", "Collection/Arrival.2016.mkv"),
            ("folder", None, "Arrival/Other.2016.mkv"),
            ("extras", None, "Extras/Arrival.trailer.mkv"),
        ]:
            db.db.execute(
                "INSERT INTO pack_files(pack_hash, imdb_id, file_path) VALUES (?,?,?)",
                (name, identity, path),
            )
        assert [r[5] for r in candidates(db, imdb="tt1", tmdb=10)] == ["child"]
        rows = candidates(db, q="Arrival")
        assert [r[5] for r in rows] == ["child"]
        assert rows[0][0] == "Collection"
        assert "movie_pack_imdb" in candidates(db, imdb="tt1").memberships[0]
    finally:
        db.db.close()


def test_fuzzy_sql_is_index_supported_bounded_and_not_score_ordered():
    sql, values, strategies = search.coverage_query("tv", "tt1", 10, "Breaking Badd", 1, 2, 20, 4)
    assert "%%>>" in sql
    assert "strict_word_similarity" in sql
    # Extent similarity alone can match an episode name after the series name.
    assert "AND similarity(%s, identity_title)" in sql
    assert "LIMIT 2001" in sql
    assert "COUNT(*)" in sql
    assert "Breaking Badd" not in sql
    assert values[-2:] == (20, 4)
    assert "tv_title_fuzzy" in strategies
    assert (
        sql.rsplit("ORDER BY", 1)[1]
        .strip()
        .startswith("m.tier ASC, t.seeders DESC NULLS LAST, t.info_hash ASC")
    )


def test_short_numeric_title_keeps_literal_without_fuzzy():
    sql, _, strategies = search.coverage_query("movie", None, None, "883", None, None, 10, 0)
    assert "movie_title_text" in strategies
    assert "fuzzy" not in " ".join(strategies)
    assert "%%>>" not in sql


def test_coverage_benchmark_cases_are_executable_queries_without_connections():
    import benchmark_multi_strategy_postgres as benchmark

    queries = benchmark.coverage_cases()
    assert {"punctuation-883", "tv-typo", "movie-pack-imdb", "short-title", "ids-and-title"} <= set(
        queries
    )
    for query, values, descriptors in queries.values():
        assert query.startswith("\n")
        assert len(values) == query.count("%s")
        assert descriptors


def test_routes_and_diagnostics_use_the_same_coverage_stream(monkeypatch, tmp_path):
    monkeypatch.setattr(
        backend, "SETTINGS_STORE", SettingsStore(tmp_path / "settings.json", environ={})
    )
    calls = []

    def query(*args):
        calls.append(args)
        descriptors = {
            "movie_pack_title_fuzzy": {
                "field": "title",
                "match_type": "fuzzy",
                "source": "pack_file",
            }
        }
        facade = backend.ACTIVE_COLLECTOR.get()
        if facade:
            facade.merged_begin(descriptors)
        return search.CandidateRows(
            [("Collection", 123, 4, "provider", None, "private-hash", "movie", 1, 1)], descriptors
        )

    monkeypatch.setattr(backend, "query_coverage", query, raising=False)
    # Legacy routing must not execute SQL in this offline route proof.
    monkeypatch.setattr(backend, "get_conn", lambda: pytest.fail("Unexpected database connection"))
    xml = backend.execute_search(t="movie", imdbid="1", q="Arrival")
    report = backend.SearchCollector()
    assert backend.execute_search(t="movie", imdbid="1", q="Arrival", collector=report) == xml
    assert len(calls) == 2
    evidence = report.report["provenance"][0]["match_evidence"]
    assert evidence == [
        {
            "strategy": "movie_pack_title_fuzzy",
            "field": "title",
            "match_type": "fuzzy",
            "source": "pack_file",
        }
    ]
    assert "private-hash" not in str(report.report)
    report.versions("test", None)
    assert report.snapshot()["strategies"][0]["duration_ms"] is None


def test_tier_windows_presets_and_observation_faults(monkeypatch, tmp_path):
    db = TextSQL()
    store = SettingsStore(tmp_path / "settings.json", environ={})
    monkeypatch.setattr(backend, "SETTINGS_STORE", store)
    monkeypatch.setattr(backend, "get_conn", lambda: db)
    # This corpus exercises the real literal SQL/merge, not synthetic pg_trgm.
    from test_multi_strategy_search import hashes

    try:
        for index in range(1002):
            db.torrent(
                f"{index:04d}",
                imdb="tt1" if index < 1000 else None,
                title="Arrival.ITA" if index != 1000 else "Arrival.ENG",
                seeders=1 if index < 1000 else 100,
            )
        settings = store.load()
        settings["result_processing"]["preset"] = "italian_only"
        store.save(settings)
        observed = backend.SearchCollector()
        xml = backend.execute_search(
            t="movie", imdbid="1", q="Arrival", offset=999, limit=2, collector=observed
        )
        assert hashes(xml) == ["0999", "1001"]
        assert [values[-2:] for _, values in db.calls] == [(1000, 0), (1000, 1000)]
        assert observed.report["counts"]["candidates"] == 1002
        assert observed.report["counts"]["excluded"] == 1
        for event in ("merged_begin", "merged_window"):
            collector = backend.SearchCollector()
            monkeypatch.setattr(
                collector, event, lambda *args: (_ for _ in ()).throw(RuntimeError())
            )
            assert (
                backend.execute_search(
                    t="movie", imdbid="1", q="Arrival", offset=999, limit=2, collector=collector
                )
                == xml
            )
    finally:
        db.db.close()


def test_new_coverage_metrics_validate_contract_without_legacy_equality():
    from types import SimpleNamespace

    import benchmark_multi_strategy_postgres as benchmark

    db = TextSQL()
    try:
        db.torrent("exact", imdb="tt1", seeders=1)
        db.torrent("text", title="Arrival", seeders=100)
        query = search.coverage_query("movie", "tt1", None, "Arrival", None, None, 1, 0)
        cursor = SimpleNamespace(
            execute=db.execute, fetchall=db.fetchall, fetchone=lambda: db.result.fetchone()
        )
        metrics = benchmark.coverage_counts(cursor, query, 100)
        assert metrics["candidate_stream_total"] == 2
        assert metrics["returned_page"] == 1
        assert metrics["tier_seeders_hash_order"] is True
        with pytest.raises(ValueError, match="exceeds"):
            benchmark.coverage_counts(cursor, query, 1)
    finally:
        db.db.close()


def test_episode_only_file_evidence_does_not_invent_season(monkeypatch, tmp_path):
    db = TextSQL()
    monkeypatch.setattr(backend, "get_conn", lambda: db)
    monkeypatch.setattr(
        backend, "SETTINGS_STORE", SettingsStore(tmp_path / "settings.json", environ={})
    )
    try:
        db.torrent("pack", kind="series", title="Collection")
        db.file("pack", imdb="tt1", season=7, episode=2)
        db.db.execute("UPDATE files SET title='Show.S07E02'")
        collector = backend.SearchCollector()
        backend.execute_search(t="tvsearch", q="Show", ep=2, collector=collector)
        evidence = collector.report["provenance"][0]["match_evidence"]
        assert {item["field"] for item in evidence} == {"title", "episode"}
    finally:
        db.db.close()


@pytest.mark.parametrize("q", [None, "Arrival"])
def test_four_tier_movie_parent_before_high_seeder_pack_with_overlap_and_pages(
    monkeypatch, tmp_path, q
):
    from test_multi_strategy_search import hashes

    db = TextSQL()
    monkeypatch.setattr(backend, "get_conn", lambda: db)
    monkeypatch.setattr(
        backend, "SETTINGS_STORE", SettingsStore(tmp_path / "settings.json", environ={})
    )
    try:
        db.torrent("z_parent", imdb="tt1", seeders=1, title="Arrival")
        db.torrent("b_overlap", imdb="tt1", seeders=2, title="Arrival")
        db.torrent("a_child", imdb="ttCollection", seeders=100, title="Collection")
        for name in ("a_child", "b_overlap"):
            db.db.execute(
                "INSERT INTO pack_files(pack_hash,imdb_id,file_path) VALUES (?,?,?)",
                (name, "tt1", "Arrival.mkv"),
            )
        parameters = dict(t="movie", imdbid="1", q=q)
        collector = backend.SearchCollector()
        assert hashes(backend.execute_search(**parameters, collector=collector)) == [
            "b_overlap",
            "z_parent",
            "a_child",
        ]
        assert hashes(backend.execute_search(**parameters, limit=1)) == ["b_overlap"]
        assert hashes(backend.execute_search(**parameters, limit=1, offset=1)) == ["z_parent"]
        assert hashes(backend.execute_search(**parameters, limit=1, offset=2)) == ["a_child"]
        assert {"movie_imdb", "movie_pack_imdb"} <= set(
            collector.report["provenance"][0]["strategies"]
        )
        # Child selection never replaces collection metadata or invents confidence.
        assert collector.report["releases"][2]["title"] == "Collection"
        assert collector.report["provenance"][2]["match_evidence"][0] == {
            "strategy": "movie_pack_imdb",
            "field": "imdb",
            "match_type": "exact",
            "source": "pack_file",
        }
        assert collector.report["provenance"][2]["relevance"] is None
        assert all(strategy["duration_ms"] is None for strategy in collector.report["strategies"])
    finally:
        db.db.close()


@pytest.mark.parametrize("q", [None, "Show"])
@pytest.mark.parametrize("ids", [{"imdbid": "1"}, {"tmdbid": 10}, {"imdbid": "1", "tmdbid": 10}])
def test_four_tier_tv_parent_before_high_seeder_file_with_overlap_and_pages(
    monkeypatch, tmp_path, q, ids
):
    from test_multi_strategy_search import hashes

    db = TextSQL()
    monkeypatch.setattr(backend, "get_conn", lambda: db)
    monkeypatch.setattr(
        backend, "SETTINGS_STORE", SettingsStore(tmp_path / "settings.json", environ={})
    )
    try:
        for name, seeders, episode in [
            ("z_parent", 1, 2),
            ("b_overlap", 2, 2),
            ("a_child", 100, 1),
        ]:
            db.torrent(
                name,
                imdb="tt1",
                tmdb=10,
                kind="series",
                title="Show",
                season=1,
                episode=episode,
                seeders=seeders,
            )
        for name in ("b_overlap", "a_child"):
            db.file(name, imdb="tt1", season=1, episode=2)
        db.db.execute("UPDATE files SET title='Show.S01E02'")
        parameters = dict(t="tvsearch", q=q, season=1, ep=2, **ids)
        collector = backend.SearchCollector()
        assert hashes(backend.execute_search(**parameters, collector=collector)) == [
            "b_overlap",
            "z_parent",
            "a_child",
        ]
        assert hashes(backend.execute_search(**parameters, limit=1)) == ["b_overlap"]
        assert hashes(backend.execute_search(**parameters, limit=1, offset=1)) == ["z_parent"]
        assert hashes(backend.execute_search(**parameters, limit=1, offset=2)) == ["a_child"]
        overlap = collector.report["provenance"][0]
        assert any(name.startswith("tv_file_") for name in overlap["strategies"])
        assert any(not name.startswith("tv_file_") for name in overlap["strategies"])
        if "tmdbid" in ids:
            evidence = collector.report["provenance"][2]["match_evidence"]
            assert {
                "strategy": "tv_file_tmdb_season_episode",
                "field": "tmdb",
                "match_type": "exact",
                "source": "torrent",
            } in evidence
            assert {
                "strategy": "tv_file_tmdb_season_episode",
                "field": "episode",
                "match_type": "exact",
                "source": "file",
            } in evidence
    finally:
        db.db.close()


def test_four_tier_every_strategy_has_explicit_correct_rank():
    for kind in ("movie", "tv"):
        query, _, descriptors = search.coverage_query(kind, "tt1", 10, "Breaking Badd", 1, 2, 20, 0)
        for name in descriptors:
            rank = (
                3
                if name.endswith("_fuzzy")
                else 2
                if name.endswith("_text")
                else 1
                if name.startswith(("tv_file_", "movie_pack_"))
                else 0
            )
            assert f"WHEN '{name}' THEN {rank}" in query


def test_four_tier_benchmark_rejects_old_exact_child_first_stream():
    from types import SimpleNamespace

    import benchmark_multi_strategy_postgres as benchmark

    descriptors = {"movie_imdb": {}, "movie_pack_imdb": {}}
    parent = ("Parent", 1, 1, "provider", None, "z_parent", "movie", 1, 0, 1)
    child = ("Collection", 1, 100, "provider", None, "a_child", "movie", 0, 1, 1)
    for rows, rejected in [([parent, child], False), ([child, parent], True)]:
        cursor = SimpleNamespace(
            execute=lambda *args: None, fetchone=lambda: (2,), fetchall=lambda: rows
        )
        generated = ("SELECT candidates LIMIT %s OFFSET %s", (100, 0), descriptors)
        if rejected:
            with pytest.raises(AssertionError):
                benchmark.coverage_counts(cursor, generated, 100)
        else:
            assert (
                benchmark.coverage_counts(cursor, generated, 100)["tier_seeders_hash_order"] is True
            )
