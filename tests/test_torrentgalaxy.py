import unittest

from comet.scrapers.models import ScrapeRequest
from comet.scrapers.torrentgalaxy import TorrentGalaxyScraper


class _Response:
    def __init__(self, status, payload):
        self.status = status
        self.payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    async def json(self):
        return self.payload


class _Session:
    def __init__(self, responses):
        self.responses = list(responses)
        self.urls = []

    def get(self, url):
        self.urls.append(url)
        return self.responses.pop(0)


class TorrentGalaxyTests(unittest.IsolatedAsyncioTestCase):
    async def test_movie_uses_imdb_and_rejects_invalid_rows(self):
        session = _Session(
            [
                _Response(
                    200,
                    {
                        "results": [
                            {
                                "n": "Movie.2026.1080p",
                                "h": "A" * 40,
                                "se": "12",
                                "s": "1234",
                            },
                            {"n": "bad", "h": "not-a-hash"},
                            None,
                        ]
                    },
                )
            ]
        )
        request = ScrapeRequest(
            media_type="movie",
            media_id="tt1234567",
            media_only_id="tt1234567",
            title="Movie",
            year=2026,
        )

        torrents = await TorrentGalaxyScraper(None, session, "https://tg.test").scrape(
            request
        )

        self.assertIn("keywords:tt1234567:format:json", session.urls[0])
        self.assertEqual(len(torrents), 1)
        self.assertEqual(torrents[0]["infoHash"], "a" * 40)
        self.assertEqual(torrents[0]["seeders"], 12)
        self.assertEqual(torrents[0]["size"], 1234)

    async def test_episode_queries_every_selected_title_and_deduplicates(self):
        row = {"n": "Show.S02E03.1080p", "h": "b" * 40, "se": 4, "s": 5}
        session = _Session(
            [
                _Response(200, {"data": [row]}),
                _Response(200, {"data": [row]}),
            ]
        )
        request = ScrapeRequest(
            media_type="series",
            media_id="tt123:2:3",
            media_only_id="tt123",
            title="Show",
            season=2,
            episode=3,
            search_titles=("Show", "Localized"),
        )

        torrents = await TorrentGalaxyScraper(None, session, "https://tg.test").scrape(
            request
        )

        self.assertEqual(len(torrents), 1)
        self.assertIn("Show+S02E03", session.urls[0])
        self.assertIn("Localized+S02E03", session.urls[1])

    async def test_http_failure_surfaces_to_health_tracking(self):
        session = _Session([_Response(503, {})])
        request = ScrapeRequest(
            media_type="movie",
            media_id="tt123",
            media_only_id="tt123",
            title="Movie",
        )

        with self.assertRaisesRegex(RuntimeError, "all concurrent tasks failed"):
            await TorrentGalaxyScraper(None, session, "https://tg.test").scrape(request)


if __name__ == "__main__":
    unittest.main()
