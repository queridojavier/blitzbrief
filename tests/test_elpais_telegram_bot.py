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
    def test_elpais_scraper_ignores_cards_not_signed_by_author(self):
        html = """
        <html><body>
          <article>
            <h2><a href="/deportes/2026-06-01/articulo-bueno.html">Artículo bueno</a></h2>
            <a href="/autor/manuel-jabois-sueiro/">Manuel Jabois</a>
            <time datetime="2026-06-01T08:00:00+00:00"></time>
          </article>
          <article>
            <h2><a href="/espana/2026-06-01/ultima-hora.html">Última hora ajena</a></h2>
            <a href="/autor/otra-persona/">Otra Persona</a>
            <time datetime="2026-06-01T09:00:00+00:00"></time>
          </article>
        </body></html>
        """

        with patch.object(bot, "_fetch_page", return_value=(html, None)):
            articles = bot.fetch_elpais_articles(
                "Manuel Jabois",
                "manuel-jabois-sueiro",
                datetime(2026, 6, 1, tzinfo=timezone.utc),
            )

        self.assertEqual([article["title"] for article in articles], ["Artículo bueno"])

    def test_elpais_scraper_returns_only_latest_author_article(self):
        html = """
        <html><body>
          <article>
            <h2><a href="/deportes/2026-06-01/mas-reciente.html">Más reciente</a></h2>
            <a href="/autor/manuel-jabois-sueiro/">Manuel Jabois</a>
            <time datetime="2026-06-01T10:00:00+00:00"></time>
          </article>
          <article>
            <h2><a href="/deportes/2026-06-01/mas-antiguo.html">Más antiguo</a></h2>
            <a href="/autor/manuel-jabois-sueiro/">Manuel Jabois</a>
            <time datetime="2026-06-01T08:00:00+00:00"></time>
          </article>
        </body></html>
        """

        with patch.object(bot, "_fetch_page", return_value=(html, None)):
            articles = bot.fetch_elpais_articles(
                "Manuel Jabois",
                "manuel-jabois-sueiro",
                datetime(2026, 6, 1, tzinfo=timezone.utc),
            )

        self.assertEqual([article["title"] for article in articles], ["Más reciente"])

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
             patch.object(bot, "load_seen_articles", return_value=set()), \
             patch.object(bot, "fetch_elpais_articles", return_value=[article]), \
             patch.object(bot, "send_telegram_message", return_value=False), \
             patch.object(bot, "save_seen_articles", side_effect=lambda seen: saved_states.append(set(seen))):
            bot.run_digest()

        self.assertEqual(saved_states, [set()])

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
             patch.object(bot, "load_seen_articles", return_value=set()), \
             patch.object(bot, "fetch_podcast_segments", return_value=segments), \
             patch.object(bot, "send_telegram_audio", side_effect=[True, False]), \
             patch.object(bot, "save_seen_articles", side_effect=lambda seen: saved_states.append(set(seen))):
            bot.run_digest()

        self.assertEqual(saved_states, [{first_hash}])
        self.assertNotIn(second_hash, saved_states[0])

    def test_empty_digest_header_uses_madrid_timezone(self):
        with patch.object(bot, "datetime", FakeDateTime):
            message = bot.format_telegram_message([])

        self.assertIn(" 02 de ", message)
        self.assertNotIn(" 01 de ", message)

    def test_bitcoin_note_is_omitted_when_change_is_small(self):
        with patch.object(
            bot,
            "fetch_bitcoin_snapshot",
            return_value={"price_eur": 61234.0, "change_pct": 1.8},
        ):
            note = bot.build_bitcoin_note([])

        self.assertIsNone(note)

    def test_bitcoin_note_includes_price_and_change_when_relevant(self):
        with patch.object(
            bot,
            "fetch_bitcoin_snapshot",
            return_value={"price_eur": 61234.0, "change_pct": 4.2},
        ):
            note = bot.build_bitcoin_note([])

        self.assertEqual(note, "₿ Bitcoin: 61.234 € (+4.2% vs ayer)")

    def test_bitcoin_note_adds_context_only_for_large_moves(self):
        headlines = [
            {
                "source": "Financial Times",
                "title": "Bitcoin ETFs extend inflows after strong session",
                "description": "",
            }
        ]
        with patch.object(
            bot,
            "fetch_bitcoin_snapshot",
            return_value={"price_eur": 59876.0, "change_pct": 6.1},
        ):
            note = bot.build_bitcoin_note(headlines)

        self.assertIn("₿ Bitcoin: 59.876 € (+6.1% vs ayer)", note)
        self.assertIn("Motivo probable: Flujo de ETF o noticia regulatoria ligada a Bitcoin.", note)


if __name__ == "__main__":
    unittest.main()
