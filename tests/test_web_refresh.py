"""The site refresh: search (page, instant results, queries), the home page's latest-result band, the loading
screen's wiring, and the bot's "View on website" link buttons."""
import re
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import discord

import discord_ui
import web_queries as q
import web_server
from web_names import safe_label
try:
    import web_fixture as f
    from test_web_server import SiteTestCase
except ImportError:  # run as tests.test_web_refresh
    from tests import web_fixture as f
    from tests.test_web_server import SiteTestCase

ROOT = Path(__file__).resolve().parent.parent
ORIGIN = "https://stats.example.test"


class ParseSearchTests(unittest.TestCase):
    def test_queue_numbers_in_the_forms_people_type(self):
        for raw in ("4821", "#4821", "# 4821", "queue 4821", "Queue#4821", "  4821  "):
            self.assertEqual(web_server.parse_search(raw)[1], "4821", raw)

    def test_names_are_not_queue_numbers_and_long_queries_are_cut(self):
        self.assertEqual(web_server.parse_search("kes trel"), ("kes trel", None))
        self.assertEqual(web_server.parse_search("a   b\n c"), ("a b c", None))
        self.assertEqual(len(web_server.parse_search("x" * 200)[0]), web_server.SEARCH_MAX_CHARS)
        self.assertEqual(web_server.parse_search(None), ("", None))
        self.assertEqual(web_server.parse_search("12345678901"), ("12345678901", None))  # 11 digits: no queue


class MatchPlayersTests(unittest.TestCase):
    site = SimpleNamespace(names=SimpleNamespace(name=lambda discord_id, fallback: safe_label(fallback)))
    directory = [
        {"discord_id": 1, "slug": "ugo-fan", "stored_name": "above", "queues": 13},
        {"discord_id": 2, "slug": "1267353190948", "stored_name": "impact", "queues": 33},
        {"discord_id": 3, "slug": "gman", "stored_name": "gentlemanziank", "queues": 374},
        {"discord_id": 4, "slug": "x", "stored_name": "Player 123", "queues": 99},  # shown as Unknown player
    ]

    def names(self, text):
        return [(p["name"], p["via"]) for p in web_server.match_players(self.site, self.directory, text, 10)]

    def test_name_starts_first_then_most_queues_and_address_matches_show_the_address(self):
        self.assertEqual(self.names("an"), [("gentlemanziank", None), ("above", "ugo-fan")])
        self.assertEqual(self.names("G"), [("gentlemanziank", None), ("above", "ugo-fan")])
        self.assertEqual(self.names("Gentle"), [("gentlemanziank", None)])

    def test_digits_never_match_page_addresses_or_hidden_names(self):
        self.assertEqual(self.names("4"), [])
        self.assertEqual(self.names("123"), [])
        self.assertEqual(self.names("unknown"), [])
        self.assertEqual(self.names(""), [])


class SearchQueryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.conn = f.standard_db(str(Path(self.tmp.name) / "s.db"))
        self.addCleanup(self.conn.close)

    def test_directory_lists_only_players_with_a_page_in_this_guild(self):
        directory = q.player_directory(self.conn, f.HOME)
        self.assertEqual({p["slug"] for p in directory}, {"kestrel", "juno", "moonpie", "okra"})
        kestrel = next(p for p in directory if p["slug"] == "kestrel")
        self.assertEqual(kestrel["queues"], 2)
        self.assertEqual(directory[0]["slug"], "kestrel")  # most queues first
        self.assertNotIn("access_token", str(directory))

    def test_queue_ids_by_prefix_exact_first_then_newest(self):
        self.assertEqual(q.find_queue_ids(self.conn, f.HOME, "4", 10), ["4999", "4821", "4700"])
        self.assertEqual(q.find_queue_ids(self.conn, f.HOME, "48", 10), ["4821"])
        self.assertEqual(q.find_queue_ids(self.conn, f.HOME, "4821", 10), ["4821"])
        self.assertEqual(q.find_queue_ids(self.conn, f.HOME, "1", 10), [])  # the other guild's queue 1 isn't listed
        self.assertEqual(q.find_queue_ids(self.conn, f.HOME, "4%", 10), [])
        self.assertEqual(q.find_queue_ids(self.conn, f.HOME, "4", 1), ["4999"])


