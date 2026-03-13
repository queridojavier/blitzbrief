"""
📰 El País → Telegram: Resumen diario de tus columnistas favoritos
====================================================================

Este script consulta las páginas de autor de El País, detecta artículos
publicados en las últimas 24 horas y envía un resumen por Telegram.

Pensado para ejecutarse una vez al día (por ejemplo, a las 8:00 AM)
mediante GitHub Actions, cron, o cualquier scheduler.

Configuración:
  1. Crea un bot de Telegram con @BotFather y copia el token.
  2. Obtén tu chat_id enviando un mensaje al bot y consultando
     https://api.telegram.org/bot<TOKEN>/getUpdates
  3. Configura las variables de entorno (o edita el dict AUTHORS y las
     constantes TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID más abajo).

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

# Autores a seguir.
# Clave: nombre para mostrar | Valor: slug del autor en El País
# Para encontrar el slug, ve a https://elpais.com y busca la página del
# autor. La URL será algo como https://elpais.com/autor/manuel-jabois-sueiro/
# El slug es "manuel-jabois-sueiro".
AUTHORS: dict[str, str] = {
    "Manuel Jabois": "manuel-jabois-sueiro",
    "Juan José Millás": "juan-jose-millas",
    "Javier Cercas": "javier-cercas",
    "Rosa Montero": "rosa-montero",
    "Leila Guerriero": "leila-guerriero",
    "Sergio del Molino": "sergio-del-molino",
    "Kiko Llaneras": "kiko-llaneras",
    "Jose Luis Sastre": "jose-luis-sastre",

    # --- Añade aquí más autores ---
    # "Luz Sánchez-Mellado": "luz-sanchez-mellado",
    # "Rosa Montero": "rosa-montero",
    # "Leila Guerriero": "leila-guerriero",
    # "Elvira Lindo": "elvira-lindo",
    # "Sergio del Molino": "sergio-del-molino",
    # "Benjamín Prado": "benjamin-prado",
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


def fetch_author_page(slug: str) -> Optional[str]:
    """Descarga la página de autor de El País."""
    url = f"https://elpais.com/autor/{slug}/"
    headers = {"User-Agent": USER_AGENT}
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        return resp.text
    except requests.RequestException as e:
        log.warning(f"Error al descargar {url}: {e}")
        return None


def parse_articles(html: str, author_name: str, cutoff: datetime) -> list[dict]:
    """
    Extrae artículos de la página de autor.

    El País estructura sus páginas de autor con elementos <article> que
    contienen un <h2> con enlace y un <time> con la fecha.
    """
    soup = BeautifulSoup(html, "html.parser")
    articles = []

    for article_el in soup.select("article"):
        # Buscar el enlace del titular
        link_el = article_el.select_one("h2 a")
        if not link_el:
            continue

        title = link_el.get_text(strip=True)
        href = link_el.get("href", "")

        # Normalizar URL
        if href.startswith("/"):
            href = f"https://elpais.com{href}"

        # Buscar fecha
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

        # Buscar subtítulo / entradilla
        subtitle_el = article_el.select_one("p")
        subtitle = subtitle_el.get_text(strip=True) if subtitle_el else ""

        # Buscar etiqueta de sección (Columna, Opinión, etc.)
        tag_el = article_el.select_one("span.c_ty, .c_ty, .a_ti_s")
        tag = tag_el.get_text(strip=True) if tag_el else ""

        # Filtrar por fecha si tenemos la info
        if pub_date and pub_date < cutoff:
            continue

        articles.append({
            "title": title,
            "url": href,
            "author": author_name,
            "date": pub_date,
            "subtitle": subtitle,
            "tag": tag,
        })

    return articles


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
        lines.append(f"✍️ *{_escape_md(author)}*")
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
    log.info("Iniciando revisión de artículos de El País...")

    cutoff = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)
    seen = load_seen_articles()
    all_new_articles: list[dict] = []

    for author_name, slug in AUTHORS.items():
        log.info(f"Consultando: {author_name} ({slug})")
        html = fetch_author_page(slug)
        if not html:
            continue

        articles = parse_articles(html, author_name, cutoff)

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
