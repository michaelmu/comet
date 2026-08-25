import itertools
import random
from dataclasses import replace
from types import MappingProxyType

import pytest
from RTN import DefaultRanking, SettingsModel, parse

from comet.core.capabilities import CapabilityPlan, EligibleProvider
from comet.core.models import settings
from comet.core.sources import (
    LocatorKind,
    LocatorPolicy,
    NzbArtifactRef,
    ReleaseCandidate,
    ReleaseScope,
    TorrentLocator,
    TransportKind,
)
from comet.metadata.media_info import media_info_from_stremthru
from comet.playback.presentation import ProviderOption
from comet.results.config import (
    SORT_PRESETS,
    AlternativesConfig,
    AuxiliaryResultsConfig,
    LanguagesConfig,
    LimitRule,
    ResultsConfig,
    SortCriterion,
)
from comet.results.facts import CacheState, extract_release_facts, result_entry
from comet.results.formatting import (
    TemplateSyntaxError,
    compile_display,
    compile_template,
    context_from_entry,
    example_context,
)
from comet.results.migrations import migrate_configuration_document
from comet.results.ordering import (
    apply_limits,
    compose_auxiliary,
    reduce_alternatives,
    sort_entries,
)
from comet.results.pipeline import (
    finalize_results,
    prepare_releases,
    refresh_late_facts,
)
from comet.results.policy import ReleasePolicy
from comet.results.summary import render_filter_summary


def candidate(
    identifier: str,
    title: str,
    *,
    transport: TransportKind = TransportKind.BITTORRENT,
    size: int = 1_000_000,
    source: str = "Indexer",
    seeders: int | None = 10,
) -> ReleaseCandidate:
    if transport is TransportKind.BITTORRENT:
        locator = TorrentLocator(
            locator_id=f"locator-{identifier}",
            kind=LocatorKind.TORRENT,
            policy=LocatorPolicy(
                frozenset({"realdebrid", "alldebrid", "torbox", "direct_torrent"})
            ),
            info_hash=(identifier * 40)[:40],
        )
    else:
        locator = NzbArtifactRef(
            locator_id=f"locator-{identifier}",
            kind=LocatorKind.NZB_ARTIFACT,
            policy=LocatorPolicy(frozenset({"nzbdav", "altmount"})),
            artifact_sha256=(identifier * 64)[:64],
            manifest_identity="nm1:" + (identifier * 64)[:64],
        )
    return ReleaseCandidate(
        candidate_id=identifier,
        media_id="tt1234567",
        scope=ReleaseScope.MOVIE,
        transport=transport,
        title=title,
        locators=(locator,),
        size=size,
        source=source,
        parsed=parse(title),
        transport_stats={"seeders": seeders},
    )


def entry(
    release: ReleaseCandidate,
    provider_id: str,
    kind: str,
    position: int,
    *,
    cached: bool = False,
    rank: int = 0,
    release_position: int = 0,
):
    option = ProviderOption(
        release.candidate_id,
        EligibleProvider(provider_id, kind, position),
        release.locators,
        cached,
    )
    return result_entry(
        release,
        option,
        extract_release_facts(release),
        rank,
        provider_name=provider_id,
        release_position=release_position,
    )


@pytest.mark.parametrize("alias", ["3D", "Half-SBS", "Full-SBS", "HSBS"])
def test_three_d_aliases_are_one_visual_fact(alias):
    facts = extract_release_facts(candidate(alias, f"Movie.2026.1080p.{alias}.WEB-DL"))
    assert "3d" in facts.visual


def test_visual_codec_and_explicit_unknown_facts():
    dv_hdr = extract_release_facts(
        candidate("dv", "Movie.2026.2160p.WEB-DL.DV.HDR10+.AV1.10bit")
    )
    sdr = extract_release_facts(candidate("sdr", "Movie.2026.1080p.WEB-DL.x264"))
    assert {"dolbyVision", "hdr", "hdr10Plus", "10bit"} <= dv_hdr.visual
    assert dv_hdr.video_codec == "av1"
    assert sdr.visual == frozenset({"sdr"})


def test_language_mapping_runs_before_required_language():
    release = candidate("lang", "Movie.2026.1080p.WEB-DL-BiOMA")
    config = ResultsConfig.model_validate(
        {
            "filters": {
                "rules": [
                    {
                        "action": "addLanguage",
                        "language": "pt",
                        "all": [
                            {
                                "field": "releaseGroup",
                                "op": "oneOf",
                                "values": ["BiOMA"],
                            }
                        ],
                    }
                ]
            }
        }
    )
    policy = ReleasePolicy.compile(config, LanguagesConfig(required=["pt"]))
    facts = policy.enrich(extract_release_facts(release), now_ms=1)
    assert facts.languages == frozenset({"pt"})
    assert policy.evaluate_early(facts, now_ms=1) == 0


def test_keyword_modes_are_literal_and_bounded():
    release = candidate("keyword", "Movie.DTS-HD.Half-SBS.1080p")
    config = ResultsConfig.model_validate(
        {
            "filters": {
                "removeTrash": False,
                "keywords": {
                    "require": [{"value": "DTS", "mode": "word"}],
                    "exclude": [{"value": "*half?sbs*", "mode": "wildcard"}],
                },
            }
        }
    )
    policy = ReleasePolicy.compile(config, LanguagesConfig())
    facts = extract_release_facts(release)
    # DTS is not a complete token in DTS-HD, while the bounded wildcard matches.
    assert policy.evaluate_early(facts, now_ms=1) == policy.keyword_reject_id
    phrase = ResultsConfig.model_validate(
        {
            "filters": {
                "removeTrash": False,
                "keywords": {"require": [{"value": "DTS HD", "mode": "phrase"}]},
            }
        }
    )
    assert (
        ReleasePolicy.compile(phrase, LanguagesConfig()).evaluate_early(facts, now_ms=1)
        == 0
    )

    unicode_wildcard = ResultsConfig.model_validate(
        {
            "filters": {
                "removeTrash": False,
                "keywords": {
                    "exclude": [{"value": "*ＭＯＶＩＥ*", "mode": "wildcard"}]
                },
            }
        }
    )
    assert _policy_rejection(release, unicode_wildcard) != 0


