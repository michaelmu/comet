import copy

import pytest
from pydantic import ValidationError

from comet.api.v1.contracts import ConfigValidationRequest
from comet.core.models import ConfigModel
from comet.results.config import ResultsConfig
from comet.results.migrations import migrate_configuration_document


def test_allow_english_migrates_to_allowed_without_options():
    source = {
        "languages": {"required": ["fr"], "exclude": ["en"]},
        "options": {
            "allow_english_in_languages": True,
            "remove_unknown_languages": False,
        },
    }
    migrated = migrate_configuration_document(source, legacy_if_results_missing=True)
    assert migrated["languages"]["allowed"] == ["en"]
    assert migrated["languages"]["unknown"] == "allow"
    assert "options" not in migrated
    assert source["languages"] == {"required": ["fr"], "exclude": ["en"]}


def test_all_historical_result_fields_migrate_to_one_results_root():
    migrated = ConfigModel.model_validate(
        {
            "cachedOnly": True,
            "removeTrash": False,
            "maxSize": 1234,
            "maxResultsPerResolution": 7,
            "resolutions": {"r2160p": True, "r1080p": False},
            "resultFormat": ["title", "subtitles"],
            "sortCachedUncachedTogether": False,
            "deduplicateStreams": True,
            "scrapeDebridAccountTorrents": True,
        }
    ).model_dump()
    results = migrated["results"]
    assert results["filters"]["removeTrash"] is False
    assert results["filters"]["ranges"]["playbackSize"]["max"] == 1234
    assert results["filters"]["dimensions"]["resolution"]["only"] == [
        "2160p",
        "1440p",
        "720p",
        "576p",
        "480p",
        "360p",
        "240p",
    ]
    assert results["filters"]["rules"][0]["all"][0] == {
        "field": "cacheState",
        "op": "is",
        "value": "uncached",
        "values": None,
    }
    assert results["limits"] == [{"by": "resolution", "max": 7}]
    assert [criterion["key"] for criterion in results["sort"]] == [
        "cached",
        "resolution",
        "rank",
        "provider",
    ]
    assert results["alternatives"]["direct"] == "unlessCached"
    assert results["display"]["preset"] == "custom"
    assert results["auxiliary"] == {
        "filterSummary": "off",
        "errors": "bottom",
        "debridSync": "bottom",
    }
    assert "cachedOnly" not in migrated
    assert "resultFormat" not in migrated


@pytest.mark.parametrize("schema_version", [1, 2])
def test_legacy_result_scalars_keep_historical_pydantic_coercions(schema_version):
    config = ConfigModel.model_validate(
        {
            "schemaVersion": schema_version,
            "cachedOnly": "true",
            "removeTrash": "false",
            "maxResultsPerResolution": "5",
            "maxSize": "100",
        }
    ).model_dump(mode="json")

    results = config["results"]
    assert results["filters"]["removeTrash"] is False
    assert results["filters"]["rules"][0]["id"] == "legacy-cached-only"
    assert results["filters"]["ranges"]["playbackSize"]["max"] == 100.0
    assert results["limits"] == [{"by": "resolution", "max": 5}]


def test_legacy_untyped_language_and_resolution_mappings_remain_decodable():
    config = ConfigModel.model_validate(
        {
            "cachedOnly": False,
            "languages": {
                "required": ["fr", "fr", 7],
                "allowed": "historically-untyped",
                "unknownLegacyField": {"anything": True},
            },
            "resolutions": {
                "r2160p": 0,
                "r1080p": "false",
                "unknownLegacyField": object(),
            },
        }
    ).model_dump(mode="json")

    assert config["languages"] == {
        "required": ["fr"],
        "allowed": [],
        "exclude": [],
        "preferred": [],
        "unknown": "allow",
    }
    assert config["results"]["filters"]["dimensions"]["resolution"]["only"] == [
        "1440p",
        "1080p",
        "720p",
        "576p",
        "480p",
        "360p",
        "240p",
    ]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("cachedOnly", "not-a-boolean"),
        ("removeTrash", []),
        ("maxResultsPerResolution", "5.5"),
        ("maxResultsPerResolution", None),
        ("maxSize", "not-a-number"),
        ("maxSize", None),
    ],
)
def test_invalid_legacy_scalars_are_not_silently_discarded(field, value):
    with pytest.raises(ValidationError):
        ConfigModel.model_validate({field: value})


def test_new_configuration_gets_new_auxiliary_defaults():
    config = ConfigModel().model_dump()
    assert config["results"]["auxiliary"] == {
        "filterSummary": "whenEmpty",
        "errors": "bottom",
        "debridSync": "bottom",
    }


