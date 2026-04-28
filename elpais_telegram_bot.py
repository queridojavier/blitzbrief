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
import re
import sys
import json
import logging
import hashlib
import random
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

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
    "Diario AS": "https://feeds.as.com/mrss-s/pages/as/site/as.com/portada",
    "El Confidencial": "https://rss.elconfidencial.com/",
    "OpenAI Blog": "https://openai.com/blog/rss.xml",
    "9to5Mac": "https://9to5mac.com/feed/",
    "Anthropic News": "https://raw.githubusercontent.com/taobojlen/anthropic-rss-feed/main/anthropic_news_rss.xml",
}

# Ventana temporal: artículos publicados en las últimas N horas
LOOKBACK_HOURS = 26  # 26h para cubrir holgadamente un día completo

# User-Agent y cabeceras tipo navegador para las peticiones HTTP.
# El País ha empezado a devolver 403 en las páginas /autor/<slug>/ cuando
# solo se envía un User-Agent escueto, así que mandamos el conjunto
# completo de cabeceras que enviaría un Chrome real.
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
BROWSER_HEADERS: dict[str, str] = {
    "User-Agent": USER_AGENT,
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}

# Archivo local para evitar enviar duplicados entre ejecuciones
SEEN_FILE = Path(__file__).parent / ".elpais_seen_articles.json"
AUTHORS_FILE = Path(__file__).parent / "authors.json"

# ── Equipos a seguir ───────────────────────────────────────────────
# Fútbol: equipos a seguir (nombres tal como aparecen en ESPN)
FOLLOWED_FOOTBALL_TEAMS: list[str] = [
    "Real Madrid",
    "Málaga",
]

# Equipos de interés para filtrar noticias deportivas (en minúsculas)
FOLLOWED_TEAMS_KEYWORDS: list[str] = [
    "real madrid", "málaga", "malaga", "unicaja",
    "málaga cf", "malaga cf",
]

# ── Fuentes deportivas dedicadas ──────────────────────────────────
# Feeds RSS específicos de deporte para tener cobertura diaria
SPORTS_SOURCES: dict[str, str] = {
    "Marca Fútbol": "https://e00-marca.uecdn.es/rss/futbol.xml",
    "Marca Real Madrid": "https://e00-marca.uecdn.es/rss/futbol/real-madrid.xml",
    "Marca Málaga": "https://e00-marca.uecdn.es/rss/futbol/malaga.xml",
    "Marca Baloncesto": "https://e00-marca.uecdn.es/rss/baloncesto.xml",
    "AS Fútbol": "https://feeds.as.com/mrss-s/pages/as/site/as.com/futbol",
    "AS Baloncesto": "https://feeds.as.com/mrss-s/pages/as/site/as.com/baloncesto",
    "La Opinión Deportes": "https://www.laopiniondemalaga.es/rss/section/11017",
    "Málaga Hoy Deportes": "https://www.malagahoy.es/rss/section/deportes/",
}

# Ligas ESPN a consultar para fútbol
ESPN_FOOTBALL_LEAGUES: list[str] = [
    "esp.1",            # La Liga
    "esp.2",            # Segunda División
    "uefa.champions",   # Champions League
    "uefa.europa",      # Europa League
    "esp.copa_del_rey", # Copa del Rey
]

# Canal de TV típico por competición en España (derechos 2025-26)
COMPETITION_TV_SPAIN: dict[str, str] = {
    "La Liga": "DAZN / Movistar+ LaLiga",
    "Segunda División": "DAZN",
    "Copa del Rey": "DAZN / Movistar+",
    "Supercopa de España": "DAZN",
    "UEFA Champions League": "Movistar+ Champions",
    "UEFA Europa League": "DAZN",
    "UEFA Conference League": "DAZN",
    "Liga ACB": "Movistar+ Deportes",
    "EuroLeague": "DAZN",
    "EuroCup": "DAZN",
}

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


def load_seen_articles() -> list[str]:
    """Carga los hashes de artículos ya enviados (preservando orden)."""
    if SEEN_FILE.exists():
        try:
            data = json.loads(SEEN_FILE.read_text())
            return list(data.get("seen", []))
        except (json.JSONDecodeError, KeyError):
            return []
    return []


