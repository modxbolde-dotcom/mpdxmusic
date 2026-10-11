from __future__ import annotations
import atexit
import os
import threading
from concurrent.futures import ThreadPoolExecutor

def _env_int(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, '') or 0)
    except ValueError:
        value = 0
    return value if value > 0 else default

def _memory_limit_mb() -> int:
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
_MEMORY_LIMIT_MB = _memory_limit_mb()
_DEFAULT_YTDL_WORKERS = 2 if _MEMORY_LIMIT_MB <= 768 else 8
_DEFAULT_IO_WORKERS = 1 if _MEMORY_LIMIT_MB <= 768 else 4
_requested_ytdl_workers = _env_int('YTDL_WORKERS', _DEFAULT_YTDL_WORKERS)
_requested_io_workers = _env_int('IO_WORKERS', _DEFAULT_IO_WORKERS)
YTDL_WORKERS = min(2, _requested_ytdl_workers) if _MEMORY_LIMIT_MB <= 768 else min(8, _requested_ytdl_workers)
IO_WORKERS = min(1, _requested_io_workers) if _MEMORY_LIMIT_MB <= 768 else min(4, _requested_io_workers)
try:
    threading.stack_size(512 * 1024)
except (ValueError, RuntimeError):
    pass
YTDL_POOL = ThreadPoolExecutor(max_workers=YTDL_WORKERS, thread_name_prefix='melody-ytdl')
IO_POOL = ThreadPoolExecutor(max_workers=IO_WORKERS, thread_name_prefix='melody-io')

@atexit.register
def _shutdown() -> None:
    YTDL_POOL.shutdown(wait=False, cancel_futures=True)
    IO_POOL.shutdown(wait=False, cancel_futures=True)
