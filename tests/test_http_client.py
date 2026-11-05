"""The shared aiohttp session and the bot-wide survev.de request limiter."""
import asyncio
import unittest

import aiohttp

import http_client
from bot_config import API_CONCURRENCY_LIMIT


class ApiSlotTests(unittest.IsolatedAsyncioTestCase):
    async def test_no_more_than_the_configured_number_of_requests_run_at_once(self):
        active = peak = 0

        async def request():
            nonlocal active, peak
            async with http_client.api_slot():
                active += 1
                peak = max(peak, active)
                await asyncio.sleep(0.01)
                active -= 1

        await asyncio.gather(*(request() for _ in range(API_CONCURRENCY_LIMIT * 3)))

        self.assertEqual(peak, API_CONCURRENCY_LIMIT)


class SharedSessionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self):
        await http_client.close_http_session()

    async def test_the_session_opens_on_first_use_and_is_then_reused(self):
        await http_client.close_http_session()
        self.assertIsNone(http_client._session)

        http_client.http_session.get  # any request method opens it
        first = http_client._session
        self.assertIsInstance(first, aiohttp.ClientSession)
        http_client.http_session.post
        self.assertIs(http_client._session, first)

        await http_client.close_http_session()
        self.assertTrue(first.closed)
        self.assertIsNone(http_client._session)


if __name__ == "__main__":
    unittest.main()
