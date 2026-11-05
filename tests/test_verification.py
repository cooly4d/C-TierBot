"""The /verify flow (survev.de OAuth device code), driven against a fake survev.de with sleeps skipped."""
import asyncio
import time
import unittest
from unittest.mock import AsyncMock, patch

import survev_client

DEVICE_URL = "https://survev.de/api/oauth/device/code"
TOKEN_URL = "https://survev.de/api/oauth/token"
LINK_URL = "https://survev.de/api/external/discord_link"
DEVICE_CODE = {"deviceCode": "dev-1", "userCode": "ABCD-1234", "interval": 5, "expiresIn": 300,
               "verificationUriComplete": "https://survev.de/device?code=ABCD-1234"}


class FakeResponse:
    def __init__(self, status, payload=None, headers=None):
        self.status, self._payload, self.headers = status, payload, headers or {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def json(self, content_type="application/json"):
        return self._payload

    async def text(self):
        return ""


class FakeSurvev:
    """Hands out queued responses per URL, in order, and records every request."""

    def __init__(self, routes):
        self.routes = {url: list(responses) for url, responses in routes.items()}
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.routes[url].pop(0)


class VerificationFlowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.sender = AsyncMock()
        self.state = {"senders": [self.sender], "uri": None, "code": None, "deadline": None}
        self.save_token = self.enterContext(patch.object(survev_client, "save_token"))
        self.enterContext(patch("survev_client.asyncio.sleep", new=AsyncMock()))

    async def run_flow(self, routes, linked_token=None):
        survev = FakeSurvev(routes)
        with patch.object(survev_client, "http_session", survev), \
                patch.object(survev_client, "get_user_token", return_value=linked_token):
            await survev_client._run_verification_flow(7, self.state)
        return survev

    def messages(self):
        return [call.kwargs.get("content") for call in self.sender.await_args_list if call.kwargs.get("content")]

    async def test_link_is_saved_once_the_player_approves(self):
        survev = await self.run_flow({
            DEVICE_URL: [FakeResponse(200, DEVICE_CODE)],
            TOKEN_URL: [FakeResponse(400, {"error": "authorization_pending"}),
                        FakeResponse(429, headers={"Retry-After": "2"}),  # survev.de busy: keep waiting
                        FakeResponse(200, {"accessToken": "tok-new"})],
            LINK_URL: [FakeResponse(200, {"slug": "awang", "username": "Awang"})],
        })

        first_update = self.sender.await_args_list[0].kwargs["embed"]
        self.assertIn("ABCD-1234", first_update.description)
        self.assertEqual(self.state["code"], "ABCD-1234")
        self.save_token.assert_called_once_with(7, "tok-new", "awang", "Awang")
        self.assertEqual(self.messages(), ["✅ **Account Linked!**"])
        self.assertEqual(survev.calls[-1][1]["headers"], {"Authorization": "Bearer tok-new"})

    async def test_a_link_without_a_profile_still_saves_and_says_so(self):
        await self.run_flow({
            DEVICE_URL: [FakeResponse(200, DEVICE_CODE)],
            TOKEN_URL: [FakeResponse(200, {"accessToken": "tok-new"})],
            LINK_URL: [FakeResponse(500)] * survev_client.SLUG_LOOKUP_ATTEMPTS,
        })

        self.save_token.assert_called_once_with(7, "tok-new", None, None)
        self.assertIn("fill it in automatically", self.messages()[0])

    async def test_declining_on_survev_ends_the_flow_without_saving(self):
        await self.run_flow({
            DEVICE_URL: [FakeResponse(200, DEVICE_CODE)],
            TOKEN_URL: [FakeResponse(400, {"error": "access_denied"})],
        })

        self.save_token.assert_not_called()
        self.assertIn("declined", self.messages()[0])

    async def test_rate_limited_device_code_requests_are_retried_then_explained(self):
        survev = await self.run_flow({DEVICE_URL: [FakeResponse(429)] * survev_client.DEVICE_CODE_ATTEMPTS})

        self.assertEqual(len(survev.calls), survev_client.DEVICE_CODE_ATTEMPTS)
        self.assertIn("rate limiting", self.messages()[0])
        self.save_token.assert_not_called()

    async def test_timing_out_keeps_an_existing_link(self):
        await self.run_flow({DEVICE_URL: [FakeResponse(200, {**DEVICE_CODE, "expiresIn": 0})]}, linked_token="tok-old")

        self.save_token.assert_not_called()
        self.assertIn("already linked, so nothing changed", self.messages()[0])


class PendingVerificationTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_second_request_joins_the_pending_link_instead_of_starting_another(self):
        release = asyncio.Event()
        started = []

        async def fake_flow(discord_user_id, state):
            started.append(discord_user_id)
            state["uri"], state["code"], state["deadline"] = "https://survev.de/device?code=ABCD", "ABCD", time.monotonic() + 60
            await release.wait()

        first, second = AsyncMock(), AsyncMock()
        with patch.object(survev_client, "_run_verification_flow", side_effect=fake_flow):
            task = asyncio.create_task(survev_client.run_survev_verification(7, first))
            await asyncio.sleep(0)
            await survev_client.run_survev_verification(7, second)
            release.set()
            await task

        self.assertEqual(started, [7])
        embed = second.await_args.kwargs["embed"]
        self.assertIn("ABCD", embed.description)
        self.assertIn("already had a link pending", embed.footer.text)
        self.assertNotIn(7, survev_client._active_verifications)


if __name__ == "__main__":
    unittest.main()
