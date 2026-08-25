"""Single compatibility boundary for historical result preferences."""

from __future__ import annotations

from collections.abc import Mapping

from pydantic import TypeAdapter, ValidationError

from comet.results.config import ResultsConfig

_LEGACY_RESULT_KEYS = frozenset(
    {
        "cachedOnly",
        "removeTrash",
        "resultFormat",
        "maxResultsPerResolution",
        "maxSize",
        "resolutions",
        "sortCachedUncachedTogether",
        "deduplicateStreams",
        "options",
    }
)
_RESOLUTIONS = (
    "2160p",
    "1440p",
    "1080p",
    "720p",
    "576p",
    "480p",
    "360p",
    "240p",
)
_LEGACY_BOOL = TypeAdapter(bool | None)
_LEGACY_INT = TypeAdapter(int | None)
_LEGACY_FLOAT = TypeAdapter(float | None)
_LEGACY_LANGUAGE_KEYS = ("required", "allowed", "exclude", "preferred")


def _mapping(value) -> dict:
    return dict(value) if isinstance(value, Mapping) else {}


def _string_list(value) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    return list(dict.fromkeys(item for item in value if isinstance(item, str) and item))


def _legacy_scalar(document: Mapping, key: str, adapter: TypeAdapter, default):
    """Apply the non-strict scalar coercion used by the historical ConfigModel."""
    if key not in document:
        return default
    try:
        return adapter.validate_python(document[key])
    except ValidationError as exc:
        raise ValueError(f"legacy {key} has an invalid value") from exc


def _legacy_languages(value) -> dict:
    """Accept the old untyped mapping while emitting the bounded new shape."""
    source = _mapping(value)
    normalized = {key: _string_list(source.get(key)) for key in _LEGACY_LANGUAGE_KEYS}
    if source.get("unknown") in {"allow", "exclude"}:
        normalized["unknown"] = source["unknown"]
    return normalized


def _legacy_display(fields) -> dict:
    selected = _string_list(fields)
    if selected == ["all"]:
        return {"preset": "default"}
    selected = set(selected)
    lines = []
    if "title" in selected:
        lines.append("{?stream.legacyTitle}{stream.legacyTitle}{/stream.legacyTitle}")
    detail = []
    if "video_info" in selected:
        detail.append("{?stream.legacyVideo}{stream.legacyVideo}{/stream.legacyVideo}")
    if "audio_info" in selected:
        detail.append("{?stream.legacyAudio}{stream.legacyAudio}{/stream.legacyAudio}")
    if detail:
        lines.append(" | ".join(detail))
    detail = []
    if "quality_info" in selected:
        detail.append(
            "{?stream.legacyQuality}{stream.legacyQuality}{/stream.legacyQuality}"
        )
    if "release_group" in selected:
        detail.append("{?stream.legacyGroup}{stream.legacyGroup}{/stream.legacyGroup}")
    if detail:
        lines.append(" | ".join(detail))
    detail = []
    if "seeders" in selected:
        detail.append(
            "{?stream.legacySeeders}{stream.legacySeeders}{/stream.legacySeeders}"
        )
    if "size" in selected:
        detail.append("{?stream.legacySize}{stream.legacySize}{/stream.legacySize}")
    if "tracker" in selected:
        detail.append(
            "{?stream.legacySource}{stream.legacySource}{/stream.legacySource}"
        )
    if detail:
        lines.append(" ".join(detail))
    if "languages" in selected:
        lines.append(
            "{?stream.legacyLanguages}{stream.legacyLanguages}{/stream.legacyLanguages}"
        )
    if "subtitles" in selected:
        lines.append(
            "{?stream.legacySubtitles}{stream.legacySubtitles}{/stream.legacySubtitles}"
        )
    description = "\n".join(lines) or "Empty result format configuration"
    return {
        "preset": "custom",
        "name": "{stream.defaultName}",
        "description": description,
    }


def _legacy_sort(document: Mapping) -> list[dict]:
    mixed = document.get("sortCachedUncachedTogether")
    if mixed is False:
        return [
            {"key": "cached", "direction": "desc"},
            {"key": "resolution", "direction": "desc"},
            {"key": "rank", "direction": "desc"},
            {"key": "provider", "direction": "asc"},
        ]
    if mixed is True:
        return [
            {"key": "resolution", "direction": "desc"},
            {"key": "rank", "direction": "desc"},
            {"key": "provider", "direction": "asc"},
        ]
    return ResultsConfig().model_dump(mode="json")["sort"]


