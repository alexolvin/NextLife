"""TZ-NextLife-UI-N2 V6 (Изображения) — проверки P-R05, P-R06.

P-R05   действия на каждое фото: выровнять (поворот), обрезка, очистить
       фон, убрать пыль, сделать обложкой, пометка «фото дефекта».
       Результат сохраняется новой версией, оригинал неизменен (Э-10-1):
       sha256 строки ORIGINAL и байты файла ORIGINAL не меняются.
       Честность: чистящие операции (пыль/фон) запрещены для фото
       дефекта — сервер отдаёт 400, в UI кнопки скрыты.
P-R06   подсказки по качеству сразу после загрузки («тёмный»,
       «повёрнуто»); догенерация вариантов под каждую площадку после
       правки фото; сгенеративно изменённое фото помечено в UI («AI»).

Окружение (Э-10): копия nextlife_check, чек-сервер 127.0.0.1:8083.
Определённость:
- операции — на фикстурном предмете 902 (копия цепочки 747:
  ORIGINAL 1920x1440 -> PROCESSED 1920x1440 -> PUBLISH 1600x1200);
  поворот 90° детерминированно 1600x1200 -> 1200x1600, обрезка 10%
  от поля -> 960x1280;
- подсказки/догенерация/пометка AI — на предмете 106 (без фото):
  фикстурные JPEG собираются PIL (тёмный + EXIF orientation 6 и
  яркий нормальный); догенерация гарантированно меняет sha —
  обрезка сдвигает размер 4:3 (1600x1200 -> 1280x960);
- 747 в V6 не изменяется: файлы 747 только читаются (копируются).
"""

from __future__ import annotations

import hashlib
import shutil
import uuid
from pathlib import Path

from PIL import Image, ImageDraw

OPS_ITEM = "902"    # фикстура: пер-фото операции (удалается в finally)
HINTS_ITEM = "106"  # подсказки после загрузки, догенерация, пометка AI
SOURCE_ITEM = "747" # донор файлов цепочки (только чтение)

MEDIA_ROOT = Path("/tmp/nlcheck/data/media")
VARIANTS_DIR = MEDIA_ROOT / "variants"
FIX_DIR = Path("/tmp/nlcheck") / "v6_fixtures"


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(8192):
            h.update(chunk)
    return h.hexdigest()


def _hint_text(ctx) -> str:
    return ctx.page.evaluate(
        "() => { const e = document.querySelector("
        "'[data-testid=\"quality-hints\"]'); return e ? e.textContent : ''; }"
    )


def _cleanup_item_media(ctx, item_code: str) -> None:
    """Стереть медиа-конвейер предмета: варианты -> мастера -> оригиналы."""
    master_ids = [
        r[0]
        for r in ctx.db(
            "SELECT id FROM media_master WHERE original_id IN "
            "(SELECT id FROM media_original WHERE item_code=%s)",
            (item_code,),
        )
    ]
    ctx.db(
        "DELETE FROM media_variant WHERE master_id IN "
        "(SELECT id FROM media_master WHERE original_id IN "
        "(SELECT id FROM media_original WHERE item_code=%s))",
        (item_code,),
    )
    ctx.db(
        "DELETE FROM media_master WHERE original_id IN "
        "(SELECT id FROM media_original WHERE item_code=%s)",
        (item_code,),
    )
    ctx.db("DELETE FROM media_original WHERE item_code=%s", (item_code,))
    for mid in master_ids:
        for f in VARIANTS_DIR.glob(f"var_{mid}_*"):
            f.unlink(missing_ok=True)


