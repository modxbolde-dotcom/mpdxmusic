def format_duration(seconds: int) -> str:
    seconds = int(seconds)
    hours = seconds // 3600
    minutes = seconds % 3600 // 60
    secs = seconds % 60
    if hours:
        return f'{hours:02d}:{minutes:02d}:{secs:02d}'
    return f'{minutes:02d}:{secs:02d}'

def mention(user_id: int, name: str) -> str:
    return f'[{name}](tg://user?id={user_id})'
import html as _html
import re as _re
_MD_PRE = _re.compile('```(?:\\w+\\n)?(.*?)```', _re.DOTALL)
_MD_BOLD = _re.compile('\\*\\*(.+?)\\*\\*')
_MD_CODE = _re.compile('`([^`]+?)`')
_MD_ITALIC = _re.compile('(?<!_)_([^_]+?)_(?!_)')

def md_to_html(text: str) -> str:
    text = _MD_PRE.sub(lambda m: f'<pre>{_html.escape(m.group(1))}</pre>', text)
    text = _MD_BOLD.sub('<b>\\1</b>', text)
    text = _MD_CODE.sub(lambda m: f'<code>{_html.escape(m.group(1))}</code>', text)
    text = _MD_ITALIC.sub('<i>\\1</i>', text)
    return text

def quote_html(text: str, expandable: bool=False) -> str:
    body = md_to_html(text)
    tag = 'blockquote expandable' if expandable else 'blockquote'
    close = 'blockquote'
    return f'<{tag}>{body}</{close}>'

def mention_html(user_id: int, name: str) -> str:
    return f'<a href="tg://user?id={user_id}">{_html.escape(name)}</a>'
from pyrogram import enums as _enums
from pyrogram.errors import RPCError as _RPCError
import logging as _logging
from utils.telegram_html import verify_custom_emojis, strip_all_emoji_tags, strip_blockquote
_log = _logging.getLogger(__name__)

def premium_emoji(emoji_id: str, glyph: str) -> str:
    from utils.emoji_map import EMOJI_ID_MAP, RESOLVED_IDS
    key = glyph.replace('️', '')
    if RESOLVED_IDS and key in EMOJI_ID_MAP:
        emoji_id = EMOJI_ID_MAP[key]
    return f'<emoji id="{emoji_id}">{glyph}</emoji>'
PREMIUM_EMOJI_IDS = {'start': '5188705588925702510', 'about': '5188705588925702510', 'ping': '5269563867305879894', 'stats': '5231200819986047254', 'pause': '5359543311897998264', 'resume': '5204012324087680174', 'skip': '5368509223632118184', 'stop': '5134537521518085000', 'nowplaying': '4916169087698076775', 'queue': '6154222749691679144'}

async def send_quote(handler, text: str, *, client=None, edit: bool=False, **kwargs):
    if '<blockquote' not in text:
        text = quote_html(text)
    send = handler.edit_text if edit else handler.reply
    prepared = await verify_custom_emojis(client, text)
    try:
        return await send(prepared, parse_mode=_enums.ParseMode.HTML, **kwargs)
    except _RPCError as e:
        _log.warning('send_quote: retrying without premium emoji entities (%s)', e)
    stripped = strip_all_emoji_tags(prepared)
    try:
        return await send(stripped, parse_mode=_enums.ParseMode.HTML, **kwargs)
    except _RPCError as e:
        _log.error('send_quote: retrying as plain text (%s)', e)
    plain = strip_blockquote(stripped)
    return await send(plain, parse_mode=_enums.ParseMode.DISABLED, **kwargs)