def save_seen_articles(seen: list[str]) -> None:
    """Guarda los hashes de artículos ya enviados (máx. 500, los más recientes)."""
    SEEN_FILE.write_text(json.dumps({"seen": seen[-500:]}))


def article_hash(url: str) -> str:
    return hashlib.md5(url.encode()).hexdigest()


def _fetch_page(url: str) -> tuple[Optional[str], Optional[str]]:
    """Descarga una URL y devuelve (html, error).

    Si todo va bien: (html, None).
    Si falla:        (None, descripción del error).
    """
    try:
        resp = requests.get(url, headers=BROWSER_HEADERS, timeout=15)
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

        # Fecha (obligatoria para podcasts — sin fecha se descarta)
        pub_el = item.find("pubDate")
        pub_date = None
        if pub_el is not None and pub_el.text:
            try:
                pub_date = parsedate_to_datetime(pub_el.text)
            except (ValueError, TypeError):
                pass

        if not pub_date or pub_date < cutoff:
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
    """Recoge titulares recientes de todas las fuentes de noticias.

    Incluye las fuentes deportivas especializadas (Marca, AS, etc.) con un
    límite menor para que Gemini elija el titular deportivo más relevante
    junto al resto de categorías, en lugar de listarlos en bruto.
    """
    headlines: list[dict] = []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)

    # Fuentes generales + deportivas; las deportivas con límite más bajo
    all_sources = {**NEWS_SOURCES, **SPORTS_SOURCES}

    for source_name, feed_url in all_sources.items():
        limit = 4 if source_name in SPORTS_SOURCES else max_per_source
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
            if count >= limit:
                break
            title_el = item.find("title")
            desc_el = item.find("description")
            if title_el is None or not title_el.text:
                continue
            # Filtrar por fecha: descartar artículos de más de 24h
            pubdate_el = item.find("pubDate")
            if pubdate_el is not None and pubdate_el.text:
                try:
                    pub_dt = parsedate_to_datetime(pubdate_el.text)
                    if pub_dt < cutoff:
                        continue
                except Exception:
                    pass
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
                if count >= limit:
                    break
                title_el = entry.find("atom:title", ns)
                summary_el = entry.find("atom:summary", ns)
                if title_el is None or not title_el.text:
                    continue
                # Filtrar por fecha en feeds Atom
                updated_el = entry.find("atom:updated", ns)
                if updated_el is not None and updated_el.text:
                    try:
                        pub_dt = datetime.fromisoformat(
                            updated_el.text.replace("Z", "+00:00")
                        )
                        if pub_dt < cutoff:
                            continue
                    except Exception:
                        pass
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


# ── Fixtures deportivos (ESPN API — gratuita, sin clave) ───────────


def fetch_upcoming_fixtures() -> list[str]:
    """
    Devuelve líneas con partidos de hoy para los equipos seguidos.
    Usa la API pública de ESPN (sin clave, sin registro).
    """
    tz_madrid = ZoneInfo("Europe/Madrid")
    now = datetime.now(tz_madrid)
    today_str = now.strftime("%Y%m%d")

    lines: list[str] = []
    seen_events: set[str] = set()  # evitar duplicados entre ligas

    # Nombres de equipos en minúsculas para comparar
    followed = {t.lower() for t in FOLLOWED_FOOTBALL_TEAMS}

    for league in ESPN_FOOTBALL_LEAGUES:
        try:
            url = (
                f"https://site.api.espn.com/apis/site/v2/sports/soccer/"
                f"{league}/scoreboard?dates={today_str}"
            )
            resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=10)
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as e:
            log.warning(f"[Fixtures] Error ESPN {league}: {e}")
            continue

        events = data.get("events", [])
        for event in events:
            try:
                event_id = event["id"]
                if event_id in seen_events:
                    continue

                competitors = event["competitions"][0]["competitors"]
                home = competitors[0]["team"]["displayName"]
                away = competitors[1]["team"]["displayName"]

                # Solo partidos de equipos seguidos
                if not any(
                    f in home.lower() or f in away.lower() for f in followed
                ):
                    continue

                seen_events.add(event_id)

                # Hora del partido (viene en UTC)
                match_utc = datetime.fromisoformat(
                    event["date"].replace("Z", "+00:00")
                )
                match_local = match_utc.astimezone(tz_madrid)
                time_str = match_local.strftime("%H:%M")

                league_name = event["competitions"][0]["type"].get(
                    "abbreviation", data.get("leagues", [{}])[0].get("name", league)
                )
                # Intentar usar el nombre de la liga del evento
                if "season" in event and "type" in event["season"]:
                    league_name = event.get("name", league_name).split(" - ")[0]
                league_name = data.get("leagues", [{}])[0].get("name", league)

                channel = COMPETITION_TV_SPAIN.get(league_name, "")
                channel_str = f" — {channel}" if channel else ""
                lines.append(
                    f"⚽ {home} vs {away} ({league_name}) — {time_str}{channel_str}"
                )
            except (KeyError, ValueError, IndexError):
                continue

    log.info(f"[Fixtures] Partidos de hoy: {len(lines)}")
    return lines


