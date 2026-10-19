import json
import sqlite3
import tempfile
import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord

import db
import discord_ui
import leaderboard_bot
from discord_ui import (
    QueueGamesPagerView,
    QueueResultView,
    build_queue_game_message,
    get_queue_result_view,
    parse_game_page_id,
    refresh_queue_game_message,
    show_queue_game_breakdown,
)
from queue_stats_service import (
    QUEUE_SNAPSHOT_VERSION,
    build_queue_game_payload,
    build_queue_snapshot,
    parse_queue_match_id,
    queue_games_unavailable,
    queue_result_filename,
)

sqlite_connect = sqlite3.connect


@contextmanager
def _connect_and_close(*args, **kwargs):
    connection = sqlite_connect(*args, **kwargs)
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def snapshot(match_id="3674", **overrides):
    return {"v": 1, "match_id": match_id, "winning_team_index": 0, "team_round_wins": [2, 1],
            "match_history": [True, False, True], "games": [], **overrides}


class QueueResultStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp_dir.name) / "queue_results.db")
        self.addCleanup(self.temp_dir.cleanup)
        for patcher in (patch.object(db, "DB_PATH", self.db_path),
                        patch.object(db.sqlite3, "connect", side_effect=_connect_and_close)):
            patcher.start()
            self.addCleanup(patcher.stop)
        setup = sqlite_connect(self.db_path)
        try:
            setup.execute(db.QUEUE_RESULTS_SCHEMA)
            setup.commit()
        finally:
            setup.close()

    def _insert_raw(self, guild_id, match_id, text, saved_at):
        raw = sqlite_connect(self.db_path)
        try:
            raw.execute("INSERT INTO queue_results VALUES (?, ?, ?, ?)", (guild_id, match_id, text, saved_at))
            raw.commit()
        finally:
            raw.close()

    def test_a_saved_snapshot_round_trips(self):
        original = snapshot(games=[{"number": 1, "winner": 0, "players": [{"name": "Awang", "impact": None}]}])

        db.save_queue_result(10, "3674", original)

        self.assertEqual(db.get_queue_result(10, "3674"), original)

    def test_match_ids_are_stored_as_text_either_way(self):
        db.save_queue_result(10, 3674, snapshot())

        self.assertEqual(db.get_queue_result(10, "3674")["match_id"], "3674")

    def test_saving_again_replaces_the_earlier_snapshot(self):
        db.save_queue_result(10, "3674", snapshot(team_round_wins=[1, 0]))
        db.save_queue_result(10, "3674", snapshot(team_round_wins=[2, 1]))

        self.assertEqual(db.get_queue_result(10, "3674")["team_round_wins"], [2, 1])

    def test_the_same_queue_number_in_two_servers_is_kept_apart(self):
        db.save_queue_result(10, "3674", snapshot(winning_team_index=0))
        db.save_queue_result(11, "3674", snapshot(winning_team_index=1))

        self.assertEqual(db.get_queue_result(10, "3674")["winning_team_index"], 0)
        self.assertEqual(db.get_queue_result(11, "3674")["winning_team_index"], 1)

    def test_an_unknown_queue_is_none(self):
        self.assertIsNone(db.get_queue_result(10, "9999"))

    def test_unreadable_or_non_object_snapshots_are_none(self):
        now = datetime.now(timezone.utc).isoformat()
        self._insert_raw(10, "bad-json", "{not json", now)
        self._insert_raw(10, "a-list", json.dumps([1, 2, 3]), now)

        self.assertIsNone(db.get_queue_result(10, "bad-json"))
        self.assertIsNone(db.get_queue_result(10, "a-list"))

    def test_saving_prunes_snapshots_past_the_retention_window(self):
        now = datetime.now(timezone.utc)
        self._insert_raw(10, "old", json.dumps(snapshot("old")), (now - timedelta(days=91)).isoformat())
        self._insert_raw(10, "recent", json.dumps(snapshot("recent")), (now - timedelta(days=89)).isoformat())

        db.save_queue_result(10, "new", snapshot("new"))

        self.assertIsNone(db.get_queue_result(10, "old"))
        self.assertIsNotNone(db.get_queue_result(10, "recent"))
        self.assertIsNotNone(db.get_queue_result(10, "new"))

    def test_the_retention_window_can_be_overridden(self):
        self._insert_raw(10, "two-days", json.dumps(snapshot("two-days")),
                         (datetime.now(timezone.utc) - timedelta(days=2)).isoformat())

        db.save_queue_result(10, "new", snapshot("new"), retention_days=1)

        self.assertIsNone(db.get_queue_result(10, "two-days"))

    def test_the_default_retention_is_ninety_days(self):
        self.assertEqual(db.QUEUE_RESULT_RETENTION_DAYS, 90)