class SearchHttpTests(SiteTestCase):
    async def json(self, query):
        resp = await self.client.get("/search.json", params={"q": query})
        self.assertEqual(resp.status, 200)
        self.assertEqual(resp.content_type, "application/json")
        return await resp.json()

    async def test_instant_results_for_names_and_queue_numbers(self):
        data = await self.json("kest")
        self.assertEqual([p["url"] for p in data["players"]], ["/players/kestrel"])
        self.assertEqual(data["queues"], [])
        data = await self.json("#48")
        self.assertEqual([qd["id"] for qd in data["queues"]], ["4821"])
        self.assertEqual(data["queues"][0]["score"], "4 - 2")
        self.assertEqual(data["queues"][0]["url"], "/queues/4821")

    async def test_a_name_match_carries_no_address(self):
        data = await self.json("kestrel")
        self.assertEqual(data["players"][0]["via"], None)

    async def test_empty_and_unmatched_queries(self):
        self.assertEqual(await self.json(""), {"q": "", "players": [], "queues": []})
        data = await self.json("zzzz")
        self.assertEqual((data["players"], data["queues"]), ([], []))

    async def test_no_ids_or_tokens_in_search_responses(self):
        for query in ("", "k", "4", "okra", "OTHERGUILD"):
            for url in ("/search.json", "/search"):
                resp = await self.client.get(url, params={"q": query}, allow_redirects=False)
                body = await resp.text()
                self.assertNotIn(f.CANARY_TOKEN, body)
                self.assertNotIn("OTHERGUILDSECRETNAME", body)
                self.assertNotRegex(body, r"\d{15,}")

    async def test_search_page_lists_results_and_jumps_to_an_exact_queue(self):
        resp, body = await self.get("/search?q=juno")
        self.assertEqual(resp.status, 200)
        self.assertIn('href="/players/juno"', body)
        resp, _ = await self.get("/search?q=%234821")
        self.assertEqual(resp.status, 303)
        self.assertEqual(resp.headers["Location"], "/queues/4821")
        resp, body = await self.get("/search?q=4")
        self.assertEqual(resp.status, 200)  # several queues: a list, not a jump
        self.assertIn('href="/queues/4999"', body)
        resp, body = await self.get("/search")
        self.assertEqual(resp.status, 200)
        self.assertIn("queue number such as", body)

    async def test_search_text_is_escaped(self):
        _, body = await self.get("/search", params={"q": "<b>x</b>"})
        self.assertNotIn("<b>x</b>", body)
        self.assertIn("&lt;b&gt;x&lt;/b&gt;", body)


class HomeAndFrameTests(SiteTestCase):
    async def test_home_opens_with_the_newest_queue(self):
        _, body = await self.get("/")
        self.assertIn('class="featured"', body)
        self.assertIn('href="/queues/4999">Queue #4999</a>', body)
        self.assertIn("Team 1 wins", body)
        self.assertIn("Series score 1 to 0", body)
        self.assertIn("Recent queues", body)

    async def test_every_page_has_the_search_form_and_the_loader_script(self):
        for url in ("/", "/queues", "/leaderboard", "/hall-of-fame", "/search?q=k"):
            _, body = await self.get(url)
            self.assertIn('<form class="search" role="search" action="/search" method="get"', body, url)
            self.assertIn('<script src="/static/boot.js?v=', body, url)
            self.assertIn('class="loader" aria-hidden="true"', body, url)

    async def test_loader_assets_are_served_and_versioned(self):
        for path in ("/static/boot.js", "/static/tesseract.webp"):
            resp = await self.client.get(path)
            self.assertEqual(resp.status, 200, path)
        js = (ROOT / "web" / "static" / "boot.js").read_text(encoding="utf-8")
        self.assertIn("prefers-reduced-motion: reduce", js)
        self.assertIn("sessionStorage", js)
        self.assertNotIn("innerHTML", js)


