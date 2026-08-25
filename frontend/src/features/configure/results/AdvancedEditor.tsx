import { Plus, Trash2 } from "lucide-react";
import type { ReactNode } from "react";
import type { ConfiguratorBootstrapData } from "../../../api/generated/contracts";
import { Button } from "../../../components/ui/Button";
import { Input } from "../../../components/ui/Input";
import { Select } from "../../../components/ui/Select";
import type { PolicyRuleDraft, PredicateDraft, ResultsDraft } from "../model";
import { Panel } from "./controls";
import { isUncachedShortcut, useResultLabels } from "./vocabulary";

const RULE_OPS = [
  "is",
  "isNot",
  "oneOf",
  "noneOf",
  "contains",
  "notContains",
  "lt",
  "lte",
  "gt",
  "gte",
  "between",
  "known",
  "unknown",
] as const;
const AUXILIARY_DEFAULTS: ResultsDraft["auxiliary"] = {
  debridSync: "bottom",
  errors: "bottom",
  filterSummary: "whenEmpty",
};

/** Advanced rules plus every auxiliary placement that departs from the default. */
export function activeAdvancedCount(results: ResultsDraft): number {
  return (
    results.filters.rules.filter((rule) => !isUncachedShortcut(rule)).length +
    Object.entries(AUXILIARY_DEFAULTS).filter(
      ([key, value]) => results.auxiliary[key as keyof ResultsDraft["auxiliary"]] !== value,
    ).length
  );
}

const NUMERIC_FIELDS = new Set(["playbackSize", "releaseSize", "seeders", "ageDays", "bitrate"]);
const BOOLEAN_FIELDS = new Set(["private", "trash"]);
const NUMERIC_OPS = new Set(["lt", "lte", "gt", "gte", "between"]);
const LIST_OPS = new Set(["oneOf", "noneOf", "between"]);

function defaultPredicate(field: string, op: string): PredicateDraft {
  if (op === "known" || op === "unknown") return { field, op };
  if (op === "between") return { field, op, values: [0, 0] };
  if (LIST_OPS.has(op)) return { field, op, values: [] };
  if (NUMERIC_FIELDS.has(field)) return { field, op, value: 0 };
  if (BOOLEAN_FIELDS.has(field)) return { field, op, value: true };
  return { field, op, value: "" };
}

function csv(value: string): string[] {
  return [
    ...new Set(
      value
        .split(",")
        .map((item) => item.trim())
        .filter(Boolean),
    ),
  ];
}

export function AdvancedEditor({
  auxiliary,
  bootstrap,
  onAuxiliaryChange,
  onRulesChange,
  portability,
  rules,
  showDebridSync,
}: {
  auxiliary: ResultsDraft["auxiliary"];
  bootstrap: ConfiguratorBootstrapData;
  onAuxiliaryChange: (value: ResultsDraft["auxiliary"]) => void;
  onRulesChange: (rules: PolicyRuleDraft[]) => void;
  portability: ReactNode;
  rules: PolicyRuleDraft[];
  showDebridSync: boolean;
}) {
  const labels = useResultLabels();
  const auxiliaryKeys = [
    "filterSummary",
    "errors",
    ...(showDebridSync ? (["debridSync"] as const) : []),
  ] as const;
  return (
    <div className="results-editor__stack">
      <Panel
        hint={labels.t("configure.resultsEditor.auxiliaryHint")}
        title={labels.t("configure.resultsEditor.auxiliary")}
      >
        <div className="field-grid">
          {auxiliaryKeys.map((key) => (
            <Select
              hint={labels.hint(key)}
              key={key}
              label={labels.label(key)}
              onValueChange={(value) => onAuxiliaryChange({ ...auxiliary, [key]: value as never })}
              value={auxiliary[key]}
            >
              {(key === "filterSummary"
                ? (["off", "whenEmpty", "top", "bottom"] as const)
                : (["off", "top", "bottom"] as const)
              ).map((value) => (
                <option key={value} value={value}>
                  {labels.label(value)}
                </option>
              ))}
            </Select>
          ))}
        </div>
      </Panel>

      <Panel
        activeCount={rules.length}
        hint={labels.t("configure.resultsEditor.rulesHint")}
        title={labels.t("configure.resultsEditor.rules")}
      >
        {rules.map((rule, ruleIndex) => (
          <RuleCard
            fields={bootstrap.result_policy_fields}
            key={rule.id ?? ruleIndex}
            onChange={(next) =>
              onRulesChange(rules.map((item, position) => (position === ruleIndex ? next : item)))
            }
            onRemove={() => onRulesChange(rules.filter((_, position) => position !== ruleIndex))}
            position={ruleIndex + 1}
            rule={rule}
          />
        ))}
        <Button
          onClick={() =>
            onRulesChange([
              ...rules,
              { action: "exclude", all: [{ field: "title", op: "contains", value: "" }] },
            ])
          }
          variant="secondary"
        >
          <Plus aria-hidden="true" size={16} /> {labels.t("configure.resultsEditor.addRule")}
        </Button>
      </Panel>

      <Panel
        hint={labels.t("configure.resultsEditor.portabilityHint")}
        title={labels.t("configure.resultsEditor.portability")}
      >
        {portability}
      </Panel>
    </div>
  );
}

