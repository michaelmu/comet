"""RTN scoring without result-policy, limit, or presentation responsibilities."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from RTN import check_fetch_and_rank_many

from comet.core.sources import ReleaseCandidate
from comet.results.facts import ReleaseFacts


def score_release_records(
    records: Mapping[str, Mapping[str, object]],
    rtn_settings,
    rtn_ranking,
) -> dict[str, float]:
    """Return raw RTN scores for already normalized releases."""
    eligible = [
        (record_id, record["parsed"])
        for record_id, record in records.items()
        if record.get("parsed") is not None
    ]
    results = check_fetch_and_rank_many(
        (parsed for _record_id, parsed in eligible),
        rtn_settings,
        rtn_ranking,
    )
    return {
        record_id: rank
        for (record_id, _parsed), (_is_fetchable, _reasons, rank) in zip(
            eligible, results
        )
    }


@dataclass(frozen=True, slots=True)
class ScoredRelease:
    candidate: ReleaseCandidate
    facts: ReleaseFacts
    rank: float


def score_candidates(
    candidates: Iterable[ReleaseCandidate],
    facts: Mapping[str, ReleaseFacts],
    rtn_settings,
    rtn_ranking,
) -> tuple[ScoredRelease, ...]:
    ordered = tuple(candidates)
    scores = score_release_records(
        {candidate.candidate_id: {"parsed": candidate.parsed} for candidate in ordered},
        rtn_settings,
        rtn_ranking,
    )
    return tuple(
        ScoredRelease(
            candidate,
            facts[candidate.candidate_id],
            scores[candidate.candidate_id],
        )
        for candidate in sorted(
            ordered,
            key=lambda item: (-scores[item.candidate_id], item.candidate_id),
        )
        if candidate.candidate_id in scores
    )
