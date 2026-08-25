"""Pure orchestration for the canonical result-selection stages."""

from __future__ import annotations

import time
from dataclasses import dataclass
from types import MappingProxyType

from comet.core.capabilities import CapabilityPlan
from comet.core.sources import TORRENT_PROVIDER_KINDS, ReleaseCandidate
from comet.playback.groups import build_presentation_groups
from comet.playback.presentation import ProviderOption, build_provider_options
from comet.results.facts import (
    ReleaseFacts,
    ResultEntry,
    extract_release_facts,
    result_entry,
)
from comet.results.ordering import (
    SelectionCounts,
    apply_limits,
    reduce_alternatives,
    sort_entries,
)
from comet.results.policy import RejectionCollector, ReleasePolicy
from comet.services.ranking import ScoredRelease, score_candidates


@dataclass(frozen=True, slots=True)
class PreparedReleases:
    releases: tuple[ScoredRelease, ...]
    found_count: int
    rejection_counts: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class PipelineResult:
    candidates: tuple[ReleaseCandidate, ...]
    options: tuple[ProviderOption, ...]
    entries: tuple[ResultEntry, ...]
    found_count: int
    rejection_counts: tuple[int, ...]
    selection_counts: SelectionCounts


def prepare_releases(
    candidates: tuple[ReleaseCandidate, ...],
    *,
    policy: ReleasePolicy,
    rtn_settings,
    rtn_ranking,
    summary_enabled: bool,
    found_count: int | None = None,
    guard_rejection_counts: tuple[tuple[str, int], ...] = (),
    now_ms: int | None = None,
) -> PreparedReleases:
    """Extract/enrich/filter early facts before provider/debrid work, then score."""
    now_ms = now_ms or int(time.time() * 1_000)
    collector = RejectionCollector(len(policy.reasons), enabled=summary_enabled)
    for field, count in guard_rejection_counts:
        collector.add_count(policy.aggregate_reject_id(field), count)
    accepted = []
    facts_by_id: dict[str, ReleaseFacts] = {}
    policy_disabled = policy.is_default_fast_path
    for candidate in candidates:
        facts = extract_release_facts(candidate)
        if not policy_disabled:
            facts = policy.enrich(facts, now_ms=now_ms)
            rejection = policy.evaluate_early(facts, now_ms=now_ms)
            if rejection:
                collector.add(rejection)
                continue
        accepted.append(candidate)
        facts_by_id[candidate.candidate_id] = facts
    releases = score_candidates(
        accepted,
        facts_by_id,
        rtn_settings,
        rtn_ranking,
    )
    return PreparedReleases(
        releases,
        found_count=len(candidates) if found_count is None else found_count,
        rejection_counts=collector.counts,
    )


def refresh_late_facts(
    prepared: PreparedReleases,
    candidates: tuple[ReleaseCandidate, ...],
    *,
    policy: ReleasePolicy,
    now_ms: int | None = None,
) -> PreparedReleases:
    """Refresh facts after existing file selection enriches a candidate."""
    now_ms = now_ms or int(time.time() * 1_000)
    current = {candidate.candidate_id: candidate for candidate in candidates}
    releases = []
    policy_disabled = policy.is_default_fast_path
    for release in prepared.releases:
        candidate = current.get(release.candidate.candidate_id, release.candidate)
        if candidate is release.candidate:
            releases.append(release)
            continue
        facts = extract_release_facts(candidate)
        if not policy_disabled:
            facts = policy.enrich(facts, now_ms=now_ms)
        releases.append(ScoredRelease(candidate, facts, release.rank))
    return PreparedReleases(
        tuple(releases), prepared.found_count, prepared.rejection_counts
    )


def _cache_options(
    options: tuple[ProviderOption, ...],
    service_cache_status,
) -> tuple[ProviderOption, ...]:
    from dataclasses import replace

    resolved = []
    for option in options:
        if (
            option.provider.kind not in TORRENT_PROVIDER_KINDS
            or option.provider.kind == "direct_torrent"
        ):
            resolved.append(option)
            continue
        info_hash = option.candidate_id.removeprefix("btih:")
        cached = service_cache_status.get(info_hash, {}).get(
            option.provider.configuration_id,
            False,
        )
        resolved.append(replace(option, cached=True) if cached else option)
    return tuple(resolved)


