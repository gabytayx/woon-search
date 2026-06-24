#!/usr/bin/env python3
"""
Woning Alert — scraper
Haalt woningen op van alle bronnen en schrijft naar docs/data.json
"""

import json
import re
import time
from datetime import datetime
from pathlib import Path

ZOEKEISEN = {
    "max_prijs":  4000,
    "min_kamers": 3,
    "types": ["appartement", "woonhuis", "eengezinswoning"],
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "nl-NL,nl;q=0.9",
    "Connection": "keep-alive",
}

OUTPUT_DIR = Path(__file__).parent.parent / "docs"
DATA_FILE  = OUTPUT_DIR / "data.json"
GEZIEN_FILE = OUTPUT_DIR / "gezien.json"

# ─── Helpers ──────────────────────────────────────────────

def playwright_fetch(url: str, wacht: float = 2.5) -> str:
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-blink-features=AutomationControlled"]
            )
            ctx = browser.new_context(
                user_agent=HEADERS["User-Agent"],
                locale="nl-NL",
                viewport={"width": 1280, "height": 900},
            )
            page = ctx.new_page()
            page.goto(url, timeout=25000, wait_until="domcontentloaded")
            time.sleep(wacht)
            html = page.content()
            browser.close()
            return html
    except Exception as e:
        print(f"  ⚠️  Playwright fout ({url}): {e}")
        return ""

def extraheer_prijs(tekst: str) -> int | None:
    for m in re.finditer(r'\d{3,5}', tekst.replace(".", "").replace(",", "")):
        g = int(m.group())
        if 300 < g < 20000:
            return g
    return None

def voldoet(w: dict) -> bool:
    prijs = extraheer_prijs(w.get("prijs", ""))
    if prijs and prijs > ZOEKEISEN["max_prijs"]:
        return False
    return True

def laad_gezien() -> dict:
    if GEZIEN_FILE.exists():
        return json.loads(GEZIEN_FILE.read_text())
    return {}

def sla_gezien_op(gezien: dict):
    OUTPUT_DIR.mkdir(exist_ok=True)
    GEZIEN_FILE.write_text(json.dumps(gezien, indent=2, ensure_ascii=False))

# ─── Scrapers ─────────────────────────────────────────────

def scrape_pararius() -> list[dict]:
    from bs4 import BeautifulSoup
    resultaten = []
    max_p = ZOEKEISEN["max_prijs"]
    for kamers in [3, 4, 5]:
        url = f"https://www.pararius.nl/huurwoningen/amsterdam/0-{max_p}/{kamers}-slaapkamers"
        html = playwright_fetch(url)
        if not html:
            continue
        soup = BeautifulSoup(html, "html.parser")
        for item in soup.select("li.search-list__item--listing"):
            try:
                a = item.select_one("a.listing-search-item__link--title")
                if not a:
                    continue
                prijs_el = item.select_one(".listing-search-item__price")
                details = " · ".join(d.get_text(strip=True) for d in item.select(".illustrated-features__item"))
                resultaten.append({
                    "bron": "Pararius", "bron_url": "https://www.pararius.nl",
                    "titel": a.get_text(strip=True),
                    "prijs": prijs_el.get_text(strip=True) if prijs_el else "",
                    "details": details,
                    "link": "https://www.pararius.nl" + a["href"],
                })
            except Exception:
                pass
        time.sleep(2)
    return resultaten

