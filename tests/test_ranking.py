import unittest
from unittest.mock import patch

from RTN import DefaultRanking, SettingsModel, check_fetch, get_rank, parse

from comet.core.models import rtn_settings_default
from comet.core.sources import (
    LocatorKind,
    LocatorPolicy,
    NzbArtifactRef,
    ReleaseCandidate,
    ReleaseScope,
    TorrentLocator,
    TransportKind,
)
from comet.results.facts import extract_release_facts
from comet.services.ranking import score_candidates, score_release_records


class RankWorkerTests(unittest.TestCase):
    def test_comet_fetches_reported_size_but_still_rejects_cam(self):
        size_fetchable, size_reasons = check_fetch(
            parse("Obsession.2026.1080p.WEB-DL.4.7GB.x264"), rtn_settings_default
        )
        cam_fetchable, cam_reasons = check_fetch(
            parse("Obsession.2026.1080p.CAM.x264"), rtn_settings_default
        )

        self.assertTrue(size_fetchable)
        self.assertEqual(size_reasons, [])
        self.assertFalse(cam_fetchable)
        self.assertIn("trash_quality", cam_reasons)

    def test_score_worker_matches_individual_rtn_calls(self):
        titles = [
            "Oppenheimer.2023.2160p.REMUX.DV.HDR10Plus.TrueHD.7.1.HEVC",
            "The.Walking.Dead.S05E03.720p.WEB-DL.x264-ASAP",
            "Some.Movie.2020.CAM.XVID.MP3",
        ]
        torrents = {
            f"{index:040x}": {
                "title": title,
                "parsed": parse(title),
                "size": index * 1_000_000,
            }
            for index, title in enumerate(titles, 1)
        }
        settings = SettingsModel()
        ranking = DefaultRanking()

        expected = {}
        for info_hash, torrent in torrents.items():
            expected[info_hash] = get_rank(torrent["parsed"], settings, ranking)

        actual = score_release_records(torrents, settings, ranking)

        self.assertEqual(actual, expected)

    def test_opaque_record_identifier_is_preserved(self):
        title = "The.Matrix.1999.1080p.BluRay.x264"
        torrents = {
            "invalid": {
                "title": title,
                "parsed": parse(title),
                "size": 1_000_000,
            }
        }

        actual = score_release_records(torrents, SettingsModel(), DefaultRanking())

        self.assertEqual(tuple(actual), ("invalid",))

    def test_unexpected_rtn_error_is_not_masked(self):
        title = "The.Matrix.1999.1080p.BluRay.x264"
        torrents = {
            "1" * 40: {
                "title": title,
                "parsed": parse(title),
                "size": 1_000_000,
            }
        }

        with patch(
            "comet.services.ranking.check_fetch_and_rank_many",
            side_effect=RuntimeError("boom"),
        ):
            with self.assertRaisesRegex(RuntimeError, "boom"):
                score_release_records(torrents, SettingsModel(), DefaultRanking())

    def test_record_without_parsed_release_is_not_scored(self):
        self.assertEqual(
            score_release_records(
                {"candidate": {"size": 1_000_000}},
                SettingsModel(),
                DefaultRanking(),
            ),
            {},
        )

    def test_score_layer_does_not_apply_result_policy(self):
        title = "The.Matrix.1999.1080p.BluRay.x264"
        torrents = {
            "1" * 40: {"title": title, "parsed": parse(title), "size": 1_000_000},
            "2" * 40: {"title": title, "parsed": parse(title), "size": 2_000_000},
        }

        actual = score_release_records(torrents, SettingsModel(), DefaultRanking())

        self.assertEqual(set(actual), {"1" * 40, "2" * 40})

    def test_mixed_torrent_and_usenet_candidates_share_one_rank_order(self):
        torrent = ReleaseCandidate(
            candidate_id="z-torrent",
            media_id="tt1234567",
            scope=ReleaseScope.MOVIE,
            transport=TransportKind.BITTORRENT,
            title="Movie.2026.720p.WEB-DL.x264",
            locators=(
                TorrentLocator(
                    locator_id="torrent",
                    kind=LocatorKind.TORRENT,
                    policy=LocatorPolicy(frozenset({"direct_torrent"})),
                    info_hash="a" * 40,
                ),
            ),
            parsed=parse("Movie.2026.720p.WEB-DL.x264"),
        )
        usenet = ReleaseCandidate(
            candidate_id="a-usenet",
            media_id="tt1234567",
            scope=ReleaseScope.MOVIE,
            transport=TransportKind.USENET,
            title="Movie.2026.2160p.REMUX.DV.HDR.TrueHD.HEVC",
            locators=(
                NzbArtifactRef(
                    locator_id="nzb",
                    kind=LocatorKind.NZB_ARTIFACT,
                    policy=LocatorPolicy(frozenset({"stremio_nntp"})),
                    artifact_sha256="b" * 64,
                    manifest_identity="nm1:" + "c" * 64,
                ),
            ),
            parsed=parse("Movie.2026.2160p.REMUX.DV.HDR.TrueHD.HEVC"),
        )

        candidates = (torrent, usenet)
        actual = score_candidates(
            candidates,
            {
                candidate.candidate_id: extract_release_facts(candidate)
                for candidate in candidates
            },
            SettingsModel(),
            DefaultRanking(),
        )

        self.assertEqual(
            tuple(release.candidate for release in actual),
            (usenet, torrent),
        )


if __name__ == "__main__":
    unittest.main()
