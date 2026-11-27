"""The public website over HTTP: isolation, leakage, names, caching, limits and safety headers."""
import asyncio
import os
import re
import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from aiohttp.test_utils import TestClient, TestServer

import web_server
try:
    import web_fixture as f
except ImportError:  # run as tests.test_web_server
    from tests import web_fixture as f

CLOCK = lambda: f.ms(2026, 11, 26) / 1000  # noqa: E731
PUBLIC_URLS = ["/", "/queues", "/queues?page=1", "/queues/4821", "/queues/4821?game=2", "/queues/4700", "/queues/4999",
               "/leaderboard", "/leaderboard?month=2026-11&stat=impact", "/leaderboard?stat=kills", "/players/kestrel",
               "/players/kestrel?metric=impact", "/players/juno?metric=kills", "/hall-of-fame", "/queues/1",
               "/players/nobody", "/leaderboard?stat=bogus", "/queues?page=abc", "/leaderboard?month=2026-13"]


def fake_bot(home_members, chunked=True, other_members=None):
    guilds = {
        f.HOME: SimpleNamespace(chunked=chunked, get_member=lambda i: home_members.get(i)),
        f.OTHER: SimpleNamespace(chunked=True, get_member=lambda i: (other_members or {}).get(i)),
    }
    return SimpleNamespace(get_guild=lambda gid: guilds.get(gid))


class SiteTestCase(unittest.IsolatedAsyncioTestCase):
    bot = None

    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmp.name) / "site.db")
        self.conn = f.standard_db(self.path)
        self.conn.close()
        self.app = web_server.create_app(f.HOME, self.path, bot=self.bot, clock=CLOCK)
        self.client = TestClient(TestServer(self.app))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        self.app["site"].db.executor.shutdown(wait=True)
        self.tmp.cleanup()

    async def get(self, url, **kw):
        resp = await self.client.get(url, allow_redirects=False, **kw)
        return resp, await resp.text()


