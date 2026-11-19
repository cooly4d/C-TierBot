"""Golden-image tests: each card fixture below is rendered and compared with a saved reference in tests/golden/.

They catch accidental visual changes, for example when shared drawing code is refactored. After an intended visual
change, rewrite the references and look at every changed file before committing:

    UPDATE_GOLDEN=1 python -m unittest tests.test_golden_images

References are stored at half size to keep the repository small, and compared with a small tolerance so that a
different Pillow or FreeType build on another computer doesn't fail them.
"""
import io
import os
import unittest
from datetime import datetime, timezone
from unittest import mock

from PIL import Image, ImageChops

import image_utils as iu

GOLDEN_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "golden")
UPDATE = os.getenv("UPDATE_GOLDEN") == "1"
MAX_MEAN_DIFF = 0.6        # mean per-pixel difference (0-255, greyscale)
MAX_CHANGED_SHARE = 0.002  # share of pixels that differ by 48 or more


class _FixedDatetime(datetime):
    """The compare card prints the current month; pin it so the references don't change every month."""

    @classmethod
    def now(cls, tz=None):
        return datetime(2026, 11, 19, 12, 0, tzinfo=tz or timezone.utc)


def _player(name, kills, damage, impact=None, games=5, guest=False):
    return {"display_name": name, "username": name, "guest": guest,
            "stats": {"games": games, "wins": 0, "kills": kills, "damage": damage, "impact": impact}}


TEAM_1 = [_player("Awang", 31, 9120, 78.4), _player("xXSniperQueenXx", 18, 6400, 55.5), _player("Mia", 14, 5210, 61.0),
          _player("GuestPlayer", 9, 3100, None, guest=True)]
TEAM_2 = [_player("Bravo", 25, 8100, 71.0), _player("Delta", 12, 7900, 66.2), _player("stepz", 6, 2100, 40.0, games=2),
          _player("Kilo_Warrior_99", 4, 1500, None, games=3), _player("Charlie", 10, 4200, 48.0)]
HISTORY = [True, False, True, False, True]


def _history_rows(count=10):
    rows = []
    for i in range(count):
        won = (i * 7) % 3 != 0
        rows.append({"match_id": str(4614 - i), "team_index": 0, "winning_team_index": 0 if won else 1,
                     "team_round_wins": [3, 1] if won else [2, 3], "won": won, "games": 4,
                     "kills": 5 + (i * 3) % 9, "total_damage": 3000 + i * 200,
                     "avg_damage": 820.0 + ((i * 137) % 600), "impact": None if i % 4 == 3 else 48.0 + (i * 11) % 40,
                     "duration_ms": 1_800_000, "match_start_ms": 1, "match_end_ms": 2,
                     "display_name": "Awang", "username": "Awang"})
    return rows


def _inventory_items():
    manifest = iu.load_item_manifest()
    picks = [t for t in manifest if t.startswith(("outfit", "karambit", "bayonet", "emote_", "heal_", "death_",
                                                  "crosshair"))]
    items = []
    for item_type in picks[::9][:30]:
        meta = manifest[item_type]
        rarity = meta.get("rarity") or 1
        items.append({"type": item_type, "name": meta["name"], "rarity": rarity,
                      "value": [0, 120, 450, 1600, 5200, 22000][rarity % 6]})
    items += [dict(items[2]), dict(items[2]), dict(items[5])]
    items.append({"type": "unknown_item_xyz", "name": "Mystery Thing", "rarity": 3, "value": 300})
    return items


def _market():
    outfits = [t for t in iu.load_item_manifest() if t.startswith("outfit")]
    return {"balance": 4321, "offers": [
        {"slot": 0, "items": [{"type": outfits[10]}], "price": 450, "purchased": False},
        {"slot": 1, "items": [{"type": "karambit_fade"}], "price": 2200, "purchased": True},
        {"slot": 2, "items": [{"type": outfits[40]}, {"type": "emote_gg"}, {"type": "heal_moon"},
                              {"type": "death_confetti"}], "price": 3000, "purchased": False},
        {"slot": 3, "items": [{"type": outfits[70]}, {"type": "boost_star"}], "price": 1800, "purchased": False},
        {"slot": 4, "items": [{"type": outfits[90]}], "price": 1980, "purchased": False},
    ]}


LEFT_STATS = {"games": 812, "wins": 97, "kills": 2311, "damage": 912_400}
RIGHT_STATS = {"games": 640, "wins": 71, "kills": 1820, "damage": 701_100}
LEFT_SUMMARY = {"matches": 60, "total_duration_ms": 31 * 3_600_000 + 59_000, "all_time_avg_damage": 1180,
                "monthly_avg_damage": 1244}
RIGHT_SUMMARY = {"matches": 41, "total_duration_ms": 19 * 3_600_000, "all_time_avg_damage": 1012,
                 "monthly_avg_damage": 1390}
LONG_TEAM = [_player("TheVeryLongUsernameThatNeverEnds", 20, 6000, 70), _player("Ω_unicode_名前", 10, 3000, 50),
             _player("AnotherExtremelyLongGuestName", 5, 1000, None, 1, True), _player("Z", 1, 100, 10)]
ALL_LINKED_1 = [{**e, "guest": False} for e in TEAM_1]
ALL_LINKED_2 = [{**e, "guest": False, "stats": {**e["stats"], "games": 5}} for e in TEAM_2[:4]]

