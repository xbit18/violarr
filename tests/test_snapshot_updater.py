import asyncio
import hashlib
import io
import json
import threading
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from settings import SettingsStore
from snapshot_updater import Snapshot, SnapshotUpdater, install_snapshot_updater
from version import APP_VERSION


def make_store(tmp_path, environ=None):
    return SettingsStore(path=tmp_path / "settings.json", environ=environ or {})


def test_snapshot_requests_use_versioned_violarr_user_agent(tmp_path, monkeypatch):
    metadata = {
        "tag_name": "db-2026-10-05",
        "assets": [
            {
                "name": "snapshot.dump",
                "browser_download_url": "https://example/snapshot.dump",
                "digest": f"sha256:{hashlib.sha256(b'dump').hexdigest()}",
            }
        ],
    }
    responses = [json.dumps(metadata).encode(), b"dump"]
    requests = []

    def urlopen(request, timeout):
        requests.append(request)
        return io.BytesIO(responses.pop(0))

    monkeypatch.setattr("snapshot_updater.urlopen", urlopen)
    monkeypatch.setattr("snapshot_updater.subprocess.run", lambda *args, **kwargs: None)
    updater = SnapshotUpdater(settings_store=make_store(tmp_path))

    snapshot = updater.get_latest_snapshot()
    path = updater.download_snapshot(snapshot)
    path.unlink()

    assert [request.get_header("User-agent") for request in requests] == [
        f"Violarr/{APP_VERSION}",
        f"Violarr/{APP_VERSION}",
    ]


def test_updater_reads_effective_store_settings_and_runtime_reconfigure(tmp_path):
    store = make_store(tmp_path)
    settings = store.load()
    settings["database_update"] = {"enabled": False, "interval_seconds": 3600}
    store.save(settings)
    updater = SnapshotUpdater(settings_store=store)
    wake = threading.Event()
    updater._signal_wake = wake.set

    assert updater.status()["enabled"] is False
    assert updater.interval == 3600

    settings["database_update"] = {"enabled": True, "interval_seconds": 120}
    store.save(settings)
    updater.reconfigure()

    assert updater.status()["enabled"] is True
    assert updater.interval == 120
    assert wake.is_set()


def test_updater_environment_precedence_remains_effective(tmp_path):
    store = make_store(
        tmp_path,
        {"DB_AUTO_UPDATE": "false", "DB_UPDATE_INTERVAL": "7200"},
    )
    updater = SnapshotUpdater(settings_store=store)

    assert updater.status()["enabled"] is False
    assert updater.interval == 7200
    assert store.load_persisted()["database_update"]["enabled"] is True


def test_update_status_transitions_and_failure_is_sanitized(tmp_path, monkeypatch):
    updater = SnapshotUpdater(settings_store=make_store(tmp_path))
    updater.state_file = tmp_path / "snapshot-version"
    monkeypatch.setattr(
        updater,
        "get_latest_snapshot",
        lambda: (_ for _ in ()).throw(RuntimeError("password=do-not-return")),
    )
    monkeypatch.setattr(updater, "_admin_connection", lambda: (_ for _ in ()).throw(OSError()))

    updater.update_once()
    status = updater.status()

    assert status["updating"] is False
    assert status["last_check"] is not None
    assert status["last_error"] == "Snapshot update failed"
    assert "do-not-return" not in json.dumps(status)


