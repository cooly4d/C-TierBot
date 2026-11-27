"""The public website and operator panel, served by the bot process (aiohttp on the bot's event loop).

Safety rules, all enforced here:
- Every query runs in a dedicated two-thread executor on a read-only SQLite connection, behind a non-waiting
  admission counter (4 jobs at most; a fifth request gets 503 at once). A job has 2 seconds in all; a progress
  handler aborts it then, or at once when the server stops. A job's queries share one read transaction, so a
  page never mixes two states of the database. The operator panel's file reads use the same executor,
  admission and deadline, and shutdown waits for them too.
- Rendered public pages are cached for 60 s (256 entries). Nothing renders Pillow images.
- Public pages never set or read cookies. The operator panel (/admin) is mounted only when its config is valid.
  Its console runs the bot's console commands through the object the bot passes in (console_service.PanelConsole);
  this module never imports the console or Discord.

This module never imports `db` at import time, so the local preview can point DB_PATH at an export first:
    python web_server.py --db PATH [--port 8080]
"""
import asyncio
import hashlib
import logging
import math
import os
import re
import secrets
import sqlite3
import sys
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from aiohttp import web
from jinja2 import Environment, FileSystemLoader, select_autoescape

import web_admin
import web_queries as q
from web_charts import METRICS, bar_fractions, format_value, round_half_up, trend_chart
from web_names import UNKNOWN_PLAYER, NameResolver, public_queue_id, safe_label, safe_slug

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent
TEMPLATES_DIR = ROOT / "web" / "templates"
STATIC_DIR = ROOT / "web" / "static"
QUEUES_PER_PAGE = 25
HISTORY_PER_PAGE = 25
TREND_QUEUES = 16
STATS = ("damage", "impact", "kills")
STAT_LABELS = {"damage": "Avg dmg", "impact": "Avg impact", "kills": "Avg kills"}
# The player chart's metric buttons, worded like the chart's units and the leaderboard.
METRIC_PILLS = {"damage": "Avg dmg", "impact": "Avg impact", "kills": "Kills per game"}
# Same labels as bot_config.HALL_OF_FAME_RECORDS (a test keeps them equal); not imported, so this module
# doesn't create the Discord client.
HALL_OF_FAME_LABELS = {
    "most_kills": "Most Kills in a Queue",
    "most_avg_damage": "Most Avg Damage in a Queue",
    "most_damage_game": "Most Damage in a Single Game",
    "longest_queue": "Longest Queue Duration",
}
CSP = ("default-src 'self'; img-src 'self'; style-src 'self'; script-src 'self'; font-src 'self'; "
       "connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
PUBLIC_MAX_AGE = "public, max-age=60"
WARMUP_MAX_AGE = "public, max-age=10"
ERROR_MAX_AGE = "public, max-age=10"


class Busy(Exception):
    """The site is at capacity, the DB is locked, a job ran out of time, or the server is stopping."""


class WebDB:
    """Read-only SQLite jobs (and the panel's file reads) in a dedicated executor with non-waiting admission."""

    def __init__(self, db_path: str, workers: int = 2, capacity: int = 4, job_seconds: float = 2.0,
                 busy_timeout: float = 0.25):
        self.uri = Path(db_path).resolve().as_uri() + "?mode=ro"
        self.executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="web-db")
        self.capacity = capacity
        self.job_seconds = job_seconds
        self.busy_timeout = busy_timeout
        self.admitted = 0
        self.stopping = False
        self._lock = threading.Lock()
        self.running: set = set()

    def connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.uri, uri=True, timeout=self.busy_timeout, check_same_thread=False)

    def _release(self, future):
        with self._lock:
            self.admitted -= 1
            self.running.discard(future)

    async def run(self, fn, *args):
        """`fn(conn, *args)` on a read-only connection, all its queries in one read transaction."""
        return await self._submit(self._job, fn, args)

    async def run_files(self, fn, *args):
        """`fn(*args)` for the operator panel's file reads: same admission, deadline and shutdown tracking. `fn`
        calls web_queries.checkpoint() between files."""
        return await self._submit(self._file_job, fn, args)

    async def _submit(self, worker, fn, args):
        with self._lock:
            if self.stopping or self.admitted >= self.capacity:
                raise Busy()
            self.admitted += 1
        deadline = time.monotonic() + self.job_seconds
        try:
            future = self.executor.submit(worker, deadline, fn, args)
        except RuntimeError:
            with self._lock:
                self.admitted -= 1
            raise Busy()
        with self._lock:
            self.running.add(future)
        future.add_done_callback(self._release)
        try:
            return await asyncio.wrap_future(future)
        except sqlite3.OperationalError as exc:
            text = str(exc).lower()
            if "interrupt" in text or "locked" in text or "busy" in text:
                raise Busy() from exc
            raise

    def _expired(self, deadline: float) -> bool:
        return self.stopping or time.monotonic() > deadline

    def _job(self, deadline: float, fn, args):
        # A queued job whose time already ran out never starts; one that overran never returns its result.
        if self._expired(deadline):
            raise sqlite3.OperationalError("interrupted")
        q._job.deadline = deadline
        q._job.stopping = lambda: self.stopping
        conn = self.connect()
        try:
            conn.set_progress_handler(lambda: 1 if self._expired(deadline) else 0, 1000)
            # One read transaction for the whole job: every SELECT sees the same commit (a queue saved between
            # the archive's count and its list can't appear in one and not the other).
            conn.execute("BEGIN")
            result = fn(conn, *args)
            if self._expired(deadline):
                raise sqlite3.OperationalError("interrupted")
            return result
        finally:
            try:
                conn.rollback()
            finally:
                conn.close()
                q._job.deadline = None
                q._job.stopping = None

    def _file_job(self, deadline: float, fn, args):
        if self._expired(deadline):
            raise sqlite3.OperationalError("interrupted")
        q._job.deadline = deadline
        q._job.stopping = lambda: self.stopping
        try:
            result = fn(*args)
            if self._expired(deadline):
                raise sqlite3.OperationalError("interrupted")
            return result
        finally:
            q._job.deadline = None
            q._job.stopping = None

    def begin_stop(self):
        self.stopping = True

    async def stop(self, deadline: float):
        self.stopping = True
        self.executor.shutdown(wait=False, cancel_futures=True)
        while True:
            with self._lock:
                pending = [f for f in self.running if not f.done()]
            if not pending:
                return
            if time.monotonic() >= deadline:
                log.warning("Website shutdown: %d worker job(s) still running at the deadline.", len(pending))
                return
            await asyncio.sleep(0.02)


