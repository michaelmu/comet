import { describe, expect, it } from "vitest";
import type { ConfigModel } from "../../api/generated/contracts";
import {
  configurationExport,
  mergeConfigurationImport,
  parseConfigurationImport,
  sharedPreferenceText,
} from "./portability";

const configuration = {
  accounts: { account: { apiKey: "secret-token", endpoint: "https://private.example" } },
  languages: { preferred: ["fr", "en"], unknown: "exclude" },
  playbackProviders: [
    {
      accountId: "account",
      configurationId: "provider-1",
      displayName: "Living room",
      kind: "realdebrid",
      options: {},
    },
  ],
  results: {
    filters: {
      rules: [
        {
          action: "exclude",
          all: [{ field: "providerId", op: "is", value: "provider-1" }],
          id: "portable-provider",
        },
      ],
    },
  },
  schemaVersion: 2,
} satisfies ConfigModel;

describe("configuration portability", () => {
  it("exports preferences without secrets and downgrades exact provider IDs", () => {
    const exported = configurationExport(configuration, "preferences");
    const document = parseConfigurationImport(exported.text);

    expect(exported.text).not.toContain("secret-token");
    expect(exported.text).not.toContain("private.example");
    expect(exported.text).not.toContain("provider-1");
    expect(Object.keys(document.configuration).sort()).toEqual(["languages", "results"]);
    expect(document.configuration.results?.filters?.rules?.[0]?.all[0]).toMatchObject({
      field: "providerKind",
      value: "realdebrid",
    });
    expect(exported.warnings).toEqual({ downgradedRules: 1, omittedRules: 0 });
  });

  it("omits exact provider rules when a provider kind has multiple bindings", () => {
    const duplicate = {
      ...configuration,
      playbackProviders: [
        ...(configuration.playbackProviders ?? []),
        {
          configurationId: "provider-2",
          displayName: "Bedroom",
          kind: "realdebrid",
          options: {},
        },
      ],
    } satisfies ConfigModel;
    const exported = configurationExport(duplicate, "preferences");

    expect(exported.text).not.toContain("provider-1");
    expect(exported.text).not.toContain("portable-provider");
    expect(exported.warnings).toEqual({ downgradedRules: 0, omittedRules: 1 });
  });

  it("keeps account bindings on preferences import and replaces them on full import", () => {
    const preferences = parseConfigurationImport(
      configurationExport({ results: { display: { preset: "compact" } } }, "preferences").text,
    );
    expect(mergeConfigurationImport(configuration, preferences).accounts).toEqual(
      configuration.accounts,
    );

    const full = parseConfigurationImport(
      configurationExport({ results: { display: { preset: "technical" } } }, "full").text,
    );
    expect(mergeConfigurationImport(configuration, full).accounts).toBeUndefined();
  });

  it("replaces both preference roots, including deleting an absent root", () => {
    const imported = parseConfigurationImport(configurationExport({}, "preferences").text);
    const merged = mergeConfigurationImport(configuration, imported);

    expect(merged.languages).toBeUndefined();
    expect(merged.results).toBeUndefined();
    expect(merged.accounts).toEqual(configuration.accounts);
    expect(merged.playbackProviders).toEqual(configuration.playbackProviders);
  });

  it("summarizes typed text and ignores values picked from Comet's own vocabulary", () => {
    const values = sharedPreferenceText({
      languages: { allowed: ["en"], preferred: ["fr"] },
      results: {
        display: { description: "My description", name: "My name", preset: "custom" },
        filters: {
          dimensions: {
            quality: { exclude: ["cam"], only: ["webdl"] },
            releaseGroup: { only: ["FraMeSToR"] },
          },
          keywords: { exclude: [{ value: "AI upscale" }] },
          rules: [
            {
              action: "addLanguage",
              all: [{ field: "releaseGroup", op: "is", value: "GROUP" }],
              id: "language-map",
              language: "pt",
            },
          ],
        },
        sort: [{ key: "quality", order: ["remux", "webdl"] }],
      },
    });

    expect(values.sort()).toEqual([
      "AI upscale",
      "FraMeSToR",
      "GROUP",
      "My description",
      "My name",
      "language-map",
    ]);
  });
});
