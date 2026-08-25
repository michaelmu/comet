# Results pipeline benchmark

Run on 2026-08-11 with Python 3.13 using
`uv run python scripts/run_results_pipeline_benchmark.py --sizes 1000,5000 --repeats 3`.
Reported durations are medians. Candidate title parsing and fixture construction
occur before timing.

| Entries | Facts pass | Disabled policy | One five-criterion sort | ns / (n log2 n) |
| ---: | ---: | ---: | ---: | ---: |
| 1,000 | 15.40 ms | 0.51 ms | 2.66 ms | 266.9 |
| 5,000 | 86.32 ms | 2.71 ms | 14.21 ms | 231.3 |

The benchmark reports exactly one facts pass, one key pass, and the number of
categorical lookup tables built once per applicable criterion before sorting.
The stable
normalized sort cost as the batch grows is consistent with the intended
`O(n log n)` sort. With every custom filter disabled, the measured evaluator
dispatch is an upper bound of 0.51–2.71 ms for 1,000–5,000 entries; the
integrated pipeline detects the compiled empty fast path once and bypasses even
those per-entry calls. It performs no keyword matching, regex, network work or
per-release diagnostic allocation. The executable benchmark is
kept in the repository so future changes can be compared on the same machine;
the numbers above are evidence, not timing thresholds for CI.
