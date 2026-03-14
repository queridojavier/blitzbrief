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
import random
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
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

# ── Autores (cargados desde authors.json) ─────────────────────────
# El archivo authors.json contiene tres secciones:
#   "elpais":   { "Nombre": "slug" }
#   "elplural": { "Nombre": "slug" }
#   "rss":      { "Nombre": "url-del-feed" }
# Se pueden añadir/eliminar con los comandos /add y /remove del bot.

ELPAIS_AUTHORS: dict[str, str] = {}
ELPLURAL_AUTHORS: dict[str, str] = {}
RSS_AUTHORS: dict[str, str] = {}

# Ventana temporal: artículos publicados en las últimas N horas
LOOKBACK_HOURS = 26  # 26h para cubrir holgadamente un día completo

# User-Agent para las peticiones HTTP
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# Archivo local para evitar enviar duplicados entre ejecuciones
SEEN_FILE = Path(__file__).parent / ".elpais_seen_articles.json"
AUTHORS_FILE = Path(__file__).parent / "authors.json"

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Funciones auxiliares
# ---------------------------------------------------------------------------


def load_authors() -> None:
    """Carga los autores desde authors.json a las variables globales."""
    global ELPAIS_AUTHORS, ELPLURAL_AUTHORS, RSS_AUTHORS
    if AUTHORS_FILE.exists():
        try:
            data = json.loads(AUTHORS_FILE.read_text(encoding="utf-8"))
            ELPAIS_AUTHORS = data.get("elpais", {})
            ELPLURAL_AUTHORS = data.get("elplural", {})
            RSS_AUTHORS = data.get("rss", {})
            log.info(
                f"Autores cargados: {len(ELPAIS_AUTHORS)} El País, "
                f"{len(ELPLURAL_AUTHORS)} El Plural, {len(RSS_AUTHORS)} RSS"
            )
        except (json.JSONDecodeError, KeyError) as e:
            log.error(f"Error al leer {AUTHORS_FILE}: {e}")
    else:
        log.warning(f"No se encontró {AUTHORS_FILE}. Sin autores configurados.")


def save_authors() -> None:
    """Guarda los autores actuales a authors.json."""
    data = {
        "elpais": ELPAIS_AUTHORS,
        "elplural": ELPLURAL_AUTHORS,
        "rss": RSS_AUTHORS,
    }
    AUTHORS_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    log.info("Autores guardados en authors.json")


def add_author(source: str, name: str, slug_or_url: str) -> str:
    """Añade un autor. Devuelve mensaje de resultado."""
    source = source.lower()
    if source == "elpais":
        if name in ELPAIS_AUTHORS:
            return f"⚠️ {name} ya está en El País."
        ELPAIS_AUTHORS[name] = slug_or_url
        save_authors()
        return f"✅ {name} añadido a El País (slug: {slug_or_url})"
    elif source == "elplural":
        if name in ELPLURAL_AUTHORS:
            return f"⚠️ {name} ya está en El Plural."
        ELPLURAL_AUTHORS[name] = slug_or_url
        save_authors()
        return f"✅ {name} añadido a El Plural (slug: {slug_or_url})"
    elif source == "rss":
        if name in RSS_AUTHORS:
            return f"⚠️ {name} ya está en RSS."
        RSS_AUTHORS[name] = slug_or_url
        save_authors()
        return f"✅ {name} añadido como RSS (feed: {slug_or_url})"
    else:
        return (
            f"❌ Fuente '{source}' no reconocida.\n"
            "Usa: elpais, elplural o rss"
        )


def remove_author(name: str) -> str:
    """Elimina un autor de cualquier fuente. Devuelve mensaje de resultado."""
    if name in ELPAIS_AUTHORS:
        del ELPAIS_AUTHORS[name]
        save_authors()
        return f"🗑 {name} eliminado de El País."
    elif name in ELPLURAL_AUTHORS:
        del ELPLURAL_AUTHORS[name]
        save_authors()
        return f"🗑 {name} eliminado de El Plural."
    elif name in RSS_AUTHORS:
        del RSS_AUTHORS[name]
        save_authors()
        return f"🗑 {name} eliminado de RSS."
    else:
        return f"❌ No se encontró a '{name}' en ninguna fuente."


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


