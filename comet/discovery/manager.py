"""Transport-neutral discovery orchestration for configured source branches."""

import asyncio
import threading
import time
import weakref
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, replace
from typing import TypeVar

from comet.core.capabilities import CapabilityPlan
from comet.core.scrape import ScrapeContext, scraper_timeout
from comet.core.sources import ReleaseCandidate, TransportKind
from comet.discovery.base import DiscoveryAdapter
from comet.discovery.capabilities import DiscoveryBranchFingerprint
from comet.discovery.coverage import SearchCoverageRepository, query_fingerprint
from comet.discovery.models import (
    CandidateNormalizationResult,
    DiscoveryBatch,
    DiscoveryContext,
    MediaQuery,
)
from comet.discovery.repository import ReleaseDiscoveryRepository
from comet.observability import log
from comet.services.lock import DistributedLock

_T = TypeVar("_T")


@dataclass(frozen=True, slots=True)
class DiscoveryResult:
    candidates: tuple[ReleaseCandidate, ...]
    diagnostics: tuple[str, ...]
    capability_plan: CapabilityPlan
    inflight: bool = False
    found_count: int = 0
    rejection_counts: tuple[tuple[str, int], ...] = ()

    def __post_init__(self) -> None:
        if self.found_count == 0 and self.candidates:
            object.__setattr__(self, "found_count", len(self.candidates))


@dataclass(frozen=True, slots=True)
class _ScheduledSearch:
    source_id: str
    provider_name: str
    branches: tuple[str, ...]
    started: float
    cancellation: asyncio.Event
    operation: asyncio.Task