def test_remove_trash_does_not_disable_language_or_resolution_policy():
    release = candidate("guard", "Movie.2026.720p.WEB-DL.FRENCH")
    config = ResultsConfig.model_validate(
        {
            "filters": {
                "removeTrash": False,
                "dimensions": {"resolution": {"only": ["2160p"]}},
            }
        }
    )
    policy = ReleasePolicy.compile(config, LanguagesConfig(required=["en"]))
    assert policy.evaluate_early(extract_release_facts(release), now_ms=1) != 0


def _policy_rejection(release, results, languages=None):
    policy = ReleasePolicy.compile(results, languages or LanguagesConfig())
    facts = policy.enrich(extract_release_facts(release), now_ms=1)
    early = policy.evaluate_early(facts, now_ms=1)
    if early:
        return early
    option = ProviderOption(
        release.candidate_id,
        EligibleProvider("direct", "direct_torrent", 0),
        release.locators,
    )
    return policy.evaluate_late(result_entry(release, option, facts, 0), now_ms=1)


def test_early_require_and_refreshed_late_languages_are_enforced():
    required_title = ResultsConfig.model_validate(
        {
            "filters": {
                "removeTrash": False,
                "rules": [
                    {
                        "action": "require",
                        "all": [
                            {"field": "title", "op": "contains", "value": "wanted"}
                        ],
                    }
                ],
            }
        }
    )
    policy = ReleasePolicy.compile(required_title, LanguagesConfig())
    assert policy.require_rules[0].phase.value == "early"
    prepared = prepare_releases(
        (candidate("miss", "Movie.2026.1080p.WEB-DL"),),
        policy=policy,
        rtn_settings=SettingsModel(),
        rtn_ranking=DefaultRanking(),
        summary_enabled=True,
        now_ms=1,
    )
    assert prepared.releases == ()

    release = candidate("language", "Movie.2026.1080p.WEB-DL")
    language_results = ResultsConfig.model_validate({"filters": {"removeTrash": False}})
    languages = LanguagesConfig(required=["fr"])
    language_policy = ReleasePolicy.compile(language_results, languages)
    prepared = prepare_releases(
        (release,),
        policy=language_policy,
        rtn_settings=SettingsModel(),
        rtn_ranking=DefaultRanking(),
        summary_enabled=True,
        now_ms=1,
    )
    assert len(prepared.releases) == 1  # unknown is deferred until file inspection
    inspected = replace(
        release,
        media_info=media_info_from_stremthru({"audio": [{"lang": "eng"}], "v": 1}),
    )
    refreshed = refresh_late_facts(
        prepared, (inspected,), policy=language_policy, now_ms=1
    )
    plan = CapabilityPlan(
        frozenset({TransportKind.BITTORRENT}),
        (),
        (EligibleProvider("direct", "direct_torrent", 0),),
        (),
    )
    pipeline = finalize_results(
        refreshed,
        capability_plan=plan,
        service_cache_status={},
        failed_provider_ids=frozenset(),
        provider_names={},
        results=language_results,
        languages=languages,
        policy=language_policy,
        now_ms=1,
    )
    assert pipeline.entries == ()


def test_real_policy_dv_container_and_bad_quality_matrix():
    dv_rule = ResultsConfig.model_validate(
        {
            "filters": {
                "removeTrash": False,
                "rules": [
                    {
                        "action": "exclude",
                        "all": [
                            {"field": "visual", "op": "oneOf", "values": ["DV"]},
                            {"field": "container", "op": "noneOf", "values": ["MP4"]},
                        ],
                    }
                ],
            }
        }
    )
    base = candidate("dv", "Movie.2026.1080p.WEB-DL.DV.mkv")
    mp4 = replace(
        base,
        locators=(replace(base.locators[0], selection_title="Movie.mp4"),),
    )
    mkv = replace(
        base,
        locators=(replace(base.locators[0], selection_title="Movie.mkv"),),
    )
    unknown = replace(
        base,
        title="Movie.2026.1080p.WEB-DL.DV.HDR",
        parsed=parse("Movie.2026.1080p.WEB-DL.DV.HDR"),
        locators=(replace(base.locators[0], selection_title=None),),
    )
    hdr = candidate("hdr", "Movie.2026.1080p.WEB-DL.HDR.mkv")
    assert _policy_rejection(mp4, dv_rule) == 0
    assert _policy_rejection(mkv, dv_rule) != 0
    assert _policy_rejection(unknown, dv_rule) != 0
    assert _policy_rejection(hdr, dv_rule) == 0
    dv_hdr = candidate("dv-hdr", "Movie.2026.1080p.WEB-DL.DV.HDR.mkv")
    assert _policy_rejection(dv_hdr, dv_rule) != 0

    quality_policy = ResultsConfig.model_validate(
        {
            "filters": {
                "removeTrash": False,
                "dimensions": {"quality": {"exclude": ["CAM", "TeleSync", "Screener"]}},
            }
        }
    )
    for label in ("CAM", "TS", "Screener"):
        release = candidate(label, f"Movie.2026.1080p.{label}")
        assert _policy_rejection(release, quality_policy) != 0