def game_row(identity, username, slug, team, kills, damage, impact):
    return {"identity": identity, "username": username, "slug": slug, "rank": 1, "kills": kills,
            "damage_dealt": damage, "impact_score": impact, "display_team_index": team}


class BuildQueueSnapshotTests(unittest.TestCase):
    def match_result(self):
        return {
            "winning_team_index": 0,
            "team_round_wins": [1, 0],
            "match_history": [True],
            "teams": [
                [{"discord_id": 101, "username": "Alice", "slug": "alice", "guest": False,
                  "display_name": "Alice The Great", "stats": {}},
                 {"discord_id": None, "username": "GuestG", "slug": None, "guest": True,
                  "display_name": "GuestG", "stats": {}}],
                [{"discord_id": 201, "username": "Carol", "slug": "carol", "guest": False,
                  "display_name": "Carol", "stats": {}}],
            ],
            "individual_games": [{
                "game_number": 1, "guid": "g1", "winning_display_team_index": 0,
                "players": [
                    game_row("alice", "Alice", "alice", 0, 5, 1000, 80),
                    game_row("guestg", "GuestG", None, 0, 1, 200, None),
                    game_row("carol", "Carol", "carol", 1, 4, 900, 74),
                    game_row("rando1", "Rando1", None, None, 0, 50, 12),
                ],
            }],
        }

    def test_games_list_the_displayed_players_with_their_resolved_names(self):
        result = build_queue_snapshot("3674", self.match_result())

        (game,) = result["games"]
        self.assertEqual((game["number"], game["winner"]), (1, 0))
        self.assertEqual([p["name"] for p in game["players"]], ["Alice The Great", "GuestG", "Carol"])
        self.assertEqual([p["team"] for p in game["players"]], [0, 0, 1])
        self.assertEqual([p["guest"] for p in game["players"]], [False, True, False])
        self.assertEqual([p["discord_id"] for p in game["players"]], [101, None, 201])

    def test_lobby_strangers_are_left_out(self):
        result = build_queue_snapshot("3674", self.match_result())

        self.assertNotIn("Rando1", [p["name"] for p in result["games"][0]["players"]])

    def test_stats_are_carried_and_unscored_impact_stays_none(self):
        players = build_queue_snapshot("3674", self.match_result())["games"][0]["players"]

        self.assertEqual([(p["kills"], p["damage"], p["impact"]) for p in players],
                         [(5, 1000, 80), (1, 200, None), (4, 900, 74)])

    def test_series_context_is_carried_and_the_snapshot_is_json_safe(self):
        result = build_queue_snapshot(3674, self.match_result())

        self.assertEqual((result["v"], result["match_id"]), (QUEUE_SNAPSHOT_VERSION, "3674"))
        self.assertEqual((result["winning_team_index"], result["team_round_wins"], result["match_history"]),
                         (0, [1, 0], [True]))
        self.assertEqual(json.loads(json.dumps(result)), result)

    def test_rows_without_a_matching_team_entry_are_skipped(self):
        match_result = self.match_result()
        match_result["individual_games"][0]["players"].append(game_row("ghost", "Ghost", "ghost", 1, 1, 1, 1))

        names = [p["name"] for p in build_queue_snapshot("3674", match_result)["games"][0]["players"]]

        self.assertNotIn("Ghost", names)

    def test_a_result_without_games_gives_an_empty_snapshot(self):
        result = build_queue_snapshot("3674", {"teams": [], "individual_games": None})

        self.assertEqual(result["games"], [])


def stored_row(team, name, kills, damage, impact, guest=False):
    return {"team": team, "name": name, "discord_id": None, "guest": guest,
            "kills": kills, "damage": damage, "impact": impact}


