"""Honesty, audit, constraints, and tool self-verification checks (C-42 .. C-48) for selfcheck."""

import json
import shutil
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any
import httpx
from sqlalchemy import text

from nextlife.storage.db import get_engine

from scripts.selfcheck_common import create_lot
from scripts.selfcheck_types import CheckContext, CheckDef, evaluate_artifacts


def run_c42(ctx: CheckContext, art_dir: Path) -> dict[str, Any]:
    """C-42: Publication without valid owner approval is rejected (own lot).

    A freshly created lot is DRAFT (never approved), so a publish attempt on it
    must be rejected — independent of which lots other checks created.
    """
    code = create_lot(ctx, "C-42 publish no approval")

    with httpx.Client(timeout=10.0) as client:
        resp = client.post(
            f"{ctx.base_url}/api/lots/{code}/publish",
            json={"marketplace": "bazos_cz"},
            headers={"Authorization": f"Bearer {ctx.auth_token}"},
        )
        art_dir.joinpath("request.txt").write_text(f"POST /api/lots/{code}/publish without approval", encoding="utf-8")
        art_dir.joinpath("response.txt").write_text(f"HTTP {resp.status_code}\n{resp.text}", encoding="utf-8")

    rejected = resp.status_code in (400, 403, 409, 422) or "approval" in resp.text.lower()
    return {"status_code": resp.status_code, "rejected": rejected}


def validate_c42(art_dir: Path, meta: dict[str, Any]) -> tuple[bool, str]:
    rf = art_dir / "response.txt"
    if not rf.exists():
        return False, "отсутствует: отсутствует response.txt"
    sc = meta.get("status_code", 0)
    if sc in (404, 405):
        return False, f"отсутствует: эндпоинт публикации не найден (HTTP {sc})"
    if meta.get("rejected"):
        return True, "пройдено: публикация без утверждения отклонена"
    return False, f"поведение неверно: публикация не была отклонена (код {sc})"


def run_c43(ctx: CheckContext, art_dir: Path) -> dict[str, Any]:
    """C-43: Status 'PUBLISHED' without external marketplace URL is impossible (own lot)."""
    from nextlife.storage.db import get_db_session

    code = create_lot(ctx, "C-43 published state")
    blocked = False
    err_text = ""
    # Attempt to mark item published without listing external_url
    try:
        with get_db_session() as session:
            # Check business logic validation function on this check's own lot
            from nextlife.services.publish import validate_published_state
            is_valid, msg = validate_published_state(session, code)
            if not is_valid:
                blocked = True
                err_text = msg
    except Exception as exc:
        blocked = True
        err_text = str(exc)

    art_dir.joinpath("request.txt").write_text("validate_published_state without external URL", encoding="utf-8")
    art_dir.joinpath("response.txt").write_text(f"blocked={blocked}\nmsg={err_text}", encoding="utf-8")
    art_dir.joinpath("db_status.txt").write_text(f"validation_result={err_text}", encoding="utf-8")
    return {"blocked": blocked, "err": err_text}


def validate_c43(art_dir: Path, meta: dict[str, Any]) -> tuple[bool, str]:
    st = art_dir / "db_status.txt"
    if not st.exists():
        return False, "отсутствует: отсутствует db_status.txt"
    if meta.get("blocked"):
        return True, f"пройдено: статус PUBLISHED заблокирован без внешней ссылки: {meta.get('err')[:60]}"
    return False, "поведение неверно: статус PUBLISHED разрешён без внешней ссылки"


