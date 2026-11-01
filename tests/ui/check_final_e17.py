"""TZ-NextLife-UI-N2 финальная приёмка — проверка P-E17.

P-E17   удаление несуществующего фото даёт сообщение «не найдено»,
       а не ложный баннер «Фотография удалена» (правило честности
       Э-2: баннер «удалено» — только после подтверждённого
       результата). Реальное удаление продолжает работать:
       баннер «Фотография удалена», строки цепочки и файлы
       удалены, аудит PHOTO_DELETED.

Окружение (Э-10): копия nextlife_check, чек-сервер 127.0.0.1:8083.
Определённость:
- случай «не найдено» — на предмете 747 (реальная цепочка, не
  изменяется): POST с несуществующим id фото (uuid4), затем переход
  по адресу, который вернул именно сервер (redirect URL из ответа),
  — проверка не подставляет notice сам;
- реальное удаление — на фикстуре 917 (одна строка ORIGINAL, файл —
  копия ORIGINAL 747 в /tmp/nlcheck), реальный клик по крестику
  (confirm()-диалог принят через page.on("dialog"));
- фикстура 917 удаляется в finally, 747 остаётся нетронутым.
"""

from __future__ import annotations

import hashlib
import shutil
import uuid
from pathlib import Path

NOTFOUND_ITEM = "747"  # случай «не найдено»: не изменяется
DEL_ITEM = "917"       # фикстура: реальное удаление (final — cleanup)
SOURCE_ITEM = "747"    # донор файла (только чтение)

MEDIA_ROOT = Path("/tmp/nlcheck/data/media")


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(8192):
            h.update(chunk)
    return h.hexdigest()


def _setup_del_fixture(ctx) -> Path:
    """Предмет 917: одна строка ORIGINAL (копия файла ORIGINAL 747)."""
    rows = ctx.db(
        "SELECT width, height, path FROM item_photo "
        "WHERE item_code=%s AND role='ORIGINAL'",
        (SOURCE_ITEM,),
    )
    ctx.expect(len(rows) == 1,
               f"фикстура: у {SOURCE_ITEM} ожидалась 1 строка ORIGINAL, "
               f"найдено {len(rows)}")
    w, h, src = rows[0]
    dst = MEDIA_ROOT / DEL_ITEM / "originals" / f"e17_orig_{uuid.uuid4().hex[:8]}.jpg"
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    ctx.db(
        "INSERT INTO item (code, kind, title, category_key, attrs, condition_key, "
        "defects, status, created_at, updated_at) "
        "VALUES (%s, 'SELL', 'UI Check E17 Delete 917', 'electronics', '{}'::jsonb, "
        "'used', '[]'::jsonb, 'DRAFT', NOW(), NOW())",
        (DEL_ITEM,),
    )
    ctx.db(
        "INSERT INTO item_photo (id, item_code, role, path, sha256, width, height, "
        "derived_from_id, transform_log, ai_generated, vlm_verdict, "
        "created_at, updated_at) "
        "VALUES (%s, %s, 'ORIGINAL', %s, %s, %s, %s, NULL, '[]'::jsonb, false, "
        "'{}'::jsonb, NOW(), NOW())",
        (str(uuid.uuid4()), DEL_ITEM, str(dst), _sha256_file(dst), w, h),
    )
    return dst


def _cleanup_del_fixture(ctx) -> None:
    """Файлы и строки фото уже удалены операцией; остаются item + media-уровни."""
    ctx.db(
        "DELETE FROM media_variant WHERE master_id IN "
        "(SELECT id FROM media_master WHERE original_id IN "
        "(SELECT id FROM media_original WHERE item_code=%s))",
        (DEL_ITEM,),
    )
    ctx.db(
        "DELETE FROM media_master WHERE original_id IN "
        "(SELECT id FROM media_original WHERE item_code=%s)",
        (DEL_ITEM,),
    )
    ctx.db("DELETE FROM media_original WHERE item_code=%s", (DEL_ITEM,))
    ctx.db("DELETE FROM item_photo WHERE item_code=%s", (DEL_ITEM,))
    ctx.db("DELETE FROM item WHERE code=%s", (DEL_ITEM,))
    shutil.rmtree(MEDIA_ROOT / DEL_ITEM, ignore_errors=True)


