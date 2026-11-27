"""Website lifecycle in the bot process, retention, diagnostic log writes, snapshot v2 linkage, export, preview."""
import asyncio
import json
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import db
import leaderboard_bot as app
import queue_stats_service as qss
import web_queries
import web_server
try:
    import web_fixture as f
except ImportError:  # run as tests.test_web_lifecycle
    from tests import web_fixture as f
from tools import export_preview_db

ROOT = Path(__file__).resolve().parent.parent


class LifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmp.name) / "site.db")
        f.standard_db(self.path).close()

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def test_port_zero_starts_nothing(self):
        self.assertIsNone(await web_server.start_web_server(0, f.HOME, self.path))

    async def test_an_occupied_port_is_logged_and_the_bot_carries_on(self):
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        port = sock.getsockname()[1]
        try:
            with self.assertLogs("web_server", level="ERROR"):
                server = await web_server.start_web_server(port, f.HOME, self.path, host="127.0.0.1")
            self.assertIsNone(server)
            reached = True  # the stand-in for bot.start() runs
        finally:
            sock.close()
        self.assertTrue(reached)

    async def test_shutdown_finishes_within_the_deadline_with_work_in_flight(self):
        server = await web_server.start_web_server(18765, f.HOME, self.path, host="127.0.0.1")
        self.assertIsNotNone(server)
        site = server.site

        def python_work(conn):  # Python between queries, checking the deadline between units as every job does
            while True:
                web_queries.checkpoint()
                time.sleep(0.01)

        blocked = asyncio.ensure_future(site.db.run(python_work))
        endless = asyncio.ensure_future(site.db.run(lambda conn: conn.execute(
            "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c) SELECT COUNT(*) FROM c").fetchone()))
        reader, writer = await asyncio.open_connection("127.0.0.1", 18765)
        writer.write(b"GET /queues/4821 HTTP/1.1\r\nHost: x\r\n")  # a request held open, never finished
        await writer.drain()
        await asyncio.sleep(0.2)
        start = time.monotonic()
        await server.stop(time.monotonic() + 5)
        self.assertLess(time.monotonic() - start, 5.5)
        self.assertFalse([fut for fut in site.db.running if not fut.done()])  # every worker has really exited
        with self.assertRaises(web_server.Busy):
            await endless  # aborted by the stopping flag
        with self.assertRaises(web_server.Busy):
            await blocked
        writer.close()

    async def test_sigterm_starts_one_shutdown_whose_clock_starts_at_the_signal(self):
        app._shutdown_task = None
        fake_web = type("W", (), {})()
        fake_web.begin_stop = lambda: setattr(fake_web, "stopped_at", time.monotonic())
        fake_web.stop = AsyncMock()
        app._web_server = fake_web

        async def slow_close():
            await asyncio.sleep(3)

        try:
            with patch.object(app.bot, "close", side_effect=slow_close):
                signalled = time.monotonic()
                task = app.request_shutdown()
                self.assertIs(app.request_shutdown(), task)  # idempotent
                await task
            done = time.monotonic()
            self.assertLess(fake_web.stopped_at - signalled, 0.1)  # admission stops at once
            self.assertLess(done - signalled, 5)
            deadline = fake_web.stop.await_args.args[0]
            self.assertAlmostEqual(deadline, signalled + 5, delta=0.2)
        finally:
            app._shutdown_task = None
            app._web_server = None

    async def test_main_passes_the_home_guild_constant(self):
        import bot_config
        with patch.object(app, "WEB_PORT", 8080), \
                patch("web_server.start_web_server", new=AsyncMock(return_value=None)) as start:
            await app.start_website()
        self.assertEqual(start.await_args.args[1], bot_config.HOME_GUILD_ID)
        self.assertEqual(bot_config.HOME_GUILD_ID, 1363265113880264745)


class RetentionTests(unittest.TestCase):
    def test_normalize_retention_days(self):
        self.assertEqual(db.normalize_retention_days("90"), 90)
        self.assertEqual(db.normalize_retention_days("0"), 0)
        self.assertEqual(db.normalize_retention_days(0), 0)
        for bad in ("-1", -1, "abc", "7776000", 7776000, "1.5", None, True):
            with self.assertLogs("db", level="ERROR"):
                self.assertEqual(db.normalize_retention_days(bad), 0, bad)

    def test_snapshot_cutoff(self):
        now = datetime(2026, 11, 26, tzinfo=timezone.utc)
        self.assertIsNone(db.snapshot_cutoff(now, 0))
        self.assertEqual(db.snapshot_cutoff(now, 90), (now - timedelta(days=90)).isoformat())
        self.assertIsNone(db.snapshot_cutoff(now, 10 ** 9))

    def test_zero_and_bad_values_keep_old_and_new_snapshots(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "r.db")
            conn = sqlite3.connect(path)
            conn.execute(db.QUEUE_RESULTS_SCHEMA)
            old = (datetime.now(timezone.utc) - timedelta(days=400)).isoformat()
            conn.execute("INSERT INTO queue_results VALUES (10, 'old', '{}', ?)", (old,))
            conn.commit()
            conn.close()
            with patch.object(db, "DB_PATH", path), patch.object(db.sqlite3, "connect", side_effect=_closing_connect):
                for value in (0, "-1", "abc", 7776000):
                    watch = self.assertNoLogs("db", level="ERROR") if value == 0 else self.assertLogs("db", level="ERROR")
                    with watch:
                        db.save_queue_result(10, f"new{value}", {"games": []}, retention_days=value)
            remaining = _REAL_CONNECT(path)
            ids = sorted(r[0] for r in remaining.execute("SELECT match_id FROM queue_results"))
            remaining.close()
            self.assertIn("old", ids)
            self.assertEqual(len(ids), 5)

    def test_unavailable_text_drops_the_day_count_when_keeping_forever(self):
        with patch.object(qss, "QUEUE_RESULT_RETENTION_DAYS", 0):
            self.assertNotIn("days", qss.queue_games_unavailable("4821"))
        with patch.object(qss, "QUEUE_RESULT_RETENTION_DAYS", 90):
            self.assertIn("90 days", qss.queue_games_unavailable("4821"))


