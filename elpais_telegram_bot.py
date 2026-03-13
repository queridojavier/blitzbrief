"""
📰 Vigía — Resumen diario de tus columnistas favoritos → Telegram
==================================================================

Este script consulta las páginas de autor de El País y El Plural,
detecta artículos publicados en las últimas 24 horas y envía un
resumen por Telegram.

Pensado para ejecutarse una vez al día (por ejemplo, a las 8:00 AM)
mediante GitHub Actions, cron, o cualquier scheduler.

Configuración:
  1. Crea un bot de Telegram con @BotFather y copia el token.
  2. Obtén tu chat_id enviando un mensaje al bot y consultando
     https://api.telegram.org/bot<TOKEN>/getUpdates
  3. Configura las variables de entorno (o edita los dicts de autores
     y las constantes TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID más abajo).

Uso local:
  pip install requests beautifulsoup4
  export TELEGRAM_BOT_TOKEN="tu_token"
  export TELEGRAM_CHAT_ID="tu_chat_id"
  python elpais_telegram_bot.py
"""

import os
import sys
import json
import logging
import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import requests
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------

# Token del bot de Telegram y chat_id del destinatario.
# Se leen de variables de entorno (ideal para GitHub Actions / CI).
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# ── Autores de El País ─────────────────────────────────────────────
# Clave: nombre para mostrar | Valor: slug del autor en El País
# URL: https://elpais.com/autor/<slug>/
ELPAIS_AUTHORS: dict[str, str] = {
    "Manuel Jabois": "manuel-jabois-sueiro",
    "Juan José Millás": "juan-jose-millas",
    "Javier Cercas": "javier-cercas",
    "Rosa Montero": "rosa-montero",
    "Leila Guerriero": "leila-guerriero",
    "Sergio del Molino": "sergio-del-molino-molina",
    "Kiko Llaneras": "francisco-llaneras-estrada",
    "José Luis Sastre": "jose-luis-sastre-cebolla",
    # --- Añade aquí más autores de El País ---
    # "Luz Sánchez-Mellado": "luz-sanchez-mellado",
    # "Elvira Lindo": "elvira-lindo",
}

# ── Autores de El Plural ──────────────────────────────────────────
# Clave: nombre para mostrar | Valor: slug del tag en El Plural
# URL: https://www.elplural.com/tag/<slug>
ELPLURAL_AUTHORS: dict[str, str] = {
    "Benjamín Prado": "benjamin-prado",
    # --- Añade aquí más autores de El Plural ---
}

# Ventana temporal: artículos publicados en las últimas N horas
LOOKBACK_HOURS = 26  # 26h para cubrir holgadamente un día completo

# User-Agent para las peticiones HTTP
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# Archivo local para evitar enviar duplicados entre ejecuciones
SEEN_FILE = Path(__file__).parent / ".elpais_seen_articles.json"

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Funciones auxiliares
# ---------------------------------------------------------------------------


def load_seen_articles() -> set[str]:
    """Carga los hashes de artículos ya enviados."""
    if SEEN_FILE.exists():
        try:
            data = json.loads(SEEN_FILE.read_text())
            return set(data.get("seen", []))
        except (json.JSONDecodeError, KeyError):
            return set()
    return set()


def save_seen_articles(seen: set[str]) -> None:
    """Guarda los hashes de artículos ya enviados (máx. 500 últimos)."""
    trimmed = list(seen)[-500:]
    SEEN_FILE.write_text(json.dumps({"seen": trimmed}))


def article_hash(url: str) -> str:
    return hashlib.md5(url.encode()).hexdigest()


def _fetch_page(url: str) -> Optional[str]:
    """Descarga una URL y devuelve el HTML."""
    headers = {"User-Agent": USER_AGENT}
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        return resp.text
    except requests.RequestException as e:
        log.warning(f"Error al descargar {url}: {e}")
        return None


# ── Scraper: El País ──────────────────────────────────────────────


def fetch_elpais_articles(
    author_name: str, slug: str, cutoff: datetime
) -> list[dict]:
    """Extrae artículos recientes de la página de autor de El País."""
    url = f"https://elpais.com/autor/{slug}/"
    html = _fetch_page(url)
    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")
    articles = []

    for article_el in soup.select("article"):
        link_el = article_el.select_one("h2 a")
        if not link_el:
            continue

        title = link_el.get_text(strip=True)
        href = link_el.get("href", "")
        if href.startswith("/"):
            href = f"https://elpais.com{href}"

        # Fecha
        time_el = article_el.select_one("time")
        pub_date = None
        if time_el:
            datetime_attr = time_el.get("datetime", "")
            if datetime_attr:
                try:
                    pub_date = datetime.fromisoformat(
                        datetime_attr.replace("Z", "+00:00")
                    )
                except ValueError:
                    pass

        # Subtítulo / entradilla
        subtitle_el = article_el.select_one("p")
        subtitle = subtitle_el.get_text(strip=True) if subtitle_el else ""

        # Etiqueta de sección
        tag_el = article_el.select_one("span.c_ty, .c_ty, .a_ti_s")
        tag = tag_el.get_text(strip=True) if tag_el else ""

        if pub_date and pub_date < cutoff:
            continue

        articles.append({
            "title": title,
            "url": href,
            "author": author_name,
            "source": "El País",
            "date": pub_date,
            "subtitle": subtitle,
            "tag": tag,
        })

    return articles


# ── Scraper: El Plural ────────────────────────────────────────────


