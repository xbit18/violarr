"""Offline harness validation: no real connections, credentials or Docker access."""

from types import SimpleNamespace

import benchmark_multi_strategy_postgres as benchmark
import pytest
from test_multi_strategy_search import LocalSQL


@pytest.fixture(autouse=True)
def offline_connections(monkeypatch):
    monkeypatch.setattr(benchmark.psycopg, "connect", lambda **_: pytest.fail("Real connection"))
    for key in ("PGHOSTADDR", "PGSERVICE", "PGOPTIONS", "PGPASSWORD"):
        monkeypatch.delenv(key, raising=False)


@pytest.mark.parametrize(
    "extra", ["", " port=5433", " password=explicit", " port=5433 password=explicit"]
)
def test_explicit_socket_options(extra, monkeypatch):
    monkeypatch.setenv("PGPORT", "6543")
    options = benchmark.local_options("host=/var/run/postgresql dbname=icv_db user=icv" + extra)
    assert options == {
        "host": "/var/run/postgresql",
        "dbname": "icv_db",
        "user": "icv",
        "port": "5433" if "port=" in extra else "5432",
        "connect_timeout": "3",
        "passfile": "/dev/null",
        **({"password": "explicit"} if "password=" in extra else {}),
    }
    assert benchmark.os.environ["PGPORT"] == "6543"


@pytest.mark.parametrize(
    "dsn",
    [
        "dbname=icv_db user=icv",
        "host='' dbname=icv_db user=icv",
        "host=var/run/postgresql dbname=icv_db user=icv",
        "host=@socket dbname=icv_db user=icv",
        "host=/socket,/other dbname=icv_db user=icv",
        "host=/socket dbname='' user=icv",
        "host=/socket dbname=icv_db user=''",
        "host=/socket dbname=icv_db user=icv password=''",
        "host=/socket dbname=icv_db user=icv port=''",
        "host=/socket dbname=icv_db user=icv port=65536",
        "host=/socket dbname=icv_db user=icv port=0",
        "host=/socket dbname=icv_db user=icv port=abc",
        "host=/socket dbname=icv_db user=icv hostaddr=127.0.0.1",
        "host=/socket dbname=icv_db user=icv options=unsafe",
        "host=/socket dbname=icv_db user=icv service=ambient",
        "host=/socket dbname='host=remote' user=icv",
        "host=/socket dbname=postgresql://remote/db user=icv",
        "host=127.0.0.1 port=65536 dbname=test user=test password=test",
    ],
)
def test_reject_ambiguous_socket_or_invalid_tcp(dsn):
    with pytest.raises(ValueError):
        benchmark.local_options(dsn)


@pytest.mark.parametrize("key", ["PGHOSTADDR", "PGSERVICE", "PGOPTIONS", "PGPASSWORD"])
def test_socket_rejects_ambient_routing_or_credentials(key, monkeypatch):
    monkeypatch.setenv(key, "synthetic")
    with pytest.raises(ValueError):
        benchmark.local_options("host=/var/run/postgresql dbname=icv_db user=icv")


@pytest.mark.parametrize(
    "dsn",
    [
        "host=remote port=5432 dbname=test user=test password=test",
        "host=127.0.0.1 dbname=test user=test password=test",
        "host=127.0.0.1 port=5432 dbname=test user=test password=test service=ambient",
        "host=127.0.0.1 port=0 dbname=test user=test password=test",
    ],
)
def test_reject_implicit_or_remote_connection(dsn):
    with pytest.raises(ValueError):
        benchmark.local_options(dsn)


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1", "::1"])
def test_explicit_local_options_disable_ambient_password_file(host):
    options = benchmark.local_options(f"host={host} port=5432 dbname=test user=test password=test")
    assert options == {
        "host": host,
        "port": "5432",
        "dbname": "test",
        "user": "test",
        "password": "test",
        "hostaddr": "::1" if host == "::1" else "127.0.0.1",
        "passfile": "/dev/null",
        "connect_timeout": "3",
    }


def test_analysis_requires_human_review_before_connect(monkeypatch):
    monkeypatch.setattr(benchmark.psycopg, "connect", lambda **_: pytest.fail("Connected"))
    with pytest.raises(SystemExit) as error:
        benchmark.main(["--dsn-env", "UNUSED", "--schema", "dataset", "--analyze"])
    assert error.value.code == 2


def test_no_arguments_do_not_connect(monkeypatch):
    monkeypatch.setattr(benchmark.psycopg, "connect", lambda **_: pytest.fail("Connected"))
    with pytest.raises(SystemExit) as error:
        benchmark.main([])
    assert error.value.code == 2


@pytest.mark.parametrize("socket", [False, True])
def test_default_is_nonexecuting_explain_and_exception_rolls_back(monkeypatch, socket):
    calls = []
    passed = []

    class Fake:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            calls.append("closed")

        def cursor(self):
            return self

        def execute(self, query, values=None):
            calls.append(str(query))
            if "statement_timeout" in str(query):
                assert values == ("1000",)

        def fetchone(self):
            return ([{"Plan": {"Node Type": "Result"}}],)

        def rollback(self):
            calls.append("rollback")

    def connect(**options):
        passed.append(options)
        return Fake()

    monkeypatch.setattr(benchmark.psycopg, "connect", connect)
    options = benchmark.local_options(
        "host=/var/run/postgresql dbname=icv_db user=icv"
        if socket
        else "host=localhost port=5432 dbname=test user=test password=test"
    )
    args = SimpleNamespace(schema="dataset", timeout_ms=1000, analyze=False)
    benchmark.run(options, args)
    assert passed == [dict(options, autocommit=True)]
    plans = [call for call in calls if call.startswith("EXPLAIN")]
    assert len(plans) == 4
    assert all("ANALYZE" not in plan for plan in plans)
    assert calls.count("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY") == 1
    assert any("SET LOCAL search_path" in call for call in calls)
    assert "SELECT set_config('lock_timeout', '1000', true)" in calls
    assert calls[-2:] == ["rollback", "closed"]

    monkeypatch.setattr(
        benchmark,
        "explain",
        lambda *_, **__: (_ for _ in ()).throw(ValueError("simulated failure")),
    )
    calls.clear()
    with pytest.raises(ValueError):
        benchmark.run(options, args)
    assert calls[-2:] == ["rollback", "closed"]


