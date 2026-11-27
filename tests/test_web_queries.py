"""Website queries: archive definition, rosters, player URLs, summaries and Hall of Fame scoping."""
import sqlite3
import tempfile
import unittest
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import db
import web_queries as q
try:
    import web_fixture as f
except ImportError:  # run as tests.test_web_queries
    from tests import web_fixture as f


class QueryTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmp.name) / "site.db")
        self.conn = f.standard_db(self.path)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()


class ArchiveTests(QueryTestCase):
    def test_the_archive_is_the_union_of_snapshots_and_stat_rows_of_this_guild_only(self):
        self.conn.execute("INSERT INTO processed_matches VALUES (?, '5555', 'x')", (f.HOME,))
        ids = q.list_queue_ids(self.conn, f.HOME, 50, 0)
        self.assertEqual(sorted(ids), ["4700", "4821", "4999"])
        self.assertEqual(q.count_queues(self.conn, f.HOME), 3)
        self.assertNotIn("5555", ids)  # processed_matches is not the archive

    def test_snapshot_only_queues_are_dated_by_saved_time_and_labelled(self):
        queue = q.get_queue(self.conn, f.HOME, "4999")
        self.assertTrue(queue["date_is_saved"])
        self.assertIsNone(queue["duration_ms"])
        self.assertTrue(queue["has_snapshot"])

    def test_row_only_queue_uses_rows_and_has_no_games(self):
        queue = q.get_queue(self.conn, f.HOME, "4700")
        self.assertFalse(queue["has_snapshot"])
        self.assertEqual(queue["games"], [])
        self.assertEqual(queue["score"], [1, 4])
        self.assertEqual(queue["winner"], 1)

    def test_other_guild_queue_with_the_same_number_never_appears(self):
        queue = q.get_queue(self.conn, f.HOME, "4821")
        names = [p["stored_name"] for team in queue["teams"] for p in team]
        self.assertNotIn("OTHERGUILDSECRETNAME", names)
        self.assertIsNone(q.get_queue(self.conn, f.HOME, "1"))


class RosterTests(unittest.TestCase):
    def test_v2_keeps_a_verified_slug_and_a_logged_out_namesake_apart(self):
        snap = {"v": 2, "games": [f.game(1, 0, [
            f.prow(0, "RavenNick", discord_id=9, slug="raven", damage=1000),
            f.prow(0, "Raven", damage=400),
        ])]}
        team = q.build_queue_roster(snap)[0]
        self.assertEqual(len(team), 2)
        linked = [p for p in team if p["linked"]][0]
        guest = [p for p in team if not p["linked"]][0]
        self.assertEqual((linked["discord_id"], linked["stats"]["damage"]), (9, 1000))
        self.assertEqual((guest["discord_id"], guest["stored_name"], guest["stats"]["damage"]), (None, "Raven", 400))

    def test_v2_same_name_logged_out_rows_across_games_merge_with_a_marker(self):
        snap = {"v": 2, "games": [f.game(1, 0, [f.prow(0, "Player")]), f.game(2, 0, [f.prow(0, "Player")])]}
        player = q.build_queue_roster(snap)[0][0]
        self.assertEqual(player["stats"]["games"], 2)
        self.assertTrue(player["combined_by_name"])

    def test_v1_ambiguous_names_on_one_team_get_no_series_totals(self):
        snap = {"v": 1, "games": [f.game(1, 0, [
            {"team": 0, "name": "Player", "discord_id": None, "guest": True, "kills": 1, "damage": 100, "impact": None},
            {"team": 0, "name": "Player", "discord_id": None, "guest": True, "kills": 2, "damage": 200, "impact": None},
        ])]}
        team = q.build_queue_roster(snap)[0]
        self.assertEqual(len(team), 2)  # both observations stay visible
        self.assertEqual([p["stats"] for p in team], [None, None])

    def test_v1_snapshots_still_group_linked_players_by_id(self):
        row = {"team": 0, "name": "A", "discord_id": 5, "guest": False, "kills": 1, "damage": 100, "impact": 50}
        snap = {"v": 1, "games": [f.game(1, 0, [row]), f.game(2, 0, [dict(row, damage=300)])]}
        team = q.build_queue_roster(snap)[0]
        self.assertEqual((len(team), team[0]["stats"]["damage"], team[0]["stats"]["avg"]), (1, 400, 200))

    def test_a_side_switcher_appears_once_per_side(self):
        snap = {"v": 2, "games": [f.game(1, 0, [f.prow(0, "Swap", discord_id=4, slug="swap")]),
                                  f.game(2, 1, [f.prow(1, "Swap", discord_id=4, slug="swap")])]}
        teams = q.build_queue_roster(snap)
        self.assertEqual((len(teams[0]), len(teams[1])), (1, 1))

    def test_partial_label_and_mvp_follow_the_card_rules(self):
        snap = {"v": 2, "games": [
            f.game(1, 0, [f.prow(0, "A", discord_id=1, slug="a", impact=80), f.prow(1, "Sub", discord_id=2, slug="s", impact=90)]),
            f.game(2, 0, [f.prow(0, "A", discord_id=1, slug="a", impact=80), f.prow(1, "B", discord_id=3, slug="b", impact=50)]),
        ]}
        teams = q.build_queue_roster(snap)
        self.assertEqual([p["partial"] for p in teams[1]], ["1/2", "1/2"])
        self.assertEqual(q.pick_mvp(teams, 2, True), (0, 0))  # the sub's 90 doesn't count: not every game
        self.assertIsNone(q.pick_mvp(teams, 2, False))


