"""Bounded user configuration for the result pipeline."""

from __future__ import annotations

from typing import Annotated, Literal, get_args, get_origin

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from comet.results.facts import FACT_VOCABULARY

MAX_FACET_VALUES = 64
MAX_KEYWORDS_PER_ACTION = 32
MAX_POLICY_RULES = 64
MAX_PREDICATES_PER_RULE = 8
MAX_SORT_CRITERIA = 20
MAX_LIMIT_RULES = 32
MAX_TEMPLATE_LENGTH = 4_096
MAX_TEXT_VALUE_LENGTH = 128

ShortText = Annotated[str, Field(min_length=1, max_length=MAX_TEXT_VALUE_LENGTH)]
Scalar = str | bool | int | float
_NUMERIC_POLICY_FIELDS = frozenset(
    {"playbackSize", "releaseSize", "seeders", "ageDays", "bitrate"}
)

# `cached`/`uncached` describe a debrid option, `directTorrent` a P2P option,
# `needsDownload` any option that must be fetched before playback, and
# `torrent`/`usenet` the release transport itself.
Scope = Literal[
    "all",
    "cached",
    "uncached",
    "directTorrent",
    "needsDownload",
    "torrent",
    "usenet",
]
PolicyField = Literal[
    "mediaType",
    "resolution",
    "quality",
    "visual",
    "videoCodec",
    "audio",
    "channels",
    "languages",
    "subtitles",
    "releaseType",
    "releaseGroup",
    "edition",
    "flags",
    "container",
    "source",
    "transport",
    "providerKind",
    "providerId",
    "cacheState",
    "playbackSize",
    "releaseSize",
    "seeders",
    "ageDays",
    "bitrate",
    "private",
    "trash",
    "title",
]
PredicateOperator = Literal[
    "is",
    "isNot",
    "oneOf",
    "noneOf",
    "contains",
    "notContains",
    "lt",
    "lte",
    "gt",
    "gte",
    "between",
    "known",
    "unknown",
]
SortKey = Literal[
    "resolution",
    "cached",
    "language",
    "keyword",
    "preferenceRule",
    "rank",
    "quality",
    "videoCodec",
    "hdr",
    "audio",
    "channels",
    "subtitles",
    "size",
    "seeders",
    "age",
    "provider",
    "transport",
    "source",
    "releaseGroup",
    "private",
]

RESULT_SORT_KEYS = get_args(SortKey)
RESULT_POLICY_FIELDS = get_args(PolicyField)
RESULT_SCOPES = get_args(Scope)
# Sort keys whose order is a list of canonical values instead of asc/desc alone.
RESULT_SORT_VOCABULARY = {
    "resolution": FACT_VOCABULARY["resolution"],
    "quality": FACT_VOCABULARY["quality"],
    "videoCodec": FACT_VOCABULARY["videoCodec"],
    "hdr": FACT_VOCABULARY["visual"],
    "audio": FACT_VOCABULARY["audio"],
    "channels": FACT_VOCABULARY["channels"],
    "transport": FACT_VOCABULARY["transport"],
}


class StrictConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


def _deduplicate(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value.strip() for value in values if value.strip()))


class LanguagesConfig(StrictConfigModel):
    required: list[ShortText] = Field(default_factory=list, max_length=64)
    allowed: list[ShortText] = Field(default_factory=list, max_length=64)
    exclude: list[ShortText] = Field(default_factory=list, max_length=64)
    preferred: list[ShortText] = Field(default_factory=list, max_length=64)
    unknown: Literal["allow", "exclude"] = "allow"

    @field_validator("required", "allowed", "exclude", "preferred")
    @classmethod
    def normalize_lists(cls, value: list[str]) -> list[str]:
        return _deduplicate(value)


class FacetConfig(StrictConfigModel):
    only: list[ShortText] = Field(default_factory=list, max_length=MAX_FACET_VALUES)
    exclude: list[ShortText] = Field(default_factory=list, max_length=MAX_FACET_VALUES)

    @field_validator("only", "exclude")
    @classmethod
    def normalize_values(cls, value: list[str]) -> list[str]:
        return _deduplicate(value)


