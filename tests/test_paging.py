"""/inventory and /market keep their paging state in each button's custom_id (whose data, which filter or mode,
which page) and read the owner's token from the database on every click, so their buttons survive a restart."""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord

import discord_ui
import leaderboard_bot


def component_interaction(custom_id, values=None):
    interaction = MagicMock()
    interaction.type = discord.InteractionType.component
    interaction.data = {"custom_id": custom_id}
    if values is not None:
        interaction.data["values"] = values
    return interaction


class PagingViewTests(unittest.IsolatedAsyncioTestCase):
    async def test_inventory_view_encodes_owner_filter_and_target_pages(self):
        view = discord_ui.InventoryPaginationView(42, 1, 3, "3", {"all": 30, "3": 9})

        self.assertEqual([c.custom_id for c in view.children], ["inv_prev:42:3:0", "inv_next:42:3:2", "inv_rarity:42"])
        self.assertEqual([c.disabled for c in view.children[:2]], [False, False])
        select = view.children[2]
        self.assertEqual([o.value for o in select.options if o.default], ["3"])
        self.assertIn("(9)", next(o.label for o in select.options if o.value == "3"))

    async def test_market_view_encodes_owner_mode_and_target_pages(self):
        view = discord_ui.ShopPaginationView(42, 1, 2, "daily")
        buttons = {c.label: c for c in view.children}

        self.assertEqual([buttons[label].custom_id for label in ("Daily", "Weekly", "All")],
                         ["mkt_mode:42:daily", "mkt_mode:42:weekly", "mkt_mode:42:all"])
        self.assertEqual(buttons["Daily"].style, discord.ButtonStyle.primary)
        self.assertEqual((buttons["◀ Previous"].custom_id, buttons["Next ▶"].custom_id),
                         ("mkt_prev:42:daily:0", "mkt_next:42:daily:1"))
        self.assertEqual((buttons["◀ Previous"].disabled, buttons["Next ▶"].disabled), (False, True))

    async def test_one_page_results_disable_both_buttons_and_keep_ids_unique(self):
        # Discord rejects a message whose components share a custom_id; on one page both buttons target page 0.
        for view in (discord_ui.InventoryPaginationView(42, 0, 1), discord_ui.ShopPaginationView(42, 0, 1)):
            with self.subTest(view=type(view).__name__):
                ids = [c.custom_id for c in view.children]
                self.assertEqual(len(ids), len(set(ids)))
                paging = [c for c in view.children
                          if isinstance(c, discord.ui.Button) and c.label in ("◀ Previous", "Next ▶")]
                self.assertEqual(len(paging), 2)
                self.assertTrue(all(c.disabled for c in paging))

    def test_custom_id_parsers(self):
        self.assertEqual(discord_ui.parse_inventory_page_id("inv_next:42:5:3"), (42, "5", 3))
        self.assertEqual(discord_ui.parse_inventory_page_id("inv_prev:42:bogus:0"), (42, "all", 0))
        self.assertEqual(discord_ui.parse_market_id("mkt_prev:42:weekly:1"), (42, "weekly", 1))
        self.assertEqual(discord_ui.parse_market_id("mkt_mode:42:daily"), (42, "daily", 0))
        self.assertEqual(discord_ui.parse_market_id("mkt_mode:42:bogus"), (42, "all", 0))


class PagingRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def test_clicks_route_with_the_state_from_the_custom_id(self):
        cases = [
            ("inv_next:42:3:2", None, "refresh_inventory_message", (42, "3", 2)),
            ("inv_rarity:42", ["4"], "refresh_inventory_message", (42, "4", 0)),
            ("mkt_prev:42:weekly:0", None, "refresh_market_message", (42, "weekly", 0)),
            ("mkt_mode:42:daily", None, "refresh_market_message", (42, "daily", 0)),
        ]
        for custom_id, values, handler, args in cases:
            interaction = component_interaction(custom_id, values)
            with self.subTest(custom_id=custom_id), patch.object(leaderboard_bot, handler, new=AsyncMock()) as refresh:
                await leaderboard_bot.log_interaction(interaction)
                refresh.assert_awaited_once_with(interaction, *args)

    async def test_buttons_on_messages_sent_before_the_change_say_the_menu_expired(self):
        for custom_id, command in (("inventory_next", "/inventory"), ("inventory_rarity", "/inventory"),
                                   ("market_prev", "/market"), ("market_mode_weekly", "/market")):
            interaction = component_interaction(custom_id)
            interaction.response.send_message = AsyncMock()
            with self.subTest(custom_id=custom_id):
                await leaderboard_bot.log_interaction(interaction)
                self.assertIn(command, interaction.response.send_message.await_args.args[0])
                self.assertTrue(interaction.response.send_message.await_args.kwargs["ephemeral"])


