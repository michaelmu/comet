import asyncio
import time

import orjson
from RTN import ParsedData

from comet.core.capabilities import (
    CapabilityPlan,
    EligibleDiscovery,
    EligibleProvider,
)
from comet.core.execution import get_executor
from comet.core.models import database, settings
from comet.core.scrape import ScrapeContext
from comet.core.sources import (
    ReleaseCandidate,
    TransportKind,
)
from comet.discovery.manager import SearchCoordinator
from comet.discovery.models import MediaQuery
from comet.discovery.torrent_models import ScrapeRequest
from comet.discovery.torrent_registry import (
    SERVER_TORRENT_ACCOUNT_PARTITION,
    torrent_adapter_registry,
)
from comet.discovery.torrent_repository import (
    TorrentReleaseRepository,
)
from comet.observability import current_request_id
from comet.services.filtering import (
    filter_release_records,
    normalize_release_candidates,
    release_normalization_fingerprint,
)
from comet.services.torrent_manager import torrent_update_queue
from comet.utils.languages import select_indexer_titles
from comet.utils.media_ids import normalize_cache_media_ids
from comet.utils.parsing import (
    MediaScope,
    ensure_multi_language,
    load_cached_parsed,
    resolve_media_scope,
)


class TorrentResultAccumulator:
    def __init__(
        self,
        media_type: str,
        media_full_id: str,
        media_only_id: str,
        title: str,
        year: int,
        year_end: int,
        season: int,
        episode: int,
        aliases: dict,
        remove_adult_content: bool,
        is_kitsu: bool = False,
        search_episode: int | None = None,
        search_season: int | None = None,
        cache_media_ids: list[str] | None = None,
        target_air_date: str | None = None,
        reject_unknown_episode_files: bool = False,
        media_scope: MediaScope | None = None,
        cache_task_adder=None,
        summary_enabled: bool = False,
    ):
        self.media_type = media_type
        self.media_id = media_full_id
        self.media_only_id = media_only_id
        self.title = title
        self.year = year
        self.year_end = year_end
        self.season = season
        self.episode = episode
        self.search_episode = search_episode if search_episode is not None else episode
        self.search_season = search_season if search_season is not None else season
        self.media_scope = (
            resolve_media_scope(media_type, season, episode)
            if media_scope is None
            else media_scope
        )
        self.aliases = aliases
        self.remove_adult_content = remove_adult_content
        self.is_kitsu = is_kitsu
        self.cache_media_ids = normalize_cache_media_ids(
            self.media_only_id, cache_media_ids
        )
        self.target_air_date = target_air_date
        self.reject_unknown_episode_files = reject_unknown_episode_files

        self.seen_hashes = set()
        self.torrents = {}
        self.ready_to_cache = []
        self.primary_cached = False
        self.live_result_timestamp = time.time()
        self.cache_task_adder = cache_task_adder
        self.found_count = 0
        self.guard_rejection_counts: dict[str, int] | None = (
            {} if summary_enabled else None
        )
        self._counted_presentation_rejections: set[tuple[str, str, str]] | None = (
            set() if summary_enabled else None
        )

    def _reject_guard(self, reason: str, count: int = 1) -> None:
        if self.guard_rejection_counts is not None and count:
            self.guard_rejection_counts[reason] = (
                self.guard_rejection_counts.get(reason, 0) + count
            )

    def _reject_presentation(self, torrent: dict, reason: str) -> None:
        if self._counted_presentation_rejections is None:
            return
        identity = (torrent["infoHash"], torrent["title"], reason)
        if identity not in self._counted_presentation_rejections:
            self._counted_presentation_rejections.add(identity)
            self._reject_guard(reason)

    def _matches_requested_scope(
        self,
        parsed: ParsedData,
        *,
        reject_unknown_override: bool | None = None,
        scope_is_known: bool = False,
    ) -> bool:
        reject_unknown = (
            self.reject_unknown_episode_files
            if reject_unknown_override is None
            else reject_unknown_override
        )
        return self.media_scope.matches_parsed(
            parsed,
            self.search_season,
            self.search_episode,
            target_air_date=self.target_air_date,
            reject_unknown_episode_files=reject_unknown,
            scope_is_known=scope_is_known,
        )

    async def scrape_torrents(
        self,
        context: ScrapeContext,
    ):
        request = ScrapeRequest(
            media_type=self.media_type,
            media_id=self.media_id,
            media_only_id=self.media_only_id,
            title=self.title,
            year=self.year,
            year_end=self.year_end,
            season=self.search_season,
            episode=self.search_episode,
            context=context,
            search_titles=select_indexer_titles(
                self.title,
                self.aliases,
                settings.INDEXER_LANGUAGES,
                include_canonical=settings.INDEXER_INCLUDE_CANONICAL_TITLE,
                include_original=settings.INDEXER_INCLUDE_ORIGINAL_TITLE,
            ),
        )
        adapters = torrent_adapter_registry.build_adapters(request)
        source_ids = tuple(adapters)
        discovery = tuple(
            EligibleDiscovery(
                configuration_id,
                frozenset({TransportKind.BITTORRENT}),
            )
            for configuration_id in source_ids
        )
        plan = CapabilityPlan(
            frozenset({TransportKind.BITTORRENT}),
            source_ids,
            (EligibleProvider("direct_torrent", "direct_torrent", 0),),
            (),
            discovery,
        )
        branch_fingerprints = torrent_adapter_registry.branch_fingerprints(
            adapters,
            context,
        )
        discovery_result = await SearchCoordinator(
            adapters,
            database=database,
            candidate_normalizer=self._normalize_candidates,
        ).search(
            MediaQuery(
                media_id=self.media_only_id,
                media_type=self.media_type,
                season=self.search_season,
                episode=self.search_episode,
                title_aliases=request.query_titles,
                year=self.year,
                request_media_id=self.media_id,
                title=self.title,
                year_end=self.year_end,
                search_titles=request.query_titles,
                normalization_fingerprint=release_normalization_fingerprint(
                    title=self.title,
                    year=self.year,
                    year_end=self.year_end,
                    media_type=self.media_type,
                    aliases=self.aliases,
                ),
            ),
            plan,
            account_partition=SERVER_TORRENT_ACCOUNT_PARTITION,
            trace_id=current_request_id(),
            work_class=context,
            branch_fingerprints=branch_fingerprints,
        )
        await self.filter_manager(
            [
                self._candidate_scrape_result(candidate)
                for candidate in discovery_result.candidates
            ],
        )

        await self.cache_torrents(defer=context is ScrapeContext.LIVE)

        self._publish_ready_torrents(self.ready_to_cache)
        return discovery_result

    async def _normalize_candidates(
        self,
        candidates: tuple[ReleaseCandidate, ...],
    ) -> tuple[ReleaseCandidate, ...]:
        return await normalize_release_candidates(
            candidates,
            title=self.title,
            year=self.year,
            year_end=self.year_end,
            media_type=self.media_type,
            aliases=self.aliases,
            content_id=self.media_id,
        )

    def _publish_ready_torrents(self, torrents: list[dict]) -> None:
        """Expose already-filtered releases through the legacy torrent view."""
        for torrent in torrents:
            if (
                torrent.get("isPrivate")
                and not settings.INDEXER_PRIVATE_TORRENTS_ENABLED
            ):
                self._reject_presentation(torrent, "private")
                continue
            if not self._matches_requested_scope(torrent["parsed"]):
                self._reject_presentation(torrent, "episode")
                continue

            info_hash = torrent["infoHash"]
            self.torrents[info_hash] = {
                "fileIndex": torrent["fileIndex"],
                "title": torrent["title"],
                "seeders": torrent["seeders"],
                "size": torrent["size"],
                "tracker": torrent["tracker"],
                "sources": torrent["sources"],
                "parsed": torrent["parsed"],
                "updatedAt": self.live_result_timestamp,
                "isPrivate": bool(torrent.get("isPrivate")),
            }

    async def ingest_release_candidates(
        self, source_id: str, candidates: tuple[ReleaseCandidate, ...]
    ) -> None:
        """Send discovered BitTorrent candidates through the existing pipeline."""
        scrape_results = [
            self._candidate_scrape_result(candidate, source_id)
            for candidate in candidates
            if candidate.transport is TransportKind.BITTORRENT
        ]
        if not scrape_results:
            return

        ready_count = len(self.ready_to_cache)
        await self.filter_manager(scrape_results, track_diagnostics=False)
        new_ready = self.ready_to_cache[ready_count:]
        if new_ready:
            await self.cache_torrents(new_ready)
        self._publish_ready_torrents(new_ready)

    @staticmethod
    def _candidate_scrape_result(
        candidate: ReleaseCandidate,
        source_id: str = "Discovery",
    ) -> dict:
        locator = candidate.locators[0]
        seeders = candidate.transport_stats.get("seeders")
        tracker_sources = candidate.transport_stats.get("tracker_sources", ())
        return {
            "title": candidate.title,
            "infoHash": locator.info_hash,
            "fileIndex": locator.file_index,
            "seeders": seeders,
            "size": candidate.size,
            "tracker": candidate.source or source_id,
            "sources": list(tracker_sources),
            "parsed": candidate.parsed,
            "isPrivate": candidate.is_private,
        }

    async def _fetch_cached_rows(self, media_id: str):
        return await TorrentReleaseRepository(database).load_cache_rows(
            media_id,
            self.media_scope,
            self.search_season,
            self.search_episode,
        )

    async def get_cached_torrents(self):
        rows = []
        cache_row_groups = await asyncio.gather(
            *(
                self._fetch_cached_rows(cache_media_id)
                for cache_media_id in self.cache_media_ids
            )
        )
        for cache_media_id, cache_rows in zip(self.cache_media_ids, cache_row_groups):
            if cache_rows and cache_media_id == self.media_only_id:
                self.primary_cached = True
            rows.extend(cache_rows)

        if rows:
            best_rows = {}

            def row_priority(item):
                row, _parsed = item
                preferred_scope = (
                    row["episode"] is None
                    if self.media_scope.is_aggregate
                    else (
                        self.search_episode is not None
                        and row["episode"] == self.search_episode
                    )
                )
                has_file_index = row["file_index"] is not None
                has_specific_title = bool(row["title"])
                updated_at = row["updated_at"]
                return (
                    preferred_scope,
                    has_file_index,
                    has_specific_title,
                    updated_at,
                )

            for row in rows:
                parsed_data = load_cached_parsed(row["parsed_json"])
                if parsed_data is None:
                    continue
                info_hash = row["info_hash"]
                current = best_rows.get(info_hash)
                item = (row, parsed_data)
                if current is None or row_priority(item) > row_priority(current):
                    best_rows[info_hash] = item

            rows = list(best_rows.values())

        self.found_count += len(rows)

        for row, parsed_data in rows:
            is_private = bool(row.get("is_private"))
            if is_private and not settings.INDEXER_PRIVATE_TORRENTS_ENABLED:
                self._reject_guard("private")
                continue
            ensure_multi_language(parsed_data)

            if self.remove_adult_content and parsed_data.adult:
                self._reject_guard("adult")
                continue

            target_season = self.search_season
            if (
                target_season is not None
                and parsed_data.seasons
                and target_season not in parsed_data.seasons
            ):
                self._reject_guard("episode")
                continue

            reject_unknown_override = (
                True
                if self.reject_unknown_episode_files and self.search_episode is not None
                else None
            )
            if not self._matches_requested_scope(
                parsed_data,
                reject_unknown_override=reject_unknown_override,
                scope_is_known=True,
            ):
                self._reject_guard("episode")
                continue

            info_hash = row["info_hash"]
            self.torrents[info_hash] = {
                "fileIndex": row["file_index"],
                "title": row["title"],
                "seeders": row["seeders"],
                "size": row["size"],
                "tracker": row["tracker"],
                "sources": orjson.loads(row["sources_json"]),
                "parsed": parsed_data,
                "updatedAt": row["updated_at"],
                "isPrivate": is_private,
            }

    def _append_cache_file_infos(self, file_infos: list[dict], torrent: dict):
        parsed = torrent["parsed"]
        cache_seasons = parsed.seasons or [
            self.search_season if self.search_season is not None else self.season
        ]
        parsed_episodes = parsed.episodes or [None]

        if self.reject_unknown_episode_files and self.search_episode is not None:
            if not self._matches_requested_scope(parsed, reject_unknown_override=True):
                return

            cache_seasons = [self.search_season]
            parsed_episodes = [self.search_episode]

        episode = None if len(parsed_episodes) > 1 else parsed_episodes[0]
        info_hash = torrent["infoHash"]
        file_index = torrent["fileIndex"]
        title = torrent["title"]
        size = torrent["size"]
        seeders = torrent["seeders"]
        tracker = torrent["tracker"]
        sources = torrent["sources"]
        is_private = bool(torrent.get("isPrivate"))

        for season in cache_seasons:
            file_infos.append(
                {
                    "info_hash": info_hash,
                    "index": file_index,
                    "title": title,
                    "size": size,
                    "season": season,
                    "episode": episode,
                    "parsed": parsed,
                    "seeders": seeders,
                    "tracker": tracker,
                    "sources": sources,
                    "is_private": is_private,
                }
            )

    async def cache_torrents(
        self,
        torrents: list[dict] | None = None,
        *,
        defer: bool = True,
        only_missing: bool = False,
    ):
        file_infos = []
        for torrent in self.ready_to_cache if torrents is None else torrents:
            self._append_cache_file_infos(file_infos, torrent)

        if not file_infos:
            return

        if defer and self.cache_task_adder is not None:
            self.cache_task_adder(
                self._cache_file_infos,
                file_infos,
                only_missing,
            )
        else:
            await self._cache_file_infos(file_infos, only_missing)

    async def _cache_file_infos(
        self,
        file_infos: list[dict],
        only_missing: bool,
    ) -> None:
        if only_missing:
            existing = await TorrentReleaseRepository(database).existing_media_keys(
                self.media_only_id,
                tuple(dict.fromkeys(row["info_hash"] for row in file_infos)),
            )
            file_infos = [
                row
                for row in file_infos
                if (row["info_hash"], row["season"], row["episode"]) not in existing
            ]

        if file_infos:
            await torrent_update_queue.add_torrent_infos(
                file_infos,
                self.media_only_id,
            )

    async def filter_manager(
        self,
        torrents: list[dict],
        *,
        track_diagnostics: bool = True,
    ):
        if len(torrents) == 0:
            return

        new_torrents = [
            torrent
            for torrent in torrents
            if (torrent["infoHash"], torrent["title"]) not in self.seen_hashes
        ]
        self.seen_hashes.update(
            (torrent["infoHash"], torrent["title"]) for torrent in new_torrents
        )

        if not new_torrents:
            return

        if track_diagnostics:
            self.found_count += len(new_torrents)

        unparsed_torrents = []
        for torrent in new_torrents:
            parsed = torrent.get("parsed")
            if parsed is None:
                unparsed_torrents.append(torrent)
                continue
            if self.remove_adult_content and parsed.adult:
                if track_diagnostics:
                    self._reject_guard("adult")
                continue
            self.ready_to_cache.append(torrent)

        if not unparsed_torrents:
            return

        loop = asyncio.get_running_loop()
        chunk_size = 20
        chunk_counts = []
        tasks = []
        for i in range(0, len(unparsed_torrents), chunk_size):
            counts = (
                {}
                if track_diagnostics and self.guard_rejection_counts is not None
                else None
            )
            chunk_counts.append(counts)
            tasks.append(
                loop.run_in_executor(
                    get_executor(),
                    filter_release_records,
                    unparsed_torrents[i : i + chunk_size],
                    self.title,
                    self.year,
                    self.year_end,
                    self.media_type,
                    self.aliases,
                    self.remove_adult_content,
                    self.media_id,
                    counts,
                )
            )
        results = await asyncio.gather(*tasks)
        for counts in chunk_counts:
            for reason, count in (counts or {}).items():
                self._reject_guard(reason, count)
        for result in results:
            self.ready_to_cache.extend(result)
