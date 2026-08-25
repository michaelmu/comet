# Results customization traceability

This checklist maps every accepted requirement in
`PROPOSITION_CUSTOMISATION_RESULTATS.md` to implementation and verification
evidence. An unchecked item is not complete.

## Canonical configuration and migrations

- [x] Immutable, typed `ResultsConfig` graph with bounded filters, rules, sort,
  limits, alternatives, display, and auxiliary policy.
- [x] Legacy normalization is centralized before runtime consumers.
- [x] `allow_english_in_languages: true` migrates to `languages.allowed += en`;
  the switch and canonical `options` object are removed.
- [x] `remove_unknown_languages` migrates to `languages.unknown`.
- [x] `cachedOnly`, `removeTrash`, `maxSize`, `resolutions`,
  `maxResultsPerResolution`, `resultFormat`, `sortCachedUncachedTogether`,
  `deduplicateStreams`, and sync action placement migrate exactly.
- [x] Existing schema v1/v2 documents decode without schema v3; `z1` remains
  byte-for-byte unchanged.
- [x] Legacy configurations default filter summary to off and put errors/sync
  actions at the bottom; new configurations use the proposed defaults.
- [x] Canonical v1/v2/legacy and import/export round trips are tested.

## Facts, scoring, and policy

- [x] One immutable `ReleaseFacts` extraction supplies filtering, ordering, and
  formatting; extraction/normalization occurs once per release.
- [x] One immutable `ResultEntry` retains the raw RTN score through rendering.
- [x] Metadata precedence is inspected file, selected file/extension, RTN parse,
  then unknown; no policy feature introduces network I/O.
- [x] Episode `playbackSize` prefers selected-file size while `releaseSize`
  preserves the whole pack size.
- [x] The fact registry marks early/late availability and rules are split
  automatically while sharing one evaluator.
- [x] Title/year/episode/adult correctness guards remain non-bypassable.
- [x] `removeTrash` filters only the explicit RTN trash fact; RTN fetchability no
  longer silently controls language/resolution/custom eligibility.
- [x] Disabled custom features take the constant/empty-tuple fast path.

## Filters and enrichment

- [x] `only`/`exclude` facets cover resolution, quality, visual flags
  (3D/DV/HDR/HDR10+/SDR/10-bit/upscaled), video codec, audio, channels,
  subtitles, release type/group/edition/flags, source/indexer, transport, and
  provider; language eligibility remains in its dedicated ordered policy.
- [x] Ranges cover playback size, release size, seeders, age, and bitrate with
  precise bounded scopes; movie/series combinations use the same flat rules.
- [x] Unknown is explicit: it never aliases English or a known value, never
  matches known-only/exclude accidentally, and always sorts last.
- [x] Literal `word`, normalized `phrase`, and bounded `*`/`?` wildcard matchers
  support title/releaseGroup/source targets and exclude/require/prefer actions.
- [x] Flat bounded rules support exclude/require/prefer/addLanguage with the
  approved fields/operators only; no regex/script/SEL/function/remote resource.
- [x] Release-group/keyword language enrichment runs before language eligibility
  and affects rendered language facts.
- [x] 3D/Half-SBS/Full-SBS/HSBS, DV, DV+HDR, SDR, AV1, CAM/TS/Screener, bounded
  keywords, language mappings, and ordered preferences have contract tests.

## Cache, ordering, alternatives, limits

- [x] Cache state is cached/uncached only for torrent options handled by debrid;
  Usenet and direct torrent are always notApplicable.
- [x] A failed debrid check creates a provider error/no option, never a false
  uncached result.
- [x] The common rule policy implements uncached exclusion for all/selected
  debrids with portable providerKind and exact providerId.
- [x] One configurable lexicographic sort supports resolution, cached, language,
  keyword, preferenceRule, RTN rank, quality, codec, HDR, audio, channels,
  subtitles, size, seeders, age, provider, transport, source, release group,
  and private, with direction/order/scope semantics.
- [x] Missing values always sort last; out-of-scope entries are neutral; stable
  immutable tie-breakers make ordering independent of async arrival.
- [x] Smart Comet, Instant, Quality and seeders, and Language-first presets
  expand to ordinary immediately editable criteria with no backend branches.
- [x] Default sorting fixes cache priority within resolution buckets and the old
  regression test is replaced with the intended invariant.
- [x] Alternatives implement cached/uncached/Usenet all|best,
  hideUncachedWhenCached, and direct always|unlessCached per exact candidate.
- [x] `[RD cached, AD uncached, TB uncached]` and all alternative combinations are
  tested, including all-uncached and Usenet/direct notApplicable behavior.
- [x] Limits run after sort and alternatives and cover total/resolution/quality/
  provider/transport/source/releaseGroup; all except provider count distinct
  release groups rather than provider duplications.
- [x] No filename-similarity/fuzzy merge influences alternatives or limits.

## Fallback capabilities

