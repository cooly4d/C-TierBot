"""Characterization tests for neatqueue_client.calculate_queue_match_stats.

The survev.de/NeatQueue lookups are faked, so these pin down how the function turns a queue's games into
teams, scores and per-game rows without touching the network.
"""
import contextlib
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord

import neatqueue_client

START_MS = int(datetime(2026, 10, 1, 18, 0, tzinfo=timezone.utc).timestamp() * 1000)
END_MS = START_MS + 3_600_000
DISCORD_IDS = {"alice": 101, "bob": 102, "carol": 201, "dave": 202}
DEFAULT_TOKENS = {101: "tok-101", 201: "tok-201"}


def player(slug, name, team_id, rank, kills, damage, **extra):
    return {
        "slug": slug, "username": name, "team_id": team_id,
        "rank": rank, "kills": kills, "damage_dealt": damage, **extra,
    }


def lobby(winner, a_team=10, b_team=20, extras=False, impact=None):
    """One game's public scoreboard: team A (alice, bob, an unlinked guest) vs team B (carol, dave).
    `impact` maps a username to that game's impact_score (None = unrated); omit it for maps that don't score impact."""
    a_rank, b_rank = (1, 2) if winner == "A" else (2, 1)
    rows = [
        player("alice", "Alice", a_team, a_rank, 5, 1000),
        player("bob", "Bob", a_team, a_rank, 3, 600),
        player(None, "GuestG", a_team, a_rank, 1, 200),
        player("carol", "Carol", b_team, b_rank, 4, 900),
        player("dave", "Dave", b_team, b_rank, 2, 500),
    ]
    if extras:
        rows += [player(None, "Rando1", 30, 3, 0, 50), player(None, "Rando2", 30, 3, 1, 70)]
    if impact is not None:
        for row in rows:
            row["impact_score"] = impact.get(row["username"])
    return rows


def finished_history(roster=((101, 102), (201, 202)), **overrides):
    entry = {
        "game_number": 3674,
        "winner": 0,
        "start_time": "2026-10-01T18:00:00+00:00",
        "teams": [[{"id": str(pid)} for pid in team] for team in roster],
    }
    entry.update(overrides)
    return [entry]


def own_games(*guid_and_minute):
    return [{"guid": guid, "end_time": f"2026-10-01T18:{minute:02d}:00.000Z"} for guid, minute in guid_and_minute]


def panel_message(*team_mentions):
    embed = discord.Embed(title="Results for Queue#3674")
    for index, mentions in enumerate(team_mentions, start=1):
        embed.add_field(name=f"Team {index}", value=mentions)
    return SimpleNamespace(embeds=[embed], created_at=datetime(2026, 10, 1, 18, 0, tzinfo=timezone.utc))


@contextlib.contextmanager
def fake_world(*, history=None, history_error=None, anchors=None, boards=None, tokens=None, panel=None):
    anchors = anchors or {}
    boards = boards or {}
    tokens = DEFAULT_TOKENS if tokens is None else tokens

    async def fake_own_matches(_session, token, _start_ms, _end_ms):
        return anchors.get(token, [])

    async def fake_board(_session, guid):
        return boards.get(guid)

    with contextlib.ExitStack() as stack:
        stack.enter_context(patch.object(
            neatqueue_client, "fetch_neatqueue_history", AsyncMock(return_value=(history, history_error))))
        stack.enter_context(patch.object(neatqueue_client, "get_match_end_ms", AsyncMock(return_value=END_MS)))
        stack.enter_context(patch.object(neatqueue_client, "get_user_token", side_effect=tokens.get))
        stack.enter_context(patch.object(
            neatqueue_client, "fetch_player_matches_in_window", side_effect=fake_own_matches))
        stack.enter_context(patch.object(neatqueue_client, "fetch_public_match_data", side_effect=fake_board))
        stack.enter_context(patch.object(neatqueue_client, "get_discord_id_by_slug", side_effect=DISCORD_IDS.get))
        stack.enter_context(patch.object(neatqueue_client, "find_queue_panel_message", AsyncMock(return_value=panel)))
        yield


async def calculate(**world):
    with fake_world(**world):
        return await neatqueue_client.calculate_queue_match_stats("3674", 999)


