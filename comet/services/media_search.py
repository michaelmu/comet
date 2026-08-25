import asyncio
import threading
import time
from collections import defaultdict
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from enum import StrEnum
from itertools import chain
from typing import Any

from comet.core.capabilities import (
    CapabilityPlan,
    CapabilityPlanner,
    CapabilityStateSnapshot,
)
from comet.core.capability_bindings import (
    ensure_playback_capability_states,
    native_instance_credential_material,
)
from comet.core.models import database, settings
from comet.core.scrape import ScrapeContext
from comet.core.sources import ReleaseCandidate, ReleaseScope
from comet.debrid.exceptions import DebridAuthError, DebridLinkGenerationError
from comet.discovery import SearchCoordinator, build_discovery_adapters
from comet.discovery.capabilities import (
    build_discovery_branch_fingerprints,
    ensure_discovery_capability_states,
    record_discovery_capability_failure,
)
from comet.discovery.manager import DiscoveryResult
from comet.discovery.models import CandidateNormalizationResult, MediaQuery
from comet.discovery.torrent_repository import (
    TorrentReleaseRepository,
    torrent_candidate_from_runtime,
)
from comet.metadata.manager import (
    MetadataFetchStatus,
    MetadataScraper,
)
from comet.metadata.release_date import release_dates
from comet.observability import current_request_id, log, metrics
from comet.playback.presentation import (
    ProviderOption,
    issue_fallback_option_capability,
    issue_provider_option_capability,
)
from comet.playback.registry import build_playback_providers
from comet.playback.repository import RenderedCandidateIds, RenderedReleaseRepository
from comet.playback.tokens import CapabilityCodec
from comet.results.pipeline import (
    PipelineResult,
    PreparedReleases,
    finalize_results,
    prepare_releases,
    refresh_late_facts,
)
from comet.services.anime import anime_mapper
from comet.services.cache_state import CacheStateManager, mark_scope_scraped
from comet.services.debrid import DebridService, prefer_torrent_update
from comet.services.debrid_account_scraper import (
    ensure_account_snapshot_ready,
    get_account_torrents_for_media,
    schedule_account_snapshot_refresh,
)
from comet.services.filtering import (
    normalize_release_candidates_with_stats,
    release_normalization_fingerprint,
)
from comet.services.orchestration import TorrentResultAccumulator
from comet.usenet.access import NativeAccessAuthorizer
from comet.utils.http_client import http_client_manager
from comet.utils.parsing import MediaScope, parse_media_id, resolve_media_scope

BackgroundTaskAdder = Callable[..., Any]


class MediaSearchStatus(StrEnum):
    OK = "ok"
    INVALID = "invalid"
    DISABLED = "disabled"
    UNRELEASED = "unreleased"
    METADATA_UNAVAILABLE = "metadata_unavailable"
    METADATA_NOT_FOUND = "metadata_not_found"
    METADATA_UNSUPPORTED = "metadata_unsupported"
    BUSY = "busy"


@dataclass(slots=True)
class MediaSearchResult:
    status: MediaSearchStatus
    metadata: dict = field(default_factory=dict)
    aliases: dict = field(default_factory=dict)
    media_scope: MediaScope | None = None
    torrents: dict = field(default_factory=dict)
    service_cache_status: dict = field(default_factory=dict)
    debrid_errors: dict = field(default_factory=dict)
    cache_state: str = "unknown"
    media_only_id: str = ""
    search_season: int | None = None
    search_episode: int | None = None
    is_torrent_only: bool = False
    show_account_sync_trigger: bool = False
    use_account_scrape: bool = False
    candidates: tuple = ()
    discovery_diagnostics: tuple[str, ...] = ()
    provider_options: tuple[ProviderOption, ...] = ()
    rendered_candidate_ids: dict[str, RenderedCandidateIds] = field(
        default_factory=dict
    )
    provider_capabilities: dict[tuple[str, str], str] = field(default_factory=dict)
    candidate_count: int = 0
    pipeline: PipelineResult | None = None


@dataclass(slots=True)
class _DiscoveryTaskOwner:
    task: asyncio.Task[DiscoveryResult] | None = None

    def start(self, coroutine) -> asyncio.Task[DiscoveryResult]:
        self.task = asyncio.create_task(coroutine)
        return self.task

    async def close(self) -> None:
        if self.task is None:
            return
        self.task.cancel()
        await asyncio.gather(self.task, return_exceptions=True)


async def _gather_owned(*coroutines):
    tasks = [asyncio.create_task(coroutine) for coroutine in coroutines]
    try:
        return await asyncio.gather(*tasks)
    except BaseException:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise


class SearchCapacityTracker:
    """Report persistent search admission pressure without per-request noise."""

    def __init__(self, *, clock=time.monotonic, reminder_seconds: float = 900.0):
        self._clock = clock
        self._reminder_seconds = reminder_seconds
        self._lock = threading.Lock()
        self._busy = False
        self._changed_at = clock()
        self._last_emitted_at = self._changed_at
        self._suppressed_count = 0

    def observe(self, busy: bool) -> None:
        now = self._clock()
        event = None
        fields = {}
        with self._lock:
            if not busy:
                if self._busy:
                    event = "recovered"
                    fields = {
                        "duration_ms": (now - self._changed_at) * 1000,
                        "suppressed_count": self._suppressed_count,
                    }
                    self._busy = False
                    self._changed_at = now
                    self._last_emitted_at = now
                    self._suppressed_count = 0
            elif not self._busy:
                event = "degraded"
                fields = {"suppressed_count": 0}
                self._busy = True
                self._changed_at = now
                self._last_emitted_at = now
                self._suppressed_count = 0
            else:
                self._suppressed_count += 1
                if now - self._last_emitted_at >= self._reminder_seconds:
                    event = "degraded"
                    fields = {"suppressed_count": self._suppressed_count}
                    self._last_emitted_at = now
                    self._suppressed_count = 0
        if event == "degraded":
            log.warning(
                "search.capacity.degraded",
                "Search capacity is degraded",
                error_code="search_capacity",
                suppressed_count=fields["suppressed_count"],
            )
        elif event == "recovered":
            log.info(
                "search.capacity.recovered",
                "Search capacity recovered",
                duration_ms=fields["duration_ms"],
                suppressed_count=fields["suppressed_count"],
            )


_search_capacity_tracker = SearchCapacityTracker()


