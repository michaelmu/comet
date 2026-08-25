import { ArrowDown, ArrowUp, Ban, Check, Plus, X } from "lucide-react";
import { type ReactNode, useState } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "../../../components/ui/Button";
import { Input } from "../../../components/ui/Input";
import { Select } from "../../../components/ui/Select";
import type { FacetDraft, ResultScope, SortDirection } from "../model";
import { SCOPE_GROUPS, useResultLabels } from "./vocabulary";

export function Panel({
  action,
  activeCount = 0,
  children,
  hint,
  id,
  title,
}: {
  action?: ReactNode;
  activeCount?: number;
  children: ReactNode;
  hint?: string | undefined;
  id?: string;
  title: string;
}) {
  return (
    <section className="results-panel" id={id}>
      <header className="results-panel__header">
        <h3>{title}</h3>
        {activeCount > 0 ? <span className="results-panel__count">{activeCount}</span> : null}
        {action ? <div className="results-panel__action">{action}</div> : null}
      </header>
      {hint ? <p className="results-panel__hint">{hint}</p> : null}
      <div className="results-panel__body">{children}</div>
    </section>
  );
}

/**
 * One control for both facet sides: a value is neutral, kept exclusively, or
 * excluded. Cycling through a single chip makes the contradictory
 * "only and exclude the same value" state unrepresentable.
 */
export function FacetChips({
  label,
  onChange,
  value,
  values,
}: {
  label: string;
  onChange: (value: FacetDraft) => void;
  value: FacetDraft;
  values: readonly string[];
}) {
  const labels = useResultLabels();
  const cycle = (item: string) => {
    const only = value.only.filter((entry) => entry !== item);
    const exclude = value.exclude.filter((entry) => entry !== item);
    if (value.only.includes(item)) onChange({ exclude: [...exclude, item], only });
    else if (value.exclude.includes(item)) onChange({ exclude, only });
    else onChange({ exclude, only: [...only, item] });
  };
  const active = value.only.length + value.exclude.length;
  return (
    <fieldset className="facet">
      <legend>
        <span>{label}</span>
        {active > 0 ? (
          <Button
            className="facet__clear"
            onClick={() => onChange({ exclude: [], only: [] })}
            variant="ghost"
          >
            <X aria-hidden="true" size={13} />
            {labels.t("configure.resultsEditor.clear")}
          </Button>
        ) : null}
      </legend>
      <div className="chip-row">
        {values.map((item) => {
          const state = value.only.includes(item)
            ? "only"
            : value.exclude.includes(item)
              ? "exclude"
              : "neutral";
          return (
            <button
              aria-label={`${labels.label(item)} — ${labels.t(`configure.resultsEditor.chipState.${state}`)}`}
              className={`chip chip--${state}`}
              key={item}
              onClick={() => cycle(item)}
              title={labels.hint(item)}
              type="button"
            >
              {state === "only" ? <Check aria-hidden="true" size={13} /> : null}
              {state === "exclude" ? <Ban aria-hidden="true" size={13} /> : null}
              {labels.label(item)}
            </button>
          );
        })}
      </div>
    </fieldset>
  );
}

/** Ordered free-text values entered one at a time instead of a comma-separated blob. */
export function TagInput({
  label,
  onChange,
  placeholder,
  values,
}: {
  label: string;
  onChange: (values: string[]) => void;
  placeholder?: string;
  values: readonly string[];
}) {
  const { t } = useTranslation();
  const [draft, setDraft] = useState("");
  const commit = () => {
    const value = draft.trim();
    if (value && !values.includes(value)) onChange([...values, value]);
    setDraft("");
  };
  return (
    <div className="tag-input">
      <span className="field__label">{label}</span>
      {values.length > 0 ? (
        <div className="chip-row">
          {values.map((value) => (
            <button
              aria-label={t("actions.removeSelection", { label: value })}
              className="chip chip--tag"
              key={value}
              onClick={() => onChange(values.filter((entry) => entry !== value))}
              type="button"
            >
              {value}
              <X aria-hidden="true" size={13} />
            </button>
          ))}
        </div>
      ) : null}
      <div className="tag-input__entry">
        <Input
          label={label}
          labelHidden
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => {
            if (event.key !== "Enter") return;
            event.preventDefault();
            commit();
          }}
          {...(placeholder ? { placeholder } : {})}
          value={draft}
        />
        <Button aria-label={t("configure.resultsEditor.add")} onClick={commit} variant="secondary">
          <Plus aria-hidden="true" size={16} />
        </Button>
      </div>
    </div>
  );
}

export function ScopeField({
  disabled = false,
  labelHidden = false,
  onChange,
  scopes,
  value,
}: {
  disabled?: boolean;
  labelHidden?: boolean;
  onChange: (scope: ResultScope) => void;
  scopes: readonly string[];
  value: ResultScope;
}) {
  const labels = useResultLabels();
  const available = new Set(scopes);
  return (
    <Select
      disabled={disabled}
      {...(labelHidden ? {} : { hint: labels.hint(value) })}
      label={labels.t("configure.resultsEditor.appliesTo")}
      labelHidden={labelHidden}
      onValueChange={(scope) => onChange(scope as ResultScope)}
      // Compact rows drop the hint line; the explanation stays on hover.
      title={labelHidden ? labels.hint(value) : undefined}
      value={value}
    >
      <option value="all">{labels.label("all")}</option>
      {SCOPE_GROUPS.map((group) => (
        <optgroup key={group.id} label={labels.t(`configure.resultsEditor.scopeGroup.${group.id}`)}>
          {group.scopes
            .filter((scope) => available.has(scope))
            .map((scope) => (
              <option key={scope} value={scope}>
                {labels.label(scope)}
              </option>
            ))}
        </optgroup>
      ))}
    </Select>
  );
}

export function DirectionToggle({
  onChange,
  value,
}: {
  onChange: (direction: SortDirection) => void;
  value: SortDirection;
}) {
  const labels = useResultLabels();
  const next = value === "desc" ? "asc" : "desc";
  return (
    <Button
      className="direction-toggle"
      onClick={() => onChange(next)}
      title={labels.label(next)}
      variant="secondary"
    >
      {value === "desc" ? (
        <ArrowDown aria-hidden="true" size={15} />
      ) : (
        <ArrowUp aria-hidden="true" size={15} />
      )}
      {labels.label(value)}
    </Button>
  );
}