class PlayerTests(QueryTestCase):
    def test_current_slug_resolves_and_an_old_account_redirects(self):
        self.assertEqual(q.resolve_player_slug(self.conn, f.HOME, "kestrel"), ("page", 111111111111111111))
        # Kestrel re-runs /verify as a new account.
        self.conn.execute("UPDATE users SET slug = 'kestrel2' WHERE discord_id = 111111111111111111")
        self.assertEqual(q.resolve_player_slug(self.conn, f.HOME, "kestrel"), ("redirect", 111111111111111111))
        self.assertEqual(q.player_slugs(self.conn, f.HOME, [111111111111111111]), {111111111111111111: "kestrel2"})

    def test_an_account_linked_only_through_another_guild_has_no_page(self):
        self.conn.execute("DELETE FROM player_queue_stats WHERE guild_id = ? AND discord_id = 444444444444444444", (f.HOME,))
        self.assertIsNone(q.resolve_player_slug(self.conn, f.HOME, "okra"))

    def test_a_shared_slug_goes_to_the_user_with_more_queues_and_the_other_gets_no_link(self):
        f.add_user(self.conn, 555, "kestrel", "Copycat")
        f.add_row(self.conn, f.HOME, 4700, discord_id=555, name="Copycat", slug="kestrel", team=0)
        self.assertEqual(q.resolve_player_slug(self.conn, f.HOME, "kestrel"), ("page", 111111111111111111))
        slugs = q.player_slugs(self.conn, f.HOME, [111111111111111111, 555])
        self.assertEqual(slugs, {111111111111111111: "kestrel"})

    def test_a_slug_with_a_long_digit_run_is_never_linked(self):
        self.conn.execute("UPDATE users SET slug = 'k123456789012345678' WHERE discord_id = 111111111111111111")
        self.assertEqual(q.player_slugs(self.conn, f.HOME, [111111111111111111]), {})
        self.assertIsNone(q.resolve_player_slug(self.conn, f.HOME, "k123456789012345678"))

    def test_summary_equals_the_bots_summary_for_a_one_guild_player(self):
        now = datetime(2026, 11, 26, tzinfo=timezone.utc)
        mine = q.get_player_summary(self.conn, f.HOME, 333333333333333333, now=now)
        self.conn.commit()
        real_connect = sqlite3.connect

        @contextmanager
        def connect_and_close(*args, **kwargs):
            connection = real_connect(*args, **kwargs)
            try:
                with connection:
                    yield connection
            finally:
                connection.close()

        with patch.object(db, "DB_PATH", self.path), patch.object(db, "datetime") as fake_dt,                 patch.object(db.sqlite3, "connect", side_effect=connect_and_close):
            fake_dt.now.return_value = now
            fake_dt.side_effect = lambda *a, **k: datetime(*a, **k)
            theirs = db.get_player_queue_stats_summary(333333333333333333)
        self.assertEqual(mine, theirs)

    def test_history_enrichment_only_matches_safely(self):
        # A legacy row (no kills) for Kestrel in 4821; the snapshot has his discord_id, so it fills.
        self.conn.execute("UPDATE player_queue_stats SET kills = NULL WHERE match_id = '4821' AND discord_id = 111111111111111111")
        rows = q.get_player_history(self.conn, f.HOME, 111111111111111111, 10)
        row = [r for r in rows if r["match_id"] == "4821"][0]
        self.assertEqual(row["kills"], 24)
        self.assertEqual(row["score"], "4-2")
        self.assertTrue(row["won"])

    def test_stored_names_are_the_newest_main_guild_row(self):
        names = q.stored_names(self.conn, f.HOME, [111111111111111111, 444444444444444444])
        self.assertEqual(names[111111111111111111], "KestrelNick")
        self.assertEqual(names[444444444444444444], "OkraOld")


class HallOfFameTests(QueryTestCase):
    def test_only_this_guilds_records_are_returned(self):
        hof = q.get_hall_of_fame(self.conn, f.HOME)
        self.assertEqual(set(hof), {"most_kills"})


if __name__ == "__main__":
    unittest.main()