def _public_discovery_diagnostics(
    plan_diagnostics: tuple[str, ...],
    runtime_diagnostics: tuple[str, ...],
    *,
    has_candidates: bool,
) -> tuple[str, ...]:
    """Retain configured failures and at most one all-source outage."""
    diagnostics = dict.fromkeys(plan_diagnostics)
    if has_candidates:
        return tuple(diagnostics)
    for diagnostic in runtime_diagnostics:
        if diagnostic not in diagnostics:
            diagnostics[diagnostic] = None
            break
    return tuple(diagnostics)


def _bittorrent_enabled(config: Mapping[str, Any]) -> bool:
    """Keep legacy configurations torrent-capable while honoring v2 transport intent."""
    if config["schemaVersion"] != 2:
        return True
    return "bittorrent" in config["enabledTransports"]


def _discovery_title_aliases(
    title: str,
    aliases: Mapping[str, list[str]],
) -> tuple[str, ...]:
    return tuple(dict.fromkeys(chain((title,), chain.from_iterable(aliases.values()))))


async def _search_configured_sources(
    config: Mapping[str, Any],
    session,
    *,
    user_session=None,
    media_id: str,
    media_type: str,
    season: int | None,
    episode: int | None,
    title_aliases: tuple[str, ...] = (),
    title: str | None = None,
    aliases: dict[str, list[str]] | None = None,
    year: int | None = None,
    year_end: int | None = None,
    air_date: str | None = None,
    absolute_episode: int | None = None,
    search_scope: str | None = None,
    add_background_task: BackgroundTaskAdder | None = None,
) -> DiscoveryResult:
    """Run only adapters reachable from this request's capability plan."""
    user_session = session if user_session is None else user_session
    capability_states = None
    codec = None
    if config["schemaVersion"] == 2 and settings.COMET_CAPABILITY_SECRET:
        codec = CapabilityCodec(settings.COMET_CAPABILITY_SECRET)
        providers = build_playback_providers(
            config,
            session,
            user_session=user_session,
            database=database,
        )
        provider_states = await ensure_playback_capability_states(
            config,
            codec,
            database,
            providers,
            instance_credential_material={
                "comet_native_usenet": native_instance_credential_material(
                    settings.USENET_NATIVE_ACCESS_TOKEN,
                    settings.USENET_NATIVE_SERVERS,
                )
            },
        )
        discovery_states = await ensure_discovery_capability_states(
            config,
            codec,
            database,
            session,
            user_session=user_session,
        )
        capability_states = CapabilityStateSnapshot(provider_states, discovery_states)
    account_partition = (
        codec.configuration_partition_for_config(config) if codec is not None else None
    )

    async def record_runtime_discovery_failure(
        source_configuration_id: str,
        state: str,
        error_code: str,
        retry_after: int | None,
    ) -> None:
        if codec is None:
            return
        await record_discovery_capability_failure(
            config,
            codec,
            database,
            source_configuration_id,
            state=state,
            error_code=error_code,
            retry_after=retry_after,
        )

    adapters = build_discovery_adapters(
        config,
        session,
        user_session=user_session,
        database=database if account_partition is not None else None,
        account_partition=account_partition,
        runtime_failure_recorder=(
            record_runtime_discovery_failure if account_partition is not None else None
        ),
    )
    planner = CapabilityPlanner(
        usenet_offered=settings.USENET_ENABLED,
        native_authorizer=NativeAccessAuthorizer(settings.USENET_NATIVE_ACCESS_TOKEN),
        native_engine_enabled=settings.USENET_ENGINE_ENABLED,
        native_instance_pool_available=bool(settings.USENET_NATIVE_SERVERS),
        native_user_servers_allowed=settings.USENET_NATIVE_ALLOW_USER_SERVERS,
    )
    plan = planner.build(config, capability_states)
    branch_fingerprints = (
        {
            (
                branch.source_configuration_id,
                branch.branch_family,
            ): branch
            for branch in build_discovery_branch_fingerprints(
                config,
                codec,
                account_partition=account_partition,
            )
        }
        if codec is not None
        else None
    )
    aliases = {} if aliases is None else aliases

    async def normalize_candidates(candidates: tuple[ReleaseCandidate, ...]):
        return await normalize_release_candidates_with_stats(
            candidates,
            title=title,
            year=year,
            year_end=year_end,
            media_type=media_type,
            aliases=aliases,
            content_id=media_id,
            collect_rejections=(
                config["_resultsModel"].auxiliary.filterSummary != "off"
            ),
        )

    normalization_fingerprint = (
        release_normalization_fingerprint(
            title=title,
            year=year,
            year_end=year_end,
            media_type=media_type,
            aliases=aliases,
        )
        if title is not None
        else None
    )
    result = await SearchCoordinator(
        adapters,
        database=database if account_partition is not None else None,
        background_task_adder=add_background_task,
        candidate_normalizer=(normalize_candidates if title is not None else None),
    ).search(
        MediaQuery(
            media_id,
            media_type,
            season,
            episode,
            title_aliases=title_aliases,
            title=title,
            year=year,
            year_end=year_end,
            air_date=air_date,
            absolute_episode=absolute_episode,
            search_scope=search_scope,
            normalization_fingerprint=normalization_fingerprint,
        ),
        plan,
        account_partition=account_partition,
        trace_id=current_request_id(),
        branch_fingerprints=branch_fingerprints,
    )
    diagnostics = _public_discovery_diagnostics(
        plan.diagnostics,
        result.diagnostics,
        has_candidates=bool(result.candidates),
    )
    if diagnostics == result.diagnostics:
        return result
    return replace(result, diagnostics=diagnostics)


