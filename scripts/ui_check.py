#!/usr/bin/env python3
"""ui_check.py — прибор регрессионных проверок TZ-NextLife-UI-N2 (раздел 3).

Принцип (ТЗ §3.1): на каждый дефект ревизии — именованная проверка; приёмка —
только парой прогонов «красный на коммите до правки, зелёный после».

Возможности (ТЗ §3.2):
1. сверка экрана с БД (значение на экране == значению в БД в ожидаемом формате);
2. сбор ошибок консоли и сети (4xx/5xx, включая изображения) — роняют проверку;
3. подсчёт элементов (число миниатюр == число логических фото);
4. геометрия: правый край в вьюпорте, цель нажатия >= 44x44, img не шире контейнера;
5. вычисленные стили (display:none у скрытых, модалка невидима в исходном состоянии);
6. сценарии (загрузил фото -> миниатюра, удалил -> счётчик -1);
7. сравнение с эталоном: 360/390/768/1280, допуск, сохранение diff.

Среда (ТЗ §2, Э-10): прогон — на копии БД (nextlife_check) и отдельном каталоге
медиа (/tmp/nlcheck/data/media), сервер 127.0.0.1:8083. Рабочая база Владельца
не затрагивается; тестовые лоты создаются/удаляются только в копии.

Отчёт: <out>/report.md и report.json; код выхода = число падений.
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO))

from PIL import Image, ImageChops  # noqa: E402

# допуски (совместимы с ui_audit.py)
PIXEL_TOL = 32
DIFF_FRAC = 0.0005

VIEWPORTS = [
    ("360", {"width": 360, "height": 800}),
    ("390", {"width": 390, "height": 844}),
    ("768", {"width": 768, "height": 1024}),
    ("1280", {"width": 1280, "height": 900}),
]

DEFAULT_BASE = "http://127.0.0.1:8083"

STABILIZE_JS = (
    "window.scrollTo(0, 0); "
    "document.getAnimations && document.getAnimations().forEach(a => a.finish());"
)


from scripts.checklib import CheckFail  # noqa: E402,F401  (re-export для совместимости)


def _load_checks(checks_dir: Path) -> list[tuple[str, str, Callable]]:
    found: list[tuple[str, str, Callable]] = []
    for mod_path in sorted(checks_dir.glob("check_*.py")):
        spec = importlib.util.spec_from_file_location(mod_path.stem, mod_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        for item in getattr(mod, "CHECKS", []):
            found.append(item)
    return found


def _git_head() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=REPO, capture_output=True, text=True
        ).stdout.strip()
    except Exception:  # noqa: BLE001
        return "unknown"


def _compare_images(a: Image.Image, b: Image.Image, diff_path: Path | None) -> tuple[str, str]:
    if a.size != b.size:
        return ("FAIL", f"размер {a.size[0]}x{a.size[1]} != эталон {b.size[0]}x{b.size[1]}")
    diff = ImageChops.difference(a, b).convert("L")
    mask = diff.point(lambda v: 255 if v > PIXEL_TOL else 0)
    changed = mask.histogram()[255]
    frac = changed / (a.size[0] * a.size[1])
    if frac <= DIFF_FRAC:
        return ("PASS", f"совпадение (изменено {frac:.4%})")
    if diff_path is not None:
        ImageChops.difference(a, b).point(lambda v: min(255, v * 8)).save(diff_path)
    return ("FAIL", f"расхождение {frac:.2%} пикселей ({changed})")


class Ctx:
    """Контекст одной проверки: страница, БД, ошибки, скриншоты, эталоны."""

    def __init__(self, base: str, shot_dir: Path, golden_dir: Path, token: str,
                 create_golden: bool = True):
        from nextlife.storage.db import get_engine
        from playwright.sync_api import sync_playwright

        self.base = base
        self.shot_dir = shot_dir
        self.golden_dir = golden_dir
        self.create_golden = create_golden
        self.failures: list[str] = []
        self.console: list[str] = []
        self.net: list[str] = []
        self._pw = sync_playwright().start()
        self.browser = self._pw.chromium.launch()
        self._ctx = self.browser.new_context(
            viewport={"width": 360, "height": 800}, device_scale_factor=2
        )
        self._ctx.add_cookies([{"name": "nextlife_token", "value": token, "url": base + "/"}])
        self.page = self._ctx.new_page()
        self.page.on(
            "console",
            lambda m: m.type in ("error", "warning") and self.console.append(f"[{m.type}] {m.text[:300]}"),
        )
        self.page.on(
            "response",
            lambda r: r.status >= 400 and self.net.append(f"{r.status} {r.request.method} {r.url}"),
        )
        # Single DSN source: raw psycopg2 connection from the app engine's pool
        # (no direct DBAPI connect call, no driver-suffix stripping). db() needs
        # low-level positional-parameter SQL, so it runs on the raw DBAPI
        # connection; the pool checkout is released in close().
        self._proxy = get_engine().raw_connection()
        self._conn = self._proxy.driver_connection
        self._vidx = 0

    # --- навигация -----------------------------------------------------
    def nav(self, url: str, wait_ms: int = 900) -> str:
        """Переход с очисткой счётчиков ошибок; возвращает видимый текст."""
        self.console.clear()
        self.net.clear()
        self.page.goto(url if url.startswith("http") else self.base + url, wait_until="load")
        self.page.wait_for_timeout(wait_ms)
        return self.page.evaluate("() => document.body.innerText")

    def body_text(self) -> str:
        return self.page.evaluate("() => document.body.innerText")

    def shot(self, name: str, full: bool = True) -> Path:
        path = self.shot_dir / f"{name}.png"
        self.page.evaluate(STABILIZE_JS)
        self.page.wait_for_timeout(120)
        self.page.screenshot(path=str(path), full_page=full)
        return path

    def golden(self, key: str, url: str) -> list[tuple[str, str, str]]:
        """Эталонное сравнение экрана в 4 вьюпортах (ТЗ §3.2.7).
        Возвращает [(vp, status, detail)]; отсутствие эталона -> GOLDEN-CREATED."""
        import shutil

        results: list[tuple[str, str, str]] = []
        for vp_name, vp in VIEWPORTS:
            gdir = self.golden_dir / key
            gdir.mkdir(parents=True, exist_ok=True)
            gpath = gdir / f"{vp_name}.png"
            cpath = self.shot_dir / "golden" / f"{key}_{vp_name}.png"
            cpath.parent.mkdir(parents=True, exist_ok=True)
            self.page.set_viewport_size(vp)
            self.nav(url, wait_ms=700)
            self.page.evaluate(STABILIZE_JS)
            self.page.wait_for_timeout(120)
            self.page.screenshot(path=str(cpath), full_page=True)
            if not gpath.exists():
                if self.create_golden:
                    shutil.copyfile(cpath, gpath)
                    results.append((vp_name, "GOLDEN-CREATED", str(gpath)))
                else:
                    results.append((vp_name, "GOLDEN-ABSENT", "RED-прогон: эталон не создаётся"))
                continue
            status, detail = _compare_images(Image.open(cpath), Image.open(gpath),
                                             gdir / f"diff_{vp_name}.png")
            results.append((vp_name, status, detail))
        self.page.set_viewport_size({"width": 360, "height": 800})
        return results

    # --- БД (копия, только чтение/контролируемые фикстуры) -------------
    def db(self, sql: str, params: tuple | None = None) -> list[tuple]:
        cur = self._conn.cursor()
        cur.execute(sql, params or ())
        rows = cur.fetchall() if cur.description is not None else []
        self._conn.commit()
        return rows

    # --- ассерты --------------------------------------------------------
    def expect(self, cond: bool, msg: str) -> None:
        if not cond:
            raise CheckFail(msg)

    def text_has(self, text: str, needle: str, what: str) -> None:
        self.expect(needle in text, f"{what}: «{needle}» не найдено; экран: {text[:400]!r}")

    def text_not_has(self, text: str, needle: str, what: str) -> None:
        self.expect(needle not in text, f"{what}: найдено «{needle}»; экран: {text[:400]!r}")

    def no_errors(self) -> None:
        """ТЗ §3.2.2: любая JS-ошибка и любой 4xx/5xx (включая изображения) роняют проверку."""
        problems = [f"console: {c}" for c in self.console] + [f"net: {n}" for n in self.net]
        if problems:
            raise CheckFail("ошибки консоли/сети: " + " | ".join(problems[:8]))

    # --- геометрия (ТЗ §3.2.4) ------------------------------------------
    def check_geometry(self, what: str) -> None:
        bad = self.page.evaluate(
            """() => {
                const out = [];
                const vw = document.documentElement.clientWidth;
                document.querySelectorAll('*').forEach(el => {
                    const r = el.getBoundingClientRect();
                    if (r.width === 0 || r.height === 0) return;
                    if (r.right > vw + 1) out.push('overflow:' + (el.className || el.tagName).toString().slice(0, 40) + ' right=' + Math.round(r.right));
                    const tag = el.tagName;
                    if (['A','BUTTON','INPUT','SELECT'].includes(tag)) {
                        if (r.width < 44 || r.height < 44) out.push('tap-target:' + tag + ' ' + Math.round(r.width) + 'x' + Math.round(r.height));
                    }
                    if (tag === 'IMG') {
                        const par = el.parentElement;
                        if (par && r.width > par.getBoundingClientRect().width + 2) out.push('img-wide:' + (el.className||'') + ' ' + Math.round(r.width));
                    }
                });
                return out;
            }"""
        )
        self.expect(not bad, f"{what}: геометрия: " + "; ".join(bad[:10]))

    # --- стили (ТЗ §3.2.5) ----------------------------------------------
    def hidden(self, selector: str, what: str) -> None:
        disp = self.page.evaluate(
            f"(s) => {{ const e = document.querySelector(s); return e ? getComputedStyle(e).display : 'MISSING'; }}",
            selector,
        )
        self.expect(disp == "none", f"{what}: {selector} display={disp} (ожидалось none)")

    def close(self) -> None:
        try:
            self._proxy.close()
            self.browser.close()
            self._pw.stop()
        except Exception:  # noqa: BLE001
            pass


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(REPO / "ui-check" / "run"))
    ap.add_argument("--golden-dir", default=str(REPO / "ui-check" / "golden"))
    ap.add_argument("--only", default="", help="список ID через запятую")
    ap.add_argument("--base", default=os.environ.get("NL_BASE_URL", DEFAULT_BASE))
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--no-golden-create", action="store_true",
                    help="RED-прогон: отсутствующие эталоны не создавать (GOLDEN-ABSENT)")
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    golden_dir = Path(args.golden_dir)

    from nextlife.core.exceptions import SecretResolutionError
    from nextlife.core.secrets import resolve_secret

    try:
        token = resolve_secret("NEXTLIFE_WEB_TOKEN")
    except SecretResolutionError:
        raise SystemExit(
            "NEXTLIFE_WEB_TOKEN не задан в окружении процесса: задайте токен "
            "веб-интерфейса (auth.web_token_secret_ref)"
        ) from None
    # DSN comes only from the process environment, like the other files
    # (the old env-file reader is gone); get_engine() resolves the same var.
    if not os.environ.get("NEXTLIFE_DSN"):
        raise SystemExit("NEXTLIFE_DSN не задан в окружении процесса (process environment)")
    checks = _load_checks(REPO / "tests" / "ui")
    if args.list:
        for cid, name, _ in checks:
            print(f"{cid}\t{name}")
        return

    if args.only:
        want = {s.strip() for s in args.only.split(",") if s.strip()}
        checks = [c for c in checks if c[0] in want]

    results: list[dict[str, Any]] = []
    for cid, name, fn in checks:
        shot_dir = out / "shots" / cid
        shot_dir.mkdir(parents=True, exist_ok=True)
        entry: dict[str, Any] = {"id": cid, "name": name, "status": "FAIL", "evidence": []}
        ctx = Ctx(args.base, shot_dir, golden_dir, token,
                  create_golden=not args.no_golden_create)
        t0 = time.time()
        try:
            fn(ctx)
            ctx.no_errors()
            entry["status"] = "PASS"
            entry["evidence"].append(f"все ассерты выполнены ({time.time() - t0:.1f} c)")
        except CheckFail as exc:
            entry["status"] = "FAIL"
            entry["evidence"].append(str(exc))
        except Exception as exc:  # noqa: BLE001
            entry["status"] = "ERROR"
            entry["evidence"].append(f"{exc.__class__.__name__}: {exc}")
        finally:
            entry["console"] = ctx.console.copy()
            entry["net"] = ctx.net.copy()
            entry["shots"] = sorted(p.name for p in shot_dir.glob("*.png"))
            ctx.close()
        results.append(entry)
        print(f"[{entry['status']}] {cid} {name}: {entry['evidence'][0][:160] if entry['evidence'] else ''}")

    # --- отчёт -----------------------------------------------------------
    fail_count = sum(1 for r in results if r["status"] != "PASS")
    commit = _git_head()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
    lines = [
        "# UI-Check — отчёт (TZ-NextLife-UI-N2 §3)",
        "",
        f"- Запущено: {now}",
        f"- Базовый URL: {args.base} (копия БД nextlife_check, media /tmp/nlcheck/data/media)",
        f"- Коммит кода: **{commit}**",
        f"- Проверок: {len(results)}, падений: **{fail_count}** (код выхода {fail_count})",
        "",
        "| ID | Проверка | Результат | Доказательство |",
        "| --- | --- | --- | --- |",
    ]
    for r in results:
        ev = " | ".join(r["evidence"][:3]).replace("|", "/")[:300]
        lines.append(f"| {r['id']} | {r['name']} | {r['status']} | {ev} |")
    all_console = [f"[{r['id']}] {c}" for r in results for c in r.get("console", [])]
    all_net = [f"[{r['id']}] {n}" for r in results for n in r.get("net", [])]
    lines += ["", "## Ошибки консоли и сети", ""]
    lines += [f"- {c}" for c in all_console[:40]]
    lines += [f"- {n}" for n in all_net[:40]]
    if not all_console and not all_net:
        lines.append("- нет")
    (out / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (out / "report.json").write_text(
        json.dumps({"commit": commit, "ts": now, "base": args.base,
                    "fail_count": fail_count, "results": results},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\nreport: {out / 'report.md'} | падений: {fail_count} | commit {commit}")
    sys.exit(fail_count)


if __name__ == "__main__":
    main()
