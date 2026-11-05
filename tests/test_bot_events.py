"""Discord event handlers and slash commands in leaderboard_bot that other test modules don't cover."""
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord

import discord_ui
import leaderboard_bot as app
from bot_config import NEATQUEUE_BOT_ID

BOT_USER_ID = 123
TRACKED_CHANNEL_ID = 55


def neatqueue_message(title, author_id=NEATQUEUE_BOT_ID, channel_id=TRACKED_CHANNEL_ID):
    return SimpleNamespace(
        author=SimpleNamespace(id=author_id),
        guild=SimpleNamespace(id=10),
        channel=SimpleNamespace(id=channel_id),
        embeds=[discord.Embed(title=title)],
        created_at=datetime.now(timezone.utc),
        edited_at=datetime.now(timezone.utc),
    )


class RawMessageEditTests(unittest.IsolatedAsyncioTestCase):
    """NeatQueue fills in results by editing its messages, often long after they left discord.py's cache."""

    def setUp(self):
        self.enterContext(patch.object(app.bot._connection, "user", SimpleNamespace(id=BOT_USER_ID)))
        self.enterContext(patch.object(app, "get_guild_queue_channels", return_value=[TRACKED_CHANNEL_ID]))
        self.post = self.enterContext(patch.object(app, "post_queue_result", new_callable=AsyncMock))
        self.channel = MagicMock()
        self.get_channel = self.enterContext(patch.object(app.bot, "get_channel", return_value=self.channel))

    async def edit(self, message=None, guild_id=10, fetch_error=None):
        self.channel.fetch_message = AsyncMock(return_value=message, side_effect=fetch_error)
        await app.on_raw_message_edit(SimpleNamespace(guild_id=guild_id, channel_id=TRACKED_CHANNEL_ID, message_id=999))

    async def test_an_edited_winner_announcement_or_results_panel_posts_the_queue(self):
        for title in ("🏆 Winner For Queue#4620 🏆", "Results for Queue#4620"):
            self.post.reset_mock()
            with self.subTest(title=title):
                message = neatqueue_message(title)
                await self.edit(message)
                self.post.assert_awaited_once_with(message, "4620")

    async def test_untracked_channels_other_authors_and_the_bot_itself_are_ignored(self):
        for message in (neatqueue_message("Results for Queue#4620", channel_id=77),
                        neatqueue_message("Results for Queue#4620", author_id=999),
                        neatqueue_message("Results for Queue#4620", author_id=BOT_USER_ID),
                        neatqueue_message("Queue#4620 voting")):
            with self.subTest(author=message.author.id, channel=message.channel.id, title=message.embeds[0].title):
                await self.edit(message)
        self.post.assert_not_awaited()

    async def test_a_message_that_cannot_be_fetched_is_skipped(self):
        await self.edit(fetch_error=discord.NotFound(MagicMock(status=404), "Unknown Message"))
        self.post.assert_not_awaited()

    async def test_edits_outside_a_server_are_ignored_without_any_lookup(self):
        await self.edit(neatqueue_message("Results for Queue#4620"), guild_id=None)
        self.get_channel.assert_not_called()
        self.post.assert_not_awaited()


class SlashCommandSetTests(unittest.TestCase):
    def test_leaderboards_and_profile_replace_the_old_commands(self):
        names = {command.name for command in app.bot.tree.get_commands()}
        self.assertTrue({"leaderboard", "leaderboard_season", "leaderboard_fries", "profile"} <= names)
        self.assertFalse({"leaderboard_weekly", "leaderboard_monthly", "history"} & names)


class LeaderboardCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_leaderboard_opens_on_weekly_kills_with_period_buttons(self):
        interaction = MagicMock()
        interaction.response.defer = AsyncMock()
        interaction.followup.send = AsyncMock()
        embed = discord.Embed(title="🏆 Server Weekly Leaderboard")
        with patch.object(app, "generate_leaderboard_embed", new=AsyncMock(return_value=embed)) as build:
            await app.leaderboard.callback(interaction)

        build.assert_awaited_once_with("Weekly", 7, "kills")
        view = interaction.followup.send.await_args.kwargs["view"]
        self.assertEqual([c.custom_id for c in view.children], ["leaderboard_weekly", "leaderboard_monthly", "leaderboard_sort"])
        self.assertEqual(view.weekly_button.style, discord.ButtonStyle.primary)


class ProfileCommandTests(unittest.IsolatedAsyncioTestCase):
    def interaction(self, guild_id=10):
        interaction = MagicMock()
        interaction.guild_id = guild_id
        interaction.user = SimpleNamespace(id=101, display_name="Awang")
        interaction.response.defer = AsyncMock()
        interaction.followup.send = AsyncMock()
        return interaction

    async def test_profile_with_server_history_sends_the_card_and_trend_buttons(self):
        interaction = self.interaction()
        embed, file = discord.Embed(title="Awang's NeatQueue stats"), MagicMock()
        payload = AsyncMock(return_value=(embed, file, "damage", None))
        with patch.object(app, "build_profile_payload", new=payload), patch.object(app, "get_user_token", return_value="tok"):
            await app.profile.callback(interaction, None)

        payload.assert_awaited_once_with(interaction.user, "tok", 10)
        sent = interaction.followup.send.await_args.kwargs
        self.assertIs(sent["file"], file)
        self.assertEqual([c.custom_id for c in sent["view"].children],
                         ["history_metric:damage:10:101", "history_metric:impact:10:101", "history_metric:kills:10:101"])
        self.assertIsInstance(sent["view"], discord_ui.ProfileMetricView)

    async def test_profile_without_history_sends_only_the_stat_fields(self):
        interaction = self.interaction(guild_id=None)
        embed = discord.Embed(title="Awang's NeatQueue stats")
        with patch.object(app, "build_profile_payload", new=AsyncMock(return_value=(embed, None, "damage", None))), \
                patch.object(app, "get_user_token", return_value=None):
            await app.profile.callback(interaction, None)

        self.assertEqual(interaction.followup.send.await_args.kwargs, {"embed": embed})

    async def test_nothing_to_show_sends_the_message(self):
        interaction = self.interaction()
        with patch.object(app, "build_profile_payload", new=AsyncMock(return_value=(None, None, "damage", "No cached"))), \
                patch.object(app, "get_user_token", return_value=None):
            await app.profile.callback(interaction, None)

        interaction.followup.send.assert_awaited_once_with("No cached")


if __name__ == "__main__":
    unittest.main()
