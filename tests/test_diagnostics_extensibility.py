"""Synthetic observation contracts; these fixtures are not search strategies."""

from hashlib import sha256
from xml.etree.ElementTree import fromstring

import pytest

import app as backend
import search_diagnostics as diagnostics
from settings import SettingsStore


def row(title="Movie.ITA", info_hash="a" * 40):
    return (title, 123, 4, "provider", None, info_hash, "movie")


def test_overlapping_strategies_keep_identity_and_occurrences_separate():
    collector = diagnostics.SearchCollector()
    result = diagnostics.DiagnosticResult.from_row(row())
    collector.strategy_begin("synthetic_exact", {"field": "imdb", "password": "secret"})
    collector.strategy_result(result, "0:0")
    collector.strategy_result(result, "0:1")
    collector.strategy_complete(candidates=2)
    collector.strategy_begin("synthetic_title", {"field": "title", "match_type": "contains"})
    collector.strategy_result(result, "1000:7")
    collector.strategy_complete(status="partial", candidates=1)
    collector.strategy_begin("synthetic_failed")
    collector.strategy_complete(status="failed")
    report = collector.report
    assert report["report_version"] == 2
    assert [item["status"] for item in report["strategies"]] == ["success", "partial", "failed"]
    assert [item["candidates"] for item in report["strategies"]] == [2, 1, None]
    assert all(item["unique_contribution"] is None for item in report["strategies"])
    provenance = report["provenance"][0]
    assert provenance["identity"] == sha256(("a" * 40).encode()).hexdigest()
    assert provenance["occurrences"] == ["0:0", "0:1", "1000:7"]
    assert provenance["strategies"] == ["synthetic_exact", "synthetic_title"]
    assert provenance["deduplicated"] is None
    assert provenance["relevance"] is None
    assert provenance["included"] is None
    assert "secret" not in str(report)
    assert "a" * 40 not in str(report)
    collector.result_facts(result, deduplicated=True, included=True)
    assert provenance["deduplicated"] is True
    assert provenance["included"] is True


def test_identity_stable_across_reordering_and_window_offsets():
    identities = []
    for offset, rows in [
        (0, [row(), row("Other", "b" * 40)]),
        (1000, [row("Other", "b" * 40), row()]),
    ]:
        collector = diagnostics.SearchCollector()
        collector.strategy_begin("fixture")
        collector.window(rows, 1000, offset)
        identities.append({item["identity"] for item in collector.report["provenance"]})
    assert identities[0] == identities[1]


EVENTS = [
    "begin",
    "complete",
    "finish",
    "inputs",
    "processing_settings",
    "serialization_settings",
    "strategy_begin",
    "strategy_complete",
    "strategy_result",
    "window",
    "observer",
    "page",
    "serialized",
    "safe_metadata",
]


@pytest.mark.parametrize("event", EVENTS)
@pytest.mark.parametrize("preset", ["unfiltered", "italian_preferred"])
def test_observation_faults_preserve_exact_xml(monkeypatch, tmp_path, event, preset):
    store = SettingsStore(path=tmp_path / "settings.json", environ={})
    settings = store.load()
    settings["result_processing"]["preset"] = preset
    store.save(settings)
    monkeypatch.setattr(backend, "SETTINGS_STORE", store)

    def query(*args):
        diagnostics.record_strategy("fixture")
        return [row(), row(), row("Other", "b" * 40)]

    monkeypatch.setattr(backend, "query_generic", query)
    expected = backend.execute_search(limit=2, offset=0)

    def fault(*args, **kwargs):
        raise RuntimeError("observation failed /private/path password=secret")

    monkeypatch.setattr(diagnostics.SearchCollector, event, fault, raising=False)
    collector = diagnostics.SearchCollector()
    actual = backend.execute_search(limit=2, offset=0, collector=collector)
    assert actual == expected
    assert len(fromstring(actual).findall("channel/item")) == (3 if preset == "unfiltered" else 2)
    assert diagnostics.ACTIVE_COLLECTOR.get() is None