def test_selector_aliases_and_rtn_string_bitrate_are_canonical():
    results = ResultsConfig.model_validate(
        {
            "filters": {
                "removeTrash": False,
                "dimensions": {
                    "quality": {"only": ["WEB-DL"]},
                    "videoCodec": {"only": ["x265"]},
                    "visual": {"only": ["DV"]},
                    "audio": {"only": ["DD+"]},
                },
            }
        }
    )
    release = candidate("aliases", "Movie.2026.1080p.WEB-DL.DV.x265.DD+.mkv")
    assert _policy_rejection(release, results) == 0
    parsed = release.parsed.model_copy(update={"bitrate": "15mbps"})
    assert extract_release_facts(replace(release, parsed=parsed)).bitrate == 15_000_000
    parsed = release.parsed.model_copy(update={"bitrate": "15000kbps"})
    assert extract_release_facts(replace(release, parsed=parsed)).bitrate == 15_000_000

    unknown = candidate("unknown", "Movie.2026.WEB-DL")
    only_unknown = ResultsConfig.model_validate(
        {
            "filters": {
                "removeTrash": False,
                "dimensions": {"resolution": {"only": ["unknown"]}},
            }
        }
    )
    assert _policy_rejection(unknown, only_unknown) == 0
    assert _policy_rejection(release, only_unknown) != 0


def test_provider_specific_size_and_container_feed_late_policy():
    release = candidate("options", "Movie.2026.1080p.WEB-DL.DV.mkv", size=1000)
    mp4 = replace(release.locators[0], selection_title="Movie.mp4", selection_size=100)
    mkv = replace(release.locators[0], selection_title="Movie.mkv", selection_size=900)
    facts = extract_release_facts(replace(release, locators=(mp4, mkv)))
    first = result_entry(
        release,
        ProviderOption("options", EligibleProvider("a", "realdebrid", 0), (mp4,)),
        facts,
        0,
    )
    second = result_entry(
        release,
        ProviderOption("options", EligibleProvider("b", "alldebrid", 1), (mkv,)),
        facts,
        0,
    )
    assert (first.playback_size, first.container) == (100, "mp4")
    assert (second.playback_size, second.container) == (900, "mkv")
    results = ResultsConfig.model_validate(
        {
            "filters": {
                "removeTrash": False,
                "rules": [
                    {
                        "action": "exclude",
                        "all": [
                            {"field": "visual", "op": "oneOf", "values": ["DV"]},
                            {"field": "container", "op": "noneOf", "values": ["MP4"]},
                        ],
                    }
                ],
            }
        }
    )
    policy = ReleasePolicy.compile(results, LanguagesConfig())
    assert policy.evaluate_late(first, now_ms=1) == 0
    assert policy.evaluate_late(second, now_ms=1) != 0


def test_disabled_custom_policy_uses_empty_fast_path_without_diagnostics():
    releases = tuple(
        candidate(str(index), f"Movie.2026.1080p.WEB-DL-G{index}")
        for index in range(20)
    )
    config = ResultsConfig.model_validate({"filters": {"removeTrash": False}})
    policy = ReleasePolicy.compile(config, LanguagesConfig())
    assert policy.is_default_fast_path
    prepared = prepare_releases(
        releases,
        policy=policy,
        rtn_settings=SettingsModel(),
        rtn_ranking=DefaultRanking(),
        summary_enabled=False,
        now_ms=1,
    )
    assert {item.candidate.candidate_id for item in prepared.releases} == {
        item.candidate_id for item in releases
    }
    assert prepared.rejection_counts == ()


def test_guard_policy_and_selection_rejections_share_dense_collector():
    release = candidate("aggregate", "Movie.2026.1080p.WEB-DL")
    results = ResultsConfig.model_validate(
        {
            "filters": {"removeTrash": False},
            "alternatives": {"uncached": "best"},
        }
    )
    languages = LanguagesConfig()
    policy = ReleasePolicy.compile(results, languages)
    prepared = prepare_releases(
        (release,),
        policy=policy,
        rtn_settings=SettingsModel(),
        rtn_ranking=DefaultRanking(),
        summary_enabled=True,
        found_count=3,
        guard_rejection_counts=(("title", 2),),
        now_ms=1,
    )
    plan = CapabilityPlan(
        frozenset({TransportKind.BITTORRENT}),
        (),
        tuple(
            EligibleProvider(provider, kind, index)
            for index, (provider, kind) in enumerate(
                (("rd", "realdebrid"), ("ad", "alldebrid"), ("tb", "torbox"))
            )
        ),
        (),
    )
    pipeline = finalize_results(
        prepared,
        capability_plan=plan,
        service_cache_status={},
        failed_provider_ids=frozenset(),
        provider_names={},
        results=results,
        languages=languages,
        policy=policy,
        now_ms=1,
    )
    counts = {
        reason.field: count
        for reason, count in zip(policy.reasons, pipeline.rejection_counts)
        if count
    }
    assert pipeline.found_count == 3
    assert counts == {"title": 2, "alternatives": 2}


def test_disabled_policy_bypasses_per_release_evaluator_dispatch(monkeypatch):
    releases = (candidate("fast", "Movie.2026.1080p.WEB-DL"),)
    config = ResultsConfig.model_validate({"filters": {"removeTrash": False}})
    policy = ReleasePolicy.compile(config, LanguagesConfig())

    def unexpected(*_args, **_kwargs):
        raise AssertionError("disabled policy must bypass evaluator dispatch")

    monkeypatch.setattr(ReleasePolicy, "enrich", unexpected)
    monkeypatch.setattr(ReleasePolicy, "evaluate_early", unexpected)
    prepared = prepare_releases(
        releases,
        policy=policy,
        rtn_settings=SettingsModel(),
        rtn_ranking=DefaultRanking(),
        summary_enabled=False,
        now_ms=1,
    )
    assert len(prepared.releases) == 1


