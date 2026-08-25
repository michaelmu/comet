import type {
  ConfigModel,
  ConfiguratorBootstrapData,
  DiscoverySourceEntry,
  PlaybackProviderEntry,
} from "../../api/generated/contracts";

export interface DebridDraft {
  accountId: string;
  apiKey: string;
  configurationId: string;
  service: string;
}

export interface BindingDraft {
  accountId?: string;
  configurationId: string;
  displayName: string;
  enabled: boolean;
  kind: string;
  options: Record<string, unknown>;
}

export type ResultScope =
  | "all"
  | "cached"
  | "uncached"
  | "directTorrent"
  | "needsDownload"
  | "torrent"
  | "usenet";
export type SortDirection = "asc" | "desc";
export type SortKey =
  | "resolution"
  | "cached"
  | "language"
  | "keyword"
  | "preferenceRule"
  | "rank"
  | "quality"
  | "videoCodec"
  | "hdr"
  | "audio"
  | "channels"
  | "subtitles"
  | "size"
  | "seeders"
  | "age"
  | "provider"
  | "transport"
  | "source"
  | "releaseGroup"
  | "private";
export type DimensionKey =
  | "resolution"
  | "quality"
  | "visual"
  | "videoCodec"
  | "audio"
  | "channels"
  | "subtitles"
  | "releaseType"
  | "releaseGroup"
  | "edition"
  | "flags"
  | "source"
  | "transport"
  | "providerKind"
  | "providerId";
export type RangeKey = "playbackSize" | "releaseSize" | "seeders" | "ageDays" | "bitrate";

export interface FacetDraft {
  exclude: string[];
  only: string[];
}

export interface RangeDraft {
  max?: number;
  min?: number;
  scope: ResultScope;
}

export interface KeywordPatternDraft {
  mode: "word" | "phrase" | "wildcard";
  target: "title" | "releaseGroup" | "source";
  value: string;
}

export interface PredicateDraft {
  field: string;
  op: string;
  value?: string | number | boolean;
  values?: Array<string | number | boolean>;
}

export interface PolicyRuleDraft {
  action: "exclude" | "require" | "prefer" | "addLanguage";
  all: PredicateDraft[];
  id?: string;
  language?: string;
}

export interface SortCriterionDraft {
  direction: SortDirection;
  key: SortKey;
  order?: string[];
  scope: ResultScope;
}

export interface LimitRuleDraft {
  by: "total" | "resolution" | "quality" | "provider" | "transport" | "source" | "releaseGroup";
  max: number;
}

export interface ResultsDraft {
  alternatives: {
    cached: "all" | "best";
    direct: "always" | "unlessCached";
    fallback: boolean;
    hideUncachedWhenCached: boolean;
    uncached: "all" | "best";
    usenet: "all" | "best";
  };
  auxiliary: {
    debridSync: "off" | "top" | "bottom";
    errors: "off" | "top" | "bottom";
    filterSummary: "off" | "whenEmpty" | "top" | "bottom";
  };
  display: {
    description?: string;
    name?: string;
    preset: "default" | "compact" | "technical" | "custom";
  };
  filters: {
    dimensions: Record<DimensionKey, FacetDraft>;
    keywords: Record<"exclude" | "require" | "prefer", KeywordPatternDraft[]>;
    ranges: Partial<Record<RangeKey, RangeDraft>>;
    removeTrash: boolean;
    rules: PolicyRuleDraft[];
  };
  limits: LimitRuleDraft[];
  sort: SortCriterionDraft[];
}

export interface ConfigureFormValues {
  allowedLanguages: string[];
  bittorrentEnabled: boolean;
  debridServices: DebridDraft[];
  excludedLanguages: string[];
  nativeAccessToken: string;
  preferredLanguages: string[];
  proxyPassword: string;
  requiredLanguages: string[];
  results: ResultsDraft;
  schemaVersion: 1 | 2;
  scrapeDebridAccountTorrents: boolean;
  unknownLanguages: "allow" | "exclude";
  usenetEnabled: boolean;
  usenetProviders: BindingDraft[];
  usenetSources: BindingDraft[];
}

