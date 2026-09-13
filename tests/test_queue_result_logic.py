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

from queue_stats_service import build_queue_stats_payload


class QueueResultLogicTests(unittest.TestCase):
    def test_derive_team_round_wins_from_round_winners(self):
        round_winner_display_indices = [0, 1, 0, 0]
        self.assertEqual(
            neatqueue_client.derive_team_round_wins_from_round_winners(round_winner_display_indices, 2),
            [3, 1],
        )


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


if __name__ == "__main__":
    unittest.main()

