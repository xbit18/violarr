"""Runtime-only metadata buffer and response-transparent ASGI observation."""

from collections import deque
from copy import deepcopy
from threading import Lock
from time import perf_counter

from starlette.datastructures import QueryParams

from diagnostic_models import MonitoredRequest, SearchInput
from search_diagnostics import (
    PARAMETERS,
    ObservationFacade,
    SearchCollector,
    safe_parameters,
    safe_text,
)


class SearchMonitor:
    def __init__(self):
        self._lock = Lock()
        self._enabled = False
        self._entries = deque(maxlen=100)
        self._sequence = 0
        self._clear_cutoff = 0

    def status(self):
        with self._lock:
            return {"enabled": self._enabled, "capacity": 100, "count": len(self._entries)}

    def configure(self, enabled):
        with self._lock:
            self._enabled = enabled
        return self.status()

    def clear(self):
        with self._lock:
            self._entries.clear()
            self._clear_cutoff = self._sequence

    def recent(self):
        with self._lock:
            return deepcopy(list(reversed(self._entries)))

    def reserve(self):
        with self._lock:
            if not self._enabled:
                return None
            self._sequence += 1
            return self._sequence

    def append(self, report, status_code, duration_ms, request_id):
        original, changed = safe_parameters(report["original"])
        normalized, normalized_changed = safe_parameters(report["normalized"])
        replayable = report["replayable"] and not changed and not normalized_changed
        try:
            SearchInput.model_validate(original)
        except ValueError:
            replayable = False
        # Explicit projection: no rows, headers, URLs, raw exceptions or settings.
        entry = {
            "timestamp": report["generated_at"],
            "original": original,
            "normalized": normalized,
            "strategy": safe_text(report["strategy"], 64),
            "strategies": deepcopy(report["strategies"][:32]),
            "stages": deepcopy(report["stages"]),
            "counts": dict(report["counts"]),
            "duration_ms": duration_ms,
            "status_code": status_code,
            "errors": deepcopy(report["errors"][:4]),
            "truncated": report["truncated"] or changed or normalized_changed,
            "replayable": replayable,
        }
        if status_code >= 400 and not entry["errors"]:
            entry["errors"] = [
                {
                    "stage": "http",
                    "code": f"http_{status_code}",
                    "message": "Request unavailable" if status_code == 503 else "Request failed",
                }
            ]
        entry["id"] = request_id
        # Reject incomplete/corrupt observations rather than poison the history API.
        entry = MonitoredRequest.model_validate(entry).model_dump()
        with self._lock:
            if request_id <= self._clear_cutoff:
                return
            # Slow older requests must not evict or outrank newer arrivals.
            entries = sorted([*self._entries, entry], key=lambda item: item["id"])[-100:]
            self._entries.clear()
            self._entries.extend(entries)


class MonitoringCollector(ObservationFacade):
    """Metadata-only facade; response transparency is shared with diagnostics."""

    def __init__(self):
        super().__init__(SearchCollector(detailed=False))

    @property
    def report(self):
        return self.collector.report


class SearchMonitoringMiddleware:
    def __init__(self, app, monitor):
        self.app = app
        self.monitor = monitor

    async def __call__(self, scope, receive, send):
        collector = None
        try:
            if (
                scope["type"] == "http"
                and scope["path"] == "/api"
                and self.monitor.status()["enabled"]
            ):
                params = QueryParams(scope.get("query_string", b""))
                if params.get("t", "search") in {"search", "movie", "tvsearch"}:
                    request_id = self.monitor.reserve()
                    if request_id is not None:
                        collector = MonitoringCollector()
                        collector.inputs(
                            {
                                "t": "search",
                                **{key: params[key] for key in PARAMETERS if key in params},
                            },
                            {},
                        )
                        scope.setdefault("state", {})["search_collector"] = collector
        except Exception:
            collector = None
        if collector is None:
            await self.app(scope, receive, send)
            return
        started = perf_counter()
        status_code = 500

        async def observed_send(message):
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, observed_send)
        finally:
            try:
                self.monitor.append(
                    collector.report, status_code, (perf_counter() - started) * 1000, request_id
                )
            except Exception:
                pass
