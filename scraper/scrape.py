#!/usr/bin/env python3
"""
Woning Alert — scraper
Haalt huurwoningen op van alle bronnen en schrijft naar docs/data.json
"""

import json
import re
import time
from datetime import datetime
from pathlib import Path

ZOEKEISEN = {
    "max_prijs":  4000,
    "min_kamers": 3,
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "nl-NL,nl;q=0.9",
    "Connection": "keep-alive",
}

OUTPUT_DIR  = Path(__file__).parent.parent / "docs"
DATA_FILE   = OUTPUT_DIR / "data.json"
GEZIEN_FILE = OUTPUT_DIR / "gezien.json"

# Woorden die duiden op KOOP — deze woningen skippen we
KOOP_SIGNALEN = [
    "k.k.", "v.o.n.", "koopprijs", "vraagprijs", "koop",
    "te koop", "for sale", "verkoop",
]

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
    """Haal een getal uit prijstekst zoals '€ 2.500 /mnd'"""
    for m in re.finditer(r'\d{3,6}', tekst.replace(".", "").replace(",", "")):
        g = int(m.group())
        if 300 < g < 20000:
            return g
    return None


def extraheer_datum(item) -> str:
    """
    Extraheer plaatsingsdatum uit een listing-item.
    Strategie: van specifiek naar generiek.
    """
    maanden = ["", "jan", "feb", "mrt", "apr", "mei", "jun",
               "jul", "aug", "sep", "okt", "nov", "dec"]

    def iso_naar_leesbaar(dt: str) -> str:
        m = re.match(r'(\d{4})-(\d{2})-(\d{2})', dt)
        if m:
            return f"{int(m.group(3))} {maanden[int(m.group(2))]} {m.group(1)}"
        return ""

    # 1. <time datetime="2026-06-24">
    for el in item.select("time[datetime]"):
        leesbaar = iso_naar_leesbaar(el.get("datetime", ""))
        if leesbaar:
            return leesbaar

    # 2. Elementen met datum-classes (breed gezocht)
    datum_selectors = (
        "[class*='date'], [class*='datum'], [class*='Date'], "
        "[class*='since'], [class*='posted'], [class*='placed'], "
        "[class*='listed'], [class*='aangeboden'], [class*='plaatsingsdatum'], "
        "[class*='available'], [class*='beschikbaar'], [class*='online']"
    )
    for el in item.select(datum_selectors):
        tekst = el.get_text(strip=True)
        if tekst and len(tekst) < 60:
            # ISO datum erin?
            leesbaar = iso_naar_leesbaar(tekst)
            if leesbaar:
                return leesbaar
            # Datum-achtig patroon?
            if re.search(r'\d{1,2}[-/. ]\d{1,2}[-/. ]\d{2,4}', tekst):
                return tekst.strip()
            if re.search(r'\d{1,2}\s+(?:jan|feb|mrt|apr|mei|jun|jul|aug|sep|okt|nov|dec)', tekst, re.I):
                return tekst.strip()

    # 3. Zoek in platte tekst naar datum-labels
    # Pararius: "Aangeboden sinds 15 mei 2026"
    # Funda: "Beschikbaar per 01-06-2026" / "Aangeboden op 20-05-2026"
    # Huurwoningen.nl: "Datum: 24-06-2026"
    # MVGM/Vesteda: "Available since June 1"
    tekst = item.get_text(separator=" ")

    label_patronen = [
        # Label + datum in één patroon vangen
        r'(?:aangeboden\s+(?:sinds|op|per)|beschikbaar\s+(?:per|vanaf|sinds)|'
        r'datum[:\s]+|geplaatst\s+(?:op|per)|online\s+(?:per|sinds|vanaf)|'
        r'listed\s+(?:on|since)|available\s+(?:per|since|from))'
        r'\s*:?\s*'
        r'(\d{1,2}[-/. ]\d{1,2}[-/. ]\d{2,4}|\d{1,2}\s+\w+\s+\d{4}|\d{4}-\d{2}-\d{2})',
    ]
    for patroon in label_patronen:
        m = re.search(patroon, tekst, re.IGNORECASE)
        if m:
            datum_str = m.group(1).strip()
            leesbaar = iso_naar_leesbaar(datum_str)
            return leesbaar if leesbaar else datum_str

    # 4. Fallback: elk datumpatroon in de tekst
    for patroon in [
        r'\b(\d{1,2}[-/]\d{1,2}[-/]\d{4})\b',
        r'\b(\d{4}-\d{2}-\d{2})\b',
        r'\b(\d{1,2}\s+(?:jan(?:uari)?|feb(?:ruari)?|mrt|maart|apr(?:il)?|mei|jun(?:i)?|'
        r'jul(?:i)?|aug(?:ustus)?|sep(?:tember)?|okt(?:ober)?|nov(?:ember)?|dec(?:ember)?)'
        r'\s+\d{4})\b',
    ]:
        m = re.search(patroon, tekst, re.IGNORECASE)
        if m:
            datum_str = m.group(1).strip()
            leesbaar = iso_naar_leesbaar(datum_str)
            return leesbaar if leesbaar else datum_str

    return ""

