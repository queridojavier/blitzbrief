"""
📰 BlitzBrief — Resumen diario de tus columnistas favoritos → Telegram
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
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

# ── Autores (cargados desde authors.json) ─────────────────────────
# El archivo authors.json contiene tres secciones:
#   "elpais":   { "Nombre": "slug" }
#   "elplural": { "Nombre": "slug" }
#   "rss":      { "Nombre": "url-del-feed" }
# Se pueden añadir/eliminar con los comandos /add y /remove del bot.

ELPAIS_AUTHORS: dict[str, str] = {}
ELPLURAL_AUTHORS: dict[str, str] = {}
RSS_AUTHORS: dict[str, str] = {}
PODCAST_SOURCES: dict[str, dict] = {}

# ── Fuentes de noticias para el briefing ──────────────────────────
NEWS_SOURCES: dict[str, str] = {
    "El País": "https://feeds.elpais.com/mrss-s/pages/ep/site/elpais.com/portada",
    "eldiario.es": "https://www.eldiario.es/rss/",
    "Diario Sur": "https://www.diariosur.es/rss/2.0/",
    "La Opinión de Málaga": "https://www.laopiniondemalaga.es/rss/",
    "El Español": "https://www.elespanol.com/rss/",
    "Málaga Hoy": "https://www.malagahoy.es/rss/",
    "The Guardian": "https://www.theguardian.com/world/rss",
    "New York Times": "https://rss.nytimes.com/services/xml/rss/nyt/World.xml",
    "Marca": "https://e00-marca.uecdn.es/rss/portada.xml",
    "Diario AS": "https://news.google.com/rss/search?q=site:as.com&hl=es&gl=ES&ceid=ES:es",
    "El Confidencial": "https://rss.elconfidencial.com/",
    "OpenAI Blog": "https://openai.com/blog/rss.xml",
    "9to5Mac": "https://9to5mac.com/feed/",
    "Anthropic News": "https://raw.githubusercontent.com/taobojlen/anthropic-rss-feed/main/anthropic_news_rss.xml",
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
    global ELPAIS_AUTHORS, ELPLURAL_AUTHORS, RSS_AUTHORS, PODCAST_SOURCES
    if AUTHORS_FILE.exists():
        try:
            data = json.loads(AUTHORS_FILE.read_text(encoding="utf-8"))
            ELPAIS_AUTHORS = data.get("elpais", {})
            ELPLURAL_AUTHORS = data.get("elplural", {})
            RSS_AUTHORS = data.get("rss", {})
            PODCAST_SOURCES = data.get("podcast", {})
            log.info(
                f"Autores cargados: {len(ELPAIS_AUTHORS)} El País, "
                f"{len(ELPLURAL_AUTHORS)} El Plural, {len(RSS_AUTHORS)} RSS, "
                f"{len(PODCAST_SOURCES)} Podcast"
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
        "podcast": PODCAST_SOURCES,
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

        if title_el is None or link_el is None:
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


# ── Scraper: Podcast (RSS + filtro por título) ────────────────────


def fetch_podcast_segments(
    label: str, feed_url: str, title_filter: str,
    cutoff: datetime, errors: Optional[list] = None,
) -> list[dict]:
    """Extrae segmentos de podcast que coincidan con un filtro en el título."""
    xml_text, err = _fetch_page(feed_url)
    if not xml_text:
        if errors is not None and err:
            errors.append(f"Podcast — {label}: {err}")
        return []

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        log.warning(f"Error al parsear podcast feed {feed_url}: {e}")
        if errors is not None:
            errors.append(f"Podcast — {label}: XML inválido")
        return []

    segments = []
    filter_lower = title_filter.lower()

    for item in root.findall(".//item"):
        title_el = item.find("title")
        if title_el is None or not title_el.text:
            continue
        title = title_el.text.strip()

        # Comprobar que el título empieza con el filtro (ej. "La Contra |")
        # para evitar falsos positivos ("guerra contra Irán", etc.)
        if not title.lower().startswith(filter_lower):
            continue

        # Fecha
        pub_el = item.find("pubDate")
        pub_date = None
        if pub_el is not None and pub_el.text:
            try:
                pub_date = parsedate_to_datetime(pub_el.text)
            except (ValueError, TypeError):
                pass

        if pub_date and pub_date < cutoff:
            continue

        # URL del audio (enclosure)
        enclosure = item.find("enclosure")
        audio_url = ""
        if enclosure is not None:
            audio_url = enclosure.get("url", "")

        # Duración
        duration = ""
        for tag_name in ["itunes:duration", "duration"]:
            dur_el = item.find(tag_name)
            if dur_el is not None and dur_el.text:
                duration = dur_el.text.strip()
                break
        # Buscar con namespace itunes si no se encontró
        if not duration:
            ns = {"itunes": "http://www.itunes.com/dtds/podcast-1.0.dtd"}
            dur_el = item.find("itunes:duration", ns)
            if dur_el is not None and dur_el.text:
                duration = dur_el.text.strip()

        segments.append({
            "title": title,
            "audio_url": audio_url,
            "label": label,
            "date": pub_date,
            "duration": duration,
        })

    return segments


def send_telegram_audio(audio_url: str, title: str, duration_str: str) -> bool:
    """Envía un audio por Telegram usando sendAudio (reproductor inline)."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log.error("Falta TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID.")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendAudio"
    payload: dict = {
        "chat_id": TELEGRAM_CHAT_ID,
        "audio": audio_url,
        "title": title,
        "caption": f"🎙 {title}",
    }

    # Convertir duración "HH:MM:SS" o "MM:SS" a segundos
    if duration_str:
        parts = duration_str.split(":")
        try:
            if len(parts) == 3:
                secs = int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
            elif len(parts) == 2:
                secs = int(parts[0]) * 60 + int(parts[1])
            else:
                secs = int(parts[0])
            payload["duration"] = secs
        except ValueError:
            pass

    try:
        resp = requests.post(url, json=payload, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if data.get("ok"):
            log.info(f"Audio enviado: {title}")
            return True
        else:
            log.error(f"Error de Telegram sendAudio: {data}")
            return False
    except requests.RequestException as e:
        log.error(f"Error al enviar audio: {e}")
        return False


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


# ── Briefing de noticias con IA ────────────────────────────────────


def fetch_news_headlines(max_per_source: int = 7) -> list[dict]:
    """Recoge titulares recientes de todas las fuentes de noticias."""
    headlines: list[dict] = []

    for source_name, feed_url in NEWS_SOURCES.items():
        xml_text, err = _fetch_page(feed_url)
        if not xml_text:
            log.warning(f"[Briefing] No se pudo descargar {source_name}: {err}")
            continue

        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError:
            log.warning(f"[Briefing] XML inválido de {source_name}")
            continue

        count = 0
        # RSS estándar
        for item in root.findall(".//item"):
            if count >= max_per_source:
                break
            title_el = item.find("title")
            desc_el = item.find("description")
            if title_el is None or not title_el.text:
                continue
            title = title_el.text.strip()
            desc = ""
            if desc_el is not None and desc_el.text:
                desc = BeautifulSoup(desc_el.text, "html.parser").get_text(strip=True)
                desc = desc[:200]
            headlines.append({
                "source": source_name,
                "title": title,
                "description": desc,
            })
            count += 1

        # Atom (por si algún feed usa <entry> en vez de <item>)
        if count == 0:
            ns = {"atom": "http://www.w3.org/2005/Atom"}
            for entry in root.findall(".//atom:entry", ns):
                if count >= max_per_source:
                    break
                title_el = entry.find("atom:title", ns)
                summary_el = entry.find("atom:summary", ns)
                if title_el is None or not title_el.text:
                    continue
                title = title_el.text.strip()
                desc = ""
                if summary_el is not None and summary_el.text:
                    desc = BeautifulSoup(
                        summary_el.text, "html.parser"
                    ).get_text(strip=True)[:200]
                headlines.append({
                    "source": source_name,
                    "title": title,
                    "description": desc,
                })
                count += 1

        log.info(f"[Briefing] {source_name}: {count} titulares")

    return headlines


def generate_news_briefing(headlines: list[dict]) -> Optional[str]:
    """Envía los titulares a Gemini 2.5 Flash para generar un briefing categorizado."""
    if not GEMINI_API_KEY:
        log.error("Falta GEMINI_API_KEY para generar el briefing.")
        return None

    if not headlines:
        log.info("[Briefing] Sin titulares, nada que resumir.")
        return None

    # Preparar los titulares como texto
    headlines_text = ""
    for h in headlines:
        headlines_text += f"[{h['source']}] {h['title']}"
        if h["description"]:
            headlines_text += f" — {h['description']}"
        headlines_text += "\n"

    today = datetime.now(timezone(timedelta(hours=2))).strftime("%d/%m/%Y")

    prompt = f"""Hoy es {today}. Eres el editor de un briefing matutino ultra-breve.
Tu trabajo es SELECCIONAR la noticia más importante de cada categoría, no comprimir todas.

De todos los titulares, elige LA ÚNICA NOTICIA MÁS RELEVANTE de cada sección y resúmela en una frase corta.

FORMATO (una línea por sección):
🌍 Internacional: [la noticia internacional más importante hoy]
🏛 España: [la noticia nacional más relevante hoy]
💰 Economía: [solo si hay algo económico realmente destacable]
📍 Málaga: [solo si hay algo local relevante de Málaga o Andalucía]
⚽ Deporte: [solo si hay noticias de Real Madrid, Málaga CF o Unicaja]
🤖 Tech: [solo si hay un lanzamiento, anuncio o novedad REAL de hoy]

REGLAS ESTRICTAS:
- UNA sola noticia por sección, la más importante, en UNA frase de máximo 15 palabras
- OMITE secciones enteras si no hay nada genuinamente novedoso o relevante HOY
- Para Tech: ignora noticias sobre productos ya lanzados hace días/semanas. Solo incluye si es algo nuevo de hoy
- Para Deporte: solo incluye si hay partido hoy/mañana o fichaje/resultado de Real Madrid, Málaga CF o Unicaja
- Todo en español
- NO uses asteriscos, negritas ni markdown
- NO añadas introducción, cierre, fuentes ni relleno

TITULARES:
{headlines_text}"""

    try:
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            "gemini-2.5-flash:generateContent"
            f"?key={GEMINI_API_KEY}"
        )
        resp = requests.post(
            url,
            headers={"content-type": "application/json"},
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"maxOutputTokens": 8192},
            },
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()
        candidates = data.get("candidates", [])
        if candidates:
            parts = candidates[0].get("content", {}).get("parts", [])
            if parts and parts[0].get("text"):
                return parts[0]["text"]
        log.error(f"[Briefing] Respuesta inesperada de Gemini: {data}")
        return None
    except requests.RequestException as e:
        log.error(f"[Briefing] Error al llamar a Gemini API: {e}")
        return None


