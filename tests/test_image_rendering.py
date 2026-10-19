import io
import unittest

from PIL import Image, ImageChops

import image_utils
from image_utils import (
    QUEUE_IMG_MUTED,
    QUEUE_IMG_TEXT,
    _queue_cell,
    generate_queue_game_image,
    generate_queue_result_image,
    has_impact_scores,
    shows_impact_column,
)


def person(name, kills, damage, impact=None, games=5, guest=False):
    return {
        "display_name": name, "username": name, "guest": guest,
        "stats": {"games": games, "wins": 0, "kills": kills, "damage": damage, "impact": impact},
    }


def team_a(impact=True):
    return [person("Awang", 31, 9120, 78.4 if impact else None), person("Mia", 18, 6400, 55.5 if impact else None),
            person("GuestPlayer", 9, 3100, None, guest=True)]


def team_b(impact=True):
    return [person("Bravo", 25, 8100, 71.0 if impact else None), person("Delta", 12, 14900, 100 if impact else None)]


def decode(buffer):
    return Image.open(io.BytesIO(buffer.getvalue())).convert("RGB")


def differs(a, b):
    return a.size != b.size or ImageChops.difference(a, b).getbbox() is not None


class ImpactColumnLayoutTests(unittest.TestCase):
    HISTORY = [True, False, True]

    def render(self, teams, **kwargs):
        return decode(generate_queue_result_image("3674", teams, 0, self.HISTORY, [2, 1], duration_ms=600_000, **kwargs))

    def test_scored_players_add_a_column_without_changing_the_canvas_size(self):
        scored = self.render([team_a(), team_b()])
        unscored = self.render([team_a(impact=False), team_b(impact=False)])

        self.assertEqual(scored.size, unscored.size)
        self.assertEqual(scored.width, image_utils.QUEUE_IMG_WIDTH)
        self.assertTrue(differs(scored, unscored))

    def test_a_series_nobody_scored_keeps_the_original_layout(self):
        with_none = self.render([team_a(impact=False), team_b(impact=False)])
        without_key = [[{**e, "stats": {k: v for k, v in e["stats"].items() if k != "impact"}} for e in team]
                       for team in (team_a(impact=False), team_b(impact=False))]

        self.assertFalse(differs(with_none, self.render(without_key)))

    def test_three_team_results_ignore_impact_and_keep_the_original_layout(self):
        third = [person("Foxtrot", 3, 900, 20.0)]
        scored = self.render([team_a(), team_b(), third])
        stripped = self.render([team_a(impact=False), team_b(impact=False), [person("Foxtrot", 3, 900)]])

        self.assertFalse(differs(scored, stripped))

    def test_every_series_shape_renders(self):
        shapes = {
            "no history": dict(match_history=None, team_round_wins=None, duration_ms=None),
            "single game": dict(match_history=[True], team_round_wins=[1, 0], duration_ms=900_000),
            "twelve games": dict(match_history=[True, False] * 6, team_round_wins=[6, 6], duration_ms=9_000_000),
            "unknown games": dict(match_history=[None, None], team_round_wins=[0, 0], duration_ms=None),
        }
        for name, kwargs in shapes.items():
            with self.subTest(shape=name):
                image = decode(generate_queue_result_image("3674", [team_a(), team_b()], None, **kwargs))
                self.assertEqual(image.width, image_utils.QUEUE_IMG_WIDTH)

    def test_an_empty_team_still_renders_with_impact_elsewhere(self):
        image = decode(generate_queue_result_image("3674", [team_a(), []], 0, self.HISTORY, [2, 1]))

        self.assertEqual(image.width, image_utils.QUEUE_IMG_WIDTH)


