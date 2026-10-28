from datetime import datetime, timedelta, timezone
import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord
from PIL import Image

import db
import discord_ui
import image_utils
import leaderboard_bot
import leaderboard_service

sqlite_connect = sqlite3.connect


@contextmanager
def _connect_and_close(*args, **kwargs):
    connection = sqlite_connect(*args, **kwargs)
    try:
        with connection:
            yield connection
    finally:
        connection.close()


class HistoryDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp_dir.name) / "test_history.db")
        self.db_path_patch = patch.object(db, "DB_PATH", self.db_path)
        self.db_path_patch.start()
        self.connect_patch = patch.object(db.sqlite3, "connect", side_effect=_connect_and_close)
        self.connect_patch.start()

        conn = sqlite_connect(self.db_path)
        try:
            conn.execute(
                """
                CREATE TABLE player_queue_stats (
                    guild_id INTEGER NOT NULL,
                    match_id TEXT NOT NULL,
                    player_key TEXT NOT NULL,
                    discord_id INTEGER,
                    display_name TEXT,
                    username TEXT,
                    slug TEXT,
                    team_index INTEGER NOT NULL,
                    total_damage INTEGER NOT NULL,
                    avg_damage REAL NOT NULL,
                    games INTEGER,
                    duration_ms INTEGER NOT NULL,
                    match_start_ms INTEGER,
                    match_end_ms INTEGER,
                    cached_at TEXT NOT NULL,
                    kills INTEGER,
                    impact REAL,
                    winning_team_index INTEGER,
                    team_round_wins TEXT,
                    PRIMARY KEY (guild_id, match_id, player_key)
                )
                """
            )
            conn.execute(db.QUEUE_RESULTS_SCHEMA)
            conn.commit()
        finally:
            conn.close()

    def tearDown(self):
        self.connect_patch.stop()
        self.db_path_patch.stop()
        self.temp_dir.cleanup()

    def test_cache_player_queue_stats_stores_new_metrics_across_months(self):
        # Match from an arbitrary timestamp (even prior month)
        old_month_ms = 1_700_000_000_000
        player_entry = {
            "discord_id": 101,
            "username": "Awang",
            "slug": "awang",
            "stats": {"games": 3, "damage": 4500, "kills": 14, "impact": 78.5},
        }
        match_result = {
            "is_final": True,
            "match_start_ms": old_month_ms,
            "match_end_ms": old_month_ms + 600_000,
            "duration_ms": 600_000,
            "winning_team_index": 0,
            "team_round_wins": [3, 1],
            "teams": [
                [player_entry, dict(player_entry, discord_id=102), dict(player_entry, discord_id=103)],
                [dict(player_entry, discord_id=201), dict(player_entry, discord_id=202)],
            ],
        }

        cached_count = db.cache_player_queue_stats("5001", 10, match_result)
        self.assertEqual(cached_count, 5)

        history = db.get_player_queue_history(10, 101, limit=10)
        self.assertEqual(len(history), 1)
        row = history[0]
        self.assertEqual(row["match_id"], "5001")
        self.assertEqual(row["team_index"], 0)
        self.assertEqual(row["winning_team_index"], 0)
        self.assertEqual(row["won"], True)
        self.assertEqual(row["team_round_wins"], [3, 1])
        self.assertEqual(row["kills"], 14)
        self.assertEqual(row["total_damage"], 4500)
        self.assertEqual(row["avg_damage"], 1500.0)
        self.assertEqual(row["impact"], 78.5)

    def test_get_player_queue_history_orders_newest_first_and_respects_limit(self):
        for i in range(15):
            conn = sqlite_connect(self.db_path)
            try:
                conn.execute(
                    """
                    INSERT INTO player_queue_stats (
                        guild_id, match_id, player_key, discord_id, display_name, username, slug,
                        team_index, total_damage, avg_damage, games, duration_ms, match_start_ms,
                        match_end_ms, cached_at, kills, impact, winning_team_index, team_round_wins
                    ) VALUES (?, ?, ?, ?, ?, ?, NULL, 0, ?, ?, 3, 600000, ?, ?, 'test', ?, ?, ?, ?)
                    """,
                    (
                        10,
                        str(1000 + i),
                        "discord:101",
                        101,
                        "Awang",
                        "Awang",
                        3000 + i * 100,
                        1000.0 + i * 33.3,
                        100000 + i * 1000,
                        100600 + i * 1000,
                        5 + i,
                        60.0 + i,
                        0 if i % 2 == 0 else 1,
                        json.dumps([3, 1] if i % 2 == 0 else [1, 3]),
                    ),
                )
                conn.commit()
            finally:
                conn.close()

        history = db.get_player_queue_history(10, 101, limit=10)
        self.assertEqual(len(history), 10)
        # Newest match_end_ms first
        self.assertEqual(history[0]["match_id"], "1014")
        self.assertEqual(history[9]["match_id"], "1005")

    def test_legacy_row_backfills_from_queue_results_snapshot(self):
        # Insert a legacy row with NULL kills, impact, winning_team_index, team_round_wins
        conn = sqlite_connect(self.db_path)
        try:
            conn.execute(
                """
                INSERT INTO player_queue_stats (
                    guild_id, match_id, player_key, discord_id, display_name, username, slug,
                    team_index, total_damage, avg_damage, games, duration_ms, match_start_ms,
                    match_end_ms, cached_at, kills, impact, winning_team_index, team_round_wins
                ) VALUES (10, '3000', 'discord:101', 101, 'Awang', 'Awang', NULL, 0, 3000, 1000.0, 3, 600000, 100, 200, 'test', NULL, NULL, NULL, NULL)
                """
            )
            conn.commit()
        finally:
            conn.close()

        # Save snapshot in queue_results
        snapshot = {
            "v": 1,
            "match_id": "3000",
            "winning_team_index": 0,
            "team_round_wins": [3, 0],
            "games": [
                {
                    "number": 1,
                    "winner": 0,
                    "players": [
                        {"discord_id": 101, "name": "Awang", "kills": 5, "damage": 1000, "impact": 80.0, "team": 0},
                        {"discord_id": 201, "name": "Rival", "kills": 1, "damage": 500, "impact": 40.0, "team": 1},
                    ],
                },
                {
                    "number": 2,
                    "winner": 0,
                    "players": [
                        {"discord_id": 101, "name": "Awang", "kills": 6, "damage": 2000, "impact": 90.0, "team": 0},
                        {"discord_id": 201, "name": "Rival", "kills": 2, "damage": 700, "impact": 45.0, "team": 1},
                    ],
                },
            ],
        }
        db.save_queue_result(10, "3000", snapshot)

        history = db.get_player_queue_history(10, 101, limit=10)
        self.assertEqual(len(history), 1)
        row = history[0]
        self.assertEqual(row["match_id"], "3000")
        self.assertEqual(row["winning_team_index"], 0)
        self.assertEqual(row["team_round_wins"], [3, 0])
        self.assertEqual(row["won"], True)
        self.assertEqual(row["kills"], 11)
        self.assertEqual(row["impact"], 85.0)


    def test_get_player_queue_stats_summary_calculates_all_time_and_monthly_impact(self):
        now = datetime.now(timezone.utc)
        current_month_ms = int(datetime(now.year, now.month, 15, tzinfo=timezone.utc).timestamp() * 1000)
        prev_month_ms = int((datetime(now.year, now.month, 1, tzinfo=timezone.utc) - timedelta(days=15)).timestamp() * 1000)

        conn = sqlite_connect(self.db_path)
        try:
            # Match 1 (previous month): 2 games, damage 2000 (avg 1000), impact 60.0
            conn.execute(
                """
                INSERT INTO player_queue_stats (
                    guild_id, match_id, player_key, discord_id, display_name, username, slug,
                    team_index, total_damage, avg_damage, games, duration_ms, match_start_ms,
                    match_end_ms, cached_at, kills, impact, winning_team_index, team_round_wins
                ) VALUES (10, '1', 'discord:101', 101, 'Awang', 'Awang', NULL, 0, 2000, 1000.0, 2, 600000, ?, ?, 'test', 6, 60.0, 0, '[2, 0]')
                """,
                (prev_month_ms - 600000, prev_month_ms),
            )
            # Match 2 (current month): 4 games, damage 8000 (avg 2000), impact 90.0
            conn.execute(
                """
                INSERT INTO player_queue_stats (
                    guild_id, match_id, player_key, discord_id, display_name, username, slug,
                    team_index, total_damage, avg_damage, games, duration_ms, match_start_ms,
                    match_end_ms, cached_at, kills, impact, winning_team_index, team_round_wins
                ) VALUES (10, '2', 'discord:101', 101, 'Awang', 'Awang', NULL, 0, 8000, 2000.0, 4, 1200000, ?, ?, 'test', 20, 90.0, 0, '[3, 1]')
                """,
                (current_month_ms - 1200000, current_month_ms),
            )
            # Match 3 (current month, unrated map with impact NULL): 1 game, damage 1000, impact NULL
            conn.execute(
                """
                INSERT INTO player_queue_stats (
                    guild_id, match_id, player_key, discord_id, display_name, username, slug,
                    team_index, total_damage, avg_damage, games, duration_ms, match_start_ms,
                    match_end_ms, cached_at, kills, impact, winning_team_index, team_round_wins
                ) VALUES (10, '3', 'discord:101', 101, 'Awang', 'Awang', NULL, 0, 1000, 1000.0, 1, 300000, ?, ?, 'test', 2, NULL, 0, '[1, 0]')
                """,
                (current_month_ms - 300000, current_month_ms),
            )
            conn.commit()
        finally:
            conn.close()

        summary = db.get_player_queue_stats_summary(101)
        self.assertEqual(summary["matches"], 3)
        self.assertEqual(summary["total_duration_ms"], 2_100_000)
        # All-time games = 2 + 4 + 1 = 7, total damage = 11000 -> 11000 / 7
        self.assertAlmostEqual(summary["all_time_avg_damage"], 11000 / 7)
        # All-time impact scored games = 2 + 4 = 6. Points = 2*60 + 4*90 = 480 -> 480 / 6 = 80.0
        self.assertAlmostEqual(summary["all_time_avg_impact"], 80.0)

        # Monthly games = 4 + 1 = 5, damage = 9000 -> 9000 / 5 = 1800.0
        self.assertAlmostEqual(summary["monthly_avg_damage"], 1800.0)
        # Monthly impact scored games = 4. Points = 4 * 90 = 360 -> 360 / 4 = 90.0
        self.assertAlmostEqual(summary["monthly_avg_impact"], 90.0)

    def test_get_player_queue_stats_summary_empty(self):
        summary = db.get_player_queue_stats_summary(999999)
        self.assertEqual(summary["matches"], 0)
        self.assertEqual(summary["total_duration_ms"], 0)
        self.assertIsNone(summary["all_time_avg_damage"])
        self.assertIsNone(summary["monthly_avg_damage"])
        self.assertIsNone(summary["all_time_avg_impact"])
        self.assertIsNone(summary["monthly_avg_impact"])


