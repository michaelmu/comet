import { ChevronDown, Plus, Trash2 } from "lucide-react";
import { useState } from "react";
import type { ConfiguratorBootstrapData } from "../../../api/generated/contracts";
import { Button } from "../../../components/ui/Button";
import { Select } from "../../../components/ui/Select";
import type { ResultScope, SortCriterionDraft, SortKey } from "../model";
import { ReorderableList } from "../ReorderableList";
import { DirectionToggle, Panel, ScopeField, TagInput } from "./controls";
import { DELEGATED_SORT_ORDERS, sortOrderControl, useResultLabels } from "./vocabulary";

function samePreset(
  sort: readonly SortCriterionDraft[],
  preset: readonly { readonly [key: string]: string }[],
): boolean {
  return (
    sort.length === preset.length &&
    sort.every(
      (criterion, index) =>
        criterion.key === preset[index]?.key &&
        criterion.direction === (preset[index]?.direction ?? "desc") &&
        criterion.scope === (preset[index]?.scope ?? "all"),
    )
  );
}

export function OrderEditor({
  bootstrap,
  onChange,
  sort,
}: {
  bootstrap: ConfiguratorBootstrapData;
  onChange: (sort: SortCriterionDraft[]) => void;
  sort: SortCriterionDraft[];
}) {
  const labels = useResultLabels();
  const vocabulary = bootstrap.result_sort_vocabulary;
  const [expanded, setExpanded] = useState<SortKey | null>(null);
  const active = new Set(sort.map(({ key }) => key));
  const remaining = bootstrap.result_sort_keys.filter((key) => !active.has(key as SortKey));
  const replace = (index: number, criterion: SortCriterionDraft) =>
    onChange(sort.map((item, position) => (position === index ? criterion : item)));
  /** An order equal to the canonical one carries no information, so it is dropped. */
  const setOrder = (
    index: number,
    criterion: SortCriterionDraft,
    order: string[],
    canonical?: readonly string[],
  ) => {
    const { order: _discarded, ...rest } = criterion;
    const redundant =
      !order.length || (canonical !== undefined && String(order) === String(canonical));
    replace(index, redundant ? rest : { ...rest, order });
  };

  return (
    <div className="results-editor__stack">
      <Panel
        hint={labels.t("configure.resultsEditor.orderHint")}
        title={labels.t("configure.resultsEditor.presets")}
      >
        <div className="chip-row">
          {Object.entries(bootstrap.result_sort_presets).map(([name, preset]) => (
            <button
              className={`chip${samePreset(sort, preset) ? " chip--only" : ""}`}
              key={name}
              onClick={() =>
                onChange(
                  preset.map((criterion) => ({
                    direction: criterion.direction === "asc" ? "asc" : "desc",
                    key: criterion.key as SortKey,
                    scope: (criterion.scope ?? "all") as ResultScope,
                  })),
                )
              }
              type="button"
            >
              {labels.t(`configure.resultsEditor.preset.${name}`, { defaultValue: name })}
            </button>
          ))}
        </div>
      </Panel>

      <ReorderableList
        className="criteria-list"
        getId={(criterion) => criterion.key}
        getLabel={(criterion) => labels.label(criterion.key)}
        items={sort}
        label={labels.t("configure.resultsEditor.reorder")}
        onChange={onChange}
        renderItem={(criterion, index, reorder) => {
          const orderControl = sortOrderControl(criterion.key, vocabulary);
          const delegated = DELEGATED_SORT_ORDERS[criterion.key];
          const open = expanded === criterion.key;
          const hint = labels.hint(criterion.key);
          return (
            <div className="criteria-card">
              <div className="criteria-row">
                {reorder.handle}
                <span className="criteria-row__rank">{index + 1}</span>
                <div className="criteria-row__identity">
                  <Select
                    label={labels.t("configure.resultsEditor.criterion")}
                    labelHidden
                    onValueChange={(key) => replace(index, { ...criterion, key: key as SortKey })}
                    value={criterion.key}
                  >
                    {bootstrap.result_sort_keys
                      .filter((key) => key === criterion.key || !active.has(key as SortKey))
                      .map((key) => (
                        <option key={key} value={key}>
                          {labels.label(key)}
                        </option>
                      ))}
                  </Select>
                  {hint ? <small>{hint}</small> : null}
                </div>
                <DirectionToggle
                  onChange={(direction) => replace(index, { ...criterion, direction })}
                  value={criterion.direction}
                />
                <Button
                  aria-expanded={open}
                  aria-label={labels.t("configure.resultsEditor.criterionOptions")}
                  className={`criteria-row__toggle${open ? " criteria-row__toggle--open" : ""}`}
                  onClick={() => setExpanded(open ? null : criterion.key)}
                  variant="ghost"
                >
                  {criterion.scope === "all" ? null : (
                    <span className="criteria-row__scope">{labels.label(criterion.scope)}</span>
                  )}
                  <ChevronDown aria-hidden="true" size={16} />
                </Button>
                <Button
                  aria-label={labels.t("configure.resultsEditor.remove")}
                  disabled={sort.length === 1}
                  onClick={() => onChange(sort.filter((_, position) => position !== index))}
                  variant="ghost"
                >
                  <Trash2 aria-hidden="true" size={16} />
                </Button>
              </div>
              {open ? (
                <div className="criteria-detail">
                  <ScopeField
                    onChange={(scope) => replace(index, { ...criterion, scope })}
                    scopes={bootstrap.result_scopes}
                    value={criterion.scope}
                  />
                  {delegated ? (
                    <small>{labels.t(`configure.resultsEditor.orderOwnedBy.${delegated}`)}</small>
                  ) : orderControl === "values" ? (
                    <ValueOrder
                      onChange={(order) =>
                        setOrder(index, criterion, order, vocabulary[criterion.key])
                      }
                      values={vocabulary[criterion.key] ?? []}
                      {...(criterion.order ? { order: criterion.order } : {})}
                    />
                  ) : orderControl === "tags" ? (
                    <TagInput
                      label={labels.t("configure.resultsEditor.valueOrder")}
                      onChange={(order) => setOrder(index, criterion, order)}
                      values={criterion.order ?? []}
                    />
                  ) : null}
                </div>
              ) : null}
            </div>
          );
        }}
      />

      {remaining.length > 0 ? (
        <Select
          label={labels.t("configure.resultsEditor.addCriterion")}
          labelHidden
          leadingIcon={<Plus aria-hidden="true" size={16} />}
          onValueChange={(key) =>
            key && onChange([...sort, { direction: "desc", key: key as SortKey, scope: "all" }])
          }
          value=""
        >
          <option value="">{labels.t("configure.resultsEditor.addCriterion")}</option>
          {remaining.map((key) => (
            <option key={key} value={key}>
              {labels.label(key)}
            </option>
          ))}
        </Select>
      ) : null}
    </div>
  );
}

function ValueOrder({
  onChange,
  order,
  values,
}: {
  onChange: (order: string[]) => void;
  order?: readonly string[];
  values: readonly string[];
}) {
  const labels = useResultLabels();
  const items = [
    ...(order ?? []).filter((value) => values.includes(value)),
    ...values.filter((value) => !order?.includes(value)),
  ];
  return (
    <div className="value-order">
      <span className="field__label">{labels.t("configure.resultsEditor.valueOrder")}</span>
      <ReorderableList
        className="value-order__list"
        getId={(value) => value}
        getLabel={(value) => labels.label(value)}
        items={items}
        label={labels.t("configure.resultsEditor.reorderValues")}
        onChange={onChange}
        renderItem={(value, position, reorder) => (
          <div className="value-order__row">
            {reorder.handle}
            <span className="value-order__rank">{position + 1}</span>
            <span>{labels.label(value)}</span>
          </div>
        )}
      />
    </div>
  );
}
