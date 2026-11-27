"""Regression tests for the first code inspection of the website build (WEB-IMPL-01..21)."""
import asyncio
import json
import os
import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiohttp.test_utils import TestClient, TestServer

import db
import leaderboard_bot as app
import queue_stats_service as qss
import web_admin
import web_queries as q
import web_server
try:
    import web_fixture as f
except ImportError:  # run as tests.test_web_inspection_fixes
    from tests import web_fixture as f

ORIGIN = "https://ctier.example"
PASSWORD = "correct-horse-battery-staple-42"
FAST_HASH = web_admin.hash_password(PASSWORD, n=2 ** 14)
SECRET = "ab" * 32


class RetentionParsingTests(unittest.TestCase):  # 01
    def test_malformed_values_never_raise(self):
        for bad in ("++90", "+-1", "٣٠", "9" * 40, " 90x", "1e3", "90.0"):
            with self.assertLogs("db", level="ERROR"):
                self.assertEqual(db.normalize_retention_days(bad), 0, bad)
        self.assertEqual(db.normalize_retention_days(" +30 "), 30)


class QueryFixTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = f.standard_db(str(Path(self.tmp.name) / "s.db"))

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_enrichment_prefers_the_exact_id_and_follows_side_switches(self):  # 04
        did = 333333333333333333  # moonpie, team 0 in 4821
        self.conn.execute("UPDATE player_queue_stats SET kills = NULL, games = 2 WHERE match_id = '4821' AND discord_id = ?",
                          (did,))
        games = [
            f.game(1, 0, [f.prow(0, "moonpieNick", discord_id=did, slug="moonpie", kills=2),
                          f.prow(0, "moonpieNick", kills=50)]),           # a guest namesake on the same team
            f.game(2, 1, [f.prow(1, "moonpieNick", discord_id=did, slug="moonpie", kills=3)]),  # switched sides
        ]
        f.add_snapshot(self.conn, f.HOME, 4821, games)
        row = [r for r in q.get_player_history(self.conn, f.HOME, did, 10) if r["match_id"] == "4821"][0]
        self.assertEqual(row["kills"], 5)

    def test_a_snapshot_result_is_authoritative_even_when_unknown(self):  # 05
        f.add_snapshot(self.conn, f.HOME, 4821, [f.game(1, None, [f.prow(0, "A")])], winner=None, score=(2, 2))
        queue = q.get_queue(self.conn, f.HOME, "4821")
        self.assertIsNone(queue["winner"])  # the stale stat rows still say team 0 won
        self.assertEqual(queue["score"], [2, 2])

    def test_mvp_counts_a_side_switcher_once_like_the_card(self):  # 08
        snap = {"v": 2, "games": [
            f.game(1, 0, [f.prow(0, "Star", slug="star", discord_id=1, impact=95), f.prow(0, "Mid", slug="mid", discord_id=2, impact=60)]),
            f.game(2, 0, [f.prow(0, "Star", slug="star", discord_id=1, impact=95), f.prow(0, "Mid", slug="mid", discord_id=2, impact=60)]),
            f.game(3, 1, [f.prow(1, "Star", slug="star", discord_id=1, impact=95), f.prow(0, "Mid", slug="mid", discord_id=2, impact=60)]),
            f.game(4, 1, [f.prow(1, "Star", slug="star", discord_id=1, impact=95), f.prow(0, "Mid", slug="mid", discord_id=2, impact=60)]),
        ]}
        teams = q.build_queue_roster(snap)
        mvp = q.pick_mvp(teams, 4, True)
        self.assertEqual(teams[mvp[0]][mvp[1]]["person"], "slug:star")

    def test_hall_of_fame_query_is_scoped_in_sql(self):  # 21
        seen = []
        self.conn.set_trace_callback(seen.append)
        q.get_hall_of_fame(self.conn, f.HOME)
        self.assertTrue(any("FROM hall_of_fame WHERE guild_id = " in s for s in seen))

    def test_an_expired_or_overrunning_job_is_refused(self):  # 16
        webdb = web_server.WebDB(str(Path(self.tmp.name) / "s.db"))
        try:
            with self.assertRaises(sqlite3.OperationalError):
                webdb._job(time.monotonic() - 1, lambda conn: 1, ())
            q._job.deadline = time.monotonic() - 1
            with self.assertRaises(sqlite3.OperationalError):
                q.checkpoint()
        finally:
            q._job.deadline = None
            webdb.executor.shutdown()