def scrape_funda() -> list[dict]:
    from bs4 import BeautifulSoup
    max_p = ZOEKEISEN["max_prijs"]
    min_k = ZOEKEISEN["min_kamers"]
    url = (f"https://www.funda.nl/zoeken/huur/?selected_area=%5B%22amsterdam%22%5D"
           f"&price_max={max_p}&rooms_min={min_k}"
           "&object_type%5B%5D=apartment&object_type%5B%5D=house&sort=date_down")
    html = playwright_fetch(url, wacht=4)
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    resultaten = []
    listings = (soup.select("[data-test-id='search-result-item']") or
                soup.select(".search-result--list") or
                soup.select("div[class*='search-result']"))
    for item in listings:
        try:
            a = item.select_one("a")
            if not a:
                continue
            href = a.get("href", "")
            if not href.startswith("http"):
                href = "https://www.funda.nl" + href
            titel_el = (item.select_one("[data-test-id='street-name-house-number']") or
                        item.select_one("h2") or item.select_one("[class*='title']"))
            prijs_el = (item.select_one("[data-test-id='price-rent']") or
                        item.select_one("[class*='price']"))
            resultaten.append({
                "bron": "Funda", "bron_url": "https://www.funda.nl",
                "titel": titel_el.get_text(strip=True) if titel_el else "Zie link",
                "prijs": prijs_el.get_text(strip=True) if prijs_el else "",
                "details": "", "link": href,
            })
        except Exception:
            pass
    return resultaten

def scrape_huurwoningen() -> list[dict]:
    from bs4 import BeautifulSoup
    max_p = ZOEKEISEN["max_prijs"]
    min_k = ZOEKEISEN["min_kamers"]
    url = f"https://www.huurwoningen.nl/in/amsterdam/?price=0-{max_p}&bedrooms={min_k}"
    html = playwright_fetch(url)
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    resultaten = []
    for item in soup.select(".listing-search-item, [class*='listing']"):
        try:
            a = item.select_one("a[href*='/huurwoning/']") or item.select_one("a")
            if not a:
                continue
            href = a.get("href", "")
            if not href.startswith("http"):
                href = "https://www.huurwoningen.nl" + href
            titel_el = item.select_one("h2, [class*='title']")
            prijs_el = item.select_one("[class*='price'], [class*='prijs']")
            resultaten.append({
                "bron": "Huurwoningen.nl", "bron_url": "https://www.huurwoningen.nl",
                "titel": titel_el.get_text(strip=True) if titel_el else "Zie link",
                "prijs": prijs_el.get_text(strip=True) if prijs_el else "",
                "details": "", "link": href,
            })
        except Exception:
            pass
    return resultaten

def scrape_huislijn() -> list[dict]:
    from bs4 import BeautifulSoup
    max_p = ZOEKEISEN["max_prijs"]
    min_k = ZOEKEISEN["min_kamers"]
    url = f"https://www.huislijn.nl/huurwoning/amsterdam?MinHuur=0&MaxHuur={max_p}&MinKamers={min_k}&order=Nieuwste"
    html = playwright_fetch(url)
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    resultaten = []
    for item in soup.select(".search-result, [class*='property-result'], article"):
        try:
            a = item.select_one("a[href*='/huurwoning/']") or item.select_one("a")
            if not a:
                continue
            href = a.get("href", "")
            if not href.startswith("http"):
                href = "https://www.huislijn.nl" + href
            if "/huurwoning/" not in href:
                continue
            titel_el = item.select_one("h2, h3, [class*='address'], [class*='title']")
            prijs_el = item.select_one("[class*='price'], [class*='prijs']")
            resultaten.append({
                "bron": "Huislijn", "bron_url": "https://www.huislijn.nl",
                "titel": titel_el.get_text(strip=True) if titel_el else "Zie link",
                "prijs": prijs_el.get_text(strip=True) if prijs_el else "",
                "details": "", "link": href,
            })
        except Exception:
            pass
    return resultaten

