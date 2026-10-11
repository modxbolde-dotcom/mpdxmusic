from __future__ import annotations
import logging
import os
import time
from melody.logging import redact_sensitive_text
log = logging.getLogger(__name__)
_applied = False
PROBE_TIMEOUT_LOCAL = float(os.getenv('FFPROBE_LOCAL_TIMEOUT', '4'))
PROBE_TIMEOUT_REMOTE = float(os.getenv('FFPROBE_REMOTE_TIMEOUT', '4'))
HELP_PROBE_TIMEOUT = float(os.getenv('FFPROBE_HELP_TIMEOUT', '6'))
_supported_flags_cache: 'dict[str, set[str] | None]' = {}

async def _supported_flags(binary: str) -> 'set[str] | None':
    if binary in _supported_flags_cache:
        return _supported_flags_cache[binary]
    import asyncio
    import re
    flags: 'set[str] | None' = None
    try:
        proc = await asyncio.create_subprocess_exec(binary, '-h', 'full', stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=HELP_PROBE_TIMEOUT)
            text = stdout.decode('utf-8', 'ignore')
            found = set(re.findall('(?m)^ *(-\\w+).*?\\s+', text))
            if found:
                found.add('-i')
                flags = found
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
    except FileNotFoundError:
        raise
    except Exception as exc:
        log.debug('capability probe for %s failed (%s)', binary, exc)
    if flags is None:
        log.warning('⚠️ could not read %s capabilities — using its command as built (no flag filtering). Playback continues.', binary)
    _supported_flags_cache[binary] = flags
    return flags

def _patch_cleanup_commands(_ffmpeg_mod, _media_stream_mod) -> None:
    original_cleanup = getattr(_ffmpeg_mod, 'cleanup_commands', None)
    if original_cleanup is None or getattr(original_cleanup, '_melody_patched', False):
        return

    async def cleanup_commands(commands, process_name=None, blacklist=None):
        if not commands:
            return list(commands or [])
        binary = process_name or commands[0]
        try:
            supported = await _supported_flags(binary)
        except FileNotFoundError:
            return await original_cleanup(commands, process_name, blacklist)
        if supported is None:
            if not blacklist:
                return list(commands)
            supported = None
        new_commands: list = []
        ignore_next = False
        for value in commands:
            if not value:
                continue
            if value[0] == '-':
                ignore_next = supported is not None and value not in supported or (blacklist is not None and value in blacklist)
            if not ignore_next:
                new_commands.append(value)
            elif value[0] != '-':
                ignore_next = False
        return new_commands
    cleanup_commands._melody_patched = True
    _ffmpeg_mod.cleanup_commands = cleanup_commands
    if hasattr(_media_stream_mod, 'cleanup_commands'):
        _media_stream_mod.cleanup_commands = cleanup_commands
    log.info('⚡ py-tgcalls capability probe cached (instant playback start).')

class StreamProbeUnavailable(Exception):
    pass

def _is_local_file(path) -> bool:
    try:
        return isinstance(path, str) and os.path.isfile(path)
    except Exception:
        return False

def _is_local_proxy_url(path) -> bool:
    return isinstance(path, str) and path.lower().startswith(('http://127.0.0.1:', 'http://localhost:'))

def _is_local_source(path) -> bool:
    return _is_local_file(path) or _is_local_proxy_url(path)

def _size(path) -> int:
    try:
        return os.path.getsize(path)
    except Exception:
        return 0

def _is_local_playable(path) -> bool:
    return _is_local_file(path) and _size(path) > 8192

async def _wait_for_growth(path, seconds: float=4.0) -> bool:
    import asyncio
    start = _size(path)
    waited = 0.0
    while waited < seconds:
        await asyncio.sleep(0.5)
        waited += 0.5
        if _size(path) > max(start, 65536):
            return True
    return _size(path) > 65536

def _resolve_ffprobe() -> 'str | None':
    import shutil
    found = shutil.which('ffprobe')
    if found:
        return found
    for candidate in ('/usr/bin/ffprobe', '/usr/local/bin/ffprobe', '/app/vendor/ffmpeg/ffprobe', '/app/bin/ffprobe', '/opt/homebrew/bin/ffprobe'):
        if os.path.exists(candidate) and os.access(candidate, os.X_OK):
            return candidate
    ffmpeg = shutil.which('ffmpeg')
    if ffmpeg:
        sibling = os.path.join(os.path.dirname(ffmpeg), 'ffprobe')
        if os.path.exists(sibling) and os.access(sibling, os.X_OK):
            return sibling
    return None

def ensure_ffprobe_on_path() -> bool:
    import shutil
    if shutil.which('ffprobe'):
        return True
    found = _resolve_ffprobe()
    if not found:
        return False
    os.environ['PATH'] = os.path.dirname(found) + os.pathsep + os.environ.get('PATH', '')
    return bool(shutil.which('ffprobe'))
_HAS_FFPROBE = None

