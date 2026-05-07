"""
🏋️ BlitzHealth — Digest semanal de salud y entrenamiento → Telegram
====================================================================

Scrapea 5 fuentes RSS de salud/fitness cada domingo, filtra contenido
de los últimos 7 días, genera un resumen con Gemini y lo envía por
Telegram. Guarda cada digest como markdown en digests/health/.

Mismo patrón que elpais_telegram_bot.py: requests + ElementTree + Gemini REST.

Uso:
  pip install requests beautifulsoup4
  export TELEGRAM_BOT_TOKEN="..." TELEGRAM_CHAT_ID="..." GEMINI_API_KEY="..."
  python blitzhealth.py
"""

import os
import sys
import time
import logging
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

LOOKBACK_DAYS = 7

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

DIGESTS_DIR = Path(__file__).parent / "digests" / "health"

# ── Fuentes RSS ───────────────────────────────────────────────────
HEALTH_SOURCES: dict[str, str] = {
    "Marcos Vázquez (blog)": "https://www.fitnessrevolucionario.com/feed/",
    "Marcos Vázquez (podcast)": "https://www.fitnessrevolucionario.com/feed/podcast/",
    "Peter Attia": "https://peterattiamd.com/feed/",
    "Layne Norton": "https://feeds.megaphone.fm/BRMD7227498498",
    "Steve Magness": "https://stevemagness.substack.com/feed",
}

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("blitzhealth")


# ---------------------------------------------------------------------------
# HTTP helper
# ---------------------------------------------------------------------------

def _fetch_page(url: str) -> tuple[Optional[str], Optional[str]]:
    """Descarga una URL y devuelve (contenido, error)."""
    headers = {"User-Agent": USER_AGENT}
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        return resp.text, None
    except requests.RequestException as e:
        log.warning(f"Error al descargar {url}: {e}")
        return None, str(e)


# ---------------------------------------------------------------------------
# Scraper RSS
# ---------------------------------------------------------------------------

def fetch_rss_articles(
    author_name: str, feed_url: str, cutoff: datetime,
    errors: Optional[list] = None,
) -> list[dict]:
    """Extrae artículos/episodios recientes de un feed RSS/Atom."""
    xml_text, err = _fetch_page(feed_url)
    if not xml_text:
        log.error(f"RSS — {author_name}: {err}")
        if errors is not None and err:
            errors.append(f"{author_name}: {err}")
        return []

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        log.warning(f"Error al parsear RSS de {feed_url}: {e}")
        return []

    articles: list[dict] = []

    # Nombre de la fuente desde el feed
    channel = root.find("channel")
    source_name = "Blog"
    if channel is not None:
        title_el = channel.find("title")
        if title_el is not None and title_el.text:
            source_name = title_el.text.strip()

    # Buscar items (RSS) o entry (Atom)
    items = root.findall(".//item")
    if not items:
        # Intentar Atom
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        items = root.findall(".//atom:entry", ns)

    for item in items:
        title_el = item.find("title")
        link_el = item.find("link")
        pub_el = item.find("pubDate")
        desc_el = item.find("description")

        # Atom fallbacks
        if link_el is None:
            ns = {"atom": "http://www.w3.org/2005/Atom"}
            link_el = item.find("atom:link", ns)
        if pub_el is None:
            pub_el = item.find("published") or item.find(
                "{http://www.w3.org/2005/Atom}published"
            ) or item.find("updated") or item.find(
                "{http://www.w3.org/2005/Atom}updated"
            )

        if title_el is None:
            continue

        title = (title_el.text or "").strip()

        # Extraer URL
        href = ""
        if link_el is not None:
            href = link_el.text or link_el.get("href", "") or ""
        href = href.strip()

        if not href:
            continue

        # Parsear fecha
        pub_date = None
        if pub_el is not None and pub_el.text:
            try:
                pub_date = parsedate_to_datetime(pub_el.text)
            except (ValueError, TypeError):
                try:
                    pub_date = datetime.fromisoformat(
                        pub_el.text.replace("Z", "+00:00")
                    )
                except (ValueError, TypeError):
                    pass

        if pub_date and pub_date < cutoff:
            continue

        # Descripción
        subtitle = ""
        if desc_el is not None and desc_el.text:
            subtitle = unescape(desc_el.text)
            subtitle = BeautifulSoup(subtitle, "html.parser").get_text(strip=True)

        # Content:encoded (algunos feeds ponen el contenido completo aquí)
        content_el = item.find("{http://purl.org/rss/1.0/modules/content/}encoded")
        full_content = ""
        if content_el is not None and content_el.text:
            full_content = BeautifulSoup(
                unescape(content_el.text), "html.parser"
            ).get_text(strip=True)

        articles.append({
            "title": title,
            "url": href,
            "author": author_name,
            "source": source_name,
            "date": pub_date,
            "subtitle": subtitle[:300],
            "content": full_content[:2000],
        })

    return articles