def test_facets_and_rule_operands_are_normalized_once_at_compile_time(monkeypatch):
    import comet.results.policy as policy_module

    config = ResultsConfig.model_validate(
        {
            "filters": {
                "removeTrash": False,
                "dimensions": {"quality": {"only": ["WEB-DL"]}},
                "rules": [
                    {
                        "action": "exclude",
                        "all": [{"field": "title", "op": "contains", "value": "CAM"}],
                    }
                ],
            }
        }
    )
    policy = ReleasePolicy.compile(config, LanguagesConfig())

    def unexpected(*_args, **_kwargs):
        raise AssertionError("matching must use precompiled normalized operands")

    monkeypatch.setattr(policy_module, "normalize_fact_selector", unexpected)
    monkeypatch.setattr(policy_module, "normalize_search_text", unexpected)
    accepted = extract_release_facts(candidate("web", "Movie.2026.1080p.WEB-DL"))
    rejected = extract_release_facts(candidate("cam", "Movie.2026.1080p.WEB-DL.CAM"))
    assert policy.evaluate_early(accepted, now_ms=1) == 0
    assert policy.evaluate_early(rejected, now_ms=1) != 0


def test_cache_is_not_applicable_to_usenet_and_direct():
    torrent = candidate("torrent", "Movie.2026.1080p.WEB-DL")
    usenet = candidate(
        "usenet",
        "Movie.2026.1080p.WEB-DL",
        transport=TransportKind.USENET,
    )
    assert (
        entry(torrent, "direct", "direct_torrent", 0).cache_state
        is CacheState.NOT_APPLICABLE
    )
    assert entry(usenet, "nzb", "nzbdav", 0).cache_state is CacheState.NOT_APPLICABLE
    assert entry(torrent, "rd", "realdebrid", 0).cache_state is CacheState.UNCACHED


def test_episode_playback_size_prefers_selected_file_and_keeps_pack_size():
    pack = candidate("pack", "Show.S01.1080p.WEB-DL", size=20_000)
    selected = replace(
        pack,
        scope=ReleaseScope.EPISODE,
        locators=(replace(pack.locators[0], selection_size=2_000),),
    )
    facts = extract_release_facts(selected)
    assert facts.playback_size == 2_000
    assert facts.release_size == 20_000

    usenet = candidate(
        "usenet-pack",
        "Show.S01.1080p.WEB-DL",
        transport=TransportKind.USENET,
        size=30_000,
    )
    selected_usenet = replace(
        usenet,
        scope=ReleaseScope.EPISODE,
        locators=(
            replace(
                usenet.locators[0],
                selection_hint_name="Show.S01E01.mkv",
                selection_hint_size=3_000,
            ),
        ),
    )
    usenet_facts = extract_release_facts(selected_usenet)
    assert usenet_facts.playback_size == 3_000
    assert usenet_facts.release_size == 30_000
    assert usenet_facts.container == "mkv"


def test_default_sort_prioritizes_cache_inside_resolution_and_languages_are_ordered():
    fr = candidate("fr", "Movie.2026.1080p.WEB-DL.FRENCH")
    en = candidate("en", "Movie.2026.1080p.WEB-DL.ENGLISH")
    values = (
        entry(en, "ad", "alldebrid", 1, cached=False, rank=100),
        entry(en, "rd", "realdebrid", 0, cached=True, rank=0),
        entry(fr, "rd", "realdebrid", 0, cached=True, rank=0),
    )
    results = ResultsConfig()
    languages = LanguagesConfig(preferred=["fr", "en"])
    policy = ReleasePolicy.compile(results, languages)
    ordered = sort_entries(
        values,
        results.sort,
        languages=languages,
        policy=policy,
        now_ms=1,
    )
    assert [(item.facts.candidate_id, item.provider_id) for item in ordered] == [
        ("fr", "rd"),
        ("en", "rd"),
        ("en", "ad"),
    ]


