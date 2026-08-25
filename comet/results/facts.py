"""Immutable normalized facts shared by policy, ordering, and rendering."""

from __future__ import annotations

import math
import time
import unicodedata
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import PurePath
from types import MappingProxyType
from typing import TYPE_CHECKING

from comet.core.sources import (
    EasynewsHttpRef,
    NzbArtifactRef,
    ReleaseCandidate,
    ReleaseScope,
    TorrentLocator,
    TransportKind,
)

if TYPE_CHECKING:
    from comet.playback.presentation import ProviderOption


class FactPhase(StrEnum):
    EARLY = "early"
    LATE = "late"


class CacheState(StrEnum):
    CACHED = "cached"
    UNCACHED = "uncached"
    NOT_APPLICABLE = "notApplicable"


@dataclass(frozen=True, slots=True)
class FactDefinition:
    phase: FactPhase
    multiple: bool = False
    numeric: bool = False


FACT_REGISTRY = {
    "mediaType": FactDefinition(FactPhase.EARLY),
    "resolution": FactDefinition(FactPhase.EARLY),
    "quality": FactDefinition(FactPhase.EARLY),
    "visual": FactDefinition(FactPhase.EARLY, multiple=True),
    "videoCodec": FactDefinition(FactPhase.EARLY),
    "audio": FactDefinition(FactPhase.LATE, multiple=True),
    "channels": FactDefinition(FactPhase.LATE, multiple=True),
    "languages": FactDefinition(FactPhase.LATE, multiple=True),
    "subtitles": FactDefinition(FactPhase.LATE, multiple=True),
    "releaseType": FactDefinition(FactPhase.EARLY),
    "releaseGroup": FactDefinition(FactPhase.EARLY),
    "edition": FactDefinition(FactPhase.EARLY),
    "flags": FactDefinition(FactPhase.EARLY, multiple=True),
    "container": FactDefinition(FactPhase.LATE),
    "source": FactDefinition(FactPhase.EARLY),
    # A BitTorrent candidate may be delivered through debrid or directly.
    "transport": FactDefinition(FactPhase.LATE),
    "providerKind": FactDefinition(FactPhase.LATE),
    "providerId": FactDefinition(FactPhase.LATE),
    "cacheState": FactDefinition(FactPhase.LATE),
    "playbackSize": FactDefinition(FactPhase.LATE, numeric=True),
    "releaseSize": FactDefinition(FactPhase.EARLY, numeric=True),
    "seeders": FactDefinition(FactPhase.EARLY, numeric=True),
    "ageDays": FactDefinition(FactPhase.EARLY, numeric=True),
    "bitrate": FactDefinition(FactPhase.LATE, numeric=True),
    "private": FactDefinition(FactPhase.EARLY),
    "trash": FactDefinition(FactPhase.EARLY),
    "title": FactDefinition(FactPhase.EARLY),
}
_FACT_ATTRIBUTES = {
    "mediaType": "media_type",
    "resolution": "resolution",
    "quality": "quality",
    "visual": "visual",
    "videoCodec": "video_codec",
    "audio": "audio",
    "channels": "channels",
    "languages": "languages",
    "subtitles": "subtitles",
    "releaseType": "release_type",
    "releaseGroup": "release_group",
    "edition": "edition",
    "flags": "flags",
    "source": "source",
    "releaseSize": "release_size",
    "seeders": "seeders",
    "bitrate": "bitrate",
    "private": "private",
    "trash": "trash",
    "title": "normalized_title",
}

