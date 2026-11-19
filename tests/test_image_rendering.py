import io
import unittest
from unittest.mock import patch

from PIL import Image, ImageChops, ImageDraw

import image_utils
from image_utils import (
    QUEUE_IMG_ACCENT,
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


class QueueProgressLabelTests(unittest.TestCase):
    def test_live_and_final_cards_draw_the_appropriate_status_and_result_text(self):
        original_text = ImageDraw.ImageDraw.text
        for is_final, leader, expected in ((False, 1, "Team 2 leads"),
                                           (False, None, "No team leading"),
                                           (True, 1, "Team 2 Wins")):
            with self.subTest(is_final=is_final, leader=leader):
                drawn = []

                def capture(draw, xy, text, *args, **kwargs):
                    drawn.append((text, xy))
                    return original_text(draw, xy, text, *args, **kwargs)

                with patch.object(ImageDraw.ImageDraw, "text", new=capture):
                    rendered = decode(generate_queue_result_image(
                        "4603", [team_a(), team_b()], leader, [True, False, True],
                        [1, 2] if leader is not None else [1, 1], is_final=is_final))
                self.assertEqual(rendered.width, image_utils.QUEUE_IMG_WIDTH)
                texts = dict(drawn)
                self.assertIn(expected, texts)
                self.assertEqual("IN PROGRESS" in texts, not is_final)
                if not is_final:
                    self.assertNotIn("Team 2 Wins", texts)
                    self.assertEqual(texts["IN PROGRESS"][0], image_utils.QUEUE_IMG_WIDTH)
                    self.assertLess(texts["IN PROGRESS"][1], texts[" - "][1])


class TimelineTests(unittest.TestCase):
    """The MATCH TIMELINE: one lane per team, each game's dot in the lane of the team that won it."""
    S = 2  # the renderer's supersampling factor
    BOX_Y = 27
    LANE_Y = [(BOX_Y + 64) * S, (BOX_Y + 100) * S]
    MIDDLE_Y = (LANE_Y[0] + LANE_Y[1]) / 2
    BOX_CENTER_X = ((image_utils.QUEUE_IMG_WIDTH - image_utils.QUEUE_IMG_PADDING) - 560 / 2) * S
    LANE_CENTER_X = BOX_CENTER_X + 20 * S  # lanes start after the T1/T2 labels, so their middle is right of the box's

    def timeline(self, render):
        """[(x, y, filled)] for every game marker drawn inside the timeline box (accent rings excluded)."""
        original_ellipse = ImageDraw.ImageDraw.ellipse
        box_x0, box_x1 = self.BOX_CENTER_X - 280 * self.S, self.BOX_CENTER_X + 280 * self.S
        markers = []

        def ellipse(draw, xy, *args, **kwargs):
            x, y = (xy[0] + xy[2]) / 2, (xy[1] + xy[3]) / 2
            if box_x0 < x < box_x1 and kwargs.get("outline") != QUEUE_IMG_ACCENT:
                markers.append((x, y, kwargs.get("fill") is not None))
            return original_ellipse(draw, xy, *args, **kwargs)

        with patch.object(ImageDraw.ImageDraw, "ellipse", new=ellipse):
            render()
        return markers

    def test_each_game_sits_in_the_lane_of_the_team_that_won_it(self):
        markers = self.timeline(lambda: generate_queue_result_image(
            "4614", [team_a(), team_b()], 0, [True, False, True], [2, 1]))

        self.assertEqual([y for _x, y, _filled in markers], [self.LANE_Y[0], self.LANE_Y[1], self.LANE_Y[0]])
        self.assertTrue(all(filled for *_xy, filled in markers))
        self.assertEqual(sorted(x for x, _y, _f in markers), [x for x, _y, _f in markers])

    def test_a_tied_series_still_shows_who_won_each_game(self):
        # No leader, so match_history can't say who won; the per-game winners can.
        markers = self.timeline(lambda: generate_queue_result_image(
            "4702", [team_a(), team_b()], None, [None] * 4, [2, 2], is_final=False, game_winners=[0, 1, 1, 0]))

        self.assertEqual([y for _x, y, _f in markers],
                         [self.LANE_Y[0], self.LANE_Y[1], self.LANE_Y[1], self.LANE_Y[0]])

    def test_an_unknown_winner_is_a_hollow_ring_between_the_lanes(self):
        markers = self.timeline(lambda: generate_queue_result_image(
            "4614", [team_a(), team_b()], 0, [True, None], [1, 0]))

        self.assertEqual(markers[1][1:], (self.MIDDLE_Y, False))

    def test_a_single_game_is_centred(self):
        markers = self.timeline(lambda: generate_queue_result_image(
            "4615", [team_a(), team_b()], 0, [True], [1, 0], is_final=False))

        self.assertEqual([(x, y) for x, y, _f in markers], [(self.LANE_CENTER_X, self.LANE_Y[0])])

    def test_game_card_places_every_game_from_its_winners(self):
        markers = self.timeline(lambda: generate_queue_game_image(
            "4614", 2, 3, [team_a(), team_b()], 1, [True, False, True], [1, 1], [0, 1, 0]))

        self.assertEqual([y for _x, y, _f in markers], [self.LANE_Y[0], self.LANE_Y[1], self.LANE_Y[0]])

    def test_winners_are_rebuilt_from_the_older_match_history(self):
        self.assertEqual(image_utils.winners_from_history([True, False, None], 1, 2), [1, 0, None])
        self.assertEqual(image_utils.winners_from_history([True, False], None, 2), [None, None])
        self.assertEqual(image_utils.winners_from_history([False], 0, 3), [None])


class MvpTests(unittest.TestCase):
    def test_the_best_impact_among_players_of_every_game_wins(self):
        teams = [[person("Awang", 5, 900, 80, games=5), person("Sub", 9, 2000, 95, games=2)],
                 [person("Bravo", 4, 800, 70, games=5)]]
        self.assertEqual(image_utils.pick_mvp(teams, 5), (0, 0))

    def test_a_shared_best_score_or_no_scores_gives_no_mvp(self):
        tied = [[person("Awang", 5, 900, 80.2)], [person("Bravo", 4, 800, 79.6)]]
        self.assertIsNone(image_utils.pick_mvp(tied, 5))
        self.assertIsNone(image_utils.pick_mvp([[person("Awang", 5, 900)], [person("Bravo", 4, 800)]], 5))
        self.assertIsNone(image_utils.pick_mvp([[person("Awang", 5, 900, 80)]], None))

    def test_a_player_who_switched_sides_counts_once(self):
        mover_1 = {**person("Mover", 5, 900, 90, games=2), "discord_id": 7}
        mover_2 = {**person("Mover", 2, 400, 90, games=1), "discord_id": 7}
        teams = [[person("Awang", 5, 900, 70, games=3), mover_1], [person("Bravo", 4, 800, 60, games=3), mover_2]]
        self.assertEqual(image_utils.pick_mvp(teams, 3), (0, 1))  # tagged on the row with most of their games

    def test_only_finished_series_with_a_winner_get_the_tag(self):
        drawn = []
        original_text = ImageDraw.ImageDraw.text

        def capture(draw, xy, text, *args, **kwargs):
            drawn.append(text)
            return original_text(draw, xy, text, *args, **kwargs)

        cases = [(0, True, True), (0, False, False), (None, True, False)]
        for winner, is_final, expected in cases:
            with self.subTest(winner=winner, is_final=is_final):
                drawn.clear()
                with patch.object(ImageDraw.ImageDraw, "text", new=capture):
                    generate_queue_result_image("4614", [team_a(), team_b()], winner, [True] * 5, [5, 0],
                                                is_final=is_final)
                self.assertEqual("MVP" in drawn, expected)


class PartialPlayerLabelTests(unittest.TestCase):
    def drawn_texts(self, render):
        original_text = ImageDraw.ImageDraw.text
        drawn = []

        def capture(draw, xy, text, *args, **kwargs):
            drawn.append((text, kwargs.get("fill")))
            return original_text(draw, xy, text, *args, **kwargs)

        with patch.object(ImageDraw.ImageDraw, "text", new=capture):
            render()
        return drawn

    def test_only_players_who_missed_games_get_a_label(self):
        self.assertEqual(image_utils.partial_games_label(person("Sub", 1, 174, games=1), 7), "1/7")
        self.assertIsNone(image_utils.partial_games_label(person("Full", 1, 174, games=7), 7))
        self.assertIsNone(image_utils.partial_games_label(person("Sub", 1, 174, games=1), None))

    def test_a_substitute_is_labelled_and_the_footer_explains_it(self):
        subbed = [person("Awang", 31, 9120, games=3), person("Mia", 18, 6400, games=2),
                  person("StepZ", 4, 900, games=1, guest=True)]
        others = [person("Bravo", 25, 8100, games=3), person("Delta", 12, 4900, games=3)]
        texts = self.drawn_texts(lambda: generate_queue_result_image(
            "4614", [subbed, others], 0, [True, False, True], [2, 1], duration_ms=600_000))

        colors = dict(texts)
        self.assertEqual((colors.get("2/3"), colors.get("1/3")), (QUEUE_IMG_ACCENT, QUEUE_IMG_ACCENT))
        self.assertNotIn("3/3", colors)
        footer = next(text for text, _ in texts if text.startswith("Unlinked"))
        self.assertEqual(colors[footer], QUEUE_IMG_MUTED)
        self.assertEqual(colors.get("2/3 = played 2 of 3 games"), QUEUE_IMG_ACCENT)

    def test_full_series_and_game_cards_have_no_label(self):
        full = self.drawn_texts(lambda: generate_queue_result_image(
            "3674", [team_a(), team_b()], 0, [True, False, True], [2, 1], duration_ms=600_000))
        game = self.drawn_texts(lambda: generate_queue_game_image(
            "4614", 7, 7, [[person("StepZ", 1, 174, games=1)], [person("Bravo", 0, 90, games=1)]], 0,
            [True] * 7, [4, 3]))

        for texts in (full, game):
            self.assertFalse(any("/" in text and text.replace("/", "").isdigit() for text, _ in texts))
            self.assertFalse(any("games" in text and "played" in text for text, _ in texts))


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


class UnlinkedIconTests(unittest.TestCase):
    def test_each_size_is_built_once_and_reused(self):
        icon = image_utils._build_unlinked_icon(36)
        self.assertIs(image_utils._build_unlinked_icon(36), icon)
        self.assertEqual(icon.size, (36, 36))
        self.assertEqual(image_utils._build_unlinked_icon(48).size, (48, 48))

    def test_reusing_the_icon_leaves_cards_unchanged(self):
        first = decode(generate_queue_result_image("3674", [team_a(), team_b()], 0, [True], [1, 0]))
        second = decode(generate_queue_result_image("3674", [team_a(), team_b()], 0, [True], [1, 0]))
        self.assertFalse(differs(first, second))


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
