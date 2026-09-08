import importlib.util
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "neatqueue_client.py"
SPEC = importlib.util.spec_from_file_location("neatqueue_client", MODULE_PATH)
neatqueue_client = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(neatqueue_client)


class QueueResultLogicTests(unittest.TestCase):
    def test_derive_team_round_wins_from_round_winners(self):
        round_winner_display_indices = [0, 1, 0, 0]
        self.assertEqual(
            neatqueue_client.derive_team_round_wins_from_round_winners(round_winner_display_indices, 2),
            [3, 1],
        )


if __name__ == "__main__":
    unittest.main()
