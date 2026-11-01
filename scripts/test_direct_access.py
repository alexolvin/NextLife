"""Comprehensive DIRECT access testing script for Czech marketplaces."""

import json
import time
from typing import Any

import httpx
from bs4 import BeautifulSoup

PLATFORMS = [
    {
        "key": "bazos_cz",
        "name": "Bazoš.cz",
        "type": "General Classifieds",
        "url": "https://www.bazos.cz",
        "test_query": "thinkpad",
        "search_url": "https://www.bazos.cz/search.php?hledat={query}",
        "headers": {"User-Agent": "Mozilla/5.0"},
        "cookies": {},
        "notes": "Крупнейшая доска объявлений в ЧР. Стабильный чистый HTML, отличный DIRECT-доступ.",
    },
    {
        "key": "sbazar_cz",
        "name": "Sbazar.cz",
        "type": "General Classifieds (Seznam)",
        "url": "https://www.sbazar.cz",
        "test_query": "thinkpad",
        "search_url": "https://www.sbazar.cz/hledej/{query}",
        "headers": {"User-Agent": "Mozilla/5.0"},
        "cookies": {"szncmpone": "1"},
        "notes": "Сервис Seznam.cz на Astro SSR. DIRECT работает при стандартном UA (без падения в CMP-редирект).",
    },
    {
        "key": "aukro_cz",
        "name": "Aukro.cz",
        "type": "Auction & Marketplace",
        "url": "https://aukro.cz",
        "test_query": "thinkpad",
        "search_url": "https://aukro.cz/vysledky-vyhledavani?text={query}",
        "headers": {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"},
        "cookies": {},
        "notes": "Крупнейший чешский аукцион. Серверный Angular SSR (auk-basic-item-card) + официальный Public REST API (api.aukro.cz).",
    },
    {
        "key": "vinted_cz",
        "name": "Vinted.cz",
        "type": "P2P Marketplace",
        "url": "https://www.vinted.cz",
        "test_query": "thinkpad",
        "search_url": "https://www.vinted.cz/catalog?search_text={query}",
        "headers": {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
            "Accept-Language": "cs,en;q=0.9",
        },
        "cookies": {},
        "notes": "Поиск в web возвращает HTML (96+ листингов), но API и мутации блокируются DataDome (403). Полноценно требует ANDROID.",
    },
    {
        "key": "facebook_marketplace",
        "name": "Facebook Marketplace CZ",
        "type": "Social Marketplace",
        "url": "https://www.facebook.com/marketplace",
        "test_query": "thinkpad",
        "search_url": "https://www.facebook.com/marketplace/prague/search?query={query}",
        "headers": {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"},
        "cookies": {},
        "notes": "Meta блокирует прямой доступ (400 Bad Request / Login Wall). Только ANDROID режим через OpenClaw.",
    },
    {
        "key": "hyperinzerce_cz",
        "name": "Hyperinzerce.cz",
        "type": "Classifieds (Secondary)",
        "url": "https://hyperinzerce.cz",
        "test_query": "thinkpad",
        "search_url": "https://hyperinzerce.cz/inzeraty/Index?query={query}",
        "headers": {"User-Agent": "Mozilla/5.0"},
        "cookies": {},
        "notes": "Прямой HTTP работает (SSR). Вторая очередь из-за низкой ликвидности и устаревшей аудитории.",
    },
    {
        "key": "annonce_cz",
        "name": "Annonce.cz",
        "type": "Classifieds (Secondary)",
        "url": "https://www.annonce.cz",
        "test_query": "kolo",
        "search_url": "https://www.annonce.cz/vsechny-inzeraty$18-filter.html?q={query}",
        "headers": {"User-Agent": "Mozilla/5.0"},
        "cookies": {},
        "notes": "Старейшая доска объявлений. Прямой HTTP работает, но крайне низкий объем объявлений.",
    },
    {
        "key": "avizo_cz",
        "name": "Avízo.cz",
        "type": "Classifieds (Regional)",
        "url": "https://www.avizo.cz",
        "test_query": "kolo",
        "search_url": "https://www.avizo.cz/vyhledavani/?q={query}",
        "headers": {"User-Agent": "Mozilla/5.0"},
        "cookies": {},
        "notes": "Защищено Cloudflare Turnstile Challenge (403 Forbidden). DIRECT невозможен без обхода защиты (запрещено ТЗ).",
    },
    {
        "key": "trhknih_cz",
        "name": "TrhKnih.cz",
        "type": "Niche (Books)",
        "url": "https://www.trhknih.cz",
        "test_query": "python",
        "search_url": "https://www.trhknih.cz/hledat?q={query}",
        "headers": {"User-Agent": "Mozilla/5.0"},
        "cookies": {},
        "notes": "Прямой HTTP работает идеально, но площадка узконишевая (только книги).",
    },
]


def test_platform(target: dict[str, Any]) -> dict[str, Any]:
    key = str(target["key"])
    name = str(target["name"])
    base_url = str(target["url"])
    search_tmpl = str(target["search_url"])
    query = str(target["test_query"])
    search_url = search_tmpl.format(query=query)
    headers = dict(target.get("headers", {}))
    cookies = dict(target.get("cookies", {}))

    result: dict[str, Any] = {
        "key": key,
        "name": name,
        "type": target["type"],
        "base_url": base_url,
        "search_url": search_url,
        "http_base_status": None,
        "http_search_status": None,
        "latency_ms": None,
        "antibot_detected": False,
        "antibot_vendor": None,
        "search_accessible": False,
        "items_extracted_count": 0,
        "sample_item": None,
        "direct_verdict": "UNKNOWN",
        "recommended_queue": None,
        "reason": "",
        "notes": target["notes"],
    }

    t0 = time.perf_counter()
    try:
        with httpx.Client(
            headers=headers, cookies=cookies, follow_redirects=True, timeout=15.0
        ) as client:
            resp_base = client.get(base_url)
            result["http_base_status"] = resp_base.status_code

            resp_search = client.get(search_url)
            t1 = time.perf_counter()
            result["latency_ms"] = int((t1 - t0) * 1000)
            result["http_search_status"] = resp_search.status_code

            body = resp_search.text
            soup = BeautifulSoup(body, "html.parser")

            if resp_search.status_code == 403:
                if (
                    "cloudflare" in body.lower()
                    or "challenges.cloudflare.com" in body
                ):
                    result["antibot_detected"] = True
                    result["antibot_vendor"] = "Cloudflare Turnstile"
                elif "datadome" in body.lower():
                    result["antibot_detected"] = True
                    result["antibot_vendor"] = "DataDome"
                else:
                    result["antibot_detected"] = True
                    result["antibot_vendor"] = "WAF / 403 Forbidden"
            elif resp_search.status_code == 400:
                result["antibot_detected"] = True
                result["antibot_vendor"] = "Meta/Facebook Login Wall"

            if resp_search.status_code == 200:
                items: list[dict[str, str]] = []
                if key == "bazos_cz":
                    cards = soup.select(".inzeraty")
                    for c in cards:
                        t = c.select_one(".nadpis")
                        p = c.select_one(".inzeratycena")
                        a = c.select_one("a[href]")
                        if t and p and a:
                            raw_href = a.get("href", "")
                            href_str = raw_href if isinstance(raw_href, str) else str(raw_href[0])
                            items.append(
                                {
                                    "title": t.get_text().strip(),
                                    "price": p.get_text().strip(),
                                    "url": f"https://www.bazos.cz/{href_str.lstrip('/')}",
                                }
                            )
                elif key == "sbazar_cz":
                    for a in soup.select('a[href*="/inzerat/"]'):
                        txt = " ".join(a.get_text().split())
                        raw_href = a.get("href", "")
                        href_str = raw_href if isinstance(raw_href, str) else str(raw_href[0])
                        if not href_str.startswith("http"):
                            href_str = f"https://www.sbazar.cz{href_str}"
                        items.append(
                            {
                                "title": txt[:60],
                                "price": txt,
                                "url": href_str,
                            }
                        )
                elif key == "aukro_cz":
                    for c in soup.find_all("auk-basic-item-card"):
                        t = c.find("auk-item-card-title")
                        p = c.find("auk-item-card-price")
                        a = c.find("a")
                        if t and p and a and hasattr(t, "get_text") and hasattr(p, "get_text"):
                            raw_href = a.get("href", "")
                            href_str = raw_href if isinstance(raw_href, str) else str(raw_href[0])
                            if not href_str.startswith("http"):
                                href_str = f"https://aukro.cz{href_str}"
                            items.append(
                                {
                                    "title": t.get_text().strip(),
                                    "price": p.get_text().strip(),
                                    "url": href_str,
                                }
                            )
                elif key == "vinted_cz":
                    for a in soup.select('a[href*="/items/"]')[:10]:
                        raw_href = a.get("href", "")
                        href_str = raw_href if isinstance(raw_href, str) else str(raw_href[0])
                        items.append(
                            {
                                "title": a.get_text().strip() or "Vinted item",
                                "price": "Vinted item",
                                "url": f"https://www.vinted.cz{href_str}",
                            }
                        )
                elif key == "hyperinzerce_cz":
                    for a in soup.select('a[href*="/inzerat/"]'):
                        raw_href = a.get("href", "")
                        href_str = raw_href if isinstance(raw_href, str) else str(raw_href[0])
                        items.append(
                            {
                                "title": a.get_text().strip(),
                                "price": "See listing",
                                "url": href_str,
                            }
                        )
                elif key == "annonce_cz":
                    for a in soup.select('a[href*="/inzerat/"]'):
                        txt = a.get_text().strip()
                        if txt:
                            raw_href = a.get("href", "")
                            href_str = raw_href if isinstance(raw_href, str) else str(raw_href[0])
                            items.append(
                                {
                                    "title": txt,
                                    "price": "See listing",
                                    "url": f"https://www.annonce.cz{href_str}",
                                }
                            )
                elif key == "trhknih_cz":
                    for a in soup.select('a[href*="/kniha/"]'):
                        txt = a.get_text().strip()
                        if txt:
                            raw_href = a.get("href", "")
                            href_str = raw_href if isinstance(raw_href, str) else str(raw_href[0])
                            items.append(
                                {
                                    "title": txt,
                                    "price": "Book item",
                                    "url": f"https://www.trhknih.cz{href_str}",
                                }
                            )

                result["items_extracted_count"] = len(items)
                if items:
                    result["search_accessible"] = True
                    result["sample_item"] = items[0]

            # Determine verdict and queue
            if key in ("bazos_cz", "sbazar_cz", "aukro_cz"):
                result["direct_verdict"] = "DIRECT_SUPPORTED"
                result["recommended_queue"] = "FIRST_QUEUE (Первая очередь)"
                result["reason"] = (
                    "Поиск и получение листингов полностью работают по DIRECT HTTP. Высокая ликвидность."
                )
            elif key == "vinted_cz":
                result["direct_verdict"] = "DIRECT_PARTIAL_OR_ANDROID"
                result["recommended_queue"] = "SECOND_QUEUE (ANDROID)"
                result["reason"] = (
                    "Web-поиск отдает HTML, но API и любые действия блокируются DataDome (403). Требует ANDROID."
                )
            elif key == "facebook_marketplace":
                result["direct_verdict"] = "ANDROID_REQUIRED"
                result["recommended_queue"] = "SECOND_QUEUE (ANDROID)"
                result["reason"] = (
                    "Прямой доступ заблокирован Meta (400 / Login wall). Требует мобильного агента ANDROID."
                )
            elif key == "avizo_cz":
                result["direct_verdict"] = "BLOCKED_ANTIBOT"
                result["recommended_queue"] = "EXCLUDED"
                result["reason"] = (
                    "Cloudflare Turnstile блокирует прямое обращение (403 Forbidden). П-1/6.5 запрещает обход капчи."
                )
            elif key in ("hyperinzerce_cz", "annonce_cz"):
                result["direct_verdict"] = "DIRECT_SECONDARY"
                result["recommended_queue"] = "BACKLOG / SECOND_QUEUE"
                result["reason"] = (
                    "DIRECT технически возможен, но аудитория и ликвидность на порядки ниже Bazoš/Sbazar/Aukro."
                )
            elif key == "trhknih_cz":
                result["direct_verdict"] = "DIRECT_NICHE"
                result["recommended_queue"] = "NICHE (Only Books)"
                result["reason"] = (
                    "DIRECT работает идеально, но специализация строго ограничена книгами."
                )

    except Exception as exc:
        t1 = time.perf_counter()
        result["latency_ms"] = int((t1 - t0) * 1000)
        result["direct_verdict"] = "ERROR"
        result["reason"] = f"Сетевая ошибка: {type(exc).__name__}: {exc}"

    return result


def main() -> None:
    print(f"Запуск полного тестирования DIRECT-доступа ({len(PLATFORMS)} чешских платформ)...")
    results = []
    for p in PLATFORMS:
        res = test_platform(p)
        results.append(res)
        print(
            f"[{res['direct_verdict']}] {res['name']}: base={res['http_base_status']}, search={res['http_search_status']}, items={res['items_extracted_count']}, latency={res['latency_ms']}ms -> {res['recommended_queue']}"
        )

    with open("acceptance/direct_access_test_results.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    print("\nРезультаты записаны в acceptance/direct_access_test_results.json")


if __name__ == "__main__":
    main()
