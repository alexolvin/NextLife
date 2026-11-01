import pytest

from nextlife.text.czech import (
    CzechCalqueDetectedError,
    load_czech_phrases_config,
    validate_czech_listing_text,
)


def test_czech_dictionary_contains_required_phrases():
    """DoD: Dictionary contains idioms specified in TZ section V.2."""
    cfg = load_czech_phrases_config()
    phrases = cfg.get("phrases", {})

    # Check key phrases from TZ
    assert phrases["condition"]["like_new"] == "stav nového kusu"
    assert phrases["condition"]["fully_functional"] == "plně funkční, bez vad"
    assert phrases["packaging"]["original_box"] == "původní balení součástí"
    assert "osobní předání Praha" in phrases["delivery"]["personal_prague"]
    assert "přechodu na jiný model" in phrases["reason"]["upgrade"]


def test_russian_calque_fails_validation():
    """DoD: Text with Russian calque ('в отличном состоянии', 'полный комплект') fails validation."""
    bad_text_1 = "Prodám RTX 3090, karta je в отличном состоянии, bez vad."
    with pytest.raises(CzechCalqueDetectedError) as exc_1:
        validate_czech_listing_text(bad_text_1)
    assert "в отличном состоянии" in str(exc_1.value)

    bad_text_2 = "Grafická karta, полный комплект, osobní předání Praha."
    with pytest.raises(CzechCalqueDetectedError) as exc_2:
        validate_czech_listing_text(bad_text_2)
    assert "полный комплект" in str(exc_2.value)


def test_english_calque_fails_validation():
    """DoD: Text with English calque ('in great condition', 'mint condition') fails validation."""
    bad_text = "Apple iPhone 14 Pro, in great condition, původní balení."
    with pytest.raises(CzechCalqueDetectedError) as exc:
        validate_czech_listing_text(bad_text)
    assert "in great condition" in str(exc.value)


def test_authentic_czech_text_passes_validation():
    """Verify authentic Czech text composed of idiomatic phrases passes cleanly."""
    valid_text = (
        "Prodám grafickou kartu ASUS RTX 3090. Stav nového kusu, plně funkční, bez vad. "
        "Původní balení součástí. Osobní předání Praha / možnost zaslání přes Zásilkovnu. "
        "Prodávám z důvodu přechodu na jiný model."
    )
    # Must not raise
    validate_czech_listing_text(valid_text)
