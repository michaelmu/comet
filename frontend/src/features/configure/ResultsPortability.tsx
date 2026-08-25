import { Clipboard, Download, Upload } from "lucide-react";
import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import type { ConfigModel } from "../../api/generated/contracts";
import { Alert } from "../../components/ui/Alert";
import { Button } from "../../components/ui/Button";
import {
  type ConfigurationExportScope,
  configurationExport,
  mergeConfigurationImport,
  parseConfigurationImport,
  sharedPreferenceText,
} from "./portability";

function download(text: string, scope: ConfigurationExportScope) {
  const url = URL.createObjectURL(new Blob([text], { type: "application/json" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = `comet-${scope}-${new Date().toISOString().slice(0, 10)}.json`;
  link.click();
  URL.revokeObjectURL(url);
}

export function ResultsPortability({
  configuration,
  onImport,
}: {
  configuration: ConfigModel;
  onImport: (configuration: ConfigModel) => Promise<void>;
}) {
  const { t } = useTranslation();
  const input = useRef<HTMLInputElement>(null);
  const [message, setMessage] = useState<{
    text: string;
    tone: "danger" | "success" | "warning";
  } | null>(null);
  const sharedText = sharedPreferenceText(configuration);
  const warningText = (warnings: { downgradedRules: number; omittedRules: number }) =>
    [
      ...(warnings.downgradedRules
        ? [t("configure.resultsEditor.portableRules", { count: warnings.downgradedRules })]
        : []),
      ...(warnings.omittedRules
        ? [t("configure.resultsEditor.omittedRules", { count: warnings.omittedRules })]
        : []),
    ].join(" ");
  const exportScope = async (scope: ConfigurationExportScope, copy = false) => {
    try {
      if (scope === "full" && !window.confirm(t("configure.resultsEditor.fullExportConfirm"))) {
        return;
      }
      if (
        scope === "preferences" &&
        sharedText.length > 0 &&
        !window.confirm(t("configure.resultsEditor.shareTextConfirm", { count: sharedText.length }))
      ) {
        return;
      }
      const result = configurationExport(configuration, scope);
      const warnings = warningText(result.warnings);
      if (
        warnings &&
        !window.confirm(t("configure.resultsEditor.portableExportConfirm", { warning: warnings }))
      ) {
        return;
      }
      if (copy) await navigator.clipboard.writeText(result.text);
      else download(result.text, scope);
      setMessage(
        warnings
          ? { text: warnings, tone: "warning" }
          : { text: t("configure.resultsEditor.exported"), tone: "success" },
      );
    } catch {
      setMessage({ text: t("configure.resultsEditor.exportError"), tone: "danger" });
    }
  };
  const importFile = async (file: File) => {
    try {
      if (file.size > 256 * 1024) throw new Error("too_large");
      const imported = parseConfigurationImport(await file.text());
      if (
        imported.scope === "full" &&
        !window.confirm(t("configure.resultsEditor.fullImportConfirm"))
      )
        return;
      await onImport(mergeConfigurationImport(configuration, imported));
      setMessage({ text: t("configure.resultsEditor.imported"), tone: "success" });
    } catch {
      setMessage({ text: t("configure.resultsEditor.importError"), tone: "danger" });
    } finally {
      if (input.current) input.current.value = "";
    }
  };
  return (
    <div className="results-portability">
      {message ? <Alert tone={message.tone}>{message.text}</Alert> : null}
      <input
        accept="application/json,.json"
        className="visually-hidden"
        onChange={(event) => {
          const file = event.target.files?.[0];
          if (file) void importFile(file);
        }}
        ref={input}
        type="file"
      />
      <div className="option-stack">
        <Button onClick={() => input.current?.click()} variant="secondary">
          <Upload aria-hidden="true" size={16} />
          {t("configure.resultsEditor.import")}
        </Button>
        <Button onClick={() => void exportScope("preferences")} variant="secondary">
          <Download aria-hidden="true" size={16} />
          {t("configure.resultsEditor.exportPreferences")}
        </Button>
        <Button onClick={() => void exportScope("preferences", true)} variant="secondary">
          <Clipboard aria-hidden="true" size={16} />
          {t("configure.resultsEditor.copyPreferences")}
        </Button>
        <Button onClick={() => void exportScope("full")} variant="secondary">
          <Download aria-hidden="true" size={16} />
          {t("configure.resultsEditor.exportFull")}
        </Button>
      </div>
      {sharedText.length > 0 ? (
        <details className="shared-text-summary">
          <summary>{t("configure.resultsEditor.sharedText", { count: sharedText.length })}</summary>
          <ul>
            {sharedText.map((value) => (
              <li key={value}>
                <code>{value}</code>
              </li>
            ))}
          </ul>
        </details>
      ) : null}
      <small>{t("configure.resultsEditor.fullExportWarning")}</small>
    </div>
  );
}
