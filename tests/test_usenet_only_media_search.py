import asyncio
import base64
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from comet.core.capabilities import CapabilityPlan, EligibleProvider
from comet.core.capability_states import EffectiveCapabilityState
from comet.discovery.manager import DiscoveryResult
from comet.metadata.manager import MetadataFetchResult, MetadataFetchStatus
from comet.metadata.release_date import ReleaseInfo
from comet.playback.presentation import ProviderOption
from comet.playback.repository import RenderedCandidateIds
from comet.playback.tokens import CapabilityCodec
from comet.results.ordering import SelectionCounts
from comet.results.pipeline import PipelineResult
from comet.services.cache_state import (
    CacheCheckResult,
    CacheState,
    ScrapeDecision,
)
from comet.services.media_search import (
    MediaSearchStatus,
    SearchCapacityTracker,
    _discovery_title_aliases,
    _issue_provider_capabilities,
    _public_discovery_diagnostics,
    _search_configured_sources,
    search_media,
)

_EMPTY_PLAN = CapabilityPlan(frozenset(), (), (), ())


def _pipeline(candidates=(), options=()) -> PipelineResult:
    return PipelineResult(
        tuple(candidates),
        tuple(options),
        (),
        len(candidates),
        (),
        SelectionCounts(),
    )


def _metadata_result(metadata: dict) -> MetadataFetchResult:
    return MetadataFetchResult(MetadataFetchStatus.OK, metadata, {})


