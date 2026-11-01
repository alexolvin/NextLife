"""Deal settlement and net seller payout calculation (TZ v2.0 Section III.8)."""

from decimal import Decimal

from pydantic import BaseModel, Field


class SettlementLine(BaseModel):
    name: str
    sign: str  # "+" or "−"
    amount: Decimal
    is_confirmed: bool = True
    note: str | None = None


class SettlementResult(BaseModel):
    price_gross: Decimal
    shipping_received: Decimal
    shipping_cost_actual: Decimal
    cod_fee_actual: Decimal
    packaging_cost: Decimal

    # Marketplace commission details
    marketplace_commission: Decimal | None = None
    marketplace_commission_confirmed: bool = True

    # Net payout calculation
    net_payout: Decimal | None = None
    net_payout_min: Decimal | None = None
    net_payout_max: Decimal | None = None

    is_fully_confirmed: bool = True
    lines: list[SettlementLine] = Field(default_factory=list)


def calculate_settlement(
    price_gross: Decimal,
    shipping_received: Decimal = Decimal("0.00"),
    shipping_cost_actual: Decimal = Decimal("0.00"),
    cod_fee_actual: Decimal = Decimal("0.00"),
    packaging_cost: Decimal = Decimal("0.00"),
    marketplace_commission_rate: Decimal | None = None,
    marketplace_commission_fixed: Decimal | None = None,
    commission_confirmed: bool = True,
    default_max_commission_rate: Decimal = Decimal("0.15"),
) -> SettlementResult:
    """Calculate net seller payout according to TZ Section III.8.

    Formula:
      + price_gross
      + shipping_received
      − marketplace_commission
      − shipping_cost_actual
      − cod_fee_actual
      − packaging_cost
      ─────────────────────────
      = net_payout (на руки)

    DoD: If commission is unknown/unconfirmed, it NEVER defaults to zero;
    instead it produces a range (min/max) clearly marked 'не подтверждено'.
    """
    p_gross = Decimal(str(price_gross))
    s_rec = Decimal(str(shipping_received))
    s_act = Decimal(str(shipping_cost_actual))
    cod_act = Decimal(str(cod_fee_actual))
    pkg_cost = Decimal(str(packaging_cost))

    base_funds = p_gross + s_rec - s_act - cod_act - pkg_cost

    lines: list[SettlementLine] = [
        SettlementLine(name="Цена предмета", sign="+", amount=p_gross, is_confirmed=True),
    ]
    if s_rec > 0:
        lines.append(SettlementLine(name="Доставка, полученная с покупателя", sign="+", amount=s_rec, is_confirmed=True))

    if commission_confirmed and (marketplace_commission_rate is not None or marketplace_commission_fixed is not None):
        comm_pct = Decimal(str(marketplace_commission_rate or 0))
        comm_fix = Decimal(str(marketplace_commission_fixed or 0))
        commission_amount = (p_gross * comm_pct) + comm_fix
        net_payout = base_funds - commission_amount

        lines.append(
            SettlementLine(
                name="Комиссия площадки",
                sign="−",
                amount=commission_amount,
                is_confirmed=True,
            )
        )
        payout_min = None
        payout_max = None
        fully_confirmed = True
    else:
        # Commission is unknown or unconfirmed
        # DoD: Never substitute with 0. Return a range with explicit 'не подтверждено' note.
        commission_amount = None
        net_payout = None
        fully_confirmed = False

        # Worst case: upper bound commission (e.g. 15%)
        max_possible_comm = p_gross * default_max_commission_rate
        payout_min = base_funds - max_possible_comm
        payout_max = base_funds  # 0% commission best case

        lines.append(
            SettlementLine(
                name="Комиссия площадки",
                sign="−",
                amount=Decimal("0.00"),
                is_confirmed=False,
                note="не подтверждено",
            )
        )

    if s_act > 0:
        lines.append(SettlementLine(name="Фактическая стоимость доставки", sign="−", amount=s_act, is_confirmed=True))
    if cod_act > 0:
        lines.append(SettlementLine(name="Комиссия dobírka (наложенный платёж)", sign="−", amount=cod_act, is_confirmed=True))
    if pkg_cost > 0:
        lines.append(SettlementLine(name="Упаковка", sign="−", amount=pkg_cost, is_confirmed=True))

    return SettlementResult(
        price_gross=p_gross,
        shipping_received=s_rec,
        shipping_cost_actual=s_act,
        cod_fee_actual=cod_act,
        packaging_cost=pkg_cost,
        marketplace_commission=commission_amount,
        marketplace_commission_confirmed=commission_confirmed and fully_confirmed,
        net_payout=net_payout,
        net_payout_min=payout_min,
        net_payout_max=payout_max,
        is_fully_confirmed=fully_confirmed,
        lines=lines,
    )