def fetch_elplural_articles(
    author_name: str, slug: str, cutoff: datetime
) -> list[dict]:
    """
    Extrae artículos recientes del tag de autor en El Plural.

    El Plural no muestra fechas en el listado, así que visitamos cada
    artículo para extraer la fecha del meta tag article:published_time.
    Solo comprobamos los primeros 5 artículos del listado (ya están
    ordenados de más reciente a más antiguo).
    """
    url = f"https://www.elplural.com/tag/{slug}"
    html = _fetch_page(url)
    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")
    articles = []

    # Los artículos están en divs .item con un <h3><a>
    items = soup.select("div.item h3 a")[:5]

    for link_el in items:
        title = link_el.get_text(strip=True)
        href = link_el.get("href", "")
        if href.startswith("/"):
            href = f"https://www.elplural.com{href}"

        # Obtener fecha del artículo individual
        pub_date = _fetch_elplural_article_date(href)

        if pub_date and pub_date < cutoff:
            continue

        # Subtítulo del listado (hermano p.excerpt)
        parent_item = link_el.find_parent("div", class_="item")
        subtitle = ""
        if parent_item:
            excerpt_el = parent_item.select_one("p.excerpt")
            if excerpt_el:
                subtitle = excerpt_el.get_text(strip=True)

        articles.append({
            "title": title,
            "url": href,
            "author": author_name,
            "source": "El Plural",
            "date": pub_date,
            "subtitle": subtitle,
            "tag": "",
        })

    return articles


def _fetch_elplural_article_date(url: str) -> Optional[datetime]:
    """Extrae la fecha de publicación de un artículo de El Plural."""
    html = _fetch_page(url)
    if not html:
        return None
    soup = BeautifulSoup(html, "html.parser")
    meta = soup.select_one('meta[property="article:published_time"]')
    if meta and meta.get("content"):
        try:
            return datetime.fromisoformat(meta["content"])
        except ValueError:
            pass
    return None


def format_telegram_message(all_articles: list[dict]) -> str:
    """Formatea el mensaje de Telegram con Markdown."""
    now = datetime.now(timezone.utc).astimezone(
        timezone(timedelta(hours=1))  # CET
    )
    date_str = now.strftime("%A %d de %B de %Y").capitalize()

    lines = [f"📰 *Tu prensa del día*", f"_{date_str}_", ""]

    # Agrupar por autor
    by_author: dict[str, list[dict]] = {}
    for art in all_articles:
        by_author.setdefault(art["author"], []).append(art)

    for author, articles in by_author.items():
        source = articles[0].get("source", "")
        source_label = f" \\({_escape_md(source)}\\)" if source else ""
        lines.append(f"✍️ *{_escape_md(author)}*{source_label}")
        for art in articles:
            title = _escape_md(art["title"])
            tag = f" _{_escape_md(art['tag'])}_" if art.get("tag") else ""
            lines.append(f"  • [{title}]({art['url']}){tag}")
            if art.get("subtitle"):
                sub = _escape_md(art["subtitle"][:120])
                lines.append(f"    _{sub}_")
        lines.append("")

    if not all_articles:
        lines.append("Hoy no hay artículos nuevos de tus autores\\. ¡Día libre\\! 📚")

    return "\n".join(lines)


def _escape_md(text: str) -> str:
    """Escapa caracteres especiales para MarkdownV2 de Telegram."""
    special = r"_*[]()~`>#+-=|{}.!"
    for ch in special:
        text = text.replace(ch, f"\\{ch}")
    return text


def send_telegram_message(text: str) -> bool:
    """Envía un mensaje por Telegram."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log.error(
            "Falta TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID. "
            "Configura las variables de entorno."
        )
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "MarkdownV2",
        "disable_web_page_preview": True,
    }

    try:
        resp = requests.post(url, json=payload, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        if data.get("ok"):
            log.info("Mensaje enviado correctamente a Telegram.")
            return True
        else:
            log.error(f"Error de Telegram: {data}")
            return False
    except requests.RequestException as e:
        log.error(f"Error al enviar mensaje: {e}")
        return False


# ---------------------------------------------------------------------------
# Flujo principal
# ---------------------------------------------------------------------------


def main():
    log.info("Iniciando Vigía — revisión de artículos...")

    cutoff = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)
    seen = load_seen_articles()
    all_new_articles: list[dict] = []

    # ── El País ───────────────────────────────────────────────────
    for author_name, slug in ELPAIS_AUTHORS.items():
        log.info(f"[El País] Consultando: {author_name} ({slug})")
        articles = fetch_elpais_articles(author_name, slug, cutoff)
        for art in articles:
            h = article_hash(art["url"])
            if h not in seen:
                all_new_articles.append(art)
                seen.add(h)
        log.info(f"  → {len(articles)} artículo(s) reciente(s)")

    # ── El Plural ─────────────────────────────────────────────────
    for author_name, slug in ELPLURAL_AUTHORS.items():
        log.info(f"[El Plural] Consultando: {author_name} ({slug})")
        articles = fetch_elplural_articles(author_name, slug, cutoff)
        for art in articles:
            h = article_hash(art["url"])
            if h not in seen:
                all_new_articles.append(art)
                seen.add(h)
        log.info(f"  → {len(articles)} artículo(s) reciente(s)")

    # Ordenar por fecha (más reciente primero)
    all_new_articles.sort(
        key=lambda a: a.get("date") or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )

    if all_new_articles:
        message = format_telegram_message(all_new_articles)
        log.info(
            f"Encontrados {len(all_new_articles)} artículo(s) nuevo(s). Enviando..."
        )
        success = send_telegram_message(message)
        if success:
            save_seen_articles(seen)
    else:
        log.info("No hay artículos nuevos hoy.")
        # Opción: enviar igualmente un mensaje de "nada nuevo"
        # Descomenta las siguientes líneas si quieres recibir notificación
        # incluso cuando no haya artículos nuevos:
        # message = format_telegram_message([])
        # send_telegram_message(message)

    save_seen_articles(seen)
    log.info("Hecho.")


if __name__ == "__main__":
    main()