def is_koopwoning(w: dict) -> bool:
    """Detecteer koopwoningen op basis van prijs/titel/link tekst."""
    tekst = (w.get("prijs", "") + " " + w.get("titel", "") + " " + w.get("link", "")).lower()
    return any(sig in tekst for sig in KOOP_SIGNALEN)

def is_huur_url(href: str) -> bool:
    """Check of een URL waarschijnlijk een huurwoning is (niet koop)."""
    koop_url_signalen = ["/koop/", "/koopwoning/", "/te-koop/", "/for-sale/", "/buy/", "/verkoop/"]
    return not any(sig in href.lower() for sig in koop_url_signalen)

def voldoet(w: dict) -> bool:
    """Filtert op prijs en sluit koopwoningen uit."""
    if is_koopwoning(w):
        return False
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
                    "plaatsingsdatum": extraheer_datum(item),
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
    # /huur/ in de URL zorgt dat Funda alleen huurwoningen toont
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
            # Zorg dat het een huurwoning-URL is
            if "/huur/" not in href and "/huurwoning" not in href:
                continue
            titel_el = (item.select_one("[data-test-id='street-name-house-number']") or
                        item.select_one("h2") or item.select_one("[class*='title']"))
            prijs_el = (item.select_one("[data-test-id='price-rent']") or
                        item.select_one("[class*='price']"))
            resultaten.append({
                "bron": "Funda", "bron_url": "https://www.funda.nl",
                "titel": titel_el.get_text(strip=True) if titel_el else "Zie link",
                "prijs": prijs_el.get_text(strip=True) if prijs_el else "",
                "details": "", "link": href,
                    "plaatsingsdatum": extraheer_datum(item),
            })
        except Exception:
            pass
    return resultaten

def scrape_huurwoningen() -> list[dict]:
    from bs4 import BeautifulSoup
    max_p = ZOEKEISEN["max_prijs"]
    min_k = ZOEKEISEN["min_kamers"]
    url = f"https://www.huurwoningen.nl/in/amsterdam/?price=0-{max_p}&bedrooms={min_k}"
    html = playwright_fetch(url, wacht=4)  # langer wachten voor JS-prijzen
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    resultaten = []
    for item in soup.select(".listing-search-item, [class*='listing']"):
        try:
            a = item.select_one("a[href*='/huren/']") or item.select_one("a[href*='/huurwoning/']") or item.select_one("a")
            if not a:
                continue
            href = a.get("href", "")
            if not href.startswith("http"):
                href = "https://www.huurwoningen.nl" + href
            if "/huren/" not in href and "/huurwoning/" not in href:
                continue
            titel_el = item.select_one("h2, [class*='title'], [class*='address'], [class*='street']")
            # Prijzen zitten in spans met data-attributen of aparte price class
            prijs_el = (item.select_one("[class*='price']") or
                        item.select_one("[data-price]") or
                        item.select_one("[class*='rent']"))
            resultaten.append({
                "bron": "Huurwoningen.nl", "bron_url": "https://www.huurwoningen.nl",
                "titel": titel_el.get_text(strip=True) if titel_el else "Zie link",
                "prijs": prijs_el.get_text(strip=True) if prijs_el else "",
                "details": "", "link": href,
                    "plaatsingsdatum": extraheer_datum(item),
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
                    "plaatsingsdatum": extraheer_datum(item),
            })
        except Exception:
            pass
    return resultaten

def scrape_jaap() -> list[dict]:
    from bs4 import BeautifulSoup
    max_p = ZOEKEISEN["max_prijs"]
    min_k = ZOEKEISEN["min_kamers"]
    # /huurhuizen/ in URL = alleen huur
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
            # Jaap: alleen huurwoningen-URLs meenemen
            if "/huurhuizen/" not in href and "/huurwoning" not in href:
                continue
            titel_el = item.select_one("h2, h3, [class*='address']")
            prijs_el = item.select_one("[class*='price'], [class*='prijs']")
            resultaten.append({
                "bron": "Jaap", "bron_url": "https://www.jaap.nl",
                "titel": titel_el.get_text(strip=True) if titel_el else "Zie link",
                "prijs": prijs_el.get_text(strip=True) if prijs_el else "",
                "details": "", "link": href,
                    "plaatsingsdatum": extraheer_datum(item),
            })
        except Exception:
            pass
    return resultaten

