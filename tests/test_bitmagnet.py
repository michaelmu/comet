import unittest

from comet.scrapers.bitmagnet import BitmagnetScraper


class _Response:
    def __init__(self, status, body):
        self.status = status
        self.body = body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    async def text(self):
        return self.body


class _Session:
    def __init__(self, response):
        self.response = response

    def get(self, *_args, **_kwargs):
        return self.response


class BitmagnetScraperTests(unittest.IsolatedAsyncioTestCase):
    async def test_non_200_response_is_a_scraper_failure(self):
        scraper = BitmagnetScraper(None, _Session(_Response(503, "unavailable")), "x")

        with self.assertRaisesRegex(RuntimeError, "HTTP 503"):
            await scraper.scrape_page("tt123", "movie", 0, 100)

    async def test_empty_response_is_a_scraper_failure(self):
        scraper = BitmagnetScraper(None, _Session(_Response(200, "")), "x")

        with self.assertRaisesRegex(ValueError, "empty response payload"):
            await scraper.scrape_page("tt123", "movie", 0, 100)

    async def test_valid_empty_feed_is_successful(self):
        body = '<rss><channel></channel></rss>'
        scraper = BitmagnetScraper(None, _Session(_Response(200, body)), "x")

        self.assertEqual(
            await scraper.scrape_page("tt123", "movie", 0, 100),
            [],
        )
