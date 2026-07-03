#!/usr/bin/env python3
"""
notify.py — Telegram push voor nieuwe woningen
================================================
Draait NA scrape.py in de GitHub Actions workflow.

Wat het doet:
  1. Leest docs/data.json (de nieuwste scrape-resultaten)
  2. Vergelijkt met docs/seen.json (alles wat al eerder gezien is)
  3. Stuurt een Telegram-bericht voor elke NIEUWE woning
  4. Werkt docs/seen.json bij

Eerste run: alles in data.json wordt als "gezien" gemarkeerd
ZONDER berichten te sturen (anders krijg je 50 pings tegelijk).

Vereist twee environment variables (via GitHub Secrets):
  TELEGRAM_BOT_TOKEN  — van @BotFather
  TELEGRAM_CHAT_ID    — jouw chat id
"""

import json
import os
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

DATA_FILE = Path("docs/data.json")
SEEN_FILE = Path("docs/seen.json")

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# Max aantal berichten per run (bescherming tegen spam als een
# scraper ineens 100 "nieuwe" resultaten geeft door een site-wijziging)
MAX_BERICHTEN = 15


def stuur_telegram(tekst: str) -> bool:
    """Stuur een bericht via de Telegram Bot API."""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": CHAT_ID,
        "text": tekst,
        "parse_mode": "HTML",
        "disable_web_page_preview": "false",
    }).encode()
    try:
        req = urllib.request.Request(url, data=data)
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status == 200
    except Exception as e:
        print(f"  ⚠️  Telegram fout: {e}")
        return False


def maak_bericht(woning: dict) -> str:
    """Formatteer één woning als Telegram-bericht."""
    titel = woning.get("titel", "Onbekend adres")
    prijs = woning.get("prijs", "prijs onbekend")
    details = woning.get("details", "")
    bron = woning.get("bron", "")
    link = woning.get("link", "")

    regels = [
        f"🏠 <b>{titel}</b>",
        f"💶 {prijs}",
    ]
    if details:
        regels.append(f"📐 {details}")
    regels.append(f"🔎 Bron: {bron}")
    regels.append(f"\n👉 <a href=\"{link}\">Bekijk & reageer direct</a>")
    return "\n".join(regels)


def main() -> None:
    if not DATA_FILE.exists():
        print("❌ docs/data.json niet gevonden — draai eerst scrape.py")
        sys.exit(1)

    data = json.loads(DATA_FILE.read_text(encoding="utf-8"))

    # data.json kan een lijst zijn, of een dict met een 'woningen'/'listings' key
    if isinstance(data, dict):
        woningen = (
            data.get("woningen")
            or data.get("listings")
            or data.get("results")
            or []
        )
    else:
        woningen = data

    huidige_links = {w.get("link") for w in woningen if w.get("link")}
    print(f"📊 {len(huidige_links)} woningen in huidige scrape")

    # ── Eerste run: alles markeren als gezien, niets sturen ──
    if not SEEN_FILE.exists():
        SEEN_FILE.write_text(
            json.dumps(sorted(huidige_links), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        print(f"🆕 Eerste run: {len(huidige_links)} woningen gemarkeerd "
              "als gezien (geen berichten gestuurd)")
        return

    gezien = set(json.loads(SEEN_FILE.read_text(encoding="utf-8")))
    nieuw = [w for w in woningen
             if w.get("link") and w["link"] not in gezien]

    print(f"✨ {len(nieuw)} nieuwe woning(en) gevonden")

    if not BOT_TOKEN or not CHAT_ID:
        print("⚠️  TELEGRAM_BOT_TOKEN of TELEGRAM_CHAT_ID ontbreekt — "
              "geen berichten gestuurd")
    else:
        for woning in nieuw[:MAX_BERICHTEN]:
            ok = stuur_telegram(maak_bericht(woning))
            status = "✅" if ok else "❌"
            print(f"  {status} {woning.get('titel', '?')}")
            time.sleep(1)  # Telegram rate limit: max ~1 msg/sec

        if len(nieuw) > MAX_BERICHTEN:
            stuur_telegram(
                f"⚠️ Nog {len(nieuw) - MAX_BERICHTEN} extra nieuwe woningen "
                "gevonden — check de site voor de rest."
            )

    # ── seen.json bijwerken (gezien + nieuw, max 5000 om file klein te houden) ──
    alle = gezien | huidige_links
    if len(alle) > 5000:
        # Houd alleen links die nog in de huidige scrape zitten + recente historie
        alle = huidige_links | set(sorted(alle)[-4000:])
    SEEN_FILE.write_text(
        json.dumps(sorted(alle), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"💾 seen.json bijgewerkt ({len(alle)} links)")


if __name__ == "__main__":
    main()
