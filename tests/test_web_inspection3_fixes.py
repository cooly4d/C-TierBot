"""Regression tests for code inspection 3 (WEB-R12-01..16). Browser-side fixes (R12-12 rapid metric switches,
R12-13 hit targets, R12-14 game colours) are checked in Chrome; see the review log."""
import asyncio
import io
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import queue_stats_service as qss
import web_admin
import web_queries as q
import web_server
from web_names import safe_slug
try:
    import web_fixture as f
    from test_queue_result_logic import build_payload
    from test_web_server import SiteTestCase, fake_bot
except ImportError:  # run as tests.test_web_inspection3_fixes
    from tests import web_fixture as f
    from tests.test_queue_result_logic import build_payload
    from tests.test_web_server import SiteTestCase, fake_bot

ROOT = Path(__file__).resolve().parent.parent
FAST_HASH = web_admin.hash_password("correct-horse-battery-staple-42", n=2 ** 14)
SECRET = "ab" * 32


class FileReadTests(unittest.TestCase):  # 01
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        q._job.deadline = None
        q._job.stopping = None
        self.tmp.cleanup()

    def test_only_regular_files_are_read(self):
        (self.dir / "queue_stats_dir.json").mkdir()
        (self.dir / "queue_stats_ok.json").write_text(json.dumps({"status": "ok"}))
        listed = {e["name"]: e["readable"] for e in web_admin.list_logs(str(self.dir))}
        self.assertEqual(listed, {"queue_stats_ok.json": True, "queue_stats_dir.json": False})
        self.assertEqual(web_admin.read_log(str(self.dir), "queue_stats_dir.json")[0], "unreadable")

    def test_a_read_stops_at_the_next_chunk_once_stopping(self):
        path = self.dir / "queue_stats_big.json"
        path.write_text(json.dumps({"pad": "x" * 600_000}))
        reads = []
        real_read = os.read

        def counting_read(fd, n):
            reads.append(n)
            if len(reads) == 2:
                q._job.stopping = lambda: True  # shutdown begins while the file is being read
            return real_read(fd, n)

        with patch("web_admin.os.read", side_effect=counting_read):
            with self.assertRaises(sqlite3.OperationalError):
                web_admin._read_json_file(str(path))
        self.assertEqual(len(reads), 2)  # ten chunks would be needed; it stopped after the second


