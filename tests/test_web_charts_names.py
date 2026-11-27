"""Website chart geometry, the trend-value rule and the name rule."""
import unittest
from types import SimpleNamespace

from web_charts import bar_fractions, format_value, game_count, trend_chart, trend_geometry, trend_value
from web_names import NameResolver, UNKNOWN_PLAYER, safe_label, safe_slug

HOME, OTHER = 1363265113880264745, 1075111506548625438


class TrendValueTests(unittest.TestCase):
    def test_kills_per_game_divides_by_the_players_own_game_count(self):
        # tests/test_history.py's fixture: 14 kills over 3 games.
        row = {"kills": 14, "games": 3, "avg_damage": 1000.0, "total_damage": 3000}
        self.assertAlmostEqual(trend_value(row, "kills"), 14 / 3)
        self.assertEqual(format_value("kills", trend_value(row, "kills")), "4.7")

    def test_a_substitute_is_divided_by_the_games_they_played(self):
        self.assertEqual(trend_value({"kills": 5, "games": 2, "avg_damage": 1105.0, "total_damage": 2210}, "kills"), 2.5)

    def test_legacy_rows_infer_games_or_stay_missing(self):
        legacy = {"kills": 12, "games": None, "avg_damage": 1000.0, "total_damage": 6000}
        self.assertEqual(game_count(legacy), 6)
        self.assertEqual(trend_value(legacy, "kills"), 2.0)
        unknown = {"kills": 12, "games": None, "avg_damage": 0.0, "total_damage": 0}
        self.assertIsNone(trend_value(unknown, "kills"))
        self.assertIsNone(trend_value(unknown, "damage"))
        self.assertIsNone(trend_value({"kills": None, "games": 6, "avg_damage": 900.0, "total_damage": 5400}, "kills"))
        self.assertIsNone(trend_value({"impact": None}, "impact"))

    def test_zero_is_a_value_but_missing_is_never_zero(self):
        self.assertEqual(trend_value({"kills": 0, "games": 4, "avg_damage": 10.0, "total_damage": 40}, "kills"), 0.0)


class TrendGeometryTests(unittest.TestCase):
    def points(self, values, key="avg_damage"):
        rows = []
        for i, v in enumerate(values):
            rows.append({"match_id": str(4800 + i), "won": i % 2 == 0, "score": "4-2",
                         "row": {key: v, "games": 6, "avg_damage": v if key == "avg_damage" else 1000.0,
                                 "total_damage": 6000, "impact": v if key == "impact" else None, "kills": 12}})
        return rows

    def test_slots_sit_at_column_centres_and_values_map_to_the_box(self):
        geo = trend_geometry(self.points([1000, 1500, 2000]), "damage")
        self.assertEqual([s["x"] for s in geo["slots"]], [round(1000 / 6, 2), 500.0, round(5000 / 6, 2)])
        ys = [s["y"] for s in geo["slots"]]
        self.assertGreater(ys[0], ys[1])
        self.assertGreater(ys[1], ys[2])
        self.assertTrue(all(0 <= y <= 300 for y in ys))
        self.assertEqual(geo["latest"], "2,000")
        self.assertEqual([t["fraction"] for t in geo["ticks"]], [0.0, 0.5, 1.0])
        self.assertEqual([t["label"] for t in geo["ticks"]], ["1,000", "1,500", "2,000"])  # round values, all points inside

    def test_a_single_queue_and_equal_values_draw_without_dividing_by_zero(self):
        one = trend_geometry(self.points([1200]), "damage")
        self.assertEqual(len(one["slots"]), 1)
        self.assertEqual(one["line"], "")  # one point: a dot, no line
        same = trend_geometry(self.points([900, 900, 900]), "damage")
        self.assertEqual(len({s["y"] for s in same["slots"]}), 1)

    def test_missing_points_break_the_line_and_are_never_drawn_as_zero(self):
        pts = self.points([1000, 1100, 1200, 1300, 1400])
        pts[2]["row"]["avg_damage"] = None
        geo = trend_geometry(pts, "damage")
        self.assertIsNone(geo["slots"][2]["y"])
        self.assertEqual(geo["line"].count("M"), 2)
        self.assertEqual(geo["slots"][2]["text"], "—")

    def test_a_metric_with_no_values_is_an_empty_state(self):
        # A player with damage in every queue and no impact scores (tests/test_history.py:321's case).
        charts = trend_chart(self.points([1000, 1100, 1200]))
        self.assertFalse(charts["damage"]["empty"])
        self.assertTrue(charts["impact"]["empty"])
        self.assertEqual(charts["impact"]["empty_text"], "No impact scores in these queues.")
        self.assertIsNone(charts["impact"]["latest"])

    def test_bar_fractions(self):
        self.assertEqual(bar_fractions([1482, 741]), [1.0, 0.5])
        self.assertEqual(bar_fractions([0, 0]), [0.0, 0.0])
        self.assertEqual(bar_fractions([]), [])


class NameRuleTests(unittest.TestCase):
    def test_ids_and_the_player_fallback_never_reach_the_page(self):
        self.assertEqual(safe_label("Player 123456789012345678"), UNKNOWN_PLAYER)
        self.assertEqual(safe_label("x123456789012345678y"), UNKNOWN_PLAYER)
        self.assertEqual(safe_label("Player (2)"), "Player (2)")
        self.assertEqual(safe_label(""), UNKNOWN_PLAYER)
        self.assertEqual(safe_label(None), UNKNOWN_PLAYER)
        self.assertFalse(safe_slug("abc123456789012345678"))
        self.assertFalse(safe_slug("a/b"))
        self.assertTrue(safe_slug("kestrel"))

    def fake_bot(self, home_members, other_members=None, chunked=True):
        guilds = {
            HOME: SimpleNamespace(chunked=chunked, get_member=lambda i: home_members.get(i)),
            OTHER: SimpleNamespace(chunked=True, get_member=lambda i: (other_members or {}).get(i)),
        }
        return SimpleNamespace(get_guild=lambda gid: guilds.get(gid))

    def test_linked_players_get_their_current_main_server_name(self):
        bot = self.fake_bot({7: SimpleNamespace(display_name="MainNick")})
        names = NameResolver(bot, HOME)
        self.assertTrue(names.ready)
        self.assertEqual(names.name(7, "OldNick"), "MainNick")

    def test_another_servers_cache_is_never_read(self):
        bot = self.fake_bot({}, {7: SimpleNamespace(display_name="OtherServerNick")})
        self.assertEqual(NameResolver(bot, HOME).name(7, "StoredName"), "StoredName")

    def test_before_the_cache_is_ready_and_without_a_bot_the_stored_name_is_used(self):
        self.assertFalse(NameResolver(self.fake_bot({}, chunked=False), HOME).ready)
        self.assertEqual(NameResolver(None, HOME).name(7, "Stored"), "Stored")
        self.assertFalse(NameResolver(None, HOME).ready)
        self.assertEqual(NameResolver(None, HOME).name(None, "sl1ce"), "sl1ce")


if __name__ == "__main__":
    unittest.main()
