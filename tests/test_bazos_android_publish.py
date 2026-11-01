"""Bazoš Android publish: screen decisions only. The phone is not leased."""

from pathlib import Path


def _node(text: str = "", desc: str = "", bounds: str = "", cls: str = "android.widget.TextView", rid: str = "") -> str:
    return (
        f'<node text="{text}" content-desc="{desc}" resource-id="{rid}" '
        f'class="{cls}" bounds="{bounds}" />'
    )


def test_section_labels_cover_every_tree_key():
    from nextlife.core.config import load_config
    from nextlife.services.bazos_android_publish import section_label

    tops = [node["top"] for node in load_config().marketplaces["bazos_cz"].category_tree]
    assert len(tops) == 20
    labels = [section_label(top) for top in tops]
    assert all(labels)
    assert section_label("pc") == "PC"
    assert section_label("mobil") == "Mobily"
    assert section_label("sluzby") == "Služby"
    assert section_label("ostatni") == "Ostatní"
    assert section_label("not-a-section") is None


def test_rubric_is_exact_and_rejects_a_model_name():
    from nextlife.services.bazos_android_publish import rubric_label

    assert rubric_label("pc", "Notebooky") == "Notebooky"
    assert rubric_label("pc", "notebooky") == "Notebooky"
    assert rubric_label("foto", "Objektivy") == "Objektivy"
    assert rubric_label("mobil", "Apple") == "Apple"
    assert rubric_label("mobil", "iphone") is None
    assert rubric_label("pc", "Ostatní") == "Ostatní"


def test_screen_kind_and_field_roles_from_the_mapped_form():
    from nextlife.services.bazos_android_publish import field_roles, screen_kind

    form = "<hierarchy>" + "".join(
        [
            _node("Add photos", bounds="[496,411][695,458]"),
            _node("Start voice recording", bounds="[926,859][1058,991]"),
            _node("Location (ZIP)", bounds="[973,1121][1036,1168]"),
            _node("Select section", bounds="[973,1331][1036,1378]"),
            _node("Select category", bounds="[973,1541][1036,1588]"),
            _node("Choose another option", bounds="[731,1797][1036,1929]"),
            _node("Ad submitting", bounds="[44,163][350,221]"),
            _node("", bounds="[44,640][1036,772]", cls="android.widget.EditText"),
            _node("", bounds="[44,860][948,992]", cls="android.widget.EditText"),
            _node("", bounds="[44,1709][1036,1841]", cls="android.widget.EditText"),
        ]
    ) + "</hierarchy>"
    assert screen_kind(form) == "form"
    roles = field_roles(form)
    assert set(roles) == {"title", "description", "price"}
    assert roles["title"]["bounds"][1] == 640
    assert roles["description"]["bounds"][1] == 860
    assert roles["price"]["bounds"][1] == 1709

    assert screen_kind("<hierarchy>" + _node("All sections") + _node("Sections") + "</hierarchy>") == "sections"
    assert screen_kind("<hierarchy>" + _node("CONTINUE") + "</hierarchy>") == "scam"
    assert screen_kind("<hierarchy></hierarchy>") == "unknown"


def test_new_photo_descs_ignore_photos_already_on_the_phone():
    from nextlife.services.bazos_android_publish import new_photo_descs

    old = "Photo taken on Sep 29, 2026 17:18"
    fresh = "Photo taken on Oct 29, 2026 23:55"
    assert new_photo_descs([old, old], [old, old, fresh]) == [fresh]
    assert new_photo_descs([old], [old]) == []


def test_fresh_photo_boxes_skip_the_unchanged_probe_grid():
    """The probe picker showed the same three rows before and after push_media."""
    from nextlife.services.bazos_android_publish import fresh_photo_boxes

    old = "Photo taken on Sep 29, 2026 17:18"
    grid = [
        (old, (0, 1309, 358, 1667)),
        (old, (361, 1309, 719, 1667)),
        (old, (722, 1309, 1080, 1667)),
    ]
    assert fresh_photo_boxes(grid, grid) == []
    shifted = [
        (old, (0, 400, 358, 758)),
        (old, (361, 400, 719, 758)),
        (old, (722, 400, 1080, 758)),
        (old, (0, 800, 358, 1158)),
        (old, (361, 800, 719, 1158)),
    ]
    assert fresh_photo_boxes(grid, shifted) == []


def test_fresh_photo_boxes_use_bounds_when_the_caption_repeats():
    from nextlife.services.bazos_android_publish import fresh_photo_boxes

    old = "Photo taken on Sep 29, 2026 17:18"
    new = "Photo taken on Oct 31, 2026 12:00"
    before = [
        (old, (0, 1309, 358, 1667)),
        (old, (361, 1309, 719, 1667)),
        (old, (722, 1309, 1080, 1667)),
    ]
    added = (old, (0, 1700, 358, 2058))
    assert fresh_photo_boxes(before, [*before, added]) == [added[1]]
    newcomers = [
        (new, (0, 1700, 358, 2058)),
        (new, (361, 1700, 719, 2058)),
    ]
    assert fresh_photo_boxes(before, [*before, *newcomers]) == [box for _desc, box in newcomers]


