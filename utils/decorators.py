import asyncio
import functools
import re
import time
from pyrogram import enums
from pyrogram import Client, ContinuePropagation, StopPropagation
from pyrogram.errors import FloodWait
from pyrogram.types import Message, CallbackQuery
from melody.config import Config
from utils.formatters import quote_html
from melody.logging import LOGGER, send_error_log
from utils.database import is_banned, is_gbanned, get_auth_users
from utils import cmd_lock
from utils.error_translate import translate as translate_error
from utils.tasks import spawn
_admin_cache: dict = {}
_ADMIN_TTL = 90
_ADMIN_NEGATIVE_TTL = 5
_ban_cache: dict = {}
_BAN_TTL = 30
_BAN_CHECK_TIMEOUT = 2.0
_AUTH_CHECK_TIMEOUT = 1.5
_chat_admins: dict = {}
_CHAT_ADMINS_TTL = 600

async def refresh_chat_admins(client: Client, chat_id: int) -> set:
    admins: set = set()
    try:
        async for m in client.get_chat_members(chat_id, filter=enums.ChatMembersFilter.ADMINISTRATORS):
            if m.user:
                admins.add(m.user.id)
    except Exception as exc:
        LOGGER.warning('Admin-list refresh failed for chat %s: %s', chat_id, exc)
        return _chat_admins.get(chat_id, (0, set()))[1]
    _chat_admins[chat_id] = (time.monotonic() + _CHAT_ADMINS_TTL, admins)
    for c, u in [k for k in _admin_cache if k[0] == chat_id]:
        _admin_cache.pop((c, u), None)
    for uid in admins:
        _set_cached_admin(chat_id, uid, True)
    return admins

def _chat_admin_set(chat_id: int) -> 'set | None':
    entry = _chat_admins.get(chat_id)
    if not entry or entry[0] <= time.monotonic():
        _chat_admins.pop(chat_id, None)
        return None
    return entry[1]

def invalidate_admin_cache(chat_id: int | None=None) -> None:
    if chat_id is None:
        _admin_cache.clear()
        _chat_admins.clear()
        return
    _chat_admins.pop(chat_id, None)
    for key in [k for k in _admin_cache if k[0] == chat_id]:
        _admin_cache.pop(key, None)

def _is_cached_admin(chat_id: int, user_id: int) -> bool | None:
    admins = _chat_admin_set(chat_id)
    if admins is not None and user_id in admins:
        return True
    entry = _admin_cache.get((chat_id, user_id))
    if entry is None:
        return None
    is_admin, ts = entry
    ttl = _ADMIN_TTL if is_admin else _ADMIN_NEGATIVE_TTL
    if time.monotonic() - ts > ttl:
        _admin_cache.pop((chat_id, user_id), None)
        return None
    return is_admin

def _set_cached_admin(chat_id: int, user_id: int, is_admin: bool) -> None:
    _admin_cache[chat_id, user_id] = (is_admin, time.monotonic())

def _is_admin_status(status) -> bool:
    return status in (enums.ChatMemberStatus.OWNER, enums.ChatMemberStatus.ADMINISTRATOR) or str(status).lower() in {'chatmemberstatus.owner', 'chatmemberstatus.administrator', 'owner', 'creator', 'administrator'}

async def _fetch_admin_status(client: Client, chat_id: int, user_id: int) -> 'bool | None':
    if _chat_admin_set(chat_id) is None:
        spawn(refresh_chat_admins(client, chat_id))
    for attempt in range(2):
        try:
            member = await client.get_chat_member(chat_id, user_id)
            return _is_admin_status(member.status)
        except Exception as exc:
            if attempt == 0:
                await asyncio.sleep(0.15)
                continue
            LOGGER.warning('Admin lookup failed for user %s in chat %s: %s', user_id, chat_id, exc)
    return None

def _cached_ban_status(user_id: int) -> tuple[bool, bool] | None:
    entry = _ban_cache.get(user_id)
    if entry is None:
        return None
    banned, gbanned, ts = entry
    if time.monotonic() - ts > _BAN_TTL:
        _ban_cache.pop(user_id, None)
        return None
    return (banned, gbanned)

def _set_cached_ban_status(user_id: int, banned: bool, gbanned: bool) -> None:
    _ban_cache[user_id] = (banned, gbanned, time.monotonic())

async def _auth_users_fast(chat_id: int) -> list | None:
    try:
        return await asyncio.wait_for(get_auth_users(chat_id), timeout=_AUTH_CHECK_TIMEOUT)
    except Exception as exc:
        LOGGER.debug('auth lookup timed out for %s: %s', chat_id, type(exc).__name__)
        return None

async def _check_banned(user_id: int) -> tuple[bool, bool]:
    cached = _cached_ban_status(user_id)
    if cached is not None:
        return cached
    banned, gbanned = await asyncio.wait_for(asyncio.gather(is_banned(user_id), is_gbanned(user_id)), timeout=_BAN_CHECK_TIMEOUT)
    result = (bool(banned), bool(gbanned))
    _set_cached_ban_status(user_id, *result)
    return result

