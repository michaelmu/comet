from RTN import parse

from comet.api.endpoints.stream import _build_kodi_meta
from comet.core.capabilities import EligibleProvider
from comet.core.sources import (
    LocatorKind,
    LocatorPolicy,
    ReleaseCandidate,
    ReleaseScope,
    TorrentLocator,
    TransportKind,
)
from comet.metadata.media_info import media_info_from_stremthru
from comet.playback.presentation import ProviderOption
from comet.results.facts import extract_release_facts, result_entry
from comet.results.formatting import context_from_entry


def _media_info():
    return media_info_from_stremthru(
        {
            "video": {"codec": "hevc", "w": 3840, "h": 1600},
            "subtitle": [
                {"lang": "eng"},
                {"lang": "spa", "title": "Latin"},
            ],
            "format": {"dur": 7_200_000_000_000, "br": 20_000_000},
            "has_chapters": True,
            "v": 1,
        }
    )


def _entry():
    parsed = parse("Movie.2026.2160p.HEVC.mkv")
    media_info = _media_info()
    locator = TorrentLocator(
        locator_id="torrent",
        kind=LocatorKind.TORRENT,
        policy=LocatorPolicy(frozenset({"direct_torrent"})),
        info_hash="a" * 40,
    )
    candidate = ReleaseCandidate(
        candidate_id="btih:" + "a" * 40,
        media_id="tt1234567",
        scope=ReleaseScope.MOVIE,
        transport=TransportKind.BITTORRENT,
        title="Movie.2026.2160p.HEVC.mkv",
        locators=(locator,),
        size=100,
        parsed=parsed,
        media_info=media_info,
    )
    option = ProviderOption(
        candidate.candidate_id,
        EligibleProvider("direct", "direct_torrent", 0),
        candidate.locators,
    )
    return result_entry(
        candidate,
        option,
        extract_release_facts(candidate),
        10,
        provider_name="Direct",
    )


def test_embedded_subtitles_have_a_distinct_result_component():
    entry = _entry()

    emoji = context_from_entry(entry).fields["subtitles.flags"]
    plain = context_from_entry(entry, kodi=True).fields["subtitles.codes"]

    assert emoji == "🇬🇧/💃🏻"
    assert plain == "EN/LA"


def test_kodi_metadata_exposes_exact_measured_properties():
    entry = _entry()
    components = {
        "subtitles": context_from_entry(entry, kodi=True).fields["subtitles.codes"]
    }

    metadata = _build_kodi_meta(
        entry.candidate.parsed,
        components,
        entry.candidate.media_info,
    )

    assert metadata["width"] == 3840
    assert metadata["height"] == 1600
    assert metadata["subtitles"] == ["en", "la"]
    assert metadata["subtitlesInfo"] == "EN/LA"
    assert metadata["duration"] == 7_200
    assert metadata["bitrate"] == 20_000_000
    assert metadata["hasChapters"] is True
