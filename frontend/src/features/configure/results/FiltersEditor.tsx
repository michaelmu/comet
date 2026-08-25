import { Plus, Trash2 } from "lucide-react";
import type { ConfiguratorBootstrapData } from "../../../api/generated/contracts";
import { Button } from "../../../components/ui/Button";
import { Checkbox } from "../../../components/ui/Checkbox";
import { Input } from "../../../components/ui/Input";
import { MultiSelect, type MultiSelectOption } from "../../../components/ui/MultiSelect";
import { Select } from "../../../components/ui/Select";
import { Switch } from "../../../components/ui/Switch";
import type {
  DimensionKey,
  KeywordPatternDraft,
  RangeKey,
  ResultScope,
  ResultsDraft,
} from "../model";
import { FacetChips, Panel, ScopeField, TagInput } from "./controls";
import {
  FILTER_PANELS,
  facetControl,
  facetCount,
  isUncachedShortcut,
  RANGE_UNITS,
  useResultLabels,
} from "./vocabulary";

const RECIPES: Record<string, (filters: ResultsDraft["filters"]) => ResultsDraft["filters"]> = {
  no3d: (filters) => facet(filters, "visual", { exclude: ["3d"], only: [] }),
  sdr: (filters) => facet(filters, "visual", { exclude: [], only: ["sdr"] }),
  nodv: (filters) => facet(filters, "visual", { exclude: ["dolbyVision"], only: [] }),
  noav1: (filters) => facet(filters, "videoCodec", { exclude: ["av1"], only: [] }),
  nocam: (filters) =>
    facet(filters, "quality", { exclude: ["cam", "telesync", "telecine", "screener"], only: [] }),
  dvmp4: (filters) => ({
    ...filters,
    rules: [
      ...filters.rules.filter((rule) => rule.id !== "recipe-dv-mp4"),
      {
        action: "exclude",
        all: [
          { field: "visual", op: "oneOf", values: ["dolbyVision"] },
          { field: "container", op: "noneOf", values: ["mp4"] },
        ],
        id: "recipe-dv-mp4",
      },
    ],
  }),
};

function facet(
  filters: ResultsDraft["filters"],
  key: DimensionKey,
  value: { exclude: string[]; only: string[] },
): ResultsDraft["filters"] {
  return { ...filters, dimensions: { ...filters.dimensions, [key]: value } };
}

export function activeFilterCount(filters: ResultsDraft["filters"]): number {
  return (
    (filters.removeTrash ? 0 : 1) +
    Object.values(filters.dimensions).reduce((total, facet) => total + facetCount(facet), 0) +
    Object.keys(filters.ranges).length +
    Object.values(filters.keywords).reduce((total, patterns) => total + patterns.length, 0) +
    filters.rules.filter(isUncachedShortcut).length
  );
}

