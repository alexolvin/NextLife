"""Types and specifications for NextLife selfcheck tool (Task K1, Phase F2)."""

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class CheckContext:
    base_url: str
    test_dsn: str
    auth_token: str
    out_dir: Path
    media_dir: Path
    temp_dir: Path
    oc_token: str = ""


@dataclass
class CheckDef:
    id: str
    category: str
    title: str
    description: str
    runner: Callable[[CheckContext, Path], dict[str, Any] | None]
    required_artifacts: list[str] = field(default_factory=list)
    validator: Callable[[Path, dict[str, Any]], tuple[bool, str]] | None = None


@dataclass
class CheckResult:
    id: str
    category: str
    title: str
    status: str  # PASS, FAIL, BLOCKED
    reason: str
    artifacts: list[str]


def evaluate_artifacts(art_dir: Path, check: CheckDef, meta: dict[str, Any]) -> tuple[str, str, list[str]]:
    """Determine status strictly from generated artifacts with 3-way categorization (K1-053)."""
    artifacts = sorted([p.name for p in art_dir.iterdir() if p.is_file()])

    # Check for explicit blocked state
    blocked_file = art_dir / "blocked_reason.txt"
    if blocked_file.exists() and blocked_file.stat().st_size > 0:
        return "BLOCKED", blocked_file.read_text(encoding="utf-8").strip(), artifacts

    # Verify all required artifacts exist and are non-empty
    for req in check.required_artifacts:
        req_path = art_dir / req
        if not req_path.exists():
            return "FAIL", f"отсутствует: обязательный артефакт '{req}' не найден", artifacts
        if req_path.stat().st_size == 0 and req != "dom_element.txt":
            return "FAIL", f"отсутствует: обязательный артефакт '{req}' пуст (0 байт)", artifacts

    # Run custom validator if provided
    if check.validator is not None:
        try:
            ok, reason = check.validator(art_dir, meta)
            if ok:
                if not reason.startswith("пройдено:") and not reason.startswith("PASS"):
                    reason = f"пройдено: {reason}"
                return "PASS", reason, artifacts
            else:
                # Classify failure: отсутствует vs поведение неверно (К1-053)
                if not (reason.startswith("отсутствует:") or reason.startswith("поведение неверно:")):
                    if any(k in reason.lower() for k in ("404", "405", "not found", "не найден", "отсутств")):
                        reason = f"отсутствует: {reason}"
                    else:
                        reason = f"поведение неверно: {reason}"
                return "FAIL", reason, artifacts
        except Exception as exc:
            return "FAIL", f"поведение неверно: ошибка валидации артефактов: {exc}", artifacts

    return "PASS", "пройдено: все обязательные артефакты присутствуют", artifacts

