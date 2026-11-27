"""Regression tests for the second code inspection of the website build (WEB-R10-01..18)."""
import asyncio
import json
import re
import socket
import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from aiohttp.test_utils import TestClient, TestServer

import leaderboard_bot as app
import neatqueue_client
import queue_stats_service as qss
import web_admin
import web_charts
import web_queries as q
import web_server
try:
    import web_fixture as f
except ImportError:  # run as tests.test_web_inspection2_fixes
    from tests import web_fixture as f
from tools import make_admin_password

ORIGIN = "https://ctier.example"
PASSWORD = "correct-horse-battery-staple-42"
FAST_HASH = web_admin.hash_password(PASSWORD, n=2 ** 14)
SECRET = "ab" * 32
SNOWFLAKE = "123456789012345678"
KESTREL = 111111111111111111


class HttpCase(unittest.IsolatedAsyncioTestCase):
    admin = False

    def extra(self, conn):
        pass

    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmp.name) / "site.db")
        conn = f.standard_db(self.path)
        self.extra(conn)
        conn.commit()
        conn.close()
        self.log_dir = Path(self.tmp.name) / "log"
        self.log_dir.mkdir()
        cfg = web_admin.parse_admin_config(ORIGIN, FAST_HASH, SECRET) if self.admin else None
        self.app = web_server.create_app(f.HOME, self.path, admin_config=cfg, log_dir=str(self.log_dir))
        self.client = TestClient(TestServer(self.app))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        site = self.app["site"]
        site.db.executor.shutdown(wait=True)
        if site.checker:
            site.checker.executor.shutdown(wait=True)
        self.tmp.cleanup()

    async def body(self, path, **kw):
        resp = await self.client.get(path, allow_redirects=False, **kw)
        return resp, await resp.text()

    def session(self):
        site = self.app["site"]
        return {"Cookie": f"{web_admin.COOKIE_NAME}={web_admin.make_session(site.admin)}"}


class NonPublicQueueIdTests(HttpCase):  # 01
    def extra(self, conn):
        f.add_row(conn, f.HOME, SNOWFLAKE, discord_id=KESTREL, name="KestrelNick", slug="kestrel", end_ms=f.ms(2026, 11, 22))
        f.add_snapshot(conn, f.HOME, SNOWFLAKE, [f.game(1, 0, [f.prow(0, "KestrelNick", discord_id=KESTREL, slug="kestrel")])],
                       saved_at="2026-11-22T19:05:00+00:00")
        conn.execute("INSERT INTO hall_of_fame VALUES ('most_damage_game', 9999, ?, 'KestrelNick', ?, ?, "
                     "'2026-11-22T19:05:00+00:00', NULL, 1)", (KESTREL, SNOWFLAKE, f.HOME))

    async def test_a_stored_snowflake_never_reaches_a_page_or_a_link(self):
        for path in ("/", "/queues", "/players/kestrel", "/players/kestrel?metric=kills", "/hall-of-fame"):
            resp, text = await self.body(path)
            self.assertEqual(resp.status, 200, path)
            self.assertNotIn(SNOWFLAKE, text, path)
        _, archive = await self.body("/queues")
        listed = re.findall(r'href="/queues/([0-9]+)"', archive)
        self.assertEqual(sorted(set(listed)), ["4700", "4821", "4999"])
        self.assertIn("3 saved finished queues", archive)  # the count matches what can be listed
        _, player = await self.body("/players/kestrel")
        self.assertIn("<th scope=\"row\">—</th>", player)  # the row stays (its stats count), without a link
        self.assertEqual((await self.client.get(f"/queues/{SNOWFLAKE}")).status, 404)

    def test_the_validator(self):
        from web_names import public_queue_id
        self.assertEqual(public_queue_id(4821), "4821")
        for bad in (SNOWFLAKE, "48a1", "", None, True, "٤٨٢١", "4821\n"):
            self.assertIsNone(public_queue_id(bad), bad)


