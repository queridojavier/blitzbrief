# 📰 El País → Telegram: Resumen diario de columnistas

Bot que cada mañana te envía por Telegram los artículos nuevos de tus columnistas favoritos de El País.

## Cómo funciona

1. Consulta las páginas de autor de El País (no necesita API ni suscripción)
2. Detecta artículos publicados en las últimas ~24 horas
3. Te envía un mensaje formateado por Telegram agrupado por autor

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

Edita el diccionario `AUTHORS` en `elpais_telegram_bot.py`. El slug es la parte final de la URL de la página del autor en El País:

```
https://elpais.com/autor/manuel-jabois-sueiro/
                        ^^^^^^^^^^^^^^^^^^^^^^ → este es el slug
```

Algunos slugs de ejemplo:

| Autor | Slug |
|-------|------|
| Manuel Jabois | `manuel-jabois-sueiro` |
| Juan José Millás | `juan-jose-millas` |
| Luz Sánchez-Mellado | `luz-sanchez-mellado` |
| Rosa Montero | `rosa-montero` |
| Elvira Lindo | `elvira-lindo` |
| Sergio del Molino | `sergio-del-molino` |
| Benjamín Prado | `benjamin-prado` |
| Javier Cercas | `javier-cercas` |
| Leila Guerriero | `leila-guerriero` |

### 4. Opción A: GitHub Actions (recomendado, gratis)

1. Crea un repo en GitHub (puede ser privado)
2. Sube `elpais_telegram_bot.py` a la raíz del repo
3. Crea `.github/workflows/elpais_digest.yml` con el contenido del archivo incluido
4. Ve a **Settings → Secrets and variables → Actions** y añade:
   - `TELEGRAM_BOT_TOKEN` → tu token del bot
   - `TELEGRAM_CHAT_ID` → tu chat_id
5. Listo. Se ejecutará cada mañana a las ~7:00 hora España

Para probarlo manualmente: ve a **Actions → El País Daily Digest → Run workflow**.

### 4. Opción B: Ejecución local con cron (Mac/Linux)

```bash
# Instalar dependencias
pip install requests beautifulsoup4

# Variables de entorno (añadir a ~/.zshrc o ~/.bashrc)
export TELEGRAM_BOT_TOKEN="tu_token_aquí"
export TELEGRAM_CHAT_ID="tu_chat_id_aquí"

# Probar manualmente
python elpais_telegram_bot.py

# Programar con cron (cada día a las 7:00)
crontab -e
# Añadir esta línea:
0 7 * * * cd /ruta/al/proyecto && /usr/bin/python3 elpais_telegram_bot.py
```

## Ejemplo de mensaje

```
📰 Tu prensa del día
Jueves 12 de marzo de 2026

✍️ Manuel Jabois
  • Título del artículo — Columna
    Entradilla del artículo...

✍️ Juan José Millás
  • Otro artículo interesante — Opinión
    Breve descripción...
```

## Notas

- El script guarda un archivo `.elpais_seen_articles.json` para no enviar duplicados entre ejecuciones. En GitHub Actions este archivo no persiste (cada ejecución es limpia), pero el filtro temporal de 26 horas evita repeticiones igualmente.
- Si El País cambia la estructura de su web, el parser podría necesitar ajustes. Es HTML scraping, no una API oficial.
- El script es respetuoso: hace una petición por autor, sin concurrencia agresiva.
