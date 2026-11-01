"""Tests for publication with mandatory evidence and external access verification (T-048).

DoD:
- Without external credentials / access -> BLOCKED-EXTERNAL.
- Absence of external_url on fixture/mock response strictly does NOT grant PUBLISHED status.
- Valid publication creates Listing with external_url and publish_evidence, and logs to TruthLog.
- Module length <= 400 lines.
"""

from decimal import Decimal
from unittest.mock import MagicMock

from sqlalchemy import select

from nextlife.connectors.base import PublishResult
from nextlife.core.status import ItemStatus
from nextlife.services.lot_creation import create_item_and_lot
from nextlife.services.publish import publish_lot
from nextlife.storage.db import get_db_session
from nextlife.storage.models import AuditEvent, Item, Listing, Lot, TruthLog


def test_publish_without_external_credentials_yields_blocked_external():
    with get_db_session() as session:
        created = create_item_and_lot(
            session=session,
            title="Vintage Clock",
            category_key="general",
            price=Decimal("1800.00"),
        )
        lot_code = created["lot_code"]
        item_code = created["item_code"]

        # Call with require_credentials=True for a marketplace requiring secrets
        publish_lot(
            session=session,
            lot_code=lot_code,
            target_marketplaces=["bazos_cz"],
            require_credentials=True,
        )
        session.commit()

        # In test environment without external live credentials, it reports BLOCKED-EXTERNAL
        # Or if auth is none, let's verify blocked_external flag behavior with mock
        # Let's test with a mock config that requires secret
        from nextlife.core.config import MarketplaceConfig, load_config
        mock_auth_cfg = MarketplaceConfig(
            key="secure_market",
            title="Secure Market",
            country="CZ",
            currency="CZK",
            access_mode="DIRECT",
            base_url="https://secure.example.com",
            auth={"type": "api_key", "secret_ref": "MISSING_LIVE_API_KEY"},
            capabilities={"post": True},
            selectors={"search_result": ".item", "price": ".price"},
            mapping={},
            rate_limits={},
        )
        cfg = load_config()
        cfg.marketplaces["secure_market"] = mock_auth_cfg

        res_secure = publish_lot(
            session=session,
            lot_code=lot_code,
            target_marketplaces=["secure_market"],
            require_credentials=True,
            config_bundle=cfg,
        )
        session.commit()

        assert res_secure["lot_status"] == "BLOCKED-EXTERNAL"
        assert "secure_market" in res_secure["blocked_external"]
        assert "BLOCKED-EXTERNAL" in res_secure["blocked_external"]["secure_market"]

        # Check AuditEvent
        audit = session.scalar(
            select(AuditEvent)
            .where(AuditEvent.entity == "lot", AuditEvent.entity_id == lot_code, AuditEvent.action == "LOT_PUBLISH_ATTEMPT")
            .order_by(AuditEvent.at.desc())
        )
        assert audit is not None
        assert audit.data["final_lot_status"] == "BLOCKED-EXTERNAL"

        # Cleanup
        session.query(Listing).filter(Listing.item_code == item_code).delete()
        session.query(Lot).filter(Lot.code == lot_code).delete()
        session.query(Item).filter(Item.code == item_code).delete()
        session.commit()


