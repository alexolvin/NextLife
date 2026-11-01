"""Best-effort og:image extraction from a listing page (wizard thumbnails).

The Vinted Android search flow returns links without image URLs (the
uiautomator dump has no card thumbnails), so the wizard fetches each
listing page's og:image. The fetch is streaming and head-only: it reads
just enough of the response to find the og:image tag and stops. Any
failure (403 block, timeout, missing tag) returns "" — the wizard card
shows its placeholder, never a fabricated image.
"""

import re

import httpx

_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

# Read at most this much of the page: og:image lives in <head>, well inside.
_MAX_CHARS = 300_000

# og:image with the content attribute in either order; matches
# og:image:secure_url as well (also a valid image URL).
_OG_IMAGE_PATTERNS = (
    re.compile(r'<meta[^>]+og:image[^>]*content=["\']([^"\']+)["\']', re.IGNORECASE),
    re.compile(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]*og:image', re.IGNORECASE),
)


def fetch_og_image(url: str, timeout_s: float = 6.0) -> str:
    """Return the listing page's og:image URL, or "" when unavailable."""
    if not url:
        return ""
    try:
        with httpx.stream(
            "GET",
            url,
            timeout=timeout_s,
            follow_redirects=True,
            headers={"User-Agent": _BROWSER_UA, "Accept": "text/html"},
        ) as resp:
            if resp.status_code != 200:
                return ""
            buf = ""
            for chunk in resp.iter_text():
                buf += chunk
                if "og:image" in buf or len(buf) > _MAX_CHARS:
                    break
    except Exception:
        return ""
    for pattern in _OG_IMAGE_PATTERNS:
        m = pattern.search(buf)
        if m:
            return m.group(1)
    return ""
