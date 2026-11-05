import io
import os
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw

import image_utils


def _render(generate, *args):
    return Image.open(io.BytesIO(generate(*args).getvalue())).convert("RGB")


class ItemIconAssetTests(unittest.TestCase):
    """The exported icon set in assets/items (tools/export_item_icons.mts)."""

    def test_manifest_and_icon_files_match(self):
        manifest = image_utils.load_item_manifest()
        self.assertGreater(len(manifest), 100)
        pngs = {name[:-4] for name in os.listdir(image_utils.ITEM_ICON_DIR)
                if name.endswith(".png") and not name.startswith("_")}
        self.assertEqual(pngs, set(manifest))
        for item_type, info in manifest.items():
            self.assertTrue(info.get("name"), item_type)
            self.assertIn(info.get("kind"), {"outfit", "melee", "emote", "heal_effect", "boost_effect",
                                             "death_effect", "crate_key", "crosshair"}, item_type)

    def test_known_icon_is_a_square_rgba_image(self):
        icon = image_utils.load_item_icon("outfitBase")
        self.assertIsNotNone(icon)
        self.assertEqual(icon.mode, "RGBA")
        self.assertEqual(icon.size[0], icon.size[1])

    def test_untrusted_types_never_reach_the_file_system(self):
        for bad in (None, "", "../leaderboard", "a/b", "a\\b", "_golden_fries", "x" * 65, 42, "outfit.png"):
            with self.subTest(bad=bad):
                self.assertIsNone(image_utils.load_item_icon(bad))
        self.assertIsNone(image_utils.load_item_icon("noSuchItem"))

    def test_shop_labels_use_the_item_name_with_a_readable_fallback(self):
        self.assertEqual(image_utils.prettify_shop_item_type("outfitBase"), "Basic Outfit")
        self.assertEqual(image_utils.prettify_shop_item_type("brand_new_item"), "Brand New Item")
        self.assertEqual(image_utils.prettify_shop_item_type(None), "Unknown")
        self.assertIsNone(image_utils.shop_item_rarity("brand_new_item"))


class GroupInventoryItemsTests(unittest.TestCase):
    def test_groups_keep_the_item_type_for_the_icon(self):
        grouped = image_utils.group_inventory_items([
            {"name": "Galaxy", "rarity": 4, "value": 900},
            {"type": "outfitGalaxy", "name": "Galaxy", "rarity": 4, "value": 900},
        ])
        self.assertEqual(grouped, [{"name": "Galaxy", "rarity": 4, "count": 2, "value": 900, "type": "outfitGalaxy"}])


class CardIconTests(unittest.TestCase):
    def test_inventory_card_draws_the_icon_when_there_is_one(self):
        item = {"name": "Basic Outfit", "rarity": 1, "value": 100}
        with_icon = _render(image_utils.generate_inventory_image, "Awang", [{**item, "type": "outfitBase"}])
        without = _render(image_utils.generate_inventory_image, "Awang", [{**item, "type": "noSuchItem"}])
        self.assertEqual(with_icon.size, without.size)
        x0 = image_utils.QUEUE_IMG_PADDING
        y0 = image_utils.QUEUE_IMG_HEADER_HEIGHT + image_utils.QUEUE_IMG_PADDING
        card_width = (image_utils.QUEUE_IMG_WIDTH - 2 * x0 - image_utils.INVENTORY_CARD_GAP * 3) // 4
        size = image_utils.INVENTORY_ICON_SIZE
        icon_box = (x0 + card_width - 14 - size, y0 + image_utils.INVENTORY_CARD_HEIGHT - 12 - size,
                    x0 + card_width - 14, y0 + image_utils.INVENTORY_CARD_HEIGHT - 12)
        self.assertNotEqual(with_icon.crop(icon_box).tobytes(), without.crop(icon_box).tobytes())
        self.assertEqual(len(without.crop(icon_box).getcolors()), 1)  # just the card background

    def test_market_renders_singles_bundles_and_unknown_items(self):
        market = {"balance": 1234, "offers": [
            {"slot": 0, "items": [{"type": "outfitBase"}], "price": 100, "purchased": False},
            {"slot": 1, "items": [{"type": "outfitBase"}, {"type": "noSuchItem"}, {"type": "outfitBase"},
                                  {"type": "outfitBase"}], "price": 900, "purchased": True},
            {"slot": 7, "items": [], "price": None, "purchased": False},
        ]}
        image = _render(image_utils.generate_shop_image, "Awang", market)
        self.assertEqual(image.width, image_utils.QUEUE_IMG_WIDTH)

    def test_amounts_fall_back_to_the_word_fries_without_the_icon(self):
        image = Image.new("RGB", (300, 60))
        draw = ImageDraw.Draw(image)
        with patch.object(image_utils, "_load_fries_icon", return_value=None), \
                patch.object(draw, "text", wraps=draw.text) as text:
            image_utils.draw_fries_amount(image, draw, (0, 0), 1500, image_utils.load_font(20), (255, 255, 255))
        self.assertEqual(text.call_args.args[1], "1,500 fries")


if __name__ == "__main__":
    unittest.main()
