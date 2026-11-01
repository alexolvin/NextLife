"""Configuration schemas and loaders for NextLife."""

import os
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ValidationError
from sqlalchemy import make_url

from nextlife.core.exceptions import ConfigError
from nextlife.core.secrets import resolve_secret


class ServerConfig(BaseModel):
    host: str
    port: int


class DatabaseConfig(BaseModel):
    dsn_secret_ref: str
    schema_name: str


class StorageConfig(BaseModel):
    media_dir: str
    profiles_dir: str


class AdbNodeConfig(BaseModel):
    """TZ M1 B.2: адрес adb-узла и порт — в переменных окружения, не в коде.

    The fields hold the ENV VAR NAMES; the values are resolved at runtime
    (owner confirmation 6 supplies the Tailscale address of the USB node).
    """

    host_env: str = "NEXTLIFE_ADB_HOST"
    port_env: str = "NEXTLIFE_ADB_PORT"


class HumanPacingConfig(BaseModel):
    """TZ M1 B.3 (property 4): темп человеческий — паузы между действиями
    из конфига."""

    min_pause_seconds: float = 2.0
    max_pause_seconds: float = 8.0


class PhoneBrokerConfig(BaseModel):
    """Access to the phone via the phonebroker service (single-phone ADB
    broker, 127.0.0.1:8090). Raw ADB is blocked by nftables for every user
    except the broker, so the bridge talks to the broker's HTTP API.

    TZ Marketplaces: the broker is the SINGLE source of truth for app
    packages and rate limits (daily cap / min interval). The NextLife config
    keeps only the transport address + token ref; the per-marketplace broker
    platform id and screen fingerprints live in the marketplace config
    (``MarketplaceConfig.android``)."""

    base_url: str = "http://127.0.0.1:8090"
    # ENV var name holding the broker bearer token (per-project).
    token_secret_ref: str = "PHONEBROKER_TOKEN_NEXTLIFE"
    lease_max_duration_s: int = 300


class ScreenFingerprintConfig(BaseModel):
    """TZ Marketplaces: a screen fingerprint tied to the app version.

    The recognizer matches a ui_dump against these fingerprints (resource-id /
    text only, never coordinates). When the app's versionName (from the broker
    ``/health``) does not match ``version``, the bridge logs a warning and
    still checks the fingerprint; a fingerprint mismatch is a FAIL ("screen
    changed"), never a guess.
    """

    name: str
    version: str
    resource_ids: list[str] = []
    texts: list[str] = []


class SearchScreenConfig(BaseModel):
    """Per-platform search UI selectors and domain (Stage 2: one code, config diffs).

    Link extraction (Phase 1): the listing link is obtained via the in-app
    Share flow — tap ``share_menu`` (toolbar) → tap ``share_action`` (the
    "Share" item) → read the URL from the share sheet ui_dump. The card tap
    uses the card node's own content-desc (unique per listing)."""

    search_field: str = ""
    result_card: str = ""
    share_menu: str = ""
    share_action: str = ""
    domain: str = ""
    # Optional: a consent/onboarding screen that can block the search field on
    # app launch (e.g. Vinted's Czech cookie consent). When set and present in
    # the UI dump before search, it is tapped once to dismiss it.
    consent_selector: str = ""


class AndroidMarketplaceConfig(BaseModel):
    """TZ Marketplaces: the per-marketplace ANDROID channel config.

    The broker is the single source of truth for the app package and the rate
    limits; this config keeps only the broker platform id, screen fingerprints,
    and the search UI selectors (Stage 2)."""

    # Platform key in the broker's broker.yaml (the app to lease).
    platform_id: str
    # Version-tied screen fingerprints for the recognizer.
    fingerprints: list[ScreenFingerprintConfig] = []
    # Search UI selectors and domain (Stage 2: one code, config diffs).
    search: SearchScreenConfig = SearchScreenConfig()