class PageCache:
    def __init__(self, ttl: float = 60.0, max_entries: int = 256, clock=time.monotonic):
        self.ttl, self.max_entries, self.clock = ttl, max_entries, clock
        self.items: OrderedDict = OrderedDict()

    def get(self, key):
        item = self.items.get(key)
        if item is None:
            return None
        stored_at, body = item
        if self.clock() - stored_at > self.ttl:
            self.items.pop(key, None)
            return None
        self.items.move_to_end(key)
        return body

    def put(self, key, body):
        self.items[key] = (self.clock(), body)
        self.items.move_to_end(key)
        while len(self.items) > self.max_entries:
            self.items.popitem(last=False)


# --- Formatting ------------------------------------------------------------------------------------------------

def fmt_int(value):
    return "—" if value is None else f"{int(value):,}"


def fmt_avg(value):
    # The cards and /leaderboard_season format averages with Python's {:,.0f}.
    return "—" if value is None else format(float(value), ",.0f")


def fmt_impact(value):
    return "—" if value is None else str(round_half_up(float(value)))


def fmt_kpg(value):
    return "—" if value is None else f"{float(value):.1f}"


def fmt_duration(ms):
    if not ms:
        return "—"
    minutes = int(ms) // 60000
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m" if hours else f"{minutes}m"


def fmt_date(ms):
    if not ms:
        return "—"
    dt = datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc)
    return f"{dt.day} {dt.strftime('%b %Y, %H:%M')} UTC"


def fmt_iso(text):
    """A stored ISO timestamp in the site's date format (UTC); text that doesn't parse is shown as it is."""
    if not text:
        return "—"
    try:
        dt = datetime.fromisoformat(str(text))
    except ValueError:
        return str(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return fmt_date(int(dt.timestamp() * 1000))


def fmt_playtime(ms):
    if not ms:
        return "0m"
    minutes = int(ms) // 60000
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m"


def month_bounds(year: int, month: int) -> tuple[int, int]:
    start = datetime(year, month, 1, tzinfo=timezone.utc)
    nxt = datetime(year + (month == 12), 1 if month == 12 else month + 1, 1, tzinfo=timezone.utc)
    return int(start.timestamp() * 1000), int(nxt.timestamp() * 1000)


def shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    index = year * 12 + (month - 1) + delta
    return index // 12, index % 12 + 1


def month_name(year: int, month: int) -> str:
    return datetime(year, month, 1).strftime("%B %Y")


def score_text(score) -> str:
    return " - ".join(str(s) for s in score) if score else "—"


def asset_version(static_dir: Path) -> str:
    digest = hashlib.sha256()
    for name in ("site.css", "site.js", "leaderboard.css"):
        try:
            digest.update((static_dir / name).read_bytes())
        except OSError:
            pass
    return digest.hexdigest()[:10]


def tokens_css() -> str:
    import design_tokens as t

    def hexc(rgb):
        return "#%02X%02X%02X" % rgb

    names = {
        "canvas": t.CANVAS, "header-top": t.HEADER_TOP, "header-bottom": t.HEADER_BOTTOM, "panel": t.PANEL,
        "panel-raised": t.PANEL_RAISED, "divider": t.DIVIDER, "timeline-fill": t.TIMELINE_FILL,
        "text": t.TEXT, "text-muted": t.TEXT_MUTED, "accent": t.ACCENT, "win": t.WIN, "lose": t.LOSE,
        "win-banner": t.WIN_BANNER, "lose-banner": t.LOSE_BANNER, "neutral-banner": t.NEUTRAL_BANNER,
        "timeline-lane": t.TIMELINE_LANE,
    }
    lines = [f"  --{k}: {hexc(v)};" for k, v in names.items()]
    return "/* Generated from design_tokens.py: the card palette, shared with the website. */\n:root {\n" \
        + "\n".join(lines) + "\n}\n"


# --- The application ---------------------------------------------------------------------------------------------

class Site:
    def __init__(self, guild_id: int, db_path: str, bot=None, admin_config=None, log_dir: str = "log",
                 backup_dir: str | None = None, clock=time.time, started_at: float | None = None, console=None):
        self.guild_id = guild_id
        self.db = WebDB(db_path)
        self.names = NameResolver(bot, guild_id)
        self.bot = bot
        self.console = console  # the panel console (console_service.PanelConsole), or None in the preview
        # Recent console runs by their form's run ID: {run_id: {"session", "expires", "command", "entry"}}. A
        # resubmitted form (Back, a retry, a double click) gets the recorded output instead of running the command
        # again, and the no-JS reply is a redirect to the panel showing it.
        self.console_runs: OrderedDict = OrderedDict()
        self.form_readers = 0  # login and console bodies being read right now
        self.cache = PageCache()
        self.admin = admin_config
        self.guard = web_admin.LoginGuard(clock=clock)
        self.checker = web_admin.PasswordChecker(admin_config, self.guard) if admin_config else None
        self.log_dir = log_dir
        self.backup_dir = (backup_dir or os.getenv("BACKUP_DIR")
                           or os.path.join(os.path.dirname(os.path.abspath(db_path)), "backups"))
        self.clock = clock
        self.started_at = started_at or time.time()
        self.env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)), autoescape=select_autoescape(["html"]),
                               trim_blocks=True, lstrip_blocks=True)
        self.env.filters.update(fmt_int=fmt_int, fmt_avg=fmt_avg, fmt_impact=fmt_impact, fmt_kpg=fmt_kpg,
                                fmt_duration=fmt_duration, fmt_date=fmt_date, fmt_iso=fmt_iso,
                                fmt_playtime=fmt_playtime, score_text=score_text)
        self.version = asset_version(STATIC_DIR)
        self.tokens = tokens_css()

    # rendering -----------------------------------------------------------------
    def render(self, template: str, **ctx) -> str:
        ctx.setdefault("v", self.version)
        return self.env.get_template(template).render(**ctx)

    def player(self, discord_id, stored_name, newest: dict, slugs: dict) -> dict:
        if discord_id is None:
            return {"name": safe_label(stored_name), "url": None, "linked": False}
        name = self.names.name(discord_id, newest.get(discord_id) or stored_name)
        slug = slugs.get(discord_id) if name != UNKNOWN_PLAYER else None
        return {"name": name, "url": f"/players/{slug}" if slug else None, "linked": True}

    def cached(self, key):
        """A cached page, only while the member cache is ready. When it isn't (a guild outage, a re-chunk), every
        cached page is dropped, since it may hold names the fallback wouldn't show."""
        if not self.names.ready:
            self.cache.items.clear()
            return None
        return self.cache.get(key)

    async def page(self, request, key, job, build, template: str, nav: str):
        """Serve a cacheable public page: cached HTML, or run `job` in the DB executor and render."""
        cached = self.cached(key)
        if cached is not None:
            return html_response(cached, PUBLIC_MAX_AGE)
        try:
            data = await self.db.run(job)
        except Busy:
            raise web.HTTPServiceUnavailable(headers={"Retry-After": "5"})
        if data is None:
            raise web.HTTPNotFound()
        if isinstance(data, web.HTTPException):
            raise data
        ctx = build(data)
        body = self.render(template, nav=nav, **ctx)
        # Until the member cache is complete, names may still be the stored fallback: short cache, not stored.
        if self.names.ready:
            self.cache.put(key, body)
            return html_response(body, PUBLIC_MAX_AGE)
        return html_response(body, WARMUP_MAX_AGE)