class IsolationAndLeakageTests(SiteTestCase):
    async def test_no_public_response_leaks_tokens_ids_or_the_other_guild(self):
        planted = ["OTHERGUILDSECRETNAME", f.CANARY_TOKEN, str(f.OTHER), str(f.HOME), "111111111111111111",
                   "222222222222222222", "333333333333333333", "444444444444444444"]
        for url in PUBLIC_URLS:
            resp, body = await self.get(url)
            blob = body + " ".join(f"{k}: {v}" for k, v in resp.headers.items())
            for value in planted:
                self.assertNotIn(value, blob, f"{value} leaked on {url} ({resp.status})")
            self.assertIsNone(re.search(r"\d{15,}", body), url)
            self.assertNotIn("Set-Cookie", resp.headers, url)
            self.assertIn(resp.status, (200, 301, 400, 404), url)

    async def test_hall_of_fame_shows_only_this_guilds_records(self):
        _, body = await self.get("/hall-of-fame")
        self.assertIn("Most Kills in a Queue", body)
        self.assertEqual(body.count("No record yet"), 3)

    async def test_archive_counts_the_union_and_ignores_unknown_params(self):
        _, body = await self.get("/queues")
        self.assertIn("3 saved finished queues", body)
        self.assertIn("#4999", body)
        self.assertIn("(saved)", body)
        _, again = await self.get("/queues?page=1&x=y")
        self.assertEqual(body, again)

    async def test_bad_values_of_known_params_are_400_or_404_never_500(self):
        for url, status in (("/leaderboard?stat=bogus", 400), ("/leaderboard?month=2026-13", 400),
                            ("/leaderboard?month=2099-01", 404), ("/queues?page=abc", 400), ("/queues?page=99", 404),
                            ("/queues/4821?game=9", 404), ("/queues/4821?game=x", 400), ("/queues/abc", 404),
                            ("/players/kestrel?metric=bogus", 400), ("/players/a%2Fb", 404)):
            resp, body = await self.get(url)
            self.assertEqual(resp.status, status, url)
            self.assertNotIn("Traceback", body)
            self.assertEqual(resp.headers["Cache-Control"], "public, max-age=10", url)

    async def test_safety_headers_on_every_public_response(self):
        resp, body = await self.get("/")
        self.assertIn("default-src 'self'", resp.headers["Content-Security-Policy"])
        self.assertIn("frame-ancestors 'none'", resp.headers["Content-Security-Policy"])
        self.assertEqual(resp.headers["X-Robots-Tag"], "noindex")
        self.assertIn('<meta name="robots" content="noindex">', body)
        self.assertNotIn("style=", body)  # strict CSP: no inline styles
        self.assertNotIn("<script>", body)
        resp, _ = await self.get("/healthz")
        self.assertEqual((resp.status, resp.headers["Cache-Control"]), (200, "no-store"))
        resp, body = await self.get("/robots.txt")
        self.assertIn("Allow: /", body)

    async def test_admin_is_a_plain_404_with_no_store_when_unconfigured(self):
        unknown, unknown_body = await self.get("/no-such-page")
        for url in ("/admin", "/admin/", "/admin/login"):
            resp, body = await self.get(url)
            self.assertEqual(resp.status, 404)
            self.assertEqual(body, unknown_body)
            self.assertEqual(resp.headers["Cache-Control"], "no-store")

    async def test_queue_page_rosters_scores_and_breakdown(self):
        _, body = await self.get("/queues/4821")
        self.assertIn("Team 1 wins", body)
        self.assertIn("data-replay", body)  # 6 known winners reproduce 4-2
        self.assertIn("MATCH TIMELINE", body)
        self.assertIn("Game breakdown", body)
        _, rows_only = await self.get("/queues/4700")
        self.assertNotIn("MATCH TIMELINE", rows_only)
        self.assertIn("wasn't saved", rows_only)

    async def test_leaderboard_matches_the_bots_ranking_function(self):
        import db
        real = sqlite3.connect(self.path)
        try:
            start, nxt = web_server.month_bounds(2026, 11)
            ranking = db.get_seasonal_queue_stats(f.HOME, start, nxt, "damage", conn=real)
        finally:
            real.close()
        _, body = await self.get("/leaderboard")
        order = re.findall(r'<span class="rank[^"]*">(\d+)</span>', body)
        self.assertEqual(len(order), len(ranking))

    async def test_leaderboard_rows_get_transition_names_from_the_player_url(self):
        _, body = await self.get("/leaderboard")
        names = re.findall(r'data-vt="(vt-[0-9a-f]{12})"', body)
        self.assertEqual(len(names), len(set(names)))
        self.assertTrue(names)

    async def test_an_old_account_slug_redirects_to_the_current_one(self):
        conn = sqlite3.connect(self.path)
        conn.execute("UPDATE users SET slug = 'kestrel2' WHERE discord_id = 111111111111111111")
        conn.commit()
        conn.close()
        resp, _ = await self.get("/players/kestrel")
        self.assertEqual((resp.status, resp.headers["Location"]), (301, "/players/kestrel2"))
        resp, _ = await self.get("/players/kestrel2")
        self.assertEqual(resp.status, 200)

    async def test_a_redirect_never_points_at_a_filtered_slug(self):
        conn = sqlite3.connect(self.path)
        conn.execute("UPDATE users SET slug = 'k123456789012345678' WHERE discord_id = 111111111111111111")
        conn.commit()
        conn.close()
        resp, _ = await self.get("/players/kestrel")
        self.assertEqual(resp.status, 404)
        self.assertNotIn("Location", resp.headers)

    async def test_player_page_carries_all_metrics_and_one_focus_target_per_point(self):
        _, body = await self.get("/players/kestrel")
        self.assertIn('data-trend-json', body)
        buttons = re.findall(r'<button type="button" class="hit"[^>]*aria-label="([^"]+)"', body)
        self.assertTrue(buttons)
        self.assertTrue(all(b.startswith("Queue #") for b in buttons))
        self.assertNotRegex(body, r'aria-hidden="true"[^>]*>\s*<button')
        _, impact = await self.get("/players/kestrel?metric=impact")
        self.assertIn("AVERAGE IMPACT TREND", impact)


class NameTests(SiteTestCase):
    bot = fake_bot({111111111111111111: SimpleNamespace(display_name="MainNick")},
                   other_members={222222222222222222: SimpleNamespace(display_name="OtherServerNick")})

    async def test_current_main_server_names_replace_stored_ones_everywhere(self):
        for url in ("/queues/4821", "/leaderboard", "/hall-of-fame", "/players/kestrel", "/"):
            _, body = await self.get(url)
            self.assertIn("MainNick", body, url)
            self.assertNotIn("KestrelNick", body, url)
            self.assertNotIn("OtherServerNick", body, url)

    async def test_players_missing_from_the_main_cache_keep_their_stored_name(self):
        _, body = await self.get("/queues/4821")
        self.assertIn("JunoNick", body)
        self.assertIn("sl1ce", body)

    async def test_pages_are_cached_for_a_minute_once_names_are_ready(self):
        resp, body = await self.get("/queues/4821")
        self.assertEqual(resp.headers["Cache-Control"], "public, max-age=60")
        resp2, body2 = await self.get("/queues/4821")
        self.assertEqual(body, body2)


class WarmupTests(SiteTestCase):
    bot = fake_bot({111111111111111111: SimpleNamespace(display_name="MainNick")}, chunked=False)

    async def test_before_the_member_cache_is_ready_pages_get_a_short_cache_and_are_not_stored(self):
        resp, _ = await self.get("/queues/4821")
        self.assertEqual(resp.headers["Cache-Control"], "public, max-age=10")
        self.assertEqual(len(self.app["site"].cache.items), 0)


