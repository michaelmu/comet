import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ConfigModel } from "../../api/generated/contracts";
import { configurationExport } from "./portability";
import { ResultsPortability } from "./ResultsPortability";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));

const configuration = {
  playbackProviders: [
    {
      configurationId: "provider-1",
      displayName: "Living room",
      kind: "realdebrid",
      options: {},
    },
    {
      configurationId: "provider-2",
      displayName: "Bedroom",
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
          id: "private-provider-rule",
        },
      ],
    },
  },
} satisfies ConfigModel;

describe("ResultsPortability", () => {
  beforeEach(() => {
    vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:export");
    vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => undefined);
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText: vi.fn().mockResolvedValue(undefined) },
    });
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("warns before a non-portable preference rule is downloaded", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    render(<ResultsPortability configuration={configuration} onImport={vi.fn()} />);

    fireEvent.click(
      screen.getByRole("button", { name: "configure.resultsEditor.exportPreferences" }),
    );

    await waitFor(() => expect(URL.createObjectURL).toHaveBeenCalledOnce());
    expect(confirm).toHaveBeenNthCalledWith(1, "configure.resultsEditor.shareTextConfirm");
    expect(confirm).toHaveBeenNthCalledWith(2, "configure.resultsEditor.portableExportConfirm");
    expect(screen.getByRole("status")).toHaveTextContent("configure.resultsEditor.omittedRules");
  });

  it("keeps full backup download-only and passes a full import to its validator once", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    const onImport = vi.fn().mockResolvedValue(undefined);
    const { container } = render(
      <ResultsPortability configuration={configuration} onImport={onImport} />,
    );

    fireEvent.click(screen.getByRole("button", { name: "configure.resultsEditor.exportFull" }));
    await waitFor(() => expect(URL.createObjectURL).toHaveBeenCalledOnce());
    expect(navigator.clipboard.writeText).not.toHaveBeenCalled();

    const full = configurationExport({ results: { display: { preset: "technical" } } }, "full");
    const input = container.querySelector<HTMLInputElement>('input[type="file"]');
    expect(input).not.toBeNull();
    const file = new File([full.text], "comet-full.json", { type: "application/json" });
    fireEvent.change(input as HTMLInputElement, { target: { files: [file] } });

    await waitFor(() => expect(onImport).toHaveBeenCalledOnce());
    expect(confirm).toHaveBeenCalledTimes(2);
    expect(onImport.mock.calls[0]?.[0].results?.display?.preset).toBe("technical");
  });
});