async def _normalize_discovery_candidates(
    candidates: tuple,
    *,
    title: str,
    year: int | None,
    year_end: int | None,
    media_type: str,
    aliases: dict[str, list[str]],
    content_id: str,
    remove_adult_content: bool,
    found_count: int | None = None,
    rejection_counts: tuple[tuple[str, int], ...] = (),
    collect_rejections: bool,
) -> CandidateNormalizationResult:
    """Apply non-bypassable correctness guards before result policy."""
    total = len(candidates) if found_count is None else found_count
    counts = dict(rejection_counts) if collect_rejections else None
    if not candidates:
        return CandidateNormalizationResult((), total, tuple((counts or {}).items()))
    if not settings.INDEXER_PRIVATE_TORRENTS_ENABLED:
        if counts is not None:
            count = sum(candidate.is_private for candidate in candidates)
            if count:
                counts["private"] = counts.get("private", 0) + count
        candidates = tuple(
            candidate for candidate in candidates if not candidate.is_private
        )
        if not candidates:
            return CandidateNormalizationResult(
                (), total, tuple((counts or {}).items())
            )
    legacy_candidates = tuple(
        candidate for candidate in candidates if candidate.parsed is None
    )
    if legacy_candidates:
        normalized = await normalize_release_candidates_with_stats(
            legacy_candidates,
            title=title,
            year=year,
            year_end=year_end,
            media_type=media_type,
            aliases=aliases,
            content_id=content_id,
            collect_rejections=collect_rejections,
        )
        if counts is not None:
            for reason, count in normalized.rejection_counts:
                counts[reason] = counts.get(reason, 0) + count
        candidates = (
            tuple(candidate for candidate in candidates if candidate.parsed is not None)
            + normalized.candidates
        )
    if remove_adult_content:
        if counts is not None:
            count = sum(candidate.parsed.adult for candidate in candidates)
            if count:
                counts["adult"] = counts.get("adult", 0) + count
        candidates = tuple(
            candidate for candidate in candidates if not candidate.parsed.adult
        )
    return CandidateNormalizationResult(
        candidates,
        total,
        tuple((reason, count) for reason, count in (counts or {}).items() if count),
    )


def _coalesce_private_torrent_candidates(candidates: tuple) -> tuple:
    private_ids = {
        candidate.candidate_id
        for candidate in candidates
        if getattr(candidate, "is_private", False)
    }
    if not private_ids:
        return candidates
    return tuple(
        replace(candidate, is_private=True)
        if candidate.candidate_id in private_ids and not candidate.is_private
        else candidate
        for candidate in candidates
    )


async def _apply_private_torrent_result_policy(torrents: dict) -> int:
    if not torrents:
        return 0
    private_hashes = await TorrentReleaseRepository(database).private_hashes(
        tuple(torrents)
    )
    if settings.INDEXER_PRIVATE_TORRENTS_ENABLED:
        for info_hash in private_hashes:
            torrents[info_hash]["isPrivate"] = True
        return 0
    else:
        for info_hash in private_hashes:
            del torrents[info_hash]
        return len(private_hashes)


async def _persist_rendered_candidates(
    candidates: tuple,
    owner_configuration_partition: bytes,
) -> dict[str, RenderedCandidateIds]:
    return await RenderedReleaseRepository(database).persist(
        candidates,
        owner_configuration_partition=owner_configuration_partition,
    )


def _issue_provider_capabilities(
    codec: CapabilityCodec,
    partition: bytes,
    entries: tuple,
    persisted_candidates: Mapping[str, RenderedCandidateIds],
    *,
    media_type: str,
    season: int | None,
    episode: int | None,
) -> dict[tuple[str, str], str]:
    selection_intent = [0] if media_type == "movie" else [1, season or 0, episode or 0]
    capabilities = {}
    for entry in entries:
        option = entry.option
        if option.provider.kind in {"direct_torrent", "stremio_nntp"}:
            continue
        persisted = persisted_candidates.get(option.candidate_id)
        if persisted is None:
            continue
        fallback_options = tuple(
            candidate
            for candidate in entry.fallback_options
            if candidate.provider.kind not in {"direct_torrent", "stremio_nntp"}
        )[:2]
        chain = (option, *fallback_options)
        if len(chain) > 1:
            capability = issue_fallback_option_capability(
                codec,
                partition=partition,
                options=chain,
                persisted=persisted,
                transport=(
                    "bittorrent" if entry.facts.transport == "torrent" else "usenet"
                ),
                selection_intent=selection_intent,
                client="stremio",
            )
        else:
            capability = issue_provider_option_capability(
                codec,
                partition=partition,
                option=option,
                persisted=persisted,
                selection_intent=selection_intent,
                client="stremio",
            )
        capabilities[(option.candidate_id, option.provider.configuration_id)] = (
            capability
        )
    return capabilities


async def _prepare_provider_view(
    config: Mapping[str, Any],
    prepared: PreparedReleases,
    capability_plan: CapabilityPlan,
    service_cache_status: Mapping[str, Mapping[str, bool]],
    *,
    failed_provider_ids: frozenset[str] = frozenset(),
    media_type: str,
    season: int | None,
    episode: int | None,
    season_norm: int,
    episode_norm: int,
) -> tuple[
    PipelineResult,
    dict[str, RenderedCandidateIds],
    dict[tuple[str, str], str],
]:
    """Persist and expose one provider-expanded mixed candidate view."""
    provider_names = {
        entry["configurationId"]: entry.get("displayName") or entry["kind"]
        for entry in config.get("playbackProviders") or ()
    }
    provider_names.update(
        {
            entry.get("configurationId", entry["service"]): entry["service"]
            for entry in config["_debridEntries"]
        }
    )
    provider_names.setdefault("direct_torrent", "Torrent")
    pipeline = finalize_results(
        prepared,
        capability_plan=capability_plan,
        service_cache_status=service_cache_status,
        failed_provider_ids=failed_provider_ids,
        provider_names=provider_names,
        results=config["_resultsModel"],
        languages=config["_languagesModel"],
        policy=config["_releasePolicy"],
        season_norm=season_norm,
        episode_norm=episode_norm,
    )
    rendered_candidate_ids = {}
    provider_capabilities = {}
    if (
        config["schemaVersion"] == 2
        and pipeline.candidates
        and settings.COMET_CAPABILITY_SECRET
    ):
        codec = CapabilityCodec(settings.COMET_CAPABILITY_SECRET)
        partition = codec.configuration_partition_for_config(config)
        rendered_candidate_ids = await _persist_rendered_candidates(
            pipeline.candidates,
            partition,
        )
        provider_capabilities = _issue_provider_capabilities(
            codec,
            partition,
            pipeline.entries,
            rendered_candidate_ids,
            media_type=media_type,
            season=season,
            episode=episode,
        )
    return (
        pipeline,
        rendered_candidate_ids,
        provider_capabilities,
    )


