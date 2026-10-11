import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from prowlarr import ProwlarrError
from settings import SettingsStore
from snapshot_updater import SnapshotUpdater
from version import APP_VERSION
from webapi import create_webapi_router


class FakeUpdater:
    def __init__(self):
        self.reconfigured = 0

    def reconfigure(self):
        self.reconfigured += 1

    async def trigger_manual(self):
        return True

    def status(self):
        return {
            "installed_version": "db-2026-01-01",
            "latest_version": "db-2026-02-01",
            "enabled": True,
            "updating": False,
            "maintenance": False,
            "last_check": None,
            "next_check": None,
            "last_error": None,
        }


class FakeProwlarr:
    def __init__(self, settings, *, fail=False, existing=False):
        self.settings = settings
        self.fail = fail
        self.existing = existing

    def status(self):
        return {
            "configured": bool(self.settings["url"] and self.settings["api_key"]),
            "connected": not self.fail,
            "indexer_installed": self.existing,
            "error": "Prowlarr is unavailable" if self.fail else None,
        }

    def test_connection(self):
        if self.fail:
            raise ProwlarrError(
                "Unable to connect to Prowlarr",
                code="prowlarr_unreachable",
                stage="connect",
            )

    def ensure_indexer(self):
        if self.fail:
            raise ProwlarrError(
                "Prowlarr could not validate the Violarr indexer",
                code="indexer_test_failed",
                stage="indexer_test",
                hint="Check the Indexer URL.",
                upstream_status=400,
                upstream_message="Unable to connect to indexer",
            )
        return {
            "created": not self.existing,
            "already_installed": self.existing,
            "indexer_id": None if self.existing else 7,
        }


@pytest.fixture
def web_client(tmp_path):
    store = SettingsStore(path=tmp_path / "settings.json", environ={})
    settings = store.load()
    settings["prowlarr"] = {
        "url": "http://prowlarr:9696",
        "indexer_url": "http://icvdb-torznab:8000/api",
        "api_key": "never-return-this",
    }
    store.save(settings)

    app = FastAPI(version=APP_VERSION)
    app.state.settings_store = store
    app.state.snapshot_updater = FakeUpdater()
    app.state.database_probe = lambda: True
    app.state.prowlarr_factory = lambda values: FakeProwlarr(values)
    app.include_router(create_webapi_router())

    return TestClient(app), app


def test_manual_update_endpoint_accepts_or_reports_stable_busy(web_client):
    client, app = web_client
    response = client.post("/webapi/database-update")
    assert response.status_code == 202
    assert response.json() == {"accepted": True}

    async def busy():
        return False

    app.state.snapshot_updater.trigger_manual = busy
    response = client.post("/webapi/database-update")
    assert response.status_code == 409
    assert response.json() == {
        "detail": {"code": "update_busy", "message": "A database update is already in progress"}
    }


def test_manual_http_acceptance_status_busy_and_maintenance_recovery(tmp_path, monkeypatch):
    import threading
    from contextlib import nullcontext

    from snapshot_updater import install_snapshot_updater

    store = SettingsStore(path=tmp_path / "settings.json", environ={})
    settings = store.load()
    settings["database_update"]["enabled"] = False
    store.save(settings)
    app = FastAPI()
    app.state.settings_store = store
    app.state.database_probe = lambda: True
    updater = install_snapshot_updater(app, store)
    updater.state_file = tmp_path / "version"
    app.include_router(create_webapi_router())
    entered, release = threading.Event(), threading.Event()

    def discover():
        entered.set()
        assert release.wait(5)
        raise RuntimeError("secret private path")

    monkeypatch.setattr(updater, "get_latest_snapshot", discover)
    monkeypatch.setattr(updater, "_admin_connection", lambda: nullcontext(None))
    monkeypatch.setattr(updater, "_drop_database", lambda *args: None)
    with TestClient(app) as client:
        try:
            assert client.post("/webapi/database-update").status_code == 202
            assert entered.wait(5)
            assert client.get("/webapi/status").json()["updater"]["phase"] == "checking"
            assert client.post("/webapi/database-update").status_code == 409
            updater.maintenance.set()
            assert client.get("/webapi/status").status_code == 503
            updater.maintenance.clear()
            assert client.get("/webapi/status").status_code == 200
        finally:
            release.set()
    assert updater.status()["phase"] == "failed"
    assert updater.status()["last_error"] == "Snapshot update failed"


