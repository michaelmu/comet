import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { formValues } from "./model";
import { LanguageStep } from "./PreferencesStep";
import { bootstrapFixture } from "./testing";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({ i18n: { resolvedLanguage: "en" }, t: (key: string) => key }),
}));

afterEach(cleanup);

const bootstrap = bootstrapFixture({
  default_configuration: { results: {} },
  languages: { en: "🇬🇧", fr: "🇫🇷" },
  result_scopes: ["all"],
  result_sort_keys: ["resolution"],
});

describe("LanguageStep", () => {
  it("shows preferred languages in their canonical order and exposes unknown policy", () => {
    const values = formValues(
      { languages: { preferred: ["fr", "en"], unknown: "exclude" }, results: {} },
      bootstrap,
    );
    render(<LanguageStep bootstrap={bootstrap} onChange={vi.fn()} values={values} />);

    const rows = screen.getAllByRole("button", {
      name: /configure.resultsEditor.reorderLanguages/,
    });
    expect(rows).toHaveLength(2);
    const ordered = screen.getByRole("list", {
      name: "configure.resultsEditor.reorderLanguages",
    });
    expect(within(ordered).getByText("🇫🇷 French")).toBeVisible();
    expect(within(ordered).getByText("🇬🇧 English")).toBeVisible();
    expect(screen.getByText("configure.resultsEditor.exclude")).toBeVisible();
  });
});
