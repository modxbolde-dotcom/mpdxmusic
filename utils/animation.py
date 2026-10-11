import asyncio
import random
import time
from pyrogram.errors import FloodWait, RPCError
FIRE_EMOJIS = ['🔥', '💥', '⚡', '✨', '🎆', '🎇', '🌟', '💫', '🎉', '🚀', '🎶', '🪩', '🎵', '💃']
_GLOBAL_MIN_GAP = 2.0
_last_edit_at = 0.0
_paused_until = 0.0
_lock = asyncio.Lock()

async def _may_edit() -> bool:
    global _last_edit_at
    now = time.monotonic()
    if now < _paused_until:
        return False
    async with _lock:
        if time.monotonic() - _last_edit_at < _GLOBAL_MIN_GAP:
            return False
        _last_edit_at = time.monotonic()
    return True

def _pause(seconds: float) -> None:
    global _paused_until
    _paused_until = max(_paused_until, time.monotonic() + max(seconds, 5.0))

class AnimatedStatus:

    def __init__(self, message, label: str='Getting your vibe ready', interval: float=6.0, max_frames: int=6):
        self._message = message
        self._label = label
        self._interval = max(interval, 4.0)
        self._max_frames = max_frames
        self._task: 'asyncio.Task | None' = None
        self._stopped = asyncio.Event()

    def start(self) -> 'AnimatedStatus':
        self._task = asyncio.create_task(self._loop())
        return self

    async def _loop(self):
        last = None
        frames = 0
        while not self._stopped.is_set() and frames < self._max_frames:
            emoji = random.choice(FIRE_EMOJIS)
            while emoji == last and len(FIRE_EMOJIS) > 1:
                emoji = random.choice(FIRE_EMOJIS)
            last = emoji
            if await _may_edit():
                frames += 1
                try:
                    await self._message.edit(f'{emoji} {self._label} {emoji}')
                except FloodWait as exc:
                    _pause(float(getattr(exc, 'value', 0) or 0))
                    return
                except RPCError:
                    return
                except Exception:
                    return
            try:
                await asyncio.wait_for(self._stopped.wait(), timeout=self._interval)
            except asyncio.TimeoutError:
                pass

    async def stop(self):
        self._stopped.set()
        if self._task:
            try:
                await self._task
            except Exception:
                pass
            self._task = None
