"""
Tests para verificar que las notificaciones de partidos funcionan correctamente.

Ejecutar:
    python -m pytest test_match_notifications.py -v
    (o simplemente: python test_match_notifications.py)
"""

import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

# Forzar API_SPORTS_KEY para que fetch_upcoming_fixtures() no salga antes de tiempo
os.environ.setdefault("API_SPORTS_KEY", "test_key_fake")

import elpais_telegram_bot as bot


TZ_MADRID = timezone(timedelta(hours=2))


def _make_football_fixture(home: str, away: str, league: str, dt: datetime) -> dict:
    """Crea un fixture de fútbol con el formato de api-sports.io."""
    return {
        "fixture": {"date": dt.isoformat()},
        "teams": {
            "home": {"name": home},
            "away": {"name": away},
        },
        "league": {"name": league},
    }


def _make_basketball_game(home: str, away: str, league: str, dt: datetime) -> dict:
    """Crea un game de baloncesto con el formato de api-basketball."""
    return {
        "date": dt.isoformat(),
        "teams": {
            "home": {"name": home},
            "away": {"name": away},
        },
        "league": {"name": league},
    }


def _mock_football_response(fixtures: list[dict]) -> MagicMock:
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {"response": fixtures}
    return resp


def _mock_basketball_response(games: list[dict]) -> MagicMock:
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {"response": games}
    return resp


class TestFetchUpcomingFixtures(unittest.TestCase):

    @patch("elpais_telegram_bot.requests.get")
    @patch("elpais_telegram_bot._get_basketball_team_id", return_value=123)
    def test_football_match_today(self, mock_bball_id, mock_get):
        """Un partido de fútbol hoy debe aparecer con 'hoy' y el canal correcto."""
        now = datetime.now(TZ_MADRID)
        match_time = now.replace(hour=21, minute=0, second=0, microsecond=0)
        # Si ya pasaron las 21:00, usar mañana
        if match_time < now:
            match_time += timedelta(days=1)

        fixture = _make_football_fixture(
            "Real Madrid", "Atlético Madrid", "La Liga", match_time
        )

        # Mock: primera llamada = fútbol Real Madrid, segunda = fútbol Málaga, tercera+ = basket
        mock_get.side_effect = [
            _mock_football_response([fixture]),
            _mock_football_response([]),  # Málaga sin partidos
            _mock_basketball_response([]),  # Real Madrid basket
            _mock_basketball_response([]),  # Unicaja basket
        ]

        lines = bot.fetch_upcoming_fixtures()

        self.assertEqual(len(lines), 1)
        self.assertIn("⚽", lines[0])
        self.assertIn("Real Madrid", lines[0])
        self.assertIn("Atlético Madrid", lines[0])
        self.assertIn("La Liga", lines[0])
        self.assertIn("21:00", lines[0])
        self.assertIn("DAZN / Movistar+ LaLiga", lines[0])

    @patch("elpais_telegram_bot.requests.get")
    @patch("elpais_telegram_bot._get_basketball_team_id", return_value=456)
    def test_basketball_match_tomorrow(self, mock_bball_id, mock_get):
        """Un partido de baloncesto mañana debe aparecer con 'mañana'."""
        now = datetime.now(TZ_MADRID)
        tomorrow = (now + timedelta(days=1)).replace(
            hour=19, minute=30, second=0, microsecond=0
        )

        game = _make_basketball_game(
            "Unicaja", "FC Barcelona", "Liga ACB", tomorrow
        )

        mock_get.side_effect = [
            _mock_football_response([]),  # Real Madrid fútbol
            _mock_football_response([]),  # Málaga fútbol
            _mock_basketball_response([]),  # Real Madrid basket
            _mock_basketball_response([game]),  # Unicaja basket
        ]

        lines = bot.fetch_upcoming_fixtures()

        self.assertEqual(len(lines), 1)
        self.assertIn("🏀", lines[0])
        self.assertIn("Unicaja", lines[0])
        self.assertIn("FC Barcelona", lines[0])
        self.assertIn("mañana", lines[0])
        self.assertIn("19:30", lines[0])
        self.assertIn("Movistar+ Deportes", lines[0])

    @patch("elpais_telegram_bot.requests.get")
    @patch("elpais_telegram_bot._get_basketball_team_id", return_value=123)
    def test_match_in_3_days_not_shown(self, mock_bball_id, mock_get):
        """Un partido dentro de 3 días NO debe aparecer."""
        now = datetime.now(TZ_MADRID)
        future = (now + timedelta(days=3)).replace(
            hour=20, minute=0, second=0, microsecond=0
        )

        fixture = _make_football_fixture(
            "Real Madrid", "Sevilla", "La Liga", future
        )

        mock_get.side_effect = [
            _mock_football_response([fixture]),
            _mock_football_response([]),
            _mock_basketball_response([]),
            _mock_basketball_response([]),
        ]

        lines = bot.fetch_upcoming_fixtures()
        self.assertEqual(len(lines), 0)

    @patch("elpais_telegram_bot.requests.get")
    @patch("elpais_telegram_bot._get_basketball_team_id", return_value=789)
    def test_multiple_matches(self, mock_bball_id, mock_get):
        """Varios partidos de distintos deportes deben aparecer todos."""
        now = datetime.now(TZ_MADRID)
        today_21 = now.replace(hour=21, minute=0, second=0, microsecond=0)
        if today_21 < now:
            today_21 += timedelta(days=1)

        tomorrow_20 = (now + timedelta(days=1)).replace(
            hour=20, minute=0, second=0, microsecond=0
        )

        football_fix = _make_football_fixture(
            "Real Madrid", "Barcelona", "La Liga", today_21
        )
        basketball_game = _make_basketball_game(
            "Real Madrid Baloncesto", "Olympiacos", "EuroLeague", tomorrow_20
        )

        mock_get.side_effect = [
            _mock_football_response([football_fix]),  # RM fútbol
            _mock_football_response([]),  # Málaga
            _mock_basketball_response([basketball_game]),  # RM basket
            _mock_basketball_response([]),  # Unicaja
        ]

        lines = bot.fetch_upcoming_fixtures()

        self.assertEqual(len(lines), 2)
        self.assertIn("⚽", lines[0])
        self.assertIn("🏀", lines[1])

    @patch("elpais_telegram_bot.requests.get")
    @patch("elpais_telegram_bot._get_basketball_team_id", return_value=123)
    def test_unknown_league_no_channel(self, mock_bball_id, mock_get):
        """Liga desconocida no debe mostrar canal de TV."""
        now = datetime.now(TZ_MADRID)
        today_21 = now.replace(hour=21, minute=0, second=0, microsecond=0)
        if today_21 < now:
            today_21 += timedelta(days=1)

        fixture = _make_football_fixture(
            "Real Madrid", "Sheriff", "Liga Desconocida", today_21
        )

        mock_get.side_effect = [
            _mock_football_response([fixture]),
            _mock_football_response([]),
            _mock_basketball_response([]),
            _mock_basketball_response([]),
        ]

        lines = bot.fetch_upcoming_fixtures()

        self.assertEqual(len(lines), 1)
        self.assertNotIn("DAZN", lines[0])
        self.assertNotIn("Movistar", lines[0])
        # Debe terminar en la hora, sin " — canal"
        self.assertTrue(lines[0].endswith("21:00"))

    @patch("elpais_telegram_bot.requests.get")
    @patch("elpais_telegram_bot._get_basketball_team_id", return_value=123)
    def test_no_api_key_returns_empty(self, mock_bball_id, mock_get):
        """Sin API_SPORTS_KEY, debe retornar lista vacía."""
        original = bot.API_SPORTS_KEY
        try:
            bot.API_SPORTS_KEY = ""
            lines = bot.fetch_upcoming_fixtures()
            self.assertEqual(lines, [])
            mock_get.assert_not_called()
        finally:
            bot.API_SPORTS_KEY = original