_REAL_CONNECT = sqlite3.connect


def _closing_connect(*args, **kwargs):
    from contextlib import contextmanager

    @contextmanager
    def cm():
        connection = _REAL_CONNECT(*args, **kwargs)
        try:
            with connection:
                yield connection
        finally:
            connection.close()
    return cm()


class LogWriteTests(unittest.TestCase):
    def test_logs_are_written_atomically_and_stale_tmp_files_are_pruned(self):
        with tempfile.TemporaryDirectory() as tmp:
            stale = Path(tmp, "queue_stats_old.json.tmp")
            stale.write_text("{")
            os.utime(stale, (time.time() - 7200, time.time() - 7200))
            fresh = Path(tmp, "queue_stats_new.json.tmp")
            fresh.write_text("{")
            with patch.object(qss, "QUEUE_STATS_LOG_DIR", tmp):
                qss.write_queue_stats_log("4821", None, None, trigger="auto_post", guild_id=1, channel_id=2)
            names = sorted(os.listdir(tmp))
            json_files = [n for n in names if n.endswith(".json")]
            self.assertEqual(len(json_files), 1)
            json.loads(Path(tmp, json_files[0]).read_text())
            self.assertNotIn("queue_stats_old.json.tmp", names)
            self.assertIn("queue_stats_new.json.tmp", names)  # under an hour old: left alone

    def test_a_failed_write_leaves_no_json_file(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(qss, "QUEUE_STATS_LOG_DIR", tmp), \
                patch.object(qss.json, "dump", side_effect=ValueError("boom")):
            with self.assertRaises(ValueError):
                qss.write_queue_stats_log("4821", None, None, trigger="auto_post", guild_id=1, channel_id=2)
            self.assertEqual(os.listdir(tmp), [])


class SnapshotV2Tests(unittest.TestCase):
    def test_a_logged_out_namesake_never_takes_a_linked_players_name_or_link(self):
        entry = {"discord_id": 9, "username": "raven", "slug": "raven", "display_name": "RavenNick", "guest": False}
        match_result = {"teams": [[entry]], "individual_games": [{"game_number": 1, "winning_display_team_index": 0, "players": [
            {"identity": "raven", "username": "raven", "slug": "raven", "display_team_index": 0, "kills": 3, "damage_dealt": 900, "impact_score": 70},
            {"identity": "raven", "username": "Raven", "slug": None, "display_team_index": 0, "kills": 1, "damage_dealt": 200, "impact_score": None},
        ]}]}
        rows = qss.build_queue_snapshot("1", match_result)["games"][0]["players"]
        self.assertEqual(qss.build_queue_snapshot("1", match_result)["v"], 2)
        linked, guest = rows
        self.assertEqual((linked["key"], linked["discord_id"], linked["name"], linked["guest"]), ("slug:raven", 9, "RavenNick", False))
        self.assertEqual((guest["key"], guest["discord_id"], guest["name"], guest["guest"]), ("name:raven#1", None, "Raven", True))


class ExportTests(unittest.TestCase):
    def test_the_export_holds_no_token_and_only_the_needed_tables(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = str(Path(tmp) / "live.db")
            f.standard_db(src).close()
            out = Path(tmp) / "out"
            out.mkdir()
            target, counts, tokens = export_preview_db.export(src, str(out))
            data = Path(target).read_bytes()
            self.assertIn(f.CANARY_TOKEN, tokens)
            self.assertNotIn(f.CANARY_TOKEN.encode(), data)
            self.assertEqual(counts["users"], 4)
            conn = sqlite3.connect(target)
            try:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM users WHERE access_token != ''").fetchone()[0], 0)
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM queue_results").fetchone()[0], 3)
            finally:
                conn.close()


class PreviewGuardTests(unittest.TestCase):
    def test_a_missing_empty_or_tableless_db_is_refused_before_db_is_imported(self):
        with tempfile.TemporaryDirectory() as tmp:
            empty = Path(tmp, "empty.db")
            empty.write_bytes(b"")
            other = Path(tmp, "other.db")
            sqlite3.connect(other).execute("CREATE TABLE x (a)").connection.close()
            sentinel = Path(tmp, "sentinel.txt")
            sentinel.write_text("keep")
            for path in (Path(tmp, "missing.db"), empty, other):
                result = subprocess.run([sys.executable, str(ROOT / "web_server.py"), "--db", str(path)], cwd=tmp,
                                        capture_output=True, text=True, timeout=60)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("Preview refused", result.stderr)
            self.assertEqual(sorted(p.name for p in Path(tmp).iterdir()), ["empty.db", "other.db", "sentinel.txt"])


if __name__ == "__main__":
    unittest.main()