export const DIRECT_TORRENT_SERVICE = "direct_torrent";
export const NATIVE_USENET_PROVIDER = "comet_native_usenet";
export const DIMENSION_KEYS: readonly DimensionKey[] = [
  "resolution",
  "quality",
  "visual",
  "videoCodec",
  "audio",
  "channels",
  "subtitles",
  "releaseType",
  "releaseGroup",
  "edition",
  "flags",
  "source",
  "transport",
  "providerKind",
  "providerId",
];
export const RANGE_KEYS: readonly RangeKey[] = [
  "playbackSize",
  "releaseSize",
  "seeders",
  "ageDays",
  "bitrate",
];
const DEFAULT_SORT: readonly SortCriterionDraft[] = [
  { direction: "desc", key: "resolution", scope: "all" },
  { direction: "desc", key: "cached", scope: "all" },
  { direction: "desc", key: "language", scope: "all" },
  { direction: "desc", key: "rank", scope: "all" },
  { direction: "asc", key: "provider", scope: "all" },
];

type MutableConfiguration = { -readonly [Key in keyof ConfigModel]: ConfigModel[Key] };

function id(): string {
  return crypto.randomUUID();
}

function stringArray(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string")
    : [];
}

function resultsDraft(
  configuration: ConfigModel,
  bootstrap: ConfiguratorBootstrapData,
): ResultsDraft {
  const defaults = bootstrap.default_configuration.results ?? {};
  const source = configuration.results ?? defaults;
  const filters = source.filters ?? {};
  const defaultFilters = defaults.filters ?? {};
  const dimensions = filters.dimensions ?? {};
  const defaultDimensions = defaultFilters.dimensions ?? {};
  const keywords = filters.keywords ?? {};
  const defaultKeywords = defaultFilters.keywords ?? {};
  const alternatives = source.alternatives ?? {};
  const defaultAlternatives = defaults.alternatives ?? {};
  const auxiliary = source.auxiliary ?? {};
  const defaultAuxiliary = defaults.auxiliary ?? {};
  const display = source.display ?? {};
  const defaultDisplay = defaults.display ?? {};
  const displayDescription = display.description ?? defaultDisplay.description;
  const displayName = display.name ?? defaultDisplay.name;
  return {
    alternatives: {
      cached: alternatives.cached ?? defaultAlternatives.cached ?? "all",
      direct: alternatives.direct ?? defaultAlternatives.direct ?? "always",
      fallback: alternatives.fallback ?? defaultAlternatives.fallback ?? false,
      hideUncachedWhenCached:
        alternatives.hideUncachedWhenCached ?? defaultAlternatives.hideUncachedWhenCached ?? false,
      uncached: alternatives.uncached ?? defaultAlternatives.uncached ?? "all",
      usenet: alternatives.usenet ?? defaultAlternatives.usenet ?? "all",
    },
    auxiliary: {
      debridSync: auxiliary.debridSync ?? defaultAuxiliary.debridSync ?? "bottom",
      errors: auxiliary.errors ?? defaultAuxiliary.errors ?? "bottom",
      filterSummary: auxiliary.filterSummary ?? defaultAuxiliary.filterSummary ?? "whenEmpty",
    },
    display: {
      ...(displayDescription ? { description: displayDescription } : {}),
      ...(displayName ? { name: displayName } : {}),
      preset: display.preset ?? defaultDisplay.preset ?? "default",
    },
    filters: {
      dimensions: Object.fromEntries(
        DIMENSION_KEYS.map((key) => [
          key,
          {
            exclude: [...(dimensions[key]?.exclude ?? defaultDimensions[key]?.exclude ?? [])],
            only: [...(dimensions[key]?.only ?? defaultDimensions[key]?.only ?? [])],
          },
        ]),
      ) as Record<DimensionKey, FacetDraft>,
      keywords: {
        exclude: (keywords.exclude ?? defaultKeywords.exclude ?? []).map((item) => ({
          mode: item.mode ?? "phrase",
          target: item.target ?? "title",
          value: item.value,
        })),
        prefer: (keywords.prefer ?? defaultKeywords.prefer ?? []).map((item) => ({
          mode: item.mode ?? "phrase",
          target: item.target ?? "title",
          value: item.value,
        })),
        require: (keywords.require ?? defaultKeywords.require ?? []).map((item) => ({
          mode: item.mode ?? "phrase",
          target: item.target ?? "title",
          value: item.value,
        })),
      },
      ranges: Object.fromEntries(
        RANGE_KEYS.flatMap((key) => {
          const range = filters.ranges?.[key] ?? defaultFilters.ranges?.[key];
          return range
            ? [
                [
                  key,
                  {
                    ...(range.max == null ? {} : { max: range.max }),
                    ...(range.min == null ? {} : { min: range.min }),
                    scope: range.scope ?? "all",
                  },
                ],
              ]
            : [];
        }),
      ),
      removeTrash: filters.removeTrash ?? defaultFilters.removeTrash ?? true,
      rules: (filters.rules ?? defaultFilters.rules ?? []).map((rule) => ({
        action: rule.action,
        all: rule.all.map((predicate) => ({
          field: predicate.field,
          op: predicate.op,
          ...(predicate.value == null ? {} : { value: predicate.value }),
          ...(predicate.values == null ? {} : { values: [...predicate.values] }),
        })),
        ...(rule.id ? { id: rule.id } : {}),
        ...(rule.language ? { language: rule.language } : {}),
      })),
    },
    limits: (source.limits ?? defaults.limits ?? []).map((rule) => ({
      by: rule.by,
      max: rule.max,
    })),
    sort: (source.sort ?? defaults.sort ?? DEFAULT_SORT).map((criterion) => ({
      direction: criterion.direction ?? "desc",
      key: criterion.key,
      ...(criterion.order ? { order: [...criterion.order] } : {}),
      scope: criterion.scope ?? "all",
    })),
  };
}