# ── Meteorología ────────────────────────────────────────────────────

# Códigos WMO → descripción corta y emoji
_WMO_DESCRIPTIONS: dict[int, tuple[str, str]] = {
    0: ("despejado", "☀️"),
    1: ("mayormente despejado", "🌤"),
    2: ("parcialmente nublado", "⛅"),
    3: ("nublado", "☁️"),
    45: ("niebla", "🌫"),
    48: ("niebla con escarcha", "🌫"),
    51: ("llovizna ligera", "🌦"),
    53: ("llovizna", "🌦"),
    55: ("llovizna intensa", "🌧"),
    61: ("lluvia ligera", "🌧"),
    63: ("lluvia", "🌧"),
    65: ("lluvia intensa", "🌧"),
    71: ("nieve ligera", "🌨"),
    73: ("nieve", "🌨"),
    75: ("nieve intensa", "🌨"),
    80: ("chubascos ligeros", "🌦"),
    81: ("chubascos", "🌧"),
    82: ("chubascos fuertes", "🌧"),
    95: ("tormenta", "⛈"),
    96: ("tormenta con granizo", "⛈"),
    99: ("tormenta con granizo fuerte", "⛈"),
}


def fetch_weather_block() -> str:
    """
    Devuelve una línea con el tiempo actual y previsión del día en Málaga.
    Usa Open-Meteo (gratis, sin API key).
    """
    try:
        resp = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": 36.7213,
                "longitude": -4.4214,
                "current": "temperature_2m,weather_code",
                "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max",
                "timezone": "Europe/Madrid",
                "forecast_days": 1,
            },
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as e:
        log.warning(f"[Meteo] Error al obtener el tiempo: {e}")
        return ""

    current = data.get("current", {})
    daily = data.get("daily", {})

    temp = current.get("temperature_2m")
    code = current.get("weather_code", 0)
    t_max_list = daily.get("temperature_2m_max", [])
    t_min_list = daily.get("temperature_2m_min", [])
    rain_list = daily.get("precipitation_probability_max", [])

    if temp is None:
        return ""

    desc, emoji = _WMO_DESCRIPTIONS.get(code, ("", "🌡"))
    parts = [f"{emoji} Málaga: {temp:.0f}°C, {desc}"]

    if t_min_list and t_max_list:
        parts.append(f"(mín {t_min_list[0]:.0f}° / máx {t_max_list[0]:.0f}°)")

    if rain_list and rain_list[0] > 20:
        parts.append(f"— 🌂 {rain_list[0]:.0f}% prob. lluvia")

    return " ".join(parts)