class PagingRefreshTests(unittest.IsolatedAsyncioTestCase):
    def interaction(self, owner):
        interaction = MagicMock()
        interaction.response.defer = AsyncMock()
        interaction.followup.send = AsyncMock()
        interaction.message.edit = AsyncMock()
        interaction.guild.get_member.return_value = owner
        return interaction

    async def test_inventory_refresh_reads_the_owner_and_token_fresh_on_each_click(self):
        owner = SimpleNamespace(id=42, display_name="Owner")
        interaction = self.interaction(owner)
        payload = AsyncMock(return_value=(
            "Owner's survev.de inventory", MagicMock(), None, 3, "inventory_42_p2.png", {"all": 30, "3": 9}))
        with patch.object(discord_ui, "get_user_token", return_value="tok-42") as get_token, \
                patch.object(discord_ui, "build_inventory_image_payload", new=payload):
            await discord_ui.refresh_inventory_message(interaction, 42, "3", 2)

        get_token.assert_called_once_with(42)
        payload.assert_awaited_once_with(owner, "tok-42", page=2, rarity_filter="3", use_cache=True)
        edit = interaction.message.edit.await_args.kwargs
        self.assertEqual(edit["embed"].title, "Owner's survev.de inventory (Rare)")
        self.assertEqual(edit["embed"].footer.text, "Page 3 of 3")
        self.assertEqual([c.custom_id for c in edit["view"].children][:2], ["inv_prev:42:3:1", "inv_next:42:3:2"])

    async def test_market_refresh_keeps_the_chosen_mode(self):
        owner = SimpleNamespace(id=42, display_name="Owner")
        interaction = self.interaction(owner)
        payload = AsyncMock(return_value=("Owner's Weekly Offers", MagicMock(), "shop_42_weekly_p0.png", None, 1))
        with patch.object(discord_ui, "get_user_token", return_value="tok-42"), \
                patch.object(discord_ui, "build_shop_image_payload", new=payload):
            await discord_ui.refresh_market_message(interaction, 42, "weekly", 0)

        payload.assert_awaited_once_with(owner, "tok-42", mode="weekly", page=0, use_cache=True)
        buttons = {c.label: c for c in interaction.message.edit.await_args.kwargs["view"].children}
        self.assertEqual(buttons["Weekly"].style, discord.ButtonStyle.primary)

    async def test_an_unlinked_owner_gets_a_private_notice_instead_of_an_edit(self):
        interaction = self.interaction(SimpleNamespace(id=42, display_name="Owner"))
        payload = AsyncMock()
        with patch.object(discord_ui, "get_user_token", return_value=None), \
                patch.object(discord_ui, "build_shop_image_payload", new=payload):
            await discord_ui.refresh_market_message(interaction, 42, "all", 0)

        payload.assert_not_awaited()
        interaction.message.edit.assert_not_awaited()
        self.assertIn("no longer linked", interaction.followup.send.await_args.args[0])
        self.assertTrue(interaction.followup.send.await_args.kwargs["ephemeral"])


class PagingCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_inventory_command_sends_buttons_for_the_requested_member(self):
        member = SimpleNamespace(id=77, display_name="Member", mention="<@77>")
        interaction = MagicMock()
        interaction.user = SimpleNamespace(id=5)
        interaction.response.defer = AsyncMock()
        interaction.followup.send = AsyncMock()
        payload = AsyncMock(return_value=("Member's survev.de inventory", MagicMock(), None, 2, "inventory_77_p0.png", None))
        with patch.object(leaderboard_bot, "get_user_token", return_value="tok-77"), \
                patch.object(discord_ui, "build_inventory_image_payload", new=payload):
            await leaderboard_bot.inventory.callback(interaction, member)

        sent = interaction.followup.send.await_args.kwargs
        self.assertEqual([c.custom_id for c in sent["view"].children], ["inv_prev:77:all:0", "inv_next:77:all:1", "inv_rarity:77"])
        self.assertEqual(sent["embed"].footer.text, "Page 1 of 2")


if __name__ == "__main__":
    unittest.main()
