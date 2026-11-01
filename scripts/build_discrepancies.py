#!/usr/bin/env python3
"""Build discrepancy registry between claimed functions and selfcheck results (Phase F3/P5).

Rule K-1: Module must strictly be <= 400 lines.
"""

from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent.parent
CLAIMED_FILE = ROOT_DIR / "correction" / "F0" / "claimed.md"
SELFCHECK_JSON = ROOT_DIR / "correction" / "selfcheck" / "selfcheck.json"
OUT_FILE = ROOT_DIR / "correction" / "DISCREPANCIES.md"


def is_requirement(text: str) -> bool:
    """Check if line is a legitimate requirement instead of log/command/test output."""
    t = text.strip()
    if not t or t in ["{", "}", "[", "]", "},", "],", ",", "```", "```bash", "```json"]:
        return False
    if (t.startswith("{") and t.endswith("}")) or (t.startswith("[") and t.endswith("]")):
        return False
    if re.match(r"^\"[a-zA-Z0-9_\-\.]+\":\s*", t) or re.match(r"^[0-9]+(\.[0-9]+)?\s*s$", t):
        return False
    if t.startswith(("$ ", "`python3 ", "`pytest", "python3 ", "`ruff", "`mypy", "`jscpd")):
        return False
    if t.endswith((" →", " ->")):
        return False
    if any(k in t for k in [
        "passed in ", "failed in ", "PASSED", "FAILED", "SKIPPED",
        "All checks passed", "Success: no issues", "Found 0 clones",
        "Hardcode check passed", "No duplicates found", "passed, 2 warnings"
    ]):
        return False
    if re.search(r"\b[0-9]+\s+passed\b", t) or re.search(r"\[\s*[0-9]+%\]", t):
        return False
    if "::" in t and any(k in t for k in ("test_", "PASSED", "FAILED")):
        return False
    if t.startswith("=") or re.match(r"^(INFO|DEBUG|WARNING|ERROR|CRITICAL)\s+", t) or "[alembic" in t:
        return False
    if ("Clean schema" in t and "created" in t) or "Alembic upgrade" in t:
        return False
    if t.startswith(("ПРОГНАНО:", "ГЕЙТЫ", "ТЕКУЩИЙ СТАТУС", "ОТКЛОНЕНИЯ", "ВОПРОСЫ")):
        return False
    return True


def filter_requirements(lines: list[str]) -> list[str]:
    """Filter input lines, returning only genuine requirements and zero logs."""
    return [line for line in lines if is_requirement(line)]


def _load_selfcheck() -> dict[str, dict[str, Any]]:
    data = json.loads(SELFCHECK_JSON.read_text(encoding="utf-8"))
    return {c["id"]: c for c in data["checks"]}


def _clean_table_cell(text: str) -> str:
    cleaned = text.replace("|", "\\|").replace("\n", " ")
    return " ".join(cleaned.split())


def _check_external_blockers(num: int, s_lower: str, c_lower: str) -> str:
    """Return BLOCKED-EXTERNAL reason if requirement depends on unavailable external systems."""
    if "t-034" in s_lower or "коннектор bazoš" in c_lower:
        return "BLOCKED-EXTERNAL: Bazoš.cz (требуется чешский номер для SMS и чистый IP)"
    if "t-035" in s_lower or "коннектор sbazar" in c_lower:
        return "BLOCKED-EXTERNAL: Sbazar.cz (требуется аккаунт Seznam.cz и live API)"
    if "t-036" in s_lower or "коннектор aukro" in c_lower:
        return "BLOCKED-EXTERNAL: Aukro.cz (требуются боевые OAuth 2.0 API токены)"
    if num == 17 or ("э3 коннекторы" in s_lower and "bazos" in c_lower) or "задача 19.2 э3" in s_lower:
        return "BLOCKED-EXTERNAL: Bazoš/Sbazar/Aukro (требуются боевые аккаунты площадок и чешский номер)"
    if "execute_buy_search" in c_lower:
        return "BLOCKED-EXTERNAL: Маркетплейсы (требуется внешний опрос чешских площадок)"
    if "android" in c_lower or "adb" in c_lower or "задача 7" in s_lower:
        return "BLOCKED-EXTERNAL: Android ADB (требуется физическое Android-устройство, подключенное по ADB)"
    if "приём писем openclaw" in s_lower or "inbox push" in c_lower or num in (61, 91):
        return "BLOCKED-EXTERNAL: OpenClaw Mail (требуется шлюз OpenClaw и доступ к почтовому серверу)"
    if "refresh_fx_rates" in c_lower or "свежести курсов" in c_lower or ("19.2 э2" in s_lower and "деньги" in s_lower):
        return "BLOCKED-EXTERNAL: ECB FX Feed (требуется внешний доступ к фиду котировок ЕЦБ)"
    if "vlm" in c_lower or "vlm" in s_lower:
        return "BLOCKED-EXTERNAL: VLM Service (требуется доступный эндпоинт VLM модели)"
    return ""