def html_response(body: str, cache_control: str, status: int = 200) -> web.Response:
    return web.Response(text=body, status=status, content_type="text/html", charset="utf-8",
                        headers={"Cache-Control": cache_control})


_DIGITS = re.compile(r"[0-9]{1,6}")


def _int_param(request, name: str, default: int, lo: int, hi: int) -> int:
    raw = request.query.get(name)
    if raw is None:
        return default
    if not _DIGITS.fullmatch(raw):
        raise web.HTTPBadRequest()
    value = int(raw)
    if value < lo or value > hi:
        raise web.HTTPNotFound()
    return value


def create_app(guild_id: int, db_path: str, bot=None, admin_config=None, log_dir: str = "log",
               backup_dir: str | None = None, clock=time.time, console=None) -> web.Application:
    site = Site(guild_id, db_path, bot=bot, admin_config=admin_config, log_dir=log_dir, backup_dir=backup_dir,
                clock=clock, console=console)
    # Outermost first: the header policy wraps everything and error pages wrap the admin gate, so a failure in
    # the gate still gets an error page and the admin headers.
    app = web.Application(middlewares=[headers_middleware, errors_middleware, admin_gate], client_max_size=64 * 1024)
    app["site"] = site
    r = app.router
    r.add_get("/", home)
    r.add_get("/queues", queues)
    r.add_get("/queues/{match_id}", queue_page)
    r.add_get("/leaderboard", leaderboard)
    r.add_get("/players/{slug}", player_page)
    r.add_get("/hall-of-fame", hall_of_fame)
    r.add_get("/healthz", healthz)
    r.add_get("/robots.txt", robots)
    r.add_get("/tokens.css", tokens)
    r.add_static("/static", str(STATIC_DIR), show_index=False, follow_symlinks=False)
    if admin_config is not None:
        r.add_get("/admin", admin_redirect)
        admin = web.Application()
        admin["site"] = site
        admin.router.add_get("/login", admin_login_form)
        admin.router.add_post("/login", admin_login)
        admin.router.add_post("/logout", admin_logout)
        admin.router.add_get("/", admin_panel)
        admin.router.add_get("/console", admin_console_get)
        admin.router.add_post("/console", admin_console)
        admin.router.add_get("/logs/{name}", admin_log)
        app.add_subapp("/admin", admin)
    return app


def is_admin_path(path: str) -> bool:
    return path == "/admin" or path.startswith("/admin/")


@web.middleware
async def headers_middleware(request, handler):
    try:
        response = await handler(request)
    except web.HTTPException as exc:
        response = exc
    apply_header_policy(request, response)
    if isinstance(response, web.HTTPException):
        raise response
    return response


def apply_header_policy(request, response):
    """The site's response headers. headers_middleware applies them to every reply; a handler that sends its reply
    itself (the stop page) applies them before prepare(), when they can still go out."""
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Content-Security-Policy"] = CSP
    response.headers["X-Robots-Tag"] = "noindex"
    response.headers["X-Frame-Options"] = "DENY"
    if is_admin_path(request.path):
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "same-origin"
    else:
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers.pop("Set-Cookie", None)
        if request.path.startswith("/static/") and response.status in (200, 206, 304):
            response.headers["Cache-Control"] = "public, max-age=86400"  # errors keep their own short lifetime


_LOGIN_OPEN = {("GET", "/admin/login"), ("POST", "/admin/login")}


@web.middleware
async def admin_gate(request, handler):
    """With the panel configured, every /admin request except GET and POST /admin/login needs a session; anything
    else (the bare /admin, unknown paths, logout, HEAD or any other method) goes straight to the login page."""
    site: Site = request.app["site"]
    if site.admin is not None and is_admin_path(request.path) and (request.method, request.path) not in _LOGIN_OPEN:
        if not _signed_in(site, request):
            raise web.HTTPSeeOther(location="/admin/login")
    return await handler(request)


@web.middleware
async def errors_middleware(request, handler):
    site: Site = request.app["site"]
    try:
        return await handler(request)
    except web.HTTPException as exc:
        if exc.status in (400, 404, 405):
            title = {400: "Bad request", 404: "Not found", 405: "Not allowed"}[exc.status]
            body = site.render("error.html", nav="", status=exc.status, title=title)
            return html_response(body, ERROR_MAX_AGE, status=exc.status)
        if exc.status == 503:
            body = site.render("error.html", nav="", status=503, title="Busy, try again in a few seconds")
            resp = html_response(body, "no-store", status=503)
            resp.headers["Retry-After"] = exc.headers.get("Retry-After", "5")
            return resp
        raise
    except Busy:
        body = site.render("error.html", nav="", status=503, title="Busy, try again in a few seconds")
        resp = html_response(body, "no-store", status=503)
        resp.headers["Retry-After"] = "5"
        return resp
    except asyncio.CancelledError:
        raise
    except Exception:
        log.exception("Website error on %s", request.path)
        body = site.render("error.html", nav="", status=500, title="Something went wrong")
        return html_response(body, "no-store", status=500)


# --- Public handlers ------------------------------------------------------------------------------------------

async def healthz(request):
    return web.Response(text="ok", headers={"Cache-Control": "no-store"})


async def robots(request):
    return web.Response(text="User-agent: *\nAllow: /\n", headers={"Cache-Control": "public, max-age=3600"})


async def tokens(request):
    return web.Response(text=request.app["site"].tokens, content_type="text/css",
                        headers={"Cache-Control": "public, max-age=86400"})


def _queue_summaries(site: Site, queues: list[dict], newest: dict, slugs: dict) -> list[dict]:
    out = []
    for qd in queues:
        teams = [[site.player(p["discord_id"], p["stored_name"], newest, slugs) for p in team] for team in qd["teams"]]
        out.append({**qd, "rosters": teams})
    return out


def _ids_in(queues) -> set:
    ids = set()
    for qd in queues:
        for team in qd["teams"]:
            ids.update(p["discord_id"] for p in team if p.get("discord_id") is not None)
        for game in qd.get("games") or []:
            for team in game["teams"]:
                ids.update(p["discord_id"] for p in team if p.get("discord_id") is not None)
    return ids


def _current_month(clock) -> tuple[int, int]:
    now = datetime.fromtimestamp(clock(), tz=timezone.utc)
    return now.year, now.month