def scrape_jaap() -> list[dict]:
    from bs4 import BeautifulSoup
    max_p = ZOEKEISEN["max_prijs"]
    min_k = ZOEKEISEN["min_kamers"]
    url = f"https://www.jaap.nl/huurhuizen/amsterdam/+{min_k}slaapkamers/+0mnd-{max_p}mnd/"
    html = playwright_fetch(url)
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    resultaten = []
    for item in soup.select(".property-list-item, .search-result, article"):
        try:
            a = item.select_one("a")
            if not a:
                continue
            href = a.get("href", "")
            if not href.startswith("http"):
                href = "https://www.jaap.nl" + href
            titel_el = item.select_one("h2, h3, [class*='address']")
            prijs_el = item.select_one("[class*='price'], [class*='prijs']")
            resultaten.append({
                "bron": "Jaap", "bron_url": "https://www.jaap.nl",
                "titel": titel_el.get_text(strip=True) if titel_el else "Zie link",
                "prijs": prijs_el.get_text(strip=True) if prijs_el else "",
                "details": "", "link": href,
            })
        except Exception:
            pass
    return resultaten

def scrape_vesteda() -> list[dict]:
    from bs4 import BeautifulSoup
    html = playwright_fetch("https://www.vesteda.com/nl/huurwoningen-amsterdam", wacht=3)
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    resultaten = []
    for item in soup.select("[class*='complex'], [class*='project'], [class*='card'], article"):
        try:
            a = item.select_one("a[href*='/nl/huurwoningen']") or item.select_one("a")
            if not a:
                continue
            href = a.get("href", "")
            if not href.startswith("http"):
                href = "https://www.vesteda.com" + href
            if "vesteda.com" not in href:
                continue
            naam_el = item.select_one("h2, h3, [class*='title'], [class*='name']")
            prijs_el = item.select_one("[class*='price'], [class*='prijs'], [class*='rent']")
            resultaten.append({
                "bron": "Vesteda", "bron_url": "https://www.vesteda.com",
                "titel": naam_el.get_text(strip=True) if naam_el else "Zie link",
                "prijs": prijs_el.get_text(strip=True) if prijs_el else "Zie website",
                "details": "", "link": href,
            })
        except Exception:
            pass
    return resultaten

def scrape_mvgm() -> list[dict]:
    from bs4 import BeautifulSoup
    html = playwright_fetch("https://ikwilhuren.nu/aanbod/amsterdam", wacht=3)
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    resultaten = []
    for item in soup.select("[class*='listing'], [class*='property'], [class*='woning'], article, .card"):
        try:
            a = (item.select_one("a[href*='ikwilhuren']") or
                 item.select_one("a[href*='/aanbod/']") or item.select_one("a"))
            if not a:
                continue
            href = a.get("href", "")
            if not href.startswith("http"):
                href = "https://ikwilhuren.nu" + href
            if "ikwilhuren.nu" not in href:
                continue
            titel_el = item.select_one("h2, h3, [class*='address'], [class*='title'], [class*='straat']")
            prijs_el = item.select_one("[class*='price'], [class*='prijs'], [class*='huur']")
            resultaten.append({
                "bron": "MVGM", "bron_url": "https://ikwilhuren.nu",
                "titel": titel_el.get_text(strip=True) if titel_el else "Zie link",
                "prijs": prijs_el.get_text(strip=True) if prijs_el else "Zie website",
                "details": "", "link": href,
            })
        except Exception:
            pass
    if not resultaten:
        tekst = soup.get_text()
        for m in re.finditer(r'(Appartement|Eengezinswoning)\s+([A-Z][^\n]{5,60}Amsterdam)', tekst):
            resultaten.append({
                "bron": "MVGM", "bron_url": "https://ikwilhuren.nu",
                "titel": f"{m.group(1)} {m.group(2).strip()}",
                "prijs": "Zie website", "details": "",
                "link": "https://ikwilhuren.nu/aanbod/amsterdam",
            })
    return resultaten[:30]