def three_game_world(**overrides):
    world = {
        "history": finished_history(),
        "anchors": {"tok-101": own_games(("g1", 10), ("g2", 20), ("g3", 30))},
        # survev.de hands out a fresh team_id per game, so the ids differ between games on purpose.
        "boards": {"g1": lobby("A", 10, 20), "g2": lobby("B", 4, 9), "g3": lobby("A", 1, 2)},
    }
    world.update(overrides)
    return world


class FinishedQueueTests(unittest.IsolatedAsyncioTestCase):
    async def test_players_are_grouped_by_survev_team_and_rounds_are_scored(self):
        result, error = await calculate(**three_game_world())

        self.assertIsNone(error)
        self.assertTrue(result["is_final"])
        self.assertEqual(result["total_games_played"], 3)
        self.assertEqual(result["team_round_wins"], [2, 1])
        self.assertEqual(result["winning_team_index"], 0)
        self.assertEqual(result["match_history"], [True, False, True])
        self.assertEqual((result["match_start_ms"], result["match_end_ms"], result["duration_ms"]),
                         (START_MS, END_MS, 3_600_000))

        team_one, team_two = result["teams"]
        self.assertEqual([e["username"] for e in team_one], ["Alice", "Bob", "GuestG"])
        self.assertEqual([e["username"] for e in team_two], ["Carol", "Dave"])
        self.assertEqual([e["discord_id"] for e in team_one], [101, 102, None])
        self.assertEqual([e["guest"] for e in team_one], [False, False, True])
        self.assertEqual(team_one[0]["stats"], {"games": 3, "wins": 2, "kills": 15, "damage": 3000, "impact": None})
        self.assertEqual(team_two[0]["stats"], {"games": 3, "wins": 1, "kills": 12, "damage": 2700, "impact": None})

    async def test_individual_games_list_every_player_ordered_by_placement_then_damage(self):
        result, _ = await calculate(**three_game_world())

        games = result["individual_games"]
        self.assertEqual([g["game_number"] for g in games], [1, 2, 3])
        self.assertEqual([g["guid"] for g in games], ["g1", "g2", "g3"])
        self.assertEqual([g["winning_display_team_index"] for g in games], [0, 1, 0])
        self.assertEqual([p["username"] for p in games[0]["players"]],
                         ["Alice", "Bob", "GuestG", "Carol", "Dave"])
        self.assertEqual([p["username"] for p in games[1]["players"]],
                         ["Carol", "Dave", "Alice", "Bob", "GuestG"])

    async def test_display_team_order_follows_the_neatqueue_roster(self):
        world = three_game_world(history=finished_history(roster=((201, 202), (101, 102))))
        result, error = await calculate(**world)

        self.assertIsNone(error)
        self.assertEqual([e["username"] for e in result["teams"][0]], ["Carol", "Dave"])
        self.assertEqual(result["team_round_wins"], [1, 2])
        self.assertEqual(result["winning_team_index"], 1)
        self.assertEqual(result["match_history"], [True, False, True])
        self.assertEqual([g["winning_display_team_index"] for g in result["individual_games"]], [1, 0, 1])

    async def test_games_are_ordered_by_the_anchors_own_timestamps(self):
        world = three_game_world(
            anchors={"tok-101": own_games(("m-third", 30), ("z-first", 10), ("a-second", 20))},
            boards={"z-first": lobby("A"), "a-second": lobby("A"), "m-third": lobby("A")},
        )
        result, _ = await calculate(**world)

        self.assertEqual([g["guid"] for g in result["individual_games"]], ["z-first", "a-second", "m-third"])

    async def test_several_anchors_only_keep_games_all_of_them_played(self):
        world = three_game_world(
            anchors={
                "tok-101": own_games(("g1", 10), ("g2", 20), ("g3", 30), ("stray", 40)),
                "tok-201": own_games(("g1", 10), ("g2", 20), ("g3", 30)),
            },
        )
        result, _ = await calculate(**world)

        self.assertEqual([g["guid"] for g in result["individual_games"]], ["g1", "g2", "g3"])

    async def test_anchors_that_disagree_entirely_fall_back_to_the_union(self):
        world = three_game_world(
            anchors={"tok-101": own_games(("g1", 10)), "tok-201": own_games(("g2", 20))},
            boards={"g1": lobby("A"), "g2": lobby("B")},
        )
        result, _ = await calculate(**world)

        self.assertEqual([g["guid"] for g in result["individual_games"]], ["g1", "g2"])
        self.assertEqual(result["team_round_wins"], [1, 1])
        self.assertIsNone(result["winning_team_index"])

    async def test_lobby_strangers_are_left_out_of_the_teams_but_kept_in_the_game_log(self):
        world = three_game_world(boards={"g1": lobby("A", extras=True), "g2": lobby("B", extras=True),
                                         "g3": lobby("A", extras=True)})
        result, _ = await calculate(**world)

        shown = {e["username"] for team in result["teams"] for e in team}
        self.assertEqual(len(result["teams"]), 2)
        self.assertFalse({"Rando1", "Rando2"} & shown)
        logged = {p["username"] for p in result["individual_games"][0]["players"]}
        self.assertTrue({"Rando1", "Rando2"} <= logged)

    async def test_two_teams_both_ranked_first_leave_the_round_and_series_undecided(self):
        both_first = [player("alice", "Alice", 10, 1, 5, 1000), player("carol", "Carol", 20, 1, 4, 900)]
        world = three_game_world(anchors={"tok-101": own_games(("g1", 10))}, boards={"g1": both_first})
        result, _ = await calculate(**world)

        self.assertEqual(result["match_history"], [None])
        self.assertEqual(result["team_round_wins"], [1, 1])
        self.assertIsNone(result["winning_team_index"])
        self.assertIsNone(result["individual_games"][0]["winning_display_team_index"])


