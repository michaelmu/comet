"""Bounded compiled templates and the shared result rendering context."""

from __future__ import annotations

import time
from dataclasses import dataclass
from functools import lru_cache
from types import MappingProxyType

from comet.results.config import MAX_TEMPLATE_LENGTH, DisplayConfig
from comet.results.facts import CacheState, ResultEntry
from comet.utils.languages import LANGUAGE_EMOJIS

MAX_RENDER_LENGTH = 8_192


@dataclass(frozen=True, slots=True)
class FieldDefinition:
    identifier: str
    public: bool = True


_PUBLIC_FIELDS = (
    "cache.icon",
    "cache.label",
    "provider.name",
    "provider.short",
    "languages.flags",
    "languages.codes",
    "subtitles.flags",
    "subtitles.codes",
    "size",
    "releaseSize",
    "age",
    "bitrate",
    "duration",
    "title",
    "resolution",
    "quality",
    "video",
    "audio",
    "releaseGroup",
    "seeders",
    "rank",
    "source",
    "transport",
)
FIELD_REGISTRY = MappingProxyType(
    {
        **{field: FieldDefinition(field) for field in _PUBLIC_FIELDS},
        **{
            f"stream.legacy{field}": FieldDefinition(
                f"stream.legacy{field}", public=False
            )
            for field in (
                "Title",
                "Video",
                "Audio",
                "Quality",
                "Group",
                "Seeders",
                "Size",
                "Source",
                "Languages",
                "Subtitles",
            )
        },
        "stream.defaultName": FieldDefinition("stream.defaultName", public=False),
        "stream.defaultDescription": FieldDefinition(
            "stream.defaultDescription", public=False
        ),
    }
)

_PRESET_TEMPLATES = {
    "default": ("{stream.defaultName}", "{stream.defaultDescription}"),
    "compact": (
        "[{provider.short}{cache.icon}] Comet{?resolution} {resolution}{/resolution}",
        (
            "{?title}{title}{/title}\n"
            "{?size}{size}{/size}{?languages.flags} · {languages.flags}{/languages.flags}"
        ),
    ),
    "technical": (
        "[{provider.short}{cache.icon}] Comet{?resolution} {resolution}{/resolution}",
        (
            "{?title}{title}{/title}\n"
            "{?video}{video}{/video}{?audio} | {audio}{/audio}\n"
            "{?quality}{quality}{/quality}{?releaseGroup} | {releaseGroup}{/releaseGroup}\n"
            "{?size}{size}{/size}{?bitrate} · {bitrate}{/bitrate}"
            "{?duration} · {duration}{/duration}{?age} · {age}{/age}\n"
            "{?languages.codes}{languages.codes}{/languages.codes}"
            "{?subtitles.codes} · SUB {subtitles.codes}{/subtitles.codes}"
            "{?source} · {source}{/source}{?transport} · {transport}{/transport}"
        ),
    ),
}

_PROVIDER_SHORT_NAMES = {
    "realdebrid": "RD",
    "alldebrid": "AD",
    "premiumize": "PM",
    "torbox": "TB",
    "debridlink": "DL",
    "stremthru": "ST",
    "debrider": "DB",
    "easydebrid": "ED",
    "offcloud": "OC",
    "pikpak": "PP",
    "direct_torrent": "P2P",
    "comet_native_usenet": "NZB",
    "stremio_nntp": "NNTP",
}

_LEGACY_FIELD_PREFIXES = MappingProxyType(
    {
        "Title": ("📄 ", ""),
        "Video": ("📹 ", ""),
        "Audio": ("🔊 ", ""),
        "Quality": ("⭐ ", ""),
        "Group": ("🏷️ ", ""),
        "Seeders": ("👤 ", "Seeders: "),
        "Size": ("💾 ", "Size: "),
        "Source": ("🔎 ", "Source: "),
        "Languages": ("", "Languages: "),
        "Subtitles": ("💬 ", "Subtitles: "),
    }
)