class DimensionFilters(StrictConfigModel):
    resolution: FacetConfig = Field(default_factory=FacetConfig)
    quality: FacetConfig = Field(default_factory=FacetConfig)
    visual: FacetConfig = Field(default_factory=FacetConfig)
    videoCodec: FacetConfig = Field(default_factory=FacetConfig)
    audio: FacetConfig = Field(default_factory=FacetConfig)
    channels: FacetConfig = Field(default_factory=FacetConfig)
    subtitles: FacetConfig = Field(default_factory=FacetConfig)
    releaseType: FacetConfig = Field(default_factory=FacetConfig)
    releaseGroup: FacetConfig = Field(default_factory=FacetConfig)
    edition: FacetConfig = Field(default_factory=FacetConfig)
    flags: FacetConfig = Field(default_factory=FacetConfig)
    source: FacetConfig = Field(default_factory=FacetConfig)
    transport: FacetConfig = Field(default_factory=FacetConfig)
    providerKind: FacetConfig = Field(default_factory=FacetConfig)
    providerId: FacetConfig = Field(default_factory=FacetConfig)


class RangeConfig(StrictConfigModel):
    min: float | None = Field(default=None, ge=0)
    max: float | None = Field(default=None, ge=0)
    scope: Scope = "all"

    @model_validator(mode="after")
    def validate_bounds(self):
        if self.min is None and self.max is None:
            raise ValueError("a numeric range requires min or max")
        if self.min is not None and self.max is not None and self.min > self.max:
            raise ValueError("numeric range min must not exceed max")
        return self


class RangeFilters(StrictConfigModel):
    playbackSize: RangeConfig | None = None
    releaseSize: RangeConfig | None = None
    seeders: RangeConfig | None = None
    ageDays: RangeConfig | None = None
    bitrate: RangeConfig | None = None

    @model_validator(mode="before")
    @classmethod
    def normalize_documented_size_alias(cls, value):
        if not isinstance(value, dict) or "sizeBytes" not in value:
            return value
        if "playbackSize" in value:
            raise ValueError("sizeBytes and playbackSize cannot both be configured")
        normalized = dict(value)
        normalized["playbackSize"] = normalized.pop("sizeBytes")
        return normalized


class KeywordPattern(StrictConfigModel):
    value: ShortText
    mode: Literal["word", "phrase", "wildcard"] = "phrase"
    target: Literal["title", "releaseGroup", "source"] = "title"


class KeywordFilters(StrictConfigModel):
    exclude: list[KeywordPattern] = Field(
        default_factory=list, max_length=MAX_KEYWORDS_PER_ACTION
    )
    require: list[KeywordPattern] = Field(
        default_factory=list, max_length=MAX_KEYWORDS_PER_ACTION
    )
    prefer: list[KeywordPattern] = Field(
        default_factory=list, max_length=MAX_KEYWORDS_PER_ACTION
    )

    @field_validator("exclude", "require", "prefer", mode="before")
    @classmethod
    def expand_literal_values(cls, value):
        if value is None:
            return []
        if not isinstance(value, list):
            return value
        return [
            {"value": item, "mode": "phrase", "target": "title"}
            if isinstance(item, str)
            else item
            for item in value
        ]


