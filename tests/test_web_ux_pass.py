"""The UI/UX and motion pass (plan r12): panel dates, the login error, metric button wording, row links and the
transition rules that keep motion off under reduced motion."""
import re
import tempfile
import unittest
from pathlib import Path

from aiohttp.test_utils import TestClient, TestServer

import web_admin
import web_server
try:
    import web_fixture as f
    from test_web_admin import ORIGIN, PASSWORD, config
except ImportError:  # run as tests.test_web_ux_pass
    from tests import web_fixture as f
    from tests.test_web_admin import ORIGIN, PASSWORD, config

STATIC = Path(web_server.__file__).resolve().parent / "web" / "static"
CLOCK = lambda: f.ms(2026, 11, 26) / 1000  # noqa: E731


def _rules_outside_media(css: str) -> str:
    """The stylesheet with every @media block removed (what applies under reduced motion too)."""
    out, i = [], 0
    while True:
        j = css.find("@media", i)
        if j < 0:
            out.append(css[i:])
            return "".join(out)
        out.append(css[i:j])
        depth, k = 0, css.index("{", j)
        while True:
            if css[k] == "{":
                depth += 1
            elif css[k] == "}":
                depth -= 1
                if depth == 0:
                    break
            k += 1
        i = k + 1


class FormatTests(unittest.TestCase):
    def test_panel_timestamps_use_the_site_date_format(self):
        self.assertEqual(web_server.fmt_iso("2026-08-06T21:14:01.468369+00:00"), "6 Aug 2026, 21:14 UTC")
        self.assertEqual(web_server.fmt_iso("2026-08-06T23:14:01+02:00"), "6 Aug 2026, 21:14 UTC")
        self.assertEqual(web_server.fmt_iso("2026-08-06T21:14:01"), "6 Aug 2026, 21:14 UTC")  # naive = UTC
        self.assertEqual(web_server.fmt_iso("not a date"), "not a date")
        self.assertEqual(web_server.fmt_iso(""), "—")
        self.assertEqual(web_server.fmt_iso(None), "—")


class StylesheetTests(unittest.TestCase):
    def setUp(self):
        self.site = (STATIC / "site.css").read_text(encoding="utf-8")
        self.board = (STATIC / "leaderboard.css").read_text(encoding="utf-8")

    def test_page_transitions_and_movement_only_without_reduced_motion(self):
        always = _rules_outside_media(self.site) + _rules_outside_media(self.board)
        for moving in ("@view-transition", "view-transition-name", "transition: transform", "stroke-width 140ms",
                       "animation:"):
            self.assertFalse(moving in always, f"{moving!r} applies under reduced motion")
        self.assertIn("@media (prefers-reduced-motion: no-preference)", self.site)
        self.assertIn("@view-transition { navigation: auto; }", self.site)
        # Colour feedback is the one thing that eases under reduced motion as well.
        self.assertIn("transition: color 140ms", always)

    def test_rows_are_named_from_their_data_vt_and_dropped_when_leaving(self):
        self.assertIn("attr(data-vt type(<custom-ident>), none)", self.board)
        self.assertIn(".vt-plain .board-row[data-vt]", self.board)
        js = (STATIC / "site.js").read_text(encoding="utf-8")
        self.assertIn('addEventListener("pageswap"', js)
        self.assertIn('location.pathname === "/leaderboard"', js)


class PageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmp.name) / "site.db")
        f.standard_db(self.path).close()
        self.app = web_server.create_app(f.HOME, self.path, clock=CLOCK, admin_config=config(),
                                         log_dir=str(Path(self.tmp.name) / "log"))
        self.client = TestClient(TestServer(self.app))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        site = self.app["site"]
        site.db.executor.shutdown(wait=True)
        if getattr(site, "checker", None) is not None:
            site.checker.executor.shutdown(wait=True)
        self.tmp.cleanup()

    async def test_the_login_error_is_tied_to_the_password_field(self):
        resp = await self.client.post("/admin/login", data={"password": "wrong-password-wrong-password"},
                                      headers={"Origin": ORIGIN}, allow_redirects=False)
        body = await resp.text()
        self.assertEqual(resp.status, 401)
        field = re.search(r'<input id="password"[^>]*>', body).group(0)
        self.assertIn('aria-invalid="true"', field)
        self.assertIn('aria-describedby="password-error"', field)
        self.assertRegex(body, r'<p class="form-error" id="password-error" role="alert">Wrong password</p>')
        fresh = await (await self.client.get("/admin/login")).text()
        self.assertNotIn("aria-invalid", fresh)
        self.assertNotIn(PASSWORD, body + fresh)

    async def test_metric_buttons_are_worded_like_the_units(self):
        body = await (await self.client.get("/players/kestrel")).text()
        labels = re.findall(r'<a class="pill" href="\?metric=\w+" data-metric="\w+"[^>]*>([^<]+)</a>', body)
        self.assertEqual(labels, ["Avg dmg", "Avg impact", "Kills per game"])

    async def test_each_queue_row_and_board_row_has_exactly_one_link(self):
        body = await (await self.client.get("/")).text()
        rows = re.findall(r'<li class="q-row"[^>]*>(.*?)</li>', body, re.S)
        self.assertTrue(rows)
        self.assertTrue(all(len(re.findall(r"<a ", row)) == 1 for row in rows))
        board = await (await self.client.get("/leaderboard")).text()
        boards = re.findall(r'<li class="board-row"[^>]*>(.*?)</li>', board, re.S)
        self.assertTrue(boards)
        self.assertTrue(all(len(re.findall(r"<a ", row)) <= 1 for row in boards))


if __name__ == "__main__":
    unittest.main()