class PanelFileJobTests(HttpCase):  # 02, 17
    admin = True

    async def test_panel_file_reads_are_admitted_like_queries(self):
        self.app["site"].db.capacity = 0
        resp = await self.client.get("/admin/", headers=self.session(), allow_redirects=False)
        self.assertEqual(resp.status, 503)
        resp = await self.client.get("/admin/logs/queue_stats_1.json", headers=self.session(), allow_redirects=False)
        self.assertEqual(resp.status, 503)

    async def test_a_log_that_isnt_utf8_is_shown_as_unreadable(self):
        (self.log_dir / "queue_stats_bad.json").write_bytes(b'{"status": "\xff\xfe"}')
        (self.log_dir / "queue_stats_big.json").write_text('{"x": "' + "a" * (web_admin.MAX_LOG_BYTES + 1) + '"}')
        for name in ("queue_stats_bad.json", "queue_stats_big.json"):
            resp, text = await self.body(f"/admin/logs/{name}", headers=self.session())
            self.assertEqual(resp.status, 200, name)
            self.assertIn("nreadable", text)
        listed = {l["name"]: l["readable"] for l in web_admin.list_logs(str(self.log_dir))}
        self.assertEqual(listed, {"queue_stats_bad.json": False, "queue_stats_big.json": False})


class PanelShutdownTests(unittest.IsolatedAsyncioTestCase):  # 02
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmp.name) / "site.db")
        f.standard_db(self.path).close()
        self.log_dir = Path(self.tmp.name) / "log"
        self.log_dir.mkdir()
        for i in range(30):
            (self.log_dir / f"queue_stats_{i:03d}.json").write_text('{"status": "ok"}')

    async def asyncTearDown(self):
        self.tmp.cleanup()

    def slow_reads(self, started):
        real = web_admin._read_json_file

        def slow(path):
            started.set()
            time.sleep(0.05)
            return real(path)
        return patch.object(web_admin, "_read_json_file", side_effect=slow)

    async def test_a_file_read_in_flight_is_tracked_and_stops_at_the_next_file(self):
        site = web_server.Site(f.HOME, self.path, log_dir=str(self.log_dir))
        started = threading.Event()
        with self.slow_reads(started):
            task = asyncio.ensure_future(site.db.run_files(web_admin.list_logs, str(self.log_dir)))
            await asyncio.get_running_loop().run_in_executor(None, started.wait, 5)
            self.assertEqual(len(site.db.running), 1)
            t0 = time.monotonic()
            await site.db.stop(time.monotonic() + 5)
            self.assertLess(time.monotonic() - t0, 1.0)  # 30 files would take 1.5 s
            self.assertFalse([fut for fut in site.db.running if not fut.done()])
            with self.assertRaises(web_server.Busy):
                await task
            with self.assertRaises(web_server.Busy):
                await site.db.run_files(web_admin.list_logs, str(self.log_dir))  # nothing new after stop

    async def test_the_shutdown_signal_path_waits_for_the_panel_worker(self):
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        server = await web_server.start_web_server(port, f.HOME, self.path, log_dir=str(self.log_dir), host="127.0.0.1")
        self.assertIsNotNone(server)
        started = threading.Event()
        app._shutdown_task = None
        app._web_server = server
        try:
            with self.slow_reads(started), patch.object(app.bot, "close", new=AsyncMock()):
                job = asyncio.ensure_future(server.site.db.run_files(web_admin.list_logs, str(self.log_dir)))
                await asyncio.get_running_loop().run_in_executor(None, started.wait, 5)
                t0 = time.monotonic()
                await app.request_shutdown()  # what the SIGTERM handler starts
                self.assertLess(time.monotonic() - t0, 5)
                self.assertFalse([fut for fut in server.site.db.running if not fut.done()])  # the worker exited
                with self.assertRaises(web_server.Busy):
                    await job
        finally:
            app._shutdown_task = None
            app._web_server = None