def stored_snapshot(impact=True):
    def score(value):
        return value if impact else None

    games = [
        {"number": 1, "winner": 0, "players": [
            stored_row(0, "Alice", 5, 1000, score(80)), stored_row(0, "Bob", 3, 600, score(50)),
            stored_row(1, "Carol", 4, 900, score(74)), stored_row(1, "Dave", 2, 500, None, guest=True)]},
        {"number": 2, "winner": 1, "players": [
            stored_row(0, "Alice", 2, 700, score(40)), stored_row(0, "Bob", 1, 300, score(35)),
            stored_row(1, "Carol", 6, 1500, score(91)), stored_row(1, "Dave", 4, 1100, score(66))]},
        {"number": 3, "winner": 0, "players": [
            stored_row(0, "Alice", 7, 2000, score(95)), stored_row(0, "Bob", 4, 1200, score(60)),
            stored_row(1, "Carol", 1, 400, score(30)), stored_row(1, "Dave", 0, 200, score(20))]},
    ]
    return {"v": 1, "match_id": "3674", "winning_team_index": 0, "team_round_wins": [2, 1],
            "match_history": [True, False, True], "games": games}


class ParseQueueMatchIdTests(unittest.TestCase):
    @staticmethod
    def message(filenames=(), content=""):
        return SimpleNamespace(attachments=[SimpleNamespace(filename=name) for name in filenames], content=content)

    def test_the_result_image_filename_is_the_source(self):
        self.assertEqual(parse_queue_match_id(self.message([queue_result_filename("3674")])), "3674")

    def test_other_attachments_are_ignored(self):
        message = self.message(["photo.png", "queue_stats_3674.png"])

        self.assertEqual(parse_queue_match_id(message), "3674")

    def test_the_text_is_the_fallback(self):
        message = self.message(content="*(Catching up)* Queue stats for NeatQueue#3674\n-# Impact is survev.de's")

        self.assertEqual(parse_queue_match_id(message), "3674")

    def test_unrelated_messages_have_no_queue(self):
        self.assertIsNone(parse_queue_match_id(self.message(["photo.png"], "hello")))
        self.assertIsNone(parse_queue_match_id(None))


class QueueResultViewTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        discord_ui._queue_result_views.clear()
        self.addCleanup(discord_ui._queue_result_views.clear)

    async def test_the_default_view_only_has_the_verify_button(self):
        view = QueueResultView()

        self.assertEqual([c.custom_id for c in view.children], ["queue_result_verify"])

    async def test_finished_queues_get_the_breakdown_button_before_the_verify_button(self):
        view = QueueResultView(include_breakdown=True)

        self.assertEqual([c.custom_id for c in view.children], ["queue_result_games", "queue_result_verify"])
        self.assertEqual(view.children[0].label, "Game breakdown")
        self.assertEqual(view.children[0].style, discord.ButtonStyle.primary)
        self.assertEqual(view.children[1].style, discord.ButtonStyle.secondary)

    async def test_both_variants_can_be_registered_as_persistent_views(self):
        self.assertTrue(QueueResultView().is_persistent())
        self.assertTrue(QueueResultView(include_breakdown=True).is_persistent())

    async def test_views_are_shared_per_variant(self):
        self.assertIs(get_queue_result_view(), get_queue_result_view(False))
        self.assertIs(get_queue_result_view(True), get_queue_result_view(include_breakdown=True))
        self.assertIsNot(get_queue_result_view(True), get_queue_result_view(False))


class QueueGamesPagerViewTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def buttons(page, total=3):
        view = QueueGamesPagerView("3674", page, total)
        return [(c.custom_id, c.disabled) for c in view.children]

    async def test_first_page_cannot_go_back(self):
        self.assertEqual(self.buttons(0), [("qgame_prev:3674:0", True), ("qgame_next:3674:1", False)])

    async def test_middle_page_goes_both_ways(self):
        self.assertEqual(self.buttons(1), [("qgame_prev:3674:0", False), ("qgame_next:3674:2", False)])

    async def test_last_page_cannot_go_forward(self):
        self.assertEqual(self.buttons(2), [("qgame_prev:3674:1", False), ("qgame_next:3674:2", True)])

    async def test_the_view_times_out_so_ephemeral_messages_do_not_pile_up(self):
        self.assertEqual(QueueGamesPagerView("3674", 0, 3).timeout, 900)

    def test_page_ids_split_back_into_queue_and_page(self):
        self.assertEqual(parse_game_page_id("qgame_next:3674:2"), ("3674", 2))
        self.assertEqual(parse_game_page_id("qgame_prev:a:b:1"), ("a:b", 1))
        with self.assertRaises(ValueError):
            parse_game_page_id("qgame_next:3674:two")


