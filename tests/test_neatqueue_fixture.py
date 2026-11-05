"""Parsing a real NeatQueue history response: queue 4620, fetched 2026-11-05 from GET /api/v1/history/{guild_id}.
Player names and every Discord ID in fixtures/neatqueue_history_finished.json were replaced; the shape, times,
team numbers and winner are as NeatQueue sent them."""
import json
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import leaderboard_bot
import neatqueue_client

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "neatqueue_history_finished.json").read_text(encoding="utf-8"))
ENTRY = FIXTURE["data"][0]


class RealHistoryShapeTests(unittest.TestCase):
    def test_the_response_unwraps_to_its_entries(self):
        self.assertEqual(neatqueue_client.extract_neatqueue_entries(FIXTURE), [ENTRY])

    def test_roster_is_two_teams_of_four_with_string_discord_ids(self):
        teams = neatqueue_client.collect_neatqueue_teams(ENTRY)

        self.assertEqual([len(team) for team in teams], [4, 4])
        self.assertTrue(all(isinstance(p["id"], str) and p["id"].isdigit() for team in teams for p in team))
        self.assertEqual([{p["team_num"] for p in team} for team in teams], [{0}, {1}])

    def test_start_is_the_naive_utc_time_field(self):
        expected = int(datetime(2026, 11, 4, 21, 23, 3, tzinfo=timezone.utc).timestamp() * 1000)
        self.assertEqual(neatqueue_client.get_match_start_ms(ENTRY), expected)

    def test_a_finished_queue_has_a_team_index_as_winner_and_no_end_time(self):
        self.assertIn(ENTRY["winner"], (0, 1))
        self.assertNotIn("end_time_ms", ENTRY)
        self.assertNotIn("end_time", ENTRY)

    @unittest.expectedFailure
    def test_queue_number_is_read_from_game_num(self):
        # Known bug, review item 6 (deprioritized by the user): NeatQueue sends the queue number as "game_num",
        # which NEATQUEUE_MATCH_ID_KEYS doesn't include, so this returns None.
        self.assertEqual(neatqueue_client.get_match_game_number(ENTRY), 4620)


class RealHistoryCatchUpTests(unittest.IsolatedAsyncioTestCase):
    @unittest.expectedFailure
    async def test_startup_catch_up_posts_a_queue_missed_while_offline(self):
        # Known bug, review item 6: get_match_game_number returns None for real entries, so nothing is posted.
        channel = MagicMock()
        channel.send = AsyncMock()
        winner_channel = int(ENTRY["winner_channel"])
        payload = AsyncMock(return_value=("content", "file", None, {"is_final": True}))
        with patch.object(leaderboard_bot, "get_all_guild_settings", return_value=[(10, winner_channel, "2026-11-04T00:00:00+00:00")]), \
                patch.object(leaderboard_bot, "fetch_neatqueue_matches_since", new=AsyncMock(return_value=([ENTRY], None))), \
                patch.object(leaderboard_bot, "try_mark_match_processed", return_value=True), \
                patch.object(leaderboard_bot, "build_queue_stats_payload", new=payload), \
                patch.object(leaderboard_bot, "save_queue_stats_log", new=AsyncMock()), \
                patch.object(leaderboard_bot, "update_guild_last_updated"), \
                patch.object(leaderboard_bot.bot, "get_channel", return_value=channel):
            await leaderboard_bot.backfill_missed_queue_results()

        payload.assert_awaited_once_with("4620", 10)
        channel.send.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