def test_metrics_preserve_nested_subplan_loops_and_sort():
    result = benchmark.plan_metrics(
        {
            "Planning Time": 1,
            "Execution Time": 2,
            "Plan": {
                "Node Type": "Sort",
                "Sort Method": "quicksort",
                "Plans": [
                    {
                        "Node Type": "Aggregate",
                        "Actual Rows": 187,
                        "Actual Loops": 1,
                        "Plans": [
                            {
                                "Node Type": "Index Scan",
                                "Subplan Name": "SubPlan 1",
                                "Actual Loops": 17065,
                                "Shared Hit Blocks": 100,
                            }
                        ],
                    }
                ],
            },
        }
    )
    assert result["nodes"][2]["Actual Loops"] == 17065
    assert result["nodes"][1]["Actual Rows"] == 187
    assert result["nodes"][0]["Sort Method"] == "quicksort"


def test_comparison_counts_full_distinct_rows_separately_from_page():
    database = LocalSQL()
    try:
        for index in range(187):
            database.torrent(
                f"h{index:03d}", imdb="tt123", tmdb=10, kind="series", season=1, episode=1
            )
            database.file(f"h{index:03d}", imdb="tt123")
        cursor = SimpleNamespace(
            execute=database.execute,
            fetchall=database.fetchall,
            fetchone=lambda: database.result.fetchone(),
        )
        args = ("tv", "tt123", 10, 1, 1, 100, 0)
        comparison = benchmark.compare_rows(cursor, args, 1000)
        assert comparison == {
            "complete_raw_rows_equal": True,
            "raw_distinct_total": 187,
            "returned_limit_100": 100,
            "surviving_match_occurrences": 748,
            "surviving_match_occurrences_scope": (
                "all surviving candidates before LIMIT/OFFSET; not prefilter intermediate rows"
            ),
        }
        with pytest.raises(ValueError, match="exceeds"):
            benchmark.compare_rows(cursor, args, 100)
    finally:
        database.db.close()


def test_rejected_candidate_has_intermediate_matches_but_zero_survivors():
    database = LocalSQL()
    try:
        database.torrent("rejected", imdb="tt123", tmdb=10, kind="series", season=1, episode=1)
        database.file("rejected", imdb="tt999", season=1, episode=1)
        args = ("tv", "tt123", 10, 1, 1, 100, 0)
        query, values, _ = benchmark.exact_query(*args)
        # Test-only extraction of the actual, unmodified matches CTE. This fixed
        # request generates three branch occurrences, rejected by final consistency.
        matches, _ = query.split(",\n        merged AS", 1)
        database.execute(matches + " SELECT COUNT(*) FROM matches", values[: matches.count("%s")])
        assert database.result.fetchone()[0] == 3
        cursor = SimpleNamespace(
            execute=database.execute,
            fetchall=database.fetchall,
            fetchone=lambda: database.result.fetchone(),
        )
        comparison = benchmark.compare_rows(cursor, args, 1000)
        assert comparison["raw_distinct_total"] == comparison["returned_limit_100"] == 0
        assert comparison["surviving_match_occurrences"] == 0
        assert comparison["surviving_match_occurrences_scope"] == (
            "all surviving candidates before LIMIT/OFFSET; not prefilter intermediate rows"
        )
        assert "raw_match_occurrences" not in comparison
    finally:
        database.db.close()


def test_help_distinguishes_survivors_from_node_level_intermediate_evidence():
    help_text = " ".join(benchmark.parser().format_help().split())
    assert "surviving_match_occurrences" in help_text
    assert "not prefilter intermediate rows" in help_text
    assert "node-level actual rows/loops" in help_text


def test_alternating_runs_warmups_and_medians_offline(monkeypatch, capsys):
    order = []

    class Fake:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def cursor(self):
            return self

        def execute(self, *args):
            pass

        def rollback(self):
            pass

    def explain(cursor, query, values, analyze=False):
        if analyze:
            order.append("original" if "files other" in query else "optimized")
        return {"Planning Time": 1, "Execution Time": 2, "Plan": {"Node Type": "Result"}}

    monkeypatch.setattr(benchmark.psycopg, "connect", lambda **_: Fake())
    monkeypatch.setattr(benchmark, "explain", explain)
    monkeypatch.setattr(benchmark, "compare_rows", lambda *_: {"complete_raw_rows_equal": True})
    args = SimpleNamespace(
        schema="dataset", timeout_ms=1000, analyze=True, max_rows=1000, warmups=2, runs=5
    )
    benchmark.run({}, args)
    expected = [
        name
        for iteration in range(7)
        for name in (("original", "optimized") if iteration % 2 == 0 else ("optimized", "original"))
    ]
    assert order == expected * 2
    output = capsys.readouterr().out
    assert output.count('"warmup": true') == 8
    assert output.count('"warmup": false') == 20
    assert output.count('"medians"') == 2
