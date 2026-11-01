"""Wizard step 3: controlled dry-run publish check (no live publication).

After the owner approves the publish parameters on step 3, this runs a
dry-run publish check for each selected marketplace:

- builds the existing draft (price/currency/text from the Item),
- calls the marketplace connector's ``publish(dry_run=True)`` when a
  connector exists (Vinted skeleton returns the structured no-op steps),
- reports an honest NOT-STARTED / BLOCKED / ERROR status otherwise.

The report is stored in ``attrs["wizard_dry_run_report"]``. The real publish
status is never changed — no Listing row is created, the Item stays in its
current status, and a live publish is never invoked.
"""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from nextlife.core.config import ConfigBundle, load_config
from nextlife.core.exceptions import InputError
from nextlife.storage.models import Item, ItemPhoto


def publish_dry_run(
    session: Session,
    item_code: str,
    config: ConfigBundle | None = None,
) -> dict[str, Any]:
    """Run the dry-run publish check for the selected marketplaces.

    Returns the report: ``{"ts", "marketplaces": {key: {status, ...}}}``.
    Raises InputError when the item is missing or no marketplaces are
    selected. Never performs a live publication.
    """
    from nextlife.connectors.base import CapabilityNotSupported
    from nextlife.connectors.registry import ConnectorConfigError, get_connector
    from nextlife.services.sell import SellServiceError, build_draft

    from nextlife.services.wizard_localize import approved_marketplaces

    cfg = config or load_config()
    item = session.get(Item, item_code)
    if not item:
        raise InputError(f"Item not found: {item_code}")

    marketplaces = approved_marketplaces(item)
    if not marketplaces:
        raise InputError(
            f"No approved frames for item {item_code} — approve at least one "
            "platform on wizard step 3 first."
        )

    try:
        draft = build_draft(session, item_code, cfg)
    except SellServiceError as exc:
        raise InputError(str(exc)) from exc

    lot_data = dict(draft)
    lot_data["photos"] = [
        p.path
        for p in session.query(ItemPhoto).filter(ItemPhoto.item_code == item_code).all()
    ]

    report: dict[str, dict[str, Any]] = {}
    for mp_key in marketplaces:
        mp_cfg = cfg.marketplaces.get(mp_key)
        if mp_cfg is None:
            report[mp_key] = {"status": "NOT-STARTED", "detail": "marketplace not in config"}
            continue
        try:
            connector = get_connector(mp_key, cfg)
        except ConnectorConfigError as exc:
            report[mp_key] = {"status": "NOT-STARTED", "detail": str(exc)}
            continue
        try:
            result = connector.publish(lot_data, dry_run=True)
            report[mp_key] = {
                "status": "DRY_RUN_OK",
                "external_url": result.external_url,
                "steps": result.evidence.get("steps", []),
            }
        except CapabilityNotSupported as exc:
            report[mp_key] = {"status": "BLOCKED", "detail": str(exc)}
        except Exception as exc:  # noqa: BLE001 - honest per-marketplace report
            report[mp_key] = {"status": "ERROR", "detail": f"{type(exc).__name__}: {exc}"}

    report_doc = {"ts": datetime.now(UTC).isoformat(), "marketplaces": report}
    attrs = dict(item.attrs or {})
    attrs["wizard_dry_run_report"] = report_doc
    item.attrs = attrs
    session.flush()
    return report_doc
