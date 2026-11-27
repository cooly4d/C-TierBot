"""Operator panel at /admin: one password, read-only.

Off unless WEB_PUBLIC_ORIGIN, WEB_ADMIN_PASSWORD_HASH and WEB_SESSION_SECRET are all valid. The password is
checked with scrypt in a one-thread executor behind a one-slot admission; sessions are a signed, stateless
`__Host-` cookie. Nothing here ever logs a password, a hash, a secret or a cookie.
"""
import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import stat
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

log = logging.getLogger(__name__)

COOKIE_NAME = "__Host-ctier_admin"
SESSION_SECONDS = 12 * 3600
FAILURE_LIMIT = 20
FAILURE_WINDOW_SECONDS = 3600
MAX_LOGIN_BODY = 1024
LOG_NAME = re.compile(r"queue_stats_[0-9A-Za-z_.-]{1,200}\.json")
# Bounded ASCII fields, checked with fullmatch before any int() conversion.
_HASH = re.compile(r"scrypt:([0-9]{1,8}):([0-9]{1,3}):([0-9]{1,3}):([0-9a-f]{32}):([0-9a-f]{64})")
_SECRET = re.compile(r"[0-9a-f]{64}")
# A session cookie: base64url payload, ".", the 43-character base64url HMAC-SHA256.
_SESSION = re.compile(r"[A-Za-z0-9_-]{1,400}\.[A-Za-z0-9_-]{43}")
MAX_LOG_BYTES = 2 * 1024 * 1024  # a diagnostic log larger than this is listed but not parsed or shown
SCRYPT_MAXMEM = 64 * 1024 * 1024
_LOOPBACK = {"127.0.0.1", "localhost", "[::1]"}


def canonical_origin(origin: str, allow_loopback_http: bool = False) -> str | None:
    """The origin as browsers send it in `Origin` (lower-case scheme and host, no default port), or None when it
    isn't an https origin with no path, host and port in range. http is accepted only for a loopback host and only
    when `allow_loopback_http` (the local preview)."""
    from urllib.parse import urlsplit
    if not isinstance(origin, str) or not origin or len(origin) > 255:
        return None
    try:
        parts = urlsplit(origin)
        port = parts.port
    except ValueError:
        return None
    if parts.path or parts.query or parts.fragment or parts.username or parts.password or not parts.hostname:
        return None
    if not re.fullmatch(r"[A-Za-z0-9.-]+|\[::1\]", parts.netloc.rsplit(":", 1)[0] if port else parts.netloc):
        return None
    if port is not None and not 1 <= port <= 65535:
        return None
    scheme = parts.scheme.lower()
    host = parts.hostname.lower() if parts.hostname != "::1" else "[::1]"
    if scheme == "https":
        default = 443
    elif scheme == "http" and allow_loopback_http and host in _LOOPBACK:
        default = 80
    else:
        return None
    return f"{scheme}://{host}" + (f":{port}" if port is not None and port != default else "")


def valid_origin(origin: str, allow_loopback_http: bool = False) -> bool:
    return canonical_origin(origin, allow_loopback_http) is not None
MIN_PASSWORD, MAX_PASSWORD = 16, 128


@dataclass(frozen=True)
class AdminConfig:
    origin: str
    hash_text: str
    n: int
    r: int
    p: int
    salt: bytes
    digest: bytes
    secret: bytes

    @property
    def session_key(self) -> bytes:
        # Tied to the password hash too, so changing the password (or the secret) ends every session.
        return hmac.new(self.secret, self.hash_text.encode(), hashlib.sha256).digest()