def scrape_fris() -> list[dict]:
    from bs4 import BeautifulSoup
    resultaten = []
    for url, base in [("https://fris.nl/volledige-woningaanbod/", "https://fris.nl"),
                      ("https://www.friswonen.nl/woningen/", "https://www.friswonen.nl")]:
        html = playwright_fetch(url)
        if not html:
            continue
        soup = BeautifulSoup(html, "html.parser")
        for item in soup.select("[class*='woning'], [class*='property'], [class*='listing'], article, .card"):
            try:
                a = item.select_one("a")
                if not a:
                    continue
                href = a.get("href", "")
                if not href.startswith("http"):
                    href = base + href
                if "amsterdam" not in item.get_text().lower():
                    continue
                titel_el = item.select_one("h2, h3, [class*='address'], [class*='title']")
                prijs_el = item.select_one("[class*='price'], [class*='prijs'], [class*='huur']")
                resultaten.append({
                    "bron": "Fris", "bron_url": base,
                    "titel": titel_el.get_text(strip=True) if titel_el else "Zie link",
                    "prijs": prijs_el.get_text(strip=True) if prijs_el else "Zie website",
                    "details": "", "link": href,
                })
            except Exception:
                pass
    return resultaten

def scrape_eefjevoogd() -> list[dict]:
    from bs4 import BeautifulSoup
    html = playwright_fetch("https://www.eefjevoogd.nl/nl/woningen/")
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    resultaten = []
    for item in soup.select("[class*='woning'], [class*='property'], [class*='listing'], article, li"):
        try:
            a = item.select_one("a[href*='/woning']") or item.select_one("a[href*='/nl/']") or item.select_one("a")
            if not a:
                continue
            href = a.get("href", "")
            if not href.startswith("http"):
                href = "https://www.eefjevoogd.nl" + href
            if "eefjevoogd.nl" not in href:
                continue
            titel_el = item.select_one("h2, h3, [class*='address'], [class*='title']")
            prijs_el = item.select_one("[class*='price'], [class*='prijs'], [class*='huur']")
            if not titel_el:
                continue
            resultaten.append({
                "bron": "Eefje Voogd", "bron_url": "https://www.eefjevoogd.nl",
                "titel": titel_el.get_text(strip=True),
                "prijs": prijs_el.get_text(strip=True) if prijs_el else "Zie website",
                "details": "", "link": href,
            })
        except Exception:
            pass
    return resultaten

def scrape_vanderlinden() -> list[dict]:
    from bs4 import BeautifulSoup
    html = playwright_fetch("https://www.vanderlinden.nl/woning-huren/")
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    resultaten = []
    for item in soup.select("[class*='woning'], [class*='property'], [class*='listing'], article, .card"):
        try:
            a = item.select_one("a[href*='/woning']") or item.select_one("a[href*='/huur']") or item.select_one("a")
            if not a:
                continue
            href = a.get("href", "")
            if not href.startswith("http"):
                href = "https://www.vanderlinden.nl" + href
            if "vanderlinden.nl" not in href:
                continue
            if "amsterdam" not in item.get_text().lower():
                continue
            titel_el = item.select_one("h2, h3, [class*='address'], [class*='title']")
            prijs_el = item.select_one("[class*='price'], [class*='prijs'], [class*='huur']")
            if not titel_el:
                continue
            resultaten.append({
                "bron": "Van der Linden", "bron_url": "https://www.vanderlinden.nl",
                "titel": titel_el.get_text(strip=True),
                "prijs": prijs_el.get_text(strip=True) if prijs_el else "Zie website",
                "details": "", "link": href,
            })
        except Exception:
            pass
    return resultaten

