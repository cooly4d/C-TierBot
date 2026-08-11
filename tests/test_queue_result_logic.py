import importlib.util
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "leaderboard_bot.py"
SPEC = importlib.util.spec_from_file_location("leaderboard_bot", MODULE_PATH)
leaderboard_bot = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(leaderboard_bot)


class QueueResultLogicTests(unittest.TestCase):
    def test_derive_team_round_wins_from_round_winners(self):
        round_winner_display_indices = [0, 1, 0, 0]
        self.assertEqual(
            leaderboard_bot.derive_team_round_wins_from_round_winners(round_winner_display_indices, 2),
            [3, 1],
        )


if __name__ == "__main__":
    unittest.main()