def owner_only(func):

    @functools.wraps(func)
    async def wrapper(client: Client, message: Message, *args, **kwargs):
        if message.from_user and message.from_user.id == Config.OWNER_ID:
            return await func(client, message, *args, **kwargs)
    return wrapper

def admin_or_auth(func):

    @functools.wraps(func)
    async def wrapper(client: Client, message: Message, *args, **kwargs):
        if not message.from_user:
            return
        user_id = message.from_user.id
        chat_id = message.chat.id
        if user_id == Config.OWNER_ID:
            return await func(client, message, *args, **kwargs)
        banned, gbanned = await _check_banned(user_id)
        if gbanned:
            return await message.reply(quote_html('❌ You are globally banned from using this bot.'), parse_mode=enums.ParseMode.HTML)
        if banned:
            return await message.reply(quote_html('❌ You are banned from using this bot.'), parse_mode=enums.ParseMode.HTML)
        auth_users = await _auth_users_fast(chat_id)
        if auth_users is not None and user_id in auth_users:
            return await func(client, message, *args, **kwargs)
        cached = _is_cached_admin(chat_id, user_id)
        if cached is True:
            return await func(client, message, *args, **kwargs)
        if cached is False:
            await message.reply(quote_html('⚠️ Only group admins or authorized users can use this command.'), parse_mode=enums.ParseMode.HTML)
            return
        status = await _fetch_admin_status(client, chat_id, user_id)
        if status is True:
            _set_cached_admin(chat_id, user_id, True)
            return await func(client, message, *args, **kwargs)
        if status is None:
            return await message.reply(quote_html('⚠️ Admin status abhi verify nahi ho paya. Ek baar phir try karo.'), parse_mode=enums.ParseMode.HTML)
        _set_cached_admin(chat_id, user_id, False)
        await message.reply(quote_html('⚠️ Only group admins or authorized users can use this command.'), parse_mode=enums.ParseMode.HTML)
    return wrapper

def channel_admin_or_auth(func):

    @functools.wraps(func)
    async def wrapper(client: Client, message: Message, *args, **kwargs):
        chat_id = message.chat.id
        if message.from_user is None:
            return await func(client, message, *args, **kwargs)
        user_id = message.from_user.id
        if user_id == Config.OWNER_ID:
            return await func(client, message, *args, **kwargs)
        banned, gbanned = await _check_banned(user_id)
        if gbanned:
            return await message.reply(quote_html('❌ You are globally banned from using this bot.'), parse_mode=enums.ParseMode.HTML)
        if banned:
            return await message.reply(quote_html('❌ You are banned from using this bot.'), parse_mode=enums.ParseMode.HTML)
        auth_users = await _auth_users_fast(chat_id)
        if auth_users is not None and user_id in auth_users:
            return await func(client, message, *args, **kwargs)
        cached = _is_cached_admin(chat_id, user_id)
        if cached is True:
            return await func(client, message, *args, **kwargs)
        if cached is False:
            await message.reply(quote_html('⚠️ Only channel admins or authorized users can use this command.'), parse_mode=enums.ParseMode.HTML)
            return
        status = await _fetch_admin_status(client, chat_id, user_id)
        if status is True:
            _set_cached_admin(chat_id, user_id, True)
            return await func(client, message, *args, **kwargs)
        if status is None:
            return await message.reply(quote_html('⚠️ Admin status abhi verify nahi ho paya. Ek baar phir try karo.'), parse_mode=enums.ParseMode.HTML)
        _set_cached_admin(chat_id, user_id, False)
        await message.reply(quote_html('⚠️ Only channel admins or authorized users can use this command.'), parse_mode=enums.ParseMode.HTML)
    return wrapper

def playmode_gate(func):
    strict = channel_admin_or_auth(func)

    @functools.wraps(func)
    async def wrapper(client: Client, message: Message, *args, **kwargs):
        try:
            from melody.plugins.music.playmode import get_access_mode
            mode = await asyncio.wait_for(get_access_mode(message.chat.id), timeout=1.0)
        except Exception:
            mode = 'everyone'
        if mode != 'everyone' or message.from_user is None:
            return await strict(client, message, *args, **kwargs)
        user_id = message.from_user.id
        if user_id != Config.OWNER_ID:
            banned, gbanned = await _check_banned(user_id)
            if gbanned:
                return await message.reply(quote_html('❌ You are globally banned from using this bot.'), parse_mode=enums.ParseMode.HTML)
            if banned:
                return await message.reply(quote_html('❌ You are banned from using this bot.'), parse_mode=enums.ParseMode.HTML)
        return await func(client, message, *args, **kwargs)
    return wrapper

