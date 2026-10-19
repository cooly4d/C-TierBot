import importlib.util
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "neatqueue_client.py"
SPEC = importlib.util.spec_from_file_location("neatqueue_client", MODULE_PATH)
neatqueue_client = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(neatqueue_client)


import io
import sqlite3
from types import SimpleNamespace
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


async def build_payload(teams=None, record_lines=(), **result_overrides):
    """Runs build_queue_stats_payload with the NeatQueue/survev.de lookups and image rendering faked out.
    Returns (content, the Hall of Fame check mock)."""
    match_result = {
        "teams": teams or [[{"discord_id": 1, "username": "Awang", "stats": {"games": 5, "kills": 30, "damage": 9000}}]],
        "winning_team_index": 0,
        "match_history": [True] * 5,
        "team_round_wins": [5, 0],
        "total_games_played": 5,
        "duration_ms": 3_600_000,
    }
    match_result.update(result_overrides)
    with patch("queue_stats_service.calculate_queue_match_stats", new=AsyncMock(return_value=(match_result, None))), \
            patch("queue_stats_service.resolve_queue_user_display_names", new=AsyncMock()), \
            patch("queue_stats_service.generate_queue_result_image", return_value=io.BytesIO(b"png")), \
            patch("queue_stats_service.cache_player_queue_stats"), \
            patch("queue_stats_service.save_queue_result") as save, \
            patch("queue_stats_service.check_queue_hall_of_fame_records", return_value=list(record_lines)) as hof:
        content, _file, error, _result = await build_queue_stats_payload("3674", 999)
    assert error is None, error
    return SimpleNamespace(content=content, hof=hof, save=save)


class HallOfFameFinalityTests(unittest.IsolatedAsyncioTestCase):
    """Only a finished queue may set Hall of Fame records: an unfinished one has partial stats and a duration
    that runs up to "now" (an abandoned panel would otherwise set an absurd Longest Queue)."""

    RECORD_LINE = "🏆 New Hall of Fame record! **Most Kills in a Queue**: 30 by Awang"

    async def _build(self, **result_overrides):
        return await build_payload(record_lines=[self.RECORD_LINE], **result_overrides)

    async def test_finished_queue_checks_records_and_announces_them(self):
        built = await self._build(is_final=True)

        built.hof.assert_called_once()
        self.assertEqual(built.hof.call_args.kwargs["duration_ms"], 3_600_000)
        self.assertIn(self.RECORD_LINE, built.content)

    async def test_unfinished_queue_never_touches_records(self):
        built = await self._build(is_final=False)

        built.hof.assert_not_called()
        self.assertNotIn("Hall of Fame", built.content)

    async def test_result_without_a_finality_flag_is_treated_as_unfinished(self):
        built = await self._build()

        built.hof.assert_not_called()
        self.assertNotIn("Hall of Fame", built.content)


class SnapshotSavingTests(unittest.IsolatedAsyncioTestCase):
    """A finished queue's per-game snapshot is stored for the Game breakdown button; an unfinished one never is."""

    async def test_finished_queue_saves_its_snapshot_for_that_guild_and_match(self):
        built = await build_payload(is_final=True)

        built.save.assert_called_once()
        guild_id, match_id, snapshot = built.save.call_args.args
        self.assertEqual((guild_id, match_id), (999, "3674"))
        self.assertEqual(snapshot["match_id"], "3674")
        self.assertEqual(snapshot["team_round_wins"], [5, 0])

    async def test_unfinished_queue_saves_nothing(self):
        built = await build_payload(is_final=False)

        built.save.assert_not_called()

    async def test_a_storage_failure_never_blocks_the_post(self):
        with patch("queue_stats_service.save_queue_result", side_effect=sqlite3.OperationalError("disk full")):
            with patch("queue_stats_service.calculate_queue_match_stats", new=AsyncMock(return_value=({
                "teams": [[{"discord_id": 1, "username": "Awang", "stats": {"games": 5, "kills": 30, "damage": 9000}}]],
                "winning_team_index": 0, "match_history": [True], "team_round_wins": [1, 0],
                "total_games_played": 1, "duration_ms": 1000, "is_final": True,
            }, None))), \
                    patch("queue_stats_service.resolve_queue_user_display_names", new=AsyncMock()), \
                    patch("queue_stats_service.generate_queue_result_image", return_value=io.BytesIO(b"png")), \
                    patch("queue_stats_service.cache_player_queue_stats"), \
                    patch("queue_stats_service.check_queue_hall_of_fame_records", return_value=[]):
                content, file, error, _ = await build_queue_stats_payload("3674", 999)

        self.assertIsNone(error)
        self.assertIsNotNone(file)
        self.assertIn("NeatQueue#3674", content)


class ImpactLegendTests(unittest.IsolatedAsyncioTestCase):
    """The message explains the Impact column, but only when the image actually has one."""

    @staticmethod
    def player(impact):
        return {"discord_id": 1, "username": "Awang", "stats": {"games": 5, "kills": 30, "damage": 9000, "impact": impact}}

    async def test_a_scored_series_explains_the_impact_column(self):
        built = await build_payload(teams=[[self.player(78.4)], [self.player(55.0)]], is_final=True)

        self.assertIn("\n-# Impact is survev.de's per-game impact score", built.content)

    async def test_an_unscored_series_has_no_legend(self):
        built = await build_payload(teams=[[self.player(None)], [self.player(None)]], is_final=True)

        self.assertNotIn("Impact", built.content)

    async def test_three_team_results_have_no_legend_because_the_column_is_hidden(self):
        teams = [[self.player(78.4)], [self.player(55.0)], [self.player(10.0)]]
        built = await build_payload(teams=teams, is_final=True)

        self.assertNotIn("Impact", built.content)


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

    async def test_shop_pagination_view_highlights_active_mode(self):
        import discord
        from discord_ui import ShopPaginationView

        view = ShopPaginationView(2, "weekly")
        view.update_button_states(0, "weekly")
        self.assertEqual(view.daily_button.style, discord.ButtonStyle.secondary)
        self.assertEqual(view.weekly_button.style, discord.ButtonStyle.primary)
        self.assertEqual(view.all_button.style, discord.ButtonStyle.secondary)
        self.assertTrue(view.prev_button.disabled)
        self.assertFalse(view.next_button.disabled)

    @patch("leaderboard_service.generate_shop_image")
    @patch("leaderboard_service.fetch_user_market", new_callable=AsyncMock)
    async def test_build_shop_image_payload_uses_cache_when_requested(self, mock_fetch, mock_image):
        import io
        from types import SimpleNamespace
        from leaderboard_service import _market_cache, build_shop_image_payload

        _market_cache.clear()
        mock_fetch.return_value = (
            {
                "market": {
                    "daily_items": ["outfit_1"],
                    "weekly_items": ["outfit_2"],
                    "user_market_items": [{"item": "outfit_1", "price": 100, "bought": False}],
                    "next_daily_reset": None,
                    "next_weekly_reset": None,
                }
            },
            None,
        )
        mock_image.side_effect = lambda *args, **kwargs: io.BytesIO(b"png")
        user = SimpleNamespace(id=777, display_name="Tester", display_avatar=SimpleNamespace(url=None))

        await build_shop_image_payload(user, "tok-abc", mode="daily", page=0, use_cache=False)
        await build_shop_image_payload(user, "tok-abc", mode="weekly", page=0, use_cache=True)

        self.assertEqual(mock_fetch.await_count, 1)


if __name__ == "__main__":
    unittest.main()