class AndroidBridgeConfig(BaseModel):
    """Config model of the ANDROID channel (TZ M1 B.1/B.2/B.3).

    Replaces the removed OpenClaw-agent stub config (host/port of a local
    fake bridge). The bridge is ADB + uiautomator2 over Tailscale; the node
    address comes from environment variables, not from this file.

    TZ Marketplaces: the broker is the single source of truth for app
    packages and rate limits (daily cap / min interval — a broker 403 is the
    only frequency control). Per-marketplace broker platform id + screen
    fingerprints live in the marketplace config (``MarketplaceConfig.android``).
    """

    enabled: bool = False
    adb: AdbNodeConfig = AdbNodeConfig()
    pacing: HumanPacingConfig = HumanPacingConfig()
    # Access to the phone via the phonebroker service (single-phone ADB
    # broker). The bridge uses this transport (raw ADB is blocked).
    phone_broker: PhoneBrokerConfig = PhoneBrokerConfig()


class MediaConfig(BaseModel):
    generative_endpoint: str
    vlm_endpoint: str


class TextGenConfig(BaseModel):
    """Text-generation model endpoint (wizard step 2). Empty endpoint = stub."""

    endpoint: str = ""
    api_key_secret_ref: str = "NEXTLIFE_TEXTGEN_API_KEY"
    model: str = ""


class AuthConfig(BaseModel):
    web_token_secret_ref: str
    # SecretRef (env var name) for the OpenClaw skill token: the credential the
    # OpenClaw runtime uses to authenticate against the NextLife API. Sourced
    # from the environment only (systemd EnvironmentFile in prod, explicit
    # export in dev/tests), resolved via get_openclaw_token().
    openclaw_token_secret_ref: str = "NEXTLIFE_OPENCLAW_TOKEN"


class FxConfig(BaseModel):
    source_url: str
    refresh_interval_seconds: int


class AppConfig(BaseModel):
    base_currency: str
    code_release_days: int
    min_comparables: int
    pii_retention_days: int
    poll_interval_seconds: int
    reservation_hours: int = 24
    unpublish_deadline_minutes: int = 15
    cod_max_amount: Decimal = Decimal("5000.00")
    prepay_required_above: Decimal = Decimal("5000.00")
    server: ServerConfig
    database: DatabaseConfig
    storage: StorageConfig
    android_bridge: AndroidBridgeConfig = AndroidBridgeConfig()
    media: MediaConfig
    text_gen: TextGenConfig = TextGenConfig()
    auth: AuthConfig
    fx: FxConfig


class MarketplaceConfig(BaseModel):
    key: str
    title: str
    country: str
    currency: str
    access_mode: str
    # Publish channel override: "" (default) = follow access_mode
    # (DIRECT -> web connector, ANDROID -> blocked until the phone
    # connector lands); "web" = publish via the web connector even when
    # access_mode is ANDROID (the search channel stays on the phone).
    publish_channel: str = ""
    base_url: str
    auth: dict[str, Any]
    capabilities: dict[str, Any]
    selectors: dict[str, Any]
    mapping: dict[str, Any] = {}
    rate_limits: dict[str, Any]
    # Route map lives in config, not in connector code (TZ M1 A.4):
    #   routes.search — path template; "{query}" placeholder is filled with the
    #   URL-encoded query; routes.search_params maps canonical parameter roles
    #   (query/min_price/max_price/zip_code) to the marketplace's parameter names.
    routes: dict[str, Any] = {}
    # Category tree for publish. Each entry is {top, label, subs}:
    # `top` is the section key (Bazoš subdomain), `subs` are the rubric labels
    # shown in that section. The LLM returns "top/sub"; publish uses `top` in
    # the URL and `sub` to pick the form dropdown. Empty list = no tree.
    category_tree: list[dict[str, Any]] = []
    # Daily canary probe query (TZ M1 D.1): a harmless search that must keep
    # returning parseable results.
    canary_query: str | None = None
    # Search transport of the DIRECT channel (TZ M1 A.3/A.4):
    #   "html" (default) — GET the search page, parse cards via selectors;
    #   "json" — the site's own web API (POST JSON, e.g. Aukro's
    #     /backend-web/api/offers/searchItemsCommon): the connector performs
    #     the probe and parses the JSON response; HTML selectors are not used.
    search_mode: str = "html"
    # Category restriction in the marketplace registry (TZ M1 A.5): when set,
    # lots whose category is not in this list are NOT offered on this
    # marketplace (the publish path refuses them; enforced by tests).
    # None = no restriction (most marketplaces).
    allowed_categories: list[str] | None = None
    # TZ Реструктуризация UI (§2.4, §6.4): Срок показа объявления в днях (listing_ttl_days)
    listing_ttl_days: int = 30
    # TZ Marketplaces: the ANDROID channel for this marketplace (None = the
    # marketplace is not reachable via the phone broker). The broker is the
    # single source of truth for the app package + rate limits; this keeps
    # only the broker platform id + version-tied screen fingerprints.
    android: AndroidMarketplaceConfig | None = None

    @property
    def max_description_length(self) -> int:
        return int(
            self.capabilities.get("max_body_len")
            or self.capabilities.get("max_description_length")
            or 3000
        )