def p_e17(ctx) -> None:
    """Не найдено → «не найдено»; реальное удаление → «удалено» + аудит."""
    fixture_file = _setup_del_fixture(ctx)
    # Крестик формы удаления оборачивён confirm() — принять диалог.
    ctx.page.on("dialog", lambda d: d.accept())
    try:
        # 1. Несуществующее фото у 747: сервер вернёт redirect; переходим
        #    по тому адресу, который вернул именно сервер, и проверяем
        #    баннер: «не найдено» и никакого ложного «Фотография удалена».
        fake_id = str(uuid.uuid4())
        resp = ctx.page.request.post(
            f"{ctx.base}/ui/items/{NOTFOUND_ITEM}/photos/{fake_id}/delete"
        )
        ctx.expect(resp.status == 200,
                   f"redirect при несуществующем фото: ожидался 200, "
                   f"получен {resp.status}")
        body = ctx.nav(resp.url)
        ctx.no_errors()
        ctx.text_has(body, "не найдено",
                     "удаление несуществующего фото: нет сообщения «не найдено»")
        ctx.text_not_has(body, "Фотография удалена",
                         "удаление несуществующего фото: ложный баннер "
                         "«Фотография удалена»")
        ctx.shot("e17_not_found")
        rows = ctx.db(
            "SELECT count(*) FROM item_photo WHERE item_code=%s",
            (NOTFOUND_ITEM,),
        )
        ctx.expect(rows[0][0] == 3,
                   f"после неудачного удаления {NOTFOUND_ITEM} должен "
                   f"остаться нетронутым: {rows[0][0]} фото")

        # 2. Реальное удаление продолжает работать: фикстура 917,
        #    реальный клик по крестику на миниатюре. Аудит допрогонный:
        #    таблица append-only, считаем прирост ровно на одну строку.
        audit_before = ctx.db(
            "SELECT count(*) FROM audit_event "
            "WHERE action='PHOTO_DELETED' AND entity_id=%s",
            (DEL_ITEM,),
        )[0][0]
        ctx.nav(f"/ui/items/{DEL_ITEM}")
        ctx.no_errors()
        ctx.text_has(ctx.body_text(), "UI Check E17 Delete 917",
                     "карточка фикстуры 917 не открыта")
        ctx.page.click('[data-testid="btn-delete-photo-0"]')
        ctx.no_errors()
        body = ctx.body_text()
        ctx.text_has(body, "Фотография удалена",
                     "реальное удаление: нет баннера «Фотография удалена»")
        ctx.shot("e17_deleted")
        rows = ctx.db(
            "SELECT count(*) FROM item_photo WHERE item_code=%s", (DEL_ITEM,)
        )
        ctx.expect(rows[0][0] == 0,
                   f"после реального удаления строки фото должны исчезнуть: "
                   f"осталось {rows[0][0]}")
        ctx.expect(not fixture_file.exists(),
                   "после реального удаления файл фото остался на диске")
        audit = ctx.db(
            "SELECT count(*) FROM audit_event "
            "WHERE action='PHOTO_DELETED' AND entity_id=%s",
            (DEL_ITEM,),
        )
        ctx.expect(audit[0][0] == audit_before + 1,
                   f"аудит PHOTO_DELETED у {DEL_ITEM}: {audit[0][0]}, "
                   f"ожидался прирост до {audit_before + 1}")
    finally:
        _cleanup_del_fixture(ctx)


CHECKS = [
    ("P-E17", "Удаление несуществующего фото даёт «не найдено», а не ложный "
              "«удалено» (Э-2); реальное удаление работает и аудится", p_e17),
]