def test_publish_fixture_missing_url_denies_published_status():
    with get_db_session() as session:
        created = create_item_and_lot(
            session=session,
            title="Item Missing URL Test",
            category_key="general",
            price=Decimal("990.00"),
        )
        lot_code = created["lot_code"]
        item_code = created["item_code"]

        # Mock connector returning missing/empty external_url
        bad_connector = MagicMock()
        bad_connector.publish.return_value = PublishResult(
            external_id="mock-123",
            external_url="",  # EMPTY URL!
            evidence={"http_status": 200, "timestamp": "2026-09-08T00:00:00Z"},
        )

        res = publish_lot(
            session=session,
            lot_code=lot_code,
            target_marketplaces=["bazos_cz"],
            connector_overrides={"bazos_cz": bad_connector},
        )
        session.commit()

        # Must NOT give PUBLISHED status
        assert res["item_status"] != ItemStatus.PUBLISHED.value
        assert res["lot_status"] != ItemStatus.PUBLISHED.value
        assert res["lot_status"] == ItemStatus.BLOCKED.value
        assert "bazos_cz" in res["failures"]
        assert "missing mandatory external_url" in res["failures"]["bazos_cz"]

        # Verify no Listing record created with empty URL
        listings = session.scalars(select(Listing).where(Listing.item_code == item_code)).all()
        assert len(listings) == 0

        # Cleanup
        session.query(Lot).filter(Lot.code == lot_code).delete()
        session.query(Item).filter(Item.code == item_code).delete()
        session.commit()


def test_publish_missing_evidence_denies_published_status():
    with get_db_session() as session:
        created = create_item_and_lot(
            session=session,
            title="Item Missing Evidence Test",
            category_key="general",
            price=Decimal("500.00"),
        )
        lot_code = created["lot_code"]
        item_code = created["item_code"]

        # Mock connector returning URL but empty evidence
        no_evidence_connector = MagicMock()
        no_evidence_connector.publish.return_value = PublishResult(
            external_id="mock-456",
            external_url="https://www.bazos.cz/inzerat/mock-456",
            evidence={},  # EMPTY EVIDENCE!
        )

        res = publish_lot(
            session=session,
            lot_code=lot_code,
            target_marketplaces=["bazos_cz"],
            connector_overrides={"bazos_cz": no_evidence_connector},
        )
        session.commit()

        # Must NOT give PUBLISHED status
        assert res["item_status"] != ItemStatus.PUBLISHED.value
        assert res["lot_status"] == ItemStatus.BLOCKED.value
        assert "missing mandatory publish_evidence" in res["failures"]["bazos_cz"]

        # Cleanup
        session.query(Lot).filter(Lot.code == lot_code).delete()
        session.query(Item).filter(Item.code == item_code).delete()
        session.commit()


def test_successful_publication_with_url_and_evidence():
    with get_db_session() as session:
        created = create_item_and_lot(
            session=session,
            title="Item Valid Publish Test",
            category_key="general",
            price=Decimal("2500.00"),
        )
        lot_code = created["lot_code"]
        item_code = created["item_code"]

        good_connector = MagicMock()
        good_connector.publish.return_value = PublishResult(
            external_id="bazos-9999",
            external_url="https://www.bazos.cz/inzerat/bazos-9999",
            evidence={
                "http_status": 200,
                "timestamp": "2026-09-08T12:00:00Z",
                "verified_snippet": "Inzerát úspěšně vložen",
            },
        )

        res = publish_lot(
            session=session,
            lot_code=lot_code,
            target_marketplaces=["bazos_cz"],
            connector_overrides={"bazos_cz": good_connector},
        )
        session.commit()

        assert res["item_status"] == ItemStatus.PUBLISHED.value
        assert res["lot_status"] == ItemStatus.PUBLISHED.value
        assert "bazos_cz" in res["successes"]

        # Verify Listing in DB
        listing = session.scalar(select(Listing).where(Listing.item_code == item_code))
        assert listing is not None
        assert listing.external_url == "https://www.bazos.cz/inzerat/bazos-9999"
        assert listing.status == "PUBLISHED"
        assert listing.publish_evidence["http_status"] == 200

        # Verify TruthLog entry
        truth = session.scalar(
            select(TruthLog)
            .where(TruthLog.claim.like(f"%{lot_code}%"))
            .order_by(TruthLog.at.desc())
        )
        assert truth is not None
        assert truth.evidence["verified_snippet"] == "Inzerát úspěšně vložen"

        # Cleanup
        session.query(Listing).filter(Listing.item_code == item_code).delete()
        session.query(Lot).filter(Lot.code == lot_code).delete()
        session.query(Item).filter(Item.code == item_code).delete()
        session.commit()
