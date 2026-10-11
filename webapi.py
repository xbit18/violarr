import json
import threading
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute

from diagnostic_models import (
    MonitoringInput,
    MonitoringStatus,
    RequestHistory,
    SearchInput,
    SearchReport,
)
from prowlarr import ProwlarrClient, ProwlarrError
from settings import SettingsError

WEBAPI_VERSION = 1
_UNCHANGED = object()


class _SafeDiagnosticsRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def handle(request):
            try:
                if self.path.startswith("/webapi/diagnostics/") and self.body_field is not None:
                    content_type = (
                        request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                    )
                    if (
                        not content_type
                        or content_type == "application/json"
                        or (
                            content_type.startswith("application/")
                            and content_type.endswith("+json")
                        )
                    ) and await request.body():
                        # Parse before FastAPI converts decoding failures to generic HTTP 400.
                        # Catch only JSON body errors, never errors raised by the endpoint.
                        try:
                            await request.json()
                        except (UnicodeDecodeError, json.JSONDecodeError):
                            raise RequestValidationError([]) from None
                return await original(request)
            except RequestValidationError:
                if not self.path.startswith("/webapi/diagnostics/"):
                    raise
                # Default validation details echo rejected input, including secrets.
                raise HTTPException(
                    status_code=422,
                    detail={
                        "code": "invalid_parameters",
                        "message": "Invalid diagnostic parameters",
                    },
                ) from None

        return handle


class _ProwlarrStateCache:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._connected: bool | None = None
        self._indexer_installed: bool | None = None
        self._error: str | None = None

    def snapshot(self, configured: bool) -> dict[str, Any]:
        with self._lock:
            return {
                "configured": configured,
                "connected": self._connected if configured else None,
                "indexer_installed": (self._indexer_installed if configured else None),
                "error": self._error if configured else None,
            }

    def reset(self) -> None:
        with self._lock:
            self._connected = None
            self._indexer_installed = None
            self._error = None

    def update(
        self,
        *,
        connected: bool | None | object = _UNCHANGED,
        indexer_installed: bool | None | object = _UNCHANGED,
        error: str | None | object = _UNCHANGED,
    ) -> None:
        with self._lock:
            if connected is not _UNCHANGED:
                self._connected = connected
            if indexer_installed is not _UNCHANGED:
                self._indexer_installed = indexer_installed
            if error is not _UNCHANGED:
                self._error = error


def _store(request: Request):
    return request.app.state.settings_store


def _prowlarr_settings(request: Request) -> dict[str, Any]:
    return _store(request).load()["prowlarr"]


def _prowlarr_client(request: Request, *, require_indexer: bool = False):
    settings = _prowlarr_settings(request)
    required = [settings["url"], settings["api_key"]]
    if require_indexer:
        required.append(settings["indexer_url"])
    if not all(required):
        raise HTTPException(status_code=400, detail="Prowlarr is not configured")
    factory = getattr(request.app.state, "prowlarr_factory", ProwlarrClient)
    return (
        factory(settings)
        if factory is not ProwlarrClient
        else factory(settings["url"], settings["api_key"], settings["indexer_url"])
    )


def _prowlarr_configured(request: Request) -> bool:
    settings = _prowlarr_settings(request)
    return bool(settings["url"] and settings["api_key"])


def _generic_prowlarr_detail(stage: str = "request") -> dict[str, Any]:
    return {
        "code": "prowlarr_request_failed",
        "message": "Prowlarr request failed",
        "stage": stage,
    }


def _safe_prowlarr_detail(request: Request, error: ProwlarrError) -> dict[str, Any]:
    detail = error.as_detail()
    serialized = json.dumps(detail, ensure_ascii=False)
    settings = _prowlarr_settings(request)
    api_key = settings["api_key"]

    if (api_key and api_key in serialized) or "traceback" in serialized.casefold():
        return _generic_prowlarr_detail(error.stage)

    return detail


def _live_prowlarr_status(request: Request) -> dict[str, Any]:
    if not _prowlarr_configured(request):
        return {
            "configured": False,
            "connected": None,
            "indexer_installed": None,
            "error": None,
        }
    try:
        status = _prowlarr_client(request).status()
        error = status.get("error")
        if error:
            settings = _prowlarr_settings(request)
            if settings["api_key"] in str(error) or "traceback" in str(error).casefold():
                error = "Prowlarr request failed"
        return {
            "configured": True,
            "connected": status.get("connected"),
            "indexer_installed": status.get("indexer_installed"),
            "error": error,
        }
    except ProwlarrError:
        return {
            "configured": True,
            "connected": False,
            "indexer_installed": None,
            "error": "Prowlarr request failed",
        }