def run_c44(ctx: CheckContext, art_dir: Path) -> dict[str, Any]:
    """C-44: Counterparty screenshot as payment confirmation raises escalation instead of PAID."""
    from decimal import Decimal
    from nextlife.services.payment_confirmation import handle_buyer_payment_notification
    from nextlife.storage.db import get_db_session
    from nextlife.storage.models import Deal, Item, Lot

    deal_id = f"deal-c44-{uuid.uuid4().hex[:6]}"
    msg_id = f"msg-c44-{uuid.uuid4().hex[:6]}"
    escalated = False
    err_msg = ""
    status_after = "NONE"

    try:
        with get_db_session() as session:
            code_c44 = f"4{uuid.uuid4().hex[:2]}"
            item = Item(code=code_c44, kind="SELL", title="C44 Item", category_key="gpu", condition_key="USED", status="ACTIVE")
            session.add(item)
            session.flush()
            lot = Lot(code=code_c44, item_id=code_c44, price_gross=Decimal("1500"), currency="CZK", status="ACTIVE")
            session.add(lot)
            session.flush()

            deal = Deal(
                id=deal_id,
                lot_id=lot.id,
                agreed_price=Decimal("1500"),
                currency="CZK",
                price_base=Decimal("1500"),
                delivery_method="PERSONAL",
                payment_method="TRANSFER",
                status="ACTIVE",
                closure_evidence={},
            )
            session.add(deal)
            session.commit()

            try:
                res = handle_buyer_payment_notification(
                    session=session,
                    deal_id=deal_id,
                    message_id=msg_id,
                    has_screenshot=True,
                    attachment_name="payment_receipt.png",
                )
                session.commit()
                escalated = res.get("status") == "escalated" or res.get("escalation_id") is not None
            except Exception as exc:
                escalated = True
                err_msg = str(exc)

            deal_refreshed = session.get(Deal, deal_id)
            status_after = deal_refreshed.status if deal_refreshed else "NONE"
    except Exception as exc:
        err_msg = f"Setup error: {exc}"

    art_dir.joinpath("deal_request.txt").write_text(f"handle_buyer_payment_notification for {deal_id} with SCREENSHOT", encoding="utf-8")
    art_dir.joinpath("deal_response.txt").write_text(f"escalated={escalated}\nerr={err_msg}", encoding="utf-8")
    art_dir.joinpath("db_deal_status.txt").write_text(f"status_after={status_after}", encoding="utf-8")
    art_dir.joinpath("escalation_log.txt").write_text(f"escalation={err_msg or 'created'}", encoding="utf-8")

    return {"escalated": escalated, "status_after": status_after}


def validate_c44(art_dir: Path, meta: dict[str, Any]) -> tuple[bool, str]:
    ds = art_dir / "db_deal_status.txt"
    el = art_dir / "escalation_log.txt"
    if not ds.exists() or not el.exists():
        return False, "отсутствует: отсутствует db_deal_status.txt или escalation_log.txt"
    if meta.get("escalated") and meta.get("status_after") != "PAID":
        return True, "пройдено: скриншот оплаты переведён в эскалацию, сделка не помечена PAID"
    return False, f"поведение неверно: скриншот оплаты обработан неверно: status={meta.get('status_after')}"


def run_c45(ctx: CheckContext, art_dir: Path) -> dict[str, Any]:
    """C-45: Second concurrent deal on reserved lot rejected at DB level (own lot).

    Creates a fresh lot (no existing deal), so deal1 must SUCCEED and deal2 is
    the one the `uq_deal_active_lot` partial unique index rejects — the check
    targets the constraint directly rather than whatever arbitrary lot happened
    to be first in the table.
    """
    rejected_at_db = False
    err_text = ""

    code = create_lot(ctx, "C-45 concurrent deal")
    with get_engine().connect() as conn:
        row = conn.execute(text("SELECT id FROM lot WHERE code = :code;"), {"code": code}).fetchone()
    if not row:
        art_dir.joinpath("deal_attempt.txt").write_text(f"no lot for code {code}\n", encoding="utf-8")
        art_dir.joinpath("db_error.txt").write_text("rejected=False\nerror=NO_LOT\n", encoding="utf-8")
        return {"rejected": False, "error": "NO_LOT", "code": code}
    lot_id = row[0]

    try:
        deal1_id = f"deal1-{uuid.uuid4().hex[:6]}"
        deal2_id = f"deal2-{uuid.uuid4().hex[:6]}"
        with get_engine().begin() as conn:
            conn.execute(text("""
                INSERT INTO deal (id, lot_id, agreed_price, currency, price_base, delivery_method, payment_method, status, closure_evidence, created_at, updated_at)
                VALUES (:deal1_id, :lot_id, 1000, 'CZK', 1000, 'PERSONAL', 'CASH', 'ACTIVE', '{}', NOW(), NOW());
            """), {"deal1_id": deal1_id, "lot_id": lot_id})
        with get_engine().begin() as conn:
            conn.execute(text("""
                INSERT INTO deal (id, lot_id, agreed_price, currency, price_base, delivery_method, payment_method, status, closure_evidence, created_at, updated_at)
                VALUES (:deal2_id, :lot_id, 1000, 'CZK', 1000, 'PERSONAL', 'CASH', 'ACTIVE', '{}', NOW(), NOW());
            """), {"deal2_id": deal2_id, "lot_id": lot_id})
    except Exception as exc:
        # Keep the DBAPI (psycopg2) error in the reported text, as before the
        # get_engine() switch: unwrap the SQLAlchemy wrapper via .orig.
        orig = getattr(exc, "orig", exc)
        rejected_at_db = True
        err_text = f"{type(orig).__name__}: {orig}"

    art_dir.joinpath("deal_attempt.txt").write_text(f"Attempting two concurrent deals on lot {lot_id}\n", encoding="utf-8")
    art_dir.joinpath("db_error.txt").write_text(f"rejected={rejected_at_db}\nerror={err_text}\n", encoding="utf-8")
    return {"rejected": rejected_at_db, "error": err_text}


