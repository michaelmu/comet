import unittest

from comet.services.orchestration import (
    _is_current_scrape_result,
    merge_torrent_candidates,
)


def _candidate(**overrides):
    candidate = {
        "title": "Movie.2026.1080p.mkv",
        "infoHash": "A" * 40,
        "fileIndex": None,
        "seeders": None,
        "size": None,
        "tracker": "Fallback",
        "sources": [],
        "parsed": object(),
    }
    candidate.update(overrides)
    return candidate


class ScraperResultValidationTests(unittest.TestCase):
    def test_rejects_non_sha1_hashes_at_the_scraper_boundary(self):
        self.assertFalse(_is_current_scrape_result(_candidate(infoHash="z" * 40)))
        self.assertFalse(_is_current_scrape_result(_candidate(infoHash="a" * 39)))
        self.assertTrue(_is_current_scrape_result(_candidate()))

    def test_merge_is_order_independent_and_preserves_rich_metadata(self):
        indexed = _candidate(
            title="Movie.2026.1080p.mkv",
            fileIndex=3,
            size=4_000,
            seeders=4,
            tracker="Torrentio|YTS",
            sources=["udp://one"],
        )
        popular = _candidate(
            title="Movie.2026.pack",
            seeders=40,
            size=20_000,
            tracker="Prowlarr",
            sources=["udp://two", "udp://one"],
        )

        forward = merge_torrent_candidates([indexed, popular])
        reverse = merge_torrent_candidates([popular, indexed])

        self.assertEqual(forward, reverse)
        self.assertEqual(len(forward), 1)
        self.assertEqual(forward[0]["infoHash"], "a" * 40)
        self.assertEqual(forward[0]["fileIndex"], 3)
        self.assertEqual(forward[0]["size"], 4_000)
        self.assertEqual(forward[0]["seeders"], 40)
        self.assertEqual(forward[0]["sources"], ["udp://one", "udp://two"])


if __name__ == "__main__":
    unittest.main()