def test_gallery_confirm_is_done_not_the_nav_add():
    from nextlife.services.bazos_android_publish import gallery_confirm_label

    xml = "<hierarchy>" + "".join(
        [
            _node("Add", bounds="[510,2190][570,2229]"),
            _node("2 photos or videos selected", bounds="[215,2156][248,2226]"),
            _node("Done", bounds="[864,2166][942,2216]"),
        ]
    ) + "</hierarchy>"
    assert gallery_confirm_label(xml) == "Done"


def test_submit_outcome_does_not_treat_a_closed_form_as_published():
    from nextlife.services.bazos_android_publish import submit_outcome

    form = "<hierarchy>" + "".join(
        [
            _node("Ad submitting", bounds="[44,163][350,221]"),
            _node("Location can't be empty", bounds="[77,1964][446,2014]"),
            _node("ADD AD", bounds="[476,1936][604,1976]"),
        ]
    ) + "</hierarchy>"
    assert submit_outcome(form) == "form_open"
    home = "<hierarchy>" + _node("Ads", rid="cz.ackee.bazos:id/bottom_nav_new_ad") + "</hierarchy>"
    assert submit_outcome(home) == "unrecognized"


def test_zip_choice_is_a_place_not_the_query():
    from nextlife.services.bazos_android_publish import (
        _zip_choice,
        _zip_miss,
        _zip_queries,
        screen_kind,
    )

    assert _zip_queries("999 99") == ["999 99", "99999"]
    miss = "<hierarchy>" + "".join(
        [
            _node("999 99", bounds="[270,127][1025,259]", cls="android.widget.EditText"),
            _node(
                "Your search for 999 99 did not match any results",
                bounds="[40,700][1040,780]",
            ),
            _node("Use current location", bounds="[132,334][480,381]"),
        ]
    ) + "</hierarchy>"
    assert screen_kind(miss) == "zip"
    plain = (
        "<hierarchy>"
        + _node("Your search for 999 99 did not match any results")
        + _node("Back")
        + "</hierarchy>"
    )
    assert screen_kind(plain) == "zip"
    assert _zip_miss(miss)
    assert _zip_choice(miss, "999 99") is None
    hit = "<hierarchy>" + _node("999 99 Praha", bounds="[44,420][900,500]") + "</hierarchy>"
    assert _zip_choice(hit, "999 99") == (44, 420, 900, 500)


def test_discard_label_is_exact():
    from nextlife.services.bazos_android_publish import discard_label

    xml = "<hierarchy>" + _node("Discard") + _node("SAVE") + "</hierarchy>"
    assert discard_label(xml) == "Discard"
    assert discard_label("<hierarchy>" + _node("CANCEL") + "</hierarchy>") is None
    leave = "<hierarchy>" + "".join(
        [
            _node("Leaving ad"),
            _node("If you leave your draft will be removed, are you sure?"),
            _node("CANCEL"),
            _node("LEAVE"),
        ]
    ) + "</hierarchy>"
    assert discard_label(leave) == "LEAVE"


def test_bare_graphics_card_is_not_rejected_as_a_bad_path(monkeypatch):
    """Lot 130 stores «Grafické karty». That must not be the format error."""
    from nextlife.services import bazos_android_publish as mod

    def boom(*_args, **_kwargs):
        raise AssertionError("phone was leased")

    monkeypatch.setattr(mod, "make_phone_broker_transport", boom)
    result = mod.bazos_android_publish_listing(
        title="Karta",
        description="test",
        price="26000",
        category="Grafické karty",
        dry_run=False,
    )
    assert result["ok"] is False
    assert "раздел/подкатегория" not in result["errors"][0]
    assert "без фото" in result["errors"][0]


def test_live_publish_does_not_lease_the_phone(monkeypatch):
    from nextlife.services import bazos_android_publish as mod

    def boom(*_args, **_kwargs):
        raise AssertionError("phone was leased")

    monkeypatch.setattr(mod, "make_phone_broker_transport", boom)
    result = mod.bazos_android_publish_listing(
        title="Objektiv",
        description="test",
        price="100",
        category="pc/Notebooky",
        dry_run=False,
    )
    assert result["ok"] is False
    assert result["channel"] == "android"
    assert "без фото" in result["errors"][0]
    assert "Устранение" in result["errors"][0]


def test_driver_records_no_success_for_an_unmapped_screen():
    source = (
        Path(__file__).resolve().parents[1] / "src/nextlife/services/bazos_android_publish.py"
    ).read_text(encoding="utf-8")
    assert 'steps.append("submitted")' not in source
    assert "submit screen unrecognized" in source
