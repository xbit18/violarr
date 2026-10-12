import asyncio
import json
import threading
from io import BytesIO
from urllib.error import HTTPError, URLError

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from application_update import (
    HIDDEN,
    HTTP_TIMEOUT,
    MAX_RESPONSE_BYTES,
    ApplicationUpdateChecker,
    install_application_update,
    stable_version,
)
from webapi import create_webapi_router


def release(tag="v1.10.0", **changes):
    return {
        "tag_name": tag,
        "html_url": f"https://github.com/xbit18/violarr/releases/tag/{tag}",
        "draft": False,
        "prerelease": False,
        **changes,
    }


class Response(BytesIO):
    def __init__(self, payload, length=None):
        super().__init__(payload)
        self.headers = {} if length is None else {"Content-Length": str(length)}
        self.read_sizes = []

    def read(self, size=-1):
        self.read_sizes.append(size)
        return super().read(size)


def checker(payload=None, installed="1.9.0", **kwargs):
    return ApplicationUpdateChecker(
        installed,
        opener=lambda *a, **k: Response(json.dumps(payload or release()).encode()),
        **kwargs,
    )


@pytest.mark.parametrize("value", ["1.2.3", "v1.2.3", "1.2.3+build.007-x"])
def test_stable_metadata(value):
    assert stable_version(value) == (1, 2, 3)


@pytest.mark.parametrize(
    "value",
    [
        None,
        123,
        "unknown",
        "dev",
        "1.2",
        "01.2.3",
        "1.2.3-rc.1",
        "1.2.3+",
        "1.2.3+a..b",
        " 1.2.3",
        "1.2.3\n",
        "1.2.3+dev.sha123",
        "1.2.3+development",
        "1.2.3+dirty",
    ],
)
def test_invalid_installed_skips_upstream(value):
    def forbidden(*args, **kwargs):
        pytest.fail("invalid installed version contacted upstream")

    instance = ApplicationUpdateChecker(value, opener=forbidden)
    instance.check_once()
    assert instance.snapshot() == HIDDEN


@pytest.mark.parametrize(
    "installed,remote,available",
    [
        ("1.9.0", "v1.10.0", True),
        ("1.1.5", "1.1.6", True),
        ("1.9.0", "2.0.0", True),
        ("2.0.0", "1.99.0", False),
        ("1.2.3+a", "v1.2.3+b", False),
    ],
)
def test_numeric_comparison(installed, remote, available):
    instance = checker(release(remote), installed)
    instance.check_once()
    result = instance.snapshot()
    assert result.update_available is available
    assert result.latest_version == (remote.removeprefix("v") if available else None)
    assert result.release_url == (release(remote)["html_url"] if available else None)


@pytest.mark.parametrize(
    "changes",
    [
        {"draft": True},
        {"prerelease": True},
        {"draft": 0},
        {"prerelease": "false"},
        {"tag_name": "v1.10.0-rc.1"},
        {"tag_name": "01.10.0"},
        {"html_url": None},
        *[
            {"html_url": url}
            for url in [
                "http://github.com/xbit18/violarr/releases/tag/v1.10.0",
                "https://evil.test/xbit18/violarr/releases/tag/v1.10.0",
                "https://user@github.com/xbit18/violarr/releases/tag/v1.10.0",
                "https://github.com/xbit18/other/releases/tag/v1.10.0",
                "https://github.com/xbit18/violarr/releases/tag/v1.10.0?x=1",
                "https://github.com/xbit18/violarr/releases/tag/v1.10.0#x",
                "https://github.com/xbit18/violarr/releases/tag/v1.11.0",
            ]
        ],
    ],
)
def test_invalid_release_preserves_cache(changes):
    instance = checker()
    instance.check_once()
    previous = instance.snapshot()
    instance._opener = lambda *a, **k: Response(json.dumps(release(**changes)).encode())
    instance.check_once()
    assert instance.snapshot() == previous


@pytest.mark.parametrize(
    "payload", [b"{", b"null", b"[]", b"\xff", b"x" * (MAX_RESPONSE_BYTES + 1)]
)
def test_bounded_invalid_response(payload):
    instance = ApplicationUpdateChecker("1.0.0", opener=lambda *a, **k: Response(payload))
    instance.check_once()
    assert instance.snapshot() == HIDDEN


@pytest.mark.parametrize(
    "error",
    [
        TimeoutError(),
        URLError("offline"),
        HTTPError("url", 403, "rate limit", {}, None),
        HTTPError("url", 429, "rate limit", {}, None),
    ],
)
def test_transport_errors_preserve_cache(error):
    instance = checker()
    instance.check_once()
    previous = instance.snapshot()

    def fail(*args, **kwargs):
        raise error

    instance._opener = fail
    instance.check_once()
    assert instance.snapshot() == previous