def test_processing_callback_fault_and_search_failure_are_independent(monkeypatch, tmp_path):
    store = SettingsStore(path=tmp_path / "settings.json", environ={})
    monkeypatch.setattr(backend, "SETTINGS_STORE", store)
    monkeypatch.setattr(backend, "query_generic", lambda *args: [row(), row()])
    expected = backend.execute_search(limit=2)

    class BrokenObserver:
        def __call__(self, *args):
            raise RuntimeError("callback failed")

    monkeypatch.setattr(diagnostics.SearchCollector, "observer", lambda *args: BrokenObserver())
    assert backend.execute_search(limit=2, collector=diagnostics.SearchCollector()) == expected

    def fail(*args):
        raise LookupError("actual search failure")

    monkeypatch.setattr(backend, "query_generic", fail)
    monkeypatch.setattr(diagnostics.SearchCollector, "fail", fail)
    with pytest.raises(LookupError, match="actual search failure"):
        backend.execute_search(collector=diagnostics.SearchCollector())
    assert diagnostics.ACTIVE_COLLECTOR.get() is None


def test_optional_phases_and_metadata_are_bounded():
    collector = diagnostics.SearchCollector()
    collector.begin("search")
    collector.complete()
    collector.begin("merge")
    collector.complete()
    for _ in range(40):
        collector.strategy_begin("fixture", {"field": "SELECT secret", "match_type": "exact"})
        collector.strategy_complete(candidates=0)
    assert len(collector.report["strategies"]) == 32
    assert collector.report["truncated"] is True
    assert collector.report["stages"]["merge"]["status"] == "success"
    assert "SELECT" not in str(collector.report)


def test_named_boundary_is_not_constructed_when_disabled_or_metadata_only(monkeypatch, tmp_path):
    store = SettingsStore(path=tmp_path / "settings.json", environ={})
    monkeypatch.setattr(backend, "SETTINGS_STORE", store)
    monkeypatch.setattr(backend, "query_generic", lambda *args: [row(), row()])
    expected = backend.make_rss([row(), row()])

    def forbidden(*args):
        pytest.fail("Metadata-only or disabled observation must not hash/adapt rows")

    monkeypatch.setattr(diagnostics.DiagnosticResult, "from_row", forbidden)
    assert backend.execute_search() == expected
    assert backend.execute_search(collector=diagnostics.SearchCollector(detailed=False)) == expected


def test_serialization_validates_identity_order_without_changing_output(monkeypatch, tmp_path):
    store = SettingsStore(path=tmp_path / "settings.json", environ={})
    monkeypatch.setattr(backend, "SETTINGS_STORE", store)
    rows = [row(), row("Other", "b" * 40)]
    monkeypatch.setattr(backend, "query_generic", lambda *args: rows)
    wrong_order = backend.make_rss(list(reversed(rows)))
    monkeypatch.setattr(backend, "make_rss", lambda *args, **kwargs: wrong_order)
    collector = diagnostics.SearchCollector()
    assert backend.execute_search(collector=collector) == wrong_order
    assert collector.report["stages"]["serialization"]["status"] == "failed"
    assert collector.report["counts"]["returned"] == 0


def test_safe_projection_drops_sensitive_extra_fields():
    collector = diagnostics.SearchCollector()
    collector.versions("1.2.3", None)
    collector.inputs({"q": "hash " + "a" * 40, "apikey": "secret"}, {})
    collector.strategy_begin("fixture", {"field": "title", "sql": "SELECT secret"})
    collector.strategy_result(diagnostics.DiagnosticResult.from_row(row()), "0:0")
    collector.strategy_complete(candidates=1)
    collector.finish()
    collector.report["password"] = "secret"
    collector.report["original"]["apikey"] = "secret"
    collector.report["strategies"][0]["sql"] = "SELECT secret"
    collector.report["provenance"][0]["magnet"] = "magnet:?secret"
    report = collector.snapshot()
    assert report["report_version"] == 2
    assert report["provenance"][0]["relevance"] is None
    assert "secret" not in str(report)
    assert "SELECT" not in str(report)
    assert "a" * 40 not in str(report)


def test_provenance_occurrence_budget_is_global():
    collector = diagnostics.SearchCollector()
    collector.strategy_begin("fixture")
    first = diagnostics.DiagnosticResult.from_row(row())
    second = diagnostics.DiagnosticResult.from_row(row(info_hash="b" * 40))
    for index in range(2001):
        collector.strategy_result(first if index < 1000 else second, f"0:{index}")
    assert sum(len(item["occurrences"]) for item in collector.report["provenance"]) == 2000
    assert collector.report["truncated"] is True