async def _prepare_discovery_only_view(
    config: Mapping[str, Any],
    discovery_result: DiscoveryResult,
    *,
    content_id: str,
    title: str,
    year: int | None,
    year_end: int | None,
    aliases: dict[str, list[str]],
    media_type: str,
    remove_adult_content: bool,
    season: int | None,
    episode: int | None,
    season_norm: int,
    episode_norm: int,
):
    """Build the independent Usenet view when the torrent branch cannot proceed."""
    normalized = await _normalize_discovery_candidates(
        _coalesce_private_torrent_candidates(discovery_result.candidates),
        title=title,
        year=year,
        year_end=year_end,
        media_type=media_type,
        aliases=aliases,
        content_id=content_id,
        remove_adult_content=remove_adult_content,
        found_count=discovery_result.found_count,
        rejection_counts=discovery_result.rejection_counts,
        collect_rejections=(config["_resultsModel"].auxiliary.filterSummary != "off"),
    )
    if isinstance(normalized, tuple):
        normalized = CandidateNormalizationResult(
            normalized,
            discovery_result.found_count or len(normalized),
            discovery_result.rejection_counts,
        )
    prepared = prepare_releases(
        normalized.candidates,
        policy=config["_releasePolicy"],
        rtn_settings=config["rtnSettings"],
        rtn_ranking=config["rtnRanking"],
        summary_enabled=config["_resultsModel"].auxiliary.filterSummary != "off",
        found_count=normalized.found_count,
        guard_rejection_counts=normalized.rejection_counts,
    )
    return await _prepare_provider_view(
        config,
        prepared,
        discovery_result.capability_plan,
        {},
        media_type=media_type,
        season=season,
        episode=episode,
        season_norm=season_norm,
        episode_norm=episode_norm,
    )


def episode_matching_policy(
    media_type: str,
    media_only_id: str,
    search_season: int | None,
    search_episode: int | None,
    *,
    has_debrid: bool,
    enable_torrent: bool,
) -> bool:
    is_imdb_episode_request = (
        media_type == "series"
        and search_season is not None
        and search_episode is not None
        and media_only_id.startswith("tt")
    )
    allow_debrid_season_packs = (
        is_imdb_episode_request and has_debrid and not enable_torrent
    )
    return is_imdb_episode_request and not allow_debrid_season_packs


def merge_service_cache_status(target: dict, incoming: dict):
    for info_hash, service_map in incoming.items():
        cache_map = target.setdefault(info_hash, {})
        for service, is_cached in service_map.items():
            if is_cached:
                cache_map[service] = True
            elif service not in cache_map:
                cache_map[service] = False


async def _mark_scope_scraped_if_populated(media_id: str, torrents: dict) -> None:
    if torrents:
        await mark_scope_scraped(media_id)


def group_debrid_entries_by_service(
    debrid_entries: list,
) -> list[tuple[str, str, list]]:
    """Keep legacy failover groups while isolating every stable v2 binding."""
    service_entries = {}
    seen_credentials = set()
    for entry in debrid_entries:
        service = entry["service"]
        credential = (service, entry["apiKey"])
        configuration_id = entry.get("configurationId")
        if configuration_id is None and credential in seen_credentials:
            continue
        seen_credentials.add(credential)
        key = service if configuration_id is None else configuration_id
        service_entries.setdefault((key, service), []).append(entry)
    return [
        (key, service, entries) for (key, service), entries in service_entries.items()
    ]


def _log_debrid_check(
    event: str,
    message: str,
    *,
    content_id: str | None,
    provider_key: str,
    service: str,
    requested_count: int,
    candidate_count: int,
    cache_state: str | None = None,
    error_code: str | None = None,
    exc: BaseException | None = None,
) -> None:
    fields = {
        "debrid_service": service,
        "requested_count": requested_count,
        "candidate_count": candidate_count,
    }
    if content_id:
        fields["content_id"] = content_id
    if provider_key != service:
        fields["provider_name"] = provider_key
    if cache_state is not None:
        fields["cache_state"] = cache_state
    if error_code is not None:
        log.warning(
            event,
            message,
            error_code=error_code,
            exc=exc,
            **fields,
        )
    else:
        log.info(event, message, **fields)


def select_debrid_refresh_hashes(
    current_hashes: set[str],
    initial_hashes: set[str],
    verified_cache_status: dict,
    *,
    had_cached_torrents: bool,
    use_account_scrape: bool,
) -> set[str]:
    if not current_hashes:
        return set()

    verified_count = sum(
        any(service_map.values())
        for info_hash, service_map in verified_cache_status.items()
        if info_hash in current_hashes
    )
    requires_full_refresh = (
        (not had_cached_torrents and not use_account_scrape)
        or verified_count == 0
        or (verified_count / len(current_hashes)) < settings.DEBRID_CACHE_CHECK_RATIO
    )
    return current_hashes if requires_full_refresh else current_hashes - initial_hashes


async def background_scrape(
    torrent_manager: TorrentResultAccumulator,
    media_id: str,
    debrid_entries: list,
    ip: str,
    session,
):
    await torrent_manager.scrape_torrents(ScrapeContext.BACKGROUND)

    if debrid_entries and torrent_manager.torrents:
        await get_and_cache_multi_service_availability(
            session,
            debrid_entries,
            torrent_manager.torrents,
            torrent_manager.media_id,
            torrent_manager.media_only_id,
            torrent_manager.search_season,
            torrent_manager.search_episode,
            torrent_manager.media_scope,
            ip,
            target_air_date=torrent_manager.target_air_date,
        )

    await _mark_scope_scraped_if_populated(media_id, torrent_manager.torrents)


async def check_multi_service_availability(
    debrid_entries: list,
    torrents: dict,
    season: int | None,
    episode: int | None,
    media_scope: MediaScope,
    *,
    content_id: str | None = None,
):
    service_cache_status = defaultdict(dict)
    info_hashes = list(torrents)
    if not info_hashes or not debrid_entries:
        return service_cache_status

    async def check_service(entry):
        service = entry["service"]
        debrid_instance = DebridService(service, entry["apiKey"], "")
        (
            cached_hashes,
            torrent_updates,
        ) = await debrid_instance.check_existing_availability(
            info_hashes, season, episode, media_scope, torrents
        )
        return cached_hashes, torrent_updates

    service_groups = group_debrid_entries_by_service(debrid_entries)
    results = await _gather_owned(
        *(check_service(entries[0]) for _key, _service, entries in service_groups)
    )

    merged_updates = {}
    for (provider_key, service, _entries), result in zip(service_groups, results):
        cached_hashes, torrent_updates = result
        _log_debrid_check(
            "debrid.cache.checked",
            "Debrid cache checked",
            content_id=content_id,
            provider_key=provider_key,
            service=service,
            requested_count=len(info_hashes),
            candidate_count=len(cached_hashes),
            cache_state="hit" if cached_hashes else "miss",
        )
        for info_hash, update in torrent_updates.items():
            merged_updates[info_hash] = prefer_torrent_update(
                merged_updates.get(info_hash), update
            )
        for info_hash in cached_hashes:
            service_cache_status[info_hash][provider_key] = True

    for info_hash, update in merged_updates.items():
        torrents[info_hash].update(update)

    return service_cache_status


