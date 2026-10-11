"""Request-scoped observations; never a second query or filtering engine."""

import re
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from time import perf_counter
from xml.etree.ElementTree import fromstring

from release_language import should_force_english

# Only legacy SQL branch selection uses this context; it holds the safe facade,
# never a raw collector, and execute_search resets it in an unconditional finally.
ACTIVE_COLLECTOR = ContextVar("search_collector", default=None)
PARAMETERS = {"t", "q", "imdbid", "tmdbid", "season", "ep", "cat", "limit", "offset"}
MAX_TEXT = 512
MAX_RELEASES = 2000
MAX_STRATEGIES = 32
STAGES = {"input", "database", "processing", "serialization", "search", "merge"}
EVIDENCE = {
    "field": {"title", "imdb", "tmdb", "season", "episode"},
    "match_type": {"exact", "contains", "token", "browse"},
}


@dataclass(frozen=True)
class DiagnosticResult:
    """Observation-only adapter. Raw rows remain unchanged in the search engine."""

    title: str | None
    size: int | None
    seeders: int | None
    provider: str | None
    torrent_type: str | None
    identity: str

    @classmethod
    def from_row(cls, row):
        title, size, seeders, provider, _, info_hash, torrent_type = row
        return cls(
            title,
            size,
            seeders,
            provider,
            torrent_type,
            sha256(str(info_hash).encode()).hexdigest(),
        )


def safe_evidence(metadata):
    return {
        key: value
        for key, value in (metadata or {}).items()
        if key in EVIDENCE and isinstance(value, str) and value in EVIDENCE[key]
    }


def safe_text(value, limit=MAX_TEXT):
    if value is None:
        return None
    text = str(value)
    text = re.sub(r"(?:magnet:\?|[a-z][a-z0-9+.-]*://)\S*", "[redacted]", text, flags=re.I)
    text = re.sub(r"(?:/|[A-Z]:\\)[^\s]+", "[redacted]", text)
    text = re.sub(r"(?:password|apikey|api_key|token)\s*[:=]\s*\S+", "[redacted]", text, flags=re.I)
    text = re.sub(r"\b(?:[a-f0-9]{40}|[a-z2-7]{32})\b", "[redacted]", text, flags=re.I)
    return "".join(c for c in text if c.isprintable())[:limit]


def safe_parameters(values):
    result = {}
    changed = False
    for key in PARAMETERS:
        if key not in values:
            continue
        value = values[key]
        clean = safe_text(value) if isinstance(value, str) else value
        if clean is not None and not isinstance(clean, (str, int)):
            clean = None
        if isinstance(clean, int) and (isinstance(clean, bool) or abs(clean) > 1000000000):
            clean = None
        result[key] = clean
        changed |= clean != value
    return result, changed


def record_strategy(name):
    collector = ACTIVE_COLLECTOR.get()
    if collector is not None:
        collector.strategy_begin(name)


