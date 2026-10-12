"""Independent, cache-only application release notification (never an installer)."""

import asyncio
import json
import re
from dataclasses import asdict, dataclass
from urllib.request import Request, urlopen

from fastapi.responses import JSONResponse

LATEST_RELEASE_URL = "https://api.github.com/repos/xbit18/violarr/releases/latest"
MAX_RESPONSE_BYTES = 256 * 1024
HTTP_TIMEOUT = 10
_STABLE = re.compile(
    r"v?(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
)


def stable_version(value):
    if not isinstance(value, str) or len(value) > 200:
        return None
    match = _STABLE.fullmatch(value)
    return tuple(int(part) for part in match.groups()) if match else None


@dataclass(frozen=True)
class UpdateState:
    update_available: bool = False
    latest_version: str | None = None
    release_url: str | None = None

    def public(self):
        return asdict(self)


HIDDEN = UpdateState()


class ApplicationUpdateChecker:
    def __init__(self, installed, *, opener=None, interval=3600):
        self.installed = stable_version(installed)
        if isinstance(installed, str) and re.search(
            r"\b(dev|development|dirty|snapshot)\b", installed.partition("+")[2], re.I
        ):
            self.installed = None
        self.interval = interval
        self._opener = opener or urlopen
        self._state = HIDDEN
        self._task = None
        self._stop = None

    def snapshot(self):
        # One immutable reference is replaced only after complete validation.
        return self._state

    def check_once(self):
        if self.installed is None:
            return
        try:
            request = Request(
                LATEST_RELEASE_URL,
                headers={"Accept": "application/vnd.github+json", "User-Agent": "Violarr"},
            )
            with self._opener(request, timeout=HTTP_TIMEOUT) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                return
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                return
            if payload.get("draft") is not False or payload.get("prerelease") is not False:
                return
            tag = payload.get("tag_name")
            latest = stable_version(tag)
            if latest is None:
                return
            url = f"https://github.com/xbit18/violarr/releases/tag/{tag}"
            if payload.get("html_url") != url:
                return
            self._state = (
                UpdateState(True, tag.removeprefix("v"), url) if latest > self.installed else HIDDEN
            )
        except Exception:
            # Optional notification: preserve the last validated state, expose no errors.
            return

    async def _run(self):
        while not self._stop.is_set():
            # Never cancel an in-flight thread and launch a replacement. Shutdown
            # waits for this bounded HTTP worker rather than orphaning it.
            await asyncio.to_thread(self.check_once)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval)
            except TimeoutError:
                pass

    async def start(self):
        if self._task is None:
            self._stop = asyncio.Event()
            self._task = asyncio.create_task(self._run())

    async def stop(self):
        if self._task is not None:
            self._stop.set()
            await asyncio.shield(self._task)
            self._task = None


def install_application_update(app, installed):
    checker = ApplicationUpdateChecker(installed)
    app.state.application_update = checker
    app.router.add_event_handler("startup", checker.start)
    app.router.add_event_handler("shutdown", checker.stop)

    @app.middleware("http")
    async def cached_update_during_maintenance(request, call_next):
        # Registered after snapshot middleware: this DB-independent read must
        # remain available even while snapshot maintenance blocks other routes.
        if request.method == "GET" and request.url.path == "/webapi/application-update":
            return JSONResponse(checker.snapshot().public())
        return await call_next(request)

    return checker