def fetch_tomorrow_weather_block() -> str:
    """
    Devuelve una línea con la previsión de mañana en Málaga.
    Solo destaca lo relevante: lluvia o calor extremo.
    Usa Open-Meteo (gratis, sin API key).
    """
    try:
        resp = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": 36.7213,
                "longitude": -4.4214,
                "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code",
                "timezone": "Europe/Madrid",
                "forecast_days": 2,
            },
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as e:
        log.warning(f"[Meteo] Error al obtener previsión de mañana: {e}")
        return ""

    daily = data.get("daily", {})
    t_max_list = daily.get("temperature_2m_max", [])
    t_min_list = daily.get("temperature_2m_min", [])
    rain_list = daily.get("precipitation_probability_max", [])
    code_list = daily.get("weather_code", [])

    # Necesitamos al menos 2 días (hoy + mañana)
    if len(t_max_list) < 2:
        return ""

    t_max = t_max_list[1]
    t_min = t_min_list[1]
    rain = rain_list[1] if len(rain_list) > 1 else 0
    code = code_list[1] if len(code_list) > 1 else 0

    desc, emoji = _WMO_DESCRIPTIONS.get(code, ("", "🌡"))
    parts = [f"{emoji} Mañana en Málaga: {t_min:.0f}°–{t_max:.0f}°C, {desc}"]

    if rain > 20:
        parts.append(f"— 🌂 {rain:.0f}% prob. lluvia")
    if t_max >= 35:
        parts.append(f"— 🥵 ¡{t_max:.0f}°C de máxima!")

    return " ".join(parts)


# ── Bitcoin ─────────────────────────────────────────────────────────


def fetch_bitcoin_block() -> str:
    """
    Devuelve un bloque con el precio de Bitcoin en EUR y variación 24h.
    Si la variación es importante (>=5%), busca una noticia que lo explique.
    Usa CoinGecko API (gratis, sin API key).
    """
    # ── Precio BTC/EUR ──────────────────────────────────────────────
    try:
        resp = requests.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={
                "ids": "bitcoin",
                "vs_currencies": "eur",
                "include_24hr_change": "true",
            },
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json().get("bitcoin", {})
    except requests.RequestException as e:
        log.warning(f"[Bitcoin] Error al obtener precio: {e}")
        return ""

    price = data.get("eur")
    change = data.get("eur_24h_change")
    if price is None:
        return ""

    price_str = f"{price:,.0f}".replace(",", ".")
    arrow = "📈" if (change or 0) >= 0 else "📉"
    change_str = f" ({change:+.1f}%)" if change is not None else ""
    line = f"{arrow} Bitcoin: {price_str} €{change_str}"

    # ── Si variación >= 5%, buscar noticia explicativa (CoinDesk) ──
    if change is not None and abs(change) >= 5:
        try:
            rss_resp = requests.get(
                "https://www.coindesk.com/arc/outboundfeeds/rss/",
                headers={"User-Agent": USER_AGENT},
                timeout=10,
            )
            if rss_resp.ok:
                root = ET.fromstring(rss_resp.text)
                items = root.findall(".//item")
                if items:
                    first_title = items[0].find("title")
                    if first_title is not None and first_title.text:
                        line += f"\n   └ {first_title.text.strip()}"
        except (requests.RequestException, ET.ParseError) as e:
            log.warning(f"[Bitcoin] Error al obtener noticias crypto: {e}")

    return line


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

    today = datetime.now(ZoneInfo("Europe/Madrid")).strftime("%d/%m/%Y")

    prompt = f"""Hoy es {today}. Eres el editor de un briefing matutino ultra-breve.
Tu única fuente son los titulares listados al final de este prompt: nada más.

ANTI-ALUCINACIÓN (lo más importante):
- USA EXCLUSIVAMENTE los titulares listados abajo. Si una noticia no aparece literalmente en esa lista, NO LA INCLUYAS.
- NO uses tu conocimiento previo del mundo, ni hechos que recuerdes, ni contexto histórico, ni sucesos que "podrían" estar pasando.
- NO inventes nombres, cifras, capturas, dimisiones, victorias, fichajes, lesiones ni detenciones.
- Si una sección no tiene un titular concreto y verificable en la lista, OMÍTELA por completo (no escribas la línea).
- Antes de redactar cada línea, comprueba mentalmente que cada dato proviene de un titular concreto de la lista.
- En caso de duda, OMITE la sección. Es mejor un briefing corto que uno con datos inventados.

SELECCIÓN:
- Para cada sección, elige el titular MÁS IMPORTANTE de los listados que encaje en esa categoría y resúmelo en una frase corta.
- Si dos titulares se contradicen, elige el más reciente o el de la fuente más fiable.

FORMATO (una línea por sección, omite la línea entera si no hay titular adecuado):
🌍 Internacional: [la noticia internacional más importante hoy]
🏛 España: [la noticia nacional más relevante hoy]
💰 Economía: [solo si hay algo económico realmente destacable]
📍 Málaga: [solo si hay algo local relevante de Málaga o Andalucía]
⚽ Deporte: [la noticia más importante hoy sobre Real Madrid (fútbol o baloncesto), Málaga CF o Unicaja: resultado, lesión, fichaje, rueda de prensa, etc.]
🤖 Tech: [solo si hay un lanzamiento, anuncio o novedad REAL de hoy]

ESTILO:
- UNA sola noticia por sección, en UNA frase de máximo 15 palabras.
- Para Tech: ignora noticias sobre productos ya lanzados hace días/semanas. Solo incluye si es algo nuevo de hoy.
- Para Deporte: incluye cualquier noticia sobre Real Madrid (fútbol O baloncesto), Málaga CF o Unicaja: resultados, fichajes, crónicas, lesiones, ruedas de prensa, etc. No te limites solo a partidos de hoy.
- Todo en español.
- NO uses asteriscos, negritas ni markdown.
- NO añadas introducción, cierre, fuentes ni relleno.

TITULARES (única fuente válida):
{headlines_text}"""

    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        "gemini-2.5-flash:generateContent"
        f"?key={GEMINI_API_KEY}"
    )
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        # temperature baja para reducir alucinaciones: queremos extracción
        # casi literal de los titulares, no creatividad.
        "generationConfig": {"maxOutputTokens": 8192, "temperature": 0.2},
    }

    # Intentar hasta 3 veces (con 10s de espera entre intentos)
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
            log.error(f"[Briefing] Respuesta inesperada de Gemini: {data}")
        except requests.RequestException as e:
            log.error(f"[Briefing] Error al llamar a Gemini API (intento {attempt + 1}): {e}")

        if attempt < 2:
            log.info(f"[Briefing] Reintentando en 10 segundos...")
            time.sleep(10)

    return None