async def get_and_cache_multi_service_availability(
    session,
    debrid_entries: list,
    torrents: dict,
    media_id: str,
    media_only_id: str,
    season: int | None,
    episode: int | None,
    media_scope: MediaScope,
    ip: str,
    target_air_date: str | None = None,
    known_cache_status: dict | None = None,
    add_background_task: BackgroundTaskAdder | None = None,
):
    service_cache_status = defaultdict(dict)
    errors = {}
    info_hashes = list(torrents)

    if not info_hashes or not debrid_entries:
        return service_cache_status, errors

    seeders_map = {
        info_hash: torrents[info_hash]["seeders"] for info_hash in info_hashes
    }
    tracker_map = {
        info_hash: torrents[info_hash]["tracker"] for info_hash in info_hashes
    }
    sources_map = {
        info_hash: torrents[info_hash]["sources"] for info_hash in info_hashes
    }
    service_groups = group_debrid_entries_by_service(debrid_entries)
    if known_cache_status is None:
        known_cache_status = {}

    async def check_service(provider_key, service, entries):
        service_info_hashes = [
            info_hash
            for info_hash in info_hashes
            if not known_cache_status.get(info_hash, {}).get(provider_key, False)
        ]
        if not service_info_hashes:
            return set(), {}, None

        auth_error = None
        for entry in entries:
            try:
                debrid_instance = DebridService(service, entry["apiKey"], ip)
                (
                    cached_hashes,
                    torrent_updates,
                ) = await debrid_instance.get_and_cache_availability(
                    session,
                    service_info_hashes,
                    seeders_map,
                    tracker_map,
                    sources_map,
                    torrents,
                    media_id,
                    media_only_id,
                    season,
                    episode,
                    media_scope,
                    target_air_date=target_air_date,
                    add_background_task=add_background_task,
                )
                return cached_hashes, torrent_updates, None
            except DebridAuthError as error:
                if auth_error is None:
                    auth_error = error
            except DebridLinkGenerationError as error:
                return None, None, error

        return None, None, auth_error

    results = await _gather_owned(
        *(
            check_service(provider_key, service, entries)
            for provider_key, service, entries in service_groups
        )
    )

    merged_updates = {}
    for (provider_key, service, _entries), result in zip(service_groups, results):
        cache_map, torrent_updates, error = result
        if error:
            _log_debrid_check(
                "debrid.availability.checked",
                "Debrid availability checked",
                content_id=media_id,
                provider_key=provider_key,
                service=service,
                requested_count=len(info_hashes),
                candidate_count=0,
                error_code=(
                    "credentials_rejected"
                    if isinstance(error, DebridAuthError)
                    else "dependency_failure"
                ),
                exc=error,
            )
            if isinstance(error, DebridAuthError):
                errors[provider_key] = error
            continue
        _log_debrid_check(
            "debrid.availability.checked",
            "Debrid availability checked",
            content_id=media_id,
            provider_key=provider_key,
            service=service,
            requested_count=len(info_hashes),
            candidate_count=len(cache_map),
        )

        for info_hash, update in torrent_updates.items():
            merged_updates[info_hash] = prefer_torrent_update(
                merged_updates.get(info_hash), update
            )

        for info_hash in cache_map:
            service_cache_status[info_hash][provider_key] = True

    for info_hash, update in merged_updates.items():
        torrents[info_hash].update(update)

    return service_cache_status, errors


async def search_media(
    media_type: str,
    media_id: str,
    config: Mapping[str, Any],
    ip: str,
    add_background_task: BackgroundTaskAdder,
    *,
    client_type: str = "stremio",
) -> MediaSearchResult:
    started_at = time.monotonic_ns()
    discovery_owner = _DiscoveryTaskOwner()
    log.info(
        "search.accepted",
        "Media search accepted",
        content_id=media_id,
        media_type=media_type,
        **({"client_type": client_type} if client_type != "stremio" else {}),
    )
    try:
        result = await _search_media(
            media_type,
            media_id,
            config,
            ip,
            add_background_task,
            discovery_owner=discovery_owner,
        )
        busy = result.status == MediaSearchStatus.BUSY
        if busy:
            metrics.observe_search_rejection("busy")
        _search_capacity_tracker.observe(busy)
        if result.status == MediaSearchStatus.OK:
            outcome = "ok"
        elif result.status == MediaSearchStatus.METADATA_UNAVAILABLE:
            outcome = "failed"
        elif (
            result.status == MediaSearchStatus.METADATA_UNSUPPORTED
            or result.status in {MediaSearchStatus.INVALID, MediaSearchStatus.BUSY}
        ):
            outcome = "rejected"
        else:
            outcome = "skipped"
        duration_ms = (time.monotonic_ns() - started_at) / 1_000_000
        completion_fields = {
            "content_id": media_id,
            "candidate_count": result.candidate_count,
            "duration_ms": duration_ms,
        }
        if result.status == MediaSearchStatus.METADATA_UNAVAILABLE:
            log.terminal(
                "search.completed",
                "Media search completed",
                outcome=outcome,
                error_code="metadata_unavailable",
                **completion_fields,
            )
        else:
            log.terminal(
                "search.completed",
                "Media search completed",
                outcome=outcome,
                **completion_fields,
            )
        return result
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        log.terminal(
            "search.completed",
            "Media search completed",
            outcome="failed",
            content_id=media_id,
            candidate_count=0,
            duration_ms=(time.monotonic_ns() - started_at) / 1_000_000,
            error_code="search_failure",
            exc=exc,
        )
        raise
    finally:
        await discovery_owner.close()