def scrape_corporatie(naam, url, base_url, link_filter) -> list[dict]:
    from bs4 import BeautifulSoup
    html = playwright_fetch(url, wacht=3)
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    resultaten = []
    for item in soup.select("[class*='woning'], [class*='property'], [class*='listing'], article, .card, li"):
        try:
            a = item.select_one(f"a[href*='{link_filter}']") or item.select_one("a")
            if not a:
                continue
            href = a.get("href", "")
            if not href.startswith("http"):
                href = base_url + href
            domain = base_url.replace("https://www.", "").replace("https://", "")
            if domain not in href:
                continue
            titel_el = item.select_one("h2, h3, [class*='address'], [class*='title']")
            prijs_el = item.select_one("[class*='price'], [class*='prijs'], [class*='huur']")
            if not titel_el:
                continue
            resultaten.append({
                "bron": naam, "bron_url": base_url,
                "titel": titel_el.get_text(strip=True),
                "prijs": prijs_el.get_text(strip=True) if prijs_el else "Zie website",
                "details": "", "link": href,
            })
        except Exception:
            pass
    return resultaten

# ─── Main ─────────────────────────────────────────────────

BRONNEN = [
    ("Pararius",        scrape_pararius),
    ("Funda",           scrape_funda),
    ("Huurwoningen.nl", scrape_huurwoningen),
    ("Huislijn",        scrape_huislijn),
    ("Jaap",            scrape_jaap),
    ("Vesteda",         scrape_vesteda),
    ("MVGM",            scrape_mvgm),
    ("Fris",            scrape_fris),
    ("Eefje Voogd",     scrape_eefjevoogd),
    ("Van der Linden",  scrape_vanderlinden),
    ("Eigen Haard",     lambda: scrape_corporatie("Eigen Haard", "https://www.eigenhaard.nl/te-huur/vrije-sector-huur/zoek", "https://www.eigenhaard.nl", "/te-huur/")),
    ("Ymere",           lambda: scrape_corporatie("Ymere", "https://www.ymere.nl/aanbod/", "https://www.ymere.nl", "/aanbod/")),
    ("Rochdale",        lambda: scrape_corporatie("Rochdale", "https://www.rochdale.nl/huren/beschikbare-woningen", "https://www.rochdale.nl", "/woning/")),
    ("De Key",          lambda: scrape_corporatie("De Key", "https://www.dekey.nl/te-huur/", "https://www.dekey.nl", "/te-huur/")),
    ("Stadgenoot",      lambda: scrape_corporatie("Stadgenoot", "https://www.stadgenoot.nl/aanbod/beschikbare-woningen/huren", "https://www.stadgenoot.nl", "/woning/")),
]

def main():
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Scraper gestart — {len(BRONNEN)} bronnen")
    OUTPUT_DIR.mkdir(exist_ok=True)

    gezien = laad_gezien()
    alle = []

    for naam, fn in BRONNEN:
        print(f"  📡 {naam}...")
        try:
            r = fn()
            print(f"     → {len(r)} gevonden")
            alle += r
        except Exception as e:
            print(f"     ⚠️  {e}")

    # Filter + dedup
    gefilterd = [w for w in alle if voldoet(w)]
    gefilterd = list({w["link"]: w for w in gefilterd}.values())
    print(f"  Totaal na filter+dedup: {len(gefilterd)}")

    # Markeer nieuw vs. gezien
    nu = datetime.now().isoformat()
    for w in gefilterd:
        w["nieuw"] = w["link"] not in gezien
        if w["nieuw"]:
            w["gezien_sinds"] = nu
            gezien[w["link"]] = nu
        else:
            w["gezien_sinds"] = gezien[w["link"]]

    # Sorteer: nieuw bovenaan, dan op datum
    gefilterd.sort(key=lambda w: (not w["nieuw"], w.get("gezien_sinds", "")), reverse=False)

    # Sla op
    data = {
        "bijgewerkt": datetime.now().strftime("%d %b %Y om %H:%M"),
        "bijgewerkt_iso": datetime.now().isoformat(),
        "aantal": len(gefilterd),
        "nieuw": sum(1 for w in gefilterd if w["nieuw"]),
        "woningen": gefilterd,
    }
    DATA_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    sla_gezien_op(gezien)
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Klaar — {len(gefilterd)} woningen, {data['nieuw']} nieuw")

if __name__ == "__main__":
    main()