def send_news_briefing() -> bool:
    """Genera y envía el briefing de noticias por Telegram."""
    log.info("[Briefing] Recopilando titulares...")
    headlines = fetch_news_headlines()

    if not headlines:
        log.info("[Briefing] No se obtuvieron titulares.")
        return False

    log.info(f"[Briefing] {len(headlines)} titulares recopilados. Generando resumen...")
    briefing = generate_news_briefing(headlines)

    if not briefing:
        return False

    # Enviar como texto plano (sin MarkdownV2 para evitar problemas de escape)
    now = datetime.now(timezone.utc).astimezone(
        timezone(timedelta(hours=2))  # CEST
    )
    date_str = now.strftime("%d/%m/%Y")

    message = f"📰 BRIEFING DE NOTICIAS — {date_str}\n\n{briefing}"

    # Enviar (partiendo en trozos si supera el límite de Telegram)
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log.error("Falta TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID.")
        return False

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
                log.error(f"[Briefing] Error de Telegram: {data}")
                success = False
        except requests.RequestException as e:
            log.error(f"[Briefing] Error al enviar: {e}")
            success = False

    if success:
        log.info("[Briefing] Enviado correctamente.")
    return success


def _split_message(text: str, max_len: int = 4096) -> list[str]:
    """Parte un mensaje largo en trozos respetando saltos de línea."""
    if len(text) <= max_len:
        return [text]

    chunks: list[str] = []
    while text:
        if len(text) <= max_len:
            chunks.append(text)
            break
        # Buscar el último salto de línea doble dentro del límite
        cut = text.rfind("\n\n", 0, max_len)
        if cut == -1:
            # Si no hay, buscar salto simple
            cut = text.rfind("\n", 0, max_len)
        if cut == -1:
            # Último recurso: cortar en el límite
            cut = max_len
        chunks.append(text[:cut])
        text = text[cut:].lstrip("\n")
    return chunks


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
    log.info("Iniciando BlitzBrief — revisión de artículos...")

    # ── Briefing de noticias (antes de las columnas) ─────────────────
    if GEMINI_API_KEY:
        send_news_briefing()
    else:
        log.info("Sin GEMINI_API_KEY — briefing omitido.")

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

    # ── Podcasts (audio directo) ────────────────────────────────────
    podcast_segments: list[dict] = []
    for label, config in PODCAST_SOURCES.items():
        feed_url = config.get("feed", "")
        title_filter = config.get("filter", "")
        if not feed_url:
            continue
        log.info(f"[Podcast] Consultando: {label} (filtro: '{title_filter}')")
        segments = fetch_podcast_segments(
            label, feed_url, title_filter, cutoff, fetch_errors
        )
        for seg in segments:
            h = article_hash(seg["audio_url"])
            if h not in seen:
                podcast_segments.append(seg)
                seen.add(h)
        log.info(f"  → {len(segments)} segmento(s) encontrado(s)")

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

    # ── Enviar audios de podcast ────────────────────────────────────
    for seg in podcast_segments:
        if seg["audio_url"]:
            send_telegram_audio(
                seg["audio_url"], seg["title"], seg.get("duration", "")
            )

    save_seen_articles(seen)

    # ── Alertas de errores ────────────────────────────────────────
    if fetch_errors:
        error_lines = [
            "⚠️ *BlitzBrief — Errores al consultar fuentes*",
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

    elif cmd == "/briefing":
        if not GEMINI_API_KEY:
            send_telegram_message(
                _escape_md("❌ No hay API key de Anthropic configurada.")
            )
            return
        send_telegram_message("📰 _Generando briefing de noticias\\.\\.\\._")
        success = send_news_briefing()
        if not success:
            send_telegram_message(
                _escape_md("❌ No se pudo generar el briefing.")
            )

    elif cmd == "/status":
        lines = ["🔎 *BlitzBrief — Estado*", ""]
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
        lines.append(
            f"*Podcasts* \\({_escape_md(str(len(PODCAST_SOURCES)))} fuentes\\)"
        )
        for name in PODCAST_SOURCES:
            lines.append(f"  • {_escape_md(name)}")
        lines.append("")
        lines.append(f"⏱ Ventana: últimas {LOOKBACK_HOURS}h")
        send_telegram_message("\n".join(lines))

    elif cmd == "/help" or cmd == "/start":
        help_text = (
            "👋 *BlitzBrief — Tu resumen de prensa*\n"
            "\n"
            "📋 *Comandos disponibles:*\n"
            "\n"
            "/update — Consultar artículos ahora\n"
            "/briefing — Briefing de noticias con IA\n"
            "/random — Artículo aleatorio de un autor\n"
            "/status — Ver autores configurados\n"
            "/add — Añadir un autor\n"
            "/remove — Eliminar un autor\n"
            "/help — Este mensaje\n"
            "\n"
            "📝 *Ejemplos:*\n"
            "`/briefing` — resumen de noticias del día\n"
            "`/random Jabois` — aleatorio de Jabois\n"
            "`/random` — autor y artículo al azar\n"
            "`/add elpais Elvira Lindo elvira\\-lindo`\n"
            "`/remove Elvira Lindo`"
        )
        send_telegram_message(help_text)


def serve() -> None:
    """Arranca el bot en modo polling (interactivo)."""
    log.info("BlitzBrief arrancado en modo bot. Esperando comandos...")
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
            log.info("BlitzBrief detenido. ¡Hasta luego!")
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
