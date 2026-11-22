"""Pieces the Coolify container relies on: DB_PATH, the SIGTERM handler, and the console.py socket."""
import asyncio
import io
import os
import signal
import sqlite3
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


class BackupTests(unittest.TestCase):
    def test_backup_copies_data_and_keeps_newest(self):
        from datetime import datetime, timezone

        import backup_db

        with tempfile.TemporaryDirectory() as tmp:
            source = os.path.join(tmp, "leaderboard.db")
            conn = sqlite3.connect(source)
            conn.execute("CREATE TABLE t (x INTEGER)")
            conn.execute("INSERT INTO t VALUES (42)")
            conn.commit()
            conn.close()
            backup_dir = os.path.join(tmp, "backups")
            for day in range(1, 5):
                target = backup_db.backup_database(
                    source, backup_dir, keep=2, now=datetime(2026, 11, day, tzinfo=timezone.utc))
            kept = sorted(os.listdir(backup_dir))
            self.assertEqual(kept, ["leaderboard-20261103-000000.db", "leaderboard-20261104-000000.db"])
            copy = sqlite3.connect(target)
            try:
                self.assertEqual(copy.execute("SELECT x FROM t").fetchone()[0], 42)
            finally:
                copy.close()


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
