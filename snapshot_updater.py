import asyncio
import hashlib
import json
import os
import subprocess
import tempfile
import threading
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.request import Request, urlopen

import psycopg
from fastapi import FastAPI
from fastapi import Request as FastAPIRequest
from fastapi.responses import JSONResponse
from psycopg import sql

from settings import SettingsStore
from version import APP_VERSION

LATEST_RELEASE_URL = os.getenv(
    "SNAPSHOT_LATEST_URL",
    "https://api.github.com/repos/xbit18/icvdb-snapshots/releases/latest",
)
SNAPSHOT_USER_AGENT = f"Violarr/{APP_VERSION}"


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _tag_date(tag: str) -> date | None:
    if not tag.startswith("db-"):
        return None

    try:
        return date.fromisoformat(tag[3:])
    except ValueError:
        return None


@dataclass(frozen=True)
class Snapshot:
    version: str
    name: str
    url: str
    digest: str


class SnapshotUpdater:
    def __init__(self, settings_store: SettingsStore | None = None) -> None:
        self.settings_store = settings_store or SettingsStore()
        update_settings = self.settings_store.load()["database_update"]
        self.enabled = update_settings["enabled"]
        self.interval = update_settings["interval_seconds"]
        self.start_delay = int(os.getenv("DB_UPDATE_START_DELAY", "60"))

        self.db_host = os.getenv("DB_HOST", "127.0.0.1")
        self.db_port = int(os.getenv("DB_PORT", "5432"))
        self.db_name = os.getenv("DB_NAME", "icv_db")
        self.db_user = os.getenv("DB_USER", "icv")
        self.db_password = os.getenv("DB_PASSWORD", "icv_internal")
        self.admin_db = os.getenv("DB_ADMIN_DB", "postgres")

        self.state_file = Path(os.getenv("SNAPSHOT_STATE_FILE", "/data/state/snapshot-version"))

        self.maintenance = threading.Event()
        self._update_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._lifecycle_lock = threading.Lock()
        self._task: asyncio.Task | None = None
        self._manual_task: asyncio.Task | None = None
        self._stopping = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._wake_event: asyncio.Event | None = None
        self._latest_version: str | None = None
        self._updating = False
        self._last_check: datetime | None = None
        self._next_check: datetime | None = None
        self._last_error: str | None = None
        self._phase = "idle"

    def _log(self, message: str) -> None:
        print(f"[snapshot-updater] {message}", flush=True)

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    def _set_next_check(self, value: datetime | None) -> None:
        with self._state_lock:
            self._next_check = value

    def _refresh_configuration(self) -> None:
        with self._state_lock:
            settings = self.settings_store.load()["database_update"]
            self.enabled = settings["enabled"]
            self.interval = settings["interval_seconds"]
            if not self.enabled:
                self._next_check = None

    def _signal_wake(self) -> None:
        with self._lifecycle_lock:
            loop = self._loop
            wake_event = self._wake_event
            if loop is None or wake_event is None:
                return
            try:
                loop.call_soon_threadsafe(wake_event.set)
            except RuntimeError:
                # Shutdown may close the event loop after it was published.
                return

    def reconfigure(self) -> None:
        with self._state_lock:
            settings = self.settings_store.load()["database_update"]
            was_enabled, interval = self.enabled, self.interval
            self.enabled = settings["enabled"]
            self.interval = settings["interval_seconds"]
            if not self.enabled or self._updating:
                self._next_check = None
            elif not was_enabled:
                self._next_check = self._now()
            elif interval != self.interval:
                self._next_check = self._now() + timedelta(seconds=self.interval)
        self._signal_wake()

    def status(self) -> dict:
        try:
            installed = self.read_local_version()
        except OSError:
            installed = None
        with self._state_lock:
            return {
                "installed_version": installed,
                "latest_version": self._latest_version,
                "enabled": self.enabled,
                "updating": self._updating,
                "phase": self._phase,
                "maintenance": self.maintenance.is_set(),
                "last_check": self._iso(self._last_check),
                "next_check": self._iso(self._next_check) if self.enabled else None,
                "last_error": self._last_error,
            }

    @staticmethod
    def _iso(value: datetime | None) -> str | None:
        return value.isoformat() if value is not None else None

    def _connection_kwargs(self, dbname: str) -> dict:
        return {
            "host": self.db_host,
            "port": self.db_port,
            "dbname": dbname,
            "user": self.db_user,
            "password": self.db_password,
            "connect_timeout": 10,
        }

    # ------------------------------------------------------------------
    # GitHub release
    # ------------------------------------------------------------------

    def get_latest_snapshot(self) -> Snapshot:
        request = Request(
            LATEST_RELEASE_URL,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": SNAPSHOT_USER_AGENT,
            },
        )

        with urlopen(request, timeout=30) as response:
            release = json.load(response)

        dump_assets = [
            asset for asset in release.get("assets", []) if asset.get("name", "").endswith(".dump")
        ]

        if not dump_assets:
            raise RuntimeError("La latest release non contiene alcun asset .dump")

        asset = dump_assets[0]
        digest = asset.get("digest")

        if not digest or not digest.startswith("sha256:"):
            raise RuntimeError("L'asset dello snapshot non espone un digest SHA256 valido")

        return Snapshot(
            version=release["tag_name"],
            name=asset["name"],
            url=asset["browser_download_url"],
            digest=digest,
        )

    # ------------------------------------------------------------------
    # Stato locale
    # ------------------------------------------------------------------

    def read_local_version(self) -> str | None:
        try:
            version = self.state_file.read_text(encoding="utf-8").strip()
            return version or None
        except FileNotFoundError:
            return None

    def write_local_version(self, version: str) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)

        temporary = self.state_file.with_suffix(".tmp")
        temporary.write_text(f"{version}\n", encoding="utf-8")

        os.replace(temporary, self.state_file)

    def _needs_update(self, local: str | None, remote: str) -> bool:
        if local is None:
            return True

        if local == remote:
            return False

        local_date = _tag_date(local)
        remote_date = _tag_date(remote)

        if local_date is not None and remote_date is not None:
            return remote_date > local_date

        # Se i tag non rispettano il formato atteso, per sicurezza
        # consideriamo diversa la release e proviamo ad aggiornarla.
        return True

    # ------------------------------------------------------------------
    # Download e verifica
    # ------------------------------------------------------------------

    def download_snapshot(self, snapshot: Snapshot) -> Path:
        fd, raw_path = tempfile.mkstemp(
            prefix="icvdb-",
            suffix=".dump",
        )
        os.close(fd)

        path = Path(raw_path)
        hasher = hashlib.sha256()

        request = Request(
            snapshot.url,
            headers={"User-Agent": SNAPSHOT_USER_AGENT},
        )

        try:
            self._log(f"Download di {snapshot.name}...")

            with urlopen(request, timeout=60) as response, path.open("wb") as output:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break

                    output.write(chunk)
                    hasher.update(chunk)

            actual_digest = f"sha256:{hasher.hexdigest()}"

            if actual_digest != snapshot.digest:
                raise RuntimeError(
                    "Checksum SHA256 non valido: "
                    f"atteso {snapshot.digest}, ottenuto {actual_digest}"
                )

            self._log("SHA256 verificato.")

            self._set_phase("validating")
            subprocess.run(
                ["pg_restore", "--list", str(path)],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
            )

            self._log("Dump PostgreSQL valido.")

            return path

        except Exception:
            path.unlink(missing_ok=True)
            raise

    # ------------------------------------------------------------------
    # PostgreSQL helpers
    # ------------------------------------------------------------------

    def _admin_connection(self):
        return psycopg.connect(
            **self._connection_kwargs(self.admin_db),
            autocommit=True,
        )

    def _database_exists(self, connection, name: str) -> bool:
        row = connection.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s",
            (name,),
        ).fetchone()

        return row is not None

    def _terminate_connections(self, connection, name: str) -> None:
        connection.execute(
            """
            SELECT pg_terminate_backend(pid)
            FROM pg_stat_activity
            WHERE datname = %s
              AND pid <> pg_backend_pid()
            """,
            (name,),
        )

    def _drop_database(self, connection, name: str) -> None:
        if not self._database_exists(connection, name):
            return

        self._terminate_connections(connection, name)

        connection.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))

    def _rename_database(
        self,
        connection,
        old_name: str,
        new_name: str,
    ) -> None:
        self._terminate_connections(connection, old_name)

        connection.execute(
            sql.SQL("ALTER DATABASE {} RENAME TO {}").format(
                sql.Identifier(old_name),
                sql.Identifier(new_name),
            )
        )

    def _create_candidate_database(self, candidate: str) -> None:
        with self._admin_connection() as connection:
            self._drop_database(connection, candidate)

            connection.execute(
                sql.SQL("CREATE DATABASE {} OWNER {}").format(
                    sql.Identifier(candidate),
                    sql.Identifier(self.db_user),
                )
            )

    def _restore_dump(
        self,
        dump_path: Path,
        candidate: str,
    ) -> None:
        environment = os.environ.copy()
        environment["PGPASSWORD"] = self.db_password

        self._log(f"Restore nel database temporaneo {candidate}...")

        subprocess.run(
            [
                "pg_restore",
                "--host",
                self.db_host,
                "--port",
                str(self.db_port),
                "--username",
                self.db_user,
                "--dbname",
                candidate,
                "--no-owner",
                "--no-privileges",
                "--exit-on-error",
                str(dump_path),
            ],
            env=environment,
            check=True,
        )

    def _validate_database(self, name: str) -> None:
        with psycopg.connect(**self._connection_kwargs(name)) as connection:
            connection.execute("SELECT 1").fetchone()

            table_count = connection.execute(
                """
                SELECT count(*)
                FROM pg_catalog.pg_class c
                JOIN pg_catalog.pg_namespace n
                  ON n.oid = c.relnamespace
                WHERE c.relkind IN ('r', 'p')
                  AND n.nspname NOT IN (
                      'pg_catalog',
                      'information_schema'
                  )
                  AND n.nspname NOT LIKE 'pg_toast%%'
                """
            ).fetchone()[0]

            if table_count == 0:
                raise RuntimeError(f"Il database {name} non contiene tabelle utente")

        self._log(f"Database {name} validato ({table_count} tabelle utente).")

    # ------------------------------------------------------------------
    # Switch sicuro
    # ------------------------------------------------------------------

    def _switch_database(
        self,
        candidate: str,
        snapshot_version: str,
    ) -> None:
        previous = f"{self.db_name}_previous"

        self.maintenance.set()

        try:
            self._log("Avvio switch del database...")

            with self._admin_connection() as connection:
                self._drop_database(connection, previous)

                if self._database_exists(connection, self.db_name):
                    self._rename_database(
                        connection,
                        self.db_name,
                        previous,
                    )

                try:
                    self._rename_database(connection, candidate, self.db_name)
                except Exception:
                    if self._database_exists(connection, previous):
                        self._rename_database(connection, previous, self.db_name)
                    raise

            try:
                self._validate_database(self.db_name)
                self.write_local_version(snapshot_version)

            except Exception:
                self._log("Validazione del nuovo database fallita. Avvio rollback...")

                with self._admin_connection() as connection:
                    self._drop_database(
                        connection,
                        self.db_name,
                    )

                    if self._database_exists(connection, previous):
                        self._rename_database(
                            connection,
                            previous,
                            self.db_name,
                        )

                raise

            with self._admin_connection() as connection:
                self._drop_database(connection, previous)

            self._log(f"Database aggiornato con successo a {snapshot_version}.")

        finally:
            self.maintenance.clear()

    # ------------------------------------------------------------------
    # Ciclo di aggiornamento
    # ------------------------------------------------------------------

    def _set_phase(self, phase: str) -> None:
        with self._state_lock:
            self._phase = phase

    def _reserve(self, *, scheduled: bool = False) -> bool:
        with self._state_lock:
            # Reconfiguration and scheduled admission share one atomic boundary.
            if scheduled and (
                not self.enabled or self._next_check is None or self._next_check > self._now()
            ):
                return False
            # Never wait for the update lock while holding state ownership.
            if not self._update_lock.acquire(blocking=False):
                return False
            self._updating = True
            self._phase = "checking"
            self._last_error = None
            self._next_check = None
        return True

    async def trigger_manual(self) -> bool:
        # Admission happens before acknowledgment, on the application event loop.
        if self._stopping or not self._reserve():
            return False
        self._manual_task = asyncio.create_task(self._run_worker(self._update_reserved))
        return True

    async def _run_worker(self, operation) -> None:
        worker = asyncio.create_task(asyncio.to_thread(operation))
        try:
            await asyncio.shield(worker)
        except asyncio.CancelledError:
            # Cancelling to_thread does not stop PostgreSQL work. Own it until it finishes.
            await worker
            raise

    def update_once(self, *, scheduled: bool = False) -> None:
        self._refresh_configuration()
        if not self.enabled:
            return

        if not self._reserve(scheduled=scheduled):
            self._log("Un aggiornamento è già in corso.")
            return

        self._update_reserved()

    def _update_reserved(self) -> None:
        dump_path: Path | None = None
        candidate = f"{self.db_name}_candidate"

        try:
            latest = self.get_latest_snapshot()
            local = self.read_local_version()
            with self._state_lock:
                self._latest_version = latest.version
                self._last_error = None

            self._log(f"Versione locale: {local or 'non registrata'}")
            self._log(f"Latest disponibile: {latest.version}")

            if not self._needs_update(local, latest.version):
                self._log("Database già aggiornato.")
                self._set_phase("up_to_date")
                return

            self._log(f"Nuovo snapshot disponibile: {latest.version}")

            self._set_phase("downloading")
            dump_path = self.download_snapshot(latest)

            self._set_phase("restoring")
            self._create_candidate_database(candidate)
            self._restore_dump(dump_path, candidate)
            self._set_phase("validating")
            self._validate_database(candidate)

            self._set_phase("switching")
            self._switch_database(
                candidate,
                latest.version,
            )
            self._set_phase("updated")

        except Exception as exc:
            self._log(f"Snapshot update failed ({type(exc).__name__}).")
            with self._state_lock:
                self._last_error = "Snapshot update failed"
                self._phase = "failed"

            # Se il candidate esiste ancora, viene rimosso.
            try:
                with self._admin_connection() as connection:
                    self._drop_database(
                        connection,
                        candidate,
                    )
            except Exception as cleanup_error:
                self._log(
                    f"Unable to remove the temporary database ({type(cleanup_error).__name__})."
                )

        finally:
            try:
                if dump_path is not None:
                    dump_path.unlink(missing_ok=True)
            except OSError:
                self._log("Unable to remove the downloaded snapshot.")
            finally:
                try:
                    with self._state_lock:
                        try:
                            settings = self.settings_store.load()["database_update"]
                            self.enabled = settings["enabled"]
                            self.interval = settings["interval_seconds"]
                            self._last_check = self._now()
                            self._next_check = (
                                self._last_check + timedelta(seconds=self.interval)
                                if self.enabled
                                else None
                            )
                        finally:
                            # Publish idle and its deadline atomically with reconfiguration.
                            self._updating = False
                finally:
                    self._update_lock.release()
                    self._signal_wake()

    async def _update_loop(self, wake_event: asyncio.Event) -> None:
        while True:
            wake_event.clear()
            with self._state_lock:
                deadline = self._next_check if self.enabled and not self._updating else None
            delay = max(0, (deadline - self._now()).total_seconds()) if deadline else None
            if delay == 0:
                await self._run_worker(lambda: self.update_once(scheduled=True))
                # Avoid spinning if an attempt returns without changing the deadline.
                with self._state_lock:
                    if self._next_check == deadline:
                        self._next_check = self._now() + timedelta(seconds=self.interval)
                continue
            try:
                await asyncio.wait_for(wake_event.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass

    async def start(self) -> None:
        with self._lifecycle_lock:
            if self._task is not None:
                return
            self._stopping = False
            self._loop = asyncio.get_running_loop()
            self._wake_event = asyncio.Event()
            wake_event = self._wake_event
            self._task = asyncio.create_task(self._update_loop(wake_event))
        self._refresh_configuration()
        if self.enabled:
            self._set_next_check(self._now() + timedelta(seconds=max(self.start_delay, 0)))
            self._log(f"Automatic updates enabled (interval: {self.interval}s).")
        else:
            self._log("Automatic updates disabled.")

    async def stop(self) -> None:
        self._stopping = True
        with self._lifecycle_lock:
            task = self._task
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        if self._manual_task is not None:
            await self._manual_task
            self._manual_task = None

        with self._lifecycle_lock:
            if self._task is task:
                self._task = None
                self._wake_event = None
                self._loop = None


def install_snapshot_updater(
    app: FastAPI,
    settings_store: SettingsStore | None = None,
) -> SnapshotUpdater:
    updater = SnapshotUpdater(settings_store=settings_store)

    @app.middleware("http")
    async def snapshot_update_middleware(
        request: FastAPIRequest,
        call_next,
    ):
        if updater.maintenance.is_set():
            return JSONResponse(
                status_code=503,
                content={"detail": "Database update in progress"},
                headers={"Retry-After": "5"},
            )

        return await call_next(request)

    @app.on_event("startup")
    async def start_snapshot_updater() -> None:
        await updater.start()

    @app.on_event("shutdown")
    async def stop_snapshot_updater() -> None:
        await updater.stop()

    app.state.snapshot_updater = updater

    return updater