export function FiltersEditor({
  bootstrap,
  debridKinds,
  filters,
  languages,
  onChange,
}: {
  bootstrap: ConfiguratorBootstrapData;
  debridKinds: readonly string[];
  filters: ResultsDraft["filters"];
  languages: readonly MultiSelectOption[];
  onChange: (filters: ResultsDraft["filters"]) => void;
}) {
  const labels = useResultLabels();
  const shortcut = filters.rules.find(isUncachedShortcut);
  const shortcutKinds =
    shortcut?.all.find(({ field }) => field === "providerKind")?.values?.map(String) ?? [];
  const setShortcut = (enabled: boolean, kinds = shortcutKinds) =>
    onChange({
      ...filters,
      rules: [
        ...filters.rules.filter((rule) => !isUncachedShortcut(rule)),
        ...(enabled
          ? [
              {
                action: "exclude" as const,
                all: [
                  { field: "cacheState", op: "is", value: "uncached" },
                  ...(kinds.length ? [{ field: "providerKind", op: "oneOf", values: kinds }] : []),
                ],
              },
            ]
          : []),
      ],
    });
  const setFacet = (key: DimensionKey, value: { exclude: string[]; only: string[] }) =>
    onChange(facet(filters, key, value));

  return (
    <div className="results-editor__stack">
      <Panel
        hint={labels.t("configure.resultsEditor.recipeHint")}
        title={labels.t("configure.resultsEditor.recipes")}
      >
        <div className="chip-row">
          {Object.entries(RECIPES).map(([name, apply]) => (
            <button
              className="chip"
              key={name}
              onClick={() => onChange(apply(filters))}
              type="button"
            >
              {labels.t(`configure.resultsEditor.recipe.${name}`)}
            </button>
          ))}
        </div>
      </Panel>

      <Panel
        hint={labels.hint("removeTrash")}
        title={labels.t("configure.resultsEditor.eligibility")}
      >
        <Switch
          checked={filters.removeTrash}
          label={labels.t("configure.resultsEditor.removeTrash")}
          onCheckedChange={(removeTrash) => onChange({ ...filters, removeTrash })}
        />
        {debridKinds.length > 0 ? (
          <>
            <Switch
              checked={shortcut !== undefined}
              label={labels.t("configure.resultsEditor.excludeUncached")}
              onCheckedChange={(enabled) => setShortcut(enabled)}
            />
            {shortcut ? (
              <div className="inline-options">
                <span className="field__label">
                  {labels.t("configure.resultsEditor.uncachedScope")}
                </span>
                {debridKinds.map((kind) => (
                  <Checkbox
                    checked={shortcutKinds.includes(kind)}
                    key={kind}
                    label={labels.t(`configure.debridServices.${kind}`, { defaultValue: kind })}
                    onChange={(event) =>
                      setShortcut(
                        true,
                        event.target.checked
                          ? [...shortcutKinds, kind]
                          : shortcutKinds.filter((item) => item !== kind),
                      )
                    }
                  />
                ))}
                {shortcutKinds.length === 0 ? (
                  <small>{labels.t("configure.resultsEditor.uncachedAllDebrids")}</small>
                ) : null}
              </div>
            ) : null}
          </>
        ) : null}
      </Panel>

      <p className="chip-legend">{labels.t("configure.resultsEditor.chipLegend")}</p>

      {FILTER_PANELS.map((panel) => (
        <Panel
          activeCount={
            panel.facets.reduce((total, key) => total + facetCount(filters.dimensions[key]), 0) +
            (panel.ranges ?? []).filter((key) => filters.ranges[key]).length
          }
          key={panel.id}
          title={labels.t(`configure.resultsEditor.panel.${panel.id}`)}
        >
          {panel.facets.map((key) => {
            const control = facetControl(key);
            const value = filters.dimensions[key];
            const label = labels.label(key);
            if (control === "chips" || control === "providerKinds") {
              const values =
                control === "providerKinds" ? debridKinds : (bootstrap.result_facets[key] ?? []);
              return values.length === 0 ? null : (
                <FacetChips
                  key={key}
                  label={label}
                  onChange={(next) => setFacet(key, next)}
                  value={value}
                  values={values}
                />
              );
            }
            const sideLabel = (side: "exclude" | "only") =>
              labels.t(`configure.resultsEditor.facetSide.${side}`);
            if (control === "languages") {
              return (
                <fieldset className="facet" key={key}>
                  <legend>{label}</legend>
                  <div className="facet-languages">
                    {(["only", "exclude"] as const).map((side) => (
                      <MultiSelect
                        emptyLabel={labels.t("configure.languages.none")}
                        key={side}
                        label={sideLabel(side)}
                        onChange={(selected) => setFacet(key, { ...value, [side]: selected })}
                        options={languages}
                        removeLabel={(optionLabel) =>
                          labels.t("actions.removeSelection", { label: optionLabel })
                        }
                        searchLabel={labels.t("configure.languages.search")}
                        selected={value[side]}
                      />
                    ))}
                  </div>
                </fieldset>
              );
            }
            return (
              <fieldset className="facet" key={key}>
                <legend>{label}</legend>
                <div className="facet-tags">
                  {(["only", "exclude"] as const).map((side) => (
                    <TagInput
                      key={side}
                      label={sideLabel(side)}
                      onChange={(values) => setFacet(key, { ...value, [side]: values })}
                      values={value[side]}
                    />
                  ))}
                </div>
              </fieldset>
            );
          })}
          {panel.ranges ? (
            <div className="range-grid">
              <span />
              <span className="range-grid__head">
                {labels.t("configure.resultsEditor.minimum")}
              </span>
              <span className="range-grid__head">
                {labels.t("configure.resultsEditor.maximum")}
              </span>
              <span className="range-grid__head">
                {labels.t("configure.resultsEditor.appliesTo")}
              </span>
              {panel.ranges.map((key) => (
                <RangeRow
                  key={key}
                  name={key}
                  onChange={(range) => {
                    const ranges = { ...filters.ranges };
                    if (range) ranges[key] = range;
                    else delete ranges[key];
                    onChange({ ...filters, ranges });
                  }}
                  scopes={bootstrap.result_scopes}
                  {...(filters.ranges[key] ? { value: filters.ranges[key] } : {})}
                />
              ))}
            </div>
          ) : null}
        </Panel>
      ))}

      <Panel
        activeCount={Object.values(filters.keywords).reduce(
          (total, patterns) => total + patterns.length,
          0,
        )}
        hint={labels.t("configure.resultsEditor.keywordHint")}
        title={labels.t("configure.resultsEditor.keywords")}
      >
        {(["exclude", "require", "prefer"] as const).map((action) => (
          <KeywordList
            action={action}
            key={action}
            onChange={(patterns) =>
              onChange({ ...filters, keywords: { ...filters.keywords, [action]: patterns } })
            }
            patterns={filters.keywords[action]}
          />
        ))}
      </Panel>
    </div>
  );
}

