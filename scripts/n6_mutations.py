#!/usr/bin/env python3
"""N6-03. Мутационная проверка прибора: 7 мутаций, каждая в отдельной ветке, с откатом.

Готово, когда: каждая мутация даёт ожидаемое падение с указанием причины,
приложены семь выводов (.a/reports/mutations/<name>.txt).
Мутация, оставшаяся зелёной, аннулирует результат очереди.

Ветки создаются без коммитов (n6-mut-<i>, откат = git checkout master + branch -D).
Файлы откатываются побайтово (рабочее дерево содержит незакоммиченную работу
N4/N5, git checkout -- <file> здесь недопустим).

Детекторы:
  m1 app.css удалён из разметки      -> прибор: N1-02/N1-04 (нет стилей -> переполнение/тапы)
  m2 тег Alpine удалён               -> линтер: директивы x-* без Alpine (N1-06)
  m3 Tailwind-класс возвращён        -> линтер: tailwind-утилита вне контракта
  m4 правило .hidden убрано          -> прибор: N1-05 (скрытые модалки видны)
  m5 изображение 640px без контейнера -> прибор: N1-02 overflow
  m6 кнопка 24px                     -> прибор: N1-04 tap < 44
  m7 defects в старой форме (БД)     -> прибор: N1-07 golden (карточка рендерится иначе)
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MUT_OUT = ROOT / ".a" / "reports" / "mutations"
MUT_TMP = ROOT / ".qwen" / "tmp" / "n6_mut"
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))
from nextlife.storage.db import get_engine  # noqa: E402

TOKEN = os.environ.get("NEXTLIFE_WEB_TOKEN", "")
BASE = os.environ.get("NEXTLIFE_BASE_URL", "http://localhost:8081")
DSN = os.environ.get("NEXTLIFE_DSN", "")
PY = sys.executable
ENV = dict(os.environ)


def sh(cmd, **kw):
    return subprocess.run(cmd, shell=True, cwd=ROOT, capture_output=True,
                          text=True, env=ENV, **kw)


def fail(msg):
    print(f"\n!! ABORT: {msg}", file=sys.stderr)
    sys.exit(2)


def git_branch_create(name):
    r = sh(f"git checkout -b {name}")
    if r.returncode != 0:
        fail(f"ветка не создана: {r.stderr}")


def git_branch_drop(name):
    sh("git checkout master")
    r = sh(f"git branch -D {name}")
    if r.returncode != 0:
        print(f"!! ветка {name} не удалась: {r.stderr}", file=sys.stderr)


def instrument(only, out_name, extra_golden=False):
    """Прогон прибора на узком 360px. Возвращает (exit_code, report_text)."""
    out = MUT_TMP / out_name
    if out.exists():
        shutil.rmtree(out)
    if extra_golden:
        (out / "golden").mkdir(parents=True)
        shutil.copytree(ROOT / "ui-audit" / "run" / "golden", out / "golden",
                        dirs_exist_ok=True)
    env = dict(ENV, NEXTLIFE_BASE_URL=BASE, NEXTLIFE_WEB_TOKEN=TOKEN,
               PLAYWRIGHT_BROWSERS_PATH=os.environ.get("PLAYWRIGHT_BROWSERS_PATH", ""))
    r = subprocess.run(
        f'{PY} scripts/ui_audit.py --out {out} --only "{only}" --viewports 360',
        shell=True, cwd=ROOT, capture_output=True, text=True, env=env)
    rep = out / "report.md"
    return r.returncode, (rep.read_text(encoding="utf-8") if rep.exists()
                          else r.stdout + r.stderr)


def linter():
    r = subprocess.run(f"{PY} scripts/check_css_contract.py", shell=True,
                       cwd=ROOT, capture_output=True, text=True, env=ENV)
    return r.returncode, r.stdout + r.stderr


def expected_fail_report(report_text, needles):
    """Причина падения: хотя бы один ожидаемый маркер в отчёте."""
    return [n for n in needles if n in report_text]


# --------------------------------------------------------------- mutations
BASE_HTML = ROOT / "templates" / "web" / "base.html"
ITEMS_HTML = ROOT / "templates" / "web" / "items.html"
APP_CSS = ROOT / "src" / "nextlife" / "api" / "static" / "app.css"


def mut1_remove_app_css():
    """Удалить <link ... app.css> из base.html. Ожидание: прибор FAIL (overflow/taps)."""
    name = "m1_no_app_css"
    branch = f"n6-mut-{name}"
    orig = BASE_HTML.read_bytes()
    try:
        git_branch_create(branch)
        text = orig.decode("utf-8")
        new = text.replace('  <link rel="stylesheet" href="/static/app.css">\n', "")
        if new == text:
            fail("m1: строка app.css не найдена в base.html")
        BASE_HTML.write_text(new, encoding="utf-8")
        rc, report = instrument("items", name)
        found = expected_fail_report(report, ["N1-02 overflow** FAIL", "N1-04 taps** FAIL"])
        verdict = "DROPPED (expected)" if (rc != 0 and found) else "STAYED GREEN (queue invalidated)"
        out = (f"МУТАЦИЯ m1: app.css удалён из разметки (base.html)\n"
               f"ВЕТКА: {branch} (откат: {branch} удалена)\n"
               f"ДЕТЕКТОР: python3 scripts/ui_audit.py --only items --viewports 360\n"
               f"РЕЗУЛЬТАТ: exit={rc}\nОЖИДАЕМОЕ ПАДЕНИЕ: {found}\n"
               f"ВЕРДИКТ: {verdict}\n\n--- report.md ---\n{report}\n")
        (MUT_OUT / f"{name}.txt").write_text(out, encoding="utf-8")
        print(f"m1: {verdict} | маркеры: {found}")
        return rc != 0 and bool(found)
    finally:
        BASE_HTML.write_bytes(orig)
        git_branch_drop(branch)


def mut2_remove_alpine():
    """Удалить тег Alpine. Ожидание: линтер FAIL (директивы x-* без Alpine)."""
    name = "m2_no_alpine"
    branch = f"n6-mut-{name}"
    orig = BASE_HTML.read_bytes()
    try:
        git_branch_create(branch)
        text = orig.decode("utf-8")
        new = text.replace('  <script defer src="/static/vendor/alpine.min.js"></script>\n', "")
        if new == text:
            fail("m2: тег Alpine не найден в base.html")
        BASE_HTML.write_text(new, encoding="utf-8")
        rc, out_txt = linter()
        found = expected_fail_report(out_txt,
                                     ["U-2: Alpine directive without Alpine loaded",
                                      "Alpine.js loaded: False"])
        verdict = "DROPPED (expected)" if (rc != 0 and found) else "STAYED GREEN (queue invalidated)"
        (MUT_OUT / f"{name}.txt").write_text(
            f"МУТАЦИЯ m2: тег Alpine удалён из base.html\n"
            f"ВЕТКА: {branch} (откат: {branch} удалена)\n"
            f"ДЕТЕКТОР: python3 scripts/check_css_contract.py\n"
            f"РЕЗУЛЬТАТ: exit={rc}\nОЖИДАЕМОЕ ПАДЕНИЕ: {found}\n"
            f"ВЕРДИКТ: {verdict}\n\n--- вывод линтера ---\n{out_txt}\n", encoding="utf-8")
        print(f"m2: {verdict} | маркеры: {found}")
        return rc != 0 and bool(found)
    finally:
        BASE_HTML.write_bytes(orig)
        git_branch_drop(branch)


def mut3_tailwind_class():
    """Вернуть Tailwind-класс в шаблон. Ожидание: линтер FAIL (tailwind-утилита)."""
    name = "m3_tailwind_class"
    branch = f"n6-mut-{name}"
    orig = ITEMS_HTML.read_bytes()
    try:
        git_branch_create(branch)
        text = orig.decode("utf-8")
        # Класс вставляется ВНУТРЬ атрибута class= (кавычка входит в
        # паттерн): в первом прогоне кампании замена кончалась до кавычки
        # и text-2xl оказывался чужим атрибутом вне class="..." — линтер
        # его (справедливо) не видел, мутация осталась зелёной.
        old = '<h2 class="page-title">'
        if old not in text:
            fail("m3: заголовок page-title не найден в items.html")
        ITEMS_HTML.write_text(
            text.replace(old, '<h2 class="page-title text-2xl">', 1),
            encoding="utf-8")
        rc, out_txt = linter()
        found = expected_fail_report(out_txt, ["Tailwind", "tailwind", "text-2xl"])
        verdict = "DROPPED (expected)" if (rc != 0 and found) else "STAYED GREEN (queue invalidated)"
        (MUT_OUT / f"{name}.txt").write_text(
            f"МУТАЦИЯ m3: Tailwind-класс text-2xl возвращён в items.html (h2.page-title)\n"
            f"ВЕТКА: {branch} (откат: {branch} удалена)\n"
            f"ДЕТЕКТОР: python3 scripts/check_css_contract.py\n"
            f"РЕЗУЛЬТАТ: exit={rc}\nОЖИДАЕМОЕ ПАДЕНИЕ: {found}\n"
            f"ВЕРДИКТ: {verdict}\n\n--- вывод линтера ---\n{out_txt}\n", encoding="utf-8")
        print(f"m3: {verdict} | маркеры: {found}")
        return rc != 0 and bool(found)
    finally:
        ITEMS_HTML.write_bytes(orig)
        git_branch_drop(branch)


def mut4_remove_hidden_rule():
    """Убрать правило .hidden. Ожидание: прибор FAIL N1-05 (модалки видны)."""
    name = "m4_no_hidden_rule"
    branch = f"n6-mut-{name}"
    orig = APP_CSS.read_bytes()
    try:
        git_branch_create(branch)
        text = orig.decode("utf-8")
        new = text.replace(".hidden { display: none !important; }\n", "")
        if new == text:
            fail("m4: правило .hidden не найдено в app.css")
        APP_CSS.write_text(new, encoding="utf-8")
        # экраны сделок именуется deal[<id8>] — фильтр по подстроке "deal"
        rc, report = instrument("deal", name)
        found = expected_fail_report(report, ["N1-05 hidden** FAIL"])
        verdict = "DROPPED (expected)" if (rc != 0 and found) else "STAYED GREEN (queue invalidated)"
        (MUT_OUT / f"{name}.txt").write_text(
            f"МУТАЦИЯ m4: правило .hidden удалено из app.css\n"
            f"ВЕТКА: {branch} (откат: {branch} удалена)\n"
            f"ДЕТЕКТОР: python3 scripts/ui_audit.py --only deal --viewports 360\n"
            f"РЕЗУЛЬТАТ: exit={rc}\nОЖИДАЕМОЕ ПАДЕНИЕ: {found}\n"
            f"ВЕРДИКТ: {verdict}\n\n--- report.md ---\n{report}\n", encoding="utf-8")
        print(f"m4: {verdict} | маркеры: {found}")
        return rc != 0 and bool(found)
    finally:
        APP_CSS.write_bytes(orig)
        git_branch_drop(branch)


def mut5_img_640():
    """Вставить изображение 640px без контейнера. Ожидание: прибор FAIL N1-02."""
    name = "m5_img_640_no_container"
    branch = f"n6-mut-{name}"
    orig = ITEMS_HTML.read_bytes()
    try:
        git_branch_create(branch)
        text = orig.decode("utf-8")
        m = re.search(r"({% block content %})\n", text)
        if not m:
            fail("m5: block content не найден в items.html")
        img = ('<img src="data:image/gif;base64,R0lGODlhAQABAAAAACw="'
               ' width="640" style="max-width:none">\n')
        ITEMS_HTML.write_text(text.replace(m.group(0), m.group(0) + img, 1),
                              encoding="utf-8")
        rc, report = instrument("items", name)
        found = expected_fail_report(report, ["N1-02 overflow** FAIL"])
        verdict = "DROPPED (expected)" if (rc != 0 and found) else "STAYED GREEN (queue invalidated)"
        (MUT_OUT / f"{name}.txt").write_text(
            f"МУТАЦИЯ m5: изображение 640px без контейнера вставлено в items.html\n"
            f"ВЕТКА: {branch} (откат: {branch} удалена)\n"
            f"ДЕТЕКТОР: python3 scripts/ui_audit.py --only items --viewports 360\n"
            f"РЕЗУЛЬТАТ: exit={rc}\nОЖИДАЕМОЕ ПАДЕНИЕ: {found}\n"
            f"ВЕРДИКТ: {verdict}\n\n--- report.md ---\n{report}\n", encoding="utf-8")
        print(f"m5: {verdict} | маркеры: {found}")
        return rc != 0 and bool(found)
    finally:
        ITEMS_HTML.write_bytes(orig)
        git_branch_drop(branch)


def mut6_btn_24px():
    """Уменьшить кнопку до 24px. Ожидание: прибор FAIL N1-04 (тап < 44)."""
    name = "m6_btn_24px"
    branch = f"n6-mut-{name}"
    orig = APP_CSS.read_bytes()
    try:
        git_branch_create(branch)
        APP_CSS.write_text(orig.decode("utf-8") +
                           "\n/* N6-03 m6: кнопка 24px (мутация) */\n"
                           ".btn { min-height: 24px; height: 24px; "
                           "padding-top: 0; padding-bottom: 0; }\n",
                           encoding="utf-8")
        rc, report = instrument("items", name)
        found = expected_fail_report(report, ["N1-04 taps** FAIL"])
        verdict = "DROPPED (expected)" if (rc != 0 and found) else "STAYED GREEN (queue invalidated)"
        (MUT_OUT / f"{name}.txt").write_text(
            f"МУТАЦИЯ m6: кнопки уменьшены до 24px (app.css, правило в конце)\n"
            f"ВЕТКА: {branch} (откат: {branch} удалена)\n"
            f"ДЕТЕКТОР: python3 scripts/ui_audit.py --only items --viewports 360\n"
            f"РЕЗУЛЬТАТ: exit={rc}\nОЖИДАЕМОЕ ПАДЕНИЕ: {found}\n"
            f"ВЕРДИКТ: {verdict}\n\n--- report.md ---\n{report}\n", encoding="utf-8")
        print(f"m6: {verdict} | маркеры: {found}")
        return rc != 0 and bool(found)
    finally:
        APP_CSS.write_bytes(orig)
        git_branch_drop(branch)


def mut7_defects_old_form():
    """Вернуть в БД запись defects в старой форме (объект {zone: text}
    вместо списка [{zone, text, photo_id}]).

    Ожидание: прибор FAIL N1-07 golden — карточка рендерится иначе.
    """
    name = "m7_defects_old_form"
    branch = f"n6-mut-{name}"
    # Single DSN source: raw psycopg2 connection from the app engine's pool.
    # mut7 holds one connection (a read plus committed writes) across the
    # instrument() subprocess, so it runs on the raw DBAPI connection; the
    # pool checkout is released via the proxy below.
    proxy = get_engine().raw_connection()
    conn = proxy.driver_connection
    cur = conn.cursor()
    # Целевой лот — 348: единственный, у которого есть БАЗОВЫЙ эталон
    # (item_348_*.png в ui-audit/run/golden) И дефекты; иначе прибор создаст
    # новый эталон (GOLDEN), а не отметит FAIL, и мутация останется зелёной.
    # Старая форма — фактическая предмиграционная запись лота 348
    # (объект {zone: text}, см. .a/reports/n4/defects_before.txt).
    code = "348"
    OLD_FORM = '{"dust": "Единичные микропылинки внутри, без влияния на снимок"}'
    cur.execute("SELECT defects FROM item WHERE code = %s", (code,))
    row = cur.fetchone()
    if not row or not row[0]:
        fail(f"m7: у лота {code} нет дефектов — мутация невозможна")
    orig_defects = row[0]
    orig_json = json.dumps(orig_defects, ensure_ascii=False) if isinstance(
        orig_defects, (list, dict)) else str(orig_defects)
    try:
        git_branch_create(branch)
        cur.execute("UPDATE item SET defects = %s::jsonb WHERE code = %s",
                    (OLD_FORM, code))
        conn.commit()
        rc, report = instrument(f"item[{code}]", name, extra_golden=True)
        found = expected_fail_report(report, ["N1-07 golden** FAIL", "golden"])
        verdict = "DROPPED (expected)" if (rc != 0 and found) else "STAYED GREEN (queue invalidated)"
        (MUT_OUT / f"{name}.txt").write_text(
            f"МУТАЦИЯ m7: defects лота {code} возвращены в старую форму "
            f"(объект {{zone:text}} вместо списка [{{zone,text,photo_id}}])\n"
            f"Было: {orig_json}\n"
            f"ВЕТКА: {branch} (откат: {branch} удалена, дефекты восстановлены)\n"
            f"ДЕТЕКТОР: python3 scripts/ui_audit.py --only item[{code}] --viewports 360 "
            f"(эталон — базовый ui-audit/run/golden)\n"
            f"РЕЗУЛЬТАТ: exit={rc}\nОЖИДАЕМОЕ ПАДЕНИЕ: {found}\n"
            f"ВЕРДИКТ: {verdict}\n\n--- report.md ---\n{report}\n", encoding="utf-8")
        print(f"m7: {verdict} | маркеры: {found}")
        return rc != 0 and bool(found)
    finally:
        cur.execute("UPDATE item SET defects = %s::jsonb WHERE code = %s",
                    (orig_json, code))
        conn.commit()
        cur.execute("SELECT defects FROM item WHERE code = %s", (code,))
        now = cur.fetchone()[0]
        now_json = json.dumps(now, ensure_ascii=False) if isinstance(now, (list, dict)) else str(now)
        if now_json != orig_json:
            print(f"!! m7: дефекты лота {code} НЕ восстановлены: {now_json}", file=sys.stderr)
        else:
            print(f"m7: дефекты лота {code} восстановлены (проверено)")
        proxy.close()
        git_branch_drop(branch)


MUTATIONS = [
    ("m1", mut1_remove_app_css),
    ("m2", mut2_remove_alpine),
    ("m3", mut3_tailwind_class),
    ("m4", mut4_remove_hidden_rule),
    ("m5", mut5_img_640),
    ("m6", mut6_btn_24px),
    ("m7", mut7_defects_old_form),
]


def main() -> int:
    ap = argparse.ArgumentParser(description="N6-03 mutation testing")
    ap.add_argument("--only", default="", help="id мутации (m1..m7) или пусто = все")
    a = ap.parse_args()
    if not DSN:
        fail("NEXTLIFE_DSN не задан")
    MUT_OUT.mkdir(parents=True, exist_ok=True)
    MUT_TMP.mkdir(parents=True, exist_ok=True)
    branch = sh("git rev-parse --abbrev-ref HEAD").stdout.strip()
    if branch != "master":
        fail(f"ожидается master, текущая ветка: {branch}")

    results = {}
    for mid, fn in MUTATIONS:
        if a.only and a.only not in mid:
            continue
        print(f"\n=== {mid} ===")
        try:
            results[mid] = fn()
        except SystemExit:
            raise
        except Exception as e:
            results[mid] = False
            (MUT_OUT / f"{mid}_ERROR.txt").write_text(
                f"МУТАЦИЯ {mid}: НЕВЕРНАЯ РАБОТА ДРАЙВЕРА\n{e}\n", encoding="utf-8")
            print(f"!! {mid}: driver error: {e}")

    print("\n=== SUMMARY ===")
    for mid, ok in results.items():
        print(f"{'DROPPED(expected)' if ok else 'STAYED GREEN(аннулирует очередь)'}  {mid}")
    bad = [m for m, ok in results.items() if not ok]
    if bad:
        print(f"\n!! Мутации, оставшиеся зелёными: {bad} — результат очереди N6 АННУЛИРОВАН.")
        return 1
    print(f"\nВсе {len(results)} мутаций дали ожидаемое падение. Выводы: {MUT_OUT}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