def test_reversed_language_preference_and_manual_preset_are_ordinary_sorting():
    assert "torrentio" not in SORT_PRESETS
    en = entry(
        candidate("en", "Movie.2026.1080p.WEB-DL.ENGLISH"),
        "rd",
        "realdebrid",
        0,
    )
    fr = entry(
        candidate("fr", "Movie.2026.1080p.WEB-DL.FRENCH"),
        "rd",
        "realdebrid",
        0,
    )
    languages = LanguagesConfig(preferred=["en", "fr"])
    descending = ResultsConfig(sort=[SortCriterion(key="language")])
    ascending = ResultsConfig(sort=[SortCriterion(key="language", direction="asc")])
    assert [
        item.facts.candidate_id
        for item in sort_entries(
            (fr, en),
            descending.sort,
            languages=languages,
            policy=ReleasePolicy.compile(descending, languages),
            now_ms=1,
        )
    ] == ["en", "fr"]
    assert [
        item.facts.candidate_id
        for item in sort_entries(
            (fr, en),
            ascending.sort,
            languages=languages,
            policy=ReleasePolicy.compile(ascending, languages),
            now_ms=1,
        )
    ] == ["fr", "en"]

    reversed_languages = LanguagesConfig(preferred=["fr", "en"])
    assert [
        item.facts.candidate_id
        for item in sort_entries(
            (en, fr),
            descending.sort,
            languages=reversed_languages,
            policy=ReleasePolicy.compile(descending, reversed_languages),
            now_ms=1,
        )
    ] == ["fr", "en"]

    preset = ResultsConfig.model_validate(
        {"sort": list(SORT_PRESETS["qualitySeeders"])}
    )
    manual = ResultsConfig.model_validate(
        {
            "sort": [
                {"key": "resolution", "direction": "desc"},
                {"key": "quality", "direction": "desc"},
                {
                    "key": "seeders",
                    "direction": "desc",
                    "scope": "needsDownload",
                },
                {"key": "size", "direction": "desc"},
            ]
        }
    )
    values = (
        entry(
            candidate("web", "Movie.2026.1080p.WEB-DL", seeders=50),
            "rd",
            "realdebrid",
            0,
        ),
        entry(
            candidate("remux", "Movie.2026.1080p.REMUX", seeders=1),
            "rd",
            "realdebrid",
            0,
        ),
    )
    for config in (preset, manual):
        assert [
            item.facts.candidate_id
            for item in sort_entries(
                values,
                config.sort,
                languages=LanguagesConfig(),
                policy=ReleasePolicy.compile(config, LanguagesConfig()),
                now_ms=1,
            )
        ] == ["remux", "web"]

    aliased_order = ResultsConfig(
        sort=[SortCriterion(key="quality", direction="desc", order=["WEB-DL", "REMUX"])]
    )
    assert [
        item.facts.candidate_id
        for item in sort_entries(
            values,
            aliased_order.sort,
            languages=LanguagesConfig(),
            policy=ReleasePolicy.compile(aliased_order, LanguagesConfig()),
            now_ms=1,
        )
    ] == ["web", "remux"]


def test_scoped_seeders_leave_out_of_scope_entries_neutral():
    usenet_a = entry(
        candidate(
            "usenet-a",
            "Movie.2026.1080p.WEB-DL",
            transport=TransportKind.USENET,
            seeders=None,
        ),
        "nzb-a",
        "nzbdav",
        0,
        release_position=0,
    )
    usenet_b = entry(
        candidate(
            "usenet-b",
            "Movie.2026.1080p.WEB-DL",
            transport=TransportKind.USENET,
            seeders=999,
        ),
        "nzb-b",
        "nzbdav",
        0,
        release_position=1,
    )
    config = ResultsConfig(sort=[SortCriterion(key="seeders", scope="needsDownload")])
    ordered = sort_entries(
        (usenet_b, usenet_a),
        config.sort,
        languages=LanguagesConfig(),
        policy=ReleasePolicy.compile(config, LanguagesConfig()),
        now_ms=1,
    )
    assert [item.facts.candidate_id for item in ordered] == [
        "usenet-a",
        "usenet-b",
    ]


def test_sort_is_independent_of_arrival_order():
    releases = [
        candidate(str(index), f"Movie.2026.1080p.WEB-DL-G{index}") for index in range(8)
    ]
    values = [
        entry(release, f"p{index}", "realdebrid", index % 2)
        for index, release in enumerate(releases)
    ]
    config = ResultsConfig()
    languages = LanguagesConfig()
    policy = ReleasePolicy.compile(config, languages)
    expected = [
        item.stable_id
        for item in sort_entries(
            values, config.sort, languages=languages, policy=policy, now_ms=1
        )
    ]
    for seed in range(10):
        shuffled = list(values)
        random.Random(seed).shuffle(shuffled)
        assert [
            item.stable_id
            for item in sort_entries(
                shuffled, config.sort, languages=languages, policy=policy, now_ms=1
            )
        ] == expected


def test_sort_ties_keep_rtn_release_order_before_stable_identifiers():
    first = candidate("z-release", "Movie.2026.1080p.WEB-DL-Z")
    second = candidate("a-release", "Movie.2026.1080p.WEB-DL-A")
    values = (
        entry(second, "provider", "realdebrid", 0, release_position=1),
        entry(first, "provider", "realdebrid", 0, release_position=0),
    )
    config = ResultsConfig(sort=[SortCriterion(key="rank")])
    languages = LanguagesConfig()
    ordered = sort_entries(
        values,
        config.sort,
        languages=languages,
        policy=ReleasePolicy.compile(config, languages),
        now_ms=1,
    )
    assert [item.facts.candidate_id for item in ordered] == ["z-release", "a-release"]

    empty = ResultsConfig(sort=[])
    ordered = sort_entries(
        tuple(reversed(values)),
        empty.sort,
        languages=languages,
        policy=ReleasePolicy.compile(empty, languages),
        now_ms=1,
    )
    assert [item.facts.candidate_id for item in ordered] == ["z-release", "a-release"]


@pytest.mark.parametrize("direction", ["asc", "desc"])
def test_unknown_values_always_sort_after_known_values(direction):
    known = candidate("known", "Movie.2026.1080p.WEB-DL")
    unknown = candidate("unknown", "Movie.2026.WEB-DL")
    values = (
        entry(unknown, "provider", "realdebrid", 0),
        entry(known, "provider", "realdebrid", 0),
    )
    config = ResultsConfig(sort=[SortCriterion(key="resolution", direction=direction)])
    languages = LanguagesConfig()
    ordered = sort_entries(
        values,
        config.sort,
        languages=languages,
        policy=ReleasePolicy.compile(config, languages),
        now_ms=1,
    )
    assert ordered[-1].facts.candidate_id == "unknown"


