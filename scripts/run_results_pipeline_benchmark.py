#!/usr/bin/env python3
"""Deterministic local benchmark for the pure result-policy and ordering path."""

from __future__ import annotations

import argparse
import math
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from RTN import parse

from comet.core.capabilities import EligibleProvider
from comet.core.sources import (
    LocatorKind,
    LocatorPolicy,
    ReleaseCandidate,
    ReleaseScope,
    TorrentLocator,
    TransportKind,
)
from comet.playback.presentation import ProviderOption
from comet.results.config import LanguagesConfig, ResultsConfig
from comet.results.facts import extract_release_facts, result_entry
from comet.results.ordering import sort_entries
from comet.results.policy import ReleasePolicy


def _candidate(index: int) -> ReleaseCandidate:
    resolution = ("2160p", "1080p", "720p", "480p")[index % 4]
    language = ("ENGLISH", "FRENCH", "SPANISH")[index % 3]
    title = f"Example.Movie.2026.{resolution}.WEB-DL.{language}.x265-G{index % 97}"
    info_hash = f"{index:040x}"[-40:]
    return ReleaseCandidate(
        candidate_id=f"btih:{info_hash}",
        media_id="tt1234567",
        scope=ReleaseScope.MOVIE,
        transport=TransportKind.BITTORRENT,
        title=title,
        locators=(
            TorrentLocator(
                locator_id=f"locator-{index}",
                kind=LocatorKind.TORRENT,
                policy=LocatorPolicy(frozenset({"realdebrid"})),
                info_hash=info_hash,
            ),
        ),
        size=(index % 80 + 1) * 1_000_000_000,
        source=f"Indexer {index % 5}",
        parsed=parse(title),
        transport_stats={"seeders": index % 500},
    )


def _measure(call, repeats: int) -> tuple[float, object]:
    samples = []
    value = None
    for _ in range(repeats):
        started = time.perf_counter_ns()
        value = call()
        samples.append((time.perf_counter_ns() - started) / 1_000_000)
    return statistics.median(samples), value


def run(size: int, repeats: int) -> None:
    candidates = tuple(_candidate(index) for index in range(size))
    facts_ms, facts = _measure(
        lambda: tuple(extract_release_facts(candidate) for candidate in candidates),
        repeats,
    )
    results = ResultsConfig.model_validate(
        {
            "filters": {"removeTrash": False},
            "sort": ResultsConfig().model_dump(mode="json")["sort"],
        }
    )
    languages = LanguagesConfig(preferred=["fr", "en", "es"])
    policy = ReleasePolicy.compile(results, languages)
    provider = EligibleProvider("provider", "realdebrid", 0)
    entries = tuple(
        result_entry(
            candidate,
            ProviderOption(
                candidate.candidate_id, provider, candidate.locators, index % 2 == 0
            ),
            fact,
            rank=float(index % 101),
            release_position=index,
        )
        for index, (candidate, fact) in enumerate(zip(candidates, facts, strict=True))
    )
    policy_ms, accepted = _measure(
        lambda: tuple(
            entry
            for entry in entries
            if policy.evaluate_early(entry.facts, now_ms=1) == 0
            and policy.evaluate_late(entry, now_ms=1) == 0
        ),
        repeats,
    )
    sort_ms, ordered = _measure(
        lambda: sort_entries(
            accepted,
            results.sort,
            languages=languages,
            policy=policy,
            now_ms=1,
        ),
        repeats,
    )
    # Read the result so an optimizing interpreter cannot discard the work.
    first = ordered[0].facts.candidate_id if ordered else "none"
    normalized_sort = sort_ms / (size * math.log2(max(size, 2))) * 1_000_000
    categorical_tables = sum(
        criterion.key
        in {
            "resolution",
            "language",
            "quality",
            "videoCodec",
            "hdr",
            "audio",
            "channels",
            "subtitles",
            "transport",
        }
        or criterion.order is not None
        for criterion in results.sort
    )
    print(
        f"n={size} facts={facts_ms:.2f}ms policy={policy_ms:.2f}ms "
        f"sort={sort_ms:.2f}ms sort_ns/(n*log2n)={normalized_sort:.1f} "
        f"facts_passes=1 sort_key_passes=1 criteria={len(results.sort)} "
        f"category_tables={categorical_tables} first={first}"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sizes", default="1000,5000,10000")
    parser.add_argument("--repeats", type=int, default=5)
    arguments = parser.parse_args()
    for size in (int(value) for value in arguments.sizes.split(",")):
        run(size, arguments.repeats)


if __name__ == "__main__":
    main()
