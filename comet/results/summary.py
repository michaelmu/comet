"""Safe aggregate-only filter summary rendering."""

from __future__ import annotations

from comet.results.pipeline import PipelineResult
from comet.results.policy import ReleasePolicy

_KIND_LABELS = {
    "guard": "guard",
    "trash": "trash",
    "facet": "filter",
    "range": "range",
    "keyword": "keyword",
    "language": "language",
    "rule": "rule",
    "selection": "selection",
}


def render_filter_summary(
    pipeline: PipelineResult,
    policy: ReleasePolicy,
    *,
    kodi: bool,
    shown_entries=None,
) -> dict:
    """Render at most one bounded row without per-release or secret material."""
    lines = [f"{pipeline.found_count} releases found"]
    for reason, count in zip(policy.reasons, pipeline.rejection_counts):
        if count:
            lines.append(f"− {count} {_KIND_LABELS[reason.kind.value]}: {reason.field}")
    visible = pipeline.entries if shown_entries is None else tuple(shown_entries)
    releases = len({entry.facts.candidate_id for entry in visible})
    lines.append(f"= {releases} releases · {len(visible)} streams shown")
    return {
        "name": "[INFO] Comet filters" if kodi else "[ℹ️] Comet filters",
        "description": "\n".join(lines),
        "url": "https://comet.feels.legal",
    }