class SearchCollector:
    def __init__(self, *, detailed=True):
        self.detailed = detailed
        self.started = perf_counter()
        self.stage_started = self.started
        self.stage = "input"
        self.selected_ids = []
        self.active_strategy = None
        self.strategy_started = self.started
        self.identities = {}
        self.provenance_occurrences = set()
        self.results = {}
        self.report = {
            "report_version": 2,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "original": {},
            "normalized": {},
            "strategy": None,
            "strategies": [],
            "provenance": [],
            "stages": {
                name: {"status": "not_run", "duration_ms": 0}
                for name in ("input", "database", "processing", "serialization")
            },
            "counts": {
                name: 0
                for name in (
                    "candidates",
                    "excluded",
                    "retained",
                    "outside_page",
                    "selected",
                    "returned",
                )
            },
            "windows": [],
            "releases": [],
            "errors": [],
            "truncated": False,
            "replayable": True,
            "limitations": [
                "Counts describe inspected SQL windows, not all database matches.",
                "Ranking is window-local; filtered pages are not backfilled.",
                "Uninspected releases may exist. Downstream acceptance is unknown.",
                "Search terms and release titles may be sensitive; review before sharing.",
            ],
        }

    def begin(self, name):
        if name not in STAGES:
            raise ValueError("Unknown observation phase")
        self.stage = name
        self.stage_started = perf_counter()
        self.report["stages"].setdefault(name, {"status": "not_run", "duration_ms": 0})[
            "status"
        ] = "running"

    def complete(self):
        stage = self.report["stages"][self.stage]
        stage["status"] = "success"
        stage["duration_ms"] += (perf_counter() - self.stage_started) * 1000

    def fail(self):
        self.strategy_complete(status="failed")
        stage = self.report["stages"][self.stage]
        stage["status"] = "failed"
        stage["duration_ms"] += (perf_counter() - self.stage_started) * 1000
        messages = {
            "input": "Search configuration unavailable",
            "database": "Database search failed",
            "processing": "Result processing failed",
            "serialization": "Torznab serialization failed",
            "search": "Search observation failed",
            "merge": "Merge observation failed",
        }
        self.report["errors"].append(
            {"stage": self.stage, "code": f"{self.stage}_failed", "message": messages[self.stage]}
        )

    def finish(self):
        self.report["duration_ms"] = (perf_counter() - self.started) * 1000

    def inputs(self, original, normalized):
        self.report["original"], changed = safe_parameters(original)
        self.report["normalized"], changed_normalized = safe_parameters(normalized)
        self.report["truncated"] |= changed or changed_normalized
        self.report["replayable"] = not self.report["truncated"]

    def window(self, rows, limit, offset):
        self.report["windows"].append({"offset": offset, "limit": limit, "candidates": len(rows)})
        self.report["counts"]["candidates"] += len(rows)
        if self.detailed:
            for index, row in enumerate(rows[:MAX_RELEASES]):
                occurrence = f"{offset}:{index}"
                result = DiagnosticResult.from_row(row)
                if len(self.results) < MAX_RELEASES:
                    self.results[occurrence] = result
                    self.strategy_result(result, occurrence)
                else:
                    self.report["truncated"] = True
        self.strategy_complete(candidates=len(rows))

    def strategy_begin(self, identifier, metadata=None):
        self.active_strategy = None
        if not isinstance(identifier, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", identifier):
            raise ValueError("Invalid strategy identifier")
        self.report["strategy"] = identifier
        if len(self.report["strategies"]) >= MAX_STRATEGIES:
            self.report["truncated"] = True
            return
        self.strategy_started = perf_counter()
        self.active_strategy = {
            "identifier": identifier,
            "status": "running",
            "duration_ms": 0,
            "candidates": None,
            "unique_contribution": None,
            "metadata": safe_evidence(metadata),
        }
        self.report["strategies"].append(self.active_strategy)

    def strategy_complete(self, *, status="success", candidates=None, unique_contribution=None):
        if status not in {"success", "partial", "failed"}:
            raise ValueError("Invalid strategy status")
        if self.active_strategy is not None:
            self.active_strategy.update(
                status=status,
                candidates=candidates,
                unique_contribution=unique_contribution,
                duration_ms=(perf_counter() - self.strategy_started) * 1000,
            )
            self.active_strategy = None

    def strategy_result(self, result: DiagnosticResult, occurrence, metadata=None):
        if not self.detailed:
            return
        occurrence_key = (result.identity, occurrence)
        if occurrence_key not in self.provenance_occurrences:
            if len(self.provenance_occurrences) >= MAX_RELEASES:
                self.report["truncated"] = True
                return
            self.provenance_occurrences.add(occurrence_key)
        provenance = self.identities.get(result.identity)
        if provenance is None:
            if len(self.identities) >= MAX_RELEASES:
                self.report["truncated"] = True
                return
            provenance = {
                "identity": result.identity,
                "occurrences": [],
                "strategies": [],
                "match_evidence": [],
                "deduplicated": None,
                "relevance": None,
                "included": None,
            }
            self.identities[result.identity] = provenance
            self.report["provenance"].append(provenance)
        if occurrence not in provenance["occurrences"]:
            provenance["occurrences"].append(safe_text(occurrence, 64))
        if self.active_strategy is not None:
            identifier = self.active_strategy["identifier"]
            if identifier not in provenance["strategies"]:
                provenance["strategies"].append(identifier)
            evidence = {
                "strategy": identifier,
                **self.active_strategy["metadata"],
                **safe_evidence(metadata),
            }
            if (
                evidence not in provenance["match_evidence"]
                and len(provenance["match_evidence"]) < MAX_STRATEGIES
            ):
                provenance["match_evidence"].append(evidence)

    def result_facts(
        self, result: DiagnosticResult, *, deduplicated=None, relevance=None, included=None
    ):
        """Only call when the engine actually observes these facts, never infer them."""
        if result.identity in self.identities:
            self.identities[result.identity].update(
                deduplicated=deduplicated, relevance=relevance, included=included
            )

    def processing_settings(self, processing):
        self.report["processing"] = {
            "preset": processing["preset"],
            "subtitle_language_correction": processing["subtitle_language_correction"],
            "custom_rule_count": len(processing["custom_rules"]),
            "custom_rules": [
                {
                    "index": index,
                    **{
                        key: rule[key]
                        for key in ("enabled", "field", "operator", "action", "score")
                        if key in rule
                    },
                    "value": self.safe_metadata(rule["value"])
                    if isinstance(rule["value"], str)
                    else rule["value"],
                }
                for index, rule in enumerate(processing["custom_rules"][:100])
            ]
            if self.detailed
            else [],
        }

    def serialization_settings(self, correction):
        self.report["serialization_settings"] = {"subtitle_language_correction": correction}

    def versions(self, application, snapshot):
        self.report["application_version"] = safe_text(application)
        self.report["snapshot_version"] = safe_text(snapshot)

    def snapshot(self):
        """Validated allowlist projection, not a serialization of collector internals."""
        from diagnostic_models import SearchReport

        original, changed = safe_parameters(self.report["original"])
        normalized, normalized_changed = safe_parameters(self.report["normalized"])
        return SearchReport.model_validate(
            {
                **self.report,
                "original": original,
                "normalized": normalized,
                "truncated": self.report["truncated"] or changed or normalized_changed,
                "replayable": self.report["replayable"] and not changed and not normalized_changed,
            }
        ).model_dump()

    def safe_metadata(self, value, limit=MAX_TEXT):
        clean = safe_text(value, limit)
        self.report["truncated"] |= clean != value
        return clean

    def observer(self, window_offset, correction):
        owner = self

        class Observer:
            order = []

            def __call__(self, index, row, reason, score, score_rules):
                counts = owner.report["counts"]
                counts["excluded" if reason else "retained"] += 1
                if not owner.detailed:
                    return
                if len(owner.report["releases"]) >= MAX_RELEASES:
                    owner.report["truncated"] = True
                    return
                result = owner.results.get(f"{window_offset}:{index}") or DiagnosticResult.from_row(
                    row
                )
                title = owner.safe_metadata(result.title)
                owner.report["releases"].append(
                    {
                        "id": f"{window_offset}:{index}",
                        "identity": result.identity,
                        "title": title,
                        "size": result.size,
                        "seeders": result.seeders,
                        "provider": owner.safe_metadata(result.provider),
                        "type": owner.safe_metadata(result.torrent_type, 32),
                        "category": 2000
                        if result.torrent_type == "movie"
                        else 5070
                        if result.torrent_type == "anime"
                        else 5000,
                        "language": None,
                        "subtitle_corrected_for_processing": bool(
                            correction
                            and owner.report["processing"]["preset"]
                            in {"italian_preferred", "italian_only"}
                            and should_force_english(result.title)
                        ),
                        "status": "excluded" if reason else "outside_page",
                        "reason": reason,
                        "score": score,
                        "score_rules": score_rules,
                    }
                )

        return Observer()

    def page(self, window_offset, indices):
        selected = [f"{window_offset}:{index}" for index in indices]
        self.selected_ids.extend(selected)
        self.report["counts"]["selected"] += len(indices)
        for release in self.report["releases"]:
            if release["id"] in selected:
                release["status"] = "selected"
        self.report["counts"]["outside_page"] = (
            self.report["counts"]["retained"] - self.report["counts"]["selected"]
        )

    def serialized(self, root=None):
        items = None
        if root is not None:
            items = root.findall("channel/item")
            if (
                root.tag != "rss"
                or root.find("channel") is None
                or len(items) != len(self.selected_ids)
            ):
                raise ValueError("Unexpected Torznab output")
            for occurrence, item in zip(self.selected_ids, items, strict=True):
                result = self.results.get(occurrence)
                if (
                    result is not None
                    and sha256((item.findtext("guid") or "").encode()).hexdigest()
                    != result.identity
                ):
                    raise ValueError("Unexpected Torznab identity order")
        self.report["counts"]["returned"] = self.report["counts"]["selected"]
        releases = {release["id"]: release for release in self.report["releases"]}
        for index, candidate_id in enumerate(self.selected_ids):
            release = releases.get(candidate_id)
            if release is not None:
                release["status"] = "returned"
                if items is not None:
                    attrs = {
                        attr.get("name"): attr.get("value")
                        for attr in items[index]
                        if attr.tag == "{http://torznab.com/schemas/2015/feed}attr"
                    }
                    release["language"] = "English" if attrs.get("language") == "English" else None


def observation_snapshot(value):
    """Detach mutable observation containers; reuse immutable scalars and results.

    SQL rows contain scalar fields, so their tuples can be reused. Lists and
    dictionaries (including nested rules) belong to the observer after copying.
    This boundary runs only when observation is enabled, without hashing rows
    or parsing XML on metadata-only paths.
    """
    if isinstance(value, dict):
        return {key: observation_snapshot(item) for key, item in value.items()}
    if isinstance(value, list):
        return [observation_snapshot(item) for item in value]
    if isinstance(value, tuple):
        items = tuple(observation_snapshot(item) for item in value)
        return value if all(a is b for a, b in zip(value, items, strict=True)) else items
    return value


class ObservationFacade:
    """Explicit failure isolation at every engine observation boundary.

    Does not catch search/processing/serialization exceptions. Callback order is
    owned here so an observer cannot affect sorting, selection or duplicate rows.
    """

    def __init__(self, collector):
        self.collector = collector

    def _fault(self):
        try:
            self.collector.report["truncated"] = True
            self.collector.report["replayable"] = False
        except Exception:
            pass

    def _call(self, name, *args, **kwargs):
        try:
            return getattr(self.collector, name)(
                *(observation_snapshot(value) for value in args),
                **{key: observation_snapshot(value) for key, value in kwargs.items()},
            )
        except Exception:
            self._fault()
            return None

    def begin(self, name):
        self._call("begin", name)

    def complete(self):
        self._call("complete")

    def fail(self):
        self._call("fail")

    def finish(self):
        self._call("finish")

    def inputs(self, original, normalized):
        self._call("inputs", original, normalized)

    def processing_settings(self, processing):
        self._call("processing_settings", processing)

    def serialization_settings(self, correction):
        self._call("serialization_settings", correction)

    def strategy_begin(self, identifier, metadata=None):
        self._call("strategy_begin", identifier, metadata)

    def strategy_complete(self, **kwargs):
        self._call("strategy_complete", **kwargs)

    def strategy_result(self, result, occurrence, metadata=None):
        self._call("strategy_result", result, occurrence, metadata)

    def result_facts(self, result, **kwargs):
        self._call("result_facts", result, **kwargs)

    def window(self, rows, limit, offset):
        self._call("window", rows, limit, offset)

    def page(self, offset, indices):
        self._call("page", offset, indices)

    def observer(self, offset, correction):
        callback = self._call("observer", offset, correction)
        owner = self

        class SafeObserver:
            def __init__(self):
                self.order = []

            def __call__(self, *args):
                try:
                    if callback is not None:
                        callback(*(observation_snapshot(value) for value in args))
                except Exception:
                    owner._fault()

        return SafeObserver()

    def serialized(self, xml):
        try:
            # Inspect only bounded detailed responses, never monitored hot paths.
            if self.collector.detailed and len(xml) > 4_000_000:
                raise ValueError("Observation XML exceeds inspection limit")
            root = fromstring(xml) if self.collector.detailed else None
            self.collector.serialized(root)
            return True
        except Exception:
            self._fault()
            self._call("fail")
            return False
