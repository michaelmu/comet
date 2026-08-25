import { ArrowDownWideNarrow, Filter, Palette, Share2, SlidersHorizontal } from "lucide-react";
import { type ReactNode, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import type { ConfiguratorBootstrapData } from "../../api/generated/contracts";
import type { MultiSelectOption } from "../../components/ui/MultiSelect";
import type { ResultsDraft } from "./model";
import { AdvancedEditor, activeAdvancedCount } from "./results/AdvancedEditor";
import { AppearanceEditor } from "./results/AppearanceEditor";
import { activeDeliveryCount, DeliveryEditor } from "./results/DeliveryEditor";
import { activeFilterCount, FiltersEditor } from "./results/FiltersEditor";
import { OrderEditor } from "./results/OrderEditor";
import { isUncachedShortcut } from "./results/vocabulary";

const TABS = [
  { icon: ArrowDownWideNarrow, id: "order" },
  { icon: Filter, id: "filters" },
  { icon: SlidersHorizontal, id: "delivery" },
  { icon: Palette, id: "appearance" },
  { icon: Share2, id: "advanced" },
] as const;

export function ResultsStep({
  bootstrap,
  configuredDebridKinds,
  onChange,
  portability,
  results,
  showDebridSync,
}: {
  bootstrap: ConfiguratorBootstrapData;
  configuredDebridKinds: readonly string[];
  onChange: (results: ResultsDraft) => void;
  portability: ReactNode;
  results: ResultsDraft;
  showDebridSync: boolean;
}) {
  const { i18n, t } = useTranslation();
  const [tab, setTab] = useState<(typeof TABS)[number]["id"]>("order");
  const update = <Key extends keyof ResultsDraft>(key: Key, value: ResultsDraft[Key]) =>
    onChange({ ...results, [key]: value });
  const languages = useMemo<MultiSelectOption[]>(() => {
    const names = new Intl.DisplayNames([i18n.resolvedLanguage ?? "en"], { type: "language" });
    return Object.entries(bootstrap.languages).map(([code, emoji]) => ({
      label: `${emoji} ${code === "multi" ? t("configure.languages.multi") : (names.of(code) ?? code)}`,
      value: code,
    }));
  }, [bootstrap.languages, i18n.resolvedLanguage, t]);
  const counts: Partial<Record<(typeof TABS)[number]["id"], number>> = {
    advanced: activeAdvancedCount(results),
    appearance: results.display.preset === "default" ? 0 : 1,
    delivery: activeDeliveryCount(results),
    filters: activeFilterCount(results.filters),
    order: results.sort.length,
  };

  return (
    <section className="results-editor">
      <nav aria-label={t("configure.sections.results")} className="results-tabs">
        {TABS.map(({ icon: Icon, id }) => (
          <button
            aria-current={id === tab ? "page" : undefined}
            aria-label={t(`configure.resultsEditor.tab.${id}`)}
            className="results-tab"
            key={id}
            onClick={() => setTab(id)}
            type="button"
          >
            <Icon aria-hidden="true" size={16} strokeWidth={1.8} />
            <span>{t(`configure.resultsEditor.tab.${id}`)}</span>
            {counts[id] ? (
              <span aria-hidden="true" className="results-tab__count">
                {counts[id]}
              </span>
            ) : null}
          </button>
        ))}
      </nav>

      {tab === "order" ? (
        <OrderEditor
          bootstrap={bootstrap}
          onChange={(sort) => update("sort", sort)}
          sort={results.sort}
        />
      ) : tab === "filters" ? (
        <FiltersEditor
          bootstrap={bootstrap}
          debridKinds={configuredDebridKinds}
          filters={results.filters}
          languages={languages}
          onChange={(filters) => update("filters", filters)}
        />
      ) : tab === "delivery" ? (
        <DeliveryEditor
          alternatives={results.alternatives}
          limits={results.limits}
          onAlternativesChange={(alternatives) => update("alternatives", alternatives)}
          onLimitsChange={(limits) => update("limits", limits)}
        />
      ) : tab === "appearance" ? (
        <AppearanceEditor
          bootstrap={bootstrap}
          display={results.display}
          onChange={(display) => update("display", display)}
        />
      ) : (
        <AdvancedEditor
          auxiliary={results.auxiliary}
          bootstrap={bootstrap}
          onAuxiliaryChange={(auxiliary) => update("auxiliary", auxiliary)}
          onRulesChange={(rules) =>
            update("filters", {
              ...results.filters,
              rules: [...rules, ...results.filters.rules.filter(isUncachedShortcut)],
            })
          }
          portability={portability}
          rules={results.filters.rules.filter((rule) => !isUncachedShortcut(rule))}
          showDebridSync={showDebridSync}
        />
      )}
    </section>
  );
}
