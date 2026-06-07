import unittest
from datetime import datetime, timezone
from unittest.mock import patch

import elpais_telegram_bot as bot


class FakeDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        base = cls(2026, 6, 1, 22, 30, tzinfo=timezone.utc)
        return base.astimezone(tz) if tz is not None else base.replace(tzinfo=None)


class BlitzBriefTests(unittest.TestCase):
    def test_elpais_feed_matches_dc_creator(self):
        xml = """<?xml version="1.0"?>
        <rss xmlns:dc="http://purl.org/dc/elements/1.1/"><channel>
          <item>
            <title>Yo estaba allí</title>
            <link>https://elpais.com/opinion/2026-06-01/yo-estaba-alli.html</link>
            <dc:creator>Juan José Millás García</dc:creator>
            <pubDate>Mon, 01 Jun 2026 10:00:00 +0000</pubDate>
            <category>Opinión</category>
            <description><![CDATA[Entradilla]]></description>
          </item>
          <item>
            <title>Riki Blanco: el que pueda hacer</title>
            <link>https://elpais.com/opinion/2026-06-01/riki-blanco.html</link>
            <dc:creator>Riki Blanco</dc:creator>
            <pubDate>Mon, 01 Jun 2026 10:00:00 +0000</pubDate>
          </item>
        </channel></rss>
        """

        with patch.object(bot, "_fetch_page", return_value=(xml, None)):
            articles = bot.fetch_elpais_articles(
                "Juan José Millás",
                "juan-jose-millas",
                datetime(2026, 6, 1, tzinfo=timezone.utc),
            )

        self.assertEqual(len(articles), 1)
        self.assertEqual(articles[0]["title"], "Yo estaba allí")
        self.assertEqual(
            articles[0]["url"],
            "https://elpais.com/opinion/2026-06-01/yo-estaba-alli.html",
        )
        self.assertEqual(articles[0]["subtitle"], "Entradilla")
        self.assertEqual(articles[0]["tag"], "Opinión")

    def test_elpais_feed_supports_multiple_creators_and_latest_only(self):
        xml = """<?xml version="1.0"?>
        <rss xmlns:dc="http://purl.org/dc/elements/1.1/"><channel>
          <item>
            <title>Más reciente</title>
            <link>https://elpais.com/opinion/2026-06-01/reciente.html</link>
            <dc:creator>Otra Persona, Juan José Millás</dc:creator>
            <pubDate>Mon, 01 Jun 2026 11:00:00 +0000</pubDate>
          </item>
          <item>
            <title>Más antiguo</title>
            <link>https://elpais.com/opinion/2026-06-01/antiguo.html</link>
            <dc:creator>Juan José Millás</dc:creator>
            <pubDate>Mon, 01 Jun 2026 09:00:00 +0000</pubDate>
          </item>
        </channel></rss>
        """

        with patch.object(bot, "_fetch_page", return_value=(xml, None)):
            articles = bot.fetch_elpais_articles(
                "Juan José Millás",
                "juan-jose-millas",
                datetime(2026, 6, 1, tzinfo=timezone.utc),
            )

        self.assertEqual([article["title"] for article in articles], ["Más reciente"])

    def test_elplural_uses_google_news_for_benjamin_prado(self):
        xml = """<?xml version="1.0"?>
        <rss><channel><title>Google News</title>
          <item>
            <title>Una nueva columna - El Plural</title>
            <link>https://news.google.com/articles/ok</link>
            <description><![CDATA[<a href="https://www.elplural.com/opinion/benjamin-prado/columna.html">Ver</a>]]></description>
            <pubDate>Mon, 01 Jun 2026 10:00:00 +0000</pubDate>
          </item>
        </channel></rss>
        """

        with patch.object(bot, "_fetch_page", return_value=(xml, None)) as fetch_page:
            articles = bot.fetch_elplural_articles(
                "Benjamín Prado",
                "benjamin-prado",
                datetime(2026, 6, 1, tzinfo=timezone.utc),
            )

        self.assertEqual(len(articles), 1)
        self.assertEqual(
            articles[0]["title"],
            "Una nueva columna",
        )
        self.assertEqual(
            articles[0]["url"],
            "https://www.elplural.com/opinion/benjamin-prado/columna.html",
        )
        self.assertEqual(articles[0]["source"], "El Plural")
        self.assertIn("site%3Aelplural.com", fetch_page.call_args.args[0])

    def test_elplural_falls_back_to_tag_page_when_google_news_is_empty(self):
        empty_google_news_xml = """<?xml version="1.0"?>
        <rss><channel><title>Google News</title></channel></rss>
        """
        tag_html = """
        <html><body>
          <div class="item">
            <h3><a href="/opinion/benjamin-prado/directa.html">Columna directa</a></h3>
            <p class="excerpt">Entradilla</p>
          </div>
        </body></html>
        """
        article_html = """
        <html><head>
          <meta property="article:published_time" content="2026-06-01T10:00:00+00:00">
        </head></html>
        """

        with patch.object(
            bot,
            "_fetch_page",
            side_effect=[
                (empty_google_news_xml, None),
                (tag_html, None),
                (article_html, None),
            ],
        ):
            articles = bot.fetch_elplural_articles(
                "Benjamín Prado",
                "benjamin-prado",
                datetime(2026, 6, 1, tzinfo=timezone.utc),
            )

        self.assertEqual([article["title"] for article in articles], ["Columna directa"])
        self.assertEqual(
            articles[0]["url"],
            "https://www.elplural.com/opinion/benjamin-prado/directa.html",
        )

    def test_google_news_rss_filters_out_non_author_entries(self):
        xml = """<?xml version="1.0"?>
        <rss><channel><title>Google News</title>
          <item>
            <title>Manuel Jabois analiza la política española</title>
            <link>https://news.google.com/articles/ok</link>
            <pubDate>Mon, 01 Jun 2026 10:00:00 +0000</pubDate>
          </item>
          <item>
            <title>Última hora en España y economía</title>
            <link>https://news.google.com/articles/bad</link>
            <pubDate>Mon, 01 Jun 2026 11:00:00 +0000</pubDate>
          </item>
        </channel></rss>
        """

        with patch.object(bot, "_fetch_page", return_value=(xml, None)):
            articles = bot.fetch_rss_articles(
                "Manuel Jabois",
                "https://news.google.com/rss/search?q=Manuel+Jabois&hl=es&gl=ES&ceid=ES:es",
                datetime(2026, 6, 1, tzinfo=timezone.utc),
            )

        self.assertEqual(len(articles), 1)
        self.assertEqual(articles[0]["title"], "Manuel Jabois analiza la política española")

    def test_google_news_rss_applies_site_filter_when_present(self):
        xml = """<?xml version="1.0"?>
        <rss><channel><title>Google News</title>
          <item>
            <title>Manuel Jabois firma su nueva columna</title>
            <link>https://news.google.com/articles/ok</link>
            <description><![CDATA[<a href="https://elpais.com/opinion/2026-06-01/columna.html">Ver</a>]]></description>
            <pubDate>Mon, 01 Jun 2026 10:00:00 +0000</pubDate>
          </item>
          <item>
            <title>Manuel Jabois comenta la actualidad</title>
            <link>https://news.google.com/articles/bad</link>
            <description><![CDATA[<a href="https://example.com/opinion/2026-06-01/ajeno.html">Ver</a>]]></description>
            <pubDate>Mon, 01 Jun 2026 11:00:00 +0000</pubDate>
          </item>
        </channel></rss>
        """

        with patch.object(bot, "_fetch_page", return_value=(xml, None)):
            articles = bot.fetch_rss_articles(
                "Manuel Jabois",
                "https://news.google.com/rss/search?q=Manuel+Jabois+site:elpais.com&hl=es&gl=ES&ceid=ES:es",
                datetime(2026, 6, 1, tzinfo=timezone.utc),
            )

        self.assertEqual(len(articles), 1)
        self.assertEqual(articles[0]["title"], "Manuel Jabois firma su nueva columna")

    def test_articles_are_not_marked_seen_when_send_fails(self):
        article = {
            "title": "Titulo",
            "url": "https://example.com/a1",
            "author": "Autor",
            "source": "El Pais",
            "date": datetime(2026, 3, 27, tzinfo=timezone.utc),
            "subtitle": "",
            "tag": "",
        }
        saved_states = []

        with patch.dict(bot.ELPAIS_AUTHORS, {"Autor": "slug"}, clear=True), \
             patch.dict(bot.ELPLURAL_AUTHORS, {}, clear=True), \
             patch.dict(bot.RSS_AUTHORS, {}, clear=True), \
             patch.dict(bot.PODCAST_SOURCES, {}, clear=True), \
             patch.object(bot, "GEMINI_API_KEY", ""), \
             patch.object(bot, "load_sent_runs", return_value={}), \
             patch.object(bot, "save_sent_runs"), \
             patch.object(bot, "load_seen_articles", return_value=[]), \
             patch.object(bot, "fetch_elpais_articles", return_value=[article]), \
             patch.object(bot, "send_telegram_message", return_value=False), \
             patch.object(bot, "fetch_tomorrow_weather_block", return_value=""), \
             patch.object(bot, "fetch_bitcoin_block", return_value=""), \
             patch.object(bot, "save_seen_articles", side_effect=lambda seen: saved_states.append(set(seen))):
            bot.run_digest(mode="evening")

        self.assertEqual(saved_states, [])

    def test_duplicate_article_urls_are_sent_once_per_run(self):
        article = {
            "title": "Misma columna",
            "url": "https://example.com/a1",
            "author": "Autor",
            "source": "El Pais",
            "date": datetime(2026, 3, 27, tzinfo=timezone.utc),
            "subtitle": "",
            "tag": "",
        }
        sent_messages = []
        saved_states = []

        with patch.dict(bot.ELPAIS_AUTHORS, {"Autor": "slug", "Autor 2": "slug2"}, clear=True), \
             patch.dict(bot.ELPLURAL_AUTHORS, {}, clear=True), \
             patch.dict(bot.RSS_AUTHORS, {}, clear=True), \
             patch.dict(bot.PODCAST_SOURCES, {}, clear=True), \
             patch.object(bot, "GEMINI_API_KEY", ""), \
             patch.object(bot, "load_sent_runs", return_value={}), \
             patch.object(bot, "save_sent_runs"), \
             patch.object(bot, "load_seen_articles", return_value=[]), \
             patch.object(bot, "fetch_elpais_articles", return_value=[article]), \
             patch.object(bot, "send_telegram_message", side_effect=lambda msg: sent_messages.append(msg) or True), \
             patch.object(bot, "fetch_tomorrow_weather_block", return_value=""), \
             patch.object(bot, "fetch_bitcoin_block", return_value=""), \
             patch.object(bot, "save_seen_articles", side_effect=lambda seen: saved_states.append(list(seen))):
            bot.run_digest(mode="evening")

        self.assertEqual(len(sent_messages), 1)
        self.assertEqual(sent_messages[0].count("Misma columna"), 1)
        self.assertEqual(saved_states, [[bot.article_hash(article["url"])]])

    def test_elplural_naive_article_date_is_comparable(self):
        tag_html = """
        <html><body>
          <div class="item">
            <h3><a href="/opinion/benjamin-prado/directa.html">Columna directa</a></h3>
          </div>
        </body></html>
        """
        article_html = """
        <html><head>
          <meta property="article:published_time" content="2026-06-01T10:00:00">
        </head></html>
        """

        with patch.object(bot, "_fetch_page", side_effect=[(tag_html, None), (article_html, None)]):
            articles = bot._fetch_elplural_tag_articles(
                "Benjamín Prado",
                "benjamin-prado",
                datetime(2026, 6, 1, tzinfo=timezone.utc),
            )

        self.assertEqual([article["title"] for article in articles], ["Columna directa"])
        self.assertEqual(articles[0]["date"].tzinfo, timezone.utc)

    def test_only_successful_podcast_sends_are_marked_seen(self):
        segments = [
            {
                "title": "Segmento 1",
                "audio_url": "https://example.com/audio1.mp3",
                "label": "Podcast",
                "date": datetime(2026, 3, 27, tzinfo=timezone.utc),
                "duration": "12:34",
            },
            {
                "title": "Segmento 2",
                "audio_url": "https://example.com/audio2.mp3",
                "label": "Podcast",
                "date": datetime(2026, 3, 27, tzinfo=timezone.utc),
                "duration": "10:00",
            },
        ]
        saved_states = []
        first_hash = bot.article_hash(segments[0]["audio_url"])
        second_hash = bot.article_hash(segments[1]["audio_url"])

        with patch.dict(bot.ELPAIS_AUTHORS, {}, clear=True), \
             patch.dict(bot.ELPLURAL_AUTHORS, {}, clear=True), \
             patch.dict(bot.RSS_AUTHORS, {}, clear=True), \
             patch.dict(bot.PODCAST_SOURCES, {"Podcast": {"feed": "feed", "filter": "x"}}, clear=True), \
             patch.object(bot, "GEMINI_API_KEY", ""), \
             patch.object(bot, "load_sent_runs", return_value={}), \
             patch.object(bot, "save_sent_runs"), \
             patch.object(bot, "load_seen_articles", return_value=[]), \
             patch.object(bot, "fetch_podcast_segments", return_value=segments), \
             patch.object(bot, "fetch_weather_block", return_value=""), \
             patch.object(bot, "send_telegram_audio", side_effect=[True, False]), \
             patch.object(bot, "save_seen_articles", side_effect=lambda seen: saved_states.append(set(seen))):
            bot.run_digest(mode="morning")

        self.assertEqual(saved_states, [{first_hash}])
        self.assertNotIn(second_hash, saved_states[0])

    def test_scheduled_digest_skips_when_already_sent_today(self):
        key = bot.digest_run_key("morning")

        with patch.object(bot, "load_sent_runs", return_value={key: True}), \
             patch.object(bot, "send_news_briefing") as send_news:
            bot.run_digest(mode="morning")

        send_news.assert_not_called()

    def test_scheduled_digest_marks_run_after_successful_send(self):
        saved_runs = []

        with patch.object(bot, "GEMINI_API_KEY", "key"), \
             patch.object(bot, "load_sent_runs", return_value={}), \
             patch.object(bot, "save_sent_runs", side_effect=lambda runs: saved_runs.append(dict(runs))), \
             patch.object(bot, "send_news_briefing", return_value=True), \
             patch.object(bot, "fetch_weather_block", return_value=""), \
             patch.object(bot, "load_seen_articles", return_value=[]), \
             patch.object(bot, "fetch_podcast_segments", return_value=[]):
            bot.run_digest(mode="morning")

        self.assertEqual(saved_runs, [{bot.digest_run_key("morning"): True}])

    def test_empty_digest_header_uses_madrid_timezone(self):
        with patch.object(bot, "datetime", FakeDateTime):
            message = bot.format_telegram_message([])

        self.assertIn(" 2 de ", message)
        self.assertNotIn(" 1 de ", message)

    def test_bitcoin_block_includes_price_and_change(self):
        class FakeResponse:
            ok = True
            text = ""

            def raise_for_status(self):
                return None

            def json(self):
                return {"bitcoin": {"eur": 61234.0, "eur_24h_change": 4.2}}

        with patch.object(bot.requests, "get", return_value=FakeResponse()):
            block = bot.fetch_bitcoin_block()

        self.assertEqual(block, "📈 Bitcoin: 61.234 € (+4.2%)")

    def test_all_briefing_sources_have_profiles(self):
        all_sources = {**bot.NEWS_SOURCES, **bot.SPORTS_SOURCES}

        missing = [
            name for name in all_sources
            if bot._source_profile(name)["orientation"] == "no clasificada"
        ]

        self.assertEqual(missing, [])

    def test_news_headlines_include_source_profile(self):
        xml = """<?xml version="1.0"?>
        <rss><channel>
          <item>
            <title>Titular de prueba</title>
            <description>Descripción</description>
            <pubDate>Sun, 07 Jun 2026 00:30:00 +0000</pubDate>
          </item>
        </channel></rss>
        """

        with patch.dict(bot.NEWS_SOURCES, {"ABC": "feed"}, clear=True), \
             patch.dict(bot.SPORTS_SOURCES, {}, clear=True), \
             patch.object(bot, "_fetch_page", return_value=(xml, None)):
            headlines = bot.fetch_news_headlines()

        self.assertEqual(len(headlines), 1)
        self.assertEqual(headlines[0]["profile"]["orientation"], "conservador / centro-derecha")
        self.assertEqual(headlines[0]["profile"]["reliability"], "media-alta")

    def test_curate_news_headlines_groups_duplicate_titles(self):
        headlines = [
            {
                "source": "El País",
                "title": "El Gobierno aprueba una nueva ley de vivienda",
                "description": "",
                "profile": bot._source_profile("El País"),
            },
            {
                "source": "ABC",
                "title": "El Gobierno aprueba la nueva ley de vivienda",
                "description": "",
                "profile": bot._source_profile("ABC"),
            },
        ]

        curated = bot.curate_news_headlines(headlines)

        self.assertEqual(len(curated), 1)
        self.assertEqual(curated[0]["source_count"], 2)
        self.assertEqual(curated[0]["sources"], ["ABC", "El País"])

    def test_curate_news_headlines_prioritizes_interests(self):
        headlines = [
            {
                "source": "BBC Mundo",
                "title": "Una noticia internacional genérica",
                "description": "",
                "profile": bot._source_profile("BBC Mundo"),
            },
            {
                "source": "Google Gemini Blog",
                "title": "Google presenta novedades de Gemini para IA",
                "description": "",
                "profile": bot._source_profile("Google Gemini Blog"),
            },
        ]

        curated = bot.curate_news_headlines(headlines)

        self.assertEqual(curated[0]["source"], "Google Gemini Blog")
        self.assertIn("Gemini", curated[0]["why_it_matters"])

    def test_interest_matching_uses_word_boundaries(self):
        headline = {
            "title": "Los socios deciden hoy el futuro del club",
            "description": "Una crónica institucional sin tecnología.",
        }

        self.assertEqual(bot._matched_interests(headline), [])

    def test_generate_news_briefing_includes_importance_context(self):
        captured = {}

        class FakeResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {
                    "candidates": [
                        {"content": {"parts": [{"text": "🏛 España: Test"}]}}
                    ]
                }

        def fake_post(url, headers, json, timeout):
            captured["prompt"] = json["contents"][0]["parts"][0]["text"]
            return FakeResponse()

        headline = {
            "source": "ABC",
            "sources": ["ABC", "El País"],
            "orientations": ["conservador / centro-derecha", "centro-izquierda / progresista"],
            "title": "El Gobierno aprueba una nueva ley de vivienda",
            "description": "",
            "importance_score": 2.0,
            "why_it_matters": "Conecta con tus intereses: economía personal.",
        }

        with patch.object(bot, "GEMINI_API_KEY", "key"), \
             patch.object(bot.requests, "post", side_effect=fake_post):
            result = bot.generate_news_briefing([headline])

        self.assertEqual(result, "🏛 España: Test")
        self.assertIn("por qué importa", captured["prompt"])
        self.assertIn("prioridad: 2.0", captured["prompt"])
        self.assertIn("ABC, El País", captured["prompt"])


if __name__ == "__main__":
    unittest.main()
