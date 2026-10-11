from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from fastapi.testclient import TestClient

import app as backend
from settings import SettingsStore


@pytest.fixture
def client(tmp_path, monkeypatch):
    store = SettingsStore(path=tmp_path / "settings.json", environ={})
    monkeypatch.setattr(backend, "SETTINGS_STORE", store)
    monkeypatch.setattr(backend.app.state, "settings_store", store)
    monitor = getattr(backend.app.state, "search_monitor", None)
    if monitor is not None:
        monitor.configure(False)
        monitor.clear()
    backend.app.state.snapshot_updater.maintenance.clear()
    monkeypatch.setattr(
        backend,
        "query_generic",
        lambda *args: [(args[0] or "Movie.ITA", 10, 2, "provider", None, "private-hash", "movie")],
    )
    yield TestClient(backend.app, raise_server_exceptions=False)
    backend.app.state.snapshot_updater.maintenance.clear()
    if monitor is not None:
        monitor.configure(False)
        monitor.clear()


def enable(client):
    response = client.put("/webapi/diagnostics/monitoring", json={"enabled": True})
    assert response.status_code == 200
    return response.json()


def test_monitor_defaults_off_and_diagnostic_report_is_safe(client):
    status = client.get("/webapi/diagnostics/monitoring")
    assert status.status_code == 200
    assert status.json() == {"enabled": False, "capacity": 100, "count": 0}
    normal = client.get("/api", params={"q": "Movie"})
    response = client.post("/webapi/diagnostics/search", json={"q": "Movie"})
    assert response.status_code == 200
    report = response.json()
    assert report["report_version"] == 2
    assert report["application_version"] == backend.APP_VERSION
    assert "snapshot_version" in report
    assert report["stages"]["serialization"]["status"] == "success"
    assert report["counts"]["returned"] == 1
    assert normal.status_code == 200
    assert "private-hash" not in str(report)
    assert client.get("/webapi/diagnostics/requests").json() == {"requests": []}


def test_monitor_history_order_clear_replay_current_state(client, monkeypatch):
    enable(client)
    for q in ["first", "second"]:
        assert (
            client.get(
                "/api",
                params={"q": q, "apikey": "never-store", "extended": 1},
                headers={"Authorization": "secret"},
            ).status_code
            == 200
        )
    entries = client.get("/webapi/diagnostics/requests").json()["requests"]
    assert [item["original"]["q"] for item in entries] == ["second", "first"]
    assert entries[0]["status_code"] == 200
    assert entries[0]["counts"]["returned"] == 1
    assert "releases" not in entries[0]
    assert "never-store" not in str(entries)
    monkeypatch.setattr(backend, "query_generic", lambda *args: [])
    report = client.post("/webapi/diagnostics/search", json=entries[0]["original"]).json()
    assert report["counts"]["returned"] == 0
    assert len(client.get("/webapi/diagnostics/requests").json()["requests"]) == 2
    assert client.delete("/webapi/diagnostics/requests").status_code == 200
    assert client.get("/webapi/diagnostics/requests").json() == {"requests": []}


@pytest.mark.parametrize(
    "payload",
    [
        {"t": "caps"},
        {"q": "x" * 513},
        {"limit": 201},
        {"offset": -1},
        {"offset": 1000001},
        {"apikey": "secret"},
        {"limit": True},
        {"season": "1"},
    ],
)
def test_strict_diagnostic_input_validation(client, payload):
    assert client.post("/webapi/diagnostics/search", json=payload).status_code == 422


def test_monitor_actual_validation_maintenance_and_failure_status(client, monkeypatch):
    enable(client)
    assert client.get("/api", params={"limit": "invalid"}).status_code == 422
    backend.app.state.snapshot_updater.maintenance.set()
    blocked = client.get("/api", params={"q": "blocked"})
    assert blocked.status_code == 503
    assert blocked.json() == {"detail": "Database update in progress"}
    assert blocked.headers["retry-after"] == "5"
    backend.app.state.snapshot_updater.maintenance.clear()

    def fail(*args):
        raise RuntimeError("SELECT password FROM /private/path postgres://user:secret@host")

    monkeypatch.setattr(backend, "query_generic", fail)
    failed = client.get("/api", params={"q": "failed"})
    assert failed.status_code == 500
    assert failed.text == "Internal Server Error"
    report = client.post("/webapi/diagnostics/search", json={"q": "failed"}).json()
    assert report["errors"][0]["code"] == "database_failed"
    entries = client.get("/webapi/diagnostics/requests").json()["requests"]
    assert [entry["status_code"] for entry in entries] == [500, 503, 422]
    assert entries[1]["strategy"] is None
    assert entries[2]["stages"]["database"]["status"] == "not_run"
    assert "private/path" not in str(entries) + str(report)
    assert "secret@" not in str(entries) + str(report)