def test_transport_timeout_headers_and_size_bound():
    response = Response(json.dumps(release()).encode(), MAX_RESPONSE_BYTES + 1)
    requests = []

    def open_response(request, timeout):
        requests.append((request, timeout))
        return response

    instance = ApplicationUpdateChecker("1.0.0", opener=open_response)
    instance.check_once()
    request, timeout = requests[0]
    assert timeout == HTTP_TIMEOUT == 10
    assert request.full_url == "https://api.github.com/repos/xbit18/violarr/releases/latest"
    assert request.get_header("User-agent") == "Violarr"
    assert request.get_header("Accept") == "application/vnd.github+json"
    assert response.read_sizes == [MAX_RESPONSE_BYTES + 1]
    assert response.closed
    assert instance.snapshot().public() == {
        "update_available": True,
        "latest_version": "1.10.0",
        "release_url": release()["html_url"],
    }


@pytest.mark.parametrize("length", [None, 1, MAX_RESPONSE_BYTES * 2])
def test_oversized_body_preserves_cache_regardless_of_content_length(length):
    instance = checker()
    instance.check_once()
    previous = instance.snapshot()
    payload = json.dumps(release("v2.0.0")).encode() + b" " * MAX_RESPONSE_BYTES
    response = Response(payload, length)
    instance._opener = lambda *a, **k: response

    instance.check_once()

    assert response.read_sizes == [MAX_RESPONSE_BYTES + 1]
    assert response.closed
    assert instance.snapshot() is previous


def test_nonblocking_lifecycle_cadence_and_shutdown():
    async def scenario():
        entered = threading.Event()
        release_worker = threading.Event()
        calls = []

        def opener(*args, **kwargs):
            calls.append(1)
            entered.set()
            assert release_worker.wait(2)
            return Response(json.dumps(release()).encode())

        instance = ApplicationUpdateChecker("1.0.0", opener=opener, interval=0.01)
        await instance.start()
        assert instance._task is not None
        for _ in range(100):
            if entered.is_set():
                break
            await asyncio.sleep(0.001)
        assert entered.is_set()
        assert instance.snapshot() == HIDDEN
        assert instance.interval == 0.01
        release_worker.set()
        for _ in range(100):
            if len(calls) >= 2:
                break
            await asyncio.sleep(0.001)
        assert len(calls) >= 2
        await instance.stop()
        count = len(calls)
        await asyncio.sleep(0.02)
        assert len(calls) == count
        assert instance._task is None
        assert ApplicationUpdateChecker("1.0.0").interval == 3600

    asyncio.run(scenario())


def test_cache_only_api_without_database_or_checker():
    app = FastAPI()
    app.include_router(create_webapi_router())
    client = TestClient(app)
    assert client.get("/webapi/application-update").json() == {
        "update_available": False,
        "latest_version": None,
        "release_url": None,
    }
    instance = checker()
    instance.check_once()
    app.state.application_update = instance
    instance._opener = lambda *a, **k: pytest.fail("API called upstream")
    for _ in range(2):
        assert client.get("/webapi/application-update").json()["update_available"] is True


def test_independent_hooks_preserve_existing_hooks():
    app = FastAPI()

    def existing():
        pass

    app.router.add_event_handler("startup", existing)
    instance = install_application_update(app, "unknown")
    assert app.router.on_startup[0] is existing
    assert app.state.application_update is instance
    with TestClient(app):
        assert instance._task is not None
    assert instance._task is None


def test_cache_read_bypasses_snapshot_maintenance():
    from fastapi.responses import JSONResponse

    app = FastAPI()

    @app.middleware("http")
    async def maintenance(request, call_next):
        return JSONResponse({"detail": "maintenance"}, status_code=503)

    install_application_update(app, "unknown")
    app.include_router(create_webapi_router())
    with TestClient(app) as client:
        assert client.get("/webapi/application-update").status_code == 200
        assert client.get("/webapi/status").status_code == 503


def test_body_read_timeout_preserves_cache():
    instance = checker()
    instance.check_once()
    previous = instance.snapshot()

    class TimeoutResponse(Response):
        def read(self, size=-1):
            self.read_sizes.append(size)
            raise TimeoutError("response body read timed out")

    response = TimeoutResponse(json.dumps(release("v2.0.0")).encode())
    instance._opener = lambda *a, **k: response
    instance.check_once()
    assert response.read_sizes == [MAX_RESPONSE_BYTES + 1]
    assert response.closed
    assert instance.snapshot() is previous


def test_shutdown_waits_for_owned_worker_without_overlap():
    async def scenario():
        entered = threading.Event()
        unblock = threading.Event()
        calls = []

        def opener(*args, **kwargs):
            calls.append(1)
            entered.set()
            assert unblock.wait(2)
            return Response(json.dumps(release()).encode())

        instance = ApplicationUpdateChecker("1.0.0", opener=opener)
        await instance.start()
        task = instance._task
        await instance.start()
        assert instance._task is task
        for _ in range(100):
            if entered.is_set():
                break
            await asyncio.sleep(0.001)
        assert entered.is_set()
        shutdown = asyncio.create_task(instance.stop())
        await asyncio.sleep(0)
        assert not shutdown.done()
        unblock.set()
        await shutdown
        assert calls == [1]
        assert instance._task is None

    asyncio.run(scenario())
