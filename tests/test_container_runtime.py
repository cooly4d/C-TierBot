"""Pieces the Coolify container relies on: DB_PATH, the SIGTERM handler, and the console.py socket."""
import asyncio
import io
import os
import signal
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import console
import db
import leaderboard_bot as app
from console_service import run_console_command_captured, start_console_socket_server


class DbPathTests(unittest.TestCase):
    def test_defaults_to_leaderboard_db_in_working_directory(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("DB_PATH", None)
            self.assertEqual(db.resolve_db_path(), "leaderboard.db")

    def test_env_path_creates_missing_parent_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, "data", "nested", "leaderboard.db")
            with patch.dict(os.environ, {"DB_PATH": target}):
                self.assertEqual(db.resolve_db_path(), target)
            self.assertTrue(os.path.isdir(os.path.dirname(target)))


class ShutdownSignalTests(unittest.TestCase):
    def test_registers_sigterm_handler(self):
        loop = MagicMock()
        self.assertTrue(app.install_shutdown_signal_handler(loop))
        self.assertEqual(loop.add_signal_handler.call_args.args[0], signal.SIGTERM)

    def test_loop_without_signal_support_is_skipped(self):
        loop = MagicMock()
        loop.add_signal_handler.side_effect = NotImplementedError
        self.assertFalse(app.install_shutdown_signal_handler(loop))


class ConsoleSocketTests(unittest.IsolatedAsyncioTestCase):
    async def test_captured_command_returns_output_instead_of_printing(self):
        with patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
            output = await run_console_command_captured(MagicMock(), "help")
        self.assertIn("send <channel_id> <message>", output)
        self.assertEqual(mock_stdout.getvalue(), "")

    async def test_console_client_round_trip(self):
        server = await start_console_socket_server(MagicMock(), 0)
        self.assertIsNotNone(server)
        port = server.sockets[0].getsockname()[1]
        try:
            help_output = await asyncio.to_thread(console.send_command, "help", port)
            unknown_output = await asyncio.to_thread(console.send_command, "nonsense", port)
        finally:
            server.close()
            await server.wait_closed()
        self.assertIn("C-TierBot Host Console Commands", help_output)
        self.assertIn("Unknown command: 'nonsense'", unknown_output)

    async def test_server_listens_on_loopback_only(self):
        server = await start_console_socket_server(MagicMock(), 0)
        try:
            self.assertEqual(server.sockets[0].getsockname()[0], "127.0.0.1")
        finally:
            server.close()
            await server.wait_closed()

    def test_client_reports_when_bot_is_not_listening(self):
        with patch("console.send_command", side_effect=ConnectionRefusedError), \
                patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
            self.assertFalse(console.run_one("status", 1))
        self.assertIn("Nothing is listening", mock_stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