def create_webapi_router() -> APIRouter:
    router = APIRouter(prefix="/webapi", tags=["webapi"], route_class=_SafeDiagnosticsRoute)
    prowlarr_cache = _ProwlarrStateCache()

    @router.post("/diagnostics/search", response_model=SearchReport)
    def diagnostic_search(request: Request, parameters: SearchInput):
        return request.app.state.diagnostic_search(parameters.model_dump())

    @router.get("/diagnostics/monitoring", response_model=MonitoringStatus)
    def monitoring_status(request: Request):
        return request.app.state.search_monitor.status()

    @router.put("/diagnostics/monitoring", response_model=MonitoringStatus)
    def configure_monitoring(request: Request, parameters: MonitoringInput):
        return request.app.state.search_monitor.configure(parameters.enabled)

    @router.get("/diagnostics/requests", response_model=RequestHistory)
    def monitored_requests(request: Request):
        return {"requests": request.app.state.search_monitor.recent()}

    @router.delete("/diagnostics/requests", response_model=RequestHistory)
    def clear_monitored_requests(request: Request):
        request.app.state.search_monitor.clear()
        return {"requests": []}

    @router.get("/status")
    def get_status(request: Request):
        settings = _store(request).load()
        try:
            database_connected = bool(request.app.state.database_probe())
        except Exception:
            database_connected = False
        processing = settings["result_processing"]
        return {
            "api_version": WEBAPI_VERSION,
            "application_version": request.app.version,
            "database": {"connected": database_connected},
            "updater": request.app.state.snapshot_updater.status(),
            "result_processing": {
                "preset": processing["preset"],
                "custom_rule_count": len(processing["custom_rules"]),
            },
            "prowlarr": prowlarr_cache.snapshot(_prowlarr_configured(request)),
        }

    @router.get("/settings")
    def get_settings(request: Request):
        return _store(request).public()

    @router.post("/database-update", status_code=202)
    async def trigger_database_update(request: Request):
        if not await request.app.state.snapshot_updater.trigger_manual():
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "update_busy",
                    "message": "A database update is already in progress",
                },
            )
        return {"accepted": True}

    @router.put("/settings")
    def put_settings(request: Request, settings: Any = Body(...)):
        try:
            public = _store(request).update_public(settings)
        except SettingsError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        request.app.state.snapshot_updater.reconfigure()
        prowlarr_cache.reset()
        return public

    @router.get("/result-processing")
    def get_result_processing(request: Request):
        return _store(request).load()["result_processing"]

    @router.put("/result-processing")
    def put_result_processing(request: Request, processing: Any = Body(...)):
        try:
            _store(request).update_result_processing(processing)
        except SettingsError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        return _store(request).load()["result_processing"]

    @router.get("/prowlarr/status")
    def get_prowlarr_status(request: Request):
        status = _live_prowlarr_status(request)
        prowlarr_cache.update(
            connected=status["connected"],
            indexer_installed=status["indexer_installed"],
            error=status["error"],
        )
        return status

    @router.post("/prowlarr/test")
    def test_prowlarr(request: Request):
        try:
            _prowlarr_client(request).test_connection()
        except HTTPException:
            raise
        except ProwlarrError as exc:
            detail = _safe_prowlarr_detail(request, exc)
            prowlarr_cache.update(connected=False, error=detail["message"])
            raise HTTPException(status_code=502, detail=detail) from None
        prowlarr_cache.update(connected=True, error=None)
        return {"connected": True, "error": None}

    @router.post("/prowlarr/indexer")
    def add_prowlarr_indexer(request: Request):
        try:
            result = _prowlarr_client(request, require_indexer=True).ensure_indexer()
        except HTTPException:
            raise
        except ProwlarrError as exc:
            detail = _safe_prowlarr_detail(request, exc)
            connected = exc.code in {"indexer_test_failed", "indexer_create_failed"}
            prowlarr_cache.update(connected=connected, error=detail["message"])
            raise HTTPException(status_code=502, detail=detail) from None
        prowlarr_cache.update(
            connected=True,
            indexer_installed=True,
            error=None,
        )
        return result

    return router