export function formValues(
  configuration: ConfigModel,
  bootstrap: ConfiguratorBootstrapData,
): ConfigureFormValues {
  const accounts = configuration.accounts ?? {};
  const providers = configuration.playbackProviders ?? [];
  const debridKinds = new Set(bootstrap.debrid_services);
  const debridServices = providers
    .filter((provider) => debridKinds.has(provider.kind) && provider.enabled !== false)
    .map((provider) => ({
      accountId: provider.accountId ?? id(),
      apiKey: String(accounts[provider.accountId ?? ""]?.apiKey ?? ""),
      configurationId: provider.configurationId,
      service: provider.kind,
    }));

  if (debridServices.length === 0) {
    const legacy = configuration.debridServices?.length
      ? configuration.debridServices
      : configuration.debridService && configuration.debridService !== "torrent"
        ? [{ service: configuration.debridService, apiKey: configuration.debridApiKey ?? "" }]
        : [];
    debridServices.push(
      ...legacy.map((entry) => ({
        accountId: id(),
        apiKey: entry.apiKey ?? "",
        configurationId: id(),
        service: entry.service,
      })),
    );
  }

  const schemaVersion = configuration.schemaVersion === 2 ? 2 : 1;
  const directTorrent = providers.find(
    (provider) => provider.kind === DIRECT_TORRENT_SERVICE && provider.enabled !== false,
  );
  const directTorrentEnabled =
    schemaVersion === 2
      ? directTorrent !== undefined
      : configuration.enableTorrent === true || configuration.debridService === "torrent";
  if (directTorrentEnabled) {
    debridServices.push({
      accountId: "",
      apiKey: "",
      configurationId: directTorrent?.configurationId ?? id(),
      service: DIRECT_TORRENT_SERVICE,
    });
  }

  const languages = configuration.languages ?? {};
  return {
    allowedLanguages: stringArray(languages.allowed),
    bittorrentEnabled:
      schemaVersion === 1
        ? bootstrap.capabilities.torrent_streams
        : (configuration.enabledTransports ?? []).includes("bittorrent"),
    debridServices,
    excludedLanguages: stringArray(languages.exclude),
    nativeAccessToken: configuration.nativeAccessToken ?? "",
    preferredLanguages: stringArray(languages.preferred),
    proxyPassword: configuration.debridStreamProxyPassword ?? "",
    requiredLanguages: stringArray(languages.required),
    results: resultsDraft(configuration, bootstrap),
    schemaVersion,
    scrapeDebridAccountTorrents: configuration.scrapeDebridAccountTorrents === true,
    unknownLanguages: languages.unknown ?? "allow",
    usenetEnabled:
      schemaVersion === 2 && (configuration.enabledTransports ?? []).includes("usenet"),
    usenetProviders: providers
      .filter(
        (provider) => !debridKinds.has(provider.kind) && provider.kind !== DIRECT_TORRENT_SERVICE,
      )
      .map(bindingDraft),
    usenetSources: (configuration.discoverySources ?? []).map(bindingDraft),
  };
}

function bindingDraft(binding: PlaybackProviderEntry | DiscoverySourceEntry): BindingDraft {
  return {
    ...(binding.accountId ? { accountId: binding.accountId } : {}),
    configurationId: binding.configurationId,
    displayName: binding.displayName ?? binding.kind,
    enabled: binding.enabled !== false,
    kind: binding.kind,
    options: { ...binding.options },
  };
}

export function emptyBinding(kind: string, displayName: string): BindingDraft {
  return { configurationId: id(), displayName, enabled: true, kind, options: {} };
}