async def home(request):
    site: Site = request.app["site"]
    year, month = _current_month(site.clock)
    start, nxt = month_bounds(year, month)

    def job(conn):
        from db import get_seasonal_queue_stats
        latest = q.list_queues(conn, site.guild_id, 10, 0)
        board = get_seasonal_queue_stats(site.guild_id, start, nxt, "damage", conn=conn)[:10]
        hof = q.get_hall_of_fame(conn, site.guild_id)
        ids = _ids_in(latest) | {e["discord_id"] for e in board} | {r["discord_id"] for r in hof.values() if r["discord_id"]}
        return latest, board, hof, q.stored_names(conn, site.guild_id, ids), q.player_slugs(conn, site.guild_id, ids)

    def build(data):
        latest, board, hof, newest, slugs = data
        return {
            "latest": _queue_summaries(site, latest, newest, slugs),
            "board_rows": _board_rows(site, board, "damage", newest, slugs),
            "month_label": month_name(year, month),
            "records": _records(site, hof, newest, slugs),
        }

    return await site.page(request, ("home", year, month), job, build, "home.html", "home")


async def queues(request):
    site: Site = request.app["site"]
    page = _int_param(request, "page", 1, 1, 100000)

    def job(conn):
        total = q.count_queues(conn, site.guild_id)
        pages = max(1, math.ceil(total / QUEUES_PER_PAGE))
        if page > pages:
            return None
        items = q.list_queues(conn, site.guild_id, QUEUES_PER_PAGE, (page - 1) * QUEUES_PER_PAGE)
        ids = _ids_in(items)
        return total, pages, items, q.stored_names(conn, site.guild_id, ids), q.player_slugs(conn, site.guild_id, ids)

    def build(data):
        total, pages, items, newest, slugs = data
        return {"total": total, "page": page, "pages": pages, "items": _queue_summaries(site, items, newest, slugs)}

    return await site.page(request, ("queues", page), job, build, "queues.html", "queues")


async def queue_page(request):
    site: Site = request.app["site"]
    match_id = public_queue_id(request.match_info["match_id"])
    if match_id is None:
        raise web.HTTPNotFound()
    raw_game = request.query.get("game")
    if raw_game is not None and not re.fullmatch(r"[0-9]{1,3}", raw_game):
        raise web.HTTPBadRequest()
    wanted = int(raw_game) if raw_game is not None else None

    def job(conn):
        queue = q.get_queue(conn, site.guild_id, match_id)
        if queue is None:
            return None
        ids = _ids_in([queue])
        return queue, q.stored_names(conn, site.guild_id, ids), q.player_slugs(conn, site.guild_id, ids)

    def build(data):
        queue, newest, slugs = data
        games = queue["games"]
        selected = len(games)
        if wanted is not None:
            if not 1 <= wanted <= len(games):
                raise web.HTTPNotFound()
            selected = wanted
        return queue_context(site, queue, newest, slugs, selected)

    key = ("queue", match_id, wanted)
    return await site.page(request, key, job, build, "queue.html", "queues")


def queue_context(site: Site, queue: dict, newest: dict, slugs: dict, selected: int) -> dict:
    winner = queue["winner"]
    team_count = max(len(queue["teams"]), 2)

    def panel_state(team_index, win_index):
        if win_index is None:
            return "neutral"
        return "win" if team_index == win_index else "lose"

    teams = []
    for t, team in enumerate(queue["teams"]):
        players = []
        for i, p in enumerate(team):
            who = site.player(p["discord_id"], p["stored_name"], newest, slugs)
            stats = p.get("stats")
            players.append({
                **who,
                "mvp": queue["mvp"] == (t, i),
                "partial": p.get("partial"),
                "combined": p.get("combined_by_name"),
                "kills": fmt_int(stats["kills"]) if stats else "—",
                "damage": fmt_int(stats["damage"]) if stats else "—",
                "avg": fmt_avg(stats["avg"]) if stats else "—",
                "impact": fmt_impact(stats["impact"]) if stats else "—",
            })
        score = queue["score"]
        record = None
        if score and len(score) == 2 and t < 2:
            record = f"{score[t]}-{score[1 - t]}"
        state = panel_state(t, winner)
        heading = f"Team {t + 1}"
        if state == "win" and record:
            heading += f" · won {record}"
        elif state == "lose" and record:
            heading += f" · lost {record}"
        teams.append({"index": t, "state": state, "heading": heading, "players": players})
    has_impact = any(p["impact"] != "—" for team in teams for p in team["players"])
    has_partial = any(p["partial"] for team in teams for p in team["players"])
    has_guest = any(not p["linked"] for team in teams for p in team["players"])
    has_combined = any(p["combined"] for team in teams for p in team["players"])

    # The timeline replays (score counting up as dots land) only when counting the known per-game winners
    # reproduces the stored series score; otherwise the stored score simply shows from first paint.
    winners_known = [g["winner"] for g in queue["games"]]
    replay = False
    if queue["games"] and queue["score"]:
        counts = [0] * len(queue["score"])
        for w in winners_known:
            if w is not None and w < len(counts):
                counts[w] += 1
        replay = counts == list(queue["score"])
    games = []
    series_counts = [0] * team_count
    exact = True  # the running series score is exact until a game with no known winner (unless replay holds)
    for g in queue["games"]:
        if g["winner"] is None and not replay:
            exact = False
        if g["winner"] is not None and g["winner"] < team_count:
            series_counts[g["winner"]] += 1
        g_teams = []
        for t, team in enumerate(g["teams"]):
            state = panel_state(t, g["winner"])
            label = f"Team {t + 1}" + (" · won" if state == "win" else " · lost" if state == "lose" else "")
            g_teams.append({"state": state, "heading": label, "players": [{
                **site.player(p["discord_id"], p["stored_name"], newest, slugs),
                "kills": fmt_int(p["kills"]), "damage": fmt_int(p["damage"]), "impact": fmt_impact(p["impact"]),
            } for p in team]})
        series = " - ".join(str(c) for c in series_counts[:max(2, len(g["teams"]))]) if exact else None
        dot_state = None
        if g["winner"] is not None:
            dot_state = "neutral" if winner is None else ("win" if g["winner"] == winner else "lose")
        games.append({
            "number": g["number"], "winner": g["winner"], "teams": g_teams, "series": series,
            "has_impact": any(p.get("impact") is not None for team in g["teams"] for p in team),
            "selected": g["number"] == selected, "dot_state": dot_state,
            "label": f"Game {g['number']}, " + (f"Team {g['winner'] + 1} won" if g["winner"] is not None else "winner unknown"),
        })
    lanes = []
    if games:
        for t in range(min(team_count, 3)):
            lanes.append({"team": t, "state": panel_state(t, winner)})
    score = queue["score"]
    score_parts = []
    if score:
        top = max(score)
        for t, value in enumerate(score):
            if winner is None or score.count(top) > 1:
                cls = "neutral"
            else:
                cls = "win" if t == winner else "lose"
            score_parts.append({"value": value, "state": cls})
    sel = next((g for g in games if g["selected"]), None)
    return {
        "queue": queue,
        "title": f"#{queue['match_id']}",
        "result_text": f"Team {winner + 1} wins" if winner is not None else "Result unknown",
        "result_state": "win" if winner is not None else "neutral",
        "score_parts": score_parts,
        "score_label": f"Series score {' to '.join(str(s) for s in score)}" if score else "No series score saved",
        "teams": teams,
        "has_impact": has_impact,
        "has_partial": has_partial,
        "has_guest": has_guest,
        "has_combined": has_combined,
        "example_partial": next((p["partial"] for team in teams for p in team["players"] if p["partial"]), None),
        "games": games,
        "lanes": lanes,
        "replay": replay,
        "winners_attr": ",".join("" if w is None else str(w) for w in winners_known),
        "score_attr": ",".join(str(s) for s in score) if score else "",
        "selected": sel,
    }