def _legacy_results(document: Mapping) -> dict:
    remove_trash = _legacy_scalar(document, "removeTrash", _LEGACY_BOOL, True)
    cached_only = _legacy_scalar(document, "cachedOnly", _LEGACY_BOOL, False)
    maximum_size = _legacy_scalar(document, "maxSize", _LEGACY_FLOAT, 0.0)
    maximum_results = _legacy_scalar(
        document, "maxResultsPerResolution", _LEGACY_INT, 0
    )
    if "maxSize" in document and maximum_size is None:
        raise ValueError("legacy maxSize has an invalid value")
    if "maxResultsPerResolution" in document and maximum_results is None:
        raise ValueError("legacy maxResultsPerResolution has an invalid value")
    filters: dict = {"removeTrash": bool(remove_trash)}
    resolutions = _mapping(document.get("resolutions"))
    if resolutions:
        allowed = [
            resolution
            for resolution in _RESOLUTIONS
            if bool(resolutions.get(f"r{resolution}", True))
        ]
        filters["dimensions"] = {
            "resolution": (
                {"only": allowed}
                if allowed
                else {"exclude": [*_RESOLUTIONS, "144p", "unknown"]}
            )
        }

    if maximum_size is not None and maximum_size > 0:
        filters["ranges"] = {"playbackSize": {"max": maximum_size}}

    rules = []
    if cached_only:
        rules.append(
            {
                "id": "legacy-cached-only",
                "action": "exclude",
                "all": [{"field": "cacheState", "op": "is", "value": "uncached"}],
            }
        )
    if rules:
        filters["rules"] = rules

    limits = []
    if maximum_results is not None and maximum_results > 0:
        limits.append({"by": "resolution", "max": maximum_results})

    alternatives = {}
    deduplicate = document.get("deduplicateStreams")
    if deduplicate is True:
        alternatives = {
            "cached": "best",
            "uncached": "all",
            "usenet": "all",
            "hideUncachedWhenCached": False,
            "direct": "unlessCached",
            "fallback": False,
        }
    elif deduplicate is False:
        alternatives = {
            "cached": "all",
            "uncached": "all",
            "usenet": "all",
            "hideUncachedWhenCached": False,
            "direct": "always",
            "fallback": False,
        }

    results = {
        "filters": filters,
        "sort": _legacy_sort(document),
        "limits": limits,
        "display": _legacy_display(document.get("resultFormat", ["all"])),
        "auxiliary": {
            "filterSummary": "off",
            "errors": "bottom",
            "debridSync": "bottom",
        },
    }
    if alternatives:
        results["alternatives"] = alternatives
    return results


def migrate_configuration_document(
    value,
    *,
    legacy_if_results_missing: bool = False,
) -> object:
    """Return one canonical document without mutating the caller's object."""
    if not isinstance(value, Mapping):
        return value
    document = dict(value)
    explicit_legacy = any(key in document for key in _LEGACY_RESULT_KEYS)
    legacy_document = explicit_legacy or (
        legacy_if_results_missing and "results" not in document
    )
    raw_options = document.get("options")
    if raw_options is not None and not isinstance(raw_options, Mapping):
        raise ValueError("legacy options must be an object")
    options = _mapping(raw_options)
    unknown_options = set(options) - {
        "allow_english_in_languages",
        "remove_unknown_languages",
        "remove_ranks_under",
    }
    if unknown_options:
        raise ValueError("legacy options contain an unknown field")
    for key in ("allow_english_in_languages", "remove_unknown_languages"):
        if key in options and type(options[key]) is not bool:
            raise ValueError(f"legacy option {key} must be boolean")
    if "remove_ranks_under" in options and document.get("schemaVersion", 1) != 1:
        raise ValueError("legacy rank threshold is only accepted in schema v1")
    languages = (
        _legacy_languages(document.get("languages"))
        if legacy_document
        else _mapping(document.get("languages"))
    )
    if options.get("allow_english_in_languages") is True:
        languages["allowed"] = list(
            dict.fromkeys((*_string_list(languages.get("allowed")), "en"))
        )
    if "unknown" not in languages and "remove_unknown_languages" in options:
        languages["unknown"] = (
            "exclude" if options.get("remove_unknown_languages") is True else "allow"
        )
    if languages or (
        legacy_document and isinstance(document.get("languages"), Mapping)
    ):
        document["languages"] = languages

    if "results" not in document and (legacy_if_results_missing or explicit_legacy):
        document["results"] = _legacy_results(document)

    for key in _LEGACY_RESULT_KEYS:
        document.pop(key, None)
    return document