class BuildQueueGamePayloadTests(unittest.IsolatedAsyncioTestCase):
    async def payload(self, page, snapshot=None, guild_id=10):
        snapshot = stored_snapshot() if snapshot is None else snapshot
        with patch("queue_stats_service.get_queue_result", return_value=snapshot):
            return await build_queue_game_payload(guild_id, "3674", page)

    async def test_returns_the_requested_game_with_its_series_context(self):
        embed, file, page, total, error = await self.payload(2)

        self.assertIsNone(error)
        self.assertEqual((page, total), (2, 3))
        self.assertEqual(embed.title, "Game 3 of 3 · Queue #3674")
        self.assertIn("Team 1 won Game 3", embed.description)
        self.assertIn("Series 2 - 1", embed.description)
        self.assertIn("Top damage: **Alice** (2,000)", embed.description)
        self.assertIn("Top impact: **Alice** (95)", embed.description)
        self.assertEqual(file.filename, "queue_3674_game_3.png")
        self.assertEqual(embed.image.url, "attachment://queue_3674_game_3.png")

    async def test_the_series_score_only_counts_games_up_to_the_selected_one(self):
        first = (await self.payload(0))[0].description
        second = (await self.payload(1))[0].description

        self.assertIn("Series 1 - 0", first)
        self.assertIn("Team 2 won Game 2", second)
        self.assertIn("Series 1 - 1", second)

    async def test_pages_outside_the_range_are_clamped(self):
        self.assertEqual((await self.payload(99))[2], 2)
        self.assertEqual((await self.payload(-5))[2], 0)

    async def test_the_image_has_alt_text_listing_every_player(self):
        _, file, *_ = await self.payload(0)

        self.assertIn("Alice 5 kills, 1,000 damage, impact 80", file.description)
        self.assertIn("Dave 2 kills, 500 damage.", file.description)
        self.assertIn("Team 2:", file.description)
        self.assertLessEqual(len(file.description), 1024)

    async def test_scored_games_carry_the_impact_footer_and_unscored_ones_do_not(self):
        scored = (await self.payload(0))[0]
        unscored = (await self.payload(0, snapshot=stored_snapshot(impact=False)))[0]

        self.assertIn("impact score", scored.footer.text)
        self.assertNotIn("Top impact", unscored.description)
        self.assertIsNone(unscored.footer.text)

    async def test_an_unknown_winner_is_said_so(self):
        snapshot = stored_snapshot()
        snapshot["games"][0]["winner"] = None

        embed = (await self.payload(0, snapshot=snapshot))[0]

        self.assertIn("Winner of Game 1 unknown", embed.description)

    async def test_nothing_saved_gives_a_clear_message_with_the_way_out(self):
        with patch("queue_stats_service.get_queue_result", return_value=None):
            result = await build_queue_game_payload(10, "3674", 0)

        self.assertEqual(result, (None, None, 0, 0, queue_games_unavailable("3674")))

    async def test_a_snapshot_without_games_counts_as_nothing_saved(self):
        result = await self.payload(0, snapshot={"v": 1, "games": []})

        self.assertEqual(result, (None, None, 0, 0, queue_games_unavailable("3674")))

    async def test_direct_messages_have_no_server_to_look_up(self):
        with patch("queue_stats_service.get_queue_result") as lookup:
            result = await build_queue_game_payload(None, "3674", 0)

        lookup.assert_not_called()
        self.assertEqual(result[-1], queue_games_unavailable("3674"))

    async def test_the_unavailable_message_names_the_command_and_the_retention(self):
        text = queue_games_unavailable("3674")

        self.assertIn("`/queue_stats 3674`", text)
        self.assertIn(f"{db.QUEUE_RESULT_RETENTION_DAYS} days", text)
        self.assertIn("`/queue_stats` with the queue number", queue_games_unavailable(None))


class BuildQueueGameMessageTests(unittest.IsolatedAsyncioTestCase):
    async def test_multi_game_queues_get_the_pager(self):
        with patch("queue_stats_service.get_queue_result", return_value=stored_snapshot()):
            embed, file, view, error = await build_queue_game_message(10, "3674", 1)

        self.assertIsNone(error)
        self.assertEqual([c.custom_id for c in view.children], ["qgame_prev:3674:0", "qgame_next:3674:2"])

    async def test_single_game_queues_have_no_pager(self):
        single = stored_snapshot()
        single["games"] = single["games"][:1]
        with patch("queue_stats_service.get_queue_result", return_value=single):
            embed, file, view, error = await build_queue_game_message(10, "3674", 0)

        self.assertIsNone(view)
        self.assertIsNotNone(embed)

    async def test_errors_come_back_without_a_view(self):
        with patch("queue_stats_service.get_queue_result", return_value=None):
            result = await build_queue_game_message(10, "3674", 0)

        self.assertEqual(result, (None, None, None, queue_games_unavailable("3674")))


class BreakdownHandlerTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def interaction(filenames=("queue_stats_3674.png",)):
        interaction = MagicMock()
        interaction.guild_id = 10
        interaction.message = SimpleNamespace(attachments=[SimpleNamespace(filename=n) for n in filenames], content="")
        interaction.response.defer = AsyncMock()
        interaction.followup.send = AsyncMock()
        interaction.edit_original_response = AsyncMock()
        return interaction

    async def test_the_button_opens_the_first_game_as_an_ephemeral_message(self):
        interaction, embed, file, view = self.interaction(), object(), object(), object()
        with patch("discord_ui.build_queue_game_message", new=AsyncMock(return_value=(embed, file, view, None))) as build:
            await show_queue_game_breakdown(interaction)

        interaction.response.defer.assert_awaited_once_with(ephemeral=True)
        build.assert_awaited_once_with(10, "3674", 0)
        interaction.followup.send.assert_awaited_once_with(embed=embed, file=file, ephemeral=True, view=view)

    async def test_a_single_game_queue_is_sent_without_a_view(self):
        interaction, embed, file = self.interaction(), object(), object()
        with patch("discord_ui.build_queue_game_message", new=AsyncMock(return_value=(embed, file, None, None))):
            await show_queue_game_breakdown(interaction)

        interaction.followup.send.assert_awaited_once_with(embed=embed, file=file, ephemeral=True)

    async def test_a_message_that_is_not_a_queue_result_gets_the_generic_notice(self):
        interaction = self.interaction(filenames=())
        with patch("discord_ui.build_queue_game_message", new=AsyncMock()) as build:
            await show_queue_game_breakdown(interaction)

        build.assert_not_awaited()
        interaction.followup.send.assert_awaited_once_with(queue_games_unavailable(None), ephemeral=True)

    async def test_missing_data_is_reported_privately(self):
        interaction = self.interaction()
        with patch("discord_ui.build_queue_game_message", new=AsyncMock(return_value=(None, None, None, "gone"))):
            await show_queue_game_breakdown(interaction)

        interaction.followup.send.assert_awaited_once_with("gone", ephemeral=True)

    async def test_paging_swaps_the_ephemeral_message_through_the_interaction(self):
        interaction, embed, file, view = self.interaction(), object(), object(), object()
        with patch("discord_ui.build_queue_game_message", new=AsyncMock(return_value=(embed, file, view, None))) as build:
            await refresh_queue_game_message(interaction, "3674", 2)

        interaction.response.defer.assert_awaited_once_with()
        build.assert_awaited_once_with(10, "3674", 2)
        interaction.edit_original_response.assert_awaited_once_with(embed=embed, attachments=[file], view=view)
        interaction.followup.send.assert_not_awaited()

    async def test_paging_reports_missing_data_instead_of_editing(self):
        interaction = self.interaction()
        with patch("discord_ui.build_queue_game_message", new=AsyncMock(return_value=(None, None, None, "gone"))):
            await refresh_queue_game_message(interaction, "3674", 2)

        interaction.edit_original_response.assert_not_awaited()
        interaction.followup.send.assert_awaited_once_with("gone", ephemeral=True)


class BreakdownRoutingTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def click(custom_id):
        interaction = MagicMock()
        interaction.type = discord.InteractionType.component
        interaction.data = {"custom_id": custom_id}
        return interaction

    async def test_the_breakdown_button_opens_the_breakdown(self):
        interaction = self.click("queue_result_games")
        with patch("leaderboard_bot.show_queue_game_breakdown", new=AsyncMock()) as show:
            await leaderboard_bot.log_interaction(interaction)

        show.assert_awaited_once_with(interaction)

    async def test_pager_buttons_route_with_the_queue_and_target_page(self):
        for custom_id, expected in {"qgame_next:3674:2": ("3674", 2), "qgame_prev:3674:0": ("3674", 0)}.items():
            interaction = self.click(custom_id)
            with self.subTest(custom_id=custom_id), \
                    patch("leaderboard_bot.refresh_queue_game_message", new=AsyncMock()) as refresh:
                await leaderboard_bot.log_interaction(interaction)
                refresh.assert_awaited_once_with(interaction, *expected)

    async def test_the_verify_button_still_routes_to_verification(self):
        interaction = self.click("queue_result_verify")
        interaction.response.defer = AsyncMock()
        with patch("leaderboard_bot.run_survev_verification", new=AsyncMock()) as verify:
            await leaderboard_bot.log_interaction(interaction)

        verify.assert_awaited_once()


