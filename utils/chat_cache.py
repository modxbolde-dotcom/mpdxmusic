from __future__ import annotations
import time
import asyncio
from typing import Any
from melody.logging import LOGGER
CHAT_TTL = 300.0
USER_TTL = 600.0
_chat_cache: dict[Any, tuple[float, Any]] = {}
_user_cache: dict[Any, tuple[float, Any]] = {}
_MAX_ENTRIES = 512
_installed = False

def _get(cache: dict, key: Any, ttl: float):
    entry = cache.get(key)
    if not entry:
        return None
    stamp, value = entry
    if time.monotonic() - stamp > ttl:
        cache.pop(key, None)
        return None
    return value

def _put(cache: dict, key: Any, value: Any) -> None:
    if len(cache) >= _MAX_ENTRIES:
        cache.clear()
    cache[key] = (time.monotonic(), value)

def forget(chat_id: Any) -> None:
    _chat_cache.pop(chat_id, None)
    _user_cache.pop(chat_id, None)

def install_chat_cache() -> None:
    global _installed
    if _installed:
        return
    try:
        from pyrogram.client import Client
    except Exception as exc:
        LOGGER.warning('chat cache not installed (%s)', exc)
        return
    original_get_chat = Client.get_chat
    original_get_users = Client.get_users
    if getattr(original_get_chat, '_melody_cached', False):
        _installed = True
        return

    async def get_chat(self, chat_id, *args, **kwargs):
        key = (id(self), chat_id)
        cached = _get(_chat_cache, key, CHAT_TTL)
        if cached is not None:
            return cached
        try:
            result = await asyncio.wait_for(original_get_chat(self, chat_id, *args, **kwargs), timeout=5.0)
        except asyncio.TimeoutError:
            result = cached
        if result is not None:
            _put(_chat_cache, key, result)
        return result

    async def get_users(self, user_ids, *args, **kwargs):
        if isinstance(user_ids, (list, tuple, set)):
            return await original_get_users(self, user_ids, *args, **kwargs)
        key = (id(self), user_ids)
        cached = _get(_user_cache, key, USER_TTL)
        if cached is not None:
            return cached
        result = await original_get_users(self, user_ids, *args, **kwargs)
        if result is not None:
            _put(_user_cache, key, result)
        return result
    get_chat._melody_cached = True
    get_users._melody_cached = True
    Client.get_chat = get_chat
    Client.get_users = get_users
    _installed = True
    LOGGER.info('chat/user info cache installed (FLOOD_WAIT guard)')