def parse_admin_config(origin: str, hash_text: str, secret: str, *,
                       allow_loopback_http: bool = False) -> AdminConfig | None:
    """The panel's config, or None (panel off). A malformed value is logged by name, never by value. The origin is
    stored in the canonical form browsers send, so the login's exact Origin check matches."""
    if not (origin or hash_text or secret):
        return None
    problems = []
    canonical = canonical_origin(origin, allow_loopback_http)
    if canonical is None:
        problems.append("WEB_PUBLIC_ORIGIN")
    m = _HASH.fullmatch(hash_text) if isinstance(hash_text, str) and len(hash_text) <= 200 else None
    if not m:
        problems.append("WEB_ADMIN_PASSWORD_HASH")
    if not (isinstance(secret, str) and _SECRET.fullmatch(secret)):
        problems.append("WEB_SESSION_SECRET")
    if problems:
        log.error("Operator panel is off: %s missing or malformed.", ", ".join(problems))
        return None
    n, r, p = int(m.group(1)), int(m.group(2)), int(m.group(3))
    # scrypt needs about 128 * n * r * p bytes; anything over half of SCRYPT_MAXMEM could fail at verify time
    # and turn the right password into "wrong", so it's refused here instead.
    if n < 2 ** 14 or n & (n - 1) or not 1 <= r <= 16 or not 1 <= p <= 4 or 128 * n * r * p > SCRYPT_MAXMEM // 2:
        log.error("Operator panel is off: WEB_ADMIN_PASSWORD_HASH has unsupported scrypt parameters.")
        return None
    return AdminConfig(canonical, hash_text, n, r, p, bytes.fromhex(m.group(4)), bytes.fromhex(m.group(5)),
                       bytes.fromhex(secret))


def valid_password_text(password: str) -> bool:
    """16 to 128 printable ASCII characters, spaces included (a multiword passphrase works)."""
    return (isinstance(password, str) and MIN_PASSWORD <= len(password) <= MAX_PASSWORD
            and all(32 <= ord(ch) < 127 for ch in password))


def hash_password(password: str, salt: bytes | None = None, n: int = 2 ** 15, r: int = 8, p: int = 1) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=n, r=r, p=p, dklen=32, maxmem=SCRYPT_MAXMEM)
    return f"scrypt:{n}:{r}:{p}:{salt.hex()}:{digest.hex()}"


def verify_password(config: AdminConfig, password: str) -> bool:
    if not isinstance(password, str) or len(password) > MAX_PASSWORD * 4:
        return False
    try:
        digest = hashlib.scrypt(password.encode(), salt=config.salt, n=config.n, r=config.r, p=config.p,
                                dklen=32, maxmem=SCRYPT_MAXMEM)
    except (ValueError, MemoryError):
        return False
    return hmac.compare_digest(digest, config.digest)


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def make_session(config: AdminConfig, now: float | None = None) -> str:
    now = int(now if now is not None else time.time())
    # sid: every sign-in gets its own cookie (and so its own console runs), even two in the same second.
    payload = _b64(json.dumps({"iat": now, "exp": now + SESSION_SECONDS, "sid": secrets.token_urlsafe(12)},
                              separators=(",", ":")).encode())
    sig = _b64(hmac.new(config.session_key, payload.encode(), hashlib.sha256).digest())
    return f"{payload}.{sig}"