def test_validation_endpoint_treats_missing_results_as_legacy_document():
    request = ConfigValidationRequest.model_validate(
        {"configuration": {"schemaVersion": 2}}
    )
    assert request.configuration.results.auxiliary.filterSummary == "off"


def test_legacy_invalid_options_remain_invalid_and_v1_rank_threshold_is_discarded():
    with pytest.raises(ValueError):
        migrate_configuration_document({"options": {"allow_english_in_languages": 1}})
    with pytest.raises(ValueError):
        migrate_configuration_document(
            {"schemaVersion": 2, "options": {"remove_ranks_under": -5}}
        )
    migrated = migrate_configuration_document(
        {"schemaVersion": 1, "options": {"remove_ranks_under": -5}},
        legacy_if_results_missing=True,
    )
    assert "options" not in migrated


def test_configuration_round_trip_is_canonical_and_does_not_mutate_input():
    source = ConfigModel().model_dump(mode="json")
    original = copy.deepcopy(source)
    first = ConfigModel.model_validate(source).model_dump(mode="json")
    second = ConfigModel.model_validate(first).model_dump(mode="json")
    assert first == second
    assert source == original


def test_rules_and_templates_are_strictly_bounded():
    with pytest.raises(ValidationError):
        ResultsConfig.model_validate(
            {
                "filters": {
                    "rules": [
                        {
                            "action": "exclude",
                            "all": [
                                {
                                    "field": "title",
                                    "op": "regex",
                                    "value": ".*",
                                }
                            ],
                        }
                    ]
                }
            }
        )
    with pytest.raises(ValidationError):
        ResultsConfig.model_validate(
            {
                "filters": {
                    "rules": [
                        {
                            "action": "exclude",
                            "all": [
                                {
                                    "field": "seeders",
                                    "op": "gte",
                                    "value": "many",
                                }
                            ],
                        }
                    ]
                }
            }
        )
    with pytest.raises(ValidationError):
        ResultsConfig.model_validate(
            {
                "display": {
                    "preset": "custom",
                    "name": "x" * 4097,
                    "description": "ok",
                }
            }
        )


def test_documented_size_bytes_alias_normalizes_to_playback_size():
    results = ResultsConfig.model_validate(
        {"filters": {"ranges": {"sizeBytes": {"max": 1_000_000}}}}
    )
    assert results.filters.ranges.playbackSize is not None
    assert results.filters.ranges.playbackSize.max == 1_000_000
    assert "sizeBytes" not in results.model_dump(mode="json")["filters"]["ranges"]


def test_empty_sort_is_valid_but_duplicate_limits_are_not():
    assert ResultsConfig.model_validate({"sort": []}).sort == []
    with pytest.raises(ValidationError):
        ResultsConfig.model_validate(
            {
                "limits": [
                    {"by": "resolution", "max": 1},
                    {"by": "resolution", "max": 2},
                ]
            }
        )


def test_between_requires_an_ordered_numeric_field_interval():
    for predicate in (
        {"field": "title", "op": "between", "values": [1, 2]},
        {"field": "seeders", "op": "between", "values": [2, 1]},
    ):
        with pytest.raises(ValidationError):
            ResultsConfig.model_validate(
                {"filters": {"rules": [{"action": "exclude", "all": [predicate]}]}}
            )


def test_legacy_empty_format_and_all_disabled_resolutions_remain_effective():
    migrated = migrate_configuration_document(
        {
            "resultFormat": [],
            "resolutions": {
                f"r{resolution}": False
                for resolution in (
                    "2160p",
                    "1440p",
                    "1080p",
                    "720p",
                    "576p",
                    "480p",
                    "360p",
                    "240p",
                )
            },
        }
    )["results"]
    assert migrated["display"]["preset"] == "custom"
    assert migrated["display"]["description"] == "Empty result format configuration"
    excluded = migrated["filters"]["dimensions"]["resolution"]["exclude"]
    assert {"2160p", "144p", "unknown"} <= set(excluded)


def test_validation_endpoint_rejects_semantically_invalid_compiled_configuration():
    with pytest.raises(ValidationError):
        ConfigValidationRequest.model_validate(
            {
                "configuration": {
                    "results": {
                        "filters": {
                            "rules": [
                                {
                                    "action": "addLanguage",
                                    "language": "fr",
                                    "all": [
                                        {
                                            "field": "cacheState",
                                            "op": "is",
                                            "value": "cached",
                                        }
                                    ],
                                }
                            ]
                        }
                    }
                }
            }
        )
    with pytest.raises(ValidationError):
        ConfigValidationRequest.model_validate(
            {
                "configuration": {
                    "results": {
                        "display": {
                            "preset": "custom",
                            "name": "{?title}{?video}",
                            "description": "ok",
                        }
                    }
                }
            }
        )