def _board_rows(site: Site, board: list[dict], stat: str, newest: dict, slugs: dict) -> list[dict]:
    fractions = bar_fractions([e["value"] for e in board])
    rows = []
    for rank, (entry, frac) in enumerate(zip(board, fractions), start=1):
        who = site.player(entry["discord_id"], entry.get("display_name"), newest, slugs)
        value = entry["value"]
        # Exactly /leaderboard_season's formats (leaderboard_service.SEASON_STATS).
        text = format(value, ",.1f") if stat == "kills" else format(value, ",.0f")
        vt = None
        if who["url"]:
            vt = "vt-" + hashlib.sha256(who["url"].encode()).hexdigest()[:12]
        rows.append({**who, "rank": rank, "games": entry["games"], "value": text, "raw": value,
                     "bar": round(frac * 100, 2), "vt": vt})
    return rows


async def leaderboard(request):
    site: Site = request.app["site"]
    year, month = _current_month(site.clock)
    raw_month = request.query.get("month")
    stat = request.query.get("stat", "damage")
    if stat not in STATS:
        raise web.HTTPBadRequest()
    if raw_month is not None:
        if not re.fullmatch(r"[0-9]{4}-[0-9]{2}", raw_month) or not 1 <= int(raw_month[5:]) <= 12 \
                or int(raw_month[:4]) < 2000:
            raise web.HTTPBadRequest()
        sel = (int(raw_month[:4]), int(raw_month[5:]))
        if sel > (year, month):
            raise web.HTTPNotFound()
    else:
        sel = (year, month)
    start, nxt = month_bounds(*sel)

    def job(conn):
        from db import get_seasonal_queue_stats
        earliest = q.earliest_month(conn, site.guild_id)
        if earliest and sel < earliest:
            return web.HTTPNotFound()
        if not earliest and sel != (year, month):
            return web.HTTPNotFound()
        board = get_seasonal_queue_stats(site.guild_id, start, nxt, stat, conn=conn)
        ids = {e["discord_id"] for e in board}
        return earliest, board, q.stored_names(conn, site.guild_id, ids), q.player_slugs(conn, site.guild_id, ids)

    def build(data):
        earliest, board, newest, slugs = data
        prev = shift_month(*sel, -1)
        nxt_m = shift_month(*sel, 1)
        return {
            "month_label": month_name(*sel),
            "month_key": f"{sel[0]:04d}-{sel[1]:02d}",
            "stat": stat,
            "stat_label": STAT_LABELS[stat],
            "stats": [{"key": s, "label": STAT_LABELS[s], "current": s == stat} for s in STATS],
            "prev": f"{prev[0]:04d}-{prev[1]:02d}" if earliest and prev >= earliest else None,
            "prev_label": datetime(prev[0], prev[1], 1).strftime("%b %Y"),
            "next": f"{nxt_m[0]:04d}-{nxt_m[1]:02d}" if nxt_m <= (year, month) else None,
            "next_label": datetime(nxt_m[0], nxt_m[1], 1).strftime("%b %Y"),
            "rows": _board_rows(site, board, stat, newest, slugs),
        }

    return await site.page(request, ("leaderboard", sel, stat), job, build, "leaderboard.html", "leaderboard")


async def player_page(request):
    site: Site = request.app["site"]
    slug = request.match_info["slug"]
    if not safe_slug(slug):
        raise web.HTTPNotFound()
    metric = request.query.get("metric", "damage")
    if metric not in METRICS:
        raise web.HTTPBadRequest()
    page = _int_param(request, "page", 1, 1, 100000)

    def job(conn):
        found = q.resolve_player_slug(conn, site.guild_id, slug)
        if found is None:
            return None
        kind, discord_id = found
        own_slug = q.player_slugs(conn, site.guild_id, [discord_id]).get(discord_id)
        if kind == "redirect" or own_slug != slug:
            if own_slug and own_slug != slug:
                return ("redirect", own_slug)
            return None
        total = q.count_player_queues(conn, site.guild_id, discord_id)
        pages = max(1, math.ceil(total / HISTORY_PER_PAGE))
        if page > pages:
            return None
        history = q.get_player_history(conn, site.guild_id, discord_id, HISTORY_PER_PAGE, (page - 1) * HISTORY_PER_PAGE)
        trend_rows = q.get_player_history(conn, site.guild_id, discord_id, TREND_QUEUES, 0)
        summary = q.get_player_summary(conn, site.guild_id, discord_id,
                                       now=datetime.fromtimestamp(site.clock(), tz=timezone.utc))
        newest = q.stored_names(conn, site.guild_id, [discord_id])
        return ("page", discord_id, summary, history, trend_rows, total, pages, newest)

    cache_key = ("player", slug, metric, page)
    cached = site.cached(cache_key)
    if cached is not None:
        return html_response(cached, PUBLIC_MAX_AGE)
    try:
        data = await site.db.run(job)
    except Busy:
        raise web.HTTPServiceUnavailable(headers={"Retry-After": "5"})
    if data is None:
        raise web.HTTPNotFound()
    if data[0] == "redirect":
        raise web.HTTPMovedPermanently(location=f"/players/{data[1]}", headers={"Cache-Control": PUBLIC_MAX_AGE})
    _, discord_id, summary, history, trend_rows, total, pages, newest = data
    name = site.names.name(discord_id, newest.get(discord_id))
    year, month = _current_month(site.clock)
    points = [{"match_id": r["match_id"], "won": r["won"], "score": r["score"], "row": r}
              for r in reversed(trend_rows)]
    charts = trend_chart(points)
    chart = charts[metric]
    from web_charts import trend_value

    def table_row(r):
        return {
            "match_id": r["match_id"], "won": r["won"], "score": r["score"],
            "kills_pg": format_value("kills", trend_value(r, "kills")),
            "avg": format_value("damage", trend_value(r, "damage")),
            "impact": format_value("impact", trend_value(r, "impact")),
            "date": r["match_end_ms"] or r["match_start_ms"],
        }

    rows = [table_row(r) for r in history]
    # Page 1's table lists the chart's queues; on older pages the chart still shows the latest ones, so they get a
    # table of their own (the equivalent of the chart's small phone targets).
    chart_rows = [table_row(r) for r in trend_rows] if page > 1 else []
    import json as _json
    won = sum(1 for r in trend_rows if r["won"] is True)
    lost = sum(1 for r in trend_rows if r["won"] is False)
    record = {
        "n": len(trend_rows), "w": won, "l": lost,
        "state": "win" if won > lost else "lose" if lost > won else "neutral",
        "win_rate": round(100 * won / (won + lost)) if won + lost else None,
    }
    payload = {m: {k: charts[m][k] for k in ("empty", "line", "area", "ticks", "latest", "latest_i", "unit",
                                              "title", "empty_text")} | {"slots": [
        {"i": s["i"], "x": s["x"], "y": s["y"], "text": s["text"], "match_id": s["match_id"], "won": s["won"],
         "score": s["score"]} for s in charts[m]["slots"]]} for m in METRICS}
    body = site.render(
        "player.html", nav="", name=name, slug=slug, summary=summary, month_label=month_name(year, month),
        month_short=datetime(year, month, 1).strftime("%B"), record=record,
        chart=chart, metric=metric, metrics=[{"key": m, "label": METRIC_PILLS[m], "current": m == metric}
                                             for m in METRICS],
        slot_class=f"n{min(max(len(points), 1), 16)}", rows=rows, chart_rows=chart_rows, page=page, pages=pages,
        total=total,
        chart_json=_json.dumps(payload, separators=(",", ":")).replace("</", "<\\/"),
    )
    if site.names.ready:
        site.cache.put(cache_key, body)
        return html_response(body, PUBLIC_MAX_AGE)
    return html_response(body, WARMUP_MAX_AGE)