def _fetch_page(url: str) -> tuple[Optional[str], Optional[str]]:
    """Descarga una URL y devuelve (html, error).

    Si todo va bien: (html, None).
    Si falla:        (None, descripción del error).
    """
    headers = {"User-Agent": USER_AGENT}
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        return resp.text, None
    except requests.RequestException as e:
        log.warning(f"Error al descargar {url}: {e}")
        return None, str(e)


# ── Scraper: El País ──────────────────────────────────────────────


def fetch_elpais_articles(
    author_name: str, slug: str, cutoff: datetime, errors: Optional[list] = None
) -> list[dict]:
    """Extrae artículos recientes de la página de autor de El País."""
    url = f"https://elpais.com/autor/{slug}/"
    html, err = _fetch_page(url)
    if not html:
        if errors is not None and err:
            errors.append(f"El País — {author_name}: {err}")
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
    author_name: str, slug: str, cutoff: datetime, errors: Optional[list] = None
) -> list[dict]:
    """
    Extrae artículos recientes del tag de autor en El Plural.

    El Plural no muestra fechas en el listado, así que visitamos cada
    artículo para extraer la fecha del meta tag article:published_time.
    Solo comprobamos los primeros 5 artículos del listado (ya están
    ordenados de más reciente a más antiguo).
    """
    url = f"https://www.elplural.com/tag/{slug}"
    html, err = _fetch_page(url)
    if not html:
        if errors is not None and err:
            errors.append(f"El Plural — {author_name}: {err}")
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
    html, _ = _fetch_page(url)
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


# ── Scraper: RSS (Substack, blogs, etc.) ──────────────────────────


def fetch_rss_articles(
    author_name: str, feed_url: str, cutoff: datetime, errors: Optional[list] = None
) -> list[dict]:
    """Extrae artículos recientes de un feed RSS/Atom."""
    xml_text, err = _fetch_page(feed_url)
    if not xml_text:
        if errors is not None and err:
            errors.append(f"RSS — {author_name}: {err}")
        return []

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        log.warning(f"Error al parsear RSS de {feed_url}: {e}")
        if errors is not None:
            errors.append(f"RSS — {author_name}: XML inválido")
        return []

    articles = []

    # Detectar nombre de la fuente desde el feed
    channel = root.find("channel")
    source_name = "Blog"
    if channel is not None:
        title_el = channel.find("title")
        if title_el is not None and title_el.text:
            source_name = title_el.text.strip()

    for item in root.findall(".//item"):
        title_el = item.find("title")
        link_el = item.find("link")
        pub_el = item.find("pubDate")
        desc_el = item.find("description")

        if not title_el or not link_el:
            continue

        title = title_el.text or ""
        href = link_el.text or ""

        # Parsear fecha RFC 2822 (formato RSS estándar)
        pub_date = None
        if pub_el is not None and pub_el.text:
            try:
                pub_date = parsedate_to_datetime(pub_el.text)
            except (ValueError, TypeError):
                pass

        if pub_date and pub_date < cutoff:
            continue

        subtitle = ""
        if desc_el is not None and desc_el.text:
            # Limpiar HTML básico de la descripción
            from html import unescape
            subtitle = unescape(desc_el.text)
            # Eliminar tags HTML
            subtitle = BeautifulSoup(subtitle, "html.parser").get_text(strip=True)

        articles.append({
            "title": title.strip(),
            "url": href.strip(),
            "author": author_name,
            "source": source_name,
            "date": pub_date,
            "subtitle": subtitle[:150],
            "tag": "",
        })

    return articles


# ── Random: artículo aleatorio de El País ─────────────────────────