class SnapshotIdentityTests(unittest.TestCase):  # 03, 04
    def board_row(self, name, player_id, slug=None):
        return {"username": name, "slug": slug, "team_id": 1, "rank": 1, "player_id": player_id}

    def test_a_literal_player_2_and_a_numbered_player_2_get_different_keys(self):
        board = [self.board_row("Player", 1), self.board_row("Player", 2), self.board_row("Player (2)", 3)]
        self.assertEqual(neatqueue_client.number_same_name_guests(board, {1: 0}), {1: "Player (2)"})
        self.assertEqual(neatqueue_client.same_name_guest_numbers(board, {1: 0}), {1: 2})
        entry = {"discord_id": None, "username": "Player", "slug": None, "display_name": "Player"}
        rows = [qss._snapshot_row(0, {"username": u, "base_username": b, "guest_number": n, "slug": None}, entry)
                for u, b, n in (("Player", "Player", 1), ("Player (2)", "Player", 2), ("Player (2)", "Player (2)", 1))]
        self.assertEqual([r["key"] for r in rows], ["name:player#1", "name:player#2", "name:player (2)#1"])
        self.assertEqual([r["name"] for r in rows], ["Player", "Player (2)", "Player (2)"])

    def test_duplicate_keys_in_one_game_are_never_added_together(self):
        snap = {"v": 2, "games": [
            f.game(1, 0, [f.prow(0, "Player (2)", key="name:x", kills=1, damage=100),
                          f.prow(0, "Player (2)", key="name:x", kills=9, damage=900)]),
            f.game(2, 0, [f.prow(0, "Player (2)", key="name:x", kills=2, damage=200)]),
        ]}
        team = q.build_queue_roster(snap)[0]
        self.assertEqual(len(team), 3)
        self.assertTrue(all(p["stats"] is None and not p["combined_by_name"] for p in team))

    def test_a_guest_first_collision_links_the_verified_row_without_a_database_lookup(self):
        guest_entry = {"discord_id": None, "username": "Raven", "slug": None, "display_name": "Raven"}
        slugged = {"username": "raven", "slug": "raven", "discord_id": 55, "base_username": "raven", "guest_number": 1}
        with patch("db.get_discord_id_by_slug", side_effect=AssertionError("DB lookup")), \
                patch("sqlite3.connect", side_effect=AssertionError("DB connect")):
            row = qss._snapshot_row(0, slugged, guest_entry)
        self.assertEqual((row["key"], row["discord_id"], row["guest"]), ("slug:raven", 55, False))

    def test_the_calculator_carries_each_rows_own_link(self):
        import inspect
        source = inspect.getsource(neatqueue_client)
        self.assertIn('"discord_id": lookup_discord_id(slug) if slug and side is not None else None', source)
        self.assertNotIn("get_discord_id_by_slug", inspect.getsource(qss))