def _setup_ops_fixture(ctx) -> None:
    """Предмет 902: копия цепочки 747 (3 роли, 3 файла в /tmp/nlcheck)."""
    rows = ctx.db(
        "SELECT role, width, height, path FROM item_photo "
        "WHERE item_code=%s ORDER BY created_at",
        (SOURCE_ITEM,),
    )
    ctx.expect(len(rows) == 3,
               f"фикстура: у {SOURCE_ITEM} ожидалась цепочка из 3 фото, найдено {len(rows)}")
    dirs = {"ORIGINAL": "originals", "PROCESSED": "sanitized", "PUBLISH": "processed"}
    new_paths: dict[str, tuple[Path, int, int]] = {}
    for role, w, h, path in rows:
        src = Path(path)
        ctx.expect(src.exists(), f"фикстура: исходный файл {SOURCE_ITEM} отсутствует: {path}")
        dst = (MEDIA_ROOT / OPS_ITEM / dirs[role]
               / f"v6_{role.lower()}_{uuid.uuid4().hex[:8]}.jpg")
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        new_paths[role] = (dst, w, h)

    ctx.db(
        "INSERT INTO item (code, kind, title, category_key, attrs, condition_key, "
        "defects, status, created_at, updated_at) "
        "VALUES (%s, 'SELL', 'UI Check V6 Ops 902', 'electronics', '{}'::jsonb, "
        "'used', '[]'::jsonb, 'DRAFT', NOW(), NOW())",
        (OPS_ITEM,),
    )
    parent_role = {"ORIGINAL": None, "PROCESSED": "ORIGINAL", "PUBLISH": "PROCESSED"}
    ids: dict[str, str] = {}
    for role in ("ORIGINAL", "PROCESSED", "PUBLISH"):
        dst, w, h = new_paths[role]
        pid = str(uuid.uuid4())
        ids[role] = pid
        parent = ids[parent_role[role]] if parent_role[role] else None
        ctx.db(
            "INSERT INTO item_photo (id, item_code, role, path, sha256, width, height, "
            "derived_from_id, transform_log, ai_generated, vlm_verdict, "
            "created_at, updated_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, '[]'::jsonb, false, "
            "'{}'::jsonb, NOW(), NOW())",
            (pid, OPS_ITEM, role, str(dst), _sha256_file(dst), w, h, parent),
        )


def _cleanup_ops_fixture(ctx) -> None:
    _cleanup_item_media(ctx, OPS_ITEM)
    ctx.db("DELETE FROM item_photo WHERE item_code=%s", (OPS_ITEM,))
    ctx.db("DELETE FROM item WHERE code=%s", (OPS_ITEM,))
    shutil.rmtree(MEDIA_ROOT / OPS_ITEM, ignore_errors=True)


def _publish_row(ctx) -> tuple[int, int, str]:
    rows = ctx.db(
        "SELECT width, height, transform_log::text FROM item_photo "
        "WHERE item_code=%s AND role='PUBLISH'",
        (OPS_ITEM,),
    )
    ctx.expect(len(rows) == 1, f"строка PUBLISH у {OPS_ITEM}: найдено {len(rows)}")
    return rows[0][0], rows[0][1], rows[0][2]


def _publish_id(ctx) -> str:
    rows = ctx.db(
        "SELECT id FROM item_photo WHERE item_code=%s AND role='PUBLISH'", (OPS_ITEM,)
    )
    ctx.expect(len(rows) == 1, f"строка PUBLISH у {OPS_ITEM}: найдено {len(rows)}")
    return rows[0][0]