def test_status_contains_stable_health_summary_without_secrets(web_client):
    client, _ = web_client

    response = client.get("/webapi/status")

    assert response.status_code == 200
    assert response.json()["api_version"] == 1
    assert response.json()["application_version"] == APP_VERSION
    assert response.json()["database"]["connected"] is True
    assert response.json()["result_processing"] == {
        "preset": "unfiltered",
        "custom_rule_count": 0,
    }
    assert response.json()["prowlarr"] == {
        "configured": True,
        "connected": None,
        "indexer_installed": None,
        "error": None,
    }
    assert "never-return-this" not in response.text


def test_status_reports_database_failure_without_calling_prowlarr(web_client):
    client, app = web_client
    app.state.database_probe = lambda: False
    calls = []

    def fail_if_called(values):
        calls.append(values)
        raise AssertionError("aggregate status called Prowlarr")

    app.state.prowlarr_factory = fail_if_called

    body = client.get("/webapi/status").json()

    assert body["database"]["connected"] is False
    assert body["prowlarr"]["connected"] is None
    assert calls == []


def test_get_and_put_settings_mask_secret_and_reconfigure_updater(web_client):
    client, app = web_client

    public = client.get("/webapi/settings").json()

    assert public["prowlarr"]["api_key_configured"] is True
    assert "api_key" not in public["prowlarr"]

    public["database_update"]["interval_seconds"] = 3600
    response = client.put("/webapi/settings", json=public)

    assert response.status_code == 200
    assert response.json()["database_update"]["interval_seconds"] == 3600
    assert app.state.snapshot_updater.reconfigured == 1
    assert "never-return-this" not in response.text


def test_database_update_settings_are_applied_to_real_runtime_updater(tmp_path):
    store = SettingsStore(
        path=tmp_path / "settings.json",
        environ={},
    )

    updater = SnapshotUpdater(settings_store=store)

    app = FastAPI(version=APP_VERSION)
    app.state.settings_store = store
    app.state.snapshot_updater = updater
    app.state.database_probe = lambda: True
    app.include_router(create_webapi_router())

    client = TestClient(app)

    assert updater.enabled is True
    assert updater.interval == 86400

    public = client.get("/webapi/settings").json()
    public["database_update"] = {
        "enabled": False,
        "interval_seconds": 3600,
    }

    response = client.put("/webapi/settings", json=public)

    assert response.status_code == 200
    assert updater.enabled is False
    assert updater.interval == 3600

    persisted = store.load_persisted()
    assert persisted["database_update"] == {
        "enabled": False,
        "interval_seconds": 3600,
    }

    public = client.get("/webapi/settings").json()
    public["database_update"] = {
        "enabled": True,
        "interval_seconds": 7200,
    }

    response = client.put("/webapi/settings", json=public)

    assert response.status_code == 200
    assert updater.enabled is True
    assert updater.interval == 7200

    persisted = store.load_persisted()
    assert persisted["database_update"] == {
        "enabled": True,
        "interval_seconds": 7200,
    }


def test_explicit_database_environment_override_still_wins_at_runtime(tmp_path):
    store = SettingsStore(
        path=tmp_path / "settings.json",
        environ={
            "DB_AUTO_UPDATE": "true",
            "DB_UPDATE_INTERVAL": "7200",
        },
    )

    updater = SnapshotUpdater(settings_store=store)

    app = FastAPI(version=APP_VERSION)
    app.state.settings_store = store
    app.state.snapshot_updater = updater
    app.state.database_probe = lambda: True
    app.include_router(create_webapi_router())

    client = TestClient(app)

    assert updater.enabled is True
    assert updater.interval == 7200

    public = client.get("/webapi/settings").json()
    public["database_update"] = {
        "enabled": False,
        "interval_seconds": 3600,
    }

    response = client.put("/webapi/settings", json=public)

    assert response.status_code == 200

    assert updater.enabled is True
    assert updater.interval == 7200

    assert store.load_persisted()["database_update"] == {
        "enabled": True,
        "interval_seconds": 86400,
    }