class TemplateSyntaxError(ValueError):
    def __init__(self, message: str, offset: int):
        super().__init__(message)
        self.offset = offset


@dataclass(frozen=True, slots=True)
class _Literal:
    value: str


@dataclass(frozen=True, slots=True)
class _Field:
    name: str


@dataclass(frozen=True, slots=True)
class _Conditional:
    name: str
    tokens: tuple[object, ...]


@dataclass(frozen=True, slots=True)
class CompiledTemplate:
    tokens: tuple[object, ...]

    def render(self, fields: MappingProxyType) -> str:
        output = []

        def rendered_tokens(tokens) -> str:
            nested = []
            for token in tokens:
                if isinstance(token, _Literal):
                    nested.append(token.value)
                elif isinstance(token, _Field):
                    nested.append(fields.get(token.name, ""))
                else:
                    value = fields.get(token.name, "")
                    if value:
                        fragment = rendered_tokens(token.tokens)
                        current_line = "".join(nested).rsplit("\n", 1)[-1]
                        if not current_line.strip():
                            stripped = fragment.lstrip()
                            if stripped.startswith(("|", "·")):
                                fragment = stripped[1:].lstrip()
                        nested.append(fragment)
            return "".join(nested)

        output.append(rendered_tokens(self.tokens))
        rendered = output[0]
        if len(rendered) > MAX_RENDER_LENGTH:
            raise ValueError("rendered template exceeds the output limit")
        return "\n".join(line.rstrip() for line in rendered.strip().splitlines())


def _field(name: str, offset: int) -> str:
    if name not in FIELD_REGISTRY:
        raise TemplateSyntaxError(f"unknown result field: {name}", offset)
    return name


def _compile_fragment(value: str, *, base_offset: int, conditional: bool) -> tuple:
    tokens = []
    literal = []

    def flush() -> None:
        if literal:
            tokens.append(_Literal("".join(literal)))
            literal.clear()

    index = 0
    while index < len(value):
        if value.startswith("{{", index):
            literal.append("{")
            index += 2
            continue
        if value.startswith("}}", index):
            literal.append("}")
            index += 2
            continue
        if value[index] == "}":
            raise TemplateSyntaxError("unescaped closing brace", base_offset + index)
        if value[index] != "{":
            literal.append(value[index])
            index += 1
            continue
        closing = value.find("}", index + 1)
        if closing < 0:
            raise TemplateSyntaxError("unclosed field", base_offset + index)
        marker = value[index + 1 : closing]
        if marker.startswith("/"):
            raise TemplateSyntaxError(
                "unexpected conditional close", base_offset + index
            )
        flush()
        if marker.startswith("?"):
            if conditional:
                raise TemplateSyntaxError(
                    "conditional blocks cannot be nested", base_offset + index
                )
            name = _field(marker[1:], base_offset + index)
            close = f"{{/{name}}}"
            close_at = value.find(close, closing + 1)
            if close_at < 0:
                raise TemplateSyntaxError(
                    f"missing conditional close for {name}", base_offset + index
                )
            body = value[closing + 1 : close_at]
            tokens.append(
                _Conditional(
                    name,
                    _compile_fragment(
                        body,
                        base_offset=base_offset + closing + 1,
                        conditional=True,
                    ),
                )
            )
            index = close_at + len(close)
            continue
        tokens.append(_Field(_field(marker, base_offset + index)))
        index = closing + 1
    flush()
    return tuple(tokens)


@lru_cache(maxsize=512)
def compile_template(value: str) -> CompiledTemplate:
    if len(value) > MAX_TEMPLATE_LENGTH:
        raise TemplateSyntaxError("template exceeds the length limit", 0)
    return CompiledTemplate(_compile_fragment(value, base_offset=0, conditional=False))


@dataclass(frozen=True, slots=True)
class CompiledDisplay:
    name: CompiledTemplate
    description: CompiledTemplate

    def render(self, context: RenderContext) -> RenderedResult:
        return RenderedResult(
            name=self.name.render(context.fields),
            description=self.description.render(context.fields),
            fields=context.fields,
        )


