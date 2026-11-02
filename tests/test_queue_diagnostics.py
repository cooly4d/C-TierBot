"""Queue lookup diagnostics must explain missing rounds without retaining credentials."""
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp

import neatqueue_client
import queue_stats_service
import survev_client
from test_queue_match_stats import (
    START_MS, END_MS, calculate, fake_world, finished_history, lobby,
    own_games, panel_message, three_game_world,
)


def response(status=200, data=None, error=None):
    context = MagicMock()
    context.__aenter__.return_value = SimpleNamespace(
        status=status, json=AsyncMock(return_value=data, side_effect=error))
    return context


class QueueDiagnosticsTests(unittest.IsolatedAsyncioTestCase):
    async def test_records_partial_and_empty_histories_and_each_selection_decision(self):
        result, error = await calculate(**three_game_world(
            history=finished_history(winner=None),
            panel=panel_message("<@101> <@102>", "<@201> <@202>"),
            tokens={101: "private-token", 102: "empty-token", 201: "other-token"},
            anchors={
                "private-token": own_games(("g1", 10), ("g2", 20), ("stray", 30), ("missing", 40)),
                "other-token": own_games(("g1", 10)),
            },
            boards={"g1": lobby("A"), "g2": lobby("B"), "stray": lobby("A", 10, 10)},
        ))
        self.assertIsNone(error)
        diag = result["diagnostics"]
        self.assertFalse(diag["is_final"])
        self.assertEqual(diag["finality_reason"], "history_winner_is_null")
        self.assertEqual(diag["neatqueue"]["selected_game_number"], 3674)
        self.assertEqual(diag["window"]["from_ms"], START_MS)
        self.assertEqual(diag["window"]["to_ms"], result["match_end_ms"])
        self.assertEqual(diag["window"]["end_source"], "lookup_current_time")
        anchors = {a["discord_id"]: a for a in diag["anchors"]}
        self.assertEqual(anchors[101]["returned_count"], 4)
        self.assertEqual(anchors[102]["matches"], [])
        self.assertEqual(anchors[201]["matches"], [{"guid": "g1", "timestamp_ms": START_MS + 600000}])
        decisions = {d["guid"]: d for d in diag["round_selection"]}
        self.assertEqual(decisions["g1"]["reason"], "intersection_nonempty_histories")
        self.assertEqual(decisions["g2"]["reason"], "not_shared_by_all_nonempty_histories")
        self.assertEqual(decisions["stray"]["reason"], "not_shared_by_all_nonempty_histories")
        self.assertFalse(decisions["stray"]["included"])
        self.assertFalse(decisions["missing"]["scoreboard_requested"])
        self.assertIsNone(decisions["missing"]["scoreboard_available"])
        self.assertEqual(decisions["g2"]["timestamp_ms"], START_MS + 1200000)
        self.assertEqual(diag["selected_guids"], ["g1"])
        self.assertEqual(diag["shared_guids"], ["g1"])
        self.assertNotIn("token", json.dumps(diag))

    async def test_disjoint_histories_still_use_union_with_no_linked_opponents(self):
        world = three_game_world(
            tokens={101: "tok-a", 102: "tok-b"},
            anchors={"tok-a": own_games(("g1", 10)), "tok-b": own_games(("g2", 20))},
            boards={"g1": lobby("A"), "g2": lobby("B")},
        )
        with fake_world(**world):
            with patch.object(neatqueue_client, "get_discord_id_by_slug", side_effect={"alice": 101, "bob": 102}.get):
                result, error = await neatqueue_client.calculate_queue_match_stats("3674", 999)
        self.assertIsNone(error)
        self.assertEqual(result["team_round_wins"], [1, 1])
        diag = result["diagnostics"]
        self.assertEqual(diag["selection_strategy"], "union_fallback_no_shared_games")
        self.assertEqual(diag["selected_guids"], ["g1", "g2"])

    async def test_excluded_rounds_are_not_fetched_and_missing_boards_are_distinguished(self):
        world = three_game_world(anchors={
            "tok-101": own_games(("g1", 10), ("missing", 20), ("excluded", 30)),
            "tok-201": own_games(("g1", 10), ("missing", 20)),
        })
        with fake_world(**world):
            with patch.object(neatqueue_client, "fetch_public_match_data",
                              AsyncMock(side_effect=lambda session, guid, **kwargs: lobby("A") if guid == "g1" else None)) as fetch:
                result, error = await neatqueue_client.calculate_queue_match_stats("3674", 999)
        self.assertIsNone(error)
        self.assertEqual({call.args[1] for call in fetch.call_args_list}, {"g1", "missing"})
        decisions = {d["guid"]: d for d in result["diagnostics"]["round_selection"]}
        self.assertTrue(decisions["missing"]["included"])
        self.assertTrue(decisions["missing"]["scoreboard_requested"])
        self.assertFalse(decisions["missing"]["scoreboard_available"])
        self.assertFalse(decisions["excluded"]["included"])
        self.assertIsNone(decisions["excluded"]["scoreboard_fetch"])

    async def test_active_queue_keeps_panel_start_and_logs_history_start_for_comparison(self):
        panel = panel_message("<@101> <@102>", "<@201> <@202>")
        panel.created_at = datetime.fromtimestamp((START_MS + 1500000) / 1000, timezone.utc)
        with fake_world(**three_game_world(history=finished_history(winner=None), panel=panel)):
            with patch.object(neatqueue_client, "fetch_player_matches_in_window",
                              AsyncMock(return_value=own_games(("g1", 30)))) as fetch:
                result, error = await neatqueue_client.calculate_queue_match_stats("3674", 999)
        self.assertIsNone(error)
        self.assertEqual(result["match_start_ms"], START_MS + 1500000)
        self.assertEqual(result["diagnostics"]["neatqueue"]["history_start_ms"], START_MS)
        self.assertEqual(result["diagnostics"]["window"]["start_source"], "panel_created_at")
        for call in fetch.call_args_list:
            self.assertEqual(call.args[2], START_MS + 1500000)

    async def test_panel_fallback_and_finished_classification_are_logged(self):
        result, _ = await calculate(**three_game_world(
            history=None, panel=panel_message("<@101> <@102>", "<@201> <@202>")))
        self.assertEqual(result["diagnostics"]["window"]["start_source"], "panel_created_at")
        self.assertEqual(result["diagnostics"]["finality_reason"], "no_history_entry")
        result, _ = await calculate(**three_game_world())
        self.assertTrue(result["diagnostics"]["is_final"])
        self.assertEqual(result["diagnostics"]["neatqueue"]["winner"], 0)
        self.assertEqual(result["diagnostics"]["finality_reason"], "history_winner_is_not_null_or_pending")

    async def test_end_boundary_reports_which_source_was_used(self):
        edited_at = datetime.fromtimestamp(END_MS / 1000, timezone.utc)
        channel = SimpleNamespace(fetch_message=AsyncMock(return_value=SimpleNamespace(
            edited_at=edited_at, created_at=edited_at)))
        cases = [
            ({"winner_message": "123", "winner_channel": "456"}, "winner_message_edited_at", END_MS + 5000),
            ({"end_time_ms": END_MS}, "neatqueue_end_time_ms", END_MS),
            ({}, "fallback_duration", START_MS + neatqueue_client.QUEUE_MATCH_FALLBACK_DURATION_MS),
        ]
        with patch.object(neatqueue_client.bot, "get_channel", return_value=channel):
            for match, source, expected in cases:
                with self.subTest(source=source):
                    diag = {}
                    actual = await neatqueue_client.get_match_end_ms(match, START_MS, diagnostics=diag)
                    self.assertEqual(actual, expected)
                    self.assertEqual(diag["end_source"], source)

    async def test_failed_lookup_keeps_diagnostics_in_the_written_command_log(self):
        with fake_world(**three_game_world(tokens={})):
            content, file, error, result = await queue_stats_service.build_queue_stats_payload("3674", 999)
        self.assertIsNone(content)
        self.assertIsNone(file)
        self.assertIsNotNone(error)
        self.assertEqual(result["diagnostics"]["anchors"], [])
        payload = self.write_log(result, error)
        self.assertEqual(payload["status"], "error")
        self.assertIsNone(payload["result_summary"])
        self.assertEqual(payload["diagnostics"]["window"]["from_ms"], START_MS)

    async def test_success_log_contains_summary_window_and_diagnostics(self):
        result, _ = await calculate(**three_game_world())
        payload = self.write_log(result, None)
        self.assertEqual(payload["status"], "ok")
        self.assertTrue(payload["result_summary"]["is_final"])
        self.assertEqual(payload["result_summary"]["match_end_ms"], END_MS)
        self.assertEqual(payload["diagnostics"], result["diagnostics"])
        self.assertEqual(len(payload["individual_games"]), 3)

    def write_log(self, result, error):
        interaction = SimpleNamespace(guild_id=999, channel_id=123, user=None)
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(queue_stats_service, "QUEUE_STATS_LOG_DIR", directory):
                queue_stats_service.write_queue_stats_command_log(interaction, "3674", result, error)
            return json.loads(next(Path(directory).glob("*.json")).read_text(encoding="utf-8"))


