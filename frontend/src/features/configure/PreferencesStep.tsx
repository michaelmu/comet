import { useTranslation } from "react-i18next";
import type { ConfiguratorBootstrapData } from "../../api/generated/contracts";
import { MultiSelect, type MultiSelectOption } from "../../components/ui/MultiSelect";
import { Select } from "../../components/ui/Select";
import type { ConfigureFormValues } from "./model";
import { ReorderableList } from "./ReorderableList";

type ChangeConfiguration = <Key extends keyof ConfigureFormValues>(
  key: Key,
  value: ConfigureFormValues[Key],
) => void;

export function LanguageStep({
  bootstrap,
  onChange,
  values,
}: {
  bootstrap: ConfiguratorBootstrapData;
  onChange: ChangeConfiguration;
  values: ConfigureFormValues;
}) {
  const { i18n, t } = useTranslation();
  const languageNames = new Intl.DisplayNames([i18n.resolvedLanguage ?? "en"], {
    type: "language",
  });
  const options: MultiSelectOption[] = Object.entries(bootstrap.languages).map(([code, emoji]) => ({
    label: `${emoji} ${code === "multi" ? t("configure.languages.multi") : (languageNames.of(code) ?? code)}`,
    value: code,
  }));
  const optionByValue = new Map(options.map((option) => [option.value, option]));
  const fields = [
    ["requiredLanguages", "required"],
    ["allowedLanguages", "allowed"],
    ["excludedLanguages", "excluded"],
  ] as const;

  return (
    <section className="language-groups">
      {fields.map(([key, label]) => (
        <MultiSelect
          emptyLabel={t("configure.languages.none")}
          key={key}
          label={t(`configure.languages.${label}`)}
          onChange={(selected) => onChange(key, selected)}
          options={options}
          removeLabel={(optionLabel) => t("actions.removeSelection", { label: optionLabel })}
          searchLabel={t("configure.languages.search")}
          selected={values[key]}
        />
      ))}
      <div className="ordered-selection">
        <MultiSelect
          emptyLabel={t("configure.languages.none")}
          label={t("configure.languages.preferred")}
          onChange={(preferredLanguages) => onChange("preferredLanguages", preferredLanguages)}
          options={options}
          removeLabel={(optionLabel) => t("actions.removeSelection", { label: optionLabel })}
          searchLabel={t("configure.languages.search")}
          selected={values.preferredLanguages}
        />
        {values.preferredLanguages.length > 1 ? (
          <ReorderableList
            className="ordered-selection__list"
            getId={(code) => code}
            getLabel={(code) => optionByValue.get(code)?.label ?? code}
            items={values.preferredLanguages}
            label={t("configure.resultsEditor.reorderLanguages")}
            onChange={(preferredLanguages) => onChange("preferredLanguages", preferredLanguages)}
            renderItem={(code, _index, reorder) => (
              <div className="ordered-selection__row">
                {reorder.handle}
                <span>{optionByValue.get(code)?.label ?? code}</span>
              </div>
            )}
          />
        ) : null}
      </div>
      <Select
        label={t("configure.resultsEditor.unknownLanguage")}
        onValueChange={(unknown) => onChange("unknownLanguages", unknown as "allow" | "exclude")}
        value={values.unknownLanguages}
      >
        <option value="allow">{t("configure.resultsEditor.allow")}</option>
        <option value="exclude">{t("configure.resultsEditor.exclude")}</option>
      </Select>
    </section>
  );
}
