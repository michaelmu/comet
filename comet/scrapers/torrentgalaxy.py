from urllib.parse import quote_plus

from comet.scrapers.base import (
    BaseScraper,
    deduplicate_torrents,
    gather_with_error_logging,
)
from comet.scrapers.models import ScrapeRequest, ScrapeResult
from comet.utils.formatting import normalize_info_hash


class TorrentGalaxyScraper(BaseScraper):
    """Search TorrentGalaxy's JSON endpoint using the same API as CocoScrapers."""

    impersonate = "chrome"

    def __init__(self, manager, session, url: str):
        super().__init__(manager, session, url)

    async def _fetch(self, query: str) -> list[dict]:
        url = (
            f"{self.url}/get-posts/keywords:{quote_plus(query)}:format:json/"
        )
        async with self.session.get(url) as response:
            if response.status != 200:
                raise RuntimeError(f"HTTP {response.status}")
            payload = await response.json()

        if not isinstance(payload, dict):
            raise ValueError("response payload is not an object")
        rows = payload.get("results") or payload.get("data") or []
        if not isinstance(rows, list):
            raise ValueError("response payload is missing a results list")
        return rows

    @staticmethod
    def _parse(row: object) -> ScrapeResult | None:
        if not isinstance(row, dict):
            return None

        title = row.get("n")
        raw_hash = row.get("h")
        if not isinstance(title, str) or not title:
            return None
        if not isinstance(raw_hash, str):
            return None

        info_hash = normalize_info_hash(raw_hash)
        if len(info_hash) != 40 or any(
            character not in "0123456789abcdef" for character in info_hash
        ):
            return None

        try:
            seeders = int(row["se"]) if row.get("se") is not None else None
        except (TypeError, ValueError):
            seeders = None
        try:
            size = int(row["s"]) if row.get("s") is not None else None
        except (TypeError, ValueError):
            size = None

        return {
            "title": title,
            "infoHash": info_hash,
            "fileIndex": None,
            "seeders": seeders,
            "size": size,
            "tracker": "TorrentGalaxy",
            "sources": [],
        }

    async def scrape(self, request: ScrapeRequest):
        if request.media_type == "movie":
            # TorrentGalaxy's IMDb lookup is both narrower and more accurate than
            # a title query for films.
            queries = (request.media_only_id,)
        else:
            queries = tuple(
                f"{title} S{request.season:02d}E{request.episode:02d}"
                for title in request.query_titles
                if request.season is not None and request.episode is not None
            )
            if not queries:
                queries = request.query_titles

        results = await gather_with_error_logging(
            (
                (f"TorrentGalaxy query {query!r}", self._fetch(query))
                for query in queries
            ),
            raise_if_all_failed=True,
        )
        torrents = []
        for rows in results:
            for row in rows:
                parsed = self._parse(row)
                if parsed is not None:
                    torrents.append(parsed)
        return deduplicate_torrents(torrents)
