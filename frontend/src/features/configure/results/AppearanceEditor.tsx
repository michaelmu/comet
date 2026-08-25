import { type RefObject, useEffect, useRef, useState } from "react";
import type {
  ConfiguratorBootstrapData,
  ResultsPreviewData,
} from "../../../api/generated/contracts";
import { Alert } from "../../../components/ui/Alert";
import { previewResults } from "../api";
import type { ResultsDraft } from "../model";
import { Panel } from "./controls";
import { useResultLabels } from "./vocabulary";

const PRESETS = ["default", "compact", "technical", "custom"] as const;
const TEMPLATES = ["name", "description"] as const;
type TemplatePart = (typeof TEMPLATES)[number];
const CUSTOM_STARTER = {
  description: "{?title}📄 {title}{/title}\n{?video}📹 {video}{/video}",
  name: "[{provider.short} {cache.icon}] Comet {resolution}",
};

/**
 * Fixed presets are rendered by the backend at bootstrap, so switching preset
 * updates the preview instantly. Only the custom templates need a round trip,
 * and only those are debounced.
 */
function usePreview(
  display: ResultsDraft["display"],
  presets: ConfiguratorBootstrapData["result_display_presets"],
): { error: boolean; preview: ResultsPreviewData | null } {
  const [custom, setCustom] = useState<ResultsPreviewData | null>(null);
  const [error, setError] = useState(false);
  const name = display.name ?? "";
  const description = display.description ?? "";
  const isCustom = display.preset === "custom";
  useEffect(() => {
    if (!isCustom) return;
    let active = true;
    const timeout = window.setTimeout(() => {
      void previewResults({ description, name, preset: "custom" })
        .then((result) => {
          if (!active) return;
          setCustom(result);
          setError(false);
        })
        .catch(() => active && setError(true));
    }, 300);
    return () => {
      active = false;
      window.clearTimeout(timeout);
    };
  }, [description, isCustom, name]);
  return {
    error: isCustom && error,
    preview: isCustom ? custom : (presets[display.preset] ?? null),
  };
}

export function AppearanceEditor({
  bootstrap,
  display,
  onChange,
}: {
  bootstrap: ConfiguratorBootstrapData;
  display: ResultsDraft["display"];
  onChange: (display: ResultsDraft["display"]) => void;
}) {
  const labels = useResultLabels();
  const { error, preview } = usePreview(display, bootstrap.result_display_presets);
  const editors: Record<TemplatePart, RefObject<HTMLTextAreaElement | null>> = {
    description: useRef<HTMLTextAreaElement>(null),
    name: useRef<HTMLTextAreaElement>(null),
  };
  // One palette serves both templates: it writes into the one being edited.
  const [target, setTarget] = useState<TemplatePart>("name");
  const insert = (field: string) => {
    const area = editors[target].current;
    const value = display[target] ?? "";
    // Replace the selection while editing, append when the editor is untouched.
    const editing = area !== null && document.activeElement === area;
    const from = editing ? area.selectionStart : value.length;
    const to = editing ? area.selectionEnd : value.length;
    const token = `{${field}}`;
    onChange({ ...display, [target]: `${value.slice(0, from)}${token}${value.slice(to)}` });
    requestAnimationFrame(() => {
      area?.focus();
      area?.setSelectionRange(from + token.length, from + token.length);
    });
  };

  return (
    <div className="results-editor__stack">
      <Panel
        hint={labels.t("configure.resultsEditor.appearanceHint")}
        title={labels.t("configure.resultsEditor.displayPreset")}
      >
        <div className="chip-row">
          {PRESETS.map((preset) => (
            <button
              className={`chip${display.preset === preset ? " chip--only" : ""}`}
              key={preset}
              onClick={() =>
                onChange(
                  preset === "custom"
                    ? {
                        description: display.description ?? CUSTOM_STARTER.description,
                        name: display.name ?? CUSTOM_STARTER.name,
                        preset,
                      }
                    : { preset },
                )
              }
              type="button"
            >
              {labels.label(preset)}
            </button>
          ))}
        </div>
        {display.preset === "custom" ? (
          <>
            {TEMPLATES.map((part) => (
              <label className="field template-editor" key={part}>
                <span className="field__label">
                  {labels.t(`configure.resultsEditor.${part}Template`)}
                </span>
                <textarea
                  maxLength={4096}
                  onChange={(event) => onChange({ ...display, [part]: event.target.value })}
                  onFocus={() => setTarget(part)}
                  ref={editors[part]}
                  rows={part === "name" ? 2 : 6}
                  value={display[part] ?? ""}
                />
              </label>
            ))}
            <fieldset className="facet">
              <legend>
                {labels.t("configure.resultsEditor.insertInto", {
                  target: labels.t(`configure.resultsEditor.${target}Template`),
                })}
              </legend>
              <div className="chip-row">
                {bootstrap.result_fields.map((field) => (
                  <button
                    className="chip"
                    key={field}
                    onClick={() => insert(field)}
                    // Keep the caret: pressing a chip must not blur the editor.
                    onMouseDown={(event) => event.preventDefault()}
                    title={`{${field}}`}
                    type="button"
                  >
                    {labels.t(`configure.resultFields.${field}`)}
                  </button>
                ))}
              </div>
            </fieldset>
          </>
        ) : null}
      </Panel>

      <Panel title={labels.t("configure.resultsEditor.preview")}>
        {error ? (
          <Alert tone="danger">{labels.t("configure.resultsEditor.previewError")}</Alert>
        ) : null}
        <figure className="result-preview">
          <strong>{preview?.name}</strong>
          <pre>{preview?.description ?? labels.t("app.loading")}</pre>
        </figure>
      </Panel>
    </div>
  );
}