@pytest.mark.parametrize("manual", [False, True])
def test_success_updates_latest_and_installed_status_without_changing_switch(
    tmp_path, monkeypatch, manual
):
    updater = SnapshotUpdater(settings_store=make_store(tmp_path))
    updater.state_file = tmp_path / "snapshot-version"
    updater.write_local_version("db-2026-01-01")
    snapshot = Snapshot("db-2026-02-01", "snapshot.dump", "https://example/dump", "sha256:x")
    dump = tmp_path / "download.dump"
    dump.write_bytes(b"dump")
    calls = []
    monkeypatch.setattr(updater, "get_latest_snapshot", lambda: snapshot)
    monkeypatch.setattr(updater, "download_snapshot", lambda value: dump)
    monkeypatch.setattr(updater, "_create_candidate_database", lambda value: calls.append("create"))
    monkeypatch.setattr(updater, "_restore_dump", lambda *args: calls.append("restore"))
    monkeypatch.setattr(updater, "_validate_database", lambda value: calls.append("validate"))

    def switch(candidate, version):
        calls.append("switch")
        updater.write_local_version(version)

    monkeypatch.setattr(updater, "_switch_database", switch)

    if manual:

        async def scenario():
            assert await updater.trigger_manual()
            await updater.stop()

        asyncio.run(scenario())
    else:
        updater.update_once()
    status = updater.status()

    assert status["phase"] == "updated"
    assert calls == ["create", "restore", "validate", "switch"]
    assert status["installed_version"] == "db-2026-02-01"
    assert status["latest_version"] == "db-2026-02-01"
    assert status["last_error"] is None


def test_reconfigure_wakes_disabled_periodic_loop_without_concurrent_update(tmp_path, monkeypatch):
    store = make_store(tmp_path)
    settings = store.load()
    settings["database_update"]["enabled"] = False
    store.save(settings)
    updater = SnapshotUpdater(settings_store=store)
    updater.start_delay = 0
    calls = []
    monkeypatch.setattr(updater, "update_once", lambda **kwargs: calls.append("update"))

    async def scenario():
        await updater.start()
        await asyncio.sleep(0.01)
        assert calls == []
        settings["database_update"] = {"enabled": True, "interval_seconds": 60}
        store.save(settings)
        updater.reconfigure()
        await asyncio.sleep(0.05)
        await updater.stop()

    asyncio.run(scenario())
    assert calls == ["update"]


def test_status_has_next_check_only_when_enabled(tmp_path):
    updater = SnapshotUpdater(settings_store=make_store(tmp_path))
    updater._set_next_check(datetime(2026, 1, 1, tzinfo=timezone.utc))
    assert updater.status()["next_check"] == "2026-01-01T00:00:00+00:00"

    settings = updater.settings_store.load_persisted()
    settings["database_update"]["enabled"] = False
    updater.settings_store.save(settings)
    updater.reconfigure()
    assert updater.status()["next_check"] is None


def test_update_lock_prevents_concurrent_snapshot_checks(tmp_path, monkeypatch):
    updater = SnapshotUpdater(settings_store=make_store(tmp_path))
    calls = []
    monkeypatch.setattr(updater, "get_latest_snapshot", lambda: calls.append("network"))
    updater._update_lock.acquire()
    try:
        updater.update_once()
    finally:
        updater._update_lock.release()

    assert calls == []
    assert updater.status()["updating"] is False


def test_status_observes_maintenance_without_network_access(tmp_path, monkeypatch):
    updater = SnapshotUpdater(settings_store=make_store(tmp_path))
    monkeypatch.setattr(
        updater,
        "get_latest_snapshot",
        lambda: (_ for _ in ()).throw(AssertionError("network called")),
    )
    updater.maintenance.set()

    assert updater.status()["maintenance"] is True


def test_concurrent_reconfigure_and_stop_are_lifecycle_safe(tmp_path):
    store = make_store(tmp_path)
    settings = store.load()
    settings["database_update"]["enabled"] = False
    store.save(settings)
    updater = SnapshotUpdater(settings_store=store)
    updater.start_delay = 0
    signal_started = threading.Event()
    release_signal = threading.Event()
    errors = []

    class ClosingLoop:
        def call_soon_threadsafe(self, callback):
            signal_started.set()
            release_signal.wait()
            raise RuntimeError("event loop is closed")

    def reconfigure():
        try:
            updater.reconfigure()
        except Exception as exc:
            errors.append(exc)

    async def scenario():
        await updater.start()
        with updater._lifecycle_lock:
            updater._loop = ClosingLoop()
        worker = threading.Thread(target=reconfigure)
        worker.start()
        await asyncio.to_thread(signal_started.wait)
        stop_task = asyncio.create_task(updater.stop())
        release_signal.set()
        await stop_task
        await asyncio.to_thread(worker.join)

    asyncio.run(scenario())

    assert errors == []
    with updater._lifecycle_lock:
        assert updater._task is None
        assert updater._loop is None
        assert updater._wake_event is None


