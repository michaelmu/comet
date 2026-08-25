#!/usr/bin/env python3
"""Reproduce URL-size measurements for canonical result configurations."""

from __future__ import annotations

import base64
import sys
import zlib
from pathlib import Path

import orjson

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from comet.core.config_codec import (
    CONFIGURATION_DICTIONARY_V1,
    CONFIGURATION_DICTIONARY_V2,
)
from comet.results.config import LanguagesConfig, ResultsConfig


def _segment_size(document: bytes, dictionary: bytes, version: str) -> int:
    compressor = zlib.compressobj(
        level=9,
        wbits=-zlib.MAX_WBITS,
        zdict=dictionary,
    )
    compressed = compressor.compress(document) + compressor.flush()
    encoded = base64.urlsafe_b64encode(compressed).rstrip(b"=")
    return len(version) + 1 + len(encoded)


def _document(results: ResultsConfig, languages: LanguagesConfig) -> bytes:
    return orjson.dumps(
        {
            "schemaVersion": 2,
            "enabledTransports": ["bittorrent", "usenet"],
            "discoverySources": [],
            "playbackProviders": [],
            "accounts": {},
            "results": results.model_dump(mode="json"),
            "languages": languages.model_dump(mode="json"),
        }
    )


def _fixtures() -> tuple[tuple[str, bytes], ...]:
    default = ResultsConfig()
    advanced = ResultsConfig.model_validate(
        {
            "filters": {
                "dimensions": {
                    "resolution": {"only": ["2160p", "1080p", "720p"]},
                    "visual": {"exclude": ["3d", "upscaled"]},
                    "audio": {"only": ["atmos", "truehd", "dtsHd"]},
                },
                "ranges": {
                    "playbackSize": {"max": 53_687_091_200},
                    "seeders": {"min": 3, "scope": "needsDownload"},
                },
                "keywords": {
                    "exclude": ["cam", "telesync"],
                    "prefer": ["remux", "criterion"],
                },
                "rules": [
                    {
                        "id": "prefer-release-group",
                        "action": "prefer",
                        "all": [
                            {
                                "field": "releaseGroup",
                                "op": "oneOf",
                                "values": ["framestor", "flux"],
                            }
                        ],
                    }
                ],
            },
            "sort": [
                {"key": "cached", "direction": "desc"},
                {"key": "resolution", "direction": "desc"},
                {"key": "seeders", "direction": "desc", "scope": "needsDownload"},
                {"key": "size", "direction": "asc"},
            ],
            "limits": [
                {"by": "resolution", "max": 8},
                {"by": "provider", "max": 15},
            ],
            "alternatives": {
                "cached": "best",
                "uncached": "best",
                "usenet": "best",
                "hideUncachedWhenCached": True,
                "direct": "unlessCached",
                "fallback": True,
            },
        }
    )
    custom = advanced.model_copy(
        update={
            "display": advanced.display.model_copy(
                update={
                    "preset": "custom",
                    "name": "[{provider.short}{cache.icon}] {resolution} {quality}",
                    "description": (
                        "{title}\n{video} | {audio}\n{size} · {languages.flags}"
                        "{?subtitles.flags} · SUB {subtitles.flags}{/subtitles.flags}"
                    ),
                }
            ),
            "auxiliary": advanced.auxiliary.model_copy(
                update={"filterSummary": "always", "errors": "top"}
            ),
        }
    )
    languages = LanguagesConfig(
        required=["fr"], allowed=["en"], preferred=["fr", "en"], unknown="exclude"
    )
    return (
        ("Default", _document(default, LanguagesConfig())),
        ("Advanced", _document(advanced, languages)),
        ("Custom templates", _document(custom, languages)),
    )


def main() -> None:
    print(
        "| Fixture | JSON bytes | Plain base64 | z1 segment | z2 segment | z2 vs z1 |"
    )
    print("| --- | ---: | ---: | ---: | ---: | ---: |")
    for name, document in _fixtures():
        plain = len(base64.urlsafe_b64encode(document).rstrip(b"="))
        z1 = _segment_size(document, CONFIGURATION_DICTIONARY_V1, "z1")
        z2 = _segment_size(document, CONFIGURATION_DICTIONARY_V2, "z2")
        reduction = (z2 / z1 - 1) * 100
        print(
            f"| {name} | {len(document):,} | {plain:,} | {z1:,} | {z2:,} "
            f"| {reduction:.1f}% |"
        )


if __name__ == "__main__":
    main()