class LimitTests(SiteTestCase):
    async def test_the_cache_never_holds_more_than_256_pages(self):
        cache = self.app["site"].cache
        for i in range(1000):
            cache.put(("x", i), "body")
        self.assertLessEqual(len(cache.items), 256)

    async def test_twenty_cold_requests_admit_four_and_turn_the_rest_away_at_once(self):
        site = self.app["site"]
        release = threading.Event()

        def slow(conn):
            release.wait(5)
            return 1

        async def one():
            try:
                await site.db.run(slow)
                return "ok"
            except web_server.Busy:
                return "busy"

        tasks = [asyncio.ensure_future(one()) for _ in range(20)]
        await asyncio.sleep(0.2)
        self.assertEqual(site.db.admitted, 4)
        release.set()
        results = await asyncio.gather(*tasks)
        self.assertEqual((results.count("ok"), results.count("busy")), (4, 16))

    async def test_a_cancelled_request_keeps_its_slot_until_the_job_finishes(self):
        site = self.app["site"]
        release = threading.Event()
        task = asyncio.ensure_future(site.db.run(lambda conn: release.wait(5)))
        await asyncio.sleep(0.1)
        task.cancel()
        await asyncio.sleep(0.1)
        self.assertEqual(site.db.admitted, 1)
        release.set()
        await asyncio.sleep(0.2)
        self.assertEqual(site.db.admitted, 0)

    async def test_a_locked_database_gives_503_quickly(self):
        locker = sqlite3.connect(self.path, isolation_level=None)
        locker.execute("BEGIN EXCLUSIVE")
        try:
            start = time.monotonic()
            resp, _ = await self.get("/queues/4700")
            elapsed = time.monotonic() - start
            self.assertEqual(resp.status, 503)
            self.assertEqual(resp.headers["Retry-After"], "5")
            self.assertLess(elapsed, 1.5)
        finally:
            locker.execute("ROLLBACK")
            locker.close()

    async def test_a_long_query_is_aborted_at_its_deadline(self):
        site = self.app["site"]
        site.db.job_seconds = 0.2

        def endless(conn):
            return conn.execute("WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c) "
                                "SELECT COUNT(*) FROM c").fetchone()

        start = time.monotonic()
        with self.assertRaises(web_server.Busy):
            await site.db.run(endless)
        self.assertLess(time.monotonic() - start, 2)


class ReadOnlyUriTests(unittest.TestCase):
    def test_an_awkward_path_opens_the_intended_file_read_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "odd #dir% name"
            folder.mkdir()
            path = folder / "x #1%.db"
            conn = f.make_db(str(path))
            conn.close()
            webdb = web_server.WebDB(str(path))
            ro = webdb.connect()
            try:
                self.assertEqual(ro.execute("SELECT COUNT(*) FROM users").fetchone()[0], 0)
                with self.assertRaises(sqlite3.OperationalError):
                    ro.execute("INSERT INTO users VALUES (1, 't', NULL, NULL)")
            finally:
                ro.close()
                webdb.executor.shutdown()
            self.assertEqual(sorted(p.name for p in folder.iterdir()), ["x #1%.db"])


class TimelineReplayTests(unittest.TestCase):
    def queue(self, winners, score, has_games=True):
        games = [{"number": i + 1, "winner": w, "teams": [[], []]} for i, w in enumerate(winners)] if has_games else []
        return {"match_id": "1", "teams": [[], []], "games": games, "winner": 0, "score": score, "mvp": None,
                "date_ms": None, "date_is_saved": False, "duration_ms": None, "has_snapshot": has_games,
                "total_games": len(winners)}

    def ctx(self, queue):
        site = SimpleNamespace(player=lambda *a: {"name": "x", "url": None, "linked": True})
        return web_server.queue_context(site, queue, {}, {}, len(queue["games"]))

    def test_replay_only_when_known_winners_reproduce_the_score(self):
        self.assertTrue(self.ctx(self.queue([0, 1, 0], [2, 1]))["replay"])
        self.assertTrue(self.ctx(self.queue([0, None, 0], [2, 0]))["replay"])  # unknown game counts for nobody
        self.assertFalse(self.ctx(self.queue([0, None, 0], [2, 1]))["replay"])
        self.assertFalse(self.ctx(self.queue([], [1, 4], has_games=False))["replay"])


class HallOfFameLabelTests(unittest.TestCase):
    def test_labels_match_the_bots(self):
        import bot_config
        self.assertEqual(web_server.HALL_OF_FAME_LABELS, bot_config.HALL_OF_FAME_RECORDS)


if __name__ == "__main__":
    unittest.main()