DIRECT_CHECK_MAP: dict[int, str] = {
    2: "C-01", 55: "C-02", 68: "C-03", 6: "C-04", 4: "C-05", 8: "C-06", 7: "C-07",
    42: "C-08", 151: "C-09", 152: "C-10", 11: "C-11", 153: "C-12", 43: "C-13",
    128: "C-14", 129: "C-15", 130: "C-16", 131: "C-17", 132: "C-18", 133: "C-19",
    135: "C-20", 136: "C-21", 137: "C-22", 138: "C-23", 139: "C-24", 20: "C-25",
    21: "C-26", 154: "C-27", 155: "C-28", 156: "C-29", 184: "C-30", 157: "C-31",
    22: "C-32", 142: "C-33", 26: "C-34", 141: "C-35", 147: "C-36", 127: "C-37",
    121: "C-38", 115: "C-39", 117: "C-40", 183: "C-41", 28: "C-42", 30: "C-43",
    169: "C-44", 158: "C-45", 162: "C-46", 10: "C-47", 186: "C-48",
}


def _map_entry(num: int, source: str, claim: str) -> tuple[str, list[str], str]:
    """Map claimed item to verification sources or mark as BLOCKED-EXTERNAL."""
    s_lower, c_lower = source.lower(), claim.lower()

    # 1. External blockers (16 items)
    blk_reason = _check_external_blockers(num, s_lower, c_lower)
    if blk_reason:
        return source, [], blk_reason

    # 2. Strict 1-to-1 selfcheck behavioral checks (48 items)
    if num in DIRECT_CHECK_MAP:
        cid = DIRECT_CHECK_MAP[num]
        return f"{source} ({cid})", [cid], ""

    # 3. All remaining items lack a dedicated behavioral check in selfcheck
    return source, [], ""