def p_r05(ctx) -> None:
    """Пер-фото операции; оригинал неизменен; обложка; пометка дефекта + guard."""
    _setup_ops_fixture(ctx)
    try:
        # 1. Карточка: одно логическое фото, на миниатюре 6 кнопок действий.
        ctx.nav(f"/ui/items/{OPS_ITEM}")
        ctx.no_errors()
        ctx.text_has(ctx.body_text(), "UI Check V6 Ops 902",
                     "карточка фикстуры 902 не открыта")
        thumbs = ctx.page.query_selector_all('[data-testid^="thumb-item-"]')
        ctx.expect(len(thumbs) == 1,
                   f"ожидалось 1 логическое фото, найдено {len(thumbs)}")
        for op in ("rotate", "crop", "clean-bg", "remove-dust", "cover", "defect"):
            btn = ctx.page.query_selector(f'[data-testid="btn-op-{op}"]')
            ctx.expect(btn is not None, f"карточка: нет кнопки действия {op!r}")
        ctx.shot("r05_ops_buttons")

        # 2. Снимок оригинала (строка + байты файла) — базовая линия Э-10-1.
        orig = ctx.db(
            "SELECT sha256, path FROM item_photo "
            "WHERE item_code=%s AND role='ORIGINAL'",
            (OPS_ITEM,),
        )
        ctx.expect(len(orig) == 1, "у фикстуры нет строки ORIGINAL")
        orig_sha, orig_path = orig[0][0], Path(orig[0][1])
        orig_file_hash = _sha256_file(orig_path)

        # 3. Выровнять (поворот 90°): 1600x1200 -> 1200x1600, шаг в журнале.
        ctx.page.click('[data-testid="btn-op-rotate"]')
        ctx.no_errors()
        w, h, log = _publish_row(ctx)
        ctx.expect((w, h) == (1200, 1600),
                   f"после поворота ожидался размер 1200x1600, получен {w}x{h}")
        ctx.expect('"rotate"' in log,
                   f"в transform_log нет шага rotate: {log[:160]!r}")

        # 4. Обрезка (10% от поля): 1200x1600 -> 960x1280, шаг в журнале.
        ctx.page.click('[data-testid="btn-op-crop"]')
        ctx.no_errors()
        w, h, log = _publish_row(ctx)
        ctx.expect((w, h) == (960, 1280),
                   f"после обрезки ожидался размер 960x1280, получен {w}x{h}")
        ctx.expect('"crop"' in log,
                   f"в transform_log нет шага crop: {log[:160]!r}")

        # 5-6. Убрать пыль / очистить фон: шаги в журнале, размер не меняем.
        ctx.page.click('[data-testid="btn-op-remove-dust"]')
        ctx.no_errors()
        _, _, log = _publish_row(ctx)
        ctx.expect('"remove_dust"' in log,
                   f"в transform_log нет шага remove_dust: {log[:160]!r}")
        ctx.page.click('[data-testid="btn-op-clean-bg"]')
        ctx.no_errors()
        _, _, log = _publish_row(ctx)
        ctx.expect('"clean_background"' in log,
                   f"в transform_log нет шага clean_background: {log[:160]!r}")
        ctx.shot("r05_ops_applied")

        # 7. Сделать обложкой: attrs.cover_photo_id + пометка в UI.
        disp_id = _publish_id(ctx)
        ctx.page.click('[data-testid="btn-op-cover"]')
        ctx.no_errors()
        row = ctx.db("SELECT attrs->>'cover_photo_id' FROM item WHERE code=%s",
                     (OPS_ITEM,))
        ctx.expect(row[0][0] == disp_id,
                   f"attrs.cover_photo_id не установлен: {row[0][0]!r}")
        mark = ctx.page.query_selector('[data-testid="photo-cover-mark"]')
        ctx.expect(mark is not None, "карточка: нет пометки «Обложка»")

        # 8. Пометка дефекта: роль DEFECT, ярлык, чистящие кнопки скрыты,
        #    сервер отказывает чисткой (400) — заявленный дефект
        #    остаётся неретушированным.
        ctx.page.click('[data-testid="btn-op-defect"]')
        ctx.no_errors()
        row = ctx.db("SELECT role FROM item_photo WHERE id=%s", (disp_id,))
        ctx.expect(row[0][0] == "DEFECT",
                   f"после пометки дефекта роль {row[0][0]!r}, ожидается DEFECT")
        tag = ctx.page.evaluate(
            "() => { const e = document.getElementById('slider-role-tag');"
            " return e ? e.textContent : ''; }"
        )
        ctx.expect("Дефект" in tag,
                   f"ярлык роли на карточке не «Дефект»: {tag!r}")
        hidden = ctx.page.evaluate(
            "() => { const e = document.querySelector("
            "'[data-testid=\"btn-op-remove-dust\"]');"
            " return !e || !e.offsetParent; }"
        )
        ctx.expect(hidden, "для фото дефекта чистящая кнопка видна в UI")
        resp = ctx.page.request.post(
            f"{ctx.base}/ui/items/{OPS_ITEM}/photos/{disp_id}/remove-dust"
        )
        ctx.expect(resp.status == 400,
                   f"чистка фото дефекта: ожидался 400, получен {resp.status}")
        # Снять пометку: роль возвращается в PUBLISH.
        ctx.page.click('[data-testid="btn-op-defect"]')
        ctx.no_errors()
        row = ctx.db("SELECT role FROM item_photo WHERE id=%s", (disp_id,))
        ctx.expect(row[0][0] == "PUBLISH",
                   f"после снятия пометки роль {row[0][0]!r}, ожидается PUBLISH")
        ctx.shot("r05_defect_guard")

        # 9. Оригинал неизменён: sha256 строки и байты файла.
        orig2 = ctx.db(
            "SELECT sha256 FROM item_photo WHERE item_code=%s AND role='ORIGINAL'",
            (OPS_ITEM,),
        )
        ctx.expect(orig2[0][0] == orig_sha,
                   "sha256 строки ORIGINAL изменился — оригинал изменён")
        ctx.expect(_sha256_file(orig_path) == orig_file_hash,
                   "байты файла ORIGINAL изменились — оригинал изменён")
        ctx.shot("r05_original_intact")
    finally:
        _cleanup_ops_fixture(ctx)