async def _search_media(
    media_type: str,
    media_id: str,
    config: Mapping[str, Any],
    ip: str,
    add_background_task: BackgroundTaskAdder,
    *,
    discovery_owner: _DiscoveryTaskOwner,
) -> MediaSearchResult:
    try:
        media_only_id, season, episode = parse_media_id(media_type, media_id)
    except ValueError:
        return MediaSearchResult(MediaSearchStatus.INVALID)
    media_scope = resolve_media_scope(media_type, season, episode)

    debrid_entries = config["_debridEntries"]
    bittorrent_enabled = _bittorrent_enabled(config)
    enable_torrent = bool(config["_enableTorrent"] and bittorrent_enabled)
    scrape_debrid_account_torrents = config["scrapeDebridAccountTorrents"]
    if not bittorrent_enabled:
        debrid_entries = []
    use_account_scrape = bool(debrid_entries and scrape_debrid_account_torrents)
    is_torrent_only = enable_torrent and not debrid_entries

    if settings.DISABLE_TORRENT_STREAMS and is_torrent_only:
        return MediaSearchResult(
            MediaSearchStatus.DISABLED,
            is_torrent_only=is_torrent_only,
            use_account_scrape=use_account_scrape,
        )

    session = await http_client_manager.get_session()
    user_session = await http_client_manager.get_user_session()
    metadata_scraper = MetadataScraper(session)
    is_kitsu = media_id.startswith("kitsu:")
    release_lookup_enabled = settings.DIGITAL_RELEASE_FILTER or (
        bittorrent_enabled
        and settings.LIVE_TORRENT_CACHE_TTL >= 0
        and settings.LIVE_TORRENT_CACHE_RECENT_TTL >= 0
    )
    release_info = None
    if release_lookup_enabled and not is_kitsu:
        metadata_result, release_info = await _gather_owned(
            metadata_scraper.fetch_metadata_and_aliases(
                media_type, media_id, media_only_id, season, episode
            ),
            release_dates.resolve(
                session,
                media_type,
                media_only_id,
                season,
                episode,
            ),
        )
    else:
        metadata_result = await metadata_scraper.fetch_metadata_and_aliases(
            media_type, media_id, media_only_id, season, episode
        )
    metadata = metadata_result.metadata
    aliases = metadata_result.aliases
    metadata_status = metadata_result.status
    if metadata is None:
        status = {
            MetadataFetchStatus.NOT_FOUND: MediaSearchStatus.METADATA_NOT_FOUND,
            MetadataFetchStatus.UNSUPPORTED: MediaSearchStatus.METADATA_UNSUPPORTED,
            MetadataFetchStatus.UNAVAILABLE: MediaSearchStatus.METADATA_UNAVAILABLE,
        }[metadata_status]
        return MediaSearchResult(
            status,
            media_scope=media_scope,
            media_only_id=media_only_id,
            search_season=season,
            search_episode=episode,
            is_torrent_only=is_torrent_only,
            use_account_scrape=use_account_scrape,
        )

    title = metadata["title"]
    year = metadata["year"]
    year_end = metadata["year_end"]
    season = metadata["season"]
    episode = metadata["episode"]

    search_episode = episode
    search_season = season

    if is_kitsu:
        kitsu_mapping = anime_mapper.get_kitsu_episode_mapping(media_only_id)
        if kitsu_mapping:
            from_episode = kitsu_mapping.get("from_episode")
            from_season = kitsu_mapping.get("from_season")
            if from_season is not None and from_season != season:
                search_season = from_season
            if episode is not None and from_episode is not None:
                new_episode = from_episode + episode - 1
                if new_episode != episode:
                    search_episode = new_episode

    anime_mapping_loaded = anime_mapper.is_loaded()
    mapped_imdb_id = (
        await anime_mapper.get_imdb_from_kitsu(media_only_id)
        if is_kitsu and anime_mapping_loaded
        else None
    )
    if release_lookup_enabled and is_kitsu:
        release_info = await release_dates.resolve(
            session,
            media_type,
            mapped_imdb_id,
            search_season,
            search_episode,
        )
    if (
        settings.DIGITAL_RELEASE_FILTER
        and release_info is not None
        and release_info.timestamp > time.time()
    ):
        return MediaSearchResult(
            MediaSearchStatus.UNRELEASED,
            metadata=metadata,
            aliases=aliases,
            media_scope=media_scope,
            media_only_id=media_only_id,
            search_season=search_season,
            search_episode=search_episode,
            is_torrent_only=is_torrent_only,
            use_account_scrape=use_account_scrape,
        )
    target_air_date = (
        release_info.date
        if media_type == "series" and release_info is not None
        else None
    )

    presentation_scope = (
        "anime_episode"
        if is_kitsu
        else {
            MediaScope.MOVIE: "movie",
            MediaScope.EPISODE: "episode",
            MediaScope.SEASON: "season_pack",
            MediaScope.SERIES: "series_pack",
        }[media_scope]
    )
    presentation_season = search_season if search_season is not None else -1
    presentation_episode = search_episode if search_episode is not None else -1

    # Discovery is transport-neutral and starts alongside the legacy torrent
    # path.  A capability plan makes this a no-op for legacy and unconfigured
    # profiles, so it cannot fan out to an ineligible service.
    discovery_task = discovery_owner.start(
        _search_configured_sources(
            config,
            session,
            user_session=user_session,
            media_id=media_only_id,
            media_type=media_type,
            season=search_season,
            episode=search_episode,
            title_aliases=_discovery_title_aliases(title, aliases),
            title=title,
            aliases=aliases,
            year=year,
            year_end=year_end,
            air_date=target_air_date,
            absolute_episode=search_episode if is_kitsu else None,
            search_scope=presentation_scope,
            add_background_task=add_background_task,
        )
    )
    remove_adult_content = settings.REMOVE_ADULT_CONTENT

    # Metadata remains useful to every transport, but the legacy torrent
    # manager owns cache/scraper/debrid side effects and must not run for a
    # Usenet-only profile.
    if not bittorrent_enabled:
        discovery_result = await discovery_task
        (
            pipeline,
            rendered_candidate_ids,
            provider_capabilities,
        ) = await _prepare_discovery_only_view(
            config,
            discovery_result,
            content_id=media_id,
            title=title,
            year=year,
            year_end=year_end,
            aliases=aliases,
            media_type=media_type,
            remove_adult_content=remove_adult_content,
            season=search_season,
            episode=search_episode,
            season_norm=presentation_season,
            episode_norm=presentation_episode,
        )
        return MediaSearchResult(
            MediaSearchStatus.OK,
            metadata=metadata,
            aliases=aliases,
            media_scope=media_scope,
            cache_state="unknown",
            media_only_id=media_only_id,
            search_season=search_season,
            search_episode=search_episode,
            is_torrent_only=False,
            use_account_scrape=False,
            candidates=pipeline.candidates,
            discovery_diagnostics=discovery_result.diagnostics,
            provider_options=pipeline.options,
            rendered_candidate_ids=rendered_candidate_ids,
            provider_capabilities=provider_capabilities,
            candidate_count=len(pipeline.candidates),
            pipeline=pipeline,
        )

    cache_media_ids = [media_only_id]
    if anime_mapping_loaded:
        if is_kitsu:
            if mapped_imdb_id:
                cache_media_ids.append(mapped_imdb_id)
        elif anime_mapper.is_anime_content(media_id, media_only_id):
            kitsu_ids = anime_mapper.get_kitsu_ids_from_imdb(media_only_id)
            if kitsu_ids:
                cache_media_ids.extend(kitsu_ids)
            kitsu_id = await anime_mapper.get_kitsu_from_imdb(media_only_id)
            if kitsu_id:
                cache_media_ids.append(kitsu_id)

    reject_unknown_episode_files = episode_matching_policy(
        media_type,
        media_only_id,
        search_season,
        search_episode,
        has_debrid=bool(debrid_entries),
        enable_torrent=enable_torrent,
    )
    results_model = config.get("_resultsModel")
    summary_enabled = (
        getattr(getattr(results_model, "auxiliary", None), "filterSummary", "off")
        != "off"
    )
    torrent_manager = TorrentResultAccumulator(
        media_type,
        media_id,
        media_only_id,
        title,
        year,
        year_end,
        season,
        episode,
        aliases,
        remove_adult_content,
        is_kitsu=is_kitsu,
        search_episode=search_episode,
        search_season=search_season,
        cache_media_ids=cache_media_ids,
        target_air_date=target_air_date,
        reject_unknown_episode_files=reject_unknown_episode_files,
        media_scope=media_scope,
        cache_task_adder=add_background_task,
        summary_enabled=summary_enabled,
    )

    await torrent_manager.get_cached_torrents()
    torrent_count = len(torrent_manager.torrents)
    cache_state = "hit" if torrent_count else "miss"
    metrics.observe_torrent_cache(media_type, cache_state, torrent_count)
    log.info(
        "scrape.cache.checked",
        "Torrent cache checked",
        content_id=media_id,
        cache_state=cache_state,
        candidate_count=torrent_count,
    )
    initial_info_hashes = set(torrent_manager.torrents)

    cache_manager = CacheStateManager(
        media_id,
        None if release_info is None else release_info.timestamp,
    )
    cache_result = await cache_manager.check_and_decide(torrent_count)
    force_scrape_now = not torrent_manager.primary_cached
    account_snapshot_ready = False
    torrent_discovery_inflight = False
    if cache_result.should_scrape_background and not force_scrape_now:
        add_background_task(
            background_scrape,
            torrent_manager,
            media_id,
            debrid_entries,
            ip,
            session,
        )

    if cache_result.should_scrape_now or force_scrape_now:
        if use_account_scrape:
            torrent_discovery_result, _ = await _gather_owned(
                torrent_manager.scrape_torrents(ScrapeContext.LIVE),
                ensure_account_snapshot_ready(debrid_entries, ip),
            )
            account_snapshot_ready = True
        else:
            torrent_discovery_result = await torrent_manager.scrape_torrents(
                ScrapeContext.LIVE
            )
        torrent_discovery_inflight = torrent_discovery_result.inflight
        await _mark_scope_scraped_if_populated(media_id, torrent_manager.torrents)

    discovery_result = await discovery_task
    discovery_candidates = _coalesce_private_torrent_candidates(
        discovery_result.candidates
    )
    normalized_discovery = await _normalize_discovery_candidates(
        discovery_candidates,
        title=title,
        year=year,
        year_end=year_end,
        media_type=media_type,
        aliases=aliases,
        content_id=media_id,
        remove_adult_content=remove_adult_content,
        found_count=discovery_result.found_count,
        rejection_counts=discovery_result.rejection_counts,
        collect_rejections=summary_enabled,
    )
    if isinstance(normalized_discovery, tuple):
        normalized_discovery = CandidateNormalizationResult(
            normalized_discovery,
            discovery_result.found_count or len(normalized_discovery),
            discovery_result.rejection_counts,
        )
    candidates = normalized_discovery.candidates
    await torrent_manager.ingest_release_candidates(
        "configured-discovery", discovery_candidates
    )

    service_cache_status = defaultdict(dict)
    verified_service_cache_status = defaultdict(dict)
    if use_account_scrape:
        if not account_snapshot_ready:
            await ensure_account_snapshot_ready(debrid_entries, ip)
        schedule_account_snapshot_refresh(add_background_task, debrid_entries, ip)
        account_torrents = await get_account_torrents_for_media(
            debrid_entries,
            media_type,
            media_scope,
            title,
            year,
            year_end,
            search_season,
            search_episode,
            aliases,
            remove_adult_content,
            target_air_date=target_air_date,
            reject_unknown_episode_files=reject_unknown_episode_files,
        )

        await torrent_manager.cache_torrents(
            [
                {"infoHash": info_hash, **torrent}
                for info_hash, torrent in account_torrents.items()
            ],
            only_missing=True,
        )

        for info_hash, account_torrent in account_torrents.items():
            existing_torrent = torrent_manager.torrents.get(info_hash)
            if existing_torrent is None:
                torrent_manager.torrents[info_hash] = account_torrent
                continue
            if (
                existing_torrent.get("fileIndex") is None
                and account_torrent["fileIndex"] is not None
            ):
                existing_torrent["fileIndex"] = account_torrent["fileIndex"]
            if (
                existing_torrent.get("size") is None
                and account_torrent["size"] is not None
            ):
                existing_torrent["size"] = account_torrent["size"]
            existing_parsed = existing_torrent.get("parsed")
            if existing_parsed is None or existing_parsed.resolution == "unknown":
                existing_torrent["parsed"] = account_torrent["parsed"]

    private_torrents_rejected = await _apply_private_torrent_result_policy(
        torrent_manager.torrents
    )
    torrent_manager._reject_guard("private", private_torrents_rejected)

    torrent_candidates = tuple(
        torrent_candidate_from_runtime(
            info_hash,
            torrent,
            media_id=media_only_id,
            scope=ReleaseScope(presentation_scope),
            season_norm=presentation_season,
            episode_norm=presentation_episode,
        )
        for info_hash, torrent in sorted(torrent_manager.torrents.items())
    )
    torrent_ids = {candidate.candidate_id for candidate in torrent_candidates}
    combined_candidates = torrent_candidates + tuple(
        candidate
        for candidate in candidates
        if candidate.candidate_id not in torrent_ids
    )
    guard_counts = normalized_discovery.rejection_counts
    torrent_guard_counts = getattr(torrent_manager, "guard_rejection_counts", None)
    if summary_enabled and isinstance(torrent_guard_counts, dict):
        merged_counts = dict(guard_counts)
        for reason, count in torrent_guard_counts.items():
            merged_counts[reason] = merged_counts.get(reason, 0) + count
        guard_counts = tuple(merged_counts.items())
    configured_torrent_ids = {
        candidate_id
        for candidate in discovery_candidates
        if (candidate_id := getattr(candidate, "candidate_id", "")).startswith("btih:")
    }
    legacy_survivors = sum(
        candidate.candidate_id not in configured_torrent_ids
        for candidate in torrent_candidates
    )
    torrent_found_count = getattr(torrent_manager, "found_count", 0)
    if type(torrent_found_count) is not int:
        torrent_found_count = legacy_survivors
    prepared = prepare_releases(
        combined_candidates,
        policy=config["_releasePolicy"],
        rtn_settings=config["rtnSettings"],
        rtn_ranking=config["rtnRanking"],
        summary_enabled=summary_enabled,
        found_count=(
            max(
                torrent_found_count,
                legacy_survivors + private_torrents_rejected,
            )
            + normalized_discovery.found_count
        ),
        guard_rejection_counts=guard_counts,
    )
    accepted_candidate_ids = {
        release.candidate.candidate_id for release in prepared.releases
    }
    torrent_manager.torrents = {
        info_hash: torrent
        for info_hash, torrent in torrent_manager.torrents.items()
        if f"btih:{info_hash}" in accepted_candidate_ids
    }

    if debrid_entries:
        existing_service_cache_status = await check_multi_service_availability(
            debrid_entries,
            torrent_manager.torrents,
            search_season,
            search_episode,
            media_scope,
            content_id=media_id,
        )
        merge_service_cache_status(service_cache_status, existing_service_cache_status)
        merge_service_cache_status(
            verified_service_cache_status, existing_service_cache_status
        )
    elif enable_torrent:
        await DebridService.apply_cached_availability_any_service(
            list(torrent_manager.torrents),
            search_season,
            search_episode,
            media_scope,
            torrent_manager.torrents,
        )

    current_info_hashes = set(torrent_manager.torrents)
    debrid_refresh_hashes = select_debrid_refresh_hashes(
        current_info_hashes,
        initial_info_hashes,
        verified_service_cache_status,
        had_cached_torrents=cache_result.has_cached_torrents,
        use_account_scrape=use_account_scrape,
    )

    debrid_errors = {}
    if debrid_entries and debrid_refresh_hashes:
        torrents_to_check = {
            info_hash: torrent
            for info_hash, torrent in torrent_manager.torrents.items()
            if info_hash in debrid_refresh_hashes
        }
        (
            fresh_service_cache_status,
            debrid_errors,
        ) = await get_and_cache_multi_service_availability(
            session,
            debrid_entries,
            torrents_to_check,
            media_id,
            media_only_id,
            search_season,
            search_episode,
            media_scope,
            ip,
            target_air_date=target_air_date,
            known_cache_status=service_cache_status,
            add_background_task=add_background_task,
        )
        merge_service_cache_status(service_cache_status, fresh_service_cache_status)

    refreshed_torrents = tuple(
        torrent_candidate_from_runtime(
            info_hash,
            torrent,
            media_id=media_only_id,
            scope=ReleaseScope(presentation_scope),
            season_norm=presentation_season,
            episode_norm=presentation_episode,
        )
        for info_hash, torrent in sorted(torrent_manager.torrents.items())
    )
    refreshed_ids = {candidate.candidate_id for candidate in refreshed_torrents}
    refreshed_candidates = refreshed_torrents + tuple(
        candidate
        for candidate in candidates
        if candidate.candidate_id not in refreshed_ids
    )
    prepared = refresh_late_facts(
        prepared,
        refreshed_candidates,
        policy=config["_releasePolicy"],
    )
    (
        pipeline,
        rendered_candidate_ids,
        provider_capabilities,
    ) = await _prepare_provider_view(
        config,
        prepared,
        discovery_result.capability_plan,
        service_cache_status,
        failed_provider_ids=frozenset(debrid_errors),
        media_type=media_type,
        season=search_season,
        episode=search_episode,
        season_norm=presentation_season,
        episode_norm=presentation_episode,
    )
    candidates = pipeline.candidates
    provider_options = pipeline.options
    visible_info_hashes = list(
        dict.fromkeys(
            entry.facts.candidate_id.removeprefix("btih:")
            for entry in pipeline.entries
            if entry.facts.candidate_id.startswith("btih:")
        )
    )

    return MediaSearchResult(
        (
            MediaSearchStatus.BUSY
            if torrent_discovery_inflight
            and not visible_info_hashes
            and not provider_options
            else MediaSearchStatus.OK
        ),
        metadata=metadata,
        aliases=aliases,
        media_scope=media_scope,
        torrents=torrent_manager.torrents,
        service_cache_status=service_cache_status,
        debrid_errors=debrid_errors,
        cache_state=cache_state,
        media_only_id=media_only_id,
        search_season=search_season,
        search_episode=search_episode,
        is_torrent_only=is_torrent_only,
        show_account_sync_trigger=use_account_scrape,
        use_account_scrape=use_account_scrape,
        candidates=candidates,
        discovery_diagnostics=discovery_result.diagnostics,
        provider_options=provider_options,
        rendered_candidate_ids=rendered_candidate_ids,
        provider_capabilities=provider_capabilities,
        candidate_count=(len(candidates)),
        pipeline=pipeline,
    )
