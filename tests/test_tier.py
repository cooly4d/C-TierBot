import io
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import aiohttp
from PIL import Image, ImageChops

import leaderboard_service
from image_utils import format_squad_tier, generate_compare_image
from survev_client import extract_squad_tier, fetch_user_stats

STATS = {
    "slug": "awang",
    "username": "Awang",
    "primaryRegion": "eu",
    "modes": [
        {"teamMode": 1, "games": 10, "rating": None, "tier": None},
        {"teamMode": 2, "games": 40, "rating": 61, "tier": "B"},
        {"teamMode": 4, "games": 120, "rating": 72, "tier": "A-"},
    ],
}


class FakeResponse:
    def __init__(self, status, payload=None):
        self.status = status
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def json(self):
        return self._payload


class FakeSession:
    def __init__(self, response=None, error=None):
        self.response, self.error, self.calls = response, error, []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self.error:
            raise self.error
        return self.response


class ExtractSquadTierTests(unittest.TestCase):
    def test_picks_the_squad_mode_and_keeps_two_character_tiers(self):
        self.assertEqual(extract_squad_tier(STATS), {"tier": "A-", "rating": 72, "region": "eu"})

    def test_players_without_squad_games_are_unranked(self):
        stats = {**STATS, "modes": [m for m in STATS["modes"] if m["teamMode"] != 4]}
        self.assertIsNone(extract_squad_tier(stats))

    def test_squad_mode_without_a_rating_is_unranked(self):
        stats = {**STATS, "modes": [{"teamMode": 4, "games": 3, "rating": None, "tier": None}]}
        self.assertIsNone(extract_squad_tier(stats))

    def test_missing_or_malformed_responses_are_unranked(self):
        for bad in (None, [], "oops", {}, {"modes": None}, {"modes": ["nope"]}):
            with self.subTest(bad=bad):
                self.assertIsNone(extract_squad_tier(bad))

    def test_rating_is_normalized_to_an_int(self):
        for raw, expected in (("72", 72), (71.6, 72), (72, 72)):
            stats = {"modes": [{"teamMode": 4, "rating": raw, "tier": "A"}]}
            with self.subTest(raw=raw):
                self.assertEqual(extract_squad_tier(stats)["rating"], expected)

    def test_unparseable_rating_is_unranked(self):
        stats = {"modes": [{"teamMode": 4, "rating": "n/a", "tier": "A"}]}
        self.assertIsNone(extract_squad_tier(stats))

    def test_blank_region_becomes_none(self):
        stats = {**STATS, "primaryRegion": ""}
        self.assertIsNone(extract_squad_tier(stats)["region"])


class FetchUserStatsTests(unittest.IsolatedAsyncioTestCase):
    async def test_posts_the_alltime_all_maps_request_with_a_bearer_token(self):
        session = FakeSession(FakeResponse(200, STATS))

        data, error = await fetch_user_stats(session, "tok-abc")

        self.assertEqual((data, error), (STATS, None))
        url, kwargs = session.calls[0]
        self.assertEqual(url, "https://survev.de/api/external/stats")
        self.assertEqual(kwargs["json"], {"interval": "alltime", "mapIdFilter": "-1"})
        self.assertEqual(kwargs["headers"], {"Authorization": "Bearer tok-abc"})

    async def test_http_errors_become_friendly_messages(self):
        expectations = {401: "/verify", 403: "read:stats", 429: "rate limiting", 500: "(500)"}
        for status, needle in expectations.items():
            with self.subTest(status=status):
                data, error = await fetch_user_stats(FakeSession(FakeResponse(status)), "tok")
                self.assertIsNone(data)
                self.assertIn(needle, error)

    async def test_network_failures_are_reported_not_raised(self):
        data, error = await fetch_user_stats(FakeSession(error=aiohttp.ClientError("boom")), "tok")

        self.assertIsNone(data)
        self.assertIn("boom", error)


class GetSquadTierTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        leaderboard_service._tier_cache.clear()
        self.clock = [1000.0]
        clock_patch = patch.object(leaderboard_service, "time", SimpleNamespace(monotonic=lambda: self.clock[0]))
        clock_patch.start()
        self.addCleanup(clock_patch.stop)
        self.addCleanup(leaderboard_service._tier_cache.clear)

    async def test_successful_lookups_are_cached_until_the_ttl_expires(self):
        session = object()
        with patch.object(leaderboard_service, "fetch_user_stats", AsyncMock(return_value=(STATS, None))) as fetch:
            first = await leaderboard_service.get_squad_tier(7, "tok", session)
            self.clock[0] += leaderboard_service.TIER_CACHE_TTL_SECONDS - 1
            second = await leaderboard_service.get_squad_tier(7, "tok", session)
            self.assertEqual(fetch.await_count, 1)

            self.clock[0] += 2
            await leaderboard_service.get_squad_tier(7, "tok", session)
            self.assertEqual(fetch.await_count, 2)

        self.assertEqual(first, ({"tier": "A-", "rating": 72, "region": "eu"}, None))
        self.assertEqual(second, first)
        self.assertIs(fetch.await_args.args[0], session)

    async def test_unranked_players_are_cached_too(self):
        unranked = {**STATS, "modes": []}
        with patch.object(leaderboard_service, "fetch_user_stats", AsyncMock(return_value=(unranked, None))) as fetch:
            self.assertEqual(await leaderboard_service.get_squad_tier(7, "tok", object()), (None, None))
            self.assertEqual(await leaderboard_service.get_squad_tier(7, "tok", object()), (None, None))
            self.assertEqual(fetch.await_count, 1)

    async def test_errors_are_returned_and_never_cached(self):
        responses = [(None, "survev.de is rate limiting the bot right now."), (STATS, None)]
        with patch.object(leaderboard_service, "fetch_user_stats", AsyncMock(side_effect=responses)) as fetch:
            tier, error = await leaderboard_service.get_squad_tier(7, "tok", object())
            self.assertIsNone(tier)
            self.assertIn("rate limiting", error)

            tier, error = await leaderboard_service.get_squad_tier(7, "tok", object())
            self.assertEqual((tier["tier"], error), ("A-", None))
            self.assertEqual(fetch.await_count, 2)

    async def test_players_are_cached_independently(self):
        with patch.object(leaderboard_service, "fetch_user_stats", AsyncMock(return_value=(STATS, None))) as fetch:
            await leaderboard_service.get_squad_tier(1, "tok-1", object())
            await leaderboard_service.get_squad_tier(2, "tok-2", object())
            self.assertEqual(fetch.await_count, 2)


RANKED = {"tier": "A-", "rating": 72, "region": "eu"}
QUEUE_SUMMARY = {
    "matches": 12,
    "total_duration_ms": 36_000_000,
    "all_time_avg_damage": 1500.0,
    "monthly_avg_damage": 1620.0,
    "all_time_avg_impact": 74.2,
    "monthly_avg_impact": 78.6,
}
NO_QUEUES = {
    "matches": 0,
    "total_duration_ms": 0,
    "all_time_avg_damage": None,
    "monthly_avg_damage": None,
    "all_time_avg_impact": None,
    "monthly_avg_impact": None,
}


class TierFormattingTests(unittest.TestCase):
    def test_compare_cell_text_and_rating_for_every_state(self):
        self.assertEqual(format_squad_tier(RANKED, verified=True), ("A- · 72", 72))
        self.assertEqual(format_squad_tier(None, verified=True), ("Unranked", None))
        self.assertEqual(format_squad_tier({"unavailable": True}, verified=True), ("N/A", None))
        self.assertEqual(format_squad_tier(RANKED, verified=False), ("-", None))

    def test_profile_field_text(self):
        field = leaderboard_service.format_squad_tier_field
        self.assertEqual(field(RANKED, None), "**A-** · rating 72 · EU")
        self.assertEqual(field({**RANKED, "region": None}, None), "**A-** · rating 72")
        self.assertEqual(field(None, None), "Unranked")
        self.assertEqual(field(None, "Please run /verify again."), "Please run /verify again.")


class BuildProfilePayloadTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.target = SimpleNamespace(id=7, display_name="Awang")

    def _patches(self, summary, tier=RANKED, tier_error=None):
        return (
            patch.object(leaderboard_service, "get_player_queue_stats_summary", return_value=summary),
            patch.object(leaderboard_service, "get_squad_tier", AsyncMock(return_value=(tier, tier_error))),
        )

    async def test_verified_player_with_queues_gets_the_tier_below_the_queue_stats(self):
        summary_patch, tier_patch = self._patches(QUEUE_SUMMARY)
        with summary_patch, tier_patch as get_tier:
            embed, message = await leaderboard_service.build_profile_payload(self.target, "tok")

        self.assertIsNone(message)
        month = datetime.now(timezone.utc).strftime("%B")
        self.assertEqual(
            [f.name for f in embed.fields],
            [
                "4v4 Play Time",
                "All-Time Avg Damage",
                f"{month} Avg Damage",
                "\u200b",
                "All-Time Avg Impact",
                f"{month} Avg Impact",
                "\u200b",
                "survev.de Squad Tier",
            ],
        )
        self.assertFalse(embed.fields[0].inline)
        self.assertEqual(embed.fields[4].value, "74")
        self.assertEqual(embed.fields[5].value, "79")
        self.assertEqual(embed.fields[-1].value, "**A-** · rating 72 · EU")
        self.assertFalse(embed.fields[-1].inline)
        self.assertIn("survev.de", embed.footer.text)
        get_tier.assert_awaited_once_with(7, "tok")

    async def test_verified_player_without_queues_still_sees_the_tier(self):
        summary_patch, tier_patch = self._patches(NO_QUEUES)
        with summary_patch, tier_patch:
            embed, message = await leaderboard_service.build_profile_payload(self.target, "tok")

        self.assertIsNone(message)
        self.assertEqual([f.name for f in embed.fields], ["survev.de Squad Tier"])
        self.assertEqual(embed.description, "No cached NeatQueue results yet.")

    async def test_unranked_player_without_queues_gets_the_usual_message(self):
        summary_patch, tier_patch = self._patches(NO_QUEUES, tier=None)
        with summary_patch, tier_patch:
            embed, message = await leaderboard_service.build_profile_payload(self.target, "tok")

        self.assertIsNone(embed)
        self.assertEqual(message, "No cached NeatQueue results found for Awang yet.")

    async def test_unverified_player_never_triggers_a_tier_lookup(self):
        summary_patch, tier_patch = self._patches(QUEUE_SUMMARY)
        with summary_patch, tier_patch as get_tier:
            embed, _ = await leaderboard_service.build_profile_payload(self.target, None)

        get_tier.assert_not_awaited()
        self.assertEqual(len(embed.fields), 7)
        self.assertEqual(embed.footer.text, "Stats from NeatQueue 4v4s only.")

    async def test_lookup_errors_are_shown_instead_of_hiding_the_queue_stats(self):
        summary_patch, tier_patch = self._patches(QUEUE_SUMMARY, tier=None, tier_error="survev.de is rate limiting the bot right now.")
        with summary_patch, tier_patch:
            embed, _ = await leaderboard_service.build_profile_payload(self.target, "tok")

        self.assertEqual(len(embed.fields), 8)
        self.assertEqual(embed.fields[-1].value, "survev.de is rate limiting the bot right now.")


    async def test_verified_player_with_missing_impact_shows_fallback_strings(self):
        summary_no_impact = {**QUEUE_SUMMARY, "all_time_avg_impact": None, "monthly_avg_impact": None}
        summary_patch, tier_patch = self._patches(summary_no_impact)
        with summary_patch, tier_patch:
            embed, _ = await leaderboard_service.build_profile_payload(self.target, "tok")

        self.assertEqual(embed.fields[4].value, "N/A")
        self.assertEqual(embed.fields[5].value, "N/A")