def _make_upload_fixtures() -> tuple[Path, Path]:
    """Тёмный JPEG с EXIF orientation 6 и яркий нормальный JPEG."""
    FIX_DIR.mkdir(parents=True, exist_ok=True)
    dark = Image.new("RGB", (800, 600), (18, 20, 16))
    d = ImageDraw.Draw(dark)
    d.ellipse((100, 100, 700, 500), fill=(32, 30, 26))
    d.rectangle((300, 250, 500, 350), fill=(40, 38, 34))
    exif = dark.getexif()
    exif[0x0112] = 6
    dark_path = FIX_DIR / "v6_dark.jpg"
    dark.save(dark_path, exif=exif.tobytes())

    normal = Image.new("RGB", (1200, 900), (235, 240, 245))
    d2 = ImageDraw.Draw(normal)
    for x in range(0, 1200, 40):
        d2.line((x, 0, x, 900), fill=(205 + (x // 40) % 35, 214, 222))
    d2.ellipse((200, 200, 900, 700), fill=(250, 220, 90))
    d2.rectangle((100, 700, 400, 850), fill=(90, 140, 200))
    normal_path = FIX_DIR / "v6_normal.jpg"
    normal.save(normal_path)
    return dark_path, normal_path


def _variants_of(ctx, item_code: str) -> list[tuple]:
    return ctx.db(
        "SELECT master_id, marketplace_key, role, sha256 FROM media_variant "
        "WHERE stale=false AND master_id IN "
        "(SELECT id FROM media_master WHERE original_id IN "
        "(SELECT id FROM media_original WHERE item_code=%s)) "
        "ORDER BY master_id, marketplace_key",
        (item_code,),
    )


def p_r06(ctx) -> None:
    """Подсказки после загрузки; догенерация вариантов; пометка AI."""
    orig_attrs: str | None = None
    try:
        row = ctx.db("SELECT attrs::text FROM item WHERE code=%s", (HINTS_ITEM,))
        ctx.expect(bool(row), f"предмет {HINTS_ITEM} отсутствует в копии")
        orig_attrs = row[0][0]

        # A. Загрузка: подсказки «тёмный» + «повёрнуто» у тёмного EXIF-6,
        #    у яркого — ни тех, ни других.
        ctx.nav(f"/ui/items/{HINTS_ITEM}")
        ctx.no_errors()
        dark_path, normal_path = _make_upload_fixtures()
        ctx.page.set_input_files(
            '#photo-file-input', [str(dark_path), str(normal_path)]
        )
        ctx.page.click('[data-testid="btn-submit-upload"]')
        ctx.no_errors()
        hints = _hint_text(ctx)
        ctx.expect("тёмный" in hints,
                   f"нет подсказки «тёмный» для тёмного снимка: {hints[:200]!r}")
        ctx.expect("повёрнуто" in hints,
                   f"нет подсказки «повёрнуто» для EXIF-повёрнутого снимка: {hints[:200]!r}")
        ctx.shot("r06_quality_hints")
        verdicts = [
            r[0]
            for r in ctx.db(
                "SELECT vlm_verdict::text FROM item_photo "
                "WHERE item_code=%s AND role='ORIGINAL'",
                (HINTS_ITEM,),
            )
        ]
        ctx.expect(len(verdicts) == 2,
                   f"ожидались 2 загруженных фото, найдено {len(verdicts)}")
        flagged = [v for v in verdicts if "тёмный" in v and "повёрнуто" in v]
        clean = [v for v in verdicts if "тёмный" not in v and "повёрнуто" not in v]
        ctx.expect(len(flagged) == 1,
                   f"подсказки должны быть ровно у одного фото: {verdicts!r}")
        ctx.expect(len(clean) == 1,
                   "у яркого нормального снимка подсказки «тёмный/повёрнуто»")

        # B. Догенерация: обрезка первого фото меняет мастер; после
        #    «Догенерировать» варианты всех трёх площадок пересобраны.
        before = _variants_of(ctx, HINTS_ITEM)
        ctx.expect(len(before) == 6,
                   f"ожидалось 6 вариантов (2 цепочки x 3 площадки), найдено {len(before)}")
        before_shas = {r[3] for r in before}
        ctx.page.click('[data-testid="btn-op-crop"]')
        ctx.no_errors()
        ctx.page.click('[data-testid="btn-regen-variants"]')
        ctx.no_errors()
        after = _variants_of(ctx, HINTS_ITEM)
        ctx.expect(len(after) == 6,
                   f"после догенерации ожидалось 6 вариантов, найдено {len(after)}")
        mps = {r[1] for r in after}
        ctx.expect(mps == {"bazos_cz", "sbazar_cz", "aukro_cz"},
                   f"варианты должны покрывать 3 площадки: {mps}")
        ctx.expect({r[3] for r in after} != before_shas,
                   "после догенерации sha вариантов не изменились — не пересобраны")
        ctx.shot("r06_regen")

        # C. Генеративно изменённое фото помечено в интерфейсе.
        pub = ctx.db(
            "SELECT id FROM item_photo WHERE item_code=%s AND role='PUBLISH' "
            "ORDER BY created_at LIMIT 1",
            (HINTS_ITEM,),
        )
        ctx.expect(len(pub) == 1, "у 106 нет публикуемой версии для пометки AI")
        ctx.db(
            "UPDATE item_photo SET ai_generated=true, updated_at=NOW() "
            "WHERE id=%s",
            (pub[0][0],),
        )
        ctx.nav(f"/ui/items/{HINTS_ITEM}")
        ctx.no_errors()
        marks = ctx.page.query_selector_all('[data-testid="photo-ai-mark"]')
        ctx.expect(len(marks) == 1,
                   f"ожидалась 1 пометка AI, найдено {len(marks)}")
        ctx.text_has(marks[0].inner_text(), "AI", "текст пометки AI")
        ctx.shot("r06_ai_mark")
    finally:
        _cleanup_item_media(ctx, HINTS_ITEM)
        ctx.db("DELETE FROM item_photo WHERE item_code=%s", (HINTS_ITEM,))
        shutil.rmtree(MEDIA_ROOT / HINTS_ITEM, ignore_errors=True)
        if orig_attrs is not None:
            ctx.db(
                "UPDATE item SET attrs=%s::jsonb, updated_at=NOW() WHERE code=%s",
                (orig_attrs, HINTS_ITEM),
            )
        shutil.rmtree(FIX_DIR, ignore_errors=True)


CHECKS = [
    ("P-R05", "Изображения: пер-фото операции, оригинал неизменен, обложка, "
              "дефект-пометка и guard честности (400 на чистку дефекта)", p_r05),
    ("P-R06", "Изображения: подсказки по качеству после загрузки, "
              "догенерация вариантов под площадки, пометка AI", p_r06),
]