class ImpactCellTests(unittest.TestCase):
    def cell(self, impact):
        return _queue_cell("Impact", person("Awang", 1, 1, impact), "Awang")

    def test_scores_are_whole_numbers_rounded_half_up(self):
        self.assertEqual(self.cell(72.5)[0], "73")
        self.assertEqual(self.cell(72.4)[0], "72")
        self.assertEqual(self.cell(100)[0], "100")

    def test_a_real_zero_is_shown_unlike_a_missing_score(self):
        self.assertEqual(self.cell(0.0), ("0", True, QUEUE_IMG_TEXT))
        self.assertEqual(self.cell(None), ("-", True, QUEUE_IMG_MUTED))

    def test_other_columns_keep_their_formatting(self):
        entry = person("Awang", 31, 9120, games=5)
        self.assertEqual(_queue_cell("Kills", entry, "Awang")[:2], ("31", False))
        self.assertEqual(_queue_cell("Dmg", entry, "Awang")[:2], ("9,120", True))
        self.assertEqual(_queue_cell("Avg", entry, "Awang")[0], "1,824")
        self.assertEqual(_queue_cell("Player", entry, "Awang")[0], "Awang")

    def test_average_damage_of_a_player_without_games_is_zero(self):
        self.assertEqual(_queue_cell("Avg", person("Awang", 0, 500, games=0), "Awang")[0], "0")


class HasImpactScoresTests(unittest.TestCase):
    def test_detects_any_scored_player(self):
        self.assertTrue(has_impact_scores([team_a(), team_b()]))
        self.assertTrue(has_impact_scores([[person("Zero", 1, 1, impact=0.0)]]))

    def test_nobody_scored(self):
        self.assertFalse(has_impact_scores([team_a(impact=False), team_b(impact=False)]))
        self.assertFalse(has_impact_scores([]))
        self.assertFalse(has_impact_scores([[]]))

    def test_entries_without_stats_or_impact_are_tolerated(self):
        self.assertFalse(has_impact_scores([[{"username": "x"}, {"stats": None}, {"stats": {"kills": 1}}]]))

    def test_the_column_needs_the_standard_two_team_layout(self):
        self.assertTrue(shows_impact_column([team_a(), team_b()]))
        self.assertFalse(shows_impact_column([team_a(), team_b(), team_a()]))
        self.assertFalse(shows_impact_column([team_a(impact=False), team_b(impact=False)]))


class GameCardTests(unittest.TestCase):
    HISTORY = [True, False, True]

    @staticmethod
    def game_team(impact=True):
        return [person("Awang", 8, 2140, 91 if impact else None, games=1),
                person("Guest", 1, 410, None, games=1, guest=True)]

    def render(self, teams=None, game_number=2, winner=0, history=HISTORY, score=(1, 1), total=3):
        teams = teams or [self.game_team(), self.game_team()]
        return decode(generate_queue_game_image("3674", game_number, total, teams, winner, history, list(score)))

    def test_every_game_state_renders_at_full_width(self):
        cases = {
            "known winner": dict(),
            "team two won": dict(winner=1),
            "unknown winner": dict(winner=None),
            "single game series": dict(game_number=1, total=1, history=[True], score=(1, 0)),
            "no timeline": dict(history=None),
            "game missing from the timeline": dict(game_number=9, total=9),
        }
        for name, kwargs in cases.items():
            with self.subTest(case=name):
                self.assertEqual(self.render(**kwargs).width, image_utils.QUEUE_IMG_WIDTH)

    def test_impact_swaps_the_layout_without_changing_the_canvas_size(self):
        scored = self.render()
        unscored = self.render(teams=[self.game_team(impact=False), self.game_team(impact=False)])

        self.assertEqual(scored.size, unscored.size)
        self.assertTrue(differs(scored, unscored))

    def test_the_timeline_ring_follows_the_selected_game(self):
        self.assertTrue(differs(self.render(game_number=1), self.render(game_number=3)))

    def test_the_winner_changes_the_picture(self):
        self.assertTrue(differs(self.render(winner=0), self.render(winner=1)))
        self.assertTrue(differs(self.render(winner=0), self.render(winner=None)))

    def test_the_series_score_is_part_of_the_header(self):
        self.assertTrue(differs(self.render(score=(1, 1)), self.render(score=(2, 0))))

    def test_missing_scores_are_padded_instead_of_crashing(self):
        buffer = generate_queue_game_image("3674", 1, 1, [self.game_team(), self.game_team()], 0, [True], [])

        self.assertGreater(len(buffer.getvalue()), 0)


if __name__ == "__main__":
    unittest.main()