def _records(site: Site, hof: dict, newest: dict, slugs: dict) -> list[dict]:
    out = []
    for key, label in HALL_OF_FAME_LABELS.items():
        rec = hof.get(key)
        if rec is None:
            out.append({"label": label, "empty": True})
            continue
        if key == "longest_queue":
            value = fmt_duration(rec["duration_ms"] if rec["duration_ms"] is not None else rec["value"])
            holder = None
        else:
            value = fmt_avg(rec["value"]) if key in ("most_avg_damage", "most_damage_game") else fmt_int(rec["value"])
            holder = site.player(rec["discord_id"], rec["stored_name"], newest, slugs)
        where = None
        if rec["match_id"]:  # None when the stored number can't be shown or linked
            where = f"Queue #{rec['match_id']}"
            if key == "most_damage_game" and rec.get("game_number"):
                where += f", Game {rec['game_number']}"
        out.append({"label": label, "empty": False, "value": value, "holder": holder, "where": where,
                    "match_id": rec["match_id"] if rec.get("has_page") else None})
    return out


async def hall_of_fame(request):
    site: Site = request.app["site"]

    def job(conn):
        hof = q.get_hall_of_fame(conn, site.guild_id)
        ids = {r["discord_id"] for r in hof.values() if r["discord_id"]}
        return hof, q.stored_names(conn, site.guild_id, ids), q.player_slugs(conn, site.guild_id, ids)

    def build(data):
        hof, newest, slugs = data
        return {"records": _records(site, hof, newest, slugs)}

    return await site.page(request, ("hof",), job, build, "hall_of_fame.html", "hof")


# --- Operator panel -------------------------------------------------------------------------------------------

def _signed_in(site: Site, request) -> bool:
    return site.admin is not None and web_admin.check_session(site.admin, web_admin.read_cookie(request),
                                                             now=site.clock())


async def admin_redirect(request):
    raise web.HTTPSeeOther(location="/admin/")  # signed in (admin_gate already sent everyone else to the login)


async def admin_login_form(request):
    site: Site = request.app["site"]
    if _signed_in(site, request):
        raise web.HTTPSeeOther(location="/admin/")
    return html_response(site.render("admin/login.html", nav="", error=None), "no-store")


def _origin_ok(site: Site, request) -> bool:
    return request.headers.get("Origin") == site.admin.origin


async def admin_login(request):
    site: Site = request.app["site"]
    if not _origin_ok(site, request):
        raise web.HTTPForbidden()
    if request.content_type != "application/x-www-form-urlencoded":
        raise web.HTTPBadRequest()
    if request.content_length is None or request.content_length > web_admin.MAX_LOGIN_BODY:
        raise web.HTTPBadRequest()
    wait = site.guard.retry_after()
    if wait:
        return _too_many(site, wait)
    fields = await _read_form(site, request, web_admin.MAX_LOGIN_BODY)
    password = (fields.get("password") or [""])[0]
    reported = request.headers.get("CF-Connecting-IP") or "unknown"
    result, wait = await site.checker.check(password)
    if result is None:
        return _too_many(site, wait)
    if not result:
        log.warning("admin login failed (reported IP %s)", reported[:64])
        resp = html_response(site.render("admin/login.html", nav="", error="Wrong password"), "no-store", status=401)
        return resp
    log.info("admin login ok (reported IP %s)", reported[:64])
    resp = web.HTTPSeeOther(location="/admin/")
    resp.headers["Set-Cookie"] = web_admin.session_cookie_header(web_admin.make_session(site.admin, now=site.clock()))
    raise resp


def _too_many(site: Site, wait: int):
    resp = html_response(site.render("admin/login.html", nav="", error="Too many attempts. Try again later."),
                         "no-store", status=429)
    resp.headers["Retry-After"] = str(wait)
    return resp


async def admin_logout(request):
    site: Site = request.app["site"]
    if not _origin_ok(site, request):
        raise web.HTTPForbidden()
    resp = web.HTTPSeeOther(location="/admin/login")
    resp.headers["Set-Cookie"] = web_admin.expiry_cookie_header()
    raise resp


async def admin_panel(request):
    site: Site = request.app["site"]
    if not _signed_in(site, request):
        raise web.HTTPSeeOther(location="/admin/login")
    entry = None
    run_id = request.query.get("run", "")
    if _RUN_ID.fullmatch(run_id):
        known = _console_runs(site).get(run_id)
        if known is not None and known["session"] == _session_key(request):
            entry = known["entry"] or _still_running(known["command"])
    return await _panel_response(site, console_entry=entry)