- [x] Hidden-but-authorized same-candidate/same-transport options can form a
  bounded sequential fallback chain; filtered options and direct torrent cannot.
- [x] Signed capability binds candidate, provider IDs, locator IDs, transport,
  expiry, client, and a bounded chain; server revalidates every binding.
- [x] Historical mono-provider capabilities still decode.
- [x] Forged, expired, cross-release, cross-transport, and filtered-chain
  capabilities fail closed; valid chains stop at the first playable URL.
- [x] One visible row retains real debrid or Usenet alternatives for fallback.

## Rendering and preview

- [x] Default, Compact, and Technical presets plus separate custom name and
  description templates use one backend renderer.
- [x] The bounded grammar supports fields, non-nested conditional blocks, and
  escaped braces only; templates/renders have centralized limits.
- [x] Templates compile once and cache by value; formatter fields come from the
  facts registry rather than parallel emoji/plain registries.
- [x] Stremio, ChillLink, Kodi, and preview render through the same engine while
  Kodi retains `cometKodiMetaV1` structured metadata.
- [x] The bounded preview endpoint uses a fixed example and performs no provider
  or network work; preview equals real rendering for identical context.
- [x] The duplicated React preview and old `resultFormat` formatting engine are
  removed after migration coverage proves compatibility.

## Auxiliary results and summary

- [x] Playable streams, errors, filter summary, sync actions, and blocking states
  are separate typed collections before final composition.
- [x] Common policy implements filterSummary off|whenEmpty|top|bottom and
  errors/debridSync off|top|bottom with stable error→summary→action ordering.
- [x] Defaults guarantee a playable index 0 whenever a playable stream exists,
  including Jellyfin/Remux with errors and sync actions present.
- [x] `debridSync: off` hides manual actions only; account-library search and
  background sync remain active.
- [x] One explicitly labelled action is retained per debrid provider.
- [x] Auxiliary rows never participate in sort, limits, fallback, playable count,
  or emptiness; truly blocking states remain visible when playback is impossible.
- [x] At most one safe deterministic summary counts distinct releases and
  provider options with dense compiled rejection IDs and no filenames, raw
  titles, secrets, credentials, URLs, or internal timings.
- [x] Summary-off allocates no per-release diagnostics or rendered reason text.

## Frontend, i18n, and portability

- [x] Five compact tabs (order, filters, delivery, appearance, advanced) expose
  every retained option end to end (model, codec, normalization, runtime, UI,
  i18n, tests), each showing how many settings are active.
- [x] One accessible pointer/keyboard reorder component is reused for providers,
  sort criteria, preferred languages, and ordered facet values, with a single
  shared drop indicator.
- [x] Closed dimensions are picked from the backend vocabulary as tri-state
  chips (neutral/only/exclude) and open ones as tag inputs; no configurator
  control asks for a canonical identifier to be typed by hand.
- [x] Every scope, criterion, action and placement identifier resolves to a
  translated label from the generated enum contract, with a one-line
  explanation wherever the identifier alone is not self-describing.
- [x] Simple facets/ranges/keywords, uncached shortcut, limits, alternatives,
  recipes, language mappings, flat advanced rules, display, auxiliary policy,
  and preview are all editable without hidden state.
- [x] Fixed display presets are rendered once at bootstrap, so switching preset
  updates the preview with no request; only custom templates are debounced.
- [x] `configure.resultFields.subtitles` exists in all 16 locales; locale parity
  and bootstrap-field translation contracts pass.
- [x] Shareable preferences export contains only results/languages and safely
  downgrades or omits non-portable providerId rules with a visible warning.
- [x] Full backup is download-only and clearly warns that credentials are
  included; full import has one sensitive confirmation.
- [x] Preferences import replaces only allowed roots; full import replaces the
  full document; both pass once through the canonical validator/normalizer.
- [x] Shareable exports cannot contain account paths, credentials, private
  endpoints, or addon URLs; no remote storage/template registry is introduced.

## Cleanup, contracts, and final evidence

- [x] Remove `_select_info_hashes_by_resolution`, ranking/presentation double
  limits, hard-coded presentation ordering, `base_streams`, old formatter style
  registries, React preview, allow-English UI/contracts/translations, canonical
  `options`, and dispersed legacy reads.
- [x] Backend format/lint/compile checks, generated-contract check, complete backend
  tests, frontend lint/typecheck/tests/build, and relevant integrations are green.
- [x] Property tests cover sort stability, unknown values, rule matcher bounds,
  release-vs-provider counting, and disabled-feature equivalence.
- [x] Benchmarks cover large batches and demonstrate one facts pass, one key pass,
  and one O(n log n) sort with near-zero disabled overhead.
- [x] Encoded sizes are recorded for default, advanced, and custom configurations;
  z2 is added only if measured improvement justifies it.
- [x] Final searches prove no rejected AIOStreams feature, TODO, dead legacy path,
  parallel engine, duplicated validation, or half-wired option remains in touched
  areas; the complete diff and this checklist are audited.