def build_discrepancies() -> None:
    checks = _load_selfcheck()
    raw_content = CLAIMED_FILE.read_text(encoding="utf-8")

    rows: list[tuple[int, str, str]] = []
    for line in raw_content.splitlines():
        line = line.strip()
        if not line.startswith("|") or line.startswith(("| ---", "| №")):
            continue
        parts = [p.strip() for p in line.split("|")[1:-1]]
        if len(parts) >= 3 and is_requirement(parts[2]):
            rows.append((int(parts[0]), parts[1], parts[2]))

    table_rows: list[dict[str, str]] = []
    blocked_items: list[dict[str, str]] = []
    blind_spots: list[dict[str, str]] = []

    for num, source, claim in rows:
        func_title, check_ids, blocked_reason = _map_entry(num, source, claim)
        if blocked_reason:
            selfcheck_res, checks_str, verdict = blocked_reason, "BLOCKED-EXTERNAL", "BLOCKED-EXTERNAL"
            blocked_items.append({"num": str(num), "source": source, "claim": claim, "reason": blocked_reason})
        elif not check_ids:
            selfcheck_res, checks_str, verdict = "Нет выделенной проверки в selfcheck", "—", "не работает"
            blind_spots.append({"num": str(num), "source": source, "claim": claim})
        else:
            checks_str = ", ".join(check_ids)
            failing = [c_id for c_id in check_ids if checks.get(c_id, {}).get("status") == "FAIL"]
            if failing:
                fail_details = "; ".join(f"{c_id}: {checks[c_id]['reason'][:40]}" for c_id in failing)
                selfcheck_res, verdict = f"FAIL ({fail_details})", "не работает"
            else:
                selfcheck_res, verdict = f"PASS ({checks_str})", "подтверждено"

        table_rows.append({
            "num": str(num),
            "func": _clean_table_cell(func_title),
            "claimed": _clean_table_cell(claim),
            "selfcheck_res": _clean_table_cell(selfcheck_res),
            "checks": checks_str,
            "verdict": verdict,
        })

    # Summary stats
    cnt_confirmed = sum(1 for r in table_rows if r["verdict"] == "подтверждено")
    cnt_blocked = sum(1 for r in table_rows if r["verdict"] == "BLOCKED-EXTERNAL")
    cnt_broken = sum(1 for r in table_rows if r["verdict"] == "не работает")
    cnt_untested = sum(1 for r in table_rows if r["verdict"] == "не проверено")

    md: list[str] = [
        "# Реестр расхождений заявленного и фактического состояния NextLife",
        "",
        f"- **Всего позиций в реестре заявленного**: `{len(table_rows)}`",
        f"- **Подтверждено собственными проверками selfcheck (`C-01` .. `C-48`)**: `{cnt_confirmed}`",
        f"- **Заблокировано внешними зависимостями (`BLOCKED-EXTERNAL`)**: `{cnt_blocked}`",
        f"- **Не работает / Нет выделенной поведенческой проверки (`FAIL` / слепая зона)**: `{cnt_broken}`",
        "",
        "> [!IMPORTANT]",
        "> В соответствии с постановлением по приоритетам и ТЗ К1-F6 (Шаг 3, К1-063),",
        "> подтверждёнными признаются исключительно те требования, которые покрыты",
        "> собственной выделенной поведенческой проверкой из набора selfcheck (`C-01` .. `C-48`).",
        "> Требования, зависящие от внешних систем, переведены в `BLOCKED-EXTERNAL` (16 позиций).",
        "> Все остальные требования (122 позиции), не имеющие собственной проверки, классифицированы",
        "> как «не работает» (слепая зона инструмента selfcheck).",
        "",
        "## 1. Сводный реестр функций",
        "",
        "| функция | заявлено | результат selfcheck / тестов | проверки | вывод |",
        "| --- | --- | --- | --- | --- |",
    ]

    for r in table_rows:
        md.append(f"| {r['func']} | {r['claimed']} | {r['selfcheck_res']} | {r['checks']} | **{r['verdict']}** |")

    md.extend([
        "",
        "---",
        "",
        "## 2. Заблокировано внешними зависимостями (BLOCKED-EXTERNAL)",
        "",
        "В соответствии с разделом 3 постановки задачи K1-priority-F4-F5, функционал,",
        "зависящий от внешних сервисов и оборудования, классифицирован со статусом `BLOCKED-EXTERNAL`.",
        "",
        f"**Всего заблокированных позиций**: `{len(blocked_items)}`",
        "",
        "### 2.1 Группировка внешних блокировок по типам систем",
        "",
        "1. **Внешние коннекторы маркетплейсов (Bazoš.cz, Sbazar.cz, Aukro.cz)**:",
        "   - Bazoš: требуется чешский номер телефона для SMS-верификации и IP без WAF/капчи.",
        "   - Sbazar: требуется боевая учётная запись Seznam.cz (активная сессия) и доступ к API.",
        "   - Aukro: требуются боевые ключи OAuth 2.0 API aukro.cz и активный профиль продавца.",
        "2. **Аппаратный мост Android ADB**:",
        "   - Требуется физическое Android-устройство, подключенное по USB/ADB с авторизацией.",
        "3. **Почтовый шлюз OpenClaw**:",
        "   - Требуется внешний запущенный шлюз OpenClaw и доступ к корпоративному почтовому серверу.",
        "4. **Внешний фид котировок валют (ECB/CNB live FX feed)**:",
        "   - Требуется внешний сетевой доступ к фиду XML котировок ЕЦБ/ЧНБ (в закрытом контуре интернет заблокирован).",
        "5. **Сервис визуальной верификации (VLM Endpoint)**:",
        "   - Требуется доступный эндпоинт VLM (Vision-Language Model) API для детекции скрытых дефектов.",
        "",
        "### 2.2 Полный перечень заблокированных позиций и требования к доступам",
        "",
        "| № | Источник / Задача | Заявленная функция | Блокирующий доступ / Причина |",
        "| --- | --- | --- | --- |",
    ])

    for b in blocked_items:
        md.append(f"| {b['num']} | {_clean_table_cell(b['source'])} | {_clean_table_cell(b['claim'])} | {_clean_table_cell(b['reason'])} |")

    md.extend([
        "",
        "---",
        "",
        "## 3. Требования без собственной поведенческой проверки (слепая зона / не работает)",
        "",
        f"**Всего позиций без собственной проверки**: `{len(blind_spots)}`",
        "",
        "В соответствии с правилом раздела 4 ТЗ К1 и К1-F6 (Шаг 3), требование считается",
        "подтверждённым только при наличии выделенной поведенческой проверки в `selfcheck`.",
        f"Все `{len(blind_spots)}` позиций переведены в категорию «не работает» (слепая зона инструмента).",
        "",
    ])

    OUT_FILE.write_text("\n".join(md), encoding="utf-8")
    print(f"Discrepancy registry generated: {OUT_FILE}")
    print(f"Total: {len(table_rows)}, Confirmed: {cnt_confirmed}, Blocked: {cnt_blocked}, Broken: {cnt_broken}, Untested: {cnt_untested}")


if __name__ == "__main__":
    build_discrepancies()