MAX_CONSOLE_BODY = 8 * 1024
FORM_READ_SECONDS = 5.0    # a login or console body that hasn't arrived by then is dropped
MAX_FORM_READERS = 8       # bodies read at once; more get 503 before any waiting
CONSOLE_RUN_SECONDS = 600  # how long a run's output can be shown again (reload, Back, a resubmitted form)
MAX_CONSOLE_RUNS = 32
_COMMAND_NAME = re.compile(r"[a-z_]{1,20}")
_RUN_ID = re.compile(r"[A-Za-z0-9_-]{16,64}")


async def _read_form(site: Site, request, limit: int) -> dict:
    """A urlencoded body of at most `limit` bytes, read within FORM_READ_SECONDS, with at most MAX_FORM_READERS
    bodies in flight, so slow uploads can't hold handlers open. (The caller checked the declared length.)"""
    if site.form_readers >= MAX_FORM_READERS:
        raise web.HTTPServiceUnavailable(headers={"Retry-After": "5"})
    site.form_readers += 1
    try:
        body = await asyncio.wait_for(request.read(), FORM_READ_SECONDS)
    except asyncio.TimeoutError:
        raise web.HTTPRequestTimeout()
    finally:
        site.form_readers -= 1
    if len(body) > limit:
        raise web.HTTPBadRequest()
    from urllib.parse import parse_qs
    return parse_qs(body.decode("utf-8", "replace"), keep_blank_values=True)


def _session_key(request) -> str:
    return hashlib.sha256((web_admin.read_cookie(request) or "").encode("utf-8")).hexdigest()


def _console_runs(site: Site) -> OrderedDict:
    """The recorded runs, oldest first: finished runs expire after CONSOLE_RUN_SECONDS and only the newest
    MAX_CONSOLE_RUNS are kept. A run whose command is still going is never dropped, so a resubmit can't run it
    again."""
    now = time.monotonic()
    runs = site.console_runs
    finished = [k for k, v in runs.items() if v["entry"] is not None]
    for run_id in [k for k in finished if runs[k]["expires"] < now]:
        del runs[run_id]
        finished.remove(run_id)
    while len(runs) > MAX_CONSOLE_RUNS and finished:
        del runs[finished.pop(0)]
    return runs


def _still_running(command: str) -> dict:
    return {"command": command, "lines": [
        {"text": "That command is still running. Reload in a moment to see its output.", "kind": "warn"}]}


async def admin_console_get(request):
    raise web.HTTPSeeOther(location="/admin/#console")


async def admin_console(request):
    """Run one console command. JSON for the page's script (Accept: application/json); otherwise the panel page with
    the output, so the console works without JavaScript. Same-origin form posts only, like logout."""
    site: Site = request.app["site"]
    if not _signed_in(site, request):
        raise web.HTTPSeeOther(location="/admin/login")
    if not _origin_ok(site, request):
        raise web.HTTPForbidden()
    if request.content_type != "application/x-www-form-urlencoded":
        raise web.HTTPBadRequest()
    if request.content_length is None or request.content_length > MAX_CONSOLE_BODY:
        raise web.HTTPBadRequest()
    fields = await _read_form(site, request, MAX_CONSOLE_BODY)
    line = " ".join((fields.get("command") or [""])[0].split("\n")).strip()
    console = site.console
    # The form's run ID: a second post of the same form (Back and resubmit, a retry, a double click) gets the first
    # one's output and never runs the command again.
    run_id = (fields.get("run") or [""])[0]
    session = _session_key(request)
    runs = _console_runs(site)
    known = runs.get(run_id) if _RUN_ID.fullmatch(run_id) else None
    if known is not None and known["session"] == session:
        return await _console_reply(request, site, run_id, known["entry"] or _still_running(known["command"]))
    if known is not None or not _RUN_ID.fullmatch(run_id):
        run_id = secrets.token_urlsafe(16)
    record = {"session": session, "expires": time.monotonic() + CONSOLE_RUN_SECONDS, "command": line,
              "entry": None}
    if line:
        runs[run_id] = record
    stopping, entry = False, None
    try:
        if not line:
            lines = []
        elif console is None:
            lines = [{"text": "The console needs the running bot, so it's off in the local preview.", "kind": "err"}]
        elif len(line) > console.max_line:
            lines = [{"text": f"Error: a command can be at most {console.max_line:,} characters.", "kind": "err"}]
        else:
            name = line.split(None, 1)[0].lower()
            reported = request.headers.get("CF-Connecting-IP") or "unknown"
            # The command's name only: message text and SQL stay out of the container log.
            log.info("admin console: %s (reported IP %s)", name if _COMMAND_NAME.fullmatch(name) else "?",
                     reported[:64])
            already = bool(getattr(console, "stop_requested", False))
            lines = await console.run(line)
            # Only the reply to the `stop confirm` that asked for the stop reports that it went out.
            stopping = not already and bool(getattr(console, "stop_requested", False))
        entry = {"command": line, "lines": lines}
    finally:
        # Recorded even when the command failed or the request was cancelled: a resubmit shows this instead of
        # running the command again.
        record["entry"] = entry or {"command": line, "lines": [
            {"text": "The command stopped before it finished. Check the bot's log before running it again.",
             "kind": "err"}]}
        if runs.get(run_id) is record:
            runs.move_to_end(run_id)  # finished just now: the newest result, not the first to be dropped
    return await _console_reply(request, site, run_id, entry if line else None, stopping=stopping)


async def _console_reply(request, site: Site, run_id: str, entry: dict | None,
                         stopping: bool = False) -> web.StreamResponse:
    """JSON for the page's script; otherwise a redirect to the panel showing the run (POST, then GET: a reload
    never posts the command again). After `stop confirm` (`stopping`) the reply is a short page of its own, sent here
    with the site's headers, and the bot is told to stop once it has gone out (or failed to)."""
    console = site.console
    if "application/json" in request.headers.get("Accept", ""):
        resp = web.json_response(entry or {"command": "", "lines": []})
        resp.headers["Cache-Control"] = "no-store"
    elif stopping:
        resp = html_response(site.render("admin/stopping.html", nav=""), "no-store")
    else:
        location = f"/admin/?run={run_id}#console" if entry else "/admin/#console"
        resp = web.Response(status=303, headers={"Location": location, "Cache-Control": "no-store"})
    if not stopping:
        return resp
    apply_header_policy(request, resp)
    try:
        await resp.prepare(request)
        await resp.write_eof()
    finally:
        console.stop_after_reply()
    return resp