def fetch_random_elpais_article(slug: str) -> Optional[dict]:
    """Elige un artículo al azar de la página de un autor de El País.

    Escoge una página aleatoria (de la 1 a la 20) y luego un artículo
    aleatorio de esa página. Si la página no tiene artículos, prueba
    con otra (hasta 3 intentos).
    """
    pages_to_try = random.sample(range(1, 21), k=min(3, 20))

    for page_num in pages_to_try:
        url = f"https://elpais.com/autor/{slug}/{page_num}/"
        html, _ = _fetch_page(url)
        if not html:
            continue

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

            # Subtítulo
            subtitle_el = article_el.select_one("p")
            subtitle = subtitle_el.get_text(strip=True) if subtitle_el else ""

            # Sección
            tag_el = article_el.select_one("span.c_ty, .c_ty, .a_ti_s")
            tag = tag_el.get_text(strip=True) if tag_el else ""

            # Fecha
            time_el = article_el.select_one("time")
            date_str = ""
            if time_el:
                dt_attr = time_el.get("datetime", "")
                if dt_attr:
                    try:
                        pub = datetime.fromisoformat(
                            dt_attr.replace("Z", "+00:00")
                        )
                        date_str = pub.strftime("%d/%m/%Y")
                    except ValueError:
                        pass

            articles.append({
                "title": title,
                "url": href,
                "subtitle": subtitle,
                "tag": tag,
                "date_str": date_str,
            })

        if articles:
            return random.choice(articles)

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
# Lógica del digest (reutilizable)
# ---------------------------------------------------------------------------


def run_digest(notify_empty: bool = False) -> None:
    """Ejecuta el ciclo completo: scraping → formateo → envío por Telegram.

    Args:
        notify_empty: si True, envía mensaje incluso cuando no hay artículos.
    """
    log.info("Iniciando Vigía — revisión de artículos...")

    cutoff = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)
    seen = load_seen_articles()
    all_new_articles: list[dict] = []
    fetch_errors: list[str] = []

    # ── El País ───────────────────────────────────────────────────
    for author_name, slug in ELPAIS_AUTHORS.items():
        log.info(f"[El País] Consultando: {author_name} ({slug})")
        articles = fetch_elpais_articles(author_name, slug, cutoff, fetch_errors)
        for art in articles:
            h = article_hash(art["url"])
            if h not in seen:
                all_new_articles.append(art)
                seen.add(h)
        log.info(f"  → {len(articles)} artículo(s) reciente(s)")

    # ── El Plural ─────────────────────────────────────────────────
    for author_name, slug in ELPLURAL_AUTHORS.items():
        log.info(f"[El Plural] Consultando: {author_name} ({slug})")
        articles = fetch_elplural_articles(author_name, slug, cutoff, fetch_errors)
        for art in articles:
            h = article_hash(art["url"])
            if h not in seen:
                all_new_articles.append(art)
                seen.add(h)
        log.info(f"  → {len(articles)} artículo(s) reciente(s)")

    # ── RSS (blogs) ───────────────────────────────────────────────
    for author_name, feed_url in RSS_AUTHORS.items():
        log.info(f"[RSS] Consultando: {author_name}")
        articles = fetch_rss_articles(author_name, feed_url, cutoff, fetch_errors)
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
        log.info("No hay artículos nuevos.")
        if notify_empty:
            message = format_telegram_message([])
            send_telegram_message(message)

    save_seen_articles(seen)

    # ── Alertas de errores ────────────────────────────────────────
    if fetch_errors:
        error_lines = [
            "⚠️ *Vigía — Errores al consultar fuentes*",
            "",
            f"_{_escape_md(str(len(fetch_errors)))} fuente\\(s\\) fallaron:_",
            "",
        ]
        for err in fetch_errors:
            error_lines.append(f"  🔴 {_escape_md(err)}")
        error_lines.append("")
        error_lines.append(
            "_Revisa que las URLs de autor sigan siendo válidas\\._"
        )
        send_telegram_message("\n".join(error_lines))
        log.warning(f"{len(fetch_errors)} fuente(s) con errores.")

    log.info("Hecho.")