_QUALITY_ALIASES = {
    "blu-ray": "bluray",
    "bluray": "bluray",
    "bdrip": "bluray",
    "bdremux": "remux",
    "remux": "remux",
    "web-dl": "webdl",
    "webdl": "webdl",
    "webmux": "webdl",
    "webrip": "webrip",
    "web-rip": "webrip",
    "hdtv": "hdtv",
    "dvd": "dvd",
    "dvdrip": "dvd",
    "cam": "cam",
    "camrip": "cam",
    "telesync": "telesync",
    "tele-sync": "telesync",
    "ts": "telesync",
    "telecine": "telecine",
    "tc": "telecine",
    "screener": "screener",
    "scr": "screener",
}
_VIDEO_CODEC_ALIASES = {
    "h264": "avc",
    "x264": "avc",
    "avc": "avc",
    "h265": "hevc",
    "x265": "hevc",
    "hevc": "hevc",
    "av1": "av1",
    "mpeg": "mpeg",
    "mpeg2": "mpeg",
    "mpeg4": "mpeg",
    "xvid": "xvid",
    "vc1": "vc1",
    "vc-1": "vc1",
}
_AUDIO_ALIASES = {
    "atmos": "atmos",
    "truehd": "truehd",
    "dolby truehd": "truehd",
    "dts lossless": "dtsHd",
    "dts-hd": "dtsHd",
    "dts-hd ma": "dtsHd",
    "dtshd": "dtsHd",
    "dts lossy": "dts",
    "dts": "dts",
    "dolby digital plus": "dolbyDigitalPlus",
    "dolbydigitalplus": "dolbyDigitalPlus",
    "dd+": "dolbyDigitalPlus",
    "eac3": "dolbyDigitalPlus",
    "e-ac-3": "dolbyDigitalPlus",
    "dolby digital": "dolbyDigital",
    "dolbydigital": "dolbyDigital",
    "dd": "dolbyDigital",
    "ac3": "dolbyDigital",
    "ac-3": "dolbyDigital",
    "aac": "aac",
    "flac": "flac",
    "mp3": "mp3",
    "opus": "opus",
}
_RELEASE_TYPE = {
    ReleaseScope.MOVIE: "movie",
    ReleaseScope.EPISODE: "episode",
    ReleaseScope.DAILY_EPISODE: "episode",
    ReleaseScope.ANIME_EPISODE: "episode",
    ReleaseScope.SEASON_PACK: "seasonPack",
    ReleaseScope.SERIES_PACK: "completeSeries",
}
_FLAG_FIELDS = (
    "remastered",
    "repack",
    "proper",
    "upscaled",
    "hardcoded",
    "dubbed",
    "subbed",
    "extended",
    "unrated",
    "uncensored",
    "commentary",
    "documentary",
    "converted",
)
_VISUAL_ALIASES = {
    "dv": "dolbyVision",
    "dolby vision": "dolbyVision",
    "dolbyvision": "dolbyVision",
    "hdr10+": "hdr10Plus",
    "hdr10plus": "hdr10Plus",
    "hdr": "hdr",
    "sdr": "sdr",
    "3d": "3d",
    "10bit": "10bit",
    "10-bit": "10bit",
    "upscaled": "upscaled",
}

# Canonical values per closed dimension, best first. Filters, categorical sort
# orders and the configurator vocabulary all read this single tuple.
FACT_VOCABULARY = MappingProxyType(
    {
        "resolution": (
            "2160p",
            "1440p",
            "1080p",
            "720p",
            "576p",
            "480p",
            "360p",
            "240p",
            "144p",
        ),
        "quality": (
            "remux",
            "bluray",
            "webdl",
            "webrip",
            "hdtv",
            "dvd",
            "screener",
            "telecine",
            "telesync",
            "cam",
        ),
        "visual": ("dolbyVision", "hdr10Plus", "hdr", "sdr", "10bit", "3d", "upscaled"),
        "videoCodec": ("hevc", "av1", "avc", "vc1", "mpeg", "xvid"),
        "audio": (
            "atmos",
            "truehd",
            "dtsHd",
            "dts",
            "dolbyDigitalPlus",
            "dolbyDigital",
            "flac",
            "aac",
            "opus",
            "mp3",
        ),
        "channels": ("7.1", "6.1", "5.1", "4.0", "2.0", "mono"),
        "releaseType": ("movie", "episode", "seasonPack", "completeSeries"),
        "flags": _FLAG_FIELDS,
        "transport": ("debridTorrent", "directTorrent", "usenet"),
    }
)
_KNOWN_RESOLUTIONS = frozenset(FACT_VOCABULARY["resolution"])


def normalize_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = " ".join(value.strip().casefold().split())
    return normalized or None


def normalize_search_text(value: object) -> str:
    if not isinstance(value, str):
        return ""
    value = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(
        "".join(
            character if character.isalnum() else " " for character in value
        ).split()
    )


def _canonical(value: object, aliases: dict[str, str]) -> str | None:
    normalized = normalize_text(value)
    return aliases.get(normalized, normalized) if normalized else None