class HistoryImageRenderingTests(unittest.TestCase):
    def sample_history(self, count=10):
        rows = []
        for i in range(count):
            rows.append({
                "match_id": str(4000 + i),
                "team_index": 0,
                "winning_team_index": 0 if i % 2 == 0 else 1,
                "team_round_wins": [3, 1] if i % 2 == 0 else [1, 3],
                "won": (i % 2 == 0),
                "games": 3,
                "kills": 8 + i,
                "total_damage": 3000 + i * 200,
                "avg_damage": 1000.0 + i * 66.7,
                "impact": 70.0 + i * 2.0 if i % 3 != 0 else None,  # some unscored
                "duration_ms": 600_000,
                "match_start_ms": 1_000_000,
                "match_end_ms": 1_600_000,
                "display_name": "Awang",
                "username": "Awang",
            })
        return rows

    def test_generate_history_image_dimensions_and_metrics(self):
        for metric in ("damage", "impact", "kills"):
            with self.subTest(metric=metric):
                buf = image_utils.generate_history_image("Awang", self.sample_history(10), metric=metric)
                img = Image.open(io.BytesIO(buf.getvalue()))
                self.assertEqual(img.width, image_utils.QUEUE_IMG_WIDTH)
                self.assertGreater(img.height, 600)

    def test_single_queue_history_renders_cleanly(self):
        buf = image_utils.generate_history_image("Awang", self.sample_history(1), metric="damage")
        img = Image.open(io.BytesIO(buf.getvalue()))
        self.assertEqual(img.width, image_utils.QUEUE_IMG_WIDTH)

    def test_empty_history_renders_gracefully(self):
        buf = image_utils.generate_history_image("Awang", [], metric="damage")
        img = Image.open(io.BytesIO(buf.getvalue()))
        self.assertEqual(img.width, image_utils.QUEUE_IMG_WIDTH)

    def test_unscored_impact_metric_renders_placeholder(self):
        rows = self.sample_history(3)
        for r in rows:
            r["impact"] = None
        buf = image_utils.generate_history_image("Awang", rows, metric="impact")
        img = Image.open(io.BytesIO(buf.getvalue()))
        self.assertEqual(img.width, image_utils.QUEUE_IMG_WIDTH)

    def test_undecided_matches_and_missing_scores(self):
        rows = [{
            "match_id": "9999",
            "team_index": 0,
            "winning_team_index": None,
            "team_round_wins": None,
            "won": None,
            "games": 1,
            "kills": None,
            "total_damage": 1200,
            "avg_damage": 1200.0,
            "impact": None,
            "duration_ms": 300_000,
            "match_start_ms": 100,
            "match_end_ms": 200,
            "display_name": "Ghost",
            "username": "Ghost",
        }]
        buf = image_utils.generate_history_image("Ghost", rows, metric="damage")
        img = Image.open(io.BytesIO(buf.getvalue()))
        self.assertEqual(img.width, image_utils.QUEUE_IMG_WIDTH)

    def test_identical_values_renders_without_zero_division(self):
        rows = self.sample_history(5)
        for r in rows:
            r["avg_damage"] = 1500.0
            r["impact"] = 75.0
            r["kills"] = 10
        for metric in ("damage", "impact", "kills"):
            with self.subTest(metric=metric):
                buf = image_utils.generate_history_image("Awang", rows, metric=metric)
                img = Image.open(io.BytesIO(buf.getvalue()))
                self.assertEqual(img.width, image_utils.QUEUE_IMG_WIDTH)

    def test_extreme_and_zero_values_render_cleanly(self):
        rows = self.sample_history(2)
        rows[0]["avg_damage"] = 0.0
        rows[0]["impact"] = 0.0
        rows[0]["kills"] = 0
        rows[1]["avg_damage"] = 50000.0
        rows[1]["impact"] = 100.0
        rows[1]["kills"] = 99
        for metric in ("damage", "impact", "kills"):
            with self.subTest(metric=metric):
                buf = image_utils.generate_history_image("Awang", rows, metric=metric)
                img = Image.open(io.BytesIO(buf.getvalue()))
                self.assertEqual(img.width, image_utils.QUEUE_IMG_WIDTH)


