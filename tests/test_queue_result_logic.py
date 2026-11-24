import importlib.util
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "neatqueue_client.py"
SPEC = importlib.util.spec_from_file_location("neatqueue_client", MODULE_PATH)
neatqueue_client = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(neatqueue_client)


import io
import json
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import db
from queue_stats_service import build_hall_of_fame_embed, build_queue_stats_payload, check_queue_hall_of_fame_records


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


def game(number, *players):
    """One game of a queue snapshot: players as (name, damage, discord_id)."""
    return {"number": number, "winner": 0, "players": [
        {"team": 0, "name": name, "discord_id": discord_id, "guest": discord_id is None, "kills": 0, "damage": damage}
        for name, damage, discord_id in players
    ]}


class SingleGameDamageRecordTests(unittest.TestCase):
    """Most Damage in a Single Game: the best single game anyone played, from the queue snapshot's per-game rows."""

    def _records(self, mock_try_set):
        return {call.args[0]: call for call in mock_try_set.call_args_list}

    @patch("queue_stats_service.try_set_hall_of_fame_record", return_value=True)
    def test_best_single_game_sets_the_record_and_names_its_game(self, mock_try_set):
        teams = [[self._entry("Regular", 2000, 5)]]
        games = [game(1, ("Regular", 400, 1), ("Sub", 300, None)), game(2, ("Regular", 500, 1), ("Sub", 996, None))]

        lines = check_queue_hall_of_fame_records(teams, "3785", 999, total_games_played=5, snapshot_games=games)

        call = self._records(mock_try_set)["most_damage_game"]
        self.assertEqual(call.args[1:6], (996.0, None, "Sub", "3785", 999))
        self.assertEqual(call.kwargs["game_number"], 2)
        self.assertIn("🏆 New Hall of Fame record! **Most Damage in a Single Game**: 996 by Sub (Game 2)", lines)

    @patch("queue_stats_service.try_set_hall_of_fame_record", return_value=True)
    def test_a_sub_who_played_one_game_can_hold_it(self, mock_try_set):
        # The per-player games floor guards queue totals and averages, not a single game.
        teams = [[self._entry("Regular", 2000, 5), self._entry("Sub", 900, 1)]]
        games = [game(5, ("Regular", 400, 1), ("Sub", 900, 2))]

        check_queue_hall_of_fame_records(teams, "1", 999, total_games_played=5, snapshot_games=games)

        self.assertEqual(self._records(mock_try_set)["most_damage_game"].args[3], "Sub")

    @patch("queue_stats_service.try_set_hall_of_fame_record", return_value=True)
    def test_short_queues_and_queues_without_games_set_nothing(self, mock_try_set):
        games = [game(1, ("Regular", 900, 1))]
        check_queue_hall_of_fame_records([[self._entry("Regular", 900, 3)]], "1", 999, total_games_played=3, snapshot_games=games)
        check_queue_hall_of_fame_records([[self._entry("Regular", 900, 5)]], "2", 999, total_games_played=5, snapshot_games=None)

        self.assertNotIn("most_damage_game", self._records(mock_try_set))

    @patch("queue_stats_service.try_set_hall_of_fame_record", return_value=False)
    def test_no_announcement_when_the_record_stands(self, _mock_try_set):
        lines = check_queue_hall_of_fame_records(
            [[self._entry("Regular", 2000, 5)]], "1", 999, total_games_played=5, snapshot_games=[game(1, ("Regular", 500, 1))])

        self.assertEqual(lines, [])

    def test_first_of_a_tie_wins(self):
        damage, row, number = db.best_game_damage([game(1, ("A", 700, 1)), game(2, ("B", 700, 2))])

        self.assertEqual((damage, row["name"], number), (700, "A", 1))
        self.assertIsNone(db.best_game_damage([]))

    def test_seed_floor_matches_the_live_floor(self):
        from bot_config import MIN_GAMES_FOR_HALL_OF_FAME_UPDATE
        self.assertEqual(db.SINGLE_GAME_RECORD_SEED_MIN_GAMES, MIN_GAMES_FOR_HALL_OF_FAME_UPDATE)

    def _entry(self, name, damage, games):
        return {"display_name": name, "username": name, "discord_id": 1,
                "stats": {"damage": damage, "games": games, "kills": 0}}