def test_put_settings_returns_422_for_invalid_or_unknown_input(web_client):
    client, _ = web_client

    settings = client.get("/webapi/settings").json()
    settings["database_update"]["interval_seconds"] = 1

    assert client.put("/webapi/settings", json=settings).status_code == 422

    settings = client.get("/webapi/settings").json()
    settings["unknown"] = True

    assert client.put("/webapi/settings", json=settings).status_code == 422


def test_result_processing_get_and_put_are_focused(web_client):
    client, _ = web_client

    assert client.get("/webapi/result-processing").json() == {
        "preset": "unfiltered",
        "custom_rules": [],
        "subtitle_language_correction": False,
    }

    response = client.put(
        "/webapi/result-processing",
        json={
            "preset": "italian_preferred",
            "custom_rules": [],
            "subtitle_language_correction": True,
        },
    )

    assert response.status_code == 200
    assert response.json()["preset"] == "italian_preferred"
    assert response.json()["subtitle_language_correction"] is True

    assert (
        client.put(
            "/webapi/result-processing",
            json={
                "preset": "unknown",
                "custom_rules": [],
                "subtitle_language_correction": True,
            },
        ).status_code
        == 422
    )


def test_prowlarr_status_test_and_idempotent_add(web_client):
    client, app = web_client

    assert client.get("/webapi/prowlarr/status").json()["connected"] is True

    assert client.get("/webapi/status").json()["prowlarr"] == {
        "configured": True,
        "connected": True,
        "indexer_installed": False,
        "error": None,
    }

    assert client.post("/webapi/prowlarr/test").json() == {
        "connected": True,
        "error": None,
    }

    assert client.post("/webapi/prowlarr/indexer").json() == {
        "created": True,
        "already_installed": False,
        "indexer_id": 7,
    }

    app.state.prowlarr_factory = lambda values: FakeProwlarr(
        values,
        existing=True,
    )

    assert client.post("/webapi/prowlarr/indexer").json()["already_installed"] is True
    assert client.get("/webapi/status").json()["prowlarr"]["indexer_installed"] is True


def test_prowlarr_errors_are_400_when_unconfigured_and_structured_when_remote_fails(
    web_client,
):
    client, app = web_client

    settings = app.state.settings_store.load_persisted()
    settings["prowlarr"]["url"] = ""
    app.state.settings_store.save(settings)

    assert client.post("/webapi/prowlarr/test").status_code == 400

    settings["prowlarr"]["url"] = "http://prowlarr:9696"
    app.state.settings_store.save(settings)

    app.state.prowlarr_factory = lambda values: FakeProwlarr(
        values,
        fail=True,
    )

    response = client.post("/webapi/prowlarr/indexer")

    assert response.status_code == 502
    assert response.json()["detail"] == {
        "code": "indexer_test_failed",
        "message": "Prowlarr could not validate the Violarr indexer",
        "stage": "indexer_test",
        "hint": "Check the Indexer URL.",
        "upstream_status": 400,
        "upstream_message": "Unable to connect to indexer",
    }
    assert "never-return-this" not in response.text

    cached = client.get("/webapi/status").json()["prowlarr"]
    assert cached["connected"] is True
    assert cached["error"] == "Prowlarr could not validate the Violarr indexer"


def test_connection_failure_returns_structured_error(web_client):
    client, app = web_client

    app.state.prowlarr_factory = lambda values: FakeProwlarr(
        values,
        fail=True,
    )

    response = client.post("/webapi/prowlarr/test")

    assert response.status_code == 502
    assert response.json()["detail"] == {
        "code": "prowlarr_unreachable",
        "message": "Unable to connect to Prowlarr",
        "stage": "connect",
    }


def test_prowlarr_error_body_removes_secret_and_traceback_text(web_client):
    client, app = web_client

    class UnsafeProwlarr(FakeProwlarr):
        def test_connection(self):
            raise ProwlarrError(
                "never-return-this\nTraceback (most recent call last): secret details",
                code="prowlarr_http_error",
                stage="connect",
                upstream_status=500,
                upstream_message="never-return-this Traceback private",
            )

    app.state.prowlarr_factory = lambda values: UnsafeProwlarr(values)

    response = client.post("/webapi/prowlarr/test")

    assert response.status_code == 502
    assert response.json() == {
        "detail": {
            "code": "prowlarr_request_failed",
            "message": "Prowlarr request failed",
            "stage": "connect",
        }
    }
    assert "never-return-this" not in response.text
    assert "Traceback" not in response.text