def normalize_fact_selector(field: str, value: object):
    """Canonicalize configured selectors with the same aliases as extracted facts."""
    if not isinstance(value, str):
        return value
    normalized = normalize_text(value)
    if normalized is None:
        return normalized
    if field == "resolution":
        return "2160p" if normalized == "4k" else normalized
    if field == "quality":
        return _QUALITY_ALIASES.get(normalized, normalized)
    if field == "videoCodec":
        return _VIDEO_CODEC_ALIASES.get(normalized, normalized)
    if field == "audio":
        return _AUDIO_ALIASES.get(normalized, normalized)
    if field == "visual":
        return _VISUAL_ALIASES.get(normalized, normalized)
    if field == "transport":
        return {
            "bittorrent": "debridTorrent",
            "torrent": "debridTorrent",
            "debridtorrent": "debridTorrent",
            "directtorrent": "directTorrent",
        }.get(normalized.replace(" ", ""), normalized)
    if field == "releaseType":
        return {
            "seasonpack": "seasonPack",
            "completeseries": "completeSeries",
        }.get(normalized.replace(" ", ""), normalized)
    if field == "cacheState" and normalized.replace(" ", "") == "notapplicable":
        return "notApplicable"
    return normalized


def _integer(value) -> int | None:
    return value if type(value) is int and value >= 0 else None


def _resolution(parsed, media_info) -> str | None:
    inspected = media_info.video.resolution if media_info and media_info.video else None
    value = normalize_text(inspected or getattr(parsed, "resolution", None))
    if value == "4k":
        return "2160p"
    return value if value in _KNOWN_RESOLUTIONS else None


def _visual(parsed, media_info) -> frozenset[str]:
    raw_hdr = (
        media_info.video.hdr
        if media_info and media_info.video and media_info.video.hdr
        else getattr(parsed, "hdr", ())
    )
    hdr = {normalize_text(value) for value in raw_hdr}
    values = set()
    if any(value in {"dv", "dolby vision", "dolbyvision"} for value in hdr):
        values.add("dolbyVision")
    if any(value in {"hdr10+", "hdr10plus"} for value in hdr):
        values.update(("hdr", "hdr10Plus"))
    elif any(value and value.startswith("hdr") for value in hdr):
        values.add("hdr")
    if not values:
        values.add("sdr")
    if getattr(parsed, "three_d", False):
        values.add("3d")
    bit_depth = normalize_text(getattr(parsed, "bit_depth", None))
    if bit_depth and bit_depth.replace("-", "") in {"10bit", "10bits"}:
        values.add("10bit")
    if getattr(parsed, "upscaled", False):
        values.add("upscaled")
    return frozenset(values)


def _video_codec(parsed, media_info) -> str | None:
    inspected = media_info.video.codec if media_info and media_info.video else None
    return _canonical(inspected or getattr(parsed, "codec", None), _VIDEO_CODEC_ALIASES)


def _audio(parsed, media_info) -> frozenset[str]:
    raw = (
        media_info.audio_codecs
        if media_info and media_info.audio_codecs
        else getattr(parsed, "audio", ())
    )
    return frozenset(
        value for item in raw if (value := _canonical(item, _AUDIO_ALIASES))
    )


def _channels(parsed, media_info) -> frozenset[str]:
    raw = (
        media_info.audio_channels
        if media_info and media_info.audio_channels
        else getattr(parsed, "channels", ())
    )
    return frozenset(value for item in raw if (value := normalize_text(item)))


def _languages(parsed, media_info) -> frozenset[str]:
    raw = (
        media_info.audio_languages
        if media_info and media_info.audio_languages
        else getattr(parsed, "languages", ())
    )
    return frozenset(value for item in raw if (value := normalize_text(item)))


def _subtitles(media_info) -> frozenset[str]:
    raw = media_info.subtitle_languages if media_info else ()
    return frozenset(value for item in raw if (value := normalize_text(item)))


def _flags(parsed) -> frozenset[str]:
    return frozenset(field for field in _FLAG_FIELDS if getattr(parsed, field, False))


def _selected_size(candidate: ReleaseCandidate) -> int | None:
    media_info = candidate.media_info
    inspected = (
        _integer(media_info.container.size)
        if media_info and media_info.container
        else None
    )
    if inspected is not None:
        return inspected
    sizes = []
    for locator in candidate.locators:
        value = (
            locator.selection_size
            if isinstance(locator, TorrentLocator)
            else locator.selection_hint_size
            if isinstance(locator, NzbArtifactRef)
            else locator.byte_size
            if isinstance(locator, EasynewsHttpRef)
            else None
        )
        normalized_size = _integer(value)
        if normalized_size is not None:
            sizes.append(normalized_size)
    return min(sizes) if sizes else candidate.size


