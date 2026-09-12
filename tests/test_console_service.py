import asyncio
import io
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from console_service import _format_table, execute_console_command


class ConsoleServiceTests(unittest.IsolatedAsyncioTestCase):
    def test_format_table(self):
        headers = ["ColA", "ColB"]
        rows = [["Val1", "Val2"], ["LongerVal", "X"]]
        table = _format_table(headers, rows)
        self.assertIn("ColA", table)
        self.assertIn("LongerVal", table)
        self.assertIn("-+-", table)

    async def test_execute_help(self):
        bot = MagicMock()
        with patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
            await execute_console_command(bot, "help")
            output = mock_stdout.getvalue()
            self.assertIn("send <channel_id> <message>", output)
            self.assertIn("dm <user_id> <message>", output)
            self.assertIn("status", output)

    async def test_execute_send_invalid_id(self):
        bot = MagicMock()
        with patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
            await execute_console_command(bot, "send not_a_number hello")
            output = mock_stdout.getvalue()
            self.assertIn("Invalid channel ID", output)

    async def test_execute_send_valid(self):
        bot = MagicMock()
        mock_channel = MagicMock(spec=discord.TextChannel)
        mock_channel.name = "general"
        mock_channel.guild.name = "MyGuild"
        mock_channel.send = AsyncMock(return_value=MagicMock(id=12345))
        bot.get_channel.return_value = mock_channel

        with patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
            await execute_console_command(bot, "send 112233 Hello World")
            output = mock_stdout.getvalue()
            self.assertIn("Sent message", output)
            self.assertIn("#general", output)
            mock_channel.send.assert_awaited_once_with("Hello World")

    async def test_execute_dm(self):
        bot = MagicMock()
        mock_user = MagicMock()
        mock_user.name = "JohnDoe"
        mock_user.id = 998877
        mock_user.send = AsyncMock(return_value=MagicMock(id=67890))
        bot.get_user.return_value = mock_user

        with patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
            await execute_console_command(bot, "dm 998877 Private greeting")
            output = mock_stdout.getvalue()
            self.assertIn("DM sent", output)
            self.assertIn("JohnDoe", output)
            mock_user.send.assert_awaited_once_with("Private greeting")

    async def test_execute_servers(self):
        bot = MagicMock()
        mock_guild = MagicMock()
        mock_guild.name = "Test Guild"
        mock_guild.id = 5555
        mock_guild.member_count = 42
        bot.guilds = [mock_guild]

        with patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
            await execute_console_command(bot, "servers")
            output = mock_stdout.getvalue()
            self.assertIn("Test Guild", output)
            self.assertIn("5555", output)
            self.assertIn("42", output)

    async def test_execute_exit(self):
        bot = MagicMock()
        bot.close = AsyncMock()
        with patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
            await execute_console_command(bot, "exit")
            output = mock_stdout.getvalue()
            self.assertIn("Shutting down bot gracefully", output)
            bot.close.assert_awaited_once()

    async def test_execute_sql(self):
        bot = MagicMock()
        with patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
            await execute_console_command(bot, "sql SELECT 1 AS test_col")
            output = mock_stdout.getvalue()
            self.assertIn("test_col", output)
            self.assertIn("1 rows", output)


if __name__ == "__main__":
    unittest.main()
