"""Buyer checklist and seller inquiry questions service (TZ v2.0 Section III.5.1 / Task T-072).

DoD:
- Generates questions for the seller and on-site inspection checklist by category.
- For GPU: produces the exact same 9 inspection protocol points as used for selling.
- Checked by outgoing filter.
- Module length <= 400 lines.
"""

from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from nextlife.comms.outgoing_filter import (
    check_context_for_internal_fields,
    render_outgoing_template,
)
from nextlife.core.exceptions import NextLifeError
from nextlife.services.inspection import (
    get_checklist_items,
    normalize_category_key,
)
from nextlife.storage.models import BuyCandidate, BuyRequest

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
TEMPLATE_PATH = BASE_DIR / "templates" / "comms" / "buyer_questions.jinja2"

CATEGORY_QUESTIONS = {
    "gpu": {
        "cs": [
            "Byla grafická karta používána k těžbě kryptoměn (mining)?",
            "Jsou na kartě neporušené tovární plomby, nebo byla otevírána / přepastována?",
            "Vydávají ventilátory při vyšších otáčkách nežádoucí zvuky či pískání cívek (coil whine)?",
            "Máte k dispozici originální balení a původní doklad o koupi / záruční list?",
            "Je možné kartu před převzetím otestovat v zátěži (FurMark / 3DMark)?",
        ],
        "ru": [
            "Использовалась ли видеокарта для майнинга криптовалют?",
            "Целы ли заводские пломбы, вскрывалась ли карта / менялась ли термопаста?",
            "Издают ли вентиляторы посторонние звуки или свист дросселей под нагрузкой?",
            "Есть ли в наличии оригинальная коробка и чек / гарантийный талон?",
            "Возможно ли проверить карту под нагрузкой (FurMark / 3DMark) перед покупкой?",
        ],
    },
    "default": {
        "cs": [
            "Je zařízení plně funkční bez jakýchkoli skrytých závad?",
            "Má předmět nějaké viditelné škrábance, praskliny nebo stopy po pádu?",
            "Máte k dispozici původní doklad o koupi a kompletní příslušenství?",
            "Bylo zařízení někdy opravováno v neautorizovaném servisu?",
        ],
        "ru": [
            "Полностью ли устройство исправно, нет ли скрытых дефектов?",
            "Имеются ли сколы, трещины, глубокие царапины или следы падений?",
            "Сохранился ли оригинальный чек о покупке и комплект аксессуаров?",
            "Сдавалось ли устройство в ремонт / вскрывалось ли?",
        ],
    },
}


class BuyerChecklistError(NextLifeError):
    """Raised when buyer checklist or inquiry generation fails."""


def get_seller_questions(category: str | None = None, lang: str = "cs") -> list[str]:
    """Retrieve tailored verification questions to ask seller before purchase."""
    cat_key = normalize_category_key(category)
    data = CATEGORY_QUESTIONS.get(cat_key) or CATEGORY_QUESTIONS["default"]
    lang_key = "cs" if lang == "cs" else "ru"
    return list(data.get(lang_key) or data["cs"])


def get_buyer_inspection_protocol(category: str | None = None, lang: str = "cs") -> list[dict[str, str]]:
    """Retrieve the category inspection protocol for the buyer.

    DoD:
    - For GPU, returns the exact same 9 points as specified in Section III.5.1.
    """
    return get_checklist_items(category=category, lang=lang)


def render_buyer_inquiry_message(
    query: str,
    category: str | None = None,
    lang: str = "cs",
    custom_questions: list[str] | None = None,
) -> str:
    """Render seller contact inquiry message with verification questions."""
    questions = custom_questions or get_seller_questions(category=category, lang=lang)

    context = {
        "query": query,
        "lang": lang,
        "questions": questions,
    }
    check_context_for_internal_fields(context)

    if not TEMPLATE_PATH.exists():
        raise BuyerChecklistError(f"Buyer questions template missing: {TEMPLATE_PATH}")

    template_str = TEMPLATE_PATH.read_text(encoding="utf-8")
    return render_outgoing_template(template_str, context)


def generate_buyer_dossier(candidate_id: str, session: Session, lang: str = "cs") -> dict[str, Any]:
    """Assemble complete buyer checklist bundle for a candidate listing."""
    cand = session.get(BuyCandidate, candidate_id)
    if not cand:
        raise BuyerChecklistError(f"BuyCandidate '{candidate_id}' not found")

    req = session.get(BuyRequest, cand.request_id) if cand.request_id else None
    query = req.query if req else "Poptávané zboží"

    category = req.attrs.get("category") if req and req.attrs else None

    questions = get_seller_questions(category=category, lang=lang)
    inspection_points = get_buyer_inspection_protocol(category=category, lang=lang)
    inquiry_msg = render_buyer_inquiry_message(query=query, category=category, lang=lang, custom_questions=questions)

    return {
        "candidate_id": cand.id,
        "query": query,
        "category": category or "default",
        "questions_count": len(questions),
        "questions": questions,
        "inspection_protocol_count": len(inspection_points),
        "inspection_protocol": inspection_points,
        "inquiry_message": inquiry_msg,
    }
