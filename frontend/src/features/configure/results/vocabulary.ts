import { useTranslation } from "react-i18next";
import type {
  DimensionKey,
  FacetDraft,
  PolicyRuleDraft,
  RangeKey,
  ResultScope,
  SortKey,
} from "../model";

/**
 * Scopes restrict a criterion or a range to part of the results. They mix two
 * independent questions, so the picker groups them instead of listing seven
 * opaque identifiers side by side.
 */
export const SCOPE_GROUPS: readonly { id: string; scopes: readonly ResultScope[] }[] = [
  { id: "playback", scopes: ["cached", "uncached", "needsDownload", "directTorrent"] },
  { id: "origin", scopes: ["torrent", "usenet"] },
];

/** Facets whose values are free text rather than a closed vocabulary. */
const OPEN_FACETS = new Set<DimensionKey>(["edition", "releaseGroup", "source", "providerId"]);

export const FILTER_PANELS: readonly {
  facets: readonly DimensionKey[];
  id: string;
  ranges?: readonly RangeKey[];
}[] = [
  { facets: ["resolution", "quality"], id: "picture" },
  { facets: ["visual", "videoCodec"], id: "image" },
  { facets: ["audio", "channels", "subtitles"], id: "sound" },
  { facets: ["releaseType", "flags", "edition", "releaseGroup"], id: "release" },
  { facets: ["transport", "source", "providerKind"], id: "origin" },
  {
    facets: [],
    id: "measures",
    ranges: ["playbackSize", "releaseSize", "seeders", "ageDays", "bitrate"],
  },
];

/** Sort keys ordered by an explicit list of free-text values. */
const OPEN_SORT_ORDERS = new Set<SortKey>(["source", "releaseGroup"]);
/** Sort keys whose order is owned by another configuration section. */
export const DELEGATED_SORT_ORDERS: Partial<Record<SortKey, string>> = {
  language: "languages",
  provider: "playback",
  subtitles: "languages",
};

export type FacetControl = "chips" | "languages" | "providerKinds" | "tags";

export function facetControl(key: DimensionKey): FacetControl {
  if (key === "subtitles") return "languages";
  if (key === "providerKind") return "providerKinds";
  return OPEN_FACETS.has(key) ? "tags" : "chips";
}

export function sortOrderControl(
  key: SortKey,
  vocabulary: Readonly<Record<string, readonly string[]>>,
): "none" | "tags" | "values" {
  if (vocabulary[key]) return "values";
  return OPEN_SORT_ORDERS.has(key) ? "tags" : "none";
}

/** Byte-friendly units so size ranges are typed in GB rather than in bytes. */
export const BYTES_PER_GIGABYTE = 1_000_000_000;
export const RANGE_UNITS: Partial<Record<RangeKey, number>> = {
  playbackSize: BYTES_PER_GIGABYTE,
  releaseSize: BYTES_PER_GIGABYTE,
  bitrate: 1_000_000,
};

/**
 * The "exclude uncached debrid results" switch serializes a plain exclusion
 * rule. Both the switch and the advanced rule editor must agree on which rules
 * it owns, so the shape is recognized in exactly one place.
 */
export function isUncachedShortcut(rule: PolicyRuleDraft): boolean {
  return (
    rule.action === "exclude" &&
    rule.all.length <= 2 &&
    rule.all.some(
      (predicate) =>
        predicate.field === "cacheState" && predicate.op === "is" && predicate.value === "uncached",
    ) &&
    rule.all.every(
      (predicate) =>
        predicate.field === "cacheState" ||
        (predicate.field === "providerKind" && predicate.op === "oneOf"),
    )
  );
}

/** Badges count configured items, so every selected value counts for one. */
export function facetCount(facet: FacetDraft): number {
  return facet.only.length + facet.exclude.length;
}

/** Identifiers that describe the same fact and therefore share one explanation. */
const HINT_ALIASES: Record<string, string> = { size: "playbackSize" };

export function useResultLabels() {
  const { t } = useTranslation();
  return {
    /** Canonical identifier → localized label, with the identifier as last resort. */
    label: (identifier: string) =>
      t(`configure.resultsEditor.values.${identifier}`, { defaultValue: identifier }),
    /** Optional one-line explanation; absent hints render nothing. */
    hint: (identifier: string) =>
      t(`configure.resultsEditor.hints.${HINT_ALIASES[identifier] ?? identifier}`, {
        defaultValue: "",
      }) || undefined,
    t,
  };
}
