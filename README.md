# 📰 Vigía — Tu resumen diario de prensa por Telegram

Bot que cada mañana te envía por Telegram los artículos nuevos de tus columnistas favoritos. Soporta múltiples fuentes: **El País**, **El Plural** y **cualquier blog con RSS** (Substack, WordPress, etc.).

## Qué hace

- Consulta las páginas de autor de El País, El Plural y feeds RSS (no necesita API ni suscripción)
- Detecta artículos publicados en las últimas ~26 horas
- Te envía un mensaje formateado por Telegram agrupado por autor
- Te avisa si alguna fuente falla (URLs rotas, cambios en la web, etc.)

## Comandos del bot

Vigía funciona como un bot interactivo de Telegram con estos comandos:

| Comando | Descripción |
|---------|-------------|
| `/update` | Forzar un digest de artículos ahora |
| `/random` | Artículo aleatorio de un autor al azar |
| `/random Jabois` | Artículo aleatorio de un autor concreto |
| `/status` | Ver todos los autores configurados |
| `/add elpais Nombre slug` | Añadir un autor de El País |
| `/add elplural Nombre slug` | Añadir un autor de El Plural |
| `/add rss Nombre url-feed` | Añadir un blog con RSS |
| `/remove Nombre` | Eliminar un autor |
| `/help` | Ver todos los comandos |

## Setup paso a paso

### 1. Crear el bot de Telegram

1. Abre Telegram y busca **@BotFather**
2. Envíale `/newbot`
3. Sigue las instrucciones (nombre + username)
4. Copia el **token** que te da (algo como `7123456789:AAH...`)

### 2. Obtener tu chat_id

1. Busca tu bot en Telegram y envíale cualquier mensaje (por ejemplo "hola")
2. Abre en el navegador: `https://api.telegram.org/bot<TU_TOKEN>/getUpdates`
3. Busca el campo `"chat":{"id": 123456789}` — ese número es tu **chat_id**

### 3. Configurar los autores

Los autores se gestionan en `authors.json`. Tiene tres secciones:

```json
{
  "elpais": {
    "Manuel Jabois": "manuel-jabois-sueiro",
    "Rosa Montero": "rosa-montero"
  },
  "elplural": {
    "Benjamín Prado": "benjamin-prado"
  },
  "rss": {
    "Antonio Ortiz": "https://www.error500.net/feed"
  }
}
```

**¿Cómo encontrar el slug de El País?** Es la parte final de la URL de la página del autor:

```
https://elpais.com/autor/manuel-jabois-sueiro/
                        ^^^^^^^^^^^^^^^^^^^^^ → slug
```

**¿Cómo encontrar el slug de El Plural?** Igual, pero del tag:

```
https://www.elplural.com/tag/benjamin-prado
                             ^^^^^^^^^^^^^^ → slug
```

**¿Blog con RSS?** Usa la URL del feed directamente (normalmente `/feed` o `/rss`).

También puedes añadir y eliminar autores directamente desde Telegram con `/add` y `/remove`.

### 4. Opción A: GitHub Actions (recomendado, gratis)

1. Haz fork de este repo (o crea uno nuevo y sube los archivos)
2. Ve a **Settings → Secrets and variables → Actions** y añade:
   - `TELEGRAM_BOT_TOKEN` → tu token del bot
   - `TELEGRAM_CHAT_ID` → tu chat_id
3. Listo. Se ejecutará cada mañana a las 9:00 hora de España

Para probarlo manualmente: **Actions → Vigía Daily Digest → Run workflow**.

### 4. Opción B: Ejecución local

```bash
# Instalar dependencias
pip install requests beautifulsoup4

# Variables de entorno
export TELEGRAM_BOT_TOKEN="tu_token_aquí"
export TELEGRAM_CHAT_ID="tu_chat_id_aquí"

# Ejecutar digest una vez
python elpais_telegram_bot.py

# Modo bot interactivo (escucha comandos de Telegram)
python elpais_telegram_bot.py --serve
```

## Ejemplo de mensaje

```
📰 Tu prensa del día
Viernes 14 de marzo de 2026

✍️ Manuel Jabois (El País)
  • Título del artículo — Columna
    Entradilla del artículo...

✍️ Benjamín Prado (El Plural)
  • Otro artículo interesante
    Breve descripción...

✍️ Antonio Ortiz (Error 500)
  • Post del blog
    Resumen del post...
```

## Alertas de errores

Si alguna fuente falla (URL rota, página caída, etc.), Vigía te envía un aviso por Telegram:

```
⚠️ Vigía — Errores al consultar fuentes

1 fuente(s) fallaron:

  🔴 El País — Autor: 404 Client Error

Revisa que las URLs de autor sigan siendo válidas.
```

## Arquitectura

```
elpais_telegram_bot.py   # Script principal (scraping + bot + digest)
authors.json             # Autores configurados (editable desde Telegram)
.github/workflows/       # GitHub Actions para ejecución automática
```

## Notas

- El archivo `.elpais_seen_articles.json` (caché local) evita enviar duplicados. En GitHub Actions no persiste entre ejecuciones, pero el filtro temporal de 26h lo compensa.
- Si El País o El Plural cambian la estructura de su web, el parser podría necesitar ajustes. Es scraping HTML, no una API oficial.
- El script es respetuoso con los servidores: hace una petición por autor, sin concurrencia agresiva.
- El cron de GitHub Actions tiene dos entradas (7:00 y 8:00 UTC) para cubrir el cambio horario de España (CET/CEST → siempre 9:00 local).