const LEGACY_RESULT_KEYS = [
  "cachedOnly",
  "removeTrash",
  "resultFormat",
  "maxResultsPerResolution",
  "maxSize",
  "resolutions",
  "options",
  "sortCachedUncachedTogether",
  "deduplicateStreams",
] as const;

function canonicalLoaded(loaded?: ConfigModel): ConfigModel {
  const document: Record<string, unknown> = { ...(loaded ?? {}) };
  for (const key of LEGACY_RESULT_KEYS) delete document[key];
  return document as ConfigModel;
}

function compactResults(results: ResultsDraft): NonNullable<ConfigModel["results"]> {
  const dimensions = Object.fromEntries(
    Object.entries(results.filters.dimensions).flatMap(([key, facet]) =>
      facet.only.length || facet.exclude.length
        ? [
            [
              key,
              {
                ...(facet.only.length ? { only: facet.only } : {}),
                ...(facet.exclude.length ? { exclude: facet.exclude } : {}),
              },
            ],
          ]
        : [],
    ),
  );
  const ranges = Object.fromEntries(
    Object.entries(results.filters.ranges).map(([key, range]) => [
      key,
      {
        ...(range?.min == null ? {} : { min: range.min }),
        ...(range?.max == null ? {} : { max: range.max }),
        ...(range?.scope && range.scope !== "all" ? { scope: range.scope } : {}),
      },
    ]),
  );
  const keywords = Object.fromEntries(
    Object.entries(results.filters.keywords).flatMap(([action, patterns]) => {
      const compact = patterns
        .filter(({ value }) => value.trim() !== "")
        .map(({ mode, target, value }) => ({
          value,
          ...(mode === "phrase" ? {} : { mode }),
          ...(target === "title" ? {} : { target }),
        }));
      return compact.length ? [[action, compact]] : [];
    }),
  );
  const filters = {
    ...(results.filters.removeTrash ? {} : { removeTrash: false }),
    ...(Object.keys(dimensions).length ? { dimensions } : {}),
    ...(Object.keys(ranges).length ? { ranges } : {}),
    ...(Object.keys(keywords).length ? { keywords } : {}),
    ...(results.filters.rules.length ? { rules: results.filters.rules } : {}),
  };
  const sort = results.sort.map(({ direction, key, order, scope }) => ({
    key,
    ...(direction === "desc" ? {} : { direction }),
    ...(scope === "all" ? {} : { scope }),
    ...(order?.length ? { order } : {}),
  }));
  const alternatives = {
    ...(results.alternatives.cached === "all" ? {} : { cached: results.alternatives.cached }),
    ...(results.alternatives.uncached === "all" ? {} : { uncached: results.alternatives.uncached }),
    ...(results.alternatives.usenet === "all" ? {} : { usenet: results.alternatives.usenet }),
    ...(results.alternatives.hideUncachedWhenCached ? { hideUncachedWhenCached: true } : {}),
    ...(results.alternatives.direct === "always" ? {} : { direct: results.alternatives.direct }),
    ...(results.alternatives.fallback ? { fallback: true } : {}),
  };
  const display =
    results.display.preset === "custom"
      ? {
          preset: "custom" as const,
          name: results.display.name ?? "",
          description: results.display.description ?? "",
        }
      : results.display.preset === "default"
        ? {}
        : { preset: results.display.preset };
  const auxiliary = {
    ...(results.auxiliary.filterSummary === "whenEmpty"
      ? {}
      : { filterSummary: results.auxiliary.filterSummary }),
    ...(results.auxiliary.errors === "bottom" ? {} : { errors: results.auxiliary.errors }),
    ...(results.auxiliary.debridSync === "bottom"
      ? {}
      : { debridSync: results.auxiliary.debridSync }),
  };
  return {
    ...(Object.keys(filters).length ? { filters } : {}),
    ...(JSON.stringify(sort) ===
    JSON.stringify(
      DEFAULT_SORT.map(({ direction, key }) => ({
        key,
        ...(direction === "desc" ? {} : { direction }),
      })),
    )
      ? {}
      : { sort }),
    ...(results.limits.some(({ max }) => max > 0)
      ? { limits: results.limits.filter(({ max }) => max > 0) }
      : {}),
    ...(Object.keys(alternatives).length ? { alternatives } : {}),
    ...(Object.keys(display).length ? { display } : {}),
    ...(Object.keys(auxiliary).length ? { auxiliary } : {}),
  } as NonNullable<ConfigModel["results"]>;
}

