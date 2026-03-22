# BlitzBrief — Tu briefing diario de prensa por Telegram

Bot de Telegram que cada mañana te envía un **briefing de noticias generado con IA** y los **artículos nuevos de tus columnistas favoritos**. Todo en un solo chat.

## Qué hace

- **Briefing de noticias con IA** — Recoge titulares de 14 fuentes (nacionales, internacionales, locales y deportivas), los procesa con Gemini 2.5 Flash y te envía un resumen categorizado
- **Digest de columnistas** — Consulta las páginas de autor de El País, El Plural y feeds RSS para detectar artículos nuevos
- **Podcasts** — Detecta segmentos de podcast por título y te envía el audio directamente en Telegram
- **Alertas de errores** — Te avisa si alguna fuente falla

## Fuentes del briefing

| Categoría | Medios |
|-----------|--------|
| Nacional | El País, eldiario.es, El Español, El Confidencial |
| Internacional | The Guardian, New York Times |
| Local (Málaga) | Diario Sur, Málaga Hoy, La Opinión de Málaga |
| Deportes | Marca, Diario AS |
| Tecnología | OpenAI Blog, 9to5Mac, Anthropic News |

## Comandos del bot

| Comando | Descripción |
|---------|-------------|
| `/update` | Forzar un digest de columnas ahora |
| `/briefing` | Briefing de noticias con IA on-demand |
| `/random` | Artículo aleatorio de un autor al azar |
| `/random Jabois` | Artículo aleatorio de un autor concreto |
| `/status` | Ver todos los autores configurados |
| `/add elpais Nombre slug` | Añadir un autor de El País |
| `/add elplural Nombre slug` | Añadir un autor de El Plural |
| `/add rss Nombre url-feed` | Añadir un blog con RSS |
| `/remove Nombre` | Eliminar un autor |
| `/help` | Ver todos los comandos |

## Setup

### 1. Crear el bot de Telegram

1. Abre Telegram y busca **@BotFather**
2. Envíale `/newbot` y sigue las instrucciones
3. Copia el **token** que te da

### 2. Obtener tu chat_id

1. Envía cualquier mensaje a tu bot
2. Abre: `https://api.telegram.org/bot<TU_TOKEN>/getUpdates`
3. Busca `"chat":{"id": 123456789}` — ese número es tu **chat_id**

### 3. Configurar autores

Los autores se gestionan en `authors.json` o directamente desde Telegram con `/add` y `/remove`:

```json
{
  "elpais": { "Manuel Jabois": "manuel-jabois-sueiro" },
  "elplural": { "Benjamín Prado": "benjamin-prado" },
  "rss": { "Antonio Ortiz": "https://www.error500.net/feed" }
}
```

### 4. Opción A: GitHub Actions (recomendado, gratis)

1. Haz fork de este repo
2. Ve a **Settings > Secrets and variables > Actions** y añade:
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
   - `GEMINI_API_KEY` (obtén una gratis en [aistudio.google.com](https://aistudio.google.com))
3. Listo. Se ejecutará cada mañana a las 9:00 hora de España

Para probarlo manualmente: **Actions > BlitzBrief Daily Digest > Run workflow**.

### 4. Opción B: Ejecución local

```bash
pip install requests beautifulsoup4

export TELEGRAM_BOT_TOKEN="tu_token"
export TELEGRAM_CHAT_ID="tu_chat_id"
export GEMINI_API_KEY="tu_api_key"

# Digest una vez (briefing + columnas)
python blitzbrief.py

# Modo bot interactivo (escucha comandos)
python blitzbrief.py --serve
```

## Arquitectura

```
blitzbrief.py            # Script principal (briefing IA + scraping + bot)
authors.json             # Autores configurados (editable desde Telegram)
.github/workflows/       # GitHub Actions para ejecución automática
```

## Notas

- El briefing usa **Gemini 2.5 Flash** (tier gratuito de Google AI Studio)
- `.elpais_seen_articles.json` evita enviar duplicados
- Si algún medio cambia su web, el parser puede necesitar ajustes (scraping HTML, no API oficial)
- El script es respetuoso con los servidores: una petición por autor, sin concurrencia agresiva