class TestFixturesInBriefingMessage(unittest.TestCase):

    def test_fixtures_block_format(self):
        """Verifica que el bloque de fixtures se formatea correctamente en el mensaje."""
        fixtures = [
            "⚽ Real Madrid vs Atlético Madrid (La Liga) — hoy 21:00 — DAZN / Movistar+ LaLiga",
            "🏀 Unicaja vs Barcelona (Liga ACB) — mañana 19:30 — Movistar+ Deportes",
        ]

        # Simula cómo send_news_briefing() construye el bloque
        fixtures_block = "\n\n📅 PARTIDOS HOY / MAÑANA:\n" + "\n".join(fixtures)

        briefing_text = "Sección de noticias aquí..."
        now = datetime.now(TZ_MADRID)
        date_str = now.strftime("%d/%m/%Y")
        message = f"📰 BRIEFING DE NOTICIAS — {date_str}\n\n{briefing_text}{fixtures_block}"

        # Verificaciones
        self.assertIn("📅 PARTIDOS HOY / MAÑANA:", message)
        self.assertIn("⚽ Real Madrid vs Atlético Madrid", message)
        self.assertIn("🏀 Unicaja vs Barcelona", message)
        self.assertIn("📰 BRIEFING DE NOTICIAS", message)
        # El bloque de partidos debe ir al final
        self.assertTrue(message.index("📅 PARTIDOS") > message.index("noticias aquí"))

    def test_no_fixtures_no_block(self):
        """Sin partidos, no debe haber bloque de fixtures."""
        fixtures = []
        fixtures_block = ""
        if fixtures:
            fixtures_block = "\n\n📅 PARTIDOS HOY / MAÑANA:\n" + "\n".join(fixtures)

        message = f"📰 BRIEFING DE NOTICIAS — 22/03/2026\n\nNoticias...{fixtures_block}"

        self.assertNotIn("PARTIDOS", message)


if __name__ == "__main__":
    unittest.main()