def _container(candidate: ReleaseCandidate) -> str | None:
    media_info = candidate.media_info
    inspected = (
        media_info.container.name if media_info and media_info.container else None
    )
    parsed = candidate.parsed
    selection_titles = []
    for locator in candidate.locators:
        title = (
            locator.selection_title
            if isinstance(locator, TorrentLocator)
            else locator.selection_hint_name
            if isinstance(locator, NzbArtifactRef)
            else locator.filename
            if isinstance(locator, EasynewsHttpRef)
            else None
        )
        if title:
            selection_titles.append(title)
    selected = PurePath(selection_titles[0]).suffix if selection_titles else None
    value = (
        inspected
        or selected
        or getattr(parsed, "container", None)
        or getattr(parsed, "extension", None)
        or PurePath(candidate.title).suffix
    )
    normalized = normalize_text(value)
    return normalized.removeprefix(".") if normalized else None


def _bitrate(candidate: ReleaseCandidate) -> int | None:
    media_info = candidate.media_info
    inspected = (
        media_info.container.bitrate if media_info and media_info.container else None
    )
    parsed = candidate.parsed
    value = inspected if inspected is not None else getattr(parsed, "bitrate", None)
    if type(value) is int and value >= 0:
        return value
    if isinstance(value, float) and value >= 0:
        return int(value)
    if isinstance(value, str):
        normalized = value.strip().casefold().replace(" ", "")
        for suffix, multiplier in (
            ("gbps", 1_000_000_000),
            ("mbps", 1_000_000),
            ("kbps", 1_000),
            ("bps", 1),
        ):
            if not normalized.endswith(suffix):
                continue
            try:
                amount = float(normalized[: -len(suffix)])
            except ValueError:
                return None
            if math.isfinite(amount) and amount >= 0:
                return int(amount * multiplier)
            return None
    return None


@dataclass(frozen=True, slots=True)
class ReleaseFacts:
    candidate_id: str
    media_id: str
    media_type: str
    title: str
    normalized_title: str
    keyword_title: str
    keyword_release_group: str
    keyword_source: str
    parsed_title: str | None
    resolution: str | None
    quality: str | None
    visual: frozenset[str]
    video_codec: str | None
    audio: frozenset[str]
    channels: frozenset[str]
    languages: frozenset[str]
    subtitles: frozenset[str]
    release_type: str
    release_group: str | None
    release_group_label: str | None
    edition: str | None
    flags: frozenset[str]
    container: str | None
    source: str | None
    source_label: str | None
    transport: str
    playback_size: int | None
    release_size: int | None
    seeders: int | None
    published_at_ms: int | None
    bitrate: int | None
    duration: float | None
    private: bool
    trash: bool

    def with_language(self, language: str) -> ReleaseFacts:
        return replace(self, languages=self.languages | {language.casefold()})

    def age_days(self, now_ms: int) -> float | None:
        if self.published_at_ms is None:
            return None
        return max(0.0, (now_ms - self.published_at_ms) / 86_400_000)


def extract_release_facts(candidate: ReleaseCandidate) -> ReleaseFacts:
    """Extract every fact once from data already attached to the candidate."""
    parsed = candidate.parsed
    if parsed is None:
        raise ValueError("release facts require normalized parsed data")
    media_info = candidate.media_info
    group_label = getattr(parsed, "group", None) or None
    source_label = candidate.source or None
    duration = (
        media_info.container.duration_seconds
        if media_info and media_info.container
        else None
    )
    return ReleaseFacts(
        candidate_id=candidate.candidate_id,
        media_id=candidate.media_id,
        media_type="movie" if candidate.scope is ReleaseScope.MOVIE else "series",
        title=candidate.title,
        normalized_title=normalize_text(candidate.title) or "",
        keyword_title=normalize_search_text(candidate.title),
        keyword_release_group=normalize_search_text(group_label),
        keyword_source=normalize_search_text(source_label),
        parsed_title=getattr(parsed, "parsed_title", None) or None,
        resolution=_resolution(parsed, media_info),
        quality=_canonical(getattr(parsed, "quality", None), _QUALITY_ALIASES),
        visual=_visual(parsed, media_info),
        video_codec=_video_codec(parsed, media_info),
        audio=_audio(parsed, media_info),
        channels=_channels(parsed, media_info),
        languages=_languages(parsed, media_info),
        subtitles=_subtitles(media_info),
        release_type=_RELEASE_TYPE[candidate.scope],
        release_group=normalize_text(group_label),
        release_group_label=group_label,
        edition=normalize_text(getattr(parsed, "edition", None)),
        flags=_flags(parsed),
        container=_container(candidate),
        source=normalize_text(source_label),
        source_label=source_label,
        transport=(
            "torrent" if candidate.transport is TransportKind.BITTORRENT else "usenet"
        ),
        playback_size=_selected_size(candidate),
        release_size=candidate.size,
        seeders=_integer(candidate.transport_stats.get("seeders")),
        published_at_ms=candidate.published_at_ms,
        bitrate=_bitrate(candidate),
        duration=duration,
        private=candidate.is_private,
        trash=bool(getattr(parsed, "trash", False)),
    )