class QueueResultSendSiteTests(unittest.IsolatedAsyncioTestCase):
    """Every place a queue result is posted attaches the Game breakdown button only for finished queues."""

    def setUp(self):
        discord_ui._queue_result_views.clear()
        self.addCleanup(discord_ui._queue_result_views.clear)

    @staticmethod
    def button_ids(view):
        return [c.custom_id for c in view.children]

    @staticmethod
    def payload(is_final):
        return AsyncMock(return_value=("content", "file", None, {"is_final": is_final}))

    async def test_queue_stats_command(self):
        for is_final, expected in ((True, ["queue_result_games", "queue_result_verify"]), (False, ["queue_result_verify"])):
            interaction = MagicMock()
            interaction.guild_id = 10
            interaction.response.defer = AsyncMock()
            interaction.followup.send = AsyncMock()
            with self.subTest(is_final=is_final), \
                    patch("leaderboard_bot.build_queue_stats_payload", new=self.payload(is_final)), \
                    patch("leaderboard_bot.write_queue_stats_command_log"):
                await leaderboard_bot.queue_stats.callback(interaction, "3674")
                self.assertEqual(self.button_ids(interaction.followup.send.await_args.kwargs["view"]), expected)

    async def test_automatic_post_for_a_finished_queue(self):
        message = MagicMock()
        message.created_at = datetime.now(timezone.utc)
        message.guild.id = 10
        message.reply = AsyncMock()
        with patch("leaderboard_bot.try_mark_match_processed", return_value=True), \
                patch("leaderboard_bot.QUEUE_RESULT_FETCH_DELAY_SECONDS", 0), \
                patch("leaderboard_bot.build_queue_stats_payload", new=self.payload(True)), \
                patch("leaderboard_bot.update_guild_last_updated"):
            await leaderboard_bot.post_queue_result(message, "3674")

        self.assertEqual(self.button_ids(message.reply.await_args.kwargs["view"]),
                         ["queue_result_games", "queue_result_verify"])

    async def test_automatic_post_for_an_unfinished_queue(self):
        message = MagicMock()
        message.created_at = datetime.now(timezone.utc)
        message.guild.id = 10
        message.reply = AsyncMock()
        with patch("leaderboard_bot.try_mark_match_processed", return_value=True), \
                patch("leaderboard_bot.QUEUE_RESULT_FETCH_DELAY_SECONDS", 0), \
                patch("leaderboard_bot.build_queue_stats_payload", new=self.payload(False)), \
                patch("leaderboard_bot.update_guild_last_updated"):
            await leaderboard_bot.post_queue_result(message, "3674")

        self.assertEqual(self.button_ids(message.reply.await_args.kwargs["view"]), ["queue_result_verify"])

    async def test_catch_up_posts_after_a_restart(self):
        channel = MagicMock()
        channel.send = AsyncMock()
        matches = [{"game_number": 3674, "winner_channel": "55"}]
        with patch("leaderboard_bot.get_all_guild_settings", return_value=[(10, 55, "2026-10-01T00:00:00+00:00")]), \
                patch("leaderboard_bot.fetch_neatqueue_matches_since", new=AsyncMock(return_value=(matches, None))), \
                patch("leaderboard_bot.try_mark_match_processed", return_value=True), \
                patch("leaderboard_bot.build_queue_stats_payload", new=self.payload(True)), \
                patch("leaderboard_bot.update_guild_last_updated"), \
                patch.object(leaderboard_bot.bot, "get_channel", return_value=channel):
            await leaderboard_bot.backfill_missed_queue_results()

        sent = channel.send.await_args.kwargs
        self.assertEqual(sent["content"], "*(Catching up)* content")
        self.assertEqual(self.button_ids(sent["view"]), ["queue_result_games", "queue_result_verify"])


if __name__ == "__main__":
    unittest.main()
