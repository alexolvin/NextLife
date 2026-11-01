from nextlife.text.usage import format_usage_profile_cs


def test_gpu_usage_profile_czech():
    """DoD: For GPU indicates 'domácí PC / netěženo (žádný mining)' in Czech."""
    res = format_usage_profile_cs(
        category_key="gpu_hardware",
        mining=False,
    )
    assert "domácím PC" in res
    assert "žádný mining" in res or "netěžilo" in res


def test_phone_battery_capacity_czech():
    """DoD: For phone indicates battery health ('kondice baterie')."""
    res = format_usage_profile_cs(
        category_key="smartphones",
        battery_health_pct=87,
    )
    assert "Kondice baterie: 87%" in res


def test_combined_owner_declaration():
    """Verify owner personal declaration is appended naturally."""
    res = format_usage_profile_cs(
        category_key="smartphones",
        battery_health_pct=92,
        owner_usage_declaration="Nošeno vždy v pouzdře a s nalepeným sklem.",
    )
    assert "Kondice baterie: 92%" in res
    assert "Nošeno vždy v pouzdře" in res
