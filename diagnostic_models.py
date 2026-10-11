"""Bounded WebAPI input and versioned diagnostic output contracts."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

Text = Annotated[str, Field(max_length=512)]
Parameter = Text | int | None


class SearchInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    t: Literal["search", "movie", "tvsearch"] = "search"
    q: Text | None = None
    imdbid: Text | None = None
    tmdbid: Annotated[int, Field(ge=-1000000000, le=1000000000)] | None = None
    season: Annotated[int, Field(ge=-1000000000, le=1000000000)] | None = None
    ep: Annotated[int, Field(ge=-1000000000, le=1000000000)] | None = None
    cat: Text | None = None
    limit: Annotated[int, Field(ge=1, le=200)] = 100
    offset: Annotated[int, Field(ge=0, le=1000000)] = 0


class MonitoringInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    enabled: bool


class MonitoringStatus(BaseModel):
    enabled: bool
    capacity: int
    count: int


class Stage(BaseModel):
    status: Literal["not_run", "running", "success", "failed"]
    duration_ms: float = Field(ge=0)


class Counts(BaseModel):
    candidates: int = Field(ge=0)
    excluded: int = Field(ge=0)
    retained: int = Field(ge=0)
    outside_page: int = Field(ge=0)
    selected: int = Field(ge=0)
    returned: int = Field(ge=0)


class SafeError(BaseModel):
    stage: Literal["input", "database", "processing", "serialization", "search", "merge", "http"]
    code: Text
    message: Text


class Window(BaseModel):
    offset: int
    limit: int
    candidates: int


class Release(BaseModel):
    id: Text
    identity: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    title: Text | None
    size: int | None
    seeders: int | None
    provider: Text | None
    type: Text | None
    category: Literal[2000, 5000, 5070]
    language: Literal["English"] | None
    subtitle_corrected_for_processing: bool
    status: Literal["excluded", "outside_page", "selected", "returned"]
    reason: Text | None
    score: float
    score_rules: list[int] = Field(max_length=100)


class RuleInfo(BaseModel):
    index: int
    enabled: bool
    field: Literal["title", "provider", "size", "seeders"]
    operator: Literal["contains", "not_contains", "equals", "gte", "lte"]
    value: Text | int | float
    action: Literal["exclude", "score"]
    score: float | None = None


class Processing(BaseModel):
    preset: Literal["unfiltered", "italian_only", "italian_preferred", "custom"]
    subtitle_language_correction: bool
    custom_rule_count: int
    custom_rules: list[RuleInfo] = Field(default_factory=list, max_length=100)


class SerializationSettings(BaseModel):
    subtitle_language_correction: bool


class SearchReport(BaseModel):
    report_version: Literal[2]
    application_version: Text
    snapshot_version: Text | None
    generated_at: Text
    original: dict[str, Parameter]
    normalized: dict[str, Parameter]
    strategy: Text | None
    strategies: list["StrategyExecution"] = Field(max_length=32)
    provenance: list["Provenance"] = Field(max_length=2000)
    stages: dict[
        Literal["input", "database", "processing", "serialization", "search", "merge"], Stage
    ]
    counts: Counts
    windows: list[Window] = Field(max_length=2)
    releases: list[Release] = Field(max_length=2000)
    processing: Processing | None = None
    serialization_settings: SerializationSettings | None = None
    errors: list[SafeError] = Field(max_length=4)
    truncated: bool
    replayable: bool
    limitations: list[Text] = Field(max_length=8)
    duration_ms: float = Field(ge=0)


class MonitoredRequest(BaseModel):
    id: int
    timestamp: Text
    original: dict[str, Parameter]
    normalized: dict[str, Parameter]
    strategy: Text | None
    strategies: list["StrategyExecution"] = Field(default_factory=list, max_length=32)
    stages: dict[str, Stage]
    counts: Counts
    duration_ms: float
    status_code: int
    errors: list[SafeError]
    truncated: bool
    replayable: bool


class RequestHistory(BaseModel):
    requests: list[MonitoredRequest] = Field(max_length=100)


class MatchMetadata(BaseModel):
    field: Literal["title", "imdb", "tmdb", "season", "episode"] | None = None
    match_type: Literal["exact", "contains", "token", "browse"] | None = None


class StrategyExecution(BaseModel):
    identifier: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")]
    status: Literal["running", "success", "partial", "failed"]
    duration_ms: float = Field(ge=0)
    candidates: int | None = Field(default=None, ge=0)
    unique_contribution: int | None = Field(default=None, ge=0)
    metadata: MatchMetadata


class MatchEvidence(MatchMetadata):
    strategy: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")]


class Provenance(BaseModel):
    identity: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    occurrences: list[Text] = Field(max_length=2000)
    strategies: list[Text] = Field(max_length=32)
    match_evidence: list[MatchEvidence] = Field(max_length=32)
    deduplicated: bool | None = None
    relevance: float | None = Field(default=None, allow_inf_nan=False)
    included: bool | None = None


SearchReport.model_rebuild()
MonitoredRequest.model_rebuild()
