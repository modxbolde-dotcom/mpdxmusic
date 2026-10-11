from __future__ import annotations
import asyncio
import glob
import logging
import os
import shutil
import time
LOGGER = logging.getLogger('MusicBot')
CACHE_GLOBS = ('/tmp/melody_*_?.*',)
STAGING_GLOB = '/tmp/melody_dl/*'
PARTIAL_GLOBS = ('/tmp/*.part', '/tmp/*.ytdl', '/tmp/melody_*.temp', '/tmp/melody_*.dl.temp')
_IN_PROGRESS_SUFFIXES = ('.part', '.ytdl', '.temp', '.tmp', '.dl')
_MIN_AGE_SECONDS = 60.0

def _env_int(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, '') or 0)
    except ValueError:
        value = 0
    return value if value > 0 else default

def cache_limit_bytes() -> int:
    mb = _env_int('SONG_CACHE_MB', 96)
    return max(16, min(mb, 256)) * 1024 * 1024

def min_free_bytes() -> int:
    return _env_int('DISK_MIN_FREE_MB', 128) * 1024 * 1024

def free_bytes(path: str='/tmp') -> int:
    try:
        return shutil.disk_usage(path).free
    except OSError:
        return 0

def _is_in_progress(path: str) -> bool:
    return path.endswith(_IN_PROGRESS_SUFFIXES) or '.part-' in path or '.dl.' in path

def _protected_video_ids() -> set[str]:
    ids: set[str] = set()
    try:
        from melody.core import queue as _queue
        ids |= _queue.active_video_ids()
    except Exception:
        pass
    return {i for i in ids if i}

def _is_protected(path: str, protected: set[str]) -> bool:
    name = os.path.basename(path)
    return any((name.startswith(f'melody_{vid}_') for vid in protected))

def _collect(patterns) -> list[tuple[str, float, int]]:
    out: list[tuple[str, float, int]] = []
    for pattern in patterns:
        for f in glob.glob(pattern):
            if _is_in_progress(f):
                continue
            try:
                st = os.stat(f)
            except OSError:
                continue
            if not os.path.isfile(f):
                continue
            out.append((f, max(st.st_mtime, st.st_atime), st.st_size))
    return out

def _unlink(path: str) -> int:
    try:
        size = os.path.getsize(path)
        os.unlink(path)
        return size
    except OSError:
        return 0

def sweep(aggressive: bool=False) -> int:
    reclaimed = 0
    now = time.time()
    stale_cutoff = now - (300 if aggressive else 1800)
    for pattern in (*PARTIAL_GLOBS, STAGING_GLOB):
        for f in glob.glob(pattern):
            try:
                if os.path.isfile(f) and os.path.getmtime(f) < stale_cutoff:
                    reclaimed += _unlink(f)
            except OSError:
                pass
    protected = _protected_video_ids()
    files = _collect(CACHE_GLOBS)
    total = sum((f[2] for f in files))
    limit = cache_limit_bytes()
    if aggressive:
        limit //= 4
    if free_bytes() < min_free_bytes():
        limit = min(limit, 16 * 1024 * 1024)
    files.sort(key=lambda x: x[1])
    for path, mtime, size in files:
        if total <= limit:
            break
        if now - mtime < _MIN_AGE_SECONDS:
            continue
        if _is_protected(path, protected):
            continue
        freed = _unlink(path)
        total -= freed
        reclaimed += freed
    if reclaimed:
        LOGGER.info('🧹 diskguard: reclaimed %.1f MB (cache %.1f MB, free %.0f MB)', reclaimed / 1048576, total / 1048576, free_bytes() / 1048576)
    return reclaimed

def boot_sweep() -> int:
    reclaimed = 0
    for pattern in (*PARTIAL_GLOBS, STAGING_GLOB):
        for f in glob.glob(pattern):
            reclaimed += _unlink(f)
    reclaimed += sweep()
    return reclaimed

async def disk_guard() -> None:
    if (os.getenv('DISK_GUARD', '') or '').lower() in {'off', '0', 'false'}:
        LOGGER.info('💾 diskguard disabled via DISK_GUARD')
        return
    interval = _env_int('DISK_CHECK_SECONDS', 90)
    LOGGER.info('💾 diskguard active: cap %d MB, min free %d MB, every %ds', cache_limit_bytes() // 1048576, min_free_bytes() // 1048576, interval)
    try:
        await asyncio.to_thread(boot_sweep)
    except Exception as exc:
        LOGGER.debug('diskguard boot sweep skipped: %s', exc)
    while True:
        try:
            await asyncio.sleep(interval)
            low = free_bytes() < min_free_bytes()
            await asyncio.to_thread(sweep, low)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            LOGGER.warning('diskguard iteration failed: %s', exc)