async def _panel_response(site: Site, console_entry: dict | None = None) -> web.Response:
    def job(conn):
        return web_admin.panel_counts(conn), web_admin.panel_channels(conn)

    def files():
        return web_admin.list_logs(site.log_dir), web_admin.list_backups(site.backup_dir)

    try:
        counts, channels = await site.db.run(job)
        logs, backups = await site.db.run_files(files)
    except Busy:
        raise web.HTTPServiceUnavailable(headers={"Retry-After": "5"})
    bot = site.bot
    status = {
        "connected": bool(bot is not None and bot.is_ready() and not bot.is_closed()),
        "latency_ms": round(bot.latency * 1000) if bot is not None and bot.latency == bot.latency
        and bot.latency != float("inf") else None,
        "uptime": fmt_playtime(int((time.time() - site.started_at) * 1000)),
    }
    guilds = []
    if bot is not None:
        for g in getattr(bot, "guilds", []):
            guilds.append({"name": g.name, "id": g.id, "members": getattr(g, "member_count", None)})
    for b in backups:
        b["date"] = fmt_date(int(b["mtime"] * 1000))
    console = site.console
    body = site.render("admin/panel.html", nav="", status=status, guilds=guilds, channels=channels, counts=counts,
                       logs=logs, backups=backups, console_on=console is not None,
                       console_reference=console.reference if console is not None else (),
                       console_max=console.max_line if console is not None else 2100, console_entry=console_entry,
                       console_run=secrets.token_urlsafe(16))
    return html_response(body, "no-store")


async def admin_log(request):
    site: Site = request.app["site"]
    if not _signed_in(site, request):
        raise web.HTTPSeeOther(location="/admin/login")
    name = request.match_info["name"]
    try:
        state, text = await site.db.run_files(web_admin.read_log, site.log_dir, name)
    except Busy:
        raise web.HTTPServiceUnavailable(headers={"Retry-After": "5"})
    if state == "invalid":
        raise web.HTTPNotFound()
    status = 200 if state == "ok" else 404 if state == "missing" else 200
    body = site.render("admin/log.html", nav="", name=name, state=state, text=text)
    return html_response(body, "no-store", status=status)


# --- Lifecycle -------------------------------------------------------------------------------------------------

class WebServer:
    def __init__(self, app: web.Application, runner: web.AppRunner):
        self.app = app
        self.runner = runner
        self.site: Site = app["site"]

    def begin_stop(self):
        self.site.db.begin_stop()
        kill_worker = getattr(self.site.console, "kill_worker", None)
        if kill_worker is not None:
            kill_worker()  # a running panel `sql` process
        if self.site.checker:
            self.site.checker.shutdown()  # no new password checks; queued ones are cancelled

    async def stop(self, deadline: float):
        self.begin_stop()
        remaining = max(0.05, deadline - time.monotonic())
        try:
            await asyncio.wait_for(self.runner.cleanup(), timeout=remaining)
        except (asyncio.TimeoutError, Exception) as exc:
            if not isinstance(exc, asyncio.TimeoutError):
                log.warning("Website shutdown error: %r", exc)
        await self.site.db.stop(deadline)
        # A running password check is one scrypt call (~0.1 s); wait for it within the same deadline.
        while self.site.checker and self.site.checker.pending() and time.monotonic() < deadline:
            await asyncio.sleep(0.02)


def _make_runner(app: web.Application) -> tuple[web.AppRunner, bool]:
    """aiohttp 3.9+ takes shutdown_timeout on the runner; older versions take it on the site."""
    import aiohttp
    major_minor = tuple(int(part) for part in aiohttp.__version__.split(".")[:2])
    if major_minor >= (3, 9):
        return web.AppRunner(app, access_log=None, shutdown_timeout=1.0), True
    return web.AppRunner(app, access_log=None), False


async def start_web_server(port: int, guild_id: int, db_path: str, bot=None, admin_config=None,
                           log_dir: str = "log", host: str = "0.0.0.0", console=None) -> WebServer | None:
    """Start the site, or log why it couldn't and return None (the bot carries on without it)."""
    if not port:
        return None
    runner = None
    try:
        app = create_app(guild_id, db_path, bot=bot, admin_config=admin_config, log_dir=log_dir, console=console)
        runner, timeout_on_runner = _make_runner(app)
        await runner.setup()
        if timeout_on_runner:
            site = web.TCPSite(runner, host, port)
        else:
            site = web.TCPSite(runner, host, port, shutdown_timeout=1.0)
        await site.start()
        log.info("Website listening on %s:%s%s", host, port, " (operator panel on)" if admin_config else "")
        return WebServer(app, runner)
    except Exception as exc:
        log.error("Website could not start on port %s: %r", port, exc)
        if runner is not None:
            try:
                await runner.cleanup()
            except Exception:
                pass
        return None


# --- Local preview ---------------------------------------------------------------------------------------------

def _check_preview_db(path: str) -> str | None:
    p = Path(path)
    if not p.is_file() or p.stat().st_size == 0:
        return "the --db file is missing or empty"
    try:
        conn = sqlite3.connect(p.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        finally:
            conn.close()
    except sqlite3.Error:
        return "the --db file doesn't open as SQLite"
    missing = {"player_queue_stats", "queue_results"} - tables
    if missing:
        return "the --db file has no " + " or ".join(sorted(missing)) + " table"
    return None


def prepare_preview_db(path: str) -> None:
    """Point DB_PATH at the disposable copy and bring its schema up to date before the preview listens, so an older
    export behaves the same whichever page is opened first (db's import runs the migrations on DB_PATH)."""
    os.environ["DB_PATH"] = os.path.abspath(path)  # before anything imports db
    import db  # noqa: F401


def preview_main(argv=None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Local preview of the C-TierBot website on a disposable DB copy.")
    parser.add_argument("--db", required=True)
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--guild", type=int, default=1363265113880264745)
    args = parser.parse_args(argv)
    problem = _check_preview_db(args.db)
    if problem:
        print(f"Preview refused: {problem}.", file=sys.stderr)
        return 2
    prepare_preview_db(args.db)
    logging.basicConfig(level=logging.INFO)
    admin = web_admin.parse_admin_config(os.getenv("WEB_PUBLIC_ORIGIN", "").strip().rstrip("/"),
                                         os.getenv("WEB_ADMIN_PASSWORD_HASH", "").strip(),
                                         os.getenv("WEB_SESSION_SECRET", "").strip(), allow_loopback_http=True)

    async def run():
        server = await start_web_server(args.port, args.guild, args.db, bot=None, admin_config=admin,
                                        log_dir=os.getenv("QUEUE_STATS_LOG_DIR") or "log", host="127.0.0.1")
        if server is None:
            return 1
        print(f"Preview at http://127.0.0.1:{args.port}/ (Ctrl+C to stop)")
        try:
            while True:
                await asyncio.sleep(3600)
        finally:
            await server.stop(time.monotonic() + 5)

    try:
        return asyncio.run(run()) or 0
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(preview_main())