def test_maintenance_middleware_returns_stable_503(tmp_path):
    app = FastAPI()
    updater = install_snapshot_updater(app, make_store(tmp_path))

    @app.get("/probe")
    def probe():
        return {"ok": True}

    updater.maintenance.set()
    response = TestClient(app).get("/probe")

    assert response.status_code == 503
    assert response.json() == {"detail": "Database update in progress"}
    assert response.headers["Retry-After"] == "5"

    updater.maintenance.clear()
    assert TestClient(app).get("/probe").json() == {"ok": True}


def test_candidate_switch_rolls_back_after_new_database_validation_failure(tmp_path, monkeypatch):
    updater = SnapshotUpdater(settings_store=make_store(tmp_path))
    updater.db_name = "icv_db"
    calls = []

    class Connection:
        def __init__(self, phase):
            self.phase = phase

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    connections = iter([Connection("switch"), Connection("rollback")])
    monkeypatch.setattr(updater, "_admin_connection", lambda: next(connections))
    monkeypatch.setattr(
        updater,
        "_database_exists",
        lambda connection, name: (
            name == ("icv_db" if connection.phase == "switch" else "icv_db_previous")
        ),
    )
    monkeypatch.setattr(
        updater,
        "_drop_database",
        lambda connection, name: calls.append((connection.phase, "drop", name)),
    )
    monkeypatch.setattr(
        updater,
        "_rename_database",
        lambda connection, old, new: calls.append((connection.phase, "rename", old, new)),
    )
    monkeypatch.setattr(
        updater,
        "_validate_database",
        lambda name: (_ for _ in ()).throw(RuntimeError("invalid candidate")),
    )
    monkeypatch.setattr(
        updater,
        "write_local_version",
        lambda version: calls.append(("unexpected-version-write", version)),
    )

    with pytest.raises(RuntimeError, match="invalid candidate"):
        updater._switch_database("icv_db_candidate", "db-2026-02-01")

    assert calls == [
        ("switch", "drop", "icv_db_previous"),
        ("switch", "rename", "icv_db", "icv_db_previous"),
        ("switch", "rename", "icv_db_candidate", "icv_db"),
        ("rollback", "drop", "icv_db"),
        ("rollback", "rename", "icv_db_previous", "icv_db"),
    ]
    assert updater.maintenance.is_set() is False


def test_manual_admission_progress_reconfiguration_and_shutdown(tmp_path, monkeypatch):
    store = make_store(tmp_path)
    settings = store.load()
    settings["database_update"] = {"enabled": False, "interval_seconds": 60}
    store.save(settings)
    updater = SnapshotUpdater(store)
    updater.state_file = tmp_path / "version"
    updater.write_local_version("db-2026-02-01")
    entered, release = threading.Event(), threading.Event()
    calls = []

    def discover():
        calls.append("discovery")
        entered.set()
        assert release.wait(5)
        return Snapshot("db-2026-02-01", "x.dump", "unused", "sha256:x")

    monkeypatch.setattr(updater, "get_latest_snapshot", discover)

    async def scenario():
        await updater.start()
        assert await updater.trigger_manual()
        assert updater.status()["phase"] == "checking"
        assert not await updater.trigger_manual()
        await asyncio.to_thread(entered.wait, 5)
        updater.update_once()  # Disabled automatic checks do not start discovery.
        settings["database_update"] = {"enabled": True, "interval_seconds": 120}
        store.save(settings)
        updater.reconfigure()
        assert updater.status()["next_check"] is None
        assert not await updater.trigger_manual()
        stopping = asyncio.create_task(updater.stop())
        await asyncio.sleep(0.01)
        assert not stopping.done()
        release.set()
        await stopping
        status = updater.status()
        assert status["phase"] == "up_to_date"
        assert not status["updating"]
        assert datetime.fromisoformat(status["next_check"]) - datetime.fromisoformat(
            status["last_check"]
        ) == timedelta(seconds=120)
        assert not await updater.trigger_manual()

    asyncio.run(scenario())
    assert calls == ["discovery"]
    assert updater._update_lock.acquire(False)
    updater._update_lock.release()