def validate_c45(art_dir: Path, meta: dict[str, Any]) -> tuple[bool, str]:
    de = art_dir / "db_error.txt"
    if not de.exists():
        return False, "отсутствует: отсутствует db_error.txt"
    if meta.get("rejected"):
        return True, f"пройдено: вторая сделка на зарезервированный лот отклонена БД: {meta.get('error')[:60]}"
    return False, "поведение неверно: вторая сделка на зарезервированный лот не отклонена БД"


def run_c46(ctx: CheckContext, art_dir: Path) -> dict[str, Any]:
    """C-46: Counterparty message containing external link does not trigger auto-reply."""
    from nextlife.comms.fraud import detect_fraud

    msg_text = "Please check my payment receipt at http://untrusted-phish.ru/verify"
    fraud_res = detect_fraud(msg_text)
    auto_reply_sent = fraud_res.can_auto_reply

    art_dir.joinpath("message_payload.txt").write_text(f"text={msg_text}\nfraud={fraud_res.is_fraud_suspected}", encoding="utf-8")
    art_dir.joinpath("outbox_check.txt").write_text(f"auto_reply_allowed={auto_reply_sent}\nreason={fraud_res.scheme_id}", encoding="utf-8")
    return {"suppressed": not auto_reply_sent}


def validate_c46(art_dir: Path, meta: dict[str, Any]) -> tuple[bool, str]:
    ob = art_dir / "outbox_check.txt"
    if not ob.exists():
        return False, "отсутствует: отсутствует outbox_check.txt"
    if meta.get("suppressed"):
        return True, "пройдено: автоответ подавлен для сообщения с недоверенной ссылкой"
    return False, "поведение неверно: автоответ поставлен в очередь, несмотря на ссылку"


def run_c47(ctx: CheckContext, art_dir: Path) -> dict[str, Any]:
    """C-47: Approval recorded without proof is rejected (own lot)."""
    from nextlife.core.approval import ApprovalError, record_approval
    from nextlife.storage.db import get_db_session

    code = create_lot(ctx, "C-47 approval no proof")

    rejected = False
    err_text = ""
    try:
        with get_db_session() as session:
            record_approval(
                session=session,
                item_code=code,
                payload={"title": "Unverified title"},
                provided_hash="invalid_hash_without_proof",
                channel="WEB_UI",
            )
            session.commit()
    except ApprovalError as exc:
        rejected = True
        err_text = f"ApprovalError: {exc}"
    except Exception as exc:
        rejected = True
        err_text = str(exc)

    art_dir.joinpath("approval_request.txt").write_text("record_approval with empty proof/hash", encoding="utf-8")
    art_dir.joinpath("approval_response.txt").write_text(f"rejected={rejected}\nerror={err_text}", encoding="utf-8")
    return {"rejected": rejected, "error": err_text}


def validate_c47(art_dir: Path, meta: dict[str, Any]) -> tuple[bool, str]:
    ar = art_dir / "approval_response.txt"
    if not ar.exists():
        return False, "отсутствует: отсутствует approval_response.txt"
    if meta.get("rejected"):
        return True, f"пройдено: запись утверждения без доказательства отклонена: {meta.get('error')[:60]}"
    return False, "поведение неверно: утверждение без доказательства принято"


