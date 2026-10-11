from xml.etree import ElementTree

import pytest

import app as backend
from settings import SettingsStore


def row(title="Movie.ITA", hash_value="secret-hash"):
    return (title, 1234, 7, "provider", None, hash_value, "movie")


@pytest.fixture
def store(tmp_path, monkeypatch):
    value = SettingsStore(path=tmp_path / "settings.json", environ={})
    monkeypatch.setattr(backend, "SETTINGS_STORE", value)
    return value


def run_search(parameters, collector):
    # Before the shared service exists, exercise the real endpoint as baseline.
    execute = getattr(backend, "execute_search", None)
    if execute is None:
        return backend.torznab(**parameters).body
    return execute(**parameters, collector=collector)


class BaselineCollector:
    report = {}


def collector():
    factory = getattr(backend, "SearchCollector", BaselineCollector)
    return factory()


def test_collector_free_nested_real_query_preserves_outer_strategy(store, monkeypatch):
    class Connection:
        nested = False

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def cursor(self):
            return self

        def execute(self, sql, values):
            if not self.nested:
                self.nested = True
                outer = backend.ACTIVE_COLLECTOR.get()
                backend.execute_search(t="movie", imdbid="tt123", collector=None)
                assert backend.ACTIVE_COLLECTOR.get() is outer

        def fetchall(self):
            return []

    # Share the recursion guard between the real generic and movie queries.
    connection = Connection()
    monkeypatch.setattr(backend, "get_conn", lambda: connection)
    observed = collector()
    backend.execute_search(q="outer", collector=observed)
    assert observed.report["strategy"] == "generic_title"
    assert [item["identifier"] for item in observed.report["strategies"]] == ["generic_title"]
    assert observed.report["strategies"][0]["status"] == "success"
    assert backend.ACTIVE_COLLECTOR.get() is None


@pytest.mark.parametrize("preset", ["unfiltered", "italian_only", "italian_preferred", "custom"])
def test_shared_search_exact_xml_and_candidate_identity(store, monkeypatch, preset):
    settings = store.load()
    settings["result_processing"]["preset"] = preset
    store.save(settings)
    same = row()
    monkeypatch.setattr(backend, "query_generic", lambda *args: [same, same, row("Fallback")])
    params = dict(
        t="search",
        q=" %_ ",
        imdbid=None,
        tmdbid=None,
        season=None,
        ep=None,
        cat="5000",
        limit=1,
        offset=1,
    )
    expected = backend.torznab(**params).body
    observed = collector()
    assert run_search(params, observed) == expected
    report = observed.report
    assert report.get("counts", {}).get("candidates") == 3
    assert report["counts"]["returned"] == (3 if preset == "unfiltered" else 1)
    window = 1 if preset == "unfiltered" else 0
    assert [item["id"] for item in report["releases"]] == [f"{window}:{i}" for i in range(3)]
    assert report["releases"][0]["status"] == (
        "returned" if preset == "unfiltered" else "outside_page"
    )
    assert report["stages"]["serialization"]["status"] == "success"
    assert "secret-hash" not in str(report)


def test_crossing_windows_no_backfill(store, monkeypatch):
    settings = store.load()
    settings["result_processing"]["preset"] = "italian_only"
    store.save(settings)
    calls = []

    def query(q, limit, offset):
        calls.append((limit, offset))
        return [row("Fallback"), row("Movie.ITA")]

    monkeypatch.setattr(backend, "query_generic", query)
    observed = collector()
    result = run_search(
        dict(
            t="search", q=None, imdbid=None, tmdbid=None, season=None, ep=None, limit=2, offset=999
        ),
        observed,
    )
    assert len(ElementTree.fromstring(result).findall("channel/item")) == 1
    assert observed.report.get("counts", {}).get("excluded") == 2
    assert calls == [(1000, 0), (1000, 1000)]
    assert [item["id"] for item in observed.report["releases"]] == [
        "0:0",
        "0:1",
        "1000:0",
        "1000:1",
    ]


