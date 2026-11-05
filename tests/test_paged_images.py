import asyncio
import io
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from PIL import Image

import discord_ui
import image_utils
import leaderboard_service

ITEMS = [{"type": "outfitBase", "name": f"Skin {i}", "rarity": 1, "value": 100 + i} for i in range(30)]


class InventoryPageCacheTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        leaderboard_service._inventory_cache.clear()
        leaderboard_service._rendered_page_cache.clear()
        self.owner = SimpleNamespace(id=42, display_name="Owner")
        self.inventory = {"username": "Owner", "items": ITEMS}
        leaderboard_service._inventory_cache[42] = self.inventory
        self.rendered = []

        def fake_render(username, items, page, rarity):
            self.rendered.append((rarity, page))
            return io.BytesIO(f"png-{rarity}-{page}".encode())

        patcher = patch.object(leaderboard_service, "generate_inventory_image", side_effect=fake_render)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(leaderboard_service._inventory_cache.clear)
        self.addCleanup(leaderboard_service._rendered_page_cache.clear)

    async def page(self, page, rarity="all"):
        payload = await leaderboard_service.build_inventory_image_payload(
            self.owner, "tok", page=page, rarity_filter=rarity, use_cache=True)
        await asyncio.sleep(0.05)  # let the background neighbour renders finish
        return payload

    async def test_neighbouring_pages_are_rendered_ahead_and_reused(self):
        _, file, _, total_pages, _, _ = await self.page(0)
        self.assertEqual(total_pages, 3)
        self.assertEqual(file.fp.read(), b"png-all-0")
        self.assertEqual(sorted(self.rendered), [("all", 0), ("all", 1)])

        _, file, *_ = await self.page(1)
        self.assertEqual(file.fp.read(), b"png-all-1")
        self.assertEqual(sorted(self.rendered), [("all", 0), ("all", 1), ("all", 2)])  # page 1 was ready; 2 queued

        await self.page(0)
        self.assertEqual(len(self.rendered), 3)  # nothing re-rendered

    async def test_a_freshly_fetched_inventory_is_drawn_again(self):
        await self.page(0)
        leaderboard_service._inventory_cache[42] = {"username": "Owner", "items": ITEMS[:5]}
        await self.page(0)
        self.assertEqual(self.rendered.count(("all", 0)), 2)

    async def test_a_failed_render_is_retried_on_the_next_click(self):
        with patch.object(leaderboard_service, "generate_inventory_image", side_effect=OSError("disk")):
            with self.assertRaises(OSError):
                await self.page(2)
        await self.page(2)
        self.assertIn(("all", 2), self.rendered)


MARKET = {"username": "Owner", "balance": 4321, "offers": [
    {"slot": 0, "items": [{"type": "outfitBase"}], "price": 100, "purchased": False},
    {"slot": 1, "items": [{"type": "outfitBase"}] * 4, "price": 900, "purchased": True},
    {"slot": 2, "items": [{"type": "outfitBase"}], "price": 300, "purchased": False},
    {"slot": 3, "items": [{"type": "outfitBase"}] * 4, "price": 2000, "purchased": False},
]}


class MarketPageCacheTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        leaderboard_service._market_cache.clear()
        leaderboard_service._rendered_page_cache.clear()
        leaderboard_service._market_cache[42] = MARKET
        self.rendered = []

        def fake_render(username, market_data, mode, page):
            self.rendered.append((mode, page))
            return io.BytesIO(f"png-{mode}-{page}".encode())

        patcher = patch.object(leaderboard_service, "generate_shop_image", side_effect=fake_render)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(leaderboard_service._market_cache.clear)
        self.addCleanup(leaderboard_service._rendered_page_cache.clear)

    async def test_the_other_modes_are_ready_before_they_are_clicked(self):
        owner = SimpleNamespace(id=42, display_name="Owner")
        _, file, *_ = await leaderboard_service.build_shop_image_payload(owner, "tok", mode="all", use_cache=True)
        await asyncio.sleep(0.05)
        self.assertEqual(file.fp.read(), b"png-all-0")
        self.assertEqual(sorted(self.rendered), [("all", 0), ("daily", 0), ("weekly", 0)])

        _, file, *_ = await leaderboard_service.build_shop_image_payload(owner, "tok", mode="weekly", use_cache=True)
        await asyncio.sleep(0.05)
        self.assertEqual(file.fp.read(), b"png-weekly-0")
        self.assertEqual(len(self.rendered), 3)  # nothing drawn twice


