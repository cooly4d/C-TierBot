"""/leaderboard (weekly/monthly, with its per-player cache) and /leaderboard_fries."""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import leaderboard_service

USERS = [(1, "tok-1"), (2, "tok-2"), (3, "tok-3")]


class LeaderboardCacheTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        leaderboard_service._timeframe_cache.clear()
        self.addCleanup(leaderboard_service._timeframe_cache.clear)
        self.clock = [1000.0]
        self.enterContext(patch.object(leaderboard_service, "time", SimpleNamespace(monotonic=lambda: self.clock[0])))
        self.enterContext(patch.object(leaderboard_service, "get_all_users", return_value=USERS))
        self.complete = {token: True for _, token in USERS}
        self.stats = {
            "tok-1": {"games": 4, "wins": 1, "kills": 9, "damage": 1500},
            "tok-2": {"games": 6, "wins": 3, "kills": 4, "damage": 2500},
            "tok-3": {"games": 0, "wins": 0, "kills": 0, "damage": 0},
        }
        self.fetched: list[tuple[str, int]] = []

        async def fake_fetch(_session, token, from_ms, to_ms, diagnostics=None):
            self.fetched.append((token, round((to_ms - from_ms) / 86_400_000)))
            diagnostics["pagination_complete"] = self.complete[token]
            return self.stats[token]

        self.enterContext(patch.object(leaderboard_service, "fetch_player_timeframe_stats", side_effect=fake_fetch))

    async def test_changing_the_stat_within_five_minutes_re_sorts_without_fetching(self):
        by_kills = await leaderboard_service.generate_leaderboard_embed("Weekly", 7, "kills")
        by_damage = await leaderboard_service.generate_leaderboard_embed("Weekly", 7, "damage")

        self.assertEqual(len(self.fetched), 3)
        self.assertLess(by_kills.fields[0].value.index("<@1>"), by_kills.fields[0].value.index("<@2>"))
        self.assertLess(by_damage.fields[0].value.index("<@2>"), by_damage.fields[0].value.index("<@1>"))
        self.assertNotIn("<@3>", by_damage.fields[0].value)  # no games in the period

    async def test_weekly_and_monthly_are_cached_separately_and_expire(self):
        await leaderboard_service.generate_leaderboard_embed("Weekly", 7, "kills")
        await leaderboard_service.generate_leaderboard_embed("Monthly", 30, "kills")
        self.assertEqual(sorted({days for _, days in self.fetched}), [7, 30])
        self.assertEqual(len(self.fetched), 6)

        self.clock[0] += leaderboard_service.LEADERBOARD_CACHE_TTL_SECONDS
        await leaderboard_service.generate_leaderboard_embed("Weekly", 7, "kills")
        self.assertEqual(len(self.fetched), 9)

    async def test_an_incomplete_fetch_is_retried_on_the_next_click(self):
        # e.g. a rate-limited page: that player is retried next time instead of sitting out for five minutes.
        self.complete["tok-2"] = False
        await leaderboard_service.generate_leaderboard_embed("Weekly", 7, "kills")
        self.fetched.clear()

        await leaderboard_service.generate_leaderboard_embed("Weekly", 7, "wins")

        self.assertEqual(self.fetched, [("tok-2", 7)])


class FriesLeaderboardTests(unittest.IsolatedAsyncioTestCase):
    async def test_players_are_ranked_by_balance_and_failed_lookups_are_left_out(self):
        markets = {
            "tok-1": ({"balance": 50}, None),
            "tok-2": (None, "survev.de is rate limiting the bot right now. Try again in a minute."),
            "tok-3": ({"balance": None}, None),
            "tok-4": ({"balance": 9000}, None),
        }
        users = [(1, "tok-1"), (2, "tok-2"), (3, "tok-3"), (4, "tok-4")]
        with patch.object(leaderboard_service, "get_all_users", return_value=users), \
                patch.object(leaderboard_service, "fetch_user_market",
                             new=AsyncMock(side_effect=lambda _session, token: markets[token])):
            embed, error = await leaderboard_service.build_leaderboard_fries_embed()

        self.assertIsNone(error)
        text = embed.fields[0].value
        self.assertLess(text.index("<@4>"), text.index("<@1>"))
        self.assertIn("**9,000**", text)
        self.assertNotIn("<@2>", text)
        self.assertNotIn("<@3>", text)

    async def test_only_the_top_ten_are_shown(self):
        users = [(i, f"tok-{i}") for i in range(12)]
        with patch.object(leaderboard_service, "get_all_users", return_value=users), \
                patch.object(leaderboard_service, "fetch_user_market",
                             new=AsyncMock(side_effect=lambda _session, token: ({"balance": int(token[4:])}, None))):
            embed, _error = await leaderboard_service.build_leaderboard_fries_embed()

        self.assertEqual(embed.fields[0].value.count("<@"), 10)
        self.assertNotIn("<@0>", embed.fields[0].value)

    async def test_no_linked_players_or_no_balances_explain_why(self):
        with patch.object(leaderboard_service, "get_all_users", return_value=[]):
            self.assertEqual(await leaderboard_service.build_leaderboard_fries_embed(), (None, "No verified users found."))

        with patch.object(leaderboard_service, "get_all_users", return_value=[(1, "tok-1")]), \
                patch.object(leaderboard_service, "fetch_user_market", new=AsyncMock(return_value=(None, "boom"))):
            embed, error = await leaderboard_service.build_leaderboard_fries_embed()
        self.assertIsNone(embed)
        self.assertIn("No Golden Fries balances", error)


if __name__ == "__main__":
    unittest.main()
