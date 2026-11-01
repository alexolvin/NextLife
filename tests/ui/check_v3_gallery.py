"""TZ-NextLife-UI-N2 V3 (Галерея) — проверки P-E04, P-E04b, P-E05, P-E08.

P-E04   число миниатюр в галерее, ленте и черновике равно числу логических
       фото (одна на цепочку, Э-6), а не числу строк таблицы item_photo;
P-E04b  главное фото слайдера — публикуемая версия цепочки, а не внутренняя;
P-E05   битое изображение помечено на экране и не даёт 404 в сети;
P-E08   предпросмотр опубликованного лота показывает реальные варианты либо
       явную надпись об отсутствии фото в карточке.
"""

from __future__ import annotations

from pathlib import Path

from scripts.checklib import CheckFail

BROKEN_ID = "v3-broken-photo"
# Dead fixture path: repo-relative (parents[2] == repo root) so it carries no
# absolute home path; the media-relative tail is what the URL builder uses.
BROKEN_PATH = str(Path(__file__).resolve().parents[2] / "data" / "media" / "106" / "originals" / "v3_missing_photo.jpg")
BROKEN_SHA = "ab" * 32


def _logical_count(ctx, code: str) -> int:
    """Число логических фото = число цепочек (корней) в копии БД."""
    rows = ctx.db(
        "SELECT count(*) FROM item_photo WHERE item_code=%s AND derived_from_id IS NULL",
        (code,),
    )
    return rows[0][0]


def _photo_url(path: str) -> str:
    """То же правило, что resolve_photo_url в src/nextlife/api/routes.py."""
    parts = Path(path).parts
    if "media" in parts:
        return "/" + "/".join(parts[parts.index("media"):])
    return "/media/" + Path(path).name


def p_e04(ctx) -> None:
    """Миниатюры галереи/ленты/черновика == число логических фото."""
    # Фикстура 463: >= 20 строк таблицы (цепочки из 3 версий) = 7 логических фото.
    total = ctx.db("SELECT count(*) FROM item_photo WHERE item_code='463'")[0][0]
    logical = _logical_count(ctx, "463")
    ctx.expect(logical == 7, f"фикстура сломана: логических фото у 463 = {logical} (ожидалось 7)")
    ctx.expect(total >= 20, f"фикстура сломана: строк item_photo у 463 = {total} (ожидалось >= 20)")

    # 1) Галерея карточки 463: 7 миниатюр, а не 20 строк конвейера.
    ctx.nav("/ui/items/463")
    ctx.no_errors()
    thumbs = ctx.page.evaluate("() => document.querySelectorAll('[data-testid^=\"thumb-item-\"]').length")
    ctx.expect(thumbs == logical,
               f"галерея 463: миниатюр {thumbs}, логических фото {logical} (строк в таблице {total})")
    ctx.text_has(ctx.body_text(), "Фотогалерея (7)", "галерея 463: счётчик в заголовке")
    counter = ctx.page.evaluate(
        "() => { const e = document.getElementById('slider-counter'); return e ? e.innerText.trim() : ''; }"
    )
    ctx.expect(counter == "1 / 7", f"слайдер 463: счётчик {counter!r} (ожидалось '1 / 7')")
    ctx.shot("e04_gallery_463")

    # 2) Галерея 747: одна цепочка из трёх версий = одна миниатюра.
    ctx.nav("/ui/items/747")
    ctx.no_errors()
    thumbs747 = ctx.page.evaluate("() => document.querySelectorAll('[data-testid^=\"thumb-item-\"]').length")
    logical747 = _logical_count(ctx, "747")
    ctx.expect(thumbs747 == logical747 == 1,
               f"галерея 747: миниатюр {thumbs747}, логических фото {logical747} (ожидалось 1 на цепочку)")

    # 3) Лента: счётчик фото у карточек == логическим.
    ctx.nav("/ui/items")
    ctx.no_errors()
    card463 = ctx.page.evaluate(
        "() => { const e = document.querySelector('#item-463'); return e ? e.innerText : null; }"
    )
    ctx.expect(card463 is not None, "лента: карточка #463 не найдена")
    ctx.expect(f"📸 Фото: {logical}" in card463,
               f"лента 463: счётчик фото != {logical}: {card463[:200]!r}")
    card747 = ctx.page.evaluate(
        "() => { const e = document.querySelector('#item-747'); return e ? e.innerText : null; }"
    )
    ctx.expect(card747 is not None, "лента: карточка #747 не найдена")
    ctx.expect("📸 Фото: 1" in card747, f"лента 747: счётчик фото != 1: {card747[:200]!r}")
    ctx.shot("e04_feed_counts")

    # 4) Черновик 463: полоса фото == логическим.
    ctx.nav("/ui/draft/463")
    ctx.no_errors()
    strip = ctx.page.evaluate("() => document.querySelectorAll('.thumb-strip > *').length")
    ctx.expect(strip == logical,
               f"черновик 463: элементов в полосе фото {strip}, логических фото {logical}")
    ctx.shot("e04_draft_463")