def scrape_vesteda() -> list[dict]:
    from bs4 import BeautifulSoup
    # Vesteda toont per project; elk project heeft meerdere woningen
    # We scrapen de overzichtspagina en pakken projectlinks
    url = "https://www.vesteda.com/nl/huurwoningen-amsterdam"
    html = playwright_fetch(url, wacht=4)
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    resultaten = []

    # Vesteda gebruikt data-testid attributen op hun kaarten
    selectors = [
        "[data-testid*='complex']",
        "[data-testid*='unit']",
        "[class*='ComplexCard']",
        "[class*='UnitCard']",
        "[class*='complex-card']",
        "[class*='unit-card']",
        "article",
    ]
    items = []
    for sel in selectors:
        items = soup.select(sel)
        if items:
            break

    for item in items:
        try:
            a = item.select_one("a[href*='/nl/huurwoningen']") or item.select_one("a")
            if not a:
                continue
            href = a.get("href", "")
            if not href.startswith("http"):
                href = "https://www.vesteda.com" + href
            if "vesteda.com" not in href or "/nl/huurwoningen" not in href:
                continue

            naam_el = item.select_one("h2, h3, h4, [class*='title'], [class*='name'], [class*='Title'], [class*='Name']")
            prijs_el = item.select_one("[class*='price'], [class*='Price'], [class*='rent'], [class*='Rent']")

            naam = naam_el.get_text(strip=True) if naam_el else ""
            prijs = prijs_el.get_text(strip=True) if prijs_el else "Zie website"

            # Skip lege/nutteloze kaarten
            if not naam or naam.lower() in ["", "zie link", "zie website"]:
                continue

            resultaten.append({
                "bron": "Vesteda", "bron_url": "https://www.vesteda.com",
                "titel": naam,
                "prijs": prijs,
                "details": "",
                    "plaatsingsdatum": extraheer_datum(item),
                "link": href,
            })
        except Exception:
            pass

    # Dedup op link
    return list({r["link"]: r for r in resultaten}.values())

def scrape_mvgm() -> list[dict]:
    from bs4 import BeautifulSoup
    url = "https://ikwilhuren.nu/aanbod/amsterdam"
    html = playwright_fetch(url, wacht=3)
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    resultaten = []

    # MVGM gebruikt li-elementen met woninginfo als platte tekst
    # Probeer specifiekere selectors eerst
    items = (soup.select("li[class*='listing']") or
             soup.select("[class*='property-item']") or
             soup.select("article"))

    for item in items:
        try:
            a = item.select_one("a")
            if not a:
                continue
            href = a.get("href", "")
            if not href.startswith("http"):
                href = "https://ikwilhuren.nu" + href
            if "ikwilhuren.nu" not in href:
                continue
            titel_el = item.select_one("h2, h3, [class*='address'], [class*='title'], [class*='straat']")
            prijs_el = item.select_one("[class*='price'], [class*='prijs'], [class*='huur']")
            if not titel_el:
                continue
            resultaten.append({
                "bron": "MVGM", "bron_url": "https://ikwilhuren.nu",
                "titel": titel_el.get_text(strip=True),
                "prijs": prijs_el.get_text(strip=True) if prijs_el else "Zie website",
                "details": "", "link": href,
                    "plaatsingsdatum": extraheer_datum(item),
            })
        except Exception:
            pass

    # Fallback: regex op platte tekst (MVGM rendert listings als tekst)
    if not resultaten:
        tekst = soup.get_text(separator="\n")
        for m in re.finditer(r'(Appartement|Eengezinswoning)\s+([A-Z][^\n]{5,60}Amsterdam[^\n]*)', tekst):
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
    for url, base in [
        ("https://fris.nl/volledige-woningaanbod/", "https://fris.nl"),
        ("https://www.friswonen.nl/woningen/", "https://www.friswonen.nl"),
    ]:
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
                tekst = item.get_text().lower()
                if "amsterdam" not in tekst:
                    continue
                # Skip koop
                if any(sig in tekst for sig in KOOP_SIGNALEN):
                    continue
                titel_el = item.select_one("h2, h3, [class*='address'], [class*='title']")
                prijs_el = item.select_one("[class*='price'], [class*='prijs'], [class*='huur']")
                if not titel_el:
                    continue
                resultaten.append({
                    "bron": "Fris", "bron_url": base,
                    "titel": titel_el.get_text(strip=True),
                    "prijs": prijs_el.get_text(strip=True) if prijs_el else "Zie website",
                    "details": "", "link": href,
                    "plaatsingsdatum": extraheer_datum(item),
                })
            except Exception:
                pass
    return resultaten