class ShippingMethod(BaseModel):
    key: str
    title: str
    cost_base: float
    currency: str
    allowed_countries: list[str]


class ShippingConfig(BaseModel):
    methods: list[ShippingMethod]


class FAQPattern(BaseModel):
    key: str
    intents: list[str]
    response_template: str
    required_field: str | None = None
    required_fields: list[str] = []


class FAQConfig(BaseModel):
    patterns: list[FAQPattern]


class ConfigBundle:
    def __init__(
        self,
        app: AppConfig,
        marketplaces: dict[str, MarketplaceConfig],
        shipping: ShippingConfig,
        faq: FAQConfig,
    ) -> None:
        self.app = app
        self.marketplaces = marketplaces
        self.shipping = shipping
        self.faq = faq


def _load_yaml_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ConfigError(f"Config file not found: {path}")
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
            if not isinstance(data, dict):
                raise ConfigError(f"Config file {path} does not contain a valid YAML dictionary")
            return data
    except yaml.YAMLError as exc:
        raise ConfigError(f"YAML syntax error in file '{path}': {exc}") from exc


def load_config(config_dir: Path | str | None = None) -> ConfigBundle:
    """Load and validate all configuration files from the config directory."""
    if config_dir is None:
        # Default to repo root config/
        base_dir = Path(__file__).resolve().parent.parent.parent.parent
        c_dir = base_dir / "config"
    else:
        c_dir = Path(config_dir)

    nextlife_yaml_path = c_dir / "nextlife.yaml"
    raw_nextlife = _load_yaml_file(nextlife_yaml_path)

    # Rename schema to schema_name for Pydantic if present
    if "database" in raw_nextlife and isinstance(raw_nextlife["database"], dict):
        if "schema" in raw_nextlife["database"]:
            raw_nextlife["database"]["schema_name"] = raw_nextlife["database"].pop("schema")

    try:
        app_config = AppConfig.model_validate(raw_nextlife)
    except ValidationError as exc:
        first_err = exc.errors()[0]
        loc = " -> ".join(str(x) for x in first_err["loc"])
        msg = first_err["msg"]
        raise ConfigError(
            f"Config validation error in file '{nextlife_yaml_path}', key '{loc}': {msg}"
        ) from exc

    # Load marketplaces
    marketplaces_dir = c_dir / "marketplaces"
    marketplaces: dict[str, MarketplaceConfig] = {}
    if marketplaces_dir.exists():
        for yaml_file in marketplaces_dir.glob("*.yaml"):
            raw_mp = _load_yaml_file(yaml_file)
            try:
                mp_config = MarketplaceConfig.model_validate(raw_mp)
                marketplaces[mp_config.key] = mp_config
            except ValidationError as exc:
                first_err = exc.errors()[0]
                loc = " -> ".join(str(x) for x in first_err["loc"])
                msg = first_err["msg"]
                raise ConfigError(
                    f"Config validation error in file '{yaml_file}', key '{loc}': {msg}"
                ) from exc

    # Load shipping
    shipping_path = c_dir / "shipping.yaml"
    raw_shipping = _load_yaml_file(shipping_path)
    try:
        shipping_config = ShippingConfig.model_validate(raw_shipping)
    except ValidationError as exc:
        first_err = exc.errors()[0]
        loc = " -> ".join(str(x) for x in first_err["loc"])
        msg = first_err["msg"]
        raise ConfigError(
            f"Config validation error in file '{shipping_path}', key '{loc}': {msg}"
        ) from exc

    # Load faq
    faq_path = c_dir / "faq.yaml"
    raw_faq = _load_yaml_file(faq_path)
    try:
        faq_config = FAQConfig.model_validate(raw_faq)
    except ValidationError as exc:
        first_err = exc.errors()[0]
        loc = " -> ".join(str(x) for x in first_err["loc"])
        msg = first_err["msg"]
        raise ConfigError(
            f"Config validation error in file '{faq_path}', key '{loc}': {msg}"
        ) from exc

    return ConfigBundle(
        app=app_config,
        marketplaces=marketplaces,
        shipping=shipping_config,
        faq=faq_config,
    )