def check_session(config: AdminConfig, value: str | None, now: float | None = None) -> bool:
    # Format first (bounded ASCII base64url), so compare_digest only ever sees ASCII strings.
    if not isinstance(value, str) or not _SESSION.fullmatch(value):
        return False
    payload, sig = value.split(".")
    expected = _b64(hmac.new(config.session_key, payload.encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(sig, expected):
        return False
    try:
        data = json.loads(_unb64(payload))
        iat, exp = int(data["iat"]), int(data["exp"])
    except (ValueError, KeyError, TypeError):
        return False
    now = now if now is not None else time.time()
    return iat <= now + 60 and now < exp


class LoginGuard:
    """One global failure limit (the client address can't be verified, so there is no per-IP limit) and a
    one-slot password check whose slot is released only when scrypt has really finished."""

    def __init__(self, clock=time.time):
        self.clock = clock
        self.failures: deque = deque()
        self.locked_until = 0.0
        self.busy = False
        self._lock = threading.Lock()

    def retry_after(self) -> int:
        with self._lock:
            now = self.clock()
            while self.failures and self.failures[0] <= now - FAILURE_WINDOW_SECONDS:
                self.failures.popleft()
            if now < self.locked_until:
                return max(1, int(self.locked_until - now + 0.999))
            return 0

    def record_failure(self):
        with self._lock:
            now = self.clock()
            self.failures.append(now)
            while self.failures and self.failures[0] <= now - FAILURE_WINDOW_SECONDS:
                self.failures.popleft()
            if len(self.failures) >= FAILURE_LIMIT:
                self.locked_until = now + FAILURE_WINDOW_SECONDS

    def try_take(self) -> bool:
        with self._lock:
            if self.busy:
                return False
            self.busy = True
            return True

    def begin(self):
        """Atomically: ("locked", seconds) during a lockout, ("busy", 5) while another check runs, or ("ok", 0)
        with the password slot reserved."""
        with self._lock:
            now = self.clock()
            while self.failures and self.failures[0] <= now - FAILURE_WINDOW_SECONDS:
                self.failures.popleft()
            if now < self.locked_until:
                return "locked", max(1, int(self.locked_until - now + 0.999))
            if self.busy:
                return "busy", 5
            self.busy = True
            return "ok", 0

    def release(self, _future=None):
        with self._lock:
            self.busy = False


def expiry_cookie_header() -> str:
    # Set explicitly: aiohttp's del_cookie leaves out Secure, which browsers require for a __Host- cookie.
    return f"{COOKIE_NAME}=; Secure; HttpOnly; SameSite=Strict; Path=/; Max-Age=0"


def session_cookie_header(value: str) -> str:
    return f"{COOKIE_NAME}={value}; Secure; HttpOnly; SameSite=Strict; Path=/; Max-Age={SESSION_SECONDS}"


def read_cookie(request) -> str | None:
    return request.cookies.get(COOKIE_NAME)


class PasswordChecker:
    """Runs verify_password in its own one-thread executor behind LoginGuard's slot."""

    def __init__(self, config: AdminConfig, guard: LoginGuard, verify=verify_password):
        self.config = config
        self.guard = guard
        self.verify = verify
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="web-login")
        self.running: set = set()

    async def check(self, password: str):
        """(True/False, 0), or (None, retry_after) when locked out or another check is running (429 at once).
        The lockout check and the slot reservation happen together under the guard's lock."""
        state, wait = self.guard.begin()
        if state != "ok":
            return None, wait
        try:
            future = self.executor.submit(self._run, password)
        except RuntimeError:
            self.guard.release()
            return None, 5
        self.running.add(future)
        future.add_done_callback(self._done)
        return await asyncio.wrap_future(future), 0

    def _done(self, future):
        self.running.discard(future)
        self.guard.release()

    def _run(self, password: str) -> bool:
        ok = self.verify(self.config, password)
        if not ok:
            self.guard.record_failure()  # counted before the slot is released
        return ok

    def shutdown(self):
        self.executor.shutdown(wait=False, cancel_futures=True)

    def pending(self) -> list:
        return [f for f in list(self.running) if not f.done()]


# --- Panel data (runs in the website's DB executor, read-only connection) ---------------------------------------

def panel_counts(conn) -> dict:
    def one(sql):
        try:
            return conn.execute(sql).fetchone()[0]
        except Exception:
            return None
    return {
        "linked users": one("SELECT COUNT(*) FROM users"),
        "processed matches": one("SELECT COUNT(*) FROM processed_matches"),
        "saved snapshots": one("SELECT COUNT(*) FROM queue_results"),
        "player stat rows": one("SELECT COUNT(*) FROM player_queue_stats"),
    }


def panel_channels(conn) -> list[dict]:
    return [{"guild_id": g, "channel_id": c, "last_updated": u} for g, c, u in
            conn.execute("SELECT guild_id, channel_id, last_updated FROM guild_settings ORDER BY guild_id, channel_id")]


_READ_CHUNK = 64 * 1024


def _read_json_file(path: str):
    """The parsed JSON of a diagnostic log no larger than MAX_LOG_BYTES. Raises OSError or ValueError (bad
    UTF-8 and bad JSON both) for anything that can't be shown.

    Only a regular file is read: the open never follows a link and never waits (a FIFO or device under a log name
    is refused before any read). The read goes in 64 KB chunks with a checkpoint before each, so the job's
    deadline or a shutdown stops it within one local chunk read."""
    from web_queries import checkpoint
    no_follow = getattr(os, "O_NOFOLLOW", 0)
    before = None
    if not no_follow:
        # No O_NOFOLLOW (Windows, the local preview): refuse a link or reparse point by name, then make sure the
        # file opened is the one checked.
        before = os.lstat(path)
        if stat.S_ISLNK(before.st_mode) or getattr(before, "st_file_attributes", 0) & 0x400:  # REPARSE_POINT
            raise ValueError("a link")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NONBLOCK", 0) | no_follow
    fd = os.open(path, flags)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("not a regular file")
        if before is not None and (info.st_dev, info.st_ino) != (before.st_dev, before.st_ino):
            raise ValueError("changed while opening")
        if info.st_size > MAX_LOG_BYTES:
            raise ValueError("too large")
        chunks, total = [], 0
        while True:
            checkpoint()
            chunk = os.read(fd, _READ_CHUNK)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_LOG_BYTES:
                raise ValueError("too large")
            chunks.append(chunk)
    finally:
        os.close(fd)
    try:
        return json.loads(b"".join(chunks).decode("utf-8"))
    except RecursionError:  # nested deeper than the parser goes: unreadable, like bad JSON
        raise ValueError("nested too deeply") from None


def list_logs(log_dir: str, limit: int = 50) -> list[dict]:
    """Runs in the website's admitted executor (WebDB.run_files): stops between files at its deadline."""
    from web_queries import checkpoint
    try:
        names = [n for n in os.listdir(log_dir) if LOG_NAME.fullmatch(n)]
    except OSError:
        return []
    out = []
    for name in sorted(names, reverse=True)[:limit]:
        checkpoint()
        path = os.path.join(log_dir, name)
        try:
            size = os.path.getsize(path)
            data = _read_json_file(path)
            status = data.get("status") if isinstance(data, dict) else None
            event = data.get("event") if isinstance(data, dict) else None
            match_id = data.get("match_id") if isinstance(data, dict) else None
            readable = True
        except (OSError, ValueError):
            size, status, event, match_id, readable = None, None, None, None, False
        out.append({"name": name, "size": size, "status": status, "event": event, "match_id": match_id,
                    "readable": readable})
    return out


def read_log(log_dir: str, name: str):
    """("ok", pretty JSON text) | ("missing", None) | ("unreadable", None) | ("invalid", None)."""
    if not isinstance(name, str) or not LOG_NAME.fullmatch(name) or name.startswith("."):
        return "invalid", None
    path = os.path.join(log_dir, name)
    try:
        data = _read_json_file(path)
    except FileNotFoundError:
        return "missing", None
    except (OSError, ValueError):  # unreadable, too large, not UTF-8, not JSON, or nested too deeply
        return "unreadable", None
    try:
        return "ok", json.dumps(data, indent=2, ensure_ascii=False)
    except RecursionError:
        return "unreadable", None


def list_backups(backup_dir: str) -> list[dict]:
    from web_queries import checkpoint
    try:
        names = sorted((n for n in os.listdir(backup_dir) if n.startswith("leaderboard-") and n.endswith(".db")),
                       reverse=True)
    except OSError:
        return []
    out = []
    for name in names:
        checkpoint()
        try:
            st = os.stat(os.path.join(backup_dir, name))
            out.append({"name": name, "size": st.st_size, "mtime": st.st_mtime})
        except OSError:
            continue
    return out