class ImpactScoreTests(unittest.IsolatedAsyncioTestCase):
    IMPACTS = {
        "g1": {"Alice": 80, "Bob": 50, "GuestG": None, "Carol": 74, "Dave": 30},
        "g2": {"Alice": 60, "Bob": 50, "GuestG": None, "Carol": 73, "Dave": None},
        "g3": {"Alice": None, "Bob": 50, "GuestG": None, "Carol": 71, "Dave": 31},
    }

    def _scored_world(self, **overrides):
        return three_game_world(
            boards={"g1": lobby("A", 10, 20, extras=True, impact=self.IMPACTS["g1"]),
                    "g2": lobby("B", 4, 9, extras=True, impact=self.IMPACTS["g2"]),
                    "g3": lobby("A", 1, 2, extras=True, impact=self.IMPACTS["g3"])},
            **overrides,
        )

    async def test_series_impact_is_the_mean_over_the_games_that_were_scored(self):
        result, _ = await calculate(**self._scored_world())

        impact = {e["username"]: e["stats"]["impact"] for team in result["teams"] for e in team}
        self.assertEqual(impact, {"Alice": 70.0, "Bob": 50.0, "GuestG": None, "Carol": 72.7, "Dave": 30.5})

    async def test_maps_without_impact_scoring_leave_every_player_unscored(self):
        result, _ = await calculate(**three_game_world())

        self.assertEqual({e["stats"]["impact"] for team in result["teams"] for e in team}, {None})

    async def test_values_that_are_not_scores_are_ignored(self):
        junk = {"Alice": "oops", "Bob": True, "GuestG": None, "Carol": 0, "Dave": 55}
        world = three_game_world(anchors={"tok-101": own_games(("g1", 10))},
                                 boards={"g1": lobby("A", impact=junk)})
        result, _ = await calculate(**world)

        impact = {e["username"]: e["stats"]["impact"] for team in result["teams"] for e in team}
        self.assertEqual(impact, {"Alice": None, "Bob": None, "GuestG": None, "Carol": 0.0, "Dave": 55.0})

    async def test_game_rows_carry_the_score_the_identity_and_the_display_team(self):
        result, _ = await calculate(**self._scored_world())

        rows = {p["username"]: p for p in result["individual_games"][0]["players"]}
        self.assertEqual(rows["Alice"]["impact_score"], 80)
        self.assertIsNone(rows["GuestG"]["impact_score"])
        self.assertEqual((rows["Alice"]["identity"], rows["GuestG"]["identity"]), ("alice", "guestg"))
        self.assertEqual([rows[n]["display_team_index"] for n in ("Alice", "Bob", "GuestG")], [0, 0, 0])
        self.assertEqual([rows[n]["display_team_index"] for n in ("Carol", "Dave")], [1, 1])
        self.assertEqual([rows[n]["display_team_index"] for n in ("Rando1", "Rando2")], [None, None])

    async def test_display_team_index_follows_the_neatqueue_roster_order(self):
        world = self._scored_world(history=finished_history(roster=((201, 202), (101, 102))))
        result, _ = await calculate(**world)

        rows = {p["username"]: p for p in result["individual_games"][0]["players"]}
        self.assertEqual((rows["Carol"]["display_team_index"], rows["Alice"]["display_team_index"]), (0, 1))


