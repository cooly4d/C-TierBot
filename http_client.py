"""The one aiohttp session the whole bot shares, and the bot-wide survev.de request limiter.

survev.de rate limits its external API per OAuth app (every request the bot makes counts against one budget)
and its public scoreboard endpoint per IP, so all survev.de requests go through `api_slot()` instead of each
feature creating its own semaphore.
"""
import asyncio
import contextlib

import aiohttp

from bot_config import API_CONCURRENCY_LIMIT

_session: aiohttp.ClientSession | None = None
_session_loop: asyncio.AbstractEventLoop | None = None
_semaphore: asyncio.Semaphore | None = None
_semaphore_loop: asyncio.AbstractEventLoop | None = None


def _real_session() -> aiohttp.ClientSession:
    """The shared session, opened on first use. A new one is opened if the event loop changed (only happens
    between test cases), since a session can't be used outside the loop it was created in."""
    global _session, _session_loop
    loop = asyncio.get_running_loop()
    if _session is None or _session.closed or _session_loop is not loop:
        _session = aiohttp.ClientSession()
        _session_loop = loop
    return _session


class _SharedSession:
    """Stands in for the shared aiohttp.ClientSession (`http_session.post(...)` etc.). The real session is only
    opened when a request is actually made, so code that merely passes it around opens no connections."""

    def __getattr__(self, name):
        return getattr(_real_session(), name)


http_session = _SharedSession()


async def close_http_session():
    """Closes the shared session on shutdown."""
    global _session, _session_loop
    if _session is not None and not _session.closed:
        await _session.close()
    _session, _session_loop = None, None


def _api_semaphore() -> asyncio.Semaphore:
    global _semaphore, _semaphore_loop
    loop = asyncio.get_running_loop()
    if _semaphore is None or _semaphore_loop is not loop:
        _semaphore = asyncio.Semaphore(API_CONCURRENCY_LIMIT)
        _semaphore_loop = loop
    return _semaphore


@contextlib.asynccontextmanager
async def api_slot():
    """Holds one of the API_CONCURRENCY_LIMIT bot-wide survev.de request slots for a single request.
    Take it around one HTTP call only, never around a call that itself takes a slot, or callers can deadlock."""
    async with _api_semaphore():
        yield
