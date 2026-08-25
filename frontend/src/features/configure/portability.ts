import { z } from "zod";
import type {
  ConfigModel,
  PolicyPredicate,
  PolicyRule,
  ResultsConfig,
} from "../../api/generated/contracts";

export type ConfigurationExportScope = "preferences" | "full";

const envelopeSchema = z.strictObject({
  configuration: z.record(z.string(), z.unknown()),
  format: z.literal("comet-config"),
  scope: z.enum(["preferences", "full"]),
  version: z.literal(1),
});

export interface ConfigurationEnvelope {
  configuration: ConfigModel;
  format: "comet-config";
  scope: ConfigurationExportScope;
  version: 1;
}

export interface PortabilityWarnings {
  downgradedRules: number;
  omittedRules: number;
}

function portableResults(configuration: ConfigModel): {
  results: ConfigModel["results"];
  warnings: PortabilityWarnings;
} {
  const providers = configuration.playbackProviders ?? [];
  const providerKinds = new Map(
    providers.map((provider) => [provider.configurationId, provider.kind]),
  );
  const kindCounts = new Map<string, number>();
  for (const provider of providers) {
    kindCounts.set(provider.kind, (kindCounts.get(provider.kind) ?? 0) + 1);
  }
  const warnings: PortabilityWarnings = { downgradedRules: 0, omittedRules: 0 };
  const rules: PolicyRule[] = (configuration.results?.filters?.rules ?? []).flatMap((rule) => {
    if (!rule.all.some((predicate) => predicate.field === "providerId")) return [rule];
    const rewritten: Array<PolicyPredicate | null> = rule.all.map((predicate) => {
      if (predicate.field !== "providerId") return predicate;
      const identifiers = predicate.values ?? (predicate.value == null ? [] : [predicate.value]);
      const kinds = identifiers.flatMap((identifier) => {
        const kind = typeof identifier === "string" ? providerKinds.get(identifier) : undefined;
        return kind ? [kind] : [];
      });
      const uniqueKinds = [...new Set(kinds)];
      if (
        uniqueKinds.length !== identifiers.length ||
        uniqueKinds.some((kind) => kindCounts.get(kind) !== 1)
      ) {
        return null;
      }
      if (predicate.values) {
        const { value: _discarded, ...remaining } = predicate;
        return { ...remaining, field: "providerKind", values: uniqueKinds };
      }
      const { values: _discarded, ...remaining } = predicate;
      const value = uniqueKinds[0];
      return value === undefined ? null : { ...remaining, field: "providerKind", value };
    });
    if (rewritten.some((predicate) => predicate === null)) {
      warnings.omittedRules += 1;
      return [];
    }
    warnings.downgradedRules += 1;
    return [
      {
        ...rule,
        all: rewritten.filter((predicate): predicate is PolicyPredicate => predicate !== null),
      },
    ];
  });
  const results: ResultsConfig | undefined = configuration.results
    ? ({
        ...configuration.results,
        filters: { ...configuration.results.filters, rules },
      } as ResultsConfig)
    : undefined;
  return { results, warnings };
}

export function configurationExport(
  configuration: ConfigModel,
  scope: ConfigurationExportScope,
): { text: string; warnings: PortabilityWarnings } {
  const portable = portableResults(configuration);
  const exportedConfiguration: ConfigModel =
    scope === "preferences"
      ? {
          ...(configuration.languages ? { languages: configuration.languages } : {}),
          ...(portable.results ? { results: portable.results } : {}),
        }
      : configuration;
  return {
    text: JSON.stringify(
      {
        configuration: exportedConfiguration,
        format: "comet-config",
        scope,
        version: 1,
      } satisfies ConfigurationEnvelope,
      null,
      2,
    ),
    warnings: scope === "preferences" ? portable.warnings : { downgradedRules: 0, omittedRules: 0 },
  };
}

/**
 * Free text the user typed, as opposed to canonical values picked from Comet's
 * own vocabulary: only these can carry something personal into a shared export.
 */
const OPEN_TEXT_DIMENSIONS = ["releaseGroup", "edition", "source", "providerId"] as const;

export function sharedPreferenceText(configuration: ConfigModel): string[] {
  const results = configuration.results;
  const dimensions = results?.filters?.dimensions;
  const values = [
    ...OPEN_TEXT_DIMENSIONS.flatMap((key) => [
      ...(dimensions?.[key]?.only ?? []),
      ...(dimensions?.[key]?.exclude ?? []),
    ]),
    ...(results?.filters?.keywords?.exclude ?? []).map(({ value }) => value),
    ...(results?.filters?.keywords?.require ?? []).map(({ value }) => value),
    ...(results?.filters?.keywords?.prefer ?? []).map(({ value }) => value),
    ...(results?.filters?.rules ?? []).flatMap((rule) => [
      ...(rule.id ? [rule.id] : []),
      ...rule.all.flatMap((predicate) => [
        ...(typeof predicate.value === "string" ? [predicate.value] : []),
        ...(predicate.values ?? []).flatMap((value) => (typeof value === "string" ? [value] : [])),
      ]),
    ]),
    ...(results?.display?.preset === "custom"
      ? [results.display.name ?? "", results.display.description ?? ""]
      : []),
  ];
  return [...new Set(values.map((value) => value.trim()).filter(Boolean))];
}

export function parseConfigurationImport(text: string): ConfigurationEnvelope {
  return envelopeSchema.parse(JSON.parse(text)) as ConfigurationEnvelope;
}

export function mergeConfigurationImport(
  current: ConfigModel,
  imported: ConfigurationEnvelope,
): ConfigModel {
  if (imported.scope === "full") return imported.configuration;
  const merged: Record<string, unknown> = {
    ...current,
    languages: imported.configuration.languages,
    results: imported.configuration.results,
  };
  if (!imported.configuration.languages) delete merged.languages;
  if (!imported.configuration.results) delete merged.results;
  return merged as ConfigModel;
}
