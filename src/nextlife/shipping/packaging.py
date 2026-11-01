"""Packaging materials, cost calculations, and dispatch checklists (TZ v2.0 Section III.2.4)."""

from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

DEFAULT_PACKAGING_CONFIG_PATH = (
    Path(__file__).resolve().parent.parent.parent.parent / "config" / "packaging.yaml"
)


class PackagingType(BaseModel):
    type_id: str
    display_name: str
    weight_add_g: int
    dim_add_l_mm: int
    dim_add_w_mm: int
    dim_add_h_mm: int
    cost_czk: Decimal = Field(default=Decimal("0.0"))


def load_packaging_types(config_path: Path | str | None = None) -> dict[str, PackagingType]:
    """Load packaging types dictionary from config/packaging.yaml."""
    cfg_file = Path(config_path) if config_path else DEFAULT_PACKAGING_CONFIG_PATH
    if not cfg_file.exists():
        return {}

    try:
        raw = yaml.safe_load(cfg_file.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}

    types_dict = raw.get("packaging_types", {})
    result = {}
    for tid, data in types_dict.items():
        if isinstance(data, dict):
            result[tid] = PackagingType(
                type_id=tid,
                display_name=data.get("display_name", tid),
                weight_add_g=int(data.get("weight_add_g", 0)),
                dim_add_l_mm=int(data.get("dim_add_l_mm", 0)),
                dim_add_w_mm=int(data.get("dim_add_w_mm", 0)),
                dim_add_h_mm=int(data.get("dim_add_h_mm", 0)),
                cost_czk=Decimal(str(data.get("cost_czk", 0.0))),
            )
    return result


def calculate_packaging(
    raw_weight_g: int,
    raw_l_mm: int,
    raw_w_mm: int,
    raw_h_mm: int,
    packaging_type: str = "box_small",
    config_path: Path | str | None = None,
) -> dict[str, Any]:
    """Calculate dimensions, weight and cost after adding packaging."""
    types_map = load_packaging_types(config_path)
    pkg = types_map.get(
        packaging_type,
        PackagingType(
            type_id=packaging_type,
            display_name=packaging_type,
            weight_add_g=150,
            dim_add_l_mm=30,
            dim_add_w_mm=30,
            dim_add_h_mm=30,
            cost_czk=Decimal("30.00"),
        ),
    )

    packed_weight_g = raw_weight_g + pkg.weight_add_g
    packed_l_mm = raw_l_mm + pkg.dim_add_l_mm
    packed_w_mm = raw_w_mm + pkg.dim_add_w_mm
    packed_h_mm = raw_h_mm + pkg.dim_add_h_mm

    return {
        "packaging_type": pkg.type_id,
        "display_name": pkg.display_name,
        "packed_weight_g": packed_weight_g,
        "packed_l_mm": packed_l_mm,
        "packed_w_mm": packed_w_mm,
        "packed_h_mm": packed_h_mm,
        "packaging_cost_czk": pkg.cost_czk,
    }


def get_packaging_checklist(category_key: str, fragile: bool = False) -> list[str]:
    """Generate dispatch packaging checklist based on category and fragile requirement."""
    checklist: list[str] = [
        "Подобрать прочную коробку соответствующего размера без внешних деформаций",
    ]

    cat_norm = category_key.lower()

    if "gpu" in cat_norm or "hardware" in cat_norm or "electronics" in cat_norm:
        checklist.append("Поместить плату/компонент в антистатический пакет (ESD-пакет)")
        checklist.append("Установить защитные заглушки на разъёмы (PCIe, видеовыходы, контакты)")
        checklist.append("Использовать оригинальный ложемент или плотный поролон для фиксации")

    if fragile:
        checklist.append("Обернуть предмет воздушно-пузырчатой плёнкой (минимум 3 слоя)")
        checklist.append("Заполнить пустоты наполнителем (пенопласт/крафт), чтобы предмет не двигался при встряхивании")
        checklist.append("Нанести яркую маркировку 'KŘEHKÉ / FRAGILE / ХРУПКОЕ' на две стороны коробки")
    else:
        checklist.append("Заполнить внутренние пустоты крафт-бумагой или амортизирующим материалом")

    checklist.extend([
        "Сделать чёткое фото содержимого коробки перед запечатыванием (доказательство отправки)",
        "Проклеить швы коробки широким скотчем по схеме 'H'",
        "Наклеить транспортировочный ярлык со штрих-кодом на плоскую поверхность коробки",
    ])

    return checklist
