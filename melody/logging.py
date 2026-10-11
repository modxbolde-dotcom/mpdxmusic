from utils.branding import BOT_NAME
import html
import logging
import colorlog
import re
import traceback
from urllib.parse import urlsplit, urlunsplit
from pyrogram import enums
from pyrogram.types import LinkPreviewOptions
from melody.config import Config
from strings.themes import fancy
fmt = colorlog.ColoredFormatter('%(log_color)s%(levelname)-8s%(reset)s %(blue)s%(name)s%(reset)s: %(message)s', log_colors={'DEBUG': 'cyan', 'INFO': 'green', 'WARNING': 'yellow', 'ERROR': 'red', 'CRITICAL': 'bold_red'})
handler = logging.StreamHandler()
handler.setFormatter(fmt)
logging.basicConfig(level=logging.INFO, handlers=[handler])
for _noisy, _level in (('pyrogram.session', logging.WARNING), ('pyrogram.connection', logging.WARNING), ('pyrogram.client', logging.WARNING), ('pyrogram.dispatcher', logging.WARNING), ('httpx', logging.WARNING), ('httpcore', logging.WARNING), ('urllib3', logging.WARNING), ('yt_dlp', logging.WARNING), ('asyncio', logging.WARNING)):
    logging.getLogger(_noisy).setLevel(_level)

class _FloodWaitNoiseFilter(logging.Filter):

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        return 'Waiting for' not in msg or 'before continuing' not in msg
logging.getLogger('pyrogram.session.session').addFilter(_FloodWaitNoiseFilter())
LOGGER = logging.getLogger('MusicBot')
_SENSITIVE_URL_RE = re.compile('https?://[^\\s<>\\"\']+', re.IGNORECASE)

def redact_sensitive_text(value) -> str:
    text = str(value or '')

    def _redact(match):
        raw = match.group(0)
        try:
            parts = urlsplit(raw)
            host = (parts.hostname or '').lower()
            if host.endswith(('googlevideo.com', 'youtube.com', 'youtube-nocookie.com')):
                return urlunsplit((parts.scheme, parts.netloc.split('@')[-1], parts.path, '', ''))
            if parts.query or parts.fragment:
                return urlunsplit((parts.scheme, parts.netloc.split('@')[-1], parts.path, '', ''))
        except Exception:
            return '<url-redacted>'
        return raw
    return _SENSITIVE_URL_RE.sub(_redact, text)

def _utc_now() -> str:
    import datetime
    return datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S')

async def log_activity(text: str):
    try:
        from melody import bot
        from melody.config import Config
        if not Config.LOG_GROUP_ID:
            return
        await bot.send_message(Config.LOG_GROUP_ID, text, parse_mode=enums.ParseMode.HTML, link_preview_options=LinkPreviewOptions(is_disabled=True))
    except Exception as exc:
        LOGGER.debug('log_activity failed to deliver: %s | text=%s', exc, text)

def _audit_person(user) -> str:
    if not user:
        return 'Unknown (ID unavailable)'
    name = ' '.join((part for part in (getattr(user, 'first_name', None), getattr(user, 'last_name', None)) if part)) or getattr(user, 'title', None) or 'Unknown'
    username = getattr(user, 'username', None)
    uid = getattr(user, 'id', None)
    suffix = f' @{username}' if username else ''
    return f"{name}{suffix} (ID: {(uid if uid is not None else '—')})"

async def log_group_event(client, action: str, chat_id: int, *, actor=None, target=None, assistant=None, old_status: str | None=None, new_status: str | None=None, result: str | None=None, details: str | None=None):
    try:
        from html import escape
        chat = await client.get_chat(chat_id)
        title = getattr(chat, 'title', None) or getattr(chat, 'first_name', None) or 'Private chat'
        username = getattr(chat, 'username', None)
        chat_line = f"{title}{(f' @{username}' if username else '')} (ID: {chat_id})"
        count = 'unavailable'
        try:
            counter = getattr(client, 'get_chat_members_count', None)
            if counter is not None:
                count = str(await counter(chat_id))
        except Exception:
            pass
        me = None
        try:
            me = await client.get_me()
        except Exception:
            pass
        admin_lines = []
        for label, user in (('Bot', me), ('Assistant', assistant)):
            if not user:
                continue
            try:
                member = await client.get_chat_member(chat_id, user.id)
                admin_lines.append(f"{label}: {getattr(member, 'status', 'unknown')}")
            except Exception as exc:
                admin_lines.append(f'{label}: unavailable ({type(exc).__name__})')
        lines = [f"#group_audit #{str(action).lower().replace(' ', '_')}", f'<b>🏠 Group action: {escape(str(action))}</b>', f'• Group: <b>{escape(chat_line)}</b>', f'• Members at event time: <code>{escape(count)}</code>']
        if actor is not None:
            lines.append(f'• By / actor: <code>{escape(_audit_person(actor))}</code>')
        if target is not None:
            lines.append(f'• Target: <code>{escape(_audit_person(target))}</code>')
        if old_status or new_status:
            lines.append(f"• Status: <code>{escape(old_status or '—')} → {escape(new_status or '—')}</code>")
        if admin_lines:
            lines.append('• Permission snapshot: ' + ' · '.join((escape(x) for x in admin_lines)))
        if result:
            lines.append(f'• Result: <b>{escape(str(result))}</b>')
        if details:
            lines.append(f'• Details: {escape(str(details))}')
        await log_activity('\n'.join(lines))
    except Exception as exc:
        LOGGER.debug('group audit log failed for %s/%s: %s', chat_id, action, exc)

