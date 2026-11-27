"""The operator panel: config, password tool, sessions, login, throttle, logout and the read-only pages."""
import asyncio
import json
import logging
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path

from aiohttp.test_utils import TestClient, TestServer

import web_admin
import web_server
try:
    import web_fixture as f
except ImportError:  # run as tests.test_web_admin
    from tests import web_fixture as f
from tools import make_admin_password

ORIGIN = "https://ctier.example"
PASSWORD = "correct-horse-battery-staple-42"
FAST_HASH = web_admin.hash_password(PASSWORD, n=2 ** 14)
SECRET = "ab" * 32


def config(hash_text=FAST_HASH, secret=SECRET, origin=ORIGIN):
    return web_admin.parse_admin_config(origin, hash_text, secret)


class ConfigTests(unittest.TestCase):
    def test_all_three_values_are_needed_and_values_are_never_logged(self):
        self.assertIsNone(web_admin.parse_admin_config("", "", ""))
        with self.assertLogs("web_admin", level=logging.ERROR) as logs:
            self.assertIsNone(web_admin.parse_admin_config(ORIGIN, "scrypt:bad", SECRET))
            self.assertIsNone(web_admin.parse_admin_config(ORIGIN, FAST_HASH, "nothex"))
            self.assertIsNone(web_admin.parse_admin_config("ftp://x", FAST_HASH, SECRET))
        text = "\n".join(logs.output)
        self.assertNotIn(SECRET, text)
        self.assertNotIn(FAST_HASH, text)
        self.assertNotIn("nothex", text)
        self.assertIsNotNone(config())

    def test_the_password_tool_makes_a_hash_the_app_verifies(self):
        values = make_admin_password.make()
        self.assertEqual(len(values["password"]), 32)
        self.assertNotIn("$", values["hash"] + values["secret"])
        cfg = web_admin.parse_admin_config(ORIGIN, values["hash"], values["secret"])
        self.assertTrue(web_admin.verify_password(cfg, values["password"]))
        self.assertFalse(web_admin.verify_password(cfg, values["password"] + "x"))
        for bad in ("x" * 15, "x" * 129, "pässwörd-pässwörd", "tab\tin-the-middle-here"):
            with self.assertRaises(ValueError):
                make_admin_password.make(bad)
        self.assertEqual(len(make_admin_password.make("y" * 128)["password"]), 128)

    def test_sessions_reject_tampering_expiry_future_iat_and_old_secrets(self):
        cfg = config()
        now = 1_800_000_000
        good = web_admin.make_session(cfg, now)
        self.assertTrue(web_admin.check_session(cfg, good, now + 60))
        self.assertFalse(web_admin.check_session(cfg, good[:-2] + "xx", now))
        self.assertFalse(web_admin.check_session(cfg, good, now + web_admin.SESSION_SECONDS + 1))
        self.assertFalse(web_admin.check_session(cfg, web_admin.make_session(cfg, now + 3600), now))
        other_secret = config(secret="cd" * 32)
        self.assertFalse(web_admin.check_session(other_secret, good, now))
        other_password = config(hash_text=web_admin.hash_password("another-password-1234", n=2 ** 14))
        self.assertFalse(web_admin.check_session(other_password, good, now))
        self.assertFalse(web_admin.check_session(cfg, None, now))

    def test_log_names_are_validated(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "queue_stats_1.json").write_text(json.dumps({"status": "ok", "x": "<script>"}))
            Path(tmp, "queue_stats_2.json").write_text("{not json")
            for bad in ("../secret.json", "/etc/passwd", "queue_stats_1.json.tmp", "queue_stats_x/../../a.json"):
                self.assertEqual(web_admin.read_log(tmp, bad)[0], "invalid")
            self.assertEqual(web_admin.read_log(tmp, "queue_stats_9.json"), ("missing", None))
            self.assertEqual(web_admin.read_log(tmp, "queue_stats_2.json"), ("unreadable", None))
            listed = {l["name"]: l["readable"] for l in web_admin.list_logs(tmp)}
            self.assertEqual(listed, {"queue_stats_2.json": False, "queue_stats_1.json": True})


class AdminHttpTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmp.name) / "site.db")
        f.standard_db(self.path).close()
        self.log_dir = Path(self.tmp.name) / "log"
        self.log_dir.mkdir()
        (self.log_dir / "queue_stats_20261126_a.json").write_text(json.dumps({"status": "ok", "note": "<script>x</script>"}))
        self.now = [1_800_000_000.0]
        self.app = web_server.create_app(f.HOME, self.path, admin_config=config(), log_dir=str(self.log_dir),
                                         clock=lambda: self.now[0])
        self.client = TestClient(TestServer(self.app))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        site = self.app["site"]
        site.db.executor.shutdown(wait=True)
        site.checker.executor.shutdown(wait=True)
        self.tmp.cleanup()

    async def post_login(self, password, origin=ORIGIN, headers=None):
        h = {"Origin": origin, "Content-Type": "application/x-www-form-urlencoded"} if origin else \
            {"Content-Type": "application/x-www-form-urlencoded"}
        h.update(headers or {})
        return await self.client.post("/admin/login", data=f"username=operator&password={password}", headers=h,
                                      allow_redirects=False)

    def session_cookie(self):
        return {"Cookie": f"{web_admin.COOKIE_NAME}={web_admin.make_session(self.app['site'].admin, self.now[0])}"}

    async def test_unauthenticated_requests_redirect_to_a_working_login_form(self):
        resp = await self.client.get("/admin/", allow_redirects=False)
        self.assertEqual((resp.status, resp.headers["Location"]), (303, "/admin/login"))
        self.assertEqual(resp.headers["Cache-Control"], "no-store")
        form = await self.client.get("/admin/login", allow_redirects=False)
        self.assertEqual(form.status, 200)
        self.assertIn('name="password"', await form.text())
        self.assertEqual(form.headers["Cache-Control"], "no-store")
        self.assertEqual(form.headers["Referrer-Policy"], "same-origin")

    async def test_login_sets_a_host_cookie_and_the_panel_loads(self):
        resp = await self.post_login(PASSWORD)
        self.assertEqual((resp.status, resp.headers["Location"]), (303, "/admin/"))
        cookie = resp.headers["Set-Cookie"]
        for part in ("__Host-ctier_admin=", "Secure", "HttpOnly", "SameSite=Strict", "Path=/"):
            self.assertIn(part, cookie)
        self.assertNotIn("Domain", cookie)
        panel = await self.client.get("/admin/", headers=self.session_cookie(), allow_redirects=False)
        body = await panel.text()
        self.assertEqual(panel.status, 200)
        self.assertIn("Operator panel", body)
        self.assertIn("queue_stats_20261126_a.json", body)
        self.assertEqual(panel.headers["Cache-Control"], "no-store")

    async def test_wrong_password_is_401_and_wrong_or_missing_origin_is_403_without_hashing(self):
        resp = await self.post_login("wrong-password-wrong-password")
        self.assertEqual(resp.status, 401)
        self.assertIn("Wrong password", await resp.text())
        calls = []
        self.app["site"].checker.verify = lambda cfg, pw: calls.append(pw) or False
        for origin in ("https://evil.example", None):
            resp = await self.post_login(PASSWORD, origin=origin)
            self.assertEqual(resp.status, 403)
        self.assertEqual(calls, [])

    async def test_twenty_failures_close_login_for_everyone_whatever_ip_they_claim(self):
        calls = []
        self.app["site"].checker.verify = lambda cfg, pw: calls.append(pw) or pw == PASSWORD
        for i in range(19):
            await self.post_login("nope-nope-nope-nope", headers={"CF-Connecting-IP": f"10.0.0.{i}"})
        ok = await self.post_login(PASSWORD)
        self.assertEqual(ok.status, 303)  # 19 failures: the operator still gets in; a success doesn't reset
        await self.post_login("nope-nope-nope-nope", headers={"CF-Connecting-IP": "10.9.9.9"})
        hashed = len(calls)
        blocked = await self.post_login(PASSWORD, headers={"CF-Connecting-IP": "1.2.3.4"})
        self.assertEqual(blocked.status, 429)
        self.assertIn("Retry-After", blocked.headers)
        self.assertEqual(len(calls), hashed)  # refused before hashing
        self.now[0] += 3601
        self.app["site"].guard.clock = lambda: self.now[0]
        again = await self.post_login(PASSWORD)
        self.assertEqual(again.status, 303)

    async def test_one_check_at_a_time_even_when_a_client_gives_up(self):
        started, release = threading.Event(), threading.Event()

        def slow_verify(cfg, pw):
            started.set()
            release.wait(5)
            return False

        self.app["site"].checker.verify = slow_verify
        first = asyncio.ensure_future(self.post_login("first-first-first-first"))
        await asyncio.get_running_loop().run_in_executor(None, started.wait, 5)
        first.cancel()
        await asyncio.sleep(0.1)
        second = await self.post_login("second-second-second-second")
        self.assertEqual(second.status, 429)  # the slot is still held by the running check
        release.set()
        await asyncio.sleep(0.2)
        self.assertFalse(self.app["site"].guard.busy)

    async def test_logout_sends_a_secure_expiry_cookie(self):
        resp = await self.client.post("/admin/logout", headers={"Origin": ORIGIN, **self.session_cookie()},
                                      allow_redirects=False)
        self.assertEqual(resp.status, 303)
        cookie = resp.headers["Set-Cookie"]
        for part in ("__Host-ctier_admin=;", "Secure", "Path=/", "Max-Age=0"):
            self.assertIn(part, cookie)
        self.assertNotIn("Domain", cookie)
        cross = await self.client.post("/admin/logout", headers={"Origin": "https://evil.example", **self.session_cookie()},
                                       allow_redirects=False)
        self.assertEqual(cross.status, 403)
        anonymous = await self.client.post("/admin/logout", headers={"Origin": ORIGIN}, allow_redirects=False)
        self.assertEqual((anonymous.status, anonymous.headers["Location"]), (303, "/admin/login"))

    async def test_log_pages_escape_content_and_reject_bad_names(self):
        page = await self.client.get("/admin/logs/queue_stats_20261126_a.json", headers=self.session_cookie())
        body = await page.text()
        self.assertNotIn("<script>x</script>", body)
        self.assertIn("&lt;script&gt;", body)
        gone = await self.client.get("/admin/logs/queue_stats_gone.json", headers=self.session_cookie())
        self.assertEqual(gone.status, 404)
        self.assertIn("No longer available", await gone.text())
        bad = await self.client.get("/admin/logs/..%2Fsite.db", headers=self.session_cookie())
        self.assertEqual(bad.status, 404)

    async def test_public_pages_ignore_the_admin_cookie(self):
        plain = await (await self.client.get("/hall-of-fame")).text()
        with_cookie = await self.client.get("/hall-of-fame", headers=self.session_cookie())
        self.assertEqual(await with_cookie.text(), plain)
        self.assertNotIn("Set-Cookie", with_cookie.headers)


if __name__ == "__main__":
    unittest.main()
