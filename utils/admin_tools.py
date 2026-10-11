from utils.buttons import ikb
import asyncio
import html
import inspect
import logging
import time
from pyrogram import enums
from pyrogram.errors import RPCError
from pyrogram.types import ChatPermissions, ChatPrivileges, InlineKeyboardMarkup, Message
from melody.config import Config
from utils.gc_db import is_sudo
ADMIN_STATUSES = (enums.ChatMemberStatus.OWNER, enums.ChatMemberStatus.ADMINISTRATOR)
log = logging.getLogger(__name__)
MUTED_PERMS = ChatPermissions(can_send_messages=False)
_PERM_FIELDS = set(inspect.signature(ChatPermissions.__init__).parameters)

def _perms(**kwargs) -> ChatPermissions:
    unknown = [k for k in kwargs if k not in _PERM_FIELDS]
    if unknown:
        log.debug('admin_tools: ignoring unsupported ChatPermissions flags %s', unknown)
    return ChatPermissions(**{k: v for k, v in kwargs.items() if k in _PERM_FIELDS})
UNMUTED_PERMS = _perms(can_send_messages=True, can_send_media_messages=True, can_send_plain=True, can_send_photos=True, can_send_videos=True, can_send_audios=True, can_send_docs=True, can_send_voices=True, can_send_roundvideos=True, can_send_stickers=True, can_send_gifs=True, can_send_games=True, can_send_inline=True, can_send_other_messages=True, can_send_polls=True, can_add_web_page_previews=True, can_invite_users=True, can_change_info=False, can_pin_messages=False)

def mention(user) -> str:
    if user is None:
        return '<i>Unknown</i>'
    uid = getattr(user, 'id', None) or 0
    name = getattr(user, 'first_name', None) or getattr(user, 'title', None) or 'User'
    return f'<a href="tg://user?id={uid}">{html.escape(str(name))}</a>'

def mention_id(user_id: int, name: str='User') -> str:
    return f'<a href="tg://user?id={user_id}">{html.escape(str(name))}</a>'

def close_kb(extra: list | None=None) -> InlineKeyboardMarkup:
    rows = list(extra or [])
    rows.append([ikb('✵ CLOSE ✵', callback_data='gc_close')])
    return InlineKeyboardMarkup(rows)

def card(title: str, body: str, footer: str='') -> str:
    from utils.melody_theme import card as themed_card
    return themed_card(title, body, footer=footer)

async def get_status(client, chat_id: int, user_id: int):
    try:
        member = await client.get_chat_member(chat_id, user_id)
        return member
    except Exception:
        return None

def _is_admin_status(status) -> bool:
    if status in ADMIN_STATUSES:
        return True
    return str(status) in ('ChatMemberStatus.OWNER', 'ChatMemberStatus.ADMINISTRATOR', 'creator', 'administrator')

async def is_admin(client, chat_id: int, user_id: int) -> bool:
    if user_id == Config.OWNER_ID or await is_sudo(user_id):
        return True
    member = await get_status(client, chat_id, user_id)
    return bool(member) and _is_admin_status(member.status)

async def is_group_owner(client, chat_id: int, user_id: int) -> bool:
    if user_id == Config.OWNER_ID or await is_sudo(user_id):
        return True
    member = await get_status(client, chat_id, user_id)
    if not member:
        return False
    return member.status == enums.ChatMemberStatus.OWNER or str(member.status) in ('ChatMemberStatus.OWNER', 'creator')

async def extract_target(client, message: Message, arg_index: int=1):
    if message.reply_to_message and message.reply_to_message.from_user:
        return message.reply_to_message.from_user
    for entity in message.entities or []:
        if entity.type == enums.MessageEntityType.TEXT_MENTION and entity.user:
            return entity.user
    parts = (message.command or [])[arg_index:]
    if not parts:
        return None
    ref = parts[0].strip()
    if ref.startswith('@'):
        ref = ref[1:]
    try:
        return await client.get_users(int(ref) if ref.lstrip('-').isdigit() else ref)
    except Exception:
        return None

def extract_reason(message: Message, start: int=1) -> str:
    parts = (message.command or [])[start:]
    if message.reply_to_message:
        return ' '.join(parts).strip()
    return ' '.join(parts[1:]).strip()