def test_ring_eviction_concurrency_bounded_values(client):
    enable(client)
    with ThreadPoolExecutor(max_workers=8) as pool:
        responses = list(
            pool.map(lambda i: client.get("/api", params={"q": f"request-{i}"}), range(110))
        )
    assert all(response.status_code == 200 for response in responses)
    entries = client.get("/webapi/diagnostics/requests").json()["requests"]
    assert len(entries) == 100
    assert len({entry["id"] for entry in entries}) == 100
    assert all(entry["original"]["q"] == entry["normalized"]["q"] for entry in entries)
    client.get(
        "/api",
        params={"q": "x" * 10000, "cat": "magnet:?xt=secret", "imdbid": "postgres://user:pw@host"},
    )
    entry = client.get("/webapi/diagnostics/requests").json()["requests"][0]
    assert entry["truncated"] is True
    assert entry["replayable"] is False
    assert len(entry["original"]["q"]) == 512
    assert "magnet:" not in str(entry)
    assert "user:pw" not in str(entry)


def test_monitor_storage_fault_does_not_change_response(client, monkeypatch):
    enable(client)
    expected = client.get("/api", params={"q": "same"})

    def fail(*args, **kwargs):
        raise RuntimeError("monitor broken")

    monkeypatch.setattr(backend.app.state.search_monitor, "append", fail)
    actual = client.get("/api", params={"q": "same"})
    assert (actual.status_code, actual.content) == (expected.status_code, expected.content)


def test_monitor_bounded_integer_fields_and_nonreplayable_validation(client):
    enable(client)
    client.get("/api", params={"offset": 10**100, "q": "huge"})
    entry = client.get("/webapi/diagnostics/requests").json()["requests"][0]
    assert entry["original"]["offset"] is None
    assert entry["truncated"] is True
    assert entry["replayable"] is False


@pytest.mark.parametrize(
    "event",
    [
        "begin",
        "complete",
        "finish",
        "inputs",
        "processing_settings",
        "serialization_settings",
        "strategy_begin",
        "strategy_complete",
        "window",
        "observer",
        "page",
        "serialized",
    ],
)
def test_monitor_collector_fault_does_not_change_response(client, monkeypatch, event):
    enable(client)
    expected = client.get("/api", params={"q": "same"})

    def fail(*args, **kwargs):
        raise RuntimeError("collector broken")

    monkeypatch.setattr(backend.SearchCollector, event, fail)
    actual = client.get("/api", params={"q": "same"})
    assert (actual.status_code, actual.content) == (expected.status_code, expected.content)


def test_processing_error_report_and_strict_toggle(client, monkeypatch):
    assert client.put("/webapi/diagnostics/monitoring", json={"enabled": "true"}).status_code == 422
    settings = backend.SETTINGS_STORE.load()
    settings["result_processing"]["preset"] = "italian_only"
    backend.SETTINGS_STORE.save(settings)

    def fail(*args, **kwargs):
        raise RuntimeError("/private/secret password=secret")

    monkeypatch.setattr(backend, "process_results", fail)
    report = client.post("/webapi/diagnostics/search", json={"q": "same"}).json()
    assert report["errors"][0]["code"] == "processing_failed"
    assert report["stages"]["database"]["status"] == "success"
    assert report["stages"]["serialization"]["status"] == "not_run"
    assert report["counts"]["returned"] == 0


def test_diagnostic_validation_never_echoes_rejected_secrets(client):
    response = client.post("/webapi/diagnostics/search", json={"apikey": "never-echo-this-secret"})
    assert response.status_code == 422
    assert "never-echo-this-secret" not in response.text
    toggle = client.put("/webapi/diagnostics/monitoring", json={"enabled": "password=secret"})
    assert toggle.status_code == 422
    assert "password=secret" not in toggle.text


