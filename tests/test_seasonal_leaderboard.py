import sqlite3
import tempfile
import unittest
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import db
from leaderboard_service import _parse_season_month

sqlite_connect = sqlite3.connect


@contextmanager
def _connect_and_close(*args, **kwargs):
    connection = sqlite_connect(*args, **kwargs)
    try:
        with connection:
            yield connection
    finally:
        connection.close()


class SeasonalQueueStatsTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp_dir.name) / "seasonal.db")
        self.db_path_patch = patch.object(db, "DB_PATH", self.db_path)
        self.db_path_patch.start()
        self.connect_patch = patch.object(db.sqlite3, "connect", side_effect=_connect_and_close)
        self.connect_patch.start()
        connection = sqlite_connect(self.db_path)
        try:
            connection.execute(
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
                    PRIMARY KEY (guild_id, match_id, player_key)
                )
                """
            )
            connection.commit()
        finally:
            connection.close()

    def tearDown(self):
        self.connect_patch.stop()
        self.db_path_patch.stop()
        self.temp_dir.cleanup()

    def _insert_row(self, guild_id, match_id, discord_id, damage, average, games, end_ms):
        connection = sqlite_connect(self.db_path)
        try:
            connection.execute(
                """
                INSERT INTO player_queue_stats (
                    guild_id, match_id, player_key, discord_id, display_name, username, slug,
                    team_index, total_damage, avg_damage, games, duration_ms, match_start_ms,
                    match_end_ms, cached_at
                ) VALUES (?, ?, ?, ?, ?, ?, NULL, 0, ?, ?, ?, 1, NULL, ?, 'test')
                """,
                (guild_id, match_id, f"discord:{discord_id}", discord_id, f"Player {discord_id}",
                 f"player{discord_id}", damage, average, games, end_ms),
            )
            connection.commit()
        finally:
            connection.close()

    def test_seasonal_average_is_game_weighted_and_excludes_zero_damage_rows(self):
        start_ms = 1_759_276_800_000  # 2025-09-01 UTC
        end_ms = 1_759_363_200_000
        self._insert_row(10, "a", 1, 100, 100.0, 1, start_ms)
        self._insert_row(10, "b", 1, 600, 200.0, 3, end_ms)
        self._insert_row(10, "c", 1, 0, 0.0, 2, end_ms)
        self._insert_row(10, "d", 2, 0, 0.0, None, end_ms)

        rows = db.get_seasonal_queue_stats(10, start_ms, end_ms + 1)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["discord_id"], 1)
        self.assertEqual(rows[0]["games"], 4)
        self.assertEqual(rows[0]["total_damage"], 700)
        self.assertEqual(rows[0]["avg_damage"], 175.0)

    def test_legacy_positive_damage_row_infers_game_count(self):
        start_ms = 1_759_276_800_000
        self._insert_row(10, "legacy", 1, 560, 140.0, None, start_ms)

        rows = db.get_seasonal_queue_stats(10, start_ms, start_ms + 1)

        self.assertEqual(rows[0]["games"], 4)
        self.assertEqual(rows[0]["avg_damage"], 140.0)

    def test_cache_keeps_existing_player_threshold_and_stores_games(self):
        match_end_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        player = {
            "discord_id": 1,
            "username": "player1",
            "slug": "player-1",
            "stats": {"games": 1, "damage": 75},
        }
        match_result = {
            "is_final": True,
            "match_end_ms": match_end_ms,
            "duration_ms": 60_000,
            "teams": [[player, dict(player, discord_id=2), dict(player, discord_id=3)],
                      [dict(player, discord_id=4), dict(player, discord_id=5)]],
        }

        cached_count = db.cache_player_queue_stats("eligible", 10, match_result)
        below_threshold_count = db.cache_player_queue_stats(
            "too-small",
            10,
            {**match_result, "teams": [[player], [dict(player, discord_id=2), dict(player, discord_id=3)]]},
        )

        connection = sqlite_connect(self.db_path)
        try:
            stored_games = connection.execute(
                "SELECT games FROM player_queue_stats WHERE match_id = 'eligible' AND discord_id = 1"
            ).fetchone()[0]
        finally:
            connection.close()

        self.assertEqual(cached_count, 5)
        self.assertEqual(below_threshold_count, 0)
        self.assertEqual(stored_games, 1)

    def test_seasonal_query_is_scoped_to_guild_and_half_open_month(self):
        start_ms = 1_759_276_800_000
        next_start_ms = start_ms + 100
        self._insert_row(10, "inside", 1, 100, 100.0, 1, start_ms)
        self._insert_row(10, "at-end", 2, 200, 200.0, 1, next_start_ms)
        self._insert_row(11, "other-guild", 3, 300, 300.0, 1, start_ms)

        rows = db.get_seasonal_queue_stats(10, start_ms, next_start_ms)

        self.assertEqual([row["discord_id"] for row in rows], [1])

    def test_month_parser_handles_year_rollover_and_rejects_invalid_format(self):
        start, next_start = _parse_season_month("2025-12")

        self.assertEqual((start.year, start.month, start.day), (2025, 12, 1))
        self.assertEqual((next_start.year, next_start.month, next_start.day), (2026, 1, 1))
        with self.assertRaises(ValueError):
            _parse_season_month("2025-13")
        with self.assertRaises(ValueError):
            _parse_season_month("Dec 2025")


if __name__ == "__main__":
    unittest.main()