class UsenetOnlyMediaSearchTests(unittest.IsolatedAsyncioTestCase):
    def test_hidden_alternatives_are_issued_as_one_signed_fallback_chain(self):
        candidate_key = "candidate"
        committed_candidate = str(uuid.uuid4())
        committed_locator = str(uuid.uuid4())
        locator = SimpleNamespace(locator_id="locator")
        providers = tuple(
            EligibleProvider(str(uuid.uuid4()), kind, index)
            for index, kind in enumerate(("torbox_usenet", "nzbdav", "easynews"))
        )
        options = tuple(
            ProviderOption(candidate_key, provider, (locator,))
            for provider in providers
        )
        entry = SimpleNamespace(
            option=options[0],
            fallback_options=options[1:],
            facts=SimpleNamespace(transport="usenet"),
        )
        codec = CapabilityCodec(
            base64.urlsafe_b64encode(b"a" * 32).decode().rstrip("=")
        )
        partition = codec.configuration_partition(b"config")

        capabilities = _issue_provider_capabilities(
            codec,
            partition,
            (entry,),
            {
                candidate_key: RenderedCandidateIds(
                    committed_candidate, {"locator": committed_locator}
                )
            },
            media_type="movie",
            season=None,
            episode=None,
        )
        token = capabilities[(candidate_key, providers[0].configuration_id)]
        intent = codec.decode_fallback_playback_intent(token, partition=partition)

        self.assertEqual(intent.candidate_id, committed_candidate)
        self.assertEqual(len(intent.options), 3)
        self.assertEqual(
            tuple(option.provider_configuration_id for option in intent.options),
            tuple(provider.configuration_id for provider in providers),
        )

    async def test_known_future_release_stops_before_discovery(self):
        config = {
            "schemaVersion": 2,
            "enabledTransports": ("usenet",),
            "_debridEntries": [],
            "_enableTorrent": False,
            "scrapeDebridAccountTorrents": False,
        }
        metadata = {
            "title": "Upcoming",
            "year": 2027,
            "year_end": None,
            "season": None,
            "episode": None,
        }
        with (
            patch(
                "comet.services.media_search.http_client_manager.get_session",
                new=AsyncMock(return_value=object()),
            ),
            patch(
                "comet.services.media_search.MetadataScraper.fetch_metadata_and_aliases",
                new=AsyncMock(return_value=_metadata_result(metadata)),
            ),
            patch(
                "comet.services.media_search.release_dates.resolve",
                new=AsyncMock(return_value=ReleaseInfo("2027-01-01", 2_000)),
            ),
            patch("comet.services.media_search.time.time", return_value=1_000),
            patch(
                "comet.services.media_search._search_configured_sources",
                new=AsyncMock(),
            ) as discovery,
            patch(
                "comet.services.media_search.settings.DIGITAL_RELEASE_FILTER",
                True,
            ),
        ):
            result = await search_media(
                "movie", "tt1234567", config, "", lambda *_args, **_kwargs: None
            )

        self.assertEqual(result.status, MediaSearchStatus.UNRELEASED)
        discovery.assert_not_awaited()

    def test_discovery_aliases_use_normalized_metadata_without_revalidation(self):
        long_title = "A" * 300
        aliases = {"lang:fr": [long_title, *(str(index) for index in range(70))]}

        result = _discovery_title_aliases("Original", aliases)

        self.assertEqual(result[:2], ("Original", long_title))
        self.assertEqual(len(result), 72)

    def test_capacity_pressure_is_transition_only_with_recovery(self):
        now = [0.0]
        tracker = SearchCapacityTracker(
            clock=lambda: now[0],
            reminder_seconds=900,
        )
        with (
            patch("comet.services.media_search.log.warning") as warning,
            patch("comet.services.media_search.log.info") as info,
        ):
            tracker.observe(True)
            self.assertEqual(warning.call_count, 1)
            for _ in range(100):
                tracker.observe(True)
            self.assertEqual(warning.call_count, 1)
            now[0] = 901
            tracker.observe(True)
            self.assertEqual(warning.call_count, 2)
            tracker.observe(False)
            self.assertEqual(info.call_count, 1)
            self.assertEqual(
                info.call_args.args[:2],
                ("search.capacity.recovered", "Search capacity recovered"),
            )

    def test_public_diagnostics_keep_configuration_failures_without_leaking_ids(self):
        self.assertEqual(
            _public_discovery_diagnostics(
                ("TorBox: authentication failed",),
                ("Discovery is temporarily unavailable",),
                has_candidates=False,
            ),
            (
                "TorBox: authentication failed",
                "Discovery is temporarily unavailable",
            ),
        )
        self.assertEqual(
            _public_discovery_diagnostics(
                ("TorBox: temporarily unreachable",),
                ("Discovery source is temporarily unavailable",),
                has_candidates=True,
            ),
            ("TorBox: temporarily unreachable",),
        )

    async def test_configured_usenet_discovery_uses_a_profile_partition(self):
        config = {
            "schemaVersion": 2,
            "enabledTransports": ("usenet",),
            "playbackProviders": [
                {
                    "configurationId": "torbox",
                    "displayName": "TorBox",
                    "kind": "torbox_usenet",
                    "enabled": True,
                }
            ],
            "discoverySources": [
                {
                    "configurationId": "search",
                    "kind": "newznab",
                    "enabled": True,
                }
            ],
        }
        secret = base64.urlsafe_b64encode(b"x" * 32).decode().rstrip("=")
        expected = DiscoveryResult((), (), _EMPTY_PLAN)
        with (
            patch("comet.services.media_search.settings.USENET_ENABLED", True),
            patch(
                "comet.services.media_search.settings.COMET_CAPABILITY_SECRET", secret
            ),
            patch(
                "comet.services.media_search.ensure_playback_capability_states",
                new=AsyncMock(return_value={}),
            ) as preflight,
            patch(
                "comet.services.media_search.ensure_discovery_capability_states",
                new=AsyncMock(return_value={}),
            ),
            patch(
                "comet.services.media_search.build_discovery_adapters", return_value={}
            ),
            patch(
                "comet.services.media_search.SearchCoordinator.search",
                new=AsyncMock(return_value=expected),
            ) as search,
        ):
            result = await _search_configured_sources(
                config,
                object(),
                media_id="tt1234567",
                media_type="movie",
                season=None,
                episode=None,
            )

        self.assertEqual(result.candidates, expected.candidates)
        self.assertIs(result.capability_plan, expected.capability_plan)
        self.assertEqual(result.diagnostics, ("TorBox: validation required",))
        self.assertEqual(len(search.await_args.kwargs["account_partition"]), 32)
        preflight.assert_awaited_once()

    async def test_failed_playback_preflight_prunes_discovery_before_fanout(self):
        config = {
            "schemaVersion": 2,
            "enabledTransports": ("usenet",),
            "playbackProviders": [
                {
                    "configurationId": "torbox",
                    "displayName": "TorBox",
                    "kind": "torbox_usenet",
                    "enabled": True,
                }
            ],
            "discoverySources": [
                {
                    "configurationId": "search",
                    "kind": "newznab",
                    "enabled": True,
                }
            ],
        }
        secret = base64.urlsafe_b64encode(b"x" * 32).decode().rstrip("=")
        failed = EffectiveCapabilityState(
            "auth_failed",
            False,
            False,
            False,
            "api_key_rejected",
        )
        with (
            patch("comet.services.media_search.settings.USENET_ENABLED", True),
            patch(
                "comet.services.media_search.settings.COMET_CAPABILITY_SECRET",
                secret,
            ),
            patch(
                "comet.services.media_search.ensure_playback_capability_states",
                new=AsyncMock(return_value={"torbox": failed}),
            ),
            patch(
                "comet.services.media_search.ensure_discovery_capability_states",
                new=AsyncMock(return_value={}),
            ),
            patch(
                "comet.services.media_search.build_discovery_adapters",
                return_value={"search": object()},
            ),
            patch(
                "comet.services.media_search.SearchCoordinator.search",
                new=AsyncMock(return_value=DiscoveryResult((), (), _EMPTY_PLAN)),
            ) as search,
        ):
            result = await _search_configured_sources(
                config,
                object(),
                media_id="tt1234567",
                media_type="movie",
                season=None,
                episode=None,
            )

        plan = search.await_args.args[1]
        self.assertEqual(plan.providers, ())
        self.assertEqual(plan.discovery_source_ids, ())
        self.assertEqual(plan.diagnostics, ("TorBox: authentication failed",))
        self.assertEqual(result.diagnostics, plan.diagnostics)

    async def test_failed_discovery_preflight_prunes_adapter_before_fanout(self):
        config = {
            "schemaVersion": 2,
            "enabledTransports": ("usenet",),
            "playbackProviders": [
                {
                    "configurationId": "torbox",
                    "displayName": "TorBox",
                    "kind": "torbox_usenet",
                    "enabled": True,
                }
            ],
            "discoverySources": [
                {
                    "configurationId": "search",
                    "kind": "newznab",
                    "enabled": True,
                }
            ],
        }
        secret = base64.urlsafe_b64encode(b"x" * 32).decode().rstrip("=")
        valid = EffectiveCapabilityState("valid", True, False, False)
        failed = EffectiveCapabilityState(
            "auth_failed",
            False,
            False,
            False,
            "api_key_rejected",
        )
        adapter = object()
        with (
            patch("comet.services.media_search.settings.USENET_ENABLED", True),
            patch(
                "comet.services.media_search.settings.COMET_CAPABILITY_SECRET",
                secret,
            ),
            patch(
                "comet.services.media_search.ensure_playback_capability_states",
                new=AsyncMock(return_value={"torbox": valid}),
            ),
            patch(
                "comet.services.media_search.ensure_discovery_capability_states",
                new=AsyncMock(return_value={"search": failed}),
            ),
            patch(
                "comet.services.media_search.build_discovery_adapters",
                return_value={"search": adapter},
            ),
            patch(
                "comet.services.media_search.SearchCoordinator.search",
                new=AsyncMock(return_value=DiscoveryResult((), (), _EMPTY_PLAN)),
            ) as search,
        ):
            result = await _search_configured_sources(
                config,
                object(),
                media_id="tt1234567",
                media_type="movie",
                season=None,
                episode=None,
            )

        plan = search.await_args.args[1]
        self.assertEqual([item.kind for item in plan.providers], ["torbox_usenet"])
        self.assertEqual(plan.discovery_source_ids, ())
        self.assertEqual(
            plan.diagnostics,
            ("newznab: authentication failed",),
        )
        self.assertEqual(result.diagnostics, plan.diagnostics)

    async def test_usenet_only_profile_never_enters_torrent_manager(self):
        config = {
            "schemaVersion": 2,
            "enabledTransports": ("usenet",),
            "_debridEntries": [{"service": "realdebrid", "apiKey": "secret"}],
            "_enableTorrent": True,
            "scrapeDebridAccountTorrents": True,
            "cachedOnly": False,
            "maxResultsPerResolution": 0,
        }
        metadata = {
            "title": "Private request title",
            "year": 2024,
            "year_end": None,
            "season": None,
            "episode": None,
        }
        with (
            patch(
                "comet.services.media_search.http_client_manager.get_session",
                new=AsyncMock(return_value=object()),
            ),
            patch(
                "comet.services.media_search.MetadataScraper.fetch_metadata_and_aliases",
                new=AsyncMock(return_value=_metadata_result(metadata)),
            ),
            patch(
                "comet.services.media_search._prepare_discovery_only_view",
                new=AsyncMock(return_value=(_pipeline(), {}, {})),
            ),
            patch(
                "comet.services.media_search.TorrentResultAccumulator"
            ) as torrent_manager,
            patch("comet.services.media_search.CacheStateManager") as cache_manager,
            patch(
                "comet.services.media_search.ensure_account_snapshot_ready",
                new=AsyncMock(),
            ) as ensure_snapshot,
            patch(
                "comet.services.media_search.schedule_account_snapshot_refresh",
                new=AsyncMock(),
            ) as refresh_snapshot,
            patch(
                "comet.services.media_search.get_account_torrents_for_media",
                new=AsyncMock(),
            ) as account_torrents,
            patch(
                "comet.services.media_search.check_multi_service_availability",
                new=AsyncMock(),
            ) as check_availability,
            patch(
                "comet.services.media_search.get_and_cache_multi_service_availability",
                new=AsyncMock(),
            ) as cache_availability,
            patch(
                "comet.services.media_search.DebridService."
                "apply_cached_availability_any_service",
                new=AsyncMock(),
            ) as apply_availability,
        ):
            result = await search_media(
                "movie", "tt1234567", config, "", lambda *_args, **_kwargs: None
            )

        self.assertEqual(result.status, MediaSearchStatus.OK)
        self.assertEqual(result.torrents, {})
        torrent_manager.assert_not_called()
        cache_manager.assert_not_called()
        ensure_snapshot.assert_not_awaited()
        refresh_snapshot.assert_not_awaited()
        account_torrents.assert_not_awaited()
        check_availability.assert_not_awaited()
        cache_availability.assert_not_awaited()
        apply_availability.assert_not_awaited()

    async def test_usenet_only_profile_returns_configured_discovery_candidates(self):
        config = {
            "schemaVersion": 2,
            "enabledTransports": ("usenet",),
            "_debridEntries": [],
            "_enableTorrent": False,
            "scrapeDebridAccountTorrents": False,
        }
        metadata = {
            "title": "Example",
            "year": 2024,
            "year_end": None,
            "season": None,
            "episode": None,
        }
        discovery = DiscoveryResult(
            ("usenet-candidate",),
            ("safe diagnostic",),
            _EMPTY_PLAN,
        )
        with (
            patch(
                "comet.services.media_search.http_client_manager.get_session",
                new=AsyncMock(return_value=object()),
            ),
            patch(
                "comet.services.media_search.MetadataScraper.fetch_metadata_and_aliases",
                new=AsyncMock(return_value=_metadata_result(metadata)),
            ),
            patch(
                "comet.services.media_search._search_configured_sources",
                new=AsyncMock(return_value=discovery),
            ) as search_sources,
            patch(
                "comet.services.media_search._prepare_discovery_only_view",
                new=AsyncMock(
                    return_value=(
                        _pipeline(discovery.candidates, ("playable-option",)),
                        {},
                        {},
                    )
                ),
            ),
            patch(
                "comet.services.media_search.settings.COMET_CAPABILITY_SECRET",
                "",
            ),
            patch(
                "comet.services.media_search.TorrentResultAccumulator"
            ) as torrent_manager,
        ):
            result = await search_media(
                "movie", "tt1234567", config, "", lambda *_args, **_kwargs: None
            )

        self.assertEqual(result.candidates, ("usenet-candidate",))
        self.assertEqual(result.provider_options, ("playable-option",))
        self.assertEqual(result.discovery_diagnostics, ("safe diagnostic",))
        search_sources.assert_awaited_once()
        torrent_manager.assert_not_called()

    async def test_inflight_torrent_branch_returns_independent_usenet_results(self):
        config = {
            "schemaVersion": 2,
            "enabledTransports": ("bittorrent", "usenet"),
            "_debridEntries": [],
            "_enableTorrent": True,
            "scrapeDebridAccountTorrents": False,
            "cachedOnly": False,
            "removeTrash": True,
            "rtnSettings": None,
            "rtnRanking": None,
            "maxSize": 0,
            "_releasePolicy": object(),
            "_resultsModel": MagicMock(auxiliary=MagicMock(filterSummary="off")),
        }
        metadata = {
            "title": "Example",
            "year": 2024,
            "year_end": None,
            "season": None,
            "episode": None,
        }
        discovery = DiscoveryResult(
            ("raw-usenet",),
            (),
            _EMPTY_PLAN,
        )
        manager = MagicMock()
        manager.get_cached_torrents = AsyncMock()
        manager.scrape_torrents = AsyncMock(
            return_value=DiscoveryResult((), (), _EMPTY_PLAN, inflight=True)
        )
        manager.ingest_release_candidates = AsyncMock()
        manager.primary_cached = False
        manager.torrents = {}
        filtered_candidate = MagicMock()
        filtered_candidate.candidate_id = "usenet:test"
        prepared = MagicMock()
        prepared.releases = (MagicMock(candidate=filtered_candidate),)
        cache_manager = MagicMock()
        cache_manager.check_and_decide = AsyncMock(
            return_value=CacheCheckResult(
                CacheState.EMPTY,
                ScrapeDecision.SCRAPE_FOREGROUND,
                False,
            )
        )
        with (
            patch(
                "comet.services.media_search.http_client_manager.get_session",
                new=AsyncMock(return_value=object()),
            ),
            patch(
                "comet.services.media_search.MetadataScraper."
                "fetch_metadata_and_aliases",
                new=AsyncMock(return_value=_metadata_result(metadata)),
            ),
            patch(
                "comet.services.media_search._search_configured_sources",
                new=AsyncMock(return_value=discovery),
            ),
            patch(
                "comet.services.media_search._normalize_discovery_candidates",
                new=AsyncMock(return_value=(filtered_candidate,)),
            ),
            patch(
                "comet.services.media_search.prepare_releases",
                return_value=prepared,
            ),
            patch(
                "comet.services.media_search.refresh_late_facts",
                return_value=prepared,
            ),
            patch(
                "comet.services.media_search._prepare_provider_view",
                new=AsyncMock(
                    return_value=(
                        _pipeline((filtered_candidate,), ("playable-option",)),
                        {},
                        {},
                    )
                ),
            ) as prepare,
            patch(
                "comet.services.media_search.TorrentResultAccumulator",
                return_value=manager,
            ),
            patch(
                "comet.services.media_search.CacheStateManager",
                return_value=cache_manager,
            ),
            patch(
                "comet.services.media_search.anime_mapper.is_loaded",
                return_value=False,
            ),
            patch(
                "comet.services.media_search.settings.DIGITAL_RELEASE_FILTER",
                False,
            ),
            patch(
                "comet.services.media_search.settings.LIVE_TORRENT_CACHE_RECENT_TTL",
                -1,
            ),
        ):
            result_with_usenet = await search_media(
                "movie",
                "tt1234567",
                config,
                "",
                lambda *_args, **_kwargs: None,
            )
            prepare.return_value = (_pipeline(), {}, {})
            result_without_fallback = await search_media(
                "movie",
                "tt1234567",
                config,
                "",
                lambda *_args, **_kwargs: None,
            )

        self.assertEqual(result_with_usenet.status, MediaSearchStatus.OK)
        self.assertEqual(result_with_usenet.candidates, (filtered_candidate,))
        self.assertEqual(result_with_usenet.provider_options, ("playable-option",))
        self.assertEqual(result_without_fallback.status, MediaSearchStatus.BUSY)
        self.assertEqual(prepare.await_count, 2)

    async def test_discovery_task_is_cancelled_and_joined_after_torrent_failure(self):
        config = {
            "schemaVersion": 2,
            "enabledTransports": ("bittorrent", "usenet"),
            "_debridEntries": [],
            "_enableTorrent": True,
            "scrapeDebridAccountTorrents": False,
            "cachedOnly": False,
            "removeTrash": True,
        }
        metadata = {
            "title": "Example",
            "year": 2024,
            "year_end": None,
            "season": None,
            "episode": None,
        }
        discovery_started = asyncio.Event()
        discovery_cancelled = asyncio.Event()

        async def discovery(*_args, **_kwargs):
            discovery_started.set()
            try:
                await asyncio.Event().wait()
            finally:
                discovery_cancelled.set()

        async def fail_cached_torrents():
            await discovery_started.wait()
            raise RuntimeError("torrent cache failed")

        manager = MagicMock()
        manager.get_cached_torrents = AsyncMock(side_effect=fail_cached_torrents)
        with (
            patch(
                "comet.services.media_search.http_client_manager.get_session",
                new=AsyncMock(return_value=object()),
            ),
            patch(
                "comet.services.media_search.MetadataScraper."
                "fetch_metadata_and_aliases",
                new=AsyncMock(return_value=_metadata_result(metadata)),
            ),
            patch(
                "comet.services.media_search._search_configured_sources",
                new=discovery,
            ),
            patch(
                "comet.services.media_search.TorrentResultAccumulator",
                return_value=manager,
            ),
            patch(
                "comet.services.media_search.anime_mapper.is_loaded",
                return_value=False,
            ),
            patch(
                "comet.services.media_search.settings.DIGITAL_RELEASE_FILTER",
                False,
            ),
            patch(
                "comet.services.media_search.settings.LIVE_TORRENT_CACHE_RECENT_TTL",
                -1,
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "torrent cache failed"):
                await search_media(
                    "movie",
                    "tt1234567",
                    config,
                    "",
                    lambda *_args, **_kwargs: None,
                )

        self.assertTrue(discovery_cancelled.is_set())