def _ffprobe_available() -> bool:
    global _HAS_FFPROBE
    if _HAS_FFPROBE is None:
        _HAS_FFPROBE = ensure_ffprobe_on_path()
        if not _HAS_FFPROBE:
            log.warning('⚠️ ffprobe binary not found — stream pre-probe disabled (playback continues without it).')
    return _HAS_FFPROBE

def _short_source(path) -> str:
    text = str(path)
    if text.startswith(('http://', 'https://')):
        head = text.split('?', 1)[0]
        return f'{head}?…' if '?' in text else head
    return text

def _is_missing_binary(exc: 'BaseException | None') -> bool:
    if exc is None:
        return False
    if isinstance(exc, FileNotFoundError):
        return True
    text = f'{exc}'.lower()
    return 'no such file or directory' in text and 'ffprobe' in text
try:
    _REMOTE_CHECK_TIMEOUT = max(0.25, float(os.getenv('REMOTE_CHECK_TIMEOUT', '2.5')))
except (TypeError, ValueError):
    _REMOTE_CHECK_TIMEOUT = 2.5
_IS_CLOUD = bool(os.getenv('DYNO') or os.getenv('RAILWAY_ENVIRONMENT') or os.getenv('RENDER_SERVICE_ID') or os.getenv('FLY_APP_NAME'))
try:
    _cloud_budget = max(0.25, float(os.getenv('REMOTE_CHECK_CLOUD_BUDGET', '0.9')))
except (TypeError, ValueError):
    _cloud_budget = 0.9
_REMOTE_CHECK_BUDGET = min(_REMOTE_CHECK_TIMEOUT, _cloud_budget if _IS_CLOUD else _REMOTE_CHECK_TIMEOUT)
_remote_probe_disabled = False
try:
    _REMOTE_CHECK_CACHE_TTL = max(0.5, float(os.getenv('REMOTE_CHECK_CACHE_TTL', '10')))
except (TypeError, ValueError):
    _REMOTE_CHECK_CACHE_TTL = 10.0
_remote_reach_cache: dict[str, float] = {}
_REMOTE_REACH_CACHE_MAX = 256

def _http_reachable_sync(url: str, timeout: float, headers: dict | None=None) -> bool:
    from urllib.request import Request, urlopen
    user_agents = ['Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36']
    if 'c=TVHTML5' in url:
        user_agents.insert(0, 'Mozilla/5.0 (ChromiumStylePlatform) Cobalt/Version')
    elif 'c=ANDROID' in url:
        user_agents.insert(0, 'com.google.android.youtube/19.09.37 (Linux; U; Android 14)')
    supplied = {str(key): str(value) for key, value in (headers or {}).items() if value is not None}
    supplied_agent = supplied.get('User-Agent')
    if supplied_agent:
        user_agents.insert(0, supplied_agent)
    try:
        from curl_cffi import requests as curl_requests
    except Exception:
        curl_requests = None
    for agent in user_agents:
        request_headers = {'Range': 'bytes=0-1', 'User-Agent': agent, 'Accept': '*/*'}
        if supplied.get('Referer'):
            request_headers['Referer'] = supplied['Referer']
        for header_name in ('Origin', 'Cookie'):
            if supplied.get(header_name):
                request_headers[header_name] = supplied[header_name]
        if curl_requests is not None:
            try:
                resp = curl_requests.get(url, headers=request_headers, timeout=timeout, impersonate='chrome124', stream=True)
                try:
                    if 200 <= int(resp.status_code) < 400:
                        first_bytes = next(resp.iter_content(chunk_size=2), b'')
                        return bool(first_bytes)
                finally:
                    resp.close()
            except Exception:
                continue
        req = Request(url, method='GET', headers=request_headers)
        try:
            with urlopen(req, timeout=timeout) as resp:
                if 200 <= getattr(resp, 'status', 200) < 400:
                    return bool(resp.read(2))
        except Exception:
            continue
    return False

async def _remote_reachable(url: str, headers: dict | None=None) -> bool:
    import asyncio
    if not isinstance(url, str) or not url.lower().startswith(('http://', 'https://')):
        return False
    now = time.monotonic()
    cached_until = _remote_reach_cache.get(url, 0.0)
    if cached_until > now:
        return True
    loop = asyncio.get_running_loop()
    try:
        from melody.core.pools import IO_POOL
        reachable = await asyncio.wait_for(loop.run_in_executor(IO_POOL, _http_reachable_sync, url, _REMOTE_CHECK_BUDGET, headers), timeout=_REMOTE_CHECK_BUDGET + 0.25)
        if reachable:
            _remote_reach_cache[url] = time.monotonic() + _REMOTE_CHECK_CACHE_TTL
            if len(_remote_reach_cache) > _REMOTE_REACH_CACHE_MAX:
                cutoff = sorted(_remote_reach_cache, key=_remote_reach_cache.get)
                for old_url in cutoff[:len(_remote_reach_cache) - _REMOTE_REACH_CACHE_MAX]:
                    _remote_reach_cache.pop(old_url, None)
        return bool(reachable)
    except Exception:
        return False

