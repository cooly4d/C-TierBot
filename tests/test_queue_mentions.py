import asyncio
import io
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import leaderboard_bot as app


class QueueMentionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.bot_user = SimpleNamespace(id=123)
        self.enterContext(patch.object(app.bot._connection, "user", self.bot_user))
        self.enterContext(patch.object(app, "_queue_mention_busy", set()))
        self.enterContext(patch.object(app, "_queue_mention_cooldowns", {}))
        self.clock = MagicMock(return_value=100)
        self.enterContext(patch.object(app, "time", SimpleNamespace(monotonic=self.clock)))
        self.payload = self.enterContext(patch.object(app, "build_queue_stats_payload", new_callable=AsyncMock))
        self.payload.side_effect = lambda *_: (
            "Queue stats for NeatQueue#4607", discord.File(io.BytesIO(b"png"), filename="queue_stats_4607.png"),
            None, {"is_final": False},
        )
        self.process = self.enterContext(patch.object(app.bot, "process_commands", new_callable=AsyncMock))
        self.mark = self.enterContext(patch.object(app, "try_mark_match_processed"))
        self.updated = self.enterContext(patch.object(app, "update_guild_last_updated"))
        self.channels = self.enterContext(patch.object(app, "get_guild_queue_channels", return_value=[]))

    def message(self, name="queue-4607", content="<@123>", channel_id=55):
        channel = SimpleNamespace(id=channel_id, name=name, typing=MagicMock(return_value=AsyncMock()))
        return SimpleNamespace(
            guild=SimpleNamespace(id=10), channel=channel,
            author=SimpleNamespace(id=99, bot=False), webhook_id=None,
            content=content, mentions=[self.bot_user], embeds=[], reply=AsyncMock(),
        )

    async def test_ping_reuses_live_stats_without_setup_or_automatic_tracking(self):
        message = self.message(content="<@123> how are we doing?")
        await app.on_message(message)
        self.payload.assert_awaited_once_with("4607", 10)
        sent = message.reply.await_args.kwargs
        self.assertEqual(sent["file"].filename, "queue_stats_4607.png")
        self.assertEqual([item.custom_id for item in sent["view"].children], ["queue_result_verify"])
        self.assertEqual(sent["allowed_mentions"].to_dict(), {"parse": []})
        self.assertFalse(sent["mention_author"])
        self.mark.assert_not_called()
        self.updated.assert_not_called()
        self.channels.assert_not_called()
        self.process.assert_not_awaited()

    async def test_final_results_include_breakdown(self):
        self.payload.side_effect = None
        self.payload.return_value = ("Final stats", None, None, {"is_final": True})
        for content in ("stats <@!123>", "score?"):
            with self.subTest(content=content):
                app._queue_mention_cooldowns.clear()
                message = self.message(content=content)
                message.mentions = [self.bot_user] if "<@" in content else []
                await app.on_message(message)
                self.assertEqual([item.custom_id for item in message.reply.await_args.kwargs["view"].children],
                                 ["queue_result_games", "queue_result_verify"])

    async def test_score_question_without_mention_posts_live_stats(self):
        contents = ("score?", "Score?", "SCORE?", "  score? \n", "what is the score?",
                    "What's the Score? guys", "score??", "`score?`")
        for content in contents:
            with self.subTest(content=content):
                app._queue_mention_cooldowns.clear()
                message = self.message(content=content)
                message.mentions = []
                await app.on_message(message)
                self.payload.assert_awaited_with("4607", 10)
                sent = message.reply.await_args.kwargs
                self.assertEqual(sent["file"].filename, "queue_stats_4607.png")
                self.assertEqual([item.custom_id for item in sent["view"].children], ["queue_result_verify"])
        self.assertEqual(self.payload.await_count, len(contents))
        self.mark.assert_not_called()
        self.updated.assert_not_called()
        self.channels.assert_not_called()
        self.process.assert_not_awaited()

    async def test_score_question_ignores_other_text_and_channels(self):
        cases = [("queue-4607", text) for text in ("score", "what is the score", "scores?", "scoreboard?", "underscore?",
                                                     "score ?", "")]
        cases += [(name, "score?") for name in ("general", "queue-", "queue-4607-chat")]
        for name, content in cases:
            with self.subTest(name=name, content=content):
                message = self.message(name=name, content=content)
                message.mentions = []
                await app.on_message(message)
                message.reply.assert_not_awaited()
        self.payload.assert_not_awaited()

    async def test_score_question_still_ignores_bots_webhooks_and_dms(self):
        for changes in ({"author": SimpleNamespace(id=app.NEATQUEUE_BOT_ID, bot=True)},
                        {"webhook_id": 456}, {"guild": None}):
            with self.subTest(changes=changes):
                message = self.message(content="score?")
                message.mentions = []
                for key, value in changes.items():
                    setattr(message, key, value)
                await app.on_message(message)
                message.reply.assert_not_awaited()
        self.payload.assert_not_awaited()

    async def test_score_and_mentions_share_cooldown_in_both_directions(self):
        for first_content, next_content in (("score?", "<@123>"), ("<@123>", "score?")):
            with self.subTest(first=first_content):
                app._queue_mention_cooldowns.clear()
                self.payload.reset_mock()
                for content in (first_content, next_content):
                    message = self.message(content=content)
                    message.mentions = [self.bot_user] if "<@" in content else []
                    await app.on_message(message)
                self.assertEqual(self.payload.await_count, 1)
                message.reply.assert_not_awaited()
                message.channel.typing.assert_not_called()
                self.process.assert_not_awaited()

    async def test_only_exact_queue_names_with_numeric_ids(self):
        for name in ("queue", "queue-", "queue-4607-chat", "other-queue-4607", "queue-abc", "queue-４６０７"):
            with self.subTest(name=name):
                message = self.message(name=name)
                await app.on_message(message)
                message.reply.assert_not_awaited()
        self.payload.assert_not_awaited()
        for name in ("queue-7", "queue-10000"):
            app._queue_mention_cooldowns.clear()
            await app.on_message(self.message(name=name))
            self.assertEqual(self.payload.await_args.args[0], name.split("-")[1])

    async def test_ignores_bots_webhooks_dms_and_non_explicit_pings(self):
        variants = [
            {"author": SimpleNamespace(id=123, bot=True)},
            {"author": SimpleNamespace(id=app.NEATQUEUE_BOT_ID, bot=True)},
            {"webhook_id": 456}, {"guild": None}, {"mentions": []},
            {"content": "@ctierbot"}, {"content": "@everyone"}, {"content": "<@&123>"},
            {"content": "<@999>"}, {"content": "a reply with an implicit ping"},
        ]
        for changes in variants:
            with self.subTest(changes=changes):
                message = self.message()
                for key, value in changes.items():
                    setattr(message, key, value)
                await app.on_message(message)
                message.reply.assert_not_awaited()
        self.payload.assert_not_awaited()
        self.assertEqual(self.process.await_count, len(variants))

    async def test_voting_messages_do_not_trigger_mention_stats(self):
        message = self.message()
        message.author = SimpleNamespace(id=app.NEATQUEUE_BOT_ID, bot=True)
        message.embeds = [discord.Embed(title="🏆 Winner For Queue#4607 🏆")]
        for _ in range(3):
            await app.on_message(message)
        self.payload.assert_not_awaited()
        message.reply.assert_not_awaited()

    async def test_configured_automatic_result_route_still_runs(self):
        self.channels.return_value = [55]
        message = self.message(name="results")
        message.author = SimpleNamespace(id=app.NEATQUEUE_BOT_ID, bot=True)
        message.embeds = [discord.Embed(title="Winner For Queue#4607")]
        with patch.object(app, "post_queue_result", new_callable=AsyncMock) as post:
            await app.on_message(message)
        post.assert_awaited_once_with(message, "4607")
        self.payload.assert_not_awaited()

    async def test_cooldown_is_channel_scoped_and_expires(self):
        await app.on_message(self.message())
        blocked = self.message()
        blocked.author.id = 888
        self.clock.return_value = 110
        await app.on_message(blocked)
        blocked.reply.assert_not_awaited()
        blocked.channel.typing.assert_not_called()
        self.process.assert_not_awaited()
        self.assertEqual(self.payload.await_count, 1)
        await app.on_message(self.message(name="queue-4608", channel_id=56))
        self.assertEqual(self.payload.await_count, 2)
        self.clock.return_value = 130
        await app.on_message(self.message())
        self.assertEqual(self.payload.await_count, 3)

    async def test_overlapping_pings_only_run_one_lookup_even_after_30_seconds(self):
        entered, release = asyncio.Event(), asyncio.Event()

        async def slow_payload(*_):
            entered.set()
            await release.wait()
            return "stats", None, None, {"is_final": False}

        self.payload.side_effect = slow_payload
        first = asyncio.create_task(app.on_message(self.message()))
        try:
            await entered.wait()
            self.clock.return_value = 200
            duplicate = self.message(content="score?")
            duplicate.mentions = []
            await app.on_message(duplicate)
            duplicate.reply.assert_not_awaited()
            duplicate.channel.typing.assert_not_called()
            self.process.assert_not_awaited()
            self.assertEqual(self.payload.await_count, 1)
        finally:
            release.set()
            await first
        self.assertFalse(app._queue_mention_busy)

    async def test_lookup_error_is_replied_without_a_view(self):
        self.payload.side_effect = None
        self.payload.return_value = (None, None, "No linked player found.", {})
        message = self.message()
        await app.on_message(message)
        self.assertEqual(message.reply.await_args.args, ("No linked player found.",))
        self.assertNotIn("view", message.reply.await_args.kwargs)
        self.assertFalse(app._queue_mention_busy)

    async def test_unexpected_failure_releases_request_and_allows_retry(self):
        self.payload.side_effect = RuntimeError("private diagnostic")
        message = self.message()
        with patch("builtins.print") as log:
            await app.on_message(message)
        self.assertNotIn("private diagnostic", str(log.call_args))
        self.assertIn("try again", message.reply.await_args.args[0])
        self.assertFalse(app._queue_mention_busy)
        self.assertFalse(app._queue_mention_cooldowns)
        self.payload.side_effect = None
        self.payload.return_value = ("stats", None, None, {"is_final": False})
        await app.on_message(self.message())
        self.assertEqual(self.payload.await_count, 2)

    async def test_deleted_request_does_not_leave_queue_busy(self):
        message = self.message()
        message.reply.side_effect = discord.NotFound(SimpleNamespace(status=404, reason="Not Found"), "deleted")
        await app.on_message(message)
        self.assertFalse(app._queue_mention_busy)
        self.assertFalse(app._queue_mention_cooldowns)


if __name__ == "__main__":
    unittest.main()
