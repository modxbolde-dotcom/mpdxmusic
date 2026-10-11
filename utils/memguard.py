from __future__ import annotations
import asyncio
import ctypes
import gc
import logging
import os
LOGGER = logging.getLogger('MusicBot')
_libc = None

def _malloc_trim() -> bool:
    global _libc
    try:
        if _libc is None:
            _libc = ctypes.CDLL('libc.so.6')
        _libc.malloc_trim(0)
        return True
    except Exception:
        return False

def rss_mb() -> float:
    try:
        with open('/proc/self/statm', 'rb') as fh:
            pages = int(fh.read().split()[1])
        return pages * os.sysconf('SC_PAGE_SIZE') / (1024 * 1024)
    except Exception:
        try:
            import resource
            return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
        except Exception:
            return 0.0

def _env_int(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, '') or 0)
    except ValueError:
        value = 0
    return value if value > 0 else default

def memory_quota_mb() -> int:
    try:
        available = int(float(os.getenv('MEMORY_AVAILABLE', '') or 0))
        if available > 0:
            return available
    except ValueError:
        pass
    for path in ('/sys/fs/cgroup/memory.max', '/sys/fs/cgroup/memory/memory.limit_in_bytes'):
        try:
            with open(path) as fh:
                raw = fh.read().strip()
            if raw and raw != 'max':
                limit = int(raw) // (1024 * 1024)
                if 0 < limit < 64 * 1024:
                    return limit
        except (OSError, ValueError):
            continue
    return 0

def _soft_limit_mb() -> int:
    explicit = os.getenv('MEM_SOFT_LIMIT_MB', '').strip()
    quota = memory_quota_mb()
    if explicit:
        requested = _env_int('MEM_SOFT_LIMIT_MB', 300)
        if quota and quota <= 768:
            return min(requested, int(quota * 0.65))
        return requested
    return int(quota * 0.55) if quota else 260

async def memory_guard() -> None:
    if (os.getenv('MEM_GUARD', 'on') or 'on').lower() in ('off', '0', 'false'):
        return
    soft_limit = _soft_limit_mb()
    quota = memory_quota_mb()
    interval = _env_int('MEM_CHECK_SECONDS', 3)
    peak = 0.0
    LOGGER.info('🧠 memguard active: trim above %d MB (memory limit %s, every %ds)', soft_limit, f'{quota} MB' if quota else 'unknown', interval)
    while True:
        try:
            await asyncio.sleep(interval)
            before = rss_mb()
            peak = max(peak, before)
            if before < soft_limit:
                continue
            gc.collect()
            trimmed = _malloc_trim()
            after = rss_mb()
            LOGGER.info('🧹 memguard: RSS %.0f MB → %.0f MB (limit %d MB, malloc_trim=%s)', before, after, soft_limit, 'yes' if trimmed else 'n/a')
            if quota and after > quota * 0.72:
                LOGGER.warning('⚠️ memguard: RSS %.0f MB is near the %d MB memory limit — dropping caches.', after, quota)
                _emergency_release()
                gc.collect()
                _malloc_trim()
                LOGGER.info('🧯 memguard: RSS after emergency release: %.0f MB', rss_mb())
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            LOGGER.warning('memguard iteration failed: %s', exc)

def _emergency_release() -> None:
    try:
        from melody.core.ytdl import drop_caches
        LOGGER.info('🧯 memguard: dropped %d cached ytdl entries', drop_caches())
    except Exception as exc:
        LOGGER.debug('memguard ytdl cache drop skipped: %s', exc)
    try:
        for obj in gc.get_objects():
            cache_clear = getattr(obj, 'cache_clear', None)
            if callable(cache_clear) and hasattr(obj, 'cache_info'):
                try:
                    cache_clear()
                except Exception:
                    pass
    except Exception as exc:
        LOGGER.debug('memguard lru sweep skipped: %s', exc)
    try:
        from utils.diskguard import sweep as _disk_sweep
        _disk_sweep(aggressive=True)
    except Exception as exc:
        LOGGER.debug('memguard disk sweep skipped: %s', exc)
    try:
        import glob
        import time as _time
        cutoff = _time.time() - 600
        for path in glob.glob('/tmp/*.part') + glob.glob('/tmp/melody_dl/*'):
            try:
                if os.path.isfile(path) and os.path.getmtime(path) < cutoff:
                    os.remove(path)
            except OSError:
                pass
    except Exception as exc:
        LOGGER.debug('memguard tmp sweep skipped: %s', exc)
