import type { ConfiguratorBootstrapData } from "../../api/generated/contracts";

/** Minimal bootstrap payload for component tests; override only what a test asserts. */
export function bootstrapFixture(
  overrides: Partial<Omit<ConfiguratorBootstrapData, "capabilities">> & {
    capabilities?: Partial<ConfiguratorBootstrapData["capabilities"]>;
  } = {},
): ConfiguratorBootstrapData {
  return {
    debrid_services: [],
    default_configuration: {},
    languages: {},
    native_usenet_sources: [],
    result_display_presets: {},
    result_facets: {},
    result_fields: [],
    result_policy_fields: [],
    result_scopes: [],
    result_sort_keys: [],
    result_sort_presets: {},
    result_sort_vocabulary: {},
    usenet_provider_kinds: [],
    usenet_source_kinds: [],
    ...overrides,
    capabilities: {
      native_usenet: false,
      proxy_debrid_stream: false,
      stremio_api_prefix: "",
      torrent_streams: true,
      usenet: false,
      ...overrides.capabilities,
    },
  };
}