class _LocalSingleFlight:
    """Collapse identical work inside one event loop without owning its cache."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._tasks = weakref.WeakKeyDictionary()

    def _discard(self, loop, key: str, task: asyncio.Task) -> None:
        if not task.cancelled():
            task.exception()
        with self._lock:
            tasks = self._tasks.get(loop)
            if tasks is None or tasks.get(key) is not task:
                return
            tasks.pop(key, None)
            if not tasks:
                self._tasks.pop(loop, None)

    async def run(self, key: str, factory: Callable[[], Awaitable[_T]]) -> _T:
        loop = asyncio.get_running_loop()
        with self._lock:
            tasks = self._tasks.setdefault(loop, {})
            task = tasks.get(key)
            if task is None:
                task = loop.create_task(factory())
                tasks[key] = task
                task.add_done_callback(
                    lambda completed, loop=loop, key=key: self._discard(
                        loop, key, completed
                    )
                )
        return await asyncio.shield(task)


_local_singleflight = _LocalSingleFlight()


def _source_timeout(adapter: DiscoveryAdapter, work_class: ScrapeContext) -> float:
    return scraper_timeout(
        getattr(adapter, "discovery_name", type(adapter).__name__),
        work_class,
    )


async def _run_before_deadline[T](
    operation: Awaitable[T],
    deadline: float,
    cancellation: asyncio.Event,
) -> T:
    timer = asyncio.get_running_loop().call_at(deadline, cancellation.set)
    try:
        async with asyncio.timeout_at(deadline):
            return await operation
    finally:
        timer.cancel()


class SearchCoordinator:
    """Runs only capability-plan reachable adapters and preserves partial success."""

    def __init__(
        self,
        adapters: Mapping[str, DiscoveryAdapter],
        *,
        database=None,
        background_task_adder: Callable | None = None,
        candidate_normalizer: (
            Callable[
                [tuple[ReleaseCandidate, ...]],
                Awaitable[tuple[ReleaseCandidate, ...] | CandidateNormalizationResult],
            ]
            | None
        ) = None,
        fresh_ttl: float = 900.0,
        empty_ttl: float = 30.0,
        retry_ttl: float = 60.0,
    ):
        self._adapters = dict(adapters)
        self._database = database
        self._background_task_adder = background_task_adder
        self._candidate_normalizer = candidate_normalizer
        self._fresh_ttl = float(fresh_ttl)
        self._empty_ttl = float(empty_ttl)
        self._retry_ttl = float(retry_ttl)

    async def search(
        self,
        query: MediaQuery,
        capability_plan: CapabilityPlan,
        *,
        account_partition: bytes | None = None,
        trace_id: str | None = None,
        work_class: ScrapeContext = ScrapeContext.LIVE,
        branch_fingerprints: (
            Mapping[tuple[str, str], DiscoveryBranchFingerprint] | None
        ) = None,
    ) -> DiscoveryResult:
        loop = asyncio.get_running_loop()
        display_names = {
            source.configuration_id: source.display_name
            for source in capability_plan.discovery
            if source.display_name
        }
        scheduled: list[_ScheduledSearch] = []
        for source in capability_plan.discovery:
            adapter = self._adapters[source.configuration_id]
            branches = tuple(
                branch.value
                for branch in sorted(
                    source.branches,
                    key=lambda item: item.value,
                )
            )
            provider_name = (
                source.display_name
                or getattr(adapter, "discovery_name", None)
                or type(adapter)
                .__name__.removesuffix("Scraper")
                .removesuffix("Adapter")
            )
            source_started = loop.time()
            source_deadline = source_started + _source_timeout(adapter, work_class)
            cancellation = asyncio.Event()
            scheduled.append(
                _ScheduledSearch(
                    source.configuration_id,
                    provider_name,
                    branches,
                    source_started,
                    cancellation,
                    asyncio.create_task(
                        self._search_source(
                            adapter,
                            source.configuration_id,
                            branches,
                            query,
                            account_partition,
                            branch_fingerprints,
                            hard_deadline=source_deadline,
                            cancellation=cancellation,
                            trace_id=trace_id,
                            work_class=work_class,
                        )
                    ),
                )
            )
        if not scheduled:
            return DiscoveryResult((), (), capability_plan)

        tasks = [source.operation for source in scheduled]
        try:
            await asyncio.wait(tasks)
        except asyncio.CancelledError:
            for source in scheduled:
                source.cancellation.set()
                source.operation.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise

        candidates = []
        diagnostics = []
        had_failures = False
        had_inflight = False
        for source in scheduled:
            operation = source.operation
            failure_outcome = None
            failure_exception = None
            if operation.cancelled():
                response = None
                failure_outcome = "timeout"
                had_inflight = True
            else:
                try:
                    response = operation.result()
                except TimeoutError:
                    response = None
                    failure_outcome = "timeout"
                    had_inflight = True
                except Exception as exc:
                    response = None
                    failure_exception = exc
                if failure_outcome is None and failure_exception is not None:
                    failure_outcome = "failed"
                elif failure_outcome is None:
                    had_inflight = had_inflight or response.inflight
            if failure_outcome is not None:
                had_failures = True
                log.warning(
                    "discovery.provider.completed",
                    "Discovery provider completed",
                    provider_name=source.provider_name,
                    duration_ms=(loop.time() - source.started) * 1000,
                    outcome=failure_outcome,
                    exc=failure_exception,
                )
                continue
            log.info(
                "discovery.provider.completed",
                "Discovery provider completed",
                provider_name=source.provider_name,
                candidate_count=len(response.candidates),
                duration_ms=(loop.time() - source.started) * 1000,
            )
            for diagnostic in response.diagnostics:
                if diagnostic not in diagnostics:
                    diagnostics.append(diagnostic)
            for candidate in response.candidates:
                if any(
                    capability_plan.compatible_providers(locator)
                    for locator in candidate.locators
                ):
                    display_name = display_names.get(source.source_id)
                    candidates.append(
                        replace(candidate, source=display_name)
                        if display_name
                        else candidate
                    )
        if had_failures and not candidates:
            diagnostics.append("Discovery is temporarily unavailable")
        raw_candidates = tuple(candidates)
        normalized = CandidateNormalizationResult(raw_candidates, len(raw_candidates))
        if self._candidate_normalizer is not None and raw_candidates:
            normalized = await self._candidate_normalizer(raw_candidates)
        if isinstance(normalized, tuple):
            normalized = CandidateNormalizationResult(normalized, len(raw_candidates))
        return DiscoveryResult(
            normalized.candidates,
            tuple(diagnostics),
            capability_plan,
            inflight=had_inflight and not candidates,
            found_count=normalized.found_count,
            rejection_counts=normalized.rejection_counts,
        )

    async def _search_source(
        self,
        adapter: DiscoveryAdapter,
        source_configuration_id: str,
        branches: tuple[str, ...],
        query: MediaQuery,
        account_partition: bytes | None,
        fingerprints: (Mapping[tuple[str, str], DiscoveryBranchFingerprint] | None),
        *,
        hard_deadline: float,
        cancellation: asyncio.Event,
        trace_id: str | None,
        work_class: ScrapeContext,
    ) -> DiscoveryBatch:
        if self._database is None or account_partition is None or fingerprints is None:
            response = await _run_before_deadline(
                adapter.search(
                    query,
                    _context(
                        branches,
                        source_configuration_id,
                        account_partition,
                        cancellation,
                        trace_id,
                        work_class,
                    ),
                ),
                hard_deadline,
                cancellation,
            )
            return response
        branch_pairs = tuple(
            (
                branch,
                fingerprints[(source_configuration_id, branch)],
            )
            for branch in branches
        )

        candidates = []
        diagnostics = []
        covered = set()
        inflight = False
        cold_tasks = []
        coverage_repository = SearchCoverageRepository(self._database)
        release_repository = ReleaseDiscoveryRepository(self._database)
        states = await asyncio.gather(
            *(
                coverage_repository.effective(query, identity.fingerprint)
                for _branch, identity in branch_pairs
            )
        )
        served = [
            index
            for index, effective in enumerate(states)
            if effective.state in {"fresh", "stale", "stale_wait"}
        ]
        loaded = await asyncio.gather(
            *(
                release_repository.load_active(
                    query,
                    branch_pairs[index][1].fingerprint,
                    owner_configuration_partition=account_partition,
                    account_partition=account_partition,
                    public_visibility=branch_pairs[index][1].public_visibility,
                )
                for index in served
            )
        )
        cached_by_index = dict(zip(served, loaded))
        for index, (branch, identity) in enumerate(branch_pairs):
            effective = states[index]
            cached = cached_by_index.get(index)
            if cached is not None:
                candidates.extend(cached)
                covered.add(branch)
            if effective.state in {"fresh", "stale_wait"}:
                continue
            if effective.state == "failed_wait":
                diagnostics.append("Discovery source is temporarily unavailable")
                continue
            if effective.state == "stale" and self._background_task_adder is not None:
                self._background_task_adder(
                    self._refresh_branch,
                    adapter,
                    source_configuration_id,
                    branch,
                    identity,
                    query,
                    account_partition,
                    hard_deadline,
                    trace_id,
                    False,
                    ScrapeContext.BACKGROUND,
                )
                continue
            cold_tasks.append(
                asyncio.create_task(
                    self._refresh_branch(
                        adapter,
                        source_configuration_id,
                        branch,
                        identity,
                        query,
                        account_partition,
                        hard_deadline,
                        trace_id,
                        True,
                        work_class,
                    )
                )
            )
        if cold_tasks:
            refreshed = await asyncio.gather(*cold_tasks)
            for batch in refreshed:
                candidates.extend(batch.candidates)
                diagnostics.extend(batch.diagnostics)
                covered.update(batch.coverage)
                inflight = inflight or batch.inflight
        return DiscoveryBatch(
            tuple(candidates),
            tuple(diagnostics),
            frozenset(covered),
            inflight,
        )

    async def _refresh_branch(
        self,
        adapter: DiscoveryAdapter,
        source_configuration_id: str,
        branch: str,
        identity: DiscoveryBranchFingerprint,
        query: MediaQuery,
        account_partition: bytes,
        hard_deadline: float,
        trace_id: str | None,
        wait_for_lock: bool,
        work_class: ScrapeContext,
    ) -> DiscoveryBatch:
        lock_key = (
            "discovery:"
            + query_fingerprint(query)
            + ":"
            + identity.fingerprint
            + ":"
            + work_class.value
        )
        return await _local_singleflight.run(
            f"{id(self._database)}:{lock_key}",
            lambda: self._refresh_branch_distributed(
                adapter,
                source_configuration_id,
                branch,
                identity,
                query,
                account_partition,
                hard_deadline,
                trace_id,
                wait_for_lock,
                work_class,
                lock_key,
            ),
        )

    async def _refresh_branch_distributed(
        self,
        adapter: DiscoveryAdapter,
        source_configuration_id: str,
        branch: str,
        identity: DiscoveryBranchFingerprint,
        query: MediaQuery,
        account_partition: bytes,
        hard_deadline: float,
        trace_id: str | None,
        wait_for_lock: bool,
        work_class: ScrapeContext,
        lock_key: str,
    ) -> DiscoveryBatch:
        operation_timeout = _source_timeout(adapter, work_class)
        repository = ReleaseDiscoveryRepository(self._database)
        coverage_repository = SearchCoverageRepository(self._database)
        lock = DistributedLock(
            lock_key,
            retry_interval=0.1,
            database=self._database,
        )
        loop = asyncio.get_running_loop()
        if not wait_for_lock:
            started = loop.time()
            hard_deadline = started + operation_timeout
        wait_timeout = max(0.0, hard_deadline - loop.time()) if wait_for_lock else None
        acquired = await lock.acquire(wait_timeout=wait_timeout)
        if not acquired:
            return DiscoveryBatch(
                diagnostics=(
                    ("Discovery refresh is already running",) if wait_for_lock else ()
                ),
                inflight=wait_for_lock,
            )
        try:
            effective = await coverage_repository.effective(
                query,
                identity.fingerprint,
            )
            if effective.state in {"fresh", "stale_wait"}:
                cached = await repository.load_active(
                    query,
                    identity.fingerprint,
                    owner_configuration_partition=account_partition,
                    account_partition=account_partition,
                    public_visibility=identity.public_visibility,
                )
                return DiscoveryBatch(cached, coverage=frozenset({branch}))
            if effective.state == "failed_wait":
                return DiscoveryBatch(
                    diagnostics=("Discovery source is temporarily unavailable",)
                )

            cancellation = asyncio.Event()
            provider_name = getattr(adapter, "discovery_name", None) or type(
                adapter
            ).__name__.removesuffix("Scraper").removesuffix("Adapter")

            async def refresh():
                refresh_started = loop.time()
                log.info(
                    "discovery.refresh.started",
                    "Discovery source scrape started",
                    provider_name=provider_name,
                    source_type=branch,
                    operation=work_class.value,
                )
                try:
                    response = await _run_before_deadline(
                        adapter.search(
                            query,
                            _context(
                                (branch,),
                                source_configuration_id,
                                (
                                    None
                                    if identity.public_visibility
                                    else account_partition
                                ),
                                cancellation,
                                trace_id,
                                work_class,
                            ),
                        ),
                        hard_deadline,
                        cancellation,
                    )
                    log.info(
                        "discovery.refresh.completed",
                        "Discovery source scrape completed",
                        provider_name=provider_name,
                        source_type=branch,
                        operation=work_class.value,
                        candidate_count=len(response.candidates),
                        duration_ms=(loop.time() - refresh_started) * 1000,
                    )
                    return response
                finally:
                    cancellation.set()

            if loop.time() >= hard_deadline:
                return DiscoveryBatch(inflight=True)
            response = await lock.run(refresh())
            if branch not in response.coverage:
                await coverage_repository.record_failure(
                    query,
                    identity.fingerprint,
                    next_refresh_at=time.time() + self._retry_ttl,
                )
                return DiscoveryBatch(diagnostics=response.diagnostics)
            branch_transport = TransportKind(branch)
            branch_candidates = tuple(
                candidate
                for candidate in response.candidates
                if candidate.transport is branch_transport
            )
            await repository.persist_success(
                query,
                identity.fingerprint,
                branch_candidates,
                discovery_configuration_id=source_configuration_id,
                owner_configuration_partition=account_partition,
                account_partition=account_partition,
                public_visibility=identity.public_visibility,
                next_refresh_at=time.time()
                + (self._fresh_ttl if branch_candidates else self._empty_ttl),
            )
            canonical_candidates = await repository.load_active(
                query,
                identity.fingerprint,
                owner_configuration_partition=account_partition,
                account_partition=account_partition,
                public_visibility=identity.public_visibility,
            )
            return DiscoveryBatch(
                canonical_candidates,
                response.diagnostics,
                frozenset({branch}),
            )
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            await coverage_repository.record_failure(
                query,
                identity.fingerprint,
                next_refresh_at=time.time() + self._retry_ttl,
            )
            return DiscoveryBatch(inflight=True)
        except Exception:
            await coverage_repository.record_failure(
                query,
                identity.fingerprint,
                next_refresh_at=time.time() + self._retry_ttl,
            )
            raise
        finally:
            await lock.release()


def _context(
    branches: tuple[str, ...],
    configuration_id: str,
    account_partition: bytes | None,
    cancellation: asyncio.Event,
    trace_id: str | None,
    work_class: ScrapeContext,
) -> DiscoveryContext:
    return DiscoveryContext(
        branches=frozenset(branches),
        account_partition=account_partition,
        configuration_id=configuration_id,
        cancellation=cancellation,
        trace_id=trace_id,
        work_class=work_class,
    )