def send_news_briefing() -> bool:
    """Genera y envía el briefing matutino: titulares del día + partidos."""
    log.info("[Briefing] Recopilando titulares...")
    headlines = fetch_news_headlines()

    if not headlines:
        log.info("[Briefing] No se obtuvieron titulares.")
        return False

    log.info(f"[Briefing] {len(headlines)} titulares recopilados. Generando resumen...")
    briefing = generate_news_briefing(headlines)

    now = datetime.now(ZoneInfo("Europe/Madrid"))
    date_str = now.strftime("%d/%m/%Y")

    if briefing:
        header = f"📰 BRIEFING DE NOTICIAS — {date_str}"
    else:
        # Fallback: Gemini no respondió — enviar titulares en bruto
        log.warning("[Briefing] Gemini no disponible, enviando titulares en bruto.")
        seen_titles: set[str] = set()
        lines = [f"📰 TITULARES — {date_str}", ""]
        for h in headlines:
            if h["title"] in seen_titles:
                continue
            seen_titles.add(h["title"])
            lines.append(f"• [{h['source']}] {h['title']}")
        briefing = "\n".join(lines[2:])   # el header ya va aparte
        header = lines[0]

    # Partidos de hoy
    fixtures = fetch_upcoming_fixtures()
    fixtures_section = ""
    if fixtures:
        fixtures_section = "\n\n📅 PARTIDOS HOY:\n" + "\n".join(fixtures)

    message = f"{header}\n\n{briefing}{fixtures_section}"
    success = _send_plain_message(message)
    if success:
        log.info("[Briefing] Enviado correctamente.")
    return success