@pytest.mark.parametrize(
    ("method", "path"),
    [("POST", "/webapi/diagnostics/search"), ("PUT", "/webapi/diagnostics/monitoring")],
)
@pytest.mark.parametrize("body", [b"\xff", b'{"q":'])
def test_diagnostic_malformed_json_uses_safe_validation_contract(client, method, path, body):
    response = client.request(
        method, path, content=body, headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 422
    assert response.json() == {
        "detail": {
            "code": "invalid_parameters",
            "message": "Invalid diagnostic parameters",
        }
    }


def test_diagnostic_body_normalization_leaves_other_errors_untouched(client, monkeypatch):
    from fastapi import HTTPException

    ordinary = client.put(
        "/webapi/settings", content=b"\xff", headers={"Content-Type": "application/json"}
    )
    assert ordinary.status_code == 400

    def fail(parameters):
        raise HTTPException(status_code=409, detail="Application conflict")

    monkeypatch.setattr(backend.app.state, "diagnostic_search", fail)
    response = client.post("/webapi/diagnostics/search", json={})
    assert response.status_code == 409
    assert response.json() == {"detail": "Application conflict"}


def test_detailed_concurrent_reports_are_request_scoped(client):
    with ThreadPoolExecutor(max_workers=8) as pool:
        reports = list(
            pool.map(
                lambda i: client.post(
                    "/webapi/diagnostics/search", json={"q": f"query-{i}"}
                ).json(),
                range(20),
            )
        )
    assert [report["releases"][0]["title"] for report in reports] == [
        f"query-{i}" for i in range(20)
    ]
    assert all(report["counts"]["returned"] == 1 for report in reports)


def test_report_flags_provider_and_rule_metadata_truncation(client, monkeypatch):
    settings = backend.SETTINGS_STORE.load()
    settings["result_processing"].update(
        preset="custom",
        custom_rules=[
            {
                "enabled": True,
                "field": "title",
                "operator": "contains",
                "value": "magnet:?xt=private",
                "action": "exclude",
            }
        ],
    )
    backend.SETTINGS_STORE.save(settings)
    monkeypatch.setattr(
        backend,
        "query_generic",
        lambda *args: [("Movie", 10, 2, "x" * 1000, None, "hash", "movie")],
    )
    response = client.post("/webapi/diagnostics/search", json={"q": "Movie"})
    assert response.status_code == 200
    report = response.json()
    assert report["truncated"] is True
    assert len(report["releases"][0]["provider"]) == 512
    assert "magnet:" not in str(report)


def test_monitor_orders_by_request_arrival_not_completion(client, monkeypatch):
    enable(client)
    entered = Event()
    release = Event()

    def query(q, *args):
        if q == "earlier":
            entered.set()
            assert release.wait(5)
        return []

    monkeypatch.setattr(backend, "query_generic", query)
    with ThreadPoolExecutor(max_workers=2) as pool:
        slow = pool.submit(client.get, "/api", params={"q": "earlier"})
        assert entered.wait(5)
        assert client.get("/api", params={"q": "later"}).status_code == 200
        release.set()
        assert slow.result().status_code == 200
    entries = client.get("/webapi/diagnostics/requests").json()["requests"]
    assert [entry["original"]["q"] for entry in entries] == ["later", "earlier"]


@pytest.mark.parametrize("disable", [True, False])
def test_clear_discards_inflight_capture_and_allows_postclear_requests(
    client, monkeypatch, disable
):
    entered = Event()
    release = Event()
    old_row = ("private-before-clear", 10, 2, "provider", None, "private-hash", "movie")
    expected = backend.make_rss([old_row])

    def query(q, *args):
        if q == "private-before-clear":
            entered.set()
            assert release.wait(10)
            return [old_row]
        return []

    monkeypatch.setattr(backend, "query_generic", query)
    enable(client)
    with ThreadPoolExecutor(max_workers=8) as pool:
        slow = pool.submit(client.get, "/api", params={"q": "private-before-clear"})
        try:
            assert entered.wait(5)
            if disable:
                assert (
                    client.put(
                        "/webapi/diagnostics/monitoring", json={"enabled": False}
                    ).status_code
                    == 200
                )
            assert client.delete("/webapi/diagnostics/requests").json() == {"requests": []}
            if not disable:
                responses = list(
                    pool.map(lambda i: client.get("/api", params={"q": f"postclear-{i}"}), range(8))
                )
                assert all(response.status_code == 200 for response in responses)
        finally:
            release.set()
        response = slow.result(timeout=5)
    assert response.status_code == 200
    assert response.content == expected
    entries = client.get("/webapi/diagnostics/requests").json()["requests"]
    assert {entry["original"]["q"] for entry in entries} == (
        set() if disable else {f"postclear-{i}" for i in range(8)}
    )
    if disable:
        assert client.get("/webapi/diagnostics/monitoring").json() == {
            "enabled": False,
            "capacity": 100,
            "count": 0,
        }
        enable(client)
        with ThreadPoolExecutor(max_workers=8) as pool:
            responses = list(
                pool.map(lambda i: client.get("/api", params={"q": f"postclear-{i}"}), range(8))
            )
        assert all(response.status_code == 200 for response in responses)
        entries = client.get("/webapi/diagnostics/requests").json()["requests"]
    assert {entry["original"]["q"] for entry in entries} == {f"postclear-{i}" for i in range(8)}
    assert len({entry["id"] for entry in entries}) == 8
    assert [entry["id"] for entry in entries] == sorted(
        (entry["id"] for entry in entries), reverse=True
    )
