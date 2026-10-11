from __future__ import annotations
import time
from utils.tasks import spawn
_inflight: 'dict[tuple[int, str, int], float]' = {}
_QUEUEABLE_PLAY_COMMANDS = {'play', 'vplay', 'cplay', 'cvplay', 'playforce', 'vplayforce'}
_recent: 'dict[tuple[int, int, str], float]' = {}
_seen_messages: 'dict[tuple[int, int], float]' = {}
MAX_HOLD = 90.0
COOLDOWN = 0.8
_ACK_EMOJI = '👍'

def _cmd_name(update) -> 'str | None':
    text = getattr(update, 'text', None) or getattr(update, 'caption', None)
    if not text or text[0] not in '/!.':
        return None
    first = text.split(maxsplit=1)[0][1:]
    return first.split('@', 1)[0].lower() or None

def _prune(now: float) -> None:
    for key, ts in list(_inflight.items()):
        if now - ts > MAX_HOLD:
            _inflight.pop(key, None)
    for key, ts in list(_recent.items()):
        if now - ts > 30:
            _recent.pop(key, None)
    for key, ts in list(_seen_messages.items()):
        if now - ts > 120:
            _seen_messages.pop(key, None)

def instant_ack(update) -> None:
    try:
        react = getattr(update, 'react', None)
        if react is None:
            return

        async def _go():
            try:
                await react(_ACK_EMOJI)
            except Exception:
                pass
        spawn(_go())
    except Exception:
        pass

async def acquire(update) -> 'tuple[bool, str | None]':
    cmd = _cmd_name(update)
    if not cmd:
        return (True, None)
    chat = getattr(update, 'chat', None)
    user = getattr(update, 'from_user', None)
    chat_id = getattr(chat, 'id', 0) or 0
    user_id = getattr(user, 'id', 0) or 0
    now = time.monotonic()
    _prune(now)
    msg_key = (chat_id, getattr(update, 'id', 0) or 0)
    if msg_key[1] and msg_key in _seen_messages:
        return (True, None)
    recent_key = (chat_id, user_id, cmd)
    last = _recent.get(recent_key)
    if last is not None and now - last < COOLDOWN:
        return (False, None)
    lock_user_id = user_id if cmd in _QUEUEABLE_PLAY_COMMANDS else 0
    key = (chat_id, cmd, lock_user_id)
    if key in _inflight:
        instant_ack(update)
        return (False, None)
    _inflight[key] = now
    if msg_key[1]:
        _seen_messages[msg_key] = now
    instant_ack(update)
    return (True, f'{chat_id}|{cmd}|{lock_user_id}')

def release(key: 'str | None', update=None) -> None:
    if not key:
        return
    try:
        chat_str, cmd, lock_user_str = key.split('|', 2)
        chat_id = int(chat_str)
        lock_user_id = int(lock_user_str)
        _inflight.pop((chat_id, cmd, lock_user_id), None)
        user = getattr(update, 'from_user', None)
        _recent[chat_id, getattr(user, 'id', 0) or 0, cmd] = time.monotonic()
    except Exception:
        pass