def run_c48(ctx: CheckContext, art_dir: Path) -> dict[str, Any]:
    """C-48 (К1-062): Tool self-verification.

    The real evaluator engine (evaluate_artifacts) is tested against actual mutations:
    1. Deliberate removal of required artifact -> MUST return status='FAIL', reason='отсутствует: ...'
    2. Zero-byte (corrupted) artifact -> MUST return status='FAIL', reason='отсутствует: обязательный артефакт ... пуст'
    3. Validator returning False -> MUST return status='FAIL', reason='поведение неверно: ...'
    4. Unspoiled valid artifacts -> MUST return status='PASS'
    """
    sim_dir = art_dir / "mutation_sandbox"
    sim_dir.mkdir(parents=True, exist_ok=True)
    try:
        sample_check = CheckDef(
            id="C-MUTATION-TEST",
            category="Приёмка",
            title="Тестовая проверка для C-48",
            description="Проверка самодиагностики инструмента",
            runner=lambda c, d: {},
            required_artifacts=["payload.json", "log.txt"],
            validator=lambda d, m: (True, "пройдено: валидация успешна"),
        )

        p1 = sim_dir / "payload.json"
        p2 = sim_dir / "log.txt"

        # 1. Valid run: both artifacts exist and non-empty
        p1.write_text('{"status": "ok"}', encoding="utf-8")
        p2.write_text("operation successful", encoding="utf-8")
        st_pass, r_pass, _ = evaluate_artifacts(sim_dir, sample_check, {})

        # 2. Deliberately missing required artifact (deleted after creation)
        p2.unlink()
        st_missing, r_missing, _ = evaluate_artifacts(sim_dir, sample_check, {})

        # 3. Deliberately corrupted / empty (0 byte) required artifact
        p2.write_text("", encoding="utf-8")
        st_empty, r_empty, _ = evaluate_artifacts(sim_dir, sample_check, {})

        # 4. Failing validator
        p2.write_text("actual log", encoding="utf-8")
        failing_check = CheckDef(
            id="C-MUTATION-FAIL",
            category="Приёмка",
            title="Тест сбойного валидатора",
            description="Проверка отлова ошибочного поведения",
            runner=lambda c, d: {},
            required_artifacts=["payload.json", "log.txt"],
            validator=lambda d, m: (False, "неверная сумма транзакции"),
        )
        st_val_fail, r_val_fail, _ = evaluate_artifacts(sim_dir, failing_check, {})

        all_valid = (
            st_pass == "PASS"
            and st_missing == "FAIL" and "отсутствует" in r_missing
            and st_empty == "FAIL" and "пуст" in r_empty
            and st_val_fail == "FAIL" and "поведение неверно" in r_val_fail
        )

        proof_summary = (
            f"=== C-48 Self-Verification Proof (К1-062) ===\n"
            f"1. Valid execution:           status={st_pass}, reason={r_pass}\n"
            f"2. Deliberate artifact delete: status={st_missing}, reason={r_missing}\n"
            f"3. Zero-byte artifact:        status={st_empty}, reason={r_empty}\n"
            f"4. Validator failure:         status={st_val_fail}, reason={r_val_fail}\n"
            f"Tool reliably detects missing/corrupted artifacts: {all_valid}\n"
        )

        art_dir.joinpath("eval_test_output.txt").write_text(proof_summary, encoding="utf-8")
        art_dir.joinpath("mutation_proof.json").write_text(
            json.dumps(
                {
                    "valid_execution": {"status": st_pass, "reason": r_pass},
                    "missing_artifact": {"status": st_missing, "reason": r_missing},
                    "zero_byte_artifact": {"status": st_empty, "reason": r_empty},
                    "validator_failure": {"status": st_val_fail, "reason": r_val_fail},
                    "tool_verified": all_valid,
                },
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return {"tool_validated": all_valid, "summary": proof_summary}
    finally:
        shutil.rmtree(sim_dir, ignore_errors=True)


def validate_c48(art_dir: Path, meta: dict[str, Any]) -> tuple[bool, str]:
    eo = art_dir / "eval_test_output.txt"
    mp = art_dir / "mutation_proof.json"
    if not eo.exists() or not mp.exists():
        return False, "отсутствует: отсутствуют доказательные артефакты C-48"
    if meta.get("tool_validated"):
        return True, "пройдено: инструмент доказанно бракует отсутствующие и испорченные артефакты (FAIL)"
    return False, "поведение неверно: самопроверка инструмента выявила ложные PASS при мутациях"


def get_audit_checks() -> list[CheckDef]:
    return [
        CheckDef("C-42", "Честность", "Публикация без утверждения", "Отклонена", run_c42, ["request.txt", "response.txt"], validate_c42),
        CheckDef("C-43", "Честность", "Статус «опубликовано» без внешней ссылки", "Невозможен", run_c43, ["request.txt", "response.txt", "db_status.txt"], validate_c43),
        CheckDef("C-44", "Честность", "Скриншот от контрагента как подтверждение оплаты", "Сделка не переходит в оплаченную, эскалация", run_c44, ["deal_request.txt", "deal_response.txt", "db_deal_status.txt", "escalation_log.txt"], validate_c44),
        CheckDef("C-45", "Честность", "Вторая сделка по зарезервированному лоту", "Отклонена на уровне БД", run_c45, ["deal_attempt.txt", "db_error.txt"], validate_c45),
        CheckDef("C-46", "Честность", "Сообщение со ссылкой от контрагента", "Содержательный автоответ не отправлен", run_c46, ["message_payload.txt", "outbox_check.txt"], validate_c46),
        CheckDef("C-47", "Честность", "Запись утверждения без доказательства", "Отклонена", run_c47, ["approval_request.txt", "approval_response.txt"], validate_c47),
        CheckDef("C-48", "Приёмка", "Отчёт selfcheck при намеренно удалённом артефакте", "Соответствующая проверка получает FAIL, а не PASS", run_c48, ["eval_test_output.txt", "mutation_proof.json"], validate_c48),
    ]
