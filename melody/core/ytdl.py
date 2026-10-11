from utils.branding import BOT_NAME
import asyncio
import base64
from contextlib import contextmanager
import glob
import heapq
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import zipfile
from melody.core.pools import YTDL_POOL
try:
    _RESOLVE_TIMEOUT = float(os.getenv('RESOLVE_TIMEOUT', '4.0'))
except ValueError:
    _RESOLVE_TIMEOUT = 6.0
_DIRECT_RESOLVE_MAX = max(1.0, float(os.getenv('DIRECT_RESOLVE_MAX', '4.0')))
_RESOLVE_TIMEOUT = min(_RESOLVE_TIMEOUT, _DIRECT_RESOLVE_MAX)
try:
    _INNERTUBE_HEADSTART = float(os.getenv('INNERTUBE_HEADSTART', '0.70'))
except Exception:
    _INNERTUBE_HEADSTART = 0.6
try:
    _IT_MUTE_AFTER = max(1, int(os.getenv('INNERTUBE_MUTE_AFTER', '2')))
except Exception:
    _IT_MUTE_AFTER = 1
try:
    _IT_MUTE_TTL = max(60.0, float(os.getenv('INNERTUBE_MUTE_TTL', '300')))
except Exception:
    _IT_MUTE_TTL = 300.0
_it_stream_fail_streak = 0
_it_stream_muted_until = 0.0

def _innertube_stream_muted() -> bool:
    return _it_stream_muted_until > _time_mod.monotonic()

def direct_stream_muted() -> bool:
    return False

def _note_innertube_stream(ok: bool) -> None:
    global _it_stream_fail_streak, _it_stream_muted_until
    if ok:
        if _it_stream_muted_until or _it_stream_fail_streak:
            LOGGER.info('#stream innertube direct path healthy again — re-enabled')
        _it_stream_fail_streak = 0
        _it_stream_muted_until = 0.0
        return
    _it_stream_fail_streak += 1
    if _it_stream_fail_streak >= _IT_MUTE_AFTER and (not _innertube_stream_muted()):
        _it_stream_muted_until = _time_mod.monotonic() + _IT_MUTE_TTL
        LOGGER.info('#stream innertube blocked on this host (%d/%d) — skipping direct InnerTube probes for %.0fs, using cookie yt-dlp first', _it_stream_fail_streak, _IT_MUTE_AFTER, _IT_MUTE_TTL)
try:
    _FAST_TIMEOUT = float(os.getenv('FAST_RESOLVE_TIMEOUT', '1.0'))
except ValueError:
    _FAST_TIMEOUT = 1.0
import httpx as _httpx

class _NoStoreCookies(_httpx.Cookies):

    def extract_cookies(self, response) -> None:
        return
_http_client: '_httpx.AsyncClient | None' = None
_http_client_lock = asyncio.Lock()
_http_sync_client: '_httpx.Client | None' = None
_http_sync_client_lock = threading.Lock()

def _http2_available() -> bool:
    try:
        __import__('h2')
    except Exception:
        return False
    return True

def _http_client_kwargs() -> dict:
    kwargs: dict = {'http2': _http2_available(), 'timeout': _httpx.Timeout(12.0, connect=5.0), 'limits': _httpx.Limits(max_connections=8, max_keepalive_connections=2), 'headers': {'User-Agent': 'Mozilla/5.0 (compatible; MusicBot/1.0)'}, 'follow_redirects': True, 'cookies': _NoStoreCookies()}
    proxy = os.getenv('YTDLP_PROXY', '').strip()
    if proxy:
        kwargs['proxy'] = proxy
    return kwargs

def get_http_sync_client() -> '_httpx.Client':
    global _http_sync_client
    if _http_sync_client is not None and (not _http_sync_client.is_closed):
        return _http_sync_client
    with _http_sync_client_lock:
        if _http_sync_client is not None and (not _http_sync_client.is_closed):
            return _http_sync_client
        _http_sync_client = _httpx.Client(**_http_client_kwargs())
        LOGGER.info('✅ Persistent sync HTTP client (connection pool) initialized')
        return _http_sync_client
_BGUTIL_PLUGIN_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '..', 'vendor', 'bgutil-ytdlp-pot-provider', 'plugin')
_BGUTIL_PLUGIN_DIR = os.path.realpath(os.path.normpath(_BGUTIL_PLUGIN_DIR))
if os.path.isdir(_BGUTIL_PLUGIN_DIR):
    sys.path[:] = [entry for entry in sys.path if os.path.realpath(entry or os.curdir) != _BGUTIL_PLUGIN_DIR]
    sys.path.insert(0, _BGUTIL_PLUGIN_DIR)
from yt_dlp import YoutubeDL
from melody.config import Config
from melody.logging import LOGGER, redact_sensitive_text, send_error_log
_YTDLP_INIT_LOCK = threading.Lock()

@contextmanager
def _locked_ytdl(opts: dict):
    with _YTDLP_INIT_LOCK:
        ydl = YoutubeDL(opts)
    with ydl as active:
        yield active
_WARM_YDL: dict = {}
_WARM_YDL_LOCK = threading.Lock()
try:
    _WARM_YDL_TTL = max(60.0, float(os.getenv('YTDLP_WARM_TTL', '900')))
except Exception:
    _WARM_YDL_TTL = 300.0

def _warm_extract(opts: dict, target: str, cache_key: str='resolve'):
    now = _time_mod.monotonic()
    entry = None
    with _WARM_YDL_LOCK:
        cached = _WARM_YDL.get(cache_key)
        if cached and cached['born'] + _WARM_YDL_TTL > now:
            entry = cached
        else:
            if cached:
                _WARM_YDL.pop(cache_key, None)
                try:
                    cached['ydl'].close()
                except Exception:
                    pass
            try:
                with _YTDLP_INIT_LOCK:
                    entry = {'ydl': YoutubeDL(opts), 'lock': threading.Lock(), 'born': now}
                _WARM_YDL[cache_key] = entry
            except Exception as exc:
                LOGGER.debug('warm YoutubeDL unavailable: %s', exc)
                entry = None
    if entry is not None and entry['lock'].acquire(blocking=False):
        try:
            return entry['ydl'].extract_info(target, download=False)
        except Exception:
            with _WARM_YDL_LOCK:
                if _WARM_YDL.get(cache_key) is entry:
                    _WARM_YDL.pop(cache_key, None)
            raise
        finally:
            entry['lock'].release()
    with _locked_ytdl(opts) as ydl:
        return ydl.extract_info(target, download=False)
import time as _time_mod
_META_CACHE_TTL = 900
_META_DISK_TTL = 259200
_meta_cache: dict = {}
_QUERY_NOISE_WORDS = {'song', 'songs', 'gana', 'gaana', 'gane', 'play', 'full', 'audio', 'video', 'lyrics', 'lyrical', 'official', 'mp3', 'mp4', 'hd', '4k', 'new', 'latest', 'version', 'remix', 'original', 'track', 'music', 'bajao', 'sunao', 'please'}

def _norm_query_key(query: 'str | None') -> 'str | None':
    if not query:
        return None
    text = query.strip().lower()
    if text.startswith('http') or text.startswith('www.'):
        return None
    text = re.sub('[^a-z0-9\\s]+', ' ', text)
    tokens = [t for t in text.split() if t and t not in _QUERY_NOISE_WORDS]
    if not tokens:
        return None
    return 'q:' + ' '.join(sorted(tokens))

def _fuzzy_query_key(query: 'str | None') -> 'str | None':
    norm = _norm_query_key(query)
    if not norm:
        return None
    body = norm[2:].replace(' ', '')
    if len(body) < 8:
        return None
    skeleton = re.sub('(.)\\1+', '\\1', body)
    skeleton = re.sub('(?!^)[aeiouyh]', '', skeleton)
    if len(skeleton) < 5:
        return None
    return 'f:' + skeleton

def _meta_cache_get(key: str) -> 'dict | None':
    entry = _meta_cache.get(key)
    if entry:
        if _time_mod.time() - entry['ts'] <= _META_CACHE_TTL:
            return entry['data']
        _meta_cache.pop(key, None)
    try:
        from utils.song_cache import get_persistent_meta
        data = get_persistent_meta(key, ttl=_META_DISK_TTL)
        if data:
            _meta_cache[key] = {'data': data, 'ts': _time_mod.time()}
            return data
    except Exception:
        pass
    if not key.startswith(('q:', 'f:', 'id:')):
        for alias in (_norm_query_key(key), _fuzzy_query_key(key)):
            if not alias:
                continue
            data = _meta_cache_get(alias)
            if data:
                LOGGER.debug('⚡ meta cache alias HIT %s -> %s', key[:40], alias[:40])
                _meta_cache[key] = {'data': data, 'ts': _time_mod.time()}
                return data
    return None

def _meta_cache_put(key: str, info: dict) -> None:
    _meta_cache[key] = {'data': info, 'ts': _time_mod.time()}
    aliases = [key]
    try:
        vid = (info or {}).get('id')
        if vid:
            id_key = f'id:{vid}'
            _meta_cache[id_key] = {'data': info, 'ts': _time_mod.time()}
            aliases.append(id_key)
    except Exception:
        pass
    try:
        title = (info or {}).get('title') or ''
        for source in (key if not key.startswith(('q:', 'f:', 'id:')) else '', title):
            for alias in (_norm_query_key(source), _fuzzy_query_key(source)):
                if alias and alias not in aliases:
                    _meta_cache[alias] = {'data': info, 'ts': _time_mod.time()}
                    aliases.append(alias)
    except Exception:
        pass
    if len(_meta_cache) > (60 if os.getenv('MEMORY_LIMIT_MB', '512').strip().isdigit() and int(os.getenv('MEMORY_LIMIT_MB', '512')) <= 768 else 200):
        oldest = min(_meta_cache, key=lambda k: _meta_cache[k]['ts'])
        _meta_cache.pop(oldest, None)
    try:
        from utils.song_cache import put_persistent_meta
        for alias in aliases:
            put_persistent_meta(alias, info)
    except Exception:
        pass
_download_futures: dict = {}
_download_jobs: set[asyncio.Task] = set()
_download_futures_lock = asyncio.Lock()
_download_cancel_events: dict[str, tuple[threading.Event, object | None, int]] = {}

def cancel_download(video_id: str, audio_only: bool=True, owner=None) -> bool:
    key = f'{video_id}:{_cache_tag(audio_only)}'
    entry = _download_cancel_events.get(key)
    if entry is None:
        return False
    event, current_owner, _priority = entry
    if owner is not None and current_owner is not None and (current_owner != owner):
        return False
    event.set()
    return True

def cancel_lower_priority_downloads(max_priority: int=0, *, exclude_video_id: str | None=None) -> int:
    canceled = 0
    for key, entry in list(_download_cancel_events.items()):
        try:
            event, _owner, priority = entry
        except (TypeError, ValueError):
            continue
        video_id = key.rsplit(':', 1)[0]
        if exclude_video_id and video_id == exclude_video_id:
            continue
        if int(priority) <= int(max_priority) or event.is_set():
            continue
        event.set()
        canceled += 1
    if canceled:
        LOGGER.info('#download preempted %d low-priority job(s)', canceled)
    return canceled

class _DownloadCancelled(Exception):
    pass

class _EarlyDownloadState:

    def __init__(self):
        self.ready = asyncio.Event()
        self.path: str | None = None
_download_early_states: dict[str, _EarlyDownloadState] = {}

def is_download_cancelled(exc: BaseException) -> bool:
    return isinstance(exc, _DownloadCancelled)

class _PriorityDownloadGate:

    def __init__(self, capacity: int):
        self.capacity = max(1, capacity)
        self.running = 0
        self.sequence = 0
        self.waiters: list[tuple[int, int, asyncio.Future]] = []
        self.lock = asyncio.Lock()

    async def acquire(self, priority: int=0) -> None:
        async with self.lock:
            if self.running < self.capacity and (not self.waiters):
                self.running += 1
                return
            loop = asyncio.get_running_loop()
            future = loop.create_future()
            self.sequence += 1
            heapq.heappush(self.waiters, (int(priority), self.sequence, future))
        try:
            await future
        except asyncio.CancelledError:
            async with self.lock:
                for index, item in enumerate(self.waiters):
                    if item[2] is future:
                        self.waiters.pop(index)
                        heapq.heapify(self.waiters)
                        break
            raise

    async def release(self) -> None:
        async with self.lock:
            self.running = max(0, self.running - 1)
            while self.waiters:
                _, _, future = heapq.heappop(self.waiters)
                if future.cancelled():
                    continue
                self.running += 1
                future.set_result(None)
                break

def _consume_download_future(future: asyncio.Future) -> None:
    if future.cancelled():
        return
    try:
        future.exception()
    except (asyncio.CancelledError, Exception):
        pass

def _memory_budget_mb() -> int:
    for name in ('MEMORY_LIMIT_MB', 'MEMORY_AVAILABLE_MB', 'DYNO_RAM_MB'):
        raw = (os.getenv(name) or '').strip()
        if raw:
            try:
                value = int(raw)
            except ValueError:
                continue
            if value > 0:
                return value
    return 512
_MEMORY_BUDGET_MB = _memory_budget_mb()
_DEFAULT_CONCURRENT_DOWNLOADS = 1 if _MEMORY_BUDGET_MB <= 1024 else 2
try:
    _requested_downloads = max(1, int(os.getenv('MAX_CONCURRENT_DOWNLOADS', '') or _DEFAULT_CONCURRENT_DOWNLOADS))
except ValueError:
    _requested_downloads = _DEFAULT_CONCURRENT_DOWNLOADS
_MAX_CONCURRENT_DOWNLOADS = 1 if _MEMORY_BUDGET_MB <= 1024 else min(2, _requested_downloads)
_download_slots: '_PriorityDownloadGate | None' = None

def _download_semaphore() -> '_PriorityDownloadGate':
    global _download_slots
    if _download_slots is None:
        _download_slots = _PriorityDownloadGate(_MAX_CONCURRENT_DOWNLOADS)
    return _download_slots
_BGUTIL_STATUS_LOGGED = False
_BGUTIL_CONFIG_LOCK = threading.Lock()

def _bgutil_server_home() -> str:
    return os.path.normpath(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '..', 'vendor', 'bgutil-ytdlp-pot-provider', 'server'))
_BGUTIL_HTTP_PORT = 4416
_BGUTIL_HTTP_PROC: 'subprocess.Popen | None' = None
_BGUTIL_HTTP_READY = False
_BGUTIL_HTTP_LOCK = threading.Lock()

def ensure_bgutil_http_server() -> bool:
    global _BGUTIL_HTTP_PROC
    global _BGUTIL_HTTP_READY
    with _BGUTIL_HTTP_LOCK:
        if _BGUTIL_HTTP_READY:
            if _BGUTIL_HTTP_PROC is not None and _BGUTIL_HTTP_PROC.poll() is None:
                return True
            LOGGER.warning('⚠️ bgutil PO-token server stopped — resetting health and restarting')
            _BGUTIL_HTTP_READY = False
            _BGUTIL_HTTP_PROC = None
        if _BGUTIL_HTTP_PROC is not None and _BGUTIL_HTTP_PROC.poll() is None:
            return False
        server_home = _bgutil_server_home()
        main_ts = os.path.join(server_home, 'src', 'main.ts')
        deno = _deno_path()
        if not os.path.isfile(main_ts) or not (os.path.isfile(deno) or shutil.which(deno)):
            return False
        try:
            os.makedirs(os.path.join(server_home, 'cache'), exist_ok=True)
            _BGUTIL_HTTP_PROC = subprocess.Popen([deno, 'run', '--allow-env', '--allow-net', f"--allow-ffi={os.path.join(server_home, 'node_modules')}", f"--allow-write={os.path.join(server_home, 'cache')}", f"--allow-read={os.path.join(server_home, 'cache')},{os.path.join(server_home, 'node_modules')}", main_ts, '--port', str(_BGUTIL_HTTP_PORT)], cwd=server_home, env={**os.environ, 'XDG_CACHE_HOME': os.path.join(server_home, 'cache')}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            LOGGER.info('🚀 bgutil PO-token HTTP server starting on 127.0.0.1:%d', _BGUTIL_HTTP_PORT)
            return False
        except Exception as exc:
            LOGGER.warning('Could not start bgutil HTTP server, falling back to script mode: %s', exc)
            _BGUTIL_HTTP_PROC = None
            return False

def _wait_for_bgutil_http(timeout: float=3.0) -> bool:
    deadline = time.monotonic() + max(0.0, timeout)
    while time.monotonic() < deadline:
        if _bgutil_http_alive():
            return True
        proc = _BGUTIL_HTTP_PROC
        if proc is None or proc.poll() is not None:
            return False
        time.sleep(0.1)
    return _bgutil_http_alive()

def _bgutil_http_alive() -> bool:
    global _BGUTIL_HTTP_READY
    global _BGUTIL_HTTP_PROC
    previously_ready = _BGUTIL_HTTP_READY
    if _BGUTIL_HTTP_READY:
        proc = _BGUTIL_HTTP_PROC
        if proc is None or proc.poll() is not None:
            _BGUTIL_HTTP_READY = False
            LOGGER.warning('⚠️ bgutil health flag was stale — provider process is not alive')
    try:
        client = get_http_sync_client()
        resp = client.get(f'http://127.0.0.1:{_BGUTIL_HTTP_PORT}/ping', timeout=1.5)
        if resp.status_code == 200:
            _BGUTIL_HTTP_READY = True
            LOGGER.info('✅ bgutil PO-token HTTP server is up — using warm HTTP provider (fast path)')
            return True
    except Exception:
        pass
    _BGUTIL_HTTP_READY = False
    if previously_ready:
        proc = _BGUTIL_HTTP_PROC
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=0.5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
            LOGGER.warning('⚠️ bgutil PO-token server became unresponsive — restarting it')
        _BGUTIL_HTTP_PROC = None
    return False

async def warm_up_bgutil_server(timeout: float=20.0) -> None:
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(YTDL_POOL, ensure_deno_runtime)
    if not ensure_bgutil_http_server() and _BGUTIL_HTTP_PROC is None:
        return
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if await loop.run_in_executor(YTDL_POOL, _bgutil_http_alive):
            return
        await asyncio.sleep(0.5)
    LOGGER.warning('bgutil HTTP server did not respond within %.0fs — falling back to (slower) script mode', timeout)
_DENO_RUNTIME_DIR = '/tmp/melody_deno'

def _ensure_deno_cache_env() -> None:
    if os.environ.get('DENO_DIR'):
        return
    for candidate in (os.path.normpath(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '..', 'vendor', 'deno_cache')), '/app/vendor/deno_cache'):
        if os.path.isdir(candidate):
            os.environ['DENO_DIR'] = candidate
            return
    fallback = os.path.join(_DENO_RUNTIME_DIR, 'cache')
    os.makedirs(fallback, exist_ok=True)
    os.environ['DENO_DIR'] = fallback
_ensure_deno_cache_env()
_DENO_LOCK = threading.Lock()
_DENO_MIRRORS = ('https://dl.deno.land/release/{ver}/deno-x86_64-unknown-linux-gnu.zip', 'https://github.com/denoland/deno/releases/download/{ver}/deno-x86_64-unknown-linux-gnu.zip', 'https://github.com/denoland/deno/releases/latest/download/deno-x86_64-unknown-linux-gnu.zip')

def _deno_path() -> str:
    bundled = os.path.normpath(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '..', 'vendor', 'deno', 'bin', 'deno'))
    if os.path.isfile(bundled):
        return bundled
    runtime = os.path.join(_DENO_RUNTIME_DIR, 'deno')
    if os.path.isfile(runtime):
        return runtime
    return shutil.which('deno') or 'deno'

def _has_deno() -> bool:
    path = _deno_path()
    return bool(os.path.isfile(path) or shutil.which(path))