def test_sort_key_is_evaluated_once_per_entry_and_criterion(monkeypatch):
    from comet.results import ordering

    values = tuple(
        entry(
            candidate(str(index), f"Movie.2026.{720 + (index % 2) * 360}p.WEB-DL"),
            f"provider-{index % 3}",
            "realdebrid",
            index % 3,
            rank=index,
        )
        for index in range(100)
    )
    config = ResultsConfig()
    languages = LanguagesConfig()
    policy = ReleasePolicy.compile(config, languages)
    calls = 0
    original = ordering._criterion_value

    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(ordering, "_criterion_value", counted)
    sort_entries(values, config.sort, languages=languages, policy=policy, now_ms=1)
    assert calls == len(values) * len(config.sort)


def test_sort_builds_each_categorical_position_table_once(monkeypatch):
    from comet.results import ordering

    values = tuple(
        entry(candidate(str(index), "Movie.2026.1080p.WEB-DL"), "rd", "realdebrid", 0)
        for index in range(20)
    )
    criteria = (
        SortCriterion(key="resolution"),
        SortCriterion(key="quality", order=["WEB-DL", "REMUX"]),
        SortCriterion(key="seeders"),
    )
    calls = 0
    original = ordering._positions

    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(ordering, "_positions", counted)
    config = ResultsConfig(sort=list(criteria))
    sort_entries(
        values,
        criteria,
        languages=LanguagesConfig(),
        policy=ReleasePolicy.compile(config, LanguagesConfig()),
        now_ms=1,
    )
    assert calls == 2


def test_alternative_matrix_and_fallback_stays_on_exact_candidate():
    release = candidate("same", "Movie.2026.1080p.WEB-DL")
    other = candidate("other", "Movie.2026.1080p.WEB-DL")
    values = (
        entry(release, "rd", "realdebrid", 0, cached=True),
        entry(release, "ad", "alldebrid", 1),
        entry(release, "tb", "torbox", 2),
        entry(other, "other", "realdebrid", 3),
    )
    selected, counts = reduce_alternatives(
        values,
        AlternativesConfig(
            cached="best",
            hideUncachedWhenCached=True,
            fallback=True,
        ),
    )
    assert [item.provider_id for item in selected] == ["rd", "other"]
    assert [
        option.provider.configuration_id for option in selected[0].fallback_options
    ] == ["ad", "tb"]
    assert counts.alternatives_hidden == 2


def test_fallback_never_includes_direct_or_policy_filtered_options():
    release = candidate("same", "Movie.2026.1080p.WEB-DL")
    direct_values = (
        entry(release, "rd", "realdebrid", 0, cached=True),
        entry(release, "ad", "alldebrid", 1),
        entry(release, "direct", "direct_torrent", 2),
    )
    selected, _ = reduce_alternatives(
        direct_values,
        AlternativesConfig(
            cached="best",
            hideUncachedWhenCached=True,
            direct="unlessCached",
            fallback=True,
        ),
    )
    assert [option.provider.kind for option in selected[0].fallback_options] == [
        "alldebrid"
    ]

    results = ResultsConfig.model_validate(
        {
            "filters": {"dimensions": {"providerId": {"exclude": ["ad"]}}},
            "alternatives": {"uncached": "best", "fallback": True},
        }
    )
    languages = LanguagesConfig()
    policy = ReleasePolicy.compile(results, languages)
    prepared = prepare_releases(
        (release,),
        policy=policy,
        rtn_settings=SettingsModel(),
        rtn_ranking=DefaultRanking(),
        summary_enabled=True,
        now_ms=1,
    )
    plan = CapabilityPlan(
        frozenset({TransportKind.BITTORRENT}),
        (),
        (
            EligibleProvider("rd", "realdebrid", 0),
            EligibleProvider("ad", "alldebrid", 1),
            EligibleProvider("tb", "torbox", 2),
        ),
        (),
    )
    pipeline = finalize_results(
        prepared,
        capability_plan=plan,
        service_cache_status={},
        failed_provider_ids=frozenset(),
        provider_names={},
        results=results,
        languages=languages,
        policy=policy,
        now_ms=1,
    )
    assert [entry.provider_id for entry in pipeline.entries] == ["rd"]
    assert [
        option.provider.configuration_id
        for option in pipeline.entries[0].fallback_options
    ] == ["tb"]


@pytest.mark.parametrize(
    "cached_mode,uncached_mode", itertools.product(("all", "best"), repeat=2)
)
def test_all_cached_uncached_alternative_combinations(cached_mode, uncached_mode):
    release = candidate("same", "Movie.2026.1080p.WEB-DL")
    values = (
        entry(release, "rd", "realdebrid", 0, cached=True),
        entry(release, "pm", "premiumize", 1, cached=True),
        entry(release, "ad", "alldebrid", 2),
        entry(release, "tb", "torbox", 3),
    )
    selected, _ = reduce_alternatives(
        values,
        AlternativesConfig(cached=cached_mode, uncached=uncached_mode),
    )
    expected = (2 if cached_mode == "all" else 1) + (2 if uncached_mode == "all" else 1)
    assert len(selected) == expected


def test_best_uncached_and_usenet_keep_bounded_same_transport_fallbacks():
    torrent = candidate("torrent-best", "Movie.2026.1080p.WEB-DL")
    torrent_values = tuple(
        entry(torrent, provider, kind, index)
        for index, (provider, kind) in enumerate(
            (("rd", "realdebrid"), ("ad", "alldebrid"), ("tb", "torbox"))
        )
    )
    selected, _ = reduce_alternatives(
        torrent_values, AlternativesConfig(uncached="best", fallback=True)
    )
    assert [item.provider_id for item in selected] == ["rd"]
    assert [
        item.provider.configuration_id for item in selected[0].fallback_options
    ] == [
        "ad",
        "tb",
    ]

    usenet = candidate(
        "usenet-best",
        "Movie.2026.1080p.WEB-DL",
        transport=TransportKind.USENET,
    )
    usenet_values = (
        entry(usenet, "nzb-a", "nzbdav", 0),
        entry(usenet, "nzb-b", "altmount", 1),
    )
    selected, _ = reduce_alternatives(
        usenet_values, AlternativesConfig(usenet="best", fallback=True)
    )
    assert [item.provider_id for item in selected] == ["nzb-a"]
    assert [
        item.provider.configuration_id for item in selected[0].fallback_options
    ] == ["nzb-b"]