class SnapshotCollisionTests(unittest.TestCase):  # 06
    def run_order(self, rows, entry):
        match_result = {"teams": [[entry]], "individual_games": [{"game_number": 1, "winning_display_team_index": 0,
                                                                  "players": rows}]}
        return {r["key"]: r["name"] for r in qss.build_queue_snapshot("1", match_result)["games"][0]["players"]}

    def test_an_unverified_slug_and_a_logged_out_namesake_keep_their_own_names_in_either_order(self):
        slugged = {"identity": "raven", "username": "raven", "slug": "raven", "discord_id": None,
                   "display_team_index": 0, "kills": 1, "damage_dealt": 1, "impact_score": None}
        loggedout = {"identity": "raven", "username": "Raven", "slug": None, "display_team_index": 0,
                     "kills": 1, "damage_dealt": 1, "impact_score": None}
        entry_slug_first = {"discord_id": None, "username": "raven", "slug": "raven", "display_name": "raven", "guest": True}
        entry_guest_first = {"discord_id": None, "username": "Raven", "slug": None, "display_name": "Raven", "guest": True}
        for entry in (entry_slug_first, entry_guest_first):
            names = self.run_order([slugged, loggedout], entry)
            self.assertEqual(names, {"slug:raven": "raven", "name:raven#1": "Raven"})


class ContextTests(unittest.TestCase):
    def ctx(self, winners, score):
        games = [{"number": i + 1, "winner": w, "teams": [[], []]} for i, w in enumerate(winners)]
        queue = {"match_id": "1", "teams": [[], []], "games": games, "winner": 0, "score": score, "mvp": None,
                 "date_ms": None, "date_is_saved": False, "duration_ms": None, "has_snapshot": True,
                 "total_games": len(winners)}
        site = SimpleNamespace(player=lambda *a: {"name": "x", "url": None, "linked": True})
        return web_server.queue_context(site, queue, {}, {}, len(games))

    def test_captions_turn_unknown_after_an_unresolved_game(self):  # 17
        captions = [g["series"] for g in self.ctx([0, None, 0], [2, 1])["games"]]
        self.assertEqual(captions, ["1 - 0", None, None])
        exact = [g["series"] for g in self.ctx([0, None, 0], [2, 0])["games"]]  # replay holds: still exact
        self.assertEqual(exact, ["1 - 0", "1 - 0", "2 - 0"])

    def test_leaderboard_values_use_the_bots_formats(self):  # 09
        from leaderboard_service import SEASON_STATS
        site = SimpleNamespace(player=lambda *a: {"name": "x", "url": None, "linked": True})
        for stat, value in (("damage", 100.5), ("damage", 101.5), ("impact", 62.5), ("kills", 2.25)):
            row = web_server._board_rows(site, [{"discord_id": 1, "display_name": "x", "games": 3, "value": value}],
                                         stat, {}, {})[0]
            self.assertEqual(row["value"], SEASON_STATS[stat][3].format(value), (stat, value))


class AdminFixTests(unittest.TestCase):
    def test_config_rejects_bad_origins_and_unusable_scrypt_settings(self):  # 11
        self.assertIsNone(web_admin.parse_admin_config("http://ctier.example", FAST_HASH, SECRET))
        self.assertIsNone(web_admin.parse_admin_config("https://x:99999", FAST_HASH, SECRET))
        big = "scrypt:131072:8:1:" + "00" * 16 + ":" + "00" * 32
        self.assertIsNone(web_admin.parse_admin_config(ORIGIN, big, SECRET))
        self.assertIsNotNone(web_admin.parse_admin_config("http://127.0.0.1:8080", FAST_HASH, SECRET,
                                                       allow_loopback_http=True))  # the preview only

    def test_lockout_and_slot_are_checked_together(self):  # 10
        now = [1000.0]
        guard = web_admin.LoginGuard(clock=lambda: now[0])
        self.assertEqual(guard.begin(), ("ok", 0))
        self.assertEqual(guard.begin()[0], "busy")
        for _ in range(web_admin.FAILURE_LIMIT):
            guard.record_failure()
        guard.release()
        self.assertEqual(guard.begin()[0], "locked")