def ensure_deno_runtime() -> bool:
    with _DENO_LOCK:
        if _has_deno():
            return True
        version = 'v2.9.5'
        try:
            client = get_http_sync_client()
            resp = client.get('https://dl.deno.land/release-latest.txt', timeout=6)
            if resp.status_code == 200 and resp.text.strip().startswith('v'):
                version = resp.text.strip()
        except Exception:
            pass
        os.makedirs(_DENO_RUNTIME_DIR, exist_ok=True)
        target = os.path.join(_DENO_RUNTIME_DIR, 'deno')
        for mirror in _DENO_MIRRORS:
            url = mirror.format(ver=version)
            try:
                client = get_http_sync_client()
                resp = client.get(url, timeout=90, follow_redirects=True)
                if resp.status_code != 200 or len(resp.content) < 1000000:
                    continue
                archive = os.path.join(_DENO_RUNTIME_DIR, 'deno.zip')
                with open(archive, 'wb') as fh:
                    fh.write(resp.content)
                with zipfile.ZipFile(archive) as zf:
                    zf.extract('deno', _DENO_RUNTIME_DIR)
                os.chmod(target, 493)
                os.unlink(archive)
                LOGGER.info('✅ Deno %s installed at runtime (%s) — YouTube player challenge solvable again', version, target)
                return True
            except Exception as exc:
                LOGGER.warning('Deno mirror failed (%s): %s', url, exc)
        LOGGER.error('❌ Could not install Deno — YouTube formats stay PO-token gated. Redeploy so bin/post_compile can vendor Deno.')
        return False
try:
    __import__('curl_cffi')
    _CURL_CFFI_OK = True
    LOGGER.info('✅ curl_cffi available — Chrome TLS impersonation enabled')
except ImportError:
    _CURL_CFFI_OK = False
    LOGGER.warning("curl_cffi not installed — YouTube may block requests on cloud IPs. Add 'curl_cffi>=0.7.0' to requirements.txt and redeploy.")
COOKIES_FILE = '/tmp/melody_yt_cookies.txt'
_ON_CLOUD_HOST: bool = bool(os.environ.get('DYNO') or os.environ.get('RAILWAY_ENVIRONMENT') or os.environ.get('RENDER_SERVICE_ID') or os.environ.get('FLY_APP_NAME') or os.environ.get('K_SERVICE') or os.environ.get('WEBSITE_INSTANCE_ID'))
if _ON_CLOUD_HOST:
    LOGGER.info('☁️  Cloud host detected — direct CDN + download fallback race enabled')

def should_try_direct_stream() -> bool:
    return os.getenv('DIRECT_STREAM', 'true').strip().lower() not in {'0', 'false', 'no', 'off'}

def _json_cookies_to_netscape(json_text: str) -> str:
    import json
    try:
        cookies = json.loads(json_text)
    except (json.JSONDecodeError, ValueError):
        return json_text
    if not isinstance(cookies, list):
        return json_text
    lines = ['# Netscape HTTP Cookie File', f'# Generated by {BOT_NAME}']
    for c in cookies:
        domain = c.get('domain', '')
        include_sub = 'TRUE' if domain.startswith('.') else 'FALSE'
        path = c.get('path', '/')
        secure = 'TRUE' if c.get('secure', False) else 'FALSE'
        expiry = int(c.get('expirationDate') or c.get('expires') or 0)
        name = c.get('name', '')
        value = c.get('value', '')
        lines.append(f'{domain}\t{include_sub}\t{path}\t{secure}\t{expiry}\t{name}\t{value}')
    return '\n'.join(lines) + '\n'

def _normalize_netscape(text: str) -> str:
    out = ['# Netscape HTTP Cookie File', f'# Generated by {BOT_NAME}']
    now = int(time.time())
    for line in text.lstrip('\ufeff').splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith('#'):
            continue
        parts = stripped.split('\t')
        if len(parts) != 7:
            parts = stripped.split()
            if len(parts) > 7:
                parts = parts[:6] + [' '.join(parts[6:])]
        if len(parts) == 6:
            parts.append('')
        if len(parts) != 7:
            continue
        # 0 is a session cookie. Only discard cookies whose exported expiry
        # is genuinely in the past; never apply an artificial short TTL.
        try:
            expiry = int(float(parts[4] or 0))
        except (TypeError, ValueError):
            expiry = 0
        if expiry > 0 and expiry <= now:
            continue
        out.append('\t'.join(parts))
    return '\n'.join(out) + '\n'

def _is_netscape_cookies(text: str) -> bool:
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if len(line.split('\t')) == 7 or len(line.split()) >= 6:
            return True
    return False
_COOKIE_TEXT: str = ''  # backwards-compatible alias for the first/active cookie
_COOKIE_POOL: list[str] = []
_COOKIE_CURSOR: int = 0
_COOKIE_SELECTED_BY_THREAD: dict[int, int] = {}
_COOKIE_REJECTED_UNTIL: dict[int, float] = {}
_COOKIE_DIR = '/tmp/melody_cookies'
_COOKIE_LOCK = threading.Lock()
# Copies are temporary runtime files; the cookie data itself is persisted in MongoDB.
try:
    _COOKIE_TTL_SECONDS = max(3600, int(os.getenv('YT_COOKIE_COPY_TTL_SECONDS', '86400')))
except (TypeError, ValueError):
    _COOKIE_TTL_SECONDS = 86400

def _write_atomic(path: str, text: str) -> None:
    tmp = f'{path}.{os.getpid()}.{threading.get_ident()}.tmp'
    with open(tmp, 'w', encoding='utf-8', newline='\n') as f:
        f.write(text)
    os.replace(tmp, path)

def _reap_cookie_copies() -> None:
    cutoff = time.time() - _COOKIE_TTL_SECONDS
    try:
        for name in os.listdir(_COOKIE_DIR):
            path = os.path.join(_COOKIE_DIR, name)
            try:
                if os.path.getmtime(path) < cutoff:
                    os.remove(path)
            except OSError:
                pass
    except OSError:
        pass
_COOKIE_DISTRUST_UNTIL: float = 0.0  # kept for compatibility with older callers
_COOKIE_DISTRUST_SECONDS: float = float(os.getenv('YT_COOKIE_DISTRUST_SECONDS', '900'))

def _eligible_cookie_indexes(now: float | None = None) -> list[int]:
    now = time.time() if now is None else now
    return [i for i in range(len(_COOKIE_POOL)) if _COOKIE_REJECTED_UNTIL.get(i, 0.0) <= now]

def cookies_distrusted() -> bool:
    # True only when every configured cookie is temporarily cooling down.
    return bool(_COOKIE_POOL) and not _eligible_cookie_indexes()

def note_cookie_rejected(reason: str='') -> None:
    # Do not disable the entire pool after one bot-wall response. Cool down only
    # the cookie used by this yt-dlp worker and immediately rotate to another.
    thread_id = threading.get_ident()
    with _COOKIE_LOCK:
        index = _COOKIE_SELECTED_BY_THREAD.get(thread_id)
        if index is None or index >= len(_COOKIE_POOL):
            return
        _COOKIE_REJECTED_UNTIL[index] = time.time() + _COOKIE_DISTRUST_SECONDS
        remaining = len(_eligible_cookie_indexes())
    LOGGER.warning('🍪 YouTube rejected cookie #%d (%s); rotating cookie pool (%d still available).', index + 1, reason or 'bot check', remaining)

def cookiefile_for_run() -> 'str | None':
    global _COOKIE_CURSOR
    with _COOKIE_LOCK:
        eligible = _eligible_cookie_indexes()
        if not eligible:
            return None
        # Round-robin prevents one account from being used for every song.
        chosen = next((i for i in eligible if i >= _COOKIE_CURSOR), eligible[0])
        _COOKIE_CURSOR = (chosen + 1) % max(1, len(_COOKIE_POOL))
        _COOKIE_SELECTED_BY_THREAD[threading.get_ident()] = chosen
        text = _COOKIE_POOL[chosen]
        try:
            os.makedirs(_COOKIE_DIR, exist_ok=True)
            _reap_cookie_copies()
            path = os.path.join(_COOKIE_DIR, f'c_{os.getpid()}_{threading.get_ident()}_{time.time_ns()}_{chosen}.txt')
            _write_atomic(path, text)
            return path
        except OSError as exc:
            LOGGER.warning('cookie copy failed (%s) — falling back to master file', exc)
            return COOKIES_FILE if os.path.exists(COOKIES_FILE) else None

def _store_cookies(text: str, *, append: bool = False) -> None:
    global _COOKIE_TEXT, _COOKIE_POOL, _COOKIE_CURSOR
    normalized = _normalize_netscape(text)
    if not _is_netscape_cookies(normalized):
        return
    with _COOKIE_LOCK:
        if not append:
            _COOKIE_POOL = []
            _COOKIE_REJECTED_UNTIL.clear()
        if normalized not in _COOKIE_POOL:
            _COOKIE_POOL.append(normalized)
        _COOKIE_TEXT = _COOKIE_POOL[0] if _COOKIE_POOL else normalized
        _COOKIE_CURSOR %= max(1, len(_COOKIE_POOL))
        # Keep the legacy master file in sync with the first cookie.
        _write_atomic(COOKIES_FILE, _COOKIE_TEXT)

def remove_runtime_cookies() -> int:
    """Remove all active cookies and their temporary yt-dlp copies."""
    global _COOKIE_TEXT, _COOKIE_POOL, _COOKIE_CURSOR, _HAS_COOKIES
    with _COOKIE_LOCK:
        removed = len(_COOKIE_POOL)
        _COOKIE_TEXT = ''
        _COOKIE_POOL = []
        _COOKIE_CURSOR = 0
        _COOKIE_REJECTED_UNTIL.clear()
        _COOKIE_SELECTED_BY_THREAD.clear()
        for path in (COOKIES_FILE,):
            try:
                os.unlink(path)
            except OSError:
                pass
        try:
            for name in os.listdir(_COOKIE_DIR):
                try:
                    os.unlink(os.path.join(_COOKIE_DIR, name))
                except OSError:
                    pass
        except OSError:
            pass
        _HAS_COOKIES = False
    LOGGER.info('🍪 Runtime YouTube cookie pool removed (%d cookie(s)).', removed)
    return removed

def cookie_pool_snapshot() -> list[str]:
    with _COOKIE_LOCK:
        return list(_COOKIE_POOL)

def cookie_pool_status() -> dict:
    """Return safe cookie-pool metrics without exposing cookie contents."""
    now = time.time()
    with _COOKIE_LOCK:
        # Rewriting through _store_cookies is deliberately avoided here: this
        # function is also used by the owner UI and must remain side-effect free.
        total = len(_COOKIE_POOL)
        available = len(_eligible_cookie_indexes(now))
        cooling = max(0, total - available)
        cooldown_until = max((_COOKIE_REJECTED_UNTIL.get(i, 0.0) for i in range(total)), default=0.0)
    return {
        'total': total,
        'available': available,
        'cooling_down': cooling,
        'all_cooling': bool(total and available == 0),
        'next_retry_seconds': max(0, int(cooldown_until - now)) if cooling else 0,
    }

def _write_cookies():
    if not Config.YT_COOKIES:
        LOGGER.warning('⚠️ YT_COOKIES is not set — bot will run WITHOUT a YouTube login. Cloud IPs are heavily bot-checked, so cookies are strongly recommended.')
        return
    raw = Config.YT_COOKIES.strip()
    if raw.startswith('['):
        netscape = _json_cookies_to_netscape(raw)
        if _is_netscape_cookies(netscape):
            _store_cookies(netscape, append=False)
            LOGGER.info('✅ YT_COOKIES (plain JSON→Netscape) written to %s', COOKIES_FILE)
            return
    if raw.startswith('#') or _is_netscape_cookies(raw):
        _store_cookies(raw, append=False)
        LOGGER.info('✅ YT_COOKIES (plain Netscape text) written to %s', COOKIES_FILE)
        return
    try:
        raw_bytes = base64.b64decode(raw, validate=True)
    except Exception as e:
        LOGGER.warning('❌ YT_COOKIES is neither plain cookies.txt text nor valid base64 — skipping cookies: %s', e)
        return
    try:
        decoded = raw_bytes.decode('utf-8')
    except UnicodeDecodeError:
        LOGGER.warning('❌ YT_COOKIES base64 decodes to binary data (not a text cookie file). Paste the raw cookies.txt content directly into the config var — no base64 encoding needed. Skipping cookies.')
        return
    decoded = decoded.strip()
    if decoded.startswith('['):
        netscape = _json_cookies_to_netscape(decoded)
        if not _is_netscape_cookies(netscape):
            LOGGER.warning('❌ YT_COOKIES JSON conversion produced no valid entries — skipping.')
            return
        _store_cookies(netscape, append=False)
        LOGGER.info('✅ YT_COOKIES (base64 JSON→Netscape) written to %s', COOKIES_FILE)
        return
    if _is_netscape_cookies(decoded):
        _store_cookies(decoded, append=False)
        LOGGER.info('✅ YT_COOKIES (base64 Netscape) written to %s', COOKIES_FILE)
        return
    LOGGER.warning("❌ YT_COOKIES format not recognised (not JSON array, not Netscape). Export cookies using a browser extension like 'Get cookies.txt LOCALLY' while logged into youtube.com, then paste the file content as-is. Skipping cookies — yt-dlp will run without authentication.")
_write_cookies()
_HAS_COOKIES = bool(_COOKIE_POOL)


def _decode_cookie_input(raw: str) -> str | None:
    """Accept browser cookie text, JSON, base64, and common pasted wrappers."""
    raw = str(raw or '').strip().lstrip('\ufeff')
    if not raw:
        return None
    # Telegram/Markdown code fences and labels are common when pasting base64.
    if raw.startswith('```') and raw.endswith('```'):
        raw = raw[3:-3].strip()
        first, sep, rest = raw.partition('\n')
        if first.strip().lower() in {'text', 'txt', 'base64', 'cookies'}:
            raw = rest.strip() if sep else ''
    lowered = raw.lower()
    for prefix in ('base64:', 'cookies:', 'cookie:'):
        if lowered.startswith(prefix):
            raw = raw[len(prefix):].strip()
            break
    decoded = raw
    if decoded.startswith('['):
        decoded = _json_cookies_to_netscape(decoded)
    elif not _is_netscape_cookies(decoded):
        try:
            # Base64 pasted in Telegram may contain line breaks/spaces.
            compact = ''.join(decoded.split())
            decoded_text = base64.b64decode(compact, validate=True).decode('utf-8')
            decoded = _json_cookies_to_netscape(decoded_text) if decoded_text.lstrip().startswith('[') else decoded_text
        except Exception:
            return None
    decoded = _normalize_netscape(decoded)
    return decoded if _is_netscape_cookies(decoded) else None
def set_runtime_cookies(raw: str, *, append: bool = True) -> bool:
    """Validate and activate owner-supplied cookies without a restart.

    New uploads are appended to the in-memory pool by default. The caller can
    pass append=False for a deliberate full replacement/migration.
    """
    global _HAS_COOKIES, _COOKIE_DISTRUST_UNTIL
    decoded = _decode_cookie_input(raw)
    if not decoded:
        return False
    _store_cookies(decoded, append=append)
    _HAS_COOKIES = bool(_COOKIE_POOL)
    _COOKIE_DISTRUST_UNTIL = 0.0
    LOGGER.info('🍪 Runtime YouTube cookie accepted; pool size=%d.', len(_COOKIE_POOL))
    return _HAS_COOKIES

if _HAS_COOKIES:
    LOGGER.info('🍪 Cookie-authenticated YouTube session ACTIVE — using logged-in requests')
else:
    LOGGER.warning('🍪 No cookies loaded — running as anonymous guest (more likely to be blocked from cloud IPs)')
if not _HAS_COOKIES and (not os.getenv('YTDLP_PROXY', '').strip()):
    LOGGER.error("🚨 ROOT CAUSE OF EVERY 'Sign in to confirm you're not a bot' FAILURE: no YT_COOKIES and no YTDLP_PROXY are configured. From a datacenter IP YouTube returns ZERO playable formats to an anonymous session even with a valid PO token, so playback cannot work. Fix: set the YT_COOKIES config var to the contents of a cookies.txt exported while logged into youtube.com, or set YTDLP_PROXY to a residential proxy. Everything else in the resolver ladder is already working.")
_SMALL_MUXED_SELECTOR = 'best[acodec!=none][height<=144]/best[acodec!=none][height<=240]/best[acodec!=none][height<=360]/worst[acodec!=none]/worst/best'