async def protected_target(client, message: Message, user, allow_admin: bool=False, positive: bool=False) -> 'str | None':
    if user is None:
        return None
    if positive:
        return None
    if user.id == Config.OWNER_ID:
        return '👑 <b>Yᴇ ᴍᴇʀᴇ ᴏᴡɴᴇʀ ʜᴀɪ</b> — action denied.'
    if await is_sudo(user.id):
        return '⚡ <b>Sᴜᴅᴏ ᴜsᴇʀ</b> — action denied.'
    me = await client.get_me()
    if user.id == me.id:
        return '🎶 <b>Mᴜᴊʜ ᴘᴇ ʜɪ ?</b> Nice try 😌'
    member = await get_status(client, message.chat.id, user.id)
    if member and _is_admin_status(member.status):
        if not allow_admin:
            return '🛡 <b>Tᴀʀɢᴇᴛ ɪs ᴀɴ ᴀᴅᴍɪɴ</b> — demote them first.'
        if member.status == enums.ChatMemberStatus.OWNER or str(member.status) in ('ChatMemberStatus.OWNER', 'creator'):
            return '👑 <b>Yᴇ ɢʀᴏᴜᴘ ᴋᴀ ᴏᴡɴᴇʀ ʜᴀɪ</b> — inhe koi demote nahi kar sakta.'
    return None
RIGHT_KEYS = ('can_manage_chat', 'can_delete_messages', 'can_manage_video_chats', 'can_restrict_members', 'can_promote_members', 'can_change_info', 'can_invite_users', 'can_pin_messages', 'can_manage_topics', 'can_post_messages', 'can_edit_messages', 'can_post_stories', 'can_edit_stories', 'can_delete_stories')
CORE_RIGHT_KEYS = ('can_delete_messages', 'can_restrict_members', 'can_promote_members', 'can_change_info', 'can_invite_users', 'can_pin_messages', 'can_manage_video_chats')

async def privileges_of(client, chat_id: int, user_id: int):
    member = await get_status(client, chat_id, user_id)
    if not member or not _is_admin_status(member.status):
        return None
    return getattr(member, 'privileges', None)

async def is_full_rights(client, chat_id: int, user_id: int) -> bool:
    if user_id == Config.OWNER_ID or await is_sudo(user_id):
        return True
    if await is_group_owner(client, chat_id, user_id):
        return True
    privileges = await privileges_of(client, chat_id, user_id)
    if privileges is None:
        return False
    return all((bool(getattr(privileges, key, False)) for key in CORE_RIGHT_KEYS))
_BG_TASKS: set = set()

async def auto_delete(msg, seconds: int=120) -> None:

    async def _worker():
        try:
            await asyncio.sleep(seconds)
        except Exception:
            return
        for attempt in range(4):
            try:
                await msg.delete()
                return
            except Exception:
                try:
                    await asyncio.sleep(1.5 * (attempt + 1))
                except Exception:
                    return
        try:
            from melody import bot
            await bot.delete_messages(msg.chat.id, msg.id)
        except Exception:
            pass
    task = asyncio.create_task(_worker())
    _BG_TASKS.add(task)
    task.add_done_callback(_BG_TASKS.discard)

def human_delta(ts: 'float | int | None') -> str:
    if not ts:
        return '—'
    delta = max(0, int(time.time() - float(ts)))
    if delta < 60:
        return f'{delta}s ago'
    if delta < 3600:
        return f'{delta // 60}m ago'
    if delta < 86400:
        return f'{delta // 3600}h {delta % 3600 // 60}m ago'
    return f'{delta // 86400}d ago'
FULL_PRIVILEGES = ChatPrivileges(can_manage_chat=True, can_delete_messages=True, can_manage_video_chats=True, can_restrict_members=True, can_promote_members=True, can_change_info=True, can_invite_users=True, can_pin_messages=True)
BASIC_PRIVILEGES = ChatPrivileges(can_manage_chat=True, can_delete_messages=True, can_manage_video_chats=True, can_restrict_members=True, can_promote_members=False, can_change_info=False, can_invite_users=True, can_pin_messages=True)

async def safe_call(coro):
    try:
        await coro
        return (True, '')
    except RPCError as exc:
        return (False, html.escape(str(exc)))
    except Exception as exc:
        return (False, html.escape(str(exc)))