class ClosingConnection(sqlite3.Connection):
    """db.py's `with sqlite3.connect(...)` never closes, and Windows can't delete an open file."""

    def __exit__(self, *exc):
        try:
            return super().__exit__(*exc)
        finally:
            self.close()


def days_ago(days):
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


OLD_HALL_OF_FAME_SCHEMA = (
    "CREATE TABLE hall_of_fame (record_type TEXT PRIMARY KEY, value REAL NOT NULL, discord_id INTEGER, "
    "display_name TEXT, match_id TEXT NOT NULL, guild_id INTEGER NOT NULL, achieved_at TEXT NOT NULL, "
    "duration_ms INTEGER)"
)


class SingleGameDamageSeedTests(unittest.IsolatedAsyncioTestCase):
    """The game_number migration seeds the record once, in the same transaction, from the saved snapshots; the
    record is shown in /hall_of_fame."""

    def setUp(self):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        self.db_path = str(Path(temp_dir.name) / "hof.db")
        connect = sqlite3.connect
        self.connect = lambda *args, **kwargs: connect(*args, factory=ClosingConnection, **kwargs)
        for patcher in (patch.object(db, "DB_PATH", self.db_path),
                        patch.object(db.sqlite3, "connect", side_effect=self.connect)):
            patcher.start()
            self.addCleanup(patcher.stop)
        with self.connect(self.db_path) as c:
            c.execute(db.QUEUE_RESULTS_SCHEMA)
            c.execute(OLD_HALL_OF_FAME_SCHEMA)  # a database from before the record existed

    def _save(self, guild_id, match_id, games, saved_at=None):
        with self.connect(self.db_path) as c:
            c.execute("INSERT INTO queue_results VALUES (?, ?, ?, ?)",
                      (guild_id, match_id, json.dumps({"games": games}), saved_at or days_ago(1)))

    def _columns(self):
        with self.connect(self.db_path) as c:
            return {row[1] for row in c.execute("PRAGMA table_info(hall_of_fame)")}

    def test_migration_seeds_the_best_game_of_a_full_queue(self):
        four = [game(n, ("Filler", 100, 1)) for n in range(1, 4)]
        self._save(7, "3785", four + [game(4, ("babiziank", 996, None))], saved_at=days_ago(20))
        self._save(7, "4765", four + [game(4, ("Tenno", 985, 42))])
        self._save(7, "4999", four + [game(4, ("Late tie", 996, 5))])  # saved later: the earlier 996 keeps it
        self._save(8, "9000", [game(1, ("Short", 2000, 3))])  # three games short of a record
        self._save(8, "old", four + [game(4, ("Expired", 3000, 3))], saved_at=days_ago(200))
        self._save(8, "bad", [None, {}, {"players": [None, {"damage": "lots"}]}, {"players": "x"}])

        self.assertTrue(db.migrate_hall_of_fame_game_number(4))

        record = db.get_hall_of_fame_record("most_damage_game")
        self.assertEqual(
            (record["value"], record["display_name"], record["discord_id"], record["match_id"], record["guild_id"],
             record["game_number"]),
            (996.0, "babiziank", None, "3785", 7, 4),
        )
        self.assertLess(record["achieved_at"], days_ago(19))  # when the game was saved, not when it was seeded

    def test_zero_damage_queues_and_empty_history_seed_nothing(self):
        self._save(7, "1", [game(n, ("Nobody", 0, 1)) for n in range(1, 5)])

        self.assertTrue(db.migrate_hall_of_fame_game_number(4))
        self.assertIsNone(db.get_hall_of_fame_record("most_damage_game"))

    def test_failed_seed_keeps_nothing_then_retries_and_a_reset_stays_empty(self):
        self._save(7, "3785", [game(n, ("babiziank", 996, None)) for n in range(1, 5)])
        with patch.object(db, "_seed_single_game_damage_record", side_effect=RuntimeError("stopped")):
            with self.assertRaises(RuntimeError):
                db.migrate_hall_of_fame_game_number(4)
        self.assertNotIn("game_number", self._columns())  # rolled back with the seed

        self.assertTrue(db.migrate_hall_of_fame_game_number(4))  # the next start does both
        self.assertEqual(db.get_hall_of_fame_record("most_damage_game")["value"], 996.0)

        db.clear_hall_of_fame_records()
        self.assertFalse(db.migrate_hall_of_fame_game_number(4))  # later starts never reseed
        self.assertIsNone(db.get_hall_of_fame_record("most_damage_game"))

    def test_seed_never_lowers_an_existing_record(self):
        with self.connect(self.db_path) as c:
            c.execute("INSERT INTO hall_of_fame VALUES ('most_damage_game', 1200, 5, 'Hex.', '4520', 7, 'x', NULL)")
        self._save(7, "3785", [game(n, ("babiziank", 996, None)) for n in range(1, 5)])

        self.assertTrue(db.migrate_hall_of_fame_game_number(4))
        self.assertEqual(db.get_hall_of_fame_record("most_damage_game")["value"], 1200.0)

    async def test_hall_of_fame_embed_shows_the_game(self):
        db.migrate_hall_of_fame_game_number(4)
        db.try_set_hall_of_fame_record("most_damage_game", 996.0, None, "babiziank", "3785", 7, game_number=1)
        db.try_set_hall_of_fame_record("most_avg_damage", 451.75, 5, "Hex.", "4624", 7)

        embed = await build_hall_of_fame_embed()

        fields = {field.name: field.value for field in embed.fields}
        self.assertEqual(list(fields)[:3], ["Most Kills in a Queue", "Most Avg Damage in a Queue", "Most Damage in a Single Game"])
        self.assertEqual(fields["Most Damage in a Single Game"], "**996** — babiziank (Queue #3785, Game 1)")
        self.assertEqual(fields["Most Avg Damage in a Queue"], "**452** — <@5> (Queue #4624)")


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
            patch("queue_stats_service.generate_queue_result_image", return_value=io.BytesIO(b"png")) as render, \
            patch("queue_stats_service.cache_player_queue_stats"), \
            patch("queue_stats_service.save_queue_result") as save, \
            patch("queue_stats_service.check_queue_hall_of_fame_records", return_value=list(record_lines)) as hof:
        content, _file, error, _result = await build_queue_stats_payload("3674", 999)
    assert error is None, error
    return SimpleNamespace(content=content, hof=hof, save=save, render=render)


