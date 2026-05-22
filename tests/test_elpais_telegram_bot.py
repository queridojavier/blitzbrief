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
    def test_elpais_uses_google_news_and_verifies_byline(self):
        xml = """<?xml version="1.0"?>
        <rss><channel><title>Google News</title>
          <item>
            <title>Manuel Jabois firma una columna sobre política - El País</title>
            <link>https://news.google.com/articles/ok</link>
            <description><![CDATA[<a href="https://elpais.com/opinion/2026-06-01/columna-jabois.html">Ver</a>]]></description>
            <pubDate>Mon, 01 Jun 2026 10:00:00 +0000</pubDate>
          </item>
          <item>
            <title>Entrevista a Manuel Jabois en la sección cultura</title>
            <link>https://news.google.com/articles/mention</link>
            <description><![CDATA[<a href="https://elpais.com/cultura/2026-06-01/entrevista.html">Ver</a>]]></description>
            <pubDate>Mon, 01 Jun 2026 11:00:00 +0000</pubDate>
          </item>
        </channel></rss>
        """
        article_pages = {
            "https://elpais.com/opinion/2026-06-01/columna-jabois.html": (
                '<html><body><address><a href="/autor/manuel-jabois-sueiro/">'
                "Manuel Jabois</a></address></body></html>"
            ),
            "https://elpais.com/cultura/2026-06-01/entrevista.html": (
                '<html><body><address><a href="/autor/otra-persona/">'
                "Otra Persona</a></address></body></html>"
            ),
        }

        def fake_fetch(url):
            if url in article_pages:
                return article_pages[url], None
            return xml, None

        with patch.object(bot, "_fetch_page", side_effect=fake_fetch):
            articles = bot.fetch_elpais_articles(
                "Manuel Jabois",
                "manuel-jabois-sueiro",
                datetime(2026, 6, 1, tzinfo=timezone.utc),
            )

        self.assertEqual(len(articles), 1)
        self.assertEqual(articles[0]["title"], "Manuel Jabois firma una columna sobre política")
        self.assertEqual(articles[0]["url"], "https://elpais.com/opinion/2026-06-01/columna-jabois.html")
        self.assertEqual(articles[0]["source"], "El País")

    def test_elpais_skips_items_without_elpais_url_in_description(self):
        xml = """<?xml version="1.0"?>
        <rss><channel><title>Google News</title>
          <item>
            <title>Manuel Jabois analiza la actualidad política - El País</title>
            <link>https://news.google.com/articles/redirect-jabois</link>
            <pubDate>Mon, 01 Jun 2026 10:00:00 +0000</pubDate>
          </item>
        </channel></rss>
        """

        with patch.object(bot, "_fetch_page", return_value=(xml, None)):
            articles = bot.fetch_elpais_articles(
                "Manuel Jabois",
                "manuel-jabois-sueiro",
                datetime(2026, 6, 1, tzinfo=timezone.utc),
            )

        self.assertEqual(articles, [])

    def test_elpais_filters_articles_older_than_cutoff(self):
        xml = """<?xml version="1.0"?>
        <rss><channel><title>Google News</title>
          <item>
            <title>Manuel Jabois firma columna reciente - El País</title>
            <link>https://news.google.com/articles/new</link>
            <description><![CDATA[<a href="https://elpais.com/opinion/2026-06-01/nueva.html">Ver</a>]]></description>
            <pubDate>Mon, 01 Jun 2026 11:00:00 +0000</pubDate>
          </item>
          <item>
            <title>Manuel Jabois firma columna antigua - El País</title>
            <link>https://news.google.com/articles/old</link>
            <description><![CDATA[<a href="https://elpais.com/opinion/2026-05-31/vieja.html">Ver</a>]]></description>
            <pubDate>Sun, 31 May 2026 09:00:00 +0000</pubDate>
          </item>
        </channel></rss>
        """
        article_html = (
            '<html><body><address><a href="/autor/manuel-jabois-sueiro/">'
            "Manuel Jabois</a></address></body></html>"
        )

        def fake_fetch(url):
            if "news.google.com" in url:
                return xml, None
            return article_html, None

        with patch.object(bot, "_fetch_page", side_effect=fake_fetch):
            articles = bot.fetch_elpais_articles(
                "Manuel Jabois",
                "manuel-jabois-sueiro",
                datetime(2026, 6, 1, tzinfo=timezone.utc),
            )

        self.assertEqual(
            [article["title"] for article in articles],
            ["Manuel Jabois firma columna reciente"],
        )

    def test_elpais_uses_google_news_before_direct_scraping(self):
        google_news_xml = """<?xml version="1.0"?>
        <rss><channel><title>Google News</title>
          <item>
            <title>Manuel Jabois firma su nueva columna - EL PAÍS</title>
            <link>https://news.google.com/articles/ok</link>
            <description><![CDATA[<a href="https://elpais.com/opinion/2026-06-01/columna.html">Ver</a>]]></description>
            <pubDate>Mon, 01 Jun 2026 10:00:00 +0000</pubDate>
          </item>
        </channel></rss>
        """

        article_html = (
            '<html><body><address><a href="/autor/manuel-jabois-sueiro/">'
            "Manuel Jabois</a></address></body></html>"
        )

        def fake_fetch(url):
            if "news.google.com" in url:
                return google_news_xml, None
            return article_html, None

        with patch.object(bot, "_fetch_page", side_effect=fake_fetch) as fetch_page:
            articles = bot.fetch_elpais_articles(
                "Manuel Jabois",
                "manuel-jabois-sueiro",
                datetime(2026, 6, 1, tzinfo=timezone.utc),
            )

        self.assertEqual(len(articles), 1)
        self.assertEqual(articles[0]["title"], "Manuel Jabois firma su nueva columna")
        self.assertEqual(
            articles[0]["url"],
            "https://elpais.com/opinion/2026-06-01/columna.html",
        )
        self.assertIn("news.google.com/rss/search", fetch_page.call_args_list[0].args[0])

    def test_elpais_does_not_hit_author_page_when_google_news_is_empty(self):
        empty_google_news_xml = """<?xml version="1.0"?>
        <rss><channel><title>Google News</title></channel></rss>
        """
        errors = []

        with patch.object(
            bot,
            "_fetch_page",
            return_value=(empty_google_news_xml, None),
        ) as fetch_page:
            articles = bot.fetch_elpais_articles(
                "Manuel Jabois",
                "manuel-jabois-sueiro",
                datetime(2026, 6, 1, tzinfo=timezone.utc),
                errors,
            )

        self.assertEqual(articles, [])
        self.assertEqual(errors, [])
        self.assertEqual(fetch_page.call_count, 1)
        self.assertIn("news.google.com/rss/search", fetch_page.call_args.args[0])

    def test_elpais_google_news_success_avoids_direct_page_error(self):
        google_news_xml = """<?xml version="1.0"?>
        <rss><channel><title>Google News</title>
          <item>
            <title>Manuel Jabois firma su nueva columna - EL PAÍS</title>
            <link>https://news.google.com/articles/ok</link>
            <description><![CDATA[<a href="https://elpais.com/opinion/2026-06-01/columna.html">Ver</a>]]></description>
            <pubDate>Mon, 01 Jun 2026 10:00:00 +0000</pubDate>
          </item>
        </channel></rss>
        """
        errors = []

        article_html = (
            '<html><body><address><a href="/autor/manuel-jabois-sueiro/">'
            "Manuel Jabois</a></address></body></html>"
        )

        def fake_fetch(url):
            if "news.google.com" in url:
                return google_news_xml, None
            return article_html, None

        with patch.object(bot, "_fetch_page", side_effect=fake_fetch):
            articles = bot.fetch_elpais_articles(
                "Manuel Jabois",
                "manuel-jabois-sueiro",
                datetime(2026, 6, 1, tzinfo=timezone.utc),
                errors,
            )

        self.assertEqual(len(articles), 1)
        self.assertEqual(errors, [])

    def test_elpais_google_news_fallback_filters_out_non_author_entries(self):
        xml = """<?xml version="1.0"?>
        <rss><channel><title>Google News</title>
          <item>
            <title>Laporta celebra por todo lo alto el título de Liga - El País</title>
            <link>https://news.google.com/articles/bad</link>
            <description><![CDATA[<a href="https://elpais.com/deportes/2026-06-01/laporta.html">Ver</a>]]></description>
            <pubDate>Mon, 01 Jun 2026 11:00:00 +0000</pubDate>
          </item>
          <item>
            <title>Manuel Jabois firma su nueva columna - EL PAÍS</title>
            <link>https://news.google.com/articles/ok</link>
            <description><![CDATA[<a href="https://elpais.com/opinion/2026-06-01/columna.html">Ver</a>]]></description>
            <pubDate>Mon, 01 Jun 2026 10:00:00 +0000</pubDate>
          </item>
        </channel></rss>
        """
        article_pages = {
            "https://elpais.com/deportes/2026-06-01/laporta.html": (
                '<html><body><address><a href="/autor/otra-persona/">'
                "Otra Persona</a></address></body></html>"
            ),
            "https://elpais.com/opinion/2026-06-01/columna.html": (
                '<html><body><address><a href="/autor/manuel-jabois-sueiro/">'
                "Manuel Jabois</a></address></body></html>"
            ),
        }

        def fake_fetch(url):
            if url in article_pages:
                return article_pages[url], None
            return xml, None

        with patch.object(bot, "_fetch_page", side_effect=fake_fetch):
            articles = bot._fetch_elpais_google_news_articles(
                "Manuel Jabois",
                "manuel-jabois-sueiro",
                datetime(2026, 6, 1, tzinfo=timezone.utc),
            )

        self.assertEqual(
            [article["title"] for article in articles],
            ["Manuel Jabois firma su nueva columna"],
        )
        self.assertEqual(
            [article["url"] for article in articles],
            ["https://elpais.com/opinion/2026-06-01/columna.html"],
        )

    def test_elpais_google_news_skips_google_link_when_original_is_missing(self):
        xml = """<?xml version="1.0"?>
        <rss><channel><title>Google News</title>
          <item>
            <title>Manuel Jabois firma su nueva columna - EL PAÍS</title>
            <link>https://news.google.com/articles/ok</link>
            <pubDate>Mon, 01 Jun 2026 10:00:00 +0000</pubDate>
          </item>
        </channel></rss>
        """

        with patch.object(bot, "_fetch_page", return_value=(xml, None)):
            articles = bot._fetch_elpais_google_news_articles(
                "Manuel Jabois",
                "manuel-jabois-sueiro",
                datetime(2026, 6, 1, tzinfo=timezone.utc),
            )

        self.assertEqual(articles, [])

    def test_elplural_uses_google_news_for_benjamin_prado(self):
        xml = """<?xml version="1.0"?>
        <rss><channel><title>Google News</title>
          <item>
            <title>Benjamín Prado publica una nueva columna - El Plural</title>
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
            "Benjamín Prado publica una nueva columna",
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
             patch.object(bot, "load_seen_articles", return_value=[]), \
             patch.object(bot, "fetch_elpais_articles", return_value=[article]), \
             patch.object(bot, "send_telegram_message", return_value=False), \
             patch.object(bot, "fetch_tomorrow_weather_block", return_value=""), \
             patch.object(bot, "fetch_bitcoin_block", return_value=""), \
             patch.object(bot, "save_seen_articles", side_effect=lambda seen: saved_states.append(set(seen))):
            bot.run_digest(mode="evening")

        self.assertEqual(saved_states, [])

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
             patch.object(bot, "load_seen_articles", return_value=[]), \
             patch.object(bot, "fetch_podcast_segments", return_value=segments), \
             patch.object(bot, "fetch_weather_block", return_value=""), \
             patch.object(bot, "send_telegram_audio", side_effect=[True, False]), \
             patch.object(bot, "save_seen_articles", side_effect=lambda seen: saved_states.append(set(seen))):
            bot.run_digest(mode="morning")

        self.assertEqual(saved_states, [{first_hash}])
        self.assertNotIn(second_hash, saved_states[0])

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


if __name__ == "__main__":
    unittest.main()
