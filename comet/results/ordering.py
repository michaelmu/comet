"""Single-pass keys, provider alternatives, limits, and auxiliary composition."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from comet.results.config import (
    AlternativesConfig,
    AuxiliaryResultsConfig,
    LanguagesConfig,
    LimitRule,
    SortCriterion,
)
from comet.results.facts import (
    FACT_VOCABULARY,
    CacheState,
    ResultEntry,
    normalize_fact_selector,
)
from comet.results.policy import ReleasePolicy, in_scope

if TYPE_CHECKING:
    from comet.playback.presentation import ProviderOption


def _categorical_score(value, positions: Mapping[str, int]) -> int | None:
    if value is None or value == () or value == frozenset():
        return None
    if isinstance(value, (set, frozenset, tuple, list)):
        matches = [positions.get(item) for item in value]
        known = [item for item in matches if item is not None]
        return max(known) if known else None
    return positions.get(value)


def _positions(field: str, order: Sequence[str]) -> Mapping[str, int]:
    return {
        normalize_fact_selector(field, item): len(order) - index
        for index, item in enumerate(order)
    }


def _directional(value: float | str, direction: str):
    if isinstance(value, (int, float)):
        return -value if direction == "desc" else value
    if direction == "asc":
        return value
    # Invert Unicode codepoints to retain a tuple key without repeated compares.
    return tuple(-ord(character) for character in value)


def _component(value, criterion: SortCriterion):
    if value is None:
        return (1, 0)
    return (0, _directional(value, criterion.direction))


def _criterion_value(
    entry: ResultEntry,
    criterion: SortCriterion,
    *,
    languages: LanguagesConfig,
    policy: ReleasePolicy,
    now_ms: int,
    positions: Mapping[str, int] | None,
):
    facts = entry.facts
    if criterion.scope != "all" and not in_scope(criterion.scope, facts, entry):
        return 0
    key = criterion.key
    if key == "resolution":
        return _categorical_score(facts.resolution, positions or {})
    if key == "cached":
        return 1 if entry.cache_state is CacheState.CACHED else 0
    if key == "language":
        return _categorical_score(facts.languages, positions or {})
    if key == "keyword":
        rank = policy.keyword_rank(facts)
        return None if rank is None else len(policy.keyword_prefer) - rank
    if key == "preferenceRule":
        rank = policy.preference_rule_rank(entry, now_ms=now_ms)
        return None if rank is None else len(policy.prefer_rules) - rank
    if key == "rank":
        return entry.rank
    if key == "quality":
        return _categorical_score(facts.quality, positions or {})
    if key == "videoCodec":
        return _categorical_score(facts.video_codec, positions or {})
    if key == "hdr":
        return _categorical_score(facts.visual, positions or {})
    if key == "audio":
        return _categorical_score(facts.audio, positions or {})
    if key == "channels":
        return _categorical_score(facts.channels, positions or {})
    if key == "subtitles":
        return _categorical_score(facts.subtitles, positions or {})
    if key == "size":
        return entry.playback_size
    if key == "seeders":
        return facts.seeders
    if key == "age":
        return facts.published_at_ms
    if key == "provider":
        return (
            _categorical_score(entry.provider_id, positions)
            if positions is not None
            else entry.provider_position
        )
    if key == "transport":
        return _categorical_score(entry.delivery_transport, positions or {})
    if key == "source":
        return (
            _categorical_score(facts.source, positions)
            if positions is not None
            else facts.source
        )
    if key == "releaseGroup":
        return (
            _categorical_score(facts.release_group, positions)
            if positions is not None
            else facts.release_group
        )
    if key == "private":
        return int(facts.private)
    raise AssertionError(f"unknown sort key: {key}")


def sort_entries(
    entries: Sequence[ResultEntry],
    criteria: Sequence[SortCriterion],
    *,
    languages: LanguagesConfig,
    policy: ReleasePolicy,
    now_ms: int,
) -> tuple[ResultEntry, ...]:
    """Precompute one tuple per entry, then perform exactly one stable sort."""

    categorical = {
        "resolution": ("resolution", FACT_VOCABULARY["resolution"]),
        "language": ("languages", tuple(languages.preferred)),
        "quality": ("quality", FACT_VOCABULARY["quality"]),
        "videoCodec": ("videoCodec", FACT_VOCABULARY["videoCodec"]),
        "hdr": ("visual", FACT_VOCABULARY["visual"]),
        "audio": ("audio", FACT_VOCABULARY["audio"]),
        "channels": ("channels", FACT_VOCABULARY["channels"]),
        "subtitles": ("subtitles", tuple(languages.preferred)),
        "transport": ("transport", FACT_VOCABULARY["transport"]),
    }
    prepared = []
    for criterion in criteria:
        specification = categorical.get(criterion.key)
        if criterion.order is not None:
            field = (
                specification[0]
                if specification
                else ("providerId" if criterion.key == "provider" else criterion.key)
            )
            positions = _positions(field, criterion.order)
        elif specification:
            positions = _positions(*specification)
        else:
            positions = None
        prepared.append((criterion, positions))

    def key(entry: ResultEntry):
        configured = tuple(
            _component(
                _criterion_value(
                    entry,
                    criterion,
                    languages=languages,
                    policy=policy,
                    now_ms=now_ms,
                    positions=positions,
                ),
                criterion,
            )
            for criterion, positions in prepared
        )
        return (
            *configured,
            entry.release_position,
            entry.provider_position,
            entry.facts.candidate_id,
            entry.provider_id,
        )

    return tuple(sorted(entries, key=key))


@dataclass(frozen=True, slots=True)
class SelectionCounts:
    alternatives_hidden: int = 0
    limit_options_hidden: int = 0
    limit_releases_hidden: int = 0


def _best_or_all(entries: list[ResultEntry], mode: str) -> list[ResultEntry]:
    return entries if mode == "all" else entries[:1]


def reduce_alternatives(
    entries: Sequence[ResultEntry],
    config: AlternativesConfig,
) -> tuple[tuple[ResultEntry, ...], SelectionCounts]:
    """Reduce only exact candidate alternatives while preserving global order."""
    by_candidate: dict[str, list[ResultEntry]] = defaultdict(list)
    for entry in entries:
        by_candidate[entry.facts.candidate_id].append(entry)

    retained_ids: set[tuple[str, str]] = set()
    fallback_by_visible: dict[tuple[str, str], tuple[ProviderOption, ...]] = {}
    for candidate_entries in by_candidate.values():
        cached = [
            entry
            for entry in candidate_entries
            if entry.cache_state is CacheState.CACHED
        ]
        uncached = [
            entry
            for entry in candidate_entries
            if entry.cache_state is CacheState.UNCACHED
        ]
        usenet = [
            entry for entry in candidate_entries if entry.facts.transport == "usenet"
        ]
        direct = [
            entry
            for entry in candidate_entries
            if entry.provider_kind == "direct_torrent"
        ]
        visible = []
        visible.extend(_best_or_all(cached, config.cached))
        if not (cached and config.hideUncachedWhenCached):
            visible.extend(_best_or_all(uncached, config.uncached))
        visible.extend(_best_or_all(usenet, config.usenet))
        if config.direct == "always" or not cached:
            visible.extend(direct)
        visible.sort(key=lambda item: candidate_entries.index(item))
        retained_ids.update(entry.stable_id for entry in visible)

        if config.fallback:
            for transport in ("torrent", "usenet"):
                transport_visible = [
                    entry
                    for entry in visible
                    if entry.facts.transport == transport
                    and entry.provider_kind != "direct_torrent"
                ]
                hidden = [
                    entry.option
                    for entry in candidate_entries
                    if entry.facts.transport == transport
                    and entry.provider_kind != "direct_torrent"
                    and entry.stable_id not in retained_ids
                ]
                if transport_visible and hidden:
                    fallback_by_visible[transport_visible[0].stable_id] = tuple(hidden)

    retained = tuple(
        replace(entry, fallback_options=fallback_by_visible.get(entry.stable_id, ()))
        for entry in entries
        if entry.stable_id in retained_ids
    )
    return retained, SelectionCounts(alternatives_hidden=len(entries) - len(retained))


def _limit_value(entry: ResultEntry, by: str):
    if by == "total":
        return "total"
    if by == "resolution":
        return entry.facts.resolution or "unknown"
    if by == "quality":
        return entry.facts.quality or "unknown"
    if by == "provider":
        return entry.provider_id
    if by == "transport":
        return entry.delivery_transport
    if by == "source":
        return entry.facts.source or "unknown"
    if by == "releaseGroup":
        return entry.facts.release_group or "unknown"
    raise AssertionError(f"unknown limit key: {by}")


def apply_limits(
    entries: Sequence[ResultEntry],
    limits: Sequence[LimitRule],
    *,
    release_identity: Mapping[str, str] | Callable[[ResultEntry], str] | None = None,
) -> tuple[tuple[ResultEntry, ...], SelectionCounts]:
    active = tuple(rule for rule in limits if rule.max > 0)
    if not active:
        return tuple(entries), SelectionCounts()
    identity = (
        release_identity
        if callable(release_identity)
        else lambda entry: (
            release_identity.get(entry.facts.candidate_id, entry.facts.candidate_id)
            if release_identity is not None
            else entry.facts.candidate_id
        )
    )
    counts: dict[tuple[str, object], int] = defaultdict(int)
    accepted_releases: set[str] = set()
    rejected_releases: set[str] = set()
    retained = []
    hidden_options = 0
    for entry in entries:
        release_id = identity(entry)
        if release_id in rejected_releases:
            hidden_options += 1
            continue
        if release_id not in accepted_releases:
            release_rules = [rule for rule in active if rule.by != "provider"]
            if any(
                counts[(rule.by, _limit_value(entry, rule.by))] >= rule.max
                for rule in release_rules
            ):
                rejected_releases.add(release_id)
                hidden_options += 1
                continue
            accepted_releases.add(release_id)
            for rule in release_rules:
                counts[(rule.by, _limit_value(entry, rule.by))] += 1

        provider_rules = [rule for rule in active if rule.by == "provider"]
        if any(
            counts[(rule.by, _limit_value(entry, rule.by))] >= rule.max
            for rule in provider_rules
        ):
            hidden_options += 1
            continue
        for rule in provider_rules:
            counts[(rule.by, _limit_value(entry, rule.by))] += 1
        retained.append(entry)
    return tuple(retained), SelectionCounts(
        limit_options_hidden=hidden_options,
        limit_releases_hidden=len(rejected_releases),
    )


def compose_auxiliary(
    playable: Sequence[dict],
    *,
    errors: Sequence[dict],
    summary: dict | None,
    actions: Sequence[dict],
    policy: AuxiliaryResultsConfig,
) -> list[dict]:
    """Compose stable zones without treating an auxiliary row as playable."""
    top: list[dict] = []
    bottom: list[dict] = []

    def place(items: Sequence[dict], position: str) -> None:
        if position == "top":
            top.extend(items)
        elif position == "bottom":
            bottom.extend(items)

    place(errors, policy.errors)
    show_summary = summary is not None and (
        policy.filterSummary in {"top", "bottom"}
        or (policy.filterSummary == "whenEmpty" and not playable)
    )
    if show_summary:
        place(
            (summary,),
            "bottom" if policy.filterSummary == "whenEmpty" else policy.filterSummary,
        )
    place(actions, policy.debridSync)
    return [*top, *playable, *bottom]
