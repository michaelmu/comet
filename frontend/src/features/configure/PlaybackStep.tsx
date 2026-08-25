import { KeyRound, Plus, Trash2, UserPlus } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { ConfiguratorBootstrapData } from "../../api/generated/contracts";
import { Button } from "../../components/ui/Button";
import { Input } from "../../components/ui/Input";
import { Select } from "../../components/ui/Select";
import { Switch } from "../../components/ui/Switch";
import { type DebridDraft, DIRECT_TORRENT_SERVICE } from "./model";
import { ReorderableList } from "./ReorderableList";

const DEBRID_RESOURCES: Partial<
  Record<string, { apiKeyUrl: string; login?: true; referralUrl?: string }>
> = {
  realdebrid: {
    apiKeyUrl: "https://real-debrid.com/apitoken",
    referralUrl: "https://real-debrid.com/?id=16161532",
  },
  torbox: {
    apiKeyUrl: "https://torbox.app/settings",
    referralUrl: "https://torbox.app/subscription?referral=1ffb2238-1c5f-402e-a2ce-3d7a86c52d02",
  },
  alldebrid: {
    apiKeyUrl: "https://alldebrid.com/apikeys",
  },
  debridlink: {
    apiKeyUrl: "https://debrid-link.com/webapp/apikey",
    referralUrl: "https://debrid-link.fr/id/G7mli",
  },
  premiumize: {
    apiKeyUrl: "https://premiumize.me/account",
  },
  debrider: {
    apiKeyUrl: "https://debrider.app/dashboard/account",
  },
  easydebrid: {
    apiKeyUrl: "https://paradise-cloud.com/products/easydebrid",
  },
  offcloud: {
    apiKeyUrl: "https://offcloud.com/#/account",
  },
  pikpak: {
    apiKeyUrl: "https://mypikpak.com",
    login: true,
  },
};

export function PlaybackStep({
  bootstrap,
  debridServices,
  onDebridServicesChange,
  onProxyPasswordChange,
  onScrapeChange,
  proxyPassword,
  scrape,
  showDebridOptions,
}: {
  bootstrap: ConfiguratorBootstrapData;
  debridServices: DebridDraft[];
  onDebridServicesChange: (services: DebridDraft[]) => void;
  onProxyPasswordChange: (password: string) => void;
  onScrapeChange: (enabled: boolean) => void;
  proxyPassword: string;
  scrape: boolean;
  showDebridOptions: boolean;
}) {
  const { t } = useTranslation();
  const selectedServices = new Set(debridServices.map(({ service }) => service));
  const supportedServices = bootstrap.capabilities.torrent_streams
    ? [...bootstrap.debrid_services, DIRECT_TORRENT_SERVICE]
    : bootstrap.debrid_services;
  const nextService = supportedServices.find((service) => !selectedServices.has(service));
  const serviceLabel = (service: string) =>
    t(
      service === DIRECT_TORRENT_SERVICE
        ? "configure.playback.enableTorrent"
        : `configure.debridServices.${service}`,
    );

  return (
    <section className="configuration-fields">
      <div className="debrid-list">
        <ReorderableList
          getId={(entry) => entry.configurationId}
          getLabel={(entry) => serviceLabel(entry.service)}
          items={debridServices}
          label={t("configure.playback.reorderService")}
          onChange={onDebridServicesChange}
          renderItem={(entry, index, reorder) => {
            const resources = DEBRID_RESOURCES[entry.service];
            return (
              <article className="debrid-row" data-debrid-id={entry.configurationId}>
                {reorder.handle}
                <Select
                  label={t("configure.playback.service")}
                  labelHidden
                  onValueChange={(service) =>
                    onDebridServicesChange(
                      debridServices.map((current, position) =>
                        position === index
                          ? {
                              ...current,
                              accountId:
                                service === DIRECT_TORRENT_SERVICE
                                  ? ""
                                  : current.service === DIRECT_TORRENT_SERVICE
                                    ? crypto.randomUUID()
                                    : current.accountId,
                              apiKey: service === DIRECT_TORRENT_SERVICE ? "" : current.apiKey,
                              service,
                            }
                          : current,
                      ),
                    )
                  }
                  value={entry.service}
                >
                  {supportedServices
                    .filter(
                      (service) => service === entry.service || !selectedServices.has(service),
                    )
                    .map((service) => (
                      <option key={service} value={service}>
                        {serviceLabel(service)}
                      </option>
                    ))}
                </Select>
                {entry.service === DIRECT_TORRENT_SERVICE ? (
                  <div className="debrid-row__p2p">{t("configure.playback.p2pNoKey")}</div>
                ) : (
                  <div className="debrid-credentials">
                    <Input
                      autoComplete="off"
                      label={t("configure.playback.apiKey")}
                      labelHidden
                      onChange={(event) =>
                        onDebridServicesChange(
                          debridServices.map((current, position) =>
                            position === index
                              ? { ...current, apiKey: event.target.value }
                              : current,
                          ),
                        )
                      }
                      placeholder={t(
                        resources?.login
                          ? "configure.playback.pikpakFormat"
                          : "configure.playback.apiKey",
                      )}
                      type={resources?.login ? "text" : "password"}
                      value={entry.apiKey}
                    />
                    {resources ? (
                      <div className="debrid-credentials__links">
                        <a href={resources.apiKeyUrl} rel="noreferrer" target="_blank">
                          <KeyRound aria-hidden="true" size={13} />
                          {t("configure.playback.getApiKey")}
                        </a>
                        {resources.referralUrl ? (
                          <a href={resources.referralUrl} rel="noreferrer" target="_blank">
                            <UserPlus aria-hidden="true" size={13} />
                            {t("configure.playback.createAccount")}
                          </a>
                        ) : null}
                      </div>
                    ) : null}
                  </div>
                )}
                <Button
                  aria-label={t("configure.playback.removeService")}
                  onClick={() =>
                    onDebridServicesChange(
                      debridServices.filter((_, position) => position !== index),
                    )
                  }
                  variant="ghost"
                >
                  <Trash2 aria-hidden="true" size={17} />
                </Button>
              </article>
            );
          }}
        />
        {nextService ? (
          <Button
            onClick={() =>
              onDebridServicesChange([
                ...debridServices,
                {
                  accountId: nextService === DIRECT_TORRENT_SERVICE ? "" : crypto.randomUUID(),
                  apiKey: "",
                  configurationId: crypto.randomUUID(),
                  service: nextService,
                },
              ])
            }
            variant="secondary"
          >
            <Plus aria-hidden="true" size={17} />
            {t("configure.playback.addService")}
          </Button>
        ) : null}
      </div>
      {showDebridOptions ? (
        <div className="option-stack">
          <Switch
            checked={scrape}
            label={t("configure.playback.scrapeLibraries")}
            onCheckedChange={onScrapeChange}
          />
        </div>
      ) : null}
      {bootstrap.capabilities.proxy_debrid_stream && showDebridOptions ? (
        <Input
          autoComplete="off"
          label={t("configure.playback.proxyPassword")}
          onChange={(event) => onProxyPasswordChange(event.target.value)}
          type="password"
          value={proxyPassword}
        />
      ) : null}
    </section>
  );
}