class MvpAndEnrichmentTests(unittest.TestCase):  # 05, 06
    def test_same_name_substitutes_on_opposite_teams_never_add_up_to_a_full_series(self):
        snap = {"v": 2, "games": []}
        for n in range(1, 5):
            side = 0 if n <= 2 else 1  # "Player" played games 1-2 for team 1 and games 3-4 for team 2
            snap["games"].append(f.game(n, 0, [
                f.prow(side, "Player", key="name:player#1", impact=99),
                f.prow(0, "Steady", slug="steady", discord_id=1, impact=70),
            ]))
        teams = q.build_queue_roster(snap)
        mvp = q.pick_mvp(teams, 4, True)
        self.assertEqual(teams[mvp[0]][mvp[1]]["person"], "slug:steady")

    def test_a_partly_resolved_series_leaves_the_legacy_kills_missing(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        conn = f.standard_db(str(Path(tmp.name) / "e.db"))
        self.addCleanup(conn.close)
        did = 333333333333333333
        conn.execute("UPDATE player_queue_stats SET kills = NULL, impact = NULL, games = 2 "
                     "WHERE match_id = '4821' AND discord_id = ?", (did,))
        f.add_snapshot(conn, f.HOME, 4821, [
            f.game(1, 0, [f.prow(0, "moonpieNick", discord_id=did, slug="moonpie", kills=2)]),
            f.game(2, 0, [f.prow(0, "moonpieNick", kills=50), f.prow(0, "moonpieNick", kills=40)]),  # ambiguous
        ])
        row = [r for r in q.get_player_history(conn, f.HOME, did, 10) if r["match_id"] == "4821"][0]
        self.assertIsNone(row["kills"])
        self.assertIsNone(row["impact"])
        self.assertIsNone(web_charts.trend_value(row, "kills"))


class ReadTransactionTests(unittest.TestCase):  # 07
    def test_a_commit_between_two_reads_of_one_job_is_never_half_seen(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = str(Path(tmp.name) / "t.db")
        f.standard_db(path).close()
        webdb = web_server.WebDB(path)
        self.addCleanup(webdb.executor.shutdown)

        def job(conn):
            first = q.count_queues(conn, f.HOME)
            writer = sqlite3.connect(path, timeout=0.05)
            try:
                f.add_row(writer, f.HOME, 5000, name="Late")
                writer.commit()
            except sqlite3.OperationalError:
                pass  # rollback-journal mode: the writer waits for this read transaction to end
            finally:
                writer.close()
            return first, q.count_queues(conn, f.HOME), len(q.list_queue_ids(conn, f.HOME, 100, 0))

        first, second, listed = asyncio.run(webdb.run(job))
        self.assertEqual(first, second)
        self.assertEqual(first, listed)


class StoredNameFallbackTests(HttpCase):  # 08
    LEFT = 555555555555555555

    def extra(self, conn):
        f.add_user(conn, self.LEFT, "gone", "Gone")
        for match_id, name, saved in ((5101, "OldName", "2026-11-01T10:00:00+00:00"),
                                      (5102, "NewName", "2026-11-02T10:00:00+00:00")):
            f.add_snapshot(conn, f.HOME, match_id, [f.game(1, 0, [f.prow(0, name, discord_id=self.LEFT, slug="gone"),
                                                                  f.prow(1, "Opp")])], saved_at=saved)
        conn.execute("INSERT OR REPLACE INTO hall_of_fame VALUES ('most_avg_damage', 1700, 666666666666666666, 'HofOnly', '4821', "
                     "?, '2026-11-20T19:05:00+00:00', NULL, NULL)", (f.HOME,))

    async def test_a_snapshot_only_player_has_one_name_on_every_page(self):
        for match_id in (5101, 5102):
            _, text = await self.body(f"/queues/{match_id}")
            self.assertIn("NewName", text)
            self.assertNotIn("OldName", text)

    def test_the_hall_of_fame_name_is_the_last_fallback(self):
        conn = sqlite3.connect(self.path)
        try:
            names = q.stored_names(conn, f.HOME, [self.LEFT, 666666666666666666, KESTREL])
        finally:
            conn.close()
        self.assertEqual(names, {self.LEFT: "NewName", 666666666666666666: "HofOnly", KESTREL: "KestrelNick"})


class AdminGateTests(HttpCase):  # 09, 11
    admin = True

    async def test_a_non_ascii_cookie_goes_to_the_login_page(self):
        self.assertFalse(web_admin.check_session(self.app["site"].admin, "é.é"))
        self.assertFalse(web_admin.check_session(self.app["site"].admin, "a." + "é" * 43))
        resp = await self.client.get("/admin/", headers={"Cookie": f"{web_admin.COOKIE_NAME}=é.é"}, allow_redirects=False)
        self.assertEqual((resp.status, resp.headers["Location"]), (303, "/admin/login"))
        self.assertEqual(resp.headers["Cache-Control"], "no-store")

    async def test_a_failure_inside_the_gate_still_gets_the_admin_headers(self):
        with patch.object(web_server, "_signed_in", side_effect=RuntimeError("boom")), \
                self.assertLogs("web_server", level="ERROR"):
            resp = await self.client.get("/admin/", allow_redirects=False)
        self.assertEqual(resp.status, 500)
        self.assertEqual(resp.headers["Cache-Control"], "no-store")
        self.assertEqual(resp.headers["Referrer-Policy"], "same-origin")

    async def test_only_get_and_post_of_the_login_page_are_open(self):
        for method in ("HEAD", "PUT", "DELETE"):
            resp = await self.client.request(method, "/admin/login", allow_redirects=False)
            self.assertEqual((resp.status, resp.headers.get("Location")), (303, "/admin/login"), method)
            self.assertEqual(resp.headers["Cache-Control"], "no-store")
        self.assertEqual((await self.client.get("/admin/login", allow_redirects=False)).status, 200)

    async def test_a_multiword_passphrase_works_from_the_tool_to_the_login(self):
        phrase = "correct horse battery staple"
        values = make_admin_password.make(phrase)
        cfg = web_admin.parse_admin_config(ORIGIN, values["hash"], values["secret"])
        site = self.app["site"]
        old = site.checker
        site.admin = cfg
        site.checker = web_admin.PasswordChecker(cfg, site.guard)
        old.executor.shutdown(wait=True)
        from urllib.parse import quote_plus
        resp = await self.client.post("/admin/login", data=f"password={quote_plus(phrase)}", allow_redirects=False,
                                      headers={"Origin": ORIGIN, "Content-Type": "application/x-www-form-urlencoded"})
        self.assertEqual(resp.status, 303)


class ConfigTests(unittest.IsolatedAsyncioTestCase):  # 10
    def test_overlong_or_non_ascii_numeric_fields_turn_the_panel_off_quietly(self):
        good = FAST_HASH.split(":")
        for n in ("9" * 5000, "١٦٣٨٤", "0" * 9 + "16384"):
            text = ":".join(["scrypt", n] + good[2:])
            with self.assertLogs("web_admin", level="ERROR") as logs:
                self.assertIsNone(web_admin.parse_admin_config(ORIGIN, text, SECRET))
            self.assertNotIn(n[:20], "\n".join(logs.output))
        for hash_text, secret in ((FAST_HASH + "\n", SECRET), (FAST_HASH, SECRET + "\n")):  # fullmatch, not $
            with self.assertLogs("web_admin", level="ERROR"):
                self.assertIsNone(web_admin.parse_admin_config(ORIGIN, hash_text, secret))

    async def test_a_panel_config_error_never_stops_the_public_site(self):
        with patch.object(app, "WEB_PORT", 8080), \
                patch("web_admin.parse_admin_config", side_effect=ValueError("Exceeds the limit for integer string")), \
                patch("web_server.start_web_server", new=AsyncMock(return_value=None)) as start:
            with self.assertLogs("leaderboard_bot", level="ERROR"):
                await app.start_website()
        self.assertIsNone(start.await_args.kwargs["admin_config"])


class StaticAndTimelineTests(HttpCase):  # 12, 13
    def extra(self, conn):
        games = [f.game(n, None if n == 9 else n % 2, [f.prow(0, "A"), f.prow(1, "B")]) for n in range(1, 18)]
        f.add_snapshot(conn, f.HOME, 6000, games, winner=0, score=(9, 7))

    async def test_a_missing_asset_keeps_a_short_lifetime(self):
        missing = await self.client.get("/static/not-there.css")
        self.assertEqual(missing.status, 404)
        self.assertEqual(missing.headers["Cache-Control"], web_server.ERROR_MAX_AGE)
        present = await self.client.get("/static/site.css")
        self.assertEqual(present.headers["Cache-Control"], "public, max-age=86400")

    async def test_a_17_game_series_keeps_every_lane_on_one_row(self):
        resp, text = await self.body("/queues/6000")
        self.assertEqual(resp.status, 200)
        # One column per game (the grid flows down each column): T1, the unknown-winner lane, T2, game label.
        grid = text.split('class="timeline-grid has-mid"', 1)[1].split("</figure>", 1)[0]
        self.assertEqual(grid.count('class="lane-cell"'), 2 * 17)
        self.assertEqual(grid.count('class="lane-cell lane-mid"'), 17)
        self.assertEqual(grid.count('class="tl-label'), 17)
        css = (web_server.STATIC_DIR / "site.css").read_text(encoding="utf-8")
        self.assertNotIn(".timeline-grid.n", css)
        self.assertIn("grid-auto-flow: column", css)
        self.assertIn(".timeline-grid.has-mid { grid-template-rows: 1.625rem 1.25rem 1.625rem auto; }", css)


class TrendLatestTests(unittest.TestCase):  # 15
    def test_the_ring_sits_on_the_newest_queue_that_has_a_value(self):
        rows = [{"match_id": str(4800 + i), "won": True, "score": "4-2",
                 "row": {"impact": v, "games": 6, "avg_damage": 1000.0, "total_damage": 6000, "kills": 12}}
                for i, v in enumerate([60, 70, None])]
        geo = web_charts.trend_geometry(rows, "impact")
        self.assertEqual((geo["latest"], geo["latest_i"]), ("70", 1))
        self.assertEqual(web_charts.trend_geometry(rows, "damage")["latest_i"], 2)
        js = (web_server.STATIC_DIR / "site.js").read_text(encoding="utf-8")
        self.assertIn("const latest = to.latest_i;", js)


class PhoneDateColumnTests(unittest.TestCase):  # 16
    def test_the_date_column_is_never_hidden(self):
        css = (web_server.STATIC_DIR / "site.css").read_text(encoding="utf-8")
        self.assertIsNone(re.search(r"\.col-date\s*\{[^}]*display:\s*none", css))


if __name__ == "__main__":
    unittest.main()
