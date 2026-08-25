import { Plus, Trash2 } from "lucide-react";
import { Button } from "../../../components/ui/Button";
import { Input } from "../../../components/ui/Input";
import { Select } from "../../../components/ui/Select";
import { Switch } from "../../../components/ui/Switch";
import type { LimitRuleDraft, ResultsDraft } from "../model";
import { Panel } from "./controls";
import { useResultLabels } from "./vocabulary";

const LIMIT_KEYS: readonly LimitRuleDraft["by"][] = [
  "total",
  "resolution",
  "quality",
  "provider",
  "transport",
  "source",
  "releaseGroup",
];

const ALTERNATIVE_DEFAULTS: ResultsDraft["alternatives"] = {
  cached: "all",
  direct: "always",
  fallback: false,
  hideUncachedWhenCached: false,
  uncached: "all",
  usenet: "all",
};

/** Limits plus every alternatives choice that departs from the default. */
export function activeDeliveryCount(results: ResultsDraft): number {
  return (
    results.limits.length +
    Object.entries(ALTERNATIVE_DEFAULTS).filter(
      ([key, value]) => results.alternatives[key as keyof ResultsDraft["alternatives"]] !== value,
    ).length
  );
}

const SINGLE_RESULT: ResultsDraft["alternatives"] = {
  cached: "best",
  direct: "unlessCached",
  fallback: true,
  hideUncachedWhenCached: true,
  uncached: "best",
  usenet: "best",
};

export function DeliveryEditor({
  alternatives,
  limits,
  onAlternativesChange,
  onLimitsChange,
}: {
  alternatives: ResultsDraft["alternatives"];
  limits: LimitRuleDraft[];
  onAlternativesChange: (value: ResultsDraft["alternatives"]) => void;
  onLimitsChange: (value: LimitRuleDraft[]) => void;
}) {
  const labels = useResultLabels();
  const available = LIMIT_KEYS.filter((key) => !limits.some((rule) => rule.by === key));
  const [next] = available;
  return (
    <div className="results-editor__stack">
      <Panel
        action={
          <Button onClick={() => onAlternativesChange(SINGLE_RESULT)} variant="secondary">
            {labels.t("configure.resultsEditor.singleFallback")}
          </Button>
        }
        hint={labels.t("configure.resultsEditor.alternativesHint")}
        title={labels.t("configure.resultsEditor.alternatives")}
      >
        <div className="field-grid">
          {(["cached", "uncached", "usenet"] as const).map((key) => (
            <Select
              key={key}
              label={labels.label(key)}
              onValueChange={(value) =>
                onAlternativesChange({ ...alternatives, [key]: value as "all" | "best" })
              }
              value={alternatives[key]}
            >
              <option value="all">{labels.t("configure.resultsEditor.everyProvider")}</option>
              <option value="best">{labels.t("configure.resultsEditor.bestProvider")}</option>
            </Select>
          ))}
          <Select
            hint={labels.hint("direct")}
            label={labels.label("directTorrent")}
            onValueChange={(direct) =>
              onAlternativesChange({ ...alternatives, direct: direct as "always" | "unlessCached" })
            }
            value={alternatives.direct}
          >
            <option value="always">{labels.label("always")}</option>
            <option value="unlessCached">{labels.label("unlessCached")}</option>
          </Select>
        </div>
        <Switch
          checked={alternatives.hideUncachedWhenCached}
          hint={labels.hint("hideUncachedWhenCached")}
          label={labels.label("hideUncachedWhenCached")}
          onCheckedChange={(hideUncachedWhenCached) =>
            onAlternativesChange({ ...alternatives, hideUncachedWhenCached })
          }
        />
        <Switch
          checked={alternatives.fallback}
          hint={labels.hint("fallback")}
          label={labels.label("fallback")}
          onCheckedChange={(fallback) => onAlternativesChange({ ...alternatives, fallback })}
        />
      </Panel>

      <Panel
        activeCount={limits.length}
        hint={labels.t("configure.resultsEditor.limitsHint")}
        title={labels.t("configure.resultsEditor.limits")}
      >
        {limits.map((rule, index) => (
          <div className="limit-row" key={rule.by}>
            <Select
              label={labels.t("configure.resultsEditor.limitBy")}
              labelHidden
              onValueChange={(by) =>
                onLimitsChange(
                  limits.map((item, position) =>
                    position === index ? { ...item, by: by as LimitRuleDraft["by"] } : item,
                  ),
                )
              }
              value={rule.by}
            >
              {LIMIT_KEYS.filter((key) => key === rule.by || available.includes(key)).map((key) => (
                <option key={key} value={key}>
                  {labels.label(key)}
                </option>
              ))}
            </Select>
            <Input
              label={labels.t("configure.resultsEditor.maximum")}
              labelHidden
              max={1000}
              min={0}
              onChange={(event) =>
                onLimitsChange(
                  limits.map((item, position) =>
                    position === index
                      ? {
                          ...item,
                          max: Number.isFinite(event.target.valueAsNumber)
                            ? event.target.valueAsNumber
                            : 0,
                        }
                      : item,
                  ),
                )
              }
              type="number"
              value={rule.max}
            />
            <Button
              aria-label={labels.t("configure.resultsEditor.remove")}
              onClick={() => onLimitsChange(limits.filter((_, position) => position !== index))}
              variant="ghost"
            >
              <Trash2 aria-hidden="true" size={16} />
            </Button>
          </div>
        ))}
        {next ? (
          <Button
            onClick={() => onLimitsChange([...limits, { by: next, max: 10 }])}
            variant="secondary"
          >
            <Plus aria-hidden="true" size={16} /> {labels.t("configure.resultsEditor.addLimit")}
          </Button>
        ) : null}
      </Panel>
    </div>
  );
}
