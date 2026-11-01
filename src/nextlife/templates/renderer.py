"""Centralized Jinja2 template rendering with strict internal field leakage filtering (TZ v2.0 Section V.1, VI.1)."""

from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from nextlife.comms.outgoing_filter import (
    FORBIDDEN_INTERNAL_FIELDS,
    InternalFieldLeakageError,
    check_context_for_internal_fields,
)

TEMPLATES_ROOT = Path(__file__).resolve().parent.parent.parent.parent / "templates"


def get_jinja_env(subpath: str = "") -> Environment:
    """Create Jinja2 Environment pointing to templates directory."""
    target_dir = (TEMPLATES_ROOT / subpath) if subpath else TEMPLATES_ROOT
    return Environment(
        loader=FileSystemLoader(target_dir),
        autoescape=select_autoescape(["html", "xml"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )


def render_template(template_name: str, context: dict[str, Any], subpath: str = "listing") -> str:
    """Render Jinja2 template while strictly validating against internal field leakage.

    DoD:
    - Internal fields (such as price_floor, home_address) are rejected.
    - All templates render through this centralized renderer.
    """
    # 1. Enforce strict filter on context
    check_context_for_internal_fields(context)

    # 2. Render template
    env = get_jinja_env(subpath)
    tmpl = env.get_template(template_name)
    rendered = tmpl.render(context)

    # 3. Double-check rendered text for accidental leaks
    for forbidden in FORBIDDEN_INTERNAL_FIELDS:
        if forbidden in rendered:
            raise InternalFieldLeakageError(
                f"Forbidden internal field '{forbidden}' detected in rendered output of {template_name}"
            )

    return rendered