# ---------------------------------------------------------------------------
# Modo bot interactivo (polling de Telegram)
# ---------------------------------------------------------------------------


def _get_updates(offset: int = 0) -> list[dict]:
    """Obtiene mensajes nuevos de Telegram con long polling."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
    params = {"offset": offset, "timeout": 30, "allowed_updates": ["message"]}
    try:
        resp = requests.get(url, params=params, timeout=35)
        data = resp.json()
        return data.get("result", [])
    except requests.RequestException as e:
        log.warning(f"Error en getUpdates: {e}")
        return []


def _handle_command(text: str, chat_id: int) -> None:
    """Procesa un comando recibido por Telegram."""
    cmd = text.strip().split()[0].lower().split("@")[0]  # /update@BotName → /update

    if cmd == "/update":
        send_telegram_message("🔄 _Consultando fuentes\\.\\.\\._")
        run_digest(notify_empty=True)

    elif cmd == "/add":
        # /add <fuente> <nombre> <slug_o_url>
        # Ejemplo: /add elpais Elvira Lindo elvira-lindo
        # Ejemplo: /add rss Kiko Llaneras https://example.com/feed
        parts = text.strip().split()
        if len(parts) < 4:
            send_telegram_message(
                "ℹ️ *Uso de /add:*\n\n"
                "`/add elpais Nombre Apellido slug`\n"
                "`/add elplural Nombre Apellido slug`\n"
                "`/add rss Nombre Apellido url\\-del\\-feed`\n\n"
                "*Ejemplos:*\n"
                "`/add elpais Elvira Lindo elvira\\-lindo`\n"
                "`/add rss Mi Blog https://blog\\.com/feed`"
            )
            return
        source = parts[1]
        # El slug/url es siempre la última palabra,
        # el nombre es todo lo que hay entre fuente y slug/url
        slug_or_url = parts[-1]
        name = " ".join(parts[2:-1])
        result = add_author(source, name, slug_or_url)
        send_telegram_message(_escape_md(result))

    elif cmd == "/remove":
        # /remove <nombre>
        # Ejemplo: /remove Elvira Lindo
        parts = text.strip().split()
        if len(parts) < 2:
            send_telegram_message(
                "ℹ️ *Uso de /remove:*\n\n"
                "`/remove Nombre Apellido`\n\n"
                "*Ejemplo:*\n"
                "`/remove Elvira Lindo`"
            )
            return
        name = " ".join(parts[1:])
        result = remove_author(name)
        send_telegram_message(_escape_md(result))

    elif cmd == "/random":
        # /random <nombre> — artículo aleatorio de un autor de El País
        parts = text.strip().split()
        if len(parts) < 2:
            # Sin nombre: elegir autor al azar de El País
            if not ELPAIS_AUTHORS:
                send_telegram_message(
                    _escape_md("❌ No hay autores de El País configurados.")
                )
                return
            author_name = random.choice(list(ELPAIS_AUTHORS.keys()))
        else:
            query = " ".join(parts[1:]).lower()
            # Buscar coincidencia parcial (ej. "jabois" → "Manuel Jabois")
            matches = [
                name for name in ELPAIS_AUTHORS
                if query in name.lower()
            ]
            if not matches:
                send_telegram_message(
                    _escape_md(f"❌ No encontré a '{' '.join(parts[1:])}' en El País.\n"
                               "Usa /status para ver los autores disponibles.")
                )
                return
            author_name = matches[0]

        slug = ELPAIS_AUTHORS[author_name]
        send_telegram_message(
            f"🎲 _Buscando artículo aleatorio de {_escape_md(author_name)}\\.\\.\\._"
        )

        article = fetch_random_elpais_article(slug)
        if article:
            lines = [
                f"🎲 *Artículo aleatorio de {_escape_md(author_name)}*",
                "",
            ]
            if article["tag"]:
                lines.append(f"_{_escape_md(article['tag'])}_")
            title_esc = _escape_md(article["title"])
            lines.append(f"📰 [{title_esc}]({article['url']})")
            if article["subtitle"]:
                lines.append(f"_{_escape_md(article['subtitle'][:150])}_")
            if article["date_str"]:
                lines.append(f"\n📅 {_escape_md(article['date_str'])}")
            send_telegram_message("\n".join(lines))
        else:
            send_telegram_message(
                _escape_md(f"❌ No pude obtener artículos de {author_name}.")
            )

    elif cmd == "/status":
        lines = ["🔎 *Vigía — Estado*", ""]
        lines.append(f"*El País* \\({_escape_md(str(len(ELPAIS_AUTHORS)))} autores\\)")
        for name in ELPAIS_AUTHORS:
            lines.append(f"  • {_escape_md(name)}")
        lines.append("")
        lines.append(
            f"*El Plural* \\({_escape_md(str(len(ELPLURAL_AUTHORS)))} autores\\)"
        )
        for name in ELPLURAL_AUTHORS:
            lines.append(f"  • {_escape_md(name)}")
        lines.append("")
        lines.append(
            f"*Blogs RSS* \\({_escape_md(str(len(RSS_AUTHORS)))} autores\\)"
        )
        for name in RSS_AUTHORS:
            lines.append(f"  • {_escape_md(name)}")
        lines.append("")
        lines.append(f"⏱ Ventana: últimas {LOOKBACK_HOURS}h")
        send_telegram_message("\n".join(lines))

    elif cmd == "/help" or cmd == "/start":
        help_text = (
            "👋 *Vigía — Tu resumen de prensa*\n"
            "\n"
            "📋 *Comandos disponibles:*\n"
            "\n"
            "/update — Consultar artículos ahora\n"
            "/random — Artículo aleatorio de un autor\n"
            "/status — Ver autores configurados\n"
            "/add — Añadir un autor\n"
            "/remove — Eliminar un autor\n"
            "/help — Este mensaje\n"
            "\n"
            "📝 *Ejemplos:*\n"
            "`/random Jabois` — aleatorio de Jabois\n"
            "`/random` — autor y artículo al azar\n"
            "`/add elpais Elvira Lindo elvira\\-lindo`\n"
            "`/remove Elvira Lindo`"
        )
        send_telegram_message(help_text)


def serve() -> None:
    """Arranca el bot en modo polling (interactivo)."""
    log.info("Vigía arrancado en modo bot. Esperando comandos...")
    log.info("Envía /update desde Telegram para forzar un digest.")

    offset = 0

    # Descartar mensajes antiguos al arrancar
    old = _get_updates(offset)
    if old:
        offset = old[-1]["update_id"] + 1
        log.info(f"Descartados {len(old)} mensaje(s) antiguo(s).")

    while True:
        try:
            updates = _get_updates(offset)
            for update in updates:
                offset = update["update_id"] + 1
                msg = update.get("message", {})
                chat_id = msg.get("chat", {}).get("id")
                text = msg.get("text", "")

                # Solo responder al chat autorizado
                if str(chat_id) != str(TELEGRAM_CHAT_ID):
                    log.info(f"Mensaje ignorado de chat_id={chat_id}")
                    continue

                if text.startswith("/"):
                    log.info(f"Comando recibido: {text}")
                    _handle_command(text, chat_id)

        except KeyboardInterrupt:
            log.info("Vigía detenido. ¡Hasta luego!")
            break
        except Exception as e:
            log.error(f"Error en el bucle de polling: {e}")
            import time
            time.sleep(5)


# ---------------------------------------------------------------------------
# Punto de entrada
# ---------------------------------------------------------------------------


def main():
    load_authors()
    if "--serve" in sys.argv:
        serve()
    else:
        run_digest()


if __name__ == "__main__":
    main()