def get_database_url(config: ConfigBundle | None = None) -> str:
    """The single source of the database URL: the SecretRef declared in
    config/nextlife.yaml (NEXTLIFE_DSN), resolved from the process
    environment. Both the application (storage/db.py) and alembic/env.py
    resolve the DSN through this function; no other code resolves it.
    """
    cfg = config or load_config()
    return resolve_secret(cfg.app.database.dsn_secret_ref)


def get_libpq_dsn(config: ConfigBundle | None = None) -> str:
    """The single DSN source expressed as a plain libpq/psycopg2 URL.

    Resolves the same SecretRef as get_database_url() and drops the SQLAlchemy
    driver suffix (e.g. ``+psycopg2``) via make_url, so raw psycopg2 and external
    tools (psql, pg_dump) receive a URL they accept. This is the only place that
    strips the driver suffix; no other code does.
    """
    url = make_url(get_database_url(config))
    return url.set(drivername="postgresql").render_as_string(hide_password=False)


def get_openclaw_token(config: ConfigBundle | None = None) -> str:
    """Resolve the OpenClaw skill token from the environment.

    Single source for the token the OpenClaw runtime uses to authenticate
    against the NextLife API: the SecretRef declared in config/nextlife.yaml
    (auth.openclaw_token_secret_ref, default NEXTLIFE_OPENCLAW_TOKEN). Resolved
    from the process env via resolve_secret, which names the variable in the
    error when it is unset.
    """
    cfg = config or load_config()
    return resolve_secret(cfg.app.auth.openclaw_token_secret_ref)


def get_data_dir() -> Path:
    """The single source of the base data directory.

    Resolved from the NEXTLIFE_DATA_DIR environment variable when set
    (expanded, absolute or CWD-relative); otherwise the default ``data``
    directory relative to the process working directory. Media, profiles and
    handover documents all hang off this base, so a deployment points
    NEXTLIFE_DATA_DIR at a stable absolute location (e.g. a systemd
    WorkingDirectory) instead of relying on the process CWD.
    """
    env = os.environ.get("NEXTLIFE_DATA_DIR")
    if env:
        return Path(env).expanduser()
    return Path("data")


def get_media_dir() -> Path:
    """Media storage root: ``<data_dir>/media``.

    Every media path in the app (the /media static mount, the upload temp
    dir, the ingest/sanitize/resize defaults and the variants dir) derives from
    this one value, so they stay in agreement and follow NEXTLIFE_DATA_DIR.
    """
    return get_data_dir() / "media"