class ApiDiagnosticsTests(unittest.IsolatedAsyncioTestCase):
    async def test_partial_history_reports_failed_page_without_logging_response_or_token(self):
        session = SimpleNamespace(post=MagicMock(side_effect=[
            response(data=[{"guid": "g1"}] * 200), response(status=429, data={"secret": "private-token"}),
        ]))
        diag = {}
        matches = await survev_client.fetch_player_matches_in_window(
            session, "private-token", START_MS, END_MS, diagnostics=diag)
        self.assertEqual(len(matches), 200)
        self.assertFalse(diag["pagination_complete"])
        self.assertEqual(diag["pages"][1], {"offset": 200, "limit": 200, "http_status": 429, "error": "http_error"})
        self.assertNotIn("private-token", json.dumps(diag))

    async def test_empty_success_and_invalid_response_are_distinguishable(self):
        for data, complete in (([], True), ({}, False)):
            with self.subTest(data=data):
                diag = {}
                session = SimpleNamespace(post=MagicMock(return_value=response(data=data)))
                self.assertEqual(await survev_client.fetch_player_matches_in_window(
                    session, "private-token", START_MS, END_MS, diagnostics=diag), [])
                self.assertEqual(diag["pagination_complete"], complete)
                self.assertEqual(diag["pages"][0]["http_status"], 200)

    async def test_network_exception_records_type_not_sensitive_message(self):
        session = SimpleNamespace(post=MagicMock(side_effect=aiohttp.ClientError("private-token")))
        diag = {}
        await survev_client.fetch_player_matches_in_window(session, "private-token", START_MS, END_MS, diagnostics=diag)
        self.assertEqual(diag["pages"][0]["error"], "ClientError")
        self.assertNotIn("private-token", json.dumps(diag))

    async def test_scoreboard_status_is_recorded(self):
        for status, data in ((200, lobby("A")), (200, []), (503, None)):
            with self.subTest(status=status, data=data):
                diag = {}
                session = SimpleNamespace(post=MagicMock(return_value=response(status, data)))
                board = await survev_client.fetch_public_match_data(session, "g1", diagnostics=diag)
                self.assertEqual(board, data)
                self.assertEqual(diag["http_status"], status)
