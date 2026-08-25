import json
from functools import lru_cache

from pydantic import ValidationError

from comet.core.config_codec import (
    decode_configuration_segment,
    encode_configuration_segment,
)
from comet.core.credentials import api_credential
from comet.core.models import (
    ConfigModel,
    default_config,
    rtn_ranking_default,
    rtn_settings_default,
    settings,
)
from comet.core.sources import TORRENT_PROVIDER_KINDS
from comet.results.config import LanguagesConfig, ResultsConfig
from comet.results.formatting import compile_display
from comet.results.migrations import migrate_configuration_document
from comet.results.policy import ReleasePolicy


def _normalize_debrid_config(validated_config: dict) -> dict:
    if validated_config.get("schemaVersion") == 2:
        debrid_entries, enable_torrent = _normalize_v2_torrent_providers(
            validated_config
        )
        validated_config["_debridEntries"] = debrid_entries
        validated_config["_enableTorrent"] = enable_torrent
        return validated_config

    debrid_entries = []
    enable_torrent = False

    debrid_services = validated_config["debridServices"]
    if debrid_services:
        debrid_entries = [
            {"service": entry["service"], "apiKey": entry["apiKey"]}
            for entry in debrid_services
        ]
        enable_torrent = validated_config["enableTorrent"]
    else:
        legacy_service = validated_config["debridService"]

        if legacy_service == "torrent":
            enable_torrent = True
        else:
            debrid_entries.append(
                {
                    "service": legacy_service,
                    "apiKey": validated_config["debridApiKey"],
                }
            )

    validated_config["_debridEntries"] = debrid_entries
    validated_config["_enableTorrent"] = enable_torrent

    return validated_config


def _normalize_v2_torrent_providers(
    config: dict,
) -> tuple[list[dict[str, str]], bool]:
    """Resolve canonical v2 torrent providers from their account envelopes."""
    if "bittorrent" not in (config.get("enabledTransports") or ()):
        return [], False
    accounts = config.get("accounts") or {}
    normalized = []
    direct_enabled = False
    for provider in config.get("playbackProviders") or ():
        if not provider.get("enabled"):
            continue
        kind = provider.get("kind")
        if kind not in TORRENT_PROVIDER_KINDS:
            continue
        if kind == "direct_torrent":
            direct_enabled = True
            continue
        account_id = provider.get("accountId")
        credential = api_credential(accounts.get(account_id)) or ""
        normalized.append(
            {
                "configurationId": provider["configurationId"],
                "service": kind,
                "apiKey": credential,
            }
        )
    return normalized, direct_enabled


def _reject_nonfinite_json_constant(_value):
    raise ValueError("non-finite JSON number")


class _ValidatedConfiguration(dict):
    def __init__(self, config: dict, url_segment: str):
        super().__init__(config)
        self.url_segment = url_segment


def normalize_validated_config(validated_config: dict) -> dict:
    """Build the runtime representation shared by every configuration entrypoint."""
    results = validated_config["results"]
    languages = validated_config["languages"]
    results_model = ResultsConfig.model_validate(results)
    languages_model = LanguagesConfig.model_validate(languages)
    validated_config["_resultsModel"] = results_model
    validated_config["_languagesModel"] = languages_model
    validated_config["_releasePolicy"] = ReleasePolicy.compile(
        results_model, languages_model
    )
    validated_config["_displayRenderer"] = compile_display(results_model.display)
    options = {
        "allow_english_in_languages": False,
        "remove_unknown_languages": languages["unknown"] == "exclude",
        # Eligibility belongs to ReleasePolicy. RTN remains the scorer/parser.
        "remove_all_trash": False,
    }

    validated_config["rtnSettings"] = rtn_settings_default.model_copy(
        update={
            "resolutions": rtn_settings_default.resolutions,
            "options": rtn_settings_default.options.model_copy(update=options),
            "languages": rtn_settings_default.languages.model_copy(
                update={"preferred": languages["preferred"]}
            ),
        }
    )
    validated_config["rtnRanking"] = rtn_ranking_default

    if (
        settings.PROXY_DEBRID_STREAM
        and settings.PROXY_DEBRID_STREAM_PASSWORD
        == validated_config["debridStreamProxyPassword"]
        and validated_config["debridApiKey"] == ""
        and not validated_config["debridServices"]
    ):
        validated_config["debridService"] = (
            settings.PROXY_DEBRID_STREAM_DEBRID_DEFAULT_SERVICE
        )
        validated_config["debridApiKey"] = (
            settings.PROXY_DEBRID_STREAM_DEBRID_DEFAULT_APIKEY
        )

    validated_config = _normalize_debrid_config(validated_config)
    if validated_config["schemaVersion"] == 2:
        validated_config["enabledTransports"] = tuple(
            transport
            for transport in ("bittorrent", "usenet")
            if transport in (validated_config["enabledTransports"] or ())
        )
    return validated_config


@lru_cache(maxsize=512)
def _parse_and_validate_config(b64config: str):
    try:
        decoded = decode_configuration_segment(b64config)
        config = migrate_configuration_document(
            json.loads(
                decoded.decode("utf-8"),
                parse_constant=_reject_nonfinite_json_constant,
            ),
            legacy_if_results_missing=True,
        )
    except ValueError:
        return None
    try:
        validated_config = ConfigModel.model_validate(config).model_dump()
    except ValidationError:
        return None
    try:
        return _ValidatedConfiguration(
            normalize_validated_config(validated_config),
            encode_configuration_segment(decoded),
        )
    except ValueError:
        return None


_DEFAULT_VALIDATED_CONFIG = normalize_validated_config(default_config.copy())


def config_check(b64config: str | None):
    if not b64config:
        return _DEFAULT_VALIDATED_CONFIG

    return _parse_and_validate_config(b64config)


def configuration_url_segment(config: dict, original_segment: str) -> str:
    """Return the cached shortest URL segment for a validated configuration."""
    if isinstance(config, _ValidatedConfiguration):
        return config.url_segment
    return encode_configuration_segment(decode_configuration_segment(original_segment))
