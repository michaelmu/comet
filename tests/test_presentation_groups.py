import unittest
from dataclasses import replace

from RTN import parse

from comet.core.sources import (
    LocatorKind,
    LocatorPolicy,
    NzbArtifactRef,
    ReleaseCandidate,
    ReleaseScope,
    TorrentLocator,
    TransportKind,
)
from comet.playback.groups import build_presentation_groups


def _candidate(
    candidate_id: str,
    transport: TransportKind,
    title: str,
    *,
    size: int = 1_000_000_000,
) -> ReleaseCandidate:
    if transport is TransportKind.BITTORRENT:
        locator = TorrentLocator(
            locator_id=f"{candidate_id}:torrent",
            kind=LocatorKind.TORRENT,
            policy=LocatorPolicy(frozenset({"direct_torrent"})),
            info_hash="a" * 40,
        )
    else:
        locator = NzbArtifactRef(
            locator_id=f"{candidate_id}:nzb",
            kind=LocatorKind.NZB_ARTIFACT,
            policy=LocatorPolicy(frozenset({"stremio_nntp"})),
            artifact_sha256="b" * 64,
            manifest_identity="nm1:" + "c" * 64,
        )
    return ReleaseCandidate(
        candidate_id=candidate_id,
        media_id="tt1234567",
        scope=ReleaseScope.MOVIE,
        transport=transport,
        title=title,
        locators=(locator,),
        size=size,
        parsed=parse(title),
    )


class PresentationGroupingTests(unittest.TestCase):
    def test_exact_cross_family_alternatives_share_one_group(self):
        torrent = _candidate(
            "torrent",
            TransportKind.BITTORRENT,
            "Movie.2026.1080p.WEB-DL-GROUP",
        )
        usenet = _candidate(
            "usenet",
            TransportKind.USENET,
            "Movie.2026.1080p.WEB-DL-GROUP",
        )

        groups = build_presentation_groups((torrent, usenet))

        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0].resolution, "1080p")
        self.assertEqual(groups[0].candidates, (torrent, usenet))

    def test_identical_releases_in_different_scopes_never_share_a_group(self):
        movie = _candidate(
            "movie",
            TransportKind.BITTORRENT,
            "Example.2026.1080p.WEB-DL-GROUP",
        )
        episode = replace(
            _candidate(
                "episode",
                TransportKind.USENET,
                "Example.2026.1080p.WEB-DL-GROUP",
            ),
            scope=ReleaseScope.EPISODE,
        )

        groups = build_presentation_groups((movie, episode))

        self.assertEqual(len(groups), 2)
        self.assertEqual(
            {group.candidates[0].scope.value for group in groups},
            {"movie", "episode"},
        )

    def test_conflicting_known_resolutions_never_share_a_group(self):
        first = _candidate(
            "1080",
            TransportKind.BITTORRENT,
            "Movie.2026.1080p.WEB-DL-GROUP",
        )
        second = _candidate(
            "2160",
            TransportKind.USENET,
            "Movie.2026.2160p.WEB-DL-GROUP",
        )

        groups = build_presentation_groups((first, second))

        self.assertEqual(len(groups), 2)
        self.assertEqual({group.resolution for group in groups}, {"1080p", "2160p"})

    def test_unknown_resolution_is_not_inferred_from_another_candidate(self):
        known = _candidate(
            "known",
            TransportKind.BITTORRENT,
            "Movie.2026.1080p.WEB-DL-GROUP",
        )
        unknown = _candidate(
            "unknown",
            TransportKind.USENET,
            "Movie.2026.WEB-DL-GROUP",
        )

        groups = build_presentation_groups((known, unknown))

        self.assertEqual(len(groups), 2)
        self.assertEqual(
            {group.resolution for group in groups},
            {"1080p", "unknown"},
        )