def test_limits_count_release_once_but_provider_lines_individually():
    first = candidate("first", "Movie.2026.1080p.WEB-DL-G1")
    second = candidate("second", "Movie.2026.1080p.WEB-DL-G2")
    values = (
        entry(first, "rd", "realdebrid", 0),
        entry(first, "ad", "alldebrid", 1),
        entry(first, "tb", "torbox", 2),
        entry(second, "rd", "realdebrid", 0),
    )
    limited, counts = apply_limits(
        values,
        (LimitRule(by="resolution", max=1),),
    )
    assert [item.facts.candidate_id for item in limited] == ["first"] * 3
    assert counts.limit_releases_hidden == 1
    provider_limited, _ = apply_limits(
        values,
        (LimitRule(by="provider", max=1),),
    )
    assert [item.provider_id for item in provider_limited].count("rd") == 1


def test_limits_apply_after_quality_sort_and_direct_transport_is_distinct():
    lower = entry(
        candidate("lower", "Movie.2026.1080p.WEB-DL", seeders=1),
        "rd",
        "realdebrid",
        0,
    )
    higher = entry(
        candidate("higher", "Movie.2026.1080p.WEB-DL", seeders=50),
        "ad",
        "alldebrid",
        1,
    )
    remux = entry(
        candidate("remux-limit", "Movie.2026.1080p.REMUX", seeders=0),
        "rd",
        "realdebrid",
        0,
    )
    config = ResultsConfig(
        sort=[
            SortCriterion(key="quality"),
            SortCriterion(key="seeders"),
        ]
    )
    ordered = sort_entries(
        (lower, higher, remux),
        config.sort,
        languages=LanguagesConfig(),
        policy=ReleasePolicy.compile(config, LanguagesConfig()),
        now_ms=1,
    )
    limited, _ = apply_limits(ordered, (LimitRule(by="quality", max=1),))
    assert [item.facts.candidate_id for item in limited] == [
        "remux-limit",
        "higher",
    ]

    release = candidate("delivery", "Movie.2026.1080p.WEB-DL")
    direct_release = candidate("delivery-direct", "Movie.2026.1080p.WEB-DL")
    debrid = entry(release, "rd", "realdebrid", 0)
    direct = entry(direct_release, "direct", "direct_torrent", 1)
    assert debrid.delivery_transport == "debridTorrent"
    assert direct.delivery_transport == "directTorrent"
    assert context_from_entry(debrid).fields["transport"] == "Torrent"
    assert context_from_entry(direct).fields["transport"] == "Direct torrent"
    transport_limited, _ = apply_limits(
        (debrid, direct), (LimitRule(by="transport", max=1),)
    )
    assert transport_limited == (debrid, direct)

    direct_only = ResultsConfig.model_validate(
        {
            "filters": {
                "removeTrash": False,
                "dimensions": {"transport": {"only": ["directTorrent"]}},
            }
        }
    )
    direct_policy = ReleasePolicy.compile(direct_only, LanguagesConfig())
    facts = extract_release_facts(direct_release)
    assert direct_policy.evaluate_early(facts, now_ms=1) == 0
    assert direct_policy.evaluate_late(direct, now_ms=1) == 0
    assert direct_policy.evaluate_late(debrid, now_ms=1) != 0

    # `torrent` names the release transport, never the delivery channel: a
    # legacy selector keeps meaning the debrid-served option it always meant.
    debrid_only = ResultsConfig.model_validate(
        {
            "filters": {
                "removeTrash": False,
                "dimensions": {"transport": {"only": ["torrent"]}},
            }
        }
    )
    debrid_policy = ReleasePolicy.compile(debrid_only, LanguagesConfig())
    assert debrid_policy.evaluate_late(debrid, now_ms=1) == 0
    assert debrid_policy.evaluate_late(direct, now_ms=1) != 0


def test_auxiliary_never_precedes_playable_by_default_and_off_is_independent():
    playable = [{"name": "play"}]
    errors = [{"name": "error"}]
    actions = [{"name": "sync"}]
    summary = {"name": "summary"}
    assert compose_auxiliary(
        playable,
        errors=errors,
        actions=actions,
        summary=summary,
        policy=AuxiliaryResultsConfig(),
    ) == [*playable, *errors, *actions]
    assert compose_auxiliary(
        [],
        errors=errors,
        actions=actions,
        summary=summary,
        policy=AuxiliaryResultsConfig(
            filterSummary="top", errors="off", debridSync="off"
        ),
    ) == [summary]


@pytest.mark.parametrize(
    ("position", "playable", "expected"),
    [
        ("off", [{"name": "play"}], ["play"]),
        ("whenEmpty", [{"name": "play"}], ["play"]),
        ("whenEmpty", [], ["summary"]),
        ("top", [{"name": "play"}], ["summary", "play"]),
        ("bottom", [{"name": "play"}], ["play", "summary"]),
    ],
)
def test_auxiliary_summary_modes(position, playable, expected):
    rendered = compose_auxiliary(
        playable,
        errors=(),
        actions=(),
        summary={"name": "summary"},
        policy=AuxiliaryResultsConfig(
            filterSummary=position, errors="off", debridSync="off"
        ),
    )
    assert [item["name"] for item in rendered] == expected