def error_handler(func):

    @functools.wraps(func)
    async def wrapper(client, update, *args, **kwargs):
        lock_key = None
        if isinstance(update, Message):
            try:
                allowed, lock_key = await cmd_lock.acquire(update)
                if not allowed:
                    return
            except Exception:
                lock_key = None
        try:
            return await func(client, update, *args, **kwargs)
        except (StopPropagation, ContinuePropagation):
            raise
        except FloodWait as e:
            LOGGER.warning('FloodWait %ss in %s — sleeping once and retrying.', e.value, func.__name__)
            try:
                await asyncio.sleep(e.value)
                return await func(client, update, *args, **kwargs)
            except Exception:
                pass
        except Exception as e:
            ctx = {'command': func.__name__}
            try:
                if isinstance(update, CallbackQuery):
                    if update.message and update.message.chat:
                        ctx['chat_id'] = update.message.chat.id
                        ctx['chat_title'] = update.message.chat.title
                        ctx['chat_username'] = getattr(update.message.chat, 'username', None)
                    if update.from_user:
                        ctx['user_id'] = update.from_user.id
                        ctx['user_name'] = update.from_user.first_name or ''
                elif hasattr(update, 'chat') and update.chat:
                    ctx['chat_id'] = update.chat.id
                    ctx['chat_title'] = update.chat.title
                    ctx['chat_username'] = getattr(update.chat, 'username', None)
                if hasattr(update, 'from_user') and update.from_user:
                    ctx['user_id'] = update.from_user.id
                    ctx['user_name'] = update.from_user.first_name or ''
            except Exception:
                pass
            try:
                info = await translate_error(e)
            except Exception:
                info = {'human': 'An unexpected problem interrupted this command.', 'user': '🌸 <b>Ye command puri nahi ho payi.</b> Thoda ruk kar dubara bhejo.'}
            ctx['human_reason'] = info.get('human', '')
            await send_error_log(f'Error in {func.__name__}: {e}', exc=e, context=ctx, human=info.get('human'))
            try:
                if isinstance(update, CallbackQuery):
                    plain = re.sub('<[^>]+>', '', info['user']).strip()
                    await update.answer(plain[:195], show_alert=True)
                else:
                    await update.reply(info['user'], parse_mode=enums.ParseMode.HTML, disable_web_page_preview=True)
            except Exception:
                pass
        finally:
            cmd_lock.release(lock_key, update)
    return wrapper

async def cb_admin_or_auth(client: Client, cb, chat_id: int | None=None) -> bool:
    user = getattr(cb, 'from_user', None)
    if not user:
        return False
    user_id = user.id
    chat = getattr(getattr(cb, 'message', None), 'chat', None)
    if chat_id is None:
        chat_id = getattr(chat, 'id', None)
    if chat_id is None:
        return False
    if getattr(chat, 'id', None) != chat_id:
        chat = None
    if user_id == Config.OWNER_ID:
        return True
    banned, gbanned = await _check_banned(user_id)
    if gbanned or banned:
        await cb.answer('❌ You are banned from using this bot.', show_alert=True)
        return False
    if chat is not None and getattr(chat, 'type', None) is not None and (str(chat.type) in ('ChatType.PRIVATE', 'private')):
        return True
    auth_users = await _auth_users_fast(chat_id)
    if auth_users is not None and user_id in auth_users:
        return True
    cached = _is_cached_admin(chat_id, user_id)
    if cached is True:
        return True
    if cached is False:
        await cb.answer('⚠️ Sirf group admins ya authorized users ye control kar sakte hain.', show_alert=True)
        return False
    try:
        member = await client.get_chat_member(chat_id, user_id)
        if str(member.status) in ('ChatMemberStatus.OWNER', 'ChatMemberStatus.ADMINISTRATOR', 'creator', 'administrator'):
            _set_cached_admin(chat_id, user_id, True)
            return True
    except Exception:
        pass
    _set_cached_admin(chat_id, user_id, False)
    await cb.answer('⚠️ Sirf group admins ya authorized users ye control kar sakte hain.', show_alert=True)
    return False

async def is_admin_or_auth(client: Client, chat_id: int, user_id: int) -> bool:
    if not user_id:
        return False
    if user_id == Config.OWNER_ID:
        return True
    banned, gbanned = await _check_banned(user_id)
    if banned or gbanned:
        return False
    auth_users = await _auth_users_fast(chat_id)
    if auth_users is not None and user_id in auth_users:
        return True
    cached = _is_cached_admin(chat_id, user_id)
    if cached is not None:
        return cached
    status = await _fetch_admin_status(client, chat_id, user_id)
    if status is None:
        return False
    _set_cached_admin(chat_id, user_id, status)
    return status

async def cb_playmode_gate(client: Client, cb, chat_id: int | None=None) -> bool:
    user = getattr(cb, 'from_user', None)
    if not user:
        return False
    if chat_id is None:
        chat = getattr(getattr(cb, 'message', None), 'chat', None)
        chat_id = getattr(chat, 'id', None)
    if chat_id is None:
        return False
    if user.id != Config.OWNER_ID:
        banned, gbanned = await _check_banned(user.id)
        if gbanned or banned:
            await cb.answer('❌ You are banned from using this bot.', show_alert=True)
            return False
    try:
        from melody.plugins.music.playmode import get_access_mode
        mode = await asyncio.wait_for(get_access_mode(chat_id), timeout=1.0)
    except Exception:
        mode = 'everyone'
    if mode == 'everyone':
        return True
    return await cb_admin_or_auth(client, cb, chat_id=chat_id)
