import re
import pyrogram
from pyrogram import filters as _filters
_applied = False
_cached_username = ''
_DESTRUCTIVE_CMDS = {'end', 'stop', 'cend', 'cstop'}

def _get_bot_username_lower() -> str:
    global _cached_username
    config_u = ''
    try:
        from melody.config import Config
        config_u = (getattr(Config, 'BOT_USERNAME', '') or '').lstrip('@').lower()
    except Exception:
        pass
    live_u = ''
    try:
        from melody import bot as _bot
        me = getattr(_bot, 'me', None)
        live_u = (getattr(me, 'username', '') or '').lstrip('@').lower()
    except Exception:
        pass
    u = live_u or config_u or _cached_username
    if u != _cached_username:
        _cached_username = u
    return _cached_username

async def _trigger_enabled(chat_id: int) -> bool:
    try:
        from utils.database import get_setting
        val = await get_setting(chat_id, 'trigger_response', None)
        if val is None:
            return True
        return bool(val)
    except Exception:
        return True

def apply_command_patch() -> None:
    global _applied
    if _applied:
        return
    original_command = _filters.command

    def command(commands, prefixes='/', case_sensitive=False, **kwargs):
        cmds = [commands] if isinstance(commands, str) else list(commands)
        lowered = {c if case_sensitive else c.lower() for c in cmds}
        prefix_list = [prefixes] if isinstance(prefixes, str) and len(prefixes) == 1 else list(prefixes or [])
        is_destructive = any((c in _DESTRUCTIVE_CMDS for c in lowered))

        async def func(flt, client, message):
            text = message.text or message.caption
            if not text:
                return False
            text = text.strip()
            if prefix_list and text[0] not in prefix_list:
                return False
            without_prefix = text[1:] if prefix_list else text
            parts = re.split('\\s+', without_prefix)
            if not parts or not parts[0]:
                return False
            head_full = parts[0]
            head = head_full.split('@', 1)[0]
            if not (head if case_sensitive else head.lower()) in lowered:
                return False
            if '@' in head_full:
                suffix = head_full.split('@', 1)[1].lower()
                our_bot = _get_bot_username_lower()
                if is_destructive and our_bot and (suffix != our_bot):
                    return False
                if suffix != our_bot:
                    chat_id = message.chat.id if message.chat else 0
                    if not await _trigger_enabled(chat_id):
                        return False
            message.command = [head] + parts[1:]
            return True
        return _filters.create(func, 'CommandAnyBot', commands=lowered, prefixes=set(prefix_list) if prefix_list else {''}, case_sensitive=case_sensitive)
    _filters.command = command
    pyrogram.filters.command = command
    _applied = True
    return original_command