class HistoryPayloadAndRoutingTests(unittest.IsolatedAsyncioTestCase):
    @patch("leaderboard_service.get_player_queue_history")
    async def test_build_history_payload_success(self, mock_get_history):
        mock_get_history.return_value = [
            {
                "match_id": "3674",
                "team_index": 0,
                "winning_team_index": 0,
                "team_round_wins": [3, 1],
                "won": True,
                "games": 3,
                "kills": 12,
                "total_damage": 4200,
                "avg_damage": 1400.0,
                "impact": 75.0,
                "duration_ms": 600000,
                "match_start_ms": 100,
                "match_end_ms": 200,
                "display_name": "Awang",
                "username": "Awang",
            }
        ]
        target = SimpleNamespace(id=101, display_name="Awang")
        embed, file, norm_metric, error = await leaderboard_service.build_history_payload(10, target, "damage")

        self.assertIsNone(error)
        self.assertEqual(norm_metric, "damage")
        self.assertIn("Awang's Queue History", embed.title)
        self.assertIn("Average Damage", embed.description)
        self.assertEqual(file.filename, "history_10_101_damage.png")
        self.assertIn("Queue #3674 (Won)", file.description)
        self.assertEqual(embed.footer.text, "Toggle the trend chart above with the buttons below.")
        self.assertNotIn("Impact is survev.de", embed.footer.text)

    @patch("leaderboard_service.get_player_queue_history", return_value=[])
    async def test_build_history_payload_empty_results(self, _mock_history):
        target = SimpleNamespace(id=101, display_name="Awang")
        embed, file, norm_metric, error = await leaderboard_service.build_history_payload(10, target, "damage")

        self.assertIsNone(embed)
        self.assertIsNone(file)
        self.assertIn("No cached NeatQueue history found", error)

    async def test_build_history_payload_no_guild(self):
        target = SimpleNamespace(id=101, display_name="Awang")
        embed, file, norm_metric, error = await leaderboard_service.build_history_payload(None, target, "damage")

        self.assertIsNone(embed)
        self.assertIn("only available within a server", error)

    def test_parse_history_metric_id(self):
        metric, guild_id, discord_id = discord_ui.parse_history_metric_id("history_metric:impact:12345:67890")
        self.assertEqual((metric, guild_id, discord_id), ("impact", 12345, 67890))

    async def test_history_metric_view_button_styles(self):
        view = discord_ui.HistoryMetricView(12345, 67890, active_metric="impact")
        buttons = {c.label: (c.style, c.custom_id) for c in view.children}
        self.assertEqual(buttons["Damage"][0], discord.ButtonStyle.secondary)
        self.assertEqual(buttons["Impact"][0], discord.ButtonStyle.primary)
        self.assertEqual(buttons["Kills"][0], discord.ButtonStyle.secondary)
        self.assertEqual(buttons["Impact"][1], "history_metric:impact:12345:67890")

    async def test_log_interaction_routes_history_metric_click(self):
        interaction = MagicMock()
        interaction.type = discord.InteractionType.component
        interaction.data = {"custom_id": "history_metric:kills:10:101"}

        with patch("leaderboard_bot.refresh_history_message", new=AsyncMock()) as refresh:
            await leaderboard_bot.log_interaction(interaction)

        refresh.assert_awaited_once_with(interaction, "kills", 10, 101)


if __name__ == "__main__":
    unittest.main()
