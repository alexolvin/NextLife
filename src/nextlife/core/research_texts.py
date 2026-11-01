"""Deterministic Czech listing-text variants from market research.

TZ-NextLife-UI-N2 §4 V5 (P-R08): на основе найденных аналогов — 2-3 варианта
чешского текста, редактируемые. Варианты строятся из названия предмета и
рыночного диапазона цен по аналогам; без единого аналога вариантов нет
(честность: ни диапазон, ни текст без источников).
"""

from decimal import Decimal


def _czech_noun(count: int) -> str:
    """Склонение «подробный аналог» по числу: 1 / 2-4 / 5+."""
    mod10, mod100 = count % 10, count % 100
    if mod10 == 1 and mod100 != 11:
        return "1 podobný inzerát"
    if 2 <= mod10 <= 4 and not 12 <= mod100 <= 14:
        return f"{count} podobné inzeráty"
    return f"{count} podobných inzerátů"


def generate_research_texts(
    title: str,
    count: int,
    min_price: Decimal | None,
    max_price: Decimal | None,
    currency: str = "CZK",
) -> list[str]:
    """2-3 чешских варианта текста объявления.

    ``count`` — число найденных аналогов (0 -> пустой список);
    ``min_price``/``max_price`` — рыночный диапазон (None, если выборка
    меньше порога — вариант строится без цен).
    """
    if count < 1 or not title:
        return []

    from nextlife.money.money import format_money

    noun = _czech_noun(count)
    has_range = (
        min_price is not None and max_price is not None and min_price <= max_price
    )
    min_s = format_money(min_price, currency) if has_range else ""
    max_s = format_money(max_price, currency) if has_range else ""

    if has_range:
        return [
            (
                f"Prodám {title}. Na srovnání: {noun}, jejich ceny se pohybují "
                f"mezi {min_s} a {max_s}. Stav odpovídá popisu. Osobní odběr i "
                "odeslání po poště. Na dotazy odpovím co nejdříve."
            ),
            (
                f"{title} — k prodeji. {noun} v nabídce, trhová cena {min_s} až "
                f"{max_s}. Rychlé vyřízení, osobní odběr i odeslání. Dotazy vítány."
            ),
            (
                f"Prodám {title}. Pro srovnání: {noun}, ceny {min_s} až {max_s}. "
                "Zařízení je ve stavu, jak je popsáno. Odeslání zajišťuji. "
                "V případě dotazů napište."
            ),
        ]
    return [
        (
            f"Prodám {title}. Na srovnání: {noun} v nabídce. Stav odpovídá popisu. "
            "Osobní odběr i odeslání po poště. Na dotazy odpovím co nejdříve."
        ),
        (
            f"{title} — k prodeji. {noun} v nabídce. Rychlé vyřízení, osobní "
            "odběr i odeslání. Dotazy vítány."
        ),
        (
            f"Prodám {title}. Pro srovnání: {noun}. Zařízení je ve stavu, "
            "jak je popsáno. Odeslání zajišťuji. V případě dotazů napište."
        ),
    ]