class QolUnitTests(unittest.TestCase):
    def test_times_are_time_elements_with_the_utc_text(self):
        html = str(web_server.fmt_when(f.ms(2026, 11, 20, 19)))
        self.assertEqual(html, '<time datetime="2026-11-20T19:00Z" data-ms="%d">20 Nov 2026, 19:00 UTC</time>'
                         % f.ms(2026, 11, 20, 19))
        self.assertEqual(web_server.fmt_when(None), "—")

    def test_day_headings(self):
        now = f.ms(2026, 11, 26, 12)
        self.assertEqual(web_server.day_heading(f.ms(2026, 11, 26, 1), now), ("2026-11-26", "Today"))
        self.assertEqual(web_server.day_heading(f.ms(2026, 11, 25, 23), now), ("2026-11-25", "Yesterday"))
        self.assertEqual(web_server.day_heading(f.ms(2026, 11, 20), now), ("2026-11-20", "20 Nov"))
        self.assertEqual(web_server.day_heading(f.ms(2025, 11, 20), now), ("2025-11-20", "20 Nov 2025"))
        self.assertEqual(web_server.day_heading(None, now), ("", "Date unknown"))

    def test_neighbours_stay_in_this_guilds_saved_queues(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = f.standard_db(str(Path(tmp) / "n.db"))
            try:
                self.assertEqual(q.neighbour_queue_ids(conn, f.HOME, "4821"), ("4700", "4999"))
                self.assertEqual(q.neighbour_queue_ids(conn, f.HOME, "4700"), (None, "4821"))  # not the other guild's 1
                self.assertEqual(q.neighbour_queue_ids(conn, f.HOME, "4999"), ("4821", None))
                self.assertEqual(q.neighbour_queue_ids(conn, f.HOME, "x"), (None, None))
            finally:
                conn.close()


class QolHttpTests(SiteTestCase):
    async def test_queue_page_links_its_neighbours_and_keeps_the_notes(self):
        _, body = await self.get("/queues/4821")
        self.assertIn('href="/queues/4700" rel="prev"', body)
        self.assertIn('href="/queues/4999" rel="next"', body)
        self.assertIn("<summary>Notes on these stats</summary>", body)
        self.assertIn('class="legend detail-block"', body)
        self.assertIn("data-copy", body)
        self.assertRegex(body, r'<time datetime="[0-9T:-]+Z" data-ms="\d+">')

    async def test_archive_days_and_leaderboard_games(self):
        _, body = await self.get("/queues")
        self.assertRegex(body, r'data-day-list><li class="q-day"><h2 class="q-day-title">')
        self.assertEqual(body.count('class="q-day"'), 3)  # three different days in the fixture
        _, board = await self.get("/leaderboard")
        self.assertIn('<span class="games-count">', board)
        self.assertNotIn('games-count detail', board)
        _, home = await self.get("/")
        self.assertIn('games-count detail', home)

    async def test_search_suggestions_carry_the_instant(self):
        resp = await self.client.get("/search.json", params={"q": "4821"})
        data = await resp.json()
        self.assertEqual(data["queues"][0]["ms"], f.ms(2026, 11, 20))

    async def test_most_damage_game_record_opens_its_game(self):
        conn = sqlite3.connect(self.path)
        conn.execute("INSERT INTO hall_of_fame VALUES ('most_damage_game', 1500, 111111111111111111, 'KestrelNick', "
                     "'4821', ?, '2026-11-20T19:05:00+00:00', NULL, 2)", (f.HOME,))
        conn.commit()
        conn.close()
        _, body = await self.get("/hall-of-fame")
        self.assertIn('href="/queues/4821?game=2#games"', body)
        conn = sqlite3.connect(self.path)
        conn.execute("UPDATE hall_of_fame SET game_number = 9 WHERE record_type = 'most_damage_game'")
        conn.commit()
        conn.close()
        self.app["site"].cache.items.clear()
        _, body = await self.get("/hall-of-fame")
        self.assertIn('href="/queues/4821">Queue #4821, Game 9</a>', body)  # no such game saved: the queue only


class ArchiveOrderTests(SiteTestCase):
    async def test_a_rerun_old_queue_stays_in_its_place(self):
        # Queue 4500 has only a snapshot, saved again just now (a /queue_stats rerun): it must not become the newest.
        conn = sqlite3.connect(self.path)
        f.add_snapshot(conn, f.HOME, 4500, [f.game(1, 0, [f.prow(0, "Old", kills=1, damage=500)])],
                       winner=0, score=(1, 0), saved_at="2026-11-26T00:00:00+00:00")
        conn.commit()
        self.assertEqual(q.list_queue_ids(conn, f.HOME, 10, 0), ["4999", "4821", "4700", "4500"])
        conn.close()
        _, home = await self.get("/")
        self.assertIn('href="/queues/4999">Queue #4999</a>', home)
        _, archive = await self.get("/queues")
        # 4999 is snapshot-only too: no data-ms, and no heading of its own from its save date.
        self.assertNotRegex(archive, r'<li class="q-row" data-ms="[0-9]+">\s*<a class="q-id" href="/queues/4500"')
        self.assertEqual(archive.index('href="/queues/4999"') < archive.index('href="/queues/4500"'), True)
        # 4999 (saved date only) opens with "Date unknown"; 4821 and 4700 have their days; 4500 joins 4700's.
        self.assertEqual(re.findall(r'q-day-title">([^<]+)<', archive), ["Date unknown", "20 Nov", "10 Nov"])


class SiteButtonTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        discord_ui._queue_result_views.clear()
        self.addCleanup(discord_ui._queue_result_views.clear)
        patcher = mock.patch.object(discord_ui, "WEB_PUBLIC_ORIGIN", ORIGIN)
        patcher.start()
        self.addCleanup(patcher.stop)

    @staticmethod
    def links(view):
        return [c.url for c in (view.children if view else []) if getattr(c, "style", None) == discord.ButtonStyle.link]

    def test_links_only_for_the_home_server_over_https(self):
        self.assertEqual(discord_ui.site_url(discord_ui.HOME_GUILD_ID, "/queues/1"), f"{ORIGIN}/queues/1")
        self.assertIsNone(discord_ui.site_url(f.OTHER, "/queues/1"))
        self.assertIsNone(discord_ui.site_url(None, "/queues/1"))
        with mock.patch.object(discord_ui, "WEB_PUBLIC_ORIGIN", ""):
            self.assertIsNone(discord_ui.site_url(discord_ui.HOME_GUILD_ID, "/"))
        with mock.patch.object(discord_ui, "WEB_PUBLIC_ORIGIN", "http://127.0.0.1:8080"):
            self.assertIsNone(discord_ui.site_url(discord_ui.HOME_GUILD_ID, "/"))

    async def test_finished_home_queues_link_to_their_page(self):
        view = discord_ui.queue_result_view(discord_ui.HOME_GUILD_ID, "4821", True)
        self.assertEqual(self.links(view), [f"{ORIGIN}/queues/4821"])
        self.assertTrue(view.is_persistent())
        self.assertEqual([c.custom_id for c in view.children if c.custom_id],
                         ["queue_result_games", "queue_result_verify"])
        self.assertEqual(view.children[-1].label, "View on website")

    async def test_other_cases_keep_the_shared_view(self):
        shared = discord_ui.get_queue_result_view(True)
        self.assertIs(discord_ui.queue_result_view(f.OTHER, "4821", True), shared)
        self.assertIs(discord_ui.queue_result_view(discord_ui.HOME_GUILD_ID, "#4821", True), shared)
        self.assertIs(discord_ui.queue_result_view(discord_ui.HOME_GUILD_ID, "4821", False),
                      discord_ui.get_queue_result_view(False))
        self.assertEqual(self.links(shared), [])

    async def test_season_leaderboard_and_hall_of_fame_links(self):
        view = discord_ui.SeasonalLeaderboardView("2026-11", 0, 3, "impact",
                                                  discord_ui.site_url(discord_ui.HOME_GUILD_ID,
                                                                      "/leaderboard?month=2026-11&stat=impact"))
        self.assertEqual(self.links(view), [f"{ORIGIN}/leaderboard?month=2026-11&stat=impact"])
        self.assertEqual(self.links(discord_ui.SeasonalLeaderboardView("2026-11", 0, 3, "impact")), [])
        self.assertEqual(self.links(discord_ui.hall_of_fame_view(discord_ui.HOME_GUILD_ID)), [f"{ORIGIN}/hall-of-fame"])
        self.assertIsNone(discord_ui.hall_of_fame_view(f.OTHER))

    async def test_player_page_link_follows_the_sites_own_rule(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "b.db")
            f.standard_db(path).close()
            with mock.patch.object(discord_ui, "DB_PATH", path), \
                    mock.patch.object(discord_ui, "HOME_GUILD_ID", f.HOME):
                self.assertEqual(await discord_ui.player_page_url(f.HOME, 111111111111111111),
                                 f"{ORIGIN}/players/kestrel")
                self.assertIsNone(await discord_ui.player_page_url(f.HOME, 999))  # no page
                self.assertIsNone(await discord_ui.player_page_url(f.OTHER, 111111111111111111))
                view = discord_ui.ProfileView(f.HOME, 1, "overview", site=f"{ORIGIN}/players/kestrel")
                self.assertEqual(self.links(view), [f"{ORIGIN}/players/kestrel"])
            with mock.patch.object(discord_ui, "DB_PATH", str(Path(tmp) / "missing" / "x.db")), \
                    mock.patch.object(discord_ui, "HOME_GUILD_ID", f.HOME):
                self.assertIsNone(await discord_ui.player_page_url(f.HOME, 111111111111111111))


if __name__ == "__main__":
    unittest.main()