def _ydl_opts(audio_only: bool=True) -> dict:
    fmt = f"bestaudio[ext=webm][abr<={_env_int('YT_AUDIO_MAX_ABR', 128)}]/bestaudio[ext=webm][abr<=96]/bestaudio[ext=webm]/bestaudio[ext=opus]/bestaudio[ext=ogg]/bestaudio[acodec=opus]/bestaudio[abr<=128]/bestaudio[ext=m4a]/bestaudio[format_id=140]/bestaudio[format_id=139]/bestaudio[protocol=m3u8]/bestaudio[protocol=m3u8_native]/bestaudio*[vcodec=none]/{_SMALL_MUXED_SELECTOR}" if audio_only else 'best[vcodec!=none][acodec!=none][height<=720]/best[vcodec!=none][acodec!=none]/bestvideo[height<=480]+bestaudio[abr<=128]/best[height<=480]/best'
    try:
        ensure_bgutil_http_server()
    except Exception as exc:
        LOGGER.debug('lazy bgutil startup skipped: %s', exc)
    cookiefile = cookiefile_for_run()
    has_cookies = bool(cookiefile)
    user_agent = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36' if has_cookies else 'Mozilla/5.0 (Linux; Android 13; SM-S908B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/112.0.0.0 Mobile Safari/537.36'
    extractor_args: dict = {'player_client': ['web_safari', 'mweb', 'android_vr'] if has_cookies else ['mweb', 'android_vr', 'tv_simply'], 'formats': ['missing_pot'], 'player_skip': ['configs', 'initial_data'], 'skip': ['translated_subs']}
    provider_args: dict = {'youtube': extractor_args}
    bgutil_server = _bgutil_server_home()
    bgutil_script = os.path.join(bgutil_server, 'src', 'generate_once.ts')
    global _BGUTIL_STATUS_LOGGED
    with _BGUTIL_CONFIG_LOCK:
        http_ready = _wait_for_bgutil_http(timeout=_env_float('BGUTIL_HTTP_READY_WAIT', 3.0))
        if http_ready:
            provider_args['youtubepot-bgutilhttp'] = {'base_url': [f'http://127.0.0.1:{_BGUTIL_HTTP_PORT}']}
            if not _BGUTIL_STATUS_LOGGED:
                LOGGER.info('✅ bgutil PO-token provider configured (HTTP, warm): 127.0.0.1:%d', _BGUTIL_HTTP_PORT)
                _BGUTIL_STATUS_LOGGED = True
        elif os.path.isfile(bgutil_script):
            provider_args['youtubepot-bgutilscript'] = {'server_home': [bgutil_server]}
            if not _BGUTIL_STATUS_LOGGED:
                LOGGER.info('✅ bgutil PO-token provider configured (script, slower): %s', bgutil_server)
                _BGUTIL_STATUS_LOGGED = True
        elif not _BGUTIL_STATUS_LOGGED:
            LOGGER.warning("⚠️ bgutil PO-token provider not installed at %s — YouTube may block cloud-host requests with 'Sign in to confirm you're not a bot'. Redeploy so bin/post_compile can install it.", bgutil_server)
            _BGUTIL_STATUS_LOGGED = True
    opts: dict = {'quiet': True, 'cachedir': os.getenv('YTDLP_CACHE_DIR', '/tmp/yt-dlp-cache'), 'noprogress': True, 'no_color': True, 'no_warnings': True, 'noplaylist': True, 'format': fmt, 'postprocessors': [], 'fixup': 'never', 'geo_bypass': True, 'geo_bypass_country': 'US', 'socket_timeout': _env_int('YT_SOCKET_TIMEOUT', 5), 'retries': _env_int('YT_RETRIES', 1), 'fragment_retries': _env_int('YT_FRAGMENT_RETRIES', 5), 'extractor_retries': _env_int('YT_EXTRACTOR_RETRIES', 1), 'file_access_retries': 3, 'external_downloader': {'default': 'native'}, 'hls_prefer_native': True, 'hls_use_mpegts': _env_flag('HLS_USE_MPEGTS', True), 'check_formats': False, 'playlist_items': '1', 'getcomments': False, 'writesubtitles': False, 'writeautomaticsub': False, 'concurrent_fragment_downloads': min(2, _env_int('YT_CONCURRENT_FRAGMENTS', 2)) if os.getenv('MEMORY_LIMIT_MB', '512').strip().isdigit() and int(os.getenv('MEMORY_LIMIT_MB', '512')) <= 768 else _env_int('YT_CONCURRENT_FRAGMENTS', 8), 'remote_components': ['ejs:github'], 'extractor_args': provider_args, 'merge_output_format': 'mp4', 'http_headers': {'User-Agent': user_agent, 'Referer': 'https://www.youtube.com/'}}
    if _has_deno():
        opts['js_runtimes'] = {'deno': {'path': _deno_path()}}
    if cookiefile:
        opts['cookiefile'] = cookiefile
    proxy = os.getenv('YTDLP_PROXY', '').strip()
    if proxy:
        opts['proxy'] = proxy
    return opts

async def search_youtube(query: str, limit: int=5) -> list[dict]:

    def _search():
        opts = {**_ydl_opts(), 'default_search': f'ytsearch{limit}', 'extract_flat': True}
        with _locked_ytdl(opts) as ydl:
            info = ydl.extract_info(query, download=False)
            return info.get('entries', [])
    loop = asyncio.get_running_loop()
    if ytapi_enabled():
        try:
            api_results = await asyncio.wait_for(loop.run_in_executor(YTDL_POOL, _ytapi_search_sync, query, limit), timeout=4.0)
            if api_results:
                return [{'id': r['id'], 'title': r['title'], 'duration': r['duration'], 'url': r['webpage_url'], 'thumbnail': r['thumbnail'], 'uploader': r['uploader']} for r in api_results]
        except Exception as exc:
            LOGGER.debug('search_youtube: API v3 failed (%s) — using yt-dlp', exc)
    try:
        results = await loop.run_in_executor(YTDL_POOL, _search)
        return [{'id': e.get('id', ''), 'title': e.get('title', 'Unknown'), 'duration': e.get('duration', 0), 'url': f"https://www.youtube.com/watch?v={e.get('id', '')}", 'thumbnail': e.get('thumbnail') or f"https://i.ytimg.com/vi/{e.get('id', '')}/hqdefault.jpg", 'uploader': e.get('uploader', 'Unknown')} for e in results if e.get('id')]
    except Exception as exc:
        await send_error_log('search_youtube failed', exc, context={'video_id': query})
        return []

async def find_playable_candidate(query: str, exclude_video_id: str='', want_video: bool=False) -> dict | None:
    if not query or query.strip().lower().startswith(('http://', 'https://')):
        return None
    try:
        candidates = await asyncio.wait_for(search_youtube(query, limit=5), timeout=4.0)
    except Exception as exc:
        LOGGER.debug('alternate search failed for %r: %s', query[:60], exc)
        return None
    for candidate in candidates:
        video_id = candidate.get('id', '')
        if not video_id or video_id == exclude_video_id:
            continue
        try:
            resolved = await asyncio.wait_for(resolve_stream_urls(video_id, want_video=want_video), timeout=3.5)
            if not resolved:
                continue
            result = dict(candidate)
            result['stream_url'] = resolved.get('audio') or resolved.get('video') or resolved.get('url') or resolved.get('audio_url') or resolved.get('video_url') or ''
            result['webpage_url'] = result.get('url') or f'https://www.youtube.com/watch?v={video_id}'
            LOGGER.info('♻️ Replaced unavailable result %s with playable candidate %s', exclude_video_id, video_id)
            return result
        except Exception as exc:
            LOGGER.info('Skipping unplayable alternate %s: %s', video_id, exc)
    return None

async def get_playlist_entries(url_or_query: str, limit: int=50) -> list[dict]:

    def _extract():
        opts = {**_ydl_opts(), 'extract_flat': 'in_playlist', 'noplaylist': False, 'playlist_items': f'1:{max(1, limit)}', 'ignoreerrors': True}
        opts.pop('format', None)
        with _locked_ytdl(opts) as ydl:
            info = ydl.extract_info(url_or_query, download=False)
            if not info:
                return []
            if 'entries' in info:
                return [e for e in info['entries'] if e]
            return [info] if info.get('id') else []
    try:
        loop = asyncio.get_running_loop()
        entries = await loop.run_in_executor(YTDL_POOL, _extract)
        results = []
        for e in entries:
            vid = e.get('id', '')
            if not vid:
                continue
            results.append({'id': vid, 'title': e.get('title', 'Unknown'), 'duration': int(e.get('duration') or 0), 'url': f'https://www.youtube.com/watch?v={vid}', 'thumbnail': e.get('thumbnail') or f'https://i.ytimg.com/vi/{vid}/hqdefault.jpg', 'uploader': e.get('uploader', 'Unknown')})
        return results
    except Exception as exc:
        await send_error_log('get_playlist_entries failed', exc, context={'video_id': url_or_query})
        return []

def _extract_video_id(url: str) -> str | None:
    import re
    patterns = ['(?:v=|/v/|youtu\\.be/|/embed/|/shorts/)([A-Za-z0-9_-]{11})']
    for p in patterns:
        m = re.search(p, url)
        if m:
            return m.group(1)
    return None
_YTAPI_BASE = 'https://www.googleapis.com/youtube/v3'
_ytapi_dead_keys: set = set()

def _ytapi_keys() -> list:
    raw = (getattr(Config, 'YOUTUBE_API_KEY', '') or '').replace(' ', '')
    keys = [k for k in raw.split(',') if k]
    return [k for k in keys if k not in _ytapi_dead_keys]

def ytapi_enabled() -> bool:
    return bool(_ytapi_keys())

def _ytapi_get(path: str, params: dict) -> 'dict | None':
    client = get_http_sync_client()
    for key in _ytapi_keys():
        try:
            resp = client.get(f'{_YTAPI_BASE}/{path}', params={**params, 'key': key}, timeout=6.0)
        except Exception as exc:
            LOGGER.debug('ytapi %s network error: %s', path, exc)
            continue
        if resp.status_code == 200:
            try:
                return resp.json()
            except Exception:
                return None
        if resp.status_code in (400, 403):
            body = resp.text[:200]
            if 'quota' in body.lower() or 'keyInvalid' in body or 'API key not valid' in body:
                _ytapi_dead_keys.add(key)
                LOGGER.warning('YOUTUBE_API_KEY retired (quota/invalid): %s', body)
                continue
        LOGGER.debug('ytapi %s -> HTTP %s', path, resp.status_code)
    return None

def extract_video_id(url: str) -> str | None:
    return _extract_video_id(url or '')

def _iso8601_to_seconds(value: str) -> int:
    import re as _re
    m = _re.fullmatch('P(?:(\\d+)D)?T?(?:(\\d+)H)?(?:(\\d+)M)?(?:(\\d+)S)?', value or '')
    if not m:
        return 0
    d, h, mi, s = (int(g or 0) for g in m.groups())
    return d * 86400 + h * 3600 + mi * 60 + s

def _ytapi_item_to_info(item: dict) -> 'dict | None':
    if not item:
        return None
    vid = item.get('id')
    if isinstance(vid, dict):
        vid = vid.get('videoId')
    if not is_valid_video_id(vid):
        return None
    snippet = item.get('snippet') or {}
    thumbs = snippet.get('thumbnails') or {}
    thumb = ''
    for size in ('maxres', 'standard', 'high', 'medium', 'default'):
        if thumbs.get(size, {}).get('url'):
            thumb = thumbs[size]['url']
            break
    duration = _iso8601_to_seconds((item.get('contentDetails') or {}).get('duration') or '')
    return {'id': vid, 'title': snippet.get('title') or 'Unknown', 'duration': duration, 'webpage_url': f'https://www.youtube.com/watch?v={vid}', 'thumbnail': thumb or f'https://i.ytimg.com/vi/{vid}/hqdefault.jpg', 'uploader': snippet.get('channelTitle') or 'Unknown'}

def _ytapi_details_sync(video_id: str) -> 'dict | None':
    if not (ytapi_enabled() and is_valid_video_id(video_id)):
        return None
    data = _ytapi_get('videos', {'part': 'snippet,contentDetails', 'id': video_id, 'maxResults': 1})
    items = (data or {}).get('items') or []
    return _ytapi_item_to_info(items[0]) if items else None

def _ytapi_search_sync(query: str, limit: int=1) -> 'dict | list | None':
    if not ytapi_enabled():
        return None if limit == 1 else []
    data = _ytapi_get('search', {'part': 'snippet', 'q': query, 'type': 'video', 'maxResults': max(1, min(limit, 25))})
    items = [i for i in (data or {}).get('items') or [] if i]
    ids = []
    for it in items:
        vid = (it.get('id') or {}).get('videoId')
        if is_valid_video_id(vid):
            ids.append(vid)
    if not ids:
        return None if limit == 1 else []
    details = _ytapi_get('videos', {'part': 'snippet,contentDetails', 'id': ','.join(ids)})
    by_id = {}
    for it in (details or {}).get('items') or []:
        info = _ytapi_item_to_info(it)
        if info:
            by_id[info['id']] = info
    results = []
    for it in items:
        vid = (it.get('id') or {}).get('videoId')
        info = by_id.get(vid) or _ytapi_item_to_info(it)
        if info:
            results.append(info)
    if limit == 1:
        return results[0] if results else None
    return results

def _innertube_search_sync(query: str) -> dict | None:
    import json
    _SEARCH_URL = 'https://www.youtube.com/youtubei/v1/search'
    _CLIENT_CONTEXTS = [('WEB', '2.20260801.00.00', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'), ('IOS', '20.10.4', 'com.google.ios.youtube/20.10.4 (iPhone16,2; U; CPU iOS 18_3_2 like Mac OS X)')]
    for client_name, client_ver, ua in _CLIENT_CONTEXTS:
        url = f'{_SEARCH_URL}?prettyPrint=false'
        client_ctx = {'clientName': client_name, 'clientVersion': client_ver, 'hl': 'en', 'gl': 'US', 'utcOffsetMinutes': 0}
        if client_name == 'ANDROID':
            client_ctx['androidSdkVersion'] = 30
        elif client_name == 'IOS':
            client_ctx['deviceMake'] = 'Apple'
            client_ctx['deviceModel'] = 'iPhone16,2'
        payload = json.dumps({'context': {'client': client_ctx}, 'query': query, 'params': 'EgIQAQ=='}).encode('utf-8')
        try:
            client = get_http_sync_client()
            resp = client.post(url, content=payload, headers={'Content-Type': 'application/json', 'User-Agent': ua, 'Accept-Language': 'en-US,en;q=0.9'}, timeout=12.0)
            if resp.status_code != 200:
                LOGGER.warning('InnerTube %s search HTTP %s for %s', client_name, resp.status_code, query[:40])
                continue
            data = json.loads(resp.text)
        except Exception as exc:
            LOGGER.warning('InnerTube %s search error: %s', client_name, exc)
            continue
        if data:
            break
    if not data:
        LOGGER.warning('InnerTube search: all client contexts failed for: %s', query[:50])
        return None

    def _parse_video_renderer(vr: dict) -> 'dict | None':
        vid = vr.get('videoId', '')
        if not vid:
            return None
        title = vr.get('title', {}).get('runs', [{}])[0].get('text', '') or query
        duration_text = vr.get('lengthText', {}).get('simpleText', '') or vr.get('lengthText', {}).get('runs', [{}])[0].get('text', '')
        duration = 0
        if duration_text:
            parts = [int(p) for p in duration_text.split(':') if p.isdigit()]
            if len(parts) == 2:
                duration = parts[0] * 60 + parts[1]
            elif len(parts) == 3:
                duration = parts[0] * 3600 + parts[1] * 60 + parts[2]
        thumbs = vr.get('thumbnail', {}).get('thumbnails') or []
        thumbnail = thumbs[-1].get('url', '') if thumbs else f'https://i.ytimg.com/vi/{vid}/hqdefault.jpg'
        uploader = vr.get('ownerText', {}).get('runs', [{}])[0].get('text', '') or vr.get('longBylineText', {}).get('runs', [{}])[0].get('text', '') or 'Unknown'
        return {'id': vid, 'webpage_url': f'https://www.youtube.com/watch?v={vid}', 'title': title, 'duration': duration, 'thumbnail': thumbnail, 'uploader': uploader}
    try:
        contents = data.get('contents', {}).get('sectionListRenderer', {}).get('contents', [])
        for section in contents:
            items = section.get('itemSectionRenderer', {}).get('contents', [])
            for item in items:
                vr = item.get('videoRenderer')
                if not vr:
                    continue
                result = _parse_video_renderer(vr)
                if result:
                    LOGGER.info('✅ InnerTube search OK: %s', result['title'][:50])
                    return result
    except Exception as exc:
        LOGGER.warning('InnerTube response parse error: %s', exc)
    try:

        def _walk(node, out):
            if isinstance(node, dict):
                vr = node.get('videoRenderer')
                if vr and vr.get('videoId'):
                    out.append(vr)
                for v in node.values():
                    _walk(v, out)
            elif isinstance(node, list):
                for item in node:
                    _walk(item, out)
        renderers: list = []
        _walk(data, renderers)
        for vr in renderers:
            result = _parse_video_renderer(vr)
            if result:
                LOGGER.info('✅ InnerTube search OK (tree-walk): %s', result['title'][:50])
                return result
    except Exception as exc:
        LOGGER.warning('InnerTube tree-walk parse error: %s', exc)
    LOGGER.warning('❌ InnerTube search: no video results for: %s', query[:50])
    return None
_INVIDIOUS_INSTANCES = ['https://invidious.f5.si', 'https://invidious.privacydev.net', 'https://invidious.nerdvpn.de', 'https://invidious.fdn.fr', 'https://invidious.io.lol', 'https://invidious.einfachzocken.eu', 'https://invidious.privacyredirect.com', 'https://invidious.jing.rocks', 'https://invidious.asir.dev', 'https://invidious.drgns.space']
_INVIDIOUS_INSTANCES = [item.strip().rstrip('/') for item in (os.getenv('INVIDIOUS_INSTANCES') or '').split(',') if item.strip().startswith('https://')] or _INVIDIOUS_INSTANCES
try:
    _INVIDIOUS_MAX_INSTANCES = max(1, min(3, int(os.getenv('INVIDIOUS_MAX_INSTANCES', '3'))))
except (TypeError, ValueError):
    _INVIDIOUS_MAX_INSTANCES = 3
_INVIDIOUS_DEAD: dict = {}
_INVIDIOUS_DEAD_LOCK = threading.Lock()
try:
    _INVIDIOUS_DEAD_TTL = max(60.0, float(os.getenv('INVIDIOUS_DEAD_TTL', '900')))
except (TypeError, ValueError):
    _INVIDIOUS_DEAD_TTL = 900.0

def _invidious_live_instances() -> list:
    now = time.time()
    with _INVIDIOUS_DEAD_LOCK:
        for host, until in list(_INVIDIOUS_DEAD.items()):
            if until <= now:
                _INVIDIOUS_DEAD.pop(host, None)
        dead = set(_INVIDIOUS_DEAD)
    live = [inst for inst in _INVIDIOUS_INSTANCES if inst not in dead]
    return live[:_INVIDIOUS_MAX_INSTANCES]

def _mark_invidious_dead(instance: str) -> None:
    with _INVIDIOUS_DEAD_LOCK:
        _INVIDIOUS_DEAD[instance] = time.time() + _INVIDIOUS_DEAD_TTL

def _invidious_search_sync(query: str) -> dict | None:
    import json, urllib.parse
    encoded = urllib.parse.quote_plus(query)
    live = _invidious_live_instances()
    if not live:
        LOGGER.debug('Invidious search skipped — every instance is in failure cooldown')
        return None
    for inst in live:
        try:
            url = f'{inst}/api/v1/search?q={encoded}&type=video'
            client = get_http_sync_client()
            resp = client.get(url, headers={'User-Agent': 'Mozilla/5.0 (compatible; MusicBot/1.0)'}, timeout=5.0)
            if resp.status_code != 200:
                _mark_invidious_dead(inst)
                continue
            items = json.loads(resp.text)
            res = next((i for i in items if i.get('type') == 'video' and i.get('videoId')), None)
            if not res:
                continue
            vid = res['videoId']
            thumbs = res.get('videoThumbnails') or []
            thumb = next((t.get('url', '') for t in thumbs if t.get('quality') in {'medium', 'high'}), thumbs[0].get('url', '') if thumbs else '')
            LOGGER.info('✅ Invidious search OK (%s): %s', inst, res.get('title', '')[:50])
            return {'id': vid, 'webpage_url': f'https://www.youtube.com/watch?v={vid}', 'title': res.get('title') or query, 'duration': int(res.get('lengthSeconds') or 0), 'thumbnail': thumb, 'uploader': res.get('author') or ''}
        except Exception as exc:
            _mark_invidious_dead(inst)
            LOGGER.debug('Invidious (%s) failed: %s', inst, exc)
    LOGGER.warning('❌ All Invidious instances failed for: %s', query[:50])
    return None

def is_valid_video_id(vid: 'str | None') -> bool:
    import re
    return bool(re.fullmatch('[A-Za-z0-9_-]{11}', vid or ''))

def _normalize_info(info: 'dict | None') -> 'dict | None':
    if not info or not info.get('id'):
        return None
    if info.get('_type') in ('playlist', 'multi_video', 'url_transparent'):
        return None
    vid = str(info.get('id', ''))
    import re
    if not (is_valid_video_id(vid) or re.fullmatch('tg[A-Za-z0-9_]+', vid)):
        LOGGER.warning('Discarding metadata with non-video id %r', vid[:60])
        return None
    return {'id': vid, 'title': info.get('title', 'Unknown'), 'duration': int(info.get('duration') or 0), 'url': info.get('webpage_url') or f'https://www.youtube.com/watch?v={vid}', 'stream_url': '', 'thumbnail': info.get('thumbnail') or f'https://i.ytimg.com/vi/{vid}/hqdefault.jpg', 'uploader': info.get('uploader') or info.get('channel') or 'Unknown'}

def _youtube_oembed_sync(video_id: str) -> dict | None:
    import json
    from urllib.parse import quote
    url = 'https://www.youtube.com/oembed?format=json&url=' + quote(f'https://www.youtube.com/watch?v={video_id}', safe='')
    try:
        client = get_http_sync_client()
        response = client.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=2.0)
        if response.status_code != 200:
            return None
        data = json.loads(response.text)
        return {'id': video_id, 'title': data.get('title') or 'Unknown', 'uploader': data.get('author_name') or 'Unknown', 'webpage_url': f'https://www.youtube.com/watch?v={video_id}', 'thumbnail': f'https://i.ytimg.com/vi/{video_id}/hqdefault.jpg', 'duration': 0}
    except Exception as exc:
        LOGGER.debug('YouTube oEmbed failed for %s: %s', video_id, type(exc).__name__)
        return None

def _innertube_next_sync(video_id: str) -> 'dict | None':
    import json
    _ANDROID_KEY = 'AIzaSyA8eiZmM1FaDVjRy-df2KTyQ_vz_yYM39w'
    _WEB_KEY = 'AIzaSyAO_FJ2SlqU8Q4STEhlGCjw-FfWAH9IrJg'
    _NEXT_URL = 'https://www.youtube.com/youtubei/v1/next'
    _CLIENT_CONTEXTS = [('WEB', _WEB_KEY, '2.20240807.01.00', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'), ('ANDROID', _ANDROID_KEY, '19.29.37', 'com.google.android.youtube/19.29.37 (Linux; U; Android 11) gzip'), ('IOS', _ANDROID_KEY, '19.29.1', 'com.google.ios.youtube/19.29.1 (iPhone16,2; U; CPU iOS 17_5_1 like Mac OS X;)')]
    data = None
    for client_name, api_key, client_ver, ua in _CLIENT_CONTEXTS:
        url = f'{_NEXT_URL}?key={api_key}&prettyPrint=false'
        client_ctx = {'clientName': client_name, 'clientVersion': client_ver, 'hl': 'en', 'gl': 'US', 'utcOffsetMinutes': 0}
        if client_name == 'ANDROID':
            client_ctx['androidSdkVersion'] = 30
        elif client_name == 'IOS':
            client_ctx['deviceMake'] = 'Apple'
            client_ctx['deviceModel'] = 'iPhone16,2'
        payload = json.dumps({'context': {'client': client_ctx}, 'videoId': video_id}).encode('utf-8')
        try:
            client = get_http_sync_client()
            resp = client.post(url, content=payload, headers={'Content-Type': 'application/json', 'User-Agent': ua}, timeout=6.0)
            if resp.status_code != 200:
                LOGGER.warning('InnerTube %s next HTTP %s for %s', client_name, resp.status_code, video_id)
                continue
            data = json.loads(resp.text)
        except Exception as exc:
            LOGGER.warning('InnerTube %s next error: %s', client_name, exc)
            continue
        if data:
            break
    if not data:
        return None

    def _walk(node, out):
        if isinstance(node, dict):
            renderer = node.get('compactVideoRenderer') or node.get('videoRenderer')
            if renderer and renderer.get('videoId'):
                out.append(renderer)
            for v in node.values():
                _walk(v, out)
        elif isinstance(node, list):
            for item in node:
                _walk(item, out)
    renderers: list = []
    _walk(data, renderers)
    for r in renderers:
        if r.get('videoId') == video_id:
            title = r.get('title', {}).get('simpleText') or r.get('title', {}).get('runs', [{}])[0].get('text') or 'Unknown'
            length_text = r.get('lengthText', {}).get('simpleText', '') or (r.get('lengthText', {}).get('runs', [{}])[0].get('text', '') if r.get('lengthText') else '')
            duration = 0
            if length_text:
                parts = [int(p) for p in length_text.split(':') if p.isdigit()]
                if len(parts) == 2:
                    duration = parts[0] * 60 + parts[1]
                elif len(parts) == 3:
                    duration = parts[0] * 3600 + parts[1] * 60 + parts[2]
            thumbs = r.get('thumbnail', {}).get('thumbnails') or []
            thumb = thumbs[-1].get('url', '') if thumbs else f'https://i.ytimg.com/vi/{video_id}/hqdefault.jpg'
            return {'id': video_id, 'title': title, 'duration': duration, 'webpage_url': f'https://www.youtube.com/watch?v={video_id}', 'thumbnail': thumb, 'uploader': 'Unknown'}
    return {'id': video_id, 'title': 'Unknown', 'duration': 0, 'webpage_url': f'https://www.youtube.com/watch?v={video_id}', 'thumbnail': f'https://i.ytimg.com/vi/{video_id}/hqdefault.jpg', 'uploader': 'Unknown'}

async def get_video_info(url_or_query: str) -> dict | None:
    cache_key = url_or_query.strip().lower()
    if is_valid_video_id(url_or_query.strip()):
        cached = _meta_cache_get(f'id:{url_or_query.strip()}')
        if cached:
            _meta_cache_put(cache_key, cached)
            return cached
        minimal = _normalize_info({'id': url_or_query.strip(), 'title': 'YouTube track', 'duration': 0, 'webpage_url': f'https://www.youtube.com/watch?v={url_or_query.strip()}', 'thumbnail': f'https://i.ytimg.com/vi/{url_or_query.strip()}/hqdefault.jpg', 'uploader': 'YouTube'})
        if minimal:
            _meta_cache_put(cache_key, minimal)
            _meta_cache_put(f'id:{url_or_query.strip()}', minimal)
            return minimal
    raw_task = asyncio.ensure_future(_get_video_info_once(url_or_query))
    try:
        result = await asyncio.wait_for(asyncio.shield(raw_task), timeout=_FAST_TIMEOUT)
        if result:
            return result
    except asyncio.TimeoutError:
        pass
    except Exception:
        pass
    from melody.core.query_fix import is_url, query_variants
    variants: list[str] = []
    if not is_url(url_or_query):
        try:
            variants = await query_variants(url_or_query)
        except Exception:
            variants = []
    if variants:
        LOGGER.info('🪄 raw query slow/empty — racing corrected variants: %r', variants)
    correction_tasks = [asyncio.ensure_future(_get_video_info_once(v, _quiet=True)) for v in variants]
    result = None
    pending = {raw_task, *correction_tasks}
    try:
        while pending:
            done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED, timeout=8.0)
            if not done:
                break
            for task in done:
                try:
                    r = task.result()
                except Exception:
                    r = None
                if r:
                    result = r
            if result:
                break
    except Exception:
        pass
    for p in pending:
        p.cancel()
    if result:
        _meta_cache_put(cache_key, result)
        return result
    LOGGER.info('No playable search result for %r (variants=%r)', url_or_query[:80], variants)
    return None

async def _get_video_info_once(url_or_query: str, _quiet: bool=False) -> dict | None:
    cache_key = url_or_query.strip().lower()
    cached = _meta_cache_get(cache_key)
    if cached:
        LOGGER.debug('⚡ get_video_info cache HIT for %s', cache_key[:50])
        return cached
    import re
    is_url = bool(re.match('https?://', url_or_query))
    vid_id = _extract_video_id(url_or_query) if is_url else url_or_query.strip() if re.fullmatch('[A-Za-z0-9_-]{11}', url_or_query.strip()) else None
    if vid_id:
        cached = _meta_cache_get(f'id:{vid_id}')
        if cached:
            LOGGER.debug('⚡ get_video_info cache HIT by video_id %s', vid_id)
            _meta_cache_put(cache_key, cached)
            return cached
    if is_url:
        query = f'ytsearch1:{url_or_query}' if not vid_id else f'ytsearch1:https://www.youtube.com/watch?v={vid_id}'
    else:
        query = f'ytsearch1:{url_or_query}'

    def _ytdlp_info():
        opts = {**_ydl_opts(), 'extract_flat': 'in_playlist', 'default_search': 'ytsearch1', 'noplaylist': False, 'socket_timeout': 5}
        with _locked_ytdl(opts) as ydl:
            info = ydl.extract_info(query, download=False)
            if info and 'entries' in info and info['entries']:
                return info['entries'][0]
            return info
    if vid_id:

        def innertube_fn():
            info = _innertube_player_sync(vid_id)
            if info and info.get('title') and (info.get('title') != 'Unknown'):
                return info
            return _innertube_next_sync(vid_id) or info
    else:
        innertube_fn = lambda: _innertube_search_sync(url_or_query)
    loop = asyncio.get_running_loop()
    fast_jobs: dict = {}
    if ytapi_enabled():
        api_fn = (lambda: _ytapi_details_sync(vid_id)) if vid_id else lambda: _ytapi_search_sync(url_or_query, 1)
        fast_jobs['ytapi'] = asyncio.ensure_future(loop.run_in_executor(YTDL_POOL, api_fn))
    if vid_id:
        fast_jobs['oembed'] = asyncio.ensure_future(loop.run_in_executor(YTDL_POOL, _youtube_oembed_sync, vid_id))
    fast_jobs['innertube'] = asyncio.ensure_future(loop.run_in_executor(YTDL_POOL, innertube_fn))
    ytdlp_future = None
    result = None
    pending = set(fast_jobs.values())
    try:
        while pending:
            done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED, timeout=_FAST_TIMEOUT)
            if not done:
                break
            for task in done:
                try:
                    info = task.result()
                    normalized = _normalize_info(info if isinstance(info, dict) else None)
                    if normalized:
                        if result is None or (result.get('title') == 'Unknown' and normalized.get('title') != 'Unknown'):
                            result = normalized
                except Exception:
                    pass
            if result:
                break
    except Exception:
        pass
    for p in pending:
        p.cancel()
    if result:
        if ytdlp_future is not None:
            ytdlp_future.cancel()
        LOGGER.info('⚡ Fast-path (API/InnerTube race) resolved %s', result['id'])
        _meta_cache_put(cache_key, result)
        return result
    ytdlp_future = asyncio.ensure_future(loop.run_in_executor(YTDL_POOL, _ytdlp_info))
    pending = {ytdlp_future}
    try:
        while pending:
            done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED, timeout=8.0)
            if not done:
                break
            for task in done:
                try:
                    info = task.result()
                    normalized = _normalize_info(info)
                    if normalized:
                        result = normalized
                except Exception:
                    pass
            if result:
                break
    except Exception:
        pass
    for p in pending:
        p.cancel()
    if result:
        _meta_cache_put(cache_key, result)
        return result
    LOGGER.info('API v3 + InnerTube + yt-dlp all failed — trying Invidious | %s', url_or_query[:50])
    try:
        info = await loop.run_in_executor(YTDL_POOL, _invidious_search_sync, url_or_query)
        result = _normalize_info(info)
        if result:
            _meta_cache_put(cache_key, result)
            return result
    except Exception:
        pass
    if not _quiet:
        LOGGER.info('no result for %r — will retry with corrected spelling', query[:60])
    return None

def _cache_tag(audio_only: bool) -> str:
    return 'a' if audio_only else 'v'

def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default

def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default

def _env_flag(name: str, default: bool=True) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {'0', 'false', 'no', 'off'}
_EARLY_HANDOFF_BYTES = _env_int('EARLY_HANDOFF_BYTES', 16000)
_EARLY_HANDOFF_RATIO = _env_float('EARLY_HANDOFF_RATIO', 0.001)
_EARLY_HANDOFF_LARGE_FILE_BYTES = _env_int('EARLY_HANDOFF_LARGE_FILE_BYTES', 10000000)
_EARLY_HANDOFF_LARGE_FILE_PREFIX = _env_int('EARLY_HANDOFF_LARGE_FILE_PREFIX', 32000)
_EARLY_HANDOFF_ENABLED = _env_flag('EARLY_HANDOFF', True)
_EARLY_AUDIO_HANDOFF_ENABLED = _env_flag('EARLY_AUDIO_HANDOFF', True)
_EARLY_AUDIO_STREAMABLE_EXTS = {'webm', 'ogg', 'oga', 'opus', 'mp3', 'flac', 'wav', 'mka', 'm4a', 'ts', 'mpegts', 'm2ts', 'aac'}
_EARLY_AUDIO_MOOV_CHECK_EXTS = {'m4a'}

def _early_handoff_allowed(audio_only: bool) -> bool:
    return bool(_EARLY_HANDOFF_ENABLED or (audio_only and _EARLY_AUDIO_HANDOFF_ENABLED))

def _m4a_moov_at_head(path: str) -> bool:
    try:
        with open(path, 'rb') as fh:
            head = fh.read(262144)
    except OSError:
        return False
    return b'moov' in head

def _mpegts_at_head(path: str) -> bool:
    try:
        with open(path, 'rb') as fh:
            head = fh.read(188 * 4)
    except OSError:
        return False
    if len(head) < 188 * 2:
        return False
    return all((head[i] == 71 for i in range(0, len(head) - 187, 188)))

def _early_audio_file_is_safe(path: str) -> bool:
    if not _early_audio_path_is_safe(path):
        return _mpegts_at_head(path)
    name = os.path.basename(path or '').lower()
    name = re.sub('\\.part-frag\\d+$', '', name)
    name = re.sub('\\.frag\\d+$', '', name)
    for suffix in ('.part', '.ytdl', '.temp'):
        while name.endswith(suffix):
            name = name[:-len(suffix)]
    ext = name.rsplit('.', 1)[-1] if '.' in name else ''
    if ext in _EARLY_AUDIO_MOOV_CHECK_EXTS:
        return _m4a_moov_at_head(path)
    return True

def _early_handoff_ready(downloaded: int, total: int=0) -> bool:
    downloaded = max(0, int(downloaded or 0))
    total = max(0, int(total or 0))
    if total >= _EARLY_HANDOFF_LARGE_FILE_BYTES:
        required = _EARLY_HANDOFF_LARGE_FILE_PREFIX
    elif total:
        required = min(_EARLY_HANDOFF_BYTES, max(32000, int(total * _EARLY_HANDOFF_RATIO)))
    else:
        required = _EARLY_HANDOFF_BYTES
    return downloaded >= required

def _early_audio_path_is_safe(path: str) -> bool:
    name = os.path.basename(path or '').lower()
    name = re.sub('\\.part-frag\\d+$', '', name)
    name = re.sub('\\.frag\\d+$', '', name)
    for suffix in ('.part', '.ytdl', '.temp'):
        while name.endswith(suffix):
            name = name[:-len(suffix)]
    ext = name.rsplit('.', 1)[-1] if '.' in name else ''
    return ext in _EARLY_AUDIO_STREAMABLE_EXTS

def _complete_cache_files(video_id: str, tag: str) -> list:
    out = []
    for f in glob.glob(f'/tmp/melody_{video_id}_{tag}.*'):
        if f.endswith('.part') or f.endswith('.ytdl') or f.endswith('.temp') or f.endswith('.tmp') or f.endswith('.dl') or f.endswith('.early') or ('.part-' in f) or ('.dl.' in f):
            continue
        try:
            if os.path.getsize(f) <= 0:
                continue
        except OSError:
            continue
        out.append(f)
    return out

def cached_file_path(video_id: str, audio_only: bool=True) -> 'str | None':
    try:
        found = _complete_cache_files(video_id, _cache_tag(audio_only))
    except Exception:
        return None
    return found[0] if found else None

def on_cloud_host() -> bool:
    return _ON_CLOUD_HOST
_EARLY_HANDOFF_TIMEOUT = _env_float('EARLY_HANDOFF_TIMEOUT', 2.0)
_downloads_in_progress: dict = {}

def is_download_in_progress(video_id: str, audio_only: bool=True) -> bool:
    return bool(_downloads_in_progress.get(f'{video_id}:{_cache_tag(audio_only)}'))
_DOWNLOAD_LADDER: tuple = ({}, {'_client': ['mweb']}, {'concurrent_fragment_downloads': 1}, {'_client': ['android_vr', 'web_safari']}, {'_client': ['ios', 'ios_music', 'mweb'], 'concurrent_fragment_downloads': 1}, {'_format': 'bestaudio[ext=webm]/bestaudio[ext=opus]/bestaudio[ext=ogg]/bestaudio[ext=m4a]/bestaudio*[vcodec=none]/bestaudio/' + _SMALL_MUXED_SELECTOR, '_client': ['tv', 'web']}, {'_format': 'bestaudio[ext=webm]/bestaudio[ext=opus]/bestaudio[ext=ogg]/bestaudio[ext=m4a]/bestaudio*[vcodec=none]/bestaudio/' + _SMALL_MUXED_SELECTOR, '_no_merge': True}, {'_client': ['tv_simply', 'tv'], '_format': 'bestaudio[ext=webm][protocol^=http]/bestaudio[ext=opus][protocol^=http]/bestaudio[ext=ogg][protocol^=http]/bestaudio[ext=m4a][protocol*=dash]/bestaudio[format_id=251]/bestaudio[format_id=250]/bestaudio[format_id=249]/bestaudio[format_id=140]/bestaudio[protocol^=http]/bestaudio*[vcodec=none]/bestaudio/' + _SMALL_MUXED_SELECTOR, 'concurrent_fragment_downloads': 1, '_no_merge': True}, {'_client': ['web_safari', 'web_embedded'], '_format': 'bestaudio[ext=webm][protocol^=http]/bestaudio[ext=opus][protocol^=http]/bestaudio[ext=ogg][protocol^=http]/bestaudio[ext=m4a][protocol*=dash]/bestaudio[format_id=251]/bestaudio[format_id=140]/bestaudio[protocol^=http]/bestaudio*[vcodec=none]/bestaudio/' + _SMALL_MUXED_SELECTOR, '_no_merge': True})

def _apply_ladder_step(opts: dict, step: dict, audio_only: bool) -> dict:
    out = dict(opts)
    for key, value in step.items():
        if key.startswith('_'):
            continue
        out[key] = value
    clients = step.get('_client')
    if clients:
        extractor_args = {k: dict(v) for k, v in (out.get('extractor_args') or {}).items()}
        youtube = dict(extractor_args.get('youtube') or {})
        youtube['player_client'] = list(clients)
        extractor_args['youtube'] = youtube
        out['extractor_args'] = extractor_args
        client = str(clients[0]).lower()
        if client in {'ios', 'ios_music'}:
            ua = 'com.google.ios.youtube/20.10.4 (iPhone16,2; U; CPU iOS 18_3_2 like Mac OS X)'
        elif client in {'android', 'android_music', 'android_vr'}:
            ua = 'com.google.android.youtube/20.10.38 (Linux; U; Android 14)'
        elif client in {'tv', 'tv_simply'}:
            ua = 'Mozilla/5.0 (SMART-TV; LINUX; Tizen 6.5) AppleWebKit/537.36 TV Safari/537.36'
        else:
            ua = 'Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Chrome/125.0 Mobile Safari/537.36'
        headers = dict(out.get('http_headers') or {})
        headers['User-Agent'] = ua
        out['http_headers'] = headers
        if client not in {'web', 'web_safari', 'mweb', 'web_embedded', 'tv', 'tv_simply'}:
            out.pop('cookiefile', None)
    fmt = step.get('_format')
    if fmt:
        out['format'] = fmt if audio_only else 'best[vcodec!=none][acodec!=none]/best[height<=480]/best'
    if step.get('_no_merge'):
        out['postprocessors'] = []
        out.pop('merge_output_format', None)
    return out

def _purge_partial_outputs(outtmpl) -> None:
    if isinstance(outtmpl, dict):
        outtmpl = outtmpl.get('default')
    if not isinstance(outtmpl, str):
        return
    base = outtmpl.split('.%(ext)s')[0]
    for leftover in glob.glob(f'{base}.*'):
        if re.search('\\.f\\d+\\.', leftover) or leftover.endswith(('.part', '.ytdl')):
            try:
                os.unlink(leftover)
            except OSError:
                pass
_PERMANENT_DOWNLOAD_MARKERS = ('this video is unavailable', 'video unavailable', "this content isn't available", 'this content isn’t available', 'video has been removed', 'private video', 'no video formats found', 'sign in to confirm your age', 'members-only content', 'not available in your country', 'this video is drm protected', 'drm protected', 'only images are available', 'only storyboards are available')

def _is_permanent_download_error(exc: BaseException) -> bool:
    current: BaseException | None = exc
    for _ in range(8):
        if current is None:
            break
        text = str(current).lower()
        if 'mobile browser' in text or 'youtube app' in text:
            return False
        if any((marker in text for marker in _PERMANENT_DOWNLOAD_MARKERS)):
            return True
        current = current.__cause__ or current.__context__
    return False
_BOTWALL_MARKERS = ('sign in to confirm', "confirm you're not a bot", 'confirm you’re not a bot', 'login_required', 'please sign in', 'use --cookies')

def _is_botwall_error(exc: 'BaseException | None') -> bool:
    current: BaseException | None = exc
    for _ in range(8):
        if current is None:
            break
        text = str(current).lower()
        if any((marker in text for marker in _BOTWALL_MARKERS)):
            return True
        current = current.__cause__ or current.__context__
    return False
_COOKIELESS_CLIENTS = ['mweb', 'android_vr', 'tv_simply', 'web_embedded']

def _strip_cookies_from_opts(opts: dict) -> dict:
    out = dict(opts)
    out.pop('cookiefile', None)
    extractor_args = {k: dict(v) for k, v in (out.get('extractor_args') or {}).items()}
    youtube = dict(extractor_args.get('youtube') or {})
    youtube['player_client'] = list(_COOKIELESS_CLIENTS)
    extractor_args['youtube'] = youtube
    out['extractor_args'] = extractor_args
    return out

def _extract_with_retries(url: str, base_opts: dict, audio_only: bool):
    last_exc: BaseException | None = None
    deadline = _time_mod.monotonic() + _env_float('DOWNLOAD_DEADLINE', 35.0)
    botwall_hits = 0
    botwall_budget = max(1, _env_int('YT_BOTWALL_MAX_RUNGS', len(_DOWNLOAD_LADDER)))
    for index, step in enumerate(_DOWNLOAD_LADDER):
        if index and _time_mod.monotonic() > deadline:
            LOGGER.warning('download ladder deadline hit for %s after %d attempts', url, index)
            break
        if cookies_distrusted():
            base_opts = _strip_cookies_from_opts(base_opts)
        else:
            # Rotate on every retry rung as well as every new song. If one
            # account/session is rejected, the next rung gets another cookie.
            fresh_cookie = cookiefile_for_run()
            if fresh_cookie:
                base_opts = dict(base_opts)
                base_opts['cookiefile'] = fresh_cookie
        opts = _apply_ladder_step(base_opts, step, audio_only)
        try:
            with _locked_ytdl(opts) as ydl:
                info = ydl.extract_info(url, download=True)
                path = ydl.prepare_filename(info)
                requested = info.get('requested_downloads') or []
                final_path = info.get('filepath') or info.get('_filename')
                if requested and isinstance(requested[0], dict):
                    final_path = requested[0].get('filepath') or final_path
                if final_path and os.path.exists(final_path):
                    path = final_path
                if index or info.get('vcodec') not in (None, 'none'):
                    LOGGER.info('#download rung %d picked format_id=%s ext=%s vcodec=%s acodec=%s for %s', index + 1, info.get('format_id'), info.get('ext'), info.get('vcodec'), info.get('acodec'), url)
                if os.path.exists(path) and os.path.getsize(path) < 1024:
                    raise RuntimeError(f'downloaded file is empty ({os.path.basename(path)})')
                return (info, path)
        except FileNotFoundError as exc:
            outtmpl = opts.get('outtmpl')
            if isinstance(outtmpl, dict):
                outtmpl = outtmpl.get('default')
            base = outtmpl.split('.%(ext)s')[0] if isinstance(outtmpl, str) else ''
            completed = [p for p in glob.glob(f'{base}.*') if not p.endswith(('.part', '.ytdl', '.temp', '.tmp')) and '.temp.' not in p and os.path.isfile(p) and (os.path.getsize(p) >= 1024)]
            if completed:
                LOGGER.info('Recovered completed download after temp rename race: %s', completed[0])
                return ({}, completed[0])
            last_exc = exc
            _purge_partial_outputs(opts.get('outtmpl'))
            LOGGER.warning('download attempt %d/%d hit a missing temp file for %s; trying fallback', index + 1, len(_DOWNLOAD_LADDER), url)
            continue
        except _DownloadCancelled:
            raise
        except Exception as exc:
            last_exc = exc
            if _is_botwall_error(exc):
                botwall_hits += 1
                if botwall_hits >= 3:
                    note_cookie_rejected('download rung %d' % (index + 1))
                if botwall_hits >= botwall_budget:
                    _purge_partial_outputs(opts.get('outtmpl'))
                    LOGGER.info('download blocked by YouTube bot check on %d rungs for %s — aborting ladder early so the direct rescue can run', botwall_hits, url)
                    raise
            if _is_permanent_download_error(exc):
                _purge_partial_outputs(opts.get('outtmpl'))
                LOGGER.info('download permanently unavailable for %s — skipping remaining fallback clients: %s', url, redact_sensitive_text(exc))
                raise
            _purge_partial_outputs(opts.get('outtmpl'))
            LOGGER.warning('download attempt %d/%d failed for %s: %s', index + 1, len(_DOWNLOAD_LADDER), url, redact_sensitive_text(exc))
    raise last_exc if last_exc else RuntimeError(f'download failed for {url}')

def _download_audio_sync(video_id: str, audio_only: bool=True, early_event: 'threading.Event | None'=None, early_holder: 'dict | None'=None, cancel_event: 'threading.Event | None'=None) -> str:
    url = video_id if is_direct_url(video_id) else f'https://www.youtube.com/watch?v={video_id}'
    tag = _cache_tag(audio_only)
    import uuid as _uuid
    attempt_dir = os.path.join(STAGING_DIR, f'yt_{video_id}_{tag}_{_uuid.uuid4().hex[:8]}')
    os.makedirs(attempt_dir, exist_ok=True)
    outtmpl = os.path.join(attempt_dir, 'file.%(ext)s')
    _downloads_in_progress[f'{video_id}:{tag}'] = True

    def _cancel_hook(_d):
        if cancel_event is not None and cancel_event.is_set():
            raise _DownloadCancelled('superseded by a newer playback request')

    def _hook(d):
        if early_event is None or early_event.is_set():
            return
        if d.get('status') != 'downloading':
            return
        fp = d.get('filename') or d.get('tmpfilename')
        if fp and (not os.path.exists(fp)):
            alt = d.get('tmpfilename') or f'{fp}.part'
            if alt and os.path.exists(alt):
                fp = alt
        if not audio_only or not _early_audio_file_is_safe(fp or ''):
            return
        downloaded = d.get('downloaded_bytes') or 0
        if fp:
            try:
                downloaded = max(downloaded, os.path.getsize(fp))
            except OSError:
                pass
        total = d.get('total_bytes') or d.get('total_bytes_estimate') or 0
        ready = _early_handoff_ready(downloaded, total)
        if fp and ready and os.path.exists(fp):
            if early_holder is not None:
                stable = f'/tmp/melody_{video_id}_{tag}.early'
                try:
                    link_tmp = f'{stable}.{os.getpid()}.tmp'
                    if os.path.lexists(link_tmp):
                        os.unlink(link_tmp)
                    os.symlink(fp, link_tmp)
                    os.replace(link_tmp, stable)
                    early_holder['early_path'] = stable
                except OSError:
                    early_holder['early_path'] = fp
            LOGGER.info('⚡ #download early audio handoff %s variant=%s bytes=%d path=%s', video_id, tag, downloaded, os.path.basename((early_holder or {}).get('early_path') or fp))
            early_event.set()
    opts = {**_ydl_opts(audio_only=audio_only), 'outtmpl': outtmpl, 'extract_flat': False}
    opts['progress_hooks'] = [*opts.get('progress_hooks', []), _cancel_hook]
    if early_event is not None:
        opts['progress_hooks'].append(_hook)
    _watch_stop = threading.Event()

    def _watch_staging():
        while not _watch_stop.wait(0.1):
            if early_event is None or early_event.is_set():
                return
            try:
                candidates = glob.glob(os.path.join(attempt_dir, '*'))
            except OSError:
                continue
            for cand in candidates:
                if not audio_only or not _early_audio_file_is_safe(cand):
                    continue
                try:
                    size = os.path.getsize(cand)
                except OSError:
                    continue
                if not _early_handoff_ready(size):
                    continue
                if early_holder is not None:
                    stable = f'/tmp/melody_{video_id}_{tag}.early'
                    try:
                        link_tmp = f'{stable}.{os.getpid()}.tmp'
                        if os.path.lexists(link_tmp):
                            os.unlink(link_tmp)
                        os.symlink(cand, link_tmp)
                        os.replace(link_tmp, stable)
                        early_holder['early_path'] = stable
                    except OSError:
                        early_holder['early_path'] = cand
                LOGGER.info('⚡ #download early audio handoff (watcher) %s variant=%s bytes=%d path=%s', video_id, tag, size, os.path.basename(cand))
                early_event.set()
                return
    _watcher = None
    if early_event is not None and audio_only:
        _watcher = threading.Thread(target=_watch_staging, daemon=True, name=f'melody-early-watch-{video_id[:8]}')
        _watcher.start()
    try:
        try:
            info, filepath = _extract_with_retries(url, opts, audio_only)
        except FileNotFoundError:
            found = _complete_cache_files(video_id, tag)
            if found:
                filepath = found[0]
            else:
                raise
        if not os.path.exists(filepath):
            found = _complete_cache_files(video_id, tag)
            if found:
                filepath = found[0]
            else:
                raise FileNotFoundError(f'Downloaded file not found for video_id={video_id}')
        if filepath.startswith(attempt_dir + os.sep):
            ext = filepath.rsplit('.', 1)[-1] if '.' in filepath else 'webm'
            canonical = f'/tmp/melody_{video_id}_{tag}.{ext}'
            try:
                existing = _complete_cache_files(video_id, tag)
                if existing:
                    filepath = existing[0]
                else:
                    os.replace(filepath, canonical)
                    filepath = canonical
                    early_path = (early_holder or {}).get('early_path')
                    if early_path and early_path.startswith('/tmp/melody_'):
                        try:
                            link_tmp = f'{early_path}.{os.getpid()}.tmp'
                            if os.path.lexists(link_tmp):
                                os.unlink(link_tmp)
                            os.symlink(canonical, link_tmp)
                            os.replace(link_tmp, early_path)
                        except OSError:
                            pass
            except OSError:
                pass
        if early_holder is not None:
            early_holder['final_path'] = filepath
        try:
            LOGGER.info('#download file %s variant=%s ext=%s size=%d early=%s', video_id, tag, filepath.rsplit('.', 1)[-1], os.path.getsize(filepath), bool(early_event is not None and early_event.is_set()))
        except Exception:
            pass
        return filepath
    finally:
        try:
            _watch_stop.set()
        except Exception:
            pass
        try:
            import shutil as _shutil
            _shutil.rmtree(attempt_dir, ignore_errors=True)
        except Exception:
            pass
        _downloads_in_progress.pop(f'{video_id}:{tag}', None)
        if early_event is not None:
            early_event.set()

def _replied_media_object(message):
    if not message:
        return None
    if message.audio:
        return message.audio
    if message.video:
        return message.video
    if message.voice:
        return message.voice
    if message.video_note:
        return message.video_note
    doc = message.document
    if doc:
        mime_kind = (getattr(doc, 'mime_type', None) or '').split('/')[0].lower()
        filename = (getattr(doc, 'file_name', None) or '').lower()
        ext = filename.rsplit('.', 1)[-1] if '.' in filename else ''
        if mime_kind in ('audio', 'video') or ext in {'mp3', 'mp4', 'm4a', 'aac', 'flac', 'wav', 'ogg', 'oga', 'opus', 'webm', 'mkv', 'mov', 'avi', 'm4v'}:
            return doc
    return None

def has_playable_media(message) -> bool:
    return _replied_media_object(message) is not None

def tagged_media_limit_reason(message, video: bool=False) -> str | None:
    media = _replied_media_object(message)
    if not media:
        return None
    duration = int(getattr(media, 'duration', 0) or 0)
    if duration > _TAGGED_MAX_DURATION:
        return f'❌ Tagged media {duration // 3600}h se zyada lamba hai. Shorter audio/video ya direct `/live` stream use karo.'
    return None
_tg_media_locks: 'dict[str, asyncio.Lock]' = {}
TG_MEDIA_SOURCES: 'dict[str, tuple[int, int]]' = {}
_TG_STATE_MAX = 256

def _bound_dict(store: dict, limit: int) -> None:
    while len(store) > limit:
        try:
            store.pop(next(iter(store)))
        except (StopIteration, KeyError):
            break

def _tg_media_lock(key: str) -> 'asyncio.Lock':
    lock = _tg_media_locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        for stale in [k for k, v in list(_tg_media_locks.items()) if not v.locked()][:max(0, len(_tg_media_locks) - _TG_STATE_MAX)]:
            _tg_media_locks.pop(stale, None)
        _tg_media_locks[key] = lock
    return lock

def _synthetic_media_id(chat_id: int, message_id: int) -> str:
    return f"tg{str(chat_id).replace('-', 'n')}_{message_id}"

def is_direct_url(video_id: 'str | None') -> bool:
    return bool(video_id) and str(video_id).lower().startswith(('http://', 'https://'))

def is_tg_media_id(video_id: str) -> bool:
    return bool(re.fullmatch('tg(n?)(\\d+)_(\\d+)', video_id or ''))

def _parse_synthetic_media_id(video_id: str) -> 'tuple[int, int] | None':
    match = re.fullmatch('tg(n?)(\\d+)_(\\d+)', video_id or '')
    if not match:
        return None
    sign, chat_digits, message_id = match.groups()
    chat_id = int(chat_digits) * (-1 if sign else 1)
    return (chat_id, int(message_id))
STAGING_DIR = '/tmp/melody_dl'

def _sweep_staging_dir(max_age: float=1800.0) -> None:
    now = _time_mod.time()
    for f in glob.glob(os.path.join(STAGING_DIR, '*')):
        try:
            if now - os.path.getmtime(f) < max_age:
                continue
            os.unlink(f)
        except OSError:
            pass
_TG_STREAMABLE_EXTS = {'mp3', 'ogg', 'oga', 'opus', 'webm', 'mkv', 'flac', 'wav'}
_TAGGED_PROXY_THRESHOLD_MB = max(32, _env_int('TAGGED_PROXY_THRESHOLD_MB', 256))
_TAGGED_PROXY_THRESHOLD_BYTES = _TAGGED_PROXY_THRESHOLD_MB * 1024 * 1024
_TAGGED_MAX_DURATION = max(600, _env_int('TAGGED_MAX_DURATION_SECONDS', 6 * 3600))
_TG_EARLY_PATHS: 'dict[str, str]' = {}
_TG_INFLIGHT: 'dict[str, asyncio.Task]' = {}
_TG_EARLY_MIN_BYTES = _env_int('TG_EARLY_BYTES', 1500000)
_TG_EARLY_RATIO = 0.3

def tg_early_path(video_id: str, audio_only: bool=True) -> 'str | None':
    path = _TG_EARLY_PATHS.get(f'{video_id}:{_cache_tag(audio_only)}')
    if path and os.path.exists(path) and (os.path.getsize(path) > 0):
        return path
    return None

async def _download_tg_media_file(client, message, dest: str, early_cb=None) -> 'str | None':
    staging_dir = STAGING_DIR
    try:
        os.makedirs(staging_dir, exist_ok=True)
    except OSError:
        staging_dir = '/tmp'
    _sweep_staging_dir()
    unique = os.path.join(staging_dir, f'{os.path.basename(dest)}.{os.getpid()}.{int(_time_mod.time() * 1000)}.dl')
    got = None
    last_exc = None
    attempted: list = []
    try:
        for attempt in range(2):
            attempted.append(unique)
            try:
                if early_cb is not None:
                    _seen = {'fired': False}
                    _stem = unique

                    def _progress(current, total, _stem=_stem, _seen=_seen):
                        if _seen['fired']:
                            return
                        need = max(_TG_EARLY_MIN_BYTES, int((total or 0) * _TG_EARLY_RATIO))
                        if current < need:
                            return
                        candidates = [f for f in glob.glob(f'{_stem}*') if os.path.isfile(f) and os.path.getsize(f) > 0]
                        if not candidates:
                            return
                        _seen['fired'] = True
                        early_cb(max(candidates, key=os.path.getsize))
                    got = await client.download_media(message, file_name=unique, progress=_progress)
                else:
                    got = await client.download_media(message, file_name=unique)
                break
            except FileNotFoundError as exc:
                if os.path.exists(dest) and os.path.getsize(dest) > 0:
                    return dest
                last_exc = exc
                unique = f'{unique}.r{attempt + 1}'
                await asyncio.sleep(0.5)
    finally:
        for stale in attempted:
            for leftover in glob.glob(f'{stale}*'):
                if got and os.path.abspath(leftover) == os.path.abspath(got):
                    continue
                try:
                    os.unlink(leftover)
                except OSError:
                    pass
    if got is None:
        if os.path.exists(dest) and os.path.getsize(dest) > 0:
            return dest
        raise last_exc if last_exc else FileNotFoundError(dest)
    if not got or not os.path.exists(got):
        return dest if os.path.exists(dest) and os.path.getsize(dest) > 0 else None
    try:
        os.replace(got, dest)
    except OSError:
        return got
    return dest

def _tg_media_info(media, message, vid: str, stream_url: str='') -> dict:
    title = getattr(media, 'file_name', None) or getattr(media, 'title', None) or 'Tagged Media'
    uploader = getattr(media, 'performer', None) or (message.from_user.first_name if message.from_user else None) or 'Telegram'
    return {'id': vid, 'title': title, 'duration': int(getattr(media, 'duration', 0) or 0), 'url': '', 'stream_url': stream_url or '', 'thumbnail': '', 'uploader': uploader}

async def download_replied_media(client, message, video: bool=False) -> 'dict | None':
    media = _replied_media_object(message)
    if not media:
        return None
    if tagged_media_limit_reason(message, video=video):
        LOGGER.info('tagged media rejected before download: size/duration limit')
        return None
    vid = _synthetic_media_id(message.chat.id, message.id)
    tag = _cache_tag(not video)
    TG_MEDIA_SOURCES[vid] = (message.chat.id, message.id)
    _bound_dict(TG_MEDIA_SOURCES, _TG_STATE_MAX)
    key = f'{vid}:{tag}'
    async with _tg_media_lock(key):
        cached = _complete_cache_files(vid, tag)
        if not cached:
            file_size = int(getattr(media, 'file_size', 0) or 0)
            file_name = getattr(media, 'file_name', None) or ''
            if file_size >= _TAGGED_PROXY_THRESHOLD_BYTES:
                try:
                    from utils.tg_media_proxy import create_media_proxy
                    proxy_url = await create_media_proxy(client, message, size=file_size, filename=file_name or 'media')
                    LOGGER.info('tagged media using range proxy id=%s size_mb=%.0f', vid, file_size / 1048576)
                    return _tg_media_info(media, message, vid, proxy_url)
                except Exception as exc:
                    LOGGER.warning('tagged range proxy unavailable id=%s error=%s', vid, type(exc).__name__)
                    return None
            ext = file_name.rsplit('.', 1)[-1] if '.' in file_name else None
            if not ext:
                ext = 'mp4' if message.video or message.video_note else 'ogg' if message.voice else 'mp3'
            dest = f'/tmp/melody_{vid}_{tag}.{ext}'
            size = int(getattr(media, 'file_size', 0) or 0)
            can_stream_early = ext.lower() in _TG_STREAMABLE_EXTS and size > 3000000 and _EARLY_HANDOFF_ENABLED
            if can_stream_early:
                loop = asyncio.get_running_loop()
                ready = asyncio.Event()

                def _on_early(path: str):
                    _TG_EARLY_PATHS[key] = path
                    _bound_dict(_TG_EARLY_PATHS, _TG_STATE_MAX)
                    loop.call_soon_threadsafe(ready.set)

                async def _run():
                    try:
                        return await _download_tg_media_file(client, message, dest, early_cb=_on_early)
                    finally:
                        _TG_EARLY_PATHS.pop(key, None)
                        _TG_INFLIGHT.pop(key, None)
                task = asyncio.ensure_future(_run())
                _TG_INFLIGHT[key] = task
                ready_task = asyncio.ensure_future(ready.wait())
                try:
                    await asyncio.wait({task, ready_task}, return_when=asyncio.FIRST_COMPLETED, timeout=_EARLY_HANDOFF_TIMEOUT)
                finally:
                    ready_task.cancel()
                if not task.done() and _TG_EARLY_PATHS.get(key):
                    LOGGER.info('⚡ tagged media %s handed off early while downloading', vid)
                    return _tg_media_info(media, message, vid)
                try:
                    filepath = await task
                except Exception as exc:
                    filepath = None
                    found = _complete_cache_files(vid, tag)
                    if found:
                        filepath = found[0]
                    else:
                        await send_error_log(f'download_replied_media failed for message {message.id}', exc, context={'chat_id': message.chat.id if message.chat else None, 'user_id': message.from_user.id if message.from_user else None})
                        return None
                if not filepath or not os.path.exists(filepath):
                    return None
                return _tg_media_info(media, message, vid)
            try:
                filepath = await _download_tg_media_file(client, message, dest)
            except Exception as exc:
                found = _complete_cache_files(vid, tag)
                if found:
                    filepath = found[0]
                else:
                    await send_error_log(f'download_replied_media failed for message {message.id}', exc, context={'chat_id': message.chat.id if message.chat else None, 'user_id': message.from_user.id if message.from_user else None})
                    return None
            if not filepath or not os.path.exists(filepath):
                return None
    return _tg_media_info(media, message, vid)

async def download_audio(video_id: str, audio_only: bool=True, priority: int=0, owner=None, allow_early: bool=False) -> str:
    import re
    is_synthetic = bool(re.fullmatch('tg[A-Za-z0-9_]+', video_id or ''))
    if not is_synthetic and (not re.fullmatch('[A-Za-z0-9_-]{11}', video_id or '')):
        raise ValueError(f'download_audio: invalid YouTube video_id {video_id!r} (expected 11-char [A-Za-z0-9_-] ID)')
    tag = _cache_tag(audio_only)
    _download_requested_at = _time_mod.monotonic()
    if not is_synthetic:
        try:
            from utils.telegram_archive import restore_archived_file
            from melody import bot as _bot
            restored = await restore_archived_file(_bot, video_id, video=not audio_only)
            if restored and (restored.startswith(('http://127.0.0.1:', 'http://localhost:')) or (os.path.isfile(restored) and (os.path.getsize(restored) > 0))):
                LOGGER.info('#download telegram-archive-hit %s variant=%s source=%s', video_id, tag, 'proxy' if restored.startswith(('http://127.0.0.1:', 'http://localhost:')) else 'file')
                return restored
        except Exception as exc:
            LOGGER.debug('telegram archive restore skipped for %s: %s', video_id, exc)
    cached = _complete_cache_files(video_id, tag)
    if cached:
        LOGGER.info('#download cache-hit %s variant=%s wait=%.2fs', video_id, tag, _time_mod.monotonic() - _download_requested_at)
        return cached[0]
    if is_synthetic:
        early = tg_early_path(video_id, audio_only)
        if early:
            LOGGER.info('⚡ using in-progress tagged media file for %s', video_id)
            return early
    if is_synthetic:
        early = tg_early_path(video_id, audio_only)
        if early:
            LOGGER.info('⚡ using in-progress tagged media file for %s', video_id)
            return early
        inflight = _TG_INFLIGHT.get(f'{video_id}:{tag}')
        if inflight is not None and (not inflight.done()):
            try:
                done_path = await asyncio.shield(inflight)
                if done_path and os.path.exists(done_path):
                    return done_path
            except Exception as exc:
                LOGGER.warning('tagged-media in-flight download failed for %s: %s', video_id, exc)
        source = TG_MEDIA_SOURCES.get(video_id) or _parse_synthetic_media_id(video_id)
        if source:
            chat_id, message_id = source
            try:
                from melody import bot as _bot
                if _bot is not None:
                    msg = await _bot.get_messages(chat_id, message_id)
                    info = await download_replied_media(_bot, msg, video=not audio_only)
                    if info:
                        proxy_url = info.get('stream_url') or ''
                        if proxy_url.startswith(('http://127.0.0.1:', 'http://localhost:')):
                            LOGGER.info('♻️ Re-created tagged media range proxy for %s', video_id)
                            return proxy_url
                        found = _complete_cache_files(video_id, tag)
                        if found:
                            LOGGER.info('♻️ Re-downloaded tagged media for %s', video_id)
                            return found[0]
            except Exception as exc:
                LOGGER.warning('tagged-media re-download failed for %s: %s', video_id, exc)
        raise FileNotFoundError(f'download_audio: cached Telegram media for {video_id!r} not found (the tagged message file is no longer in /tmp — re-tag the message)')
    dedup_key = f'{video_id}:{tag}'
    async with _download_futures_lock:
        existing = _download_futures.get(dedup_key)
        state = _download_early_states.get(dedup_key)
        if existing is None or existing.done():
            loop = asyncio.get_running_loop()
            fut = loop.create_future()
            fut.add_done_callback(_consume_download_future)
            _download_futures[dedup_key] = fut
            state = _EarlyDownloadState() if allow_early or audio_only else None
            if state is not None:
                _download_early_states[dedup_key] = state
            existing = None
        else:
            fut = existing
    if existing is not None:
        LOGGER.debug('⚡ download_audio dedup: reusing in-flight download for %s', dedup_key)
        if allow_early and state is not None:
            ready_task = asyncio.create_task(state.ready.wait())
            future_task = asyncio.ensure_future(asyncio.shield(fut))
            future_task.add_done_callback(_consume_download_future)
            try:
                done, _ = await asyncio.wait({ready_task, future_task}, return_when=asyncio.FIRST_COMPLETED)
                if state.path and os.path.exists(state.path):
                    return state.path
            finally:
                for task in (ready_task, future_task):
                    if not task.done():
                        task.cancel()
        return await asyncio.shield(fut)
    cancel_event = threading.Event()
    _download_cancel_events[dedup_key] = (cancel_event, owner, int(priority))
    job = asyncio.create_task(_run_download_job(video_id, audio_only, tag, priority, _download_requested_at, cancel_event, fut, state), name=f'download-job:{video_id[:8]}')
    _download_jobs.add(job)
    job.add_done_callback(_download_jobs.discard)
    job.add_done_callback(_consume_download_future)
    if allow_early and state is not None:
        ready_task = asyncio.create_task(state.ready.wait())
        future_task = asyncio.ensure_future(asyncio.shield(fut))
        future_task.add_done_callback(_consume_download_future)
        try:
            done, _ = await asyncio.wait({ready_task, future_task}, return_when=asyncio.FIRST_COMPLETED)
            if state.path and os.path.exists(state.path):
                return state.path
        finally:
            for task in (ready_task, future_task):
                if not task.done():
                    task.cancel()
    return await asyncio.shield(fut)

async def wait_for_download(video_id: str, audio_only: bool=True, timeout: float | None=None) -> str | None:
    key = f'{video_id}:{_cache_tag(audio_only)}'
    async with _download_futures_lock:
        future = _download_futures.get(key)
    if future is None:
        found = _complete_cache_files(video_id, _cache_tag(audio_only))
        return found[0] if found else None
    waiter = asyncio.shield(future)
    if timeout is not None:
        return await asyncio.wait_for(waiter, timeout=max(0.1, timeout))
    return await waiter

async def wait_for_early_download(video_id: str, audio_only: bool=True, timeout: float | None=None) -> str | None:
    key = f'{video_id}:{_cache_tag(audio_only)}'
    async with _download_futures_lock:
        future = _download_futures.get(key)
        state = _download_early_states.get(key)
    if future is None:
        found = _complete_cache_files(video_id, _cache_tag(audio_only))
        return found[0] if found else None
    if state is None:
        waiter = asyncio.shield(future)
    else:
        ready = asyncio.create_task(state.ready.wait())
        completed = asyncio.ensure_future(asyncio.shield(future))
        ready.add_done_callback(_consume_download_future)
        completed.add_done_callback(_consume_download_future)
        waiter = asyncio.create_task(_first_download_path(ready, completed, state, audio_only))
    try:
        if timeout is not None:
            return await asyncio.wait_for(waiter, timeout=max(0.1, timeout))
        return await waiter
    finally:
        if state is not None and (not waiter.done()):
            waiter.cancel()

async def _first_download_path(ready, completed, state, audio_only: bool):
    try:
        done, _ = await asyncio.wait({ready, completed}, return_when=asyncio.FIRST_COMPLETED)
        if state.path and os.path.exists(state.path):
            return state.path
        if completed in done:
            path = completed.result()
            return path if path and os.path.exists(path) else None
        path = await completed
        return path if path and os.path.exists(path) else None
    finally:
        for task in (ready, completed):
            if not task.done():
                task.cancel()

def is_download_inflight(video_id: str, audio_only: bool=True) -> bool:
    return f'{video_id}:{_cache_tag(audio_only)}' in _download_futures

class _LoopEvent(threading.Event):

    def __init__(self, loop: asyncio.AbstractEventLoop, aio_event: asyncio.Event):
        super().__init__()
        self._loop = loop
        self._aio_event = aio_event

    def set(self) -> None:
        super().set()
        try:
            self._loop.call_soon_threadsafe(self._aio_event.set)
        except RuntimeError:
            pass

async def _run_download_job(video_id: str, audio_only: bool, tag: str, priority: int, requested_at: float, cancel_event: threading.Event, future: asyncio.Future, early_state: _EarlyDownloadState | None) -> None:
    dedup_key = f'{video_id}:{tag}'
    try:
        result = await _download_audio_impl(video_id, audio_only, tag, priority=priority, requested_at=requested_at, cancel_event=cancel_event, early_state=early_state)
        if not future.done():
            future.set_result(result)
    except BaseException as exc:
        if not future.done():
            if isinstance(exc, asyncio.CancelledError):
                future.cancel()
            else:
                future.set_exception(exc)
    finally:
        _download_cancel_events.pop(dedup_key, None)
        async with _download_futures_lock:
            if _download_futures.get(dedup_key) is future:
                _download_futures.pop(dedup_key, None)
            _download_early_states.pop(dedup_key, None)

async def _download_audio_impl(video_id: str, audio_only: bool, tag: str, priority: int=0, requested_at: float | None=None, cancel_event: 'threading.Event | None'=None, early_state: _EarlyDownloadState | None=None) -> str:
    gate = _download_semaphore()
    request_started_at = requested_at if requested_at is not None else _time_mod.monotonic()
    await gate.acquire(priority)
    slot_started_at = _time_mod.monotonic()
    LOGGER.info('#download start %s variant=%s priority=%d slot_wait=%.2fs', video_id, tag, priority, slot_started_at - request_started_at)
    try:
        if cancel_event is not None and cancel_event.is_set():
            raise _DownloadCancelled('superseded by a newer playback request')
        result = await _download_audio_locked(video_id, audio_only, tag, cancel_event=cancel_event, early_state=early_state)
        LOGGER.info('#download complete %s variant=%s elapsed=%.2fs total=%.2fs', video_id, tag, _time_mod.monotonic() - slot_started_at, _time_mod.monotonic() - request_started_at)
        return result
    finally:
        await gate.release()

async def _download_audio_locked(video_id: str, audio_only: bool, tag: str, cancel_event: 'threading.Event | None'=None, early_state: _EarlyDownloadState | None=None) -> str:
    loop = asyncio.get_running_loop()
    early_holder: dict = {}
    early_async = asyncio.Event()
    done_async = asyncio.Event()
    early_event = _LoopEvent(loop, early_async)

    def _run_download():
        try:
            _download_audio_sync(video_id, audio_only, early_event=early_event if early_state is not None and _early_handoff_allowed(audio_only) else None, early_holder=early_holder, cancel_event=cancel_event)
        except Exception as exc:
            early_holder['error'] = exc
            early_event.set()
        finally:
            try:
                loop.call_soon_threadsafe(done_async.set)
            except RuntimeError:
                pass
    dl_thread = threading.Thread(target=_run_download, daemon=True, name=f'melody-early-dl-{video_id[:8]}')
    dl_thread.start()
    if early_state is not None and _early_handoff_allowed(audio_only):
        early_task = asyncio.ensure_future(early_async.wait())
        done_task = asyncio.ensure_future(done_async.wait())
        try:
            await asyncio.wait({early_task, done_task}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for t in (early_task, done_task):
                if not t.done():
                    t.cancel()
        path = early_holder.get('early_path')
        if path and os.path.exists(path):
            early_state.path = path
            early_state.ready.set()
    await done_async.wait()
    if 'error' in early_holder:
        dl_error = early_holder['error']
        if audio_only and (not isinstance(dl_error, _DownloadCancelled)) and (not _is_permanent_download_error(dl_error)):
            try:
                rescued = await _http_rescue_download(video_id, tag, early_state=early_state, cancel_event=cancel_event)
            except Exception as rescue_exc:
                LOGGER.info('#download HTTP rescue failed for %s: %s', video_id, redact_sensitive_text(rescue_exc))
                rescued = None
            if rescued:
                LOGGER.info('🛟 #download HTTP rescue supplied %s after yt-dlp failure (%s)', video_id, type(dl_error).__name__)
                return rescued
        raise dl_error
    path = early_holder.get('final_path')
    if path and os.path.exists(path) and (not path.endswith('.part')):
        return path
    found = _complete_cache_files(video_id, tag)
    if found:
        return found[0]
    raise FileNotFoundError(f'Downloaded file not found for video_id={video_id}')
_RESCUE_EXT_BY_MIME = {'audio/webm': 'webm', 'video/webm': 'webm', 'audio/mp4': 'm4a', 'video/mp4': 'm4a', 'audio/mpeg': 'mp3', 'audio/ogg': 'webm'}

async def _http_rescue_download(video_id: str, tag: str, early_state: '_EarlyDownloadState | None'=None, cancel_event: 'threading.Event | None'=None) -> 'str | None':
    if os.getenv('HTTP_RESCUE_DOWNLOAD', '1') == '0':
        return None
    try:
        urls = await resolve_stream_urls(video_id, want_video=False, force=True)
    except Exception as exc:
        LOGGER.info('#download rescue resolve failed for %s: %s', video_id, exc)
        return None
    audio_url = (urls or {}).get('audio')
    if not audio_url or not str(audio_url).lower().startswith(('http://', 'https://')):
        return None
    headers = dict((urls or {}).get('headers') or {})
    if '.m3u8' in audio_url:
        return await _ffmpeg_rescue_download(video_id, tag, audio_url, headers)
    budget = _env_float('HTTP_RESCUE_TIMEOUT', 60.0)
    started = _time_mod.monotonic()
    tmp_path = f'/tmp/melody_{video_id}_{tag}.rescue.part'
    written = 0
    ext = 'm4a'
    try:
        client_kwargs = _http_client_kwargs()
        client_kwargs['timeout'] = _httpx.Timeout(budget, connect=8.0)
        async with _httpx.AsyncClient(**client_kwargs) as client:
            async with client.stream('GET', audio_url, headers=headers) as response:
                if response.status_code >= 400:
                    LOGGER.info('#download rescue GET %s returned HTTP %s', video_id, response.status_code)
                    return None
                mime = str(response.headers.get('content-type', '')).split(';')[0].strip().lower()
                ext = _RESCUE_EXT_BY_MIME.get(mime, 'm4a')
                with open(tmp_path, 'wb') as fh:
                    async for chunk in response.aiter_bytes(64 * 1024):
                        if cancel_event is not None and cancel_event.is_set():
                            raise _DownloadCancelled('rescue download superseded by a newer request')
                        if _time_mod.monotonic() - started > budget:
                            raise asyncio.TimeoutError('rescue download exceeded its budget')
                        fh.write(chunk)
                        written += len(chunk)
        if written < 1024:
            raise RuntimeError(f'rescue download produced only {written} bytes')
        final_path = f'/tmp/melody_{video_id}_{tag}.{ext}'
        os.replace(tmp_path, final_path)
        LOGGER.info('✅ #download rescue fetched %s over HTTP (%.1f KB in %.2fs)', video_id, written / 1024.0, _time_mod.monotonic() - started)
        if early_state is not None and (not early_state.ready.is_set()):
            early_state.path = final_path
            early_state.ready.set()
        return final_path
    except _DownloadCancelled:
        raise
    except Exception as exc:
        LOGGER.info('#download rescue GET failed for %s: %s', video_id, redact_sensitive_text(exc))
        return None
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass

async def _ffmpeg_rescue_download(video_id: str, tag: str, url: str, headers: dict) -> 'str | None':
    final_path = f'/tmp/melody_{video_id}_{tag}.m4a'
    tmp_path = f'{final_path}.rescue.part'
    header_blob = ''.join((f'{k}: {v}\r\n' for k, v in (headers or {}).items()))
    cmd = ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y']
    if header_blob:
        cmd += ['-headers', header_blob]
    cmd += ['-i', url, '-vn', '-c:a', 'copy', '-bsf:a', 'aac_adtstoasc', '-f', 'mp4', tmp_path]
    try:
        proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
        try:
            _, err = await asyncio.wait_for(proc.communicate(), timeout=_env_float('HTTP_RESCUE_TIMEOUT', 60.0))
        except asyncio.TimeoutError:
            proc.kill()
            raise
        if proc.returncode != 0 or not os.path.exists(tmp_path) or os.path.getsize(tmp_path) < 1024:
            LOGGER.info('#download HLS rescue failed for %s: %s', video_id, redact_sensitive_text((err or b'').decode('utf-8', 'ignore')[:200]))
            return None
        os.replace(tmp_path, final_path)
        LOGGER.info('✅ #download HLS rescue remuxed %s', video_id)
        return final_path
    except Exception as exc:
        LOGGER.info('#download HLS rescue error for %s: %s', video_id, exc)
        return None
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass

def _innertube_related_sync(video_id: str, exclude: set) -> list[dict]:
    import json
    _ANDROID_KEY = 'AIzaSyA8eiZmM1FaDVjRy-df2KTyQ_vz_yYM39w'
    _WEB_KEY = 'AIzaSyAO_FJ2SlqU8Q4STEhlGCjw-FfWAH9IrJg'
    _NEXT_URL = 'https://www.youtube.com/youtubei/v1/next'
    _CLIENT_CONTEXTS = [('WEB', _WEB_KEY, '2.20260801.00.00', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'), ('ANDROID', _ANDROID_KEY, '20.10.38', 'com.google.android.youtube/20.10.38 (Linux; U; Android 14) gzip'), ('IOS', _ANDROID_KEY, '20.10.4', 'com.google.ios.youtube/20.10.4 (iPhone16,2; U; CPU iOS 18_3_2 like Mac OS X)'), ('ANDROID_MUSIC', _ANDROID_KEY, '7.16.51', 'com.google.android.apps.youtube.music/7.16.51 (Linux; U; Android 14)'), ('IOS_MUSIC', _ANDROID_KEY, '8.12.2', 'com.google.ios.youtubemusic/8.12.2 (iPhone16,2; U; CPU iOS 18_3_2 like Mac OS X)')]
    data = None
    for client_name, api_key, client_ver, ua in _CLIENT_CONTEXTS:
        url = f'{_NEXT_URL}?key={api_key}&prettyPrint=false'
        client_ctx = {'clientName': client_name, 'clientVersion': client_ver, 'hl': 'en', 'gl': 'US', 'utcOffsetMinutes': 0}
        if client_name == 'ANDROID':
            client_ctx['androidSdkVersion'] = 30
        elif client_name == 'IOS':
            client_ctx['deviceMake'] = 'Apple'
            client_ctx['deviceModel'] = 'iPhone16,2'
        payload = json.dumps({'context': {'client': client_ctx}, 'videoId': video_id}).encode('utf-8')
        try:
            client = get_http_sync_client()
            resp = client.post(url, content=payload, headers={'Content-Type': 'application/json', 'User-Agent': ua}, timeout=3.0)
            if resp.status_code != 200:
                LOGGER.warning('InnerTube %s related HTTP %s for %s', client_name, resp.status_code, video_id)
                continue
            data = json.loads(resp.text)
        except Exception as exc:
            LOGGER.warning('InnerTube %s related error: %s', client_name, exc)
            continue
        if data:
            break
    if not data:
        LOGGER.warning('InnerTube related: all client contexts failed for %s', video_id)
        return []

    def _extract_video_id(renderer):
        vid = renderer.get('videoId')
        if vid:
            return vid
        nav = renderer.get('navigationEndpoint', {})
        if isinstance(nav, dict):
            we = nav.get('watchEndpoint')
            if isinstance(we, dict) and we.get('videoId'):
                return we['videoId']
            we2 = nav.get('watchNextEndpoint', {})
            if isinstance(we2, dict) and we2.get('videoId'):
                return we2['videoId']
        cid = renderer.get('contentId')
        if cid:
            return cid
        return ''

    def _walk(node, out):
        if isinstance(node, dict):
            renderer = node.get('playlistPanelVideoRenderer') or node.get('compactVideoRenderer') or node.get('videoRenderer') or node.get('gridVideoRenderer')
            if renderer and _extract_video_id(renderer):
                out.append(renderer)
            lockup = node.get('lockupViewModel')
            if lockup and _extract_video_id(lockup):
                out.append(lockup)
            for v in node.values():
                _walk(v, out)
        elif isinstance(node, list):
            for item in node:
                _walk(item, out)
    renderers: list = []
    try:
        _walk(data, renderers)
    except Exception as exc:
        LOGGER.warning('InnerTube related parse error: %s', exc)
        return []
    results = []
    seen = set()
    for r in renderers:
        vid = _extract_video_id(r)
        if not vid or vid in exclude or vid in seen or (vid == video_id):
            continue
        seen.add(vid)
        title = r.get('title', {}).get('simpleText') or r.get('title', {}).get('runs', [{}])[0].get('text') or r.get('metadata', {}).get('lockupMetadataRenderer', {}).get('title', {}).get('runs', [{}])[0].get('text') or 'Unknown'
        length_text = r.get('lengthText', {}).get('simpleText', '') or (r.get('lengthText', {}).get('runs', [{}])[0].get('text', '') if r.get('lengthText') else '')
        duration = 0
        if length_text:
            parts = [int(p) for p in length_text.split(':') if p.isdigit()]
            if len(parts) == 2:
                duration = parts[0] * 60 + parts[1]
            elif len(parts) == 3:
                duration = parts[0] * 3600 + parts[1] * 60 + parts[2]
        thumbs = r.get('thumbnail', {}).get('thumbnails') or []
        thumbnail = thumbs[-1].get('url', '') if thumbs else f'https://i.ytimg.com/vi/{vid}/hqdefault.jpg'
        byline = r.get('longBylineText', {}).get('runs', [{}])[0].get('text') or r.get('shortBylineText', {}).get('runs', [{}])[0].get('text') or r.get('longBylineText', {}).get('simpleText') or r.get('shortBylineText', {}).get('simpleText') or 'Unknown'
        results.append({'id': vid, 'title': title, 'duration': duration, 'url': f'https://www.youtube.com/watch?v={vid}', 'thumbnail': thumbnail, 'uploader': byline})
        if len(results) >= 10:
            break
    if results:
        LOGGER.info('✅ InnerTube related fallback OK: %d candidates for %s', len(results), video_id)
    else:
        LOGGER.warning('❌ InnerTube related fallback: no candidates for %s', video_id)
    return results

def _innertube_player_sync(video_id: str) -> 'dict | None':
    import json
    _PLAYER_URL = 'https://www.youtube.com/youtubei/v1/player'
    _CLIENT_CONTEXTS = [('ANDROID_VR', '1.62.27', 'com.google.android.apps.youtube.vr.oculus/1.62.27 (Linux; U; Android 12) gzip'), ('TVHTML5_SIMPLY_EMBEDDED_PLAYER', '7.20250605', 'Mozilla/5.0 (SMART-TV; LINUX; Tizen 6.5) AppleWebKit/537.36 (KHTML, like Gecko) 85.0.4183.93/6.5 TV Safari/537.36'), ('WEB', '2.20260801.00.00', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36')]
    for client_name, client_ver, ua in _CLIENT_CONTEXTS:
        client_ctx = {'clientName': client_name, 'clientVersion': client_ver, 'hl': 'en', 'gl': 'US', 'utcOffsetMinutes': 0}
        if client_name.startswith('ANDROID'):
            client_ctx['androidSdkVersion'] = 32
        payload = json.dumps({'context': {'client': client_ctx}, 'videoId': video_id, 'contentCheckOk': True, 'racyCheckOk': True}).encode('utf-8')
        try:
            resp = get_http_sync_client().post(f'{_PLAYER_URL}?prettyPrint=false', content=payload, headers={'Content-Type': 'application/json', 'User-Agent': ua}, timeout=8.0)
            if resp.status_code != 200:
                continue
            details = (json.loads(resp.text) or {}).get('videoDetails') or {}
        except Exception as exc:
            LOGGER.debug('InnerTube %s player error for %s: %s', client_name, video_id, exc)
            continue
        if not details.get('videoId'):
            continue
        thumbs = (details.get('thumbnail', {}) or {}).get('thumbnails') or []
        return {'id': details.get('videoId', video_id), 'title': details.get('title') or 'Unknown', 'duration': int(details.get('lengthSeconds') or 0), 'webpage_url': f'https://www.youtube.com/watch?v={video_id}', 'thumbnail': thumbs[-1].get('url') if thumbs else f'https://i.ytimg.com/vi/{video_id}/hqdefault.jpg', 'uploader': details.get('author') or 'Unknown'}
    return None
try:
    _INNERTUBE_TIMEOUT = float(os.getenv('INNERTUBE_TIMEOUT', '3'))
except Exception:
    _INNERTUBE_TIMEOUT = 3.0
_IT_WEB_VERSION = '2.20250606.01.00'
_IT_WEB_UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36'
_CLIENT_IDS = {'WEB_REMIX': '67', 'TVHTML5_SIMPLY_EMBEDDED_PLAYER': '85', 'IOS': '5', 'IOS_MUSIC': '26', 'ANDROID_MUSIC': '21', 'ANDROID': '3', 'ANDROID_VR': '28', 'TVHTML5': '7', 'MWEB': '2', 'WEB': '1', 'WEB_EMBEDDED_PLAYER': '56'}
_IT_SESSION_TTL = 1800.0
_it_session: dict = {}
_it_session_lock = threading.Lock()

def _innertube_cookie_map() -> dict:
    out: dict = {}
    if not _COOKIE_TEXT:
        return out
    for line in _COOKIE_TEXT.splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        parts = line.split('\t')
        if len(parts) >= 7 and 'youtube.com' in parts[0]:
            out[parts[5]] = parts[6]
    return out

def _sapisid_hash(origin: str='https://www.youtube.com') -> str:
    import hashlib
    jar = _innertube_cookie_map()
    sapisid = jar.get('__Secure-3PAPISID') or jar.get('SAPISID') or jar.get('__Secure-1PAPISID') or ''
    if not sapisid:
        return ''
    ts = str(int(time.time()))
    digest = hashlib.sha1(f'{ts} {sapisid} {origin}'.encode('utf-8')).hexdigest()
    return f'SAPISIDHASH {ts}_{digest}'

def _fetch_visitor_data() -> str:
    import json
    try:
        resp = get_http_sync_client().post('https://www.youtube.com/youtubei/v1/visitor_id?prettyPrint=false', content=json.dumps({'context': {'client': {'clientName': 'WEB', 'clientVersion': _IT_WEB_VERSION, 'hl': 'en', 'gl': 'US'}}}).encode('utf-8'), headers={'Content-Type': 'application/json', 'User-Agent': _IT_WEB_UA, 'Origin': 'https://www.youtube.com', 'X-Youtube-Client-Name': '1', 'X-Youtube-Client-Version': _IT_WEB_VERSION}, timeout=_INNERTUBE_TIMEOUT)
        if resp.status_code != 200:
            return ''
        data = json.loads(resp.text) or {}
        return (data.get('responseContext') or {}).get('visitorData') or ''
    except Exception as exc:
        LOGGER.debug('visitorData fetch failed: %s', exc)
        return ''

def _fetch_po_token(visitor_data: str) -> str:
    import json
    if not visitor_data or not _bgutil_http_alive():
        return ''
    try:
        resp = get_http_sync_client().post(f'http://127.0.0.1:{_BGUTIL_HTTP_PORT}/get_pot', content=json.dumps({'content_binding': visitor_data}).encode('utf-8'), headers={'Content-Type': 'application/json'}, timeout=_env_float('BGUTIL_POT_TIMEOUT', 4.0))
        if resp.status_code != 200:
            return ''
        data = json.loads(resp.text) or {}
        return data.get('poToken') or data.get('po_token') or ''
    except Exception as exc:
        LOGGER.debug('bgutil PO token fetch failed: %s', exc)
        return ''

def _innertube_session(refresh: bool=False) -> dict:
    now = time.monotonic()
    with _it_session_lock:
        cached = _it_session.get('data')
        if cached and (not refresh) and (_it_session.get('at', 0) + _IT_SESSION_TTL > now):
            return cached
        visitor = _fetch_visitor_data()
        pot = _fetch_po_token(visitor) if visitor else ''
        data = {'visitor': visitor, 'pot': pot}
        _it_session['data'] = data
        _it_session['at'] = now
        if visitor:
            LOGGER.info('🔐 InnerTube session ready (visitorData ok, PO token %s)', 'ok' if pot else 'unavailable')
        return data

def _innertube_cookie_header() -> str:
    if not _COOKIE_TEXT:
        return ''
    pairs = []
    for line in _COOKIE_TEXT.splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        parts = line.split('\t')
        if len(parts) >= 7 and 'youtube.com' in parts[0]:
            pairs.append(f'{parts[5]}={parts[6]}')
    return '; '.join(pairs)

def _innertube_streams_sync(video_id: str, want_video: bool=False) -> 'dict | None':
    import json
    from concurrent.futures import as_completed
    _CLIENTS = [('IOS', '20.10.4', 'com.google.ios.youtube/20.10.4 (iPhone16,2; U; CPU iOS 18_3_2 like Mac OS X)', {'deviceMake': 'Apple', 'deviceModel': 'iPhone16,2', 'osName': 'iPhone', 'osVersion': '18.3.2.22D82'}), ('IOS_MUSIC', '8.12.2', 'com.google.ios.youtubemusic/8.12.2 (iPhone16,2; U; CPU iOS 18_3_2 like Mac OS X)', {'deviceMake': 'Apple', 'deviceModel': 'iPhone16,2', 'osName': 'iPhone', 'osVersion': '18.3.2.22D82'}), ('ANDROID_MUSIC', '7.16.51', 'com.google.android.apps.youtube.music/7.16.51 (Linux; U; Android 14)', {'androidSdkVersion': 34}), ('ANDROID', '20.10.38', 'com.google.android.youtube/20.10.38 (Linux; U; Android 14)', {'androidSdkVersion': 34}), ('ANDROID_VR', '1.62.27', 'com.google.android.apps.youtube.vr.oculus/1.62.27 (Linux; U; Android 12) gzip', {'androidSdkVersion': 32}), ('TVHTML5', '7.20250605', 'Mozilla/5.0 (SMART-TV; LINUX; Tizen 6.5) AppleWebKit/537.36 (KHTML, like Gecko) 85.0.4183.93/6.5 TV Safari/537.36', {}), ('MWEB', '2.20250605.01.00', 'Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Mobile Safari/537.36', {})]
    _WEB_FAMILY = ('TVHTML5', 'MWEB', 'WEB', 'WEB_EMBEDDED_PLAYER', 'WEB_REMIX', 'TVHTML5_SIMPLY_EMBEDDED_PLAYER')
    cookie_header = _innertube_cookie_header()
    _MOBILE_ORDER = ('IOS', 'ANDROID', 'ANDROID_MUSIC', 'IOS_MUSIC', 'ANDROID_VR')
    _by_name = {c[0]: c for c in _CLIENTS}
    session = {'visitor': '', 'pot': ''}
    if cookie_header:
        session = _innertube_session()
        _CLIENTS = [('WEB', _IT_WEB_VERSION, _IT_WEB_UA, {}), ('TVHTML5_SIMPLY_EMBEDDED_PLAYER', '7.20250605', 'Mozilla/5.0 (SMART-TV; LINUX; Tizen 6.5) AppleWebKit/537.36 (KHTML, like Gecko) 85.0.4183.93/6.5 TV Safari/537.36', {})]
    else:
        _CLIENTS = [_by_name[n] for n in _MOBILE_ORDER if n in _by_name]

    def _probe(entry):
        client_name, client_ver, ua, extra = entry
        ctx = {'clientName': client_name, 'clientVersion': client_ver, 'hl': 'en', 'gl': 'US', 'utcOffsetMinutes': 0, **extra}
        body = {'context': {'client': ctx}, 'videoId': video_id, 'contentCheckOk': True, 'racyCheckOk': True}
        if client_name.startswith('TVHTML5') or client_name == 'WEB_EMBEDDED_PLAYER':
            body['context']['thirdParty'] = {'embedUrl': 'https://www.youtube.com/'}
        is_web = client_name in _WEB_FAMILY
        if is_web and session.get('visitor'):
            ctx['visitorData'] = session['visitor']
            if session.get('pot'):
                body['serviceIntegrityDimensions'] = {'poToken': session['pot']}
        payload = json.dumps(body).encode('utf-8')
        headers = {'Content-Type': 'application/json', 'User-Agent': ua, 'X-Youtube-Client-Name': _CLIENT_IDS.get(client_name, '1'), 'X-Youtube-Client-Version': client_ver, 'Origin': 'https://www.youtube.com'}
        if is_web and session.get('visitor'):
            headers['X-Goog-Visitor-Id'] = session['visitor']
            headers['X-Origin'] = 'https://www.youtube.com'
            auth = _sapisid_hash()
            if auth:
                headers['Authorization'] = auth
                headers['X-Goog-AuthUser'] = '0'
        if cookie_header and client_name in _WEB_FAMILY:
            headers['Cookie'] = cookie_header
        try:
            resp = get_http_sync_client().post('https://www.youtube.com/youtubei/v1/player?prettyPrint=false', content=payload, headers=headers, timeout=_INNERTUBE_TIMEOUT)
            if resp.status_code != 200:
                return None
            data = json.loads(resp.text) or {}
        except Exception as exc:
            LOGGER.debug('InnerTube %s streams error for %s: %s', client_name, video_id, exc)
            return None
        status = ((data.get('playabilityStatus') or {}).get('status') or '').upper()
        if status not in ('OK', 'LIVE_STREAM_OFFLINE'):
            LOGGER.info('#stream innertube %s for %s -> %s (%s)', client_name, video_id, status or '?', str((data.get('playabilityStatus') or {}).get('reason') or '')[:60])
            if status == 'LOGIN_REQUIRED' and is_web:
                _it_session.pop('at', None)
                note_cookie_rejected('innertube %s' % client_name)
            return None
        sd = data.get('streamingData') or {}
        raw = list(sd.get('formats') or []) + list(sd.get('adaptiveFormats') or [])
        formats = []
        for f in raw:
            url = f.get('url')
            if not url or f.get('signatureCipher') or f.get('cipher'):
                continue
            mime = f.get('mimeType') or ''
            is_video = mime.startswith('video/')
            has_audio = 'mp4a' in mime or 'opus' in mime or mime.startswith('audio/')
            is_dash = bool(f.get('initRange') or f.get('indexRange'))
            protocol = 'dash' if is_dash else 'https'
            if is_web and session.get('pot') and ('pot=' not in url):
                sep = '&' if '?' in url else '?'
                url = url + sep + 'pot=' + session['pot']
            formats.append({'url': url, 'protocol': protocol, 'vcodec': 'avc1' if is_video else 'none', 'acodec': 'mp4a' if has_audio and (not is_video or ',' in mime) else 'none' if is_video else 'mp4a', 'height': f.get('height') or 0, 'abr': (f.get('averageBitrate') or f.get('bitrate') or 0) / 1000 if not is_video else 0, 'tbr': (f.get('bitrate') or 0) / 1000})
        if not formats:
            hls = sd.get('hlsManifestUrl')
            if hls:
                formats = [{'url': hls, 'protocol': 'm3u8_native', 'vcodec': 'avc1', 'acodec': 'mp4a', 'height': 480, 'abr': 128, 'tbr': 500}]
            else:
                LOGGER.debug('InnerTube %s: no unciphered format for %s, trying next client', client_name, video_id)
                return None
        return {'formats': formats, 'client': client_name, 'headers': {'User-Agent': ua, 'Referer': 'https://www.youtube.com/'}, 'hlsManifestUrl': sd.get('hlsManifestUrl'), 'is_live': bool((data.get('videoDetails') or {}).get('isLiveContent') and (not sd.get('formats')))}
    if not _CLIENTS:
        return None
    deadline = time.monotonic() + _INNERTUBE_TIMEOUT + 0.5
    futures = {YTDL_POOL.submit(_probe, entry): entry[0] for entry in _CLIENTS}
    try:
        try:
            for fut in as_completed(futures, timeout=max(0.1, deadline - time.monotonic())):
                try:
                    result = fut.result()
                except Exception:
                    continue
                if result:
                    picked = _pick_stream_formats(result, want_video)
                    if not picked:
                        LOGGER.debug('InnerTube: %s response has no usable %s format for %s', result['client'], 'video' if want_video else 'audio', video_id)
                        continue
                    result['_picked'] = picked
                    LOGGER.debug('InnerTube: %s served %s formats for %s', result['client'], len(result['formats']), video_id)
                    for other in futures:
                        other.cancel()
                    return result
        except Exception:
            LOGGER.debug('InnerTube: all clients timed out for %s', video_id)
    finally:
        for future in futures:
            if not future.done():
                future.cancel()
    return None

def _resolve_stream_urls_innertube(video_id: str, want_video: bool) -> dict:
    info = _innertube_streams_sync(video_id, want_video=want_video)
    picked = (info or {}).get('_picked') or _pick_stream_formats(info or {}, want_video)
    if not picked:
        _note_innertube_stream(False)
        raise ValueError('innertube: no directly streamable format')
    picked['is_live'] = bool((info or {}).get('is_live'))
    picked['headers'] = {k: v for k, v in ((info or {}).get('headers') or {}).items() if k in ('User-Agent', 'Referer')} or {k: v for k, v in (_ydl_opts().get('http_headers') or {}).items() if k in ('User-Agent', 'Referer')}
    urls = [u for u in (picked.get('video'), picked.get('audio')) if u]
    picked['expires_at'] = min((_url_expiry(u) for u in urls)) - _STREAM_URL_SAFETY_MARGIN
    _note_innertube_stream(True)
    return picked

async def get_video_details(video_id: str) -> 'dict | None':
    if not is_valid_video_id(video_id):
        return None
    cache_key = f'vid:{video_id}'
    cached = _meta_cache_get(cache_key)
    if cached:
        return cached
    loop = asyncio.get_running_loop()
    for fn in (_ytapi_details_sync, _innertube_player_sync, _innertube_next_sync):
        if fn is _ytapi_details_sync and (not ytapi_enabled()):
            continue
        try:
            info = await loop.run_in_executor(YTDL_POOL, fn, video_id)
        except Exception:
            info = None
        norm = _normalize_info(info)
        if norm and (norm.get('title') != 'Unknown' or norm.get('duration')):
            _meta_cache_put(cache_key, norm)
            return norm
    return None
_RELATED_CACHE: dict = {}
_RELATED_TTL = 300.0
_RELATED_FAILURE_TTL = 45.0
_RELATED_FAILURES: dict[str, float] = {}

async def get_related_videos(video_id: str, exclude_ids: list[str]=None) -> list[dict]:
    exclude = set(exclude_ids or [])

    def _filtered(items: list[dict]) -> list[dict]:
        return [i for i in items if i.get('id') and i['id'] not in exclude]
    now = time.monotonic()
    if _RELATED_FAILURES.get(video_id, 0.0) > now:
        return []
    cached = _RELATED_CACHE.get(video_id)
    if cached and cached[0] > time.monotonic():
        hit = _filtered(cached[1])
        if hit:
            return hit

    def _remember(items: list[dict]) -> list[dict]:
        if items:
            _RELATED_CACHE[video_id] = (time.monotonic() + _RELATED_TTL, items)
            if len(_RELATED_CACHE) > 256:
                for key in list(_RELATED_CACHE)[:128]:
                    _RELATED_CACHE.pop(key, None)
        return _filtered(items)
    try:
        loop = asyncio.get_running_loop()
        fast = await loop.run_in_executor(YTDL_POOL, _innertube_related_sync, video_id, set())
        if fast:
            return _remember(fast)
    except Exception as exc:
        LOGGER.info('InnerTube related lookup failed — trying yt-dlp: %s', exc)

    def _related():
        url = f'https://www.youtube.com/watch?v={video_id}&list=RD{video_id}'
        opts = {**_ydl_opts(), 'extract_flat': True, 'playlist_items': '1:10'}
        opts.pop('format', None)
        opts['ignoreerrors'] = True
        opts['skip_download'] = True
        with _locked_ytdl(opts) as ydl:
            info = ydl.extract_info(url, download=False)
            return info.get('entries') or [] if info else []
    try:
        loop = asyncio.get_running_loop()
        entries = await loop.run_in_executor(YTDL_POOL, _related)
        results = []
        for e in entries:
            if not e:
                continue
            vid = e.get('id', '')
            if vid and vid not in exclude:
                results.append({'id': vid, 'title': e.get('title', 'Unknown'), 'duration': e.get('duration', 0), 'url': f'https://www.youtube.com/watch?v={vid}', 'thumbnail': e.get('thumbnail') or f'https://i.ytimg.com/vi/{vid}/hqdefault.jpg', 'uploader': e.get('uploader', 'Unknown')})
        if results:
            return _remember(results)
        LOGGER.info('yt-dlp related-videos returned nothing — trying InnerTube fallback | %s', video_id)
    except Exception as exc:
        LOGGER.warning('get_related_videos (yt-dlp) failed — trying InnerTube fallback: %s', exc)
    _RELATED_FAILURES[video_id] = time.monotonic() + _RELATED_FAILURE_TTL
    return []
_POPULAR_QUERIES = ['top songs 2026', 'trending music 2026', 'popular hindi songs 2026', 'top english songs 2026', 'punjabi hits 2026']

async def warm_popular_metadata() -> None:
    try:
        for q in _POPULAR_QUERIES:
            info = await get_video_info(q)
            if info:
                LOGGER.debug('Pre-warmed metadata: %s → %s', q, info.get('title', '?')[:40])
        LOGGER.info('✅ Popular metadata pre-warm complete (%d queries)', len(_POPULAR_QUERIES))
    except Exception as exc:
        LOGGER.debug('Popular metadata pre-warm skipped: %s', exc)
_stream_url_cache: dict = {}
_stream_url_locks: dict = {}
_stream_url_failures: dict = {}
_STREAM_URL_FAILURE_TTL = 8.0
_STREAM_URL_FALLBACK_TTL = 1800
_STREAM_URL_SAFETY_MARGIN = 300
_STREAM_URL_CACHE_MAX = 128

def _prune_stream_url_state(now: 'float | None'=None) -> None:
    now = _time_mod.time() if now is None else now
    expired = [key for key, value in _stream_url_cache.items() if value.get('expires_at', 0) <= now]
    for key in expired:
        _stream_url_cache.pop(key, None)
        lock = _stream_url_locks.get(key)
        if lock is None or not lock.locked():
            _stream_url_locks.pop(key, None)
    expired_failures = [key for key, deadline in _stream_url_failures.items() if deadline <= _time_mod.monotonic()]
    for key in expired_failures:
        _stream_url_failures.pop(key, None)
    if len(_stream_url_cache) > _STREAM_URL_CACHE_MAX:
        oldest = sorted(_stream_url_cache, key=lambda key: _stream_url_cache[key].get('expires_at', 0))[:len(_stream_url_cache) - _STREAM_URL_CACHE_MAX]
        for key in oldest:
            _stream_url_cache.pop(key, None)
            lock = _stream_url_locks.get(key)
            if lock is None or not lock.locked():
                _stream_url_locks.pop(key, None)

def drop_caches() -> int:
    freed = 0
    for store in (_meta_cache, _RELATED_CACHE, _stream_url_cache, _TG_EARLY_PATHS):
        try:
            freed += len(store)
            store.clear()
        except Exception:
            pass
    try:
        freed += len(_stream_url_failures)
        _stream_url_failures.clear()
        for key, lock in list(_stream_url_locks.items()):
            if not lock.locked():
                _stream_url_locks.pop(key, None)
        for key, lock in list(_tg_media_locks.items()):
            if not lock.locked():
                _tg_media_locks.pop(key, None)
    except Exception:
        pass
    return freed

def _url_expiry(url: str) -> float:
    try:
        from urllib.parse import parse_qs, urlparse
        qs = parse_qs(urlparse(url).query)
        for key in ('expire', 'expires'):
            if qs.get(key):
                return float(qs[key][0])
    except Exception:
        pass
    return _time_mod.time() + _STREAM_URL_FALLBACK_TTL

def _max_stream_height() -> int:
    import os as _os
    return {'1080p': 1080, '720p': 720, '480p': 480, '360p': 360}.get((_os.getenv('VIDEO_QUALITY') or '480p').strip().lower(), 480)

def _pick_stream_formats(info: dict, want_video: bool) -> dict:
    all_formats = [f for f in info.get('formats') or [] if f.get('url')]

    def usable(f) -> bool:
        proto = f.get('protocol') or ''
        if 'm3u8' in proto:
            return False
        is_audio_only = f.get('vcodec') in (None, 'none') and f.get('acodec') not in (None, 'none')
        is_dash_audio = 'dash' in proto and is_audio_only
        return proto.startswith('http') and (not 'dash' in proto or is_dash_audio)

    def hls_ok(f) -> bool:
        proto = f.get('protocol') or ''
        return 'm3u8' in proto or (f.get('url') or '').split('?')[0].endswith('.m3u8')
    hls_formats = [f for f in all_formats if hls_ok(f)]
    formats = [f for f in all_formats if usable(f)] or hls_formats

    def dash_audio_ok(f) -> bool:
        proto = f.get('protocol') or ''
        return 'dash' in proto and f.get('vcodec') in (None, 'none') and (f.get('acodec') not in (None, 'none'))
    if not want_video:
        dash_audio = [f for f in all_formats if dash_audio_ok(f)]
        if dash_audio:
            formats = list(formats) + dash_audio
    audio_only_fmts = [f for f in formats if f.get('acodec') not in (None, 'none') and f.get('vcodec') in (None, 'none')]
    video_only_fmts = [f for f in formats if f.get('vcodec') not in (None, 'none') and f.get('acodec') in (None, 'none')]
    muxed_fmts = [f for f in formats if f.get('vcodec') not in (None, 'none') and f.get('acodec') not in (None, 'none')]

    def abr(f):
        return f.get('abr') or f.get('tbr') or 0
    audio_pick = None
    if audio_only_fmts:
        try:
            max_abr = max(96, min(float(os.getenv('YT_AUDIO_MAX_ABR', '160')), 320))
        except (TypeError, ValueError):
            max_abr = 160.0
        capped = [f for f in audio_only_fmts if abr(f) and abr(f) <= max_abr]
        audio_pick = max(capped or audio_only_fmts, key=abr)
    if not want_video:
        dash_audio_fmts = [f for f in formats if (f.get('protocol') or '') == 'dash' and f.get('acodec') not in (None, 'none') and (f.get('vcodec') in (None, 'none'))]
        if dash_audio_fmts:
            best_dash = max(dash_audio_fmts, key=lambda f: f.get('abr') or f.get('tbr') or 0)
            LOGGER.info('#stream selected DASH audio format (abr=%s)', best_dash.get('abr') or best_dash.get('tbr'))
            return {'audio': best_dash['url'], 'video': None}
        hls_manifest = info.get('hlsManifestUrl')
        if not audio_pick and hls_manifest:
            return {'audio': hls_manifest, 'video': None}
        if not audio_pick and hls_formats:
            return {'audio': hls_formats[0]['url'], 'video': None}
        if not audio_pick:
            if muxed_fmts:
                cheapest = min(muxed_fmts, key=lambda f: (f.get('height') or 0, f.get('tbr') or 0))
                return {'audio': cheapest['url'], 'video': None}
            metadata_light = [f for f in formats if str(f.get('url') or '').startswith(('http://', 'https://'))]
            if metadata_light:
                cheapest = min(metadata_light, key=lambda f: (f.get('height') or 0, f.get('tbr') or f.get('abr') or 0))
                LOGGER.info('#stream accepted metadata-light direct audio URL')
                return {'audio': cheapest['url'], 'video': None}
            return {}
        return {'audio': audio_pick['url'], 'video': None}

    def height(f):
        return f.get('height') or 0
    cap = _max_stream_height()
    muxed_ok = [f for f in muxed_fmts if 0 < height(f) <= cap]
    if muxed_ok:
        best_muxed = max(muxed_ok, key=lambda f: (height(f), f.get('tbr') or 0))
        return {'video': best_muxed['url'], 'audio': best_muxed['url']}
    if [f for f in video_only_fmts if 0 < height(f) <= cap] and audio_pick:
        video_pick = max([f for f in video_only_fmts if 0 < height(f) <= cap], key=lambda f: (height(f), f.get('tbr') or 0))
        LOGGER.info('#stream direct adaptive pair selected height=%sp video+audio', height(video_pick))
        return {'video': video_pick['url'], 'audio': audio_pick['url']}
    if muxed_fmts:
        best_muxed = max(muxed_fmts, key=lambda f: (height(f), f.get('tbr') or 0))
        return {'video': best_muxed['url'], 'audio': best_muxed['url']}
    return {}

def _invidious_streams_sync(video_id: str, want_video: bool) -> dict | None:
    import json
    live = _invidious_live_instances()
    if not live:
        LOGGER.debug('#stream Invidious rescue skipped for %s — all instances cooling down', video_id)
        return None
    for instance in live:
        try:
            response = get_http_sync_client().get(f'{instance}/api/v1/videos/{video_id}', headers={'User-Agent': 'Mozilla/5.0 (compatible; MusicBot/1.0)'}, timeout=_env_float('INVIDIOUS_TIMEOUT', 1.2))
            if response.status_code != 200:
                _mark_invidious_dead(instance)
                continue
            data = json.loads(response.text) or {}
            formats = []
            for item in list(data.get('adaptiveFormats') or []) + list(data.get('formatStreams') or []):
                url = item.get('url')
                if not url:
                    continue
                mime = str(item.get('type') or item.get('mimeType') or '')
                is_video = mime.startswith('video/') or bool(item.get('size'))
                has_audio = mime.startswith('audio/') or 'audio' in mime or bool(item.get('audioQuality'))
                formats.append({'url': url, 'protocol': 'm3u8_native' if '.m3u8' in url else 'https', 'vcodec': 'h264' if is_video else 'none', 'acodec': 'aac' if has_audio else 'none', 'height': int(item.get('height') or 0), 'abr': float(item.get('bitrate') or 0) / 1000, 'tbr': float(item.get('bitrate') or 0) / 1000})
            hls_url = data.get('hlsUrl')
            if hls_url:
                formats.append({'url': hls_url, 'protocol': 'm3u8_native', 'vcodec': 'h264', 'acodec': 'aac', 'height': 480, 'abr': 160, 'tbr': 600})
            picked = _pick_stream_formats({'formats': formats}, want_video)
            if not picked:
                continue
            urls = [u for u in (picked.get('video'), picked.get('audio')) if u]
            picked['headers'] = {'User-Agent': 'Mozilla/5.0 (compatible; MusicBot/1.0)'}
            picked['expires_at'] = min((_url_expiry(u) for u in urls)) - _STREAM_URL_SAFETY_MARGIN
            LOGGER.info('#stream Invidious direct fallback resolved %s via %s', video_id, instance)
            return picked
        except Exception as exc:
            _mark_invidious_dead(instance)
            LOGGER.debug('Invidious direct resolver %s failed: %s', instance, exc)
    return None

def _resolve_stream_urls_sync(target: str, want_video: bool) -> dict:
    opts = {**_ydl_opts(audio_only=not want_video), 'extract_flat': False, 'skip_download': True, 'noplaylist': True, 'playlist_items': '1', 'check_formats': False, 'getcomments': False, 'writesubtitles': False, 'writeautomaticsub': False}
    _ex = dict(opts.get('extractor_args') or {})
    _yt = dict(_ex.get('youtube') or {})
    _yt.setdefault('player_skip', ['configs'])
    _yt.setdefault('skip', ['translated_subs'])
    _ex['youtube'] = _yt
    opts['extractor_args'] = _ex
    opts.pop('format', None)
    info = _warm_extract(opts, target, cache_key=f"resolve:{('v' if want_video else 'a')}")
    if info and info.get('entries'):
        info = info['entries'][0]
    info = info or {}
    picked = _pick_stream_formats(info, want_video)
    if not picked and info.get('url'):
        top_url = str(info['url'])
        proto = str(info.get('protocol') or '').lower()
        is_hls = 'm3u8' in proto or top_url.split('?', 1)[0].endswith('.m3u8')
        if proto.startswith('http') and (is_hls or 'dash' not in proto):
            picked = {'video': top_url, 'audio': top_url} if want_video else {'audio': top_url, 'video': None}
    if not picked:
        safe_target_id = _extract_video_id(target) or 'unknown'
        LOGGER.info('#stream direct formats unavailable for %s: formats=%d http=%d hls=%d audio=%d muxed=%d video=%d', safe_target_id, len(info.get('formats') or []), sum((1 for f in info.get('formats') or [] if str(f.get('protocol') or '').startswith('http'))), sum((1 for f in info.get('formats') or [] if 'm3u8' in str(f.get('protocol') or '') or str(f.get('url') or '').split('?')[0].endswith('.m3u8'))), sum((1 for f in info.get('formats') or [] if f.get('acodec') not in (None, 'none') and f.get('vcodec') in (None, 'none'))), sum((1 for f in info.get('formats') or [] if f.get('acodec') not in (None, 'none') and f.get('vcodec') not in (None, 'none'))), sum((1 for f in info.get('formats') or [] if f.get('vcodec') not in (None, 'none'))))
        raise ValueError('no directly streamable HTTP/HLS format found')
    picked['is_live'] = bool((info or {}).get('is_live'))
    urls = [u for u in (picked.get('video'), picked.get('audio')) if u]
    resolved_headers = dict((info or {}).get('http_headers') or {})
    if not resolved_headers:
        for fmt_info in (info or {}).get('formats') or []:
            if fmt_info.get('url') in urls and fmt_info.get('http_headers'):
                resolved_headers.update(fmt_info['http_headers'])
                break
    picked['headers'] = {k: v for k, v in (resolved_headers or _ydl_opts().get('http_headers') or {}).items() if k in ('User-Agent', 'Referer')}
    picked['expires_at'] = min((_url_expiry(u) for u in urls)) - _STREAM_URL_SAFETY_MARGIN
    return picked

def _cache_late_resolve(key: str):

    def _done(task) -> None:
        try:
            late = task.result()
        except Exception:
            return
        if not late:
            return
        cached = _stream_url_cache.get(key)
        if cached and cached.get('expires_at', 0) > _time_mod.time():
            return
        _stream_url_cache[key] = late
        _stream_url_failures.pop(key, None)
        _prune_stream_url_state()
        LOGGER.info('🔗 #stream late direct resolve cached for %s', key)
    return _done

async def resolve_stream_urls(video_id: str, want_video: bool=False, *, force: bool=False) -> dict:
    import re
    if re.match('https?://', video_id or ''):
        target = video_id
    elif re.fullmatch('[A-Za-z0-9_-]{11}', video_id or ''):
        target = f'https://www.youtube.com/watch?v={video_id}'
    else:
        raise ValueError(f'resolve_stream_urls: not a streamable source {video_id!r}')
    key = f"{video_id}:{('v' if want_video else 'a')}"
    _prune_stream_url_state()
    cached = None if force else _stream_url_cache.get(key)
    if cached and cached.get('expires_at', 0) > _time_mod.time():
        LOGGER.debug('⚡ stream-url cache HIT for %s', key)
        return cached
    failure_until = 0.0 if force else _stream_url_failures.get(key, 0.0)
    now = _time_mod.monotonic()
    if failure_until > now and (not force):
        failure_age = _STREAM_URL_FAILURE_TTL - (failure_until - now)
        if failure_age >= 0.75:
            raise ValueError('direct stream temporarily unavailable (cached failure)')
    if failure_until:
        _stream_url_failures.pop(key, None)
    lock = _stream_url_locks.setdefault(key, asyncio.Lock())
    async with lock:
        cached = None if force else _stream_url_cache.get(key)
        if cached and cached.get('expires_at', 0) > _time_mod.time():
            return cached
        failure_until = 0.0 if force else _stream_url_failures.get(key, 0.0)
        now = _time_mod.monotonic()
        if failure_until > now and (not force):
            failure_age = _STREAM_URL_FAILURE_TTL - (failure_until - now)
            if failure_age >= 0.75:
                raise ValueError('direct stream temporarily unavailable (cached failure)')
        if failure_until:
            _stream_url_failures.pop(key, None)
        vid_only = _extract_video_id(target) or video_id
        loop = asyncio.get_running_loop()
        _t0 = _time_mod.monotonic()
        tasks = []
        it_task = None
        if re.fullmatch('[A-Za-z0-9_-]{11}', vid_only or '') and (not _innertube_stream_muted()):
            it_task = asyncio.ensure_future(loop.run_in_executor(YTDL_POOL, _resolve_stream_urls_innertube, vid_only, want_video))
            tasks.append(it_task)
        ydl_task = None
        if it_task is not None:
            done_fast, _ = await asyncio.wait({it_task}, timeout=_INNERTUBE_HEADSTART)
            if done_fast:
                try:
                    fast = it_task.result()
                except Exception as exc:
                    fast = None
                    LOGGER.debug('innertube head-start failed for %s: %s', video_id, exc)
                if fast:
                    _stream_url_cache[key] = fast
                    _prune_stream_url_state()
                    LOGGER.info('⚡ #stream innertube head-start resolved %s', video_id)
                    return fast
        ydl_task = asyncio.ensure_future(loop.run_in_executor(YTDL_POOL, _resolve_stream_urls_sync, target, want_video))
        tasks.append(ydl_task)
        resolved = None
        last_exc: Exception | None = None
        pending = {t for t in tasks if not t.done()}
        deadline = max(_t0 + _RESOLVE_TIMEOUT, _time_mod.monotonic() + 1.5)
        try:
            while pending:
                remaining = deadline - _time_mod.monotonic()
                if remaining <= 0:
                    break
                done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED, timeout=remaining)
                if not done:
                    break
                for t in done:
                    try:
                        resolved = t.result()
                    except Exception as exc:
                        last_exc = exc
                        continue
                    if resolved:
                        break
                if resolved:
                    break
        finally:
            for t in pending:
                t.add_done_callback(_cache_late_resolve(key))
        if not resolved and re.fullmatch('[A-Za-z0-9_-]{11}', vid_only or ''):
            try:
                resolved = await asyncio.wait_for(loop.run_in_executor(YTDL_POOL, _invidious_streams_sync, vid_only, want_video), timeout=_env_float('DIRECT_RESCUE_TIMEOUT', 2.5))
            except Exception as exc:
                last_exc = exc
                LOGGER.info('#stream Invidious rescue failed for %s: %s', vid_only, exc)
            if not resolved:
                LOGGER.info('#stream Invidious rescue found no format for %s', vid_only)
        if not resolved:
            if not pending:
                _stream_url_failures[key] = _time_mod.monotonic() + _STREAM_URL_FAILURE_TTL
            LOGGER.info('#stream direct resolve gave up for %s after %.2fs (pending=%d): %s', video_id, _time_mod.monotonic() - _t0, len(pending), last_exc)
            raise last_exc or ValueError('no directly streamable http format found')
        _stream_url_failures.pop(key, None)
        _stream_url_cache[key] = resolved
        _prune_stream_url_state()
        LOGGER.info('🔗 #stream resolved direct %s URLs for %s in %.2fs (no download)', 'video+audio' if want_video else 'audio', video_id, _time_mod.monotonic() - _t0)
        return resolved