class BuildComparePayloadTests(unittest.IsolatedAsyncioTestCase):
    async def _compare(self, tokens, tier_results):
        left, right = SimpleNamespace(id=1, name="Awang"), SimpleNamespace(id=2, name="Bravo")
        stats = {"games": 10, "wins": 2, "kills": 20, "damage": 5000}
        with patch.object(leaderboard_service, "get_user_token", side_effect=tokens.get), \
                patch.object(leaderboard_service, "fetch_player_timeframe_stats", AsyncMock(return_value=stats)), \
                patch.object(leaderboard_service, "fetch_user_inventory", AsyncMock(return_value=({"items": []}, None))), \
                patch.object(leaderboard_service, "get_player_queue_stats_summary", return_value=NO_QUEUES), \
                patch.object(leaderboard_service, "get_squad_tier", AsyncMock(side_effect=tier_results)) as get_tier, \
                patch.object(leaderboard_service, "generate_compare_image", return_value=io.BytesIO(b"png")) as render:
            await leaderboard_service.build_compare_payload(left, right)
        return render.call_args.args, get_tier

    async def test_tiers_reach_the_image_in_left_right_order(self):
        args, get_tier = await self._compare({1: "tok-1", 2: "tok-2"}, [(RANKED, None), (None, None)])

        self.assertEqual(args[-2:], (RANKED, None))
        self.assertEqual(get_tier.await_count, 2)

    async def test_failed_lookups_are_marked_unavailable(self):
        args, _ = await self._compare({1: "tok-1", 2: "tok-2"}, [(None, "boom"), (RANKED, None)])

        self.assertEqual(args[-2:], ({"unavailable": True}, RANKED))

    async def test_members_without_a_token_are_not_looked_up(self):
        args, get_tier = await self._compare({1: "tok-1"}, [(RANKED, None)])

        self.assertEqual(args[-2:], (RANKED, None))
        get_tier.assert_awaited_once()
        self.assertEqual(get_tier.await_args.args[0], 1)
        self.assertIsNotNone(get_tier.await_args.args[2])  # shares the request's HTTP session


class CompareImageTierRowTests(unittest.TestCase):
    STATS = {"games": 100, "wins": 20, "kills": 250, "damage": 90000}

    def _render(self, left_tier, right_tier, right_stats=STATS):
        buffer = generate_compare_image(
            "Awang", self.STATS, 5400, QUEUE_SUMMARY, "Bravo", right_stats, 3200, NO_QUEUES, left_tier, right_tier)
        return Image.open(io.BytesIO(buffer.getvalue())).convert("RGB")

    def test_every_tier_state_renders_at_the_same_size(self):
        sizes = {
            self._render(left, right).size
            for left, right in [(RANKED, None), (None, {"unavailable": True}), ({**RANKED, "tier": "B-"}, RANKED)]
        }
        self.assertEqual(len(sizes), 1)

    def test_the_tier_changes_the_picture(self):
        ranked, unranked = self._render(RANKED, RANKED), self._render(None, RANKED)
        self.assertIsNotNone(ImageChops.difference(ranked, unranked).getbbox())

    def test_defaults_keep_old_callers_working(self):
        buffer = generate_compare_image("Awang", self.STATS, 5400, QUEUE_SUMMARY, "Bravo", None, None, None)
        self.assertGreater(len(buffer.getvalue()), 0)


if __name__ == "__main__":
    unittest.main()