class PolicyPredicate(StrictConfigModel):
    field: PolicyField
    op: PredicateOperator
    value: Scalar | None = None
    values: list[Scalar] | None = Field(default=None, max_length=MAX_FACET_VALUES)

    @model_validator(mode="after")
    def validate_operand(self):
        if self.op in {"known", "unknown"}:
            if self.value is not None or self.values is not None:
                raise ValueError(f"{self.op} does not accept an operand")
            return self
        if self.op in {"oneOf", "noneOf", "between"}:
            if not self.values:
                raise ValueError(f"{self.op} requires values")
            if self.value is not None:
                raise ValueError(f"{self.op} does not accept value")
            if self.op == "between":
                if self.field not in _NUMERIC_POLICY_FIELDS:
                    raise ValueError("between requires a numeric fact")
                if len(self.values) != 2 or any(
                    isinstance(item, (str, bool)) for item in self.values
                ):
                    raise ValueError("between requires exactly two numeric values")
                if self.values[0] > self.values[1]:
                    raise ValueError("between lower bound must not exceed upper bound")
            return self
        if self.value is None or self.values is not None:
            raise ValueError(f"{self.op} requires one value")
        if self.op in {"lt", "lte", "gt", "gte"}:
            if self.field not in _NUMERIC_POLICY_FIELDS:
                raise ValueError(f"{self.op} requires a numeric fact")
            if isinstance(self.value, (str, bool)):
                raise ValueError(f"{self.op} requires a numeric value")
        if self.op in {"contains", "notContains"} and not isinstance(self.value, str):
            raise ValueError(f"{self.op} requires a string value")
        return self


class PolicyRule(StrictConfigModel):
    id: ShortText | None = None
    action: Literal["exclude", "require", "prefer", "addLanguage"]
    all: list[PolicyPredicate] = Field(min_length=1, max_length=MAX_PREDICATES_PER_RULE)
    language: ShortText | None = None

    @model_validator(mode="after")
    def validate_action(self):
        if self.action == "addLanguage":
            if self.language is None:
                raise ValueError("addLanguage requires language")
        elif self.language is not None:
            raise ValueError("language is only valid for addLanguage")
        return self


class FilterConfig(StrictConfigModel):
    removeTrash: bool = True
    dimensions: DimensionFilters = Field(default_factory=DimensionFilters)
    ranges: RangeFilters = Field(default_factory=RangeFilters)
    keywords: KeywordFilters = Field(default_factory=KeywordFilters)
    rules: list[PolicyRule] = Field(default_factory=list, max_length=MAX_POLICY_RULES)


class SortCriterion(StrictConfigModel):
    key: SortKey
    direction: Literal["asc", "desc"] = "desc"
    scope: Scope = "all"
    order: list[ShortText] | None = Field(default=None, max_length=MAX_FACET_VALUES)

    @field_validator("order")
    @classmethod
    def normalize_order(cls, value: list[str] | None) -> list[str] | None:
        return _deduplicate(value) if value is not None else None


class LimitRule(StrictConfigModel):
    by: Literal[
        "total",
        "resolution",
        "quality",
        "provider",
        "transport",
        "source",
        "releaseGroup",
    ]
    max: int = Field(ge=0, le=1_000)


class AlternativesConfig(StrictConfigModel):
    cached: Literal["all", "best"] = "all"
    uncached: Literal["all", "best"] = "all"
    usenet: Literal["all", "best"] = "all"
    hideUncachedWhenCached: bool = False
    direct: Literal["always", "unlessCached"] = "always"
    fallback: bool = False


class DisplayConfig(StrictConfigModel):
    preset: Literal["default", "compact", "technical", "custom"] = "default"
    name: Annotated[str, Field(max_length=MAX_TEMPLATE_LENGTH)] | None = None
    description: Annotated[str, Field(max_length=MAX_TEMPLATE_LENGTH)] | None = None

    @model_validator(mode="after")
    def validate_custom_templates(self):
        custom_values = self.name is not None or self.description is not None
        if self.preset == "custom":
            if not self.name or not self.description:
                raise ValueError("custom display requires name and description")
        elif custom_values:
            raise ValueError("templates are only valid with the custom preset")
        return self


class AuxiliaryResultsConfig(StrictConfigModel):
    filterSummary: Literal["off", "whenEmpty", "top", "bottom"] = "whenEmpty"
    errors: Literal["off", "top", "bottom"] = "bottom"
    debridSync: Literal["off", "top", "bottom"] = "bottom"