@pytest.mark.parametrize("detailed", [False, True])
@pytest.mark.parametrize("raises", [False, True])
@pytest.mark.parametrize("event", ["window", "processing_settings", "callback"])
def test_mutating_observations_preserve_execution(monkeypatch, tmp_path, event, raises, detailed):
    store = SettingsStore(path=tmp_path / "settings.json", environ={})
    settings = store.load()
    settings["result_processing"]["preset"] = "custom"
    settings["result_processing"]["custom_rules"] = [
        {
            "enabled": True,
            "field": "title",
            "operator": "contains",
            "value": "ITA",
            "action": "score",
            "score": 10,
        }
    ]
    store.save(settings)
    monkeypatch.setattr(backend, "SETTINGS_STORE", store)
    rows = [list(row("Other", "b" * 40)), list(row())]
    monkeypatch.setattr(backend, "query_generic", lambda *args: rows)
    expected = backend.execute_search(limit=2)
    original_rows = [item.copy() for item in rows]

    def mutate(*args):
        if event == "window":
            observed_rows = args[1]
            observed_rows.reverse()
            observed_rows[0][0] = "Changed"
        elif event == "processing_settings":
            processing = args[1]
            processing["preset"] = "italian_only"
            processing["custom_rules"][0]["value"] = "Other"
        else:
            args[1][0] = "Changed"
            args[4].append(99)
        if raises:
            raise RuntimeError("mutation before observation failure")

    if event == "callback":
        monkeypatch.setattr(diagnostics.SearchCollector, "observer", lambda *args: mutate)
    else:
        monkeypatch.setattr(diagnostics.SearchCollector, event, mutate)
    assert (
        backend.execute_search(limit=2, collector=diagnostics.SearchCollector(detailed=detailed))
        == expected
    )
    assert rows == original_rows
    assert store.load() == settings
    assert diagnostics.ACTIVE_COLLECTOR.get() is None


@pytest.mark.parametrize("raises", [False, True])
def test_facade_snapshots_mutable_event_inputs(monkeypatch, raises):
    collector = diagnostics.SearchCollector()
    facade = diagnostics.ObservationFacade(collector)
    payload = {"nested": [{"value": "original"}]}
    indices = [0, 1]
    score_rules = [0]
    mutable_row = list(row())

    def mutate(*args, **kwargs):
        for value in (*args, *kwargs.values()):
            if isinstance(value, dict):
                value["nested"][0]["value"] = "changed"
            elif isinstance(value, list):
                value.clear()
        if raises:
            raise RuntimeError("mutation before observation failure")

    for name in (
        "inputs",
        "strategy_begin",
        "strategy_complete",
        "strategy_result",
        "result_facts",
        "page",
    ):
        monkeypatch.setattr(collector, name, mutate)
    facade.inputs(payload, payload)
    facade.strategy_begin("fixture", payload)
    facade.strategy_complete(candidates=payload)
    result = diagnostics.DiagnosticResult.from_row(row())
    facade.strategy_result(result, "0:0", payload)
    facade.result_facts(result, included=payload)
    facade.page(0, indices)
    monkeypatch.setattr(collector, "observer", lambda *args: mutate)
    facade.observer(0, False)(0, mutable_row, None, 0, score_rules)
    assert payload == {"nested": [{"value": "original"}]}
    assert indices == [0, 1]
    assert score_rules == [0]
    assert mutable_row == list(row())


def test_disabled_observation_does_not_snapshot(monkeypatch, tmp_path):
    monkeypatch.setattr(
        backend, "SETTINGS_STORE", SettingsStore(path=tmp_path / "settings.json", environ={})
    )
    monkeypatch.setattr(backend, "query_generic", lambda *args: [row()])

    def forbidden(*args):
        pytest.fail("Disabled observation must not snapshot inputs")

    monkeypatch.setattr(diagnostics, "observation_snapshot", forbidden, raising=False)
    assert backend.execute_search() == backend.make_rss([row()])