function RuleCard({
  fields,
  onChange,
  onRemove,
  position,
  rule,
}: {
  fields: readonly string[];
  onChange: (rule: PolicyRuleDraft) => void;
  onRemove: () => void;
  position: number;
  rule: PolicyRuleDraft;
}) {
  const labels = useResultLabels();
  const replace = (index: number, predicate: PredicateDraft) =>
    onChange({
      ...rule,
      all: rule.all.map((item, position) => (position === index ? predicate : item)),
    });
  return (
    <fieldset className="facet rule-card">
      <legend>{rule.id || labels.t("configure.resultsEditor.rule", { position })}</legend>
      <div className="rule-header">
        <Select
          hint={labels.hint(rule.action)}
          label={labels.t("configure.resultsEditor.action")}
          labelHidden
          onValueChange={(action) => {
            const { language: _discarded, ...remaining } = rule;
            onChange(
              action === "addLanguage"
                ? { ...remaining, action, language: rule.language ?? "en" }
                : { ...remaining, action: action as "exclude" | "prefer" | "require" },
            );
          }}
          value={rule.action}
        >
          {(["exclude", "require", "prefer", "addLanguage"] as const).map((action) => (
            <option key={action} value={action}>
              {labels.label(action)}
            </option>
          ))}
        </Select>
        {rule.action === "addLanguage" ? (
          <Input
            label={labels.label("language")}
            labelHidden
            onChange={(event) => onChange({ ...rule, language: event.target.value })}
            placeholder={labels.label("language")}
            value={rule.language ?? ""}
          />
        ) : null}
        <Input
          label={labels.t("configure.resultsEditor.ruleId")}
          labelHidden
          placeholder={labels.t("configure.resultsEditor.ruleId")}
          onChange={(event) => {
            const { id: _discarded, ...remaining } = rule;
            onChange(event.target.value ? { ...remaining, id: event.target.value } : remaining);
          }}
          value={rule.id ?? ""}
        />
        <Button
          aria-label={labels.t("configure.resultsEditor.removeRule")}
          onClick={onRemove}
          variant="ghost"
        >
          <Trash2 aria-hidden="true" size={16} />
        </Button>
      </div>
      {rule.all.map((predicate, index) => {
        const numeric = NUMERIC_FIELDS.has(predicate.field);
        const boolean = BOOLEAN_FIELDS.has(predicate.field);
        const list = LIST_OPS.has(predicate.op);
        const operandLess = predicate.op === "known" || predicate.op === "unknown";
        return (
          // biome-ignore lint/suspicious/noArrayIndexKey: the row identity is its position; keying on content remounts the input on every keystroke
          <div className="predicate-row" key={index}>
            <Select
              label={labels.t("configure.resultsEditor.field")}
              labelHidden
              onValueChange={(field) => replace(index, defaultPredicate(field, predicate.op))}
              value={predicate.field}
            >
              {fields.map((field) => (
                <option key={field} value={field}>
                  {labels.label(field)}
                </option>
              ))}
            </Select>
            <Select
              label={labels.t("configure.resultsEditor.operator")}
              labelHidden
              onValueChange={(op) => replace(index, defaultPredicate(predicate.field, op))}
              value={predicate.op}
            >
              {RULE_OPS.filter((op) => !NUMERIC_OPS.has(op) || numeric).map((op) => (
                <option key={op} value={op}>
                  {labels.label(op)}
                </option>
              ))}
            </Select>
            {operandLess ? (
              <span />
            ) : boolean && !list ? (
              <Select
                label={labels.t("configure.resultsEditor.value")}
                labelHidden
                onValueChange={(value) => replace(index, { ...predicate, value: value === "true" })}
                value={String(predicate.value ?? true)}
              >
                <option value="true">{labels.label("true")}</option>
                <option value="false">{labels.label("false")}</option>
              </Select>
            ) : (
              <Input
                label={labels.t("configure.resultsEditor.value")}
                labelHidden
                onChange={(event) =>
                  replace(index, {
                    field: predicate.field,
                    op: predicate.op,
                    ...(list
                      ? {
                          values: numeric
                            ? csv(event.target.value).map(Number).filter(Number.isFinite)
                            : boolean
                              ? csv(event.target.value).flatMap((value) =>
                                  value === "true" ? [true] : value === "false" ? [false] : [],
                                )
                              : csv(event.target.value),
                        }
                      : { value: numeric ? Number(event.target.value) : event.target.value }),
                  })
                }
                placeholder={labels.t(
                  list ? "configure.resultsEditor.commaSeparated" : "configure.resultsEditor.value",
                )}
                type={numeric && !list ? "number" : "text"}
                value={list ? (predicate.values ?? []).join(", ") : String(predicate.value ?? "")}
              />
            )}
            <Button
              aria-label={labels.t("configure.resultsEditor.removePredicate")}
              disabled={rule.all.length === 1}
              onClick={() =>
                onChange({ ...rule, all: rule.all.filter((_, position) => position !== index) })
              }
              variant="ghost"
            >
              <Trash2 aria-hidden="true" size={16} />
            </Button>
          </div>
        );
      })}
      <Button
        onClick={() =>
          onChange({ ...rule, all: [...rule.all, { field: "title", op: "contains", value: "" }] })
        }
        variant="secondary"
      >
        <Plus aria-hidden="true" size={16} /> {labels.t("configure.resultsEditor.addPredicate")}
      </Button>
    </fieldset>
  );
}
