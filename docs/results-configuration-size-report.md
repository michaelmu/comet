# Results configuration codec measurements

Measured on 2026-08-11 with Python 3.13 by running
`uv run python scripts/measure_results_config_sizes.py`. It uses raw DEFLATE
level 9 and URL-safe
base64 without padding. The fixtures contain a canonical schema-v2 document
and fully materialized bounded result defaults. The advanced fixture adds
facets, ranges, keywords, a flat rule, four sort criteria, two limits,
fallback alternatives and language policy. The custom fixture additionally
uses both custom display templates and non-default auxiliary placement.

| Fixture | JSON bytes | Plain base64 | z1 segment | z2 segment | z2 vs z1 |
| --- | ---: | ---: | ---: | ---: | ---: |
| Default | 1,586 | 2,115 | 594 | 453 | -23.7% |
| Advanced | 2,114 | 2,819 | 934 | 763 | -18.3% |
| Custom templates | 2,272 | 3,030 | 1,035 | 866 | -16.3% |

The 16–24% reduction is material for configurations embedded in request URLs,
so `z2` is enabled for new encodes when it is the shortest representation.
The encoder still compares plain base64, z1 and z2 for every document. The z1
dictionary is unchanged and both historical plain-base64 and z1 segments
continue to decode with their original dictionaries.