export function configurationDocument(
  values: ConfigureFormValues,
  bootstrap: ConfiguratorBootstrapData,
  loaded?: ConfigModel,
): ConfigModel {
  const debridServices = values.debridServices.filter(
    (entry) => entry.service !== DIRECT_TORRENT_SERVICE,
  );
  const directTorrent = values.debridServices.find(
    (entry) => entry.service === DIRECT_TORRENT_SERVICE,
  );
  const common: ConfigModel = {
    ...canonicalLoaded(loaded),
    debridStreamProxyPassword: values.proxyPassword,
    languages: {
      ...(values.allowedLanguages.length ? { allowed: values.allowedLanguages } : {}),
      ...(values.excludedLanguages.length ? { exclude: values.excludedLanguages } : {}),
      ...(values.preferredLanguages.length ? { preferred: values.preferredLanguages } : {}),
      ...(values.requiredLanguages.length ? { required: values.requiredLanguages } : {}),
      ...(values.unknownLanguages === "exclude" ? { unknown: "exclude" as const } : {}),
    },
    results: compactResults(values.results),
    scrapeDebridAccountTorrents: values.scrapeDebridAccountTorrents,
  };

  if (
    values.schemaVersion === 1 &&
    values.bittorrentEnabled &&
    !values.usenetEnabled &&
    !values.results.alternatives.fallback
  ) {
    const document: MutableConfiguration = {
      ...common,
      debridServices: debridServices.map(({ apiKey, service }) => ({ apiKey, service })),
      enableTorrent: directTorrent !== undefined,
      schemaVersion: 1,
    };
    delete document.accounts;
    delete document.debridApiKey;
    delete document.debridService;
    delete document.discoverySources;
    delete document.enabledTransports;
    delete document.nativeAccessToken;
    delete document.playbackProviders;
    return document;
  }

  const accounts: Record<string, Record<string, unknown>> = { ...(loaded?.accounts ?? {}) };
  const playbackProviders: PlaybackProviderEntry[] = debridServices.map((entry) => {
    const previous = loaded?.playbackProviders?.find(
      ({ configurationId }) => configurationId === entry.configurationId,
    );
    accounts[entry.accountId] = { apiKey: entry.apiKey, kind: entry.service };
    return {
      accountId: entry.accountId,
      configurationId: entry.configurationId,
      displayName: previous?.displayName ?? entry.service,
      enabled: true,
      kind: entry.service,
      options: {},
    };
  });
  if (directTorrent) {
    playbackProviders.push({
      configurationId: directTorrent.configurationId,
      displayName: DIRECT_TORRENT_SERVICE,
      enabled: true,
      kind: DIRECT_TORRENT_SERVICE,
      options: {},
    });
  }
  const activePlaybackKinds = new Set(playbackProviders.map(({ kind }) => kind));
  playbackProviders.push(
    ...(loaded?.playbackProviders ?? []).filter(
      (provider) =>
        provider.enabled === false &&
        (bootstrap.debrid_services.includes(provider.kind) ||
          provider.kind === DIRECT_TORRENT_SERVICE) &&
        !activePlaybackKinds.has(provider.kind),
    ),
  );
  if (values.usenetEnabled) playbackProviders.push(...values.usenetProviders.map(bindingDocument));
  const discoverySources = values.usenetEnabled ? values.usenetSources.map(bindingDocument) : [];
  const referencedAccounts = new Set(
    [...playbackProviders, ...discoverySources]
      .map((binding) => binding.accountId)
      .filter((accountId): accountId is string => accountId != null),
  );
  const nativeAccessToken =
    values.usenetEnabled &&
    values.nativeAccessToken &&
    values.usenetProviders.some(({ kind }) => kind === NATIVE_USENET_PROVIDER)
      ? values.nativeAccessToken
      : "";
  const document: MutableConfiguration = {
    ...common,
    accounts: Object.fromEntries(
      Object.entries(accounts).filter(([accountId]) => referencedAccounts.has(accountId)),
    ),
    discoverySources,
    enabledTransports: [
      ...(values.bittorrentEnabled ? ["bittorrent"] : []),
      ...(values.usenetEnabled ? ["usenet"] : []),
    ],
    ...(nativeAccessToken ? { nativeAccessToken } : {}),
    playbackProviders,
    schemaVersion: 2,
  };
  delete document.debridApiKey;
  delete document.debridService;
  delete document.debridServices;
  delete document.enableTorrent;
  return document;
}

function bindingDocument(binding: BindingDraft): PlaybackProviderEntry {
  return {
    ...(binding.accountId ? { accountId: binding.accountId } : {}),
    configurationId: binding.configurationId,
    displayName: binding.displayName,
    enabled: binding.enabled,
    kind: binding.kind,
    options: binding.options,
  };
}
