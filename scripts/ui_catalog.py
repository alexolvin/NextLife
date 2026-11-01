#!/usr/bin/env python3
"""N6-01. Каталог проверок UI: U-01…U-NN, по одной на экран и состояние.

Сводит проверки N1 (батарея N1-01…N1-07 + смоки) и экраны/состояния N5
в плоский каталог. Контроль полноты: отсутствие ЛЮБОЙ проверки каталога
в артефактах прогонов (results.json состояния) роняет прогон (exit 1).

Артефакты:
  scripts/ui_catalog.py [--strict]
    --strict: кроме полноты требует, чтобы все проверки были PASS.
  .a/reports/n6/catalog_U.csv — сам каталог (id, состояние, экран/проверка,
    статус, доказательство).

Единица каталога:
  instrument-состояние: (state, screen) — к экрану применена вся батарея N1
    (N1-01…N1-07 + смоки) на каждом из 4 вьюпортов;
  custom-состояние: (state, check) — именованная Playwright-проверка.
"""
import argparse
import csv
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import ui_audit_states as S  # noqa: E402

STATES_DIR = ROOT / "ui-audit" / "states"
CATALOG_OUT = ROOT / ".a" / "reports" / "n6" / "catalog_U.csv"
VIEWPORTS = ["360", "390", "768", "1280"]
# Имя снимка-проверки: <badge>:<base>_<vp>. Группируем по base —
# каждый вьюпорт обязан внести ровно один снимок с этим base.
VP_SHOT_RE = re.compile(r"^(?P<badge>golden|screenshot):(?P<base>.+)_(?P<vp>360|390|768|1280)$")


def expected_units():
    """Полный ожидаемый набор: (state_id, unit, kind)."""
    units = []
    for st in S.STATES:
        if st["kind"] == "instrument":
            for name, path, _group in st["screens"]:
                units.append((st["id"], f"{name} ({path})", "instrument"))
        else:
            units.append((st["id"], f"custom:{st['fn']}", "custom"))
    return units


def load_results(state_id):
    p = STATES_DIR / state_id / "results.json"
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def check_instrument(state_id, screen_name):
    """Вся батарея N1 на экране во всех 4 вьюпортах. Возвращает (ok, detail)."""
    res = load_results(state_id)
    if res is None:
        return False, "results.json отсутствует"
    by_screen = {}
    for r in res:
        base, vp = r["screen"].rsplit("@", 1)
        by_screen.setdefault(base, set()).add(vp)
    vps = by_screen.get(screen_name)
    if vps is None:
        return False, "экран отсутствует в отчёте"
    missing = [v for v in VIEWPORTS if v not in vps]
    if missing:
        return False, f"нет вьюпортов: {','.join(missing)}"
    fails = [r for r in res if r["screen"].rsplit("@", 1)[0] == screen_name
             and r["status"] == "FAIL"]
    if fails:
        bad = [f"{c['n']}: {c['d'][:60]}" for c in fails[0]["checks"] if c["s"] == "FAIL"]
        return False, f"FAIL: {'; '.join(bad[:2])}"
    blocked = [r for r in res if r["screen"].rsplit("@", 1)[0] == screen_name
               and r["status"] == "BLOCKED"]
    if blocked:
        return False, f"BLOCKED: {len(blocked)}"
    return True, "4/4 вьюпорта, N1-01…N1-07 + смоки зелёные"


def check_custom(state_id):
    """Все именованные проверки кастомного состояния во всех 4 вьюпортах."""
    res = load_results(state_id)
    if res is None:
        return False, "results.json отсутствует"
    # Имена с префиксом golden:/screenshot: включают вьюпорт в имени
    # (golden:lb_first_360 существует ТОЛЬКО во входе 360) — группируем по
    # базовому имени: оно обязано присутствовать по одному разу в каждом
    # из 4 вьюпортов. Функциональные проверки имеют общее имя.
    plain, shots = {}, {}
    for r in res:
        m = VP_SHOT_RE.match(r["check"])
        if m:
            shots.setdefault(m["base"], set()).add(m["vp"])
        else:
            plain.setdefault(r["check"], set()).add(r["vp"])
    if not (plain or shots):
        return False, "пустой отчёт"
    for c, vps in {**plain, **shots}.items():
        missing = [v for v in VIEWPORTS if v not in vps]
        if missing:
            return False, f"{c}: нет вьюпортов {','.join(missing)}"
    bad = [r for r in res if r["status"] == "FAIL"]
    if bad:
        return False, f"FAIL: {bad[0]['check']} — {bad[0]['detail'][:80]}"
    return True, f"{len(plain) + len(shots)} проверок × 4 вьюпорта зелёные"


def main() -> int:
    ap = argparse.ArgumentParser(description="N6-01 catalog + completeness control")
    ap.add_argument("--strict", action="store_true",
                    help="требовать PASS по всем пунктам каталога")
    a = ap.parse_args()

    units = expected_units()
    rows = []
    problems = []
    for i, (state_id, unit, kind) in enumerate(units, start=1):
        uid = f"U-{i:02d}"
        if kind == "instrument":
            screen = unit.split(" (")[0]
            ok, detail = check_instrument(state_id, screen)
        else:
            ok, detail = check_custom(state_id)
        evidence = f"ui-audit/states/{state_id}/" + (
            "report.md" if kind == "instrument" else "results.json")
        status = "PASS" if ok else "MISSING"
        if a.strict and not ok:
            status = "FAIL"
        if not ok:
            problems.append(f"{uid} {state_id} / {unit}: {detail}")
        rows.append([uid, state_id, unit, status, detail, evidence])

    CATALOG_OUT.parent.mkdir(parents=True, exist_ok=True)
    with CATALOG_OUT.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["id", "state", "screen_or_check", "status", "detail", "evidence"])
        w.writerows(rows)

    print(f"Каталог: {len(rows)} проверок (U-01…U-{len(rows):02d}) -> {CATALOG_OUT}")
    for uid, state_id, unit, status, detail, _e in rows:
        print(f"  {uid}  {status:8} {state_id:18} {unit[:70]}")
    if problems:
        print(f"\nКОНТРОЛЬ ПОЛНОТЫ: FAIL — {len(problems)} проверок отсутствует(ют)/красные:")
        for p in problems:
            print(f"  ❌ {p}")
        return 1
    print(f"\nКОНТРОЛЬ ПОЛНОТЫ: OK — все {len(rows)} проверок каталога присутствуют в артефактах.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
