import importlib.util
import os
import tempfile
import unittest
from pathlib import Path


class MonthlyLeaderboardStatsTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_cwd = os.getcwd()
        os.chdir(self.temp_dir.name)

    def tearDown(self):
        try:
            import leaderboard_bot
            if hasattr(leaderboard_bot, "conn"):
                leaderboard_bot.conn.close()
        except Exception:
            pass
        os.chdir(self.original_cwd)
        self.temp_dir.cleanup()

    def load_module(self):
        module_path = Path(__file__).resolve().with_name("leaderboard_bot.py")
        spec = importlib.util.spec_from_file_location("leaderboard_bot", module_path)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        return module

    def test_records_monthly_queue_stats(self):
        module = self.load_module()

        match_result = {
            "teams": [
                [
                    {
                        "discord_id": 42,
                        "stats": {
                            "games": 2,
                            "kills": 10,
                            "damage": 250,
                        },
                    }
                ]
            ],
            "duration_ms": 120000,
        }

        module.record_monthly_stats_from_queue_result(match_result)

        month_key = module.get_month_key()
        stats = module.get_monthly_stats(month_key)

        self.assertEqual(len(stats), 1)
        self.assertEqual(stats[0]["discord_id"], 42)
        self.assertEqual(stats[0]["avg_damage"], 125.0)
        self.assertEqual(stats[0]["total_kills"], 10)
        self.assertEqual(stats[0]["total_time_ms"], 120000)
        self.assertEqual(stats[0]["games_played"], 2)


if __name__ == "__main__":
    unittest.main()
