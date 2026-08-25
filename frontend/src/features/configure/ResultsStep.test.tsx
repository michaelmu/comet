import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { formValues } from "./model";
import { ResultsStep } from "./ResultsStep";
import { bootstrapFixture } from "./testing";

const { previewResults } = vi.hoisted(() => ({
  previewResults: vi.fn().mockResolvedValue({
    description: "Custom description",
    name: "Custom name",
  }),
}));

vi.mock("./api", () => ({ previewResults }));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ i18n: { resolvedLanguage: "en" }, t: (key: string) => key }),
}));

Element.prototype.scrollIntoView = vi.fn();

afterEach(() => {
  cleanup();
  previewResults.mockClear();
});

const resultFields = ["title", "size", "provider.short"];

const bootstrap = bootstrapFixture({
  debrid_services: ["realdebrid", "torbox"],
  result_display_presets: {
    compact: { description: "Compact description", name: "Compact name" },
    default: { description: "Default description", name: "Default name" },
    technical: { description: "Technical description", name: "Technical name" },
  },
  result_facets: {
    quality: ["remux", "bluray", "webdl"],
    resolution: ["2160p", "1080p", "720p"],
  },
  result_fields: resultFields,
  result_policy_fields: ["seeders", "private", "ageDays", "title"],
  result_scopes: ["all", "cached", "needsDownload"],
  result_sort_keys: ["resolution", "seeders"],
  result_sort_presets: {},
  result_sort_vocabulary: { resolution: ["2160p", "1080p", "720p"] },
});

function renderStep(results: ReturnType<typeof formValues>["results"], onChange = vi.fn()) {
  render(
    <ResultsStep
      bootstrap={bootstrap}
      configuredDebridKinds={["realdebrid"]}
      onChange={onChange}
      portability={<span>portability</span>}
      results={results}
      showDebridSync={false}
    />,
  );
  return onChange;
}

function openTab(id: string) {
  fireEvent.click(screen.getByRole("button", { name: `configure.resultsEditor.tab.${id}` }));
}

