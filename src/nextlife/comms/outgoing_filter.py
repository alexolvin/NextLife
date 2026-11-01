"""Outgoing communication filter for internal field leakage prevention (TZ v2.0 III.1, VI.1).

Enforces strict isolation of internal fields (such as price_floor, serial_number, private credentials)
from ever appearing in outgoing messages, templates, or marketplace listings.
"""

import re
from typing import Any

from jinja2 import Environment, meta

from nextlife.core.exceptions import NextLifeError


class InternalFieldLeakageError(NextLifeError):
    """Raised when an outgoing template or message attempts to access or leak an internal field."""


FORBIDDEN_INTERNAL_FIELDS = {
    "price_floor",
    "home_address",
    "owner_address",
    "owner_phone",
    "internal_notes",
    "raw_cost",
}


def is_internal_field(key: str, val: Any = None, field_info: Any = None) -> bool:
    """Check if a field or value is marked internal."""
    key_lower = key.lower()
    if key_lower in FORBIDDEN_INTERNAL_FIELDS:
        return True
    if hasattr(val, "internal") and getattr(val, "internal", False) is True:
        return True
    if isinstance(val, dict) and val.get("internal") is True:
        return True
    if field_info is not None:
        if getattr(field_info, "json_schema_extra", None):
            extra = field_info.json_schema_extra
            if isinstance(extra, dict) and extra.get("internal") is True:
                return True
        if getattr(field_info, "internal", False) is True:
            return True
    return False


def serialize_for_outgoing(obj: Any) -> Any:
    """Serialize model, dataclass, or dictionary for outgoing communication.

    Guarantees all fields marked internal=True or in FORBIDDEN_INTERNAL_FIELDS
    are strictly stripped at serialization time.
    """
    if obj is None:
        return None

    # Pydantic models
    if hasattr(obj, "model_dump") and hasattr(obj.__class__, "model_fields"):
        out: dict[str, Any] = {}
        for fname, finfo in obj.__class__.model_fields.items():
            if is_internal_field(fname, field_info=finfo):
                continue
            val = getattr(obj, fname)
            if is_internal_field(fname, val):
                continue
            out[fname] = serialize_for_outgoing(val)
        return out

    # SQLAlchemy models
    if hasattr(obj, "__table__"):
        out = {}
        for col in obj.__table__.columns:
            if is_internal_field(col.name):
                continue
            val = getattr(obj, col.name, None)
            if is_internal_field(col.name, val):
                continue
            out[col.name] = serialize_for_outgoing(val)
        return out

    # Dictionaries
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if is_internal_field(str(k), v):
                continue
            out[k] = serialize_for_outgoing(v)
        return out

    # Lists
    if isinstance(obj, (list, tuple, set)):
        return [serialize_for_outgoing(item) for item in obj]

    return obj


def check_context_for_internal_fields(context: dict[str, Any], path: str = "") -> None:
    """Recursively inspect context to detect values marked internal=True or forbidden internal keys."""
    for key, val in context.items():
        full_key = f"{path}.{key}" if path else str(key)

        if is_internal_field(str(key), val):
            raise InternalFieldLeakageError(
                f"Attempt to leak internal field '{full_key}' in outgoing context."
            )

        if isinstance(val, dict):
            check_context_for_internal_fields(val, full_key)
        elif hasattr(val, "model_dump"):
            check_context_for_internal_fields(val.model_dump(), full_key)
        elif hasattr(val, "__table__"):
            cols = {c.name: getattr(val, c.name, None) for c in val.__table__.columns}
            check_context_for_internal_fields(cols, full_key)


def check_text_for_blocked_values(text: str, blocked_values: list[Any]) -> None:
    """Check text against numeric blocked values regardless of formatting (e.g. 12 500, 12500.00)."""
    from decimal import Decimal

    for b in blocked_values:
        if b is None:
            continue
        try:
            val_dec = Decimal(str(b))
        except Exception:
            continue

        val_int = int(val_dec)
        int_str = str(val_int)
        spaced_str = f"{val_int:,}".replace(",", " ")
        comma_str = f"{val_int:,}"
        dot_str = f"{val_int:,}".replace(",", ".")
        dec_dot = f"{val_dec:.2f}"
        dec_comma = f"{val_dec:.2f}".replace(".", ",")

        candidates = [int_str, spaced_str, comma_str, dot_str, dec_dot, dec_comma]
        for c in candidates:
            pattern = re.compile(rf"(?<!\d){re.escape(c)}(?!\d)")
            if pattern.search(text):
                raise InternalFieldLeakageError(
                    f"Blocked internal value '{b}' (formatted as '{c}') detected in outgoing text."
                )


def render_outgoing_template(
    template_str: str,
    context: dict[str, Any],
    blocked_values: list[Any] | None = None,
) -> str:
    """Render outgoing template using Jinja2 with strict validation against internal fields.

    Raises:
        InternalFieldLeakageError: if any forbidden field is referenced in template or passed in context.
    """
    # 1. Inspect the template AST for referenced variables
    env = Environment(autoescape=False)
    parsed_content = env.parse(template_str)
    undeclared_variables = meta.find_undeclared_variables(parsed_content)

    for var in undeclared_variables:
        if is_internal_field(var):
            raise InternalFieldLeakageError(
                f"Forbidden internal field '{var}' referenced in outgoing template."
            )

    # Also check via regex for manual substitutions like {price_floor} or $price_floor
    pattern = re.compile(r'[{%$]\s*([a-zA-Z0-9_]+)')
    for match in pattern.finditer(template_str):
        var_name = match.group(1)
        if is_internal_field(var_name):
            raise InternalFieldLeakageError(
                f"Forbidden internal field '{var_name}' detected in template syntax."
            )

    # 2. Check context values
    check_context_for_internal_fields(context)

    # 3. Render
    template = env.from_string(template_str)
    rendered = template.render(context)

    # 4. Final safety check on rendered output for keywords
    for forbidden in FORBIDDEN_INTERNAL_FIELDS:
        if forbidden in rendered:
            raise InternalFieldLeakageError(
                f"Forbidden keyword '{forbidden}' leaked into rendered outgoing text."
            )

    # 5. Check formatted numeric leakages
    if blocked_values:
        check_text_for_blocked_values(rendered, blocked_values)

    return rendered