def test_idle_interval_changes_do_not_trigger_immediate_checks(tmp_path, monkeypatch):
    store = make_store(tmp_path)
    updater = SnapshotUpdater(store)
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(updater, "_now", lambda: now)
    updater._set_next_check(now + timedelta(seconds=60))
    settings = store.load()
    settings["database_update"]["interval_seconds"] = 120
    store.save(settings)
    updater.reconfigure()
    assert updater._next_check == now + timedelta(seconds=120)
    updater.reconfigure()  # Unrelated saves preserve the existing deadline.
    assert updater._next_check == now + timedelta(seconds=120)
    settings["database_update"]["enabled"] = False
    store.save(settings)
    updater.reconfigure()
    assert updater._next_check is None
    settings["database_update"]["enabled"] = True
    store.save(settings)
    updater.reconfigure()
    assert updater._next_check == now


def test_startup_delay_and_idle_reconfiguration_do_not_run_discovery(tmp_path, monkeypatch):
    store = make_store(tmp_path)
    updater = SnapshotUpdater(store)
    updater.start_delay = 60
    calls = []
    monkeypatch.setattr(updater, "get_latest_snapshot", lambda: calls.append("unexpected"))

    async def scenario():
        await updater.start()
        before = updater._next_check
        await asyncio.sleep(0.01)
        assert calls == []
        settings = store.load()
        settings["database_update"]["interval_seconds"] = 120
        store.save(settings)
        updater.reconfigure()
        assert updater._next_check > before
        await asyncio.sleep(0.01)
        assert calls == []
        await updater.stop()

    asyncio.run(scenario())


def test_completion_publishes_idle_and_deadline_atomically_with_reconfiguration(
    tmp_path, monkeypatch
):
    updater = SnapshotUpdater(make_store(tmp_path))
    updater.state_file = tmp_path / "version"
    updater.write_local_version("same")
    monkeypatch.setattr(
        updater, "get_latest_snapshot", lambda: Snapshot("same", "x", "unused", "sha256:x")
    )

    class ReconfiguringLock:
        def __init__(self):
            self.lock = threading.Lock()
            self.observed = False

        def __enter__(self):
            self.lock.acquire()

        def __exit__(self, *args):
            self.lock.release()
            if updater._last_check is not None and not self.observed:
                self.observed = True
                # Reconfigure at the first observable completion boundary.
                updater.reconfigure()

    updater._state_lock = ReconfiguringLock()
    updater.update_once()
    assert updater._state_lock.observed
    assert updater._next_check == updater._last_check + timedelta(seconds=updater.interval)
    assert not updater.status()["updating"]


@pytest.mark.parametrize("change", ["interval", "disable"])
def test_scheduled_admission_revalidates_after_idle_reconfiguration(tmp_path, monkeypatch, change):
    store = make_store(tmp_path)
    updater = SnapshotUpdater(store)
    updater.start_delay = 0
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(updater, "_now", lambda: now)
    refreshed, release = threading.Event(), threading.Event()
    original_refresh = updater._refresh_configuration
    discoveries = []

    def refresh_then_pause():
        original_refresh()
        if threading.current_thread() is not threading.main_thread():
            refreshed.set()
            assert release.wait(5)

    monkeypatch.setattr(updater, "_refresh_configuration", refresh_then_pause)
    monkeypatch.setattr(updater, "get_latest_snapshot", lambda: discoveries.append("unexpected"))

    async def scenario():
        await updater.start()
        try:
            assert await asyncio.to_thread(refreshed.wait, 5)
            assert updater.status()["updating"] is False
            settings = store.load()
            settings["database_update"] = {
                "enabled": change != "disable",
                "interval_seconds": 604800,
            }
            store.save(settings)
            updater.reconfigure()
            expected = now + timedelta(seconds=604800) if change == "interval" else None
            assert updater._next_check == expected
        finally:
            release.set()
            # Shutdown waits for the dispatched attempt, even when it has not reserved yet.
            await updater.stop()
        assert updater._next_check == expected
        assert not updater.status()["updating"]
        assert updater._task is None

    asyncio.run(scenario())
    assert discoveries == []
    assert updater._update_lock.acquire(False)
    updater._update_lock.release()