class LifecycleTests(unittest.IsolatedAsyncioTestCase):  # 01
    async def test_stop_returns_only_after_the_workers_really_exit(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = str(Path(tmp.name) / "site.db")
        f.standard_db(path).close()
        site = web_server.Site(f.HOME, path)
        started = threading.Event()

        def busy(conn):
            started.set()
            while True:  # Python work between queries: it checks the deadline between units, as every job does
                q.checkpoint()
                time.sleep(0.01)

        job = asyncio.ensure_future(site.db.run(busy))
        await asyncio.get_running_loop().run_in_executor(None, started.wait, 5)
        t0 = time.monotonic()
        await site.db.stop(time.monotonic() + 5)
        self.assertLess(time.monotonic() - t0, 1.0)
        self.assertFalse([fut for fut in site.db.running if not fut.done()])  # nothing still running
        with self.assertRaises(web_server.Busy):
            await job


class ResultWriteTests(unittest.IsolatedAsyncioTestCase):  # 02
    async def test_result_writes_run_off_the_event_loop_in_order(self):
        loop_thread = threading.get_ident()
        calls = []
        ticks = []

        def slow(name):
            def run(*args, **kwargs):
                calls.append((name, threading.get_ident()))
                time.sleep(0.15)  # a write waiting on a lock
                return [] if name == "hof" else None
            return run

        async def heartbeat():
            while True:
                ticks.append(time.monotonic())
                await asyncio.sleep(0.02)

        result = {"teams": [[{"discord_id": 1, "username": "A", "stats": {"games": 5, "kills": 1, "damage": 9}}]],
                  "winning_team_index": 0, "match_history": [True] * 5, "team_round_wins": [5, 0],
                  "total_games_played": 5, "duration_ms": 60_000, "is_final": True}
        beat = asyncio.ensure_future(heartbeat())
        try:
            with patch("queue_stats_service.check_queue_hall_of_fame_records", side_effect=slow("hof")), \
                    patch("queue_stats_service.cache_player_queue_stats", side_effect=slow("rows")), \
                    patch("queue_stats_service.save_queue_result", side_effect=slow("snapshot")), \
                    patch("queue_stats_service.build_queue_snapshot", return_value={"games": []}), \
                    patch("queue_stats_service.calculate_queue_match_stats", new=AsyncMock(return_value=(result, None))), \
                    patch("queue_stats_service.resolve_queue_user_display_names", new=AsyncMock()), \
                    patch("queue_stats_service.generate_queue_result_image", return_value=io.BytesIO(b"png")):
                await qss.build_queue_stats_payload("3674", 999)
        finally:
            beat.cancel()
        self.assertEqual([c[0] for c in calls], ["hof", "rows", "snapshot"])
        self.assertTrue(all(t != loop_thread for _, t in calls))
        gaps = [b - a for a, b in zip(ticks, ticks[1:])]
        self.assertTrue(gaps and max(gaps) < 0.12, max(gaps or [0]))  # the loop kept running during 0.45 s of writes

    async def test_record_lines_still_reach_the_post(self):
        built = await build_payload(record_lines=["🏆 New Hall of Fame record!"], is_final=True)
        self.assertIn("New Hall of Fame record", built.content)


class SiteFixTests(SiteTestCase):  # 03, 06, 07, 15
    async def asyncSetUp(self):
        await super().asyncSetUp()
        conn = sqlite3.connect(self.path)
        # 03: a stored number with an embedded NUL and a private suffix, in stat rows and in a snapshot.
        f.add_row(conn, f.HOME, "4822\x00private", name="NulRow", team=0)
        f.add_snapshot(conn, f.HOME, "4823\x00private", [f.game(1, 0, [f.prow(0, "NulSnap")])])
        # 06: a v1 snapshot whose only scored players share a name on one team (no series totals for them).
        f.add_snapshot(conn, f.HOME, 4830, [f.game(1, 0, [
            f.prow(0, "Twin", impact=50), f.prow(0, "Twin", impact=60), f.prow(1, "Solo", impact=None)])], v=1)
        # 07: a record whose queue has no stat rows and no snapshot.
        conn.execute("INSERT OR REPLACE INTO hall_of_fame VALUES ('longest_queue', 3600000, NULL, 'Queue Duration', "
                     "'1234', ?, '2026-11-20T19:05:00+00:00', 3600000, NULL)", (f.HOME,))
        conn.commit()
        conn.close()

    async def test_archive_skips_numbers_with_an_embedded_nul(self):
        _, body = await self.get("/queues")
        self.assertNotIn("private", body)
        self.assertNotIn("/queues/4822", body)
        self.assertNotIn("/queues/4823", body)
        conn = sqlite3.connect(self.path)
        try:
            listed = q.list_queue_ids(conn, f.HOME, 100, 0)
            self.assertEqual(q.count_queues(conn, f.HOME), len(listed))
            self.assertFalse([m for m in listed if not m.isdigit()])
        finally:
            conn.close()

    async def test_a_game_with_scores_shows_its_impact_column(self):
        _, body = await self.get("/queues/4830")
        series, _, breakdown = body.partition('id="games-h"')
        self.assertNotIn(">Impact<", series)  # no series totals for the twins
        self.assertIn(">Impact<", breakdown)
        self.assertIn(">50<", breakdown)

    async def test_a_record_without_a_queue_page_is_plain_text(self):
        for url in ("/", "/hall-of-fame"):
            _, body = await self.get(url)
            self.assertIn("Queue #1234", body, url)
            self.assertNotIn('href="/queues/1234"', body, url)
            self.assertIn('href="/queues/4821"', body, url)  # a record with a page keeps its link

    async def test_metric_links_keep_the_history_page(self):
        conn = sqlite3.connect(self.path)
        for n in range(30):
            f.add_row(conn, f.HOME, 5000 + n, discord_id=111111111111111111, name="KestrelNick", slug="kestrel",
                      end_ms=f.ms(2026, 11, 1) + n * 60_000)
        conn.commit()
        conn.close()
        _, body = await self.get("/players/kestrel?page=2")
        self.assertIn('href="?metric=impact&amp;page=2"', body)
        _, first = await self.get("/players/kestrel")
        self.assertIn('href="?metric=impact"', first)


class ReadinessCacheTests(SiteTestCase):  # 08
    async def asyncSetUp(self):
        self.bot = fake_bot({111111111111111111: SimpleNamespace(display_name="MainNick")})
        await super().asyncSetUp()

    async def test_losing_readiness_drops_cached_pages(self):
        guild = self.bot.get_guild(f.HOME)
        resp, body = await self.get("/queues/4821")
        self.assertEqual(resp.headers["Cache-Control"], "public, max-age=60")
        self.assertIn("MainNick", body)
        guild.chunked = False  # the guild re-chunks or goes away
        for url in ("/queues/4821", "/players/kestrel"):
            resp, body = await self.get(url)
            self.assertEqual(resp.headers["Cache-Control"], "public, max-age=10", url)
            self.assertNotIn("MainNick", body, url)
        self.assertEqual(len(self.app["site"].cache.items), 0)
        guild.chunked = True
        resp, body = await self.get("/queues/4821")
        self.assertEqual(resp.headers["Cache-Control"], "public, max-age=60")
        self.assertIn("MainNick", body)


class LogNameTests(unittest.TestCase):  # 09
    def test_two_lookups_in_one_clock_tick_get_two_complete_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            frozen = qss.datetime(2026, 11, 27, 12, 0, 0, tzinfo=qss.timezone.utc)
            fake_dt = SimpleNamespace(now=lambda tz=None: frozen)
            with patch.object(qss, "QUEUE_STATS_LOG_DIR", tmp), patch.object(qss, "datetime", fake_dt):
                for _ in range(2):
                    qss.write_queue_stats_log("3674", None, None, trigger="auto_result", guild_id=1, channel_id=2)
            names = sorted(os.listdir(tmp))
            self.assertEqual(len(names), 2)
            self.assertTrue(all(web_admin.LOG_NAME.fullmatch(n) for n in names))
            for n in names:
                self.assertEqual(json.loads(Path(tmp, n).read_text())["match_id"], "3674")

    def test_an_existing_temp_name_is_never_reused_or_removed(self):
        frozen = qss.datetime(2026, 11, 27, 12, 0, 0, tzinfo=qss.timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(qss, "QUEUE_STATS_LOG_DIR", tmp), \
                    patch.object(qss, "datetime", SimpleNamespace(now=lambda tz=None: frozen)), \
                    patch.object(qss.secrets, "token_hex", return_value="deadbeef"):
                qss.write_queue_stats_log("1", None, None, trigger="auto_result", guild_id=1, channel_id=2)
                final = os.listdir(tmp)[0]
                Path(tmp, final).unlink()
                Path(tmp, final + ".tmp").write_text("another writer")  # the same name, still being written
                with self.assertRaises(FileExistsError):
                    qss.write_queue_stats_log("1", None, None, trigger="auto_result", guild_id=1, channel_id=2)
                self.assertEqual(Path(tmp, final + ".tmp").read_text(), "another writer")
                self.assertFalse(Path(tmp, final).exists())


class OriginTests(unittest.TestCase):  # 10
    def test_the_origin_is_stored_as_browsers_send_it(self):
        cfg = web_admin.parse_admin_config("HTTPS://Ctier.Example:443", FAST_HASH, SECRET)
        self.assertEqual(cfg.origin, "https://ctier.example")
        self.assertEqual(web_admin.parse_admin_config("https://ctier.example:8443", FAST_HASH, SECRET).origin,
                         "https://ctier.example:8443")

    def test_http_loopback_only_for_the_preview(self):
        with self.assertLogs("web_admin", level="ERROR"):
            self.assertIsNone(web_admin.parse_admin_config("http://127.0.0.1:8080", FAST_HASH, SECRET))
        cfg = web_admin.parse_admin_config("http://127.0.0.1:8080", FAST_HASH, SECRET, allow_loopback_http=True)
        self.assertEqual(cfg.origin, "http://127.0.0.1:8080")
        with self.assertLogs("web_admin", level="ERROR"):
            self.assertIsNone(web_admin.parse_admin_config("http://ctier.example", FAST_HASH, SECRET,
                                                           allow_loopback_http=True))


class PreviewSchemaTests(unittest.TestCase):  # 11
    def test_the_preview_migrates_an_older_export_before_listening(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp, "old.db")
            conn = sqlite3.connect(path)
            # The table as it was before the games/kills/impact/result columns were added.
            conn.execute("CREATE TABLE player_queue_stats (guild_id INTEGER NOT NULL, match_id TEXT NOT NULL, "
                         "player_key TEXT NOT NULL, discord_id INTEGER, display_name TEXT, username TEXT, slug TEXT, "
                         "team_index INTEGER NOT NULL, total_damage INTEGER NOT NULL, avg_damage REAL NOT NULL, "
                         "duration_ms INTEGER NOT NULL, match_start_ms INTEGER, match_end_ms INTEGER, "
                         "cached_at TEXT NOT NULL, PRIMARY KEY (guild_id, match_id, player_key))")
            conn.execute("CREATE TABLE queue_results (guild_id INTEGER, match_id TEXT, snapshot TEXT, saved_at TEXT)")
            conn.commit()
            conn.close()
            code = "import sys, web_server; web_server.prepare_preview_db(sys.argv[1])"
            subprocess.run([sys.executable, "-c", code, str(path)], cwd=ROOT, check=True, capture_output=True,
                           timeout=60)
            conn = sqlite3.connect(path)
            try:
                columns = {r[1] for r in conn.execute("PRAGMA table_info(player_queue_stats)")}
            finally:
                conn.close()
            self.assertTrue({"games", "kills", "impact", "winning_team_index", "team_round_wins"} <= columns, columns)


class SlugTests(unittest.TestCase):  # 16
    def test_dot_segments_and_trailing_newlines_are_not_slugs(self):
        for bad in (".", "..", "kestrel\n", "a/b", ""):
            self.assertFalse(safe_slug(bad), repr(bad))
        for good in ("kestrel", "k.e-s_t", "..k", "k.."):
            self.assertTrue(safe_slug(good), good)


if __name__ == "__main__":
    unittest.main()