@lru_cache(maxsize=512)
def _compile_display_values(preset: str, name: str | None, description: str | None):
    if preset == "custom":
        assert name is not None and description is not None
        values = name, description
    else:
        values = _PRESET_TEMPLATES[preset]
    return CompiledDisplay(*(compile_template(value) for value in values))


def compile_display(display: DisplayConfig) -> CompiledDisplay:
    return _compile_display_values(display.preset, display.name, display.description)


@dataclass(frozen=True, slots=True)
class RenderContext:
    fields: MappingProxyType


@dataclass(frozen=True, slots=True)
class RenderedResult:
    name: str
    description: str
    fields: MappingProxyType


def _format_bytes(value: int | None) -> str:
    if value is None:
        return ""
    amount = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if amount < 1024:
            return f"{amount:.1f} {unit}"
        amount /= 1024
    return f"{amount:.1f} PB"


def _format_duration(value: float | None) -> str:
    if value is None:
        return ""
    minutes = round(value / 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m" if hours else f"{minutes}m"


def _format_bitrate(value: int | None) -> str:
    return f"{value / 1_000_000:.1f} Mbps" if value is not None else ""


def _flags(values) -> str:
    return "/".join(
        LANGUAGE_EMOJIS.get(value, value.upper()) for value in sorted(values)
    )


def _codes(values) -> str:
    return "/".join(sorted(value.upper() for value in values))


def _cache_values(entry: ResultEntry) -> tuple[str, str]:
    if entry.provider_kind == "direct_torrent":
        return "🧲", "P2P"
    if entry.facts.transport == "usenet":
        return "📰", "NZB"
    if entry.cache_state is CacheState.CACHED:
        return "⚡", "Cached"
    return "⬇️", "On demand"


def _provider_short(entry: ResultEntry) -> str:
    known = _PROVIDER_SHORT_NAMES.get(entry.provider_kind)
    if known:
        return known
    words = entry.provider_kind.replace("-", "_").split("_")
    initials = "".join(word[:1] for word in words if word).upper()
    return (initials or entry.provider_kind.upper())[:8]


def _default_provider_label(entry: ResultEntry) -> str:
    if entry.provider_kind == "direct_torrent":
        return "TORRENT"
    return (
        _provider_short(entry)
        if entry.facts.transport == "torrent"
        else entry.provider_name
    )


def _video(entry: ResultEntry) -> str:
    facts = entry.facts
    parts = [facts.video_codec or ""]
    visual_labels = {
        "dolbyVision": "DV",
        "hdr10Plus": "HDR10+",
        "hdr": "HDR",
        "sdr": "SDR",
        "10bit": "10bit",
        "3d": "3D",
        "upscaled": "UPSCALED",
    }
    parts.extend(
        visual_labels[value]
        for value in (
            "dolbyVision",
            "hdr10Plus",
            "hdr",
            "sdr",
            "10bit",
            "3d",
            "upscaled",
        )
        if value in facts.visual
    )
    return " • ".join(part for part in parts if part)


def _audio(entry: ResultEntry) -> str:
    labels = {
        "atmos": "Atmos",
        "truehd": "TrueHD",
        "dtsHd": "DTS-HD",
        "dts": "DTS",
        "dolbyDigitalPlus": "Dolby Digital Plus",
        "dolbyDigital": "Dolby Digital",
        "aac": "AAC",
        "flac": "FLAC",
        "mp3": "MP3",
        "opus": "OPUS",
    }
    values = [labels.get(value, value) for value in sorted(entry.facts.audio)]
    values.extend(sorted(entry.facts.channels))
    return " • ".join(values)


def _quality(entry: ResultEntry) -> str:
    facts = entry.facts
    values = [facts.quality or "", facts.edition or ""]
    values.extend(flag.upper() for flag in sorted(facts.flags))
    return " • ".join(value for value in values if value)


def _legacy_raw_values(entry: ResultEntry) -> dict[str, str]:
    """Reproduce the pre-results formatter from already attached release data."""
    parsed = entry.candidate.parsed
    video = [getattr(parsed, "codec", None) or ""]
    video.extend(getattr(parsed, "hdr", ()) or ())
    video.append(getattr(parsed, "bit_depth", None) or "")
    audio = [
        *(getattr(parsed, "audio", ()) or ()),
        *(getattr(parsed, "channels", ()) or ()),
    ]
    quality = [
        getattr(parsed, "quality", None) or "",
        getattr(parsed, "edition", None) or "",
    ]
    quality.extend(
        label
        for attribute, label in (
            ("proper", "PROPER"),
            ("repack", "REPACK"),
            ("upscaled", "UPSCALED"),
            ("remastered", "REMASTERED"),
            ("extended", "EXTENDED"),
        )
        if getattr(parsed, attribute, False)
    )
    source = entry.facts.source_label or ""
    if source.startswith("Comet|"):
        from comet.core.models import settings

        if settings.COMET_CLEAN_TRACKER:
            source = f"Comet|{source.rsplit('|', 1)[-1]}"
    languages = getattr(parsed, "languages", ()) or ()
    subtitles = (
        entry.candidate.media_info.subtitle_languages
        if entry.candidate.media_info is not None
        else ()
    )
    return {
        "Title": entry.facts.title,
        "Video": " • ".join(item for item in video if item),
        "Audio": " • ".join(item for item in audio if item),
        "Quality": " • ".join(item for item in quality if item),
        "Group": getattr(parsed, "group", None) or "",
        "Seeders": "" if entry.facts.seeders is None else str(entry.facts.seeders),
        "Size": _format_bytes(entry.playback_size),
        "Source": source,
        "Languages": "/".join(languages),
        "Subtitles": "/".join(subtitles),
    }


def _legacy_components(raw_values: dict[str, str], *, kodi: bool) -> dict[str, str]:
    prefix_index = 1 if kodi else 0
    components = {}
    for field, raw_value in raw_values.items():
        if not raw_value:
            continue
        value = raw_value
        if not kodi and field in {"Languages", "Subtitles"}:
            value = "/".join(
                LANGUAGE_EMOJIS.get(code.casefold(), code)
                for code in raw_value.split("/")
            )
        components[field] = f"{_LEGACY_FIELD_PREFIXES[field][prefix_index]}{value}"
    return components


def _legacy_description(components: dict[str, str]) -> str:
    lines = []
    if "Title" in components:
        lines.append(components["Title"])
    for fields, separator in (
        (("Video", "Audio"), " | "),
        (("Quality", "Group"), " | "),
        (("Seeders", "Size", "Source"), " "),
    ):
        values = [components[field] for field in fields if field in components]
        if values:
            lines.append(separator.join(values))
    for field in ("Languages", "Subtitles"):
        if field in components:
            lines.append(components[field])
    return "\n".join(lines) or "Empty result format configuration"


def context_from_entry(entry: ResultEntry, *, kodi: bool = False) -> RenderContext:
    facts = entry.facts
    cache_icon, cache_label = _cache_values(entry)
    video = _video(entry)
    audio = _audio(entry)
    quality = _quality(entry)
    legacy_raw = _legacy_raw_values(entry)
    legacy_components = _legacy_components(legacy_raw, kodi=kodi)
    size = _format_bytes(entry.playback_size)
    release_size = _format_bytes(facts.release_size)
    age = ""
    if facts.published_at_ms is not None:
        age = f"{facts.age_days(time.time_ns() // 1_000_000):.0f}d"
    values = {
        "cache.icon": "" if kodi else cache_icon,
        "cache.label": cache_label,
        "provider.name": entry.provider_name,
        "provider.short": _provider_short(entry),
        "languages.flags": _flags(facts.languages),
        "languages.codes": _codes(facts.languages),
        "subtitles.flags": _flags(facts.subtitles),
        "subtitles.codes": _codes(facts.subtitles),
        "size": size,
        "releaseSize": release_size,
        "age": age,
        "bitrate": _format_bitrate(facts.bitrate),
        "duration": _format_duration(facts.duration),
        "title": facts.title,
        "resolution": facts.resolution or "",
        "quality": quality,
        "video": video,
        "audio": audio,
        "releaseGroup": facts.release_group_label or "",
        "seeders": "" if facts.seeders is None else str(facts.seeders),
        "rank": f"{entry.rank:g}",
        "source": facts.source_label or "",
        "transport": {
            "usenet": "Usenet",
            "directTorrent": "Direct torrent",
        }.get(entry.delivery_transport, "Torrent"),
    }
    if kodi:
        kodi_status = (
            "P2P"
            if entry.provider_kind == "direct_torrent"
            else "NZB"
            if entry.facts.transport == "usenet"
            else cache_label
        )
        prefix = " ".join(
            item
            for item in (
                f"[{' '.join(item for item in (_default_provider_label(entry), kodi_status) if item)}]",
                facts.resolution or "",
            )
            if item
        )
        details = [
            size,
            f"S:{facts.seeders}" if facts.seeders is not None else "",
            legacy_raw["Video"],
            legacy_raw["Audio"],
            legacy_raw["Quality"],
            legacy_raw["Group"],
        ]
        details = [item for item in details if item]
        default_name = f"{prefix} | {' | '.join(details)}" if details else prefix
    else:
        default_name = " ".join(
            item
            for item in (
                f"[{_default_provider_label(entry)}{cache_icon}]",
                "Comet",
                facts.resolution or "",
            )
            if item
        )
    values["stream.defaultName"] = default_name
    values["stream.defaultDescription"] = _legacy_description(legacy_components)
    for field, value in legacy_components.items():
        values[f"stream.legacy{field}"] = value
    return RenderContext(MappingProxyType(values))


def example_context(*, kodi: bool = False) -> RenderContext:
    values = {
        "cache.icon": "" if kodi else "⚡",
        "cache.label": "Cached",
        "provider.name": "RealDebrid",
        "provider.short": "RD",
        "languages.flags": "🇫🇷/🇬🇧",
        "languages.codes": "FR/EN",
        "subtitles.flags": "🇫🇷/🇬🇧",
        "subtitles.codes": "FR/EN",
        "size": "18.4 GB",
        "releaseSize": "64.2 GB",
        "age": "42d",
        "bitrate": "38.5 Mbps",
        "duration": "2h 28m",
        "title": "Example.Movie.2026.2160p.WEB-DL.DV.HDR10+.HEVC.TrueHD.Atmos",
        "resolution": "2160p",
        "quality": "webdl",
        "video": "hevc • DV • HDR10+ • 10bit",
        "audio": "Atmos • TrueHD • 7.1",
        "releaseGroup": "ExampleGroup",
        "seeders": "128",
        "rank": "1240",
        "source": "ExampleIndexer",
        "transport": "Torrent",
    }
    values["stream.defaultName"] = "[RD Cached] 2160p" if kodi else "[RD⚡] Comet 2160p"
    example_legacy = {
        "Title": values["title"],
        "Video": values["video"],
        "Audio": values["audio"],
        "Quality": values["quality"],
        "Group": values["releaseGroup"],
        "Seeders": values["seeders"],
        "Size": values["size"],
        "Source": values["source"],
        "Languages": values["languages.codes" if kodi else "languages.flags"],
        "Subtitles": values["subtitles.codes" if kodi else "subtitles.flags"],
    }
    example_components = _legacy_components(example_legacy, kodi=kodi)
    values["stream.defaultDescription"] = _legacy_description(example_components)
    for field, value in example_components.items():
        values[f"stream.legacy{field}"] = value
    return RenderContext(MappingProxyType(values))
