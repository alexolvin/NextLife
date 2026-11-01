"""SecretRef resolver for NextLife.

Resolution order (first hit wins):
  1. the process environment (systemd EnvironmentFile in production,
     explicit export in development and tests);
  2. the project-root ``.env`` file.

The ``.env`` file is loaded ONLY here — it is the single .env-loading site in
the codebase (no other mechanism reads it). The process environment always
takes precedence over ``.env``, so a systemd EnvironmentFile in prod overrides
``.env``.
"""

import os
from pathlib import Path

from nextlife.core.exceptions import SecretResolutionError

# The single .env file NextLife reads: the project-root .env. This module lives
# at <root>/src/nextlife/core/secrets.py, so the repository root is four levels
# up from the file.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_ENV_FILE = _PROJECT_ROOT / ".env"

_SECRET_REF_PREFIXES = ("SecretRef:", "env:")


def _parse_env_file(path: Path) -> dict[str, str]:
    """Parse a dotenv-style file into a mapping without touching os.environ.

    Supports ``KEY=value`` lines, optional surrounding single/double quotes,
    an optional leading ``export ``, ``#`` comment lines and blank lines. A
    missing file, or a file that cannot be read (e.g. ``PermissionError`` when a
    different user such as the openclaw CLI reads this user's ``.env``), yields an
    empty mapping -- i.e. the variable is treated as absent, with no traceback;
    the caller then raises ``SecretResolutionError`` naming the missing variable.
    """
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        # Unreadable .env == "no variables here" (no traceback); the caller raises
        # SecretResolutionError naming the variable. This is what keeps a
        # cross-user CLI (openclaw) from leaking this user's .env.
        return values
    for raw in content.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if key:
            values[key] = value
    return values


def resolve_secret(secret_ref: str) -> str:
    """Resolve a SecretRef to its actual value.

    Lookup order (first hit wins): the process environment, then the
    project-root ``.env`` file (see module docstring for the precedence rule).

    Example formats:
        'NEXTLIFE_DSN' -> os.environ['NEXTLIFE_DSN']
        'SecretRef:NEXTLIFE_DSN' -> os.environ['NEXTLIFE_DSN']
        'env:NEXTLIFE_DSN' -> os.environ['NEXTLIFE_DSN']
    """
    clean_ref = secret_ref.strip()
    for prefix in _SECRET_REF_PREFIXES:
        if clean_ref.startswith(prefix):
            clean_ref = clean_ref[len(prefix) :]
            break

    if not clean_ref:
        return ""

    val = os.environ.get(clean_ref)
    if val is not None:
        return val

    val = _parse_env_file(_ENV_FILE).get(clean_ref)
    if val is None:
        raise SecretResolutionError(
            f"Failed to resolve secret reference '{secret_ref}': variable '{clean_ref}' "
            "is not set in the process environment or in .env."
        )
    return val