def scrape_eefjevoogd() -> list[dict]:
    from bs4 import BeautifulSoup
    # Gebruik de expliciete huur-URL met availability filter
    urls = [
        "https://www.eefjevoogd.nl/nl/woningen/?availability=for-rent",
        "https://www.eefjevoogd.nl/nl/woningen/huur/",
        "https://www.eefjevoogd.nl/nl/rent/",
    ]
    for url in urls:
        html = playwright_fetch(url, wacht=3)
        if not html or len(html) < 1000:
            continue
        soup = BeautifulSoup(html, "html.parser")
        resultaten = []

        for item in soup.select("[class*='woning'], [class*='property'], [class*='listing'], article, .card, li"):
            try:
                a = item.select_one("a")
                if not a:
                    continue
                href = a.get("href", "")
                if not href.startswith("http"):
                    href = "https://www.eefjevoogd.nl" + href
                if "eefjevoogd.nl" not in href:
                    continue
                # Skip koop-URLs
                if not is_huur_url(href):
                    continue
                tekst = item.get_text().lower()
                # Skip als koop-signalen in tekst
                if any(sig in tekst for sig in KOOP_SIGNALEN):
                    continue
                titel_el = item.select_one("h2, h3, [class*='address'], [class*='title']")
                prijs_el = item.select_one("[class*='price'], [class*='prijs'], [class*='huur'], [class*='rent']")
                if not titel_el:
                    continue
                resultaten.append({
                    "bron": "Eefje Voogd", "bron_url": "https://www.eefjevoogd.nl",
                    "titel": titel_el.get_text(strip=True),
                    "prijs": prijs_el.get_text(strip=True) if prijs_el else "Zie website",
                    "details": "", "link": href,
                    "plaatsingsdatum": extraheer_datum(item),
                })
            except Exception:
                pass

        if resultaten:
            return resultaten

    return []

def scrape_vanderlinden() -> list[dict]:
    from bs4 import BeautifulSoup
    html = playwright_fetch("https://www.vanderlinden.nl/woning-huren/")
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    resultaten = []
    for item in soup.select("[class*='woning'], [class*='property'], [class*='listing'], article, .card"):
        try:
            a = item.select_one("a")
            if not a:
                continue
            href = a.get("href", "")
            if not href.startswith("http"):
                href = "https://www.vanderlinden.nl" + href
            if "vanderlinden.nl" not in href:
                continue
            tekst = item.get_text().lower()
            if "amsterdam" not in tekst:
                continue
            if any(sig in tekst for sig in KOOP_SIGNALEN):
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
                    "plaatsingsdatum": extraheer_datum(item),
            })
        except Exception:
            pass
    return resultaten

def scrape_corporatie(naam, url, base_url, link_filter) -> list[dict]:
    """Generieke scraper voor woningcorporaties."""
    from bs4 import BeautifulSoup
    html = playwright_fetch(url, wacht=3)
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    resultaten = []

    # Sla navigatie-elementen en footer over
    for nav in soup.select("nav, footer, header, [class*='nav'], [class*='footer'], [class*='menu']"):
        nav.decompose()

    for item in soup.select("[class*='woning'], [class*='property'], [class*='listing'], article, .card"):
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
            titel = titel_el.get_text(strip=True)
            # Skip navigatie-achtige titels
            if len(titel) < 5 or titel.lower() in ["inloggen", "contact", "zoeken", "menu", "aanbod"]:
                continue
            resultaten.append({
                "bron": naam, "bron_url": base_url,
                "titel": titel,
                "prijs": prijs_el.get_text(strip=True) if prijs_el else "Zie website",
                "details": "", "link": href,
                    "plaatsingsdatum": extraheer_datum(item),
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

    # Filter op huur + prijs + dedup op link
    gefilterd = [w for w in alle if voldoet(w)]
    gefilterd = list({w["link"]: w for w in gefilterd}.values())
    print(f"  Totaal na filter+dedup: {len(gefilterd)}")

    # Debug: toon hoeveel datums gevonden zijn
    met_datum = sum(1 for w in gefilterd if w.get("plaatsingsdatum"))
    print(f"  Met plaatsingsdatum: {met_datum}/{len(gefilterd)}")

    # Markeer nieuw vs. gezien
    nu = datetime.now().isoformat()
    for w in gefilterd:
        w["nieuw"] = w["link"] not in gezien
        if w["nieuw"]:
            w["gezien_sinds"] = nu
            gezien[w["link"]] = nu
        else:
            w["gezien_sinds"] = gezien[w["link"]]

    # Sorteer: nieuw bovenaan
    gefilterd.sort(key=lambda w: (not w["nieuw"], w.get("gezien_sinds", "")))

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