FIXTURES = {
    "queue_final": lambda: iu.generate_queue_result_image(
        "4614", [TEAM_1, TEAM_2], 0, HISTORY, [3, 2], duration_ms=42 * 60_000 + 18_000),
    "queue_live": lambda: iu.generate_queue_result_image(
        "4615", [TEAM_1, TEAM_2], 1, [True, False, True], [1, 2], duration_ms=19 * 60_000, is_final=False),
    "queue_no_impact": lambda: iu.generate_queue_result_image(
        "4616", [[{**e, "stats": {**e["stats"], "impact": None}} for e in team] for team in (TEAM_1, TEAM_2)], None,
        [None, None], [1, 1]),
    "queue_all_linked": lambda: iu.generate_queue_result_image(
        "4620", [ALL_LINKED_1, ALL_LINKED_2], 0, HISTORY, [3, 2], duration_ms=40 * 60_000),
    "queue_long_names": lambda: iu.generate_queue_result_image(
        "12345", [LONG_TEAM, TEAM_2[:4]], 1, [False, False, True, False], [1, 3], duration_ms=3 * 3_600_000 + 300_000),
    "game_card": lambda: iu.generate_queue_game_image("4614", 3, 5, [
        [_player("Awang", 7, 2100, 80, 1), _player("xXSniperQueenXx", 4, 1300, 52, 1), _player("Mia", 3, 1100, 60, 1),
         _player("GuestPlayer", 2, 700, None, 1, True)],
        [_player("Bravo", 5, 1800, 70, 1), _player("Delta", 2, 1400, 63, 1), _player("stepz", 1, 600, 38, 1),
         _player("Charlie", 2, 900, 47, 1)]], 0, HISTORY, [2, 1], [0, 1, 0, 1, 0]),
    "history_damage": lambda: iu.generate_history_image("Awang", _history_rows(), "damage"),
    "history_impact": lambda: iu.generate_history_image("Awang", _history_rows(), "impact"),
    "history_empty": lambda: iu.generate_history_image("Awang", [], "damage"),
    "history_long_name": lambda: iu.generate_history_image("TheVeryLongUsernameThatNeverEnds_Again", _history_rows(3),
                                                           "kills"),
    "compare": lambda: iu.generate_compare_image(
        "Awang", LEFT_STATS, 48_200, LEFT_SUMMARY, "xXSniperQueenXx", RIGHT_STATS, 102_900, RIGHT_SUMMARY,
        {"tier": "A-", "rating": 72, "region": "EU"}, None),
    "compare_unverified": lambda: iu.generate_compare_image(
        "Awang", LEFT_STATS, 48_200, LEFT_SUMMARY, "NewPlayer", None, None, None,
        {"tier": "A-", "rating": 72, "region": "EU"}, None),
    "inventory": lambda: iu.generate_inventory_image("Awang", _inventory_items(), 0, "all"),
    "inventory_empty_filter": lambda: iu.generate_inventory_image("Awang", _inventory_items(), 0, "0"),
    "shop": lambda: iu.generate_shop_image("Awang", _market(), "all", 0),
    "shop_empty": lambda: iu.generate_shop_image("Awang", {"balance": 0, "offers": []}, "daily", 0),
}


BUNDLED_FONTS = {
    "QUEUE_FONT_PATH": os.path.join(os.path.dirname(os.path.abspath(iu.__file__)), "QuattrocentoSans-Regular.ttf"),
    "QUEUE_FONT_BOLD_PATH": os.path.join(os.path.dirname(os.path.abspath(iu.__file__)), "QuattrocentoSans-Bold.ttf"),
}


def render_fixture(name: str) -> Image.Image:
    # The bundled fonts, whatever QUEUE_FONT_PATH says, so a local font override can't change the pictures.
    with mock.patch.object(iu, "datetime", _FixedDatetime), mock.patch.multiple(iu, **BUNDLED_FONTS):
        buffer = FIXTURES[name]()
    image = Image.open(io.BytesIO(buffer.getvalue())).convert("RGB")
    return image.resize((image.width // 2, image.height // 2), Image.Resampling.LANCZOS)


def difference(a: Image.Image, b: Image.Image) -> tuple[float, float]:
    """(mean greyscale difference, share of pixels that differ by 48 or more)."""
    histogram = ImageChops.difference(a, b).convert("L").histogram()
    total = a.width * a.height
    mean = sum(level * count for level, count in enumerate(histogram)) / total
    return mean, sum(histogram[48:]) / total


class GoldenImageTests(unittest.TestCase):
    def test_cards_match_their_references(self):
        os.makedirs(GOLDEN_DIR, exist_ok=True)
        for name in FIXTURES:
            with self.subTest(card=name):
                rendered = render_fixture(name)
                path = os.path.join(GOLDEN_DIR, f"{name}.png")
                if UPDATE:
                    rendered.save(path, optimize=True)
                    continue
                self.assertTrue(os.path.exists(path), f"{name}: no reference picture; run with UPDATE_GOLDEN=1")
                with Image.open(path) as stored:
                    reference = stored.convert("RGB")
                self.assertEqual(rendered.size, reference.size, f"{name}: card size changed")
                mean, changed = difference(rendered, reference)
                self.assertLessEqual(mean, MAX_MEAN_DIFF, f"{name}: looks different (mean diff {mean:.2f})")
                self.assertLessEqual(changed, MAX_CHANGED_SHARE, f"{name}: {changed:.2%} of pixels changed")

    def test_every_reference_has_a_fixture(self):
        if not os.path.isdir(GOLDEN_DIR):
            self.skipTest("no references yet")
        stale = sorted(f for f in os.listdir(GOLDEN_DIR) if f.endswith(".png") and f[:-4] not in FIXTURES)
        self.assertEqual(stale, [], "delete references whose fixture was removed")


if __name__ == "__main__":
    unittest.main()