def test_automatic_shutdown_waits_for_thread_and_latest_disabled_settings(tmp_path, monkeypatch):
    store = make_store(tmp_path)
    updater = SnapshotUpdater(store)
    updater.start_delay = 0
    updater.state_file = tmp_path / "version"
    updater.write_local_version("db-2026-02-01")
    entered, release = threading.Event(), threading.Event()

    def discover():
        entered.set()
        assert release.wait(5)
        return Snapshot("db-2026-02-01", "x.dump", "unused", "sha256:x")

    monkeypatch.setattr(updater, "get_latest_snapshot", discover)

    async def scenario():
        await updater.start()
        assert await asyncio.to_thread(entered.wait, 5)
        assert not await updater.trigger_manual()
        settings = store.load()
        settings["database_update"] = {"enabled": False, "interval_seconds": 604800}
        store.save(settings)
        updater.reconfigure()
        stopping = asyncio.create_task(updater.stop())
        await asyncio.sleep(0.01)
        assert not stopping.done()
        release.set()
        await stopping
        assert updater.status()["next_check"] is None
        assert updater.interval == 604800
        assert not updater.status()["updating"]

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["rename", "version"])
def test_switch_restores_previous_on_rename_or_version_write_failure(
    tmp_path, monkeypatch, failure
):
    from contextlib import nullcontext

    updater = SnapshotUpdater(make_store(tmp_path))
    databases = {updater.db_name, "candidate"}
    previous = f"{updater.db_name}_previous"
    monkeypatch.setattr(updater, "_admin_connection", lambda: nullcontext(None))
    monkeypatch.setattr(updater, "_database_exists", lambda connection, name: name in databases)
    monkeypatch.setattr(updater, "_drop_database", lambda connection, name: databases.discard(name))

    def rename(connection, old, new):
        if old == "candidate" and failure == "rename":
            raise RuntimeError("rename failed")
        databases.remove(old)
        databases.add(new)

    monkeypatch.setattr(updater, "_rename_database", rename)
    monkeypatch.setattr(updater, "_validate_database", lambda name: None)
    monkeypatch.setattr(
        updater, "write_local_version", lambda version: (_ for _ in ()).throw(OSError())
    )
    with pytest.raises((RuntimeError, OSError)):
        updater._switch_database("candidate", "version")
    assert updater.db_name in databases
    assert previous not in databases
    assert not updater.maintenance.is_set()


def test_download_cleanup_failure_still_releases_update_lock(tmp_path, monkeypatch):
    updater = SnapshotUpdater(make_store(tmp_path))
    snapshot = Snapshot("new", "x.dump", "unused", "sha256:x")
    phases = []
    monkeypatch.setattr(updater, "read_local_version", lambda: None)
    monkeypatch.setattr(updater, "get_latest_snapshot", lambda: snapshot)

    class Dump:
        def unlink(self, **kwargs):
            raise OSError("private path")

    monkeypatch.setattr(
        updater,
        "download_snapshot",
        lambda value: phases.append(updater.status()["phase"]) or Dump(),
    )
    monkeypatch.setattr(
        updater, "_create_candidate_database", lambda name: phases.append(updater.status()["phase"])
    )
    monkeypatch.setattr(updater, "_restore_dump", lambda *args: None)
    monkeypatch.setattr(
        updater, "_validate_database", lambda name: phases.append(updater.status()["phase"])
    )
    monkeypatch.setattr(
        updater, "_switch_database", lambda *args: phases.append(updater.status()["phase"])
    )
    updater.update_once()
    assert phases == ["downloading", "restoring", "validating", "switching"]
    assert updater.status()["phase"] == "updated"
    assert not updater.status()["updating"]
    assert updater._update_lock.acquire(False)
    updater._update_lock.release()
