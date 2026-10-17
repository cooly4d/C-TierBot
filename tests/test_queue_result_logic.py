import importlib.util
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "neatqueue_client.py"
SPEC = importlib.util.spec_from_file_location("neatqueue_client", MODULE_PATH)
neatqueue_client = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(neatqueue_client)


import io
import unittest
from unittest.mock import AsyncMock, patch

from queue_stats_service import build_queue_stats_payload, check_queue_hall_of_fame_records


class QueueResultLogicTests(unittest.TestCase):
    def test_derive_team_round_wins_from_round_winners(self):
        round_winner_display_indices = [0, 1, 0, 0]
        self.assertEqual(
            neatqueue_client.derive_team_round_wins_from_round_winners(round_winner_display_indices, 2),
            [3, 1],
        )


class HallOfFameEligibilityTests(unittest.TestCase):
    def _make_entry(self, name, damage, games, kills, discord_id="1"):
        return {
            "display_name": name,
            "username": name,
            "discord_id": discord_id,
            "stats": {"damage": damage, "games": games, "kills": kills},
        }

    @patch("queue_stats_service.try_set_hall_of_fame_record", return_value=True)
    def test_sub_with_too_few_games_is_excluded(self, mock_try_set):
        # Sub played 1 of 5 games with an outlier round; a full-time player has lower
        # per-round numbers but meets the games floor and should win instead.
        sub = self._make_entry("Sub", damage=560, games=1, kills=10)
        regular = self._make_entry("Regular", damage=1000, games=4, kills=6)
        teams = [[sub, regular]]

        check_queue_hall_of_fame_records(teams, "4178", 999, total_games_played=5)

        holder_names = {call.args[3] for call in mock_try_set.call_args_list}
        self.assertIn("Regular", holder_names)
        self.assertNotIn("Sub", holder_names)

    @patch("queue_stats_service.try_set_hall_of_fame_record", return_value=True)
    def test_eligible_candidates_still_considered(self, mock_try_set):
        strong = self._make_entry("Strong", damage=2000, games=5, kills=20)
        weak = self._make_entry("Weak", damage=100, games=5, kills=1)
        teams = [[strong, weak]]

        check_queue_hall_of_fame_records(teams, "4178", 999, total_games_played=5)

        holder_names = {call.args[3] for call in mock_try_set.call_args_list}
        self.assertIn("Strong", holder_names)
        self.assertNotIn("Weak", holder_names)


class QueueStatsServiceTests(unittest.IsolatedAsyncioTestCase):
    @patch("queue_stats_service.cache_player_queue_stats")
    @patch("queue_stats_service.check_queue_hall_of_fame_records", return_value=[])
    @patch("queue_stats_service.generate_queue_result_image", return_value=io.BytesIO(b"fake_png"))
    @patch("queue_stats_service.resolve_queue_user_display_names", new_callable=AsyncMock)
    @patch("queue_stats_service.calculate_queue_match_stats", new_callable=AsyncMock)
    async def test_build_queue_stats_payload_caches_player_stats(
        self,
        mock_calc,
        mock_resolve,
        mock_gen_img,
        mock_hof,
        mock_cache,
    ):
        mock_calc.return_value = (
            {
                "teams": [[{"id": "1", "stats": {"damage": 100}}]],
                "winning_team_index": 0,
                "match_history": [True],
                "team_round_wins": [1],
                "total_games_played": 1,
            },
            None,
        )
        content, file, error, match_result = await build_queue_stats_payload("123", 456)
        self.assertIsNone(error)
        self.assertIsNotNone(match_result)
        mock_cache.assert_called_once_with("123", 456, match_result)


class RefactoredHelpersTests(unittest.IsolatedAsyncioTestCase):
    def test_store_pagination_state_evicts_oldest_entries(self):
        from discord_ui import MAX_PAGINATION_STATE_ENTRIES, store_pagination_state

        state = {}
        for i in range(MAX_PAGINATION_STATE_ENTRIES + 5):
            store_pagination_state(state, i, ("user", "tok", "all", 0, 1))

        self.assertEqual(len(state), MAX_PAGINATION_STATE_ENTRIES)
        self.assertNotIn(0, state)
        self.assertIn(MAX_PAGINATION_STATE_ENTRIES + 4, state)

    @patch("leaderboard_service.fetch_player_timeframe_stats", new_callable=AsyncMock)
    @patch("leaderboard_service.get_all_users", return_value=[(101, "tok1"), (202, "tok2")])
    async def test_generate_leaderboard_embed_concurrent_fetch(self, _mock_users, mock_fetch):
        from leaderboard_service import generate_leaderboard_embed

        mock_fetch.side_effect = [
            {"games": 3, "wins": 1, "kills": 12, "damage": 900},
            {"games": 5, "wins": 2, "kills": 25, "damage": 1800},
        ]
        embed = await generate_leaderboard_embed("Weekly", 7, "kills")
        self.assertIn("<@202>", embed.fields[0].value)
        self.assertEqual(mock_fetch.await_count, 2)


if __name__ == "__main__":
    unittest.main()

