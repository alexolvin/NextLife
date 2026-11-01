import subprocess

from nextlife.templates.renderer import render_template


def test_zero_html_tags_in_src():
    """DoD: grep across src/ for HTML tags must yield zero matches.

    Исключение vendor/: TZ N3-01 обязывает класть сторонние minified-бандлы
    (Alpine.js, HTMX) в src/nextlife/api/static/vendor/ — это чужой
    минифицированный JS, а не HTML в приложении.
    """
    cmd = (
        "grep -rnE --exclude-dir=__pycache__ --exclude-dir=vendor "
        "'<(div|span|html|body|p|table|h[1-6]|form)' src/"
    )
    proc = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    assert proc.stdout.strip() == "", f"Found forbidden HTML tags in src/: {proc.stdout}"


def test_render_listing_with_all_sections():
    """DoD: Test renders listing template with all sections populated."""
    context = {
        "title": "Grafická karta ASUS TUF Gaming RTX 3090 24GB",
        "condition_headline": "Stav: Perfektní, plně funkční (Třída A)",
        "description": "Karta používaná pouze v domácím PC pro občasné hraní v dobře větrané skříni. Kompletní vyčištění.",
        "defects": [
            {
                "description_cs": "Drobné mikroskopické oděrky na zadním backplate",
                "photo_id": "photo_002",
            }
        ],
        "completeness": [
            "Grafická karta ASUS RTX 3090",
            "Původní krabice s pěnovou výstelkou",
            "Podpěrný držák (GPU support bracket)",
        ],
        "delivery_options": [
            {"summary_text_cs": "Zásilkovna na výdejní místo: 79 Kč (převodem 15 079 Kč)"},
            {"summary_text_cs": "Balíkovna do boxu: 65 Kč (převodem 15 065 Kč)"},
            {"summary_text_cs": "Osobní odběr Praha (metro Dejvická): Zdarma"},
        ],
        "legal_disclaimer": "Soukromý prodej použitého zboží bez možnosti odpočtu DPH dle § 900 občanského zákoníku.",
    }

    rendered = render_template("base.jinja2", context, subpath="listing")

    # Verify all sections are present in rendered text
    assert "ASUS TUF Gaming RTX 3090" in rendered
    assert "STAV A ZNÁMKY POUŽÍVÁNÍ:" in rendered
    assert "Drobné mikroskopické oděrky" in rendered
    assert "OBSAH BALENÍ:" in rendered
    assert "Podpěrný držák" in rendered
    assert "DOPRAVA A PŘEVZETÍ:" in rendered
    assert "Zásilkovna na výdejní místo: 79 Kč" in rendered
    assert "Soukromý prodej použitého zboží" in rendered