def _release_identities(
    candidates: tuple[ReleaseCandidate, ...],
    *,
    season_norm: int,
    episode_norm: int,
) -> MappingProxyType:
    groups = build_presentation_groups(
        candidates,
        season_norm=season_norm,
        episode_norm=episode_norm,
    )
    return MappingProxyType(
        {
            candidate.candidate_id: min(
                member.candidate_id for member in group.candidates
            )
            for group in groups
            for candidate in group.candidates
        }
    )


def finalize_results(
    prepared: PreparedReleases,
    *,
    capability_plan: CapabilityPlan,
    service_cache_status,
    failed_provider_ids: frozenset[str],
    provider_names: dict[str, str],
    results,
    languages,
    policy: ReleasePolicy,
    season_norm: int = -1,
    episode_norm: int = -1,
    now_ms: int | None = None,
) -> PipelineResult:
    """Expand, filter late, sort once, reduce alternatives, then apply limits."""
    now_ms = now_ms or int(time.time() * 1_000)
    releases_by_id = {
        release.candidate.candidate_id: release for release in prepared.releases
    }
    release_positions = {
        release.candidate.candidate_id: position
        for position, release in enumerate(prepared.releases)
    }
    candidates = tuple(release.candidate for release in prepared.releases)
    options = tuple(
        option
        for option in build_provider_options(candidates, capability_plan)
        if option.provider.configuration_id not in failed_provider_ids
    )
    options = _cache_options(options, service_cache_status)
    summary_enabled = results.auxiliary.filterSummary != "off"
    collector = RejectionCollector(len(policy.reasons), enabled=summary_enabled)
    entries = []
    policy_disabled = policy.is_default_fast_path
    for option in options:
        release = releases_by_id[option.candidate_id]
        entry = result_entry(
            release.candidate,
            option,
            release.facts,
            release.rank,
            provider_name=provider_names.get(option.provider.configuration_id),
            release_position=release_positions[option.candidate_id],
        )
        if not policy_disabled:
            rejection = policy.evaluate_late(entry, now_ms=now_ms)
            if rejection:
                collector.add(rejection)
                continue
        entries.append(entry)

    ordered = sort_entries(
        entries,
        results.sort,
        languages=languages,
        policy=policy,
        now_ms=now_ms,
    )
    alternatives, alternative_counts = reduce_alternatives(
        ordered, results.alternatives
    )
    collector.add_count(
        policy.aggregate_reject_id("alternatives"),
        alternative_counts.alternatives_hidden,
    )
    identities = _release_identities(
        tuple(entry.candidate for entry in alternatives),
        season_norm=season_norm,
        episode_norm=episode_norm,
    )
    limited, limit_counts = apply_limits(
        alternatives,
        results.limits,
        release_identity=identities,
    )
    collector.add_count(
        policy.aggregate_reject_id("limits"), limit_counts.limit_options_hidden
    )
    candidates_by_id = {candidate.candidate_id: candidate for candidate in candidates}
    visible_candidate_ids = tuple(
        dict.fromkeys(entry.facts.candidate_id for entry in limited)
    )
    return PipelineResult(
        candidates=tuple(
            candidates_by_id[candidate_id] for candidate_id in visible_candidate_ids
        ),
        options=tuple(entry.option for entry in limited),
        entries=limited,
        found_count=prepared.found_count,
        rejection_counts=tuple(
            left + right
            for left, right in zip(
                prepared.rejection_counts or (0,) * len(policy.reasons),
                collector.counts or (0,) * len(policy.reasons),
            )
        ),
        selection_counts=SelectionCounts(
            alternatives_hidden=alternative_counts.alternatives_hidden,
            limit_options_hidden=limit_counts.limit_options_hidden,
            limit_releases_hidden=limit_counts.limit_releases_hidden,
        ),
    )