describe("ResultsStep", () => {
  it("reorders the canonical resolution values from the criterion detail panel", () => {
    const { results } = formValues(
      { results: { sort: [{ direction: "desc", key: "resolution", scope: "all" }] } },
      bootstrap,
    );
    const onChange = renderStep(results);

    fireEvent.click(
      screen.getByRole("button", { name: "configure.resultsEditor.criterionOptions" }),
    );
    const list = screen.getByRole("list", { name: "configure.resultsEditor.reorderValues" });
    expect(
      within(list)
        .getAllByRole("listitem")
        .map((row) => row.textContent),
    ).toEqual([
      "1configure.resultsEditor.values.2160p",
      "2configure.resultsEditor.values.1080p",
      "3configure.resultsEditor.values.720p",
    ]);

    fireEvent.keyDown(
      screen.getByRole("button", {
        name: /reorderValues: configure\.resultsEditor\.values\.2160p/,
      }),
      { key: "ArrowDown" },
    );
    expect(onChange.mock.calls.at(-1)?.[0].sort[0].order).toEqual(["1080p", "2160p", "720p"]);
  });

  it("cycles a facet value between neutral, only and excluded", () => {
    const { results } = formValues({ results: {} }, bootstrap);
    const onChange = renderStep(results);
    openTab("filters");

    const chip = screen.getByRole("button", { name: /values\.2160p —/ });
    fireEvent.click(chip);
    expect(onChange.mock.calls.at(-1)?.[0].filters.dimensions.resolution).toEqual({
      exclude: [],
      only: ["2160p"],
    });
  });

  it("previews fixed presets from the bootstrap without calling the API", () => {
    const { results } = formValues({ results: { display: { preset: "compact" } } }, bootstrap);
    renderStep(results);
    openTab("appearance");

    expect(screen.getByText("Compact name")).toBeInTheDocument();
    expect(previewResults).not.toHaveBeenCalled();
  });

  it("previews custom templates through the shared backend renderer", async () => {
    const { results } = formValues(
      {
        results: {
          display: { description: "{title}", name: "{provider.short}", preset: "custom" },
        },
      },
      bootstrap,
    );
    const onChange = renderStep(results);
    openTab("appearance");

    await waitFor(() =>
      expect(previewResults).toHaveBeenCalledWith({
        description: "{title}",
        name: "{provider.short}",
        preset: "custom",
      }),
    );
    expect(await screen.findByText("Custom name")).toBeInTheDocument();
    // One palette writes into whichever template is focused.
    const palette = screen.getByRole("group", { name: /configure.resultsEditor.insertInto/ });
    fireEvent.focus(screen.getByLabelText("configure.resultsEditor.descriptionTemplate"));
    fireEvent.click(within(palette).getByRole("button", { name: "configure.resultFields.size" }));
    expect(onChange.mock.calls.at(-1)?.[0].display).toMatchObject({
      description: "{title}{size}",
      name: "{provider.short}",
    });
  });

  it("shows only configured debrids and hides the manual sync policy when scraping is off", () => {
    const { results } = formValues({ results: {} }, bootstrap);
    renderStep(results);

    openTab("filters");
    fireEvent.click(
      screen.getByRole("switch", { name: "configure.resultsEditor.excludeUncached" }),
    );
    openTab("advanced");
    expect(
      screen.queryByRole("combobox", { name: "configure.resultsEditor.values.debridSync" }),
    ).not.toBeInTheDocument();
  });

  it("counts every configured value and keeps the uncached shortcut out of the rules editor", () => {
    const { results } = formValues(
      {
        results: {
          filters: {
            dimensions: { transport: { only: ["debridTorrent", "directTorrent"] } },
            ranges: { seeders: { min: 5 } },
          },
        },
      },
      bootstrap,
    );
    const onChange = renderStep(results);

    const badge = (tab: string) =>
      screen.getByRole("button", { name: `configure.resultsEditor.tab.${tab}` }).textContent;
    expect(badge("filters")).toContain("3");

    openTab("filters");
    fireEvent.click(
      screen.getByRole("switch", { name: "configure.resultsEditor.excludeUncached" }),
    );
    const withShortcut = onChange.mock.calls.at(-1)?.[0];
    expect(withShortcut.filters.rules).toHaveLength(1);

    cleanup();
    renderStep(withShortcut);
    expect(
      screen.getByRole("button", { name: "configure.resultsEditor.tab.advanced" }).textContent,
    ).not.toContain("1");
    openTab("advanced");
    expect(screen.queryByRole("group", { name: /configure.resultsEditor.rule/ })).toBeNull();
  });

  it("emits numeric and boolean rule operands with their canonical scalar types", () => {
    const { results } = formValues(
      {
        results: {
          filters: {
            rules: [
              { action: "require", all: [{ field: "seeders", op: "gte", value: 1 }] },
              { action: "exclude", all: [{ field: "private", op: "is", value: true }] },
              { action: "require", all: [{ field: "ageDays", op: "between", values: [1, 2] }] },
            ],
          },
        },
      },
      bootstrap,
    );
    const onChange = renderStep(results);
    openTab("advanced");

    fireEvent.change(screen.getByRole("spinbutton", { name: "configure.resultsEditor.value" }), {
      target: { value: "7" },
    });
    expect(onChange.mock.calls.at(-1)?.[0].filters.rules[0].all[0].value).toBe(7);

    fireEvent.change(screen.getByRole("textbox", { name: "configure.resultsEditor.value" }), {
      target: { value: "3, 9" },
    });
    expect(onChange.mock.calls.at(-1)?.[0].filters.rules[2].all[0].values).toEqual([3, 9]);

    const booleanValue = screen.getByRole("combobox", { name: "configure.resultsEditor.value" });
    fireEvent.click(booleanValue);
    fireEvent.click(screen.getByRole("option", { name: "configure.resultsEditor.values.false" }));
    expect(onChange.mock.calls.at(-1)?.[0].filters.rules[1].all[0].value).toBe(false);
  });
});