def test_invalid_serialized_bytes_cannot_claim_success(store, monkeypatch):
    monkeypatch.setattr(backend, "query_generic", lambda *args: [row()])
    monkeypatch.setattr(backend, "make_rss", lambda *args, **kwargs: b"not XML")
    observed = collector()
    assert (
        run_search(
            dict(
                t="search",
                q=None,
                imdbid=None,
                tmdbid=None,
                season=None,
                ep=None,
                limit=1,
                offset=0,
            ),
            observed,
        )
        == b"not XML"
    )
    assert observed.report["stages"]["serialization"]["status"] == "failed"
    assert observed.report["counts"]["returned"] == 0
    assert observed.report["releases"][0]["status"] != "returned"


def test_database_error_is_sanitized_and_partial(store, monkeypatch):
    def fail(*args):
        raise RuntimeError("postgres://user:password@host /private/path SELECT secret")

    monkeypatch.setattr(backend, "query_generic", fail)
    observed = collector()
    with pytest.raises(RuntimeError):
        run_search(
            dict(
                t="search",
                q=None,
                imdbid=None,
                tmdbid=None,
                season=None,
                ep=None,
                limit=1,
                offset=0,
            ),
            observed,
        )
    assert observed.report.get("errors") == [
        {"stage": "database", "code": "database_failed", "message": "Database search failed"}
    ]
    assert observed.report["stages"]["serialization"]["status"] == "not_run"


@pytest.mark.parametrize(
    ("params", "strategy", "sql_values"),
    [
        ({"t": "search", "q": ""}, "generic_browse", (1, 0)),
        ({"t": "search", "q": " %_ "}, "generic_title", ("% %_ %", 1, 0)),
        ({"t": "movie", "imdbid": "  "}, "movie_imdb", ("tt", 1, 0)),
        (
            {"t": "movie", "imdbid": "TT12", "tmdbid": 0},
            "multi_strategy",
            ("ttTT12", 0, 0, "ttTT12", 1, 0),
        ),
        ({"t": "movie", "tmdbid": 0, "q": "title"}, "movie_tmdb", (0, 1, 0)),
        ({"t": "movie", "q": "title"}, "movie_title", ("%title%", 1, 0)),
        ({"t": "movie"}, "movie_empty", None),
        (
            {"t": "tvsearch", "imdbid": "12", "season": 0, "ep": 0},
            "multi_strategy",
            ("tt12", 0, 0, "tt12", 0, 0, "tt12", "tt12", 1, 0),
        ),
        (
            {"t": "tvsearch", "imdbid": "12", "season": 1},
            "multi_strategy",
            ("tt12", 1, "%S01%", "%Season 1%", "tt12", 1, "tt12", "tt12", 1, 0),
        ),
        (
            {"t": "tvsearch", "q": "", "season": 1, "ep": 9},
            "tv_title_season",
            ("%%", 1, "%S01%", "%Season 1%", 1, 0),
        ),
        (
            {"t": "tvsearch", "imdbid": "12", "tmdbid": 77},
            "multi_strategy",
            ("tt12", 77, 77, "tt12", "tt12", 1, 0),
        ),
        ({"t": "tvsearch", "q": "title"}, "tv_title", ("%title%", 1, 0)),
        ({"t": "tvsearch"}, "tv_empty", None),
    ],
)
def test_real_query_branch_strategy_and_binding(store, monkeypatch, params, strategy, sql_values):
    class Connection:
        values = None
        connections = 0

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def cursor(self):
            return self

        def execute(self, sql, values):
            self.values = values

        def fetchall(self):
            return []

    conn = Connection()

    def connect():
        conn.connections += 1
        return conn

    monkeypatch.setattr(backend, "get_conn", connect)
    observed = collector()
    run_search({"limit": 1, "offset": 0, **params}, observed)
    assert observed.report["strategy"] == strategy
    assert conn.values == sql_values
    assert conn.connections == 1