def apply_pytgcalls_probe_patch() -> None:
    global _applied
    if _applied:
        return
    try:
        from pytgcalls import ffmpeg as _ffmpeg_mod
        from pytgcalls.types.stream import media_stream as _media_stream_mod
    except Exception as exc:
        log.warning('pytgcalls probe patch skipped (%s)', exc)
        return
    original = getattr(_ffmpeg_mod, 'check_stream', None)
    if original is None or getattr(original, '_melody_patched', False):
        _applied = True
        return

    async def check_stream(ffmpeg_parameters, path, stream_parameters, *args, **kwargs):
        import asyncio as _asyncio
        last_exc: BaseException | None = None
        local = _is_local_source(path)
        stream_headers = None
        if len(args) > 1 and isinstance(args[1], dict):
            stream_headers = args[1]
        elif isinstance(kwargs.get('headers'), dict):
            stream_headers = kwargs['headers']
        if not _ffprobe_available():
            return None
        if str(path).endswith('.part') or '.part-' in str(path) or '.ytdl' in str(path):
            log.info('⚡ Early-handoff partial file — skipping ffprobe.')
            return None
        if str(path).endswith('.part') or '.part-' in str(path) or '.ytdl' in str(path):
            log.info('⚡ Early-handoff partial file — skipping ffprobe.')
            return None
        if _is_local_proxy_url(path):
            log.info('⚡ Telegram media proxy detected — skipping ffprobe pre-check, playing directly.')
            return None
        if not local:
            if '.m3u8' in str(path).lower():
                log.info('⚡ HLS source detected — skipping remote preflight.')
                return None
            if await _remote_reachable(path, stream_headers):
                log.info('⚡ remote source reachable — skipping ffprobe pre-check, playing directly.')
                return None
            log.debug('remote source failed ranged preflight — using download fallback.')
            raise StreamProbeUnavailable('remote source failed bounded ranged preflight')
        attempts = (1,) if local else (1, 2)
        for attempt in attempts:
            try:
                return await _asyncio.wait_for(original(ffmpeg_parameters, path, stream_parameters, *args, **kwargs), timeout=PROBE_TIMEOUT_LOCAL if local else PROBE_TIMEOUT_REMOTE)
            except Exception as exc:
                name = type(exc).__name__
                if name in ('ImageSourceFound', 'LiveStreamFound', 'InvalidVideoProportion'):
                    raise
                if name == 'NoVideoSourceFound' and local:
                    return None
                last_exc = exc
                if _is_missing_binary(exc):
                    global _HAS_FFPROBE
                    _HAS_FFPROBE = False
                    log.warning('ffprobe binary missing — pre-probe disabled, playing %s directly.', path)
                    return None
                log.log(logging.WARNING if local and attempt == attempts[-1] else logging.DEBUG, 'ffprobe check_stream failed (%s: %s) attempt %d for %s', name, exc, attempt, _short_source(path))
        if local:
            if not _is_local_playable(path) and await _wait_for_growth(path):
                try:
                    return await _asyncio.wait_for(original(ffmpeg_parameters, path, stream_parameters, *args, **kwargs), timeout=PROBE_TIMEOUT_LOCAL)
                except Exception as exc:
                    name = type(exc).__name__
                    if name in ('ImageSourceFound', 'LiveStreamFound', 'InvalidVideoProportion'):
                        raise
                    last_exc = exc
            log.warning('ffprobe unusable for local file %s (%s) — playing without probe.', path, type(last_exc).__name__ if last_exc else '?')
            return None
        safe_detail = redact_sensitive_text(last_exc or 'unknown probe failure')
        raise StreamProbeUnavailable(f'ffprobe could not read source ({type(last_exc).__name__}: {safe_detail})') from last_exc
    check_stream._melody_patched = True
    _ffmpeg_mod.check_stream = check_stream
    if hasattr(_media_stream_mod, 'check_stream'):
        _media_stream_mod.check_stream = check_stream
    _patch_cleanup_commands(_ffmpeg_mod, _media_stream_mod)
    _patch_terminate(_ffmpeg_mod)
    _applied = True
    log.info('🛠 py-tgcalls patch active — probe failures non-fatal, binary capability probe cached (help=%.0fs, local=%.0fs, remote=%.0fs).', HELP_PROBE_TIMEOUT, PROBE_TIMEOUT_LOCAL, PROBE_TIMEOUT_REMOTE)

def _patch_terminate(_ffmpeg_mod) -> None:
    try:
        from asyncio import subprocess as _asubprocess
        proc_cls = _asubprocess.Process
        if getattr(proc_cls.terminate, '_melody_patched', False):
            return
        original_terminate = proc_cls.terminate

        def terminate(self):
            try:
                return original_terminate(self)
            except (ProcessLookupError, OSError):
                return None
        terminate._melody_patched = True
        proc_cls.terminate = terminate
        if not getattr(proc_cls.kill, '_melody_patched', False):
            original_kill = proc_cls.kill

            def kill(self):
                try:
                    return original_kill(self)
                except (ProcessLookupError, OSError):
                    return None
            kill._melody_patched = True
            proc_cls.kill = kill
    except Exception as exc:
        log.debug('terminate() patch skipped (%s)', exc)