class InventoryClickTests(unittest.IsolatedAsyncioTestCase):
    def interaction(self, owner):
        interaction = MagicMock()
        interaction.response.is_done.return_value = False
        interaction.response.defer = AsyncMock()
        interaction.response.edit_message = AsyncMock()
        interaction.message.edit = AsyncMock()
        interaction.guild.get_member.return_value = owner
        return interaction

    async def test_a_cached_inventory_is_answered_with_the_edit_itself(self):
        owner = SimpleNamespace(id=42, display_name="Owner")
        interaction = self.interaction(owner)
        payload = AsyncMock(return_value=("Owner's survev.de inventory", MagicMock(), None, 3, "inventory_42_p1.png", None))
        with patch.object(discord_ui, "has_cached_inventory", return_value=True), \
                patch.object(discord_ui, "get_user_token", return_value="tok-42"), \
                patch.object(discord_ui, "build_inventory_image_payload", new=payload):
            await discord_ui.refresh_inventory_message(interaction, 42, "all", 1)

        interaction.response.defer.assert_not_awaited()
        interaction.message.edit.assert_not_awaited()
        edit = interaction.response.edit_message.await_args.kwargs
        self.assertEqual(edit["embed"].footer.text, "Page 2 of 3")

    async def test_an_unlinked_owner_is_told_privately_before_any_answer(self):
        interaction = self.interaction(SimpleNamespace(id=42, display_name="Owner"))
        interaction.response.send_message = AsyncMock()
        with patch.object(discord_ui, "has_cached_inventory", return_value=True), \
                patch.object(discord_ui, "get_user_token", return_value=None):
            await discord_ui.refresh_inventory_message(interaction, 42, "all", 1)

        self.assertIn("no longer linked", interaction.response.send_message.await_args.args[0])
        self.assertTrue(interaction.response.send_message.await_args.kwargs["ephemeral"])


class MarketClickTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_cached_market_is_answered_with_the_edit_itself(self):
        owner = SimpleNamespace(id=42, display_name="Owner")
        interaction = InventoryClickTests.interaction(None, owner)
        payload = AsyncMock(return_value=("Owner's Weekly Offers", MagicMock(), "shop_42_weekly_p0.png", None, 1))
        with patch.object(discord_ui, "has_cached_market", return_value=True),                 patch.object(discord_ui, "get_user_token", return_value="tok-42"),                 patch.object(discord_ui, "build_shop_image_payload", new=payload):
            await discord_ui.refresh_market_message(interaction, 42, "weekly", 0)

        interaction.response.defer.assert_not_awaited()
        interaction.message.edit.assert_not_awaited()
        interaction.response.edit_message.assert_awaited_once()


class InventoryHeaderTests(unittest.TestCase):
    def test_header_shows_the_whole_inventorys_value_even_when_filtered(self):
        items = ITEMS + [{"type": "outfitBase", "name": "Old", "rarity": 4, "value": None, "pricePaid": 50}]
        with patch.object(image_utils, "draw_fries_amount", wraps=image_utils.draw_fries_amount) as amounts:
            image_utils.generate_inventory_image("Owner", items, 0, "4")
        header = [c for c in amounts.call_args_list if c.kwargs.get("align") == "right"]
        self.assertEqual(len(header), 1)
        self.assertEqual(header[0].args[3], sum(100 + i for i in range(30)) + 50)

    def test_the_page_is_a_compact_palette_png(self):
        image = Image.open(image_utils.generate_inventory_image("Owner", ITEMS[:12]))
        self.assertEqual(image.mode, "P")
        self.assertEqual(image.height, image_utils.INVENTORY_HEADER_HEIGHT + 32
                         + 3 * image_utils.INVENTORY_CARD_HEIGHT + 2 * image_utils.INVENTORY_CARD_GAP
                         + image_utils.INVENTORY_FOOTER_HEIGHT)

    def test_market_header_shows_the_balance_and_the_page_is_a_palette_png(self):
        with patch.object(image_utils, "draw_fries_amount", wraps=image_utils.draw_fries_amount) as amounts:
            image = Image.open(image_utils.generate_shop_image("Owner", MARKET, "all", 0))
        header = [c for c in amounts.call_args_list if c.kwargs.get("align") == "right"]
        self.assertEqual([c.args[3] for c in header], [4321])
        self.assertEqual(image.mode, "P")
        self.assertEqual(image.height, image_utils.INVENTORY_HEADER_HEIGHT + 32 + 2 * image_utils.SHOP_CARD_HEIGHT
                         + image_utils.SHOP_CARD_GAP + image_utils.INVENTORY_FOOTER_HEIGHT)


if __name__ == "__main__":
    unittest.main()