class ImpactHelperTests(unittest.TestCase):
    def test_numeric_scores_pass_through_including_zero(self):
        for raw in (0, 74, 74.5):
            with self.subTest(raw=raw):
                self.assertEqual(neatqueue_client.parse_impact_score(raw), raw)

    def test_everything_else_is_unscored(self):
        for raw in (None, True, False, "74", [], {}):
            with self.subTest(raw=raw):
                self.assertIsNone(neatqueue_client.parse_impact_score(raw))

    def test_identity_prefers_the_slug_and_falls_back_to_the_lowercased_name(self):
        self.assertEqual(neatqueue_client.player_identity("alice", "Alice"), "alice")
        self.assertEqual(neatqueue_client.player_identity(None, "GuestG"), "guestg")


class UnfinishedQueueTests(unittest.IsolatedAsyncioTestCase):
    async def test_roster_comes_from_the_panel_and_the_result_is_not_final(self):
        world = three_game_world(
            history=None,
            history_error="NeatQueue API returned 404",
            panel=panel_message("<@101> <@102>", "<@201> <@202>"),
            anchors={"tok-101": own_games(("g1", 10))},
            boards={"g1": lobby("A")},
        )
        result, error = await calculate(**world)

        self.assertIsNone(error)
        self.assertFalse(result["is_final"])
        self.assertEqual(result["match_start_ms"], START_MS)
        self.assertGreater(result["duration_ms"], 0)
        self.assertEqual([e["username"] for e in result["teams"][0]], ["Alice", "Bob", "GuestG"])

    async def test_a_history_entry_without_a_winner_counts_as_unfinished(self):
        world = three_game_world(
            history=finished_history(winner=None),
            panel=panel_message("<@101> <@102>", "<@201> <@202>"),
        )
        result, error = await calculate(**world)

        self.assertIsNone(error)
        self.assertFalse(result["is_final"])


class ErrorPathTests(unittest.IsolatedAsyncioTestCase):
    async def test_no_verified_player_gives_a_helpful_error(self):
        result, error = await calculate(**three_game_world(tokens={}))

        self.assertIsNone(result)
        self.assertIn("No linked", error)

    async def test_finished_entry_without_a_start_time_is_rejected(self):
        history = finished_history()
        del history[0]["start_time"]
        result, error = await calculate(**three_game_world(history=history))

        self.assertIsNone(result)
        self.assertIn("start time", error)

    async def test_finished_entry_without_a_roster_is_rejected(self):
        result, error = await calculate(**three_game_world(history=finished_history(teams=[])))

        self.assertIsNone(result)
        self.assertIn("no player roster", error)

    async def test_missing_panel_surfaces_the_history_error(self):
        world = three_game_world(history=None, history_error="boom", panel=None)
        result, error = await calculate(**world)

        self.assertIsNone(result)
        self.assertEqual(error, "boom")

    async def test_panel_without_team_fields_is_rejected(self):
        world = three_game_world(history=None, panel=SimpleNamespace(
            embeds=[discord.Embed(title="Results for Queue#3674")],
            created_at=datetime(2026, 10, 1, 18, 0, tzinfo=timezone.utc)))
        result, error = await calculate(**world)

        self.assertIsNone(result)
        self.assertIn("player roster", error)

    async def test_empty_scoreboards_are_rejected(self):
        result, error = await calculate(**three_game_world(boards={}))

        self.assertIsNone(result)
        self.assertIn("no player data", error)


if __name__ == "__main__":
    unittest.main()
