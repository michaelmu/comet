"""Expand ranked releases into provider-pinned playback options."""

import uuid
from dataclasses import dataclass

from comet.core.capabilities import CapabilityPlan, EligibleProvider
from comet.core.sources import (
    SERVER_USENET_PROVIDER_KINDS,
    TORRENT_PROVIDER_KINDS,
    Locator,
    ReleaseCandidate,
)
from comet.playback.repository import RenderedCandidateIds
from comet.playback.tokens import (
    MAX_FALLBACK_CHAIN_LENGTH,
    PLAYBACK_INTENT_TTL_SECONDS,
    CapabilityCodec,
)


@dataclass(frozen=True, slots=True)
class ProviderOption:
    candidate_id: str
    provider: EligibleProvider
    locators: tuple[Locator, ...]
    cached: bool = False


def build_provider_options(
    candidates: tuple[ReleaseCandidate, ...], capability_plan: CapabilityPlan
) -> tuple[ProviderOption, ...]:
    """Aggregate compatible locators once for each configured provider binding."""
    options = []
    for candidate in candidates:
        locators_by_provider: dict[str, list[Locator]] = {}
        providers_by_id: dict[str, EligibleProvider] = {}
        for locator in candidate.locators:
            for provider in capability_plan.compatible_providers(locator):
                providers_by_id[provider.configuration_id] = provider
                locators_by_provider.setdefault(provider.configuration_id, []).append(
                    locator
                )
        candidate_options = []
        for provider_id, locators in locators_by_provider.items():
            provider = providers_by_id[provider_id]
            ordered = tuple(sorted(locators, key=lambda locator: locator.locator_id))
            candidate_options.append(
                ProviderOption(
                    candidate.candidate_id,
                    provider,
                    ordered,
                )
            )
        options.extend(
            sorted(
                candidate_options,
                key=lambda option: (
                    option.provider.list_position,
                    option.provider.configuration_id,
                ),
            )
        )
    return tuple(options)


def issue_provider_option_capability(
    codec: CapabilityCodec,
    *,
    partition: bytes,
    option: ProviderOption,
    persisted: RenderedCandidateIds,
    selection_intent: list,
    client: str,
) -> str:
    """Create a session-lived server playback capability from committed IDs."""
    candidate_id = uuid.UUID(persisted.candidate_id).bytes
    provider_id = uuid.UUID(option.provider.configuration_id).bytes
    locator_ids = [
        uuid.UUID(persisted.locator_ids[locator.locator_id]).bytes
        for locator in option.locators
    ]
    return codec.encode(
        "pi2",
        partition=partition,
        suffix=[candidate_id, provider_id, locator_ids, selection_intent, client],
        ttl=PLAYBACK_INTENT_TTL_SECONDS,
    )


def issue_fallback_option_capability(
    codec: CapabilityCodec,
    *,
    partition: bytes,
    options: tuple[ProviderOption, ...],
    persisted: RenderedCandidateIds,
    transport: str,
    selection_intent: list,
    client: str,
) -> str:
    """Sign two or three ordered server-side attempts for one rendered release."""
    if not 2 <= len(options) <= MAX_FALLBACK_CHAIN_LENGTH:
        raise ValueError("fallback chain must contain two or three providers")
    candidate_ids = {option.candidate_id for option in options}
    provider_ids = {option.provider.configuration_id for option in options}
    if len(candidate_ids) != 1 or len(provider_ids) != len(options):
        raise ValueError("fallback chain must use one candidate and unique providers")
    if transport == "bittorrent":
        allowed_kinds = TORRENT_PROVIDER_KINDS - {"direct_torrent"}
    elif transport == "usenet":
        allowed_kinds = SERVER_USENET_PROVIDER_KINDS
    else:
        raise ValueError("fallback transport is invalid")
    if any(option.provider.kind not in allowed_kinds for option in options):
        raise ValueError("fallback provider is incompatible with transport")
    suffix_options = []
    for option in options:
        locator_ids = [
            uuid.UUID(persisted.locator_ids[locator.locator_id]).bytes
            for locator in option.locators
        ]
        if not locator_ids:
            raise ValueError("fallback provider has no committed locator")
        suffix_options.append(
            [uuid.UUID(option.provider.configuration_id).bytes, locator_ids]
        )
    return codec.encode(
        "pf2",
        partition=partition,
        suffix=[
            uuid.UUID(persisted.candidate_id).bytes,
            transport,
            suffix_options,
            selection_intent,
            client,
        ],
        ttl=PLAYBACK_INTENT_TTL_SECONDS,
    )


def issue_nzb_handoff_capability(
    codec: CapabilityCodec,
    *,
    partition: bytes,
    option: ProviderOption,
    persisted: RenderedCandidateIds,
    selection_intent: list,
    ttl: int,
) -> str:
    """Create one reusable lazy handoff from committed NZB transforms."""
    suffix = [
        uuid.UUID(persisted.candidate_id).bytes,
        uuid.UUID(option.provider.configuration_id).bytes,
        [
            uuid.UUID(persisted.locator_ids[locator.locator_id]).bytes
            for locator in option.locators
        ],
        selection_intent,
        "stremio",
    ]
    return codec.encode(
        "ni2",
        partition=partition,
        suffix=suffix,
        ttl=ttl,
    )