# ---------------------------------------------------------------------------
# Recopilar todas las fuentes
# ---------------------------------------------------------------------------

def fetch_all_sources(
    errors: Optional[list] = None,
) -> dict[str, list[dict]]:
    """Scrapea las 5 fuentes y devuelve {autor: [artículos]}.

    Si se pasa `errors`, acumula ahí los fallos de fetch por fuente.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)
    result: dict[str, list[dict]] = {}

    for author, feed_url in HEALTH_SOURCES.items():
        log.info(f"Scrapeando: {author}")
        articles = fetch_rss_articles(author, feed_url, cutoff, errors)
        if articles:
            result[author] = articles
            log.info(f"  → {len(articles)} artículos/episodios encontrados")
        else:
            log.info(f"  → Sin contenido nuevo esta semana")

    return result


# ---------------------------------------------------------------------------
# Gemini
# ---------------------------------------------------------------------------

def generate_health_digest(sources_data: dict[str, list[dict]]) -> Optional[str]:
    """Envía el contenido semanal a Gemini para generar el digest."""
    if not GEMINI_API_KEY:
        log.error("Falta GEMINI_API_KEY.")
        return None

    if not sources_data:
        log.info("Sin contenido nuevo esta semana, nada que resumir.")
        return None

    # Construir el bloque de contenido para el prompt
    content_block = ""
    for author, articles in sources_data.items():
        content_block += f"\n== {author} ==\n"
        for art in articles:
            date_str = art["date"].strftime("%d/%m") if art["date"] else "?"
            content_block += f"\n[{date_str}] {art['title']}\n"
            content_block += f"URL: {art['url']}\n"
            if art.get("content"):
                content_block += f"{art['content'][:1500]}\n"
            elif art.get("subtitle"):
                content_block += f"{art['subtitle']}\n"

    today = datetime.now(ZoneInfo("Europe/Madrid")).strftime("%d/%m/%Y")

    prompt = f"""Hoy es {today}. Eres un editor especializado en salud, fitness y longevidad.

A partir del contenido publicado esta semana por estos autores de referencia, genera un digest semanal en español con esta estructura exacta:

1. 📋 RESUMEN POR AUTOR
Para cada autor que haya publicado algo, resume brevemente qué ha publicado (título + 1-2 frases sobre el contenido). Incluye el enlace a cada artículo/episodio.

2. 🎯 PUNTOS CLAVE ACCIONABLES
Las 3-5 ideas más prácticas y aplicables de todo lo publicado esta semana. Cosas concretas que alguien puede hacer.

REGLAS:
- Todo en español
- Conciso pero completo
- No uses asteriscos ni formato markdown con ** (usa texto plano con los emojis de sección)
- No añadas introducción genérica ni cierre motivacional
- Si un autor no ha publicado nada esta semana, indícalo brevemente

