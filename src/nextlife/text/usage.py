"""Usage profile natural Czech phrasing generator (TZ v2.0 Section II.2, V.2)."""



def format_usage_profile_cs(
    category_key: str,
    owner_usage_declaration: str | None = None,
    battery_health_pct: int | None = None,
    is_gpu: bool = False,
    mining: bool = False,
) -> str:
    """Format category-specific owner usage profile in natural idiomatic Czech.

    DoD:
    - For GPU: explicitly includes 'používáno v domácím PC / netěženo (žádný mining)'.
    - For phones/laptops: explicitly includes battery condition ('kondice baterie: X%').
    """
    cat_lower = category_key.lower()
    phrases: list[str] = []

    # 1. GPU specific phrasing
    if is_gpu or "gpu" in cat_lower or "grafick" in cat_lower:
        if not mining:
            phrases.append("Používáno výhradně v běžném domácím PC pro občasné hraní, na kartě se nikdy netěžilo (žádný mining).")
        else:
            phrases.append("Karta byla v minulosti využita pro mining v kontrolovaných podmínkách.")

    # 2. Battery-operated electronics (phone, laptop, tablet)
    if battery_health_pct is not None or "phone" in cat_lower or "mobil" in cat_lower or "telefon" in cat_lower:
        if battery_health_pct is not None:
            phrases.append(f"Kondice baterie: {battery_health_pct}%.")

    # 3. Additional owner declaration integration
    if owner_usage_declaration and owner_usage_declaration.strip():
        decl = owner_usage_declaration.strip()
        # Ensure it doesn't duplicate GPU/battery if already translated
        if "mining" not in decl.lower() and "baterie" not in decl.lower():
            phrases.append(f"Informace od majitele: {decl}")

    return " ".join(phrases)