async def send_error_log(text: str, exc: Exception=None, context: dict | None=None, human: str | None=None):
    try:
        from melody import bot
        safe_text = html.escape(redact_sensitive_text(text))
        tags = '#error #crash'
        cmd = (context or {}).get('command')
        if cmd:
            tags += f" #{str(cmd).lower().replace(' ', '_')}"
        msg = f"<b>⚠️ {fancy(f'{BOT_NAME} Error Log')}</b>\n{tags}\n\n<code>{safe_text}</code>"
        if context:
            lines = []
            label_map = {'chat_title': '🏠 Chat', 'chat_id': '🆔 Chat ID', 'user_name': '🙋 User', 'user_id': '🆔 User ID', 'command': '💬 Command', 'song_title': '🎵 Song', 'video_id': '🎬 Video ID', 'uploader': '👤 Uploader'}
            for key, label in label_map.items():
                val = context.get(key)
                if val is not None and val != '':
                    lines.append(f'{label}: <code>{html.escape(redact_sensitive_text(val))}</code>')
            chat_username = context.get('chat_username')
            if chat_username:
                uname = str(chat_username).lstrip('@')
                lines.append(f'🔗 Cʜᴀᴛ Lɪɴᴋ: https://t.me/{html.escape(uname)}')
            if context.get('user_id'):
                lines.append(f"""🔗 Usᴇʀ: <a href="tg://user?id={context['user_id']}">open profile</a>""")
            for key, val in context.items():
                if key in label_map or key == 'chat_username' or val is None or (val == ''):
                    continue
                lines.append(f'{html.escape(key)}: <code>{html.escape(redact_sensitive_text(val))}</code>')
            if lines:
                msg += '\n\n' + '\n'.join(lines)
        if human:
            msg += f'\n\n🧠 <b>Rᴇᴀʟ ɪssᴜᴇ (human language):</b>\n{html.escape(redact_sensitive_text(human))}'
        if exc:
            msg += f"\n\n🐞 <b>Eʀʀᴏʀ Tʏᴘᴇ:</b> <code>{html.escape(type(exc).__name__)}</code>\n📄 <b>Mᴇssᴀɢᴇ:</b> <code>{html.escape(redact_sensitive_text(exc) or '(empty)')}</code>"
            tb_obj = getattr(exc, '__traceback__', None)
            frames = traceback.extract_tb(tb_obj) if tb_obj else []
            if frames:
                last = frames[-1]
                msg += f"\n📍 <b>Wʜᴇʀᴇ:</b> <code>{html.escape(str(last.filename))}:{last.lineno}</code> in <code>{html.escape(str(last.name))}</code>\n🧾 <b>Cᴏᴅᴇ:</b> <code>{html.escape(str(last.line or ''))}</code>"
                chain = ' → '.join((f'{f.name}:{f.lineno}' for f in frames[-6:]))
                msg += f'\n🧗 <b>Cᴀʟʟ ᴘᴀᴛʜ:</b> <code>{html.escape(chain)}</code>'
            cause = getattr(exc, '__cause__', None) or getattr(exc, '__context__', None)
            if cause is not None:
                msg += f'\n🔗 <b>Cᴀᴜsᴇᴅ ʙʏ:</b> <code>{html.escape(type(cause).__name__)}: {html.escape(redact_sensitive_text(str(cause)[:200]))}</code>'
            msg += f'\n🕒 <b>Tɪᴍᴇ (UTC):</b> <code>{_utc_now()}</code>'
            tb = ''.join(traceback.format_exception(type(exc), exc, tb_obj)) if tb_obj else traceback.format_exc()
            safe_tb = html.escape(redact_sensitive_text(tb[-3000:]))
            msg += f'\n\n<pre>{safe_tb}</pre>'
        owner_id = getattr(Config, 'OWNER_ID', 0)
        if owner_id:
            msg += f'\n\n👑 <a href="tg://user?id={owner_id}">Oᴡɴᴇʀ</a> — please check.'
        if len(msg) > 4000:
            msg = msg[:2600] + '\n\n<i>… trimmed …</i>\n\n' + msg[-1200:]
        await bot.send_message(Config.LOG_GROUP_ID, msg, parse_mode=enums.ParseMode.HTML, link_preview_options=LinkPreviewOptions(is_disabled=True))
    except Exception as delivery_exc:
        LOGGER.error('Failed to send log to group: %s', redact_sensitive_text(text), exc_info=delivery_exc)
        if exc:
            LOGGER.error('Original error that failed to deliver: %s', redact_sensitive_text(exc), exc_info=exc)