function RangeRow({
  name,
  onChange,
  scopes,
  value,
}: {
  name: RangeKey;
  onChange: (value: { max?: number; min?: number; scope: ResultScope } | undefined) => void;
  scopes: readonly string[];
  value?: { max?: number; min?: number; scope: ResultScope };
}) {
  const labels = useResultLabels();
  const hint = labels.hint(name);
  const unit = RANGE_UNITS[name] ?? 1;
  const setBound = (part: "min" | "max", raw: string) => {
    const next = { ...(value ?? { scope: "all" as ResultScope }) };
    if (raw === "") delete next[part];
    else {
      const parsed = Number(raw);
      if (!Number.isFinite(parsed) || parsed < 0) return;
      next[part] = Math.round(parsed * unit);
    }
    onChange(next.min === undefined && next.max === undefined ? undefined : next);
  };
  const display = (bound?: number) =>
    bound === undefined ? "" : String(Number((bound / unit).toFixed(2)));
  return (
    <div className="range-row">
      <span className="range-row__label">
        {labels.label(name)}
        {unit > 1 ? <small>{labels.t(`configure.resultsEditor.unit.${name}`)}</small> : null}
        {hint ? <small>{hint}</small> : null}
      </span>
      <Input
        label={labels.t("configure.resultsEditor.minimum")}
        labelHidden
        min={0}
        onChange={(event) => setBound("min", event.target.value)}
        placeholder={labels.t("configure.resultsEditor.minimum")}
        type="number"
        value={display(value?.min)}
      />
      <Input
        label={labels.t("configure.resultsEditor.maximum")}
        labelHidden
        min={0}
        onChange={(event) => setBound("max", event.target.value)}
        placeholder={labels.t("configure.resultsEditor.maximum")}
        type="number"
        value={display(value?.max)}
      />
      <ScopeField
        disabled={value === undefined}
        labelHidden
        onChange={(scope) => value && onChange({ ...value, scope })}
        scopes={scopes}
        value={value?.scope ?? "all"}
      />
    </div>
  );
}

function KeywordList({
  action,
  onChange,
  patterns,
}: {
  action: "exclude" | "prefer" | "require";
  onChange: (patterns: KeywordPatternDraft[]) => void;
  patterns: KeywordPatternDraft[];
}) {
  const labels = useResultLabels();
  const replace = (index: number, pattern: KeywordPatternDraft) =>
    onChange(patterns.map((item, position) => (position === index ? pattern : item)));
  return (
    <fieldset className="facet">
      <legend>{labels.t(`configure.resultsEditor.keyword.${action}`)}</legend>
      {patterns.map((pattern, index) => (
        // biome-ignore lint/suspicious/noArrayIndexKey: the row identity is its position; keying on content remounts the input on every keystroke
        <div className="keyword-row" key={index}>
          <Input
            label={labels.t("configure.resultsEditor.value")}
            labelHidden
            onChange={(event) => replace(index, { ...pattern, value: event.target.value })}
            placeholder={labels.t("configure.resultsEditor.wordOrPhrase")}
            value={pattern.value}
          />
          <Select
            hint={labels.hint(pattern.mode)}
            label={labels.t("configure.resultsEditor.mode")}
            labelHidden
            onValueChange={(mode) =>
              replace(index, { ...pattern, mode: mode as KeywordPatternDraft["mode"] })
            }
            value={pattern.mode}
          >
            {(["word", "phrase", "wildcard"] as const).map((mode) => (
              <option key={mode} value={mode}>
                {labels.label(mode)}
              </option>
            ))}
          </Select>
          <Select
            label={labels.t("configure.resultsEditor.target")}
            labelHidden
            onValueChange={(target) =>
              replace(index, { ...pattern, target: target as KeywordPatternDraft["target"] })
            }
            value={pattern.target}
          >
            {(["title", "releaseGroup", "source"] as const).map((target) => (
              <option key={target} value={target}>
                {labels.label(target)}
              </option>
            ))}
          </Select>
          <Button
            aria-label={labels.t("configure.resultsEditor.remove")}
            onClick={() => onChange(patterns.filter((_, position) => position !== index))}
            variant="ghost"
          >
            <Trash2 aria-hidden="true" size={16} />
          </Button>
        </div>
      ))}
      <Button
        onClick={() => onChange([...patterns, { mode: "phrase", target: "title", value: "" }])}
        variant="secondary"
      >
        <Plus aria-hidden="true" size={16} /> {labels.t("configure.resultsEditor.add")}
      </Button>
    </fieldset>
  );
}