@dataclass(frozen=True, slots=True)
class ResultEntry:
    candidate: ReleaseCandidate
    option: ProviderOption
    facts: ReleaseFacts
    rank: float
    cache_state: CacheState
    provider_id: str
    provider_kind: str
    provider_name: str
    release_position: int
    provider_position: int
    option_playback_size: int | None = None
    option_container: str | None = None
    fallback_options: tuple[ProviderOption, ...] = ()

    @property
    def stable_id(self) -> tuple[str, str]:
        return self.facts.candidate_id, self.provider_id

    @property
    def playback_size(self) -> int | None:
        return (
            self.option_playback_size
            if self.option_playback_size is not None
            else self.facts.playback_size
            if self.facts.playback_size is not None
            else self.facts.release_size
        )

    @property
    def container(self) -> str | None:
        return self.option_container or self.facts.container

    @property
    def delivery_transport(self) -> str:
        """How this option is served, as opposed to where the release was found."""
        if self.provider_kind == "direct_torrent":
            return "directTorrent"
        return (
            "debridTorrent"
            if self.facts.transport == "torrent"
            else self.facts.transport
        )


def result_entry(
    candidate: ReleaseCandidate,
    option: ProviderOption,
    facts: ReleaseFacts,
    rank: float,
    *,
    provider_name: str | None = None,
    release_position: int = 0,
) -> ResultEntry:
    kind = option.provider.kind
    cache_state = (
        CacheState.NOT_APPLICABLE
        if kind == "direct_torrent" or candidate.transport is TransportKind.USENET
        else (CacheState.CACHED if option.cached else CacheState.UNCACHED)
    )
    option_sizes = []
    option_containers = []
    for locator in option.locators:
        size = (
            locator.selection_size
            if isinstance(locator, TorrentLocator)
            else locator.selection_hint_size
            if isinstance(locator, NzbArtifactRef)
            else locator.byte_size
            if isinstance(locator, EasynewsHttpRef)
            else None
        )
        if (normalized_size := _integer(size)) is not None:
            option_sizes.append(normalized_size)
        title = (
            locator.selection_title
            if isinstance(locator, TorrentLocator)
            else locator.selection_hint_name
            if isinstance(locator, NzbArtifactRef)
            else locator.filename
            if isinstance(locator, EasynewsHttpRef)
            else None
        )
        if title and (suffix := PurePath(title).suffix):
            option_containers.append(suffix.removeprefix(".").casefold())
    return ResultEntry(
        candidate=candidate,
        option=option,
        facts=facts,
        rank=rank,
        cache_state=cache_state,
        provider_id=option.provider.configuration_id,
        provider_kind=kind,
        provider_name=provider_name or kind,
        release_position=release_position,
        provider_position=option.provider.list_position,
        option_playback_size=min(option_sizes) if option_sizes else None,
        option_container=option_containers[0] if option_containers else None,
    )


def fact_value(
    facts: ReleaseFacts,
    field: str,
    *,
    entry: ResultEntry | None = None,
    now_ms: int | None = None,
):
    attribute = _FACT_ATTRIBUTES.get(field)
    if attribute is not None:
        return getattr(facts, attribute)
    if field == "container":
        return entry.container if entry is not None else facts.container
    if field == "transport":
        return entry.delivery_transport if entry is not None else facts.transport
    if field == "playbackSize":
        if entry is not None:
            return entry.playback_size
        return (
            facts.playback_size
            if facts.playback_size is not None
            else facts.release_size
        )
    if field == "ageDays":
        return facts.age_days(now_ms or int(time.time() * 1000))
    if entry is not None:
        if field == "providerKind":
            return entry.provider_kind
        if field == "providerId":
            return entry.provider_id
        if field == "cacheState":
            return entry.cache_state.value
    return None