def p_e04b(ctx) -> None:
    """Главное фото слайдера — публикуемая версия цепочки, а не внутренняя."""
    # Фикстура 463: обложка указывает на версию внутренней цепочки из трёх ступеней.
    cover = ctx.db("SELECT attrs->>'cover_photo_id' FROM item WHERE code='463'")[0][0]
    ctx.expect(bool(cover), "фикстура сломана: у 463 нет обложки (attrs.cover_photo_id)")
    # Публикуемая версия цепочки обложки (вверх до корня, вниз до PUBLISH).
    pub = ctx.db(
        "WITH RECURSIVE up AS ("
        "  SELECT id, derived_from_id FROM item_photo WHERE item_code='463' AND id=%s "
        "  UNION ALL "
        "  SELECT p.id, p.derived_from_id FROM item_photo p JOIN up ON p.id = up.derived_from_id"
        "), root AS (SELECT id FROM up WHERE derived_from_id IS NULL), "
        "down AS ("
        "  SELECT p.id, p.role, p.path FROM item_photo p JOIN root r ON p.id = r.id "
        "  UNION ALL "
        "  SELECT p.id, p.role, p.path FROM item_photo p JOIN down d ON p.derived_from_id = d.id"
        ") SELECT path FROM down WHERE role='PUBLISH' LIMIT 1",
        (cover,),
    )
    if not pub:
        raise CheckFail(
            "главное фото — внутренняя версия: цепочка обложки 463 не доведена до "
            "публикуемой (PUBLISH) версии (продавец видит обработанный промежуточный кадр)"
        )
    expected = _photo_url(pub[0][0])

    ctx.nav("/ui/items/463")
    ctx.no_errors()
    main_src = ctx.page.evaluate(
        "() => { const e = document.getElementById('slider-main-img'); return e ? e.getAttribute('src') : null; }"
    )
    ctx.expect(main_src == expected,
               f"главное фото слайдера 463: {main_src!r} (ожидалась публикуемая версия {expected!r})")
    ctx.expect("sanitized" not in (main_src or ""),
               f"главное фото слайдера — внутренняя (sanitized) версия: {main_src!r}")
    ctx.shot("e04b_slider_publish")


def p_e05(ctx) -> None:
    """Битое изображение помечено и не даёт 404 в сети (фикстура — строка с мёртвым путём)."""
    # Фикстура: строка item_photo на файл, которого нет на диске медиа-сервера.
    ctx.db("DELETE FROM item_photo WHERE id=%s", (BROKEN_ID,))
    ctx.db(
        "INSERT INTO item_photo (id, item_code, role, path, sha256, width, height, "
        "derived_from_id, transform_log, ai_generated, vlm_verdict, created_at, updated_at) "
        "VALUES (%s, '106', 'ORIGINAL', %s, %s, 100, 100, NULL, '[]', FALSE, '{}', NOW(), NOW())",
        (BROKEN_ID, BROKEN_PATH, BROKEN_SHA),
    )
    try:
        ctx.nav("/ui/items/106")
        ctx.no_errors()  # до правки: <img> на мёртвый путь → 404 в сети
        mark = ctx.page.query_selector('[data-testid="photo-broken-mark"]')
        ctx.expect(mark is not None, "битое фото не помечено в галерее")
        dead_img = ctx.page.evaluate(
            "() => Array.from(document.querySelectorAll('img')).some("
            "i => (i.getAttribute('src') || '').includes('v3_missing_photo'))"
        )
        ctx.expect(not dead_img, "на экране есть <img>, указывающий на отсутствующий файл")
        ctx.shot("e05_broken_mark")
        # Лента: карточка 106 без 404 на миниатюре.
        ctx.nav("/ui/items")
        ctx.no_errors()
    finally:
        # Строка фото + всё, что конвейер мог ей создать (по sha256 оригинала).
        ctx.db("DELETE FROM item_photo WHERE id=%s", (BROKEN_ID,))
        ctx.db(
            "DELETE FROM media_variant WHERE master_id IN ("
            "  SELECT id FROM media_master WHERE original_id IN ("
            "    SELECT id FROM media_original WHERE item_code='106' AND sha256=%s))",
            (BROKEN_SHA,),
        )
        ctx.db(
            "DELETE FROM media_master WHERE original_id IN ("
            "  SELECT id FROM media_original WHERE item_code='106' AND sha256=%s)",
            (BROKEN_SHA,),
        )
        ctx.db("DELETE FROM media_original WHERE item_code='106' AND sha256=%s", (BROKEN_SHA,))


def p_e08(ctx) -> None:
    """Превью опубликованного лота: реальные варианты или явная надпись об отсутствии."""
    # 1) Опубликованный лот 348: у него есть фото → превью показывает реальные варианты.
    ctx.nav("/ui/items/348/preview/bazos_cz")
    ctx.no_errors()
    variants = ctx.page.evaluate("() => document.querySelectorAll('[data-testid^=\"variant-image-\"]').length")
    ctx.expect(variants > 0,
               "превью опубликованного лота 348: вариантов 0 («0 фото» без объяснения)")
    ctx.shot("e08_published_variants")

    # 2) Лот 106 без фото: явная надпись об отсутствии в карточке.
    ctx.nav("/ui/items/106/preview/bazos_cz")
    ctx.no_errors()
    note = ctx.page.query_selector('[data-testid="preview-no-media-note"]')
    ctx.expect(note is not None, "превью лота без фото: нет явной надписи об отсутствии")
    ctx.shot("e08_no_media_note")


CHECKS = [
    ("P-E04", "миниатюры галереи/ленты/черновика == число логических фото (не строк таблицы)", p_e04),
    ("P-E04b", "главное фото слайдера — публикуемая версия, а не внутренняя", p_e04b),
    ("P-E05", "битое изображение помечено и не даёт 404 в сети", p_e05),
    ("P-E08", "превью опубликованного лота: реальные варианты либо явная надпись об отсутствии", p_e08),
]