def test_custom_first_exclusion_stable_scores_subtitle(store, monkeypatch):
    settings = store.load()
    rules = [
        {
            "enabled": True,
            "field": "title",
            "operator": "contains",
            "value": "drop",
            "action": "exclude",
        },
        {
            "enabled": True,
            "field": "title",
            "operator": "contains",
            "value": "drop",
            "action": "exclude",
        },
        {
            "enabled": True,
            "field": "seeders",
            "operator": "gte",
            "value": 1,
            "action": "score",
            "score": 10,
        },
    ]
    settings["result_processing"].update(
        preset="custom", custom_rules=rules, subtitle_language_correction=True
    )
    store.save(settings)
    rows = [row("drop"), row("Show [SUB ITA]"), row("Movie.ITA")]
    monkeypatch.setattr(backend, "query_generic", lambda *args: rows)
    observed = collector()
    result = run_search({"limit": 1, "offset": 0}, observed)
    assert ElementTree.fromstring(result).findtext("channel/item/title") == "Show [SUB ITA]"
    assert [item["status"] for item in observed.report["releases"]] == [
        "excluded",
        "returned",
        "outside_page",
    ]
    assert observed.report["releases"][0]["reason"] == "custom_rule:0"
    assert observed.report["releases"][1]["score_rules"] == [2]
    assert observed.report["releases"][1]["language"] == "English"


def test_serialization_success_requires_rss_output_with_actual_items(store, monkeypatch):
    monkeypatch.setattr(backend, "query_generic", lambda *args: [row()])
    monkeypatch.setattr(backend, "make_rss", lambda *args, **kwargs: b"<rss><channel/></rss>")
    observed = collector()
    assert run_search({"limit": 1, "offset": 0}, observed) == b"<rss><channel/></rss>"
    assert observed.report["counts"]["returned"] == 0


def test_settings_reads_remain_separate_and_report_actual_xml_language(store, monkeypatch):
    first = store.load()
    first["result_processing"]["subtitle_language_correction"] = True
    second = store.load()
    second["result_processing"].update(
        preset="italian_preferred", subtitle_language_correction=False
    )
    calls = []

    def load():
        calls.append(len(calls))
        return first if len(calls) == 1 else second

    monkeypatch.setattr(store, "load", load)
    monkeypatch.setattr(backend, "query_generic", lambda *args: [row("Show [SUB ITA]")])
    observed = collector()
    result = run_search({"limit": 1, "offset": 0}, observed)
    assert len(calls) == 2
    assert b'language" value="English' in result
    assert observed.report["releases"][0]["language"] == "English"
    assert observed.report["processing"]["subtitle_language_correction"] is False
    assert observed.report["releases"][0]["subtitle_corrected_for_processing"] is False


@pytest.mark.parametrize("preset", ["unfiltered", "custom", "italian_preferred", "italian_only"])
@pytest.mark.parametrize("correction", [True, False])
def test_processing_subtitle_flag_matches_applied_preset(store, monkeypatch, preset, correction):
    settings = store.load()
    settings["result_processing"].update(preset=preset, subtitle_language_correction=correction)
    store.save(settings)
    monkeypatch.setattr(backend, "query_generic", lambda *args: [row("Show [SUB ITA]"), row()])
    observed = collector()
    result = run_search({"limit": 2, "offset": 0}, observed)
    subtitle, ordinary = observed.report["releases"]
    applied = correction and preset in {"italian_preferred", "italian_only"}
    assert subtitle["subtitle_corrected_for_processing"] is applied
    assert ordinary["subtitle_corrected_for_processing"] is False
    if preset == "italian_only" and correction:
        assert subtitle["status"] == "excluded"
    else:
        assert subtitle["language"] == ("English" if correction else None)
        assert (b'language" value="English' in result) is correction


def test_processing_subtitle_flag_independent_of_earlier_serialization_settings(store, monkeypatch):
    first = store.load()
    first["result_processing"]["subtitle_language_correction"] = False
    second = store.load()
    second["result_processing"].update(
        preset="italian_preferred", subtitle_language_correction=True
    )
    settings_reads = iter([first, second])
    monkeypatch.setattr(store, "load", lambda: next(settings_reads))
    monkeypatch.setattr(backend, "query_generic", lambda *args: [row("Show [SUB ITA]")])
    observed = collector()
    result = run_search({"limit": 1, "offset": 0}, observed)
    assert observed.report["releases"][0]["subtitle_corrected_for_processing"] is True
    assert observed.report["releases"][0]["language"] is None
    assert b'language" value="English' not in result