def send_evening_briefing() -> bool:
    """Envía tiempo de mañana y Bitcoin como mensajes separados."""
    sent_any = False

    tomorrow_weather = fetch_tomorrow_weather_block()
    if tomorrow_weather:
        _send_plain_message(tomorrow_weather)
        sent_any = True

    bitcoin = fetch_bitcoin_block()
    if bitcoin:
        _send_plain_message(bitcoin)
        sent_any = True

    if not sent_any:
        log.info("[Evening] Sin datos para el briefing de tarde.")
    return sent_any


def _send_plain_message(text: str) -> bool:
    """Envía un mensaje de texto plano (sin Markdown) a Telegram."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log.error("Falta TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID.")
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    for chunk in _split_message(text, max_len=3000):
        try:
            resp = requests.post(
                url, json={"chat_id": TELEGRAM_CHAT_ID, "text": chunk}, timeout=15
            )
            resp.raise_for_status()
            data = resp.json()
            if not data.get("ok"):
                log.error(f"[Telegram] Error: {data}")
                return False
        except requests.RequestException as e:
            log.error(f"[Telegram] Error al enviar: {e}")
            return False
    return True


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


def _spanish_date(dt: datetime) -> str:
    """Formatea una fecha en español (sin depender del locale del sistema)."""
    days = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
    months = [
        "enero", "febrero", "marzo", "abril", "mayo", "junio",
        "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
    ]
    return f"{days[dt.weekday()].capitalize()} {dt.day} de {months[dt.month - 1]} de {dt.year}"


def format_telegram_message(all_articles: list[dict]) -> str:
    """Formatea el mensaje de Telegram con Markdown."""
    now = datetime.now(ZoneInfo("Europe/Madrid"))
    date_str = _spanish_date(now)

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
    """Envía un mensaje por Telegram (MarkdownV2, con splitting y fallback)."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log.error(
            "Falta TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID. "
            "Configura las variables de entorno."
        )
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

    # Partir en trozos para no superar el límite de 4096 chars de Telegram
    success = True
    for chunk in _split_message(text, max_len=4000):
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": chunk,
            "parse_mode": "MarkdownV2",
            "disable_web_page_preview": True,
        }

        try:
            resp = requests.post(url, json=payload, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            if data.get("ok"):
                log.info("Mensaje enviado correctamente a Telegram.")
            else:
                log.error(f"Error de Telegram: {data}")
                # Fallback: reenviar sin MarkdownV2 si falla el parseo
                success = _send_plain_fallback(url, chunk) and success
        except requests.RequestException as e:
            log.error(f"Error al enviar mensaje: {e}")
            success = False

    return success


def _send_plain_fallback(url: str, text: str) -> bool:
    """Reenvía un mensaje como texto plano si MarkdownV2 falló."""
    log.info("[Fallback] Reintentando sin MarkdownV2...")
    # Limpiar marcas de Markdown para texto plano
    plain = re.sub(r"\\(.)", r"\1", text)  # quitar backslash-escaping
    plain = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", plain)  # [text](url) → text
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": plain,
        "disable_web_page_preview": True,
    }
    try:
        resp = requests.post(url, json=payload, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        if data.get("ok"):
            log.info("[Fallback] Mensaje enviado como texto plano.")
            return True
        log.error(f"[Fallback] Error de Telegram: {data}")
        return False
    except requests.RequestException as e:
        log.error(f"[Fallback] Error al enviar: {e}")
        return False


# ---------------------------------------------------------------------------
# Lógica del digest (reutilizable)
# ---------------------------------------------------------------------------


def run_digest(notify_empty: bool = False, mode: str = "morning") -> None:
    """Ejecuta el ciclo de envío por Telegram.

    Estructura de mensajes:
      Mañana  → 1) Titulares (Gemini + partidos)  2) Tiempo actual  3) Audio Jabois
      Tarde   → 1) Artículos columnistas           2) Tiempo mañana  3) Bitcoin

    Args:
        notify_empty: si True, envía mensaje incluso cuando no hay artículos.
        mode: "morning", "evening" o "full".
    """
    log.info(f"Iniciando BlitzBrief — modo {mode}...")

    # ── Mañana: mensaje 1 — Titulares ────────────────────────────────
    if mode in ("morning", "full"):
        if GEMINI_API_KEY:
            send_news_briefing()
        else:
            log.info("Sin GEMINI_API_KEY — briefing omitido.")

        # ── Mañana: mensaje 2 — Tiempo actual ────────────────────────
        weather = fetch_weather_block()
        if weather:
            _send_plain_message(weather)

    cutoff = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)
    seen = load_seen_articles()
    seen_set = set(seen)          # set auxiliar para búsquedas O(1)
    all_new_articles: list[dict] = []
    podcast_segments: list[dict] = []
    fetch_errors: list[str] = []

    # ── Tarde: artículos de columnistas (mensaje 1 de tarde) ─────────
    if mode in ("evening", "full"):
        for author_name, slug in ELPAIS_AUTHORS.items():
            log.info(f"[El País] Consultando: {author_name} ({slug})")
            articles = fetch_elpais_articles(author_name, slug, cutoff, fetch_errors)
            for art in articles:
                if article_hash(art["url"]) not in seen_set:
                    all_new_articles.append(art)
            log.info(f"  → {len(articles)} artículo(s) reciente(s)")

        for author_name, slug in ELPLURAL_AUTHORS.items():
            log.info(f"[El Plural] Consultando: {author_name} ({slug})")
            articles = fetch_elplural_articles(author_name, slug, cutoff, fetch_errors)
            for art in articles:
                if article_hash(art["url"]) not in seen_set:
                    all_new_articles.append(art)
            log.info(f"  → {len(articles)} artículo(s) reciente(s)")

        for author_name, feed_url in RSS_AUTHORS.items():
            log.info(f"[RSS] Consultando: {author_name}")
            articles = fetch_rss_articles(author_name, feed_url, cutoff, fetch_errors)
            for art in articles:
                if article_hash(art["url"]) not in seen_set:
                    all_new_articles.append(art)
            log.info(f"  → {len(articles)} artículo(s) reciente(s)")

    # ── Mañana: podcast (mensaje 3 — audio publicado de madrugada) ───
    if mode in ("morning", "full"):
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
                if article_hash(seg["audio_url"]) not in seen_set:
                    podcast_segments.append(seg)
            log.info(f"  → {len(segments)} segmento(s) encontrado(s)")

    # ── Enviar artículos ─────────────────────────────────────────────
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
            for art in all_new_articles:
                h = article_hash(art["url"])
                seen.append(h)
                seen_set.add(h)
            save_seen_articles(seen)
    else:
        log.info("No hay artículos nuevos.")
        if notify_empty:
            message = format_telegram_message([])
            send_telegram_message(message)

    # ── Enviar audios de podcast ─────────────────────────────────────
    for seg in podcast_segments:
        if seg["audio_url"]:
            audio_sent = send_telegram_audio(
                seg["audio_url"], seg["title"], seg.get("duration", "")
            )
            if audio_sent:
                h = article_hash(seg["audio_url"])
                seen.append(h)
                seen_set.add(h)
                save_seen_articles(seen)

    # ── Alertas de errores ───────────────────────────────────────────
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

    # ── Tarde: mensaje 2 — Tiempo de mañana ──────────────────────────
    if mode in ("evening", "full"):
        tomorrow = fetch_tomorrow_weather_block()
        if tomorrow:
            _send_plain_message(tomorrow)

        # ── Tarde: mensaje 3 — Bitcoin ────────────────────────────────
        bitcoin = fetch_bitcoin_block()
        if bitcoin:
            _send_plain_message(bitcoin)

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
            time.sleep(5)


# ---------------------------------------------------------------------------
# Punto de entrada
# ---------------------------------------------------------------------------


def main():
    load_authors()
    if "--serve" in sys.argv:
        serve()
    elif "--evening" in sys.argv:
        run_digest(mode="evening")
    elif "--morning" in sys.argv:
        run_digest(mode="morning")
    else:
        run_digest(mode="full")


if __name__ == "__main__":
    main()