CONTENIDO DE LA SEMANA:
{content_block}"""

    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        "gemini-2.5-flash:generateContent"
        f"?key={GEMINI_API_KEY}"
    )
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"maxOutputTokens": 8192},
    }

    # Hasta 3 intentos con espera entre ellos: Gemini 2.5 Flash devuelve
    # 503/overload con frecuencia y un solo fallo no debe tirar el digest.
    for attempt in range(3):
        try:
            resp = requests.post(
                url,
                headers={"content-type": "application/json"},
                json=payload,
                timeout=90,
            )
            resp.raise_for_status()
            data = resp.json()
            candidates = data.get("candidates", [])
            if candidates:
                parts = candidates[0].get("content", {}).get("parts", [])
                if parts and parts[0].get("text"):
                    return parts[0]["text"]
            log.error(
                f"Respuesta inesperada de Gemini (intento {attempt + 1}): {data}"
            )
        except requests.RequestException as e:
            log.error(
                f"Error al llamar a Gemini API (intento {attempt + 1}): {e}"
            )

        if attempt < 2:
            log.info("Reintentando en 10 segundos...")
            time.sleep(10)

    return None


# ---------------------------------------------------------------------------
# Guardar digest como markdown
# ---------------------------------------------------------------------------

def save_digest_markdown(digest: str, date: datetime) -> Path:
    """Guarda el digest en digests/health/YYYY-MM-DD.md."""
    DIGESTS_DIR.mkdir(parents=True, exist_ok=True)
    filename = date.strftime("%Y-%m-%d") + ".md"
    filepath = DIGESTS_DIR / filename

    header = f"# BlitzHealth — Semana del {date.strftime('%d/%m/%Y')}\n\n"
    filepath.write_text(header + digest, encoding="utf-8")

    log.info(f"Digest guardado en {filepath}")
    return filepath


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------

def _split_message(text: str, max_len: int = 4096) -> list[str]:
    """Parte un mensaje largo en trozos respetando saltos de línea."""
    if len(text) <= max_len:
        return [text]

    chunks: list[str] = []
    while text:
        if len(text) <= max_len:
            chunks.append(text)
            break
        cut = text.rfind("\n\n", 0, max_len)
        if cut == -1:
            cut = text.rfind("\n", 0, max_len)
        if cut == -1:
            cut = max_len
        chunks.append(text[:cut])
        text = text[cut:].lstrip("\n")
    return chunks


def send_telegram_text(text: str) -> bool:
    """Envía un mensaje de texto plano por Telegram (un único chunk)."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log.error("Falta TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID.")
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        resp = requests.post(
            url,
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text[:4000]},
            timeout=15,
        )
        resp.raise_for_status()
        return bool(resp.json().get("ok"))
    except requests.RequestException as e:
        log.error(f"Error al enviar a Telegram: {e}")
        return False


def send_telegram_digest(digest: str) -> bool:
    """Envía el digest por Telegram como texto plano."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log.error("Falta TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID.")
        return False

    now = datetime.now(ZoneInfo("Europe/Madrid"))
    date_str = now.strftime("%d/%m/%Y")
    message = f"🏋️ BLITZHEALTH — Semana del {date_str}\n\n{digest}"

    success = True
    for chunk in _split_message(message, max_len=3000):
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": chunk,
        }
        try:
            resp = requests.post(url, json=payload, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            if not data.get("ok"):
                log.error(f"Error de Telegram: {data}")
                success = False
        except requests.RequestException as e:
            log.error(f"Error al enviar: {e}")
            success = False

    if success:
        log.info("Digest enviado correctamente a Telegram.")
    return success


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    log.info("=== BlitzHealth — Digest semanal ===")

    # 1. Scrapear fuentes
    fetch_errors: list[str] = []
    sources_data = fetch_all_sources(fetch_errors)

    total = sum(len(arts) for arts in sources_data.values())
    log.info(f"Total: {total} artículos/episodios de {len(sources_data)} autores.")

    # Si TODAS las fuentes fallaron / vinieron vacías, mejor avisar a
    # Telegram que quedarse callado un domingo entero.
    if not sources_data:
        msg = "⚠️ BlitzHealth: ninguna fuente devolvió contenido esta semana."
        if fetch_errors:
            msg += "\n\nFuentes con error:\n" + "\n".join(
                f"• {e}" for e in fetch_errors
            )
        else:
            msg += "\nLos feeds responden bien pero ninguno tiene artículos en los últimos 7 días."
        send_telegram_text(msg)
        log.warning("Sin contenido — notificado a Telegram. Abortando.")
        sys.exit(1)

    # 2. Generar digest con Gemini
    log.info("Generando digest con Gemini...")
    digest = generate_health_digest(sources_data)

    if not digest:
        log.warning("No se pudo generar el digest. Abortando.")
        send_telegram_text(
            "⚠️ BlitzHealth: Gemini no respondió tras 3 intentos. "
            "Sin digest esta semana."
        )
        sys.exit(1)

    # 3. Guardar como markdown
    now = datetime.now(ZoneInfo("Europe/Madrid"))
    save_digest_markdown(digest, now)

    # 4. Enviar por Telegram
    success = send_telegram_digest(digest)
    if not success:
        log.error("Fallo al enviar por Telegram.")
        sys.exit(1)

    log.info("=== BlitzHealth completado ===")


if __name__ == "__main__":
    main()