class QueueImageFinalityTests(unittest.IsolatedAsyncioTestCase):
    async def test_payload_passes_finality_to_image_renderer(self):
        for finality in (True, False, None):
            with self.subTest(finality=finality):
                built = await build_payload(is_final=finality)
                self.assertEqual(built.render.call_args.kwargs["is_final"], bool(finality))

    async def test_payload_header_shows_in_progress_for_unfinished_queues(self):
        for finality, header in ((True, "Queue stats for NeatQueue#3674"),
                                 (False, "NeatQueue#3674 - IN PROGRESS"),
                                 (None, "NeatQueue#3674 - IN PROGRESS")):
            with self.subTest(finality=finality):
                built = await build_payload(is_final=finality)
                self.assertEqual(built.content.splitlines()[0], header)


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

    async def test_records_read_the_same_per_game_rows_the_breakdown_saves(self):
        teams = [[{"discord_id": 1, "username": "Awang", "slug": "awang",
                   "stats": {"games": 5, "kills": 30, "damage": 9000}}]]
        games = [{"game_number": 1, "winning_display_team_index": 0, "players": [
            {"display_team_index": 0, "identity": "awang", "kills": 3, "damage_dealt": 996}]}]
        built = await build_payload(teams=teams, is_final=True, individual_games=games)

        checked = built.hof.call_args.kwargs["snapshot_games"]
        self.assertEqual(checked, built.save.call_args.args[2]["games"])
        self.assertEqual(checked[0]["players"][0]["damage"], 996)

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


class RefactoredHelpersTests(unittest.IsolatedAsyncioTestCase):
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

        view = ShopPaginationView(42, 0, 2, "weekly")
        buttons = {c.label: c for c in view.children}
        self.assertEqual(buttons["Daily"].style, discord.ButtonStyle.secondary)
        self.assertEqual(buttons["Weekly"].style, discord.ButtonStyle.primary)
        self.assertEqual(buttons["All"].style, discord.ButtonStyle.secondary)
        self.assertTrue(buttons["◀ Previous"].disabled)
        self.assertFalse(buttons["Next ▶"].disabled)

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