def default_sort() -> list[SortCriterion]:
    return [
        SortCriterion(key="resolution", direction="desc"),
        SortCriterion(key="cached", direction="desc"),
        SortCriterion(key="language", direction="desc"),
        SortCriterion(key="rank", direction="desc"),
        SortCriterion(key="provider", direction="asc"),
    ]


class ResultsConfig(StrictConfigModel):
    filters: FilterConfig = Field(default_factory=FilterConfig)
    sort: list[SortCriterion] = Field(
        default_factory=default_sort, max_length=MAX_SORT_CRITERIA
    )
    limits: list[LimitRule] = Field(default_factory=list, max_length=MAX_LIMIT_RULES)
    alternatives: AlternativesConfig = Field(default_factory=AlternativesConfig)
    display: DisplayConfig = Field(default_factory=DisplayConfig)
    auxiliary: AuxiliaryResultsConfig = Field(default_factory=AuxiliaryResultsConfig)

    @field_validator("sort")
    @classmethod
    def unique_sort_keys(cls, value: list[SortCriterion]) -> list[SortCriterion]:
        keys = [criterion.key for criterion in value]
        if len(keys) != len(set(keys)):
            raise ValueError("sort criteria must be unique")
        return value

    @field_validator("limits")
    @classmethod
    def unique_limit_keys(cls, value: list[LimitRule]) -> list[LimitRule]:
        keys = [rule.by for rule in value]
        if len(keys) != len(set(keys)):
            raise ValueError("limit rules must be unique by dimension")
        return value


def _literal_values(annotation) -> tuple[str, ...]:
    if get_origin(annotation) is Literal:
        return tuple(str(value) for value in get_args(annotation))
    return tuple(
        value
        for argument in get_args(annotation)
        for value in _literal_values(argument)
    )


def result_enum_identifiers() -> tuple[str, ...]:
    """Every identifier the configurator labels, derived from the models themselves."""
    identifiers = {
        "asc",
        "desc",
        "true",
        "false",
        "unknown",
        *get_args(Scope),
        *get_args(SortKey),
        *get_args(PolicyField),
        *get_args(PredicateOperator),
        *(value for values in FACT_VOCABULARY.values() for value in values),
        *FacetConfig.model_fields,
        *DimensionFilters.model_fields,
        *RangeFilters.model_fields,
        *AlternativesConfig.model_fields,
        *AuxiliaryResultsConfig.model_fields,
    }
    for model in (
        AlternativesConfig,
        AuxiliaryResultsConfig,
        DisplayConfig,
        KeywordFilters,
        KeywordPattern,
        LanguagesConfig,
        LimitRule,
        PolicyRule,
        SortCriterion,
    ):
        for field in model.model_fields.values():
            identifiers.update(_literal_values(field.annotation))
    # Identifiers holding a dot (audio channels like 5.1) are numerals rendered
    # identically in every language, and i18next reads dots as key separators,
    # so they stay on their canonical form instead of carrying a dead entry.
    return tuple(sorted(value for value in identifiers if "." not in value))


SORT_PRESETS: dict[str, tuple[dict[str, str], ...]] = {
    "smart": (
        {"key": "resolution", "direction": "desc"},
        {"key": "cached", "direction": "desc"},
        {"key": "language", "direction": "desc"},
        {"key": "rank", "direction": "desc"},
        {"key": "provider", "direction": "asc"},
    ),
    "instant": (
        {"key": "cached", "direction": "desc"},
        {"key": "resolution", "direction": "desc"},
        {"key": "language", "direction": "desc"},
        {"key": "rank", "direction": "desc"},
        {"key": "provider", "direction": "asc"},
    ),
    "qualitySeeders": (
        {"key": "resolution", "direction": "desc"},
        {"key": "quality", "direction": "desc"},
        {"key": "seeders", "direction": "desc", "scope": "needsDownload"},
        {"key": "size", "direction": "desc"},
    ),
    "language": (
        {"key": "language", "direction": "desc"},
        {"key": "cached", "direction": "desc"},
        {"key": "resolution", "direction": "desc"},
        {"key": "rank", "direction": "desc"},
    ),
}