def test_templates_are_bounded_non_nested_and_cleanup_orphan_separator():
    template = compile_template("{?video}{video}{/video}{?audio} | {audio}{/audio}")
    context = example_context().fields.copy()
    context["video"] = ""
    assert template.render(MappingProxyType(context)) == "Atmos • TrueHD • 7.1"
    with pytest.raises(TemplateSyntaxError):
        compile_template("{?video}{?audio}{audio}{/audio}{/video}")
    with pytest.raises(TemplateSyntaxError):
        compile_template("{__code__}")


def test_preview_and_real_renderer_share_the_compiler(monkeypatch):
    display = ResultsConfig().display
    compiled = compile_display(display)
    assert compiled.render(example_context()).name == "[RD⚡] Comet 2160p"
    monkeypatch.setattr(settings, "COMET_CLEAN_TRACKER", True)
    release = candidate(
        "render",
        "Movie.2026.2160p.WEB-DL.DV.HEVC.PROPER.HARDCODED",
        source="Comet|Comet|Nyaa",
    )
    real = entry(release, "RD", "realdebrid", 0, cached=True)
    context = context_from_entry(real)
    rendered = compiled.render(context)
    assert rendered.name == "[RD⚡] Comet 2160p"
    assert rendered.description == (
        "📄 Movie.2026.2160p.WEB-DL.DV.HEVC.PROPER.HARDCODED\n"
        "📹 hevc • DV\n"
        "⭐ WEB-DL • PROPER\n"
        "👤 10 💾 976.6 KB 🔎 Comet|Nyaa"
    )
    assert rendered.description.splitlines()[2] == "⭐ WEB-DL • PROPER"
    assert "HARDCODED" in context.fields["quality"]
    monkeypatch.setattr(settings, "COMET_CLEAN_TRACKER", False)
    assert (
        context_from_entry(real)
        .fields["stream.defaultDescription"]
        .endswith("Comet|Comet|Nyaa")
    )
    compact = compile_display(
        ResultsConfig.model_validate({"display": {"preset": "compact"}}).display
    )
    assert compact.render(context).name.startswith("[RD⚡]")


def test_legacy_result_format_preserves_emoji_and_kodi_plain_layouts():
    migrated = migrate_configuration_document(
        {"resultFormat": ["title", "size", "languages"]}
    )
    display = ResultsConfig.model_validate(migrated["results"]).display
    renderer = compile_display(display)
    emoji = renderer.render(example_context())
    plain = renderer.render(example_context(kodi=True))
    assert emoji.name == "[RD⚡] Comet 2160p"
    assert emoji.description == (
        "📄 Example.Movie.2026.2160p.WEB-DL.DV.HDR10+.HEVC.TrueHD.Atmos\n"
        "💾 18.4 GB\n🇫🇷/🇬🇧"
    )
    assert plain.name == "[RD Cached] 2160p"
    assert plain.description == (
        "Example.Movie.2026.2160p.WEB-DL.DV.HDR10+.HEVC.TrueHD.Atmos\n"
        "Size: 18.4 GB\nLanguages: FR/EN"
    )


def test_full_pipeline_excludes_failed_provider_instead_of_calling_it_uncached():
    release = candidate("a", "Movie.2026.1080p.WEB-DL")
    results = ResultsConfig()
    languages = LanguagesConfig()
    policy = ReleasePolicy.compile(results, languages)
    prepared = prepare_releases(
        (release,),
        policy=policy,
        rtn_settings=SettingsModel(),
        rtn_ranking=DefaultRanking(),
        summary_enabled=True,
        now_ms=1,
    )
    plan = CapabilityPlan(
        frozenset({TransportKind.BITTORRENT}),
        (),
        (
            EligibleProvider("rd", "realdebrid", 0),
            EligibleProvider("ad", "alldebrid", 1),
        ),
        (),
    )
    pipeline = finalize_results(
        prepared,
        capability_plan=plan,
        service_cache_status={},
        failed_provider_ids=frozenset({"ad"}),
        provider_names={},
        results=results,
        languages=languages,
        policy=policy,
        now_ms=1,
    )
    assert [item.provider_id for item in pipeline.entries] == ["rd"]


def test_filter_summary_is_aggregate_only_and_never_echoes_rule_material():
    release = candidate(
        "safe-summary",
        "Secret.Movie.2026.1080p.WEB-DL-TOKEN",
        source="https://private.example/api?key=credential",
    )
    results = ResultsConfig.model_validate(
        {
            "filters": {
                "removeTrash": False,
                "rules": [
                    {
                        "id": "secret-rule-token",
                        "action": "exclude",
                        "all": [
                            {"field": "title", "op": "contains", "value": "secret"}
                        ],
                    }
                ],
            }
        }
    )
    languages = LanguagesConfig()
    policy = ReleasePolicy.compile(results, languages)
    prepared = prepare_releases(
        (release,),
        policy=policy,
        rtn_settings=SettingsModel(),
        rtn_ranking=DefaultRanking(),
        summary_enabled=True,
        now_ms=1,
    )
    pipeline = finalize_results(
        prepared,
        capability_plan=CapabilityPlan(frozenset(), (), (), ()),
        service_cache_status={},
        failed_provider_ids=frozenset(),
        provider_names={},
        results=results,
        languages=languages,
        policy=policy,
        now_ms=1,
    )
    rendered = render_filter_summary(pipeline, policy, kodi=False)
    serialized = repr(rendered).casefold()
    assert "secret" not in serialized
    assert "token" not in serialized
    assert "credential" not in serialized
    assert "private.example" not in serialized