class AdminGateTests(unittest.IsolatedAsyncioTestCase):  # 12, 13, 14, 15, 20
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmp.name) / "s.db")
        f.standard_db(self.path).close()
        self.backups = Path(self.tmp.name) / "elsewhere"
        self.backups.mkdir()
        (self.backups / "leaderboard-20261126.db").write_bytes(b"x")
        bot = SimpleNamespace(get_guild=lambda gid: SimpleNamespace(
            chunked=False, get_member=lambda i: SimpleNamespace(display_name="LiveNick")) if gid == f.HOME else None,
            is_ready=lambda: False, is_closed=lambda: True, latency=float("nan"), guilds=[])
        with patch.dict(os.environ, {"BACKUP_DIR": str(self.backups)}):
            self.app = web_server.create_app(f.HOME, self.path, bot=bot,
                                             admin_config=web_admin.parse_admin_config(ORIGIN, FAST_HASH, SECRET))
        self.client = TestClient(TestServer(self.app))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        self.app["site"].db.executor.shutdown(wait=True)
        self.app["site"].checker.executor.shutdown(wait=True)
        self.tmp.cleanup()

    async def test_every_admin_path_but_login_redirects_anonymous_visitors_to_login(self):
        for method, url in (("GET", "/admin"), ("GET", "/admin/"), ("GET", "/admin/nope"), ("POST", "/admin/logout"),
                            ("PUT", "/admin/"), ("GET", "/admin/logs/queue_stats_1.json")):
            resp = await self.client.request(method, url, headers={"Origin": ORIGIN}, allow_redirects=False)
            self.assertEqual((resp.status, resp.headers.get("Location")), (303, "/admin/login"), (method, url))
            self.assertEqual(resp.headers["Cache-Control"], "no-store")
        login = await self.client.get("/admin/login", allow_redirects=False)
        self.assertEqual(login.status, 200)

    async def test_panel_reads_backups_from_backup_dir(self):
        cookie = {"Cookie": f"{web_admin.COOKIE_NAME}={web_admin.make_session(self.app['site'].admin)}"}
        body = await (await self.client.get("/admin/", headers=cookie)).text()
        self.assertIn("leaderboard-20261126.db", body)

    async def test_redirects_carry_a_bounded_cache_lifetime(self):
        conn = sqlite3.connect(self.path)
        conn.execute("UPDATE users SET slug = 'kestrel2' WHERE discord_id = 111111111111111111")
        conn.commit()
        conn.close()
        resp = await self.client.get("/players/kestrel", allow_redirects=False)
        self.assertEqual((resp.status, resp.headers["Cache-Control"]), (301, "public, max-age=60"))

    async def test_non_ascii_digits_and_empty_values_are_400_cold_and_warm(self):
        for _ in range(2):
            for url in ("/queues?page=²", "/queues/4821?game=", "/queues/4821?game=²", "/leaderboard?month=２０２６-11"):
                resp = await self.client.get(url, allow_redirects=False)
                self.assertEqual(resp.status, 400, url)
            await self.client.get("/queues/4821")

    async def test_a_half_loaded_member_cache_is_never_used_for_names(self):
        body = await (await self.client.get("/queues/4821")).text()
        self.assertNotIn("LiveNick", body)
        self.assertIn("KestrelNick", body)

    async def test_a_suppressed_name_gets_no_profile_link(self):
        site = self.app["site"]
        who = site.player(111111111111111111, "Player 111111111111111111", {}, {111111111111111111: "kestrel"})
        self.assertEqual((who["name"], who["url"]), ("Unknown player", None))


class ShutdownFixTests(unittest.IsolatedAsyncioTestCase):  # 02, 03
    async def test_stop_waits_for_a_running_password_check_and_db_job(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "s.db")
            f.standard_db(path).close()
            server = await web_server.start_web_server(18766, f.HOME, path, host="127.0.0.1",
                                                       admin_config=web_admin.parse_admin_config(ORIGIN, FAST_HASH, SECRET))
            site = server.site
            started = threading.Event()

            def slow_verify(cfg, pw):
                started.set()
                time.sleep(0.5)
                return False

            site.checker.verify = slow_verify
            login = asyncio.ensure_future(site.checker.check("x" * 20))
            job = asyncio.ensure_future(site.db.run(lambda conn: time.sleep(0.4) or 1))
            await asyncio.get_running_loop().run_in_executor(None, started.wait, 5)
            await server.stop(time.monotonic() + 5)
            self.assertEqual(site.checker.pending(), [])
            self.assertEqual([fut for fut in site.db.running if not fut.done()], [])
            await asyncio.gather(login, job, return_exceptions=True)

    async def test_a_signal_during_website_startup_stops_the_new_listener_and_skips_discord(self):
        server = SimpleNamespace(stop=AsyncMock())

        async def starting(*args, **kwargs):
            app.request_shutdown()  # SIGTERM while the listener is starting
            return server

        app._shutdown_task = None
        app._web_server = None
        try:
            with patch.object(app, "WEB_PORT", 8080), patch("web_server.start_web_server", new=starting), \
                    patch.object(app.bot, "close", new=AsyncMock()), \
                    patch.object(app.bot, "start", new=AsyncMock()) as start, \
                    patch.object(app, "close_http_session", new=AsyncMock()), \
                    patch.object(app, "install_shutdown_signal_handler"):
                await app.main()
            server.stop.assert_awaited()
            start.assert_not_awaited()
            self.assertIsNone(app._web_server)
        finally:
            app._shutdown_task = None
            app._web_server = None


if __name__ == "__main__":
    unittest.main()
