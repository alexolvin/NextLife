import pytest

from nextlife.text.legal import (
    InvalidLegalDisclaimerError,
    MissingLegalConfigError,
    render_legal_disclaimer,
    validate_legal_disclaimer,
)


def test_load_and_render_cz_legal_disclaimer():
    rendered = render_legal_disclaimer("cz")
    assert "soukromý prodej" in rendered
    assert "1916" in rendered
    assert "14 dnů" in rendered
    assert "UPOZORNĚNÍ:" in rendered


def test_missing_legal_config_raises_error(tmp_path, monkeypatch):
    import nextlife.text.legal as legal_mod
    monkeypatch.setattr(legal_mod, "CONFIG_ROOT", tmp_path)

    with pytest.raises(MissingLegalConfigError, match="Legal config file missing"):
        render_legal_disclaimer("cz")


def test_validate_legal_disclaimer_success():
    valid_text = (
        "Prodám grafickou kartu RTX 3090.\n\n"
        "UPOZORNĚNÍ:\n"
        "- Jedná se o soukromý prodej.\n"
        "- Dle § 1916 OZ se vylučuje odpovědnost.\n"
        "- Bez nároku na vrácení do 14 dnů.\n"
    )
    assert validate_legal_disclaimer(valid_text, "cz") is True


def test_validate_legal_disclaimer_missing_clause_raises():
    # Missing § 1916
    invalid_text = (
        "Prodám grafickou kartu RTX 3090.\n"
        "Jedná se o soukromý prodej, bez možnosti vrácení do 14 dnů."
    )
    with pytest.raises(InvalidLegalDisclaimerError, match="1916"):
        validate_legal_disclaimer(invalid_text, "cz")

    # Missing soukromý prodej
    invalid_text_2 = (
        "Prodám grafickou kartu. Dle § 1916 OZ, bez vrácení do 14 dnů."
    )
    with pytest.raises(InvalidLegalDisclaimerError, match="soukromý prodej"):
        validate_legal_disclaimer(invalid_text_2, "cz")
